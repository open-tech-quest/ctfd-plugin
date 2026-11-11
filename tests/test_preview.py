"""Stateless staff room preview against pinned native CTFd."""
import hashlib
import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import test_authoring as authoring_tests
import test_integration as integration
from CTFd.models import (ChallengeFiles, Challenges, Flags, Hints, PageFiles, SolutionFiles,
                         Solutions, UserTokens, Users, db)
from CTFd.plugins.open_tech_quest import archive, authoring, assets, preview
from CTFd.plugins.open_tech_quest.models import QuestAsset, QuestEvent, QuestMaintainer, QuestRoom, QuestStep
from CTFd.utils.security.auth import generate_user_token

PREFIX = integration.PREFIX + "/manage/preview"


class PreviewIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    setUp = authoring_tests.AuthoringIntegration.setUp

    def data(self, client=None):
        response = (client or self.maintainer).get(PREFIX)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json["data"]

    def grade(self, answer="fixture-answer-1", cid=1, slug="openshift", revision=None, client=None):
        headers = {"CSRF-Token": "quest-test-csrf"}
        if revision is not None:
            headers["X-Quest-Preview-Revision"] = revision
        return (client or self.maintainer).post(
            integration.PREFIX + f"/manage/rooms/{slug}/challenges/{cid}/preview",
            json={"submission": answer}, headers=headers)

    def domain(self):
        with self.app.app_context():
            db.session.remove()
            return {table.name: hashlib.sha256(json.dumps(sorted(repr(tuple(row))
                    for row in db.session.execute(table.select()))).encode()).hexdigest()
                    for table in db.metadata.sorted_tables if table.name != "tracking"}

    def test_private_sanitized_content_and_grades_never_write_competition_or_archive(self):
        before = self.domain()
        with self.app.app_context():
            self.assertNotIn("quest_playtests", db.metadata.tables)
            fingerprint = archive.source_fingerprint(archive.collect_event(QuestEvent.query.get(1)))
        for client, expected in ((self.guest, 401), (self.alice, 403)):
            self.assertEqual(client.get(PREFIX).status_code, expected)
        for client in (self.admin, self.maintainer):
            data = self.data(client)
            encoded = json.dumps(data)
            self.assertNotIn("fixture-answer", encoded)
            self.assertNotIn("@example", encoded)
            self.assertEqual(set(data), {"revision", "rooms", "challenges"})
            self.assertEqual([s["id"] for s in data["rooms"][0]["steps"]], [1, 2])
            self.assertEqual(data["challenges"][1]["hints"][0]["content"], "Private hint")
            self.assertEqual(self.grade(client=client).json["data"]["status"], "correct")
            self.assertEqual(self.grade("wrong", client=client).json["data"]["status"], "incorrect")
        self.assertEqual(self.domain(), before)
        with self.app.app_context():
            self.assertEqual(archive.source_fingerprint(archive.collect_event(QuestEvent.query.get(1))), fingerprint)

    def test_native_regex_and_legacy_preview_body_csrf_and_unsupported_content(self):
        with self.app.app_context():
            flag = Flags.query.filter_by(challenge_id=1).one()
            flag.type, flag.content, flag.data = "regex", "TEST-[0-9]{2}", "case_insensitive"
            db.session.commit()
        self.assertEqual(self.grade("  test-12  ").json["data"]["status"], "correct")
        self.assertEqual(self.grade("test-12-extra").json["data"]["status"], "incorrect")
        path = integration.PREFIX + "/manage/rooms/openshift/challenges/1/preview"
        self.assertEqual(self.maintainer.post(path, json={"submission": "test-12"}).status_code, 403)
        self.assertEqual(self.post(self.maintainer, path, {"submission": "test-12", "revision": "bad"}).status_code, 400)
        self.assertEqual(self.grade(revision="bad").status_code, 400)
        self.assertEqual(self.grade(cid=3).status_code, 404)
        self.assertEqual(self.grade(client=self.alice).status_code, 403)
        with self.app.app_context():
            Challenges.query.get(1).logic = "all"
            Challenges.query.get(2).state = "hidden"
            db.session.commit()
        data = self.data()
        self.assertIsNotNone(data["challenges"][0]["unsupported_reason"])
        self.assertEqual(data["rooms"][0]["steps"][1]["visibility"], "hidden")
        self.assertEqual(self.grade().status_code, 409)
        self.assertEqual(self.post(self.maintainer, "/api/v1/challenges/attempt",
                                  {"challenge_id": 1, "submission": "test-12"}).status_code, 403)

    def test_per_room_revision_detects_saved_flags_and_content_without_resetting_other_rooms(self):
        with self.app.app_context():
            room = QuestRoom.query.filter_by(slug="ansible").one()
            db.session.add(QuestStep(room_id=room.id, challenge_id=3, position=0))
            db.session.commit()
        first = self.data()
        revisions = {r["slug"]: r["revision"] for r in first["rooms"]}
        self.assertEqual(first, self.data())
        self.assertEqual(self.grade(revision=revisions["openshift"]).status_code, 200)
        with self.app.app_context():
            Flags.query.filter_by(challenge_id=1).one().content = "changed-private-flag"
            Hints.query.get(1).content = "Saved hint changes"
            db.session.commit()
        second = self.data()
        updated = {r["slug"]: r["revision"] for r in second["rooms"]}
        self.assertNotEqual(updated["openshift"], revisions["openshift"])
        self.assertEqual(updated["ansible"], revisions["ansible"])
        self.assertEqual(self.grade(revision=revisions["openshift"]).status_code, 409)
        self.assertEqual(self.grade("changed-private-flag", revision=updated["openshift"]).json["data"]["status"], "correct")
        self.assertNotIn("changed-private-flag", json.dumps(second))

    def test_queued_grade_rechecks_token_role_ban_password_and_saved_revision(self):
        for mutation in ("token", "role", "banned", "password", "content"):
            with self.subTest(mutation=mutation):
                with self.app.app_context():
                    user = Users.query.get(5)
                    user.banned, user.password = False, "test-only"
                    if not QuestMaintainer.query.get(5):
                        db.session.add(QuestMaintainer(user_id=5, previous_hidden=False))
                    db.session.commit()
                    token = generate_user_token(user).value
                browser = self.app.test_client()
                headers = {"Authorization": "Token " + token, "Content-Type": "application/json"}
                data = browser.get(PREFIX, headers=headers).json["data"]
                headers["X-Quest-Preview-Revision"] = data["rooms"][0]["revision"]
                entered, release = threading.Event(), threading.Event()
                original = authoring.event
                def gate(lock=False):
                    if lock:
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("Preview gate not released")
                    return original(lock=lock)
                with patch.object(authoring, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(lambda: browser.post(integration.PREFIX + "/manage/rooms/openshift/challenges/1/preview",
                                                               json={"submission": "fixture-answer-1"}, headers=headers))
                    try:
                        self.assertTrue(entered.wait(5))
                        with self.app.app_context():
                            if mutation == "token":
                                UserTokens.query.filter_by(value=token).delete()
                            elif mutation == "role":
                                db.session.delete(QuestMaintainer.query.get(5))
                            elif mutation == "banned":
                                Users.query.get(5).banned = True
                            elif mutation == "password":
                                Users.query.get(5).password = "changed-while-waiting"
                            else:
                                Flags.query.filter_by(challenge_id=1).one().content = "new-saved-flag"
                            db.session.commit()
                    finally:
                        release.set()
                    response = pending.result()
                self.assertEqual(response.status_code, 409 if mutation == "content" else 401 if mutation == "token" else 403,
                                 response.get_data(as_text=True))

    def test_scoped_native_files_and_markdown_keep_staff_authority_and_external_links(self):
        with self.app.app_context():
            solution = Solutions(challenge_id=1, state="hidden", content="Private solution")
            db.session.add(solution)
            db.session.flush()
            file = SolutionFiles(solution_id=solution.id, location="safe/solution.png")
            unrelated = ChallengeFiles(challenge_id=3, location="other/unmapped.txt")
            page = PageFiles(location="pages/page.txt")
            db.session.add_all([file, unrelated, page])
            db.session.flush()
            fid, unrelated_id, page_id = file.id, unrelated.id, page.id
            Challenges.query.get(1).description = "![pic](/files/safe/solution.png) <a href='http://localhost/files/safe/solution.png?token=secret'>native</a> ![external](https://github.com/user-attachments/files/123/diagram.png) ![cdn](//cdn.test/files/example.png)"
            db.session.commit()
            path = Path(self.app.config["UPLOAD_FOLDER"]) / file.location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-fixture")
        source = self.data()
        text = source["challenges"][0]["description"]
        self.assertEqual(text.count(f"/manage/preview/files/{fid}/solution.png"), 2)
        self.assertNotIn("token=", text)
        self.assertIn("https://github.com/user-attachments/files/123/diagram.png", text)
        self.assertIn("//cdn.test/files/example.png", text)
        url = PREFIX + f"/files/{fid}/solution.png"
        response = self.maintainer.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        response.close()
        for client, status in ((self.guest, 401), (self.alice, 403)):
            self.assertEqual(client.get(url).status_code, status)
        self.assertEqual(self.maintainer.get(PREFIX + f"/files/{unrelated_id}/unmapped.txt").status_code, 404)
        self.assertEqual(self.maintainer.get(PREFIX + f"/files/{page_id}/page.txt").status_code, 404)

    def test_legacy_large_and_native_s3_files_stream_without_local_materialization(self):
        from CTFd.utils.uploads.uploaders import S3Uploader
        payload = b"large-preview-content-" * 300000
        with self.app.app_context():
            file = ChallengeFiles.query.filter_by(challenge_id=2).one()
            fid = file.id
            path = Path(self.app.config["UPLOAD_FOLDER"]) / file.location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        response = self.maintainer.get(PREFIX + f"/files/{fid}/private.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, payload)
        response.close()
        class Body(io.BytesIO):
            def __init__(self):
                super().__init__(payload)
                self.sizes = []
            def read(self, size=-1):
                self.sizes.append(size)
                return super().read(size)
        stream = Body()
        uploader = S3Uploader.__new__(S3Uploader)
        uploader.bucket, uploader.s3_prefix = "fixture-bucket", "prefix/"
        uploader.s3 = Mock()
        uploader.s3.get_object.return_value = {"Body": stream}
        uploader.open = Mock(side_effect=AssertionError("No native cache copies"))
        with patch.object(preview, "get_uploader", return_value=uploader):
            response = self.maintainer.get(PREFIX + f"/files/{fid}/private.txt")
            self.assertEqual(response.data, payload)
            response.close()
        uploader.s3.get_object.assert_called_once_with(Bucket="fixture-bucket", Key="prefix/known-hash/private.txt")
        uploader.open.assert_not_called()
        uploader.s3.download_file.assert_not_called()
        self.assertTrue(stream.closed)
        self.assertTrue(all(0 < size <= 65536 for size in stream.sizes))

    def test_managed_storage_does_not_hold_competition_lock(self):
        from CTFd.utils.uploads import get_uploader
        from CTFd.plugins.open_tech_quest.service import event
        with self.app.app_context():
            file = ChallengeFiles(challenge_id=1, location="safe/preview.txt")
            db.session.add(file)
            db.session.flush()
            fid = file.id
            db.session.add(QuestAsset(file_id=fid, challenge_id=1, kind="attachment", name="preview.txt", size=7, sha256="a" * 64))
            db.session.commit()
            path = Path(self.app.config["UPLOAD_FOLDER"]) / file.location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preview")
            uploader = get_uploader()
        entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
        class SlowUploader:
            s3_prefix = None
            def open(self, location):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Storage gate not released")
                return uploader.open(location)
        def lock_event():
            with self.app.app_context():
                event(lock=True)
                acquired.set()
                db.session.rollback()
        with patch.object(assets, "get_uploader", return_value=SlowUploader()), ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(lambda: self.client(5).get(PREFIX + f"/files/{fid}/preview.txt"))
            try:
                self.assertTrue(entered.wait(5))
                locking = pool.submit(lock_event)
                self.assertTrue(acquired.wait(2), "Storage retained event lock")
            finally:
                release.set()
            self.assertEqual(pending.result().data, b"preview")
            locking.result()
