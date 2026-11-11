"""Real native metadata and atomic, actionable room challenge deletion."""
import unittest
from unittest.mock import patch

from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table
from CTFd.models import (ChallengeComments, ChallengeFiles, ChallengeTopics, Challenges,
                         Fails, Files, Flags, Hints, SolutionFiles, Solutions, Tags, Topics, db)
from CTFd.plugins.open_tech_quest import authoring
from CTFd.plugins.open_tech_quest.models import (QuestAsset, QuestAudit, QuestChallengeSource, QuestStep)
import test_authoring
import test_integration as integration


class DeletionIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    path = test_authoring.AuthoringIntegration.path
    payload = test_authoring.AuthoringIntegration.payload
    setUp = test_authoring.AuthoringIntegration.setUp

    def test_native_malformed_challenge_requirements_block_without_server_error(self):
        malformed = {"prerequisites": [{"id": 1}]}
        response = self.post(self.admin, "/api/v1/challenges/3", {"requirements": malformed}, "PATCH")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        workspace = self.maintainer.get(self.path())
        self.assertEqual(workspace.status_code, 200)
        reason = workspace.json["data"]["challenges"][0]["delete_reason"]
        self.assertIn("challenge #3", reason)
        self.assertIn("prerequisite", reason)
        for path, body in ((self.path(1), {}), (self.path(), {"confirm": True})):
            failed = self.post(self.maintainer, path, body, "DELETE")
            self.assertEqual(failed.status_code, 409, failed.get_data(as_text=True))
            self.assertEqual(failed.json["error"], reason)
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(3).requirements, malformed)
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(QuestStep.query.count(), 2)
            self.assertEqual(QuestAudit.query.filter(QuestAudit.action.in_(["challenge.delete", "challenge.clear"])).count(), 0)

    def test_native_malformed_hint_requirements_block_hint_and_challenge_deletion(self):
        malformed = {"prerequisites": [[1]]}
        result = self.post(self.admin, "/api/v1/hints", {"challenge_id": 3,
            "content": "Unrelated native hint", "cost": 0, "requirements": malformed})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        hint_id = result.json["data"]["id"]
        for method, data in (("DELETE", {}), ("PATCH", {"hints": []})):
            failed = self.post(self.maintainer, self.path(2), data, method)
            self.assertEqual(failed.status_code, 409, failed.get_data(as_text=True))
            self.assertIn(f"hint #{hint_id}", failed.json["error"])
            self.assertIn("administrator", failed.json["error"])
        with self.app.app_context():
            self.assertEqual(Hints.query.get(hint_id).requirements, malformed)
            self.assertEqual(Hints.query.filter_by(challenge_id=2).count(), 1)
            self.assertIsNotNone(Challenges.query.get(1))

    def test_numeric_string_dependencies_name_the_native_owner(self):
        changed = self.post(self.admin, "/api/v1/challenges/3", {"requirements": {"prerequisites": ["1"]}}, "PATCH")
        self.assertEqual(changed.status_code, 200)
        denied = self.post(self.maintainer, self.path(1), {}, "DELETE")
        self.assertEqual(denied.status_code, 409)
        self.assertIn("challenge #3", denied.json["error"])
        self.assertIn("challenge #1", denied.json["error"])
        self.post(self.admin, "/api/v1/challenges/3", {"requirements": {"prerequisites": []}}, "PATCH")
        hint = self.post(self.admin, "/api/v1/hints", {"challenge_id": 3,
            "content": "Dependent hint", "cost": 0, "requirements": {"prerequisites": ["1"]}}).json["data"]
        denied = self.post(self.maintainer, self.path(2), {}, "DELETE")
        self.assertEqual(denied.status_code, 409)
        self.assertIn(f"hint #{hint['id']}", denied.json["error"])

    def test_workspace_unknown_dependency_matches_delete_and_inspects_once(self):
        with self.app.app_context():
            table = Table("deletion_external_test", MetaData(), Column("id", Integer, primary_key=True),
                          Column("challenge_id", Integer, ForeignKey(Challenges.id)))
            table.create(db.engine)
        try:
            with patch.object(authoring, "deletion_dependents", wraps=authoring.deletion_dependents) as inspect:
                workspace = self.maintainer.get(self.path())
                self.assertEqual(workspace.status_code, 200)
                self.assertEqual(inspect.call_count, 1)
            for row in workspace.json["data"]["challenges"]:
                self.assertFalse(row["can_delete"])
                self.assertIn("deletion_external_test", row["delete_reason"])
                failed = self.post(self.maintainer, self.path(row["id"]), {}, "DELETE")
                self.assertEqual(failed.status_code, 409)
                self.assertEqual(failed.json["error"], row["delete_reason"])
            with self.app.app_context():
                self.assertEqual(QuestStep.query.count(), 2)
                self.assertEqual(Challenges.query.count(), 3)
        finally:
            with self.app.app_context():
                db.session.rollback()
                table.drop(db.engine)

    def test_rich_middle_delete_removes_owned_metadata_and_relinks_without_storage_or_history_loss(self):
        created = self.post(self.maintainer, self.path(), self.payload()).json["data"]
        response = self.post(self.maintainer, self.path(2), {"source_key": "catalog/middle",
            "tags": ["Native tag"], "topics": ["Shared topic"],
            "solution": {"content": "Saved solution", "state": "solved"}}, "PATCH")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            solution = Solutions.query.filter_by(challenge_id=2).one()
            attachment = ChallengeFiles.query.filter_by(challenge_id=2).one()
            db.session.add(SolutionFiles(solution_id=solution.id, location="fixture/solution.txt"))
            db.session.add(ChallengeComments(challenge_id=2, content="A private author note", author_id=1))
            db.session.add(QuestAsset(file_id=attachment.id, challenge_id=2, kind="attachment", name="file.txt", size=1, sha256="0" * 64))
            db.session.commit()
            location = attachment.location
        result = self.post(self.maintainer, self.path(2), {}, "DELETE")
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(Challenges.query.get(2))
            self.assertIsNotNone(Challenges.query.get(3))
            for model in (Flags, Hints, Solutions, Tags, ChallengeTopics, ChallengeComments):
                self.assertEqual(model.query.filter_by(challenge_id=2).count(), 0)
            self.assertEqual(QuestAsset.query.count(), 0)
            self.assertEqual(QuestChallengeSource.query.count(), 0)
            self.assertEqual(Files.query.filter_by(location=location).count(), 0)
            self.assertEqual(SolutionFiles.query.count(), 0)
            self.assertEqual(Topics.query.filter_by(value="Shared topic").count(), 1)
            steps = QuestStep.query.order_by(QuestStep.position).all()
            self.assertEqual([(s.challenge_id, s.position) for s in steps], [(1, 0), (created["id"], 1)])
            self.assertEqual(Challenges.query.get(1).next_id, created["id"])
            self.assertEqual(Challenges.query.get(created["id"]).requirements["prerequisites"], [1])
            self.assertEqual(QuestAudit.query.filter_by(action="challenge.delete").count(), 1)
        self.assertNotIn(2, [c["id"] for c in self.maintainer.get(self.path()).json["data"]["challenges"]])

    def test_unused_step_cannot_be_deleted_when_another_step_has_history(self):
        with self.app.app_context():
            db.session.add(Fails(challenge_id=1, user_id=2, team_id=self.team_id, provided="fixture-only"))
            db.session.commit()
        row = self.maintainer.get(self.path()).json["data"]["challenges"][1]
        self.assertFalse(row["can_delete"])
        self.assertIn("submissions", row["delete_reason"])
        denied = self.post(self.maintainer, self.path(2), {}, "DELETE")
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(denied.json["error"], row["delete_reason"])
        with self.app.app_context():
            self.assertEqual(QuestStep.query.count(), 2)
            self.assertEqual(Fails.query.count(), 1)
            self.assertEqual(QuestAudit.query.filter(QuestAudit.action.in_(["challenge.delete", "challenge.clear"])).count(), 0)
