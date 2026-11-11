"""Portable private archive evidence from real native CTFd event history."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import unittest
from unittest.mock import patch

from sqlalchemy import event as sa_event
from CTFd.models import (Awards, ChallengeComments, ChallengeTopics, Challenges,
                         Fails, Flags, Hints, Notifications, PageFiles, Ratings,
                         SolutionFiles, Solutions, Solves, Tags, TeamComments,
                         TeamFieldEntries, TeamFields, Topics, UserComments,
                         UserFieldEntries, UserFields, Users, db)
from CTFd.plugins.open_tech_quest.archive import (canonical_json, collect_event,
                                                make_document, source_fingerprint)
from CTFd.plugins.open_tech_quest.models import (QuestAudit, QuestCorrection,
    QuestDemoBatch, QuestDemoChallenge, QuestEvent, QuestExclusion,
    QuestMaintainer, QuestPause, QuestRoomPause, QuestStart)
from CTFd.plugins.open_tech_quest.service import event, standings
import test_integration as integration


class ArchiveIntegration(unittest.TestCase):
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

    def capture(self):
        with self.app.app_context():
            value = collect_event(event(lock=True))
            db.session.rollback()
            # Exercise an ordinary portable JSON round trip, not ORM objects.
            return json.loads(canonical_json(value))

    def begin(self):
        self.action("start")
        self.start()

    def test_content_identity_and_order_include_unmapped_native_content_without_secrets(self):
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=3, previous_hidden=False))
            Challenges.query.get(3).value = 425
            Hints.query.get(1).requirements = {"prerequisites": [7]}
            db.session.add(QuestAudit(actor_id=1, action="challenge.update", detail={"challenge_id": 3,
                "fields": ["name", "flags"], "flags": [{"content": "audit-secret-flag"}],
                "password": "audit-secret-password", "email": "audit-secret@example.test"}))
            db.session.add(QuestAudit(actor_id=1, action="unknown.operation", detail={"reason": "unknown-secret"}))
            db.session.commit()
        data = self.capture()
        self.assertEqual([(p["id"], p["name"], p["role"]) for p in data["participants"]],
                         [(1, "Organizer", "administrator"), (2, "Alice", "participant"),
                          (3, "Bob", "maintainer"), (4, "Carol", "participant")])
        self.assertEqual(data["teams"][0]["members"], [2, 3])
        self.assertEqual([r["slug"] for r in data["rooms"]], ["openshift", "ansible", "rhel", "security", "ai"])
        self.assertEqual([s["challenge_id"] for s in data["rooms"][0]["steps"]], [1, 2])
        self.assertEqual([(c["id"], c["room_id"], c["value"]) for c in data["challenges"]],
                         [(1, data["rooms"][0]["id"], 100), (2, data["rooms"][0]["id"], 100), (3, None, 425)])
        self.assertEqual(data["challenges"][0]["flag_metadata"], [{"type": "static", "count": 1}])
        self.assertEqual(data["challenges"][1]["description"], "Private instructions 2")
        self.assertEqual(data["hints"][0]["content"], "Private hint")
        self.assertEqual(data["hints"][0]["prerequisites"], [7])
        self.assertEqual(data["attachments"][0]["name"], "private.txt")
        self.assertFalse(data["attachments"][0]["embedded"])
        self.assertEqual(data["audit"][-2]["detail"], {"challenge_id": 3, "fields": ["name", "flags"]})
        self.assertEqual(data["audit"][-1]["detail"], {})
        serialized = canonical_json(data)
        for secret in ("@example.test", "fixture-answer", "test-only", "audit-secret", "unknown-secret", "known-hash/"):
            self.assertNotIn(secret, serialized)
        for person in data["participants"]:
            self.assertTrue({"password", "email", "secret", "oauth_id", "ip"}.isdisjoint(person))

    def test_native_purchase_points_and_round_metadata_survive_collection(self):
        with self.app.app_context():
            Hints.query.get(1).cost = 25
            db.session.commit()
        self.begin()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        purchase = self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1})
        self.assertEqual(purchase.status_code, 200, purchase.get_data(as_text=True))
        with self.app.app_context():
            Hints.query.get(1).cost = 99
            db.session.add(Ratings(user_id=2, challenge_id=1, value=5, review="Useful exercise"))
            db.session.add(TeamComments(team_id=self.team_id, author_id=1, content="Team organizer note"))
            field = TeamFields(name="Track", field_type="text", description="Preferred track")
            db.session.add(field)
            db.session.flush()
            db.session.add(TeamFieldEntries(team_id=self.team_id, field_id=field.id, value="Automation"))
            db.session.add_all([Notifications(team_id=self.team_id, title="Team notice", content="Bring a laptop"),
                                Notifications(title="Round announcement", content="Lunch is ready")])
            db.session.commit()
        data = self.capture()
        team = next(team for team in data["results"]["teams"] if team["id"] == self.team_id)
        self.assertEqual((team["points"], team["hint_costs"]), (75, 25))
        self.assertEqual(data["hints"][0]["cost"], 99)
        self.assertEqual(data["hint_purchases"][0]["cost"], 25)
        self.assertEqual(data["hint_purchases"][0]["unlock_id"], data["unlocks"][0]["id"])
        self.assertEqual(data["hint_purchases"][0]["award_id"], data["awards"][0]["id"])
        self.assertEqual(data["awards"][0]["value"], -25)
        self.assertEqual(data["ratings"][0]["review"], "Useful exercise")
        self.assertEqual(data["team_comments"][0]["content"], "Team organizer note")
        self.assertEqual(data["team_fields"][0]["name"], "Track")
        self.assertEqual(data["team_field_entries"][0]["value"], "Automation")
        self.assertEqual([row["title"] for row in data["notifications"]], ["Team notice", "Round announcement"])
        self.assertTrue(data["attempts"][0]["credited"])
        self.assertFalse(data["attempts"][0]["recovery"])
        self.assertNotIn("fixture-answer", canonical_json(data))

    def test_microsecond_history_and_timeline_chronology_preserve_evidence(self):
        at = (datetime.utcnow() - timedelta(minutes=5)).replace(microsecond=123456)
        self.begin()
        with self.app.app_context():
            e = QuestEvent.query.get(1)
            e.start_at, e.end_at = at, at + timedelta(minutes=4)
            start = QuestStart.query.one()
            start.started_at = at
            db.session.add_all([
                Fails(user_id=2, team_id=self.team_id, challenge_id=1, date=at + timedelta(seconds=4),
                      provided="private-wrong-answer", ip="192.0.2.123"),
                Solves(user_id=2, team_id=self.team_id, challenge_id=1, date=at + timedelta(seconds=8),
                       provided="private-right-answer", ip="192.0.2.124"),
                QuestPause(started_at=at + timedelta(seconds=10), ended_at=at + timedelta(seconds=15)),
                QuestRoomPause(room_id=start.room_id, started_at=at + timedelta(seconds=11),
                               ended_at=at + timedelta(seconds=16), reason="Repair OpenShift lab"),
                QuestCorrection(team_id=self.team_id, room_id=start.room_id, adjustment_ms=-250,
                                reason="Clock reviewed", created_at=at + timedelta(seconds=20)),
                QuestExclusion(team_id=self.other_id, reason="Not competing"),
            ])
            db.session.commit()
        data = self.capture()
        self.assertTrue(data["event"]["start_at"].endswith(".123456Z"))
        self.assertEqual(data["starts"][0]["started_at"], data["event"]["start_at"])
        self.assertEqual([row["type"] for row in data["attempts"]], ["incorrect", "correct"])
        self.assertEqual([row["credited"] for row in data["attempts"]], [False, True])
        self.assertEqual(data["corrections"][0]["adjustment_ms"], -250)
        self.assertEqual(data["exclusions"][0]["reason"], "Not competing")
        self.assertEqual(data["room_pauses"][0]["reason"], "Repair OpenShift lab")
        self.assertTrue(data["room_pauses"][0]["started_at"].endswith(".123456Z"))
        dates = [entry["at"] for entry in data["timeline"]]
        self.assertEqual(dates, sorted(dates))
        types = {entry["type"] for entry in data["timeline"]}
        self.assertTrue({"attempt", "event.pause", "event.resume", "room.pause", "room.resume", "room.start", "ranking.correct"} <= types)
        self.assertEqual(len({entry["id"] for entry in data["timeline"]}), len(data["timeline"]))
        for secret in ("private-wrong-answer", "private-right-answer", "192.0.2."):
            self.assertNotIn(secret, canonical_json(data))

    def test_recovery_and_unban_count_one_earliest_eligible_attempt(self):
        self.begin()
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        with self.app.app_context():
            Users.query.get(2).banned = True
            db.session.commit()
        result = self.solve(self.bob, 1)
        self.assertEqual(result.json["data"]["status"], "correct", result.get_data(as_text=True))
        data = self.capture()
        self.assertEqual([row["type"] for row in data["attempts"]], ["correct", "quest_recovery"])
        self.assertEqual([row["credited"] for row in data["attempts"]], [False, True])
        self.assertEqual(data["attempts"][1]["original_solve_id"], data["attempts"][0]["id"])
        self.assertEqual(data["attempts"][1]["credited_user_id"], 3)
        self.assertEqual(len(data["scoring_credits"]), 1)
        with self.app.app_context():
            Users.query.get(2).banned = False
            db.session.commit()
        restored = self.capture()
        self.assertEqual([row["credited"] for row in restored["attempts"]], [True, False])
        self.assertEqual(len(restored["scoring_credits"]), 1)

    def test_published_legacy_snapshot_is_preserved_without_recalculation(self):
        self.begin()
        self.solve(self.alice, 1)
        with self.app.app_context():
            e = QuestEvent.query.get(1)
            snapshot = deepcopy(standings(e))
            snapshot.pop("scoring_version")
            snapshot["teams"][0]["rank"] = 7
            snapshot["legacy_measurement"] = 1.0
            e.snapshot, e.status = snapshot, "published"
            Challenges.query.get(1).value = 900
            db.session.commit()
        data = self.capture()
        self.assertEqual(data["event"]["snapshot"], snapshot)
        self.assertEqual(data["results"]["teams"], snapshot["teams"])
        self.assertEqual(data["results"]["scoring_version"], 1)
        self.assertEqual(data["results"]["legacy_measurement"], 1.0)
        self.assertEqual(data["challenges"][0]["value"], 900)

    def test_fingerprint_ignores_read_clocks_and_prior_archives_but_binds_hidden_flags(self):
        first = self.capture()
        fingerprint = source_fingerprint(first)
        changed = deepcopy(first)
        changed["results"]["as_of"] = "2040-01-01T00:00:00Z"
        changed["results"]["event"]["server_now"] = "2040-01-01T00:00:00Z"
        self.assertEqual(source_fingerprint(changed), fingerprint)
        with self.app.app_context():
            document = make_document(first)
            db.session.add(QuestAudit(actor_id=1, action="event.archive", detail={"summary": {"name": "prior"},
                           "document": document, "sha256": document["integrity"]["sha256"]}))
            db.session.commit()
        self.assertEqual(source_fingerprint(self.capture()), fingerprint)
        with self.app.app_context():
            Flags.query.first().content = "new-secret-flag-content"
            db.session.commit()
        after = self.capture()
        self.assertNotEqual(source_fingerprint(after), fingerprint)
        self.assertNotIn("new-secret-flag-content", canonical_json(after))
        after["event"]["name"] = "New event name"
        self.assertNotEqual(source_fingerprint(after), source_fingerprint(self.capture()))

    def test_document_integrity_and_export_metadata_do_not_mutate_source(self):
        data = self.capture()
        original = deepcopy(data)
        with patch.dict("os.environ", {"OTQ_PLUGIN_VERSION": "0.1.99"}):
            document = make_document(data)
        self.assertEqual(data, original)
        self.assertEqual(document["format"], "open-tech-quest/event-archive")
        self.assertEqual(document["schema_version"], 1)
        self.assertRegex(document["archive_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(document["exported_at"], r"\.\d{6}Z$")
        self.assertEqual(document["producer"]["plugin_version"], "0.1.99")
        integrity = document.pop("integrity")
        self.assertEqual(integrity, {"algorithm": "sha256", "sha256": hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()})
        document["data"]["event"]["name"] = "Tampered"
        self.assertNotEqual(integrity["sha256"], hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest())
        self.assertEqual(data, original)

    def test_registration_policy_and_safe_enabled_audit_values_are_preserved(self):
        response = self.post(self.admin, integration.PREFIX + "/admin/registration", {"enabled": False}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.app.app_context():
            for action in ("attendance.configure", "registration.configure"):
                db.session.add(QuestAudit(actor_id=1, action=action, detail={"enabled": False, "password": "discard-this-secret"}))
            db.session.commit()
        data = self.capture()
        self.assertEqual(data["registration"], {"enabled": False})
        self.assertEqual([row["detail"] for row in data["audit"][-2:]], [{"enabled": False}, {"enabled": False}])
        self.assertTrue(any(row["action"] == "registration.configure" and row["detail"].get("new") == "private" for row in data["audit"]))
        self.assertNotIn("discard-this-secret", canonical_json(data))
        previous = source_fingerprint(data)
        self.assertEqual(self.post(self.admin, integration.PREFIX + "/admin/registration", {"enabled": True}, "PATCH").status_code, 200)
        current = self.capture()
        self.assertEqual(current["registration"], {"enabled": True})
        self.assertNotEqual(source_fingerprint(current), previous)

    def test_authored_user_and_challenge_metadata_and_solution_files_are_preserved(self):
        at = (datetime.utcnow() - timedelta(minutes=5)).replace(microsecond=654321)
        with self.app.app_context():
            field = UserFields(name="Workshop track", field_type="text", description="Requested sessions")
            solution = Solutions(challenge_id=1, content="## Walkthrough\nDetailed authored solution", state="solved")
            topic = Topics(value="Containers")
            batch = QuestDemoBatch(active=True, challenge_ids=[3], rooms={"ansible": {"description": "Original room description"}}, created_at=at)
            db.session.add_all([field, solution, topic, batch])
            db.session.flush()
            db.session.add_all([
                UserFieldEntries(user_id=2, field_id=field.id, value={"choice": "Automation"}),
                UserComments(user_id=2, author_id=1, content="Participant organizer note", date=at),
                ChallengeComments(challenge_id=1, author_id=1, content="Challenge maintainer note", date=at),
                Tags(challenge_id=1, value="Beginner"),
                ChallengeTopics(challenge_id=1, topic_id=topic.id),
                QuestDemoChallenge(batch_id=batch.id, challenge_id=3),
                SolutionFiles(solution_id=solution.id, location="solution-hash/walkthrough.pdf", sha1sum="a" * 40),
                PageFiles(location="page-hash/not-an-event-attachment.pdf"),
            ])
            db.session.commit()
        data = self.capture()
        self.assertEqual(data["user_fields"][0]["name"], "Workshop track")
        self.assertEqual(data["user_field_entries"][0]["value"], {"choice": "Automation"})
        self.assertEqual(data["user_comments"][0]["content"], "Participant organizer note")
        self.assertTrue(data["user_comments"][0]["date"].endswith(".654321Z"))
        self.assertEqual(data["challenge_comments"][0]["content"], "Challenge maintainer note")
        self.assertEqual(data["solutions"][0]["state"], "solved")
        self.assertIn("Detailed authored solution", data["solutions"][0]["content"])
        self.assertEqual(data["tags"][0]["value"], "Beginner")
        self.assertEqual(data["topics"][0]["value"], "Containers")
        self.assertEqual(data["challenge_topics"][0]["topic_id"], data["topics"][0]["id"])
        self.assertEqual(data["demo_challenges"][0]["batch_id"], data["demo_batches"][0]["id"])
        self.assertEqual(data["demo_batches"][0]["rooms"]["ansible"]["description"], "Original room description")
        self.assertTrue(data["demo_batches"][0]["created_at"].endswith(".654321Z"))
        self.assertEqual([row["name"] for row in data["attachments"]], ["private.txt", "walkthrough.pdf"])
        attachment = data["attachments"][1]
        self.assertEqual((attachment["challenge_id"], attachment["solution_id"], attachment["sha1sum"]),
                         (1, data["solutions"][0]["id"], "a" * 40))
        for secret in ("alice@example.test", "test-only", "fixture-answer", "solution-hash/", "not-an-event-attachment"):
            self.assertNotIn(secret, canonical_json(data))

    def test_collector_does_not_select_native_auth_or_submission_secret_columns(self):
        statements = []
        with self.app.app_context():
            e = event(lock=True)
            def capture(connection, cursor, statement, parameters, context, executemany):
                statements.append(statement)
            sa_event.listen(db.engine, "before_cursor_execute", capture)
            try:
                collect_event(e)
            finally:
                sa_event.remove(db.engine, "before_cursor_execute", capture)
                db.session.rollback()
        for statement in statements:
            selected = statement.split("FROM", 1)[0].lower()
            for forbidden in ("users.email", "users.password", "users.secret", "teams.email", "teams.password",
                              "teams.secret", "submissions.provided", "submissions.ip", "flags.content", "flags.data"):
                self.assertNotIn(forbidden, selected, statement)


if __name__ == "__main__":
    unittest.main()
