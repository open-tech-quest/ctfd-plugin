"""Scheduled CLI refusals are durable health events, never silent successes."""
import contextlib
import fcntl
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_backup_worker import FakeAdapter, ACTOR, worker
import backup_operator as operator


class BackupOperator(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        os.umask(previous_umask)
        self.addCleanup(os.umask, previous_umask)
        self.temporary = tempfile.TemporaryDirectory(prefix="quest-operator-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "key").write_bytes(b"synthetic-worker-test-key" * 3)
        (self.root / "token").write_text("synthetic-worker-test-token-not-real")
        self.config = {"id": "fixture", "instance_id": "fixture", "state_root": str(self.root / "state"),
                       "signing_key_file": str(self.root / "key"), "signing_key_id": "fixture",
                       "token_file": str(self.root / "token"), "public_origin": "https://backup.example.test"}
        self.path = self.root / "worker.json"
        self.path.write_text(json.dumps({"profiles": [self.config]}))
        self.adapter = FakeAdapter(self.root)
        self.profile = worker.Profile(self.config, self.adapter)

    def invoke(self, scheduled):
        arguments = ["backup_operator.py", "--config", str(self.path), "--profile", "fixture", "backup"]
        if scheduled:
            arguments.append("--scheduled")
        with patch("sys.argv", arguments), patch.object(operator, "OpenShiftAdapter", return_value=self.adapter), contextlib.redirect_stdout(io.StringIO()):
            operator.main()

    def test_busy_schedule_records_failed_attempt_without_touching_active_operation(self):
        sentinel = self.profile.root / "recovery.json"
        sentinel.write_text('{"stage":"restoring"}')
        with (self.profile.root / "operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SystemExit) as stopped:
                self.invoke(True)
            self.assertEqual(stopped.exception.code, 1)
            with self.assertRaises(BlockingIOError):
                with (self.profile.root / "operation.lock").open("a") as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(sentinel.read_text(), '{"stage":"restoring"}')
        failed = self.profile.index(ACTOR)["schedule"]["last_job"]
        self.assertEqual((failed["status"], failed["stage"]), ("failed", "not_started"))
        self.assertTrue((self.profile.root / "backup-failure.json").exists())
        # A later manual success must not hide this refused timer attempt.
        self.invoke(False)
        self.assertEqual(self.profile.index(ACTOR)["schedule"]["last_job"]["id"], failed["id"])

    def test_busy_manual_cli_does_not_claim_a_scheduled_attempt(self):
        with (self.profile.root / "operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(worker.WorkerError):
                self.invoke(False)
        self.assertIsNone(self.profile.index(ACTOR)["schedule"]["last_job"])
        self.assertEqual(list((self.profile.root / "jobs").glob("*.json")), [])

    def test_capture_failure_keeps_one_existing_failed_scheduled_job(self):
        self.adapter.fail = "capture"
        with self.assertRaises(SystemExit) as stopped:
            self.invoke(True)
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(len(list((self.profile.root / "jobs").glob("*.json"))), 1)
        self.assertEqual(self.profile.index(ACTOR)["schedule"]["last_job"]["status"], "failed")
