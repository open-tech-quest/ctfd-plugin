"""Room-scoped standard challenge editing; native CTFd still owns grading."""
import hmac
import math
import re
from datetime import datetime
from flask import Blueprint, request, session
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from CTFd.cache import clear_challenges
from CTFd.models import (ChallengeComments, ChallengeFiles, ChallengeTopics, Challenges,
                         Flags, Hints, SolutionFiles, Solutions, Tags, Topics, UserTokens, db)
from CTFd.utils import get_app_config
from CTFd.plugins.flags import get_flag_class
from .api import (QuestError, audit, body, conflict, error, fail, http_error,
                  room_by_slug, success)
from .roles import can_manage_rooms, room_manager
from .room_topology import activity_reason, set_challenge_order, topology_reason
from .service import event, room_steps
from .models import QuestHintPurchase, QuestChallengeSource, QuestAsset, QuestStep
from . import demo

authoring_api = Blueprint("quest_authoring", __name__)
authoring_api.register_error_handler(QuestError, error)
authoring_api.register_error_handler(HTTPException, http_error)
authoring_api.register_error_handler(IntegrityError, conflict)

NAME_LIMIT = 80
DESCRIPTION_LIMIT = 10000
FLAG_LIMIT = 1024
FLAG_COUNT_LIMIT = 20
HINT_COUNT_LIMIT = 20
EDIT_FIELDS = {"name", "description", "state", "flags", "value", "hints", "attribution",
               "connection_info", "max_attempts", "tags", "topics", "solution", "source_key"}


@authoring_api.after_request
def private_workspace(response):
    # Only this authorized workspace includes saved answers.
    response.headers["Cache-Control"] = "private, no-store"
    return response


def flag_rows(challenge):
    # Raw rows also allow unknown native flag plugins to remain read-only
    # without needing to instantiate their polymorphic ORM classes.
    return db.session.query(Flags.id, Flags.type, Flags.content, Flags.data).filter(
        Flags.challenge_id == challenge.id).order_by(Flags.id).all()


def hint_rows(challenge):
    return db.session.query(Hints.id, Hints.type, Hints.title, Hints.content,
                            Hints.cost, Hints.requirements).filter_by(
                                challenge_id=challenge.id).order_by(Hints.id).all()


def hint_prerequisites(hint):
    requirements = hint.requirements or {}
    values = requirements.get("prerequisites", []) if isinstance(requirements, dict) else []
    return values if isinstance(values, list) else []


def dependency_prerequisites(requirements, owner):
    """Inspect native JSON without binding arbitrary imported objects as SQL IDs."""
    if requirements is None:
        return [], None
    values = requirements.get("prerequisites", []) if isinstance(requirements, dict) else None
    if (not isinstance(values, list) or any(
            type(value) not in (int, float, str, bool)
            or type(value) is int and not -(2 ** 63) <= value < 2 ** 63
            or type(value) is float and not math.isfinite(value)
            for value in values)):
        return [], (f"Native {owner} has invalid prerequisite metadata. An administrator must repair "
                    "its prerequisites in native CTFd before deleting challenges or hints.")
    return values, None


def hint_deletion_reason(ids):
    if not ids:
        return None
    for hint in db.session.query(Hints.id, Hints.requirements).filter(~Hints.id.in_(ids)):
        required, invalid = dependency_prerequisites(hint.requirements, f"hint #{hint.id}")
        if invalid:
            return invalid
        # Match the native purchase lookup, including imported numeric strings.
        if required and Hints.query.filter(Hints.id.in_(ids), Hints.id.in_(required)).first():
            return (f"Native hint #{hint.id} depends on a hint being removed. An administrator must "
                    "resolve its hint prerequisites in native CTFd before deletion.")
    return None


def unsupported_reason(challenge, flags=None, allow_native_flags=False):
    if challenge.type != "standard" or challenge.logic != "any":
        return "This challenge uses unsupported native challenge type or flag logic. Manage it in native CTFd."
    if challenge.function != "static":
        return "This challenge uses dynamic scoring. Manage its value in native CTFd."
    flags = flags if flags is not None else flag_rows(challenge)
    supported = (all(f.type in ("static", "regex") and (f.data is None or isinstance(f.data, str) and len(f.data) <= 64) for f in flags)
                 if allow_native_flags else all(f.type == "static" and f.data in (None, "", "case_insensitive") for f in flags))
    if not supported:
        return "This challenge uses unsupported native flag behavior. Manage it in native CTFd."
    if any(h.type != "standard" for h in hint_rows(challenge)):
        return "This challenge uses unsupported native hint behavior. Manage its hints in native CTFd."
    return None


def mutation_reason(room, challenge, e, allow_native_flags=False):
    if e.status != "draft":
        return "Challenge content is editable only while the event is in Draft."
    if demo.active_batch():
        return "An administrator must remove the loaded demo before real challenge authoring."
    return unsupported_reason(challenge, allow_native_flags=allow_native_flags) or activity_reason(room, [challenge.id])


def outside_dependency(room, challenge, removed_hint_ids=None):
    own_ids = {s.challenge_id for s in room_steps(room)}
    for candidate in Challenges.query.filter(~Challenges.id.in_(list(own_ids))).all():
        prerequisites, invalid = dependency_prerequisites(candidate.requirements, f"challenge #{candidate.id}")
        if invalid:
            return invalid
        referenced = Challenges.query.filter(Challenges.id == challenge.id, Challenges.id.in_(prerequisites)).first() if prerequisites else None
        if candidate.next_id == challenge.id or referenced:
            return (f"Native challenge #{candidate.id} outside this room depends on challenge #{challenge.id}. "
                    "An administrator must resolve that dependency in native CTFd before deletion.")
    return hint_deletion_reason(removed_hint_ids if removed_hint_ids is not None else {h.id for h in hint_rows(challenge)})


_UNINSPECTED = object()


def editable(room, challenge, e=None, mapped=True, deletion_blocker=_UNINSPECTED):
    e = e if e is not None else event()
    flags = flag_rows(challenge)
    unsupported = unsupported_reason(challenge, flags)
    edit_reason = mutation_reason(room, challenge, e)
    automation_reason = mutation_reason(room, challenge, e, allow_native_flags=True)
    delete_reason = automation_reason or topology_reason(room, e) or outside_dependency(room, challenge)
    if delete_reason is None:
        delete_reason = unknown_deletion_reason() if deletion_blocker is _UNINSPECTED else deletion_blocker
    reattach_reason = ("This challenge is already mapped." if mapped else
                       automation_reason or topology_reason(room, e, challenge_ids=[challenge.id]))
    if not mapped:
        edit_reason = automation_reason = delete_reason = "Reattach this catalog challenge to its room before editing or deleting it."
    source = QuestChallengeSource.query.get(challenge.id)
    solution = Solutions.query.filter_by(challenge_id=challenge.id).first()
    return {"id": challenge.id, "mapped": mapped, "name": challenge.name, "description": challenge.description or "",
            "state": challenge.state, "type": challenge.type, "value": challenge.value,
            "attribution": challenge.attribution or "", "connection_info": challenge.connection_info or "",
            "max_attempts": challenge.max_attempts or 0,
            "tags": [tag.value for tag in Tags.query.filter_by(challenge_id=challenge.id).order_by(Tags.id)],
            "topics": [topic.value for topic in Topics.query.join(ChallengeTopics, ChallengeTopics.topic_id == Topics.id).filter(ChallengeTopics.challenge_id == challenge.id).order_by(Topics.id)],
            "solution": {"id": solution.id, "content": solution.content or "", "state": solution.state} if solution else None,
            "source_key": source.source_key if source else None,
            "flags": [] if unsupported else [{"content": f.content, "case_insensitive": f.data == "case_insensitive"} for f in flags],
            "native_flags": [{"type": f.type, "content": f.content, "data": f.data} for f in flags],
            "hints": [{"id": h.id, "title": h.title or "", "content": h.content or "",
                       "cost": h.cost, "prerequisites": hint_prerequisites(h)} for h in hint_rows(challenge)],
            "can_edit": edit_reason is None, "edit_reason": edit_reason,
            "can_automate": automation_reason is None, "automation_reason": automation_reason,
            "can_reattach": not mapped and reattach_reason is None, "reattach_reason": reattach_reason,
            "can_delete": delete_reason is None, "delete_reason": delete_reason}


def member_challenge(room, challenge_id):
    if challenge_id not in {s.challenge_id for s in room_steps(room)}:
        fail("Challenge not found in this room", 404)
    challenge = Challenges.query.get(challenge_id)
    if challenge is None:
        fail("Challenge not found in this room", 404)
    return challenge


def checked_text(value, label, limit, empty=False):
    if (not isinstance(value, str) or len(value) > limit or "\x00" in value
            or (not empty and not value.strip())):
        fail(f"{label} must be {'a string' if empty else 'non-empty text'} of at most {limit} characters")
    return value


def checked_payload(data, create=False):
    if set(data) - EDIT_FIELDS or not data:
        fail("Unknown challenge field")
    if create and not {"name", "description", "state", "flags"} <= set(data):
        fail("A new challenge requires name, description, state and flags")
    result = {}
    for field in ("attribution", "connection_info"):
        if field in data:
            result[field] = checked_text(data[field], field, DESCRIPTION_LIMIT, empty=True)
    if "max_attempts" in data:
        if type(data["max_attempts"]) is not int or not 0 <= data["max_attempts"] <= 2147483647:
            fail("max_attempts must be a nonnegative whole number")
        result["max_attempts"] = data["max_attempts"]
    for field, limit in (("tags", 80), ("topics", 255)):
        if field in data:
            values = data[field]
            if not isinstance(values, list) or len(values) > 50:
                fail(f"{field} must be a list of at most 50 names")
            result[field] = [checked_text(value, field, limit).strip() for value in values]
            if len(set(result[field])) != len(result[field]):
                fail(f"{field} names must be unique")
    if "solution" in data:
        solution = data["solution"]
        if solution is None:
            result["solution"] = None
        else:
            if not isinstance(solution, dict) or set(solution) != {"content", "state"}:
                fail("solution must be null or contain content and state")
            if solution["state"] not in ("hidden", "visible", "solved"):
                fail("Solution state must be hidden, visible or solved")
            result["solution"] = {"content": checked_text(solution["content"], "Solution", 100000, empty=True), "state": solution["state"]}
    if "source_key" in data:
        key = data["source_key"]
        if not isinstance(key, str) or len(key) > 255 or re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", key) is None:
            fail("source_key must be a lowercase category/slug identity (maximum 255 characters)")
        result["source_key"] = key
    if "value" in data:
        if type(data["value"]) is not int or not 0 <= data["value"] <= 2147483647:
            fail("Challenge points must be a whole number from 0 to 2147483647")
        result["value"] = data["value"]
    if "name" in data:
        result["name"] = checked_text(data["name"], "Challenge name", NAME_LIMIT).strip()
    if "description" in data:
        result["description"] = checked_text(data["description"], "Description", DESCRIPTION_LIMIT, empty=True)
    if "state" in data:
        if data["state"] not in ("hidden", "visible", "locked"):
            fail("Challenge state must be hidden, visible or locked")
        result["state"] = data["state"]
    if "flags" in data:
        values = data["flags"]
        if not isinstance(values, list) or not 1 <= len(values) <= FLAG_COUNT_LIMIT:
            fail(f"Provide between 1 and {FLAG_COUNT_LIMIT} static flags")
        result["flags"] = []
        for value in values:
            if not isinstance(value, dict):
                fail("Each flag must be an object")
            legacy = set(value) == {"content", "case_insensitive"}
            if not legacy and (set(value) - {"type", "content", "data"} or not {"type", "content"} <= set(value)):
                fail("Each flag requires content/case_insensitive or native type/content/data")
            kind = "static" if legacy else value["type"]
            if kind not in ("static", "regex"):
                fail("Only native static and regex flags are supported")
            content = checked_text(value["content"], "Flag content", FLAG_LIMIT)
            if kind == "static" and content != content.strip():
                fail("Flag content cannot begin or end with whitespace; native submissions trim those edges")
            if legacy and type(value["case_insensitive"]) is not bool:
                fail("Flag case_insensitive must be true or false")
            options = ("case_insensitive" if value["case_insensitive"] else "") if legacy else value.get("data")
            if options is not None:
                options = checked_text(options, "Native flag data", 64, empty=True)
            if kind == "regex":
                try:
                    re.compile(content, re.IGNORECASE if options == "case_insensitive" else 0)
                except re.error:
                    fail("The native regex flag pattern is invalid")
            result["flags"].append({"type": kind, "content": content, "data": options})
    if "hints" in data:
        values = data["hints"]
        if not isinstance(values, list) or len(values) > HINT_COUNT_LIMIT:
            fail(f"Provide a list of at most {HINT_COUNT_LIMIT} hints")
        result["hints"], ids = [], set()
        for value in values:
            if (not isinstance(value, dict) or set(value) - {"id", "title", "content", "cost"}
                    or not {"title", "content", "cost"} <= set(value)):
                fail("Each hint requires title, content and cost, with an optional existing id")
            hint = {"title": checked_text(value["title"], "Hint title", NAME_LIMIT, empty=True),
                    "content": checked_text(value["content"], "Hint content", DESCRIPTION_LIMIT)}
            if type(value["cost"]) is not int or not 0 <= value["cost"] <= 2147483647:
                fail("Hint cost must be a whole number from 0 to 2147483647")
            hint["cost"] = value["cost"]
            if "id" in value:
                hid = value["id"]
                if create or type(hid) is not int or not 1 <= hid <= 2147483647 or hid in ids:
                    fail("Hint IDs must be unique existing hints of the edited challenge")
                hint["id"] = hid
                ids.add(hid)
            result["hints"].append(hint)
    return result


def apply_fields(challenge, data, room=None):
    existing, removed = {}, set()
    if "hints" in data:
        existing = {h.id: h for h in Hints.query.filter_by(challenge_id=challenge.id).all()}
        keep = {h["id"] for h in data["hints"] if "id" in h}
        if not keep <= existing.keys():
            fail("Hint IDs must belong to the edited challenge")
        removed = existing.keys() - keep
        if QuestHintPurchase.query.filter(QuestHintPurchase.hint_id.in_(list(existing))).first():
            fail("Hints with purchase history cannot be changed or removed", 409)
        reason = hint_deletion_reason(removed)
        if reason:
            fail(reason, 409)
    if "source_key" in data:
        source = QuestChallengeSource.query.get(challenge.id)
        if source and (source.source_key != data["source_key"] or source.room_id != room.id):
            fail("An assigned source_key cannot be changed", 409)
        duplicate = QuestChallengeSource.query.filter_by(room_id=room.id, source_key=data["source_key"]).first()
        if duplicate and duplicate.challenge_id != challenge.id:
            fail("This source_key already belongs to another challenge in this room", 409)
        if source is None:
            db.session.add(QuestChallengeSource(challenge_id=challenge.id, room_id=room.id, source_key=data["source_key"]))
    for field in ("name", "description", "state", "value", "attribution", "connection_info", "max_attempts"):
        if field in data:
            setattr(challenge, field, data[field])
    if "flags" in data:
        Flags.query.filter_by(challenge_id=challenge.id).delete(synchronize_session=False)
        for flag in data["flags"]:
            db.session.add(Flags(challenge_id=challenge.id, type=flag["type"], content=flag["content"], data=flag["data"]))
    if "hints" in data:
        for hid in removed:
            db.session.delete(existing[hid])
        for value in data["hints"]:
            hint = existing[value["id"]] if "id" in value else Hints(challenge_id=challenge.id, type="standard")
            for field in ("title", "content", "cost"):
                setattr(hint, field, value[field])
            db.session.add(hint)
    if "tags" in data:
        Tags.query.filter_by(challenge_id=challenge.id).delete(synchronize_session=False)
        db.session.add_all(Tags(challenge_id=challenge.id, value=value) for value in data["tags"])
    if "topics" in data:
        ChallengeTopics.query.filter_by(challenge_id=challenge.id).delete(synchronize_session=False)
        for value in data["topics"]:
            topic = Topics.query.filter_by(value=value).first()
            if topic is None:
                topic = Topics(value=value)
                db.session.add(topic)
                db.session.flush()
            db.session.add(ChallengeTopics(challenge_id=challenge.id, topic_id=topic.id))
    if "solution" in data:
        solution = Solutions.query.filter_by(challenge_id=challenge.id).first()
        if data["solution"] is None:
            if solution:
                unknown = deletion_dependents({"solutions", "files", "quest_assets"})
                if unknown:
                    fail("Additional plugin tables need a cleanup plan: " + ", ".join(unknown), 409)
                file_ids = [fid for (fid,) in db.session.query(SolutionFiles.id).filter_by(solution_id=solution.id)]
                QuestAsset.query.filter(QuestAsset.file_id.in_(file_ids)).delete(synchronize_session=False)
                SolutionFiles.query.filter_by(solution_id=solution.id).delete(synchronize_session=False)
                db.session.delete(solution)
        else:
            if solution is None:
                solution = Solutions(challenge_id=challenge.id)
                db.session.add(solution)
            solution.content, solution.state = data["solution"]["content"], data["solution"]["state"]


def locked_event():
    e = event(lock=True)
    # A role revoke can commit while a request waits for the event lock.
    if not can_manage_rooms():
        fail("Room management access required", 403)
    header = request.headers.get("Authorization")
    if header:
        # Native authentication ran before this lock. Recheck revocation/expiry
        # without its preset helper, which can create/commit a missing account.
        token = header.split(" ", 1)[-1]
        preset = get_app_config("PRESET_ADMIN_TOKEN")
        normal = UserTokens.query.filter_by(value=token, user_id=session.get("id")).first()
        from .lifecycle import current_user
        actor = current_user()
        valid_preset = isinstance(preset, str) and hmac.compare_digest(token, preset) and actor is not None and actor.type == "admin"
        if not request.is_json or not (valid_preset or (normal and normal.expiration and normal.expiration > datetime.utcnow())):
            fail("Your access token is invalid or expired", 401)
    return e


def deletion_dependents(roots=None):
    from sqlalchemy import inspect
    from .rounds import KNOWN_TABLES
    inspector = inspect(db.session.connection())
    references = {name: {fk["referred_table"] for fk in inspector.get_foreign_keys(name)}
                  for name in inspector.get_table_names()}
    affected = set(roots) if roots is not None else {"challenges", "flags", "hints", "solutions", "files", "tags", "challenge_topics",
                "comments", "quest_steps", "quest_assets", "quest_challenge_sources"}
    while True:
        expanded = affected | {name for name, refs in references.items() if refs & affected}
        if expanded == affected:
            return sorted(affected - KNOWN_TABLES)
        affected = expanded


def unknown_deletion_reason():
    unknown = deletion_dependents()
    return ("Additional plugin tables need an administrator-reviewed cleanup plan before deletion: "
            + ", ".join(unknown)) if unknown else None


def remove_challenges(ids):
    """Only called after all topology/history/dependency checks. Never commits."""
    if not ids:
        return
    solution_ids = [sid for (sid,) in db.session.query(Solutions.id).filter(Solutions.challenge_id.in_(ids))]
    QuestAsset.query.filter(QuestAsset.challenge_id.in_(ids)).delete(synchronize_session=False)
    QuestChallengeSource.query.filter(QuestChallengeSource.challenge_id.in_(ids)).delete(synchronize_session=False)
    SolutionFiles.query.filter(SolutionFiles.solution_id.in_(solution_ids)).delete(synchronize_session=False)
    ChallengeFiles.query.filter(ChallengeFiles.challenge_id.in_(ids)).delete(synchronize_session=False)
    for model in (Flags, Hints, Solutions, Tags, ChallengeTopics, ChallengeComments):
        model.query.filter(model.challenge_id.in_(ids)).delete(synchronize_session=False)
    Challenges.query.filter(Challenges.id.in_(ids)).update({"next_id": None}, synchronize_session=False)
    Challenges.query.filter(Challenges.id.in_(ids)).delete(synchronize_session=False)


@authoring_api.route("/manage/capabilities")
@room_manager
def capabilities():
    return success({"automation_version": 1,
                    "features": ["challenge_metadata", "source_keys", "owned_assets", "atomic_room_clear"],
                    "asset_upload": {"encoding": "base64", "max_bytes": 5242880,
                                     "kinds": ["inline", "attachment", "solution"]}})


@authoring_api.route("/manage/rooms/<slug>/challenges", methods=["DELETE"])
@room_manager
def clear_room(slug):
    data = body()
    if set(data) != {"confirm"} or data["confirm"] is not True:
        fail("Confirm room challenge removal with confirm: true")
    e = locked_event()
    room = room_by_slug(slug)
    ids = [step.challenge_id for step in room_steps(room)]
    reason = topology_reason(room, e)
    if reason:
        fail(reason, 409)
    reason = unknown_deletion_reason()
    if reason:
        fail(reason, 409)
    hint_ids = {hid for (hid,) in db.session.query(Hints.id).filter(Hints.challenge_id.in_(ids))}
    for cid in ids:
        challenge = member_challenge(room, cid)
        reason = mutation_reason(room, challenge, e, allow_native_flags=True) or outside_dependency(room, challenge, hint_ids)
        if reason:
            fail(reason, 409)
    set_challenge_order(room, [])
    remove_challenges(ids)
    audit("challenge.clear", {"room_slug": slug, "challenge_ids": ids})
    db.session.commit()
    clear_challenges()
    return success({"deleted_ids": ids, "room_slug": slug})


@authoring_api.route("/manage/rooms/<slug>/challenges", methods=["GET"])
@room_manager
def workspace(slug):
    room, e = room_by_slug(slug), event()
    reason = topology_reason(room, e)
    deletion_blocker = unknown_deletion_reason() if reason is None else None
    unmapped = QuestChallengeSource.query.filter(QuestChallengeSource.room_id == room.id,
        ~QuestChallengeSource.challenge_id.in_(db.session.query(QuestStep.challenge_id))).order_by(QuestChallengeSource.challenge_id).all()
    return success({"challenges": [editable(room, member_challenge(room, s.challenge_id), e, deletion_blocker=deletion_blocker) for s in room_steps(room)],
                    "unmapped_catalog": [editable(room, challenge, e, mapped=False, deletion_blocker=deletion_blocker) for source in unmapped
                                         if (challenge := Challenges.query.get(source.challenge_id)) is not None],
                    "can_create": reason is None, "create_reason": reason})


@authoring_api.route("/manage/rooms/<slug>/challenges", methods=["POST"])
@room_manager
def create_challenge(slug):
    data = checked_payload(body(), create=True)
    e = locked_event()
    room = room_by_slug(slug)
    reason = topology_reason(room, e)
    if reason:
        fail(reason, 409)
    ids = [s.challenge_id for s in room_steps(room)]
    challenge = Challenges(name=data["name"], description=data["description"],
                           state=data["state"], type="standard", logic="any", value=100,
                           category=room.name)
    db.session.add(challenge)
    db.session.flush()
    apply_fields(challenge, data, room)
    set_challenge_order(room, ids + [challenge.id])
    audit("challenge.create", {"room_slug": slug, "challenge_id": challenge.id})
    db.session.commit()
    clear_challenges()
    return success(editable(room, challenge, e))


@authoring_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>", methods=["PATCH"])
@room_manager
def update_challenge(slug, challenge_id):
    data = checked_payload(body())
    e = locked_event()
    room = room_by_slug(slug)
    challenge = member_challenge(room, challenge_id)
    reason = mutation_reason(room, challenge, e, allow_native_flags=True)
    if reason:
        fail(reason, 409)
    apply_fields(challenge, data, room)
    audit("challenge.update", {"room_slug": slug, "challenge_id": challenge.id, "fields": sorted(data)})
    db.session.commit()
    clear_challenges()
    return success(editable(room, challenge, e))


@authoring_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>", methods=["DELETE"])
@room_manager
def delete_challenge(slug, challenge_id):
    e = locked_event()
    room = room_by_slug(slug)
    challenge = member_challenge(room, challenge_id)
    reason = mutation_reason(room, challenge, e, allow_native_flags=True) or topology_reason(room, e) or outside_dependency(room, challenge)
    if reason:
        fail(reason, 409)
    reason = unknown_deletion_reason()
    if reason:
        fail(reason, 409)
    set_challenge_order(room, [s.challenge_id for s in room_steps(room) if s.challenge_id != challenge_id])
    # Solution files lack ON DELETE CASCADE upstream. Other owned metadata
    # cascades in the same database transaction; never call native delete(),
    # which commits and deletes submission history along the way.
    remove_challenges([challenge_id])
    audit("challenge.delete", {"room_slug": slug, "challenge_id": challenge_id})
    db.session.commit()
    clear_challenges()
    return success({"id": challenge_id, "deleted": True})


@authoring_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>/preview", methods=["POST"])
@room_manager
def preview_challenge(slug, challenge_id):
    data = body()
    if set(data) != {"submission"}:
        fail("Provide only a submission for preview")
    submission = checked_text(data["submission"], "Preview submission", FLAG_LIMIT, empty=True)
    locked_event()
    room = room_by_slug(slug)
    challenge = member_challenge(room, challenge_id)
    expected = request.headers.get("X-Quest-Preview-Revision")
    if expected is not None:
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            fail("Use a valid saved-content preview revision")
        from .preview import source
        saved_room = next(r for r in source()["rooms"] if r["id"] == room.id)
        if not hmac.compare_digest(expected, saved_room["revision"]):
            fail("Saved room content changed. Reload the preview before checking another answer.", 409)
    flags = flag_rows(challenge)
    reason = unsupported_reason(challenge, flags, allow_native_flags=True)
    if reason:
        fail(reason, 409)
    # Native standard attempts strip submission edges then compare each flag.
    # Calling compare directly avoids attempts, logs, unlocks and awards.
    from CTFd.plugins.flags import FlagException
    try:
        correct = any(get_flag_class(flag.type).compare(flag, submission.strip()) for flag in flags)
    except FlagException:
        fail("The saved native flag pattern is invalid", 409)
    return success({"status": "correct" if correct else "incorrect",
                    "message": "Correct" if correct else "Incorrect"})
