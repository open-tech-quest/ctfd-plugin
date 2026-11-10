"""Session-authenticated participant and organizer API."""
import csv
import io
import re
import math
import json
import hmac
from datetime import timezone
from functools import wraps
from urllib.parse import urlsplit
from flask import Blueprint, Response, g, jsonify, request, session
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import IntegrityError
from CTFd.cache import cache, clear_challenges, clear_standings, clear_user_session, clear_team_session
from CTFd.models import Challenges, Configs, Teams, Users, db
from CTFd.utils import _get_config
from CTFd.utils.dates import ctftime
from CTFd.utils.user import is_admin
from .models import (QuestEvent, QuestRoom, QuestStep, QuestStart, QuestPause,
                     QuestExclusion, QuestCorrection, QuestAudit, QuestReadiness, QuestTeamName)
from .service import (event, event_data, now, iso, parse_time, released,
                      room_data, room_steps, participant_steps, standings, Projection, projection)

from . import demo, onboarding, registration, roles, lifecycle, projector, rounds, archive
from .models import QuestMaintainer
from .lifecycle import current_user as get_current_user

api = Blueprint("quest_api", __name__)


class QuestError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def fail(message, status=400):
    raise QuestError(message, status)


def success(data=None):
    return jsonify(success=True, data=data)


@api.errorhandler(QuestError)
def error(exc):
    db.session.rollback()
    return jsonify(success=False, error=exc.message), exc.status


@api.errorhandler(onboarding.OnboardingError)
@api.errorhandler(projector.ProjectorError)
@api.errorhandler(rounds.RoundError)
def onboarding_error(exc):
    db.session.rollback()
    return jsonify(success=False, error=exc.message), exc.status


@api.errorhandler(HTTPException)
def http_error(exc):
    db.session.rollback()
    return jsonify(success=False, error=exc.description), exc.code


@api.errorhandler(IntegrityError)
def conflict(exc):
    db.session.rollback()
    return jsonify(success=False, error="The request conflicts with another change. Refresh and try again."), 409


def native_config(key, value):
    """Stage native lifecycle config in the same transaction as quest history."""
    row = Configs.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(Configs(key=key, value=value))


def clear_native_config():
    for key in ("start", "end", "paused"):
        cache.delete_memoized(_get_config, key)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail("A JSON object is required")
    return data


def text(value, label, maximum=512, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        fail(f"{label} must be {'a string' if empty else 'non-empty'} (maximum {maximum} characters)")
    return value.strip()


def integer(value, label, lower=1, upper=2147483647):
    if type(value) is not int or not lower <= value <= upper:
        fail(f"{label} must be an integer between {lower} and {upper}")
    return value


def boolean(value, label):
    if type(value) is not bool:
        fail(f"{label} must be true or false")
    return value


def timestamp(value):
    try:
        return parse_time(value)
    except (ValueError, TypeError, OverflowError):
        fail("Use an ISO 8601 timestamp with UTC offset")


def reason(data):
    return text(data.get("reason"), "Reason", 1000)


def audit(action, detail):
    user = get_current_user()
    db.session.add(QuestAudit(actor_id=user.id if user else None, action=action, detail=detail))


def admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        actor = lifecycle.current_user()
        if not actor:
            fail("Sign in as an administrator", 401)
        if actor.type != "admin" or actor.banned:
            fail("Administrator access required", 403)
        g.quest_administrator = True
        return fn(*args, **kwargs)
    return wrapper


def draft(e):
    if e.status != "draft":
        fail("This setting is editable only in draft", 409)


def room_by_slug(slug):
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9-]{1,32}", slug) is None:
        fail("A valid room slug is required")
    room = QuestRoom.query.filter_by(slug=slug).first()
    if room is None:
        fail("Room not found", 404)
    return room


def viewer():
    user = get_current_user()
    if not user:
        return {"authenticated": False, "id": None, "name": None, "team_id": None, "team_name": None, "admin": False,
                "role": "guest", "can_manage_rooms": False, "can_manage_users": False}
    administrator = user.type == "admin"
    show = administrator or released()
    team = Teams.query.get(user.team_id) if user.team_id and show else None
    return {"authenticated": True, "id": user.id, "name": user.name,
            "team_id": user.team_id if show else None, "team_name": team.name if team else None, "admin": administrator,
            "role": roles.role(user), "can_manage_rooms": roles.can_manage_rooms(user),
            "can_manage_users": bool(administrator and not user.banned)}


def current_rooms(e=None):
    user = get_current_user()
    view = projection(e, team_id=(user.team_id if user else None) or 0)
    return [view.room(room, user.team_id if user else None, is_admin()) for room in view.rooms]


@api.route("/bootstrap")
def bootstrap():
    e, user = event(), get_current_user()
    if (request.headers.get("Sec-Fetch-Dest") == "empty"
            and request.headers.get("X-Quest-Attendance-UI") != "1"
            and user and onboarding.can_confirm(user, e)
            and QuestReadiness.query.get(user.id) is None):
        fail("Attendance check-in is open. Refresh this page once to load the attendance confirmation.", 409)
    data = sync_data(e, user)
    data["rooms"] = current_rooms(e)
    response = success(data)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def participant_data(user, e):
    from .read_cache import cached, versions
    stamps = versions(user.team_id or 0, user.id)
    at = now()
    boundaries = [released(e), bool(e.start_at and at >= e.start_at),
                  bool(e.end_at and at >= e.end_at)]
    return cached("onboarding", user.id,
                  [stamps["roster"], stamps["event"], stamps["user"], boundaries],
                  lambda: onboarding.participant(user, e))


def sync_data(e=None, user=None):
    """Live state without rebuilding room content or unchanged rosters."""
    from .read_cache import public_revisions, token
    e = e or event()
    user = user if user is not None else get_current_user()
    at = now()
    boundaries = [released(e), bool(e.start_at and at >= e.start_at),
                  bool(e.end_at and at >= e.end_at)]
    team_id = (user.team_id or 0) if user else None
    revisions = {key: token([value, boundaries])
                 for key, value in public_revisions(team_id, user.id if user else None).items()}
    participant = None
    if user is not None and onboarding.eligible(user):
        participant = participant_data(user, e)
    return {"event": event_data(e), "viewer": viewer(),
            "registration": registration.status(), "csrfNonce": session.get("nonce"),
            "onboarding": participant, "revisions": revisions}


@api.route("/sync")
def sync_view():
    response = success(sync_data())
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api.route("/session")
def session_view():
    return success(viewer())


@api.route("/rooms")
def rooms():
    return success(current_rooms())


@api.route("/rooms/<slug>")
def room_view(slug):
    user = get_current_user()
    return success(room_data(room_by_slug(slug), user.team_id if user else None, is_admin()))


@api.route("/rooms/<slug>/start", methods=["POST"])
def start_room(slug):
    e = event(lock=True)
    user = lifecycle.current_user()
    if not user:
        fail("Sign in to start a room", 401)
    if not onboarding.eligible(user):
        fail("Only eligible participants can start rooms", 403)
    if not user.team_id:
        fail("An organizer must assign you to a team", 403)
    if e.status != "live" or not ctftime() or (e.end_at and now() >= e.end_at) or not released(e):
        fail("Rooms can start only while the event is live and rosters are released", 409)
    room = room_by_slug(slug)
    start = QuestStart.query.filter_by(team_id=user.team_id, room_id=room.id).first()
    if start is None:
        steps = participant_steps(room.id)
        if not steps or Challenges.query.get(steps[0].challenge_id).state != "visible":
            fail("This room has no available first challenge yet", 409)
        db.session.add(QuestStart(team_id=user.team_id, room_id=room.id, started_at=now()))
        # Unique constraint and event row lock protect teammates and workers.
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if not QuestStart.query.filter_by(team_id=user.team_id, room_id=room.id).first():
                raise
    else:
        db.session.commit()
    return success(room_data(room, user.team_id, is_admin()))


@api.route("/standings")
def standings_view():
    return success(standings(public=True))


@api.route("/projector")
def projector_view():
    return success(projector.serialize(event().projector, now()))


def team_data(team, readiness=None, name_locks=None):
    readiness = readiness if readiness is not None else {row.user_id: row for row in QuestReadiness.query.filter(QuestReadiness.user_id.in_([u.id for u in team.members])).all()}
    return {"id": team.id, "name": team.name, "user_ids": [u.id for u in team.members],
            "hidden": bool(team.hidden), "banned": bool(team.banned),
            "excluded": QuestExclusion.query.get(team.id) is not None,
            "name_locked": team.id in name_locks if name_locks is not None else onboarding.is_locked(team.id),
            "ready_count": sum(u.id in readiness for u in team.members)}


@api.route("/admin/state")
@admin
def admin_state():
    mapped = {s.challenge_id: s.room_id for s in QuestStep.query.all()}
    room_slugs = {r.id: r.slug for r in QuestRoom.query.all()}
    readiness = {row.user_id: row for row in QuestReadiness.query.all()}
    name_locks = {row.team_id for row in QuestTeamName.query.filter_by(name_locked=True).all()}
    return success({"event": event_data(), "rooms": current_rooms(),
                    "available_challenges": [{"id": c.id, "name": c.name, "state": c.state, "room_slug": room_slugs.get(mapped.get(c.id))} for c in Challenges.query.order_by(Challenges.id).all()],
                    "teams": [team_data(t, readiness, name_locks) for t in Teams.query.order_by(Teams.id).all()],
                    "participants": [{"id": u.id, "name": u.name, "email": u.email, "team_id": u.team_id,
                                      "ready_at": iso(readiness[u.id].ready_at) if u.id in readiness else None,
                                      "late": bool(u.id in readiness and readiness[u.id].late),
                                      "banned": bool(u.banned)} for u in Users.query.filter_by(type="user").order_by(Users.id).all() if not roles.is_maintainer(u)],
                    "audit": [{"id": a.id, "actor_id": a.actor_id, "action": a.action, "detail": a.detail, "created_at": iso(a.created_at)} for a in QuestAudit.query.filter(QuestAudit.action != rounds.ARCHIVE_ACTION).order_by(QuestAudit.id.desc()).limit(200).all()],
                    "standings": standings(), "projector": projector.serialize(event().projector, now()), "demo": demo.status(event()), "onboarding": onboarding.summary(event()), "registration": registration.status()})


@api.route("/admin/event", methods=["PATCH"])
@admin
def configure_event():
    data, e = body(), event(lock=True)
    allowed = {"name", "start_at", "end_at", "rosters_release_at", "room_clock", "overall_clock"}
    if set(data) - allowed:
        fail("Unknown event setting")
    if any(k in data for k in ("start_at", "end_at", "room_clock", "overall_clock")):
        draft(e)
    if "name" in data:
        e.name = text(data["name"], "Event name", 128)
    for key in ("start_at", "end_at", "rosters_release_at"):
        if key in data:
            setattr(e, key, timestamp(data[key]))
    for key, allowed_values in (("room_clock", ("explicit", "event")), ("overall_clock", ("event", "team"))):
        if key in data:
            if data[key] not in allowed_values:
                fail(f"Invalid {key}")
            setattr(e, key, data[key])
    if e.end_at and (not e.start_at or e.end_at <= e.start_at):
        fail("Event end must be after its configured start")
    audit("event.configure", data)
    db.session.commit()
    return success(event_data(e))


@api.route("/admin/event/action", methods=["POST"])
@admin
def event_action():
    data, e = body(), event(lock=True)
    action, at = data.get("action"), now()
    if action == "start":
        draft(e)
        blocked = onboarding.start_reason(e)
        if blocked:
            fail(blocked, 409)
        if e.start_at and e.start_at > at:
            fail("The configured start is still in the future", 409)
        if e.end_at and e.end_at <= at:
            fail("The configured end has already passed", 409)
        e.start_at = e.start_at or at
        e.status = "live"
        native_config("start", int(e.start_at.replace(tzinfo=timezone.utc).timestamp()))
        native_config("end", math.ceil(e.end_at.replace(tzinfo=timezone.utc).timestamp()) if e.end_at else None)
        native_config("paused", False)
    elif action == "pause":
        if e.status != "live":
            fail("Only a live event can pause", 409)
        e.status, e.paused_at = "paused", at
        db.session.add(QuestPause(started_at=at))
        native_config("paused", True)
    elif action == "resume":
        if e.status != "paused":
            fail("Only a paused event can resume", 409)
        if e.end_at and at >= e.end_at:
            fail("The configured cutoff has passed; close the event", 409)
        for pause in QuestPause.query.filter_by(ended_at=None).all():
            pause.ended_at = at
        e.status, e.paused_at = "live", None
        native_config("paused", False)
    elif action == "close":
        if e.status not in ("live", "paused"):
            fail("Only live or paused events can close", 409)
        cutoff = min(at, e.end_at) if e.end_at else at
        for pause in QuestPause.query.filter_by(ended_at=None).all():
            pause.ended_at = cutoff
        e.status, e.end_at, e.paused_at = "closed", cutoff, None
        onboarding.change_attendance(e, False, "event.close")
        native_config("end", int(cutoff.replace(tzinfo=timezone.utc).timestamp()))
        native_config("paused", True)
    elif action == "publish":
        if e.status != "closed":
            fail("Close the event before publishing", 409)
        e.snapshot = standings(e)
        e.status = "published"
    elif action == "unpublish":
        if e.status != "published":
            fail("Results are not published", 409)
        reason(data)
        e.status, e.snapshot = "closed", None
    elif action == "reopen":
        if e.status != "closed":
            fail("Unpublish results and close before reopening", 409)
        reason(data)
        # The closed interval is excluded when organizers resume play.
        db.session.add(QuestPause(started_at=e.end_at))
        e.status, e.paused_at, e.end_at = "paused", e.end_at, None
        native_config("end", None)
        native_config("paused", True)
    else:
        fail("Unknown event action")
    audit("event." + action, {"reason": data.get("reason"), "at": iso(at)})
    db.session.commit()
    clear_native_config()
    return success(event_data(e))


def asset_url(value):
    if value is None or value == "":
        return None
    value = text(value, "Asset URL", 2048)
    if any(ord(c) < 33 for c in value) or "\\" in value:
        fail("Asset URLs may not contain whitespace or backslashes")
    # Local curated static assets only: no remote tracking, script schemes or
    # access-controlled challenge files rendered as presentation assets.
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        fail("Use a local static asset URL")
    if not value.startswith("/plugins/open_tech_quest/assets/") or any(part in ("..", ".") for part in value.split("/")) or "%" in value:
        fail("Use an asset under /plugins/open_tech_quest/assets/")
    return value


def room_theme(value, existing=None):
    if not isinstance(value, dict) or set(value) - {"accent", "logo_url", "hero_url", "layout"}:
        fail("Invalid room theme")
    result = dict(existing or {"layout": "generic", "accent": "#00a6b2", "logo_url": None, "hero_url": None})
    for key, item in value.items():
        if key.endswith("_url"):
            result[key] = asset_url(item)
        elif key == "accent":
            if not isinstance(item, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", item) is None:
                fail("Accent must be a six-digit hex color")
            result[key] = item
        else:
            if item not in ("openshift", "ansible", "rhel", "security", "ai", "generic"):
                fail("Unknown room layout")
            result[key] = item
    return result


@api.route("/admin/rooms/<slug>", methods=["PATCH"])
@roles.room_manager
def configure_room(slug):
    data, e = body(), event(lock=True)
    if not roles.can_manage_rooms():
        fail("Room management access required", 403)
    room = room_by_slug(slug)
    if set(data) - {"name", "subtitle", "description", "theme", "challenge_ids", "position"}:
        fail("Unknown room setting")
    for field, maximum in (("name", 80), ("subtitle", 160), ("description", 10000)):
        if field in data:
            setattr(room, field, text(data[field], field, maximum, empty=field != "name"))
    if "position" in data:
        draft(e)
        room.position = integer(data["position"], "Position", 0, 100)
    if "theme" in data:
        room.theme = room_theme(data["theme"], room.theme)
    if "challenge_ids" in data:
        ids = data["challenge_ids"]
        if not isinstance(ids, list) or any(type(x) is not int or x < 1 for x in ids) or len(ids) != len(set(ids)):
            fail("challenge_ids must be a list of unique challenge IDs")
        challenges = {c.id: c for c in Challenges.query.filter(Challenges.id.in_(ids)).all()}
        if set(challenges) != set(ids):
            fail("One or more challenges do not exist")
        if QuestStep.query.filter(QuestStep.room_id != room.id, QuestStep.challenge_id.in_(ids)).first():
            fail("A challenge belongs to another room", 409)
        if ids != [step.challenge_id for step in room_steps(room)]:
            draft(e)
            from .room_topology import set_challenge_order
            set_challenge_order(room, ids)
    audit("room.configure", {"room_slug": slug, "changes": data})
    db.session.commit()
    clear_challenges()
    return success(room_data(room, admin=True))


def eligible_users():
    ready = {row.user_id for row in QuestReadiness.query.all()}
    return [u for u in Users.query.filter_by(type="user", banned=False, team_id=None).order_by(Users.id).all() if onboarding.eligible(u) and u.id in ready]


def unique_name(name, excluding=None):
    name = text(name, "Team name", 128)
    if any(t.id != excluding and t.name.casefold() == name.casefold() for t in Teams.query.all()):
        fail("Team names must be unique", 409)
    return name


def user_ids(data):
    ids = data.get("user_ids")
    if not isinstance(ids, list) or any(type(i) is not int or i < 1 for i in ids) or len(ids) != len(set(ids)):
        fail("user_ids must contain unique user IDs")
    return ids


@api.route("/admin/teams/allocate", methods=["POST"])
@admin
def allocate():
    data, e = body(), event(lock=True)
    draft(e)
    state = onboarding.settings()
    if state.phase != "check_in":
        fail("Initial allocation is available only while check-in is open", 409)
    if state.attendance_open:
        fail("Close attendance capture before previewing or applying ready-only allocation", 409)
    preview = boolean(data.get("preview", True), "preview")
    users = eligible_users()
    fingerprint = onboarding.fingerprint(e)
    size = state.team_size
    if "team_size" in data and integer(data["team_size"], "Team size", 1, 50) != size:
        fail("Save the maximum team size in onboarding settings before previewing allocation", 409)
    groups = []
    if users:
        from .onboarding_math import optimize
        order = onboarding.random_order([user.id for user in users], fingerprint)
        proposal, _ = optimize(order, {}, [], size, reserved_names=[team.name for team in Teams.query.all()], order=order)
        groups = [{"name": group["name"], "user_ids": group["user_ids"]} for group in proposal]
    if preview:
        return success({"groups": groups, "fingerprint": fingerprint})
    supplied = data.get("fingerprint")
    if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None or not __import__("hmac").compare_digest(supplied, fingerprint):
        fail("Accounts or settings changed, or your preview is outdated. Preview allocation again before applying it.", 409)
    if not groups:
        fail("Preview allocation and supply its groups")
    if data.get("groups") != groups:
        fail("Use the unchanged ready-only allocation preview; refresh the proposal before applying", 409)
    ids, names, validated = [], set(), []
    for group in groups:
        if not isinstance(group, dict):
            fail("Each group must contain name and user_ids")
        name, members = unique_name(group.get("name")), user_ids(group)
        if not members or len(members) > size or name.casefold() in names:
            fail(f"Groups require unique names and between 1 and {size} members")
        names.add(name.casefold())
        ids.extend(members)
        validated.append((name, members))
    if len(ids) != len(set(ids)) or set(ids) != {u.id for u in users}:
        fail("Eligible participants changed. Preview a complete allocation again.", 409)
    users_by_id = {u.id: u for u in users}
    for name, members in validated:
        team = Teams(name=name, captain_id=members[0])
        db.session.add(team)
        db.session.flush()
        onboarding.generated_identity(team.id)
        for uid in members:
            users_by_id[uid].team_id = team.id
    audit("teams.allocate", {"groups": groups})
    db.session.commit()
    for uid in ids:
        clear_user_session(uid)
    clear_standings()
    return success({"groups": groups})


@api.route("/admin/teams/<int:team_id>", methods=["PATCH"])
@admin
def configure_team(team_id):
    data, e = body(), event(lock=True)
    team = Teams.query.get(team_id)
    if team is None:
        fail("Team not found", 404)
    if set(data) - {"name", "excluded", "reason", "user_ids"}:
        fail("Unknown team setting")
    if e.status == "published":
        fail("Unpublish results before changing teams", 409)
    if "name" in data:
        name = text(data["name"], "Team name", 128)
        if name != team.name:
            draft(e)
            if onboarding.settings().phase in ("naming", "finalized") or onboarding.is_locked(team.id):
                reason(data)
            onboarding.lock_name(team.id, get_current_user().id)
            team.name = unique_name(name, team.id)
    changed_users = set()
    if "user_ids" in data:
        draft(e)
        ids = user_ids(data)
        state = onboarding.settings()
        current_ids = {u.id for u in team.members}
        if state.phase == "finalized" and set(ids) != current_ids:
            fail("Finalized team rosters cannot be changed", 409)
        if len(ids) > state.team_size:
            fail("Team exceeds the configured maximum size", 409)
        users = Users.query.filter(Users.id.in_(ids)).all()
        if len(users) != len(ids) or any((not onboarding.eligible(u) and u.id not in current_ids) or (u.team_id and u.team_id != team_id) for u in users):
            fail("New members must be eligible unassigned participants; existing members may be retained")
        if state.phase == "naming" and set(ids) != current_ids:
            reason(data)
            removed, added = current_ids - set(ids), set(ids) - current_ids
            if any(onboarding.eligible(Users.query.get(uid)) for uid in removed) or any(QuestReadiness.query.get(uid) is None for uid in added):
                fail("After optimization, only ready late arrivals may be added and ineligible existing members removed; ready eligible members cannot be moved", 409)
            if added and (team.banned or team.hidden):
                fail("Late arrivals require a visible non-banned team", 409)
        for user in team.members:
            changed_users.add(user.id)
            user.team_id = None
        for user in users:
            changed_users.add(user.id)
            user.team_id = team.id
        team.captain_id = ids[0] if ids else None
    if "excluded" in data:
        excluded = boolean(data["excluded"], "excluded")
        row = QuestExclusion.query.get(team.id)
        if excluded != (row is not None):
            explanation = reason(data)
            if excluded:
                db.session.add(QuestExclusion(team_id=team.id, reason=explanation))
            else:
                db.session.delete(row)
    audit("team.configure", {"team_id": team.id, "changes": data})
    db.session.commit()
    for uid in changed_users:
        clear_user_session(uid)
    clear_standings()
    clear_team_session(team_id)
    db.session.expire_all()
    return success(team_data(Teams.query.get(team_id)))


@api.route("/admin/projector", methods=["PATCH"])
@admin
def configure_projector():
    data, e = body(), event(lock=True)
    at = now()
    config = projector.configure(e.projector, data, at)
    slug = config["room_slug"]
    if config["view"] == "room":
        room_by_slug(slug)
    elif slug is not None:
        room_by_slug(slug)
    e.projector = config
    audit("projector.configure", e.projector)
    db.session.commit()
    return success(projector.serialize(e.projector, at))


@api.route("/admin/corrections", methods=["POST"])
@admin
def correction():
    data, e = body(), event(lock=True)
    if e.status not in ("live", "paused", "closed"):
        fail("Corrections require a started, unpublished event", 409)
    team_id = integer(data.get("team_id"), "Team ID")
    if Teams.query.get(team_id) is None:
        fail("Team not found", 404)
    room = room_by_slug(data.get("room_slug"))
    adjustment = integer(data.get("adjustment_ms"), "Adjustment", -86400000, 86400000)
    explanation = reason(data)
    cumulative = sum(c.adjustment_ms for c in QuestCorrection.query.filter_by(team_id=team_id, room_id=room.id).all()) + adjustment
    if abs(cumulative) > 86400000:
        fail("Cumulative room adjustment cannot exceed 24 hours")
    db.session.add(QuestCorrection(team_id=team_id, room_id=room.id, adjustment_ms=adjustment, reason=explanation))
    audit("ranking.correct", {"team_id": team_id, "room_slug": room.slug, "adjustment_ms": adjustment, "reason": explanation})
    db.session.commit()
    return success({"standings": standings()})


@api.route("/admin/export")
@admin
def export():
    data = standings()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("competition", "team_id", "team_name", "rank", "solved", "total", "active_elapsed_ms",
                     "gross_points", "hint_costs", "net_points", "scoring_version"))
    def safe_name(name):
        return "'" + name if name.startswith(("=", "+", "-", "@", "\t", "\r")) else name
    for team in data["teams"]:
        writer.writerow(("overall", team["id"], safe_name(team["name"]), team["rank"], team["total"], sum(r["total"] for r in data["rooms"]), team["overall_elapsed_ms"],
                         team.get("gross_points", ""), team.get("hint_costs", ""), team.get("points", ""), data.get("scoring_version", 1)))
        for p in team["room_progress"]:
            room = next(r for r in data["rooms"] if r["id"] == p["room_id"])
            writer.writerow((room["slug"], team["id"], safe_name(team["name"]), p["rank"], p["solved"], p["total"], p["milestone_elapsed_ms"],
                             p.get("gross_points", ""), p.get("hint_costs", ""), p.get("points", ""), data.get("scoring_version", 1)))
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=quest-standings.csv"})


@api.route("/admin/demo", methods=["POST"])
@admin
def manage_demo():
    data, e = body(), event(lock=True)
    action = data.get("action")
    if action not in ("load", "unload"):
        fail("Choose demo action load or unload")
    try:
        batch = demo.load(e) if action == "load" else demo.unload(e)
    except demo.DemoConflict as exc:
        fail(str(exc), 409)
    audit("demo." + action, {"batch_id": batch.id, "challenge_ids": batch.challenge_ids})
    db.session.commit()
    clear_challenges()
    clear_standings()
    return success(demo.status(e))


def participant_account():
    user = lifecycle.current_user()
    if user is None:
        fail("Sign in to join the onboarding process", 401)
    if not onboarding.eligible(user):
        fail("Only non-banned participant accounts can check in", 403)
    return user


@api.route("/onboarding")
def onboarding_view():
    return success(participant_data(participant_account(), event()))


@api.route("/onboarding/ready", methods=["POST"])
def ready_for_quest():
    data, e = body(), event(lock=True)
    user = participant_account()
    if data:
        fail("Readiness is recorded using the server clock; no fields are accepted")
    row = QuestReadiness.query.get(user.id)
    state = onboarding.settings()
    if row is None:
        if not onboarding.can_confirm(user, e, state):
            fail("Attendance confirmation is closed. Wait for an organizer to open attendance check-in.", 409)
        row = QuestReadiness(user_id=user.id, ready_at=now(), late=state.phase != "check_in")
        db.session.add(row)
        audit("onboarding.ready", {"user_id": user.id, "late": row.late})
    db.session.commit()
    return success(onboarding.participant(user, e))


@api.route("/onboarding/name", methods=["POST"])
def claim_team_name():
    data, e = body(), event(lock=True)
    user = participant_account()
    if data.get("confirm") is not True:
        fail("Confirm that your team's chosen name will be fixed")
    if e.status != "draft" or onboarding.settings().phase != "naming":
        fail("Team naming is available only after attendance optimization and before finalization", 409)
    if not onboarding.settings().allow_team_naming:
        fail("Participant team naming is disabled by the organizer", 409)
    if not user.team_id or QuestReadiness.query.get(user.id) is None or not released(e):
        fail("You must be a ready member of a released team to choose its name", 403)
    if onboarding.is_locked(user.team_id):
        fail("Your team has already chosen its final name. Ask an organizer for a reasoned correction.", 409)
    team = Teams.query.get(user.team_id)
    if team.banned or team.hidden:
        fail("This team is not eligible for naming", 403)
    name = text(data.get("name"), "Team name", 128)
    if re.fullmatch(r"team\s*\d+", team.name.strip(), re.IGNORECASE) and name.casefold() == team.name.strip().casefold():
        fail("Choose a final team name that differs from the provisional placeholder")
    team.name = unique_name(name, team.id)
    onboarding.lock_name(team.id, user.id)
    audit("onboarding.name", {"team_id": team.id, "name": team.name})
    db.session.commit()
    clear_team_session(team.id)
    return success(onboarding.participant(user, e))


@api.route("/admin/onboarding", methods=["PATCH"])
@admin
def configure_onboarding():
    data, e = body(), event(lock=True)
    draft(e)
    state = onboarding.settings()
    if not data or set(data) - {"team_size", "allow_team_naming"}:
        fail("Supply team_size and/or allow_team_naming")
    if "team_size" in data and state.phase not in ("check_in", "closed"):
        fail("Team capacity is fixed after attendance optimization", 409)
    if "team_size" in data:
        state.team_size = integer(data["team_size"], "Team size", 1, 50)
    if "allow_team_naming" in data:
        if state.phase not in ("check_in", "closed", "naming"):
            fail("Team naming policy is fixed after roster finalization", 409)
        state.allow_team_naming = boolean(data["allow_team_naming"], "allow_team_naming")
        onboarding.apply_name_policy()
    audit("onboarding.configure", data)
    db.session.commit()
    return success(onboarding.summary(e))


@api.route("/admin/onboarding/action", methods=["POST"])
@admin
def onboarding_action():
    data, e = body(), event(lock=True)
    draft(e)
    state = onboarding.settings()
    action = data.get("action")
    if action == "close_checkin":
        if state.phase != "check_in":
            fail("Check-in is not open", 409)
        onboarding.change_attendance(e, False, "close_checkin")
        state.phase = "closed"
    elif action == "reopen_checkin":
        if state.phase != "closed":
            fail("Only closed check-in can reopen before optimization is applied", 409)
        state.phase = "check_in"
    elif action == "finalize":
        if state.phase != "naming":
            fail("Apply the attendance plan and confirm team names before finalization", 409)
        blocked = onboarding.finalization_reason()
        if blocked:
            fail(blocked, 409)
        state.phase, state.finalized_at = "finalized", now()
    elif action == "create_late_team":
        if state.phase != "naming":
            fail("Late arrival teams can be created only during naming", 409)
        explanation = reason(data)
        ids = user_ids(data)
        if not ids or len(ids) > state.team_size:
            fail("Choose ready unassigned participants within team capacity")
        users = Users.query.filter(Users.id.in_(ids)).all()
        if len(users) != len(ids) or any(not onboarding.eligible(u) or u.team_id or QuestReadiness.query.get(u.id) is None for u in users):
            fail("Late arrival teams require ready eligible unassigned participants", 409)
        team = Teams(name=onboarding.generated_name(), captain_id=ids[0])
        db.session.add(team)
        db.session.flush()
        onboarding.generated_identity(team.id)
        for user in users:
            user.team_id = team.id
        audit("onboarding.late_team", {"team_id": team.id, "user_ids": ids, "reason": explanation})
    else:
        fail("Unknown onboarding action")
    audit("onboarding." + action, {"reason": data.get("reason")})
    db.session.commit()
    if action == "create_late_team":
        for uid in ids:
            clear_user_session(uid)
    return success(onboarding.summary(e))


@api.route("/admin/onboarding/optimize", methods=["POST"])
@admin
def optimize_onboarding():
    data, e = body(), event(lock=True)
    if set(data) - {"preview", "fingerprint"}:
        fail("Attendance plans are computed by the server; do not submit edited groups")
    preview = boolean(data.get("preview", True), "preview")
    plan = onboarding.preview(e)
    if preview:
        return success(plan)
    supplied = data.get("fingerprint")
    if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None or not __import__("hmac").compare_digest(supplied, plan["fingerprint"]):
        fail("Attendance, memberships or settings changed. Preview the current plan before applying it.", 409)
    assignments, group_members = {}, {}
    for group in plan["groups"]:
        team = Teams.query.get(group["team_id"]) if group["team_id"] else Teams(name=group["name"])
        if group["team_id"] is None:
            db.session.add(team)
            db.session.flush()
            onboarding.generated_identity(team.id)
        for uid in group["user_ids"]:
            assignments[uid] = team.id
        group_members[team.id] = group["user_ids"]
    users = Users.query.all()
    for user in users:
        user.team_id = assignments.get(user.id)
    for team in Teams.query.all():
        ids = group_members.get(team.id, [])
        team.captain_id = team.captain_id if team.captain_id in ids else (ids[0] if ids else None)
    state = onboarding.settings()
    state.phase, state.applied_at = "naming", now()
    db.session.flush()
    onboarding.apply_name_policy()
    audit("onboarding.optimize", {"fingerprint": plan["fingerprint"], "groups": plan["groups"], "unassigned_user_ids": plan["unassigned_user_ids"]})
    db.session.commit()
    for user in users:
        clear_user_session(user.id)
    for team in Teams.query.all():
        clear_team_session(team.id)
    clear_standings()
    return success(onboarding.summary(e))


@api.route("/admin/onboarding/admit", methods=["POST"])
@admin
def admit_late_arrivals():
    data, e = body(), locked_administrator()
    blocked = onboarding.late_admission_reason(e)
    if blocked:
        fail(blocked, 409)
    common = {"user_ids", "reason", "fingerprint"}
    if set(data) not in (common | {"team_id"}, common | {"new_team_name"}):
        fail("Supply user_ids, reason, fingerprint and exactly one of team_id or new_team_name")
    supplied = data["fingerprint"]
    if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None or not __import__("hmac").compare_digest(supplied, onboarding.fingerprint(e)):
        fail("Accounts, attendance or teams changed. Refresh late admission before confirming placement.", 409)
    explanation = reason(data)
    ids = user_ids(data)
    capacity = onboarding.settings().team_size
    if not ids or len(ids) > capacity:
        fail("Choose ready newcomers within the saved team capacity")
    users = Users.query.filter(Users.id.in_(ids)).all()
    if len(users) != len(ids) or any(not onboarding.eligible(user) or user.team_id is not None
                                   or QuestReadiness.query.get(user.id) is None or lifecycle.personal_activity(user) for user in users):
        fail("Every newcomer must be eligible, ready, unassigned and have no competition history", 409)
    created = "new_team_name" in data
    if created:
        name = unique_name(data["new_team_name"])
        team = Teams(name=name, captain_id=ids[0])
    else:
        team = Teams.query.get(integer(data["team_id"], "Team ID"))
        if team is None:
            fail("Team not found", 404)
        if team.hidden or team.banned or not onboarding.is_locked(team.id):
            fail("Choose a visible, non-banned team with a confirmed final name", 409)
        if len(team.members) + len(ids) > capacity:
            fail("The team does not have enough space within the saved capacity", 409)
    joined_at = now()
    if e.end_at and joined_at >= e.end_at:
        fail("The event cutoff has passed; late attendance and admission are closed.", 409)
    if created:
        actor_id = lifecycle.current_user().id
        db.session.add(team)
        db.session.flush()
        onboarding.lock_name(team.id, actor_id)
    for user in users:
        user.team_id = team.id
    audit("onboarding.admit", {"user_ids": ids, "team_id": team.id, "new_team": created,
                               "reason": explanation, "joined_at": iso(joined_at)})
    db.session.commit()
    for uid in ids:
        clear_user_session(uid)
    clear_team_session(team.id)
    clear_standings()
    return success(onboarding.summary(e))


@api.route("/admin/registration", methods=["PATCH"])
@admin
def configure_registration():
    data = body()
    event(lock=True)
    if set(data) != {"enabled"}:
        fail("Supply only enabled: true or false")
    enabled = boolean(data["enabled"], "enabled")
    previous = registration.visibility()
    value = "public" if enabled else "private"
    native_config(registration.KEY, value)
    audit("registration.configure", {"old": previous, "new": value})
    db.session.commit()
    registration.invalidate()
    return success(registration.status())


@api.route("/admin/attendance", methods=["PATCH"])
@admin
def configure_attendance():
    data = body()
    e = event(lock=True)
    if set(data) != {"enabled"}:
        fail("Supply only enabled: true or false")
    enabled = boolean(data["enabled"], "enabled")
    onboarding.change_attendance(e, enabled, "organizer")
    db.session.commit()
    return success(onboarding.attendance(e))


@api.route("/manage/rooms")
@roles.room_manager
def manage_rooms():
    mapped = {s.challenge_id: s.room_id for s in QuestStep.query.all()}
    room_slugs = {r.id: r.slug for r in QuestRoom.query.all()}
    projection = Projection()
    from .room_topology import topology_reason
    rooms = []
    for room in projection.rooms:
        payload = projection.room(room, None, True)
        blocked = topology_reason(room, projection.event)
        payload.update(can_configure_topology=blocked is None, topology_reason=blocked,
                       can_delete=blocked is None, delete_reason=blocked)
        rooms.append(payload)
    return success({"event": event_data(projection.event),
                    "rooms": rooms,
                    "available_challenges": [{"id": c.id, "name": c.name, "state": c.state,
                                               "room_slug": room_slugs.get(mapped.get(c.id))}
                                              for c in Challenges.query.order_by(Challenges.id).all()]})


@api.route("/manage/rooms", methods=["POST"])
@roles.room_manager
def create_room():
    data, e = body(), event(lock=True)
    if not roles.can_manage_rooms():
        fail("Room management access required", 403)
    draft(e)
    if set(data) - {"slug", "name", "subtitle", "description", "theme"}:
        fail("Unknown room setting")
    slug = data.get("slug")
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9-]{1,32}", slug) is None:
        fail("Room slug must contain 1 to 32 lowercase letters, digits or hyphens")
    if QuestRoom.query.filter_by(slug=slug).first():
        fail("A room already uses this slug", 409)
    room = QuestRoom(slug=slug, name=text(data.get("name"), "Room name", 80),
                     subtitle=text(data.get("subtitle", ""), "Subtitle", 160, empty=True),
                     description=text(data.get("description", ""), "Description", 10000, empty=True),
                     theme=room_theme(data.get("theme", {})),
                     position=max((r.position for r in QuestRoom.query.all()), default=-1)+1)
    db.session.add(room)
    audit("room.create", {"room_slug": slug})
    db.session.commit()
    return success(room_data(room, admin=True))


@api.route("/manage/rooms/<slug>", methods=["DELETE"])
@roles.room_manager
def delete_room(slug):
    data, e = body(), event(lock=True)
    if not roles.can_manage_rooms():
        fail("Room management access required", 403)
    draft(e)
    if set(data) != {"confirm"} or data["confirm"] is not True:
        fail("Confirm room removal with confirm: true")
    room = room_by_slug(slug)
    from .room_topology import set_challenge_order
    set_challenge_order(room, [])
    if (e.projector or {}).get("room_slug") == slug:
        e.projector = projector.remove_room(e.projector, slug)
    from .models import QuestChallengeSource
    QuestChallengeSource.query.filter_by(room_id=room.id).delete(synchronize_session=False)
    db.session.delete(room)
    audit("room.delete", {"room_slug": slug})
    db.session.commit()
    clear_challenges()
    return success({"slug": slug, "deleted": True})


def managed_user(user, e):
    blocked = roles.promotion_reason(user, e)
    team = Teams.query.get(user.team_id) if user.team_id else None
    ready = QuestReadiness.query.get(user.id)
    deletion = lifecycle.delete_reason(user, e)
    access = lifecycle.access_reason(user, e)
    return {"id": user.id, "identity": lifecycle.identity(user), "name": user.name, "email": user.email, "role": roles.role(user),
            "banned": bool(user.banned), "team_id": user.team_id, "team_name": team.name if team else None,
            "ready_at": iso(ready.ready_at) if ready else None,
            "can_change_role": blocked is None, "role_change_reason": blocked,
            "can_delete": deletion is None, "delete_reason": deletion,
            "can_set_active": access is None, "access_reason": access}


@api.after_request
def private_accounts(response):
    if request.path.startswith(("/api/v1/quest/admin/users", "/api/v1/quest/admin/archives", "/api/v1/quest/admin/round")):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def locked_administrator():
    e = event(lock=True)
    if not lifecycle.administrator():
        fail("Administrator access required", 403)
    return e


def identified_user(user_id, data):
    user = Users.query.populate_existing().filter_by(id=user_id).first()
    if user is None:
        fail("User not found", 404)
    if not lifecycle.valid_identity(user, data.get("identity")):
        fail("This account changed or your page is outdated. Refresh Users before trying again.", 409)
    return user


@api.route("/admin/users")
@admin
def managed_users():
    e = event()
    return success({"users": [managed_user(user, e) for user in Users.query.order_by(Users.id).all()]})


@api.route("/admin/users/<int:user_id>/role", methods=["PATCH"])
@admin
def change_user_role(user_id):
    data, e = body(), locked_administrator()
    user = identified_user(user_id, data)
    if set(data) != {"role", "confirm", "identity"} or data["confirm"] is not True or data["role"] not in ("participant", "maintainer"):
        fail("Supply role participant or maintainer, confirm: true and the current identity")
    if user.type != "user":
        fail("Native administrators are managed through CTFd", 409)
    previous = roles.role(user)
    if previous == data["role"]:
        db.session.commit()
        return success(managed_user(user, e))
    old_team = Teams.query.get(user.team_id) if user.team_id else None
    if data["role"] == "maintainer":
        blocked = roles.promotion_reason(user, e)
        if blocked:
            fail(blocked, 409)
        db.session.add(QuestMaintainer(user_id=user.id, previous_hidden=bool(user.hidden)))
        user.hidden = True
    else:
        grant = QuestMaintainer.query.filter_by(user_id=user.id).one()
        user.hidden = grant.previous_hidden
        db.session.delete(grant)
    user.team_id = None
    if old_team and old_team.captain_id == user.id:
        old_team.captain_id = min((member.id for member in old_team.members if member.id != user.id), default=None)
    ready = QuestReadiness.query.get(user.id)
    if ready:
        db.session.delete(ready)
    audit("user.role", {"user_id": user.id, "old": previous, "new": data["role"], "detached_team_id": old_team.id if old_team else None})
    db.session.commit()
    clear_user_session(user.id)
    if old_team:
        clear_team_session(old_team.id)
    clear_standings()
    return success(managed_user(user, e))


@api.route("/admin/users/<int:user_id>/access", methods=["PATCH"])
@admin
def configure_user_access(user_id):
    data, e = body(), locked_administrator()
    user = identified_user(user_id, data)
    if set(data) != {"active", "confirm", "identity"} or data["confirm"] is not True:
        fail("Supply active, confirm: true and the current identity")
    active = boolean(data["active"], "active")
    blocked = lifecycle.access_reason(user, e, changing=bool(user.banned) == active)
    if blocked:
        fail(blocked, 409)
    old_team = user.team_id
    lifecycle.set_active(user, active, e, "quest")
    db.session.commit()
    lifecycle.invalidate(user_id, old_team)
    return success(managed_user(user, e))


@api.route("/admin/users/<int:user_id>", methods=["DELETE"])
@admin
def delete_user(user_id):
    data, e = body(), locked_administrator()
    user = identified_user(user_id, data)
    if set(data) != {"confirm", "identity"} or data["confirm"] is not True:
        fail("Supply confirm: true and the current identity")
    blocked = lifecycle.delete_reason(user, e)
    if blocked:
        fail(blocked, 409)
    tid = lifecycle.safe_delete(user, "quest")
    db.session.commit()
    lifecycle.invalidate(user_id, tid)
    return success({"id": user_id, "deleted": True})


def archive_response(document):
    response = Response(json.dumps(document, ensure_ascii=False, allow_nan=False), mimetype='application/json')
    response.headers['Content-Disposition'] = 'attachment; filename="otq-event-' + document['archive_id'] + '.json"'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def archive_csrf():
    expected, provided = session.get('nonce'), request.headers.get('CSRF-Token')
    if not isinstance(expected, str) or not isinstance(provided, str) or not hmac.compare_digest(expected.encode(), provided.encode()):
        fail('Refresh the organizer page before saving an archive or starting a new round.', 400)


@api.route('/admin/archives')
@admin
def list_event_archives():
    return success({'archives': rounds.stored_archives()})


@api.route('/admin/archives', methods=['POST'])
@admin
def create_event_archive():
    archive_csrf()
    if body() != {}:
        fail('This action takes an empty JSON object')
    e = locked_administrator()
    row, document, summary = rounds.save_archive(e, archive.collect_event(e), get_current_user().id)
    db.session.commit()
    return success({'id': row.id, 'summary': summary, 'document': document})


@api.route('/admin/archives/export')
@admin
def export_current_event_archive():
    e = locked_administrator()
    return archive_response(archive.make_document(archive.collect_event(e)))


@api.route('/admin/archives/<int:archive_id>')
@admin
def download_event_archive(archive_id):
    row = QuestAudit.query.filter_by(id=archive_id, action=rounds.ARCHIVE_ACTION).first()
    if row is None:
        fail('Archive not found', 404)
    return archive_response(row.detail['document'])


@api.route('/admin/round/preview')
@admin
def preview_new_round():
    e = locked_administrator()
    return success(rounds.reset_preview(e, mode=request.args.get('mode', 'new_round'), actor_id=get_current_user().id))


@api.route('/admin/round/reset', methods=['POST'])
@admin
def start_new_round():
    archive_csrf()
    data = body()
    e = locked_administrator()
    return success(rounds.reset_round(e, data, get_current_user().id))
