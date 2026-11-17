"""Reviewed arrival batches preserve published teams, progress and locations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from CTFd.models import Submissions, Solves, Teams, Users, db
from CTFd.plugins.open_tech_quest import waiting
from CTFd.plugins.open_tech_quest.location_models import QuestTeamLocation, QuestVenueRoom
from CTFd.plugins.open_tech_quest.models import (QuestAudit, QuestEvent, QuestExclusion,
    QuestMaintainer, QuestOnboarding, QuestReadiness, QuestStart)
import test_integration as integration
import test_roster_flow as roster

P = integration.PREFIX
PATH = P + '/admin/onboarding/waiting'


class WaitingPlacementIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    for _name in ('client', 'post', 'configure', 'action', 'start', 'solve', 'open_onboarding',
                  'attendance_window', 'ready_user', 'onboarding_action', 'attendance_plan',
                  'apply_attendance', 'formation_pool', 'form_ready_pool', 'late_accounts', 'late_status', 'admit', 'naming_policy'):
        locals()[_name] = getattr(integration.QuestIntegration, _name)
    for _name in ('state', 'registration', 'policy', 'registered_pool', 'registered_teams', 'move'):
        locals()[_name] = getattr(roster.RosterFlowIntegration, _name)
    del _name

    def setUp(self):
        integration.QuestIntegration.setUp(self)

    def prepared(self, count=3, capacity=8):
        result = self.registered_teams(count, capacity=capacity, naming=False)
        self.registration(True)
        return result

    def people(self, count, first=200):
        ids = list(range(first, first + count))
        with self.app.app_context():
            password = Users.query.get(2).password
            db.session.execute(Users.__table__.insert(), [
                {'id': uid, 'name': f'Arrival {uid}', 'email': f'arrival-{uid}@example.test',
                 'password': password, 'type': 'user', 'verified': True} for uid in ids])
            db.session.commit()
        return ids

    def preview(self):
        response = self.post(self.admin, PATH, {'preview': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn('no-store', response.headers['Cache-Control'])
        return response.json['data']

    def apply(self, plan, client=None):
        return self.post(client or self.admin, PATH, {
            'preview': False, 'token': plan['token'], 'reason': 'Event arrival batch'})

    def distribute(self, apply=False, plan=None):
        revision = plan['revision'] if plan else self.admin.get(P + '/admin/locations').json['data']['revision']
        response = self.post(self.admin, P + '/admin/locations/distribution', {
            'revision': revision, 'strategy': 'spread', 'apply': apply, 'reassign_existing': False})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['data']

    def test_98_then_250_keeps_published_members_and_tables_and_seats_only_new_teams(self):
        original, groups = self.prepared(98)
        self.assertEqual(len(groups), 13)
        with self.app.app_context():
            for position, count in enumerate((14, 13, 15)):
                db.session.add(QuestVenueRoom(name=f'Venue {position + 1}', position=position,
                                             table_count=count, unavailable_tables=[]))
            db.session.commit()
        self.distribute(True, self.distribute())
        with self.app.app_context():
            old_members = {u.id: u.team_id for u in Users.query.filter(Users.id.in_(original))}
            old_names = {t.id: t.name for t in Teams.query.all()}
            old_tables = {r.team_id: (r.venue_room_id, r.table_number) for r in QuestTeamLocation.query.all()}
        self.assertEqual(len(old_tables), 13)
        arrivals = self.people(152)
        plan = self.preview()
        self.assertEqual([p['id'] for p in plan['participants']], arrivals)
        self.assertEqual(sum(g['team_id'] is None for g in plan['groups']), 19)
        self.assertEqual(sum(len(g['user_ids']) for g in plan['groups'] if g['team_id']), 6)
        old_location_preview = self.distribute()
        later = self.people(2, 10000)
        response = self.apply(plan)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual({u.id: u.team_id for u in Users.query.filter(Users.id.in_(original))}, old_members)
            self.assertTrue(all(Teams.query.get(tid).name == name for tid, name in old_names.items()))
            self.assertEqual({r.team_id: (r.venue_room_id, r.table_number) for r in QuestTeamLocation.query.all()}, old_tables)
            self.assertTrue(all(Users.query.get(uid).team_id is None for uid in later))
            self.assertTrue(all(Users.query.get(uid).team_id is not None for uid in arrivals))
            self.assertEqual(Teams.query.count(), 32)
            self.assertEqual(sorted(len(t.members) for t in Teams.query.all()), [7] * 6 + [8] * 26)
            self.assertEqual(QuestReadiness.query.count(), 0)
            self.assertEqual(QuestStart.query.count(), 0)
            self.assertEqual(Solves.query.count(), 0)
            self.assertIsNone(QuestEvent.query.get(1).start_at)
        stale = self.post(self.admin, P + '/admin/locations/distribution', {
            'revision': old_location_preview['revision'], 'strategy': 'spread',
            'apply': True, 'reassign_existing': False})
        self.assertEqual(stale.status_code, 409)
        locations = self.distribute()
        self.assertEqual(len(locations['assignments']), 19)
        self.distribute(True, locations)
        with self.app.app_context():
            seated = {r.team_id: (r.venue_room_id, r.table_number) for r in QuestTeamLocation.query.all()}
            self.assertEqual(len(seated), 32)
            self.assertEqual(len(set(seated.values())), 32)
            self.assertEqual({tid: seated[tid] for tid in old_tables}, old_tables)

    def test_new_signups_do_not_invalidate_and_successful_reviews_cannot_replay(self):
        self.prepared()
        ids = self.people(12)
        plan = self.preview()
        self.people(2, 10000)
        with self.app.app_context():
            db.session.add(Teams(id=99, name='Unrelated team'))
            db.session.commit()
        result = self.apply(plan)
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(self.apply(plan).status_code, 409)
        with self.app.app_context():
            self.assertTrue(all(Users.query.get(uid).team_id for uid in ids))
            self.assertIsNone(Users.query.get(10000).team_id)
            self.assertEqual(QuestAudit.query.filter_by(action='onboarding.admit_batch').count(), 1)

    def test_authority_csrf_and_unreviewed_client_groups_are_rejected(self):
        self.prepared()
        self.people(2)
        for client in (self.guest, self.alice):
            self.assertIn(self.post(client, PATH, {'preview': True}).status_code, (401, 403))
        self.assertEqual(self.admin.post(PATH, json={'preview': True}).status_code, 403)
        self.assertEqual(self.post(self.admin, PATH, {'preview': True, 'groups': []}).status_code, 400)
        plan = self.preview()
        self.assertEqual(self.apply(plan, self.alice).status_code, 403)
        self.assertEqual(self.post(self.admin, PATH, {'preview': False, 'token': plan['token'], 'reason': ''}).status_code, 400)
        altered = dict(plan, token=plan['token'] + 'tampered')
        self.assertEqual(self.apply(altered).status_code, 409)

    def test_expired_and_other_administrator_reviews_are_rejected(self):
        self.prepared()
        self.people(2)
        plan = self.preview()
        from itsdangerous import TimestampSigner
        stamp = TimestampSigner.get_timestamp(None)
        with patch.object(TimestampSigner, 'get_timestamp', return_value=stamp + 901):
            self.assertEqual(self.apply(plan).status_code, 409)
        with self.app.app_context():
            db.session.add(Users(id=10000, name='Other admin', email='other-admin@example.test',
                                 password='test-only', type='admin', verified=True))
            db.session.commit()
        self.assertEqual(self.apply(plan, self.client(10000)).status_code, 409)

    def test_selected_change_rejects_the_whole_batch(self):
        self.prepared()
        ids = self.people(3)
        plan = self.preview()
        with self.app.app_context():
            Users.query.get(ids[-1]).banned = True
            db.session.commit()
        self.assertEqual(self.apply(plan).status_code, 409)
        with self.app.app_context():
            self.assertTrue(all(Users.query.get(uid).team_id is None for uid in ids))
            self.assertEqual(Teams.query.count(), 1)

    def test_target_membership_name_and_location_changes_reject_without_overwriting(self):
        self.prepared()
        ids = self.people(3)
        with self.app.app_context():
            db.session.add(QuestVenueRoom(id=1, name='Venue', position=0, table_count=5, unavailable_tables=[]))
            db.session.flush()
            db.session.add(QuestTeamLocation(team_id=1, venue_room_id=1, table_number=1))
            db.session.commit()
        plan = self.preview()
        with self.app.app_context():
            QuestTeamLocation.query.get(1).table_number = 2
            db.session.commit()
        self.assertEqual(self.apply(plan).status_code, 409)
        plan = self.preview()
        with self.app.app_context():
            Teams.query.get(1).name = 'Renamed by organizer'
            db.session.commit()
        self.assertEqual(self.apply(plan).status_code, 409)
        plan = self.preview()
        self.people(1, 10000)
        self.assertEqual(self.admit([10000], team_id=1).status_code, 200)
        self.assertEqual(self.apply(plan).status_code, 409)
        with self.app.app_context():
            self.assertTrue(all(Users.query.get(uid).team_id is None for uid in ids))
            self.assertEqual(QuestTeamLocation.query.get(1).table_number, 2)

    def test_new_name_conflict_and_mid_apply_failure_roll_back_every_assignment(self):
        self.prepared()
        ids = self.people(12)
        plan = self.preview()
        proposed = next(g['name'] for g in plan['groups'] if g['team_id'] is None)
        with self.app.app_context():
            db.session.add(Teams(id=99, name=proposed))
            db.session.commit()
        self.assertEqual(self.apply(plan).status_code, 409)
        plan = self.preview()
        with patch('CTFd.plugins.open_tech_quest.api.audit', side_effect=RuntimeError('synthetic failure after writes')):
            with self.assertRaises(RuntimeError):
                self.apply(plan)
        with self.app.app_context():
            self.assertTrue(all(Users.query.get(uid).team_id is None for uid in ids))
            self.assertEqual(Teams.query.count(), 2)
            self.assertEqual(QuestAudit.query.filter_by(action='onboarding.admit_batch').count(), 0)

    def test_live_progress_does_not_stale_a_batch_or_move_old_members(self):
        original, _ = self.prepared()
        self.people(2)
        self.action('start')
        plan = self.preview()
        self.start(self.alice)
        self.assertEqual(self.solve(self.alice, 1).json['data']['status'], 'correct')
        with self.app.app_context():
            stamp = QuestEvent.query.get(1).start_at
            started = QuestStart.query.one().started_at
        self.assertEqual(self.apply(plan).status_code, 200)
        with self.app.app_context():
            self.assertEqual({Users.query.get(uid).team_id for uid in original}, {1})
            self.assertEqual(Solves.query.count(), 1)
            self.assertEqual(QuestStart.query.one().started_at, started)
            self.assertEqual(QuestEvent.query.get(1).start_at, stamp)
            self.assertEqual(QuestEvent.query.get(1).status, 'live')

    def test_attendance_policy_is_respected_without_fabricating_checkins(self):
        self.prepared()
        ids = self.people(4)
        with self.app.app_context():
            from CTFd.utils import set_config
            set_config('quest_require_attendance', True)
            for uid in (2, 3, 4, ids[0], ids[1]):
                db.session.add(QuestReadiness(user_id=uid, late=uid in ids))
            db.session.commit()
        plan = self.preview()
        self.assertEqual([p['id'] for p in plan['participants']], ids[:2])
        with self.app.app_context():
            db.session.add(QuestReadiness(user_id=ids[2], late=True))
            db.session.commit()
        self.assertEqual(self.apply(plan).status_code, 200)
        with self.app.app_context():
            self.assertIsNone(Users.query.get(ids[2]).team_id)
            self.assertIsNone(QuestReadiness.query.get(ids[3]))

    def test_live_registration_late_attendance_new_teams_and_locations(self):
        original = self.formation_pool(3, capacity=5)
        self.apply_attendance(self.form_ready_pool(original))
        self.assertEqual(self.onboarding_action('finalize').status_code, 200)
        with self.app.app_context():
            db.session.add(QuestVenueRoom(id=1, name='Main room', position=0,
                                         table_count=3, unavailable_tables=[]))
            db.session.commit()
        self.distribute(True, self.distribute())
        self.registration(True)
        self.action('start')
        self.start(self.alice)
        self.assertEqual(self.solve(self.alice, 1).json['data']['status'], 'correct')
        self.assertTrue(self.attendance_window(True)['late_only'])
        arrivals = []
        for number in range(4):
            client = self.app.test_client()
            self.assertEqual(client.get('/register').status_code, 200)
            with client.session_transaction() as session:
                nonce = session['nonce']
            response = client.post('/register', data={'nonce': nonce,
                'name': f'Live arrival {number}', 'email': f'live-arrival-{number}@example.test',
                'password': 'test-only'})
            self.assertEqual(response.status_code, 302)
            with client.session_transaction() as session:
                uid, nonce = session['id'], session['nonce']
            ready = client.post(P + '/onboarding/ready', json={},
                                headers={'CSRF-Token': nonce, 'X-Quest-Attendance-UI': '1'})
            self.assertEqual(ready.status_code, 200, ready.get_data(as_text=True))
            self.assertTrue(ready.json['data']['late'])
            arrivals.append(uid)
        plan = self.preview()
        self.assertEqual({p['id'] for p in plan['participants']}, set(arrivals))
        self.assertEqual(sum(g['team_id'] is None for g in plan['groups']), 1)
        self.assertEqual(self.apply(plan).status_code, 200)
        locations = self.distribute()
        self.assertEqual(len(locations['assignments']), 1)
        self.distribute(True, locations)
        with self.app.app_context():
            self.assertEqual(QuestEvent.query.get(1).status, 'live')
            self.assertEqual(Solves.query.count(), 1)
            self.assertEqual(QuestStart.query.count(), 1)
            self.assertEqual(QuestTeamLocation.query.get(1).table_number, 1)
            self.assertEqual(QuestTeamLocation.query.count(), 2)
            self.assertEqual({Users.query.get(uid).team_id for uid in original}, {1})
            self.assertEqual({Users.query.get(uid).team_id for uid in arrivals}, {1, 2})
            self.assertTrue(all(QuestReadiness.query.get(uid).late for uid in arrivals))

    def test_staff_bans_history_and_excluded_destinations_are_filtered(self):
        self.prepared()
        ids = self.people(5)
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=ids[0], previous_hidden=False))
            Users.query.get(ids[1]).banned = True
            db.session.add(Submissions(user_id=ids[2], team_id=1, challenge_id=1, type='incorrect', provided='synthetic history'))
            db.session.add(QuestExclusion(team_id=1, reason='Not competing'))
            db.session.commit()
        plan = self.preview()
        self.assertEqual([p['id'] for p in plan['participants']], ids[3:])
        self.assertTrue(all(g['team_id'] is None for g in plan['groups']))
        self.assertEqual(self.apply(plan).status_code, 200)

    def test_invalid_phases_cutoff_and_future_event_phase_require_new_preview(self):
        self.prepared()
        self.people(2)
        plan = self.preview()
        for status, phase, end in [('closed', 'finalized', None), ('published', 'finalized', None),
                                    ('draft', 'naming', None), ('draft', 'finalized', datetime.utcnow() - timedelta(seconds=1))]:
            with self.app.app_context():
                e = QuestEvent.query.get(1);e.status = status;e.end_at = end
                QuestOnboarding.query.get(1).phase = phase
                db.session.commit()
            self.assertEqual(self.post(self.admin, PATH, {'preview': True}).status_code, 409)
            self.assertEqual(self.apply(plan).status_code, 409)

    def test_empty_queue_and_large_batches_are_bounded(self):
        self.prepared()
        self.assertEqual(self.preview()['participants'], [])
        self.people(1002)
        plan = self.preview()
        self.assertEqual(len(plan['participants']), 1000)
        self.assertLess(len(plan['token']), 200000)

    def test_two_simultaneous_applications_create_only_one_batch(self):
        self.prepared()
        with self.app.app_context():
            if db.engine.dialect.name != 'mysql':
                self.skipTest('Exercise row-lock concurrency on MariaDB')
        ids = self.people(12)
        plan = self.preview()
        clients = [self.client(1), self.client(1)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda c: self.apply(plan, c), clients))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.filter_by(action='onboarding.admit_batch').count(), 1)
            self.assertTrue(all(Users.query.get(uid).team_id for uid in ids))

    def test_archived_audit_keeps_destinations_without_the_review_token(self):
        from CTFd.plugins.open_tech_quest import archive
        self.prepared()
        self.people(12)
        plan = self.preview()
        self.assertEqual(self.apply(plan).status_code, 200)
        with self.app.app_context():
            row = QuestAudit.query.filter_by(action='onboarding.admit_batch').one()
            detail = archive._audit_detail(row.action, row.detail)
            self.assertEqual(detail['groups'], row.detail['groups'])
            self.assertNotIn(plan['token'], str(detail))
