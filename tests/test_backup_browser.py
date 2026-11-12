"""Lazy browsing against a disposable worker and private synthetic index."""
import copy
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import test_backup_worker as fixtures

ACTOR, worker = fixtures.ACTOR, fixtures.worker


class BackupBrowser(unittest.TestCase):
    backup = fixtures.BackupWorker.backup

    def test_legacy_empty_query_does_not_depend_on_runtime_strict_parser(self):
        # The deployed worker's Python 3.9 raises on an empty strict query.
        with patch.object(worker, "parse_qs", side_effect=ValueError("empty strict query")) as parser:
            self.assertEqual(worker.browse_query("", {"view"}), {})
            value = self.get("/v1/backups", expected=200)
            self.assertIn("snapshots", value)
            parser.assert_not_called()

    def setUp(self):
        fixtures.BackupWorker.setUp(self)
        self.config.update(instance_name="Synthetic event", adapter={"route_host": "event.example.test"})
        original = self.backup()
        self.template = self.profile.snapshot_record(original)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), worker.Handler)
        self.server.profiles = [self.profile]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.headers = {"Authorization": "Bearer " + self.profile.token,
                        "X-Quest-Actor": json.dumps(ACTOR)}

    def clear_index(self):
        for path in (self.profile.root / "snapshots").glob("*.json"):
            path.unlink()

    def record(self, number, created_at, legacy=False):
        # Clone one real synthetic capture's metadata shape. These entries are
        # for browsing only and never claim to contain signed restore bundles.
        value = copy.deepcopy(self.template)
        identifier = f"{number:032x}"
        value["manifest"].update(snapshot_id=identifier, created_at=created_at)
        value.update(storage_id=identifier, private_fixture_marker="do-not-publish-this-field")
        if legacy:
            value["manifest"]["legacy"] = True
            value["origin"] = "legacy"
            value["signing_key_id"] = None
        worker.write_json(self.profile.root / "snapshots" / (identifier + ".json"), value)
        return identifier

    def get(self, path, *, actor=ACTOR, expected=200):
        headers = dict(self.headers, **{"X-Quest-Actor": json.dumps(actor)})
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, expected)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            return json.load(response)

    def post(self, path, body, expected=200):
        request = urllib.request.Request(self.base + path, headers=dict(self.headers, **{"Content-Type": "application/json"}),
                                         data=json.dumps(body).encode(), method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, expected)
            return json.load(response)

    def page(self, day, cursor=None, limit=20):
        values = {"day": day, "limit": str(limit)}
        if cursor is not None:
            values["before"] = cursor
        return self.get("/v1/backups/snapshots?" + urllib.parse.urlencode(values))

    def test_thousand_points_return_counts_and_requested_page_without_live_probe(self):
        self.clear_index()
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for number in range(1000):
            self.record(number, (start + timedelta(minutes=15 * number)).isoformat())
        with patch.object(self.adapter, "current", side_effect=AssertionError("Live compatibility belongs to restore review")), \
                patch.object(self.adapter, "sync_inventory", side_effect=AssertionError("Browsing must not inspect Restic"), create=True) as inventory, \
                patch.object(self.adapter, "actor_valid", wraps=self.adapter.actor_valid) as authority:
            summary = self.profile.overview(ACTOR)
            day = self.profile.day_page(ACTOR, "2026-11-10")
            self.assertGreaterEqual(authority.call_count, 2)
            inventory.assert_not_called()
        self.assertNotIn("snapshots", summary)
        self.assertNotIn("current", summary["target"])
        self.assertEqual(sum(group["count"] for group in summary["groups"]), 1000)
        self.assertEqual(sum(item["count"] for group in summary["groups"] for item in group["days"]), 1000)
        self.assertLess(len(json.dumps(summary)), 5000)
        self.assertEqual(day["total"], 96)
        self.assertEqual(len(day["snapshots"]), 20)
        self.assertTrue(day["next_cursor"])
        self.assertEqual(day["snapshots"][0]["compatibility"], {"supported": False, "reason": None, "checked": False})
        self.assertNotIn("do-not-publish-this-field", json.dumps(day))
        self.assertNotIn("manifest", day["snapshots"][0])
        self.assertNotIn("storage_id", day["snapshots"][0])

    def test_groups_use_utc_days_and_sort_across_year_and_month_boundaries(self):
        self.clear_index()
        points = [(1, "2026-12-31T23:45:00Z"), (2, "2027-01-01T00:00:00Z"),
                  (3, "2027-01-01T01:00:00+01:00"), (4, "2026-12-31T19:30:00-05:00"),
                  (5, "2026-11-30T23:59:00Z"), (6, "2026-12-01T00:01:00Z")]
        for number, stamp in points:
            self.record(number, stamp)
        summary = self.get("/v1/backups?view=summary")
        self.assertEqual([group["key"] for group in summary["groups"]], ["2027-01", "2026-12", "2026-11"])
        self.assertEqual([group["count"] for group in summary["groups"]], [3, 2, 1])
        self.assertEqual(summary["groups"][0]["days"], [{"key": "2027-01-01", "count": 3}])
        day = self.page("2027-01-01")
        self.assertEqual([row["id"] for row in day["snapshots"]], [f"{n:032x}" for n in [4, 3, 2]])

    def test_cursor_ties_survive_new_capture_and_deleted_boundary_without_duplicates(self):
        self.clear_index()
        original = [self.record(n, "2026-11-12T09:00:00Z") for n in range(55)]
        expected = list(reversed(original))
        first = self.page("2026-11-12")
        self.assertEqual([row["id"] for row in first["snapshots"]], expected[:20])
        newer = self.record(999, "2026-11-12T09:15:00Z")
        (self.profile.root / "snapshots" / (expected[19] + ".json")).unlink()
        unseen_deleted = expected[28]
        (self.profile.root / "snapshots" / (unseen_deleted + ".json")).unlink()
        seen = [row["id"] for row in first["snapshots"]]
        cursor = first["next_cursor"]
        while cursor:
            following = self.page("2026-11-12", cursor)
            self.assertLessEqual(len(following["snapshots"]), 20)
            seen.extend(row["id"] for row in following["snapshots"])
            cursor = following["next_cursor"]
        self.assertEqual(seen, [identifier for identifier in expected if identifier != unseen_deleted])
        self.assertEqual(len(seen), len(set(seen)))
        refreshed = self.page("2026-11-12")
        self.assertEqual(refreshed["snapshots"][0]["id"], newer)
        self.assertNotEqual(first["catalog_revision"], refreshed["catalog_revision"])

    def test_every_summary_and_day_read_revalidates_administrator(self):
        self.get("/v1/backups?view=summary")
        self.page("2026-11-12")
        self.adapter.authorized = False
        self.get("/v1/backups?view=summary", expected=403)
        self.get("/v1/backups/snapshots?day=2026-11-12", expected=403)
        self.get("/v1/backups?view=summary", actor={"id": 1}, expected=403)

    def test_query_validation_refuses_unbounded_or_ambiguous_reads(self):
        queries = ["", "day=2026-02-30", "day=2026-1-2", "day=not-a-day", "day=2026-11-12&day=2026-11-13",
                   "day=2026-11-12&limit=0", "day=2026-11-12&limit=51", "day=2026-11-12&limit=-1",
                   "day=2026-11-12&limit=1.5", "day=2026-11-12&limit=20&limit=30",
                   "day=2026-11-12&unknown=anything", "day=2026-11-12&before=not-a-cursor",
                   "day=2026-11-12&before=" + "a" * 5000, "day=2026-11-12&before=a&before=b"]
        for query in queries:
            with self.subTest(query=query[:100]):
                self.get("/v1/backups/snapshots?" + query, expected=400)
        self.get("/v1/backups?view=summary&view=summary", expected=400)
        self.get("/v1/backups?view=unknown", expected=400)
        self.get("/v1/backups?view=summary&unknown=value", expected=400)

    def test_empty_or_expired_day_is_valid_and_cursor_cannot_cross_days(self):
        self.clear_index()
        summary = self.get("/v1/backups?view=summary")
        self.assertEqual(summary["groups"], [])
        empty = self.page("2026-11-12")
        self.assertEqual(empty["snapshots"], [])
        self.assertEqual(empty["total"], 0)
        self.assertIsNone(empty["next_cursor"])
        for number in range(21):
            self.record(number, "2026-11-12T09:00:00Z")
        cursor = self.page("2026-11-12")["next_cursor"]
        self.get("/v1/backups/snapshots?" + urllib.parse.urlencode({"day": "2026-11-13", "before": cursor}), expected=400)

    def test_summary_keeps_failed_schedule_visible_after_manual_success(self):
        self.adapter.fail = "capture"
        failed = self.profile.enqueue("backup", None, {"operation_id": str(uuid.uuid4()),
                                      "scheduled": True}, synchronous=True)
        self.assertEqual(failed["job"]["status"], "failed")
        self.adapter.fail = None
        self.backup()
        overview = self.profile.overview(ACTOR)
        self.assertEqual(overview["schedule"]["last_job"]["id"], failed["job"]["id"])
        self.assertEqual(overview["schedule"]["last_job"]["status"], "failed")
        self.assertIsNone(overview["schedule"]["last_success_at"])

    def test_unchecked_metadata_does_not_bypass_fresh_restore_compatibility(self):
        identifier = self.template["manifest"]["snapshot_id"]
        day = self.template["manifest"]["created_at"][:10]
        row = self.page(day)["snapshots"][0]
        self.assertFalse(row["compatibility"]["checked"])
        reviewed = self.post("/v1/backups/" + identifier + "/review", {})
        self.assertTrue(reviewed["snapshot"]["compatibility"]["supported"])
        self.adapter.source["schema_revision"] = "unqualified-next-schema"
        self.post("/v1/backups/" + identifier + "/review", {}, expected=409)
        self.assertNotIn("restore", self.adapter.events)

    def test_legacy_metadata_stays_operator_only_and_fresh_review_refuses_it(self):
        self.clear_index()
        identifier = self.record(1, "2026-11-12T09:00:00Z", legacy=True)
        row = self.page("2026-11-12")["snapshots"][0]
        self.assertFalse(row["compatibility"]["supported"])
        self.assertTrue(row["compatibility"]["checked"])
        self.assertIn("operator", row["compatibility"]["reason"])
        self.post("/v1/backups/" + identifier + "/review", {}, expected=409)
        self.assertNotIn("restore", self.adapter.events)


if __name__ == "__main__":
    unittest.main()
