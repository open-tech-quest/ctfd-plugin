"""Fresh database-backed staff authorization; native CTFd admins stay native."""
from functools import wraps
from flask import g, has_request_context, jsonify, request
from .lifecycle import current_user as get_current_user
from .models import QuestMaintainer


def is_maintainer(user=None):
    user = user if user is not None else get_current_user()
    if user is None:
        return False
    memoize = has_request_context() and request.method == "GET"
    if memoize:
        if not hasattr(g, "quest_read_roles"):
            g.quest_read_roles = {}
        if user.id in g.quest_read_roles:
            return g.quest_read_roles[user.id]
    result = QuestMaintainer.query.filter_by(user_id=user.id).first() is not None
    if memoize:
        g.quest_read_roles[user.id] = result
    return result


def role(user):
    if user is None:
        return "guest"
    if user.type == "admin":
        return "administrator"
    return "maintainer" if is_maintainer(user) else "participant"


def can_manage_rooms(user=None):
    user = user if user is not None else get_current_user()
    return bool(user and not user.banned and (user.type == "admin" or is_maintainer(user)))


def room_manager(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return jsonify(success=False, error="Sign in to manage rooms"), 401
        if not can_manage_rooms(user):
            return jsonify(success=False, error="Room management access required"), 403
        return fn(*args, **kwargs)
    return wrapper


def promotion_reason(user, e):
    if user.type != "user":
        return "Native administrators are managed through CTFd."
    if is_maintainer(user):
        return None
    from .lifecycle import participation_reason
    return participation_reason(user, e)
