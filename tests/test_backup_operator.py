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
        self.adapter.repository = self.root / "operator-repository"
        (self.adapter.repository / "repo").mkdir(parents=True)
        self.profile = worker.Profile(self.config, self.adapter)

    def invoke(self, scheduled, action="backup"):
        arguments = ["backup_operator.py", "--config", str(self.path), "--profile", "fixture", action]
        if scheduled:
            arguments.append("--scheduled")
        output = io.StringIO()
        with patch("sys.argv", arguments), patch.object(operator, "OpenShiftAdapter", return_value=self.adapter), contextlib.redirect_stdout(output):
            operator.main()
        return json.loads(output.getvalue())

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
        # Production publication clears the generic marker on any success;
        # operator status must still report the actual timer failure.
        (self.profile.root / "backup-failure.json").unlink()
        self.adapter.schedule = lambda: {"enabled": True, "interval_seconds": 900,
                                         "last_success_at": worker.now(), "next_run_at": None}
        status = self.invoke(False, "status")
        self.assertTrue(status["backup_failed"])
        self.assertEqual(status["schedule"]["last_job"]["id"], failed["id"])
        self.assertIsNone(status["schedule"]["last_success_at"])

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

    def test_snapshots_reconciles_recovered_index_under_existing_lock(self):
        calls = []
        expected = [{"id": "a" * 64, "tags": ["synthetic-test"]}]
        self.adapter.options = {"restic_tag": "synthetic-test", "restic_host": "fixture"}

        def reconcile(directory):
            self.assertEqual(directory, self.profile.root / "snapshots")
            with (self.profile.root / "operation.lock").open("a") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            calls.append("reconcile")

        def restic(arguments):
            self.assertEqual(calls, ["reconcile"])
            self.assertEqual(arguments, ["snapshots", "--tag", "synthetic-test", "--host", "fixture", "--json"])
            return json.dumps(expected).encode()

        self.adapter.sync_inventory, self.adapter.restic = reconcile, restic
        self.assertEqual(self.invoke(False, "snapshots"), expected)
        self.assertEqual(calls, ["reconcile"])
