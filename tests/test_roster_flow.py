"""Organizer roster preparation without fabricated attendance or lost history."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading
import unittest
from unittest.mock import patch

from CTFd.models import Solves, Submissions, Teams, Users, db
from CTFd.plugins.open_tech_quest.models import (QuestAudit, QuestEvent, QuestMaintainer,
    QuestOnboarding, QuestReadiness, QuestRoom, QuestStart, QuestTeamName)
import test_integration as integration

P = integration.PREFIX


class RosterFlowIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve
    open_onboarding = integration.QuestIntegration.open_onboarding
    attendance_window = integration.QuestIntegration.attendance_window
    ready_user = integration.QuestIntegration.ready_user
    onboarding_action = integration.QuestIntegration.onboarding_action
    attendance_plan = integration.QuestIntegration.attendance_plan
    apply_attendance = integration.QuestIntegration.apply_attendance
    formation_pool = integration.QuestIntegration.formation_pool
    late_accounts = integration.QuestIntegration.late_accounts
    late_status = integration.QuestIntegration.late_status
    admit = integration.QuestIntegration.admit
    naming_policy = integration.QuestIntegration.naming_policy

    def setUp(self):
        integration.QuestIntegration.setUp(self)

    def state(self):
        return self.admin.get(P + "/admin/state").json["data"]["onboarding"]

    def registration(self, enabled):
        response = self.post(self.admin, P + "/admin/registration", {"enabled": enabled}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def policy(self, required):
        return self.post(self.admin, P + "/admin/onboarding", {"require_attendance": required}, "PATCH")

    def registered_pool(self, count=6, capacity=5, naming=True):
        ids = self.formation_pool(count, capacity, naming)
        self.assertEqual(self.policy(False).status_code, 200)
        self.registration(False)
        return ids

    def registered_teams(self, count=6, capacity=5, naming=True, finalized=True):
        ids = self.registered_pool(count, capacity, naming)
        plan = self.attendance_plan()
        self.apply_attendance(plan)
        if finalized:
            self.assertEqual(self.onboarding_action("finalize").status_code, 200)
        with self.app.app_context():
            groups = [(team.id, sorted(user.id for user in team.members)) for team in Teams.query.order_by(Teams.id)]
        return ids, groups

    def move(self, uid, tid, fingerprint=None, reason="Balance attending participants"):
        return self.post(self.client(1), P + "/admin/onboarding/move", {
            "user_id": uid, "team_id": tid, "fingerprint": fingerprint or self.state()["fingerprint"], "reason": reason})

    def test_registered_pool_direct_random_formation_filters_staff_and_bans_without_attendance(self):
        self.assertTrue(self.alice.get(P + "/onboarding").json["data"]["require_attendance"])
        ids = self.registered_pool(9)
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=ids[-1], previous_hidden=False))
            Users.query.get(ids[-2]).banned = True
            db.session.commit()
        plan = self.attendance_plan()
        self.assertEqual(plan, self.attendance_plan())
        placed = [uid for group in plan["groups"] for uid in group["user_ids"]]
        self.assertEqual(set(placed), set(ids[:-2]))
        self.assertNotEqual(placed, sorted(placed))
        self.assertEqual(sorted(len(group["user_ids"]) for group in plan["groups"]), [3, 4])
        self.assertEqual(self.state()["phase"], "check_in")
        self.apply_attendance(plan)
        self.assertEqual(self.state()["eligible_count"], 7)
        self.assertEqual(self.state()["unassigned_count"], 0)
        self.assertEqual(self.onboarding_action("finalize").status_code, 200)
        self.assertTrue(self.state()["can_start"])
        for uid in ids[:-2]:
            own = self.client(uid).get(P + "/onboarding").json["data"]
            self.assertFalse(own["require_attendance"])
            self.assertFalse(own["can_ready"])
            self.assertTrue(own["can_name"])
            self.assertIsNone(own["ready_at"])
        self.action("start")
        self.start(self.client(ids[0]))
        self.assertEqual(self.solve(self.client(ids[0]), 1).json["data"]["status"], "correct")
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(), 0)
            self.assertEqual(Solves.query.count(), 1)
            self.assertEqual(QuestOnboarding.query.get(1).phase, "finalized")

    def test_registered_formation_requires_closed_registration_and_fresh_accounts_at_apply(self):
        self.registered_pool(6)
        self.registration(True)
        denied = self.post(self.admin, P + "/admin/onboarding/optimize", {"preview": True})
        self.assertEqual(denied.status_code, 409)
        self.registration(False)
        plan = self.attendance_plan()
        self.registration(True)
        denied = self.post(self.admin, P + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"]})
        self.assertEqual(denied.status_code, 409)
        self.registration(False)
        plan = self.attendance_plan()
        self.late_accounts(30)
        stale = self.post(self.admin, P + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"]})
        self.assertEqual(stale.status_code, 409)
        with self.app.app_context():
            self.assertEqual(Teams.query.count(), 0)
            self.assertTrue(all(user.team_id is None for user in Users.query.all()))
            self.assertEqual(QuestReadiness.query.count(), 0)
        self.apply_attendance(self.attendance_plan())
        self.assertEqual(self.policy(True).status_code, 409)

    def test_attendance_mode_is_strict_guarded_and_invalidates_preview_without_changing_readiness(self):
        ids = self.formation_pool(3)
        for value in (0, 1, "true", None):
            self.assertEqual(self.policy(value).status_code, 400)
        self.attendance_window(True)
        self.ready_user(self.client(ids[0]))
        self.assertEqual(self.policy(False).status_code, 409)
        self.attendance_window(False)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        ready_plan = self.attendance_plan()
        self.assertEqual(self.policy(False).status_code, 200)
        self.registration(False)
        self.assertEqual(self.post(self.admin, P + "/admin/onboarding/optimize", {
            "preview": False, "fingerprint": ready_plan["fingerprint"]}).status_code, 409)
        self.assertEqual(self.post(self.admin, "/api/v1/configs/quest_require_attendance", {"value": True}, "PATCH").status_code, 409)
        with self.app.app_context():
            self.assertEqual([row.user_id for row in QuestReadiness.query.all()], [ids[0]])
        self.assertEqual(len([uid for group in self.attendance_plan()["groups"] for uid in group["user_ids"]]), 3)

    def test_name_edits_are_optional_repeatable_finalized_draft_and_detect_stale_context(self):
        ids, groups = self.registered_teams(3)
        member = self.client(ids[0])
        original = member.get(P + "/onboarding").json["data"]["team"]
        base = {"name": "First draft", "confirm": True, "team_id": original["id"], "expected_name": original["name"]}
        for missing in ("team_id", "expected_name"):
            response = member.open(P + "/onboarding/name", method="POST", json={k: v for k, v in base.items() if k != missing}, headers={"CSRF-Token": "quest-test-csrf"})
            self.assertEqual(response.status_code, 400)
        first = self.post(member, P + "/onboarding/name", base)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(self.post(member, P + "/onboarding/name", dict(base, name="Stale overwrite")).status_code, 409)
        self.assertEqual(self.post(member, P + "/onboarding/name", {"name": "Second draft", "confirm": True}).status_code, 200)
        teammate = self.client(next(uid for uid in ids if uid != ids[0]))
        self.assertEqual(self.post(teammate, P + "/onboarding/name", {"name": original["name"], "confirm": True}).status_code, 200)
        self.assertTrue(self.state()["can_start"])
        self.naming_policy(False)
        self.assertFalse(member.get(P + "/onboarding").json["data"]["can_name"])
        self.naming_policy(True)
        self.assertTrue(member.get(P + "/onboarding").json["data"]["can_name"])
        with self.app.app_context():
            before = QuestAudit.query.filter_by(action="onboarding.name").count()
        self.assertEqual(self.post(member, P + "/onboarding/name", {"name": original["name"], "confirm": True}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.name").count(), before)
        self.action("start")
        for phase in ("closed", "published"):
            with self.app.app_context():
                QuestEvent.query.get(1).status = phase
                db.session.commit()
            self.assertEqual(self.post(member, P + "/onboarding/name", {"name": "Too late", "confirm": True}).status_code, 409)

    def test_finalized_draft_moves_between_teams_and_unassigned_preserve_names_and_readiness(self):
        ids, groups = self.registered_teams(6)
        source, target = groups[0][0], groups[1][0]
        uid = groups[0][1][0]
        with self.app.app_context():
            Teams.query.get(source).captain_id = uid
            stamp = datetime.utcnow() - timedelta(minutes=5)
            db.session.add(QuestReadiness(user_id=uid, ready_at=stamp))
            db.session.commit()
            names = [(team.id, team.name) for team in Teams.query.order_by(Teams.id)]
        # Choosing a team name is provenance, not competition history.
        self.assertEqual(self.post(self.client(uid), P + "/onboarding/name", {"name": "Renamable crew", "confirm": True}).status_code, 200)
        with self.app.app_context():
            names = [(team.id, team.name) for team in Teams.query.order_by(Teams.id)]
        old_form = self.client(uid).get(P + "/onboarding").json["data"]["team"]
        self.assertEqual(self.move(uid, target).status_code, 200)
        with self.app.app_context():
            self.assertEqual(Users.query.get(uid).team_id, target)
            self.assertNotEqual(Teams.query.get(source).captain_id, uid)
            self.assertEqual(QuestReadiness.query.get(uid).ready_at, stamp)
            self.assertEqual([(team.id, team.name) for team in Teams.query.order_by(Teams.id)], names)
            self.assertEqual(QuestOnboarding.query.get(1).phase, "finalized")
        stale = self.post(self.client(uid), P + "/onboarding/name", {"name": "Wrong team", "confirm": True,
            "team_id": old_form["id"], "expected_name": old_form["name"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.move(uid, None).status_code, 200)
        self.assertEqual(self.move(uid, source).status_code, 200)
        self.assertEqual(self.move(uid, None).status_code, 200)
        self.assertTrue(self.state()["can_start"])
        exported = self.admin.get(P + "/admin/archives/export").json
        moves_exported = [row for row in exported["data"]["audit"] if row["action"] == "onboarding.move"]
        self.assertEqual(len(moves_exported), 4)
        self.assertEqual(moves_exported[0]["detail"]["from_team_id"], source)
        self.assertEqual(moves_exported[0]["detail"]["to_team_id"], target)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.get(uid).ready_at, stamp)
            self.assertIsNone(Users.query.get(uid).team_id)
            moves = QuestAudit.query.filter_by(action="onboarding.move").all()
            self.assertEqual(len(moves), 4)
            self.assertTrue(all(row.actor_id == 1 for row in moves))
            self.assertEqual(QuestStart.query.count(), 0)
            self.assertEqual(Submissions.query.count(), 0)

    def test_move_strict_authority_capacity_and_stale_fingerprints_leave_no_partial_changes(self):
        path = P + "/admin/onboarding/move"
        valid = {"user_id": 2, "team_id": self.other_id, "fingerprint": self.state()["fingerprint"], "reason": "Balance teams"}
        self.assertEqual(self.post(self.alice, path, valid).status_code, 403)
        self.assertEqual(self.admin.post(path, json=valid).status_code, 403)
        with self.guest.session_transaction() as session:
            session["nonce"] = "quest-test-csrf"
        self.assertEqual(self.post(self.guest, path, valid).status_code, 401)
        for changes in ({"user_id": True}, {"team_id": True}, {"reason": ""}, {"unknown": True}):
            self.assertEqual(self.post(self.admin, path, dict(valid, **changes)).status_code, 400)
        self.assertEqual(self.post(self.admin, path, {k: v for k, v in valid.items() if k != "reason"}).status_code, 400)
        self.assertEqual(self.move(1, self.other_id).status_code, 403)
        with self.app.app_context():
            QuestOnboarding.query.get(1).team_size = 1
            db.session.commit()
        self.assertEqual(self.move(2, self.other_id).status_code, 409)
        with self.app.app_context():
            QuestOnboarding.query.get(1).team_size = 5
            Teams.query.get(self.other_id).hidden = True
            db.session.commit()
        self.assertEqual(self.move(2, self.other_id).status_code, 409)
        with self.app.app_context():
            Teams.query.get(self.other_id).hidden = False
            db.session.commit()
        stale = self.state()["fingerprint"]
        self.assertEqual(self.move(3, self.other_id).status_code, 200)
        self.assertEqual(self.move(2, None, fingerprint=stale).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).team_id, self.team_id)
            self.assertEqual(Users.query.get(3).team_id, self.other_id)
            self.assertEqual(QuestReadiness.query.count(), 3)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.move").count(), 1)

    def test_attendance_moves_require_ready_assignment_but_preserve_checkin_on_unassign(self):
        with self.app.app_context():
            QuestOnboarding.query.get(1).grandfathered = False
            db.session.commit()
        self.late_accounts(5)
        self.assertEqual(self.move(5, self.other_id).status_code, 409)
        ready = self.alice.get(P + "/onboarding").json["data"]["ready_at"]
        self.assertEqual(self.move(2, None).status_code, 200)
        self.assertFalse(self.state()["can_start"])
        self.assertEqual(self.alice.get(P + "/onboarding").json["data"]["ready_at"], ready)
        self.assertEqual(self.move(2, self.other_id).status_code, 200)
        self.assertTrue(self.state()["can_start"])

    def test_move_never_rewrites_competition_history_or_empty_team_records(self):
        ids, groups = self.registered_teams(3, capacity=2)
        source, target = groups[0][0], groups[1][0]
        uid = groups[0][1][0]
        with self.app.app_context():
            room = QuestRoom.query.filter_by(slug="openshift").one()
            stamp = datetime.utcnow()
            row = QuestStart(team_id=target, room_id=room.id, started_at=stamp)
            db.session.add(row)
            db.session.commit()
        self.assertEqual(self.move(uid, target).status_code, 409)
        with self.app.app_context():
            self.assertEqual(QuestStart.query.one().started_at, stamp)
            self.assertEqual(Users.query.get(uid).team_id, source)
            QuestStart.query.delete()
            db.session.add(Submissions(user_id=uid, team_id=source, challenge_id=1, type="incorrect", provided="old attempt"))
            db.session.commit()
            record = [(s.id, s.user_id, s.team_id, s.provided, s.date) for s in Submissions.query.all()]
        self.assertEqual(self.move(uid, None).status_code, 409)
        with self.app.app_context():
            self.assertEqual([(s.id, s.user_id, s.team_id, s.provided, s.date) for s in Submissions.query.all()], record)
            Submissions.query.delete()
            db.session.commit()
        for member in groups[0][1]:
            self.assertEqual(self.move(member, None).status_code, 200)
        with self.app.app_context():
            self.assertIsNotNone(Teams.query.get(source))
            self.assertIsNone(Teams.query.get(source).captain_id)
            self.assertEqual(len(Teams.query.get(source).members), 0)

    def test_registered_late_admission_needs_no_attendance_and_native_writes_cannot_bypass_moves(self):
        ids, groups = self.registered_teams(3)
        self.late_accounts(30)
        self.action("start")
        result = self.admit([30], team_id=groups[0][0])
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        newcomer = self.client(30)
        self.start(newcomer)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(), 0)
            self.assertEqual(Users.query.get(30).team_id, groups[0][0])
        self.assertEqual(self.move(30, None).status_code, 409)
        for path, method, body in (
            (f"/api/v1/teams/{groups[0][0]}/members", "DELETE", {"user_id": 30}),
            (f"/api/v1/teams/{groups[0][0]}/members", "POST", {"user_id": ids[0]}),
            ("/api/v1/users/30", "PATCH", {"team_id": None}),
        ):
            self.assertEqual(self.post(self.admin, path, body, method).status_code, 409)

    def test_two_moves_to_last_team_slot_use_one_atomic_snapshot(self):
        with self.app.app_context():
            QuestOnboarding.query.get(1).team_size = 2
            db.session.commit()
        fingerprint = self.state()["fingerprint"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda uid: self.move(uid, self.other_id, fingerprint), (2, 3)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        with self.app.app_context():
            self.assertEqual(len(Teams.query.get(self.other_id).members), 2)
            self.assertEqual(len(Teams.query.get(self.team_id).members), 1)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.move").count(), 1)

    def test_move_waiting_behind_launch_rechecks_phase_and_preserves_original_membership(self):
        if self.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            self.skipTest("Production row-lock serialization is exercised on MariaDB")
        from CTFd.plugins.open_tech_quest import api as api_module
        fingerprint = self.state()["fingerprint"]
        entered, release = threading.Event(), threading.Event()
        original = api_module.onboarding.start_reason
        def gate(e):
            result = original(e)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Launch transaction was not released")
            return result
        with patch.object(api_module.onboarding, "start_reason", side_effect=gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                launch = pool.submit(lambda: self.post(self.client(1), P + "/admin/event/action", {"action": "start"}))
                self.assertTrue(entered.wait(5))
                moving = pool.submit(lambda: self.post(self.client(1), P + "/admin/onboarding/move", {
                    "user_id": 2, "team_id": self.other_id, "fingerprint": fingerprint, "reason": "Waiting move"}))
                try:
                    import time
                    time.sleep(0.1)
                    self.assertFalse(moving.done(), "Move must serialize behind event launch")
                finally:
                    release.set()
                self.assertEqual(launch.result().status_code, 200)
                self.assertEqual(moving.result().status_code, 409)
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).team_id, self.team_id)
            self.assertEqual(QuestEvent.query.get(1).status, "live")
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.move").count(), 0)

    def test_move_rejects_reused_account_identity_from_old_review(self):
        fingerprint = self.state()["fingerprint"]
        with self.app.app_context():
            original = Users.query.get(3)
            replacement = {"id": 3, "name": original.name, "email": original.email,
                "password": original.password, "type": "user", "team_id": self.team_id,
                "created": original.created + timedelta(seconds=1)}
            ready = QuestReadiness.query.get(3)
            ready_at, late = ready.ready_at, ready.late
            QuestReadiness.query.filter_by(user_id=3).delete()
            db.session.execute(Users.__table__.delete().where(Users.id == 3))
            db.session.execute(Users.__table__.insert(), replacement)
            db.session.add(QuestReadiness(user_id=3, ready_at=ready_at, late=late))
            db.session.commit()
        self.assertEqual(self.move(3, self.other_id, fingerprint).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Users.query.get(3).name, "Bob")
            self.assertEqual(Users.query.get(3).team_id, self.team_id)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.move").count(), 0)
