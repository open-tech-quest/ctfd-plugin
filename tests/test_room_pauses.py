"""Room incidents preserve parallel play, native history and global timing."""
import importlib
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from CTFd.models import Hints, HintUnlocks, Solves, Submissions, Users, db
from CTFd.utils.security.auth import generate_user_token
from CTFd.plugins.open_tech_quest import recovery
from CTFd.plugins.open_tech_quest.models import (QuestAudit, QuestEvent, QuestMaintainer,
    QuestPause, QuestRoom, QuestRoomPause, QuestStart)
from CTFd.plugins.open_tech_quest.service import Projection, standings
from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve
import test_integration as integration

P = integration.PREFIX


class RoomPauseIntegration(unittest.TestCase):
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
        self.assertEqual(self.post(self.admin, P + "/admin/rooms/ansible", {"challenge_ids": [3]}, "PATCH").status_code, 200)
        with self.app.app_context():
            db.session.add(Users(id=5, name="Maintainer", email="staff@example.test", password="test-only"))
            db.session.add(QuestMaintainer(user_id=5, previous_hidden=False))
            db.session.add(Hints(id=2, challenge_id=3, title="Other room", content="Other hint", cost=0))
            db.session.commit()
        self.maintainer = self.client(5)

    def pause_state(self, slug="openshift", client=None):
        return (client or self.admin).get(P + "/rooms/" + slug).json["data"]["pause"]

    def change(self, action="pause", slug="openshift", client=None, revision=None, reason="Lab maintenance"):
        revision = revision or self.pause_state(slug)["revision"]
        return self.post(client or self.admin, P + "/admin/rooms/" + slug + "/pause",
            {"action": action, "reason": reason, "revision": revision})

    def begin(self):
        self.action("start")
        self.start()
        self.assertEqual(self.post(self.alice, P + "/rooms/ansible/start", {}).status_code, 200)

    def test_controls_require_admin_csrf_active_phase_and_reason(self):
        revision = self.pause_state()["revision"]
        with self.guest.session_transaction() as session:
            session["nonce"] = "quest-test-csrf"
        for client, expected in [(self.guest, 401), (self.alice, 403), (self.maintainer, 403)]:
            self.assertEqual(self.change(client=client, revision=revision).status_code, expected)
        self.assertEqual(self.change().status_code, 409)
        self.action("start")
        self.assertEqual(self.change(reason=" ").status_code, 400)
        self.assertEqual(self.admin.post(P + "/admin/rooms/openshift/pause", json={
            "action": "pause", "reason": "Lab outage", "revision": self.pause_state()["revision"]}).status_code, 403)
        self.assertEqual(self.change().status_code, 200)
        self.action("close")
        self.assertEqual(self.change().status_code, 409)
        self.action("publish")
        self.assertEqual(self.change().status_code, 409)

    def test_room_pause_blocks_new_starts_and_leaves_other_rooms_open(self):
        self.action("start")
        self.assertEqual(self.change().status_code, 200)
        self.assertEqual(self.post(self.alice, P + "/rooms/openshift/start", {}).status_code, 409)
        self.assertEqual(self.post(self.alice, P + "/rooms/ansible/start", {}).status_code, 200)
        self.assertEqual(self.solve(self.alice, 3).json["data"]["status"], "correct")
        bootstrap = self.alice.get(P + "/bootstrap").json["data"]
        paused = next(r for r in bootstrap["rooms"] if r["slug"] == "openshift")
        self.assertTrue(paused["pause"]["paused"])
        self.assertEqual(paused["pause"]["reason"], "Lab maintenance")
        self.assertFalse(paused["startable"])
        self.assertEqual(self.change("resume").status_code, 200)
        self.start()

    def test_pause_preserves_history_read_access_and_blocks_attempts_and_new_hints(self):
        self.begin()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.change().status_code, 200)
        result = self.solve(self.bob, 2)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json["data"]["status"], "room_paused")
        self.assertEqual(self.alice.get("/api/v1/challenges/2").status_code, 200)
        self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1}).status_code, 409)
        self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 2}).status_code, 200)
        self.assertEqual(self.solve(self.alice, 3).json["data"]["status"], "correct")
        with self.app.app_context():
            self.assertEqual(Submissions.query.count(), 2)
            self.assertEqual(Solves.query.count(), 2)
            self.assertEqual(HintUnlocks.query.count(), 1)
            self.assertEqual(QuestStart.query.count(), 2)

    def test_already_redeemed_hints_remain_readable_and_idempotent(self):
        self.begin()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        bought = self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1})
        self.assertEqual(bought.status_code, 200)
        self.assertEqual(self.change().status_code, 200)
        self.assertIn("Private hint", self.bob.get("/api/v1/hints/1").get_data(as_text=True))
        self.assertEqual(self.post(self.bob, "/api/v1/unlocks", {"type": "hints", "target": 1}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(HintUnlocks.query.count(), 1)

    def test_overlapping_room_event_pauses_discount_union_and_overall_global_only(self):
        self.begin()
        with self.app.app_context():
            origin = datetime.utcnow() - timedelta(seconds=100)
            e = QuestEvent.query.get(1)
            e.start_at = origin
            for start in QuestStart.query.all():
                start.started_at = origin
            room = QuestRoom.query.filter_by(slug="openshift").one()
            db.session.add_all([
                QuestPause(started_at=origin + timedelta(seconds=20), ended_at=origin + timedelta(seconds=60)),
                QuestRoomPause(room_id=room.id, started_at=origin + timedelta(seconds=40), ended_at=origin + timedelta(seconds=80), reason="Room outage"),
                Solves(challenge_id=1, user_id=2, team_id=self.team_id, date=origin + timedelta(seconds=90)),
                Solves(challenge_id=3, user_id=2, team_id=self.team_id, date=origin + timedelta(seconds=95))])
            db.session.commit()
            projection = Projection(e, at=origin + timedelta(seconds=100))
            progress = projection.progress(room, self.team_id)
            self.assertEqual(progress["elapsed_ms"], 40000)
            self.assertEqual(progress["score_elapsed_ms"], 30000)
            team = next(t for t in standings(e)["teams"] if t["id"] == self.team_id)
            self.assertEqual(team["overall_elapsed_ms"], 55000)
            other = next(r for r in team["room_progress"] if r["room_id"] != room.id and r["solved"])
            self.assertEqual(other["milestone_elapsed_ms"], 55000)

    def test_open_local_pause_freezes_room_clock_but_completed_clock_is_preserved(self):
        self.begin()
        with self.app.app_context():
            origin = datetime.utcnow() - timedelta(seconds=100)
            e = QuestEvent.query.get(1)
            e.start_at = origin
            room = QuestRoom.query.filter_by(slug="openshift").one()
            QuestStart.query.filter_by(room_id=room.id).one().started_at = origin
            db.session.add(QuestRoomPause(room_id=room.id, started_at=origin + timedelta(seconds=30), reason="Lab failure"))
            db.session.commit()
            a = Projection(e, at=origin + timedelta(seconds=60)).progress(room, self.team_id)
            b = Projection(e, at=origin + timedelta(seconds=90)).progress(room, self.team_id)
            self.assertEqual(a["elapsed_ms"], 30000)
            self.assertEqual(a["elapsed_ms"], b["elapsed_ms"])
            db.session.add_all([Solves(challenge_id=1, user_id=2, team_id=self.team_id, date=origin + timedelta(seconds=10)),
                                Solves(challenge_id=2, user_id=2, team_id=self.team_id, date=origin + timedelta(seconds=20))])
            db.session.commit()
            completed = Projection(e).progress(room, self.team_id)
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(completed["elapsed_ms"], 20000)

    def test_global_resume_preserves_local_pause_and_room_resume_does_not_resume_event(self):
        self.begin()
        self.assertEqual(self.change().status_code, 200)
        self.action("pause")
        self.action("resume")
        self.assertTrue(self.pause_state()["paused"])
        self.assertEqual(self.solve(self.alice, 1).status_code, 409)
        self.action("pause")
        self.assertEqual(self.change("resume").status_code, 200)
        self.assertFalse(self.pause_state()["paused"])
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "paused")

    def test_close_reopen_keeps_local_incident_intent_and_finalizes_intervals(self):
        self.begin()
        self.assertEqual(self.change().status_code, 200)
        self.action("close")
        with self.app.app_context():
            interval = QuestRoomPause.query.one()
            self.assertTrue(interval.closed_by_event)
            self.assertEqual(interval.ended_at, QuestEvent.query.get(1).end_at)
        self.action("reopen", reason="Continue after review")
        self.assertTrue(self.pause_state()["paused"])
        self.assertEqual(self.pause_state()["reason"], "Lab maintenance")
        self.action("resume")
        self.assertTrue(self.pause_state()["paused"])
        self.assertEqual(self.change("resume").status_code, 200)

    def test_stale_pause_revisions_and_new_round_epoch_cannot_replay(self):
        self.begin()
        saved = self.pause_state()["revision"]
        self.assertEqual(self.change(revision=saved).status_code, 200)
        self.assertEqual(self.change("resume", revision=saved).status_code, 409)
        self.assertEqual(self.change("resume").status_code, 200)
        saved = self.pause_state()["revision"]
        # Same event/room IDs and timestamps cannot make an old-round action valid.
        with self.app.app_context():
            db.session.add(QuestAudit(action="event.new_round", detail={"synthetic": True}, actor_id=1))
            db.session.commit()
        self.assertEqual(self.change(revision=saved).status_code, 409)

    def test_changes_invalidate_bootstrap_sync_and_public_standings(self):
        self.begin()
        before = self.alice.get(P + "/sync").json["data"]["revisions"]
        self.assertEqual(self.change().status_code, 200)
        after = self.alice.get(P + "/sync").json["data"]["revisions"]
        self.assertNotEqual(before["content"], after["content"])
        self.assertNotEqual(before["progress"], after["progress"])
        metadata = self.guest.get(P + "/standings").json["data"]["rooms"]
        self.assertTrue(next(r for r in metadata if r["slug"] == "openshift")["pause"]["paused"])
        managed = self.maintainer.get(P + "/manage/rooms").json["data"]["rooms"]
        self.assertTrue(next(r for r in managed if r["slug"] == "openshift")["pause"]["paused"])

    def test_concurrent_administrators_create_one_interval(self):
        self.begin()
        revision = self.pause_state()["revision"]
        def change(_):
            return self.change(client=self.client(1), revision=revision).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(change, range(2))), [200, 409])
        with self.app.app_context():
            self.assertEqual(QuestRoomPause.query.count(), 1)
            self.assertEqual(QuestAudit.query.filter_by(action="room.pause").count(), 1)

    def test_admin_token_can_pause_participant_token_cannot_bypass_paused_attempts(self):
        self.begin()
        with self.app.app_context():
            admin_token = generate_user_token(Users.query.get(1)).value
            user_token = generate_user_token(Users.query.get(2)).value
        revision = self.pause_state()["revision"]
        token_client = self.app.test_client()
        headers = {"Authorization": "Token " + admin_token, "Content-Type": "application/json"}
        response = token_client.post(P + "/admin/rooms/openshift/pause",
            json={"action": "pause", "revision": revision, "reason": "Token-operated incident"}, headers=headers)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        response = token_client.post("/api/v1/challenges/attempt",
            json={"challenge_id": 1, "submission": "fixture-answer-1"},
            headers={"Authorization": "Token " + user_token, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["data"]["status"], "room_paused")

    def test_recovery_rechecks_room_pause_after_native_session_boundary(self):
        self.begin()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        with self.app.app_context():
            Users.query.get(2).banned = True
            db.session.commit()
        original_event = recovery.event
        def interrupt(lock=False):
            db.session.rollback()
            room = QuestRoom.query.filter_by(slug="openshift").one()
            db.session.add(QuestRoomPause(room_id=room.id, started_at=datetime.utcnow(), reason="Late room outage"))
            db.session.commit()
            return original_event(lock=lock)
        with patch.object(recovery, "event", side_effect=interrupt):
            response = self.solve(self.bob, 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["data"]["status"], "room_paused")
        with self.app.app_context():
            self.assertEqual(QuestRecoverySolve.query.count(), 0)
            self.assertEqual(Solves.query.count(), 1)

    def test_migration_is_idempotent_and_preserves_room_pause_history(self):
        self.begin()
        self.assertEqual(self.change().status_code, 200)
        migration = importlib.import_module("CTFd.plugins.open_tech_quest.migrations.0012_room_pauses")
        with self.app.app_context():
            db.session.remove()
            with db.engine.begin() as connection:
                operations = Operations(MigrationContext.configure(connection))
                migration.upgrade(operations)
                migration.upgrade(operations)
            self.assertEqual(QuestRoomPause.query.one().reason, "Lab maintenance")
