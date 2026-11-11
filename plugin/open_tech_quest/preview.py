"""Stateless staff content for the participant preview; no play history."""
import hashlib
import json
import re
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

from flask import Blueprint, Response, request, stream_with_context
from CTFd.models import Challenges, ChallengeFiles, Files, Hints, Solutions, SolutionFiles, db
from CTFd.utils.uploads import get_uploader
from CTFd.utils.uploads.uploaders import S3Uploader
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from .api import QuestError, error, fail, http_error, success
from .authoring import flag_rows, hint_rows, hint_prerequisites, locked_event, unsupported_reason
from .assets import download_file, image_type
from .models import QuestAsset, QuestRoom, QuestStep
from .roles import room_manager

preview_api = Blueprint("quest_preview", __name__)
preview_api.register_error_handler(QuestError, error)
preview_api.register_error_handler(HTTPException, http_error)


@preview_api.after_request
def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def file_url(file):
    name = secure_filename(file["location"].rsplit("/", 1)[-1]) or "download"
    return f"/api/v1/quest/manage/preview/files/{file['id']}/{quote(name, safe='')}"


def rewrite_files(value, files):
    """Keep external links; route native files through staff-only ownership."""
    if not value:
        return value
    locations = {unquote(f["location"]): f for f in files}
    pattern = r'(?:(?:https?:)?//[^\s<>"\'()]+)?/files/[^\s<>"\'()\[\]]+'
    def replace(match):
        value = match.group(0)
        parsed = urlsplit(value)
        if parsed.netloc:
            def origin(url):
                scheme = url.scheme or request.scheme
                return scheme, url.hostname, url.port or (443 if scheme == "https" else 80)
            try:
                if origin(parsed) != origin(urlsplit(request.host_url)):
                    return value
            except ValueError:
                return value
        file = locations.get(unquote(parsed.path)[len("/files/"):])
        return file_url(file) if file else "/api/v1/quest/manage/preview/files/0/unavailable"
    return re.sub(pattern, replace, value)


def source():
    """Saved room content and an opaque hash; flags never enter the DTO."""
    rooms = [{key: getattr(room, key) for key in ("id", "slug", "name", "subtitle", "description", "position", "theme")}
             for room in QuestRoom.query.order_by(QuestRoom.position, QuestRoom.id).all()]
    room_slugs = {room["id"]: room["slug"] for room in rooms}
    steps = QuestStep.query.order_by(QuestStep.room_id, QuestStep.position).all()
    mapping = {step.challenge_id: step.room_id for step in steps}
    solutions = {s.challenge_id: {key: getattr(s, key) for key in ("id", "state", "content")}
                 for s in Solutions.query.filter(Solutions.challenge_id.in_(mapping)).order_by(Solutions.id).all()}
    solution_ids = [s["id"] for s in solutions.values()]
    native_files = (ChallengeFiles.query.filter(ChallengeFiles.challenge_id.in_(mapping)).order_by(ChallengeFiles.id).all() +
                    SolutionFiles.query.filter(SolutionFiles.solution_id.in_(solution_ids)).order_by(SolutionFiles.id).all())
    managed = {a.file_id: a for a in QuestAsset.query.filter(QuestAsset.challenge_id.in_(mapping)).all()}
    files = [{"id": f.id, "location": f.location, "type": f.type,
              "challenge_id": getattr(f, "challenge_id", None), "solution_id": getattr(f, "solution_id", None),
              "kind": managed[f.id].kind if f.id in managed else "attachment",
              "sha256": managed[f.id].sha256 if f.id in managed else None} for f in native_files]
    challenges, flags = [], []
    for challenge in Challenges.query.filter(Challenges.id.in_(mapping)).order_by(Challenges.id).all():
        native_flags = flag_rows(challenge)
        flags.extend([{**dict(f._mapping), "challenge_id": challenge.id} for f in native_flags])
        row = {key: getattr(challenge, key) for key in ("id", "name", "state", "description", "value", "connection_info", "max_attempts")}
        row.update(room_slug=room_slugs[mapping[challenge.id]], solution=solutions.get(challenge.id),
                   unsupported_reason=unsupported_reason(challenge, native_flags, allow_native_flags=True),
                   hints=[{"id": h.id, "title": h.title, "content": h.content, "cost": h.cost,
                           "prerequisites": hint_prerequisites(h)} for h in hint_rows(challenge)],
                   files=[file_url(f) for f in files if f["challenge_id"] == challenge.id and f["kind"] != "inline"])
        challenges.append(row)
    by_id = {c["id"]: c for c in challenges}
    for room in rooms:
        room["steps"] = [{"id": s.challenge_id, "title": by_id[s.challenge_id]["name"], "value": by_id[s.challenge_id]["value"],
                          "position": s.position, "visibility": by_id[s.challenge_id]["state"]}
                         for s in steps if s.room_id == room["id"] and s.challenge_id in by_id]
    # Raw flag values bind the saved-content revision without being serialized
    # to clients, stored a second time, logged or sent through native graders.
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    for room in rooms:
        ids = {s["id"] for s in room["steps"]}
        room_solutions = {solutions[cid]["id"] for cid in ids if cid in solutions}
        room["revision"] = digest({"room": room,
            "challenges": [c for c in challenges if c["id"] in ids],
            "flags": [f for f in flags if f["challenge_id"] in ids],
            "files": [f for f in files if f["challenge_id"] in ids or f["solution_id"] in room_solutions]})
    revision = digest([[room["id"], room["revision"]] for room in rooms])
    for room in rooms:
        room["description"] = rewrite_files(room["description"], files)
    for row in challenges:
        for field in ("description", "connection_info"):
            row[field] = rewrite_files(row[field], files)
        for hint in row["hints"]:
            hint["content"] = rewrite_files(hint["content"], files)
        if row["solution"]:
            row["solution"]["content"] = rewrite_files(row["solution"]["content"], files)
    return {"revision": revision, "rooms": rooms, "challenges": challenges}


@preview_api.route("")
@room_manager
def content():
    locked_event()
    return success(source())


@preview_api.route("/files/<int:file_id>/<filename>")
@room_manager
def file_view(file_id, filename):
    locked_event()
    file = Files.query.get(file_id)
    cid = getattr(file, "challenge_id", None)
    if file is not None and file.type == "solution":
        solution = Solutions.query.get(file.solution_id)
        cid = solution.challenge_id if solution else None
    if file is None or file.type not in ("challenge", "solution") or not QuestStep.query.filter_by(challenge_id=cid).first():
        fail("File not found in the room preview", 404)
    managed = QuestAsset.query.get(file.id)
    file = SimpleNamespace(location=file.location)
    managed = SimpleNamespace(name=managed.name, kind=managed.kind) if managed else None
    # Staff authority and native room ownership are checked before storage I/O.
    # Do not retain the competition event lock during a slow provider read.
    db.session.rollback()
    if managed:
        return download_file(file, managed)
    uploader = get_uploader()
    location = (getattr(uploader, "s3_prefix", None) or "") + file.location
    name = secure_filename(file.location.rsplit("/", 1)[-1]) or "download"
    try:
        stream = (uploader.s3.get_object(Bucket=uploader.bucket, Key=location)["Body"]
                  if isinstance(uploader, S3Uploader) else uploader.open(location))
    except FileNotFoundError:
        fail("This preview attachment is unavailable in storage", 404)
    try:
        head = stream.read(32)
    except Exception:
        stream.close()
        raise
    mime, _ = image_type(head)
    def chunks():
        try:
            yield head
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            stream.close()
    response = Response(stream_with_context(chunks()), mimetype=mime or "application/octet-stream")
    response.headers.add("Content-Disposition", "inline" if mime else "attachment", filename=name)
    response.call_on_close(stream.close)
    return response
