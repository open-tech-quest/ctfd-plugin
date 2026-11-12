"""External restore fence: independent of all data that a restore replaces."""
import json
import os
import re
from pathlib import Path

from flask import current_app, has_request_context, jsonify, request
from werkzeug.exceptions import ServiceUnavailable

MESSAGE = "Backup restore maintenance is in progress. Use the operation status window and wait before signing in again."


def state():
    path = Path(current_app.config.get("OTQ_BACKUP_MAINTENANCE_FILE")
                or os.environ.get("OTQ_BACKUP_MAINTENANCE_FILE", "/var/quest-backup/maintenance.json"))
    try:
        with path.open("rb") as source:
            raw = source.read(4097)
    except FileNotFoundError:
        return None
    except OSError:
        return {"job_id": None}
    # Existence is the fence. A partial/damaged marker cannot reopen writes.
    job_id = None
    try:
        value = json.loads(raw) if len(raw) <= 4096 else None
        candidate = value.get("job_id") if isinstance(value, dict) else None
        if isinstance(candidate, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", candidate):
            job_id = candidate
    except (ValueError, UnicodeError):
        pass
    return {"job_id": job_id}


def require_available():
    # CTFd must initialize while fenced so the worker can verify it before
    # reopening. This post-lock check applies to queued HTTP writers only.
    if has_request_context() and state() is not None:
        raise ServiceUnavailable(MESSAGE)


def install(app):
    def before_native_authentication():
        # The readiness probe itself performs no application writes. All other
        # paths stop before native authentication/tracking/CSRF touch the DB.
        if request.path == "/healthcheck" and request.method in ("GET", "HEAD"):
            return None
        active = state()
        if active is not None:
            response = jsonify(success=False, error=MESSAGE, maintenance=True, job_id=active["job_id"])
            response.status_code = 503
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Retry-After"] = "5"
            return response
    app.before_request_funcs.setdefault(None, []).insert(0, before_native_authentication)
