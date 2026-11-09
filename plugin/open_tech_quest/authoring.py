"""Room-scoped standard challenge editing; native CTFd still owns grading."""
from flask import Blueprint
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from CTFd.cache import clear_challenges
from CTFd.models import Challenges, Flags, SolutionFiles, Solutions, db
from CTFd.plugins.flags import get_flag_class
from .api import (QuestError, audit, body, conflict, error, fail, http_error,
                  room_by_slug, success)
from .roles import can_manage_rooms, room_manager
from .room_topology import activity_reason, set_challenge_order, topology_reason
from .service import event, room_steps
from . import demo

authoring_api = Blueprint("quest_authoring", __name__)
authoring_api.register_error_handler(QuestError, error)
authoring_api.register_error_handler(HTTPException, http_error)
authoring_api.register_error_handler(IntegrityError, conflict)

NAME_LIMIT = 80
DESCRIPTION_LIMIT = 10000
FLAG_LIMIT = 1024
FLAG_COUNT_LIMIT = 20
EDIT_FIELDS = {"name", "description", "state", "flags", "value"}


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


def unsupported_reason(challenge, flags=None):
    if challenge.type != "standard" or challenge.logic != "any":
        return "This challenge uses unsupported native challenge type or flag logic. Manage it in native CTFd."
    if challenge.function != "static":
        return "This challenge uses dynamic scoring. Manage its value in native CTFd."
    flags = flags if flags is not None else flag_rows(challenge)
    if any(f.type != "static" or f.data not in (None, "", "case_insensitive") for f in flags):
        return "This challenge uses unsupported native flag behavior. Manage it in native CTFd."
    return None


def mutation_reason(room, challenge, e):
    if e.status != "draft":
        return "Challenge content is editable only while the event is in Draft."
    if demo.active_batch():
        return "An administrator must remove the loaded demo before real challenge authoring."
    return unsupported_reason(challenge) or activity_reason(room, [challenge.id])


def outside_dependency(room, challenge):
    own_ids = {s.challenge_id for s in room_steps(room)}
    for candidate in Challenges.query.filter(~Challenges.id.in_(list(own_ids))).all():
        requirements = candidate.requirements or {}
        prerequisites = requirements.get("prerequisites", []) if isinstance(requirements, dict) else []
        if candidate.next_id == challenge.id or (isinstance(prerequisites, list) and challenge.id in prerequisites):
            return "A challenge outside this room depends on this challenge. Resolve that dependency in native CTFd before deletion."
    return None


def editable(room, challenge, e=None):
    e = e if e is not None else event()
    flags = flag_rows(challenge)
    unsupported = unsupported_reason(challenge, flags)
    edit_reason = mutation_reason(room, challenge, e)
    delete_reason = edit_reason or topology_reason(room, e) or outside_dependency(room, challenge)
    return {"id": challenge.id, "name": challenge.name, "description": challenge.description or "",
            "state": challenge.state, "type": challenge.type, "value": challenge.value,
            "flags": [] if unsupported else [{"content": f.content, "case_insensitive": f.data == "case_insensitive"} for f in flags],
            "can_edit": edit_reason is None, "edit_reason": edit_reason,
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
        fail("Use only name, description, state, flags and value")
    if create and not (EDIT_FIELDS - {"value"}) <= set(data):
        fail("A new challenge requires name, description, state and flags")
    result = {}
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
            if not isinstance(value, dict) or set(value) != {"content", "case_insensitive"}:
                fail("Each flag requires content and case_insensitive")
            content = checked_text(value["content"], "Flag content", FLAG_LIMIT)
            if content != content.strip():
                fail("Flag content cannot begin or end with whitespace; native submissions trim those edges")
            if type(value["case_insensitive"]) is not bool:
                fail("Flag case_insensitive must be true or false")
            result["flags"].append({"content": content, "case_insensitive": value["case_insensitive"]})
    return result


def apply_fields(challenge, data):
    for field in ("name", "description", "state", "value"):
        if field in data:
            setattr(challenge, field, data[field])
    if "flags" in data:
        Flags.query.filter_by(challenge_id=challenge.id).delete(synchronize_session=False)
        for flag in data["flags"]:
            db.session.add(Flags(challenge_id=challenge.id, type="static", content=flag["content"],
                                 data="case_insensitive" if flag["case_insensitive"] else ""))


def locked_event():
    e = event(lock=True)
    # A role revoke can commit while a request waits for the event lock.
    if not can_manage_rooms():
        fail("Room management access required", 403)
    return e


@authoring_api.route("/manage/rooms/<slug>/challenges", methods=["GET"])
@room_manager
def workspace(slug):
    room, e = room_by_slug(slug), event()
    reason = topology_reason(room, e)
    return success({"challenges": [editable(room, member_challenge(room, s.challenge_id), e) for s in room_steps(room)],
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
    apply_fields(challenge, data)
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
    reason = mutation_reason(room, challenge, e)
    if reason:
        fail(reason, 409)
    apply_fields(challenge, data)
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
    reason = mutation_reason(room, challenge, e) or topology_reason(room, e) or outside_dependency(room, challenge)
    if reason:
        fail(reason, 409)
    set_challenge_order(room, [s.challenge_id for s in room_steps(room) if s.challenge_id != challenge_id])
    # Solution files lack ON DELETE CASCADE upstream. Other owned metadata
    # cascades in the same database transaction; never call native delete(),
    # which commits and deletes submission history along the way.
    solution_ids = db.session.query(Solutions.id).filter_by(challenge_id=challenge_id)
    SolutionFiles.query.filter(SolutionFiles.solution_id.in_(solution_ids)).delete(synchronize_session=False)
    Challenges.query.filter_by(id=challenge_id).delete(synchronize_session=False)
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
    room = room_by_slug(slug)
    challenge = member_challenge(room, challenge_id)
    flags = flag_rows(challenge)
    reason = unsupported_reason(challenge, flags)
    if reason:
        fail(reason, 409)
    # Native standard attempts strip submission edges then compare each flag.
    # Calling compare directly avoids attempts, logs, unlocks and awards.
    correct = any(get_flag_class("static").compare(flag, submission.strip()) for flag in flags)
    return success({"status": "correct" if correct else "incorrect",
                    "message": "Correct" if correct else "Incorrect"})
