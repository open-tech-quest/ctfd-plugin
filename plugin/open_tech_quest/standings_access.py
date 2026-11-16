"""Organizer-controlled comparisons, separate from competition results."""
import json
from copy import deepcopy
from CTFd.models import Configs

KEY = "quest_standings_visibility"
DEFAULTS = {"room_comparison": True, "overall_comparison": True}


def settings():
    # Read authority fresh: worker-local native config caches cannot reopen a view.
    row = Configs.query.filter_by(key=KEY).order_by(Configs.id).first()
    if row is None:
        return dict(DEFAULTS)
    try:
        value = json.loads(row.value)
    except (TypeError, ValueError):
        value = None
    return {key: isinstance(value, dict) and value.get(key) is True for key in DEFAULTS}


def access(user, configured=None):
    configured = settings() if configured is None else configured
    administrator = bool(user and user.type == "admin" and not user.banned)
    result = {key: administrator or enabled for key, enabled in configured.items()}
    return {**result, "projector": all(result.values())}


def present(data, scope, user, permissions):
    """Filter a copy; published snapshots and shared inputs remain untouched."""
    if scope != "team" and permissions["room_comparison"] and permissions["overall_comparison"]:
        return {**data, "access": permissions}
    result = deepcopy(data)
    result["access"] = permissions
    if scope == "team":
        team_id = user.team_id if user and not user.banned else None
        result["teams"] = [team for team in result["teams"] if team["id"] == team_id]
        result["room_leaders"] = []
        for team in result["teams"]:
            team["rank"] = None
            for progress in team["room_progress"]:
                progress["rank"] = None
        return result
    if not permissions["room_comparison"]:
        result["room_leaders"] = []
        for team in result["teams"]:
            if user and team["id"] == user.team_id:
                for progress in team["room_progress"]:
                    progress["rank"] = None
            else:
                team["room_progress"] = []
    if not permissions["overall_comparison"]:
        # Preserve room standings, without their original overall ranking order.
        result["teams"].sort(key=lambda team: team["id"])
        for team in result["teams"]:
            team["rank"], team["overall_elapsed_ms"] = None, None
            team["event_completion"] = None
            if not user or team["id"] != user.team_id:
                for key in ("points", "gross_points", "hint_costs", "total"):
                    team[key] = 0
    return result
