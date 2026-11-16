"""Authoritative display clocks reuse native progress and pause history."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from CTFd.models import Challenges, Solves, Users, db
from CTFd.plugins.open_tech_quest.models import (QuestCorrection, QuestEvent, QuestPause,
    QuestRoom, QuestRoomPause, QuestStart)
from CTFd.plugins.open_tech_quest.service import Projection, standings
import test_integration as integration


class CompetitionClocks(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure

    def setUp(self):
        integration.QuestIntegration.setUp(self)
        self.assertEqual(self.post(self.admin, integration.PREFIX+'/admin/rooms/ansible', {'challenge_ids':[3]}, 'PATCH').status_code, 200)
        self.at = datetime(2026, 11, 1, 10)
        with self.app.app_context():
            e=QuestEvent.query.get(1);e.status='live';e.start_at=self.at
            self.room_id=QuestRoom.query.filter_by(slug='openshift').one().id
            self.other_room_id=QuestRoom.query.filter_by(slug='ansible').one().id
            db.session.add_all([QuestStart(team_id=self.team_id,room_id=self.room_id,started_at=self.at+timedelta(minutes=1)),
                               QuestStart(team_id=self.team_id,room_id=self.other_room_id,started_at=self.at+timedelta(minutes=2))])
            db.session.commit()

    def view(self, minutes=10):
        return Projection(QuestEvent.query.get(1), team_id=self.team_id, at=self.at+timedelta(minutes=minutes))

    def add_solve(self, challenge, minutes):
        db.session.add(Solves(challenge_id=challenge,user_id=2,team_id=self.team_id,date=self.at+timedelta(minutes=minutes)))

    def test_event_clock_starts_at_event_not_first_room_or_last_solve(self):
        with self.app.app_context():
            e=QuestEvent.query.get(1);e.overall_clock='team';db.session.commit()
            v=self.view();clock=v.event_clock(self.team_id)
            self.assertEqual((clock['state'],clock['elapsed_ms']),('running',600000))
            self.assertEqual(v.room(v.rooms[0],self.team_id)['clock']['elapsed_ms'],540000)
            self.assertIsNone(v.event_clock(self.other_id)['finished_at'])

    def test_global_and_room_pause_union_have_distinct_scopes(self):
        with self.app.app_context():
            db.session.add(QuestPause(started_at=self.at+timedelta(minutes=3),ended_at=self.at+timedelta(minutes=5)))
            db.session.add(QuestRoomPause(room_id=self.room_id,started_at=self.at+timedelta(minutes=4),ended_at=self.at+timedelta(minutes=7),reason='Synthetic incident'))
            db.session.commit();v=self.view()
            self.assertEqual(v.event_clock(self.team_id)['elapsed_ms'],480000)
            self.assertEqual(v.room(v.rooms[0],self.team_id)['clock']['elapsed_ms'],300000)

    def test_room_pause_does_not_stop_event_clock(self):
        with self.app.app_context():
            db.session.add(QuestRoomPause(room_id=self.room_id,started_at=self.at+timedelta(minutes=3),reason='Synthetic incident'))
            db.session.commit();v=self.view();clock=v.room(v.rooms[0],self.team_id)['clock']
            self.assertEqual((clock['state'],clock['elapsed_ms']),('paused',120000))
            self.assertEqual(v.event_clock(self.team_id)['state'],'running')
            e=QuestEvent.query.get(1);e.status='paused';e.paused_at=self.at+timedelta(minutes=4)
            db.session.add(QuestPause(started_at=e.paused_at));db.session.commit()
            self.assertEqual((self.view().event_clock()['state'],self.view().event_clock()['elapsed_ms']),('paused',240000))

    def test_complete_room_uses_corrected_completion_and_freezes(self):
        with self.app.app_context():
            self.add_solve(1,3);self.add_solve(2,5)
            db.session.add(QuestCorrection(team_id=self.team_id,room_id=self.room_id,adjustment_ms=5000,reason='Synthetic correction'))
            db.session.commit()
            for minutes in (10,30):
                v=self.view(minutes);r=v.room(v.rooms[0],self.team_id)
                self.assertEqual(r['clock']['state'],'complete')
                self.assertEqual(r['clock']['elapsed_ms'],r['progress']['milestone_elapsed_ms'])
                self.assertEqual(r['clock']['elapsed_ms'],245000)
                self.assertEqual(v.event_clock(self.team_id)['state'],'running')

    def test_all_challenges_freeze_own_event_time_but_not_projector(self):
        with self.app.app_context():
            for cid,minute in ((1,3),(2,5),(3,8)):self.add_solve(cid,minute)
            db.session.add(QuestPause(started_at=self.at+timedelta(minutes=2),ended_at=self.at+timedelta(minutes=3)))
            e=QuestEvent.query.get(1);e.overall_clock='team';db.session.commit()
            for minutes in (10,30):
                c=self.view(minutes).event_clock(self.team_id)
                self.assertEqual((c['state'],c['elapsed_ms']),('complete',420000))
                self.assertEqual(c['finished_at'],'2026-11-01T10:08:00.000Z')
                self.assertEqual(self.view(minutes).event_clock()['state'],'running')
            self.assertEqual(self.view(10).event_clock(self.team_id),self.view(30).event_clock(self.team_id))
            row=next(t for t in standings()['teams'] if t['id']==self.team_id)
            self.assertEqual(row['overall_elapsed_ms'],360000)
            self.assertEqual(row['event_completion']['elapsed_ms'],420000)

    def test_cutoff_freezes_partial_paths_without_manual_close(self):
        with self.app.app_context():
            e=QuestEvent.query.get(1);e.end_at=self.at+timedelta(minutes=6);db.session.commit()
            v=self.view();c=v.event_clock(self.team_id)
            self.assertEqual((c['state'],c['elapsed_ms'],c['remaining_ms']),('ended',360000,0))
            self.assertEqual(v.room(v.rooms[0],self.team_id)['clock']['elapsed_ms'],300000)

    def test_negative_correction_waits_at_zero_before_advancing(self):
        with self.app.app_context():
            db.session.add(QuestCorrection(team_id=self.team_id,room_id=self.room_id,adjustment_ms=-1000000,reason='Synthetic correction'))
            db.session.commit();v=self.view();c=v.room(v.rooms[0],self.team_id)['clock']
            self.assertEqual((c['elapsed_ms'],c['delay_ms']),(0,460000))

    def test_hidden_challenges_withdraw_completion_requirement_and_empty_is_not_clear(self):
        with self.app.app_context():
            self.add_solve(1,3);self.add_solve(2,5)
            Challenges.query.get(3).state='hidden';db.session.commit()
            self.assertEqual(self.view().event_clock(self.team_id)['state'],'complete')
            for c in Challenges.query.all():c.state='hidden'
            db.session.commit();self.assertEqual(self.view().event_clock(self.team_id)['state'],'running')

    def test_draft_has_no_clock_even_with_a_past_scheduled_start(self):
        with self.app.app_context():
            QuestEvent.query.get(1).status='draft';db.session.commit()
            c=self.view().event_clock(self.team_id)
            self.assertEqual((c['state'],c['elapsed_ms']),('not_started',None))

    def test_publication_preserves_completion_if_current_eligibility_changes(self):
        with self.app.app_context():
            for cid,minute in ((1,3),(2,5),(3,8)):self.add_solve(cid,minute)
            e=QuestEvent.query.get(1);e.status='closed';e.end_at=self.at+timedelta(minutes=10);db.session.commit()
            e.snapshot=standings(e);e.status='published';db.session.commit()
            expected=self.view().event_clock(self.team_id)
            Users.query.get(2).banned=True;db.session.commit()
            self.assertEqual(self.view(30).event_clock(self.team_id),expected)
            self.assertEqual(self.view(30).event_clock()['state'],'ended')

    def test_bootstrap_and_room_share_clock_without_adding_projection_to_sync(self):
        with patch('CTFd.plugins.open_tech_quest.service.now',return_value=self.at+timedelta(minutes=10)):
            data=self.alice.get(integration.PREFIX+'/bootstrap').json['data']
            self.assertEqual(data['event_clock']['elapsed_ms'],600000)
            room=self.alice.get(integration.PREFIX+'/rooms/openshift').json['data']
            self.assertEqual(data['rooms'][0]['clock'],room['clock'])
            with patch('CTFd.plugins.open_tech_quest.service.projection',side_effect=AssertionError('Sync must remain lightweight')):
                sync=self.alice.get(integration.PREFIX+'/sync')
                self.assertEqual(sync.status_code,200)
                self.assertNotIn('event_clock',sync.json['data'])

    def test_publication_does_not_turn_an_incomplete_clear_into_a_later_completion(self):
        with self.app.app_context():
            self.add_solve(1,3);self.add_solve(2,5)
            db.session.add(Users(id=5,name='Later reinstated',email='reinstated@example.test',password='test-only',team_id=self.team_id,banned=True))
            db.session.flush()
            db.session.add(Solves(challenge_id=3,user_id=5,team_id=self.team_id,date=self.at+timedelta(minutes=8)))
            e=QuestEvent.query.get(1);e.status='closed';e.end_at=self.at+timedelta(minutes=10);db.session.commit()
            e.snapshot=standings(e);e.status='published';db.session.commit()
            Users.query.get(5).banned=False;db.session.commit()
            clock=self.view().event_clock(self.team_id)
            self.assertEqual((clock['state'],clock['elapsed_ms']),('ended',600000))
