"""Projector selection/rotation through authenticated native Flask requests."""
from datetime import datetime, timedelta
from unittest.mock import patch
import unittest

from CTFd.models import Users, db
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestRoom
from CTFd.plugins.open_tech_quest.service import iso
import test_integration as integration


PREFIX = integration.PREFIX


class ProjectorIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    gated_lifecycle_write = integration.QuestIntegration.gated_lifecycle_write

    def setUp(self):
        integration.QuestIntegration.setUp(self)
        self.at = datetime(2026, 11, 9, 12, 0, 0, 123000)

    def update(self, data, at=None, status=200):
        with patch("CTFd.plugins.open_tech_quest.api.now", return_value=at or self.at):
            response = self.post(self.admin, PREFIX + "/admin/projector", data, "PATCH")
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        return response.json.get("data")

    def saved(self):
        with self.app.app_context():
            return QuestEvent.query.get(1).projector

    def test_legacy_read_normalizes_without_writing_and_matches_admin_state(self):
        raw = {"view": "room", "room_slug": "openshift"}
        with self.app.app_context():
            QuestEvent.query.get(1).projector = raw
            db.session.commit()
            before = QuestAudit.query.count()
        with patch("CTFd.plugins.open_tech_quest.api.now", return_value=self.at):
            public = self.guest.get(PREFIX + "/projector").json["data"]
            private = self.admin.get(PREFIX + "/admin/state").json["data"]["projector"]
        self.assertEqual(public, private)
        self.assertEqual(public, dict(raw, rotation={"enabled": False, "anchor_at": None, "paused_at": None,
                                                   "overview_seconds": 60, "room_seconds": 30, "overall_seconds": 20},
                                      server_now=iso(self.at)))
        self.assertEqual(self.saved(), raw)
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.count(), before)

    def test_admin_only_and_csrf_required(self):
        path = PREFIX + "/admin/projector"
        self.assertEqual(self.post(self.guest, path, {"view": "matrix"}, "PATCH").status_code, 403)
        self.assertEqual(self.post(self.alice, path, {"view": "matrix"}, "PATCH").status_code, 403)
        self.assertEqual(self.admin.patch(path, json={"view": "matrix"}).status_code, 403)
        self.assertEqual(self.saved(), {"view": "overview", "room_slug": None})

    def test_all_views_and_legacy_requests_are_supported(self):
        for view in ("overview", "matrix", "overall", "awards", "room"):
            result = self.update({"view": view, "room_slug": "openshift" if view == "room" else None})
            self.assertEqual(result["view"], view)
            self.assertFalse(result["rotation"]["enabled"])
        self.assertNotIn("server_now", self.saved())
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.filter_by(action="projector.configure").count(), 5)

    def test_invalid_config_is_atomic(self):
        original = self.saved()
        invalid = [{}, {"view": "bad"}, {"view": []}, {"unexpected": 1},
                   {"view": "room", "room_slug": None}, {"room_slug": 1},
                   {"rotation": None}, {"rotation": []}, {"rotation": {"enabled": 1}},
                   {"rotation": {"anchor_at": iso(self.at)}}, {"server_now": iso(self.at)},
                   {"rotation_action": "pause", "view": "overview"}, {"rotation_action": []}]
        for key in ("overview_seconds", "room_seconds", "overall_seconds"):
            for value in (True, 9, 601, 30.0, "30", None):
                invalid.append({"view": "matrix", "rotation": {"enabled": True, key: value}})
        for data in invalid:
            with self.subTest(data=data):
                self.update(data, status=400)
                self.assertEqual(self.saved(), original)
        self.update({"view": "room", "room_slug": "missing"}, status=404)
        self.assertEqual(self.saved(), original)

    def test_enable_pause_resume_and_restart_are_durable(self):
        enabled = self.update({"rotation": {"enabled": True, "overview_seconds": 10, "room_seconds": 600}})
        self.assertEqual(enabled["rotation"]["anchor_at"], iso(self.at))
        pause_at = self.at + timedelta(seconds=17)
        paused = self.update({"rotation_action": "pause"}, pause_at)
        self.assertEqual(paused["rotation"]["paused_at"], iso(pause_at))
        self.assertEqual(self.update({"rotation_action": "pause"}, pause_at + timedelta(seconds=8))["rotation"], paused["rotation"])
        self.assertEqual(self.guest.get(PREFIX + "/projector").json["data"]["rotation"], paused["rotation"])
        resume_at = pause_at + timedelta(seconds=70)
        resumed = self.update({"rotation_action": "resume"}, resume_at)
        self.assertEqual(resumed["rotation"]["anchor_at"], iso(self.at + timedelta(seconds=70)))
        self.assertIsNone(resumed["rotation"]["paused_at"])
        self.assertEqual(self.update({"rotation_action": "resume"}, resume_at + timedelta(seconds=3))["rotation"], resumed["rotation"])
        self.update({"rotation_action": "pause"}, resume_at + timedelta(seconds=5))
        restarted = self.update({"rotation_action": "restart"}, resume_at + timedelta(seconds=20))["rotation"]
        self.assertEqual(restarted["anchor_at"], iso(resume_at + timedelta(seconds=20)))
        self.assertIsNone(restarted["paused_at"])
        self.assertEqual((restarted["overview_seconds"], restarted["room_seconds"]), (10, 600))
        self.assertEqual(self.saved()["rotation"], restarted)

    def test_unchanged_or_omitted_rotation_preserves_paused_cycle(self):
        self.update({"rotation": {"enabled": True}})
        paused = self.update({"rotation_action": "pause"}, self.at + timedelta(seconds=12))["rotation"]
        for data in ({"view": "matrix", "room_slug": None}, {"rotation": {"enabled": True}},
                     {"rotation": {"overview_seconds": 60, "room_seconds": 30, "overall_seconds": 20}}):
            self.assertEqual(self.update(data, self.at + timedelta(seconds=100))["rotation"], paused)
        changed = self.update({"rotation": {"room_seconds": 40}}, self.at + timedelta(seconds=120))["rotation"]
        self.assertEqual(changed["anchor_at"], iso(self.at + timedelta(seconds=120)))
        self.assertIsNone(changed["paused_at"])
        disabled = self.update({"view": "overall", "rotation": {"enabled": False}})["rotation"]
        self.assertEqual((disabled["anchor_at"], disabled["paused_at"], disabled["room_seconds"]), (None, None, 40))
        for action in ("pause", "resume", "restart"):
            self.update({"rotation_action": action}, status=409)
        reenabled = self.update({"rotation": {"enabled": True}}, self.at + timedelta(seconds=200))["rotation"]
        self.assertEqual(reenabled["anchor_at"], iso(self.at + timedelta(seconds=200)))
        self.assertEqual(reenabled["room_seconds"], 40)

    def test_room_deletion_retains_rotation_and_other_selection(self):
        self.update({"view": "room", "room_slug": "ansible", "rotation": {"enabled": True, "room_seconds": 45}})
        before = self.update({"rotation_action": "pause"}, self.at + timedelta(seconds=5))["rotation"]
        response = self.post(self.admin, PREFIX + "/manage/rooms/ansible", {"confirm": True}, "DELETE")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        after = self.saved()
        self.assertEqual((after["view"], after["room_slug"]), ("overview", None))
        self.assertEqual(after["rotation"], before)
        self.update({"view": "matrix"})
        self.assertEqual(self.post(self.admin, PREFIX + "/manage/rooms/ai", {"confirm": True}, "DELETE").status_code, 200)
        self.assertEqual(self.saved()["view"], "matrix")
        self.assertEqual(self.saved()["rotation"], before)

    def test_no_rooms_and_each_event_phase_still_allow_safe_projector_changes(self):
        with self.app.app_context():
            QuestRoom.query.delete()
            db.session.commit()
        for view in ("overview", "matrix", "overall", "awards"):
            self.assertEqual(self.update({"view": view, "rotation": {"enabled": True}})["view"], view)
        self.update({"view": "room", "room_slug": "openshift"}, status=404)
        for phase in ("live", "paused", "closed", "published"):
            with self.app.app_context():
                QuestEvent.query.get(1).status = phase
                db.session.commit()
            self.assertEqual(self.update({"view": "matrix"})["view"], "matrix")

    def test_waiting_write_rechecks_admin_authority_before_changing_projector(self):
        def ban_actor():
            with self.app.app_context():
                Users.query.get(1).banned = True
                db.session.commit()
        response = self.gated_lifecycle_write(1, PREFIX + "/admin/projector", {"view": "matrix"}, "PATCH", ban_actor)
        self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        self.assertEqual(self.saved(), {"view": "overview", "room_slug": None})
