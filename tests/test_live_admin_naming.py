"""Organizer corrections are independent of optional participant naming."""
import unittest

from CTFd.models import Solves, Teams, Users, db
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestStart
from CTFd.plugins.open_tech_quest.location_models import QuestTeamLocation, QuestVenueRoom
from CTFd.plugins.open_tech_quest.service import standings
import test_integration as integration
import test_live_naming as live

P = integration.PREFIX


class LiveAdminNamingIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    setUp = integration.QuestIntegration.setUp
    for _name in ('client', 'post', 'configure', 'action', 'start', 'solve', 'open_onboarding',
                  'onboarding_action', 'attendance_plan', 'apply_attendance', 'formation_pool',
                  'late_accounts', 'naming_policy', 'state', 'registration', 'policy',
                  'registered_pool', 'registered_teams'):
        locals()[_name] = getattr(live.LiveNamingIntegration, _name)
    del _name

    def correction(self, tid, name, expected, **extra):
        return self.post(self.admin, P + f'/admin/teams/{tid}', {
            'name': name, 'expected_name': expected, 'reason': 'Agreed organizer correction', **extra}, 'PATCH')

    def test_live_and_paused_corrections_work_with_participant_naming_disabled(self):
        ids, groups = self.registered_teams(3, naming=False)
        tid = groups[0][0]
        with self.app.app_context():
            original_name = Teams.query.get(tid).name
            db.session.add(QuestVenueRoom(id=1, name='Venue', position=0, table_count=2, unavailable_tables=[]))
            db.session.flush()
            db.session.add(QuestTeamLocation(team_id=tid, venue_room_id=1, table_number=2))
            db.session.commit()
        self.action('start')
        self.start(self.client(ids[0]))
        self.assertEqual(self.solve(self.client(ids[0]), 1).json['data']['status'], 'correct')
        with self.app.app_context():
            solves = [(s.id, s.user_id, s.team_id, s.date) for s in Solves.query.all()]
            starts = [(s.team_id, s.room_id, s.started_at) for s in QuestStart.query.all()]
            event_start = QuestEvent.query.get(1).start_at
            captain = Teams.query.get(tid).captain_id
            before = standings()['teams']
        for status, name in [('live', 'Organizer Foxes'), ('paused', 'Organizer Owls')]:
            if status == 'paused':
                self.action('pause')
            response = self.correction(tid, name, original_name)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.json['data']['name'], name)
            original_name = name
            own = self.client(ids[0]).get(P + '/onboarding').json['data']
            self.assertEqual(own['team']['name'], name)
            self.assertFalse(own['can_name'])
            with self.app.app_context():
                self.assertEqual({Users.query.get(uid).team_id for uid in ids}, {tid})
                self.assertEqual(Teams.query.get(tid).captain_id, captain)
                self.assertEqual([(s.id, s.user_id, s.team_id, s.date) for s in Solves.query.all()], solves)
                self.assertEqual([(s.team_id, s.room_id, s.started_at) for s in QuestStart.query.all()], starts)
                self.assertEqual(QuestEvent.query.get(1).start_at, event_start)
                self.assertEqual(QuestTeamLocation.query.get(tid).table_number, 2)
                actual = standings()['teams']
                for rows in (before, actual):
                    for row in rows:
                        row.pop('name', None)
                        for room in row['room_progress']:
                            room.pop('pause', None)
                self.assertEqual(actual, before)
        with self.app.app_context():
            audits = QuestAudit.query.filter_by(action='team.configure').all()
            self.assertEqual(len(audits), 2)
            self.assertTrue(all(a.actor_id == 1 and a.detail['changes']['reason'] for a in audits))

    def test_live_corrections_require_current_name_reason_unique_name_and_admin_csrf(self):
        _, groups = self.registered_teams(6)
        tid, other = groups[0][0], groups[1][0]
        with self.app.app_context():
            old, reserved = Teams.query.get(tid).name, Teams.query.get(other).name
        self.action('start')
        path = P + f'/admin/teams/{tid}'
        body = {'name': 'Corrected name', 'expected_name': old, 'reason': 'Typo correction'}
        self.assertEqual(self.admin.patch(path, json=body).status_code, 403)
        self.assertEqual(self.post(self.client(groups[0][1][0]), path, body, 'PATCH').status_code, 403)
        self.assertEqual(self.post(self.admin, path, {k: v for k, v in body.items() if k != 'expected_name'}, 'PATCH').status_code, 409)
        self.assertEqual(self.post(self.admin, path, {k: v for k, v in body.items() if k != 'reason'}, 'PATCH').status_code, 400)
        self.assertEqual(self.correction(tid, reserved.swapcase(), old).status_code, 409)
        self.assertEqual(self.correction(tid, 'Corrected name', old, reason='').status_code, 400)
        self.assertEqual(self.correction(tid, 'Corrected name', old).status_code, 200)
        self.assertEqual(self.correction(tid, 'Stale overwrite', old).status_code, 409)
        self.assertEqual(self.post(self.admin, path, {'expected_name': 'Corrected name'}, 'PATCH').status_code, 400)

    def test_mixed_live_membership_write_rolls_back_the_name_correction(self):
        _, groups = self.registered_teams(3)
        tid = groups[0][0]
        with self.app.app_context():
            old = Teams.query.get(tid).name
        self.action('start')
        result = self.correction(tid, 'Must roll back', old, user_ids=groups[0][1])
        self.assertEqual(result.status_code, 409)
        with self.app.app_context():
            self.assertEqual(Teams.query.get(tid).name, old)
            self.assertEqual(QuestAudit.query.filter_by(action='team.configure').count(), 0)

    def test_closed_and_published_names_remain_read_only(self):
        _, groups = self.registered_teams(3)
        tid = groups[0][0]
        with self.app.app_context():
            old = Teams.query.get(tid).name
        self.action('start')
        for status in ('closed', 'published'):
            with self.app.app_context():
                QuestEvent.query.get(1).status = status
                db.session.commit()
            self.assertEqual(self.correction(tid, 'Too late', old).status_code, 409)
            with self.app.app_context():
                self.assertEqual(Teams.query.get(tid).name, old)
