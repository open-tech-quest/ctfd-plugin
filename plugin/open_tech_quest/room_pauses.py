"""Organizer room interruptions; persisted history is shared by every team."""
import hashlib
import json
from .models import QuestRoomPause


def active(room_id):
    return (QuestRoomPause.query.filter_by(room_id=room_id, ended_at=None)
            .order_by(QuestRoomPause.id.desc()).first())


def state(e, room_id, rows, epoch):
    """Serialize primitive batched rows without per-room database reads."""
    current = next((p for p in reversed(rows) if p[3] is None), None)
    version = [e.status, e.start_at, e.end_at, epoch, room_id, rows]
    revision = hashlib.sha256(json.dumps(version, default=str, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return {"paused": current is not None,
            "started_at": current[2].isoformat(timespec="milliseconds") + "Z" if current else None,
            "reason": current[4] if current else None, "revision": revision}
