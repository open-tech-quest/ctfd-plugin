"""Stateless, revision-bound review of live content; never a second score ledger."""
import hashlib
import hmac
import json
import re

from flask import current_app, request
from sqlalchemy import or_
from CTFd.models import (Challenges, ChallengeTopics, Files, Flags, Hints, Solutions,
                         Solves, Submissions, Tags, Teams, Topics, db)
from .models import QuestAsset, QuestAudit, QuestChallengeSource, QuestHintPurchase, QuestRoom, QuestStep

ACTIVE = {"live", "paused"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    default=str, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def records(model, condition):
    table = model.__table__
    return [dict(row._mapping) for row in db.session.execute(
        table.select().where(condition).order_by(*table.primary_key.columns))]


def room_revision(room):
    ids = [cid for cid, in db.session.query(QuestStep.challenge_id).filter_by(room_id=room.id)]
    solution_ids = db.session.query(Solutions.id).filter(Solutions.challenge_id.in_(ids))
    return digest({"room": {c.name: getattr(room, c.name) for c in QuestRoom.__table__.columns},
        "steps": records(QuestStep, QuestStep.room_id == room.id),
        "challenges": records(Challenges, Challenges.id.in_(ids)),
        "flags": records(Flags, Flags.challenge_id.in_(ids)),
        "hints": records(Hints, Hints.challenge_id.in_(ids)),
        "solutions": records(Solutions, Solutions.challenge_id.in_(ids)),
        "files": records(Files, or_(Files.__table__.c.challenge_id.in_(ids), Files.__table__.c.solution_id.in_(solution_ids))),
        "assets": records(QuestAsset, QuestAsset.challenge_id.in_(ids)),
        "sources": records(QuestChallengeSource, QuestChallengeSource.room_id == room.id),
        "tags": records(Tags, Tags.challenge_id.in_(ids)),
        "topics": [(cid, tid, value) for cid, tid, value in db.session.query(ChallengeTopics.challenge_id,
            Topics.id, Topics.value).join(Topics, Topics.id == ChallengeTopics.topic_id)
            .filter(ChallengeTopics.challenge_id.in_(ids)).order_by(ChallengeTopics.challenge_id, Topics.id)]})


def catalog_revision(rooms):
    return digest([(room.id, room_revision(room)) for room in sorted(rooms, key=lambda r: r.id)])


def check_revision(e, actual, provided=None):
    from .api import fail
    provided = request.headers.get("X-Quest-Content-Revision") if provided is None else provided
    if provided is None and e.status not in ACTIVE:
        return
    if provided is None:
        fail("Refresh the page before saving live content. This page is outdated or its saved content revision is missing.", 409)
    if not isinstance(provided, str) or not hmac.compare_digest(actual, provided):
        fail("Saved content changed. Refresh the editor, review your draft and try again.", 409)


def append_reason(room, e):
    from . import demo
    from .room_topology import topology_reason
    if e.status == "draft":
        return topology_reason(room, e)
    if e.status not in ACTIVE:
        return "Challenge creation is unavailable after closing. Reopen the event before editing."
    if demo.active_batch():
        return "An administrator must remove the loaded demo before real challenge authoring."
    from .service import Projection
    p = Projection(e)
    teams = [tid for tid, in db.session.query(Teams.id).filter_by(banned=False, hidden=False)]
    if any(p.progress(room, tid)["status"] == "complete" for tid in teams):
        return "A team has completed this room. Create a new room for additional challenges; its completed path is preserved."
    return None


def competitive_fields(challenge, changes):
    from .authoring import flag_rows, hint_rows
    if challenge is None:
        return ["append"]
    fields = [key for key in ("value", "state", "max_attempts")
              if key in changes and changes[key] != getattr(challenge, key)]
    if "flags" in changes:
        old = [(f.type, f.content, f.data or "") for f in flag_rows(challenge)]
        new = [(f["type"], f["content"], f["data"] or "") for f in changes["flags"]]
        if old != new:
            fields.append("flags")
    if "hints" in changes:
        old = {h.id: h.cost for h in hint_rows(challenge)}
        new = {h["id"]: h["cost"] for h in changes["hints"] if "id" in h}
        if old != new or any("id" not in h for h in changes["hints"]):
            fields.append("hints")
    if "solution" in changes:
        old = Solutions.query.filter_by(challenge_id=challenge.id).first()
        new = changes["solution"]
        if (old.state if old else None) != (new["state"] if new else None):
            fields.append("solution")
    return fields


def impact(room, challenge, changes, e):
    from .service import Projection
    before = Projection(e)
    cid = challenge.id if challenge else None
    override = {cid: {k: changes[k] for k in ("value", "state") if k in changes}} if cid else None
    after = Projection(e, challenge_overrides=override, at=before.at) if override else before
    teams = [tid for tid, in db.session.query(Teams.id).filter_by(banned=False, hidden=False).order_by(Teams.id)]
    scores = []
    for tid in teams:
        left, right = before.progress(room, tid), after.progress(room, tid)
        old, new = before.score(tid)["points"], after.score(tid)["points"]
        if old != new or left["credited_solved"] != right["credited_solved"]:
            scores.append({"team_id": tid, "before_points": old, "after_points": new,
                "before_room_points": left["points"], "after_room_points": right["points"],
                "before_solved": left["credited_solved"], "after_solved": right["credited_solved"]})
    purchases = QuestHintPurchase.query.filter_by(challenge_id=cid).all() if cid else []
    return {"counted_teams": sum(cid in s for s in before.solves.values()) if cid else 0,
        "solve_records": Solves.query.filter_by(challenge_id=cid).count() if cid else 0,
        "hint_purchase_teams": len({p.team_id for p in purchases}), "hint_receipts": len(purchases),
        "completed_teams": sum(before.progress(room, tid)["status"] == "complete" for tid in teams),
        "teams_with_score_changes": len(scores), "team_scores": scores}


def review_token(room, challenge, changes, e, result):
    from .lifecycle import current_user, identity
    # Play continues while an organizer reviews. Content/phase/actor changes
    # invalidate consent; ordinary solves and purchases do not. Their latest
    # impact and all safety constraints are rechecked at the apply lock.
    epoch = db.session.query(db.func.max(QuestAudit.id)).filter_by(action="event.new_round").scalar()
    data = [room_revision(room), challenge.id if challenge else None, changes, e.status, e.start_at, epoch,
            identity(current_user())]
    key = current_app.secret_key
    return hmac.new(key if isinstance(key, bytes) else str(key).encode(),
                    digest(data).encode(), hashlib.sha256).hexdigest()


def review(room, challenge, changes, e):
    fields = competitive_fields(challenge, changes)
    result = impact(room, challenge, changes, e)
    warnings = []
    if "append" in fields:
        warnings.append("Adds a final step without restarting existing room clocks or changing recorded starts. No automatic time compensation is applied; use a new room for a separate start.")
    if "flags" in fields:
        warnings.append("Only future submissions use the new answers. Existing solves and failed attempts are not regraded.")
    if "value" in fields:
        warnings.append("The new point value applies uniformly to existing eligible solves and future solves; solve times do not change.")
    if "hints" in fields:
        warnings.append("New hint prices apply only to future purchases. Existing purchase costs and native charges are preserved.")
    if "state" in fields:
        warnings.append("Hidden withdraws this challenge and its hint costs from every team's results without deleting history or automatically compensating elapsed time. Locked blocks access without withdrawing credit.")
    return {"revision": review_token(room, challenge, changes, e, result),
            "requires_confirmation": e.status in ACTIVE and bool(fields),
            "competitive_fields": fields, "impact": result, "warnings": warnings}


def confirmation(room, challenge, changes, e, maintenance):
    from .api import fail
    if e.status not in ACTIVE or not competitive_fields(challenge, changes):
        return None
    if (not isinstance(maintenance, dict) or set(maintenance) != {"revision", "reason", "confirm"}
            or maintenance["confirm"] is not True or not isinstance(maintenance["reason"], str)
            or not 3 <= len(maintenance["reason"].strip()) <= 1000 or "\x00" in maintenance["reason"]):
        fail("Review this competitive change, confirm the current impact and provide a reason (3–1000 characters).", 409)
    result = review(room, challenge, changes, e)
    if not isinstance(maintenance["revision"], str) or not hmac.compare_digest(maintenance["revision"], result["revision"]):
        fail("Content or competition progress changed since review. Review the current impact again before applying.", 409)
    return {"reason": maintenance["reason"].strip(), "fields": result["competitive_fields"],
            "impact": result["impact"], "revision": result["revision"]}


def grading_revision(challenge):
    from .authoring import flag_rows
    return digest([challenge.id, challenge.type, challenge.logic, challenge.max_attempts,
                   [(f.id, f.type, f.content, f.data) for f in flag_rows(challenge)]])


def protect_native_content(path, data, e):
    """Native administrator/token routes cannot bypass live review or closure."""
    if e.status == "draft":
        return None
    from .guards import denied, native_id
    from CTFd.models import ChallengeTopics
    resources = {"challenges": Challenges, "flags": Flags, "hints": Hints,
                 "solutions": Solutions, "tags": Tags, "files": Files, "topics": Topics}
    match = re.match(r"^/api/v1/(challenges|flags|hints|solutions|tags|files|topics)(?:/([^/]+))?(?:/|$)", path)
    if not match or path == "/api/v1/challenges/attempt" or path.endswith("/ratings"):
        return None
    ids = set()
    for field in ("challenge_id", "solution_id", "challenge", "solution"):
        value = data.get(field)
        if value is None or value == "":
            continue
        if field in {"challenge", "solution"} and isinstance(value, dict):
            if set(value) != {"id"}:
                return denied("Use a positive integer content owner ID", 400)
            value = value["id"]
        target = native_id(value)
        if target is None:
            return denied("Use a positive integer content owner ID", 400)
        if field in {"challenge_id", "challenge"}:
            ids.add(target)
        else:
            ids.update(cid for cid, in db.session.query(Solutions.challenge_id).filter_by(id=target))
    resource, raw_id = match.groups()
    if raw_id:
        if raw_id == "types" and resource in {"challenges", "flags"}:
            return None
        target = native_id(raw_id)
        if target is None:
            return denied("Use a positive integer content resource ID", 400)
        if resource == "challenges":
            ids.add(target)
        elif resource == "topics":
            ids.update(cid for cid, in db.session.query(ChallengeTopics.challenge_id).filter_by(topic_id=target))
        else:
            table = resources[resource].__table__
            row = db.session.execute(table.select().where(table.c.id == target)).mappings().first()
            if row:
                if row.get("challenge_id"):
                    ids.add(row["challenge_id"])
                if resource == "files" and row.get("solution_id"):
                    ids.update(cid for cid, in db.session.query(Solutions.challenge_id).filter_by(id=row["solution_id"]))
    if ids and QuestStep.query.filter(QuestStep.challenge_id.in_(ids)).first():
        return denied("Use Open Tech Quest live maintenance to review mapped content changes. Closed or published content is read-only; reopen it first.", 409)
    return None
