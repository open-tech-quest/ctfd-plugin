"""Isolated room authoring against native CTFd, on SQLite and MariaDB."""
import json
import unittest
from unittest.mock import patch

import test_integration as integration
from CTFd.cache import clear_user_session
from CTFd.models import (ChallengeFiles, Challenges, Fails, Flags, HintUnlocks,
                         Hints, Ratings, Solves, Submissions, Users, db)
from CTFd.plugins.open_tech_quest.models import (QuestAudit, QuestCorrection,
    QuestEvent, QuestMaintainer, QuestRoom, QuestStart, QuestStep)
from CTFd.plugins.open_tech_quest import authoring

PREFIX = integration.PREFIX


class AuthoringIntegration(unittest.TestCase):
    # Reuse fixture helpers explicitly, without inheriting/discovering the
    # entire integration suite for a second time.
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
        with self.app.app_context():
            db.session.add(Users(id=5, name="Maintainer", email="maintainer@example.test",
                                 password="test-only", verified=True, hidden=True))
            db.session.flush()
            db.session.add(QuestMaintainer(user_id=5, previous_hidden=False))
            db.session.commit()
        self.maintainer = self.client(5)
        with self.guest.session_transaction() as session:
            session["nonce"] = "quest-test-csrf"

    def path(self, challenge_id=None, slug="openshift", preview=False):
        path = PREFIX + "/manage/rooms/" + slug + "/challenges"
        if challenge_id is not None:
            path += "/" + str(challenge_id)
        return path + ("/preview" if preview else "")

    def payload(self, **overrides):
        result = {"name": "A new challenge", "description": "Try this **task**.",
                  "state": "visible", "flags": [{"content": "fixture-Exact-Answer", "case_insensitive": False}]}
        result.update(overrides)
        return result

    def create(self, client=None, slug="openshift", **overrides):
        response = self.post(client or self.maintainer, self.path(slug=slug), self.payload(**overrides))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def test_workspace_authorization_room_isolation_and_private_flags(self):
        for client, expected in ((self.guest, 401), (self.alice, 403)):
            self.assertEqual(client.get(self.path()).status_code, expected)
            self.assertEqual(self.post(client, self.path(), self.payload()).status_code, expected)
            self.assertEqual(self.post(client, self.path(1), {"name": "Changed"}, "PATCH").status_code, expected)
            self.assertEqual(self.post(client, self.path(1), {}, "DELETE").status_code, expected)
            self.assertEqual(self.post(client, self.path(1, preview=True), {"submission": "fixture-answer-1"}).status_code, expected)
        for client in (self.admin, self.maintainer):
            response = client.get(self.path())
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual([c["id"] for c in response.json["data"]["challenges"]], [1, 2])
            self.assertEqual(response.json["data"]["challenges"][0]["flags"], [{"content": "fixture-answer-1", "case_insensitive": False}])
            self.assertEqual(self.post(client, self.path(1, slug="ansible"), {"name": "Wrong room"}, "PATCH").status_code, 404)
            self.assertEqual(self.post(client, self.path(1, slug="ansible", preview=True), {"submission": "fixture-answer-1"}).status_code, 404)
            self.assertEqual(self.post(client, self.path(1, slug="ansible"), {}, "DELETE").status_code, 404)
            # Challenge 3 is native but is not mapped into this room.
            self.assertEqual(self.post(client, self.path(3), {"name": "Unmapped"}, "PATCH").status_code, 404)
        for client in (self.guest, self.alice, self.maintainer):
            self.assertNotIn("fixture-answer", client.get(PREFIX + "/bootstrap").get_data(as_text=True))
        overview = self.maintainer.get(PREFIX + "/manage/rooms")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(set(overview.json["data"]), {"event", "rooms", "available_challenges"})
        for private in ("fixture-answer", "alice@example", "maintainer@example", '"audit"', '"participants"', '"teams"'):
            self.assertNotIn(private, overview.get_data(as_text=True))

    def test_create_append_native_chain_and_native_participant_solve(self):
        created = self.create()
        with self.app.app_context():
            row = Challenges.query.get(created["id"])
            self.assertEqual((row.type, row.logic, row.value), ("standard", "any", 100))
            self.assertEqual(row.requirements["prerequisites"], [1, 2])
            self.assertEqual(Challenges.query.get(2).next_id, row.id)
            self.assertIsNone(row.next_id)
            self.assertEqual(QuestStep.query.filter_by(challenge_id=row.id).one().position, 2)
        self.action("start")
        self.start()
        self.assertEqual(self.post(self.alice, "/api/v1/challenges/attempt", {"challenge_id": created["id"], "submission": "fixture-Exact-Answer"}).status_code, 403)
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.solve(self.bob, 2).json["data"]["status"], "correct")
        attempted = self.post(self.bob, "/api/v1/challenges/attempt", {"challenge_id": created["id"], "submission": "fixture-Exact-Answer"})
        self.assertEqual(attempted.json["data"]["status"], "correct", attempted.get_data(as_text=True))

    def test_patch_preserves_native_files_hints_and_value_and_replaces_flags(self):
        with self.app.app_context():
            Challenges.query.get(2).value = 375
            db.session.commit()
        response = self.post(self.maintainer, self.path(2), {"description": "", "flags": [
            {"content": "ExactCase", "case_insensitive": False},
            {"content": "MixedCase", "case_insensitive": True}]}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["data"]["description"], "")
        with self.app.app_context():
            row = Challenges.query.get(2)
            self.assertEqual(row.value, 375)
            self.assertEqual(ChallengeFiles.query.filter_by(challenge_id=2).count(), 1)
            self.assertEqual(Hints.query.filter_by(challenge_id=2).count(), 1)
            self.assertEqual(row.requirements["prerequisites"], [1])
            self.assertEqual([f.data for f in Flags.query.filter_by(challenge_id=2).order_by(Flags.id)], ["", "case_insensitive"])

    def test_room_editor_creates_and_updates_validated_point_values(self):
        created = self.create(value=250)
        self.assertEqual(created["value"], 250)
        changed = self.post(self.maintainer, self.path(created["id"]), {"value": 0}, "PATCH")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json["data"]["value"], 0)
        for value in (True, "100", -1, 1.5, 2147483648, None):
            response = self.post(self.maintainer, self.path(created["id"]), {"value": value}, "PATCH")
            self.assertEqual(response.status_code, 400, repr(value))

    def test_preview_matches_native_case_rules_without_progress_or_answer_logs(self):
        created = self.create(flags=[{"content": "ExactCase", "case_insensitive": False},
                                    {"content": "MixedCase", "case_insensitive": True}])
        with self.app.app_context():
            before_audit = QuestAudit.query.count()
        for submission, expected in (("ExactCase", "correct"), ("exactcase", "incorrect"),
                                     ("MIXEDCASE", "correct"), ("wrong", "incorrect"),
                                     ("  ExactCase  ", "correct"), ("", "incorrect")):
            response = self.post(self.maintainer, self.path(created["id"], preview=True), {"submission": submission})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.json["data"]["status"], expected)
            if submission:
                self.assertNotIn(submission, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Submissions.query.count(), 0)
            self.assertEqual(QuestStart.query.count(), 0)
            self.assertEqual(QuestAudit.query.count(), before_audit)
            serialized = json.dumps([a.detail for a in QuestAudit.query.all()])
            self.assertNotIn("ExactCase", serialized)
            self.assertNotIn("MixedCase", serialized)

    def test_delete_rebuilds_chain_without_deleting_unrelated_native_challenges(self):
        created = self.create()
        response = self.post(self.maintainer, self.path(2), {}, "DELETE")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["data"], {"id": 2, "deleted": True})
        with self.app.app_context():
            self.assertIsNone(Challenges.query.get(2))
            self.assertIsNotNone(Challenges.query.get(3))
            chain = QuestStep.query.order_by(QuestStep.position).all()
            self.assertEqual([(s.challenge_id, s.position) for s in chain], [(1, 0), (created["id"], 1)])
            self.assertEqual(Challenges.query.get(1).next_id, created["id"])
            self.assertEqual(Challenges.query.get(created["id"]).requirements["prerequisites"], [1])
            self.assertIsNone(Challenges.query.get(created["id"]).next_id)

    def test_draft_and_activity_locks_preserve_all_native_history(self):
        with self.app.app_context():
            db.session.add(Fails(challenge_id=1, user_id=2, team_id=self.team_id, provided="fixture-failed-history"))
            db.session.commit()
        for method, path, data in (("PATCH", self.path(1), {"name": "Overwrite"}),
                                   ("DELETE", self.path(1), {}), ("POST", self.path(), self.payload())):
            response = self.post(self.maintainer, path, data, method)
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        workspace = self.maintainer.get(self.path()).json["data"]
        self.assertFalse(workspace["can_create"])
        self.assertFalse(workspace["challenges"][0]["can_edit"])
        self.assertIn("submissions", workspace["challenges"][0]["edit_reason"])
        with self.app.app_context():
            self.assertEqual(Fails.query.count(), 1)
            self.assertEqual(Challenges.query.get(1).name, "Challenge 1")
            self.assertEqual(QuestAudit.query.filter(QuestAudit.action.like("challenge.%")).count(), 0)
            QuestEvent.query.get(1).status = "live"
            db.session.commit()
        self.assertEqual(self.post(self.maintainer, self.path(2), {"name": "Live overwrite"}, "PATCH").status_code, 409)
        self.assertEqual(self.post(self.maintainer, self.path(2, preview=True), {"submission": "fixture-answer-2"}).json["data"]["status"], "correct")

    def test_room_start_correction_unlock_and_rating_activity_blocks_changes(self):
        with self.app.app_context():
            room_id = QuestRoom.query.filter_by(slug="openshift").one().id
        for record in (
            lambda: QuestStart(room_id=room_id, team_id=self.team_id),
            lambda: QuestCorrection(room_id=room_id, team_id=self.team_id, adjustment_ms=1, reason="fixture"),
            lambda: HintUnlocks(target=1, user_id=2, team_id=self.team_id),
            lambda: Ratings(challenge_id=2, user_id=2, value=1),
        ):
            with self.app.app_context():
                row = record()
                db.session.add(row)
                db.session.commit()
                model, row_id = type(row), row.id
            response = self.post(self.maintainer, self.path(2), {"name": "History overwrite"}, "PATCH")
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
            with self.app.app_context():
                self.assertIsNotNone(model.query.get(row_id))
                db.session.delete(model.query.get(row_id))
                db.session.commit()

    def test_demo_content_is_locked_but_still_previewable_without_unloading(self):
        self.configure([])
        response = self.post(self.admin, PREFIX + "/admin/demo", {"action": "load"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        workspace = self.maintainer.get(self.path()).json["data"]
        challenge = workspace["challenges"][0]
        self.assertFalse(workspace["can_create"])
        self.assertFalse(challenge["can_edit"])
        self.assertFalse(challenge["can_delete"])
        self.assertIn("remove the loaded demo", challenge["edit_reason"])
        for method, path, data in (("PATCH", self.path(challenge["id"]), {"name": "Changed demo"}),
                                   ("DELETE", self.path(challenge["id"]), {}),
                                   ("POST", self.path(), self.payload())):
            self.assertEqual(self.post(self.maintainer, path, data, method).status_code, 409)
        self.assertEqual(self.post(self.maintainer, self.path(challenge["id"], preview=True), {"submission": challenge["flags"][0]["content"]}).json["data"]["status"], "correct")
        self.assertTrue(self.admin.get(PREFIX + "/admin/state").json["data"]["demo"]["active"])

    def test_unsupported_flag_logic_and_type_are_read_only_without_normalizing(self):
        with self.app.app_context():
            Challenges.query.get(1).logic = "all"
            Flags.query.filter_by(challenge_id=2).one().type = "regex"
            db.session.commit()
        workspace = self.maintainer.get(self.path()).json["data"]
        for challenge in workspace["challenges"]:
            self.assertFalse(challenge["can_edit"])
            self.assertFalse(challenge["can_delete"])
            self.assertEqual(challenge["flags"], [])
            self.assertIn("unsupported", challenge["edit_reason"])
            self.assertEqual(self.post(self.maintainer, self.path(challenge["id"]), {"name": "Normalize"}, "PATCH").status_code, 409)
            self.assertEqual(self.post(self.maintainer, self.path(challenge["id"], preview=True), {"submission": "fixture-answer"}).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(1).logic, "all")
            self.assertEqual(Flags.query.filter_by(challenge_id=2).one().type, "regex")

    def test_validation_is_strict_bounded_atomic_and_never_echoes_flags(self):
        invalid = [
            {"name": "x" * 81}, {"name": " "}, {"description": "x" * 10001},
            {"state": "unknown"}, {"flags": []}, {"flags": ["secret-invalid-input"]},
            {"flags": [{"content": "secret-invalid-input"}]},
            {"flags": [{"content": "secret-invalid-input", "case_insensitive": "false"}]},
            {"flags": [{"content": " secret-invalid-input ", "case_insensitive": False}]},
            {"flags": [{"content": "x" * 1025, "case_insensitive": False}]},
            {"flags": [{"content": "a", "case_insensitive": False}] * 21},
            {"flags": [{"content": "\x00secret-invalid-input", "case_insensitive": False}]},
            {"value": -1}, {"requirements": {"prerequisites": []}},
        ]
        for overrides in invalid:
            response = self.post(self.maintainer, self.path(), self.payload(**overrides))
            self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
            self.assertNotIn("secret-invalid-input", response.get_data(as_text=True))
        self.assertEqual(self.post(self.maintainer, self.path(1), {}, "PATCH").status_code, 400)
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(Flags.query.count(), 3)
            self.assertEqual(QuestStep.query.count(), 2)
        # A failure after creating flags/mappings rolls back the complete write.
        with patch.object(authoring, "audit", side_effect=authoring.QuestError("fixture transaction failure", 409)):
            self.assertEqual(self.post(self.maintainer, self.path(), self.payload()).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(Flags.query.count(), 3)
            self.assertEqual([s.challenge_id for s in QuestStep.query.order_by(QuestStep.position)], [1, 2])
            self.assertEqual(Challenges.query.get(1).next_id, 2)
            self.assertIsNone(Challenges.query.get(2).next_id)

    def test_fresh_role_check_banned_account_and_csrf(self):
        response = self.maintainer.post(self.path(), json=self.payload())
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            Users.query.get(5).banned = True
            db.session.commit()
            clear_user_session(5)
        self.assertEqual(self.maintainer.get(self.path()).status_code, 403)
        with self.app.app_context():
            Users.query.get(5).banned = False
            db.session.commit()
            clear_user_session(5)
        original_event = authoring.event
        def revoked_event(lock=False):
            e = original_event(lock=lock)
            QuestMaintainer.query.filter_by(user_id=5).delete()
            db.session.commit()
            return e
        with patch.object(authoring, "event", side_effect=revoked_event):
            self.assertEqual(self.post(self.maintainer, self.path(), self.payload()).status_code, 403)
        self.assertEqual(self.maintainer.get(self.path()).status_code, 403)
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)

    def test_authoring_custom_room_and_zero_rooms(self):
        with self.app.app_context():
            room = QuestRoom(slug="custom-topic", name="Custom topic", position=10, theme={"layout": "generic"})
            db.session.add(room)
            db.session.commit()
        created = self.create(slug="custom-topic", description="")
        self.assertEqual(self.maintainer.get(self.path(slug="custom-topic")).json["data"]["challenges"][0]["id"], created["id"])
        self.assertEqual(self.post(self.maintainer, self.path(created["id"], slug="custom-topic"), {}, "DELETE").status_code, 200)
        with self.app.app_context():
            QuestStep.query.delete()
            QuestRoom.query.delete()
            db.session.commit()
        overview = self.maintainer.get(PREFIX + "/manage/rooms")
        self.assertEqual(overview.json["data"]["rooms"], [])
        self.assertEqual(self.maintainer.get(self.path()).status_code, 404)

    def test_delete_refuses_external_dependencies_without_erasing_either_challenge(self):
        with self.app.app_context():
            Challenges.query.get(3).requirements = {"prerequisites": [1]}
            db.session.commit()
        response = self.post(self.maintainer, self.path(1), {}, "DELETE")
        self.assertEqual(response.status_code, 409)
        self.assertIn("outside this room", response.json["error"])
        with self.app.app_context():
            self.assertIsNotNone(Challenges.query.get(1))
            self.assertEqual(Challenges.query.get(3).requirements["prerequisites"], [1])

