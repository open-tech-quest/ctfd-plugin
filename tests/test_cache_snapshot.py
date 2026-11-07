"""A concurrent roster change must not poison a later participant read."""
import threading
import unittest
from unittest.mock import patch

import test_integration as integration
from CTFd.models import db
from CTFd.plugins.open_tech_quest import api
from CTFd.plugins.open_tech_quest.models import QuestOnboarding


class CacheSnapshotIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure

    def setUp(self):
        integration.QuestIntegration.setUp(self)
        with self.app.app_context():
            QuestOnboarding.query.get(1).phase = "closed"
            db.session.commit()

    def test_removal_between_auth_and_revisions_cannot_cache_former_roster(self):
        original_sync = api.sync_data
        writer_results = []
        writer_errors = []

        def remove_member():
            try:
                response = self.post(
                    self.admin,
                    integration.PREFIX + f"/admin/teams/{self.team_id}",
                    {"user_ids": [3]},
                    "PATCH",
                )
                writer_results.append((response.status_code, response.json))
            except Exception as error:
                writer_errors.append(error)

        def sync_after_removal(*args, **kwargs):
            # The request guard has read Alice's identity, but sync has not
            # read cache generations. SQLite's legacy SELECT behavior permits
            # the writer to commit here; MariaDB retains its old read snapshot.
            writer = threading.Thread(target=remove_member, daemon=True)
            writer.start()
            writer.join(timeout=10)
            self.assertFalse(writer.is_alive(), "Roster writer did not finish")
            self.assertEqual(writer_errors, [])
            self.assertEqual(len(writer_results), 1)
            self.assertEqual(writer_results[0][0], 200, writer_results[0][1])
            return original_sync(*args, **kwargs)

        with patch.object(api, "sync_data", side_effect=sync_after_removal):
            racing = self.alice.get(integration.PREFIX + "/sync")
        self.assertEqual(racing.status_code, 200, racing.json)

        # A response already in flight may reflect its prior snapshot. A new
        # request must never receive the former team's cached private roster.
        following = self.alice.get(integration.PREFIX + "/sync")
        self.assertEqual(following.status_code, 200, following.json)
        data = following.json["data"]
        self.assertIsNone(data["viewer"]["team_id"])
        self.assertIsNone(data["onboarding"]["team"])
