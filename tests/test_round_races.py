"""Native writers cannot cross an archive/reset or silently cascade plugin data."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from sqlalchemy import Column, ForeignKey, Integer, Table
from CTFd.models import Challenges, TeamComments, Users, db
from CTFd.plugins.open_tech_quest import guards, rounds
from CTFd.plugins.open_tech_quest.models import QuestAudit
import test_integration as integration

PREFIX = integration.PREFIX


class RoundRaceIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve

    def setUp(self):
        integration.QuestIntegration.setUp(self)
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.action("close")

    def require_mariadb(self):
        with self.app.app_context():
            if db.engine.dialect.name == "sqlite":
                self.skipTest("SQLite BEGIN IMMEDIATE serializes every writer; exercise the row-lock race on MariaDB")

    def reset_body(self, mode="new_round"):
        response = self.admin.get(PREFIX + "/admin/round/preview?mode=" + mode)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        preview = response.json["data"]
        return {"revision": preview["revision"], "mode": mode, "confirm": True,
                "event_name": preview["event_name"], "reason": "Isolated archive concurrency regression"}

    def test_unknown_transitive_foreign_keys_block_reset_without_losing_any_rows(self):
        with self.app.app_context():
            parent = Table("round_review_plugin_progress", db.metadata,
                           Column("id", Integer, primary_key=True),
                           Column("team_id", Integer, ForeignKey("teams.id", ondelete="CASCADE")))
            child = Table("round_review_nested_progress", db.metadata,
                          Column("id", Integer, primary_key=True),
                          Column("progress_id", Integer, ForeignKey(parent.c.id, ondelete="CASCADE")))
            parent.create(db.engine)
            child.create(db.engine)
            db.session.execute(parent.insert().values(id=1, team_id=self.team_id))
            db.session.execute(child.insert().values(id=1, progress_id=1))
            db.session.commit()
        try:
            for mode in ("new_round", "defaults"):
                response = self.admin.get(PREFIX + "/admin/round/preview?mode=" + mode)
                self.assertEqual(response.status_code, 200)
                preview = response.json["data"]
                self.assertFalse(preview["allowed"])
                self.assertIn(parent.name, preview["reason"])
                self.assertIn(child.name, preview["reason"])
                result = self.post(self.admin, PREFIX + "/admin/round/reset", {
                    "revision": preview["revision"], "mode": mode, "confirm": True,
                    "event_name": preview["event_name"], "reason": "Must preserve unsupported plugin evidence"})
                self.assertEqual(result.status_code, 409)
                with self.app.app_context():
                    self.assertEqual(len(db.session.execute(parent.select()).all()), 1)
                    self.assertEqual(len(db.session.execute(child.select()).all()), 1)
                    self.assertEqual(QuestAudit.query.filter_by(action="event.archive").count(), 0)
        finally:
            with self.app.app_context():
                db.session.remove()
                child.drop(db.engine)
                parent.drop(db.engine)
                db.metadata.remove(child)
                db.metadata.remove(parent)

    def queued_comment(self, writer_uid, expected_status):
        body = self.reset_body()
        resetter, writer = self.client(1), self.client(writer_uid)
        captured, writer_at_lock, release = (threading.Event() for _ in range(3))
        original_save, original_event = rounds.save_archive, guards.event

        def pause_archive(*args, **kwargs):
            captured.set()
            if not release.wait(15):
                raise AssertionError("Reset was not released by its concurrency test")
            return original_save(*args, **kwargs)

        def observe_native_lock(*args, **kwargs):
            from flask import request
            if kwargs.get("lock") and request.path == "/api/v1/comments":
                writer_at_lock.set()
            return original_event(*args, **kwargs)

        with patch.object(rounds, "save_archive", side_effect=pause_archive), patch.object(guards, "event", side_effect=observe_native_lock), ThreadPoolExecutor(max_workers=2) as pool:
            reset_future = pool.submit(self.post, resetter, PREFIX + "/admin/round/reset", body)
            try:
                self.assertTrue(captured.wait(15), "Reset did not capture its archive source")
                writer_future = pool.submit(self.post, writer, "/api/v1/comments", {
                    "team_id": self.team_id, "content": "Old-round queued organizer note"})
                self.assertTrue(writer_at_lock.wait(10), "Native metadata writer did not enter the shared lock")
                self.assertFalse(writer_future.done(), "Native writer bypassed the reset lock")
            finally:
                release.set()
            reset_result = reset_future.result(timeout=20)
            writer_result = writer_future.result(timeout=20)
        self.assertEqual(reset_result.status_code, 200, reset_result.get_data(as_text=True))
        self.assertEqual(writer_result.status_code, expected_status, writer_result.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(TeamComments.query.count(), 0)
            saved = QuestAudit.query.filter_by(action="event.archive").one().detail["document"]
            self.assertNotIn("Old-round queued organizer note", str(saved))
        return writer

    def test_native_comment_waiting_behind_reset_is_rejected_before_unarchived_write(self):
        self.require_mariadb()
        self.queued_comment(1, 409)

    def test_removed_administrator_cannot_resume_a_queued_native_write(self):
        self.require_mariadb()
        with self.app.app_context():
            db.session.add(Users(id=5, name="Second organizer", email="second@example.test",
                                 type="admin", password="test-only", verified=True))
            db.session.commit()
        writer = self.queued_comment(5, 403)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(5))
        self.assertFalse(writer.get(PREFIX + "/bootstrap").json["data"]["viewer"]["authenticated"])

    def test_mapped_challenge_edit_rechecks_round_after_its_second_lock_boundary(self):
        self.require_mariadb()
        body = self.reset_body()
        writer = self.client(1)
        released_first_lock, resume = threading.Event(), threading.Event()
        identity = {"thread": None, "rollbacks": 0}
        original_rollback = db.session.rollback

        def pause_relock(*args, **kwargs):
            result = original_rollback(*args, **kwargs)
            if threading.get_ident() == identity["thread"]:
                identity["rollbacks"] += 1
                if identity["rollbacks"] == 2:
                    released_first_lock.set()
                    if not resume.wait(15):
                        raise AssertionError("Mapped challenge relock was not resumed")
            return result

        def edit():
            identity["thread"] = threading.get_ident()
            return self.post(writer, "/api/v1/challenges/1", {
                "description": "Old-round edit must not change the new round"}, "PATCH")

        with patch.object(db.session, "rollback", side_effect=pause_relock), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit)
            try:
                self.assertTrue(released_first_lock.wait(15), "Mapped challenge write did not reach its fresh lock boundary")
                reset_result = self.post(self.admin, PREFIX + "/admin/round/reset", body)
            finally:
                resume.set()
            write_result = future.result(timeout=20)
        self.assertEqual(reset_result.status_code, 200, reset_result.get_data(as_text=True))
        self.assertEqual(write_result.status_code, 409, write_result.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(1).description, "Private instructions 1")
            self.assertEqual(QuestAudit.query.filter_by(action="event.archive").count(), 1)


if __name__ == "__main__":
    unittest.main()
