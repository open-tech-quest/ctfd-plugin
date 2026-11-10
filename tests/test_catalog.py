"""Maintainer automation with real native tokens, storage and transaction guards."""
import base64
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import request
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table
from CTFd.models import (ChallengeFiles, Challenges, Fails, Flags, Hints, Ratings,
                         SolutionFiles, Solutions, Teams, UserTokens, Users, db)
from CTFd.utils.security.auth import generate_user_token
from CTFd.plugins.open_tech_quest import authoring
from CTFd.plugins.open_tech_quest.archive import collect_event
from CTFd.plugins.open_tech_quest.models import (QuestAsset, QuestChallengeSource,
    QuestEvent, QuestMaintainer, QuestRoom, QuestStep)
from CTFd.plugins.open_tech_quest.service import event
import test_authoring
import test_integration as integration


class CatalogIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve
    path = test_authoring.AuthoringIntegration.path
    payload = test_authoring.AuthoringIntegration.payload

    def setUp(self):
        test_authoring.AuthoringIntegration.setUp(self)
        with self.app.app_context():
            self.token = generate_user_token(Users.query.get(5)).value
        self.token_client = self.app.test_client()

    def token_request(self, path, data=None, method="GET", token=None):
        return self.token_client.open(path, method=method, json=data,
            headers={"Authorization": "Token " + (token or self.token), "Content-Type": "application/json"})

    def assets_path(self, challenge_id=1):
        return self.path(challenge_id) + "/assets"

    def upload(self, challenge_id=1, kind="inline", name="example.png", content=b"\x89PNG\r\n\x1a\nfixture"):
        return self.token_request(self.assets_path(challenge_id), {"kind": kind, "name": name,
            "content_base64": base64.b64encode(content).decode()}, "POST")

    def test_capabilities_and_complete_standard_metadata_with_explicit_identity(self):
        capabilities = self.token_request(integration.PREFIX + "/manage/capabilities")
        self.assertEqual(capabilities.status_code, 200)
        self.assertEqual(capabilities.json["data"]["automation_version"], 1)
        data = self.payload(source_key="openshift/catalog-one", attribution="Catalog authors",
            connection_info="https://lab.example.test", max_attempts=12, tags=["Beginner", "Containers"],
            topics=["Automation", "Linux"], solution={"content": "# Worked solution", "state": "solved"})
        result = self.token_request(self.path(), data, "POST")
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        challenge = result.json["data"]
        for field in ("source_key", "attribution", "connection_info", "max_attempts", "tags", "topics"):
            self.assertEqual(challenge[field], data[field])
        self.assertGreater(challenge["solution"]["id"], 0)
        self.assertEqual(challenge["solution"]["content"], "# Worked solution")
        self.assertEqual(result.headers["Cache-Control"], "private, no-store")
        again = self.token_request(self.path(), data, "POST")
        self.assertEqual(again.status_code, 409)
        workspace = self.token_request(self.path()).json["data"]["challenges"]
        self.assertEqual(len(workspace), 3)
        self.assertEqual(workspace[-1]["source_key"], data["source_key"])
        with self.app.app_context():
            self.assertEqual(QuestChallengeSource.query.count(), 1)
        public = self.guest.get(integration.PREFIX + "/bootstrap").get_data(as_text=True)
        self.assertNotIn("Worked solution", public)
        self.assertNotIn("fixture-Exact-Answer", public)

    def test_source_adoption_is_explicit_immutable_and_scoped_to_room(self):
        self.assertIsNone(self.token_request(self.path()).json["data"]["challenges"][0]["source_key"])
        self.assertEqual(self.token_request(self.path(1), {"source_key": "catalog/one"}, "PATCH").status_code, 200)
        self.assertEqual(self.token_request(self.path(1), {"source_key": "catalog/one"}, "PATCH").status_code, 200)
        self.assertEqual(self.token_request(self.path(1), {"source_key": "catalog/changed"}, "PATCH").status_code, 409)
        self.assertEqual(self.token_request(self.path(2), {"source_key": "catalog/one"}, "PATCH").status_code, 409)
        self.assertEqual(self.post(self.admin, integration.PREFIX + "/admin/rooms/ansible", {"challenge_ids": [3]}, "PATCH").status_code, 200)
        self.assertEqual(self.token_request(self.path(3, slug="ansible"), {"source_key": "catalog/one"}, "PATCH").status_code, 200)

    def test_unmapped_catalog_identity_can_be_found_and_explicitly_reattached(self):
        self.token_request(self.path(1), {"source_key": "catalog/stable"}, "PATCH")
        self.assertEqual(self.post(self.maintainer, integration.PREFIX + "/admin/rooms/openshift", {"challenge_ids": [2]}, "PATCH").status_code, 200)
        workspace = self.token_request(self.path()).json["data"]
        self.assertEqual([row["id"] for row in workspace["challenges"]], [2])
        self.assertEqual([(row["id"], row["source_key"], row["mapped"]) for row in workspace["unmapped_catalog"]], [(1, "catalog/stable", False)])
        self.assertFalse(workspace["unmapped_catalog"][0]["can_automate"])
        self.assertTrue(workspace["unmapped_catalog"][0]["can_reattach"])
        self.assertIsNone(workspace["unmapped_catalog"][0]["reattach_reason"])
        self.assertEqual(self.token_request(self.path(1), {"name": "Not yet"}, "PATCH").status_code, 404)
        self.assertEqual(self.token_request(self.path(slug="ansible")).json["data"]["unmapped_catalog"], [])
        self.assertEqual(self.token_request(self.path(), self.payload(source_key="catalog/stable"), "POST").status_code, 409)
        self.assertEqual(self.post(self.maintainer, integration.PREFIX + "/admin/rooms/openshift", {"challenge_ids": [2, 1]}, "PATCH").status_code, 200)
        workspace = self.token_request(self.path()).json["data"]
        self.assertEqual(workspace["unmapped_catalog"], [])
        self.assertEqual((workspace["challenges"][-1]["id"], workspace["challenges"][-1]["source_key"]), (1, "catalog/stable"))
        self.assertTrue(workspace["challenges"][-1]["can_automate"])

    def test_unmapped_catalog_history_is_not_safe_to_reattach(self):
        self.token_request(self.path(1), {"source_key": "catalog/stable"}, "PATCH")
        self.assertEqual(self.post(self.maintainer, integration.PREFIX + "/admin/rooms/openshift", {"challenge_ids": [2]}, "PATCH").status_code, 200)
        with self.app.app_context():
            db.session.add(Fails(challenge_id=1, user_id=2, team_id=self.team_id, provided="disposable-history"))
            db.session.commit()
        workspace = self.token_request(self.path()).json["data"]
        candidate = workspace["unmapped_catalog"][0]
        self.assertFalse(candidate["can_reattach"])
        self.assertIn("submissions", candidate["reattach_reason"])
        self.assertEqual(self.post(self.maintainer, integration.PREFIX + "/admin/rooms/openshift", {"challenge_ids": [2, 1]}, "PATCH").status_code, 409)
        self.assertEqual([row["id"] for row in self.token_request(self.path()).json["data"]["challenges"]], [2])
        with self.app.app_context():
            self.assertEqual(Fails.query.count(), 1)
            self.assertEqual(QuestChallengeSource.query.get(1).source_key, "catalog/stable")

    def test_native_regex_flags_preserve_options_full_match_and_simple_editor_readonly(self):
        native = [{"type": "regex", "content": "quest-[0-9]+", "data": "case_sensitive"}]
        result = self.token_request(self.path(1), {"flags": native}, "PATCH")
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(result.json["data"]["native_flags"], native)
        self.assertFalse(result.json["data"]["can_edit"])
        self.assertEqual(result.json["data"]["flags"], [])
        self.assertTrue(result.json["data"]["can_automate"])
        for submission, expected in (("quest-42", "correct"), ("QUEST-42", "incorrect"), ("quest-42-tail", "incorrect")):
            preview = self.token_request(self.path(1, preview=True), {"submission": submission}, "POST")
            self.assertEqual(preview.json["data"]["status"], expected)
        native[0]["data"] = "case_insensitive"
        self.assertEqual(self.token_request(self.path(1), {"flags": native}, "PATCH").status_code, 200)
        self.assertEqual(self.token_request(self.path(1, preview=True), {"submission": "QUEST-42"}, "POST").json["data"]["status"], "correct")
        for invalid in ({"type": "regex", "content": "[", "data": None},
                        {"type": "custom", "content": "text", "data": None},
                        {"type": "static", "content": "text", "data": {}},
                        {"type": "static", "content": "text", "data": "x" * 65}):
            self.assertEqual(self.token_request(self.path(1), {"flags": [invalid]}, "PATCH").status_code, 400)
        self.action("start")
        self.start()
        response = self.post(self.alice, "/api/v1/challenges/attempt", {"challenge_id": 1, "submission": "QUEST-42"})
        self.assertEqual(response.json["data"]["status"], "correct")

    def test_invalid_extended_values_do_not_partially_change_content(self):
        cases = [{"max_attempts": True}, {"max_attempts": -1}, {"max_attempts": 1.5},
                 {"tags": ["x", "x"]}, {"topics": ["a" * 256]}, {"attribution": None},
                 {"connection_info": "a\x00b"}, {"source_key": "Name With Spaces"},
                 {"solution": {"content": "Text", "state": "public"}},
                 {"solution": {"content": "Text", "state": "solved", "id": 1}}]
        for case in cases:
            with self.subTest(case=case):
                response = self.token_request(self.path(1), dict(case, name="Should not save"), "PATCH")
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(1).name, "Challenge 1")
            self.assertIsNone(QuestChallengeSource.query.get(1))

    def test_upload_repeat_replacement_and_owned_preview_preserve_legacy_files(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        asset = response.json["data"]
        self.assertTrue(asset["managed"])
        self.assertEqual(self.upload().json["data"], asset)
        preview = self.token_request(asset["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.content_type.startswith("image/png"))
        self.assertEqual(self.maintainer.get(asset["url"]).status_code, 200)
        self.assertEqual(self.guest.get(asset["url"]).status_code, 401)
        replaced = self.upload(content=b"\x89PNG\r\n\x1a\nchanged").json["data"]
        self.assertEqual(replaced["id"], asset["id"])
        self.assertNotEqual(replaced["url"], asset["url"])
        self.assertNotEqual(replaced["sha256"], asset["sha256"])
        listing = self.token_request(self.assets_path(2)).json["data"]["assets"]
        self.assertEqual(len(listing), 1)
        self.assertFalse(listing[0]["managed"])
        self.assertEqual(self.token_request(self.assets_path(2) + "/" + str(listing[0]["id"]), {}, "DELETE").status_code, 409)
        self.assertEqual(self.token_request(self.assets_path(2) + "/" + str(asset["id"]), {}, "DELETE").status_code, 404)

    def test_inline_hidden_from_download_list_and_solution_file_needs_earned_access(self):
        inline = self.upload().json["data"]
        attachment = self.upload(kind="attachment", name="instructions.txt", content=b"Lab attachment").json["data"]
        self.assertEqual(self.token_request(self.path(1), {"solution": {"content": "Worked solution", "state": "solved"}}, "PATCH").status_code, 200)
        solution = self.upload(kind="solution", name="writeup.txt", content=b"Private walkthrough").json["data"]
        self.action("start")
        self.start()
        detail = self.alice.get("/api/v1/challenges/1").json["data"]
        self.assertFalse(any(inline["url"] in value for value in detail["files"]))
        self.assertTrue(any(attachment["url"] in value for value in detail["files"]))
        opened = self.alice.get(inline["url"])
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.headers["Cache-Control"], "private, no-store")
        self.assertEqual(opened.headers["X-Content-Type-Options"], "nosniff")
        opened.close()
        self.assertEqual(self.alice.get(solution["url"]).status_code, 403)
        self.assertEqual(self.solve(self.alice, 1).json["data"]["status"], "correct")
        self.assertEqual(self.alice.get(solution["url"]).status_code, 403)
        sid = self.token_request(self.path()).json["data"]["challenges"][0]["solution"]["id"]
        self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": "solutions", "target": sid}).status_code, 200)
        self.assertEqual(self.alice.get(solution["url"]).status_code, 200)
        self.assertEqual(self.carol.get(solution["url"]).status_code, 403)
        self.assertEqual(self.upload().status_code, 409)

    def test_asset_validation_path_ownership_and_session_csrf(self):
        for kind, name, content in (("inline", "bad.svg", b"<svg></svg>"),
                                    ("inline", "bad.png", b"<html>no</html>"),
                                    ("attachment", "../other.txt", b"text")):
            self.assertEqual(self.upload(kind=kind, name=name, content=content).status_code, 400)
        self.assertEqual(self.upload(kind="solution").status_code, 409)
        self.assertEqual(self.token_request(self.assets_path(), {"kind": "inline", "name": "x.png", "content_base64": "!"}, "POST").status_code, 400)
        payload = {"kind": "attachment", "name": "file.txt", "content_base64": base64.b64encode(b"hello").decode()}
        self.assertEqual(self.maintainer.post(self.assets_path(), json=payload).status_code, 403)
        self.assertEqual(self.post(self.maintainer, self.assets_path(), payload).status_code, 200)
        self.assertEqual(self.maintainer.post(self.assets_path(), data=payload, headers={"Authorization": "Token " + self.token}).status_code, 400)
        with self.app.app_context():
            self.assertEqual(QuestAsset.query.count(), 1)

    def test_postlock_revoked_token_demoted_or_banned_actor_cannot_upload(self):
        for mutation in ("token", "role", "banned", "password"):
            with self.subTest(mutation=mutation):
                with self.app.app_context():
                    user = Users.query.get(5)
                    user.banned = False
                    if not QuestMaintainer.query.get(5):
                        db.session.add(QuestMaintainer(user_id=5, previous_hidden=False))
                    db.session.commit()
                    self.token = generate_user_token(user).value
                entered, release = threading.Event(), threading.Event()
                original = authoring.event
                def gate(lock=False):
                    if lock:
                        entered.set()
                        if not release.wait(4):
                            raise RuntimeError("Test gate not released")
                    return original(lock=lock)
                with patch.object(authoring, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(self.upload)
                    try:
                        self.assertTrue(entered.wait(4))
                        with self.app.app_context():
                            if mutation == "token":
                                UserTokens.query.filter_by(value=self.token).delete()
                            elif mutation == "role":
                                db.session.delete(QuestMaintainer.query.get(5))
                            elif mutation == "banned":
                                Users.query.get(5).banned = True
                            else:
                                Users.query.get(5).password = "changed-test-password"
                            db.session.commit()
                    finally:
                        release.set()
                    self.assertIn(pending.result().status_code, (401, 403))
                with self.app.app_context():
                    self.assertEqual(QuestAsset.query.count(), 0)

    def test_delete_and_failed_upload_leave_other_files_and_native_rows_safe(self):
        first = self.upload().json["data"]
        with self.app.app_context():
            file = ChallengeFiles.query.get(first["id"])
            location = file.location
            directory = Path(self.app.config["UPLOAD_FOLDER"]) / location.rsplit("/", 1)[0]
            sibling = directory / "unrelated.txt"
            sibling.write_bytes(b"Unrelated native bytes")
        with patch("CTFd.plugins.open_tech_quest.assets.db.session.commit", side_effect=RuntimeError("rollback probe")):
            with self.assertRaises(RuntimeError):
                self.upload(name="new.png")
        with self.app.app_context():
            db.session.rollback()
            self.assertEqual(QuestAsset.query.count(), 1)
        self.assertEqual(self.token_request(self.assets_path() + "/" + str(first["id"]), {}, "DELETE").status_code, 200)
        self.assertTrue(sibling.exists())
        self.assertTrue((Path(self.app.config["UPLOAD_FOLDER"]) / location).exists())
        self.assertEqual(self.maintainer.get(first["url"]).status_code, 404)

    def test_atomic_clear_refuses_all_external_dependencies_and_preserves_category_content(self):
        with self.app.app_context():
            Challenges.query.get(3).category = "OpenShift"
            Challenges.query.get(3).requirements = {"prerequisites": ["1"]}
            db.session.commit()
        self.assertEqual(self.token_request(self.path(), {"confirm": True}, "DELETE").status_code, 409)
        with self.app.app_context():
            self.assertEqual(QuestStep.query.count(), 2)
            self.assertEqual(Challenges.query.count(), 3)
            Challenges.query.get(3).requirements = None
            db.session.add(Hints(challenge_id=3, content="Unrelated hint", requirements={"prerequisites": ["1"]}))
            db.session.commit()
        self.assertEqual(self.token_request(self.path(), {"confirm": True}, "DELETE").status_code, 409)
        with self.app.app_context():
            Hints.query.filter_by(challenge_id=3).delete()
            db.session.commit()
        self.assertEqual(self.token_request(self.path(), {"confirm": 1}, "DELETE").status_code, 400)
        cleared = self.token_request(self.path(), {"confirm": True}, "DELETE")
        self.assertEqual(cleared.status_code, 200, cleared.get_data(as_text=True))
        self.assertEqual(cleared.json["data"]["deleted_ids"], [1, 2])
        with self.app.app_context():
            self.assertEqual([c.id for c in Challenges.query.all()], [3])
            self.assertIsNotNone(QuestRoom.query.filter_by(slug="openshift").first())

    def test_clear_room_rolls_back_mapping_sources_assets_and_refuses_unknown_dependents(self):
        self.token_request(self.path(1), {"source_key": "catalog/a"}, "PATCH")
        self.upload()
        with patch.object(authoring, "remove_challenges", side_effect=RuntimeError("transaction probe")):
            with self.assertRaises(RuntimeError):
                self.token_request(self.path(), {"confirm": True}, "DELETE")
        with self.app.app_context():
            db.session.rollback()
            self.assertEqual(QuestStep.query.count(), 2)
            self.assertEqual(QuestChallengeSource.query.count(), 1)
            self.assertEqual(QuestAsset.query.count(), 1)
            table = Table("catalog_external_test", MetaData(), Column("id", Integer, primary_key=True),
                          Column("challenge_id", Integer, ForeignKey(Challenges.id)))
            table.create(db.engine)
        try:
            self.assertEqual(self.token_request(self.path(), {"confirm": True}, "DELETE").status_code, 409)
        finally:
            with self.app.app_context():
                table.drop(db.engine)

    def test_upload_waiting_for_clear_rechecks_mapping_before_writing_bytes(self):
        entered, release = threading.Event(), threading.Event()
        original = authoring.event
        def gate(lock=False):
            if lock and request.path == self.assets_path():
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("Test gate not released")
            return original(lock=lock)
        with patch.object(authoring, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.upload)
            try:
                self.assertTrue(entered.wait(4))
                cleared = self.post(self.maintainer, self.path(), {"confirm": True}, "DELETE")
                self.assertEqual(cleared.status_code, 200, cleared.get_data(as_text=True))
            finally:
                release.set()
            self.assertEqual(pending.result().status_code, 404)
        with self.app.app_context():
            self.assertEqual(QuestAsset.query.count(), 0)
            self.assertEqual(QuestStep.query.count(), 0)
            self.assertEqual([row.id for row in Challenges.query.all()], [3])

    def test_catalog_migration_adds_empty_tables_idempotently_to_populated_native_schema(self):
        migration = importlib.import_module("CTFd.plugins.open_tech_quest.migrations.0011_catalog")
        with self.app.app_context():
            room_id = QuestRoom.query.filter_by(slug="openshift").one().id
            with db.engine.begin() as connection:
                QuestAsset.__table__.drop(connection)
                QuestChallengeSource.__table__.drop(connection)
                operations = Operations(MigrationContext.configure(connection))
                migration.upgrade(operations)
                migration.upgrade(operations)
            # DDL used its own startup connection; discard the test session's
            # pre-DDL RR snapshot before inspecting the newly created tables.
            db.session.rollback()
            self.assertEqual(Users.query.count(), 5)
            self.assertEqual(Challenges.query.count(), 3)
            self.assertEqual(QuestAsset.query.count(), 0)
            db.session.add(QuestChallengeSource(room_id=room_id, challenge_id=1, source_key="migration/existing"))
            db.session.commit()
        self.assertEqual(self.token_request(self.path()).json["data"]["challenges"][0]["source_key"], "migration/existing")

    def test_archive_and_both_reset_modes_preserve_then_clear_catalog_identity(self):
        self.token_request(self.path(1), {"source_key": "catalog/a"}, "PATCH")
        self.upload()
        with self.app.app_context():
            source = collect_event(event(lock=True))
            self.assertEqual(source["challenge_sources"][0]["source_key"], "catalog/a")
            self.assertEqual(source["managed_assets"][0]["kind"], "inline")
            db.session.rollback()
        for mode in ("new_round", "defaults"):
            preview = self.admin.get(integration.PREFIX + "/admin/round/preview?mode=" + mode).json["data"]
            self.assertTrue(preview["allowed"], preview["reason"])
            response = self.post(self.admin, integration.PREFIX + "/admin/round/reset", {"revision": preview["revision"],
                "mode": mode, "confirm": True, "event_name": preview["event_name"], "reason": "Catalog integration reset"})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            with self.app.app_context():
                self.assertEqual(QuestChallengeSource.query.count(), 1 if mode == "new_round" else 0)
                self.assertEqual(QuestAsset.query.count(), 1 if mode == "new_round" else 0)

    def test_asset_and_solution_deletion_refuse_unknown_transitive_plugin_data(self):
        asset = self.upload().json["data"]
        self.assertEqual(self.token_request(self.path(1), {"solution": {"content": "Writeup", "state": "solved"}}, "PATCH").status_code, 200)
        with self.app.app_context():
            sid = Solutions.query.filter_by(challenge_id=1).one().id
            metadata = MetaData()
            file_table = Table("catalog_file_dependency_test", metadata, Column("id", Integer, primary_key=True),
                               Column("file_id", Integer, ForeignKey(ChallengeFiles.id, ondelete="CASCADE")))
            solution_table = Table("catalog_solution_dependency_test", metadata, Column("id", Integer, primary_key=True),
                                   Column("solution_id", Integer, ForeignKey(Solutions.id, ondelete="CASCADE")))
            metadata.create_all(db.engine)
            db.session.execute(file_table.insert().values(id=1, file_id=asset["id"]))
            db.session.execute(solution_table.insert().values(id=1, solution_id=sid))
            db.session.commit()
        try:
            self.assertEqual(self.token_request(self.assets_path() + "/" + str(asset["id"]), {}, "DELETE").status_code, 409)
            self.assertEqual(self.token_request(self.path(1), {"solution": None}, "PATCH").status_code, 409)
            with self.app.app_context():
                self.assertEqual(len(db.session.execute(file_table.select()).fetchall()), 1)
                self.assertIsNotNone(Solutions.query.get(sid))
                self.assertIsNotNone(QuestAsset.query.get(asset["id"]))
        finally:
            with self.app.app_context():
                db.session.rollback()
                metadata.drop_all(db.engine)

    def test_solution_removal_clears_only_its_owned_file_metadata(self):
        self.token_request(self.path(1), {"solution": {"content": "Writeup", "state": "solved"}}, "PATCH")
        solution_asset = self.upload(kind="solution", name="proof.txt", content=b"Worked explanation").json["data"]
        inline = self.upload().json["data"]
        self.assertEqual(self.token_request(self.path(1), {"solution": None}, "PATCH").status_code, 200)
        self.assertEqual([row["id"] for row in self.token_request(self.assets_path()).json["data"]["assets"]], [inline["id"]])
        with self.app.app_context():
            self.assertIsNone(QuestAsset.query.get(solution_asset["id"]))
            self.assertIsNone(SolutionFiles.query.get(solution_asset["id"]))
            self.assertIsNone(Solutions.query.filter_by(challenge_id=1).first())
