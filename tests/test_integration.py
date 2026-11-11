"""Real Flask/SQLAlchemy/native CTFd integration; disposable DB only.

Run inside pinned CTFd image with plugin installed:
python -m unittest discover -s /opt/quest-tests -p 'test_*.py' -v
DATABASE_URL may select a dedicated MariaDB database ending in `_test`.
No pytest or other test dependencies are required.
"""
import os
import tempfile
import time
import threading
from pathlib import Path
from sqlalchemy import event as sqlalchemy_event
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

from CTFd import create_app
from CTFd.config import TestingConfig
from CTFd.cache import cache
from CTFd.models import ChallengeFiles, Challenges, Configs, Flags, Hints, Solves, Submissions, Teams, Users, db
from CTFd.utils import get_config, set_config
from CTFd.plugins.open_tech_quest.models import (QuestEvent, QuestRoom, QuestStep,
    QuestStart, QuestPause, QuestAudit, QuestOnboarding, QuestReadiness, QuestTeamName, QuestMaintainer)
from CTFd.plugins.open_tech_quest.service import standings

PREFIX = "/api/v1/quest"


def reviewed_change(case, client, path, changes, reason="Synthetic reviewed maintenance"):
    collection = path.rsplit("/", 1)[0]
    workspace = client.get(collection).json["data"]
    revision = workspace["revision"]
    reviewed = case.post(client, path + "/review", {"changes": changes, "revision": revision})
    case.assertEqual(reviewed.status_code, 200, reviewed.get_data(as_text=True))
    payload = dict(changes)
    if reviewed.json["data"]["requires_confirmation"]:
        payload["maintenance"] = {"revision": reviewed.json["data"]["revision"], "reason": reason, "confirm": True}
    return client.open(path, method="PATCH", json=payload,
        headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": revision})


class QuestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quest-tests-")
        database = os.environ.get("DATABASE_URL", "sqlite:///" + cls.temp.name + "/test.db")
        if not database.startswith("sqlite:") and not database.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
            raise RuntimeError("Integration tests require a disposable database ending in _test")
        class Config(TestingConfig):
            SAFE_MODE = False
            SQLALCHEMY_DATABASE_URI = database
            SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
            UPLOAD_FOLDER = cls.temp.name + "/uploads"
            LOG_FOLDER = cls.temp.name + "/logs"
        cls.app = create_app(Config)
        # Validate real first-install behavior before test fixtures add data.
        with cls.app.app_context():
            assert get_config("user_mode") == "teams"
            assert QuestEvent.query.one().status == "draft"
            assert QuestRoom.query.count() == 5
            assert Challenges.query.count() == Solves.query.count() == 0
            assert QuestOnboarding.query.get(1).phase == "check_in"
            assert QuestOnboarding.query.get(1).team_size == 5
            assert QuestOnboarding.query.get(1).allow_team_naming is False
            assert QuestOnboarding.query.get(1).attendance_open is False
            assert QuestOnboarding.query.get(1).attendance_generation == 0
            assert QuestReadiness.query.count() == 0
            assert QuestMaintainer.query.count() == 0

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            with db.engine.connect() as connection:
                dialect = db.engine.dialect.name
                connection.execute("PRAGMA foreign_keys=OFF" if dialect == "sqlite" else "SET FOREIGN_KEY_CHECKS=0")
                revisions = [{"key": row.key, "value": row.value} for row in connection.execute(Configs.__table__.select()).fetchall() if row.key.endswith("_alembic_version")]
                transaction = connection.begin()
                for table in reversed(db.metadata.sorted_tables):
                    connection.execute(table.delete())
                if revisions:
                    connection.execute(Configs.__table__.insert(), revisions)
                transaction.commit()
                connection.execute("PRAGMA foreign_keys=ON" if dialect == "sqlite" else "SET FOREIGN_KEY_CHECKS=1")
            db.engine.dispose()
        cls.temp.cleanup()

    def setUp(self):
        with self.app.app_context():
            db.session.remove()
            with db.engine.connect() as connection:
                dialect = db.engine.dialect.name
                connection.execute("PRAGMA foreign_keys=OFF" if dialect == "sqlite" else "SET FOREIGN_KEY_CHECKS=0")
                revisions = [{"key": row.key, "value": row.value} for row in connection.execute(Configs.__table__.select()).fetchall() if row.key.endswith("_alembic_version")]
                transaction = connection.begin()
                for table in reversed(db.metadata.sorted_tables):
                    connection.execute(table.delete())
                if revisions:
                    connection.execute(Configs.__table__.insert(), revisions)
                transaction.commit()
                connection.execute("PRAGMA foreign_keys=ON" if dialect == "sqlite" else "SET FOREIGN_KEY_CHECKS=1")
            cache.clear()
            for key, value in (("setup", True), ("ctf_name", "Quest test"), ("user_mode", "teams"), ("challenge_visibility", "public"), ("account_visibility", "public"), ("verify_emails", False), ("paused", False), ("start", None), ("end", None)):
                set_config(key, value)
            db.session.add(QuestEvent(id=1))
            db.session.add(QuestOnboarding(id=1, phase="finalized", team_size=5, grandfathered=True))
            for pos, slug in enumerate(("openshift", "ansible", "rhel", "security", "ai")):
                db.session.add(QuestRoom(slug=slug, name=slug.title(), position=pos, theme={"layout": slug}))
            self.team = Teams(name="Red team")
            self.other_team = Teams(name="Blue team")
            db.session.add_all([self.team, self.other_team])
            db.session.flush()
            db.session.add_all([Users(id=1, name="Organizer", email="admin@example.test", type="admin", password="test-only", verified=True), Users(id=2, name="Alice", email="alice@example.test", password="test-only", team_id=self.team.id, verified=True), Users(id=3, name="Bob", email="bob@example.test", password="test-only", team_id=self.team.id, verified=True), Users(id=4, name="Carol", email="carol@example.test", password="test-only", team_id=self.other_team.id, verified=True)])
            self.team_id, self.other_id = self.team.id, self.other_team.id
            db.session.add_all([QuestTeamName(team_id=self.team_id, name_locked=True), QuestTeamName(team_id=self.other_id, name_locked=True)])
            for uid in (2, 3, 4):
                db.session.add(QuestReadiness(user_id=uid))
            for i in range(1, 4):
                c = Challenges(id=i, name=f"Challenge {i}", description=f"Private instructions {i}", value=100, category="Quest", type="standard", state="visible")
                db.session.add(c)
                db.session.flush()
                db.session.add(Flags(challenge_id=i, type="static", content=f"fixture-answer-{i}", data=""))
            db.session.add(ChallengeFiles(challenge_id=2, location="known-hash/private.txt"))
            db.session.add(Hints(id=1, challenge_id=2, title="Help", content="Private hint", cost=0))
            db.session.commit()
        self.admin = self.client(1)
        self.alice, self.bob, self.carol = self.client(2), self.client(3), self.client(4)
        self.guest = self.app.test_client()
        self.configure([1, 2])

    def client(self, uid):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["id"] = uid
            session["nonce"] = "quest-test-csrf"
        return client

    def post(self, client, path, data, method="POST"):
        headers = {"CSRF-Token": "quest-test-csrf"}
        if path == "/api/v1/unlocks" and isinstance(data, dict) and data.get("type") == "hints":
            hint = client.get("/api/v1/hints/" + str(data.get("target")))
            if hint.is_json and isinstance(hint.json.get("data"), dict) and hint.json["data"].get("quote_revision"):
                headers["X-Quest-Hint-Quote"] = hint.json["data"]["quote_revision"]
        return client.open(path, method=method, json=data, headers=headers)

    def configure(self, ids):
        response = self.post(self.admin, PREFIX + "/admin/rooms/openshift", {"challenge_ids": ids}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def action(self, action, **kwargs):
        response = self.post(self.admin, PREFIX + "/admin/event/action", dict(action=action, **kwargs))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def start(self, client=None):
        response = self.post(client or self.alice, PREFIX + "/rooms/openshift/start", {})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def solve(self, client, challenge_id):
        return self.post(client, "/api/v1/challenges/attempt", {"challenge_id": challenge_id, "submission": f"fixture-answer-{challenge_id}"})

    def test_safe_guest_lobby_shell_and_admin_authorization(self):
        response = self.guest.get(PREFIX + "/bootstrap")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["event"]["status"], "draft")
        serialized = response.get_data(as_text=True)
        self.assertNotIn("Private instructions", serialized)
        self.assertNotIn("fixture-answer", serialized)
        self.assertEqual(self.guest.get(PREFIX + "/admin/state").status_code, 401)
        self.assertEqual(self.alice.get(PREFIX + "/admin/state").status_code, 403)
        self.assertEqual(self.guest.get("/").status_code, 302)
        self.assertEqual(self.admin.get("/").location, "/quest")
        shell = self.alice.get("/quest/rooms/openshift").get_data(as_text=True)
        self.assertIn("window.QUEST_INIT", shell)
        self.assertIn("app/quest.js", shell)
        self.assertNotIn("alice@example", shell)
        self.assertNotIn("test-only", shell)

    def test_early_content_and_known_file_and_hint_url_guards(self):
        for client in (self.alice, self.guest):
            for path in ("/api/v1/challenges/1", "/api/v1/challenges/2/files", "/api/v1/hints/1", "/files/known-hash/private.txt"):
                self.assertIn(client.get(path).status_code, (401, 403))
        self.action("start")
        self.assertEqual(self.alice.get("/api/v1/challenges/1").status_code, 403)
        self.start()
        self.assertEqual(self.alice.get("/api/v1/challenges/1").status_code, 200)
        for path in ("/api/v1/challenges/2", "/api/v1/hints/1", "/files/known-hash/private.txt"):
            self.assertEqual(self.alice.get(path).status_code, 403)
        self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1}).status_code, 403)
        self.assertEqual(self.carol.get("/api/v1/challenges/1").status_code, 403)

    def test_native_requirements_sequence_shared_solve_and_duplicates(self):
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(2).requirements["prerequisites"], [1])
            self.assertEqual(Challenges.query.get(1).next_id, 2)
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 2).status_code, 403)
        result = self.solve(self.alice, 1)
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(result.json["data"]["status"], "correct")
        duplicate = self.solve(self.bob, 1)
        self.assertEqual(duplicate.json["data"]["status"], "already_solved")
        self.assertEqual(self.bob.get("/api/v1/challenges/2").status_code, 200)
        self.assertEqual(self.bob.get("/api/v1/hints/1").status_code, 200)
        self.assertEqual(self.solve(self.bob, 2).json["data"]["status"], "correct")
        room = self.bob.get(PREFIX + "/rooms/openshift").json["data"]
        self.assertEqual(room["progress"]["status"], "complete")
        self.assertEqual(room["progress"]["solved"], 2)
        self.assertEqual(self.carol.get(PREFIX + "/rooms/openshift").json["data"]["progress"]["solved"], 0)
        with self.app.app_context():
            self.assertEqual(Solves.query.filter_by(team_id=self.team_id).count(), 2)

    def test_idempotent_concurrent_teammate_starts(self):
        self.action("start")
        def start_user(uid):
            client = self.client(uid)
            return self.post(client, PREFIX + "/rooms/openshift/start", {})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(start_user, (2, 3)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(responses[0].json["data"]["progress"]["started_at"], responses[1].json["data"]["progress"]["started_at"])
        with self.app.app_context():
            self.assertEqual(QuestStart.query.count(), 1)

    def test_partial_room_distinct_total_ties_and_last_milestone_time(self):
        self.action("start")
        self.start(self.alice)
        self.start(self.carol)
        with self.app.app_context():
            at = datetime.utcnow() - timedelta(seconds=100)
            QuestEvent.query.get(1).start_at = at
            for start in QuestStart.query.all():
                start.started_at = at
            # Red has out-of-order step 2 only: total 1, room prefix 0.
            db.session.add(Solves(challenge_id=2, user_id=2, team_id=self.team_id, date=at + timedelta(seconds=20)))
            db.session.add(Solves(challenge_id=1, user_id=4, team_id=self.other_id, date=at + timedelta(seconds=20)))
            db.session.commit()
            result = standings()
            self.assertEqual([t["rank"] for t in result["teams"]], [1, 1])
            red = next(t for t in result["teams"] if t["id"] == self.team_id)
            blue = next(t for t in result["teams"] if t["id"] == self.other_id)
            self.assertEqual(red["room_progress"][0]["rank"], 1)
            self.assertEqual(red["room_progress"][0]["path_solved"], 0)
            self.assertEqual(blue["room_progress"][0]["milestone_elapsed_ms"], 20000)
            self.assertEqual(blue["overall_elapsed_ms"], 20000)
            self.assertEqual(blue["room_progress"][0]["rank"], 1)

    def test_pause_cutoff_snapshot_and_explicit_unpublish(self):
        self.action("start")
        self.start()
        self.action("pause")
        paused = self.solve(self.alice, 1)
        self.assertEqual(paused.status_code, 409)
        self.assertEqual(paused.json["data"]["status"], "paused")
        self.assertIn("not submitted", paused.json["error"])
        with self.app.app_context():
            self.assertEqual(QuestPause.query.filter_by(ended_at=None).count(), 1)
        self.action("resume")
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.action("close")
        self.assertEqual(self.solve(self.alice, 2).status_code, 409)
        self.action("publish")
        before = self.guest.get(PREFIX + "/standings").json["data"]["teams"]
        with self.app.app_context():
            Teams.query.get(self.team_id).name = "Edited after publication"
            db.session.add(Solves(challenge_id=2, user_id=2, team_id=self.team_id, date=datetime.utcnow() + timedelta(seconds=60)))
            db.session.commit()
        after = self.guest.get(PREFIX + "/standings").json["data"]["teams"]
        self.assertEqual(before, after)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/corrections", {"team_id": self.team_id, "room_slug": "openshift", "adjustment_ms": 1, "reason": "Test"}).status_code, 409)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/event/action", {"action": "unpublish"}).status_code, 400)
        self.action("unpublish", reason="Correct display name")
        with self.app.app_context():
            self.assertEqual(standings()["teams"][0]["total"], 1)
            self.assertTrue(QuestAudit.query.filter_by(action="event.unpublish").first())

    def test_pause_time_excluded_from_room_and_overall(self):
        self.action("start")
        self.start()
        with self.app.app_context():
            at = datetime.utcnow() - timedelta(seconds=100)
            QuestEvent.query.get(1).start_at = at
            QuestStart.query.one().started_at = at
            db.session.add(QuestPause(started_at=at + timedelta(seconds=20), ended_at=at + timedelta(seconds=60)))
            db.session.add(Solves(challenge_id=1, user_id=2, team_id=self.team_id, date=at + timedelta(seconds=80)))
            db.session.commit()
            team = next(t for t in standings()["teams"] if t["id"] == self.team_id)
            self.assertEqual(team["overall_elapsed_ms"], 40000)
            self.assertEqual(team["room_progress"][0]["milestone_elapsed_ms"], 40000)

    def test_csrf_assets_topology_and_self_allocation_guards(self):
        self.assertEqual(self.admin.patch(PREFIX + "/admin/event", json={"name": "No CSRF"}).status_code, 403)
        for url in ("javascript:alert(1)", "https://example.com/tracker.svg", "//example.com/a", "/files/private.txt", "/plugins/open_tech_quest/assets/../private", "/plugins/open_tech_quest/assets/%2e%2e/a"):
            self.assertEqual(self.post(self.admin, PREFIX + "/admin/rooms/openshift", {"theme": {"logo_url": url}}, "PATCH").status_code, 400)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/rooms/openshift", {"theme": {"hero_url": "/plugins/open_tech_quest/assets/branding/openshift.png"}}, "PATCH").status_code, 200)
        self.assertEqual(self.alice.get("/teams/new").status_code, 403)
        self.assertEqual(self.post(self.alice, "/api/v1/teams/me", {}, "DELETE").status_code, 403)
        self.action("start")
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/rooms/openshift", {"challenge_ids": [2, 1]}, "PATCH").status_code, 409)
        self.assertEqual(self.post(self.admin, "/api/v1/challenges/1", {"requirements": {}}, "PATCH").status_code, 409)
        self.assertEqual(self.post(self.admin, "/api/v1/teams/1/members", {"user_id": 4}).status_code, 409)

    def test_allocation_preview_commit_validation_and_private_rosters(self):
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "check_in"
            QuestOnboarding.query.get(1).team_size = 3
            for i in range(5, 12):
                db.session.add(Users(id=i, name=f"Player {i}", email=f"p{i}@example.test", password="test-only"))
                db.session.add(QuestReadiness(user_id=i))
            db.session.commit()
        response = self.post(self.admin, PREFIX + "/admin/teams/allocate", {"team_size": 3, "preview": True})
        groups = response.json["data"]["groups"]
        self.assertEqual(sorted(len(g["user_ids"]) for g in groups), [2, 2, 3])
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/teams/allocate", {"preview": False, "groups": groups[:-1], "fingerprint": response.json["data"]["fingerprint"]}).status_code, 409)
        result = self.post(self.admin, PREFIX + "/admin/teams/allocate", {"preview": False, "groups": groups, "fingerprint": response.json["data"]["fingerprint"]})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Users.query.filter_by(type="user", team_id=None).count(), 0)
            self.assertEqual(Teams.query.get(self.team_id).name, "Red team")
        release = (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z"
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/event", {"rosters_release_at": release}, "PATCH").status_code, 200)
        self.assertIsNone(self.alice.get(PREFIX + "/session").json["data"]["team_name"])
        self.assertEqual(self.guest.get(PREFIX + "/standings").json["data"]["teams"], [])
        self.assertEqual(self.guest.get("/api/v1/teams").status_code, 403)
        self.assertIn("participants", self.admin.get(PREFIX + "/admin/state").json["data"])

    def test_corrections_exclusions_export_and_audit(self):
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        payload = {"team_id": self.team_id, "room_slug": "openshift", "adjustment_ms": 1000, "reason": "Organizer verified workstation outage"}
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/corrections", payload).status_code, 200)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/corrections", dict(payload, adjustment_ms=86400001)).status_code, 400)
        result = self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"excluded": True, "reason": "Staff team"}, "PATCH")
        self.assertEqual(result.status_code, 200)
        teams = self.guest.get(PREFIX + "/standings").json["data"]["teams"]
        self.assertNotIn(self.team_id, [t["id"] for t in teams])
        exported = self.admin.get(PREFIX + "/admin/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("active_elapsed_ms", exported.get_data(as_text=True))
        self.assertNotIn("alice@example", exported.get_data(as_text=True))
        public = self.guest.get(PREFIX + "/standings").get_data(as_text=True)
        self.assertNotIn("reason", public)
        self.assertNotIn("email", public)
        self.assertNotIn("provided", public)

    def test_manual_membership_and_cross_room_reuse(self):
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "check_in"
            db.session.commit()
        result = self.post(self.admin, PREFIX + "/admin/rooms/ansible", {"challenge_ids": [2]}, "PATCH")
        self.assertEqual(result.status_code, 409)
        result = self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"user_ids": [2]}, "PATCH")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["data"]["user_ids"], [2])
        with self.app.app_context():
            self.assertIsNone(Users.query.get(3).team_id)

    def test_signed_attachment_downloads_respect_room_and_identity(self):
        from CTFd.utils.security.signing import serialize
        with self.app.app_context():
            file = ChallengeFiles.query.filter_by(challenge_id=2).first()
            claims = {"user_id": 2, "team_id": self.team_id, "file_id": file.id}
            valid = serialize(claims)
            wrong_team = serialize(dict(claims, team_id=self.other_id))
            wrong_file = serialize(dict(claims, file_id=file.id + 1))
            with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=int(time.time()) - 7200):
                expired = serialize(claims)
            path = Path(self.app.config["UPLOAD_FOLDER"]) / file.location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Authorized isolated test attachment")
        url = "/files/known-hash/private.txt?token="
        self.assertEqual(self.guest.get(url + valid).status_code, 403)
        self.action("start")
        self.start()
        self.assertEqual(self.guest.get(url + valid).status_code, 403)
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        response = self.guest.get(url + valid)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("Authorized", response.get_data(as_text=True))
        response.close()
        for token in (wrong_team, wrong_file, expired, "invalid"):
            self.assertEqual(self.guest.get(url + token).status_code, 403)
        self.assertEqual(self.carol.get(url + valid).status_code, 403)

    def test_time_policies_offsets_and_legacy_ownership_guard(self):
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/event", {"room_clock": "event", "overall_clock": "team", "start_at": "2026-01-01T10:00:00+02:00"}, "PATCH").status_code, 200)
        value = self.admin.get(PREFIX + "/bootstrap").json["data"]["event"]
        self.assertEqual(value["start_at"], "2026-01-01T08:00:00.000Z")
        for endpoint, payload in (("/api/v1/configs", {"paused": True}), ("/api/v1/configs/user_mode", {"value": "users"})):
            self.assertEqual(self.post(self.admin, endpoint, payload, "PATCH").status_code, 409)
        with self.app.app_context():
            from CTFd.plugins.open_tech_quest import load
            set_config("user_mode", "users")
            db.session.add(Solves(challenge_id=1, user_id=2, date=datetime.utcnow()))
            db.session.commit()
            with self.assertRaisesRegex(RuntimeError, "individual solves"):
                load(self.app)
            self.assertEqual(get_config("user_mode"), "users")

    def test_fifty_team_standings_bounded_queries_and_parallel_load(self):
        self.action("start")
        self.start()
        with self.app.app_context():
            at = datetime.utcnow() - timedelta(minutes=10)
            QuestEvent.query.get(1).start_at = at
            QuestStart.query.one().started_at = at
            password = Users.query.get(2).password
            room = QuestRoom.query.filter_by(slug="openshift").one()
            for index in range(48):
                team = Teams(name=f"Load team {index + 1:02}")
                db.session.add(team)
                db.session.flush()
                uid = index + 10
                db.session.execute(Users.__table__.insert(), {"id": uid, "name": f"Load player {index}", "email": f"load{index}@example.test", "password": password, "type": "user", "team_id": team.id})
                db.session.add(QuestStart(team_id=team.id, room_id=room.id, started_at=at))
                db.session.add(Solves(challenge_id=1, user_id=uid, team_id=team.id, date=at + timedelta(seconds=10 + index)))
            db.session.commit()
            statements = []
            def count(conn, cursor, statement, parameters, context, many):
                statements.append(statement)
            sqlalchemy_event.listen(db.engine, "before_cursor_execute", count)
            try:
                data = standings()
            finally:
                sqlalchemy_event.remove(db.engine, "before_cursor_execute", count)
            self.assertEqual(len(data["teams"]), 50)
            self.assertLessEqual(len(statements), 12)
        def fetch(_):
            client = self.app.test_client()
            started = time.perf_counter()
            response = client.get(PREFIX + "/standings")
            return response.status_code, time.perf_counter() - started, len(response.json["data"]["teams"])
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(fetch, range(50)))
        self.assertTrue(all(status == 200 and count == 50 for status, _, count in results))
        durations = sorted(duration for _, duration, _ in results)
        print(f"50-team isolated projection: 50 requests / concurrency 10; p95={durations[47]:.3f}s; max={durations[-1]:.3f}s")
        self.assertLess(durations[47], 5.0)

    def test_team_form_noop_fields_allow_draft_save_and_live_exclusion(self):
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "check_in"
            QuestTeamName.query.delete()
            db.session.commit()
        endpoint = PREFIX + f"/admin/teams/{self.team_id}"
        result = self.post(self.admin, endpoint, {"name": "Renamed team", "excluded": False, "reason": "", "user_ids": [2, 3]}, "PATCH")
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "finalized"
            for team_id in (self.team_id, self.other_id):
                lock = QuestTeamName.query.get(team_id)
                if lock is None:
                    lock = QuestTeamName(team_id=team_id)
                    db.session.add(lock)
                lock.name_locked = True
            db.session.commit()
            self.assertEqual(QuestTeamName.query.get(self.team_id).claimed_by, 1)
        self.action("start")
        payload = {"name": "Renamed team", "excluded": True, "reason": "Staff team"}
        result = self.post(self.admin, endpoint, payload, "PATCH")
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertTrue(result.json["data"]["excluded"])
        self.assertEqual(self.post(self.admin, endpoint, dict(payload, reason=""), "PATCH").status_code, 200)
        self.assertEqual(self.post(self.admin, endpoint, dict(payload, excluded=False, reason=""), "PATCH").status_code, 400)
        self.assertEqual(self.post(self.admin, endpoint, dict(payload, name="Another name"), "PATCH").status_code, 409)

    def test_native_admin_cannot_destroy_membership_or_solve_history(self):
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        with self.app.app_context():
            solve_id = Solves.query.one().id
        for path, payload, method in (
            ("/api/v1/users/2", {}, "DELETE"),
            (f"/api/v1/submissions/{solve_id}", {}, "DELETE"),
            (f"/api/v1/submissions/{solve_id}", {"type": "incorrect"}, "PATCH"),
            ("/api/v1/submissions", {"type": "correct", "challenge_id": 2, "team_id": self.team_id, "user_id": 2}, "POST"),
        ):
            response = self.post(self.admin, path, payload, method)
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Solves.query.count(), 1)
            self.assertIsNotNone(Users.query.get(2))
            self.assertEqual(next(t for t in standings()["teams"] if t["id"] == self.team_id)["total"], 1)

    def test_native_authoring_accepts_unchanged_position_without_topology_bypass(self):
        payload = {"name": "Edited native challenge", "description": "Updated native instructions", "position": "0"}
        result = self.post(self.admin, "/api/v1/challenges/1", payload, "PATCH")
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(self.post(self.admin, "/api/v1/challenges/1", {"position": "9"}, "PATCH").status_code, 409)
        self.action("start")
        self.assertEqual(self.post(self.admin, "/api/v1/challenges/1", {**payload, "description": "Unreviewed live edit"}, "PATCH").status_code, 409)
        result = reviewed_change(self, self.admin, PREFIX + "/manage/rooms/openshift/challenges/1", {"description": "Reviewed live instructions"})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        with self.app.app_context():
            challenge = Challenges.query.get(1)
            self.assertEqual(challenge.description, "Reviewed live instructions")
            self.assertEqual(challenge.position, 0)
            self.assertEqual(challenge.next_id, 2)

    def test_native_login_landing_for_unassigned_users_and_organizers(self):
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "check_in"
            db.session.commit()
        self.assertEqual(self.admin.get("/team?next=%2Fchallenges").location, "/quest")
        result = self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"user_ids": [2]}, "PATCH")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.bob.get("/team?next=%2Fchallenges").location, "/quest")
        self.assertNotEqual(self.alice.get("/team").location, "/quest")

    def test_native_membership_commit_serializes_with_event_start_on_mariadb(self):
        if self.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            self.skipTest("Production row-lock serialization is exercised on MariaDB")
        with self.app.app_context():
            db.session.add(Users(id=5, name="Waiting player", email="waiting@example.test", password="test-only"))
            db.session.add(QuestReadiness(user_id=5))
            db.session.commit()
        from CTFd.plugins.open_tech_quest.guards import event as original_event
        entered, release = threading.Event(), threading.Event()
        local = threading.local()
        def gate(lock=False):
            result = original_event(lock=lock)
            if getattr(local, "membership", False):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Test failed to release native membership transaction")
            return result
        def native_membership():
            local.membership = True
            return self.post(self.client(1), f"/api/v1/teams/{self.team_id}/members", {"user_id": 5})
        def start_event():
            return self.post(self.client(1), PREFIX + "/admin/event/action", {"action": "start"})
        with patch("CTFd.plugins.open_tech_quest.guards.event", side_effect=gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                membership = pool.submit(native_membership)
                self.assertTrue(entered.wait(3))
                opening = pool.submit(start_event)
                time.sleep(0.1)
                try:
                    self.assertFalse(opening.done(), "Event start must wait for the accepted draft membership transaction")
                finally:
                    release.set()
                self.assertEqual(membership.result().status_code, 409)
                self.assertEqual(opening.result().status_code, 200)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(5).team_id)
            self.assertEqual(QuestEvent.query.get(1).status, "live")

    def test_allocation_rechecks_population_after_waiting_for_mariadb_lock(self):
        if self.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            self.skipTest("Production repeatable-read snapshots are exercised on MariaDB")
        from CTFd.plugins.open_tech_quest.service import event as locked_event
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "check_in"
            db.session.add(Users(id=5, name="Waiting player", email="waiting@example.test", password="test-only"))
            db.session.commit()
            from CTFd.plugins.open_tech_quest import onboarding
            fingerprint = onboarding.fingerprint(locked_event())
            locked_event(lock=True)
            Users.query.get(5).team_id = self.team_id
            db.session.flush()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(lambda: self.post(self.client(1), PREFIX + "/admin/teams/allocate", {"preview": False, "groups": [{"name": "Stale proposal", "user_ids": [5]}], "fingerprint": fingerprint}))
                time.sleep(0.15)
                self.assertFalse(pending.done())
                db.session.commit()
                result = pending.result()
            self.assertEqual(result.status_code, 409, result.get_data(as_text=True))
            db.session.expire_all()
            self.assertEqual(Users.query.get(5).team_id, self.team_id)

    def test_empty_event_cannot_start_and_freeze_authoring(self):
        self.configure([])
        response = self.post(self.admin, PREFIX + "/admin/event/action", {"action": "start"})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("Configure at least one room challenge", response.json["error"])
        self.assertEqual(self.admin.get(PREFIX + "/admin/state").json["data"]["event"]["status"], "draft")
        self.configure([1])
        self.action("start")

    def load_demo(self):
        self.configure([])
        result = self.post(self.admin, PREFIX + "/admin/demo", {"action": "load"})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        return result.json["data"]

    def test_demo_admin_csrf_mappings_and_phase_guards(self):
        endpoint = PREFIX + "/admin/demo"
        with self.guest.session_transaction() as session:
            session["nonce"] = "quest-test-csrf"
        self.assertEqual(self.post(self.guest, endpoint, {"action": "load"}).status_code, 401)
        self.assertEqual(self.post(self.alice, endpoint, {"action": "load"}).status_code, 403)
        self.assertEqual(self.admin.post(endpoint, json={"action": "load"}).status_code, 403)
        self.assertEqual(self.post(self.admin, endpoint, {"action": "unknown"}).status_code, 400)
        state = self.admin.get(PREFIX + "/admin/state").json["data"]["demo"]
        self.assertFalse(state["can_load"])
        self.assertIn("mapped challenges", state["reason"])
        self.assertEqual(self.post(self.admin, endpoint, {"action": "load"}).status_code, 409)
        self.action("start")
        result = self.post(self.admin, endpoint, {"action": "load"})
        self.assertEqual(result.status_code, 409)
        self.assertIn("draft", result.json["error"])

    def test_demo_load_native_chains_preview_flags_and_no_participants(self):
        from CTFd.plugins.open_tech_quest.models import QuestDemoBatch, QuestDemoChallenge
        state = self.load_demo()
        self.assertEqual(state, {"active": True, "challenge_count": 25, "can_load": False, "can_unload": True, "reason": None})
        with self.app.app_context():
            self.assertEqual(Users.query.count(), 4)
            self.assertEqual(Teams.query.count(), 2)
            self.assertEqual(QuestStart.query.count(), 0)
            self.assertEqual(Solves.query.count(), 0)
            self.assertEqual(Challenges.query.count(), 28)
            self.assertEqual(QuestDemoBatch.query.count(), 1)
            self.assertEqual(QuestDemoChallenge.query.count(), 25)
            self.assertIsNotNone(Challenges.query.get(3))
            previews = []
            for room in QuestRoom.query.all():
                self.assertIn("### Your objectives", room.description)
                self.assertIn("demo{", room.description)
                steps = QuestStep.query.filter_by(room_id=room.id).order_by(QuestStep.position).all()
                self.assertEqual(len(steps), 5)
                ids = [step.challenge_id for step in steps]
                for position, challenge_id in enumerate(ids):
                    challenge = Challenges.query.get(challenge_id)
                    self.assertEqual(challenge.requirements["prerequisites"], ids[:position])
                    self.assertEqual(challenge.next_id, ids[position + 1] if position < 4 else None)
                    self.assertIn("### Example artifact", challenge.description)
                    self.assertEqual(challenge.type, "standard")
                    hint = Hints.query.filter_by(challenge_id=challenge_id).one()
                    self.assertEqual(hint.cost, 0)
                    flag = Flags.query.filter_by(challenge_id=challenge_id).one().content
                    self.assertTrue(flag.startswith("demo{"))
                    self.assertIn(flag, hint.content)
                    previews.append((challenge_id, flag, hint.id))
        for challenge_id, flag, hint_id in previews:
            response = self.post(self.admin, "/api/v1/challenges/attempt?preview=true", {"challenge_id": challenge_id, "submission": flag})
            self.assertEqual(response.json["data"]["status"], "correct")
            self.assertIn(flag, self.admin.get(f"/api/v1/hints/{hint_id}?preview=true").json["data"]["content"])
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/demo", {"action": "load"}).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 28)
            self.assertEqual(Solves.query.count(), 0)
            self.assertEqual(QuestAudit.query.filter_by(action="demo.load").count(), 1)

    def test_demo_unload_restores_original_copy_retains_edits_and_people(self):
        for slug in ("openshift", "ansible", "rhel", "security", "ai"):
            self.assertEqual(self.post(self.admin, PREFIX + f"/admin/rooms/{slug}", {"subtitle": f"Original {slug}", "description": f"## Original {slug} introduction"}, "PATCH").status_code, 200)
        self.load_demo()
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/rooms/ansible", {"description": "## Organizer edit\n\nKeep this scenario."}, "PATCH").status_code, 200)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/rooms/rhel", {"subtitle": "Organizer topic update"}, "PATCH").status_code, 200)
        result = self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertFalse(result.json["data"]["active"])
        self.assertTrue(result.json["data"]["can_load"])
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(Flags.query.count(), 3)
            self.assertEqual(Hints.query.count(), 1)
            self.assertEqual(Users.query.count(), 4)
            self.assertEqual(Teams.query.count(), 2)
            self.assertEqual(QuestStep.query.count(), 0)
            for room in QuestRoom.query.all():
                expected = "## Organizer edit\n\nKeep this scenario." if room.slug == "ansible" else f"## Original {room.slug} introduction"
                self.assertEqual(room.description, expected)
                self.assertEqual(room.subtitle, "Organizer topic update" if room.slug == "rhel" else f"Original {room.slug}")
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"}).status_code, 409)

    def test_demo_unload_repairs_later_real_steps_and_preserves_unmapped_content(self):
        self.load_demo()
        room = self.admin.get(PREFIX + "/rooms/openshift").json["data"]
        demo_ids = [step["id"] for step in room["steps"]]
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/rooms/openshift",{"challenge_ids":[demo_ids[0],1,demo_ids[1],2]},"PATCH").status_code,409)
        self.fixture_external_chain("openshift",[demo_ids[0],1,demo_ids[1],2])
        result = self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertFalse(result.json["data"]["can_load"])
        self.assertIn("mapped challenges", result.json["data"]["reason"])
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(Challenges.query.get(1).description, "Private instructions 1")
            self.assertEqual(Challenges.query.get(1).requirements["prerequisites"], [])
            self.assertEqual(Challenges.query.get(1).next_id, 2)
            self.assertEqual(Challenges.query.get(2).requirements["prerequisites"], [1])
            self.assertIsNone(Challenges.query.get(2).next_id)
            self.assertEqual([step.challenge_id for step in QuestStep.query.order_by(QuestStep.position).all()], [1, 2])
            self.assertEqual(Flags.query.filter_by(challenge_id=3).one().content, "fixture-answer-3")

    def test_demo_unload_refuses_submissions_and_reports_disabled_reason(self):
        from CTFd.models import Fails
        self.load_demo()
        with self.app.app_context():
            demo_id = QuestStep.query.first().challenge_id
            db.session.add(Fails(challenge_id=demo_id, user_id=2, team_id=self.team_id, provided="public demo attempt"))
            db.session.add(Solves(challenge_id=demo_id, user_id=2, team_id=self.team_id, provided="public dummy answer"))
            db.session.commit()
        state = self.admin.get(PREFIX + "/admin/state").json["data"]["demo"]
        self.assertFalse(state["can_unload"])
        self.assertIn("submissions", state["reason"])
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"}).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 28)
            self.assertEqual(Fails.query.count(), 1)
            self.assertEqual(Solves.query.count(), 1)

    def test_demo_unload_refuses_room_starts_and_competition_start(self):
        self.load_demo()
        response = self.post(self.admin, PREFIX + "/admin/event/action", {"action": "start"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Remove demo content", response.json["error"])
        with self.app.app_context():
            self.assertEqual(QuestEvent.query.get(1).status, "draft")
            db.session.add(QuestStart(team_id=self.team_id, room_id=QuestRoom.query.first().id))
            db.session.commit()
        state = self.admin.get(PREFIX + "/admin/state").json["data"]["demo"]
        self.assertFalse(state["can_unload"])
        self.assertIn("starts", state["reason"])
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"}).status_code, 409)

    def test_demo_unload_refuses_unrelated_native_dependencies(self):
        self.load_demo()
        with self.app.app_context():
            demo_id = QuestStep.query.first().challenge_id
            Challenges.query.get(3).requirements = {"prerequisites": [demo_id]}
            db.session.commit()
        state = self.admin.get(PREFIX + "/admin/state").json["data"]["demo"]
        self.assertFalse(state["can_unload"])
        self.assertIn("depends on demo", state["reason"])
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"}).status_code, 409)
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(3).requirements["prerequisites"], [demo_id])
            self.assertEqual(Challenges.query.count(), 28)

    def test_demo_does_not_claim_real_challenge_reusing_deleted_id(self):
        self.load_demo()
        with self.app.app_context():
            room = QuestRoom.query.filter_by(slug="openshift").one()
            ids = [step.challenge_id for step in QuestStep.query.filter_by(room_id=room.id).order_by(QuestStep.position).all()]
        self.fixture_external_chain("openshift",ids[:-1])
        self.assertEqual(self.post(self.admin, f"/api/v1/challenges/{ids[-1]}", {}, "DELETE").status_code, 200)
        with self.app.app_context():
            db.session.add(Challenges(id=ids[-1], name="Real replacement", description="Real organizer content", type="standard", state="visible", value=100))
            db.session.commit()
        result = self.post(self.admin, PREFIX + "/admin/demo", {"action": "unload"})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(ids[-1]).description, "Real organizer content")
            self.assertEqual(Challenges.query.count(), 4)

    def test_demo_load_failure_rolls_back_every_created_row_and_room_copy(self):
        from CTFd.plugins.open_tech_quest.demo import DemoConflict, challenge_content
        from CTFd.plugins.open_tech_quest.models import QuestDemoBatch, QuestDemoChallenge
        self.configure([])
        count = 0
        def fail_midway(slug, item):
            nonlocal count
            count += 1
            if count == 7:
                raise DemoConflict("Unable to complete demo setup; no content was saved")
            return challenge_content(slug, item)
        with patch("CTFd.plugins.open_tech_quest.demo.challenge_content", side_effect=fail_midway):
            response = self.post(self.admin, PREFIX + "/admin/demo", {"action": "load"})
        self.assertEqual(response.status_code, 409)
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(Flags.query.count(), 3)
            self.assertEqual(Hints.query.count(), 1)
            self.assertEqual(QuestStep.query.count(), 0)
            self.assertEqual(QuestDemoBatch.query.count(), 0)
            self.assertEqual(QuestDemoChallenge.query.count(), 0)
            self.assertTrue(all(room.description == "" and room.subtitle == "" for room in QuestRoom.query.all()))

    def test_demo_can_reload_after_unload_without_claiming_old_ids(self):
        from CTFd.plugins.open_tech_quest.models import QuestDemoBatch
        self.load_demo()
        for action in ("unload", "load", "unload"):
            response = self.post(self.admin, PREFIX + "/admin/demo", {"action": action})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(QuestDemoBatch.query.count(), 2)
            self.assertEqual(QuestDemoBatch.query.filter_by(active=True).count(), 0)

    def test_demo_concurrent_load_creates_only_one_batch(self):
        from CTFd.plugins.open_tech_quest.models import QuestDemoBatch
        self.configure([])
        def load_once(_):
            return self.post(self.client(1), PREFIX + "/admin/demo", {"action": "load"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(load_once, range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        with self.app.app_context():
            self.assertEqual(QuestDemoBatch.query.filter_by(active=True).count(), 1)
            self.assertEqual(Challenges.query.count(), 28)
            self.assertEqual(QuestStep.query.count(), 25)

    def test_demo_unload_serializes_native_delete_and_id_reuse_on_sqlite(self):
        if not self.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            self.skipTest("SQLite native ID reuse and writer serialization")
        self.load_demo()
        with self.app.app_context():
            room = QuestRoom.query.filter_by(slug="ai").one()
            ids = [step.challenge_id for step in QuestStep.query.filter_by(room_id=room.id).order_by(QuestStep.position).all()]
        self.fixture_external_chain("ai",ids[:-1])
        from CTFd.plugins.open_tech_quest.demo import owned_ids as original_owned_ids
        entered, release = threading.Event(), threading.Event()
        local = threading.local()
        def gate(batch):
            result = original_owned_ids(batch)
            if getattr(local, "unloading", False):
                local.calls = getattr(local, "calls", 0) + 1
                if local.calls == 2:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("Unload test gate was not released")
            return result
        def unload():
            local.unloading = True
            return self.post(self.client(1), PREFIX + "/admin/demo", {"action": "unload"})
        def native_replace():
            client = self.client(1)
            deleted = self.post(client, f"/api/v1/challenges/{ids[-1]}", {}, "DELETE")
            created = self.post(client, "/api/v1/challenges", {"name": "Real concurrent challenge", "description": "Real organizer content", "category": "Real", "value": 100, "state": "visible", "type": "standard"})
            return deleted, created
        with patch("CTFd.plugins.open_tech_quest.demo.owned_ids", side_effect=gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending_unload = pool.submit(unload)
                self.assertTrue(entered.wait(3))
                pending_native = pool.submit(native_replace)
                time.sleep(0.1)
                try:
                    self.assertFalse(pending_native.done(), "Native writes must wait for the SQLite demo transaction")
                finally:
                    release.set()
                result = pending_unload.result()
                deleted, created = pending_native.result()
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertIn(deleted.status_code, (200, 404))
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(created.json["data"]["id"]).description, "Real organizer content")

    def test_demo_load_cannot_interleave_with_competition_start(self):
        if not self.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            self.skipTest("SQLite lifecycle writer serialization")
        from CTFd.plugins.open_tech_quest.demo import active_batch as original_active_batch
        self.configure([])
        entered, release = threading.Event(), threading.Event()
        local = threading.local()
        def gate():
            result = original_active_batch()
            if getattr(local, "opening", False):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Event start gate was not released")
            return result
        def start_event():
            local.opening = True
            return self.post(self.client(1), PREFIX + "/admin/event/action", {"action": "start"})
        def load_demo():
            return self.post(self.client(1), PREFIX + "/admin/demo", {"action": "load"})
        with patch("CTFd.plugins.open_tech_quest.demo.active_batch", side_effect=gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending_start = pool.submit(start_event)
                self.assertTrue(entered.wait(3))
                pending_load = pool.submit(load_demo)
                time.sleep(0.1)
                try:
                    self.assertFalse(pending_load.done(), "Demo load must wait for the event lifecycle decision")
                finally:
                    release.set()
                started, loaded = pending_start.result(), pending_load.result()
        self.assertEqual(started.status_code, 409)
        self.assertEqual(loaded.status_code, 200, loaded.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(QuestEvent.query.get(1).status, "draft")
            self.assertIsNotNone(original_active_batch())

    def open_onboarding(self, capacity=5, allow_team_naming=True):
        with self.app.app_context():
            state = QuestOnboarding.query.get(1)
            state.phase, state.team_size, state.grandfathered = "check_in", capacity, False
            state.allow_team_naming = allow_team_naming
            state.applied_at, state.finalized_at = None, None
            QuestReadiness.query.delete()
            QuestTeamName.query.delete()
            db.session.commit()

    def attendance_window(self, enabled):
        response = self.post(self.admin, PREFIX + "/admin/attendance", {"enabled": enabled}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def ready_user(self, client):
        response = self.post(client, PREFIX + "/onboarding/ready", {})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def onboarding_action(self, action, **values):
        return self.post(self.admin, PREFIX + "/admin/onboarding/action", dict(action=action, **values))

    def attendance_plan(self):
        response = self.post(self.admin, PREFIX + "/admin/onboarding/optimize", {"preview": True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def apply_attendance(self, plan):
        response = self.post(self.admin, PREFIX + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def test_onboarding_personal_ready_is_idempotent_and_has_no_clock_side_effects(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.assertEqual(self.guest.get(PREFIX + "/onboarding").status_code, 401)
        self.assertEqual(self.post(self.admin, PREFIX + "/onboarding/ready", {}).status_code, 403)
        self.assertEqual(self.alice.post(PREFIX + "/onboarding/ready", json={}).status_code, 403)
        self.assertEqual(self.post(self.alice, PREFIX + "/onboarding/ready", {"ready_at": "2000-01-01T00:00:00Z"}).status_code, 400)
        first, second = self.ready_user(self.alice), self.ready_user(self.alice)
        self.assertEqual(first["ready_at"], second["ready_at"])
        self.assertFalse(first["late"])
        self.assertNotIn("email", str(first))
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(), 1)
            self.assertEqual(QuestStart.query.count(), 0)
            self.assertEqual(Solves.query.count(), 0)
            self.assertIsNone(QuestEvent.query.get(1).start_at)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        self.attendance_window(True)
        late = self.ready_user(self.bob)
        self.assertTrue(late["late"])
        self.attendance_window(False)
        self.assertEqual(self.onboarding_action("reopen_checkin").status_code, 200)
        self.assertTrue(self.bob.get(PREFIX + "/onboarding").json["data"]["late"])

    def test_onboarding_full_workflow_preserves_readiness_names_and_empty_team_history(self):
        self.open_onboarding()
        self.attendance_window(True)
        for client in (self.alice, self.bob, self.carol):
            self.ready_user(client)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/event/action", {"action": "start"}).status_code, 409)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        plan = self.attendance_plan()
        self.assertEqual(len(plan["groups"]), 1)
        self.assertEqual(self.apply_attendance(plan)["phase"], "naming")
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"]}).status_code, 409)
        self.assertEqual(self.onboarding_action("reopen_checkin").status_code, 409)
        self.assertEqual(self.onboarding_action("finalize").status_code, 409)
        response = self.post(self.alice, PREFIX + "/onboarding/name", {"name": "Workshop Foxes", "confirm": True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.json["data"]["team"]["name_locked"])
        self.assertEqual(self.post(self.bob, PREFIX + "/onboarding/name", {"name": "Other name", "confirm": True}).status_code, 409)
        self.assertEqual(self.onboarding_action("finalize").status_code, 200)
        self.assertTrue(self.admin.get(PREFIX + "/admin/state").json["data"]["onboarding"]["can_start"])
        public = self.guest.get(PREFIX + "/standings").json["data"]["teams"]
        self.assertEqual([team["name"] for team in public], ["Workshop Foxes"])
        with self.app.app_context():
            self.assertEqual(Teams.query.count(), 2)
            self.assertEqual(QuestReadiness.query.count(), 3)
            self.assertIsNotNone(Teams.query.get(self.other_id))
        self.action("start")

    def test_onboarding_preview_rejects_changed_attendance_capacity_identity_membership(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        self.ready_user(self.bob)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        def reject(plan):
            result = self.post(self.admin, PREFIX + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"]})
            self.assertEqual(result.status_code, 409, result.get_data(as_text=True))
            self.assertEqual(self.admin.get(PREFIX + "/admin/state").json["data"]["onboarding"]["phase"], "closed")
        plan = self.attendance_plan()
        self.attendance_window(True)
        self.ready_user(self.carol)
        self.attendance_window(False)
        reject(plan)
        plan = self.attendance_plan()
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/onboarding", {"team_size": 4}, "PATCH").status_code, 200)
        reject(plan)
        plan = self.attendance_plan()
        self.assertEqual(self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"name": "Provisional rename"}, "PATCH").status_code, 200)
        reject(plan)
        plan = self.attendance_plan()
        self.assertEqual(self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"user_ids": [2]}, "PATCH").status_code, 200)
        reject(plan)
        plan = self.attendance_plan()
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"], "groups": []}).status_code, 400)
        self.apply_attendance(plan)

    def test_onboarding_concurrent_once_name_claims_and_casefold_uniqueness(self):
        self.open_onboarding(capacity=2)
        self.attendance_window(True)
        for client in (self.alice, self.bob, self.carol):
            self.ready_user(client)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        self.apply_attendance(self.attendance_plan())
        def claim(item):
            uid, name = item
            return self.post(self.client(uid), PREFIX + "/onboarding/name", {"name": name, "confirm": True})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [(2,"Orbit Foxes"),(3,"Orbit Ravens")]))
        self.assertEqual(sorted(response.status_code for response in results), [200,409])
        name = next(r.json["data"]["team"]["name"] for r in results if r.status_code == 200)
        self.assertEqual(self.post(self.carol, PREFIX + "/onboarding/name", {"name": name.swapcase(), "confirm": True}).status_code, 409)
        self.assertEqual(self.post(self.carol, PREFIX + "/onboarding/name", {"name": "Other cohort"}).status_code, 400)
        self.assertEqual(self.post(self.carol, PREFIX + "/onboarding/name", {"name": "Other cohort", "confirm": True}).status_code, 200)
        self.assertEqual(self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"name": "Corrected name"}, "PATCH").status_code, 400)
        self.assertEqual(self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"name": "Corrected name", "reason": "Organizer verified preferred spelling"}, "PATCH").status_code, 200)

    def test_onboarding_late_arrivals_can_form_reasoned_capacity_bound_team(self):
        self.open_onboarding(capacity=1)
        self.attendance_window(True)
        for client in (self.alice,self.bob,self.carol):
            self.ready_user(client)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        self.apply_attendance(self.attendance_plan())
        with self.app.app_context():
            db.session.add(Users(id=5, name="Late participant", email="late@example.test", password="test-only"))
            db.session.commit()
        late = self.client(5)
        self.attendance_window(True)
        self.assertTrue(self.ready_user(late)["late"])
        self.attendance_window(False)
        self.assertEqual(self.onboarding_action("finalize").status_code, 409)
        self.assertEqual(self.onboarding_action("create_late_team", user_ids=[5]).status_code, 400)
        self.assertEqual(self.onboarding_action("create_late_team", user_ids=[5], reason="Late arrival after attendance plan").status_code, 200)
        self.assertTrue(late.get(PREFIX + "/onboarding").json["data"]["can_name"])
        for uid in (2,3,4,5):
            response = self.post(self.client(uid), PREFIX + "/onboarding/name", {"name": f"Final team {uid}", "confirm": True})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.onboarding_action("finalize").status_code, 200)
        self.assertEqual(self.onboarding_action("create_late_team", user_ids=[5], reason="Cannot move final member").status_code, 409)
        self.assertEqual(self.post(self.admin, PREFIX + f"/admin/teams/{self.team_id}", {"user_ids": []}, "PATCH").status_code, 409)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/onboarding", {"team_size": 10}, "PATCH").status_code, 409)

    def test_onboarding_roster_privacy_and_native_frozen_name_bypasses(self):
        self.open_onboarding(capacity=2)
        self.attendance_window(True)
        for client in (self.alice,self.bob,self.carol):
            self.ready_user(client)
        self.assertEqual(self.onboarding_action("close_checkin").status_code, 200)
        self.apply_attendance(self.attendance_plan())
        own = self.alice.get(PREFIX + "/onboarding").json["data"]
        self.assertEqual({member["id"] for member in own["team"]["members"]}, {2,3})
        self.assertNotIn("email", str(own))
        self.assertNotIn("@", str(own))
        for uid,name in ((2,"Native Guard Foxes"),(4,"Native Guard Ravens")):
            self.assertEqual(self.post(self.client(uid), PREFIX + "/onboarding/name", {"name": name, "confirm": True}).status_code, 200)
        self.assertEqual(self.onboarding_action("finalize").status_code, 200)
        for path,payload,method in ((f"/api/v1/teams/{self.team_id}",{"name":"Bypass"},"PATCH"),(f"/api/v1/teams/{self.team_id}/members",{"user_id":4},"POST"),("/api/v1/users/2",{},"DELETE")):
            self.assertEqual(self.post(self.admin,path,payload,method).status_code,409)
        self.assertEqual(self.post(self.alice,"/api/v1/teams/me",{"name":"Bypass"},"PATCH").status_code,403)
        self.assertEqual(self.admin.get("/teams/new").status_code,403)
        self.assertEqual(self.post(self.alice,"/api/v1/users/me",{"name":"Alice Updated"},"PATCH").status_code,200)
        self.assertEqual(self.alice.get(PREFIX + "/onboarding").json["data"]["team"]["name"],"Native Guard Foxes")
        release = (datetime.utcnow()+timedelta(hours=1)).isoformat()+"Z"
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/event",{"rosters_release_at":release},"PATCH").status_code,200)
        hidden = self.alice.get(PREFIX+"/onboarding").json["data"]
        self.assertIsNone(hidden["team"])
        self.assertFalse(hidden["can_name"])
        self.assertIsNotNone(hidden["ready_at"])

    def test_onboarding_optimizer_keeps_full_groups_minimal_moves_and_no_show_accounts(self):
        self.open_onboarding()
        self.attendance_window(True)
        with self.app.app_context():
            password = Users.query.get(2).password
            for uid in range(5,24):
                db.session.execute(Users.__table__.insert(), {"id":uid,"name":f"Attendee {uid}","email":f"attendee{uid}@example.test","password":password,"type":"user"})
            teams = Teams.query.order_by(Teams.id).all()
            for index in range(3):
                team = Teams(name=f"Provisional {index+3}")
                db.session.add(team)
                db.session.flush()
                teams.append(team)
            for index,uid in enumerate(range(2,23)):
                Users.query.get(uid).team_id = teams[index//5].id
                db.session.add(QuestReadiness(user_id=uid))
            Users.query.get(23).team_id = teams[-1].id
            db.session.commit()
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan = self.attendance_plan()
        self.assertEqual(sorted(len(group["user_ids"]) for group in plan["groups"]),[4,4,4,4,5])
        self.assertEqual(len(plan["moves"]),3)
        self.assertEqual(plan["unassigned_user_ids"],[23])
        self.assertEqual(self.attendance_plan(),plan)
        self.apply_attendance(plan)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(23).team_id)
            self.assertEqual(Users.query.count(),23)
            self.assertEqual(Teams.query.count(),5)
            self.assertEqual(QuestReadiness.query.count(),21)

    def test_onboarding_optimizer_preserves_activity_or_refuses_absent_members(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        with self.app.app_context():
            at = datetime.utcnow()-timedelta(minutes=1)
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id,date=at))
            db.session.add(QuestStart(team_id=self.team_id,room_id=QuestRoom.query.first().id,started_at=at))
            db.session.commit()
        refused = self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":True})
        self.assertEqual(refused.status_code,409)
        self.assertIn("activity",refused.json["error"])
        self.attendance_window(True)
        self.ready_user(self.bob)
        self.attendance_window(False)
        plan = self.attendance_plan()
        self.assertEqual(plan["groups"][0]["team_id"],self.team_id)
        self.apply_attendance(plan)
        with self.app.app_context():
            self.assertEqual(Solves.query.one().date,at)
            self.assertEqual(QuestStart.query.one().started_at,at)
            self.assertEqual(Users.query.get(2).team_id,self.team_id)
            self.assertEqual(Users.query.get(3).team_id,self.team_id)
            self.assertIsNone(Users.query.get(4).team_id)

    def test_onboarding_additive_upgrade_grandfathers_live_without_changing_history(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path("/opt/CTFd/CTFd/plugins/open_tech_quest/migrations/0004_onboarding.py")
        spec = importlib.util.spec_from_file_location("quest_onboarding_upgrade",path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.app.app_context():
            at = datetime.utcnow()-timedelta(minutes=3)
            e = QuestEvent.query.get(1)
            e.status,e.start_at = "live",at
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id,date=at+timedelta(seconds=20)))
            db.session.commit()
            expected = [(u.id,u.team_id,u.name) for u in Users.query.order_by(Users.id).all()]
            db.session.remove()
            with db.engine.connect() as connection:
                transaction = connection.begin()
                for model in (QuestReadiness,QuestTeamName,QuestOnboarding):
                    model.__table__.drop(bind=connection)
                migration.upgrade(Operations(MigrationContext.configure(connection)))
                transaction.commit()
            db.session.expire_all()
            state = QuestOnboarding.query.get(1)
            self.assertEqual(state.phase,"finalized")
            self.assertTrue(state.grandfathered)
            self.assertEqual(state.team_size,5)
            self.assertEqual([(u.id,u.team_id,u.name) for u in Users.query.order_by(Users.id).all()],expected)
            self.assertEqual(QuestReadiness.query.count(),0)
            self.assertTrue(all(row.name_locked for row in QuestTeamName.query.all()))
            self.assertEqual(QuestEvent.query.get(1).start_at,at)
            self.assertEqual(Solves.query.one().date,at+timedelta(seconds=20))

    def test_onboarding_empty_history_team_remains_in_standings(self):
        from CTFd.models import Awards
        with self.app.app_context():
            archived = Teams(name="Historical empty team")
            empty = Teams(name="Unused empty team")
            db.session.add_all([archived,empty])
            db.session.flush()
            archived_id,empty_id = archived.id,empty.id
            db.session.add(Awards(team_id=archived.id,name="Historical note",value=0))
            db.session.commit()
            ids = {team["id"] for team in standings()["teams"]}
            self.assertIn(archived_id,ids)
            self.assertNotIn(empty_id,ids)

    def test_onboarding_ban_after_finalization_does_not_deadlock_start_or_move_roster(self):
        self.open_onboarding(capacity=2)
        self.attendance_window(True)
        for client in (self.alice,self.bob,self.carol):
            self.ready_user(client)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        self.apply_attendance(self.attendance_plan())
        for uid,name in ((2,"Moderation Foxes"),(4,"Moderation Ravens")):
            self.assertEqual(self.post(self.client(uid),PREFIX+"/onboarding/name",{"name":name,"confirm":True}).status_code,200)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)
        self.assertEqual(self.post(self.admin,"/api/v1/users/3",{"banned":True},"PATCH").status_code,200)
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{"type":"admin"},"PATCH").status_code,409)
        self.assertTrue(self.admin.get(PREFIX+"/admin/state").json["data"]["onboarding"]["can_start"])
        self.action("start")
        self.assertEqual(self.bob.get(PREFIX+"/onboarding").status_code,403)
        with self.app.app_context():
            self.assertEqual(Users.query.get(3).team_id,self.team_id)
            self.assertTrue(Users.query.get(3).banned)

    def test_onboarding_reasoned_ineligible_removal_before_finalization(self):
        self.open_onboarding()
        self.attendance_window(True)
        for client in (self.alice,self.bob,self.carol):
            self.ready_user(client)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        self.apply_attendance(self.attendance_plan())
        self.assertEqual(self.post(self.admin,"/api/v1/users/3",{"banned":True},"PATCH").status_code,200)
        endpoint = PREFIX+f"/admin/teams/{self.team_id}"
        self.assertEqual(self.post(self.admin,endpoint,{"user_ids":[2,4]},"PATCH").status_code,400)
        result = self.post(self.admin,endpoint,{"user_ids":[2,4],"reason":"Banned account removed before final roster"},"PATCH")
        self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        self.assertEqual(self.post(self.admin,endpoint,{"user_ids":[2],"reason":"Arbitrary eligible move"},"PATCH").status_code,409)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(3).team_id)

    def test_onboarding_generated_placeholder_must_change_but_meaningful_name_can_confirm(self):
        self.open_onboarding(capacity=1)
        self.attendance_window(True)
        for client in (self.alice,self.bob,self.carol):
            self.ready_user(client)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan = self.attendance_plan()
        generated = next(g for g in plan["groups"] if g["team_id"] is None)
        retained = next(g for g in plan["groups"] if g["team_id"] is not None)
        generated_member = self.client(generated["user_ids"][0])
        retained_member = self.client(retained["user_ids"][0])
        self.apply_attendance(plan)
        own = generated_member.get(PREFIX+"/onboarding").json["data"]
        self.assertTrue(own["team"]["name"].startswith("Team "))
        response = self.post(generated_member,PREFIX+"/onboarding/name",{"name":own["team"]["name"].swapcase()+" ","confirm":True})
        self.assertEqual(response.status_code,400)
        self.assertIn("placeholder",response.json["error"])
        retained = retained_member.get(PREFIX+"/onboarding").json["data"]
        self.assertEqual(self.post(retained_member,PREFIX+"/onboarding/name",{"name":retained["team"]["name"],"confirm":True}).status_code,200)

    def test_onboarding_hidden_team_names_remain_reserved_when_creating_replacement(self):
        self.open_onboarding()
        self.attendance_window(True)
        with self.app.app_context():
            password = Users.query.get(2).password
            for uid in range(5,23):
                db.session.execute(Users.__table__.insert(), {"id":uid,"name":f"Attendee {uid}","email":f"attendee{uid}@example.test","password":password,"type":"user"})
            teams = Teams.query.order_by(Teams.id).all()
            for index in range(3):
                team = Teams(name=f"Team {index+3}")
                db.session.add(team)
                db.session.flush()
                teams.append(team)
            for index,team in enumerate(teams,1):
                team.name = f"Team {index}"
            hidden_id = teams[0].id
            for index,uid in enumerate(range(2,23)):
                Users.query.get(uid).team_id = teams[index//5].id
                db.session.add(QuestReadiness(user_id=uid))
            db.session.commit()
        self.assertEqual(self.post(self.admin,f"/api/v1/teams/{hidden_id}",{"hidden":True},"PATCH").status_code,200)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan = self.attendance_plan()
        fresh = [group for group in plan["groups"] if group["team_id"] is None]
        self.assertEqual(len(fresh),1)
        self.assertEqual(fresh[0]["name"],"Team 6")
        self.apply_attendance(plan)
        with self.app.app_context():
            self.assertTrue(Teams.query.get(hidden_id).hidden)
            self.assertEqual(Teams.query.get(hidden_id).name,"Team 1")
            self.assertEqual(Teams.query.count(),6)
            self.assertEqual(Users.query.filter(Users.type=="user",Users.team_id.isnot(None)).count(),21)
            self.assertEqual(Teams.query.filter_by(name="Team 6").count(),1)
            self.assertEqual(QuestReadiness.query.count(),21)

    def test_registration_toggle_authorization_csrf_strict_body_and_audit(self):
        endpoint = PREFIX + "/admin/registration"
        with self.guest.session_transaction() as session:
            session["nonce"] = "quest-test-csrf"
        self.assertEqual(self.post(self.guest, endpoint, {"enabled": False}, "PATCH").status_code, 401)
        self.assertEqual(self.post(self.alice, endpoint, {"enabled": False}, "PATCH").status_code, 403)
        self.assertEqual(self.admin.patch(endpoint, json={"enabled": False}).status_code, 403)
        for payload in ({}, {"enabled": "false"}, {"enabled": 0}, {"enabled": None}, {"enabled": False, "extra": True}):
            self.assertEqual(self.post(self.admin, endpoint, payload, "PATCH").status_code, 400)
        self.assertEqual(self.guest.get(PREFIX + "/bootstrap").json["data"]["registration"], {"enabled": True})
        response = self.post(self.admin, endpoint, {"enabled": False}, "PATCH")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"], {"enabled": False})
        self.assertEqual(self.admin.get(PREFIX + "/admin/state").json["data"]["registration"], {"enabled": False})
        with self.app.app_context():
            row = Configs.query.filter_by(key="registration_visibility").one()
            self.assertEqual(row.value, "private")
            audit = QuestAudit.query.filter_by(action="registration.configure").one()
            self.assertEqual(audit.detail, {"old": "public", "new": "private"})
            self.assertEqual(audit.actor_id, 1)

    def test_registration_native_closed_form_and_stale_public_cache_are_blocked(self):
        from CTFd.plugins.open_tech_quest.registration import invalidate
        opened = self.guest.get("/register")
        self.assertEqual(opened.status_code, 200)
        self.assertIn("password", opened.get_data(as_text=True))
        with self.guest.session_transaction() as session:
            nonce = session["nonce"]
        with self.app.app_context():
            self.assertEqual(get_config("registration_visibility"), "public")
        committed, release = threading.Event(), threading.Event()
        def delay_invalidation():
            committed.set()
            if not release.wait(3):
                raise RuntimeError("Registration cache invalidation gate was not released")
            invalidate()
        def close_registration():
            return self.post(self.client(1), PREFIX + "/admin/registration", {"enabled": False}, "PATCH")
        with patch("CTFd.plugins.open_tech_quest.registration.invalidate", side_effect=delay_invalidation):
            with ThreadPoolExecutor(max_workers=1) as pool:
                closing = pool.submit(close_registration)
                try:
                    self.assertTrue(committed.wait(3))
                    # Native cache is still public after the private DB value
                    # committed; the authoritative guard must reject both paths.
                    with self.app.app_context():
                        self.assertEqual(get_config("registration_visibility"), "public")
                    response = self.guest.get("/register", headers={"Accept": "application/json"})
                    self.assertEqual(response.status_code, 403)
                    self.assertIn("registration is closed", response.json["error"])
                    posted = self.guest.post("/register", data={"nonce": nonce, "name": "Stale form user", "email": "stale@example.test", "password": "test-only"}, headers={"Accept": "application/json"})
                    self.assertEqual(posted.status_code, 403)
                    self.assertIn("registration is closed", posted.json["error"])
                    html = self.guest.post("/register", data={"nonce": nonce, "name": "HTML stale form user", "email": "html-stale@example.test", "password": "test-only"}, headers={"Accept": "text/html,application/xhtml+xml;q=0.9"})
                    self.assertEqual(html.status_code, 403)
                    self.assertEqual(html.mimetype, "text/html")
                    self.assertIn("Public registration is closed", html.get_data(as_text=True))
                    self.assertIn("/login", html.get_data(as_text=True))
                    for headers in ({}, {"Accept": "*/*"}, {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"}):
                        navigation = self.guest.get("/register", headers=headers)
                        self.assertEqual(navigation.status_code, 403)
                        self.assertEqual(navigation.mimetype, "text/html")
                        self.assertIn("Public registration is closed", navigation.get_data(as_text=True))
                        self.assertIn("/login", navigation.get_data(as_text=True))
                        form = self.guest.post("/register", data={"nonce": nonce, "name": "Default browser form", "email": "default-browser@example.test", "password": "test-only"}, headers=headers)
                        self.assertEqual(form.status_code, 403)
                        self.assertEqual(form.mimetype, "text/html")
                        self.assertIn("/login", form.get_data(as_text=True))
                    json_body = self.guest.post("/register", json={}, headers={"CSRF-Token": nonce})
                    self.assertEqual(json_body.status_code, 403)
                    self.assertEqual(json_body.mimetype, "application/json")
                    self.assertIn("registration is closed", json_body.json["error"])
                    with self.app.app_context():
                        self.assertEqual(Users.query.count(), 4)
                finally:
                    release.set()
                self.assertEqual(closing.result().status_code, 200)
        self.assertFalse(self.guest.get(PREFIX + "/bootstrap").json["data"]["registration"]["enabled"])

    def test_registration_reopen_uses_native_account_creation_and_existing_ready_login(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/registration", {"enabled": False}, "PATCH").status_code, 200)
        existing = self.app.test_client()
        self.assertEqual(existing.get("/login").status_code, 200)
        with existing.session_transaction() as session:
            nonce = session["nonce"]
        response = existing.post("/login", data={"nonce": nonce, "name": "Alice", "password": "test-only"})
        self.assertEqual(response.status_code, 302)
        with existing.session_transaction() as session:
            self.assertEqual(session["id"], 2)
            existing_nonce = session["nonce"]
        ready = existing.post(PREFIX + "/onboarding/ready", json={}, headers={"CSRF-Token": existing_nonce})
        self.assertEqual(ready.status_code, 200, ready.get_data(as_text=True))
        self.assertIsNotNone(ready.json["data"]["ready_at"])
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/registration", {"enabled": True}, "PATCH").status_code, 200)
        self.assertEqual(self.guest.get("/register").status_code, 200)
        with self.guest.session_transaction() as session:
            nonce = session["nonce"]
        created = self.guest.post("/register", data={"nonce": nonce, "name": "Native reopened user", "email": "native-reopened@example.test", "password": "test-only"})
        self.assertEqual(created.status_code, 302, created.get_data(as_text=True))
        with self.app.app_context():
            user = Users.query.filter_by(email="native-reopened@example.test").one()
            self.assertIsNone(user.team_id)
            self.assertIsNone(QuestReadiness.query.get(user.id))
            self.assertEqual(QuestStart.query.count(), 0)

    def test_registration_state_persists_in_every_phase_without_startup_reset(self):
        from CTFd.plugins.open_tech_quest.onboarding import initialize
        from CTFd.plugins.open_tech_quest.registration import status
        for phase in ("draft", "live", "paused", "closed", "published"):
            with self.app.app_context():
                QuestEvent.query.get(1).status = phase
                db.session.commit()
                get_config("registration_visibility")  # Warm the native cache.
            response = self.post(self.admin, PREFIX + "/admin/registration", {"enabled": False}, "PATCH")
            self.assertEqual(response.status_code, 200)
            with self.app.app_context():
                self.assertEqual(get_config("registration_visibility"), "private")
                initialize(QuestEvent.query.get(1))
                self.assertEqual(status(), {"enabled": False})
                # A server-side native change remains the same source of truth.
                set_config("registration_visibility", "public")
                self.assertEqual(status(), {"enabled": True})
            self.assertTrue(self.guest.get(PREFIX + "/bootstrap").json["data"]["registration"]["enabled"])
        with self.app.app_context():
            set_config("registration_visibility", "mlc")
        self.assertFalse(self.guest.get(PREFIX + "/bootstrap").json["data"]["registration"]["enabled"])
        self.assertEqual(self.guest.get("/register").status_code, 403)

    def test_registration_native_http_config_writes_are_managed_but_reads_remain(self):
        self.assertEqual(self.post(self.admin, PREFIX + "/admin/registration", {"enabled": False}, "PATCH").status_code, 200)
        for path, method, payload in (
            ("/api/v1/configs/registration_visibility", "PATCH", {"value": "public"}),
            ("/api/v1/configs/registration_visibility", "DELETE", {}),
            ("/api/v1/configs", "PATCH", {"ctf_name": "Should not change", "registration_visibility": "public"}),
            ("/api/v1/configs", "POST", {"key": "registration_visibility", "value": "public"}),
            ("/api/v1/configs/another_key", "PATCH", {"key": "registration_visibility", "value": "public"}),
            ("/api/v1/configs/REGISTRATION_VISIBILITY", "PATCH", {"value": "public"}),
            ("/api/v1/configs", "PATCH", {"REGISTRATION_VISIBILITY": "public"}),
            ("/api/v1/configs", "POST", {"key": "REGISTRATION_VISIBILITY", "value": "public"}),
            ("/api/v1/configs/another_key", "PATCH", {"key": "REGISTRATION_VISIBILITY", "value": "public"}),
        ):
            response = self.post(self.admin, path, payload, method)
            self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
            self.assertIn("registration", response.json["error"])
        legacy = self.admin.post("/admin/plugins/open_tech_quest", data={"nonce": "quest-test-csrf", "registration_visibility": "public"})
        self.assertEqual(legacy.status_code, 409)
        native = self.admin.get("/api/v1/configs/registration_visibility")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json["data"]["value"], "private")
        with self.app.app_context():
            self.assertEqual(get_config("ctf_name"), "Quest test")
            self.assertEqual(Configs.query.filter_by(key="registration_visibility").count(), 1)
        # Native administrator provisioning is not public self-registration.
        response = self.post(self.admin, "/api/v1/users", {"name": "Provisioned user", "email": "provisioned@example.test", "password": "test-only"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_registration_close_waits_for_an_authorized_native_creation(self):
        self.assertEqual(self.guest.get("/register").status_code, 200)
        with self.guest.session_transaction() as session:
            nonce = session["nonce"]
        entered, release = threading.Event(), threading.Event()
        original_init = Users.__init__
        def gated_init(user, *args, **kwargs):
            if kwargs.get("name") == "In-flight registrant":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Native registration gate was not released")
            original_init(user, *args, **kwargs)
        def register():
            return self.guest.post("/register", data={"nonce": nonce, "name": "In-flight registrant", "email": "inflight@example.test", "password": "test-only"})
        def close():
            return self.post(self.client(1), PREFIX + "/admin/registration", {"enabled": False}, "PATCH")
        with patch.object(Users, "__init__", gated_init):
            with ThreadPoolExecutor(max_workers=2) as pool:
                registering = pool.submit(register)
                self.assertTrue(entered.wait(3))
                closing = pool.submit(close)
                time.sleep(0.1)
                try:
                    self.assertFalse(closing.done(), "Closing must serialize after the authorized native account transaction")
                finally:
                    release.set()
                self.assertEqual(registering.result().status_code, 302)
                self.assertEqual(closing.result().status_code, 200)
        with self.app.app_context():
            self.assertEqual(Users.query.filter_by(email="inflight@example.test").count(), 1)
            self.assertEqual(get_config("registration_visibility"), "private")

    def test_registration_database_collation_aliases_cannot_change_controlled_setting(self):
        from urllib.parse import quote
        with self.app.app_context():
            if db.engine.dialect.name != "mysql":
                self.skipTest("MariaDB native config key collation")
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/registration",{"enabled":False},"PATCH").status_code,200)
        with self.app.app_context():
            set_config("unrelated_registration_test", "unchanged")
        for alias in ("registration_visibility ", "régistration_visibility"):
            with self.app.app_context():
                # Establish the native database consequence rather than
                # assuming Python string equality matches the real dialect.
                self.assertEqual(Configs.query.filter_by(key=alias).one().key,"registration_visibility")
            for path,method,payload in (
                (f"/api/v1/configs/{quote(alias,safe='')}","PATCH",{"value":"public"}),
                (f"/api/v1/configs/{quote(alias,safe='')}","DELETE",{}),
                ("/api/v1/configs","PATCH",{alias:"public"}),
                ("/api/v1/configs","POST",{"key":alias,"value":"public"}),
                ("/api/v1/configs/unrelated_registration_test","PATCH",{"key":alias,"value":"public"}),
            ):
                response = self.post(self.admin,path,payload,method)
                self.assertEqual(response.status_code,409,response.get_data(as_text=True))
            legacy = self.admin.post("/admin/plugins/open_tech_quest",data={"nonce":"quest-test-csrf",alias:"public"})
            self.assertEqual(legacy.status_code,409,legacy.get_data(as_text=True))
            self.assertFalse(self.guest.get(PREFIX+"/bootstrap").json["data"]["registration"]["enabled"])
        with self.app.app_context():
            self.assertEqual(Configs.query.filter_by(key="registration_visibility").count(),1)
            self.assertEqual(Configs.query.filter_by(key="registration_visibility").one().value,"private")
            self.assertEqual(Configs.query.filter_by(key="unrelated_registration_test").one().value,"unchanged")
            self.assertEqual(QuestAudit.query.filter_by(action="registration.configure").count(),1)

    def test_attendance_defaults_off_and_registration_never_confirms_a_person(self):
        self.open_onboarding()
        before = self.alice.get(PREFIX+"/onboarding").json["data"]
        self.assertEqual(before["attendance"], {"enabled":False,"generation":0,"opened_at":None,"closed_at":None,"can_open":True,"reason":None,"late_only":False})
        self.assertFalse(before["can_ready"])
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/ready",{}).status_code,409)
        for index,enabled in enumerate((False,True)):
            if enabled:
                self.attendance_window(True)
            client = self.app.test_client()
            self.assertEqual(client.get("/register").status_code,200)
            with client.session_transaction() as session:
                nonce = session["nonce"]
            response = client.post("/register",data={"nonce":nonce,"name":f"Attendance signup {index}","email":f"attendance{index}@example.test","password":"test-only"})
            self.assertEqual(response.status_code,302,response.get_data(as_text=True))
            own = client.get(PREFIX+"/onboarding").json["data"]
            self.assertIsNone(own["ready_at"])
            self.assertEqual(own["can_ready"],enabled)
            self.assertEqual(own["attendance"]["enabled"],enabled)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(),0)
            self.assertEqual(QuestStart.query.count(),0)
            self.assertEqual(Solves.query.count(),0)
            self.assertIsNone(QuestEvent.query.get(1).start_at)

    def test_attendance_admin_strict_body_generations_audit_and_idempotence(self):
        self.open_onboarding()
        endpoint = PREFIX+"/admin/attendance"
        with self.guest.session_transaction() as session:
            session["nonce"]="quest-test-csrf"
        self.assertEqual(self.post(self.guest,endpoint,{"enabled":True},"PATCH").status_code,401)
        self.assertEqual(self.post(self.alice,endpoint,{"enabled":True},"PATCH").status_code,403)
        self.assertEqual(self.admin.patch(endpoint,json={"enabled":True}).status_code,403)
        for body in ({},{"enabled":1},{"enabled":"true"},{"enabled":None},{"enabled":True,"generation":99}):
            self.assertEqual(self.post(self.admin,endpoint,body,"PATCH").status_code,400)
        first = self.attendance_window(True)
        self.assertTrue(first["enabled"])
        self.assertEqual(first["generation"],1)
        self.assertIsNotNone(first["opened_at"])
        self.assertIsNone(first["closed_at"])
        self.assertEqual(self.attendance_window(True),first)
        closed = self.attendance_window(False)
        self.assertEqual(closed["generation"],1)
        self.assertEqual(closed["opened_at"],first["opened_at"])
        self.assertIsNotNone(closed["closed_at"])
        self.assertEqual(self.attendance_window(False),closed)
        second = self.attendance_window(True)
        self.assertEqual(second["generation"],2)
        self.assertIsNone(second["closed_at"])
        with self.app.app_context():
            records = QuestAudit.query.filter_by(action="attendance.configure").order_by(QuestAudit.id).all()
            self.assertEqual(len(records),3)
            self.assertEqual([r.detail["new"] for r in records],[True,False,True])
            self.assertTrue(all(r.actor_id == 1 and r.detail["source"] == "organizer" for r in records))
            self.assertEqual(QuestStart.query.count(),0)
        payload = self.admin.get(PREFIX+"/admin/state").json["data"]
        self.assertEqual(payload["onboarding"]["attendance"],second)
        self.assertEqual(payload["registration"],{"enabled":True})

    def test_attendance_two_devices_confirm_once_and_closed_retries_preserve_time(self):
        self.open_onboarding()
        self.attendance_window(True)
        def ready(uid):
            return self.post(self.client(uid),PREFIX+"/onboarding/ready",{})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(ready,(2,2)))
        self.assertEqual([r.status_code for r in responses],[200,200])
        timestamp = responses[0].json["data"]["ready_at"]
        self.assertEqual(responses[1].json["data"]["ready_at"],timestamp)
        self.attendance_window(False)
        self.assertEqual(self.ready_user(self.alice)["ready_at"],timestamp)
        self.assertEqual(self.ready_user(self.client(2))["ready_at"],timestamp)
        rejected = self.bob.post(PREFIX+"/onboarding/ready",json={},headers={"CSRF-Token":"quest-test-csrf","X-Quest-Attendance-UI":"1"})
        self.assertEqual(rejected.status_code,409)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(),1)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.ready").count(),1)
            self.assertEqual(QuestStart.query.count(),0)

    def test_attendance_account_switch_bootstrap_refreshes_csrf_without_weakening_checks(self):
        self.open_onboarding()
        self.attendance_window(True)
        browser = self.app.test_client()
        supported = {"Sec-Fetch-Dest":"empty", "X-Quest-Attendance-UI":"1"}
        guest = browser.get(PREFIX+"/bootstrap",headers=supported)
        self.assertEqual(guest.status_code,200)
        self.assertFalse(guest.json["data"]["viewer"]["authenticated"])
        self.assertIn("csrfNonce",guest.json["data"])
        self.assertTrue(guest.cache_control.private)
        self.assertTrue(guest.cache_control.no_store)
        def login(name):
            self.assertEqual(browser.get("/login").status_code,200)
            with browser.session_transaction() as session:
                nonce = session["nonce"]
            response = browser.post("/login",data={"nonce":nonce,"name":name,"password":"test-only"})
            self.assertEqual(response.status_code,302,response.get_data(as_text=True))
            return browser.get(PREFIX+"/bootstrap",headers=supported)
        alice = login("Alice")
        self.assertEqual(alice.json["data"]["viewer"]["id"],2)
        old_nonce = alice.json["data"]["csrfNonce"]
        self.assertIsInstance(old_nonce,str)
        self.assertEqual(browser.get("/logout").status_code,302)
        bob = login("Bob")
        self.assertEqual(bob.status_code,200)
        self.assertEqual(bob.json["data"]["viewer"]["id"],3)
        current_nonce = bob.json["data"]["csrfNonce"]
        self.assertIsInstance(current_nonce,str)
        self.assertNotEqual(current_nonce,old_nonce)
        with browser.session_transaction() as session:
            self.assertEqual(current_nonce,session["nonce"])
        self.assertTrue(bob.cache_control.private)
        self.assertTrue(bob.cache_control.no_store)
        denied = browser.post(PREFIX+"/onboarding/ready",json={},headers={"CSRF-Token":old_nonce})
        self.assertEqual(denied.status_code,403)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(),0)
        accepted = browser.post(PREFIX+"/onboarding/ready",json={},headers={"CSRF-Token":current_nonce})
        self.assertEqual(accepted.status_code,200,accepted.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.one().user_id,3)
            self.assertEqual(QuestStart.query.count(),0)

    def test_attendance_close_precedes_waiting_stale_confirmation(self):
        self.open_onboarding()
        self.attendance_window(True)
        from CTFd.plugins.open_tech_quest.onboarding import change_attendance
        entered,release = threading.Event(),threading.Event()
        def gate(e,enabled,source):
            if not enabled:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Attendance close gate was not released")
            return change_attendance(e,enabled,source)
        with patch("CTFd.plugins.open_tech_quest.onboarding.change_attendance",side_effect=gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                closing = pool.submit(lambda:self.post(self.client(1),PREFIX+"/admin/attendance",{"enabled":False},"PATCH"))
                self.assertTrue(entered.wait(3))
                confirming = pool.submit(lambda:self.post(self.client(2),PREFIX+"/onboarding/ready",{}))
                time.sleep(.1)
                try:
                    self.assertFalse(confirming.done())
                finally:
                    release.set()
                self.assertEqual(closing.result().status_code,200)
                self.assertEqual(confirming.result().status_code,409)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(),0)
            self.assertFalse(QuestOnboarding.query.get(1).attendance_open)

    def test_attendance_inflight_confirmation_finishes_before_close(self):
        self.open_onboarding()
        self.attendance_window(True)
        entered,release = threading.Event(),threading.Event()
        original_init = QuestReadiness.__init__
        def gate(row,*args,**kwargs):
            if kwargs.get("user_id") == 2:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Attendance ready gate was not released")
            original_init(row,*args,**kwargs)
        with patch.object(QuestReadiness,"__init__",gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                confirming = pool.submit(lambda:self.post(self.client(2),PREFIX+"/onboarding/ready",{}))
                self.assertTrue(entered.wait(3))
                closing = pool.submit(lambda:self.post(self.client(1),PREFIX+"/admin/attendance",{"enabled":False},"PATCH"))
                time.sleep(.1)
                try:
                    self.assertFalse(closing.done())
                finally:
                    release.set()
                confirmed,closed = confirming.result(),closing.result()
        self.assertEqual(confirmed.status_code,200)
        self.assertEqual(closed.status_code,200)
        self.assertEqual(self.ready_user(self.alice)["ready_at"],confirmed.json["data"]["ready_at"])
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.count(),1)
            self.assertFalse(QuestOnboarding.query.get(1).attendance_open)
            self.assertEqual(QuestStart.query.count(),0)

    def test_attendance_window_lifecycle_guards_and_generation_invalidate_preview(self):
        self.open_onboarding()
        self.attendance_window(True)
        for client in (self.alice,self.bob):
            self.ready_user(client)
        closed = self.onboarding_action("close_checkin")
        self.assertEqual(closed.status_code,200)
        self.assertFalse(closed.json["data"]["attendance"]["enabled"])
        self.assertEqual(self.onboarding_action("reopen_checkin").status_code,200)
        self.assertFalse(self.alice.get(PREFIX+"/onboarding").json["data"]["attendance"]["enabled"])
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan = self.attendance_plan()
        self.attendance_window(True)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":True}).status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":False,"fingerprint":plan["fingerprint"]}).status_code,409)
        self.attendance_window(False)
        stale = self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":False,"fingerprint":plan["fingerprint"]})
        self.assertEqual(stale.status_code,409)
        fresh = self.attendance_plan()
        self.assertNotEqual(fresh["fingerprint"],plan["fingerprint"])
        self.apply_attendance(fresh)
        self.attendance_window(True)
        self.assertEqual(self.onboarding_action("finalize").status_code,409)
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/name",{"name":"Window Foxes","confirm":True}).status_code,200)
        self.attendance_window(False)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)
        late = self.post(self.admin,PREFIX+"/admin/attendance",{"enabled":True},"PATCH")
        self.assertEqual(late.status_code,200)
        self.assertTrue(late.json["data"]["late_only"])
        self.assertTrue(self.admin.get(PREFIX+"/admin/state").json["data"]["onboarding"]["attendance"]["can_open"])
        start = self.post(self.admin,PREFIX+"/admin/event/action",{"action":"start"})
        self.assertEqual(start.status_code,409)
        self.assertIn("Close attendance",start.json["error"])
        self.attendance_window(False)
        self.action("start")
        self.assertTrue(self.attendance_window(True)["late_only"])
        self.action("close")
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/attendance",{"enabled":True},"PATCH").status_code,409)
        with self.app.app_context():
            sources = [r.detail["source"] for r in QuestAudit.query.filter_by(action="attendance.configure").all()]
            self.assertIn("close_checkin",sources)

    def test_attendance_legacy_browser_refresh_prompt_is_narrow_and_not_authorization(self):
        self.open_onboarding()
        old = {"Sec-Fetch-Dest":"empty"}
        supported = dict(old,**{"X-Quest-Attendance-UI":"1"})
        self.assertEqual(self.alice.get(PREFIX+"/bootstrap",headers=old).status_code,200)
        self.attendance_window(True)
        legacy = self.alice.get(PREFIX+"/bootstrap",headers=old)
        self.assertEqual(legacy.status_code,409)
        self.assertIn("Refresh",legacy.json["error"])
        self.assertEqual(self.alice.get(PREFIX+"/bootstrap",headers=supported).status_code,200)
        self.assertEqual(self.alice.get(PREFIX+"/bootstrap").status_code,200)
        self.assertEqual(self.alice.get(PREFIX+"/bootstrap",headers={"Sec-Fetch-Dest":"document"}).status_code,200)
        self.assertEqual(self.guest.get(PREFIX+"/bootstrap",headers=old).status_code,200)
        self.assertEqual(self.admin.get(PREFIX+"/bootstrap",headers=old).status_code,200)
        self.ready_user(self.alice)
        self.assertEqual(self.alice.get(PREFIX+"/bootstrap",headers=old).status_code,200)
        self.assertEqual(self.bob.get(PREFIX+"/bootstrap",headers=old).status_code,409)
        self.attendance_window(False)
        self.assertEqual(self.bob.get(PREFIX+"/bootstrap",headers=old).status_code,200)
        response = self.bob.post(PREFIX+"/onboarding/ready",json={},headers={"CSRF-Token":"quest-test-csrf","X-Quest-Attendance-UI":"1"})
        self.assertEqual(response.status_code,409)

    def test_attendance_upgrade_old_schema_preserves_ready_rosters_and_live_clocks(self):
        from sqlalchemy import MetaData, Table, inspect
        from CTFd.plugins.migrations import upgrade
        from CTFd.plugins.open_tech_quest.schema import upgrade_sqlite_attendance
        with self.app.app_context():
            stamp = datetime.utcnow()-timedelta(minutes=4)
            state = QuestOnboarding.query.get(1)
            state.phase,state.applied_at,state.finalized_at = "finalized",stamp,stamp+timedelta(seconds=1)
            e = QuestEvent.query.get(1)
            e.status,e.start_at = "live",stamp+timedelta(seconds=2)
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id,date=stamp+timedelta(seconds=10)))
            db.session.commit()
            old_values = {column.name:getattr(state,column.name) for column in QuestOnboarding.__table__.columns if not column.name.startswith("attendance_")}
            ready = [(r.user_id,r.ready_at,r.late) for r in QuestReadiness.query.order_by(QuestReadiness.user_id).all()]
            users = [(u.id,u.name,u.team_id) for u in Users.query.order_by(Users.id).all()]
            teams = [(t.id,t.name) for t in Teams.query.order_by(Teams.id).all()]
            db.session.remove()
            old_table = Table("quest_onboarding",MetaData(),*(column.copy() for column in QuestOnboarding.__table__.columns if not column.name.startswith("attendance_")))
            with db.engine.begin() as connection:
                QuestOnboarding.__table__.drop(bind=connection)
                old_table.create(bind=connection)
                connection.execute(old_table.insert().values(**old_values))
            self.assertNotIn("attendance_open",{column["name"] for column in inspect(db.engine).get_columns("quest_onboarding")})
            set_config("open_tech_quest_alembic_version","quest_0004")
            db.session.remove()
            sqlite = db.engine.dialect.name == "sqlite"
        def start_worker(_):
            with self.app.app_context():
                upgrade(plugin_name="open_tech_quest")
                upgrade_sqlite_attendance()
                return QuestOnboarding.query.get(1).attendance_generation
        if sqlite:
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(start_worker,(1,2))),[0,0])
        else:
            self.assertEqual(start_worker(1),0)
        with self.app.app_context():
            db.session.expire_all()
            state = QuestOnboarding.query.get(1)
            self.assertEqual({column.name:getattr(state,column.name) for column in QuestOnboarding.__table__.columns if not column.name.startswith("attendance_")},old_values)
            self.assertFalse(state.attendance_open)
            self.assertEqual(state.attendance_generation,0)
            self.assertIsNone(state.attendance_opened_at)
            self.assertIsNone(state.attendance_closed_at)
            self.assertEqual([(r.user_id,r.ready_at,r.late) for r in QuestReadiness.query.order_by(QuestReadiness.user_id).all()],ready)
            self.assertEqual([(u.id,u.name,u.team_id) for u in Users.query.order_by(Users.id).all()],users)
            self.assertEqual([(t.id,t.name) for t in Teams.query.order_by(Teams.id).all()],teams)
            self.assertEqual(QuestEvent.query.get(1).start_at,stamp+timedelta(seconds=2))
            self.assertEqual(Solves.query.one().date,stamp+timedelta(seconds=10))
            if not sqlite:
                columns = {column["name"]:column for column in inspect(db.engine).get_columns("quest_onboarding")}
                self.assertEqual(columns["attendance_opened_at"]["type"].fsp,6)
                self.assertEqual(columns["attendance_closed_at"]["type"].fsp,6)

    def test_attendance_fresh_latest_metadata_migration_is_idempotent(self):
        from importlib import import_module
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        from CTFd.plugins.open_tech_quest.schema import upgrade_sqlite_attendance
        self.open_onboarding()
        first = self.attendance_window(True)
        timestamp = self.ready_user(self.alice)["ready_at"]
        migration = import_module("CTFd.plugins.open_tech_quest.migrations.0005_attendance")
        with self.app.app_context():
            self.assertTrue({"attendance_open","attendance_generation","attendance_opened_at","attendance_closed_at"} <= {column["name"] for column in inspect(db.engine).get_columns("quest_onboarding")})
            db.session.remove()
            if db.engine.dialect.name == "sqlite":
                upgrade_sqlite_attendance()
                upgrade_sqlite_attendance()
            else:
                with db.engine.begin() as connection:
                    migration.upgrade(Operations(MigrationContext.configure(connection)))
                    migration.upgrade(Operations(MigrationContext.configure(connection)))
        self.assertEqual(self.alice.get(PREFIX+"/onboarding").json["data"]["attendance"],first)
        self.assertEqual(self.alice.get(PREFIX+"/onboarding").json["data"]["ready_at"],timestamp)

    def grant_role(self, uid=2, role="maintainer"):
        return self.post(self.admin,PREFIX+f"/admin/users/{uid}/role",{"role":role,"confirm":True,"identity":self.account_identity(uid)},"PATCH")

    def account_identity(self, uid):
        rows = self.admin.get(PREFIX+"/admin/users").json["data"]["users"]
        return next((row["identity"] for row in rows if row["id"] == uid),"missing")

    def fixture_external_chain(self, slug, ids):
        # Simulate legacy/external edits for defensive demo ownership tests.
        # Current HTTP management intentionally forbids changing active demos.
        with self.app.app_context():
            room = QuestRoom.query.filter_by(slug=slug).one()
            for step in QuestStep.query.filter_by(room_id=room.id).all():
                challenge = Challenges.query.get(step.challenge_id)
                challenge.requirements,challenge.next_id = None,None
                db.session.delete(step)
            db.session.flush()
            for position,cid in enumerate(ids):
                challenge = Challenges.query.get(cid)
                challenge.requirements = {"prerequisites":ids[:position],"anonymize":False}
                challenge.next_id = ids[position+1] if position+1<len(ids) else None
                challenge.position = position
                db.session.add(QuestStep(room_id=room.id,challenge_id=cid,position=position))
            db.session.commit()

    def test_maintainer_admin_only_strict_roles_and_safe_room_overview(self):
        self.open_onboarding()
        endpoint = PREFIX+"/admin/users/2/role"
        self.assertEqual(self.guest.get(PREFIX+"/admin/users").status_code,401)
        self.assertEqual(self.alice.get(PREFIX+"/admin/users").status_code,403)
        self.assertEqual(self.post(self.alice,endpoint,{"role":"maintainer","confirm":True},"PATCH").status_code,403)
        self.assertEqual(self.admin.patch(endpoint,json={"role":"maintainer","confirm":True}).status_code,403)
        for body in ({"role":"admin","confirm":True},{"role":"maintainer"},{"role":"maintainer","confirm":1},{"role":"maintainer","confirm":True,"hidden":False}):
            self.assertEqual(self.post(self.admin,endpoint,dict(body,identity=self.account_identity(2)),"PATCH").status_code,400)
        self.assertEqual(self.grant_role(1).status_code,409)
        self.assertEqual(self.grant_role(999).status_code,404)
        self.assertEqual(self.grant_role().status_code,200)
        viewer = self.alice.get(PREFIX+"/bootstrap").json["data"]["viewer"]
        self.assertEqual(viewer["role"],"maintainer")
        self.assertTrue(viewer["can_manage_rooms"])
        self.assertFalse(viewer["can_manage_users"])
        self.assertFalse(viewer["admin"])
        overview = self.alice.get(PREFIX+"/manage/rooms")
        self.assertEqual(overview.status_code,200)
        self.assertEqual(set(overview.json["data"]),{"event","rooms","available_challenges","revision"})
        self.assertRegex(overview.json["data"]["revision"], r"^[0-9a-f]{64}$")
        serialized = overview.get_data(as_text=True)
        for private in ("@example.test","fixture-answer","Private instructions","participants","audit"):
            self.assertNotIn(private,serialized)
        self.assertEqual(overview.json["data"]["rooms"][0]["challenge_ids"],[1,2])
        for path in ("/admin/state","/admin/users","/admin/export"):
            self.assertEqual(self.alice.get(PREFIX+path).status_code,403)
        self.assertNotEqual(self.alice.get("/admin").status_code,200)
        self.assertEqual(self.alice.get("/api/v1/users?view=admin&field=email&q=example").status_code,400)
        for path,body,method in (("/admin/registration",{"enabled":False},"PATCH"),("/admin/attendance",{"enabled":True},"PATCH"),("/admin/event/action",{"action":"start"},"POST"),("/admin/projector",{"view":"overall"},"PATCH"),("/admin/teams/allocate",{"preview":True},"POST"),("/admin/users/3/role",{"role":"maintainer","confirm":True},"PATCH")):
            self.assertEqual(self.post(self.alice,PREFIX+path,body,method).status_code,403,path)
        self.assertEqual(self.post(self.alice,PREFIX+"/admin/rooms/openshift",{"subtitle":"Maintainer edit"},"PATCH").status_code,200)
        self.assertEqual(self.post(self.alice,"/api/v1/challenges/1",{"description":"Native forbidden"},"PATCH").status_code,403)
        users = self.admin.get(PREFIX+"/admin/users").json["data"]["users"]
        self.assertEqual(len(users),4)
        self.assertEqual(users[0]["role"],"administrator")
        self.assertFalse(users[0]["can_change_role"])
        self.assertEqual(users[1]["role"],"maintainer")
        self.assertTrue(users[1]["can_change_role"])

    def test_maintainer_transitions_preserve_team_hidden_values_and_idempotence(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        self.ready_user(self.bob)
        with self.app.app_context():
            team = Teams.query.get(self.team_id)
            team.captain_id = 2
            db.session.commit()
        changed = self.grant_role()
        self.assertEqual(changed.status_code,200,changed.get_data(as_text=True))
        self.assertIsNone(changed.json["data"]["team_id"])
        self.assertIsNone(changed.json["data"]["ready_at"])
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).type,"user")
            self.assertTrue(Users.query.get(2).hidden)
            self.assertFalse(QuestMaintainer.query.get(2).previous_hidden)
            self.assertEqual(Users.query.get(3).team_id,self.team_id)
            self.assertEqual(Teams.query.get(self.team_id).captain_id,3)
            self.assertIsNotNone(QuestReadiness.query.get(3))
            granted_at = QuestMaintainer.query.get(2).granted_at
        self.assertEqual(self.grant_role().status_code,200)
        with self.app.app_context():
            self.assertEqual(QuestMaintainer.query.get(2).granted_at,granted_at)
            self.assertEqual(QuestAudit.query.filter_by(action="user.role").count(),1)
        self.assertNotIn(2,[u["id"] for u in self.guest.get("/api/v1/users").json["data"]])
        self.assertEqual(self.grant_role(role="participant").status_code,200)
        with self.app.app_context():
            self.assertFalse(Users.query.get(2).hidden)
            self.assertIsNone(Users.query.get(2).team_id)
            self.assertIsNone(QuestReadiness.query.get(2))
            self.assertEqual(QuestAudit.query.filter_by(action="user.role").count(),2)
        self.ready_user(self.alice)
        with self.app.app_context():
            ready_at = QuestReadiness.query.get(2).ready_at
        self.assertEqual(self.grant_role(role="participant").status_code,200)
        with self.app.app_context():
            self.assertEqual(QuestReadiness.query.get(2).ready_at,ready_at)
            user = Users.query.get(4)
            user.hidden = True
            db.session.commit()
        self.assertEqual(self.grant_role(4).status_code,200)
        self.assertEqual(self.grant_role(4,"participant").status_code,200)
        with self.app.app_context():
            self.assertTrue(Users.query.get(4).hidden)

    def test_maintainer_promotion_activity_and_optimized_roster_restrictions(self):
        self.open_onboarding()
        with self.app.app_context():
            db.session.add(QuestStart(team_id=self.team_id,room_id=QuestRoom.query.filter_by(slug="openshift").one().id))
            db.session.commit()
        rejected = self.grant_role()
        self.assertEqual(rejected.status_code,409)
        self.assertIn("activity",rejected.json["error"])
        with self.app.app_context():
            QuestStart.query.delete()
            QuestOnboarding.query.get(1).phase = "naming"
            db.session.commit()
        self.assertEqual(self.grant_role().status_code,409)
        with self.app.app_context():
            Users.query.get(2).team_id = None
            QuestEvent.query.get(1).status = "live"
            db.session.commit()
        self.assertEqual(self.grant_role().status_code,200)
        self.assertEqual(self.grant_role(role="participant").status_code,200)
        with self.app.app_context():
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id))
            db.session.commit()
        self.assertEqual(self.grant_role().status_code,409)
        with self.app.app_context():
            self.assertEqual(Solves.query.count(),1)
            self.assertIsNone(QuestMaintainer.query.get(2))

    def test_maintainer_staff_excluded_from_readiness_allocation_and_stale_plans(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        self.ready_user(self.bob)
        self.ready_user(self.carol)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        stale = self.attendance_plan()
        self.assertEqual(self.grant_role().status_code,200)
        self.assertEqual(self.alice.get(PREFIX+"/onboarding").status_code,403)
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/ready",{}).status_code,403)
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/name",{"name":"Staff","confirm":True}).status_code,403)
        self.assertEqual(self.alice.get(PREFIX+"/bootstrap",headers={"Sec-Fetch-Dest":"empty"}).status_code,200)
        state = self.admin.get(PREFIX+"/admin/state").json["data"]
        self.assertNotIn(2,[u["id"] for u in state["participants"]])
        self.assertEqual(state["onboarding"]["ready_count"],2)
        self.assertEqual(state["onboarding"]["pending_count"],0)
        apply = self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":False,"fingerprint":stale["fingerprint"]})
        self.assertEqual(apply.status_code,409)
        plan = self.attendance_plan()
        self.assertNotIn(2,[uid for group in plan["groups"] for uid in group["user_ids"]])
        self.assertNotIn(2,plan["unassigned_user_ids"])
        self.apply_attendance(plan)
        self.assertEqual(self.post(self.bob,PREFIX+"/onboarding/name",{"name":"Real participants","confirm":True}).status_code,200)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)
        self.action("start")
        self.assertEqual(self.post(self.alice,PREFIX+"/rooms/openshift/start",{}).status_code,403)
        self.assertEqual(self.solve(self.alice,1).status_code,403)
        self.assertEqual(self.post(self.alice,"/api/v1/unlocks",{"type":"hints","target":1}).status_code,403)
        self.assertEqual(self.grant_role(role="participant").status_code,200)
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/ready",{}).status_code,409)

    def test_maintainer_native_staff_assignment_visibility_and_banned_permissions(self):
        self.open_onboarding()
        self.assertEqual(self.grant_role().status_code,200)
        for body in ({"team_id":self.team_id},{"hidden":False}):
            response = self.post(self.admin,"/api/v1/users/2",body,"PATCH")
            self.assertEqual(response.status_code,409,response.get_data(as_text=True))
        self.assertEqual(self.post(self.admin,PREFIX+f"/admin/teams/{self.team_id}",{"user_ids":[2,3]},"PATCH").status_code,400)
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{"banned":True},"PATCH").status_code,200)
        from CTFd.plugins.open_tech_quest.roles import can_manage_rooms
        with self.app.app_context():
            self.assertFalse(can_manage_rooms(Users.query.get(2)))
        # Native CTFd may redirect a banned account before plugin authorization.
        self.assertIn(self.alice.get(PREFIX+"/manage/rooms").status_code,(302,403))
        self.assertIn(self.post(self.alice,PREFIX+"/admin/rooms/openshift",{"name":"Forbidden"},"PATCH").status_code,(302,403))
        self.assertEqual(self.grant_role(role="participant").status_code,200)

    def test_maintainer_revocation_applies_to_open_sessions_and_waiting_room_writes(self):
        self.open_onboarding()
        self.assertEqual(self.grant_role().status_code,200)
        self.assertEqual(self.alice.get(PREFIX+"/manage/rooms").status_code,200)
        from CTFd.plugins.open_tech_quest import authoring as authoring_module
        original = authoring_module.event
        entered,release = threading.Event(),threading.Event()
        def gate(lock=False):
            from flask import request
            if lock and request.path.endswith("/admin/rooms/openshift"):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Room role gate not released")
            return original(lock=lock)
        with patch.object(authoring_module,"event",side_effect=gate):
            with ThreadPoolExecutor(max_workers=1) as pool:
                editing = pool.submit(lambda:self.post(self.client(2),PREFIX+"/admin/rooms/openshift",{"subtitle":"Stale privileged tab"},"PATCH"))
                try:
                    self.assertTrue(entered.wait(3))
                    self.assertEqual(self.grant_role(role="participant").status_code,200)
                finally:
                    release.set()
                self.assertEqual(editing.result().status_code,403)
        self.assertEqual(self.alice.get(PREFIX+"/manage/rooms").status_code,403)
        with self.app.app_context():
            self.assertNotEqual(QuestRoom.query.filter_by(slug="openshift").one().subtitle,"Stale privileged tab")

    def test_maintainer_grants_upgrade_and_native_user_delete_cascade(self):
        from CTFd.plugins.migrations import upgrade
        from sqlalchemy import inspect
        with self.app.app_context():
            db.session.remove()
            QuestMaintainer.__table__.drop(bind=db.engine)
            set_config("open_tech_quest_alembic_version","quest_0005")
            before = [(u.id,u.name,u.type,u.team_id) for u in Users.query.order_by(Users.id).all()]
            upgrade(plugin_name="open_tech_quest")
            self.assertIn("quest_maintainers",inspect(db.engine).get_table_names())
            self.assertEqual(QuestMaintainer.query.count(),0)
            self.assertEqual([(u.id,u.name,u.type,u.team_id) for u in Users.query.order_by(Users.id).all()],before)
        self.open_onboarding()
        self.assertEqual(self.grant_role().status_code,200)
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{},"DELETE").status_code,409)
        response = self.post(self.admin,PREFIX+"/admin/users/2",{"confirm":True,"identity":self.account_identity(2)},"DELETE")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(QuestMaintainer.query.get(2))
            self.assertIsNone(Users.query.get(2))

    def test_dynamic_rooms_crud_security_native_content_preservation_and_zero_rooms(self):
        self.open_onboarding()
        self.assertEqual(self.grant_role().status_code,200)
        endpoint = PREFIX+"/manage/rooms"
        self.assertEqual(self.post(self.bob,endpoint,{"slug":"new","name":"No"}).status_code,403)
        self.assertEqual(self.alice.post(endpoint,json={"slug":"new","name":"No"}).status_code,403)
        for body in ({"slug":"Upper","name":"No"},{"slug":"bad/slug","name":"No"},{"slug":"new","name":""},{"slug":"new","name":"No","unknown":1},{"slug":"new","name":"No","theme":{"logo_url":"https://example.test/x"}}):
            self.assertEqual(self.post(self.alice,endpoint,body).status_code,400)
        created = self.post(self.alice,endpoint,{"slug":"containers-advanced","name":"Advanced containers","description":"## Team task"})
        self.assertEqual(created.status_code,200,created.get_data(as_text=True))
        room = created.json["data"]
        self.assertEqual(room["theme"],{"layout":"generic","accent":"#00a6b2","logo_url":None,"hero_url":None})
        self.assertEqual(room["position"],5)
        self.assertEqual(self.post(self.alice,endpoint,{"slug":"containers-advanced","name":"Duplicate"}).status_code,409)
        self.assertEqual(self.post(self.alice,PREFIX+"/admin/rooms/containers-advanced",{"name":"Renamed custom room","theme":{"layout":"generic"},"challenge_ids":[3]},"PATCH").status_code,200)
        with self.app.app_context():
            before = [(c.id,c.name,c.description,c.value) for c in Challenges.query.order_by(Challenges.id).all()]
            flags = [(f.challenge_id,f.content) for f in Flags.query.order_by(Flags.id).all()]
            QuestEvent.query.get(1).projector={"view":"room","room_slug":"containers-advanced"}
            db.session.commit()
        self.assertEqual(self.post(self.alice,endpoint+"/containers-advanced",{"confirm":False},"DELETE").status_code,400)
        self.assertEqual(self.post(self.alice,endpoint+"/containers-advanced",{"confirm":True},"DELETE").status_code,200)
        with self.app.app_context():
            self.assertEqual([(c.id,c.name,c.description,c.value) for c in Challenges.query.order_by(Challenges.id).all()],before)
            self.assertEqual([(f.challenge_id,f.content) for f in Flags.query.order_by(Flags.id).all()],flags)
            self.assertIsNone(Challenges.query.get(3).requirements)
            self.assertIsNone(Challenges.query.get(3).next_id)
            self.assertIsNone(QuestStep.query.filter_by(challenge_id=3).first())
            self.assertEqual(QuestEvent.query.get(1).projector,{"view":"overview","room_slug":None})
        for slug in ("openshift","ansible","rhel","security","ai"):
            self.assertEqual(self.post(self.alice,endpoint+"/"+slug,{"confirm":True},"DELETE").status_code,200)
        self.assertEqual(self.alice.get(endpoint).json["data"]["rooms"],[])
        self.assertEqual(self.guest.get(PREFIX+"/bootstrap").json["data"]["rooms"],[])
        with self.app.app_context():
            self.assertEqual(Challenges.query.count(),3)
            self.assertEqual(QuestRoom.query.count(),0)
        self.assertEqual(self.post(self.alice,endpoint,{"slug":"new-topic","name":"A new beginning"}).status_code,200)

    def test_dynamic_rooms_lifecycle_activity_and_demo_deletion_guards(self):
        from CTFd.plugins.open_tech_quest.models import QuestCorrection
        with self.app.app_context():
            room = QuestRoom.query.filter_by(slug="openshift").one()
            db.session.add(QuestCorrection(team_id=self.team_id,room_id=room.id,adjustment_ms=1,reason="Preserve history"))
            db.session.commit()
        endpoint = PREFIX+"/manage/rooms/openshift"
        refused = self.post(self.admin,endpoint,{"confirm":True},"DELETE")
        self.assertEqual(refused.status_code,409)
        with self.app.app_context():
            self.assertIsNotNone(QuestRoom.query.filter_by(slug="openshift").first())
            QuestCorrection.query.delete()
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id))
            db.session.commit()
        self.assertEqual(self.post(self.admin,endpoint,{"confirm":True},"DELETE").status_code,409)
        with self.app.app_context():
            Submissions.query.delete()
            db.session.commit()
        self.configure([])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/demo",{"action":"load"}).status_code,200)
        self.assertEqual(self.post(self.admin,endpoint,{"confirm":True},"DELETE").status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/demo",{"action":"unload"}).status_code,200)
        self.configure([1,2])
        self.action("start")
        self.assertEqual(self.post(self.admin,PREFIX+"/manage/rooms",{"slug":"late-room","name":"Too late"}).status_code,409)
        self.assertEqual(self.post(self.admin,endpoint,{"confirm":True},"DELETE").status_code,409)

    def test_maintainer_initial_allocation_and_awards_exclude_staff(self):
        from CTFd.plugins.open_tech_quest.service import now
        from CTFd.plugins.open_tech_quest.onboarding import eligible
        self.open_onboarding()
        self.assertEqual(self.grant_role().status_code,200)
        with self.app.app_context():
            db.session.add(Users(id=5,name="New participant",email="new@example.test",password="test-only"))
            db.session.add(QuestReadiness(user_id=5))
            db.session.commit()
        plan = self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).json["data"]
        self.assertEqual([uid for group in plan["groups"] for uid in group["user_ids"]],[5])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":False,"groups":plan["groups"],"fingerprint":plan["fingerprint"]}).status_code,200)
        with self.app.app_context():
            state = QuestOnboarding.query.get(1)
            state.phase,state.grandfathered = "finalized",True
            for user in Users.query.all():
                if user.team_id is not None and eligible(user):
                    if QuestReadiness.query.get(user.id) is None:
                        db.session.add(QuestReadiness(user_id=user.id))
            for team in Teams.query.all():
                lock = QuestTeamName.query.get(team.id)
                if lock is None:
                    db.session.add(QuestTeamName(team_id=team.id,name_locked=True))
                else:
                    lock.name_locked = True
            db.session.commit()
            self.assertIsNone(QuestReadiness.query.get(2))
        self.action("start")
        self.start(self.bob)
        with self.app.app_context():
            # Imported/native-admin history must not grant staff a prize.
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id,date=now()))
            db.session.commit()
        ranked = self.guest.get(PREFIX+"/standings").json["data"]
        self.assertEqual(next(t for t in ranked["teams"] if t["id"]==self.team_id)["total"],0)
        with self.app.app_context():
            self.assertEqual(Solves.query.count(),1)

    def test_maintainer_role_write_rechecks_demoted_or_banned_actor_after_waiting(self):
        from CTFd.plugins.open_tech_quest import api as api_module
        from flask import request
        self.open_onboarding()
        self.attendance_window(True)
        timestamp = self.ready_user(self.carol)["ready_at"]
        target_identity = self.account_identity(4)
        original = api_module.event
        for uid,mutation in ((5,{"type":"user"}),(6,{"banned":True})):
            with self.subTest(mutation=mutation):
                with self.app.app_context():
                    db.session.add(Users(id=uid,name=f"Temporary admin {uid}",email=f"admin{uid}@example.test",type="admin",password="test-only"))
                    db.session.commit()
                entered,release = threading.Event(),threading.Event()
                def gate(lock=False):
                    if lock and request.path == PREFIX+"/admin/users/4/role":
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError("Queued role grant gate not released")
                    return original(lock=lock)
                with patch.object(api_module,"event",side_effect=gate):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        waiting = pool.submit(lambda:self.post(self.client(uid),PREFIX+"/admin/users/4/role",{"role":"maintainer","confirm":True,"identity":target_identity},"PATCH"))
                        try:
                            self.assertTrue(entered.wait(3))
                            changed = self.post(self.admin,f"/api/v1/users/{uid}",mutation,"PATCH")
                            self.assertEqual(changed.status_code,200,changed.get_data(as_text=True))
                            with self.app.app_context():
                                actor = db.session.query(Users.type,Users.banned).filter(Users.id==uid).one()
                                self.assertTrue(actor.type != "admin" or actor.banned)
                        finally:
                            release.set()
                        response = waiting.result()
                self.assertEqual(response.status_code,403,response.get_data(as_text=True))
                self.assertEqual(response.json["error"],"Administrator access required")
                with self.app.app_context():
                    self.assertIsNone(QuestMaintainer.query.get(4))
                    self.assertEqual(Users.query.get(4).team_id,self.other_id)
                    self.assertFalse(Users.query.get(4).hidden)
                    self.assertFalse(any(a.detail.get("user_id")==4 for a in QuestAudit.query.filter_by(action="user.role").all()))
                self.assertEqual(self.carol.get(PREFIX+"/onboarding").json["data"]["ready_at"],timestamp)

    def managed_account(self, uid):
        response = self.admin.get(PREFIX+"/admin/users")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return next(row for row in response.json["data"]["users"] if row["id"] == uid)

    def account_action(self, uid, action, identity=None, **fields):
        if identity is None:
            identity = self.account_identity(uid)
        data = dict(fields,identity=identity,confirm=True)
        path = PREFIX+f"/admin/users/{uid}"+("/access" if action=="access" else "")
        return self.post(self.admin,path,data,"PATCH" if action=="access" else "DELETE")

    def test_lifecycle_controls_permissions_identity_validation_and_private_metadata(self):
        self.open_onboarding()
        row = self.managed_account(2)
        self.assertIsInstance(row["identity"],str)
        self.assertTrue(row["can_delete"])
        self.assertTrue(row["can_set_active"])
        self.assertIsNone(row["delete_reason"])
        response = self.admin.get(PREFIX+"/admin/users")
        self.assertTrue(response.cache_control.private)
        self.assertTrue(response.cache_control.no_store)
        admin = self.managed_account(1)
        self.assertFalse(admin["can_delete"])
        self.assertFalse(admin["can_set_active"])
        for suffix,method,fields in (("","DELETE",{}),("/access","PATCH",{"active":False}),("/role","PATCH",{"role":"maintainer"})):
            path = PREFIX+"/admin/users/2"+suffix
            valid = dict(fields,confirm=True,identity=row["identity"])
            self.assertEqual(self.alice.open(path,method=method,json=valid).status_code,403)
            self.assertEqual(self.post(self.alice,path,valid,method).status_code,403)
            self.assertEqual(self.admin.open(path,method=method,json=valid).status_code,403)
            missing = self.post(self.admin,path,dict(fields,confirm=True),method)
            self.assertEqual(missing.status_code,409)
            self.assertIn("Refresh",missing.json["error"])
            self.assertTrue(missing.cache_control.no_store)
            for changed in ({"confirm":1},{"confirm":False},{"unknown":True}):
                invalid = dict(valid,**changed)
                self.assertEqual(self.post(self.admin,path,invalid,method).status_code,400)
        for active in (1,"false",None):
            self.assertEqual(self.post(self.admin,PREFIX+"/admin/users/2/access",{"active":active,"confirm":True,"identity":row["identity"]},"PATCH").status_code,400)
        self.assertEqual(self.account_action(2,"access",identity="é"*64,active=False).status_code,409)
        self.assertEqual(self.account_action(1,"access",active=False).status_code,409)
        self.assertEqual(self.account_action(1,"delete").status_code,409)

    def test_lifecycle_unused_delete_repairs_captain_cleans_credentials_and_preserves_authored_content(self):
        from CTFd.models import ChallengeComments, Tracking, UserTokens
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        with self.app.app_context():
            Teams.query.get(self.team_id).captain_id=2
            db.session.add(UserTokens(user_id=2,value="disposable-delete-token"))
            db.session.add(Tracking(user_id=2,ip="127.0.0.1"))
            db.session.add(ChallengeComments(author_id=2,challenge_id=1,content="Preserve this authored note"))
            db.session.add(QuestAudit(actor_id=2,action="room.configure",detail={"room_slug":"openshift"}))
            db.session.commit()
            before = [(c.id,c.description) for c in Challenges.query.order_by(Challenges.id).all()]
            flags = [(f.challenge_id,f.content) for f in Flags.query.order_by(Flags.id).all()]
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{},"DELETE").status_code,409)
        response = self.account_action(2,"delete")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertEqual(response.json["data"],{"id":2,"deleted":True})
        self.assertTrue(response.cache_control.no_store)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(2))
            self.assertIsNone(QuestReadiness.query.get(2))
            self.assertEqual(UserTokens.query.filter_by(user_id=2).count(),0)
            self.assertEqual(Tracking.query.filter_by(user_id=2).count(),0)
            self.assertIsNotNone(Teams.query.get(self.team_id))
            self.assertEqual(Teams.query.get(self.team_id).captain_id,3)
            self.assertEqual(Users.query.get(3).team_id,self.team_id)
            self.assertEqual([(c.id,c.description) for c in Challenges.query.order_by(Challenges.id).all()],before)
            self.assertEqual([(f.challenge_id,f.content) for f in Flags.query.order_by(Flags.id).all()],flags)
            self.assertIsNone(ChallengeComments.query.one().author_id)
            self.assertEqual(ChallengeComments.query.one().content,"Preserve this authored note")
            self.assertIsNone(QuestAudit.query.filter_by(action="room.configure",actor_id=None).first().actor_id)
            self.assertEqual(QuestAudit.query.filter_by(action="user.delete").count(),1)
        self.assertNotEqual(self.alice.get(PREFIX+"/bootstrap").status_code,500)

    def test_lifecycle_stale_incarnation_rejects_delete_access_role_and_allocation(self):
        self.open_onboarding()
        with self.app.app_context():
            db.session.add(Users(id=5,name="Replaceable",email="replaceable@example.test",password="old-password"))
            db.session.add(QuestReadiness(user_id=5))
            db.session.commit()
        old = self.managed_account(5)
        plan = self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).json["data"]
        self.assertEqual(self.account_action(5,"delete").status_code,200)
        with self.app.app_context():
            db.session.add(Users(id=5,name="Replaceable",email="replaceable@example.test",password="new-password"))
            db.session.add(QuestReadiness(user_id=5))
            db.session.commit()
        new = self.managed_account(5)
        self.assertNotEqual(new["identity"],old["identity"])
        for response in (self.account_action(5,"delete",old["identity"]),self.account_action(5,"access",old["identity"],active=False),self.post(self.admin,PREFIX+"/admin/users/5/role",{"role":"maintainer","confirm":True,"identity":old["identity"]},"PATCH")):
            self.assertEqual(response.status_code,409,response.get_data(as_text=True))
        stale = self.post(self.admin,PREFIX+"/admin/teams/allocate",dict(plan,preview=False))
        self.assertEqual(stale.status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":False,"groups":plan["groups"]}).status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",dict(plan,preview=False,fingerprint="é"*64)).status_code,409)
        with self.app.app_context():
            self.assertFalse(Users.query.get(5).banned)
            self.assertIsNone(Users.query.get(5).team_id)
            self.assertIsNone(QuestMaintainer.query.get(5))
        fresh = self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).json["data"]
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",dict(fresh,preview=False)).status_code,200)

    def test_lifecycle_unused_access_demote_and_reactivate_follow_current_attendance_phase(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        with self.app.app_context():
            Teams.query.get(self.team_id).captain_id=2
            db.session.commit()
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        with self.app.app_context():
            user = Users.query.get(2)
            self.assertTrue(user.banned)
            self.assertIsNone(user.team_id)
            self.assertIsNone(QuestReadiness.query.get(2))
            self.assertEqual(Teams.query.get(self.team_id).captain_id,3)
            count = QuestAudit.query.filter_by(action="user.access").count()
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.filter_by(action="user.access").count(),count)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        self.assertIsNone(self.alice.get(PREFIX+"/onboarding").json["data"]["ready_at"])
        self.assertEqual(self.grant_role().status_code,200)
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        with self.app.app_context():
            self.assertIsNotNone(QuestMaintainer.query.get(2))
            self.assertTrue(Users.query.get(2).hidden)
        self.assertEqual(self.grant_role(role="participant").status_code,200)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        self.assertTrue(self.alice.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        self.ready_user(self.alice)
        self.assertEqual(self.grant_role().status_code,200)
        self.assertEqual(self.grant_role(role="participant").status_code,200)
        with self.app.app_context():
            user=Users.query.get(2)
            self.assertIsNone(user.team_id)
            self.assertIsNone(QuestReadiness.query.get(2))
            self.assertFalse(user.hidden)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/ready",{}).status_code,409)

    def test_lifecycle_activity_delete_refusal_and_access_preserve_teammate_score_and_clocks(self):
        self.action("start")
        self.start(self.alice)
        self.assertEqual(self.solve(self.bob,1).json["data"]["status"],"correct")
        before = self.guest.get(PREFIX+"/standings").json["data"]
        team_before = next(t for t in before["teams"] if t["id"]==self.team_id)
        row = self.managed_account(2)
        self.assertFalse(row["can_delete"])
        refusal = self.account_action(2,"delete")
        self.assertEqual(refusal.status_code,409)
        self.assertEqual(refusal.json["error"],row["delete_reason"])
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{},"DELETE").status_code,409)
        with self.app.app_context():
            starts = [(s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()]
            ready = QuestReadiness.query.get(2).ready_at
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        self.assertIn(self.solve(self.alice,2).status_code,(302,403))
        self.assertEqual(self.solve(self.bob,2).json["data"]["status"],"correct")
        after = self.guest.get(PREFIX+"/standings").json["data"]
        team_after = next(t for t in after["teams"] if t["id"]==self.team_id)
        self.assertEqual(team_before["total"],1)
        self.assertEqual(team_after["total"],2)
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).team_id,self.team_id)
            self.assertEqual(QuestReadiness.query.get(2).ready_at,ready)
            self.assertEqual([(s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()],starts)
            self.assertEqual(Solves.query.count(),2)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        self.assertEqual(self.alice.get(PREFIX+"/onboarding").json["data"]["ready_at"],ready.isoformat(timespec="milliseconds")+"Z")

    def test_lifecycle_frozen_rosters_and_naming_history_never_hard_deleted(self):
        row=self.managed_account(2)
        self.assertFalse(row["can_delete"])
        self.assertEqual(self.account_action(2,"delete").status_code,409)
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).team_id,self.team_id)
            self.assertIsNotNone(QuestReadiness.query.get(2))
            Users.query.get(4).team_id=None
            QuestTeamName.query.get(self.team_id).claimed_by=4
            db.session.commit()
        self.assertEqual(self.account_action(4,"delete").status_code,409)
        with self.app.app_context():
            self.assertEqual(QuestTeamName.query.get(self.team_id).claimed_by,4)

    def test_lifecycle_native_admin_type_access_safety_and_failed_validation_rollback(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        for body in ({"type":"user"},{"banned":True},{"banned":"true"}):
            response=self.post(self.admin,"/api/v1/users/1",body,"PATCH")
            self.assertEqual(response.status_code,409,response.get_data(as_text=True))
        self.assertEqual(self.post(self.admin,"/api/v1/users/1",{},"DELETE").status_code,409)
        # A schema error must roll back access/readiness/captain/audit staging.
        with self.app.app_context():
            before_ready=QuestReadiness.query.get(2).ready_at
        invalid=self.post(self.admin,"/api/v1/users/2",{"banned":True,"email":"invalid-email"},"PATCH")
        self.assertEqual(invalid.status_code,400,invalid.get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(Users.query.get(2).banned)
            self.assertEqual(Users.query.get(2).team_id,self.team_id)
            self.assertEqual(QuestReadiness.query.get(2).ready_at,before_ready)
            self.assertEqual(QuestAudit.query.filter_by(action="user.access").count(),0)
        self.assertEqual(self.grant_role().status_code,200)
        invalid=self.post(self.admin,"/api/v1/users/2",{"type":"admin","email":"invalid-email"},"PATCH")
        self.assertEqual(invalid.status_code,400,invalid.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).type,"user")
            self.assertTrue(Users.query.get(2).hidden)
            self.assertIsNotNone(QuestMaintainer.query.get(2))
            role_audits=QuestAudit.query.filter_by(action="user.role").count()
        promoted=self.post(self.admin,"/api/v1/users/2",{"type":"admin"},"PATCH")
        self.assertEqual(promoted.status_code,200,promoted.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Users.query.get(2).type,"admin")
            self.assertIsNone(QuestMaintainer.query.get(2))
            self.assertFalse(Users.query.get(2).hidden)
            self.assertIsNone(QuestReadiness.query.get(2))
            self.assertIsNone(Users.query.get(2).team_id)
            self.assertEqual(QuestAudit.query.filter_by(action="user.role").count(),role_audits+1)
        demoted=self.post(self.admin,"/api/v1/users/2",{"type":"user"},"PATCH")
        self.assertEqual(demoted.status_code,200,demoted.get_data(as_text=True))
        self.assertTrue(self.alice.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{"banned":"true"},"PATCH").status_code,200)
        with self.app.app_context():
            self.assertTrue(Users.query.get(2).banned)
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{"banned":"false"},"PATCH").status_code,200)

    def test_lifecycle_native_history_type_change_and_admin_delete_preserve_records(self):
        self.action("start")
        self.start()
        self.solve(self.bob,1)
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{"type":"admin"},"PATCH").status_code,409)
        self.assertEqual(self.post(self.admin,"/api/v1/users/3",{},"DELETE").status_code,409)
        with self.app.app_context():
            db.session.add(Users(id=5,name="Unused admin",email="unused-admin@example.test",password="test-only",type="admin"))
            db.session.add(QuestAudit(actor_id=1,action="Preserved audit",detail={"target_id":5}))
            db.session.commit()
        deleted=self.post(self.admin,"/api/v1/users/5",{},"DELETE")
        self.assertEqual(deleted.status_code,200,deleted.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(Users.query.get(5))
            self.assertEqual(Solves.query.count(),1)
            self.assertEqual(QuestStart.query.count(),1)
            self.assertEqual(QuestAudit.query.filter_by(action="Preserved audit").count(),1)
            self.assertEqual(Users.query.filter_by(type="admin",banned=False).count(),1)

    def gated_lifecycle_write(self, uid, path, body, method, mutation, native=False, browser=None):
        from CTFd.plugins.open_tech_quest import api as api_module, guards
        from flask import request
        module=guards if native else api_module
        original=module.event
        entered,release=threading.Event(),threading.Event()
        def gate(lock=False):
            if lock and request.path == path:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Lifecycle test gate not released")
            return original(lock=lock)
        with patch.object(module,"event",side_effect=gate):
            with ThreadPoolExecutor(max_workers=1) as pool:
                client=browser or self.client(uid)
                with client.session_transaction() as session:
                    nonce=session["nonce"]
                pending=pool.submit(lambda:client.open(path,method=method,json=body,headers={"CSRF-Token":nonce}))
                try:
                    self.assertTrue(entered.wait(3),path)
                    mutation()
                finally:
                    release.set()
                return pending.result()

    def test_lifecycle_queued_new_account_actions_reject_revoked_or_deleted_actor(self):
        self.open_onboarding()
        for uid,action,mutation in ((5,"role","delete"),(6,"access","demote"),(7,"delete","ban")):
            with self.subTest(action=action):
                with self.app.app_context():
                    db.session.add(Users(id=uid,name=f"Waiting admin {uid}",email=f"waiting-admin{uid}@example.test",type="admin",password="test-only"))
                    db.session.commit()
                fields={"role":"maintainer"} if action=="role" else ({"active":False} if action=="access" else {})
                path=PREFIX+"/admin/users/4"+("/"+action if action!="delete" else "")
                method="DELETE" if action=="delete" else "PATCH"
                data=dict(fields,confirm=True,identity=self.account_identity(4))
                def change_actor():
                    if mutation=="delete":
                        result=self.post(self.admin,f"/api/v1/users/{uid}",{},"DELETE")
                    else:
                        result=self.post(self.admin,f"/api/v1/users/{uid}",{"type":"user"} if mutation=="demote" else {"banned":True},"PATCH")
                    self.assertEqual(result.status_code,200,result.get_data(as_text=True))
                response=self.gated_lifecycle_write(uid,path,data,method,change_actor)
                self.assertEqual(response.status_code,403,response.get_data(as_text=True))
                with self.app.app_context():
                    self.assertIsNotNone(Users.query.get(4))
                    self.assertFalse(Users.query.get(4).banned)
                    self.assertEqual(Users.query.get(4).team_id,self.other_id)
                    self.assertIsNone(QuestMaintainer.query.get(4))
                    self.assertEqual(QuestAudit.query.filter_by(action="user.delete").filter(QuestAudit.detail["user_id"].as_integer()==4).count(),0)

    def test_lifecycle_queued_readiness_rechecks_deleted_or_staff_target(self):
        self.open_onboarding()
        self.attendance_window(True)
        path=PREFIX+"/onboarding/ready"
        browser=self.app.test_client()
        browser.get("/login")
        with browser.session_transaction() as session:
            nonce=session["nonce"]
        self.assertEqual(browser.post("/login",data={"nonce":nonce,"name":"Alice","password":"test-only"}).status_code,302)
        def delete_target():
            response=self.account_action(2,"delete")
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        deleted=self.gated_lifecycle_write(2,path,{},"POST",delete_target,browser=browser)
        self.assertEqual(deleted.status_code,401,deleted.get_data(as_text=True))
        def promote_target():
            self.assertEqual(self.grant_role(3).status_code,200)
        staff=self.gated_lifecycle_write(3,path,{},"POST",promote_target)
        self.assertEqual(staff.status_code,403,staff.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(QuestReadiness.query.get(2))
            self.assertIsNone(QuestReadiness.query.get(3))
            self.assertEqual(QuestStart.query.count(),0)

    def test_lifecycle_queued_start_and_attempt_recheck_deactivated_target(self):
        self.action("start")
        def deactivate():
            response=self.account_action(2,"access",active=False)
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        start=self.gated_lifecycle_write(2,PREFIX+"/rooms/openshift/start",{},"POST",deactivate)
        self.assertEqual(start.status_code,403,start.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(QuestStart.query.count(),0)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        self.start(self.bob)
        attempt=self.gated_lifecycle_write(2,"/api/v1/challenges/attempt",{"challenge_id":1,"submission":"fixture-answer-1"},"POST",deactivate,native=True)
        self.assertEqual(attempt.status_code,403,attempt.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Solves.query.count(),0)
            self.assertEqual(Submissions.query.count(),0)
            self.assertEqual(QuestStart.query.count(),1)
        self.assertEqual(self.solve(self.bob,1).json["data"]["status"],"correct")

    def test_lifecycle_optimizer_fingerprint_rejects_access_changes_and_incarnation_reuse(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        self.ready_user(self.bob)
        self.ready_user(self.carol)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan=self.attendance_plan()
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":False,"fingerprint":plan["fingerprint"]}).status_code,409)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        fresh=self.attendance_plan()
        self.assertNotEqual(fresh["fingerprint"],plan["fingerprint"])
        self.apply_attendance(fresh)

    def test_lifecycle_sqlite_real_registration_id_reuse_invalidates_old_identity(self):
        if not self.app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            self.skipTest("Native SQLite autoincrement ID reuse")
        self.open_onboarding()
        with self.app.app_context():
            db.session.add(Users(id=5,name="Reused name",email="reused-native@example.test",password="old-password"))
            db.session.commit()
        old=self.account_identity(5)
        self.assertEqual(self.account_action(5,"delete",old).status_code,200)
        client=self.app.test_client()
        self.assertEqual(client.get("/register").status_code,200)
        with client.session_transaction() as session:
            nonce=session["nonce"]
        created=client.post("/register",data={"nonce":nonce,"name":"Reused name","email":"reused-native@example.test","password":"new-password"})
        self.assertEqual(created.status_code,302,created.get_data(as_text=True))
        with client.session_transaction() as session:
            self.assertEqual(session["id"],5)
        self.assertNotEqual(self.account_identity(5),old)
        stale=self.account_action(5,"delete",old)
        self.assertEqual(stale.status_code,409)
        with self.app.app_context():
            self.assertIsNotNone(Users.query.get(5))

    def test_lifecycle_queued_native_account_change_rechecks_demoted_actor(self):
        self.open_onboarding()
        with self.app.app_context():
            db.session.add(Users(id=5,name="Queued native admin",email="queued-native@example.test",type="admin",password="test-only"))
            db.session.commit()
        def demote():
            response=self.post(self.admin,"/api/v1/users/5",{"type":"user"},"PATCH")
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        response=self.gated_lifecycle_write(5,"/api/v1/users/4",{"banned":True},"PATCH",demote,native=True)
        self.assertEqual(response.status_code,403,response.get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(Users.query.get(4).banned)
            self.assertEqual(Users.query.get(4).team_id,self.other_id)

    def native_login(self, name):
        client=self.app.test_client()
        self.assertEqual(client.get("/login").status_code,200)
        with client.session_transaction() as session:
            nonce=session["nonce"]
        self.assertEqual(client.post("/login",data={"nonce":nonce,"name":name,"password":"test-only"}).status_code,302)
        with client.session_transaction() as session:
            self.assertIn("hash",session)
        return client

    def test_lifecycle_signed_actor_replacement_cannot_inherit_queued_administrator_access(self):
        self.open_onboarding()
        with self.app.app_context():
            db.session.add(Users(id=5,name="Signed old admin",email="signed-old-admin@example.test",type="admin",password="test-only"))
            db.session.commit()
        browser=self.native_login("Signed old admin")
        path=PREFIX+"/admin/users/4/access"
        data={"active":False,"confirm":True,"identity":self.account_identity(4)}
        def replace_actor():
            self.assertEqual(self.post(self.admin,"/api/v1/users/5",{},"DELETE").status_code,200)
            # Explicit same-ID fixture covers both dialects; native SQLite
            # registration reuse is independently exercised above.
            with self.app.app_context():
                db.session.add(Users(id=5,name="Replacement admin",email="replacement-admin@example.test",type="admin",password="different-password"))
                db.session.commit()
        response=self.gated_lifecycle_write(5,path,data,"PATCH",replace_actor,browser=browser)
        self.assertEqual(response.status_code,403,response.get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(Users.query.get(4).banned)
            self.assertEqual(QuestAudit.query.filter_by(action="user.access").count(),0)

    def test_lifecycle_signed_personal_writes_recheck_deleted_or_banned_account_after_waiting(self):
        self.open_onboarding()
        carol=self.native_login("Carol")
        def delete_carol():
            self.assertEqual(self.account_action(4,"delete").status_code,200)
        deleted=self.gated_lifecycle_write(4,"/api/v1/users/me",{"name":"Deleted edit"},"PATCH",delete_carol,native=True,browser=carol)
        self.assertEqual(deleted.status_code,401,deleted.get_data(as_text=True))
        alice=self.native_login("Alice")
        def ban_alice():
            self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        banned=self.gated_lifecycle_write(2,"/api/v1/users/me",{"name":"Banned edit"},"PATCH",ban_alice,native=True,browser=alice)
        self.assertEqual(banned.status_code,403,banned.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(Users.query.get(4))
            self.assertEqual(Users.query.get(2).name,"Alice")

    def test_lifecycle_native_award_creation_serializes_with_unused_account_deletion(self):
        from flask import request
        from CTFd.models import Awards
        from CTFd.plugins.open_tech_quest import lifecycle
        self.open_onboarding()
        original=lifecycle.personal_activity
        entered,release=threading.Event(),threading.Event()
        def gate(user):
            result=original(user)
            if request.method=="DELETE" and request.path==PREFIX+"/admin/users/2":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Deletion activity gate not released")
            return result
        identity=self.account_identity(2)
        with patch.object(lifecycle,"personal_activity",side_effect=gate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                deleting=pool.submit(lambda:self.post(self.client(1),PREFIX+"/admin/users/2",{"confirm":True,"identity":identity},"DELETE"))
                self.assertTrue(entered.wait(3))
                awarding=pool.submit(lambda:self.post(self.client(1),"/api/v1/awards",{"name":"Concurrent award","value":10,"user_id":2,"team_id":self.team_id}))
                time.sleep(.1)
                try:
                    self.assertFalse(awarding.done(),"Native awards must wait for the account deletion transaction")
                finally:
                    release.set()
                deleted,award=deleting.result(),awarding.result()
        self.assertEqual(deleted.status_code,200,deleted.get_data(as_text=True))
        self.assertEqual(award.status_code,404,award.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Awards.query.count(),0)
            self.assertIsNone(Users.query.get(2))

    def test_lifecycle_existing_admin_team_write_rechecks_demoted_and_replaced_actor(self):
        self.open_onboarding()
        for uid,replace in ((5,False),(6,True)):
            with self.subTest(replace=replace):
                with self.app.app_context():
                    db.session.add(Users(id=uid,name=f"Team editor {uid}",email=f"team-editor{uid}@example.test",type="admin",password="test-only"))
                    db.session.commit()
                browser=self.native_login(f"Team editor {uid}")
                def revoke():
                    if replace:
                        self.assertEqual(self.post(self.admin,f"/api/v1/users/{uid}",{},"DELETE").status_code,200)
                        with self.app.app_context():
                            db.session.add(Users(id=uid,name=f"New editor {uid}",email=f"new-editor{uid}@example.test",type="admin",password="new-password"))
                            db.session.commit()
                    else:
                        self.assertEqual(self.post(self.admin,f"/api/v1/users/{uid}",{"type":"user"},"PATCH").status_code,200)
                response=self.gated_lifecycle_write(uid,PREFIX+f"/admin/teams/{self.team_id}",{"name":"Unauthorized queued rename"},"PATCH",revoke,browser=browser)
                self.assertEqual(response.status_code,403,response.get_data(as_text=True))
                with self.app.app_context():
                    self.assertEqual(Teams.query.get(self.team_id).name,"Red team")
                    self.assertEqual(QuestAudit.query.filter_by(action="team.configure").count(),0)

    def test_lifecycle_ban_excludes_own_contributions_teammates_recover_and_unban_deduplicates(self):
        from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve
        self.action("start")
        self.start(self.alice)
        self.assertEqual(self.solve(self.alice,1).json["data"]["status"],"correct")
        self.assertEqual(self.solve(self.bob,2).json["data"]["status"],"correct")
        def standing():
            payload=self.guest.get(PREFIX+"/standings").json["data"]
            return next(t for t in payload["teams"] if t["id"]==self.team_id)
        before=standing()
        with self.app.app_context():
            native=[(s.id,s.user_id,s.team_id,s.challenge_id,s.date,s.provided) for s in Solves.query.order_by(Solves.id).all()]
            starts=[(s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()]
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        without_alice=standing()
        self.assertEqual(without_alice["total"],1)
        self.assertEqual(without_alice["room_progress"][0]["solved"],1)
        self.assertEqual(without_alice["room_progress"][0]["path_solved"],0)
        self.assertEqual(without_alice["room_progress"][0]["solved_steps"],[2])
        relocked=self.bob.get(PREFIX+"/rooms/openshift").json["data"]["steps"]
        self.assertEqual([step["state"] for step in relocked],["available","locked"])
        self.assertIsNotNone(relocked[1]["solved_at"])
        self.assertEqual(self.bob.get("/api/v1/challenges/2").status_code,403)
        preview=self.admin.get(PREFIX+"/rooms/openshift").json["data"]["steps"]
        self.assertEqual([step["state"] for step in preview],["available","available"])
        recovered=self.solve(self.bob,1)
        self.assertEqual(recovered.json["data"]["status"],"correct",recovered.get_data(as_text=True))
        self.assertEqual(standing()["total"],2)
        self.assertEqual(standing()["room_progress"][0]["solved"],2)
        reopened=self.bob.get(PREFIX+"/rooms/openshift").json["data"]["steps"]
        self.assertEqual([step["state"] for step in reopened],["solved","solved"])
        self.assertEqual(reopened[1]["solved_at"],relocked[1]["solved_at"])
        with self.app.app_context():
            self.assertEqual(QuestRecoverySolve.query.count(),1)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        restored=standing()
        self.assertEqual(restored["total"],2)
        self.assertEqual(restored["overall_elapsed_ms"],before["overall_elapsed_ms"])
        self.assertEqual(restored["room_progress"][0]["milestone_elapsed_ms"],before["room_progress"][0]["milestone_elapsed_ms"])
        self.assertEqual(self.account_action(3,"access",active=False).status_code,200)
        self.assertEqual(standing()["total"],1)
        self.assertEqual(standing()["room_progress"][0]["solved"],1)
        self.assertEqual(self.solve(self.alice,2).json["data"]["status"],"correct")
        with self.app.app_context():
            self.assertEqual(QuestRecoverySolve.query.count(),2)
            self.assertEqual([(s.id,s.user_id,s.team_id,s.challenge_id,s.date,s.provided) for s in Solves.query.order_by(Solves.id).all()],native)
            self.assertEqual([(s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()],starts)
            self.assertEqual(Users.query.get(2).team_id,self.team_id)
            self.assertEqual(Users.query.get(3).team_id,self.team_id)

    def test_lifecycle_published_solve_contributor_ban_unban_requires_unpublish(self):
        self.action("start")
        self.start()
        self.solve(self.alice,1)
        self.action("close")
        self.action("publish")
        snapshot=self.guest.get(PREFIX+"/standings").json["data"]["teams"]
        row=self.managed_account(2)
        self.assertFalse(row["can_set_active"])
        self.assertIn("Unpublish",row["access_reason"])
        rejected=self.account_action(2,"access",active=False)
        self.assertEqual(rejected.status_code,409)
        self.assertEqual(rejected.json["error"],row["access_reason"])
        self.assertEqual(self.post(self.admin,"/api/v1/users/2",{"banned":True},"PATCH").status_code,409)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        self.assertEqual(self.guest.get(PREFIX+"/standings").json["data"]["teams"],snapshot)
        self.action("unpublish",reason="Review contribution eligibility")
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        self.action("publish")
        self.assertEqual(self.account_action(2,"access",active=True).status_code,409)
        self.action("unpublish",reason="Restore eligibility")
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        with self.app.app_context():
            self.assertEqual(Solves.query.count(),1)

    def test_lifecycle_solved_solution_uses_eligible_contributions_after_ban_and_recovery(self):
        from CTFd.models import Solutions
        with self.app.app_context():
            solution=Solutions(challenge_id=1,content="Traceable solved-only solution",state="solved")
            db.session.add(solution)
            db.session.commit()
            sid=solution.id
        self.action("start")
        self.start()
        self.solve(self.alice,1)
        unlocked=self.post(self.bob,"/api/v1/unlocks",{"type":"solutions","target":sid})
        self.assertEqual(unlocked.status_code,200,unlocked.get_data(as_text=True))
        self.assertEqual(self.bob.get(f"/api/v1/solutions/{sid}").status_code,200)
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        self.assertEqual(self.bob.get(f"/api/v1/solutions/{sid}").status_code,403)
        self.assertEqual(self.post(self.bob,"/api/v1/unlocks",{"type":"solutions","target":sid}).status_code,403)
        self.assertEqual(self.solve(self.bob,1).json["data"]["status"],"correct")
        self.assertEqual(self.bob.get(f"/api/v1/solutions/{sid}").status_code,200)

    def test_lifecycle_published_recovery_only_contributor_cannot_be_banned(self):
        self.action("start")
        self.start()
        self.solve(self.alice,1)
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        self.assertEqual(self.solve(self.bob,1).json["data"]["status"],"correct")
        with self.app.app_context():
            self.assertEqual(Solves.query.filter_by(user_id=3).count(),0)
        self.action("close")
        self.action("publish")
        self.assertFalse(self.managed_account(3)["can_set_active"])
        self.assertEqual(self.account_action(3,"access",active=False).status_code,409)
        self.assertEqual(self.post(self.admin,"/api/v1/users/3",{"banned":True},"PATCH").status_code,409)

    def timing_fixture(self, blue_last=65, recovery_at=None, banned_alice=False):
        from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve
        with self.app.app_context():
            base=(datetime.utcnow()-timedelta(minutes=5)).replace(microsecond=123456)
            e=QuestEvent.query.get(1)
            e.status,e.start_at="live",base
            rid=QuestRoom.query.filter_by(slug="openshift").one().id
            for tid in (self.team_id,self.other_id):
                db.session.add(QuestStart(team_id=tid,room_id=rid,started_at=base+timedelta(seconds=10)))
            db.session.add(QuestPause(started_at=base+timedelta(seconds=30),ended_at=base+timedelta(seconds=40)))
            original=Solves(challenge_id=1,user_id=2,team_id=self.team_id,date=base+timedelta(seconds=20))
            db.session.add_all([original,Solves(challenge_id=2,user_id=3,team_id=self.team_id,date=base+timedelta(seconds=50)),Solves(challenge_id=1,user_id=4,team_id=self.other_id,date=base+timedelta(seconds=20)),Solves(challenge_id=2,user_id=4,team_id=self.other_id,date=base+timedelta(seconds=blue_last))])
            Users.query.get(2).banned=banned_alice
            db.session.flush()
            original_id=original.id
            if recovery_at is not None:
                at=base+timedelta(seconds=recovery_at)
                db.session.add(QuestRecoverySolve(user_id=3,credited_user_id=3,team_id=self.team_id,challenge_id=1,original_solve_id=original_id,date=at,credited_at=at))
            db.session.commit()
        return base,rid,original_id

    def timing_standings(self):
        response=self.guest.get(PREFIX+"/standings")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return {row["id"]:row for row in response.json["data"]["teams"]}

    def test_lifecycle_rank_count_prefix_and_new_milestone_time_after_ban_recovery_unban(self):
        from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve
        base,rid,original_id=self.timing_fixture()
        initial=self.timing_standings()
        self.assertEqual(initial[self.team_id]["rank"],1)
        self.assertEqual(initial[self.team_id]["overall_elapsed_ms"],40000)
        self.assertEqual(initial[self.team_id]["room_progress"][0]["milestone_elapsed_ms"],30000)
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        banned=self.timing_standings()
        self.assertEqual(banned[self.team_id]["total"],1)
        self.assertEqual(banned[self.team_id]["overall_elapsed_ms"],40000)
        self.assertEqual(banned[self.team_id]["rank"],2)  # Higher net points win despite the earlier remaining milestone.
        self.assertEqual(banned[self.team_id]["room_progress"][0]["solved"],1)
        self.assertEqual(banned[self.team_id]["room_progress"][0]["path_solved"],0)
        self.assertEqual(banned[self.team_id]["room_progress"][0]["milestone_elapsed_ms"],30000)
        with self.app.app_context():
            at=base+timedelta(seconds=70)
            db.session.add(QuestRecoverySolve(user_id=3,credited_user_id=3,team_id=self.team_id,challenge_id=1,original_solve_id=original_id,date=at,credited_at=at))
            db.session.commit()
        recovered=self.timing_standings()
        self.assertEqual(recovered[self.team_id]["total"],2)
        self.assertEqual(recovered[self.team_id]["overall_elapsed_ms"],60000)
        self.assertEqual(recovered[self.team_id]["room_progress"][0]["milestone_elapsed_ms"],50000)
        self.assertEqual(recovered[self.team_id]["room_progress"][0]["solved"],2)
        self.assertEqual(recovered[self.team_id]["rank"],2)
        self.assertEqual(recovered[self.team_id]["room_progress"][0]["rank"],2)
        self.assertEqual(self.account_action(2,"access",active=True).status_code,200)
        restored=self.timing_standings()
        self.assertEqual(restored[self.team_id]["total"],2)
        self.assertEqual(restored[self.team_id]["overall_elapsed_ms"],40000)
        self.assertEqual(restored[self.team_id]["room_progress"][0]["milestone_elapsed_ms"],30000)
        self.assertEqual(restored[self.team_id]["rank"],1)
        self.assertEqual(self.account_action(3,"access",active=False).status_code,200)
        lost_last=self.timing_standings()[self.team_id]
        self.assertEqual(lost_last["total"],1)
        self.assertEqual(lost_last["overall_elapsed_ms"],20000)
        self.assertEqual(lost_last["room_progress"][0]["milestone_elapsed_ms"],10000)
        with self.app.app_context():
            self.assertEqual(Solves.query.count(),4)
            self.assertEqual(QuestRecoverySolve.query.count(),1)
            self.assertEqual(QuestStart.query.filter_by(team_id=self.team_id,room_id=rid).one().started_at,base+timedelta(seconds=10))

    def test_lifecycle_recovery_ties_clock_origins_pause_exclusion_and_cutoff(self):
        base,rid,_=self.timing_fixture(blue_last=60,recovery_at=60,banned_alice=True)
        tied=self.timing_standings()
        for tid in (self.team_id,self.other_id):
            self.assertEqual(tied[tid]["total"],2)
            self.assertEqual(tied[tid]["rank"],1)
            self.assertEqual(tied[tid]["overall_elapsed_ms"],50000)
            self.assertEqual(tied[tid]["room_progress"][0]["rank"],1)
            self.assertEqual(tied[tid]["room_progress"][0]["milestone_elapsed_ms"],40000)
        with self.app.app_context():
            e=QuestEvent.query.get(1)
            e.room_clock,e.overall_clock="event","team"
            db.session.commit()
        alternate=self.timing_standings()
        for tid in (self.team_id,self.other_id):
            self.assertEqual(alternate[tid]["overall_elapsed_ms"],40000)
            self.assertEqual(alternate[tid]["room_progress"][0]["milestone_elapsed_ms"],50000)
            self.assertEqual(alternate[tid]["rank"],1)
        with self.app.app_context():
            e=QuestEvent.query.get(1)
            e.end_at=base+timedelta(seconds=55)
            db.session.commit()
        cutoff=self.timing_standings()
        self.assertEqual(cutoff[self.team_id]["total"],1)
        self.assertEqual(cutoff[self.team_id]["room_progress"][0]["solved"],1)
        self.assertEqual(cutoff[self.team_id]["room_progress"][0]["path_solved"],0)
        self.assertEqual(cutoff[self.other_id]["room_progress"][0]["solved"],1)
        self.assertEqual(cutoff[self.team_id]["rank"],2)
        with self.app.app_context():
            QuestEvent.query.get(1).end_at=None
            db.session.add(QuestPause(started_at=base+timedelta(seconds=58),ended_at=base+timedelta(seconds=62)))
            db.session.commit()
        paused=self.timing_standings()
        self.assertEqual(paused[self.team_id]["total"],1)
        self.assertEqual(paused[self.team_id]["room_progress"][0]["solved"],1)
        self.assertEqual(paused[self.team_id]["room_progress"][0]["path_solved"],0)
        self.assertEqual(paused[self.other_id]["total"],1)

    def test_lifecycle_normal_native_solve_precision_never_predates_room_start(self):
        from sqlalchemy import inspect
        self.action("start")
        self.start()
        self.assertEqual(self.solve(self.alice,1).json["data"]["status"],"correct")
        with self.app.app_context():
            at=Solves.query.one().date
            start=QuestStart.query.filter_by(team_id=self.team_id).one().started_at
            print("Native timing precision:",db.engine.dialect.name,"start="+start.isoformat(),"solve="+at.isoformat(),"same_second="+str(start.replace(microsecond=0)==at.replace(microsecond=0)))
            self.assertGreaterEqual(at,start)
            self.assertEqual(Solves.query.one().user_id,2)
            if db.engine.dialect.name=="mysql":
                column=next(c for c in inspect(db.engine).get_columns("submissions") if c["name"]=="date")
                self.assertEqual(column["type"].fsp,6)
        row=self.timing_standings()[self.team_id]
        self.assertEqual(row["total"],1)
        self.assertEqual(row["room_progress"][0]["milestone_elapsed_ms"],round((at-start).total_seconds()*1000))

    def test_lifecycle_legacy_nullable_banned_status_matches_native_eligibility(self):
        with self.app.app_context():
            Users.query.filter(Users.id.in_([1,2])).update({"banned":None},synchronize_session=False)
            db.session.commit()
            from CTFd.plugins.open_tech_quest.lifecycle import usable_admins
            self.assertEqual(usable_admins(),1)
        self.action("start")
        self.start()
        accepted=self.solve(self.alice,1)
        self.assertEqual(accepted.json["data"]["status"],"correct",accepted.get_data(as_text=True))
        self.assertEqual(self.timing_standings()[self.team_id]["total"],1)

    def late_accounts(self, *ids):
        with self.app.app_context():
            for uid in ids:
                db.session.add(Users(id=uid,name=f"Late {uid}",email=f"late-{uid}@example.test",password="test-only",verified=True))
            db.session.commit()
        return [self.client(uid) for uid in ids]

    def late_status(self):
        response=self.admin.get(PREFIX+"/admin/state")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return response.json["data"]["onboarding"]["late_admission"]

    def admit(self, ids, fingerprint=None, **target):
        if fingerprint is None:
            fingerprint=self.late_status()["fingerprint"]
        return self.post(self.admin,PREFIX+"/admin/onboarding/admit",dict(target,user_ids=ids,reason="Checked-in event-day newcomers",fingerprint=fingerprint))

    def test_late_admission_finalized_window_is_late_only_and_personal(self):
        late,assigned,staff,banned,history=self.late_accounts(5,6,7,8,9)
        with self.app.app_context():
            Users.query.get(6).team_id=self.team_id
            Users.query.get(8).banned=True
            db.session.add(QuestMaintainer(user_id=7,previous_hidden=False))
            db.session.add(Submissions(user_id=9,team_id=self.other_id,challenge_id=1,type="incorrect",provided="Historical activity"))
            db.session.commit()
        initial=self.late_status()
        self.assertTrue(initial["allowed"])
        self.assertIsInstance(initial["fingerprint"],str)
        self.assertFalse(late.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        opened=self.attendance_window(True)
        self.assertTrue(opened["late_only"])
        self.assertTrue(late.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        self.assertFalse(assigned.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        self.assertFalse(history.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        self.assertEqual(staff.get(PREFIX+"/onboarding").status_code,403)
        for client in (assigned,history):
            self.assertEqual(self.post(client,PREFIX+"/onboarding/ready",{}).status_code,409)
            self.assertEqual(client.get(PREFIX+"/bootstrap",headers={"Sec-Fetch-Dest":"empty"}).status_code,200)
        self.assertIn(self.post(banned,PREFIX+"/onboarding/ready",{}).status_code,(302,403))
        self.assertEqual(late.get(PREFIX+"/bootstrap",headers={"Sec-Fetch-Dest":"empty"}).status_code,409)
        confirmed=self.ready_user(late)
        self.assertTrue(confirmed["late"])
        stamp=confirmed["ready_at"]
        self.attendance_window(False)
        self.assertEqual(self.ready_user(late)["ready_at"],stamp)
        self.assertEqual(self.post(assigned,PREFIX+"/onboarding/ready",{}).status_code,409)
        with self.app.app_context():
            self.assertEqual(QuestStart.query.count(),0)
            self.assertEqual(Solves.query.count(),0)
            self.assertIsNone(QuestEvent.query.get(1).start_at)
            self.assertEqual(QuestOnboarding.query.get(1).phase,"finalized")
        self.assertTrue(self.guest.get(PREFIX+"/bootstrap").json["data"]["registration"]["enabled"])

    def test_late_admission_existing_live_team_preserves_progress_and_banned_credit(self):
        self.action("start")
        self.start()
        self.solve(self.alice,1)
        self.assertEqual(self.account_action(2,"access",active=False).status_code,200)
        newcomer=self.late_accounts(5)[0]
        self.attendance_window(True)
        ready=self.ready_user(newcomer)["ready_at"]
        before=self.timing_standings()[self.team_id]
        self.assertEqual(before["total"],0)
        with self.app.app_context():
            native=[(s.id,s.user_id,s.team_id,s.date,s.provided) for s in Solves.query.all()]
            starts=[(s.id,s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()]
            e=QuestEvent.query.get(1)
            event_before=(e.status,e.start_at,e.end_at,e.room_clock,e.overall_clock)
            memberships=[(u.id,u.team_id) for u in Users.query.filter(Users.id<5).order_by(Users.id).all()]
        response=self.admit([5],team_id=self.team_id)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertEqual(self.timing_standings()[self.team_id],before)
        self.assertEqual(self.ready_user(newcomer)["ready_at"],ready)
        self.assertEqual(newcomer.get("/api/v1/challenges/1").status_code,200)
        with self.app.app_context():
            self.assertEqual([(s.id,s.user_id,s.team_id,s.date,s.provided) for s in Solves.query.all()],native)
            self.assertEqual([(s.id,s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()],starts)
            e=QuestEvent.query.get(1)
            self.assertEqual((e.status,e.start_at,e.end_at,e.room_clock,e.overall_clock),event_before)
            self.assertEqual([(u.id,u.team_id) for u in Users.query.filter(Users.id<5).order_by(Users.id).all()],memberships)
            self.assertEqual(Users.query.get(5).team_id,self.team_id)
            entry=QuestAudit.query.filter_by(action="onboarding.admit").one()
            self.assertEqual(entry.actor_id,1)
            self.assertEqual(entry.detail["user_ids"],[5])
            self.assertFalse(entry.detail["new_team"])
            self.assertIsNotNone(entry.detail["joined_at"])
        self.assertFalse(self.managed_account(5)["can_delete"])
        self.assertEqual(self.post(newcomer,PREFIX+"/onboarding/name",{"name":"Frozen","confirm":True}).status_code,409)
        recovered=self.solve(newcomer,1)
        self.assertEqual(recovered.json["data"]["status"],"correct",recovered.get_data(as_text=True))
        self.assertEqual(self.timing_standings()[self.team_id]["total"],1)

    def test_late_admission_additional_named_team_uses_normal_start_and_real_admin_lock(self):
        self.action("start")
        first,second=self.late_accounts(5,6)
        self.attendance_window(True)
        self.ready_user(first)
        self.ready_user(second)
        response=self.admit([6,5],new_team_name="Comet Crew")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        with self.app.app_context():
            team=Teams.query.filter_by(name="Comet Crew").one()
            tid=team.id
            self.assertEqual(team.captain_id,6)
            self.assertEqual({u.id for u in team.members},{5,6})
            locked=QuestTeamName.query.get(tid)
            self.assertTrue(locked.name_locked)
            self.assertEqual(locked.claimed_by,1)
            self.assertEqual(QuestStart.query.filter_by(team_id=tid).count(),0)
            self.assertEqual(Solves.query.filter_by(team_id=tid).count(),0)
            self.assertEqual(QuestOnboarding.query.get(1).phase,"finalized")
        row=self.timing_standings()[tid]
        self.assertEqual(row["total"],0)
        self.assertIsNone(row["rank"])
        self.assertEqual(first.get("/api/v1/challenges/1").status_code,403)
        self.assertEqual(self.solve(first,1).status_code,403)
        self.start(first)
        self.assertEqual(self.solve(second,1).json["data"]["status"],"correct")
        self.assertEqual(self.timing_standings()[tid]["total"],1)
        self.assertEqual(self.post(self.admin,"/api/v1/teams/"+str(tid)+"/members",{"user_id":4}).status_code,409)
        self.assertEqual(self.post(first,"/api/v1/teams/"+str(tid),{"name":"Bypass"},"PATCH").status_code,403)

    def test_late_admission_permissions_strict_body_capacity_targets_and_no_partial_changes(self):
        clients=self.late_accounts(5,6,7)
        self.attendance_window(True)
        for client in clients:
            self.ready_user(client)
        path=PREFIX+"/admin/onboarding/admit"
        fingerprint=self.late_status()["fingerprint"]
        valid={"user_ids":[5],"team_id":self.team_id,"reason":"Explicit placement","fingerprint":fingerprint}
        self.assertEqual(self.post(self.alice,path,valid).status_code,403)
        self.assertEqual(self.admin.post(path,json=valid).status_code,403)
        with self.guest.session_transaction() as session:
            session["nonce"]="quest-test-csrf"
        self.assertEqual(self.post(self.guest,path,valid).status_code,401)
        for data in (dict(valid,new_team_name="Both targets"),{k:v for k,v in valid.items() if k!="reason"},dict(valid,unknown=True),dict(valid,reason=""),dict(valid,user_ids=[5,5]),dict(valid,team_id=True)):
            self.assertEqual(self.post(self.admin,path,data).status_code,400)
        with self.app.app_context():
            QuestOnboarding.query.get(1).team_size=2
            db.session.commit()
        self.assertEqual(self.admit([5],team_id=self.team_id).status_code,409)
        with self.app.app_context():
            QuestOnboarding.query.get(1).team_size=3
            Users.query.get(2).banned=True
            db.session.commit()
        self.assertEqual(self.admit([5,6],team_id=self.team_id).status_code,409)  # Banned members still consume capacity.
        for field in ("hidden","banned"):
            with self.app.app_context():
                setattr(Teams.query.get(self.team_id),field,True)
                db.session.commit()
            self.assertEqual(self.admit([5],team_id=self.team_id).status_code,409)
            with self.app.app_context():
                setattr(Teams.query.get(self.team_id),field,False)
                db.session.commit()
        with self.app.app_context():
            QuestTeamName.query.get(self.team_id).name_locked=False
            db.session.commit()
        self.assertEqual(self.admit([5],team_id=self.team_id).status_code,409)
        self.assertEqual(self.admit([5],team_id=999999).status_code,404)
        for name in ("RED TEAM",None,""):
            result=self.admit([5],new_team_name=name)
            self.assertIn(result.status_code,(400,409),result.get_data(as_text=True))
        with self.app.app_context():
            self.assertTrue(all(Users.query.get(uid).team_id is None for uid in (5,6,7)))
            self.assertEqual(Teams.query.count(),2)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.admit").count(),0)

    def test_late_admission_rejects_staff_banned_history_not_ready_and_existing_members(self):
        self.late_accounts(5,6,7,8)
        with self.app.app_context():
            for uid in (5,6,7):
                db.session.add(QuestReadiness(user_id=uid,late=True))
            db.session.add(QuestMaintainer(user_id=5,previous_hidden=False))
            Users.query.get(6).banned=True
            db.session.add(Submissions(user_id=7,team_id=self.other_id,challenge_id=1,type="incorrect",provided="Historical attempt"))
            db.session.commit()
        for uid in (2,5,6,7,8,999999):
            result=self.admit([uid],new_team_name="Must Not Exist")
            self.assertEqual(result.status_code,409,result.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Teams.query.count(),2)
            self.assertEqual(Submissions.query.count(),1)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.admit").count(),0)

    def test_late_admission_concurrent_duplicate_new_team_is_atomic(self):
        first,second=self.late_accounts(5,6)
        self.attendance_window(True)
        self.ready_user(first)
        self.ready_user(second)
        fingerprint=self.late_status()["fingerprint"]
        payload={"user_ids":[5,6],"new_team_name":"Atomic Arrivals","reason":"One agreed final team","fingerprint":fingerprint}
        def send(_):
            return self.post(self.client(1),PREFIX+"/admin/onboarding/admit",payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(send,(1,2)))
        self.assertEqual(sorted(r.status_code for r in responses),[200,409])
        with self.app.app_context():
            team=Teams.query.filter_by(name="Atomic Arrivals").one()
            self.assertEqual({u.id for u in team.members},{5,6})
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.admit").count(),1)
            self.assertEqual(Teams.query.count(),3)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/admit",payload).status_code,409)

    def test_late_admission_fingerprints_cover_capacity_window_and_account_incarnation(self):
        newcomer=self.late_accounts(5)[0]
        self.attendance_window(True)
        self.ready_user(newcomer)
        with self.app.app_context():
            stamp=QuestReadiness.query.get(5).ready_at
        fingerprint=self.late_status()["fingerprint"]
        self.attendance_window(False)
        self.assertEqual(self.admit([5],fingerprint,team_id=self.team_id).status_code,409)
        fingerprint=self.late_status()["fingerprint"]
        with self.app.app_context():
            QuestOnboarding.query.get(1).team_size=4
            db.session.commit()
        self.assertEqual(self.admit([5],fingerprint,team_id=self.team_id).status_code,409)
        fingerprint=self.late_status()["fingerprint"]
        self.assertEqual(self.account_action(5,"delete").status_code,200)
        with self.app.app_context():
            db.session.add(Users(id=5,name="Late 5",email="late-5@example.test",password="new-account",verified=True))
            db.session.flush()
            db.session.add(QuestReadiness(user_id=5,late=True,ready_at=stamp))
            db.session.commit()
        self.assertEqual(self.admit([5],fingerprint,new_team_name="Stale Identity").status_code,409)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(5).team_id)
            self.assertIsNone(Teams.query.filter_by(name="Stale Identity").first())
        self.assertEqual(self.admit([5],team_id=self.team_id).status_code,200)

    def test_late_admission_pause_close_cutoff_and_start_guards_preserve_clocks(self):
        newcomer,waiting=self.late_accounts(5,6)
        self.attendance_window(True)
        self.ready_user(newcomer)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/event/action",{"action":"start"}).status_code,409)
        self.attendance_window(False)
        self.action("start")
        self.start()
        self.attendance_window(True)
        self.action("pause")
        with self.app.app_context():
            starts=[(s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()]
            pauses=[(p.started_at,p.ended_at) for p in QuestPause.query.all()]
        self.assertEqual(self.admit([5],team_id=self.team_id).status_code,200)
        self.assertEqual(self.solve(newcomer,1).status_code,409)
        self.assertEqual(self.post(newcomer,PREFIX+"/rooms/ansible/start",{}).status_code,409)
        with self.app.app_context():
            self.assertEqual([(s.team_id,s.room_id,s.started_at) for s in QuestStart.query.all()],starts)
            self.assertEqual([(p.started_at,p.ended_at) for p in QuestPause.query.all()],pauses)
            self.assertEqual(Solves.query.count(),0)
        self.action("close")
        status=self.late_status()
        self.assertFalse(status["allowed"])
        self.assertIsNone(status["fingerprint"])
        self.assertFalse(waiting.get(PREFIX+"/onboarding").json["data"]["can_ready"])
        self.assertFalse(self.admin.get(PREFIX+"/admin/state").json["data"]["onboarding"]["attendance"]["enabled"])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/attendance",{"enabled":True},"PATCH").status_code,409)
        self.assertEqual(self.admit([6],new_team_name="After close").status_code,409)
        self.action("publish")
        self.assertEqual(self.admit([6],new_team_name="After publish").status_code,409)
        with self.app.app_context():
            e=QuestEvent.query.get(1)
            e.status="live"
            e.end_at=datetime.utcnow()-timedelta(seconds=1)
            db.session.commit()
        self.assertFalse(self.late_status()["allowed"])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/attendance",{"enabled":True},"PATCH").status_code,409)
        self.assertEqual(self.admit([6],new_team_name="After cutoff").status_code,409)

    def test_late_admission_rechecks_revoked_actor_and_close_while_waiting(self):
        self.action("start")
        newcomer=self.late_accounts(6)[0]
        self.attendance_window(True)
        self.ready_user(newcomer)
        with self.app.app_context():
            db.session.add(Users(id=5,name="Late placement admin",email="late-admin@example.test",type="admin",password="test-only"))
            db.session.commit()
        browser=self.native_login("Late placement admin")
        path=PREFIX+"/admin/onboarding/admit"
        payload={"user_ids":[6],"team_id":self.team_id,"reason":"Waiting placement","fingerprint":self.late_status()["fingerprint"]}
        def demote():
            result=self.post(self.admin,"/api/v1/users/5",{"type":"user"},"PATCH")
            self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        denied=self.gated_lifecycle_write(5,path,payload,"POST",demote,browser=browser)
        self.assertEqual(denied.status_code,403,denied.get_data(as_text=True))
        payload["fingerprint"]=self.late_status()["fingerprint"]
        closed=self.gated_lifecycle_write(1,path,payload,"POST",lambda:self.action("close"))
        self.assertEqual(closed.status_code,409,closed.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(Users.query.get(6).team_id)
            self.assertEqual(QuestAudit.query.filter_by(action="onboarding.admit").count(),0)

    def test_late_admission_unavailable_before_finalization_and_optimizer_stays_frozen(self):
        self.open_onboarding()
        status=self.late_status()
        self.assertFalse(status["allowed"])
        self.assertIsNone(status["fingerprint"])
        self.assertEqual(self.admit([2],new_team_name="Not finalized").status_code,409)
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase="finalized"
            db.session.commit()
        self.assertTrue(self.late_status()["allowed"])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":True}).status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+f"/admin/teams/{self.team_id}",{"user_ids":[2]},"PATCH").status_code,409)

    def test_late_admission_waiting_newcomer_does_not_hold_start_after_capture_closes(self):
        self.open_onboarding()
        self.attendance_window(True)
        self.ready_user(self.alice)
        self.ready_user(self.bob)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        self.apply_attendance(self.attendance_plan())
        self.assertEqual(self.post(self.alice,PREFIX+"/onboarding/name",{"name":"On-time Foxes","confirm":True}).status_code,200)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)
        newcomer=self.late_accounts(5)[0]
        self.attendance_window(True)
        self.ready_user(newcomer)
        blocked=self.post(self.admin,PREFIX+"/admin/event/action",{"action":"start"})
        self.assertEqual(blocked.status_code,409)
        self.assertIn("Close attendance",blocked.json["error"])
        self.attendance_window(False)
        self.assertTrue(self.admin.get(PREFIX+"/admin/state").json["data"]["onboarding"]["can_start"])
        self.action("start")
        with self.app.app_context():
            self.assertIsNone(Users.query.get(5).team_id)
            self.assertTrue(QuestReadiness.query.get(5).late)
            self.assertGreater(QuestReadiness.query.get(5).ready_at,QuestOnboarding.query.get(1).finalized_at)
            tid=Users.query.get(2).team_id
        admitted=self.admit([5],team_id=tid)
        self.assertEqual(admitted.status_code,200,admitted.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Users.query.get(5).team_id,tid)
            self.assertEqual(QuestStart.query.count(),0)

    def test_late_admission_start_exception_never_bypasses_earlier_unresolved_readiness(self):
        from CTFd.plugins.open_tech_quest import onboarding
        with self.app.app_context():
            state=QuestOnboarding.query.get(1)
            state.grandfathered=False
            state.finalized_at=datetime.utcnow()
            Users.query.get(4).team_id=None
            QuestReadiness.query.get(4).late=True
            QuestReadiness.query.get(4).ready_at=state.finalized_at-timedelta(seconds=1)
            db.session.commit()
        denied=self.post(self.admin,PREFIX+"/admin/event/action",{"action":"start"})
        self.assertEqual(denied.status_code,409)
        self.assertIn("Assign every ready",denied.json["error"])
        with self.app.app_context():
            state=QuestOnboarding.query.get(1)
            QuestReadiness.query.get(4).ready_at=state.finalized_at+timedelta(seconds=1)
            for uid in (2,3):
                Users.query.get(uid).banned=True
            db.session.commit()
        denied=self.post(self.admin,PREFIX+"/admin/event/action",{"action":"start"})
        self.assertEqual(denied.status_code,409)
        self.assertIn("attended, assigned team",denied.json["error"])
        with self.app.app_context():
            state=QuestOnboarding.query.get(1)
            state.phase="naming"
            self.assertIsNotNone(onboarding.finalization_reason())

    def formation_pool(self, count=21, capacity=5, naming=False):
        self.open_onboarding(capacity=capacity,allow_team_naming=naming)
        with self.app.app_context():
            for user in Users.query.filter_by(type="user").all():
                user.team_id=None
            db.session.flush()
            Teams.query.delete()
            password=Users.query.get(2).password
            for uid in range(5,count+2):
                db.session.execute(Users.__table__.insert(),{"id":uid,"name":f"Attendee {uid}","email":f"formation{uid}@example.test","password":password,"type":"user","verified":True})
            db.session.commit()
        return list(range(2,count+2))

    def naming_policy(self, enabled):
        response=self.post(self.admin,PREFIX+"/admin/onboarding",{"allow_team_naming":enabled},"PATCH")
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return response.json["data"]

    def form_ready_pool(self, ids):
        self.attendance_window(True)
        for uid in ids:
            self.ready_user(self.client(uid))
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        return self.attendance_plan()

    def test_formation_default_morning_registration_ready_only_random_stable_apply_and_fixed_names(self):
        ids=self.formation_pool(21)
        early=self.app.test_client()
        self.assertEqual(early.get("/register").status_code,200)
        with early.session_transaction() as session:
            nonce=session["nonce"]
        self.assertEqual(early.post("/register",data={"nonce":nonce,"name":"Morning signup","email":"morning@example.test","password":"test-only"}).status_code,302)
        self.assertFalse(early.get(PREFIX+"/onboarding").json["data"]["allow_team_naming"])
        with self.app.app_context():
            self.assertEqual(Teams.query.count(),0)
            self.assertEqual(QuestReadiness.query.count(),0)
            self.assertEqual(QuestStart.query.count(),0)
            self.assertIsNone(QuestEvent.query.get(1).start_at)
        self.attendance_window(True)
        for uid in ids:
            self.ready_user(self.client(uid))
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).status_code,409)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan=self.attendance_plan()
        self.assertEqual(plan,self.attendance_plan())
        self.assertEqual([len(g["user_ids"]) for g in plan["groups"]],[5,4,4,4,4])
        self.assertEqual([g["name"] for g in plan["groups"]],["Team 1","Team 2","Team 3","Team 4","Team 5"])
        placed=[uid for g in plan["groups"] for uid in g["user_ids"]]
        self.assertEqual(set(placed),set(ids))
        self.assertNotEqual(placed,sorted(ids))
        self.assertTrue(any(max(g["user_ids"])-min(g["user_ids"])+1>len(g["user_ids"]) for g in plan["groups"]))
        with self.app.app_context():
            times={r.user_id:r.ready_at for r in QuestReadiness.query.all()}
            self.assertEqual(Teams.query.count(),0)
        self.apply_attendance(plan)
        state=self.admin.get(PREFIX+"/admin/state").json["data"]["onboarding"]
        self.assertFalse(state["allow_team_naming"])
        self.assertEqual(state["unnamed_count"],0)
        with self.app.app_context():
            for group in plan["groups"]:
                team=Teams.query.filter_by(name=group["name"]).one()
                self.assertEqual({u.id for u in team.members},set(group["user_ids"]))
                self.assertEqual(team.captain_id,group["user_ids"][0])
                lock=QuestTeamName.query.get(team.id)
                self.assertTrue(lock.name_locked)
                self.assertTrue(lock.auto_named)
                self.assertIsNone(lock.claimed_by)
            self.assertEqual({r.user_id:r.ready_at for r in QuestReadiness.query.all()},times)
            self.assertEqual(QuestStart.query.count(),0)
            self.assertEqual(Solves.query.count(),0)
            self.assertIsNone(QuestEvent.query.get(1).start_at)
        member=self.client(placed[0])
        self.assertFalse(member.get(PREFIX+"/onboarding").json["data"]["can_name"])
        self.assertEqual(self.post(member,PREFIX+"/onboarding/name",{"name":"Not allowed","confirm":True}).status_code,409)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)
        self.action("start")
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding",{"allow_team_naming":True},"PATCH").status_code,409)

    def test_formation_ready_tail_sizes_staff_and_bans_excluded_without_clock_changes(self):
        ids=self.formation_pool(25)
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=25,previous_hidden=False))
            Users.query.get(26).banned=True
            db.session.commit()
        self.attendance_window(True)
        for uid in ids[:21]:
            self.ready_user(self.client(uid))
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        plan21=self.attendance_plan()
        self.assertEqual(sorted(len(g["user_ids"]) for g in plan21["groups"]),[4,4,4,4,5])
        self.attendance_window(True)
        self.ready_user(self.client(ids[21]))
        self.attendance_window(False)
        plan22=self.attendance_plan()
        self.assertEqual(sorted(len(g["user_ids"]) for g in plan22["groups"]),[4,4,4,5,5])
        self.assertNotEqual(plan21["fingerprint"],plan22["fingerprint"])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":False,"fingerprint":plan21["fingerprint"]}).status_code,409)
        with self.app.app_context():
            QuestReadiness.query.filter(~QuestReadiness.user_id.in_(ids[:11])).delete(synchronize_session=False)
            db.session.commit()
        plan11=self.attendance_plan()
        self.assertEqual(sorted(len(g["user_ids"]) for g in plan11["groups"]),[3,4,4])
        self.assertTrue(plan11["warnings"])
        self.assertNotIn(25,plan11["unassigned_user_ids"])
        self.assertNotIn(26,plan11["unassigned_user_ids"])
        with self.app.app_context():
            self.assertEqual(Teams.query.count(),0)
            self.assertEqual(QuestStart.query.count(),0)
            self.assertEqual(Solves.query.count(),0)

    def test_formation_optional_once_naming_toggle_and_admin_correction_provenance(self):
        ids=self.formation_pool(11)
        plan=self.form_ready_pool(ids)
        self.apply_attendance(plan)
        self.naming_policy(True)
        first_group=plan["groups"][0]
        claimant=self.client(first_group["user_ids"][-1])
        with self.app.app_context():
            first=Teams.query.filter_by(name=first_group["name"]).one()
            self.assertNotEqual(first.captain_id,first_group["user_ids"][-1])
        self.assertTrue(claimant.get(PREFIX+"/onboarding").json["data"]["can_name"])
        self.assertEqual(self.post(claimant,PREFIX+"/onboarding/name",{"name":"Ready Rockets","confirm":True}).status_code,200)
        self.naming_policy(False)
        self.naming_policy(True)
        self.assertFalse(claimant.get(PREFIX+"/onboarding").json["data"]["can_name"])
        with self.app.app_context():
            chosen=Teams.query.filter_by(name="Ready Rockets").one()
            self.assertFalse(QuestTeamName.query.get(chosen.id).auto_named)
            second=Teams.query.filter_by(name=plan["groups"][1]["name"]).one()
            second_id=second.id
        self.assertTrue(self.client(plan["groups"][1]["user_ids"][0]).get(PREFIX+"/onboarding").json["data"]["can_name"])
        corrected=self.post(self.admin,PREFIX+f"/admin/teams/{second_id}",{"name":"Organizer Owls","reason":"Agreed organizer correction"},"PATCH")
        self.assertEqual(corrected.status_code,200,corrected.get_data(as_text=True))
        self.naming_policy(False)
        self.naming_policy(True)
        with self.app.app_context():
            lock=QuestTeamName.query.get(second_id)
            self.assertFalse(lock.auto_named)
            self.assertTrue(lock.name_locked)
            self.assertEqual(lock.claimed_by,1)
            self.assertEqual(Teams.query.get(second_id).name,"Organizer Owls")
        self.naming_policy(False)
        self.assertEqual(self.admin.get(PREFIX+"/admin/state").json["data"]["onboarding"]["unnamed_count"],0)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)

    def test_formation_naming_policy_strict_validation_stale_plans_and_once_claim_race(self):
        ids=self.formation_pool(5)
        for data in ({},{"allow_team_naming":1},{"allow_team_naming":"true"},{"unknown":True}):
            self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding",data,"PATCH").status_code,400)
        plan=self.form_ready_pool(ids)
        self.naming_policy(True)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding/optimize",{"preview":False,"fingerprint":plan["fingerprint"]}).status_code,409)
        plan=self.attendance_plan()
        self.apply_attendance(plan)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding",{"team_size":6},"PATCH").status_code,409)
        entered,release=threading.Event(),threading.Event()
        from CTFd.plugins.open_tech_quest import api as api_module
        from flask import request
        original=api_module.event
        def gate(lock=False):
            if lock and request.path==PREFIX+"/onboarding/name":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Naming policy gate not released")
            return original(lock=lock)
        with patch.object(api_module,"event",side_effect=gate):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(lambda:self.post(self.client(ids[0]),PREFIX+"/onboarding/name",{"name":"Stale naming form","confirm":True}))
                try:
                    self.assertTrue(entered.wait(3))
                    self.naming_policy(False)
                finally:
                    release.set()
                self.assertEqual(pending.result().status_code,409)
        self.assertEqual(self.naming_policy(True)["unnamed_count"],1)
        def claim(uid):
            return self.post(self.client(uid),PREFIX+"/onboarding/name",{"name":f"Claimed by {uid}","confirm":True})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(claim,ids[:2]))
        self.assertEqual(sorted(r.status_code for r in responses),[200,409])
        self.naming_policy(False)
        self.assertEqual(self.naming_policy(True)["unnamed_count"],0)

    def test_formation_legacy_allocate_ready_only_stable_and_exact_and_late_teams_follow_policy(self):
        ids=self.formation_pool(3)
        empty=self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True})
        self.assertEqual(empty.json["data"]["groups"],[])
        self.attendance_window(True)
        self.ready_user(self.client(ids[0]))
        self.ready_user(self.client(ids[1]))
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).status_code,409)
        self.attendance_window(False)
        preview=self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).json["data"]
        self.assertEqual(preview,self.post(self.admin,PREFIX+"/admin/teams/allocate",{"preview":True}).json["data"])
        self.assertEqual(set(uid for g in preview["groups"] for uid in g["user_ids"]),set(ids[:2]))
        changed=__import__("copy").deepcopy(preview)
        changed["groups"][0]["user_ids"].reverse()
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",dict(changed,preview=False)).status_code,409)
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/teams/allocate",dict(preview,preview=False)).status_code,200)
        self.assertEqual(self.onboarding_action("close_checkin").status_code,200)
        self.apply_attendance(self.attendance_plan())
        self.attendance_window(True)
        self.ready_user(self.client(ids[2]))
        self.attendance_window(False)
        late=self.onboarding_action("create_late_team",user_ids=[ids[2]],reason="Checked-in after grouping")
        self.assertEqual(late.status_code,200,late.get_data(as_text=True))
        own=self.client(ids[2]).get(PREFIX+"/onboarding").json["data"]
        self.assertTrue(own["team"]["name_locked"])
        self.assertFalse(own["can_name"])
        self.naming_policy(True)
        self.assertTrue(self.client(ids[2]).get(PREFIX+"/onboarding").json["data"]["can_name"])
        self.naming_policy(False)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)
        newcomer=self.late_accounts(30)[0]
        self.attendance_window(True)
        self.ready_user(newcomer)
        self.assertEqual(self.admit([30],new_team_name="Team 7").status_code,200)
        with self.app.app_context():
            team=Teams.query.filter_by(name="Team 7").one()
            self.assertTrue(QuestTeamName.query.get(team.id).name_locked)
            self.assertFalse(QuestTeamName.query.get(team.id).auto_named)
            self.assertEqual(QuestTeamName.query.get(team.id).claimed_by,1)

    def simulate_old_formation_columns(self):
        from CTFd.plugins.migrations import upgrade
        from CTFd.plugins.open_tech_quest.schema import upgrade_sqlite_attendance
        with self.app.app_context():
            db.session.remove()
            with db.engine.begin() as connection:
                connection.execute("ALTER TABLE quest_onboarding DROP COLUMN allow_team_naming")
                connection.execute("ALTER TABLE quest_team_names DROP COLUMN auto_named")
            set_config("open_tech_quest_alembic_version","quest_0007")
            db.session.remove()
            upgrade(plugin_name="open_tech_quest")
            upgrade_sqlite_attendance()

    def test_formation_upgrade_old_applied_naming_preserves_claims_and_remains_completable(self):
        self.open_onboarding()
        with self.app.app_context():
            state=QuestOnboarding.query.get(1)
            state.phase,state.applied_at="naming",datetime.utcnow()
            Teams.query.get(self.team_id).name="Team 01"
            Teams.query.get(self.other_id).name="Chosen Owls"
            claimed=datetime.utcnow()-timedelta(seconds=2)
            db.session.add(QuestTeamName(team_id=self.other_id,name_locked=True,claimed_by=4,claimed_at=claimed))
            for uid in (2,3,4):
                db.session.add(QuestReadiness(user_id=uid))
            db.session.commit()
            people=[(u.id,u.name,u.team_id) for u in Users.query.order_by(Users.id).all()]
            ready=[(r.user_id,r.ready_at) for r in QuestReadiness.query.order_by(QuestReadiness.user_id).all()]
            names=[(t.id,t.name) for t in Teams.query.order_by(Teams.id).all()]
        self.simulate_old_formation_columns()
        with self.app.app_context():
            self.assertTrue(QuestOnboarding.query.get(1).allow_team_naming)
            self.assertFalse(QuestTeamName.query.get(self.team_id).name_locked)
            self.assertTrue(QuestTeamName.query.get(self.team_id).auto_named)
            self.assertFalse(QuestTeamName.query.get(self.other_id).auto_named)
            self.assertEqual(QuestTeamName.query.get(self.other_id).claimed_by,4)
            self.assertEqual(QuestTeamName.query.get(self.other_id).claimed_at,claimed)
            self.assertEqual([(u.id,u.name,u.team_id) for u in Users.query.order_by(Users.id).all()],people)
            self.assertEqual([(r.user_id,r.ready_at) for r in QuestReadiness.query.order_by(QuestReadiness.user_id).all()],ready)
            self.assertEqual([(t.id,t.name) for t in Teams.query.order_by(Teams.id).all()],names)
            self.assertEqual(QuestStart.query.count(),0)
        self.naming_policy(False)
        self.naming_policy(True)
        self.assertTrue(self.alice.get(PREFIX+"/onboarding").json["data"]["can_name"])
        self.assertFalse(self.carol.get(PREFIX+"/onboarding").json["data"]["can_name"])
        self.naming_policy(False)
        # Re-running actual startup migration steps must not re-enable policy.
        from CTFd.plugins.migrations import upgrade
        from CTFd.plugins.open_tech_quest.schema import upgrade_sqlite_attendance
        with self.app.app_context():
            upgrade(plugin_name="open_tech_quest")
            upgrade_sqlite_attendance()
            self.assertFalse(QuestOnboarding.query.get(1).allow_team_naming)
        self.assertEqual(self.onboarding_action("finalize").status_code,200)

    def test_formation_upgrade_active_legacy_preserves_names_native_history_and_clocks(self):
        with self.app.app_context():
            stamp=(datetime.utcnow()-timedelta(minutes=3)).replace(microsecond=345678)
            e=QuestEvent.query.get(1)
            e.status,e.start_at="live",stamp
            state=QuestOnboarding.query.get(1)
            state.finalized_at=stamp-timedelta(seconds=1)
            db.session.add(QuestStart(team_id=self.team_id,room_id=QuestRoom.query.filter_by(slug="openshift").one().id,started_at=stamp+timedelta(seconds=1)))
            db.session.add(Solves(challenge_id=1,user_id=2,team_id=self.team_id,date=stamp+timedelta(seconds=2)))
            db.session.commit()
            before=standings()
            native=[(s.id,s.user_id,s.team_id,s.date) for s in Solves.query.all()]
            locks=[(n.team_id,n.name_locked,n.claimed_by,n.claimed_at) for n in QuestTeamName.query.order_by(QuestTeamName.team_id).all()]
        self.simulate_old_formation_columns()
        with self.app.app_context():
            self.assertFalse(QuestOnboarding.query.get(1).allow_team_naming)
            self.assertEqual([(n.team_id,n.name_locked,n.claimed_by,n.claimed_at) for n in QuestTeamName.query.order_by(QuestTeamName.team_id).all()],locks)
            self.assertTrue(all(not n.auto_named for n in QuestTeamName.query.all()))
            self.assertEqual([(s.id,s.user_id,s.team_id,s.date) for s in Solves.query.all()],native)
            self.assertEqual(QuestEvent.query.get(1).start_at,stamp)
            self.assertEqual(standings()["teams"],before["teams"])
        self.assertEqual(self.post(self.admin,PREFIX+"/admin/onboarding",{"allow_team_naming":True},"PATCH").status_code,409)
