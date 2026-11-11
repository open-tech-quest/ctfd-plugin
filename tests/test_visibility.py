"""Native challenge visibility across Quest paths, grading and prize clocks."""
from datetime import datetime, timedelta
import unittest

import test_integration as integration
from CTFd.cache import clear_user_session
from CTFd.models import Challenges, Hints, Solves, Users, db
from CTFd.plugins.open_tech_quest.models import (
    QuestEvent, QuestMaintainer, QuestStart, QuestStep,
)
from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve

PREFIX = integration.PREFIX
ROOM = PREFIX + "/rooms/openshift"
WORKSPACE = PREFIX + "/manage/rooms/openshift/challenges"


class VisibilityIntegration(unittest.TestCase):
    # Share real CTFd fixtures without discovering the parent suite again.
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
        self.configure([1, 2, 3])
        with self.app.app_context():
            db.session.add(Users(id=5, name="Maintainer", email="maintainer@example.test",
                                 password="test-only", verified=True, hidden=True))
            db.session.flush()
            db.session.add(QuestMaintainer(user_id=5, previous_hidden=False))
            db.session.commit()
        self.maintainer = self.client(5)

    def visibility(self, challenge_id, state):
        with self.app.app_context():
            active = QuestEvent.query.get(1).status in {"live", "paused"}
        response = (integration.reviewed_change(self, self.admin, WORKSPACE + "/" + str(challenge_id), {"state": state}) if active else
                    self.post(self.admin, "/api/v1/challenges/" + str(challenge_id), {"state": state}, "PATCH"))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def room(self, client=None):
        response = (client or self.alice).get(ROOM)
        self.assertEqual(response.status_code, 200)
        return response.json["data"]

    def standing(self):
        response = self.guest.get(PREFIX + "/standings")
        self.assertEqual(response.status_code, 200)
        data = response.json["data"]
        return data, next(team for team in data["teams"] if team["id"] == self.team_id)

    def test_hidden_metadata_is_omitted_but_staff_mapping_and_preview_survive(self):
        self.visibility(2, "hidden")
        for client in (self.guest, self.alice):
            room = self.room(client)
            self.assertEqual([step["id"] for step in room["steps"]], [1, 3])
            self.assertEqual([step["position"] for step in room["steps"]], [0, 1])
            self.assertEqual(room["progress"]["total"], 2)
            for path in (PREFIX + "/bootstrap", PREFIX + "/rooms", ROOM):
                self.assertNotIn("Challenge 2", client.get(path).get_data(as_text=True))
        preview = self.room(self.admin)
        self.assertEqual(preview["challenge_ids"], [1, 2, 3])
        self.assertEqual([step["id"] for step in preview["steps"]], [1, 2, 3])
        self.assertEqual([step["position"] for step in preview["steps"]], [0, 1, 2])
        self.assertEqual(preview["steps"][1]["state"], "available")
        self.assertEqual(self.admin.get("/api/v1/challenges/2").status_code, 200)
        workspace = self.maintainer.get(WORKSPACE).json["data"]
        self.assertEqual([item["id"] for item in workspace["challenges"]], [1, 2, 3])
        self.assertEqual(workspace["challenges"][1]["state"], "hidden")
        preview = self.post(self.maintainer, WORKSPACE + "/2/preview",
                            {"submission": "fixture-answer-2"})
        self.assertEqual(preview.json["data"]["status"], "correct")
        with self.app.app_context():
            self.assertEqual(Solves.query.count(), 0)
            self.assertEqual(QuestStart.query.count(), 0)

    def test_hidden_middle_step_is_skipped_by_room_and_native_grading(self):
        with self.app.app_context():
            db.session.add(Hints(id=2, challenge_id=3, title="Final hint",
                                 content="Fixture final hint", cost=0))
            db.session.commit()
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.visibility(2, "hidden")
        room = self.room()
        self.assertEqual([step["state"] for step in room["steps"]], ["solved", "available"])
        self.assertEqual(self.bob.get("/api/v1/challenges/3").status_code, 200)
        self.assertEqual(self.bob.get("/api/v1/hints/2").status_code, 200)
        self.assertEqual(self.solve(self.bob, 3).json["data"]["status"], "correct")
        self.assertEqual(self.room()["progress"]["status"], "complete")
        for path in ("/api/v1/challenges/2", "/api/v1/challenges/2/files",
                     "/api/v1/hints/1", "/files/known-hash/private.txt"):
            self.assertEqual(self.alice.get(path).status_code, 404)
        self.assertEqual(self.solve(self.alice, 2).status_code, 404)
        with self.app.app_context():
            # Visibility must retain the organizer's full room mapping/order.
            self.assertEqual([step.challenge_id for step in QuestStep.query.order_by(
                QuestStep.position).all()], [1, 2, 3])

    def test_hidden_leading_step_releases_first_real_content_and_hint(self):
        self.visibility(1, "hidden")
        self.action("start")
        room = self.start()
        self.assertTrue(room["startable"])
        self.assertEqual([step["id"] for step in room["steps"]], [2, 3])
        self.assertEqual(room["steps"][0]["state"], "available")
        content = self.alice.get("/api/v1/challenges/2")
        self.assertEqual(content.status_code, 200)
        self.assertIn("known-hash/private.txt", str(content.json["data"]["files"]))
        self.assertEqual(self.alice.get("/api/v1/hints/1").status_code, 200)
        self.assertEqual(self.solve(self.alice, 2).json["data"]["status"], "correct")
        self.assertEqual(self.solve(self.bob, 3).json["data"]["status"], "correct")
        # Restoring a skipped step reinstates its position and prerequisite.
        self.visibility(1, "visible")
        restored = self.room()
        self.assertEqual([step["id"] for step in restored["steps"]], [1, 2, 3])
        self.assertEqual([step["state"] for step in restored["steps"]],
                         ["available", "locked", "locked"])
        self.assertEqual(self.alice.get("/api/v1/challenges/2").status_code, 403)
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.room()["progress"]["status"], "complete")

    def test_hidden_solves_leave_awards_and_restore_original_times_when_visible(self):
        self.action("start")
        self.start()
        # Native submission timestamps have second precision on MariaDB.
        base = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=100)
        with self.app.app_context():
            QuestEvent.query.get(1).start_at = base
            QuestStart.query.one().started_at = base + timedelta(seconds=10)
            for cid, seconds in ((1, 20), (2, 50), (3, 80)):
                db.session.add(Solves(challenge_id=cid, user_id=2, team_id=self.team_id,
                                      provided="fixture-answer-" + str(cid),
                                      date=base + timedelta(seconds=seconds)))
            db.session.commit()
            original = [(row.id, row.challenge_id, row.date, row.provided)
                        for row in Solves.query.order_by(Solves.id).all()]
        _, full = self.standing()
        self.assertEqual(full["total"], 3)
        self.assertEqual(full["overall_elapsed_ms"], 80000)
        self.assertEqual(full["room_progress"][0]["milestone_elapsed_ms"], 70000)
        self.visibility(3, "hidden")
        board, partial = self.standing()
        self.assertEqual(partial["total"], 2)
        self.assertEqual(partial["overall_elapsed_ms"], 50000)
        self.assertEqual(partial["room_progress"][0]["milestone_elapsed_ms"], 40000)
        self.assertEqual(partial["room_progress"][0]["total"], 2)
        self.assertEqual(board["rooms"][0]["total"], 2)
        self.assertEqual([step["id"] for step in self.room()["steps"]], [1, 2])
        self.assertEqual(self.alice.get("/api/v1/challenges/3").status_code, 404)
        self.visibility(3, "visible")
        _, restored = self.standing()
        self.assertEqual(restored, full)
        self.visibility(2, "hidden")
        _, middle_removed = self.standing()
        self.assertEqual(middle_removed["total"], 2)
        self.assertEqual(middle_removed["overall_elapsed_ms"], 80000)
        self.assertEqual(middle_removed["room_progress"][0]["solved"], 2)
        self.assertEqual(middle_removed["room_progress"][0]["milestone_elapsed_ms"], 70000)
        with self.app.app_context():
            self.assertEqual([(row.id, row.challenge_id, row.date, row.provided)
                              for row in Solves.query.order_by(Solves.id).all()], original)

    def test_locked_solved_step_blocks_content_without_withdrawing_earned_credit(self):
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.visibility(1, "locked")
        room = self.room()
        self.assertEqual(room["steps"][0]["state"], "locked")
        self.assertIsNotNone(room["steps"][0]["solved_at"])
        self.assertEqual(room["steps"][1]["state"], "available")
        self.assertEqual(room["progress"]["total"], 3)
        self.assertEqual(room["progress"]["solved"], 1)
        self.assertEqual(self.alice.get("/api/v1/challenges/1").status_code, 404)
        self.assertEqual(self.alice.get("/api/v1/challenges/2").status_code, 200)
        self.assertEqual(self.standing()[1]["total"], 1)

    def test_locked_unsolved_step_remains_a_gate_until_organizer_unlocks_it(self):
        self.visibility(2, "locked")
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        room = self.room()
        self.assertEqual([step["id"] for step in room["steps"]], [1, 2, 3])
        self.assertEqual([step["state"] for step in room["steps"]],
                         ["solved", "locked", "locked"])
        self.assertEqual(self.alice.get("/api/v1/challenges/2").status_code, 404)
        self.assertEqual(self.alice.get("/api/v1/challenges/3").status_code, 403)
        self.assertEqual(self.solve(self.bob, 3).status_code, 403)
        self.visibility(2, "visible")
        self.assertEqual(self.bob.get("/api/v1/challenges/2").status_code, 200)
        self.assertEqual(self.solve(self.bob, 2).json["data"]["status"], "correct")
        self.assertEqual(self.solve(self.alice, 3).json["data"]["status"], "correct")

    def test_unplayable_room_cannot_start_and_existing_clock_is_idempotent(self):
        self.action("start")
        for cid in (1, 2, 3):
            self.visibility(cid, "hidden")
        self.assertFalse(self.room()["startable"])
        self.assertEqual(self.room()["progress"]["total"], 0)
        self.assertEqual(self.post(self.alice, ROOM + "/start", {}).status_code, 409)
        self.visibility(1, "locked")
        self.visibility(2, "visible")
        self.assertFalse(self.room()["startable"])
        self.assertEqual(self.post(self.alice, ROOM + "/start", {}).status_code, 409)
        with self.app.app_context():
            self.assertEqual(QuestStart.query.count(), 0)
        self.visibility(1, "hidden")
        started = self.start()
        self.visibility(2, "hidden")
        repeated = self.start(self.bob)
        self.assertEqual(repeated["progress"]["started_at"], started["progress"]["started_at"])
        with self.app.app_context():
            self.assertEqual(QuestStart.query.count(), 1)

    def test_native_locked_challenge_roundtrips_full_editor_payload(self):
        self.visibility(2, "locked")
        workspace = self.maintainer.get(WORKSPACE).json["data"]
        item = next(item for item in workspace["challenges"] if item["id"] == 2)
        self.assertTrue(item["can_edit"])
        draft = {key: item[key] for key in ("name", "description", "state", "flags")}
        draft["description"] = "Updated **locked** task"
        response = self.post(self.maintainer, WORKSPACE + "/2", draft, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["data"]["state"], "locked")
        self.assertEqual(response.json["data"]["description"], draft["description"])
        for state in ("hidden", "visible", "locked"):
            draft.update(name="Fixture " + state, state=state)
            response = self.post(self.maintainer, WORKSPACE, draft)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.json["data"]["state"], state)
        self.assertEqual(self.post(self.maintainer, WORKSPACE + "/2",
                                   {"state": "unknown"}, "PATCH").status_code, 400)

    def test_startup_backfill_repairs_old_native_requirements_without_history_changes(self):
        from CTFd.plugins.open_tech_quest.room_topology import initialize_visibility
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        with self.app.app_context():
            starts = [(row.id, row.team_id, row.room_id, row.started_at)
                      for row in QuestStart.query.all()]
            solves = [(row.id, row.challenge_id, row.user_id, row.date)
                      for row in Solves.query.all()]
            steps = [(row.id, row.challenge_id, row.position) for row in QuestStep.query.all()]
            # Simulate a previous release: hidden state with the old full chain.
            db.session.execute(Challenges.__table__.update().where(
                Challenges.id == 1).values(state="hidden"))
            db.session.commit()
            self.assertEqual(Challenges.query.get(2).requirements["prerequisites"], [1])
            initialize_visibility()
            initialize_visibility()
            self.assertEqual(Challenges.query.get(2).requirements["prerequisites"], [])
            self.assertEqual(Challenges.query.get(3).requirements["prerequisites"], [2])
            self.assertEqual(Challenges.query.get(2).next_id, 3)
            self.assertEqual([(row.id, row.team_id, row.room_id, row.started_at)
                              for row in QuestStart.query.all()], starts)
            self.assertEqual([(row.id, row.challenge_id, row.user_id, row.date)
                              for row in Solves.query.all()], solves)
            self.assertEqual([(row.id, row.challenge_id, row.position)
                              for row in QuestStep.query.all()], steps)
            self.assertEqual(QuestEvent.query.get(1).status, "live")
        self.assertEqual(self.bob.get("/api/v1/challenges/2").status_code, 200)

    def test_invalid_native_patch_keeps_visibility_and_derived_chain_atomic(self):
        self.visibility(2, "hidden")
        response = self.post(self.admin, "/api/v1/challenges/2",
                             {"state": "visible", "value": "invalid"}, "PATCH")
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(2).state, "hidden")
            self.assertEqual(Challenges.query.get(3).requirements["prerequisites"], [1])
            self.assertEqual(Challenges.query.get(1).next_id, 3)
        self.assertEqual([step["id"] for step in self.room()["steps"]], [1, 3])

    def test_recovery_grading_skips_hidden_prerequisite_and_keeps_new_credit_time(self):
        self.visibility(1, "hidden")
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 2).json["data"]["status"], "correct")
        with self.app.app_context():
            original = Solves.query.filter_by(challenge_id=2).one()
            original_id, original_at = original.id, original.date
            Users.query.get(2).banned = True
            db.session.commit()
            clear_user_session(2)
        self.assertEqual(self.room(self.bob)["steps"][0]["state"], "available")
        before = datetime.utcnow()
        recovered = self.solve(self.bob, 2)
        after = datetime.utcnow()
        self.assertEqual(recovered.status_code, 200, recovered.get_data(as_text=True))
        self.assertEqual(recovered.json["data"]["status"], "correct")
        with self.app.app_context():
            recovery = QuestRecoverySolve.query.one()
            self.assertEqual(recovery.original_solve_id, original_id)
            self.assertEqual(recovery.credited_user_id, 3)
            self.assertGreaterEqual(recovery.credited_at, before)
            self.assertLessEqual(recovery.credited_at, after)
            self.assertEqual(Solves.query.get(original_id).date, original_at)
        self.assertEqual(self.standing()[1]["total"], 1)
        self.assertEqual(self.solve(self.bob, 3).json["data"]["status"], "correct")
