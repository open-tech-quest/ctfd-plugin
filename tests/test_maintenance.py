"""Reviewed live edits preserve native evidence and keep play running."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from flask import request
from CTFd.models import Awards, Challenges, Fails, Flags, Hints, HintUnlocks, Solves, Submissions, UserTokens, Users, db
from CTFd.utils.security.auth import generate_user_token
from CTFd.plugins.open_tech_quest import authoring
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestHintPurchase, QuestMaintainer, QuestPause, QuestRoom, QuestStart, QuestStep
from CTFd.plugins.open_tech_quest.service import projection
import test_authoring
import test_integration as integration

P = integration.PREFIX
C = P + "/manage/rooms/openshift/challenges"


def reviewed_prices(case, changes, cid):
    """Old maintenance cases explicitly retain their original scoring intent."""
    if "pricing" in changes or cid is None:
        return changes
    with case.app.app_context():
        if QuestEvent.query.get(1).status not in ("live", "paused"):
            return changes
        challenge = Challenges.query.get(cid)
        value_changed = "value" in changes and changes["value"] != challenge.value
        hints = {h.id: h.cost for h in Hints.query.filter_by(challenge_id=cid)}
        hint_changed = any(h.get("id") in hints and h["cost"] != hints[h["id"]] for h in changes.get("hints", []))
    if value_changed or hint_changed:
        return {**changes, "pricing": {"challenge_existing": value_changed, "hint_existing_ids": []}}
    return changes


class MaintenanceIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    def setUp(self):
        test_authoring.AuthoringIntegration.setUp(self)
        with self.app.app_context():
            Hints.query.get(1).cost = 50
            db.session.commit()

    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve
    payload = test_authoring.AuthoringIntegration.payload

    def workspace(self):
        response = self.maintainer.get(C)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def review(self, changes, cid=1, client=None, collection=C):
        changes = reviewed_prices(self, changes, cid)
        client = client or self.maintainer
        saved = client.get(collection).json["data"]["revision"]
        path = collection + ("/" + str(cid) if cid is not None else "")
        response = self.post(client, path + "/review", {"changes": changes, "revision": saved})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return saved, response.json["data"]

    def apply(self, changes, cid=1, reviewed=None, client=None, collection=C):
        changes = reviewed_prices(self, changes, cid)
        client = client or self.maintainer
        saved, review = reviewed or self.review(changes, cid, client, collection)
        body = dict(changes)
        if review["requires_confirmation"]:
            body["maintenance"] = {"revision": review["revision"], "reason": "Reviewed synthetic correction", "confirm": True}
        return client.open(collection + ("/" + str(cid) if cid is not None else ""),
            method="PATCH" if cid is not None else "POST", json=body,
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": saved})

    def score(self, tid=None):
        with self.app.app_context():
            return projection().score(tid or self.team_id)["points"]

    def history(self):
        with self.app.app_context():
            return {m.__tablename__: [(r.id, str(r.date)) for r in m.query.order_by(m.id)]
                    for m in (Submissions, Solves, HintUnlocks, Awards)}

    def begin(self, both=False):
        self.action("start")
        self.start()
        if both:
            self.start(self.carol)

    def test_live_text_requires_revision_preserves_activity_and_never_pauses(self):
        self.begin()
        self.solve(self.alice, 1)
        before = self.history()
        saved = self.workspace()["revision"]
        changes = {"name": "Corrected title", "description": "Corrected instructions", "connection_info": "https://lab.example.test"}
        missing = self.post(self.maintainer, C + "/1", changes, "PATCH")
        self.assertEqual(missing.status_code, 409)
        self.assertIn("Refresh the page", missing.json["error"])
        response = self.maintainer.open(C + "/1", method="PATCH", json=changes,
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": saved})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertNotEqual(response.json["data"]["revision"], saved)
        self.assertEqual(self.history(), before)
        with self.app.app_context():
            self.assertEqual(QuestEvent.query.get(1).status, "live")
            self.assertEqual(QuestPause.query.count(), 0)
            self.assertEqual(Challenges.query.get(1).connection_info, changes["connection_info"])
        self.action("pause")
        self.assertEqual(self.apply({"description": "Safe during manual pause"}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(QuestEvent.query.get(1).status, "paused")
            self.assertEqual(QuestPause.query.count(), 1)

    def test_reprice_review_survives_ordinary_progress_and_preserves_native_times(self):
        self.begin(both=True)
        self.solve(self.alice, 1)
        self.solve(self.carol, 1)
        saved, review = self.review({"value": 250})
        self.assertEqual(review["competitive_fields"], ["value"])
        self.assertEqual(review["impact"]["teams_with_score_changes"], 2)
        self.assertEqual(self.post(self.bob, "/api/v1/unlocks", {"type": "hints", "target": 1}).status_code, 200)
        self.solve(self.alice, 2)
        self.post(self.carol, "/api/v1/challenges/attempt", {"challenge_id": 2, "submission": "wrong-synthetic-answer"})
        history = self.history()
        self.assertEqual(self.apply({"value": 250}, reviewed=(saved, review)).status_code, 200)
        self.assertEqual((self.score(), self.score(self.other_id)), (300, 250))
        self.assertEqual(self.history(), history)
        self.assertEqual(self.solve(self.bob, 1).json["data"]["status"], "already_solved")
        self.assertEqual(self.score(), 300)

    def test_new_answers_only_grade_future_attempts_and_review_audit_has_no_answers(self):
        self.begin(both=True)
        self.solve(self.alice, 1)
        old = self.history()
        with self.app.app_context():
            original_flag_id = Flags.query.filter_by(challenge_id=1).one().id
        changes = {"flags": [{"content": "private-new-answer", "case_insensitive": False}]}
        saved, review = self.review(changes)
        self.assertNotIn("private-new-answer", json.dumps(review))
        self.assertEqual(self.apply(changes, reviewed=(saved, review)).status_code, 200)
        self.assertEqual(self.history(), old)
        self.assertEqual(self.score(), 100)
        wrong = self.post(self.carol, "/api/v1/challenges/attempt", {"challenge_id": 1, "submission": "fixture-answer-1"})
        self.assertEqual(wrong.json["data"]["status"], "incorrect")
        correct = self.post(self.carol, "/api/v1/challenges/attempt", {"challenge_id": 1, "submission": "private-new-answer"})
        self.assertEqual(correct.json["data"]["status"], "correct")
        with self.app.app_context():
            audit = json.dumps([r.detail for r in QuestAudit.query.all()])
            self.assertNotIn("private-new-answer", audit)
            self.assertNotIn("fixture-answer-1", audit)
            self.assertEqual(Flags.query.filter_by(challenge_id=1).one().id, original_flag_id)

    def test_hint_quote_cutover_preserves_old_receipt_and_never_silently_reprices(self):
        self.begin(both=True)
        self.solve(self.alice, 1)
        self.solve(self.carol, 1)
        quote = self.carol.get("/api/v1/hints/1").json["data"]["quote_revision"]
        self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1}).status_code, 200)
        hint = self.workspace()["challenges"][1]["hints"][0]
        changes = {"hints": [{k: hint[k] for k in ("id", "title", "content", "cost")}]}
        changes["hints"][0]["cost"] = 80
        self.assertEqual(self.apply(changes, cid=2).status_code, 200)
        stale = self.carol.open("/api/v1/unlocks", method="POST", json={"type": "hints", "target": 1},
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Hint-Quote": quote})
        self.assertEqual(stale.status_code, 409, stale.get_data(as_text=True))
        self.assertIn("Cancel", stale.json["error"])
        missing = self.carol.open("/api/v1/unlocks", method="POST", json={"type": "hints", "target": 1},
            headers={"CSRF-Token": "quest-test-csrf"})
        self.assertEqual(missing.status_code, 409)
        self.assertIn("Refresh the page", missing.json["error"])
        with self.app.app_context():
            self.assertEqual(QuestHintPurchase.query.count(), 1)
            self.assertEqual((QuestHintPurchase.query.one().cost, Awards.query.one().value), (50, -50))
        self.assertEqual(self.post(self.carol, "/api/v1/unlocks", {"type": "hints", "target": 1}).status_code, 200)
        self.assertEqual((self.score(), self.score(self.other_id)), (50, 20))
        # A teammate's duplicate old receipt does not require a new quote or charge.
        duplicate = self.bob.open("/api/v1/unlocks", method="POST", json={"type": "hints", "target": 1}, headers={"CSRF-Token": "quest-test-csrf"})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.score(), 50)
        revision = self.workspace()["revision"]
        self.assertEqual(self.post(self.maintainer, C + "/2/review", {"changes": {"hints": []}, "revision": revision}).status_code, 409)

    def test_withdraw_and_locked_preserve_raw_history_but_have_distinct_credit(self):
        self.begin()
        self.solve(self.alice, 1)
        self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1})
        self.solve(self.alice, 2)
        old = self.history()
        self.assertEqual(self.score(), 150)
        reviewed = self.review({"state": "hidden"}, cid=2)
        self.assertEqual(reviewed[1]["impact"]["team_scores"][0]["after_points"], 100)
        self.assertEqual(self.apply({"state": "hidden"}, cid=2, reviewed=reviewed).status_code, 200)
        self.assertEqual(self.score(), 100)
        self.assertEqual(self.alice.get("/api/v1/hints/1").status_code, 404)
        self.assertEqual(self.apply({"state": "locked"}, cid=2).status_code, 200)
        self.assertEqual(self.score(), 150)
        self.assertEqual(self.alice.get("/api/v1/challenges/2").status_code, 404)
        self.assertEqual(self.history(), old)

    def test_append_preserves_played_steps_and_completion_rechecks_at_apply(self):
        self.begin()
        self.solve(self.alice, 1)
        with self.app.app_context():
            old_steps = [(s.id, s.challenge_id, s.position) for s in QuestStep.query.order_by(QuestStep.position)]
        changes = self.payload(name="New final step")
        reviewed = self.review(changes, cid=None)
        self.assertIn("No automatic time compensation", " ".join(reviewed[1]["warnings"]))
        with self.app.app_context():
            old_starts = [(s.id, s.team_id, s.room_id, s.started_at) for s in QuestStart.query.order_by(QuestStart.id)]
        created = self.apply(changes, cid=None, reviewed=reviewed)
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        cid = created.json["data"]["id"]
        with self.app.app_context():
            self.assertEqual([(s.id, s.challenge_id, s.position) for s in QuestStep.query.order_by(QuestStep.position)][:2], old_steps)
            self.assertEqual(Challenges.query.get(2).next_id, cid)
            self.assertEqual(Challenges.query.get(cid).requirements["prerequisites"], [1, 2])
            self.assertEqual([(s.id, s.team_id, s.room_id, s.started_at) for s in QuestStart.query.order_by(QuestStart.id)], old_starts)
        next_changes = self.payload(name="Cannot reopen a completed room")
        pending = self.review(next_changes, cid=None)
        self.solve(self.alice, 2)
        self.post(self.alice, "/api/v1/challenges/attempt", {"challenge_id": cid, "submission": "fixture-Exact-Answer"})
        rejected = self.apply(next_changes, cid=None, reviewed=pending)
        self.assertEqual(rejected.status_code, 409)
        self.assertIn("completed", rejected.json["error"])
        rooms = self.maintainer.get(P + "/manage/rooms").json["data"]
        room = self.maintainer.open(P + "/manage/rooms", method="POST", json={"slug": "extension", "name": "Additional challenges"},
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": rooms["revision"]})
        self.assertEqual(room.status_code, 200, room.get_data(as_text=True))
        self.assertEqual(self.apply(self.payload(), cid=None, collection=P + "/manage/rooms/extension/challenges").status_code, 200)

    def test_content_revision_proposal_and_actor_bind_confirmation_not_progress(self):
        self.begin()
        saved, reviewed = self.review({"value": 200})
        self.assertEqual(self.apply({"value": 201}, reviewed=(saved, reviewed)).status_code, 409)
        self.assertEqual(self.apply({"value": 200}, reviewed=(saved, reviewed), client=self.admin).status_code, 409)
        self.assertEqual(self.apply({"description": "A newer saved edit"}).status_code, 200)
        self.assertEqual(self.apply({"value": 200}, reviewed=(saved, reviewed)).status_code, 409)
        self.assertEqual(self.apply({"value": 200}).status_code, 200)

    def test_closed_published_and_native_admin_token_routes_cannot_bypass_review(self):
        with self.app.app_context():
            token = generate_user_token(Users.query.get(1)).value
        self.begin()
        cases = [("/api/v1/challenges/1", {"description": "Bypass"}, "PATCH"),
            ("/api/v1/challenges/1", {"state": "hidden"}, "PATCH"),
            ("/api/v1/flags", {"challenge_id": 1, "type": "static", "content": "bad-new-answer"}, "POST"),
            ("/api/v1/flags", {"challenge": 1, "type": "static", "content": "bad-alias-answer"}, "POST"),
            ("/api/v1/hints", {"challenge": {"id": 1}, "content": "Alias hint", "cost": 0}, "POST"),
            ("/api/v1/hints/1", {"challenge_id": 3}, "PATCH"),
            ("/api/v1/flags/+1", {"content": "URL alias"}, "PATCH")]
        for path, body, method in cases:
            response = self.post(self.admin, path, body, method)
            self.assertIn(response.status_code, (400, 409), response.get_data(as_text=True))
            response = self.app.test_client().open(path, method=method, json=body,
                headers={"Authorization": "Token " + token, "Content-Type": "application/json"})
            self.assertIn(response.status_code, (400, 409), response.get_data(as_text=True))
        for action in ("close", "publish"):
            self.action(action)
            self.assertEqual(self.apply_closed({"description": "After close"}).status_code, 409)
            self.assertEqual(self.post(self.admin, "/api/v1/challenges/1", {"state": "hidden"}, "PATCH").status_code, 409)
        with self.app.app_context():
            self.assertEqual(Flags.query.filter_by(challenge_id=1).one().content, "fixture-answer-1")
            self.assertEqual(Hints.query.count(), 1)
            self.assertEqual(Hints.query.one().challenge_id, 2)

    def apply_closed(self, data):
        return self.maintainer.open(C + "/1", method="PATCH", json=data,
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": self.workspace()["revision"]})

    def test_unmapped_native_related_owners_stay_supported_without_mapped_moves(self):
        self.begin()
        created = []
        for owner in (3, {"id": 3}):
            result = self.post(self.admin, "/api/v1/hints", {"challenge": owner, "content": "Unmapped native hint", "cost": 0})
            self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
            created.append(result.json["data"]["id"])
        for body in ({"challenge_id": 3, "challenge": 1, "content": "Conflicting owners", "cost": 0},
                     {"challenge": {"id": 1}, "content": "Mapped owner", "cost": 0}):
            self.assertEqual(self.post(self.admin, "/api/v1/hints", body).status_code, 409)
        self.assertEqual(self.post(self.admin, "/api/v1/hints/" + str(created[0]), {"challenge": {"id": 2}}, "PATCH").status_code, 409)
        self.assertEqual(self.post(self.admin, "/api/v1/hints/1", {"challenge": 3}, "PATCH").status_code, 409)
        for owner in (True, 1.0, [], {"id": 1, "name": "ambiguous"}):
            self.assertEqual(self.post(self.admin, "/api/v1/hints", {"challenge": owner, "content": "Malformed owner", "cost": 0}).status_code, 400)
        with self.app.app_context():
            self.assertEqual(Hints.query.get(1).challenge_id, 2)
            self.assertEqual([Hints.query.get(hid).challenge_id for hid in created], [3, 3])

    def test_old_backup_fields_need_fresh_review_and_preserve_later_play_and_receipts(self):
        old = self.workspace()
        source = old["challenges"][1]
        restored = {"description": source["description"], "value": source["value"],
            "flags": source["flags"], "hints": [{k: h[k] for k in ("id", "title", "content", "cost")} for h in source["hints"]]}
        self.begin()
        self.solve(self.alice, 1)
        changed = {**restored, "description": "Later instructions", "value": 175,
                   "hints": [{**h, "cost": 30} for h in restored["hints"]]}
        self.assertEqual(self.apply(changed, cid=2).status_code, 200)
        self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 1})
        self.solve(self.alice, 2)
        self.post(self.bob, "/api/v1/challenges/attempt", {"challenge_id": 2, "submission": "wrong-after-backup"})
        history = self.history()
        stale = self.maintainer.open(C + "/2", method="PATCH", json=restored,
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": old["revision"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.apply(restored, cid=2).status_code, 200)
        self.assertEqual(self.history(), history)
        self.assertEqual(self.score(), 170)
        with self.app.app_context():
            self.assertEqual((QuestHintPurchase.query.one().cost, Awards.query.one().value), (30, -30))
            self.assertEqual((Hints.query.one().id, Hints.query.one().cost), (1, 50))

    def test_queued_old_hint_quote_fails_after_price_commit_without_any_charge(self):
        from CTFd.plugins.open_tech_quest import guards
        self.begin()
        self.solve(self.alice, 1)
        quote = self.alice.get("/api/v1/hints/1").json["data"]["quote_revision"]
        entered, release = threading.Event(), threading.Event()
        original = guards.event
        def gate(lock=False):
            if lock and request.path == "/api/v1/unlocks":
                entered.set()
                if not release.wait(6):
                    raise RuntimeError("Fixture gate timed out")
            return original(lock)
        def buy():
            return self.client(2).open("/api/v1/unlocks", method="POST", json={"type": "hints", "target": 1},
                headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Hint-Quote": quote})
        hint = self.workspace()["challenges"][1]["hints"][0]
        change = {"hints": [{**{k: hint[k] for k in ("id", "title", "content", "cost")}, "cost": 75}]}
        with patch.object(guards, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(buy)
            self.assertTrue(entered.wait(4))
            try:
                self.assertEqual(self.apply(change, cid=2).status_code, 200)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=8).status_code, 409)
        with self.app.app_context():
            self.assertEqual(HintUnlocks.query.count(), 0)
            self.assertEqual(Awards.query.count(), 0)
            self.assertEqual(QuestHintPurchase.query.count(), 0)
        self.assertEqual(self.score(), 100)

    def test_queued_revoked_staff_token_cannot_apply_live_review(self):
        with self.app.app_context():
            token = generate_user_token(Users.query.get(5)).value
        self.begin()
        token_client = self.app.test_client()
        headers = {"Authorization": "Token " + token, "Content-Type": "application/json"}
        revision = token_client.get(C, headers=headers).json["data"]["revision"]
        changes = {"value": 200, "pricing": {"challenge_existing": True, "hint_existing_ids": []}}
        reviewed = token_client.post(C + "/1/review", headers=headers, json={"changes": changes, "revision": revision})
        self.assertEqual(reviewed.status_code, 200)
        payload = {**changes, "maintenance": {"revision": reviewed.json["data"]["revision"], "reason": "Queued correction", "confirm": True}}
        entered, release = threading.Event(), threading.Event()
        original = authoring.event
        def gate(lock=False):
            if lock and request.path == C + "/1":
                entered.set()
                if not release.wait(6):
                    raise RuntimeError("Fixture gate timed out")
            return original(lock)
        def apply():
            return self.app.test_client().patch(C + "/1", json=payload,
                headers={**headers, "X-Quest-Content-Revision": revision})
        with patch.object(authoring, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(apply)
            self.assertTrue(entered.wait(4))
            try:
                with self.app.app_context():
                    UserTokens.query.filter_by(value=token).delete()
                    db.session.commit()
            finally:
                release.set()
            self.assertEqual(future.result(timeout=8).status_code, 401)
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(1).value, 100)
            self.assertEqual(QuestAudit.query.filter_by(action="challenge.update").count(), 0)

    def test_concurrent_live_text_writers_cannot_overwrite_a_newer_revision(self):
        self.begin()
        revision = self.workspace()["revision"]
        barrier = threading.Barrier(2)
        def edit(uid, name):
            client = self.client(uid)
            barrier.wait(timeout=4)
            return client.patch(C + "/1", json={"name": name},
                headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": revision})
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(edit, 1, "Administrator edit")
            b = pool.submit(edit, 5, "Maintainer edit")
            responses = [a.result(timeout=10), b.result(timeout=10)]
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        with self.app.app_context():
            self.assertIn(Challenges.query.get(1).name, {"Administrator edit", "Maintainer edit"})
            self.assertEqual(QuestAudit.query.filter_by(action="challenge.update").count(), 1)
            self.assertEqual(QuestEvent.query.get(1).status, "live")

    def test_free_zero_quote_and_typed_archive_audit_keep_history_without_answers(self):
        with self.app.app_context():
            db.session.add(Hints(id=2, challenge_id=1, title="Free", content="Private free hint", cost=0))
            db.session.commit()
        self.begin()
        self.assertEqual(self.score(), 0)
        self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": "hints", "target": 2}).status_code, 200)
        self.assertEqual(self.score(), 0)
        self.solve(self.alice, 1)
        self.assertEqual(self.apply({"value": 150}).status_code, 200)
        from CTFd.plugins.open_tech_quest.archive import collect_event
        with self.app.app_context():
            audit = QuestAudit.query.filter_by(action="challenge.update").one()
            maintenance = audit.detail["maintenance"]
            self.assertNotEqual(maintenance["before_revision"], maintenance["after_revision"])
            # Only the explicit typed keys may enter archived maintenance detail.
            audit.detail = {**audit.detail, "maintenance": {**maintenance, "flags": ["NEVER-ARCHIVE-THIS"],
                "impact": {**maintenance["impact"], "provided": "NEVER-ARCHIVE-THIS"}}}
            db.session.commit()
            archived = next(a for a in collect_event(QuestEvent.query.get(1))["audit"] if a["id"] == audit.id)["detail"]["maintenance"]
            self.assertEqual(archived["reason"], "Reviewed synthetic correction")
            self.assertEqual(archived["before_revision"], maintenance["before_revision"])
            self.assertNotIn("NEVER-ARCHIVE-THIS", json.dumps(archived))
            self.assertEqual((QuestHintPurchase.query.one().cost, Awards.query.one().value), (0, 0))

    def test_recovery_cannot_commit_a_grade_captured_before_live_answer_change(self):
        from CTFd.plugins.open_tech_quest import recovery
        from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve
        self.begin()
        self.solve(self.alice, 1)
        with self.app.app_context():
            original_solve = Solves.query.one()
            stamp = original_solve.date
            Users.query.get(2).banned = True
            db.session.commit()
        original = recovery._fresh_attempt
        def changed(pending):
            # The native already-solved response may release its transaction.
            # Interpose a genuine reviewed edit before recovery reacquires it.
            db.session.rollback()
            with ThreadPoolExecutor(max_workers=1) as pool:
                response = pool.submit(self.apply, {"flags": [{"content": "new-recovery-answer", "case_insensitive": False}]}).result(timeout=8)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            return original(pending)
        with patch.object(recovery, "_fresh_attempt", side_effect=changed):
            denied = self.solve(self.bob, 1)
        self.assertEqual(denied.status_code, 409, denied.get_data(as_text=True))
        self.assertIn("changed", denied.json["error"])
        with self.app.app_context():
            self.assertEqual(QuestRecoverySolve.query.count(), 0)
            self.assertEqual(Solves.query.one().date, stamp)
            self.assertEqual(QuestAudit.query.filter_by(action="challenge.recover").count(), 0)
        retry = self.post(self.bob, "/api/v1/challenges/attempt", {"challenge_id": 1, "submission": "new-recovery-answer"})
        self.assertEqual(retry.json["data"]["status"], "correct")
        with self.app.app_context():
            self.assertEqual(QuestRecoverySolve.query.count(), 1)
            self.assertEqual(Solves.query.one().date, stamp)

    def test_old_review_cannot_replay_after_real_new_round_with_same_admin_and_content(self):
        self.begin()
        saved, reviewed = self.review({"value": 250}, client=self.admin)
        self.action("close")
        preview = self.admin.get(P + "/admin/round/preview").json["data"]
        reset = self.post(self.admin, P + "/admin/round/reset", {"revision": preview["revision"],
            "confirm": True, "event_name": preview["event_name"], "reason": "Prepare a genuinely new round", "mode": "new_round"})
        self.assertEqual(reset.status_code, 200, reset.get_data(as_text=True))
        clients = []
        for number in (1, 2):
            created = self.post(self.admin, "/api/v1/users", {"name": "New round player " + str(number),
                "email": f"new-round-{number}@example.test", "password": "new-fixture-only", "type": "user", "verified": True})
            self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
            clients.append(self.client(created.json["data"]["id"]))
        self.assertEqual(self.post(self.admin, P + "/admin/onboarding", {"team_size": 2}, "PATCH").status_code, 200)
        self.assertEqual(self.post(self.admin, P + "/admin/attendance", {"enabled": True}, "PATCH").status_code, 200)
        for client in clients:
            self.assertEqual(self.post(client, P + "/onboarding/ready", {}).status_code, 200)
        self.assertEqual(self.post(self.admin, P + "/admin/attendance", {"enabled": False}, "PATCH").status_code, 200)
        self.assertEqual(self.post(self.admin, P + "/admin/onboarding/action", {"action": "close_checkin"}).status_code, 200)
        plan = self.post(self.admin, P + "/admin/onboarding/optimize", {"preview": True}).json["data"]
        self.assertEqual(self.post(self.admin, P + "/admin/onboarding/optimize", {"preview": False, "fingerprint": plan["fingerprint"]}).status_code, 200)
        self.assertEqual(self.post(self.admin, P + "/admin/onboarding/action", {"action": "finalize"}).status_code, 200)
        self.action("start")
        self.assertEqual(self.admin.get(C).json["data"]["revision"], saved)
        rejected = self.apply({"value": 250}, reviewed=(saved, reviewed), client=self.admin)
        self.assertEqual(rejected.status_code, 409, rejected.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(1).value, 100)
        self.assertEqual(self.apply({"value": 250}, client=self.admin).status_code, 200)

    def test_queued_revoked_token_cannot_edit_or_create_live_rooms(self):
        self.begin()
        cases = [(P + "/admin/rooms/openshift", "PATCH", {"subtitle": "Revoked queued text"}),
                 (P + "/manage/rooms", "POST", {"slug": "revoked-room", "name": "Revoked queued room"})]
        for path, method, changes in cases:
            with self.subTest(method=method):
                with self.app.app_context():
                    token = generate_user_token(Users.query.get(5)).value
                headers = {"Authorization": "Token " + token, "Content-Type": "application/json"}
                saved = self.app.test_client().get(P + "/manage/rooms", headers=headers).json["data"]
                revision = saved["rooms"][0]["revision"] if method == "PATCH" else saved["revision"]
                entered, release = threading.Event(), threading.Event()
                original = authoring.event
                def gate(lock=False):
                    if lock and request.path == path:
                        entered.set()
                        if not release.wait(6):
                            raise RuntimeError("Fixture gate timed out")
                    return original(lock)
                def write():
                    return self.app.test_client().open(path, method=method, json=changes,
                        headers={**headers, "X-Quest-Content-Revision": revision})
                with patch.object(authoring, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(write)
                    self.assertTrue(entered.wait(4))
                    try:
                        with self.app.app_context():
                            UserTokens.query.filter_by(value=token).delete()
                            db.session.commit()
                    finally:
                        release.set()
                    self.assertEqual(future.result(timeout=8).status_code, 401)
                with self.app.app_context():
                    self.assertNotEqual(QuestRoom.query.filter_by(slug="openshift").one().subtitle, "Revoked queued text")
                    self.assertIsNone(QuestRoom.query.filter_by(slug="revoked-room").first())
                    self.assertEqual(QuestAudit.query.filter_by(action="room.create").count(), 0)
                    self.assertEqual(QuestAudit.query.filter_by(action="room.configure").count(), 1)
