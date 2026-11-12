"""Reviewed price scopes against real native solves, purchases and score reads."""
import importlib
import json
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations

from CTFd.models import Awards, Challenges, Hints, Solves, db
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestHintPurchase, QuestPriceChange
from CTFd.plugins.open_tech_quest.service import projection
import test_integration as integration
import test_maintenance as maintenance_tests

P = integration.PREFIX
C = maintenance_tests.C


class PriceEditsIntegration(unittest.TestCase):
    # Bind only fixture helpers, without rediscovering the maintenance tests.
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    setUp = maintenance_tests.MaintenanceIntegration.setUp
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    start = integration.QuestIntegration.start
    solve = integration.QuestIntegration.solve
    workspace = maintenance_tests.MaintenanceIntegration.workspace
    review = maintenance_tests.MaintenanceIntegration.review
    history = maintenance_tests.MaintenanceIntegration.history
    begin = maintenance_tests.MaintenanceIntegration.begin

    def checked(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["data"]

    def value_change(self, value, existing=False):
        return {"value": value, "pricing": {"challenge_existing": existing, "hint_existing_ids": []}}

    def hint_change(self, costs, existing=()):
        challenge = next(c for c in self.workspace()["challenges"] if c["id"] == 2)
        hints = [{k: h[k] for k in ("id", "title", "content", "cost")} for h in challenge["hints"]]
        for hint in hints:
            if hint["id"] in costs:
                hint["cost"] = costs[hint["id"]]
        return {"hints": hints, "pricing": {"challenge_existing": False, "hint_existing_ids": list(existing)}}

    def apply_price(self, changes, cid=1, reviewed=None, acknowledge=None):
        saved, reviewed = reviewed or self.review(changes, cid=cid)
        approval = {"revision": reviewed["revision"], "reason": "Reviewed synthetic repricing", "confirm": True}
        if acknowledge is not None:
            approval["acknowledge_negative"] = acknowledge
        return self.maintainer.open(C + "/" + str(cid), method="PATCH", json={**changes, "maintenance": approval},
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Content-Revision": saved})

    def buy(self, client=None, hint_id=1):
        return self.post(client or self.alice, "/api/v1/unlocks", {"type": "hints", "target": hint_id})

    def score(self, tid=None):
        with self.app.app_context():
            return projection().score(tid or self.team_id)["points"]

    def assert_room_score(self, client, points):
        room = self.checked(client.get(P + "/rooms/openshift"))
        self.assertEqual(room["progress"]["points"], points)

    def receipts(self):
        with self.app.app_context():
            return ([(p.id, p.team_id, p.hint_id, p.cost, p.purchased_at, p.award_id)
                     for p in QuestHintPurchase.query.order_by(QuestHintPurchase.id)],
                    [(a.id, a.value, a.date) for a in Awards.query.order_by(Awards.id)])

    def raw_review(self, changes, cid=1):
        return self.post(self.maintainer, C + "/" + str(cid) + "/review",
            {"changes": changes, "revision": self.workspace()["revision"]})

    def test_future_challenge_prices_preserve_old_teams_and_new_solves_get_new_value(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        history = self.history()
        changes = self.value_change(250)
        reviewed = self.review(changes)
        self.assertEqual(reviewed[1]["impact"]["teams_with_score_changes"], 0)
        self.checked(self.apply_price(changes, reviewed=reviewed))
        self.assertEqual(self.score(), 100)
        self.assertEqual(self.history(), history)
        self.checked(self.solve(self.carol, 1))
        self.assertEqual((self.score(), self.score(self.other_id)), (100, 250))
        self.assert_room_score(self.alice, 100)
        self.assert_room_score(self.carol, 250)

    def test_retroactive_challenge_change_lists_existing_teams_and_keeps_native_history(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.solve(self.carol, 1))
        history = self.history()
        changes = self.value_change(275, existing=True)
        reviewed = self.review(changes)
        impact = reviewed[1]["impact"]
        self.assertEqual(impact["teams_with_score_changes"], 2)
        self.assertEqual(impact["repriced_solve_records"], 2)
        self.assertEqual({r["team_id"] for r in impact["team_scores"]}, {self.team_id, self.other_id})
        self.assertEqual({r["team_name"] for r in impact["team_scores"]}, {"Red team", "Blue team"})
        self.assertTrue(all((r["before_points"], r["after_points"]) == (100, 275) for r in impact["team_scores"]))
        self.checked(self.apply_price(changes, reviewed=reviewed))
        self.assertEqual((self.score(), self.score(self.other_id)), (275, 275))
        self.assertEqual(self.history(), history)

    def test_repeated_future_prices_keep_cohorts_until_explicit_retroactive_alignment(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.apply_price(self.value_change(200)))
        self.checked(self.solve(self.carol, 1))
        self.assertEqual((self.score(), self.score(self.other_id)), (100, 200))
        # No new advertised price is needed to bring the earlier cohort into line.
        self.checked(self.apply_price(self.value_change(200, existing=True)))
        self.assertEqual((self.score(), self.score(self.other_id)), (200, 200))
        history = self.history()
        self.checked(self.apply_price(self.value_change(50)))
        self.assertEqual((self.score(), self.score(self.other_id)), (200, 200))
        self.checked(self.apply_price(self.value_change(125, existing=True)))
        self.checked(self.apply_price(self.value_change(75)))
        self.assertEqual((self.score(), self.score(self.other_id)), (125, 125))
        self.assertEqual(self.history(), history)

    def test_new_solve_with_equal_native_timestamp_still_uses_the_new_price(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        with self.app.app_context():
            original = Solves.query.filter_by(team_id=self.team_id, challenge_id=1).one().date
        self.checked(self.apply_price(self.value_change(225)))
        self.checked(self.solve(self.carol, 1))
        with self.app.app_context():
            Solves.query.filter_by(team_id=self.other_id, challenge_id=1).one().date = original
            db.session.commit()
        self.assertEqual((self.score(), self.score(self.other_id)), (100, 225))

    def test_future_hint_price_keeps_old_receipts_and_changes_new_purchases(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.solve(self.carol, 1))
        self.checked(self.buy())
        receipts = self.receipts()
        change = self.hint_change({1: 80})
        reviewed = self.review(change, cid=2)
        self.assertEqual(reviewed[1]["impact"]["teams_with_score_changes"], 0)
        self.assertEqual(reviewed[1]["impact"]["repriced_hint_receipts"], 0)
        self.checked(self.apply_price(change, cid=2, reviewed=reviewed))
        self.assertEqual(self.receipts(), receipts)
        self.assertEqual(self.score(), 50)
        self.checked(self.buy(self.carol))
        self.assertEqual((self.score(), self.score(self.other_id)), (50, 20))

    def test_per_hint_repricing_combines_existing_refund_and_future_only_increase(self):
        with self.app.app_context():
            db.session.add(Hints(id=2, challenge_id=2, title="Second clue", content="Synthetic second clue", cost=10))
            db.session.commit()
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.solve(self.carol, 1))
        self.checked(self.buy())
        self.checked(self.buy(hint_id=2))
        self.checked(self.buy(self.carol))
        receipts = self.receipts()
        change = self.hint_change({1: 30, 2: 20}, existing=[1])
        reviewed = self.review(change, cid=2)
        self.assertEqual(reviewed[1]["impact"]["repriced_hint_receipts"], 2)
        self.checked(self.apply_price(change, cid=2, reviewed=reviewed))
        self.assertEqual((self.score(), self.score(self.other_id)), (60, 70))
        self.assertEqual(self.receipts(), receipts)
        self.checked(self.buy(self.carol, hint_id=2))
        self.assertEqual(self.score(self.other_id), 50)
        self.assert_room_score(self.alice, 60)
        self.assert_room_score(self.carol, 50)

    def test_free_receipts_can_be_repriced_and_refunded_without_rewriting_original_charges(self):
        with self.app.app_context():
            Hints.query.get(1).cost = 0
            db.session.commit()
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.solve(self.carol, 1))
        self.checked(self.buy())
        originals = self.receipts()
        self.checked(self.apply_price(self.hint_change({1: 25}, existing=[1]), cid=2))
        self.assertEqual(self.score(), 75)
        self.assertEqual(self.receipts(), originals)
        self.checked(self.buy(self.carol))
        self.assertEqual(self.score(self.other_id), 75)
        originals = self.receipts()
        self.checked(self.apply_price(self.hint_change({1: 0}, existing=[1]), cid=2))
        self.assertEqual((self.score(), self.score(self.other_id)), (100, 100))
        self.assertEqual(self.receipts(), originals)

    def test_repeated_hint_prices_preserve_effective_costs_until_retroactively_selected(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.solve(self.carol, 1))
        self.checked(self.buy())
        self.checked(self.apply_price(self.hint_change({1: 80}), cid=2))
        self.checked(self.buy(self.carol))
        self.assertEqual((self.score(), self.score(self.other_id)), (50, 20))
        originals = self.receipts()
        self.checked(self.apply_price(self.hint_change({1: 80}, existing=[1]), cid=2))
        self.assertEqual((self.score(), self.score(self.other_id)), (20, 20))
        self.checked(self.apply_price(self.hint_change({1: 30}, existing=[1]), cid=2))
        self.checked(self.apply_price(self.hint_change({1: 25}), cid=2))
        self.assertEqual((self.score(), self.score(self.other_id)), (70, 70))
        self.checked(self.apply_price(self.hint_change({1: 10}, existing=[1]), cid=2))
        self.assertEqual((self.score(), self.score(self.other_id)), (90, 90))
        self.assertEqual(self.receipts(), originals)

    def test_challenge_reduction_requires_acknowledging_new_negative_balances(self):
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.checked(self.buy())
        history, originals = self.history(), self.receipts()
        change = self.value_change(20, existing=True)
        reviewed = self.review(change)
        risk = reviewed[1]["impact"]
        self.assertEqual(risk["new_negative_overall_teams"], 1)
        self.assertEqual(risk["new_negative_room_teams"], 1)
        self.assertEqual(risk["team_scores"][0]["after_points"], -30)
        for acknowledge in (None, False):
            self.assertEqual(self.apply_price(change, reviewed=reviewed, acknowledge=acknowledge).status_code, 409)
            self.assertEqual(self.score(), 50)
        for malformed in ("true", 1):
            self.assertIn(self.apply_price(change, reviewed=reviewed, acknowledge=malformed).status_code, (400, 409))
            self.assertEqual(self.score(), 50)
        self.checked(self.apply_price(change, reviewed=reviewed, acknowledge=True))
        self.assertEqual(self.score(), -30)
        self.assert_room_score(self.alice, -30)
        self.assertEqual(self.history(), history)
        self.assertEqual(self.receipts(), originals)

    def test_hint_recharge_requires_negative_ack_and_blocks_further_paid_hints(self):
        with self.app.app_context():
            db.session.add(Hints(id=2, challenge_id=2, title="Second clue", content="Synthetic second clue", cost=1))
            db.session.commit()
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.checked(self.buy())
        originals = self.receipts()
        change = self.hint_change({1: 120}, existing=[1])
        reviewed = self.review(change, cid=2)
        self.assertEqual(reviewed[1]["impact"]["new_negative_overall_teams"], 1)
        self.assertEqual(reviewed[1]["impact"]["team_scores"][0]["after_room_points"], -20)
        self.assertEqual(self.apply_price(change, cid=2, reviewed=reviewed).status_code, 409)
        self.checked(self.apply_price(change, cid=2, reviewed=reviewed, acknowledge=True))
        self.assertEqual(self.score(), -20)
        self.assertEqual(self.receipts(), originals)
        denied = self.buy(hint_id=2)
        self.assertEqual(denied.status_code, 400)
        self.assertIn("-20", denied.json["error"])

    def test_retroactive_refund_updates_the_balance_used_for_hint_affordability(self):
        with self.app.app_context():
            db.session.add(Hints(id=2, challenge_id=2, title="Second clue", content="Synthetic second clue", cost=90))
            db.session.commit()
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.checked(self.buy())
        self.assertEqual(self.buy(hint_id=2).status_code, 400)
        self.checked(self.apply_price(self.hint_change({1: 0}, existing=[1]), cid=2))
        self.assertEqual(self.score(), 100)
        self.checked(self.buy(hint_id=2))
        self.assertEqual(self.score(), 10)

    def test_room_only_negative_balance_requires_ack_when_overall_is_positive(self):
        self.checked(self.post(self.admin, P + "/admin/rooms/ansible", {"challenge_ids": [3]}, "PATCH"))
        with self.app.app_context():
            Challenges.query.get(3).value = 200
            db.session.commit()
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.checked(self.buy())
        self.checked(self.post(self.alice, P + "/rooms/ansible/start", {}))
        self.checked(self.solve(self.alice, 3))
        change = self.value_change(20, existing=True)
        reviewed = self.review(change)
        self.assertEqual(reviewed[1]["impact"]["new_negative_overall_teams"], 0)
        self.assertEqual(reviewed[1]["impact"]["new_negative_room_teams"], 1)
        self.assertEqual(self.apply_price(change, reviewed=reviewed).status_code, 409)
        self.checked(self.apply_price(change, reviewed=reviewed, acknowledge=True))
        self.assertEqual(self.score(), 170)
        self.assert_room_score(self.alice, -30)

    def test_purchase_after_review_cannot_introduce_unacknowledged_negative_risk(self):
        self.begin()
        self.checked(self.solve(self.alice, 1))
        change = self.value_change(20, existing=True)
        reviewed = self.review(change)
        self.assertEqual(reviewed[1]["impact"]["new_negative_overall_teams"], 0)
        self.checked(self.buy())
        self.assertEqual(self.apply_price(change, reviewed=reviewed).status_code, 409)
        self.assertEqual(self.score(), 50)
        fresh = self.review(change)
        self.assertEqual(fresh[1]["impact"]["new_negative_overall_teams"], 1)
        self.checked(self.apply_price(change, reviewed=fresh, acknowledge=True))
        self.assertEqual(self.score(), -30)

    def test_solves_after_review_receive_the_selected_retroactive_price(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        change = self.value_change(200, existing=True)
        reviewed = self.review(change)
        self.assertEqual(reviewed[1]["impact"]["teams_with_score_changes"], 1)
        self.checked(self.solve(self.carol, 1))
        self.checked(self.apply_price(change, reviewed=reviewed))
        self.assertEqual((self.score(), self.score(self.other_id)), (200, 200))
        with self.app.app_context():
            audit = QuestAudit.query.filter_by(action="challenge.update").order_by(QuestAudit.id.desc()).first()
            self.assertEqual(audit.detail["maintenance"]["impact"]["teams_with_score_changes"], 2)

    def test_open_hint_quote_is_rejected_after_a_scoped_price_change(self):
        self.begin()
        self.checked(self.solve(self.alice, 1))
        quote = self.checked(self.alice.get("/api/v1/hints/1"))["quote_revision"]
        self.checked(self.apply_price(self.hint_change({1: 60}), cid=2))
        stale = self.alice.open("/api/v1/unlocks", method="POST", json={"type": "hints", "target": 1},
            headers={"CSRF-Token": "quest-test-csrf", "X-Quest-Hint-Quote": quote})
        self.assertEqual(stale.status_code, 409)
        with self.app.app_context():
            self.assertEqual(QuestHintPurchase.query.count(), 0)
        self.checked(self.buy())
        self.assertEqual(self.score(), 40)

    def test_price_scope_is_explicit_and_cannot_target_foreign_hint_ids(self):
        self.begin()
        for change in ({"value": 200}, {"hints": self.hint_change({1: 60})["hints"]}):
            self.assertEqual(self.raw_review(change, cid=2 if "hints" in change else 1).status_code, 409)
        invalid = (None, [], {}, {"challenge_existing": "yes", "hint_existing_ids": []},
                   {"challenge_existing": False, "hint_existing_ids": [True]},
                   {"challenge_existing": False, "hint_existing_ids": [1, 1]},
                   {"challenge_existing": False, "hint_existing_ids": [999]},
                   {"challenge_existing": False, "hint_existing_ids": [1]},
                   {"challenge_existing": True, "hint_existing_ids": [], "unknown": True})
        for pricing in invalid:
            response = self.raw_review({"value": 200, "pricing": pricing})
            self.assertIn(response.status_code, (400, 409), repr(pricing))
        reviewed = self.review(self.value_change(200))
        self.assertEqual(self.apply_price({"value": 200}, reviewed=reviewed).status_code, 409)
        self.assertEqual(self.score(), 0)

    def test_review_binds_scope_and_paused_edits_leave_the_event_paused(self):
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.action("pause")
        change = self.value_change(200)
        reviewed = self.review(change)
        self.assertEqual(self.apply_price(self.value_change(200, existing=True), reviewed=reviewed).status_code, 409)
        self.checked(self.apply_price(change, reviewed=reviewed))
        self.assertEqual(self.score(), 100)
        event = self.checked(self.admin.get(P + "/admin/state"))["event"]
        self.assertEqual(event["status"], "paused")

    def test_recovery_uses_new_cohort_and_unban_restores_original_credit(self):
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.checked(self.apply_price(self.value_change(225)))
        def access(active):
            users = self.checked(self.admin.get(P + "/admin/users"))["users"]
            identity = next(user["identity"] for user in users if user["id"] == 2)
            self.checked(self.post(self.admin, P + "/admin/users/2/access",
                {"active": active, "identity": identity, "confirm": True}, "PATCH"))
        access(False)
        self.assertEqual(self.score(), 0)
        self.checked(self.solve(self.bob, 1))
        self.assertEqual(self.score(), 225)
        access(True)
        self.assertEqual(self.score(), 100)
        self.checked(self.apply_price(self.value_change(175, existing=True)))
        self.assertEqual(self.score(), 175)
        access(False)
        self.assertEqual(self.score(), 175)

    def cohort_history(self):
        self.begin(both=True)
        self.checked(self.solve(self.alice, 1))
        self.checked(self.buy())
        self.checked(self.apply_price(self.value_change(200)))
        self.checked(self.solve(self.carol, 1))
        self.checked(self.buy(self.carol))
        self.checked(self.apply_price(self.hint_change({1: 80}), cid=2))
        self.checked(self.apply_price(self.hint_change({1: 80}, existing=[1]), cid=2))
        self.assertEqual((self.score(), self.score(self.other_id)), (20, 120))

    def test_archive_retains_price_timeline_earned_points_and_original_effective_hint_costs(self):
        self.cohort_history()
        history, receipts = self.history(), self.receipts()
        archived = self.checked(self.post(self.admin, P + "/admin/archives", {}))
        document = archived["document"]
        source = document["data"]
        self.assertEqual(len(source["price_changes"]), 3)
        self.assertEqual([(p["kind"], p["before_value"], p["after_value"], p["apply_existing"])
                         for p in source["price_changes"]],
                         [("challenge", 100, 200, False), ("hint", 50, 80, False), ("hint", 80, 80, True)])
        changes = [event for event in source["timeline"] if event["type"] == "pricing.change"]
        self.assertEqual(len(changes), 3)
        self.assertEqual({row["team_id"]: row["earned_points"] for row in source["scoring_credits"]},
                         {self.team_id: 100, self.other_id: 200})
        self.assertEqual([(row["cost"], row["effective_cost"]) for row in source["hint_purchases"]],
                         [(50, 80), (50, 80)])
        self.assertEqual({row["id"]: row["points"] for row in source["results"]["teams"]},
                         {self.team_id: 20, self.other_id: 120})
        self.assertNotIn("fixture-answer", json.dumps(document))
        self.assertEqual(self.admin.get(P + "/admin/archives/" + str(archived["id"])).json, document)
        self.assertEqual(self.history(), history)
        self.assertEqual(self.receipts(), receipts)

    def test_new_round_archives_cohorts_before_clearing_price_history_and_keeps_current_prices(self):
        self.cohort_history()
        self.action("close")
        previous = self.checked(self.post(self.admin, P + "/admin/archives", {}))
        preview = self.checked(self.admin.get(P + "/admin/round/preview?mode=new_round"))
        self.assertTrue(preview["allowed"])
        self.assertEqual(preview["counts"]["price_changes"], 3)
        reset = self.checked(self.post(self.admin, P + "/admin/round/reset", {
            "revision": preview["revision"], "confirm": True, "event_name": preview["event_name"],
            "reason": "Prepare a fresh synthetic round", "mode": "new_round"}))
        self.assertEqual(reset["cleared"]["price_changes"], 3)
        saved = self.admin.get(P + "/admin/archives/" + str(reset["archive_id"])).json["data"]
        self.assertEqual(len(saved["price_changes"]), 3)
        self.assertEqual({row["team_id"]: row["earned_points"] for row in saved["scoring_credits"]},
                         {self.team_id: 100, self.other_id: 200})
        self.assertEqual([(row["cost"], row["effective_cost"]) for row in saved["hint_purchases"]],
                         [(50, 80), (50, 80)])
        self.assertEqual(self.admin.get(P + "/admin/archives/" + str(previous["id"])).json, previous["document"])
        with self.app.app_context():
            self.assertEqual(QuestPriceChange.query.count(), 0)
            self.assertEqual(QuestHintPurchase.query.count(), 0)
            self.assertEqual(Solves.query.count(), 0)
            self.assertEqual(Challenges.query.get(1).value, 200)
            self.assertEqual(Hints.query.get(1).cost, 80)

    def test_pricing_migration_adds_empty_history_and_preserves_rows_on_rerun(self):
        self.begin()
        self.checked(self.solve(self.alice, 1))
        self.checked(self.buy())
        history, receipts = self.history(), self.receipts()
        migration = importlib.import_module("CTFd.plugins.open_tech_quest.migrations.0013_pricing")
        with self.app.app_context():
            db.session.remove()
            try:
                with db.engine.begin() as connection:
                    QuestPriceChange.__table__.drop(connection)
                    operations = Operations(MigrationContext.configure(connection))
                    migration.upgrade(operations)
                    migration.upgrade(operations)
            finally:
                QuestPriceChange.__table__.create(db.engine, checkfirst=True)
            db.session.rollback()
            self.assertEqual(QuestPriceChange.query.count(), 0)
            self.assertEqual(Challenges.query.get(1).value, 100)
            self.assertEqual(Hints.query.get(1).cost, 50)
        self.assertEqual(self.history(), history)
        self.assertEqual(self.receipts(), receipts)
        self.assertEqual(self.score(), 50)
        self.checked(self.apply_price(self.value_change(225)))
        with self.app.app_context():
            rows = [tuple(row) for row in db.session.execute(QuestPriceChange.__table__.select())]
            self.assertEqual(len(rows), 1)
            db.session.remove()
            with db.engine.begin() as connection:
                migration.upgrade(Operations(MigrationContext.configure(connection)))
            self.assertEqual([tuple(row) for row in db.session.execute(QuestPriceChange.__table__.select())], rows)
            self.assertEqual(Challenges.query.get(1).value, 225)
        self.assertEqual(self.history(), history)
        self.assertEqual(self.receipts(), receipts)
        self.assertEqual(self.score(), 50)
