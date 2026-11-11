"""Deployment-side backup control plane; jobs and trust survive full restoration."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from . import backup_contract as contract
except ImportError:
    import backup_contract as contract


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(contract.canonical(value))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


class WorkerError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def operation_id(value):
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise WorkerError("Supply a valid operation UUID", 400)


def actor_fingerprint(actor):
    return hashlib.sha256(contract.canonical(actor)).hexdigest()


class Profile:
    """State is private and external to CTFd's database and uploaded files."""
    def __init__(self, config, adapter):
        self.config, self.adapter = config, adapter
        self.root = Path(config["state_root"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for name in ("jobs", "snapshots", "temporary"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        self.key = Path(config["signing_key_file"]).read_bytes()
        if len(self.key) < 32:
            raise ValueError("Worker signing keys require at least 32 bytes")
        self.key_id = config["signing_key_id"]
        self.trusted_keys = {self.key_id: self.key}
        for name, path in config.get("trusted_key_files", {}).items():
            self.trusted_keys[name] = Path(path).read_bytes()
        self.token = Path(config["token_file"]).read_text().strip()
        if len(self.token) < 32:
            raise ValueError("Worker API tokens require at least 32 characters")
        self.public_origin = config["public_origin"].rstrip("/")
        parsed = urlsplit(self.public_origin)
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.path:
            raise ValueError("Configure the worker's public HTTP(S) origin")
        self.max_bytes = config.get("max_backup_bytes", contract.DEFAULT_MAX_BYTES)
        self.mutex = threading.RLock()
        with (self.root / "operation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            for path in (self.root / "jobs").glob("*.json"):
                job = json.loads(path.read_text())
                if job["status"] in ("queued", "running"):
                    job.update(status="failed", stage="interrupted", updated_at=now(),
                               message="The worker restarted. An operator must inspect recovery before reopening the application.")
                    write_json(path, job)

    def _job_path(self, identifier):
        return self.root / "jobs" / (operation_id(identifier) + ".json")

    def job(self, identifier):
        path = self._job_path(identifier)
        if not path.exists():
            raise WorkerError("Operation not found", 404)
        return json.loads(path.read_text())

    def public_job(self, job):
        keys = ("id", "operation_id", "kind", "status", "stage", "message", "created_at",
                "updated_at", "safety_snapshot_id", "requires_login")
        return {key: job[key] for key in keys if key in job}

    def ticket(self, job):
        expires = int(datetime.fromisoformat(job["created_at"].replace("Z", "+00:00")).timestamp()) + 86400
        payload = f"status:{self.config['instance_id']}:{job['id']}:{expires}".encode()
        digest = hmac.new(self.key, payload, hashlib.sha256).hexdigest()
        return {"status_url": self.public_origin + "/v1/jobs/" + job["id"] + "/status",
                "status_token": str(expires) + "." + digest}

    def envelope(self, job):
        return {"job": self.public_job(job), "ticket": self.ticket(job)}

    def check_ticket(self, identifier, token):
        expected = self.ticket(self.job(identifier))["status_token"]
        try:
            unexpired = int(token.split(".", 1)[0]) >= time.time()
        except (ValueError, AttributeError):
            unexpired = False
        return unexpired and isinstance(token, str) and hmac.compare_digest(expected, token)

    def authorize(self, actor):
        if not isinstance(actor, dict) or not self.adapter.actor_valid(actor):
            raise WorkerError("This administrator session is no longer authorized. Sign in again.", 403)

    def current(self):
        value = self.adapter.current()
        if value["target"]["id"] != self.config["instance_id"]:
            raise WorkerError("The installation identity changed; operator qualification is required")
        return value

    def compatibility(self, manifest, current):
        if manifest.get("legacy"):
            return {"supported": False, "reason": "This legacy backup needs operator verification before restoration"}
        if manifest["instance"]["id"] != current["target"]["id"]:
            return {"supported": False, "reason": "This backup belongs to another installation; operator recovery is required"}
        target = current["target"]["current"]
        sources = [target]
        for item in self.config.get("qualified_restores", []):
            if item.get("target") == target:
                sources.append(item["source"])
        result = contract.compatibility(manifest, sources, target)
        if result["supported"]:
            reason = self.adapter.configuration_reason(manifest, current)
            if reason:
                return {"supported": False, "reason": reason}
        return result

    def snapshot_record(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or not all(c in "0123456789abcdef" for c in identifier):
            raise WorkerError("Invalid backup identity", 400)
        path = self.root / "snapshots" / (identifier + ".json")
        if not path.exists():
            raise WorkerError("Backup not found", 404)
        return json.loads(path.read_text())

    def snapshot(self, record, current):
        manifest = record["manifest"]
        compatible = self.compatibility(manifest, current)
        if record.get("signing_key_id") not in self.trusted_keys:
            compatible = {"supported": False, "reason": "The backup signing key requires operator recovery"}
        return {"id": manifest["snapshot_id"], "created_at": manifest["created_at"],
                "origin": record["origin"], "bytes": record["bytes"], "sha256": record["sha256"],
                "source": manifest["source"], "counts": manifest["counts"],
                "compatibility": compatible}

    def index(self, actor):
        self.authorize(actor)
        current = self.current()
        records = [json.loads(path.read_text()) for path in (self.root / "snapshots").glob("*.json")]
        jobs = [json.loads(path.read_text()) for path in (self.root / "jobs").glob("*.json")]
        jobs.sort(key=lambda value: value["created_at"], reverse=True)
        active = next((job for job in jobs if job["status"] in ("queued", "running")), None)
        schedule = self.adapter.schedule()
        scheduled = [job for job in jobs if job.get("scheduled")]
        schedule["last_job"] = self.public_job(scheduled[0]) if scheduled else None
        successful = next((job for job in scheduled if job["status"] == "succeeded"), None)
        if successful:
            schedule["last_success_at"] = successful["updated_at"]
        return {"available": True, "unavailable_reason": None, "worker_status_origin": self.public_origin,
                "target": current["target"], "schedule": schedule,
                "snapshots": [self.snapshot(record, current) for record in sorted(records, key=lambda r: r["manifest"]["created_at"], reverse=True)],
                "active_job": self.envelope(active) if active else None}

    def review(self, identifier, actor):
        self.authorize(actor)
        current, record = self.current(), self.snapshot_record(identifier)
        snapshot = self.snapshot(record, current)
        if not snapshot["compatibility"]["supported"]:
            raise WorkerError(snapshot["compatibility"]["reason"])
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        payload = {"snapshot_id": identifier, "manifest": contract.fingerprint(record["manifest"]),
                   "generation": current["generation"], "actor": actor_fingerprint(actor),
                   "expires": int(expires.timestamp())}
        encoded = base64.urlsafe_b64encode(contract.canonical(payload)).decode().rstrip("=")
        token = encoded + "." + hmac.new(self.key, ("review:" + encoded).encode(), hashlib.sha256).hexdigest()
        return {"review_token": token, "expires_at": expires.isoformat().replace("+00:00", "Z"),
                "confirmation_text": current["target"]["name"], "target": current["target"], "snapshot": snapshot,
                "comparison": {"current_counts": current["counts"], "snapshot_counts": snapshot["counts"]},
                "warnings": ["All current CTFd and Quest data and uploaded files will be replaced, including accounts, passwords, flags, teams and progress.",
                             "Activity after this backup will be lost. A separate safety backup is created before replacement.",
                             "Everyone must sign in using an account and password from the backup. Recorded timestamps are preserved; the real-world clock is not rewound."],
                "safety_backup_required": True, "full_replace": True}

    def _check_review(self, identifier, actor, body, current):
        try:
            encoded, provided = body["review_token"].split(".", 1)
            expected = hmac.new(self.key, ("review:" + encoded).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, provided):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            record = self.snapshot_record(identifier)
            valid = (payload["snapshot_id"] == identifier and payload["manifest"] == contract.fingerprint(record["manifest"])
                     and payload["generation"] == current["generation"] and payload["actor"] == actor_fingerprint(actor)
                     and payload["expires"] >= time.time())
            if not valid or body.get("confirmation_text") != current["target"]["name"] or body.get("confirm") is not True:
                raise ValueError()
            reason = body.get("reason")
            if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 1000 or "\x00" in reason:
                raise ValueError()
            compatibility = self.compatibility(record["manifest"], current)
            if not compatibility["supported"]:
                raise WorkerError(compatibility["reason"])
            return record
        except (KeyError, ValueError, TypeError, UnicodeError):
            raise WorkerError("Restore review changed or expired. Review the current target and confirm again.")

    def _progress(self, job, stage, message):
        job.update(stage=stage, message=message, updated_at=now())
        write_json(self._job_path(job["id"]), job)

    def _capture(self, origin):
        temporary = self.root / "temporary" / uuid.uuid4().hex
        temporary.mkdir(mode=0o700)
        try:
            captured = self.adapter.capture(temporary)
            manifest = {"format": contract.FORMAT, "schema_version": contract.SCHEMA_VERSION,
                        "snapshot_id": uuid.uuid4().hex, "created_at": now(),
                        "source": captured["source"], "instance": captured["instance"], "counts": captured["counts"]}
            for key in ("configuration", "schema_fingerprint", "data_images", "database_parameters"):
                if key in captured:
                    manifest[key] = captured[key]
            bundle = temporary / "portable.otqbackup"
            manifest = contract.build_bundle(bundle, manifest, self.adapter.components(temporary),
                                               self.key_id, self.key, self.max_bytes)
            record = {"manifest": manifest, "origin": origin, "signing_key_id": self.key_id, **contract.file_digest(bundle)}
            record["storage_id"] = self.adapter.publish(temporary, manifest, contract.signature(manifest, self.key_id, self.key))
            write_json(self.root / "snapshots" / (manifest["snapshot_id"] + ".json"), record)
            return manifest["snapshot_id"]
        finally:
            shutil.rmtree(temporary)

    @contextlib.contextmanager
    def materialize(self, record):
        temporary = self.root / "temporary" / uuid.uuid4().hex
        temporary.mkdir(mode=0o700)
        try:
            self.adapter.materialize(record["storage_id"], temporary)
            bundle = temporary / "portable.otqbackup"
            if bundle.exists():
                signed = contract.verify_bundle(bundle, self.trusted_keys, self.max_bytes)
                with zipfile.ZipFile(bundle) as archive:
                    for name in contract.COMPONENTS:
                        if (temporary / name).exists():
                            if contract.file_digest(temporary / name) != signed["components"][name]:
                                raise WorkerError("Stored backup components changed")
                            continue
                        with archive.open(name) as source, (temporary / name).open("xb") as output:
                            shutil.copyfileobj(source, output)
                components = {name: temporary / name for name in contract.COMPONENTS}
            else:
                components = self.adapter.components(temporary)
                key_id = record["signing_key_id"]
                if key_id not in self.trusted_keys:
                    raise WorkerError("The original signing key requires operator recovery")
                signed = contract.build_bundle(bundle, record["manifest"], components,
                                               key_id, self.trusted_keys[key_id], self.max_bytes)
            if signed != record["manifest"] or contract.file_digest(bundle)["sha256"] != record["sha256"]:
                raise WorkerError("Stored backup bytes changed; operator inspection is required")
            yield bundle, components
        finally:
            shutil.rmtree(temporary)

    def enqueue(self, kind, actor, body, snapshot_id=None, upload_path=None, synchronous=False):
        identifier = operation_id(body.get("operation_id"))
        if kind not in ("backup", "restore", "upload"):
            raise WorkerError("Unknown operation", 400)
        if actor is not None:
            self.authorize(actor)
        elif kind != "backup":
            raise WorkerError("An administrator is required", 403)
        identity = {"kind": kind, "actor": actor, "body": body, "snapshot": snapshot_id}
        if upload_path:
            identity["upload"] = contract.file_digest(upload_path)
        request_hash = hashlib.sha256(contract.canonical(identity)).hexdigest()
        with self.mutex:
            path = self._job_path(identifier)
            if path.exists():
                existing = self.job(identifier)
                if existing["request_hash"] != request_hash:
                    raise WorkerError("This operation identity belongs to a different request")
                if upload_path:
                    Path(upload_path).unlink(missing_ok=True)
                return self.envelope(existing)
            lock = (self.root / "operation.lock").open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise WorkerError("Another backup or recovery operation is running. Wait before retrying.")
            try:
                if kind == "restore":
                    self._check_review(snapshot_id, actor, body, self.current())
                job = {"id": identifier, "operation_id": identifier, "kind": kind, "status": "queued",
                       "stage": "queued", "message": "Operation queued", "created_at": now(), "updated_at": now(),
                       "request_hash": request_hash, "actor": actor, "reason": body.get("reason", "Scheduled backup"),
                       "scheduled": actor is None and body.get("scheduled") is True}
                write_json(path, job)
            except Exception:
                lock.close()
                raise
        arguments = (job, body, snapshot_id, upload_path, lock)
        if synchronous:
            self._execute(*arguments)
        else:
            threading.Thread(target=self._execute, args=arguments, daemon=False).start()
        return self.envelope(self.job(identifier))

    def _execute(self, job, body, snapshot_id, upload_path, lock):
        fenced = False
        try:
            job["status"] = "running"
            self._progress(job, "validating", "Validating the operation and current installation")
            if job["actor"] is not None:
                self.authorize(job["actor"])
            if job["kind"] == "backup":
                self._progress(job, "capturing", "Capturing a consistent full backup")
                job["snapshot_id"] = self._capture("scheduled" if job["scheduled"] else "manual")
            elif job["kind"] == "upload":
                manifest = contract.verify_bundle(upload_path, self.trusted_keys, self.max_bytes)
                self.adapter.validate_uploaded_bundle(upload_path, manifest)
                with zipfile.ZipFile(upload_path) as uploaded:
                    key_id = json.loads(uploaded.read("signature.json"))["key_id"]
                record = {"manifest": manifest, "origin": "upload", "signing_key_id": key_id,
                          **contract.file_digest(upload_path)}
                path = self.root / "snapshots" / (manifest["snapshot_id"] + ".json")
                if path.exists() and json.loads(path.read_text())["sha256"] != record["sha256"]:
                    raise WorkerError("A different backup already uses this identity")
                record["storage_id"] = self.adapter.publish_bundle(upload_path, manifest)
                write_json(path, record)
                job["snapshot_id"] = manifest["snapshot_id"]
            else:
                record = self._check_review(snapshot_id, job["actor"], body, self.current())
                with self.materialize(record) as (bundle, components):
                    manifest = contract.verify_bundle(bundle, self.trusted_keys, self.max_bytes)
                    self.adapter.validate_uploaded_bundle(bundle, manifest)
                    self.authorize(job["actor"])
                    self._check_review(snapshot_id, job["actor"], body, self.current())
                    self._progress(job, "maintenance", "Stopping application activity for full restoration")
                    fenced = True
                    self.adapter.fence_and_stop(job["id"], actor=job["actor"])
                    self._progress(job, "safety_backup", "Saving the current state as a separate safety backup")
                    job["safety_snapshot_id"] = self._capture("safety")
                    self._progress(job, "restoring", "Replacing the complete database and uploaded files")
                    self.adapter.restore(components, manifest)
                    self._progress(job, "verifying", "Clearing sessions, starting and verifying the restored application")
                    self.adapter.verify_and_reopen(manifest)
                    fenced = False
                    job["requires_login"] = True
            job.update(status="succeeded", stage="complete", message="Restore complete. Sign in using an account from the backup." if job["kind"] == "restore" else "Backup operation complete")
        except Exception as error:
            if fenced:
                try:
                    self.adapter.keep_closed(job["id"])
                except Exception:
                    pass
            message = str(error) if isinstance(error, (WorkerError, contract.BackupError)) else "The operation failed. An operator must inspect recovery before retrying."
            job.update(status="failed", stage="failed", message=message)
        finally:
            job["updated_at"] = now()
            write_json(self._job_path(job["id"]), job)
            if upload_path:
                Path(upload_path).unlink(missing_ok=True)
            lock.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass  # Never log authority proofs, capabilities, paths or payloads.

    def _response(self, status, value=None, profile=None):
        raw = contract.canonical(value) if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "private, no-store")
        origin = self.headers.get("Origin")
        if profile and origin in profile.config.get("allowed_origins", []):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _credentials(self):
        header = self.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else ""

    def _profile(self):
        token = self._credentials()
        for profile in self.server.profiles:
            if token and hmac.compare_digest(profile.token, token):
                return profile
        raise WorkerError("Worker authentication required", 401)

    def _actor(self):
        try:
            value = json.loads(self.headers.get("X-Quest-Actor", ""))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise WorkerError("Administrator authority is missing", 403)

    def _length(self, limit):
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise ValueError()
            size = int(self.headers["Content-Length"])
            if not 0 < size <= limit:
                raise ValueError()
            return size
        except ValueError:
            raise WorkerError("The request size is missing or exceeds the allowed limit", 413)

    def _body(self):
        try:
            result = json.loads(self.rfile.read(self._length(16384)))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError, UnicodeError):
            raise WorkerError("Supply a bounded JSON request", 400)

    def _dispatch(self):
        profile = None
        try:
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            if path == "/healthcheck" and self.command == "GET":
                return self._response(200, {"available": True})
            if len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[3] == "status":
                if self.command == "OPTIONS":
                    origin = self.headers.get("Origin")
                    profile = next((p for p in self.server.profiles if origin in p.config.get("allowed_origins", [])), None)
                    if profile is None:
                        raise WorkerError("Origin not permitted", 403)
                    return self._response(204, profile=profile)
                if self.command != "GET":
                    raise WorkerError("Read-only operation status", 405)
                for candidate in self.server.profiles:
                    try:
                        if candidate.check_ticket(parts[2], self._credentials()):
                            profile = candidate
                            return self._response(200, {"job": profile.public_job(profile.job(parts[2]))}, profile)
                    except WorkerError:
                        continue
                raise WorkerError("Operation status capability is invalid or expired", 403)
            profile = self._profile()
            actor = self._actor()
            if path == "/v1/backups":
                if self.command == "GET":
                    return self._response(200, profile.index(actor))
                if self.command == "POST":
                    return self._response(202, profile.enqueue("backup", actor, self._body()))
            if path == "/v1/backups/upload" and self.command == "POST":
                profile.authorize(actor)
                identifier = operation_id(self.headers.get("X-Quest-Operation-Id"))
                remaining = self._length(profile.max_bytes + contract.MAX_MANIFEST_BYTES + 65536)
                upload = profile.root / "temporary" / (uuid.uuid4().hex + ".otqbackup")
                try:
                    with upload.open("xb") as output:
                        upload.chmod(0o600)
                        while remaining:
                            block = self.rfile.read(min(1024 * 1024, remaining))
                            if not block:
                                raise WorkerError("The upload was interrupted", 400)
                            output.write(block)
                            remaining -= len(block)
                    body = {"operation_id": identifier, "reason": unquote(self.headers.get("X-Quest-Reason", ""))}
                    result = profile.enqueue("upload", actor, body, upload_path=upload)
                except Exception:
                    upload.unlink(missing_ok=True)
                    raise
                return self._response(202, result)
            if len(parts) == 3 and parts[:2] == ["v1", "jobs"] and self.command == "GET":
                profile.authorize(actor)
                return self._response(200, profile.envelope(profile.job(parts[2])))
            if len(parts) == 4 and parts[:2] == ["v1", "backups"]:
                identifier, action = parts[2:]
                if action == "review" and self.command == "POST":
                    self._body()
                    return self._response(200, profile.review(identifier, actor))
                if action == "restore" and self.command == "POST":
                    return self._response(202, profile.enqueue("restore", actor, self._body(), identifier))
                if action == "download" and self.command == "GET":
                    profile.authorize(actor)
                    record = profile.snapshot_record(identifier)
                    with profile.materialize(record) as (bundle, _):
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(bundle.stat().st_size))
                        self.send_header("Content-Disposition", 'attachment; filename="quest-backup-' + identifier + '.otqbackup"')
                        self.send_header("Cache-Control", "private, no-store")
                        self.end_headers()
                        with bundle.open("rb") as source:
                            shutil.copyfileobj(source, self.wfile)
                    return
            raise WorkerError("Worker endpoint not found", 404)
        except (WorkerError, contract.BackupError) as error:
            self.close_connection = True
            self._response(getattr(error, "status", 400), {"success": False, "error": str(error)}, profile)
        except Exception:
            self.close_connection = True
            self._response(503, {"success": False, "error": "The backup worker is unavailable; an operator must inspect its status."}, profile)

    do_GET = do_POST = do_OPTIONS = _dispatch


def serve(config, factory):
    server = ThreadingHTTPServer((config.get("listen", "127.0.0.1"), config.get("port", 9087)), Handler)
    server.profiles = [Profile(profile, factory(profile)) for profile in config["profiles"]]
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("action", choices=("serve", "backup"))
    parser.add_argument("--profile")
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    try:
        from .backup_openshift import OpenShiftAdapter
    except ImportError:
        from backup_openshift import OpenShiftAdapter
    if args.action == "serve":
        serve(config, OpenShiftAdapter)
    else:
        profile_config = next(p for p in config["profiles"] if p["id"] == args.profile)
        profile = Profile(profile_config, OpenShiftAdapter(profile_config))
        result = profile.enqueue("backup", None, {"operation_id": str(uuid.uuid4()), "scheduled": args.scheduled}, synchronous=True)
        print(json.dumps(result["job"]))
        if result["job"]["status"] != "succeeded":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
