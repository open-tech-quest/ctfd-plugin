"""Durable projector selection and server-anchored rotation settings."""
from copy import deepcopy

from .service import iso, parse_time


VIEWS = ("overview", "matrix", "room", "overall", "awards")
DURATIONS = {"overview_seconds": 60, "room_seconds": 30, "overall_seconds": 20}


class ProjectorError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        return parse_time(value)
    except (ValueError, TypeError, OverflowError):
        return None


def settings(raw):
    """Normalize a stored legacy/new config without modifying it or its clocks."""
    raw = raw if isinstance(raw, dict) else {}
    saved = raw.get("rotation")
    saved = saved if isinstance(saved, dict) else {}
    rotation = {key: saved.get(key) if type(saved.get(key)) is int and 10 <= saved[key] <= 600 else default
                for key, default in DURATIONS.items()}
    anchor, paused = _time(saved.get("anchor_at")), _time(saved.get("paused_at"))
    enabled = saved.get("enabled") is True and anchor is not None
    rotation.update(enabled=enabled, anchor_at=iso(anchor) if enabled else None,
                    paused_at=iso(paused) if enabled and paused is not None and paused >= anchor else None)
    return {"view": raw.get("view") if raw.get("view") in VIEWS else "overview",
            "room_slug": raw.get("room_slug") if isinstance(raw.get("room_slug"), str) else None,
            "rotation": rotation}


def serialize(raw, at):
    return dict(settings(raw), server_now=iso(at))


def configure(raw, data, at):
    """Validate a patch and return JSON to persist; callers hold the event lock."""
    current = settings(raw)
    if "rotation_action" in data:
        if set(data) != {"rotation_action"} or data["rotation_action"] not in ("pause", "resume", "restart"):
            raise ProjectorError("Use a single rotation_action: pause, resume or restart")
        rotation = current["rotation"]
        if not rotation["enabled"]:
            raise ProjectorError("Enable projector rotation first", 409)
        action = data["rotation_action"]
        if action == "pause" and rotation["paused_at"] is None:
            rotation["paused_at"] = iso(at)
        elif action == "resume" and rotation["paused_at"] is not None:
            rotation["anchor_at"] = iso(_time(rotation["anchor_at"]) + (at - _time(rotation["paused_at"])))
            rotation["paused_at"] = None
        elif action == "restart":
            rotation.update(anchor_at=iso(at), paused_at=None)
        return current

    if not data or set(data) - {"view", "room_slug", "rotation"}:
        raise ProjectorError("Unknown projector setting")
    if "view" in data:
        if data["view"] not in VIEWS:
            raise ProjectorError("Unknown projector view")
        current["view"] = data["view"]
    if "room_slug" in data:
        if data["room_slug"] is not None and not isinstance(data["room_slug"], str):
            raise ProjectorError("A valid room slug is required")
        current["room_slug"] = data["room_slug"]
    if "rotation" in data:
        patch = data["rotation"]
        if not isinstance(patch, dict) or set(patch) - {"enabled", *DURATIONS}:
            raise ProjectorError("Unknown projector rotation setting")
        if "enabled" in patch and type(patch["enabled"]) is not bool:
            raise ProjectorError("Rotation enabled must be true or false")
        rotation = current["rotation"]
        previous = dict(rotation)
        for key in DURATIONS:
            if key in patch:
                if type(patch[key]) is not int or not 10 <= patch[key] <= 600:
                    raise ProjectorError("Rotation durations must be integers between 10 and 600 seconds")
                rotation[key] = patch[key]
        rotation["enabled"] = patch.get("enabled", rotation["enabled"])
        if not rotation["enabled"]:
            rotation.update(anchor_at=None, paused_at=None)
        elif not previous["enabled"] or any(rotation[key] != previous[key] for key in DURATIONS):
            rotation.update(anchor_at=iso(at), paused_at=None)
    return current


def remove_room(raw, slug):
    """Preserve legacy JSON and rotation state while removing a deleted selection."""
    result = deepcopy(raw) if isinstance(raw, dict) else {}
    if result.get("room_slug") == slug:
        result["room_slug"] = None
        if result.get("view") == "room":
            result["view"] = "overview"
    return result
