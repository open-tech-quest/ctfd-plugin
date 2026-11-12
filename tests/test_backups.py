"""Native Flask/session boundary with a real, disposable HTTP worker stub."""
import hashlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import test_authoring as authoring_tests
import test_integration as integration
from CTFd.models import Users, db
from CTFd import __version__ as ctfd_version
from CTFd.utils import get_config
from CTFd.plugins.open_tech_quest import backups
from CTFd.plugins.open_tech_quest.models import QuestEvent
from CTFd.plugins.open_tech_quest.service import event
from CTFd.utils.security.signing import hmac as session_hmac
from CTFd.utils.security.auth import generate_user_token

PREFIX = integration.PREFIX + "/admin/backups"
OP = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class BackupsIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action

    def setUp(self):
        authoring_tests.AuthoringIntegration.setUp(self)
        self.calls, self.forced, self.probe = [], None, None
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                case.calls.append((self.command, self.path, dict(self.headers), raw))
                if case.probe:
                    case.probe()
                status, headers = 200, {}
                if case.forced:
                    status, value, headers = case.forced
                elif self.path.endswith("/download"):
                    value = b"synthetic-signed-backup-bytes"
                    headers["Content-Type"] = "application/zip"
                elif self.path == "/v1/backups" and self.command == "GET":
                    value = {"available": True, "unavailable_reason": None, "target": {"id": "fixture"},
                        "schedule": {"enabled": True, "last_job": {"status": "failed", "message": "Synthetic timer failure"}},
                        "snapshots": [], "active_job": case.operation()}
                elif self.path.endswith("/review"):
                    value = {"review_token": "opaque-test-review", "confirmation_text": "RESTORE Synthetic event", "full_replace": True,
                        "safety_backup_required": True, "comparison": {"current_counts": {"users": 5}, "snapshot_counts": {"users": 3}}}
                else:
                    value = case.operation()
                payload = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", headers.pop("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.marker = Path(self.temp.name) / "maintenance.json"
        self.app.config.update(OTQ_BACKUP_WORKER_URL=self.origin,
            OTQ_BACKUP_WORKER_TOKEN="synthetic-worker-token", OTQ_BACKUP_STATUS_ORIGIN=self.origin,
            OTQ_BACKUP_MAX_UPLOAD_BYTES=1024 * 1024, OTQ_BACKUP_MAINTENANCE_FILE=str(self.marker))
        self.addCleanup(self.marker.unlink, missing_ok=True)

    def operation(self):
        return {"job": {"id": OP, "operation_id": OP, "kind": "backup", "status": "queued", "stage": "queued",
                        "message": "Synthetic operation", "created_at": "2026-11-12T00:00:00Z", "updated_at": "2026-11-12T00:00:00Z"},
                "ticket": {"status_url": self.origin + "/v1/jobs/" + OP + "/status", "status_token": "synthetic-read-only-status-capability"}}

    def domain(self):
        with self.app.app_context():
            db.session.remove()
            return {table.name: hashlib.sha256(json.dumps(sorted(repr(tuple(row))
                    for row in db.session.execute(table.select()))).encode()).hexdigest()
                    for table in db.metadata.sorted_tables if table.name != "tracking"}

    def test_only_session_administrators_can_reach_any_worker_operation(self):
        for client, expected in ((self.guest, 401), (self.alice, 403), (self.maintainer, 403)):
            for method, path, data in (("GET", PREFIX, None), ("GET", PREFIX + "?view=summary", None),
                    ("GET", PREFIX + "/snapshots?day=2026-11-12", None), ("POST", PREFIX, {"reason": "Synthetic backup", "operation_id": OP}),
                    ("POST", PREFIX + "/snapshot/review", {}), ("GET", PREFIX + "/snapshot/download", None),
                    ("GET", PREFIX + "/jobs/" + OP, None), ("POST", PREFIX + "/snapshot/restore", {})):
                response = client.open(path, method=method, json=data, headers={"CSRF-Token": "quest-test-csrf"})
                self.assertEqual(response.status_code, expected)
        self.assertEqual(self.calls, [])
        response = self.admin.get(PREFIX, headers={"Authorization": "Token synthetic-invalid", "Content-Type": "application/json"})
        self.assertIn(response.status_code, (401, 403))
        with self.app.app_context():
            token = generate_user_token(Users.query.get(1)).value
        response = self.guest.get(PREFIX, headers={"Authorization": "Token " + token, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_maintenance_fences_native_and_quest_requests_before_auth_and_after_lock(self):
        before = self.domain()
        self.marker.write_text('{"active":true,"job_id":"' + OP + '"}')
        for path in (PREFIX, integration.PREFIX + "/bootstrap", "/login", "/api/v1/challenges"):
            response = self.admin.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertTrue(response.json["maintenance"])
        self.assertEqual(self.admin.get("/healthcheck").status_code, 200)
        from CTFd.plugins.open_tech_quest.service import event
        with self.app.app_context():
            self.assertEqual(event(lock=True).id, 1)
            db.session.rollback()  # Startup/worker inspection must work while HTTP stays fenced.
        self.marker.write_text("incomplete worker marker")
        self.assertEqual(self.admin.get(PREFIX).status_code, 503)
        self.marker.unlink()
        from CTFd.plugins.open_tech_quest import guards
        original = guards.event
        def fence_while_waiting(*args, **kwargs):
            self.marker.write_text('{"active":true}')
            return original(*args, **kwargs)
        with patch.object(guards, "event", side_effect=fence_while_waiting):
            response = self.post(self.admin, "/api/v1/challenges/1", {"description": "Must not be saved"}, "PATCH")
            self.assertEqual(response.status_code, 503)
        self.marker.unlink()
        self.assertEqual(before, self.domain())
        self.assertEqual(self.calls, [])

    def test_worker_http_never_holds_the_event_lock_and_failure_never_retries(self):
        probes = []
        def probe():
            with self.app.app_context():
                with db.engine.connect() as connection:
                    transaction = connection.begin()
                    if db.engine.dialect.name == "sqlite":
                        connection.execute("BEGIN IMMEDIATE")
                    else:
                        connection.execute("SELECT id FROM quest_events WHERE id=1 FOR UPDATE NOWAIT")
                    transaction.rollback()
                probes.append(True)
        self.probe = probe
        self.assertEqual(self.admin.get(PREFIX).status_code, 200)
        self.assertEqual(probes, [True])
        self.probe = None
        self.forced = (409, {"error": "Snapshot changed; review this restore again"}, {})
        response = self.post(self.admin, PREFIX + "/snapshot/review", {})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"], "Snapshot changed; review this restore again")
        self.assertEqual(len(self.calls), 2)
        self.forced = (302, b"redirect rejected", {"Location": "http://untrusted.example.test/credentials"})
        self.assertEqual(self.admin.get(PREFIX).status_code, 502)
        self.assertEqual(len(self.calls), 3)

    def test_unconfigured_worker_and_untrusted_status_tickets_have_clear_refusals(self):
        self.app.config["OTQ_BACKUP_WORKER_TOKEN"] = ""
        response = self.admin.get(PREFIX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["data"]["available"])
        self.assertIsNone(response.json["data"]["target"])
        summary = self.admin.get(PREFIX + "?view=summary")
        self.assertEqual(summary.status_code, 200)
        self.assertFalse(summary.json["data"]["available"])
        self.assertEqual(summary.json["data"]["groups"], [])
        self.assertNotIn("snapshots", summary.json["data"])
        self.assertIsNone(summary.json["data"]["current_versions"])
        self.assertEqual(self.calls, [])
        self.app.config["OTQ_BACKUP_WORKER_TOKEN"] = "synthetic-worker-token"
        operation = self.operation()
        operation["ticket"]["status_url"] = "https://untrusted.example.test/v1/jobs/" + OP + "/status"
        self.forced = (200, operation, {})
        response = self.post(self.admin, PREFIX, {"reason": "Synthetic backup", "operation_id": OP})
        self.assertEqual(response.status_code, 502)

    def test_lazy_history_proxies_only_selected_private_metadata(self):
        before = self.domain()
        overview = {"available": True, "target": {"id": "fixture", "name": "Synthetic event", "origin": "https://event.example.test"},
                    "groups": [], "catalog_revision": "synthetic-index", "schedule": None, "active_job": None}
        self.forced = (200, overview, {})
        response = self.admin.get(PREFIX + "?view=summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1], "/v1/backups?view=summary")
        self.assertEqual(response.json["data"]["worker_status_origin"], self.origin)
        self.assertNotIn("snapshots", response.json["data"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        page = {"day": "2026-11-12", "snapshots": [], "next_cursor": None, "total": 0, "catalog_revision": "synthetic-index"}
        self.forced = (200, page, {})
        response = self.admin.get(PREFIX + "/snapshots?day=2026-11-12&limit=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1], "/v1/backups/snapshots?day=2026-11-12&limit=20")
        self.assertEqual(response.json["data"], page)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(before, self.domain())

    def test_bad_history_selectors_never_contact_the_worker(self):
        for path in ("?view=", "?view=summary&view=summary", "?view=all", "?unknown=value", "?view=summary&unknown=value",
                     "/snapshots", "/snapshots?day=", "/snapshots?day=2026-02-30", "/snapshots?day=2026-11-12&limit=0",
                     "/snapshots?day=2026-11-12&limit=51", "/snapshots?day=2026-11-12&limit=01", "/snapshots?day=2026-11-12&limit=1.5",
                     "/snapshots?day=2026-11-12&day=2026-11-13", "/snapshots?%64ay=2026-11-12&day=2026-11-13",
                     "/snapshots?day=2026-11-12&before=bad",
                     "/snapshots?day=2026-11-12&unknown=value", "/snapshots?day=2026-11-12&before=" + "a" * 2000):
            response = self.admin.get(PREFIX + path)
            self.assertEqual(response.status_code, 400, path)
        self.assertEqual(self.calls, [])

    def test_summary_versions_come_from_local_runtime_without_holding_a_remote_read_transaction(self):
        with self.app.app_context():
            expected_schema = str(get_config("open_tech_quest_alembic_version") or "unknown")
            try:
                expected_native = str(db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() or "unknown")
            except SQLAlchemyError:
                expected_native = "unknown"
            finally:
                db.session.rollback()
        before = self.domain()
        self.forced = (200, {"available": True, "groups": [], "catalog_revision": "fixture",
                            "current_versions": {"schema_revision": "untrusted-remote-label"}}, {})
        original = backups.Worker.json
        probes = []

        def remote_read(remote, *args, **kwargs):
            self.assertFalse(db.session().in_transaction())
            probes.append(args[1])
            return original(remote, *args, **kwargs)

        with patch.dict(os.environ, {"OTQ_PLUGIN_VERSION": "synthetic-local-build"}), patch.object(backups.Worker, "json", remote_read):
            response = self.admin.get(PREFIX + "?view=summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["current_versions"], {
            "ctfd_version": ctfd_version, "plugin_version": "synthetic-local-build",
            "schema_revision": expected_schema, "ctfd_schema_revision": expected_native})
        self.assertEqual(probes, ["/backups?view=summary"])
        self.assertEqual([call[1] for call in self.calls], ["/v1/backups?view=summary"])
        self.assertNotIn("password_sha256", response.get_data(as_text=True))
        self.assertNotIn("synthetic-worker-token", response.get_data(as_text=True))
        self.assertEqual(before, self.domain())

    def test_create_review_restore_jobs_and_download_are_private_proxy_only(self):
        before = self.domain()
        response = self.admin.get(PREFIX)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.json["data"]["schedule"]["last_job"]["status"], "failed")
        self.assertEqual(response.json["data"]["active_job"]["ticket"]["status_url"], self.operation()["ticket"]["status_url"])
        self.assertNotIn("synthetic-worker-token", response.get_data(as_text=True))
        self.assertNotIn("password_sha256", response.get_data(as_text=True))
        self.assertEqual(self.post(self.admin, PREFIX, {"reason": "Synthetic backup", "operation_id": OP}).status_code, 202)
        reviewed = self.post(self.admin, PREFIX + "/snapshot/review", {})
        self.assertTrue(reviewed.json["data"]["full_replace"])
        applied = self.post(self.admin, PREFIX + "/snapshot/restore", {"review_token": "opaque-test-review",
            "reason": "Synthetic full replacement", "confirmation": "RESTORE Synthetic event", "operation_id": OP})
        self.assertEqual(applied.status_code, 202)
        forwarded = json.loads(self.calls[-1][3])
        self.assertEqual(forwarded["confirmation_text"], "RESTORE Synthetic event")
        self.assertTrue(forwarded["confirm"])
        self.assertNotIn("confirmation", forwarded)
        self.assertEqual(self.admin.get(PREFIX + "/jobs/" + OP).status_code, 200)
        downloaded = self.admin.get(PREFIX + "/snapshot/download")
        self.assertEqual(downloaded.data, b"synthetic-signed-backup-bytes")
        self.assertEqual(downloaded.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        proof = json.loads(self.calls[0][2]["X-Quest-Actor"])
        self.assertEqual(set(proof), {"id", "created_at", "password_sha256", "secret_sha256"})
        self.assertEqual(proof["id"], 1)
        self.assertRegex(proof["password_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(before, self.domain())

    def test_upload_requires_native_nonce_and_header_and_streams_no_native_file(self):
        before = self.domain()
        data = {"nonce": "quest-test-csrf", "reason": "Synthetic portable upload", "operation_id": OP,
                "file": (io.BytesIO(b"opaque-synthetic-backup"), "fixture.otqbackup")}
        response = self.admin.post(PREFIX + "/upload", data=data, headers={"CSRF-Token": "quest-test-csrf"})
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        method, path, headers, raw = self.calls[-1]
        self.assertEqual((method, path, raw), ("POST", "/v1/backups/upload", b"opaque-synthetic-backup"))
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["X-Quest-Operation-Id"], OP)
        self.assertEqual(before, self.domain())
        self.calls.clear()
        response = self.admin.post(PREFIX, json={"reason": "Synthetic backup", "operation_id": OP})
        self.assertIn(response.status_code, (400, 403))
        self.assertEqual(self.calls, [])
        self.app.config["OTQ_BACKUP_MAX_UPLOAD_BYTES"] = 4
        data["file"] = (io.BytesIO(b"synthetic-too-large"), "fixture.otqbackup")
        response = self.admin.post(PREFIX + "/upload", data=data, headers={"CSRF-Token": "quest-test-csrf"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.calls, [])

    def test_post_lock_role_and_password_changes_do_not_dispatch_worker_calls(self):
        original = backups.locked_event
        def demote():
            db.session.rollback()
            Users.query.filter_by(id=1).update({"type": "user"})
            db.session.commit()
            return original()
        with patch.object(backups, "locked_event", side_effect=demote):
            self.assertEqual(self.admin.get(PREFIX).status_code, 403)
        self.assertEqual(self.calls, [])
        with self.app.app_context():
            user = Users.query.get(1)
            user.type = "admin"
            old_hash = session_hmac(user.password)
            db.session.commit()
        with self.admin.session_transaction() as signed:
            signed["hash"] = old_hash
        def change_password():
            db.session.rollback()
            Users.query.get(1).password = "synthetic-new-password"
            db.session.commit()
            return original()
        with patch.object(backups, "locked_event", side_effect=change_password):
            self.assertIn(self.admin.get(PREFIX).status_code, (401, 403))
        self.assertEqual(self.calls, [])
