"""Session-admin proxy to the external durable backup worker; no DB job state."""
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from flask import Blueprint, Response, current_app, jsonify, request, session, stream_with_context
from werkzeug.exceptions import HTTPException
from CTFd.models import db
from .api import QuestError, admin, body, fail, success
from .authoring import locked_event
from .backup_contract import DEFAULT_MAX_BYTES, canonical
from . import lifecycle

backups_api = Blueprint("quest_backups", __name__)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@backups_api.errorhandler(QuestError)
def quest_error(exc):
    db.session.rollback()
    return jsonify(success=False, error=exc.message), exc.status


@backups_api.errorhandler(HTTPException)
def http_error(exc):
    db.session.rollback()
    return jsonify(success=False, error=exc.description), exc.code


@backups_api.after_request
def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def setting(name, default=None):
    return current_app.config.get(name) or os.environ.get(name) or default


def identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        fail("Invalid backup or operation identity", 400)
    return value


def reason(value):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= 1000:
        fail("Give a reason between 3 and 1000 characters")
    return value.strip()


def operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        fail("A new operation UUID is required")
    return value


def csrf():
    expected, supplied = session.get("nonce"), request.headers.get("CSRF-Token")
    if not isinstance(expected, str) or not isinstance(supplied, str) or not hmac.compare_digest(expected.encode(), supplied.encode()):
        fail("Refresh the organizer page before starting a backup or restore", 400)


def actor():
    if request.headers.get("Authorization"):
        fail("Use an administrator browser session for Backup & Restore", 403)
    locked_event()
    if not lifecycle.administrator():
        fail("Administrator access required", 403)
    user = lifecycle.current_user()
    value = {"id": user.id, "created_at": user.created.isoformat(timespec="microseconds") + "Z" if user.created else None,
             "password_sha256": hashlib.sha256((user.password or "").encode()).hexdigest(),
             "secret_sha256": hashlib.sha256((user.secret or "").encode()).hexdigest()}
    # The worker revalidates this primitive proof; never hold an event lock
    # across HTTP or the worker's consistent safety capture cannot proceed.
    db.session.rollback()
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Worker:
    def __init__(self, proof):
        self.base = setting("OTQ_BACKUP_WORKER_URL", "").rstrip("/")
        self.token = setting("OTQ_BACKUP_WORKER_TOKEN", "")
        self.status_origin = setting("OTQ_BACKUP_STATUS_ORIGIN", "").rstrip("/")
        self.proof = proof
        if not self.base or not self.token:
            fail("Backup & Restore is not configured by this installation's operator", 503)
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            fail("The backup worker address is not configured correctly", 503)
        status = urllib.parse.urlsplit(self.status_origin)
        local = status.hostname in ("localhost", "127.0.0.1", "::1")
        if not status.hostname or status.username or status.password or status.path or status.query or status.fragment or (status.scheme != "https" and not (local and status.scheme == "http")):
            fail("The external backup status origin is not configured correctly", 503)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def open(self, method, path, data=None, headers=None, timeout=30):
        outgoing = {"Authorization": "Bearer " + self.token,
                    "X-Quest-Actor": canonical(self.proof).decode("ascii"), "Accept": "application/json"}
        outgoing.update(headers or {})
        if isinstance(data, dict):
            data = canonical(data)
            outgoing["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + "/v1" + path, data=data, method=method, headers=outgoing)
        try:
            return self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                value = json.loads(exc.read(65537))
                message = value.get("error") if isinstance(value, dict) else None
            except (ValueError, UnicodeError):
                message = None
            finally:
                exc.close()
            if not isinstance(message, str) or not 1 <= len(message) <= 1000:
                message = "The backup worker could not complete this request. Check operation status before retrying."
            fail(message, exc.code if exc.code in (400, 401, 403, 404, 409, 413, 422, 503) else 502)
        except (urllib.error.URLError, TimeoutError, OSError):
            fail("The backup worker could not be reached. Check operation status before retrying; do not start a second restore.", 503)

    def json(self, method, path, data=None, headers=None, timeout=30):
        with self.open(method, path, data, headers, timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        try:
            if len(raw) > 1024 * 1024:
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            self.check_tickets(value)
            return value
        except (ValueError, UnicodeError):
            fail("The backup worker returned an invalid response. Check operation status before retrying.", 502)

    def check_tickets(self, value):
        ticket = value.get("ticket")
        if ticket is not None:
            job = value.get("job")
            if not isinstance(ticket, dict) or not isinstance(job, dict):
                raise ValueError()
            jid = job.get("id")
            if not isinstance(jid, str) or not _IDENTIFIER.fullmatch(jid) or ticket.get("status_url") != self.status_origin + "/v1/jobs/" + jid + "/status":
                raise ValueError()
            token = ticket.get("status_token")
            if not isinstance(token, str) or not 16 <= len(token) <= 2048:
                raise ValueError()
        active = value.get("active_job")
        if active is not None:
            if not isinstance(active, dict):
                raise ValueError()
            self.check_tickets(active)


def worker():
    return Worker(actor())


@backups_api.route("/admin/backups")
@admin
def index():
    proof = actor()
    if not setting("OTQ_BACKUP_WORKER_URL") or not setting("OTQ_BACKUP_WORKER_TOKEN"):
        return success({"available": False, "unavailable_reason": "The operator has not configured a backup worker for this installation.",
                        "worker_status_origin": None, "target": None, "schedule": None, "snapshots": [], "active_job": None})
    remote = Worker(proof)
    value = remote.json("GET", "/backups")
    value["worker_status_origin"] = remote.status_origin
    return success(value)


@backups_api.route("/admin/backups", methods=["POST"])
@admin
def create():
    csrf()
    data = body()
    if set(data) != {"reason", "operation_id"}:
        fail("Supply a reason and operation_id")
    payload = {"reason": reason(data["reason"]), "operation_id": operation_id(data["operation_id"])}
    return success(worker().json("POST", "/backups", payload)), 202


@backups_api.route("/admin/backups/<snapshot_id>/review", methods=["POST"])
@admin
def review(snapshot_id):
    csrf()
    if body() != {}:
        fail("Restore review takes an empty JSON object")
    return success(worker().json("POST", "/backups/" + identifier(snapshot_id) + "/review", {}))


@backups_api.route("/admin/backups/<snapshot_id>/restore", methods=["POST"])
@admin
def restore(snapshot_id):
    csrf()
    data = body()
    if set(data) != {"review_token", "reason", "confirmation", "operation_id"}:
        fail("Supply the reviewed restore token, reason, typed confirmation and operation_id")
    payload = {"reason": reason(data["reason"]), "operation_id": operation_id(data["operation_id"])}
    for key, maximum in (("review_token", 4096), ("confirmation", 512)):
        if not isinstance(data[key], str) or not 1 <= len(data[key]) <= maximum:
            fail("The restore confirmation is missing or invalid")
        payload[key] = data[key]
    payload["confirmation_text"] = payload.pop("confirmation")
    payload["confirm"] = True
    return success(worker().json("POST", "/backups/" + identifier(snapshot_id) + "/restore", payload)), 202


@backups_api.route("/admin/backups/jobs/<job_id>")
@admin
def job_status(job_id):
    return success(worker().json("GET", "/jobs/" + identifier(job_id)))


@backups_api.route("/admin/backups/upload", methods=["POST"])
@admin
def upload():
    csrf()
    if set(request.form) != {"nonce", "reason", "operation_id"} or set(request.files) != {"file"}:
        fail("Supply one backup file, nonce, reason and operation_id")
    if not isinstance(session.get("nonce"), str) or not hmac.compare_digest(request.form["nonce"], session["nonce"]):
        fail("Refresh the organizer page before uploading a backup", 400)
    archive = request.files["file"]
    archive.stream.seek(0, 2)
    size = archive.stream.tell()
    archive.stream.seek(0)
    limit = int(setting("OTQ_BACKUP_MAX_UPLOAD_BYTES", DEFAULT_MAX_BYTES))
    if not 0 < size <= limit:
        fail("The backup is empty or exceeds this installation's upload limit", 413)
    headers = {"Content-Type": "application/octet-stream", "Content-Length": str(size),
               "X-Quest-Operation-Id": operation_id(request.form["operation_id"]),
               "X-Quest-Reason": urllib.parse.quote(reason(request.form["reason"]), safe="")}
    return success(worker().json("POST", "/backups/upload", archive.stream, headers, timeout=120)), 202


@backups_api.route("/admin/backups/<snapshot_id>/download")
@admin
def download(snapshot_id):
    sid = identifier(snapshot_id)
    response = worker().open("GET", "/backups/" + sid + "/download")
    def chunks():
        try:
            while True:
                block = response.read(65536)
                if not block:
                    break
                yield block
        finally:
            response.close()
    result = Response(stream_with_context(chunks()), mimetype="application/zip")
    result.headers["Content-Disposition"] = 'attachment; filename="otq-backup-' + sid + '.otqbackup"'
    return result
