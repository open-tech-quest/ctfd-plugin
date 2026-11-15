"""Portable worker controller tests with a disposable, complete fake provider."""
import copy
import fcntl
import importlib.util
import json
import threading
import shutil
import sys
import tempfile
import unittest
import uuid
import zipfile
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

MODULES = Path(__file__).resolve().parents[1] / "plugin/open_tech_quest"
if not MODULES.exists():
    MODULES = Path("/opt/CTFd/CTFd/plugins/open_tech_quest")
sys.path.insert(0, str(MODULES))
import backup_contract as contract
import backup_worker as worker

ACTOR = {"id": 1, "created_at": "2026-11-12T00:00:00.123456Z", "password_sha256": "a" * 64, "secret_sha256": "b" * 64}


class FakeAdapter:
    def __init__(self, root, instance="fixture"):
        self.root = root
        self.store = root / "repository"
        self.store.mkdir(parents=True)
        self.instance = instance
        self.name, self.origin = "Synthetic event", "https://event.example.test"
        self.runtime_configuration = {"database_credential": "synthetic-destination-credential"}
        self.source = {"ctfd_version": "3.8.9", "plugin_version": "synthetic-version",
                       "schema_revision": "quest_test", "ctfd_schema_revision": "native-test", "image_digest": "sha256:" + "c" * 64}
        self.state = {"accounts": [{"id": 4, "password_hash": "synthetic-old-hash"}], "progress": [1], "uploads": "old uploaded data"}
        self.generation = "generation-one"
        self.authorized, self.closed, self.sessions_cleared = True, False, False
        self.events, self.fail = [], None

    def actor_valid(self, actor):
        return self.authorized and actor == ACTOR

    def current(self):
        return {"target": {"id": self.instance, "name": self.name, "origin": self.origin, "current": copy.deepcopy(self.source)},
                "generation": self.generation, "counts": {"users": len(self.state["accounts"]), "teams": 1, "challenges": 2, "solves": len(self.state["progress"])}}

    def configuration_reason(self, manifest, current):
        return None

    def schedule(self):
        return {"enabled": True, "interval_seconds": 900, "last_success_at": None, "next_run_at": None}

    def components(self, directory):
        return {name: directory / name for name in contract.COMPONENTS}

    def capture(self, directory):
        self.events.append("capture")
        if self.fail == "capture":
            raise RuntimeError("synthetic capture failure")
        (directory / "database.sql").write_bytes(contract.canonical(self.state))
        (directory / "uploads.tar.gz").write_text(self.state["uploads"])
        (directory / "infrastructure.json").write_bytes(contract.canonical({"source": self.source, "fixture": True}))
        return {"source": copy.deepcopy(self.source), "instance": {"id": self.instance, "name": self.name}, "counts": self.current()["counts"]}

    def publish(self, temporary, manifest, signature):
        storage = self.store / manifest["snapshot_id"]
        shutil.copytree(temporary, storage)
        (storage / "manifest.json").write_bytes(contract.canonical(manifest))
        (storage / "signature.json").write_bytes(contract.canonical(signature))
        return storage.name

    def materialize(self, storage_id, temporary):
        for path in (self.store / storage_id).iterdir():
            if path.name != "portable.otqbackup":
                shutil.copyfile(path, temporary / path.name)

    def publish_bundle(self, path, manifest, record):
        storage = self.store / manifest["snapshot_id"]
        storage.mkdir(exist_ok=True)
        with zipfile.ZipFile(path) as bundle:
            for name in (*contract.COMPONENTS, "manifest.json", "signature.json"):
                (storage / name).write_bytes(bundle.read(name))
        return storage.name

    def validate_uploaded_bundle(self, path, manifest):
        self.events.append("validate")

    def fence_and_stop(self, identifier, actor=None):
        self.events.append("fence_and_stop")
        self.closed = True
        if self.fail == "stop":
            raise RuntimeError("synthetic stop failure")

    def restore(self, components, manifest):
        assert self.closed
        self.events.append("restore")
        if self.fail == "restore":
            raise RuntimeError("synthetic replacement failure")
        self.state = json.loads(components["database.sql"].read_bytes())

    def verify_and_reopen(self, manifest):
        self.events.append("verify")
        if self.fail == "verify":
            raise RuntimeError("synthetic readiness failure")
        self.sessions_cleared = True
        self.closed = False
        self.events.append("reopen")

    def keep_closed(self, identifier):
        self.events.append("keep_closed")
        self.closed = True


class BackupWorker(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quest-worker-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = FakeAdapter(self.root)
        key, token = self.root / "signing.key", self.root / "worker.token"
        key.write_bytes(b"synthetic-signing-key-not-a-real-key" * 2)
        token.write_text("synthetic-worker-token-not-used-outside-tests")
        self.config = {"state_root": str(self.root / "journal"), "signing_key_file": str(key), "signing_key_id": "test-key",
                       "token_file": str(token), "public_origin": "https://backup.example.test", "instance_id": "fixture",
                       "allowed_origins": ["https://event.example.test"], "max_backup_bytes": 1024 * 1024}
        self.profile = worker.Profile(self.config, self.adapter)

    def backup(self):
        operation = self.profile.enqueue("backup", ACTOR, {"operation_id": str(uuid.uuid4()), "reason": "Synthetic backup"}, synchronous=True)
        self.assertEqual(operation["job"]["status"], "succeeded")
        return self.profile.job(operation["job"]["id"])["snapshot_id"]

    def restore_body(self, snapshot):
        reviewed = self.profile.review(snapshot, ACTOR)
        return {"operation_id": str(uuid.uuid4()), "review_token": reviewed["review_token"], "confirmation_text": reviewed["confirmation_text"],
                "confirm": True, "reason": "Synthetic full recovery"}

    def foreign_backup(self, trusted=True):
        source_adapter = FakeAdapter(self.root / "source", instance="source-installation")
        source_adapter.name, source_adapter.origin = "Synthetic source", "https://source.example.test"
        source_adapter.runtime_configuration = {"database_credential": "different-source-credential"}
        source_adapter.state = {"accounts": [{"id": 7, "password_hash": "source-password-hash"}],
                                "progress": [7, 8], "uploads": "source uploaded bytes"}
        key = self.root / "source-signing.key"
        key.write_bytes(b"separate-synthetic-source-signing-key" * 2)
        source_config = dict(self.config, state_root=str(self.root / "source-journal"),
                             instance_id=source_adapter.instance, signing_key_file=str(key),
                             signing_key_id="source-key")
        source = worker.Profile(source_config, source_adapter)
        operation = source.enqueue("backup", ACTOR, {"operation_id": str(uuid.uuid4()),
                                   "reason": "Source recovery point"}, synchronous=True)
        self.assertEqual(operation["job"]["status"], "succeeded")
        snapshot = source.job(operation["job"]["id"])["snapshot_id"]
        record = source.snapshot_record(snapshot)
        with source.materialize(record) as (bundle, _):
            path = self.root / "foreign-upload.otqbackup"
            path.write_bytes(bundle.read_bytes())
        if trusted:
            self.config["trusted_key_files"] = {"source-key": str(key)}
            self.profile = worker.Profile(self.config, self.adapter)
        return source, source_adapter, record, path

    def test_cross_installation_signed_upload_review_and_queued_restore_preserve_target(self):
        source, source_adapter, record, path = self.foreign_backup()
        source_state = copy.deepcopy(source_adapter.state)
        target_state = copy.deepcopy(self.adapter.state)
        target_configuration = copy.deepcopy(self.adapter.runtime_configuration)
        target_identity = self.adapter.current()["target"]
        original_signature = record["manifest"]
        uploaded = self.profile.enqueue("upload", ACTOR, {"operation_id": str(uuid.uuid4()),
                                        "reason": "Import recovery point from source"},
                                        upload_path=path, synchronous=True)
        self.assertEqual(uploaded["job"]["status"], "succeeded")
        snapshot = self.profile.job(uploaded["job"]["id"])["snapshot_id"]
        self.assertEqual(self.profile.snapshot_record(snapshot)["manifest"], original_signature)
        self.assertEqual(self.adapter.state, target_state)
        reviewed = self.profile.review(snapshot, ACTOR)
        self.assertTrue(reviewed["cross_installation"])
        self.assertEqual(reviewed["snapshot"]["instance"], record["manifest"]["instance"])
        self.assertEqual(reviewed["target"], target_identity)
        self.assertEqual(reviewed["confirmation_text"], target_identity["name"])
        warnings = " ".join(reviewed["warnings"])
        self.assertIn(source_adapter.name, warnings)
        self.assertIn(self.adapter.name, warnings)
        self.assertIn("credential", warnings.lower())
        body = {"operation_id": str(uuid.uuid4()), "review_token": reviewed["review_token"],
                "confirmation_text": reviewed["confirmation_text"], "confirm": True,
                "reason": "Restore source recovery point on replacement installation"}
        pending = []
        class DeferredThread:
            def __init__(self, target, args, **kwargs):
                pending.append((target, args))
            def start(self):
                pass
        self.adapter.events.clear()
        with patch.object(worker.threading, "Thread", DeferredThread):
            operation = self.profile.enqueue("restore", ACTOR, body, snapshot)
        self.assertEqual(operation["job"]["status"], "queued")
        execute, arguments = pending.pop()
        execute(*arguments)
        job = self.profile.job(operation["job"]["id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(self.adapter.state, source_state)
        self.assertEqual(source_adapter.state, source_state)
        self.assertEqual(self.adapter.runtime_configuration, target_configuration)
        self.assertEqual(self.adapter.current()["target"], target_identity)
        self.assertTrue(self.adapter.sessions_cleared)
        self.assertFalse(self.adapter.closed)
        self.assertEqual(self.adapter.events, ["validate", "fence_and_stop", "capture", "restore", "verify", "reopen"])
        safety = self.profile.snapshot_record(job["safety_snapshot_id"])
        self.assertEqual(safety["manifest"]["instance"]["id"], target_identity["id"])
        with self.profile.materialize(safety) as (_, components):
            self.assertEqual(json.loads(components["database.sql"].read_bytes()), target_state)

    def test_foreign_installation_does_not_make_untrusted_backup_importable(self):
        _, _, _, path = self.foreign_backup(trusted=False)
        original = copy.deepcopy(self.adapter.state)
        operation = self.profile.enqueue("upload", ACTOR, {"operation_id": str(uuid.uuid4()),
                                         "reason": "Attempt untrusted recovery"},
                                         upload_path=path, synchronous=True)
        self.assertEqual(operation["job"]["status"], "failed")
        self.assertIn("trusted", operation["job"]["message"])
        self.assertEqual(self.adapter.state, original)
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(list((self.profile.root / "snapshots").glob("*.json")), [])

    def test_foreign_installation_keeps_image_schema_and_target_review_guards(self):
        _, _, record, path = self.foreign_backup()
        operation = self.profile.enqueue("upload", ACTOR, {"operation_id": str(uuid.uuid4()),
                                         "reason": "Import source recovery"},
                                         upload_path=path, synchronous=True)
        self.assertEqual(operation["job"]["status"], "succeeded")
        snapshot = record["manifest"]["snapshot_id"]
        saved = copy.deepcopy(self.adapter.source)
        for field, changed in (("image_digest", "sha256:" + "f" * 64),
                               ("schema_revision", "other-schema"),
                               ("ctfd_schema_revision", "other-native-schema")):
            with self.subTest(field=field):
                self.adapter.source[field] = changed
                with self.assertRaises(worker.WorkerError):
                    self.profile.review(snapshot, ACTOR)
                self.adapter.source = copy.deepcopy(saved)
        body = self.restore_body(snapshot)
        self.adapter.generation = "new-destination-runtime-credentials"
        with self.assertRaises(worker.WorkerError):
            self.profile.enqueue("restore", ACTOR, body, snapshot, synchronous=True)
        self.assertNotIn("fence_and_stop", self.adapter.events)

    def test_full_replacement_has_durable_safety_snapshot_before_any_replace(self):
        original = copy.deepcopy(self.adapter.state)
        snapshot = self.backup()
        self.adapter.state = {"accounts": [{"id": 1, "password_hash": "synthetic-current-admin"}, {"id": 9}], "progress": [1, 2, 3], "uploads": "new uploaded data"}
        latest = copy.deepcopy(self.adapter.state)
        self.adapter.events.clear()
        operation = self.profile.enqueue("restore", ACTOR, self.restore_body(snapshot), snapshot, synchronous=True)
        self.assertEqual(operation["job"]["status"], "succeeded")
        self.assertTrue(operation["job"]["requires_login"])
        self.assertEqual(self.adapter.state, original)
        self.assertNotIn(1, [account["id"] for account in self.adapter.state["accounts"]])
        self.assertTrue(self.adapter.sessions_cleared)
        self.assertFalse(self.adapter.closed)
        events = self.adapter.events
        self.assertLess(events.index("fence_and_stop"), events.index("capture"))
        self.assertLess(events.index("capture"), events.index("restore"))
        safety = self.profile.snapshot_record(operation["job"]["safety_snapshot_id"])
        self.assertEqual(safety["origin"], "safety")
        with self.profile.materialize(safety) as (_, components):
            self.assertEqual(json.loads(components["database.sql"].read_bytes()), latest)
        recreated = worker.Profile(self.config, self.adapter)
        self.assertEqual(recreated.job(operation["job"]["id"])["status"], "succeeded")

    def test_stop_or_restore_or_readiness_failure_keeps_maintenance_closed(self):
        snapshot = self.backup()
        for failure in ("stop", "restore", "verify"):
            with self.subTest(failure=failure):
                self.adapter.fail = failure
                self.adapter.events.clear()
                operation = self.profile.enqueue("restore", ACTOR, self.restore_body(snapshot), snapshot, synchronous=True)
                self.assertEqual(operation["job"]["status"], "failed")
                self.assertTrue(self.adapter.closed)
                self.assertIn("keep_closed", self.adapter.events)
                self.assertNotIn("reopen", self.adapter.events)
                self.adapter.closed = False

    def test_expired_target_changed_or_different_actor_review_never_fences(self):
        snapshot = self.backup()
        body = self.restore_body(snapshot)
        self.adapter.events.clear()
        self.adapter.generation = "new-target-generation"
        with self.assertRaises(worker.WorkerError):
            self.profile.enqueue("restore", ACTOR, body, snapshot, synchronous=True)
        self.assertNotIn("fence_and_stop", self.adapter.events)
        self.adapter.generation = "generation-one"
        with patch.object(worker.time, "time", return_value=10 ** 12):
            with self.assertRaises(worker.WorkerError):
                self.profile.enqueue("restore", ACTOR, body, snapshot, synchronous=True)
        changed_actor = dict(ACTOR, password_sha256="d" * 64)
        with self.assertRaises(worker.WorkerError):
            self.profile.enqueue("restore", changed_actor, body, snapshot, synchronous=True)
        self.assertNotIn("fence_and_stop", self.adapter.events)

    def test_exact_operation_retry_is_idempotent_and_conflicting_request_refuses(self):
        body = {"operation_id": str(uuid.uuid4()), "reason": "Synthetic backup"}
        first = self.profile.enqueue("backup", ACTOR, body, synchronous=True)
        again = self.profile.enqueue("backup", ACTOR, body, synchronous=True)
        self.assertEqual(first, again)
        self.assertEqual(self.adapter.events.count("capture"), 1)
        with self.assertRaisesRegex(worker.WorkerError, "different request"):
            self.profile.enqueue("backup", ACTOR, dict(body, reason="Different reason"), synchronous=True)

    def test_materialized_download_is_byte_identical_and_status_ticket_is_scoped(self):
        snapshot = self.backup()
        record = self.profile.snapshot_record(snapshot)
        with self.profile.materialize(record) as (path, _):
            self.assertEqual(contract.file_digest(path)["sha256"], record["sha256"])
        operation = self.profile.index(ACTOR)
        self.assertEqual(len(operation["snapshots"]), 1)
        job = next((self.profile.root / "jobs").glob("*.json"))
        identifier = job.stem
        ticket = self.profile.ticket(self.profile.job(identifier))["status_token"]
        self.assertTrue(self.profile.check_ticket(identifier, ticket))
        self.assertFalse(self.profile.check_ticket(identifier, "invalid-capability"))
        self.assertNotIn("actor", self.profile.public_job(self.profile.job(identifier)))
        self.assertNotIn("reason", self.profile.public_job(self.profile.job(identifier)))

    def test_queued_restore_revalidates_actor_before_fence_or_safety_capture(self):
        snapshot = self.backup()
        pending = []
        class DeferredThread:
            def __init__(self, target, args, **kwargs):
                pending.append((target, args))
            def start(self):
                pass
        self.adapter.events.clear()
        with patch.object(worker.threading, "Thread", DeferredThread):
            operation = self.profile.enqueue("restore", ACTOR, self.restore_body(snapshot), snapshot)
        self.assertEqual(operation["job"]["status"], "queued")
        self.adapter.authorized = False
        target, args = pending.pop()
        target(*args)
        job = self.profile.job(operation["job"]["id"])
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("capture", self.adapter.events)
        self.assertNotIn("fence_and_stop", self.adapter.events)

    def test_upload_identity_binds_exact_bytes_and_download_reconstructs_same_bundle(self):
        snapshot = self.backup()
        original = self.profile.snapshot_record(snapshot)
        body = {"operation_id": str(uuid.uuid4()), "reason": "Synthetic upload"}
        with self.profile.materialize(original) as (bundle, _):
            raw = bundle.read_bytes()
        first_path = self.root / "first-upload.otqbackup"
        first_path.write_bytes(raw)
        operation = self.profile.enqueue("upload", ACTOR, body, upload_path=first_path, synchronous=True)
        self.assertEqual(operation["job"]["status"], "succeeded")
        self.assertFalse(first_path.exists())
        repeated_path = self.root / "repeated.otqbackup"
        repeated_path.write_bytes(raw)
        repeated = self.profile.enqueue("upload", ACTOR, body, upload_path=repeated_path, synchronous=True)
        self.assertEqual(repeated, operation)
        conflicting_path = self.root / "different.otqbackup"
        conflicting_path.write_bytes(raw + b"different bytes")
        with self.assertRaisesRegex(worker.WorkerError, "different request"):
            self.profile.enqueue("upload", ACTOR, body, upload_path=conflicting_path, synchronous=True)
        with self.profile.materialize(self.profile.snapshot_record(snapshot)) as (bundle, _):
            self.assertEqual(bundle.read_bytes(), raw)

    def test_http_status_capability_is_read_only_and_cors_is_origin_bounded(self):
        self.backup()
        job_path = next((self.profile.root / "jobs").glob("*.json"))
        job = self.profile.job(job_path.stem)
        capability = self.profile.ticket(job)["status_token"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.Handler)
        server.profiles = [self.profile]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        path = "/v1/jobs/" + job["id"] + "/status"
        headers = {"Authorization": "Bearer " + capability, "Origin": "https://event.example.test"}
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=3) as response:
            result = json.load(response)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://event.example.test")
            self.assertNotIn("actor", result["job"])
            self.assertNotIn("ticket", result)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/v1/backups", headers=headers), timeout=3)
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + path, headers={"Origin": "https://untrusted.example.test"}, method="OPTIONS"), timeout=3)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={"Origin": "https://event.example.test"}, method="OPTIONS"), timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Headers"], "Authorization")
            self.assertNotIn("POST", response.headers["Access-Control-Allow-Methods"])

    def test_retired_but_trusted_signing_key_keeps_existing_download_identity(self):
        snapshot = self.backup()
        record = self.profile.snapshot_record(snapshot)
        old_key = self.root / "trusted-old.key"
        old_key.write_bytes(self.profile.key)
        new_key = self.root / "new.key"
        new_key.write_bytes(b"new-synthetic-signing-key-for-rotation" * 2)
        rotated_config = dict(self.config, signing_key_file=str(new_key), signing_key_id="new-key",
                              trusted_key_files={"test-key": str(old_key)})
        rotated = worker.Profile(rotated_config, self.adapter)
        with rotated.materialize(record) as (path, _):
            self.assertEqual(contract.file_digest(path)["sha256"], record["sha256"])
            self.assertEqual(contract.verify_bundle(path, rotated.trusted_keys), record["manifest"])
        untrusted = worker.Profile(dict(rotated_config, trusted_key_files={}), self.adapter)
        compatibility = untrusted.index(ACTOR)["snapshots"][0]["compatibility"]
        self.assertFalse(compatibility["supported"])
        self.assertIn("key", compatibility["reason"])

    def test_status_capability_is_bound_to_profile_even_with_shared_trust_key(self):
        stamp = worker.now()
        body = {"operation_id": str(uuid.uuid4()), "reason": "Synthetic backup"}
        other_adapter = FakeAdapter(self.root / "other", instance="other-instance")
        other = worker.Profile(dict(self.config, state_root=str(self.root / "other-journal"), instance_id="other-instance"), other_adapter)
        with patch.object(worker, "now", return_value=stamp):
            first = self.profile.enqueue("backup", ACTOR, body, synchronous=True)
            second = other.enqueue("backup", ACTOR, body, synchronous=True)
        self.assertNotEqual(first["ticket"]["status_token"], second["ticket"]["status_token"])
        self.assertFalse(other.check_ticket(body["operation_id"], first["ticket"]["status_token"]))

    def test_last_scheduled_failure_survives_later_manual_ui_and_cli_backups(self):
        self.adapter.fail = "capture"
        scheduled = self.profile.enqueue("backup", None, {"operation_id": str(uuid.uuid4()), "scheduled": True}, synchronous=True)
        self.assertEqual(scheduled["job"]["status"], "failed")
        self.adapter.fail = None
        self.backup()
        manual_cli = self.profile.enqueue("backup", None, {"operation_id": str(uuid.uuid4()), "scheduled": False}, synchronous=True)
        self.assertEqual(manual_cli["job"]["status"], "succeeded")
        # The provider's latest-success record includes manual captures too.
        self.adapter.schedule = lambda: {"enabled": True, "interval_seconds": 900,
                                         "last_success_at": worker.now(), "next_run_at": None}
        schedule = self.profile.index(ACTOR)["schedule"]
        self.assertEqual(schedule["last_job"]["id"], scheduled["job"]["id"])
        self.assertEqual(schedule["last_job"]["status"], "failed")
        self.assertIsNone(schedule["last_success_at"])
        self.assertIsNone(self.profile.overview(ACTOR)["schedule"]["last_success_at"])

    def test_prior_producer_requires_exact_explicit_target_qualification(self):
        self.backup()
        prior = copy.deepcopy(self.adapter.source)
        self.adapter.source.update(plugin_version="different-tested-build", image_digest="sha256:" + "d" * 64)
        self.assertFalse(self.profile.index(ACTOR)["snapshots"][0]["compatibility"]["supported"])
        self.config["qualified_restores"] = [{"source": prior, "target": copy.deepcopy(self.adapter.source)}]
        self.assertTrue(self.profile.index(ACTOR)["snapshots"][0]["compatibility"]["supported"])
        self.adapter.source["schema_revision"] = "unqualified-next-schema"
        self.assertFalse(self.profile.index(ACTOR)["snapshots"][0]["compatibility"]["supported"])

    def test_download_refuses_during_exclusive_operation_and_retains_signed_recovery_index(self):
        snapshot = self.backup()
        record = self.profile.snapshot_record(snapshot)
        durable = json.loads((self.adapter.store / record["storage_id"] / "portable-record.json").read_text())
        self.assertEqual(durable["manifest"], record["manifest"])
        self.assertEqual(durable["sha256"], record["sha256"])
        with (self.profile.root / "operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(worker.WorkerError, "Wait before downloading"):
                with self.profile.materialize(record):
                    self.fail("Concurrent download acquired an exclusive operation's repository")
        with self.profile.materialize(record) as (bundle, _):
            self.assertEqual(contract.file_digest(bundle)["sha256"], record["sha256"])
