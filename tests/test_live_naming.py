"""Participant renaming remains a display change during live competition."""
import unittest

from CTFd.models import Solves, Teams, Users, db
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestReadiness, QuestStart
from CTFd.plugins.open_tech_quest.location_models import QuestTeamLocation, QuestVenueRoom
from CTFd.plugins.open_tech_quest.service import standings
import test_integration as integration
import test_roster_flow as roster

P = integration.PREFIX


class LiveNamingIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    for _name in ('client', 'post', 'configure', 'action', 'start', 'solve', 'open_onboarding',
                  'onboarding_action', 'attendance_plan', 'apply_attendance', 'formation_pool',
                  'late_accounts', 'naming_policy'):
        locals()[_name] = getattr(integration.QuestIntegration, _name)
    for _name in ('state', 'registration', 'policy', 'registered_pool', 'registered_teams'):
        locals()[_name] = getattr(roster.RosterFlowIntegration, _name)
    del _name

    def setUp(self):
        integration.QuestIntegration.setUp(self)

    def rename(self, client, name, context=None):
        team = context or client.get(P + '/onboarding').json['data']['team']
        return self.post(client, P + '/onboarding/name', {
            'team_id': team['id'], 'expected_name': team['name'], 'name': name, 'confirm': True})

    def test_live_and_paused_renames_preserve_competition_and_location(self):
        ids, groups = self.registered_teams(3)
        member, teammate = self.client(ids[0]), self.client(ids[1])
        tid = groups[0][0]
        with self.app.app_context():
            db.session.add(QuestVenueRoom(id=1, name='Venue', position=0, table_count=2, unavailable_tables=[]))
            db.session.flush()
            db.session.add(QuestTeamLocation(team_id=tid, venue_room_id=1, table_number=2))
            db.session.commit()
        self.action('start')
        self.start(member)
        self.assertEqual(self.solve(member, 1).json['data']['status'], 'correct')
        with self.app.app_context():
            before = standings()['teams']
            records = [(s.id, s.user_id, s.team_id, s.date) for s in Solves.query.all()]
            starts = [(s.team_id, s.room_id, s.started_at) for s in QuestStart.query.all()]
            event_start = QuestEvent.query.get(1).start_at
        for phase, client, name in [('live', member, 'Live Foxes'), ('paused', teammate, 'Paused Owls')]:
            if phase == 'paused':
                self.action('pause')
            self.assertTrue(client.get(P + '/onboarding').json['data']['can_name'])
            renamed = self.rename(client, name)
            self.assertEqual(renamed.status_code, 200, renamed.get_data(as_text=True))
            self.assertEqual(renamed.json['data']['team']['id'], tid)
            self.assertEqual(member.get(P + '/onboarding').json['data']['team']['name'], name)
            with self.app.app_context():
                actual = standings()['teams']
                for rows in (before, actual):
                    for row in rows:
                        row.pop('name', None)
                        for room in row['room_progress']:
                            room.pop('pause', None)
                self.assertEqual(actual, before)
                self.assertEqual([(s.id, s.user_id, s.team_id, s.date) for s in Solves.query.all()], records)
                self.assertEqual([(s.team_id, s.room_id, s.started_at) for s in QuestStart.query.all()], starts)
                self.assertEqual(QuestEvent.query.get(1).start_at, event_start)
                self.assertEqual({Users.query.get(uid).team_id for uid in ids}, {tid})
                location = QuestTeamLocation.query.get(tid)
                self.assertEqual((location.venue_room_id, location.table_number), (1, 2))
                self.assertEqual(QuestReadiness.query.count(), 0)
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.filter_by(action='onboarding.name').count(), 2)

    def test_live_policy_toggle_and_competing_edits_recheck_current_state(self):
        ids, groups = self.registered_teams(6, naming=False)
        member = self.client(groups[0][1][0])
        teammate = self.client(groups[0][1][1])
        self.action('start')
        self.assertEqual(self.rename(member, 'Disabled').status_code, 409)
        self.naming_policy(True)
        self.assertTrue(member.get(P + '/onboarding').json['data']['can_name'])
        old = member.get(P + '/onboarding').json['data']['team']
        self.assertEqual(self.rename(member, 'First save', old).status_code, 200)
        self.assertEqual(self.rename(teammate, 'Stale overwrite', old).status_code, 409)
        other_name = self.client(groups[1][1][0]).get(P + '/onboarding').json['data']['team']['name']
        self.assertEqual(self.rename(member, other_name.swapcase()).status_code, 409)
        self.naming_policy(False)
        self.assertFalse(member.get(P + '/onboarding').json['data']['can_name'])
        self.assertEqual(self.rename(member, 'Now disabled').status_code, 409)
        for extra in ({'team_size': 9}, {'require_attendance': True}):
            response = self.post(self.admin, P + '/admin/onboarding', dict(extra, allow_team_naming=True), 'PATCH')
            self.assertEqual(response.status_code, 409)
            self.assertFalse(member.get(P + '/onboarding').json['data']['can_name'])
        self.action('pause')
        self.naming_policy(True)
        self.assertEqual(self.rename(teammate, 'Paused save').status_code, 200)
        for phase in ('closed', 'published'):
            with self.app.app_context():
                QuestEvent.query.get(1).status = phase
                db.session.commit()
            self.assertFalse(member.get(P + '/onboarding').json['data']['can_name'])
            self.assertEqual(self.rename(member, 'After close').status_code, 409)
            self.assertEqual(self.post(self.admin, P + '/admin/onboarding', {'allow_team_naming': False}, 'PATCH').status_code, 409)

    def test_live_rename_has_native_csrf_and_team_authorization(self):
        ids, groups = self.registered_teams(6)
        member = self.client(groups[0][1][0])
        other = self.client(groups[1][1][0])
        self.action('start')
        team = member.get(P + '/onboarding').json['data']['team']
        body = {'team_id': team['id'], 'expected_name': team['name'], 'name': 'Unauthorized', 'confirm': True}
        self.assertEqual(member.post(P + '/onboarding/name', json=body).status_code, 403)
        self.assertEqual(self.post(other, P + '/onboarding/name', body).status_code, 409)
        self.assertEqual(self.post(member, f'/api/v1/teams/{team["id"]}', {'name': 'Native bypass'}, 'PATCH').status_code, 403)
        with self.app.app_context():
            self.assertEqual(Teams.query.get(team['id']).name, team['name'])
