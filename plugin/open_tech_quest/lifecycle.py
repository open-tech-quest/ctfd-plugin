"""Account lifecycle policy shared by Quest and native account mutation guards."""
import hashlib
import hmac
import re
from flask import current_app, g, has_request_context, request, session
from CTFd.cache import clear_challenges, clear_standings, clear_team_session, clear_user_session
from CTFd.models import (Awards, Comments, Notifications, Ratings, Solves, Submissions,
                         Teams, Tokens, Tracking, Unlocks, Users, db)
from CTFd.utils.security.signing import hmac as session_hmac
from .models import (QuestAudit, QuestCorrection, QuestMaintainer, QuestReadiness,
                     QuestStart, QuestTeamName)


def current_user():
    uid = session.get("id")
    # Reuse identity only within an ordinary read request. Mutations still
    # re-read after event(lock=True) discards the preceding DB snapshot.
    memoize = has_request_context() and request.method == "GET"
    key = (uid, session.get("hash"))
    previous = getattr(g, "quest_read_user", None) if memoize else None
    if previous is not None and previous[0] == key:
        return previous[1]
    user = Users.query.populate_existing().filter_by(id=uid).first() if uid is not None else None
    if uid is not None and (user is None or (session.get("hash") and session["hash"] != session_hmac(user.password))):
        session.clear()
        user = None
    if memoize:
        g.quest_read_user = (key, user)
    return user


def administrator():
    # A deleted ID can be reused while a signed request waits for the lock.
    # Its old password-bound session must not authorize the replacement account.
    if current_user() is None:
        return False
    actor = db.session.query(Users.id, Users.type, Users.banned).filter(Users.id == session.get("id")).first()
    return bool(actor and actor.type == "admin" and not actor.banned)


def identity(user):
    key = current_app.config["SECRET_KEY"]
    key = key.encode() if isinstance(key, str) else key
    stamp = user.created.isoformat(timespec="microseconds") if user.created else ""
    value = f"quest-user-incarnation:{user.id}:{stamp}:{user.secret or ''}"
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def valid_identity(user, value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None and hmac.compare_digest(identity(user), value)


def personal_activity(user):
    return (any(model.query.filter_by(user_id=user.id).first() for model in (Submissions, Awards, Unlocks, Ratings))
            or QuestTeamName.query.filter_by(claimed_by=user.id).first())


def team_activity(user):
    if not user.team_id:
        return False
    return (QuestStart.query.filter_by(team_id=user.team_id).first()
            or QuestCorrection.query.filter_by(team_id=user.team_id).first()
            or any(model.query.filter_by(team_id=user.team_id).first() for model in (Submissions, Awards, Unlocks))
            or Ratings.query.join(Users, Ratings.user_id == Users.id).filter(Users.team_id == user.team_id).first())


def participation_reason(user, e):
    if personal_activity(user):
        return "This account has competition activity or naming history. Deactivate access to preserve that history."
    if team_activity(user):
        return "This account's team has competition activity history. Deactivate access to preserve team progress."
    if user.team_id:
        from .onboarding import settings
        if e.status != "draft" or settings().phase not in ("check_in", "closed"):
            return "This account belongs to an optimized or frozen roster. Deactivate access instead."
    return None


def delete_reason(user, e, native=False):
    if user.id == session.get("id"):
        return "You cannot delete your own account."
    if user.type == "admin" and not native:
        return "Native administrators are managed through CTFd."
    return participation_reason(user, e)


def contribution_reason(user, e):
    if e.status == "published":
        from .recovery_models import QuestRecoverySolve
        if Solves.query.filter_by(user_id=user.id).first() or QuestRecoverySolve.query.filter_by(user_id=user.id).first():
            return "Unpublish results before changing access for an account with solve history."
    return None


def access_reason(user, e=None, changing=True):
    if user.id == session.get("id"):
        return "You cannot deactivate your own account."
    if user.type == "admin":
        return "Native administrators are managed through CTFd."
    if changing and e is not None:
        return contribution_reason(user, e)
    return None


def detach(user):
    tid = user.team_id
    team = Teams.query.get(tid) if tid else None
    user.team_id = None
    if team and team.captain_id == user.id:
        team.captain_id = min((u.id for u in team.members if u.id != user.id), default=None)
    return tid


def clear_ready(user):
    QuestReadiness.query.filter_by(user_id=user.id).delete(synchronize_session=False)


def record(action, detail):
    db.session.add(QuestAudit(actor_id=session.get("id"), action=action, detail=detail))


def set_active(user, active, e, source):
    if bool(user.banned) == (not active):
        return None
    previous = not bool(user.banned)
    tid = None
    from .onboarding import settings
    if not active and e.status == "draft" and settings().phase in ("check_in", "closed") and not personal_activity(user) and not team_activity(user):
        tid = detach(user)
        clear_ready(user)
    user.banned = not active
    record("user.access", {"user_id": user.id, "old": previous, "new": active, "detached_team_id": tid, "source": source})
    return tid


def remove_grant(user):
    grant = QuestMaintainer.query.filter_by(user_id=user.id).first()
    if grant:
        user.hidden = grant.previous_hidden
        db.session.delete(grant)


def safe_delete(user, source):
    uid = user.id
    tid = detach(user)
    clear_ready(user)
    QuestMaintainer.query.filter_by(user_id=uid).delete(synchronize_session=False)
    Tokens.query.filter_by(user_id=uid).delete(synchronize_session=False)
    Notifications.query.filter_by(user_id=uid).delete(synchronize_session=False)
    Tracking.query.filter_by(user_id=uid).delete(synchronize_session=False)
    # Preserve authored comments and audit rows, including native accounts.
    Comments.query.filter_by(author_id=uid).update({"author_id": None}, synchronize_session=False)
    QuestAudit.query.filter_by(actor_id=uid).update({"actor_id": None}, synchronize_session=False)
    record("user.delete", {"user_id": uid, "detached_team_id": tid, "source": source})
    db.session.flush()
    Users.query.filter_by(id=uid).delete(synchronize_session=False)
    return tid


def invalidate(uid, tid=None):
    clear_user_session(uid)
    if tid:
        clear_team_session(tid)
    clear_standings()
    clear_challenges()


def usable_admins():
    return Users.query.filter(Users.type == "admin", Users.banned.isnot(True)).count()


def native_change(user, data, e):
    """Validate and stage native PATCH policy; the native schema owns validation/commit."""
    if "type" in data and data["type"] not in ("admin", "user"):
        return "Native account type must be admin or user."
    new_type = data.get("type", user.type)
    banned = user.banned
    if "banned" in data:
        from marshmallow import ValidationError, fields
        try:
            banned = fields.Boolean().deserialize(data["banned"])
        except ValidationError:
            return "Banned must be true or false."
    changing_type = new_type != user.type
    if bool(banned) != bool(user.banned):
        blocked = contribution_reason(user, e)
        if blocked:
            return blocked
    losing_admin = user.type == "admin" and (new_type != "admin" or banned)
    if losing_admin and (changing_type or not user.banned):
        if user.id == session.get("id"):
            return "You cannot demote or deactivate your own administrator account."
        if not user.banned and usable_admins() <= 1:
            return "Keep at least one usable administrator account."
    if changing_type:
        blocked = participation_reason(user, e)
        if blocked:
            return blocked
    from .roles import is_maintainer, role
    if is_maintainer(user) and not changing_type:
        if ("team_id" in data and data["team_id"] is not None) or ("hidden" in data and data["hidden"] is not True):
            return "Revoke maintainer access through Open Tech Quest before changing staff participation or visibility."
    if changing_type:
        old = role(user)
        tid = detach(user)
        clear_ready(user)
        remove_grant(user)
        record("user.role", {"user_id": user.id, "old": old,
                             "new": "administrator" if new_type == "admin" else "participant",
                             "detached_team_id": tid, "source": "native"})
    if "banned" in data:
        set_active(user, not banned, e, "native")
    return None
