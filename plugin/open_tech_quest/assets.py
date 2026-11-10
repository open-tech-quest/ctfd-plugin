"""Bounded JSON uploads backed by native challenge/solution file ownership.

Deletion is transactional metadata removal. Provider objects are deliberately
left for operator garbage collection: deleting bytes cannot be rolled back, and
the native filesystem delete helper recursively removes a shared directory.
"""
import base64
import binascii
import hashlib
import io
import re
from urllib.parse import quote, unquote, urlsplit

from flask import Blueprint, request, send_file
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from CTFd.cache import clear_challenges
from CTFd.models import ChallengeFiles, Files, SolutionFiles, Solutions, db
from CTFd.utils.uploads import get_uploader
from .api import QuestError, audit, body, conflict, error, fail, http_error, room_by_slug, success
from .authoring import deletion_dependents, locked_event, member_challenge, mutation_reason
from .models import QuestAsset
from .roles import room_manager

MAX_BYTES = 5242880
KINDS = ("inline", "attachment", "solution")
assets_api = Blueprint("quest_assets", __name__)
assets_api.register_error_handler(QuestError, error)
assets_api.register_error_handler(HTTPException, http_error)
assets_api.register_error_handler(IntegrityError, conflict)


@assets_api.after_request
def private_assets(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def native_files(challenge_id):
    solution_ids = db.session.query(Solutions.id).filter_by(challenge_id=challenge_id)
    return (ChallengeFiles.query.filter_by(challenge_id=challenge_id).order_by(ChallengeFiles.id).all() +
            SolutionFiles.query.filter(SolutionFiles.solution_id.in_(solution_ids)).order_by(SolutionFiles.id).all())


def file_metadata(slug, challenge_id, file, managed=None):
    managed = managed if managed is not None else QuestAsset.query.get(file.id)
    return {"id": file.id, "managed": managed is not None,
            "kind": managed.kind if managed else "solution" if file.type == "solution" else "attachment",
            "name": managed.name if managed else (file.location or "").replace("\\", "/").rsplit("/", 1)[-1],
            "size": managed.size if managed else None, "sha256": managed.sha256 if managed else None,
            "url": "/files/" + quote(file.location, safe="/"),
            "preview_url": f"/api/v1/quest/manage/rooms/{slug}/challenges/{challenge_id}/assets/{file.id}/download"}


def member_file(challenge_id, file_id):
    file = next((file for file in native_files(challenge_id) if file.id == file_id), None)
    if file is None:
        fail("Asset not found in this challenge", 404)
    return file


def image_type(value):
    if value.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", (".png",)
    if value.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", (".jpg", ".jpeg")
    if value.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", (".gif",)
    if value.startswith(b"RIFF") and value[8:12] == b"WEBP":
        return "image/webp", (".webp",)
    return None, ()


def download_file(file, managed=None):
    uploader = get_uploader()
    # Native S3.open expects its complete key, unlike S3.download/delete.
    location = (getattr(uploader, "s3_prefix", None) or "") + file.location
    with uploader.open(location) as stream:
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        fail("This asset is too large for scoped preview", 409)
    name = managed.name if managed else secure_filename(file.location.rsplit("/", 1)[-1])
    mime, _ = image_type(content)
    inline = managed and managed.kind in ("inline", "solution") and mime is not None
    response = send_file(io.BytesIO(content), download_name=name,
                         mimetype=mime if inline else "application/octet-stream",
                         as_attachment=not inline)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@assets_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>/assets")
@room_manager
def list_assets(slug, challenge_id):
    room = room_by_slug(slug)
    member_challenge(room, challenge_id)
    return success({"assets": [file_metadata(slug, challenge_id, file) for file in native_files(challenge_id)]})


@assets_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>/assets", methods=["POST"])
@room_manager
def upload_asset(slug, challenge_id):
    data = body()
    if set(data) != {"kind", "name", "content_base64"} or data["kind"] not in KINDS:
        fail("Provide kind, name and content_base64")
    name = data["name"]
    if not isinstance(name, str) or not 1 <= len(name) <= 160 or any(c in name for c in ("/", "\\", "\x00")):
        fail("Use a filename without a path (maximum 160 characters)")
    name = secure_filename(name)
    if not name:
        fail("A safe non-empty filename is required")
    encoded = data["content_base64"]
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        fail("Assets may contain at most 5 MiB", 413)
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        fail("content_base64 must be valid base64")
    if not 1 <= len(content) <= MAX_BYTES:
        fail("Assets must be non-empty and at most 5 MiB", 413)
    if data["kind"] == "inline":
        mime, extensions = image_type(content)
        if not mime or not name.lower().endswith(extensions):
            fail("Inline assets must be PNG, JPEG, GIF or WebP images with matching filename extensions")
    e = locked_event()
    room = room_by_slug(slug)
    challenge = member_challenge(room, challenge_id)
    reason = mutation_reason(room, challenge, e, allow_native_flags=True)
    if reason:
        fail(reason, 409)
    solution = Solutions.query.filter_by(challenge_id=challenge_id).first()
    if data["kind"] == "solution" and solution is None:
        fail("Create this challenge's solution before uploading solution assets", 409)
    digest = hashlib.sha256(content).hexdigest()
    managed = QuestAsset.query.filter_by(challenge_id=challenge_id, kind=data["kind"], name=name).first()
    file = member_file(challenge_id, managed.file_id) if managed else None
    if managed and managed.sha256 == digest:
        return success(file_metadata(slug, challenge_id, file, managed))
    # No native upload_file(): it commits independently. Each staged upload uses
    # a fresh random native storage directory and can never overwrite another file.
    location = get_uploader().upload(file_obj=io.BytesIO(content), filename=name)
    if not isinstance(location, str) or not location:
        fail("The storage provider could not save this asset", 503)
    if file is None:
        file = SolutionFiles(solution_id=solution.id) if data["kind"] == "solution" else ChallengeFiles(challenge_id=challenge_id)
        db.session.add(file)
    file.location, file.sha1sum = location, hashlib.sha1(content).hexdigest()
    db.session.flush()
    if managed is None:
        managed = QuestAsset(file_id=file.id, challenge_id=challenge_id, kind=data["kind"], name=name)
        db.session.add(managed)
    managed.size, managed.sha256 = len(content), digest
    audit("asset.upload", {"room_slug": slug, "challenge_id": challenge_id, "file_id": file.id, "kind": data["kind"]})
    db.session.commit()
    clear_challenges()
    return success(file_metadata(slug, challenge_id, file, managed))


@assets_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>/assets/<int:file_id>", methods=["DELETE"])
@room_manager
def delete_asset(slug, challenge_id, file_id):
    if body() != {}:
        fail("This action takes an empty JSON object")
    e = locked_event()
    room = room_by_slug(slug)
    challenge = member_challenge(room, challenge_id)
    reason = mutation_reason(room, challenge, e, allow_native_flags=True)
    if reason:
        fail(reason, 409)
    file = member_file(challenge_id, file_id)
    managed = QuestAsset.query.get(file_id)
    if managed is None or managed.challenge_id != challenge_id:
        fail("Legacy files remain managed through native administrator tooling", 409)
    unknown = deletion_dependents({"files", "quest_assets"})
    if unknown:
        fail("Additional plugin tables need a cleanup plan: " + ", ".join(unknown), 409)
    db.session.delete(managed)
    db.session.delete(file)
    audit("asset.delete", {"room_slug": slug, "challenge_id": challenge_id, "file_id": file_id})
    db.session.commit()
    clear_challenges()
    return success({"id": file_id, "deleted": True})


@assets_api.route("/manage/rooms/<slug>/challenges/<int:challenge_id>/assets/<int:file_id>/download")
@room_manager
def preview_asset(slug, challenge_id, file_id):
    room = room_by_slug(slug)
    member_challenge(room, challenge_id)
    return download_file(member_file(challenge_id, file_id), QuestAsset.query.get(file_id))


def install(app):
    @app.after_request
    def hide_inline_downloads(response):
        if request.path.startswith("/files/"):
            managed = QuestAsset.query.join(Files, Files.id == QuestAsset.file_id).filter(Files.location == request.path[len("/files/"):]).first()
            if managed:
                response.headers["Cache-Control"] = "private, no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
        match = re.fullmatch(r"/api/v1/challenges/(\d+)/?", request.path)
        if request.method != "GET" or not match or response.status_code != 200 or not response.is_json:
            return response
        payload = response.get_json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            return response
        locations = {"/files/" + file.location for file in ChallengeFiles.query.join(QuestAsset, QuestAsset.file_id == ChallengeFiles.id).filter(
            QuestAsset.challenge_id == int(match[1]), QuestAsset.kind == "inline").all()}
        data["files"] = [value for value in data["files"] if not isinstance(value, str) or unquote(urlsplit(value).path) not in locations]
        response.set_data(__import__("json").dumps(payload))
        return response
