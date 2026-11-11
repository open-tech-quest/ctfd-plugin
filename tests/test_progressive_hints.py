"""Ordered team hint redemption through native routes, without history rewrites."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from CTFd.models import Awards, Hints, HintUnlocks, Users, UserTokens, db
from CTFd.utils import set_config
from CTFd.utils.security.auth import generate_user_token
from CTFd.plugins.open_tech_quest import guards
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestHintPurchase, QuestMaintainer
from CTFd.plugins.open_tech_quest.scoring import hint_access_reason
from CTFd.plugins.open_tech_quest.service import now
import test_integration as integration
import test_points


class ProgressiveHintsIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve
    begin = test_points.PointsIntegration.begin
    buy = test_points.PointsIntegration.buy
    rows = test_points.PointsIntegration.rows

    def setUp(self):
        test_points.PointsIntegration.setUp(self)
        with self.app.app_context():
            Hints.query.get(1).cost = 0
            Hints.query.get(2).cost = 0
            db.session.add_all([
                Hints(id=10, challenge_id=2, title="Second", content="Secret second hint", cost=30),
                Hints(id=20, challenge_id=2, title="Third", content="Secret third hint", cost=80),
                Hints(id=30, challenge_id=2, title="Fourth", content="Secret fourth hint", cost=0)])
            db.session.commit()
        self.begin()
        self.solve(self.alice, 1)

    def history(self):
        with self.app.app_context():
            return {"unlocks": [(u.id, u.target, u.team_id) for u in HintUnlocks.query.order_by(HintUnlocks.id)],
                    "awards": [(a.id, a.value, a.team_id) for a in Awards.query.order_by(Awards.id)],
                    "receipts": [(p.id, p.hint_id, p.cost, p.team_id) for p in QuestHintPurchase.query.order_by(QuestHintPurchase.id)],
                    "audit": [(a.id, a.action, a.detail) for a in QuestAudit.query.order_by(QuestAudit.id)]}

    def hint(self, hid, client=None):
        return (client or self.alice).get(f"/api/v1/hints/{hid}")

    def test_only_next_hint_is_offered_and_failed_or_cancelled_reveals_do_not_advance(self):
        before = self.history()
        first = self.hint(1)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn("content", first.json["data"])
        self.assertIn("no-store", first.headers["Cache-Control"])
        # Reading metadata then cancelling does not redeem even a free hint.
        self.assertEqual(self.history(), before)
        for hid in (10, 20, 30):
            self.assertEqual(self.hint(hid).status_code, 403)
            self.assertEqual(self.buy(hint_id=hid).status_code, 403)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.buy(hint_id=1).status_code, 200)
        self.assertEqual(self.buy(self.bob, 1).status_code, 200)
        self.assertEqual(self.hint(1, self.bob).json["data"]["content"], "Private hint")
        self.assertEqual(self.hint(10).status_code, 200)
        self.assertNotIn("content", self.hint(10).json["data"])
        self.assertEqual(self.buy(hint_id=10).status_code, 200)
        before = self.history()
        self.assertEqual(self.buy(hint_id=20).status_code, 400)
        self.assertEqual(self.buy(hint_id=30).status_code, 403)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.rows()[self.team_id]["points"], 70)
        self.solve(self.alice, 2)
        for hid in (20, 30):
            self.assertEqual(self.buy(hint_id=hid).status_code, 200)
            self.assertEqual(self.buy(self.bob, hid).status_code, 200)
        history = self.history()
        self.assertEqual([u[1] for u in history["unlocks"]], [1, 10, 20, 30])
        self.assertEqual([a[1] for a in history["awards"]], [0, -30, -80, 0])
        self.assertEqual(len(history["receipts"]), 4)
        for hid in (1, 10, 20, 30):
            self.assertIn("content", self.hint(hid).json["data"])
        self.assertEqual(self.rows()[self.team_id]["points"], 290)

    def test_out_of_order_legacy_purchases_stay_readable_and_duplicates_never_charge_again(self):
        with self.app.app_context():
            at = now()
            unlock = HintUnlocks(user_id=2, team_id=self.team_id, target=30, date=at)
            award = Awards(user_id=2, team_id=self.team_id, value=0, name="Legacy fourth", date=at)
            db.session.add_all([unlock, award])
            db.session.flush()
            db.session.add(QuestHintPurchase(team_id=self.team_id, challenge_id=2, hint_id=30,
                unlock_id=unlock.id, award_id=award.id, cost=0, purchased_at=at))
            db.session.commit()
        before = self.history()
        self.assertEqual(self.hint(30, self.bob).json["data"]["content"], "Secret fourth hint")
        self.assertEqual(self.buy(self.bob, 30).status_code, 200)
        self.assertEqual(self.history(), before)
        detail = self.alice.get("/api/v1/challenges/2").json["data"]
        self.assertEqual(next(h for h in detail["hints"] if h["id"] == 30)["content"], "Secret fourth hint")
        self.assertNotIn("content", next(h for h in detail["hints"] if h["id"] == 1))
        self.assertEqual(self.hint(10).status_code, 403)
        # Native prerequisites still apply to direct reads of redeemed hints.
        with self.app.app_context():
            Hints.query.get(30).requirements = {"prerequisites": [10]}
            db.session.commit()
        self.assertEqual(self.hint(30).status_code, 403)
        for hid in (1, 10):
            self.assertEqual(self.buy(hint_id=hid).status_code, 200)
        self.assertEqual(self.hint(30).status_code, 200)

    def test_native_forward_conflicts_and_cross_room_prerequisites_are_not_rewritten(self):
        for required in ([10], [1], ["10"]):
            with self.subTest(required=required):
                with self.app.app_context():
                    Hints.query.get(1).requirements = {"prerequisites": required}
                    db.session.commit()
                before = self.history()
                self.assertEqual(self.hint(1).status_code, 409)
                self.assertEqual(self.buy(hint_id=1).status_code, 409)
                self.assertEqual(self.history(), before)
                with self.app.app_context():
                    self.assertEqual(Hints.query.get(1).requirements, {"prerequisites": required})
        with self.app.app_context():
            Hints.query.get(1).requirements = {"prerequisites": ["2"]}
            db.session.commit()
        self.assertEqual(self.hint(1).status_code, 403)
        self.assertEqual(self.buy(hint_id=1).status_code, 403)
        self.assertEqual(self.buy(hint_id=2).status_code, 200)
        self.assertEqual(self.hint(1).status_code, 200)
        self.assertEqual(self.buy(hint_id=1).status_code, 200)

    def test_concurrent_teammates_share_redemption_and_cannot_skip_an_unfunded_hint(self):
        def confirm(client, hid, metadata):
            headers = {"CSRF-Token": "quest-test-csrf"}
            quote = (metadata.json.get("data") or {}).get("quote_revision")
            if quote:
                headers["X-Quest-Hint-Quote"] = quote
            return client.post("/api/v1/unlocks", json={"type": "hints", "target": hid}, headers=headers)
        def purchase(pair):
            uid, hid = pair
            client = self.client(uid)
            return confirm(client, hid, self.hint(hid, client))
        with ThreadPoolExecutor(max_workers=2) as pool:
            same = list(pool.map(purchase, [(2, 1), (3, 1)]))
        self.assertEqual([r.status_code for r in same], [200, 200])
        denied_read, earlier_committed = threading.Event(), threading.Event()
        def next_purchase(pair):
            uid, hid = pair
            client = self.client(uid)
            metadata = self.hint(hid, client)
            if hid == 20:
                denied_read.set()
                if not earlier_committed.wait(5):
                    raise RuntimeError("Earlier hint purchase did not complete")
                return metadata, confirm(client, hid, metadata)
            if not denied_read.wait(5):
                raise RuntimeError("Future hint metadata was not checked")
            try:
                return metadata, confirm(client, hid, metadata)
            finally:
                earlier_committed.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            next_pair = list(pool.map(next_purchase, [(2, 10), (3, 20)]))
        self.assertEqual(next_pair[0][1].status_code, 200)
        metadata, unconfirmed = next_pair[1]
        self.assertEqual(metadata.status_code, 403)
        self.assertIn("earlier", metadata.json["error"])
        self.assertNotIn("quote_revision", metadata.json.get("data") or {})
        self.assertEqual(unconfirmed.status_code, 409)
        self.assertIn("confirmation is missing", unconfirmed.json["error"])
        # Once progression permits a fresh confirmation, the authoritative
        # balance still refuses an 80-point hint with only 70 points left.
        metadata = self.hint(20)
        self.assertEqual(metadata.status_code, 200)
        unfunded = confirm(self.alice, 20, metadata)
        self.assertEqual(unfunded.status_code, 400)
        self.assertIn("has 70 points", unfunded.json["error"])
        self.assertIn("costs 80", unfunded.json["error"])
        history = self.history()
        self.assertEqual([r[1] for r in history["unlocks"]], [1, 10])
        self.assertEqual(len(history["receipts"]), 2)
        self.assertEqual(self.rows()[self.team_id]["points"], 70)

    def queued(self, method, path, change, browser=None, headers=None):
        browser = browser or self.client(2)
        headers = headers or {"CSRF-Token": "quest-test-csrf"}
        entered, release = threading.Event(), threading.Event()
        original = guards.event
        def gate(lock=False):
            if lock:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Hint test gate not released")
            return original(lock=lock)
        with patch.object(guards, "event", side_effect=gate), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(lambda: browser.open(path, method=method,
                json={"type": "hints", "target": 10} if method == "POST" else None, headers=headers))
            try:
                self.assertTrue(entered.wait(5))
                with self.app.app_context():
                    change()
                    db.session.commit()
            finally:
                release.set()
            return pending.result()

    def test_queued_reads_and_purchases_recheck_native_prerequisites_and_banned_actor(self):
        self.assertEqual(self.buy(hint_id=1).status_code, 200)
        for method, path in (("GET", "/api/v1/hints/10"), ("POST", "/api/v1/unlocks")):
            with self.subTest(method=method):
                with self.app.app_context():
                    Hints.query.get(10).requirements = None
                    db.session.commit()
                before = self.history()
                response = self.queued(method, path, lambda: setattr(Hints.query.get(10), "requirements", {"prerequisites": [30]}))
                self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
                self.assertEqual(self.history(), before)
                with self.app.app_context():
                    Hints.query.get(10).requirements = None
                    Users.query.get(2).banned = False
                    db.session.commit()
                response = self.queued(method, path, lambda: setattr(Users.query.get(2), "banned", True))
                self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
                self.assertEqual(self.history(), before)
                with self.app.app_context():
                    Users.query.get(2).banned = False
                    db.session.commit()

    def test_queued_revoked_participant_tokens_cannot_read_or_redeem_hints(self):
        self.assertEqual(self.buy(hint_id=1).status_code, 200)
        for method, path in (("GET", "/api/v1/hints/10"), ("POST", "/api/v1/unlocks")):
            with self.subTest(method=method):
                with self.app.app_context():
                    token = generate_user_token(Users.query.get(2), expiration=now() + timedelta(hours=1)).value
                browser = self.app.test_client()
                headers = {"Authorization": "Token " + token, "Content-Type": "application/json"}
                self.assertEqual(browser.get("/api/v1/hints/10", headers=headers).status_code, 200)
                before = self.history()
                response = self.queued(method, path, lambda: UserTokens.query.filter_by(value=token).delete(), browser, headers)
                self.assertEqual(response.status_code, 401, response.get_data(as_text=True))
                self.assertEqual(self.history(), before)
                with self.app.app_context():
                    self.assertIsNone(UserTokens.query.filter_by(value=token).first())

    def test_ended_response_strips_unredeemed_json_and_html_but_preserves_staff_preview(self):
        self.assertEqual(self.buy(hint_id=1).status_code, 200)
        response = self.alice.get("/api/v1/challenges/2")
        self.assertTrue(response.json["data"]["view"])
        self.assertEqual([h["id"] for h in response.json["data"]["hints"]], [1, 10, 20, 30])
        for secret in ("Secret second hint", "Secret third hint", "Secret fourth hint"):
            self.assertNotIn(secret, response.get_data(as_text=True))
        from datetime import timezone
        with self.app.app_context():
            at = now()
            QuestEvent.query.get(1).end_at = at
            set_config("end", int(at.replace(tzinfo=timezone.utc).timestamp()))
            set_config("view_after_ctf", True)
            db.session.commit()
        response = self.bob.get("/api/v1/challenges/2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["view"], "")
        hints = response.json["data"]["hints"]
        self.assertEqual(hints[0]["content"], "Private hint")
        for secret in ("Secret second hint", "Secret third hint", "Secret fourth hint"):
            self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertTrue(all("content" not in h and "html" not in h for h in hints[1:]))
        self.assertEqual(self.hint(20).status_code, 403)
        self.assertEqual(self.admin.get("/api/v1/hints/30?preview=true").json["data"]["content"], "Secret fourth hint")
        workspace = self.admin.get(integration.PREFIX + "/manage/rooms/openshift/challenges").json["data"]
        self.assertEqual(len(workspace["challenges"][1]["hints"]), 4)
        staff = self.admin.get(integration.PREFIX + "/manage/preview").json["data"]
        self.assertIn("Secret fourth hint", json.dumps(staff))

    def test_no_hints_and_all_redeemed_keep_native_metadata_and_hint_requirements(self):
        native = self.alice.get("/api/v1/challenges/1")
        self.assertEqual(native.json["data"]["hints"], [])
        self.assertTrue(native.json["data"]["view"])
        self.solve(self.alice, 2)
        for hid in (1, 10, 20, 30):
            self.assertEqual(self.buy(hint_id=hid).status_code, 200)
        self.assertEqual(self.hint(9999).status_code, 404)
        with self.app.app_context():
            for hint in Hints.query.filter_by(challenge_id=2).all():
                self.assertIsNone(hint_access_reason(self.team_id, hint))
                self.assertIsNone(hint.requirements)
        native = self.bob.get("/api/v1/challenges/2")
        self.assertEqual(len(native.json["data"]["hints"]), 4)
        self.assertTrue(all("content" in h for h in native.json["data"]["hints"]))

    def test_post_end_filter_still_hides_future_hints_if_session_expires_after_handler(self):
        from datetime import timezone
        from flask import request
        from CTFd.utils.security.signing import hmac
        with self.app.app_context():
            old_hash = hmac(Users.query.get(2).password)
            at = now()
            QuestEvent.query.get(1).end_at = at
            set_config("end", int(at.replace(tzinfo=timezone.utc).timestamp()))
            set_config("view_after_ctf", True)
            db.session.commit()
        with self.alice.session_transaction() as state:
            state["hash"] = old_hash
        # Native challenge detail can commit request tracking before response
        # hooks run. Interpose the same committed account state a concurrent
        # password reset can produce at that boundary.
        def reset_after_native_handler(response):
            if request.path == "/api/v1/challenges/2" and response.status_code == 200:
                Users.query.get(2).password = "reset-after-handler-test-only"
                db.session.commit()
            return response
        hooks = self.app.after_request_funcs[None]
        hooks.append(reset_after_native_handler)
        try:
            response = self.alice.get("/api/v1/challenges/2")
        finally:
            hooks.remove(reset_after_native_handler)
        self.assertEqual(response.status_code, 200)
        with self.alice.session_transaction() as state:
            self.assertNotIn("id", state)
        self.assertNotIn("Secret second hint", response.get_data(as_text=True))
        self.assertEqual(response.json["data"]["view"], "")

    def test_missing_guessed_hint_fails_closed_before_a_native_handler_relookup(self):
        from flask import request
        with self.app.app_context():
            set_config("hints_free_public_access", True)
            db.session.commit()
        # A native administrator can create the next autoincrement ID after
        # the guard's lookup. The core route then performs its own fresh lookup.
        def create_after_guard():
            if request.path == "/api/v1/hints/31":
                hint = Hints(challenge_id=2, content="New private future clue", cost=0)
                db.session.add(hint)
                db.session.commit()
                self.assertEqual(hint.id, 31)
        hooks = self.app.before_request_funcs[None]
        hooks.append(create_after_guard)
        try:
            response = self.guest.get("/api/v1/hints/31")
        finally:
            hooks.remove(create_after_guard)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("New private future clue", response.get_data(as_text=True))
