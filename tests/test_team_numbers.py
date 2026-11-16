"""Native team IDs restart after reset even with a high MariaDB counter."""
import unittest

from CTFd.models import Teams, Users, db
from CTFd.plugins.open_tech_quest.models import QuestAudit
import test_integration as integration
import test_rounds as round_tests

P = integration.PREFIX


class TeamNumberIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    setUp = integration.QuestIntegration.setUp
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    preview = round_tests.RoundIntegration.preview
    reset = round_tests.RoundIntegration.reset

    def checked(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['data']

    def fresh_round(self, mode):
        with self.app.app_context():
            # Advances the native allocator without any ALTER/TRUNCATE. MariaDB
            # retains this high-water mark when reset deletes every team.
            db.session.add(Teams(id=100000, name='Previous round high team ID'))
            db.session.commit()
        preview = self.checked(self.admin.get(P + '/admin/round/preview?mode=' + mode))
        self.checked(self.reset(preview))
        with self.app.app_context():
            self.assertEqual(Teams.query.count(), 0)
            document = QuestAudit.query.filter_by(action='event.archive').order_by(QuestAudit.id.desc()).first().detail['document']
            self.assertIn(100000, [team['id'] for team in document['data']['teams']])
        self.checked(self.post(self.admin, P + '/admin/onboarding', {'team_size': 1}, 'PATCH'))
        self.ids = []
        for index in range(4):
            user = self.checked(self.post(self.admin, '/api/v1/users', {
                'name': f'New participant {index}', 'email': f'new{index}@example.test',
                'password': 'disposable-test-only', 'verified': True, 'type': 'user'}))
            self.ids.append(user['id'])

    def ready(self, ids):
        self.checked(self.post(self.admin, P + '/admin/attendance', {'enabled': True}, 'PATCH'))
        for uid in ids:
            self.checked(self.post(self.client(uid), P + '/onboarding/ready', {}))
        self.checked(self.post(self.admin, P + '/admin/attendance', {'enabled': False}, 'PATCH'))

    def team_ids(self):
        with self.app.app_context():
            return [team.id for team in Teams.query.order_by(Teams.id)]

    def test_both_resets_restart_optimized_and_late_team_ids_without_changing_archives(self):
        for mode in ('new_round', 'defaults'):
            with self.subTest(mode=mode):
                self.fresh_round(mode)
                self.ready(self.ids[:2])
                self.checked(self.post(self.admin, P + '/admin/onboarding/action', {'action': 'close_checkin'}))
                plan = self.checked(self.post(self.admin, P + '/admin/onboarding/optimize', {'preview': True}))
                self.checked(self.post(self.admin, P + '/admin/onboarding/optimize', {'preview': False, 'fingerprint': plan['fingerprint']}))
                self.assertEqual(self.team_ids(), [1, 2])
                self.ready(self.ids[2:3])
                self.checked(self.post(self.admin, P + '/admin/onboarding/action', {
                    'action': 'create_late_team', 'user_ids': self.ids[2:3], 'reason': 'Late checked-in participant'}))
                self.assertEqual(self.team_ids(), [1, 2, 3])
                self.checked(self.post(self.admin, P + '/admin/onboarding/action', {'action': 'finalize'}))
                self.ready(self.ids[3:])
                with self.app.app_context():
                    from CTFd.plugins.open_tech_quest import onboarding
                    from CTFd.plugins.open_tech_quest.service import event
                    fingerprint = onboarding.fingerprint(event())
                self.checked(self.post(self.admin, P + '/admin/onboarding/admit', {
                    'user_ids': self.ids[3:], 'new_team_name': 'Late named team',
                    'reason': 'Admit checked-in late arrival', 'fingerprint': fingerprint}))
                self.assertEqual(self.team_ids(), [1, 2, 3, 4])
                with self.app.app_context():
                    self.assertEqual([Users.query.get(uid).team_id for uid in self.ids[2:]], [3, 4])

    def test_both_resets_restart_legacy_allocation_and_preserve_existing_team_ids(self):
        for mode in ('new_round', 'defaults'):
            with self.subTest(mode=mode):
                self.fresh_round(mode)
                self.ready(self.ids[:2])
                plan = self.checked(self.post(self.admin, P + '/admin/teams/allocate', {'preview': True}))
                self.checked(self.post(self.admin, P + '/admin/teams/allocate', dict(plan, preview=False)))
                self.assertEqual(self.team_ids(), [1, 2])
                self.ready(self.ids[2:3])
                plan = self.checked(self.post(self.admin, P + '/admin/teams/allocate', {'preview': True}))
                self.checked(self.post(self.admin, P + '/admin/teams/allocate', dict(plan, preview=False)))
                self.assertEqual(self.team_ids(), [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
