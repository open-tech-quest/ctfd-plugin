"""Weighted awards and team hint purchases through real native CTFd routes."""
import csv
import io
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from CTFd.models import Awards, Challenges, Hints, HintUnlocks, Solves, Users, db
from CTFd.utils import set_config
from CTFd.plugins.open_tech_quest.models import QuestEvent, QuestHintPurchase, QuestStart
from CTFd.plugins.open_tech_quest.service import standings
import test_integration as integration


class PointsIntegration(unittest.TestCase):
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
        self.assertEqual(self.post(self.admin, integration.PREFIX + "/admin/rooms/ansible", {"challenge_ids": [3]}, "PATCH").status_code, 200)
        with self.app.app_context():
            Challenges.query.get(2).value = 300
            Challenges.query.get(3).value = 500
            Hints.query.get(1).cost = 50
            db.session.add(Hints(id=2, challenge_id=3, title="Other room", content="Other hint", cost=60))
            db.session.commit()

    def begin(self):
        self.action("start")
        for client in (self.alice, self.carol):
            self.start(client)
            self.assertEqual(self.post(client, integration.PREFIX + "/rooms/ansible/start", {}).status_code, 200)

    def buy(self, client=None, hint_id=1):
        return self.post(client or self.alice, "/api/v1/unlocks", {"type": "hints", "target": hint_id})

    def rows(self):
        with self.app.app_context():
            return {row["id"]: row for row in standings()["teams"]}

    def test_native_values_weighted_overall_and_safe_participant_metadata(self):
        self.begin()
        self.solve(self.alice, 1)
        self.solve(self.alice, 2)
        self.solve(self.carol, 3)
        rows = self.rows()
        self.assertEqual((rows[self.team_id]["total"], rows[self.team_id]["points"], rows[self.team_id]["rank"]), (2, 400, 2))
        self.assertEqual((rows[self.other_id]["total"], rows[self.other_id]["points"], rows[self.other_id]["rank"]), (1, 500, 1))
        payload = self.alice.get(integration.PREFIX + "/bootstrap").json["data"]
        self.assertEqual(payload["rooms"][0]["steps"][1]["value"], 300)
        self.assertEqual(payload["rooms"][0]["team_score"]["points"], 400)
        self.assertEqual(self.alice.get("/api/v1/challenges/2").json["data"]["value"], 300)
        self.assertNotIn("fixture-answer", str(payload))

    def test_hint_deduction_is_shared_idempotent_and_persisted_at_purchase_price(self):
        self.begin()
        self.solve(self.alice, 1)
        self.assertEqual(self.buy().status_code, 200)
        self.assertEqual(self.buy(self.bob).status_code, 200)
        self.assertEqual(self.bob.get("/api/v1/hints/1").json["data"]["content"], "Private hint")
        with self.app.app_context():
            self.assertEqual(QuestHintPurchase.query.count(), 1)
            self.assertEqual(Awards.query.count(), 1)
            self.assertEqual(HintUnlocks.query.count(), 1)
            self.assertEqual(Awards.query.one().value, -50)
            Hints.query.get(1).cost = 99
            db.session.commit()
        row = self.rows()[self.team_id]
        self.assertEqual((row["gross_points"], row["hint_costs"], row["points"]), (100, 50, 50))
        progress = self.bob.get(integration.PREFIX + "/rooms/openshift").json["data"]["progress"]
        self.assertEqual(progress["used_hint_ids"], [1])
        self.assertEqual(progress["used_hint_costs"], {"1": 50})

    def test_used_hints_include_free_receipts_and_are_scoped_to_room_and_team(self):
        with self.app.app_context():
            Hints.query.get(2).cost = 0
            db.session.add(Hints(id=3, challenge_id=2, content="Free second clue", cost=0))
            db.session.commit()
        self.begin()
        self.solve(self.alice, 1)
        for hint_id in (1, 3, 2):
            self.assertEqual(self.buy(hint_id=hint_id).status_code, 200)
        self.assertEqual(self.buy(self.carol, 2).status_code, 200)
        for client, expected in ((self.alice, [1, 3]), (self.bob, [1, 3]), (self.carol, [])):
            rooms = client.get(integration.PREFIX + "/bootstrap").json["data"]["rooms"]
            openshift = next(room for room in rooms if room["slug"] == "openshift")
            ansible = next(room for room in rooms if room["slug"] == "ansible")
            self.assertEqual(openshift["progress"]["used_hint_ids"], expected)
            self.assertEqual(openshift["progress"]["used_hint_costs"], {"1": 50, "3": 0} if expected else {})
            self.assertEqual(ansible["progress"]["used_hint_ids"], [2])
            self.assertEqual(ansible["progress"]["used_hint_costs"], {"2": 0})
        guest = self.guest.get(integration.PREFIX + "/bootstrap").json["data"]
        self.assertTrue(all(room["progress"]["used_hint_ids"] == [] for room in guest["rooms"]))
        public = self.guest.get(integration.PREFIX + "/standings").json["data"]
        self.assertNotIn("used_hint_ids", json.dumps(public))
        self.assertNotIn("used_hint_costs", json.dumps(public))
        self.assertEqual(self.rows()[self.team_id]["points"], 50)

    def test_native_end_revelation_never_marks_an_unpurchased_hint_as_used(self):
        with self.app.app_context():
            Hints.query.get(2).cost = 0
            db.session.commit()
        self.begin()
        self.solve(self.alice, 1)
        self.assertEqual(self.buy(hint_id=2).status_code, 200)
        with self.app.app_context():
            cutoff = datetime.utcnow()
            QuestEvent.query.get(1).end_at = cutoff
            set_config("end", int(cutoff.replace(tzinfo=timezone.utc).timestamp()))
            set_config("view_after_ctf", True)
            db.session.commit()
        # The scheduled cutoff passes while Quest is still live. Native CTFd
        # exposes every hint's content, even though this team never bought #1.
        for phase in ("live", "closed"):
            if phase == "closed":
                self.action("close")
            native = self.alice.get("/api/v1/challenges/2")
            self.assertEqual(native.status_code, 200, native.get_data(as_text=True))
            hint = next(h for h in native.json["data"]["hints"] if h["id"] == 1)
            self.assertEqual(hint["content"], "Private hint")
            room = self.alice.get(integration.PREFIX + "/rooms/openshift").json["data"]
            self.assertEqual(room["progress"]["used_hint_ids"], [])
            self.assertEqual(room["progress"]["used_hint_costs"], {})
            other = self.alice.get(integration.PREFIX + "/rooms/ansible").json["data"]
            self.assertEqual(other["progress"]["used_hint_ids"], [2])
        with self.app.app_context():
            self.assertEqual(HintUnlocks.query.filter_by(target=1).count(), 0)
            self.assertEqual(QuestHintPurchase.query.filter_by(hint_id=1).count(), 0)

    def test_concurrent_same_hint_charges_once_and_different_hints_cannot_overspend(self):
        self.begin()
        self.solve(self.alice, 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda uid: self.buy(self.client(uid)), [2, 3]))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        with self.app.app_context():
            self.assertEqual(QuestHintPurchase.query.count(), 1)
            db.session.add(Hints(id=3, challenge_id=3, content="Third", cost=40))
            Hints.query.get(2).cost = 40
            db.session.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda item: self.buy(self.client(item[0]), item[1]), [(2, 2), (3, 3)]))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 400])
        self.assertEqual(self.rows()[self.team_id]["points"], 10)

    def test_hint_cost_breaks_room_progress_ties_before_time(self):
        self.begin()
        for client in (self.alice, self.carol):
            self.solve(client, 1)
        self.assertEqual(self.buy().status_code, 200)
        for client in (self.alice, self.carol):
            self.solve(client, 2)
        with self.app.app_context():
            base = datetime.utcnow() - timedelta(minutes=10)
            QuestEvent.query.get(1).start_at = base
            for start in QuestStart.query.all():
                start.started_at = base
            for solve in Solves.query.all():
                solve.date = base + timedelta(seconds=(10 * solve.challenge_id if solve.team_id == self.team_id else 20 * solve.challenge_id))
            db.session.commit()
        rows = self.rows()
        red, blue = rows[self.team_id]["room_progress"][0], rows[self.other_id]["room_progress"][0]
        self.assertEqual((red["solved"], red["points"], red["rank"]), (2, 350, 2))
        self.assertEqual((blue["solved"], blue["points"], blue["rank"]), (2, 400, 1))
        self.assertLess(red["milestone_elapsed_ms"], blue["milestone_elapsed_ms"])

    def test_more_room_solves_always_win_and_other_rooms_can_fund_hints(self):
        with self.app.app_context():
            Hints.query.get(1).cost = 350
            db.session.commit()
        self.begin()
        self.solve(self.alice, 3)
        self.solve(self.alice, 1)
        self.assertEqual(self.buy().status_code, 200)
        self.solve(self.alice, 2)
        self.solve(self.carol, 1)
        rows = self.rows()
        red, blue = rows[self.team_id]["room_progress"][0], rows[self.other_id]["room_progress"][0]
        self.assertEqual((red["solved"], red["points"], red["rank"]), (2, 50, 1))
        self.assertEqual((blue["solved"], blue["points"], blue["rank"]), (1, 100, 2))
        self.assertEqual(rows[self.team_id]["room_progress"][1]["hint_costs"], 0)
        self.assertEqual(rows[self.team_id]["points"], 550)

    def test_native_balance_cannot_spend_banned_credit_and_costs_stay_with_team(self):
        self.begin()
        self.solve(self.alice, 1)
        self.assertEqual(self.buy().status_code, 200)
        with self.app.app_context():
            Users.query.get(2).banned = True
            db.session.commit()
        # Hint 2 is in the other started room, so no blocked prerequisite masks
        # the affordability check. Native CTFd still counts Alice's old solve.
        self.assertEqual(self.buy(self.bob, 2).status_code, 400)
        row = self.rows()[self.team_id]
        self.assertEqual((row["points"], row["hint_costs"], row["rank"]), (-50, 50, None))

    def test_free_hints_cutoff_and_native_price_charge_guards(self):
        self.begin()
        with self.app.app_context():
            Hints.query.get(2).cost = 0
            db.session.commit()
        self.assertEqual(self.buy(hint_id=2).status_code, 200)
        self.solve(self.alice, 1)
        self.assertEqual(self.buy().status_code, 200)
        with self.app.app_context():
            receipt = QuestHintPurchase.query.filter_by(hint_id=1).one()
            award_id = receipt.award_id
        for path, body, method in [
            ("/api/v1/challenges/1", {"value": 999}, "PATCH"),
            ("/api/v1/hints/1", {"cost": 10}, "PATCH"),
            ("/api/v1/hints/1", {}, "DELETE"),
            (f"/api/v1/awards/{award_id}", {}, "DELETE"),
        ]:
            self.assertEqual(self.post(self.admin, path, body, method).status_code, 409, path)
        self.action("pause")
        with self.app.app_context():
            db.session.add(Hints(id=3, challenge_id=3, content="Paused", cost=0))
            db.session.commit()
        self.assertEqual(self.buy(hint_id=3).status_code, 409)
        self.action("resume")
        self.action("close")
        self.assertEqual(self.buy(hint_id=3).status_code, 409)

    def test_hidden_withdrawal_snapshot_and_csv_use_same_net_score(self):
        self.begin()
        self.solve(self.alice, 1)
        self.buy()
        self.solve(self.alice, 2)
        self.assertEqual(self.rows()[self.team_id]["points"], 350)
        self.assertEqual(self.post(self.admin, "/api/v1/challenges/2", {"state": "hidden"}, "PATCH").status_code, 200)
        self.assertEqual(self.rows()[self.team_id]["points"], 100)
        self.post(self.admin, "/api/v1/challenges/2", {"state": "visible"}, "PATCH")
        self.action("close")
        self.action("publish")
        before = self.guest.get(integration.PREFIX + "/standings").json["data"]
        with self.app.app_context():
            Challenges.query.get(1).value = 999
            db.session.commit()
        self.assertEqual(self.guest.get(integration.PREFIX + "/standings").json["data"]["teams"], before["teams"])
        rows = list(csv.DictReader(io.StringIO(self.admin.get(integration.PREFIX + "/admin/export").get_data(as_text=True))))
        row = next(r for r in rows if r["competition"] == "overall" and int(r["team_id"]) == self.team_id)
        self.assertEqual((row["net_points"], row["hint_costs"], row["scoring_version"]), ("350", "50", "2"))
        # A legacy published snapshot stays count-based; no invented zero score.
        with self.app.app_context():
            e = QuestEvent.query.get(1)
            legacy = dict(e.snapshot)
            legacy.pop("scoring_version")
            for team in legacy["teams"]:
                for key in ("points", "gross_points", "hint_costs"):
                    team.pop(key)
            e.snapshot = legacy
            db.session.commit()
            restored = standings()
            self.assertEqual(restored["scoring_version"], 1)
            self.assertNotIn("points", restored["teams"][0])

    def test_native_legacy_hint_receipts_upgrade_once_using_recorded_amount(self):
        self.begin()
        self.solve(self.alice, 1)
        self.buy()
        with self.app.app_context():
            QuestHintPurchase.query.delete()
            Hints.query.get(1).cost = 999
            db.session.commit()
            upgrade = importlib.import_module("CTFd.plugins.open_tech_quest.migrations.0010_points").upgrade
            for _ in range(2):
                upgrade(Operations(MigrationContext.configure(db.session.connection())))
                db.session.commit()
            self.assertEqual(QuestHintPurchase.query.count(), 1)
            self.assertEqual(QuestHintPurchase.query.one().cost, 50)

    def test_unlock_target_aliases_cannot_bypass_purchase_or_access_checks(self):
        self.begin()
        self.solve(self.alice, 1)
        for value in (1.0, True, None, [], {}, -1, 0, "1.0", "-1", "", 2147483648):
            response = self.buy(hint_id=value)
            self.assertEqual(response.status_code, 400, repr(value))
        for value in ("1", "01", 1):
            self.assertEqual(self.buy(hint_id=value).status_code, 200)
        with self.app.app_context():
            self.assertEqual(QuestHintPurchase.query.count(), 1)
            self.assertEqual(HintUnlocks.query.count(), 1)
            self.assertEqual(Awards.query.one().value, -50)

    def test_native_hint_reassignment_is_frozen_and_missing_csrf_never_charges(self):
        self.begin()
        self.solve(self.alice, 1)
        response = self.post(self.admin, "/api/v1/hints/1", {"challenge_id": 3}, "PATCH")
        self.assertEqual(response.status_code, 409)
        response = self.alice.post("/api/v1/unlocks", json={"type": "hints", "target": 1})
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(QuestHintPurchase.query.count(), 0)
            self.assertEqual(Awards.query.count(), 0)

    def test_native_url_aliases_cannot_change_prices_or_skip_content_guards(self):
        self.begin()
        self.solve(self.alice, 1)
        for alias in ("1.0", "+1", "1e0", "1 ", "1" * 5000):
            self.assertEqual(self.post(self.admin, "/api/v1/challenges/" + alias, {"value": 999}, "PATCH").status_code, 400)
            self.assertEqual(self.post(self.admin, "/api/v1/hints/" + alias, {"cost": 0}, "PATCH").status_code, 400)
            self.assertEqual(self.alice.get("/api/v1/challenges/" + alias).status_code, 400)
        for kind in (None, [], {}, 1):
            self.assertEqual(self.post(self.alice, "/api/v1/unlocks", {"type": kind, "target": 1}).status_code, 400)
        with self.app.app_context():
            self.assertEqual(Challenges.query.get(1).value, 100)
            self.assertEqual(Hints.query.get(1).cost, 50)

    def test_legacy_upgrade_refuses_an_ambiguous_charge_instead_of_guessing(self):
        self.begin()
        self.solve(self.alice, 1)
        self.buy()
        with self.app.app_context():
            receipt = QuestHintPurchase.query.one()
            award = Awards.query.get(receipt.award_id)
            db.session.add(Awards(user_id=award.user_id, team_id=award.team_id,
                                 name=award.name, category=award.category,
                                 value=award.value, date=award.date))
            QuestHintPurchase.query.delete()
            db.session.commit()
            upgrade = importlib.import_module("CTFd.plugins.open_tech_quest.migrations.0010_points").upgrade
            with self.assertRaisesRegex(RuntimeError, "original charge cannot be linked uniquely"):
                upgrade(Operations(MigrationContext.configure(db.session.connection())))
            db.session.rollback()
            self.assertEqual(QuestHintPurchase.query.count(), 0)
