"""Private, portable event evidence. Callers authorize and hold the event lock.

This is a report, not a CTFd backup: credentials, native email/IP fields, flags,
submitted answers and attachment bytes/locations are deliberately not selected.
Authored content and organizer notes are retained and can themselves be private.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import uuid

import CTFd
from CTFd.models import (Awards, ChallengeComments, ChallengeFiles, ChallengeTopics,
                         Challenges, Flags, Hints, Notifications, Ratings,
                         SolutionFiles, Solutions, Submissions, Tags, TeamComments,
                         TeamFieldEntries, TeamFields, Teams, Topics, Unlocks,
                         UserComments, UserFieldEntries, UserFields, Users, db)
from . import read_cache, registration
from .models import (QuestAsset, QuestChallengeSource, QuestAudit, QuestCorrection, QuestDemoBatch, QuestDemoChallenge, QuestExclusion,
                     QuestHintPurchase, QuestMaintainer, QuestOnboarding,
                     QuestPause, QuestReadiness, QuestRoom, QuestStart,
                     QuestStep, QuestTeamName)
from .recovery_models import QuestRecoverySolve
from .service import Projection, standings


FORMAT = "open-tech-quest/event-archive"
SCHEMA_VERSION = 1


def timestamp(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds") + "Z"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _row(row, fields):
    return {field: timestamp(value) if isinstance(value := getattr(row, field), datetime) else deepcopy(value)
            for field in fields.split()}


# Each nested object has its own schema: never copy arbitrary audit detail blobs.
_EVENT_FIELDS = "name start_at end_at rosters_release_at room_clock overall_clock".split()
_ROOM_FIELDS = "name subtitle description challenge_ids".split()
_TEAM_FIELDS = "name user_ids excluded reason".split()
_SCALARS = {"team_id", "user_id", "challenge_id", "hint_id", "room_id", "room_slug", "batch_id",
            "original_solve_id", "detached_team_id", "cost", "adjustment_ms", "reason", "at", "late",
            "name", "old", "new", "source", "generation", "new_team", "joined_at", "fingerprint",
            "team_size", "allow_team_naming", "file_id", "kind"}
_LISTS = {"user_ids", "challenge_ids", "unassigned_user_ids", "fields"}


def _flat(detail, keys):
    return {key: value for key, value in detail.items() if key in keys and
            (value is None or type(value) in (str, bool, int))}


def _audit_detail(action, raw):
    if not isinstance(raw, dict):
        return {}
    known = (action.startswith("event.") or action.startswith("demo.") or
             action.startswith("onboarding.") or action in {
                 "attendance.configure", "registration.configure", "room.create", "room.delete",
                 "room.configure", "team.configure", "teams.allocate", "projector.configure",
                 "ranking.correct", "user.access", "user.delete", "user.role", "hint.purchase",
                 "challenge.create", "challenge.update", "challenge.delete", "challenge.recover", "challenge.clear", "asset.upload", "asset.delete"})
    if not known:
        return {}
    result = _flat(raw, _SCALARS | (set(_EVENT_FIELDS) if action == "event.configure" else set()))
    if action in {"challenge.create", "challenge.update"} and isinstance(raw.get("maintenance"), dict):
        maintenance = raw["maintenance"]
        selected = _flat(maintenance, {"reason", "revision", "before_revision", "after_revision"})
        if isinstance(maintenance.get("fields"), list):
            selected["fields"] = [field for field in maintenance["fields"] if isinstance(field, str)]
        impact = maintenance.get("impact")
        if isinstance(impact, dict):
            keys = {"counted_teams", "solve_records", "hint_purchase_teams", "hint_receipts", "completed_teams", "teams_with_score_changes"}
            selected["impact"] = {key: value for key, value in impact.items() if key in keys and type(value) is int}
            if isinstance(impact.get("team_scores"), list):
                score_keys = {"team_id", "before_points", "after_points", "before_room_points", "after_room_points", "before_solved", "after_solved"}
                selected["impact"]["team_scores"] = [{key: value for key, value in row.items()
                    if key in score_keys and type(value) is int} for row in impact["team_scores"] if isinstance(row, dict)]
        result["maintenance"] = selected
    if action in ("attendance.configure", "registration.configure") and type(raw.get("enabled")) is bool:
        result["enabled"] = raw["enabled"]
    for key in _LISTS:
        if isinstance(raw.get(key), list):
            result[key] = [item for item in raw[key] if type(item) is int or (key == "fields" and isinstance(item, str))]
    if action in ("teams.allocate", "onboarding.optimize") and isinstance(raw.get("groups"), list):
        result["groups"] = [
            {**_flat(group, {"team_id", "name", "new"}),
             **({"user_ids": [uid for uid in group["user_ids"] if type(uid) is int]} if isinstance(group.get("user_ids"), list) else {})}
            if isinstance(group, dict) else [uid for uid in group if type(uid) is int]
            for group in raw["groups"] if isinstance(group, (dict, list))]
    if action in ("room.configure", "team.configure") and isinstance(raw.get("changes"), dict):
        keys = _ROOM_FIELDS if action == "room.configure" else _TEAM_FIELDS
        changes = raw["changes"]
        result["changes"] = _flat(changes, set(keys))
        for key in ("challenge_ids", "user_ids"):
            if key in keys and isinstance(changes.get(key), list):
                result["changes"][key] = [item for item in changes[key] if type(item) is int]
    if action == "projector.configure":
        result.update(_flat(raw, {"view", "room_slug"}))
        if isinstance(raw.get("rotation"), dict):
            result["rotation"] = _flat(raw["rotation"], {"enabled", "anchor_at", "paused_at", "overview_seconds", "room_seconds", "overall_seconds"})
    return result


def _timeline(data):
    entries = []

    def add(kind, row, at, summary, **links):
        if at:
            entries.append({"id": f"{kind}:{row.get('id', row.get('user_id', row.get('team_id', 'event')))}",
                            "at": at, "type": kind, "summary": summary, **links})

    for row in data["attempts"]:
        add("attempt", row, row["date"], row["type"], user_id=row["user_id"], team_id=row["team_id"],
            challenge_id=row["challenge_id"], room_id=row["room_id"],
            details={"credited": row["credited"], "recovery": row["recovery"]})
    for row in data["starts"]:
        add("room.start", row, row["started_at"], "Room started", team_id=row["team_id"], room_id=row["room_id"])
    for row in data["pauses"]:
        add("event.pause", row, row["started_at"], "Event paused")
        add("event.resume", row, row["ended_at"], "Event resumed")
    for key, kind, date, summary in (("readiness", "attendance.ready", "ready_at", "Attendance confirmed"),
                                     ("corrections", "ranking.correct", "created_at", "Room time corrected"),
                                     ("hint_purchases", "hint.purchase", "purchased_at", "Hint purchased"),
                                     ("ratings", "challenge.rating", "date", "Challenge rated")):
        for row in data[key]:
            add(kind, row, row[date], summary, **{key: row[key] for key in ("team_id", "user_id", "room_id", "challenge_id") if key in row})
    for row in data["audit"]:
        add("audit", row, row["created_at"], row["action"], user_id=row["actor_id"], details=row["detail"])
    for key, kind in (("start_at", "event.start"), ("end_at", "event.cutoff")):
        add(kind, {}, data["event"][key], "Configured event boundary")
    return sorted(entries, key=lambda row: (row["at"], row["type"], row["id"]))


def collect_event(e):
    """Collect primitive rows from the caller's locked coherent transaction.

    Results retain the authoritative published snapshot where one exists;
    scoring_credits/attempt.credited describe the current source projection.
    Excluding an entire team from prizes does not erase its solve evidence.
    """
    users = db.session.query(Users.id, Users.name, Users.team_id, Users.type, Users.hidden,
                             Users.banned, Users.verified, Users.created).order_by(Users.id).all()
    grants = {row.user_id: row for row in QuestMaintainer.query.all()}
    people = {user.id: user for user in users}
    room_rows = QuestRoom.query.order_by(QuestRoom.position, QuestRoom.id).all()
    steps = QuestStep.query.order_by(QuestStep.room_id, QuestStep.position, QuestStep.id).all()
    mapping = {step.challenge_id: step.room_id for step in steps}
    view = Projection(e)
    recovery = {row.id: row for row in db.session.query(QuestRecoverySolve.id, QuestRecoverySolve.credited_at,
                QuestRecoverySolve.credited_user_id, QuestRecoverySolve.original_solve_id).order_by(QuestRecoverySolve.id).all()}
    # Select only safe base submission columns, never provided or ip.
    submission_rows = db.session.query(Submissions.id, Submissions.user_id, Submissions.team_id,
                                      Submissions.challenge_id, Submissions.date, Submissions.type).order_by(Submissions.date, Submissions.id).all()
    attempts = []
    credited = set()
    for row in submission_rows:
        restored = recovery.get(row.id)
        at = restored.credited_at if restored else row.date
        user = people.get(row.user_id)
        key = row.team_id, row.challenge_id
        eligible = (user is not None and user.type == "user" and not user.banned and user.id not in grants and
                    at is not None and at == view.solves.get(row.team_id, {}).get(row.challenge_id) and
                    (row.type == "correct" or restored is not None) and key not in credited)
        if eligible:
            credited.add(key)
        attempts.append({**_row(row, "id user_id team_id challenge_id date type"), "room_id": mapping.get(row.challenge_id),
                         "credited": bool(eligible), "recovery": restored is not None,
                         "credited_at": timestamp(restored.credited_at) if restored else None,
                         "credited_user_id": restored.credited_user_id if restored else None,
                         "original_solve_id": restored.original_solve_id if restored else None})
    rooms = [{**_row(room, "id slug name subtitle description position theme"),
              "steps": [_row(step, "id challenge_id position") for step in steps if step.room_id == room.id]}
             for room in room_rows]
    flag_types = {}
    for challenge_id, flag_type, count in db.session.query(Flags.challenge_id, Flags.type, db.func.count(Flags.id)).group_by(Flags.challenge_id, Flags.type).order_by(Flags.challenge_id, Flags.type).all():
        flag_types.setdefault(challenge_id, []).append({"type": flag_type, "count": count})
    hints = [{**_row(row, "id challenge_id title type content cost requirements"),
              "prerequisites": deepcopy(row.prerequisites)} for row in Hints.query.order_by(Hints.id).all()]
    solutions = [_row(row, "id challenge_id content state") for row in Solutions.query.order_by(Solutions.id).all()]
    solution_challenges = {row["id"]: row["challenge_id"] for row in solutions}
    attachments = [{**_row(row, "id type challenge_id sha1sum"), "solution_id": None,
                    "name": (row.location or "").replace("\\", "/").split("/")[-1], "embedded": False}
                   for row in ChallengeFiles.query.order_by(ChallengeFiles.id).all()]
    attachments.extend({**_row(row, "id type solution_id sha1sum"),
                        "challenge_id": solution_challenges.get(row.solution_id),
                        "name": (row.location or "").replace("\\", "/").split("/")[-1], "embedded": False}
                       for row in SolutionFiles.query.order_by(SolutionFiles.id).all())
    state = QuestOnboarding.query.get(1)
    data = {
        "event": _row(e, "id name status start_at end_at paused_at rosters_release_at room_clock overall_clock projector snapshot"),
        "results": deepcopy(standings(e)),
        "source_revisions": read_cache.versions(),
        "participants": [{**_row(user, "id name team_id hidden banned verified created"),
                          "role": "administrator" if user.type == "admin" else "maintainer" if user.id in grants else "participant",
                          "maintainer_granted_at": timestamp(grants[user.id].granted_at) if user.id in grants else None}
                         for user in users],
        "teams": [{**_row(team, "id name captain_id hidden banned created"),
                   "members": sorted(user.id for user in users if user.team_id == team.id)}
                  for team in db.session.query(Teams.id, Teams.name, Teams.captain_id, Teams.hidden,
                                               Teams.banned, Teams.created).order_by(Teams.id).all()],
        "onboarding": _row(state, "phase team_size applied_at finalized_at grandfathered attendance_open attendance_generation attendance_opened_at attendance_closed_at allow_team_naming") if state else None,
        "registration": registration.status(),
        "readiness": [_row(row, "user_id ready_at late") for row in QuestReadiness.query.order_by(QuestReadiness.user_id).all()],
        "naming": [_row(row, "team_id claimed_by claimed_at name_locked auto_named") for row in QuestTeamName.query.order_by(QuestTeamName.team_id).all()],
        "rooms": rooms,
        "challenges": [{**_row(row, "id name description attribution connection_info next_id max_attempts value category type state logic initial minimum decay position function requirements"),
                        "room_id": mapping.get(row.id), "flag_metadata": flag_types.get(row.id, [])}
                       for row in Challenges.query.order_by(Challenges.id).all()],
        "hints": hints,
        "solutions": solutions,
        "tags": [_row(row, "id challenge_id value") for row in Tags.query.order_by(Tags.id).all()],
        "topics": [_row(row, "id value") for row in Topics.query.order_by(Topics.id).all()],
        "challenge_topics": [_row(row, "id challenge_id topic_id") for row in ChallengeTopics.query.order_by(ChallengeTopics.id).all()],
        "attachments": sorted(attachments, key=lambda row: row["id"]),
        "challenge_sources": [_row(row, "challenge_id room_id source_key") for row in QuestChallengeSource.query.order_by(QuestChallengeSource.challenge_id).all()],
        "managed_assets": [_row(row, "file_id challenge_id kind name size sha256 created_at") for row in QuestAsset.query.order_by(QuestAsset.file_id).all()],
        "attempts": attempts,
        "scoring_credits": [{"team_id": team_id, "challenge_id": challenge_id, "at": timestamp(at)}
                            for team_id, solves in sorted(view.solves.items()) for challenge_id, at in sorted(solves.items())],
        "starts": [_row(row, "id team_id room_id started_at") for row in QuestStart.query.order_by(QuestStart.started_at, QuestStart.id).all()],
        "pauses": [_row(row, "id started_at ended_at") for row in QuestPause.query.order_by(QuestPause.started_at, QuestPause.id).all()],
        "corrections": [_row(row, "id team_id room_id adjustment_ms reason created_at") for row in QuestCorrection.query.order_by(QuestCorrection.created_at, QuestCorrection.id).all()],
        "exclusions": [_row(row, "team_id reason") for row in QuestExclusion.query.order_by(QuestExclusion.team_id).all()],
        "hint_purchases": [_row(row, "id team_id challenge_id hint_id unlock_id award_id cost purchased_at") for row in QuestHintPurchase.query.order_by(QuestHintPurchase.purchased_at, QuestHintPurchase.id).all()],
        "unlocks": [_row(row, "id user_id team_id target date type") for row in Unlocks.query.order_by(Unlocks.date, Unlocks.id).all()],
        "awards": [_row(row, "id user_id team_id type name description date value category") for row in Awards.query.order_by(Awards.date, Awards.id).all()],
        "ratings": [_row(row, "id user_id challenge_id value review date") for row in Ratings.query.order_by(Ratings.date, Ratings.id).all()],
        "team_comments": [_row(row, "id team_id author_id content date") for row in TeamComments.query.order_by(TeamComments.date, TeamComments.id).all()],
        "team_fields": [_row(row, "id name field_type description required public editable") for row in TeamFields.query.order_by(TeamFields.id).all()],
        "team_field_entries": [_row(row, "id team_id field_id value") for row in TeamFieldEntries.query.order_by(TeamFieldEntries.id).all()],
        "user_comments": [_row(row, "id user_id author_id content date") for row in UserComments.query.order_by(UserComments.date, UserComments.id).all()],
        "user_fields": [_row(row, "id name field_type description required public editable") for row in UserFields.query.order_by(UserFields.id).all()],
        "user_field_entries": [_row(row, "id user_id field_id value") for row in UserFieldEntries.query.order_by(UserFieldEntries.id).all()],
        "challenge_comments": [_row(row, "id challenge_id author_id content date") for row in ChallengeComments.query.order_by(ChallengeComments.date, ChallengeComments.id).all()],
        "demo_batches": [_row(row, "id active active_slot challenge_ids rooms created_at removed_at") for row in QuestDemoBatch.query.order_by(QuestDemoBatch.created_at, QuestDemoBatch.id).all()],
        "demo_challenges": [_row(row, "id batch_id challenge_id") for row in QuestDemoChallenge.query.order_by(QuestDemoChallenge.id).all()],
        "notifications": [_row(row, "id team_id user_id title content date") for row in Notifications.query.order_by(Notifications.date, Notifications.id).all()],
        "audit": [{**_row(row, "id actor_id action created_at"), "detail": _audit_detail(row.action, row.detail)}
                  for row in QuestAudit.query.filter(QuestAudit.action != "event.archive").order_by(QuestAudit.created_at, QuestAudit.id).all()],
    }
    data["timeline"] = _timeline(data)
    return data


def source_fingerprint(data):
    source = deepcopy(data)
    results = source.get("results", {})
    results.pop("as_of", None)
    results.get("event", {}).pop("server_now", None)
    return hashlib.sha256(canonical_json(source).encode("utf-8")).hexdigest()


def make_document(data):
    document = {"format": FORMAT, "schema_version": SCHEMA_VERSION,
                "archive_id": uuid.uuid4().hex, "exported_at": timestamp(datetime.utcnow()),
                "producer": {"name": "Open Tech Quest", "plugin": "open_tech_quest",
                             "plugin_version": os.environ.get("OTQ_PLUGIN_VERSION", "dev"),
                             "ctfd_version": getattr(CTFd, "__version__", "unknown"),
                             "source": "https://github.com/open-tech-quest/ctfd-plugin"},
                "data": deepcopy(data)}
    document["integrity"] = {"algorithm": "sha256", "sha256": hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()}
    return document
