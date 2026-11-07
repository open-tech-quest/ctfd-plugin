"""Shared read reuse must preserve native ownership, visibility and time rules."""
from datetime import datetime, timedelta
from unittest.mock import patch
import unittest

import test_integration as integration
from CTFd.cache import cache
from CTFd.models import Awards, Challenges, Solves, Users, db
from CTFd.plugins.open_tech_quest import read_cache, service
from CTFd.plugins.open_tech_quest.models import QuestEvent, QuestPause


class ReadCacheIntegration(unittest.TestCase):
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
        self.start(self.alice)
        self.start(self.carol)

    def room(self, client=None):
        response = (client or self.alice).get(integration.PREFIX + "/rooms/openshift")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]


    def shared(self):
        with self.app.app_context():
            return read_cache.shared_reads_enabled()

    def test_teammates_share_raw_inputs_but_other_team_stays_isolated(self):
        with patch.object(service, "_metadata_inputs", wraps=service._metadata_inputs) as metadata, patch.object(service, "_history_inputs", wraps=service._history_inputs) as history:
            first = self.room(self.alice)
            second = self.room(self.bob)
            self.assertEqual(first["steps"], second["steps"])
            self.assertEqual(metadata.call_count, 1 if self.shared() else 2)
            self.assertEqual(history.call_count, 1 if self.shared() else 2)
            self.room(self.carol)
            self.assertEqual(metadata.call_count, 1 if self.shared() else 3)
            self.assertEqual(history.call_count, 2 if self.shared() else 3)
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.room(self.bob)["progress"]["solved"], 1)
        self.assertEqual(self.room(self.carol)["progress"]["solved"], 0)
        self.assertEqual(self.room(self.guest)["progress"]["solved"], 0)

    def test_other_team_solve_updates_global_standings_without_evicting_own_history(self):
        self.room()
        before = self.alice.get(integration.PREFIX + "/standings").json["data"]
        self.assertEqual(next(t["total"] for t in before["teams"] if t["id"] == self.other_id), 0)
        self.assertEqual(self.solve(self.carol, 1).json["data"]["status"], "correct")
        with patch.object(service, "_history_inputs", wraps=service._history_inputs) as history:
            self.assertEqual(self.room()["progress"]["solved"], 0)
            self.assertEqual(history.call_count, 0 if self.shared() else 1)
            after = self.alice.get(integration.PREFIX + "/standings").json["data"]
            self.assertEqual(next(t["total"] for t in after["teams"] if t["id"] == self.other_id), 1)
            self.assertEqual(history.call_count, 1 if self.shared() else 2)

    def test_bulk_visibility_change_immediately_withdraws_cached_step(self):
        self.assertEqual(len(self.room()["steps"]), 2)
        with self.app.app_context():
            Challenges.query.filter_by(id=1).update({"state": "hidden"}, synchronize_session=False)
            db.session.commit()
        room = self.room()
        self.assertEqual([s["id"] for s in room["steps"]], [2])
        self.assertEqual(room["progress"]["total"], 1)
        self.assertEqual(self.alice.get("/api/v1/challenges/1").status_code, 404)

    def test_raw_sql_ban_removes_cached_credit_and_prerequisite_access(self):
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.room(self.bob)["steps"][1]["state"], "available")
        with self.app.app_context():
            db.session.execute("UPDATE users SET banned = 1 WHERE id = 2")
            db.session.commit()
        self.assertEqual(self.room(self.bob)["progress"]["solved"], 0)
        self.assertEqual(self.bob.get("/api/v1/challenges/2").status_code, 403)
        self.assertFalse(self.bob.get("/api/v1/challenges/1").json["data"]["solved_by_me"])

    def test_read_during_rolled_back_write_cannot_poison_previous_generation(self):
        self.room()
        with self.app.test_request_context(method="GET"):
            original = read_cache.versions(self.team_id)
            before = service.projection(team_id=self.team_id)
            self.assertEqual(before.challenges[1].name, "Challenge 1")
            Challenges.query.filter_by(id=1).update({"name": "Uncommitted"}, synchronize_session=False)
            self.assertNotEqual(read_cache.versions(self.team_id)["content"], original["content"])
            changed = service.projection(team_id=self.team_id)
            self.assertEqual(changed.challenges[1].name, "Uncommitted")
            db.session.rollback()
            self.assertEqual(read_cache.versions(self.team_id), original)
            restored = service.projection(team_id=self.team_id)
            self.assertEqual(restored.challenges[1].name, "Challenge 1")
        self.assertEqual(self.room()["steps"][0]["title"], "Challenge 1")

    def test_delete_invalidates_native_solve_and_new_owner_credit(self):
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.room(self.bob)["progress"]["solved"], 1)
        with self.app.app_context():
            row = Solves.query.filter_by(team_id=self.team_id, challenge_id=1).one()
            db.session.delete(row)
            db.session.commit()
        self.assertEqual(self.room(self.bob)["progress"]["solved"], 0)

    def test_future_contribution_becomes_eligible_without_write_or_expiration(self):
        future = datetime.utcnow() + timedelta(seconds=10, microseconds=123456)
        with self.app.app_context():
            db.session.add(Solves(challenge_id=1, user_id=2, team_id=self.team_id, date=future))
            db.session.commit()
        with patch.object(service, "now", return_value=future - timedelta(seconds=1)):
            self.assertEqual(self.room()["progress"]["solved"], 0)
        with patch.object(service, "_history_inputs", wraps=service._history_inputs) as history, patch.object(service, "now", return_value=future + timedelta(seconds=1)):
            self.assertEqual(self.room()["progress"]["solved"], 1)
            self.assertEqual(history.call_count, 0 if self.shared() else 1)
            with self.app.test_request_context(method="GET"):
                self.assertEqual(service.projection(team_id=self.team_id).solves[self.team_id][1], future)

    def test_scheduled_cutoff_uses_raw_contribution_times_after_cache_hit(self):
        cutoff = datetime.utcnow() + timedelta(seconds=10)
        with self.app.app_context():
            QuestEvent.query.get(1).end_at = cutoff
            db.session.add(Solves(challenge_id=1, user_id=2, team_id=self.team_id, date=cutoff + timedelta(seconds=1)))
            db.session.commit()
        with patch.object(service, "now", return_value=cutoff - timedelta(seconds=1)):
            self.assertEqual(self.room()["progress"]["solved"], 0)
        with patch.object(service, "now", return_value=cutoff + timedelta(seconds=2)):
            self.assertEqual(self.room()["progress"]["solved"], 0)
        with self.app.app_context():
            QuestEvent.query.get(1).end_at = None
            db.session.commit()
        with patch.object(service, "now", return_value=cutoff + timedelta(seconds=2)):
            self.assertEqual(self.room()["progress"]["solved"], 1)

    def test_pause_changes_rebuild_inputs_and_open_pause_clock_remains_live(self):
        at = datetime.utcnow()
        self.room()
        with self.app.app_context():
            db.session.add(QuestPause(started_at=at))
            db.session.commit()
        with patch.object(service, "now", return_value=at + timedelta(seconds=1)):
            before = self.room()["progress"]["elapsed_ms"]
        with patch.object(service, "now", return_value=at + timedelta(seconds=20)):
            self.assertEqual(self.room()["progress"]["elapsed_ms"], before)
        with self.app.app_context():
            QuestPause.query.one().ended_at = at + timedelta(seconds=10)
            db.session.commit()
        with patch.object(service, "now", return_value=at + timedelta(seconds=20)):
            self.assertEqual(self.room()["progress"]["elapsed_ms"], before + 10000)

    def test_private_activity_versions_do_not_evict_other_users_rosters(self):
        with self.app.app_context():
            original = read_cache.versions(self.team_id, 2)
            other = read_cache.versions(self.other_id, 4)
            db.session.add(Awards(user_id=2, name="Fixture", value=0))
            db.session.commit()
            updated = read_cache.versions(self.team_id, 2)
            self.assertNotEqual(updated["user"], original["user"])
            self.assertEqual(read_cache.versions(self.other_id, 4)["user"], other["user"])

    def test_write_and_outside_request_projections_never_use_shared_cache(self):
        self.room()
        with self.app.test_request_context(method="POST"):
            with patch.object(read_cache.cache, "get", side_effect=AssertionError("A write consulted shared cache")):
                self.assertEqual(service.projection(team_id=self.team_id).solves, {})
        with self.app.app_context():
            with patch.object(read_cache.cache, "get", side_effect=AssertionError("An authoritative calculation consulted shared cache")):
                self.assertEqual(service.projection(team_id=self.team_id).solves, {})



    def test_repeatable_read_late_reader_only_populates_its_old_generation(self):
        if not self.shared():
            self.skipTest("Shared cache requires verified MariaDB/MySQL REPEATABLE READ")
        from concurrent.futures import ThreadPoolExecutor
        def writer():
            with self.app.app_context():
                db.session.add(Solves(challenge_id=1, user_id=2, team_id=self.team_id, date=datetime.utcnow()))
                db.session.commit()
        with self.app.test_request_context(method="GET"):
            before = service.projection(team_id=self.team_id)
            self.assertEqual(before.solves, {})
            old = read_cache.versions(self.team_id)["team"]
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(writer).result(timeout=10)
            # A cache miss after the writer commits must still use this
            # transaction's old revision and old history. It must never fill
            # the new writer generation with its repeatable-read snapshot.
            read_cache.clear_request_reads()
            self.assertEqual(read_cache.versions(self.team_id)["team"], old)
            original_get = read_cache.cache.get
            def miss_shared(key, *args, **kwargs):
                return None if key.startswith("quest-read-") else original_get(key, *args, **kwargs)
            with patch.object(read_cache.cache, "get", side_effect=miss_shared):
                self.assertEqual(service.projection(team_id=self.team_id).solves, {})
            db.session.rollback()
            self.assertNotEqual(read_cache.versions(self.team_id)["team"], old)
            self.assertIn(1, service.projection(team_id=self.team_id).solves[self.team_id])


    def test_read_committed_connection_override_bypasses_shared_inputs(self):
        if not self.shared():
            self.skipTest("Isolation override test requires MySQL/MariaDB")
        with self.app.test_request_context(method="GET"):
            db.session.connection(execution_options={"isolation_level": "READ COMMITTED"})
            self.assertFalse(read_cache.shared_reads_enabled())
            with patch.object(read_cache.cache, "get", side_effect=AssertionError("READ COMMITTED consulted shared inputs")):
                self.assertEqual(service.projection(team_id=self.team_id).solves, {})

    def test_event_get_memo_is_cleared_on_raw_write_rollback_and_lock(self):
        from flask import g
        with self.app.test_request_context(method="GET"):
            first = service.event()
            original = first.name
            self.assertIs(service.event(), first)
            db.session.execute("UPDATE quest_events SET name = 'Uncommitted event' WHERE id = 1")
            self.assertEqual(service.event().name, "Uncommitted event")
            db.session.rollback()
            self.assertEqual(service.event().name, original)
            locked = service.event(lock=True)
            self.assertEqual(locked.name, original)
            self.assertFalse(hasattr(g, "quest_read_event"))
            db.session.rollback()

    def test_cache_failure_reads_authoritative_database(self):
        expected = self.room()["steps"]
        original_get, original_set = read_cache.cache.get, read_cache.cache.set
        def unavailable_get(key, *args, **kwargs):
            if key.startswith("quest-read-"):
                raise RuntimeError("shared read cache unavailable")
            return original_get(key, *args, **kwargs)
        def unavailable_set(key, *args, **kwargs):
            if key.startswith("quest-read-"):
                raise RuntimeError("shared read cache unavailable")
            return original_set(key, *args, **kwargs)
        with patch.object(read_cache.cache, "get", side_effect=unavailable_get), patch.object(read_cache.cache, "set", side_effect=unavailable_set), patch.object(service, "_history_inputs", wraps=service._history_inputs) as history:
            self.assertEqual(self.room(self.bob)["steps"], expected)
            self.assertEqual(history.call_count, 1)
