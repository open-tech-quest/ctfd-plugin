"""Live synchronization preserves attendance, identity and timed privacy."""
from datetime import datetime, timedelta
from unittest.mock import patch
import unittest

import test_integration as integration
from CTFd.models import Users, db
from CTFd.utils.security.signing import hmac
from CTFd.plugins.open_tech_quest import onboarding, read_cache
from CTFd.plugins.open_tech_quest.models import QuestEvent, QuestMaintainer, QuestOnboarding


class SyncIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve
    setUp = integration.QuestIntegration.setUp

    def sync(self, client=None):
        response = (client or self.alice).get(integration.PREFIX + "/sync")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        return response.json["data"]

    def test_initial_and_live_shapes_preserve_private_identity_without_room_content(self):
        boot = self.alice.get(integration.PREFIX + "/bootstrap").json["data"]
        live = self.sync()
        self.assertIn("rooms", boot)
        self.assertNotIn("rooms", live)
        for key in ("viewer", "registration", "csrfNonce", "onboarding", "revisions"):
            self.assertEqual(live[key], boot[key])
        self.assertEqual(set(live["revisions"]), {"content", "roster", "progress"})
        self.assertTrue(all(isinstance(v, str) and v for v in live["revisions"].values()))
        self.assertEqual(live["viewer"]["id"], 2)
        self.assertEqual({m["id"] for m in live["onboarding"]["team"]["members"]}, {2, 3})
        self.assertNotIn("Private instructions", str(live))
        self.assertNotIn("fixture-answer", str(live))
        self.assertNotIn("alice@example", str(live))
        guest = self.sync(self.guest)
        self.assertFalse(guest["viewer"]["authenticated"])
        self.assertIsNone(guest["onboarding"])
        self.assertNotEqual(guest["csrfNonce"], boot["csrfNonce"])
        self.assertIsNone(self.sync(self.admin)["onboarding"])

    def test_warm_live_reads_reuse_roster_but_never_another_users_readiness(self):
        with self.app.app_context():
            shared = read_cache.shared_reads_enabled()
        with patch.object(onboarding, "participant", wraps=onboarding.participant) as build:
            self.sync(self.alice)
            self.sync(self.alice)
            self.assertEqual(build.call_count, 1 if shared else 2)
            other = self.sync(self.carol)
            self.assertEqual(build.call_count, 2 if shared else 3)
        self.assertEqual(other["viewer"]["id"], 4)
        self.assertEqual({m["id"] for m in other["onboarding"]["team"]["members"]}, {4})
        with self.app.app_context():
            Users.query.get(2).name = "Alice changed"
            db.session.commit()
        live = self.sync(self.bob)
        self.assertEqual(next(m["name"] for m in live["onboarding"]["team"]["members"] if m["id"] == 2), "Alice changed")

    def test_other_team_solve_updates_standings_without_evicting_own_progress(self):
        self.action("start")
        self.start(self.alice)
        self.start(self.carol)
        own = self.sync(self.alice)["revisions"]
        other = self.sync(self.carol)["revisions"]
        before = self.guest.get(integration.PREFIX + "/standings").json["data"]
        self.assertEqual(self.solve(self.carol, 1).json["data"]["status"], "correct")
        self.assertEqual(self.sync(self.alice)["revisions"]["progress"], own["progress"])
        self.assertNotEqual(self.sync(self.carol)["revisions"]["progress"], other["progress"])
        after = self.guest.get(integration.PREFIX + "/standings").json["data"]
        self.assertEqual(next(t["total"] for t in before["teams"] if t["id"] == self.other_id), 0)
        self.assertEqual(next(t["total"] for t in after["teams"] if t["id"] == self.other_id), 1)

    def test_live_roster_release_and_cutoff_advance_without_database_writes(self):
        at = datetime.utcnow()
        boundary = at + timedelta(minutes=1)
        with self.app.app_context():
            e = QuestEvent.query.get(1)
            e.rosters_release_at = boundary
            e.end_at = boundary
            db.session.commit()
        def read(instant):
            with patch("CTFd.plugins.open_tech_quest.api.now", return_value=instant), \
                 patch("CTFd.plugins.open_tech_quest.service.now", return_value=instant), \
                 patch("CTFd.plugins.open_tech_quest.onboarding.now", return_value=instant):
                return self.sync()
        before = read(at)
        self.assertIsNone(before["viewer"]["team_id"])
        self.assertIsNone(before["onboarding"]["team"])
        self.assertTrue(before["onboarding"]["attendance"]["can_open"])
        after = read(boundary + timedelta(seconds=1))
        self.assertEqual(after["viewer"]["team_id"], self.team_id)
        self.assertEqual(after["onboarding"]["team"]["id"], self.team_id)
        self.assertFalse(after["onboarding"]["attendance"]["can_open"])
        self.assertNotEqual(before["revisions"]["roster"], after["revisions"]["roster"])

    def test_live_attendance_and_staff_role_changes_are_immediate_after_warmup(self):
        self.sync()
        response = self.post(self.admin, integration.PREFIX + "/admin/attendance", {"enabled": True}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(self.sync()["onboarding"]["attendance"]["enabled"])
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=2, previous_hidden=False))
            db.session.commit()
        state = self.sync()
        self.assertEqual(state["viewer"]["role"], "maintainer")
        self.assertIsNone(state["onboarding"])

    def test_password_bound_session_does_not_reuse_identity_between_requests(self):
        with self.app.app_context():
            stamp = hmac(Users.query.get(2).password)
        with self.alice.session_transaction() as session:
            session["hash"] = stamp
        self.assertTrue(self.sync()["viewer"]["authenticated"])
        with self.app.app_context():
            Users.query.get(2).password = "replacement-password"
            db.session.commit()
        state = self.sync()
        self.assertFalse(state["viewer"]["authenticated"])
        self.assertIsNone(state["onboarding"])
        self.assertIsNone(state["csrfNonce"])
