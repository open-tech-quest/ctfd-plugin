"""Comparison controls protect real native requests and published snapshots."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from CTFd.models import Configs, Solves, Users, db
from CTFd.plugins.open_tech_quest import archive, standings_access
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestMaintainer
import test_integration as integration

P = integration.PREFIX


class StandingsAccess(unittest.TestCase):
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
        self.action("start")
        self.start(self.alice)
        self.start(self.carol)
        self.assertEqual(self.solve(self.alice, 1).json['data']['status'], 'correct')
        for cid in (1, 2):
            self.assertEqual(self.solve(self.carol, cid).json['data']['status'], 'correct')

    def update(self, **data):
        response = self.post(self.admin, P+'/admin/standings/visibility', data, 'PATCH')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['data']

    def read(self, client=None, view=None):
        return (client or self.alice).get(P+'/standings'+('' if view is None else '?view='+view))

    def test_defaults_preserve_public_comparisons_and_personal_view_is_own_only(self):
        public = self.read(self.guest).json['data']
        self.assertEqual(len(public['teams']), 2)
        self.assertTrue(all(public['access'].values()))
        personal = self.read(view='team').json['data']
        self.assertEqual([t['id'] for t in personal['teams']], [self.team_id])
        self.assertIsNone(personal['teams'][0]['rank'])
        self.assertTrue(all(p['rank'] is None for p in personal['teams'][0]['room_progress']))
        self.assertEqual(personal['room_leaders'], [])
        with self.app.app_context():
            self.assertEqual(Configs.query.filter_by(key=standings_access.KEY).count(), 0)

    def test_both_off_protect_direct_urls_legacy_requests_and_projector(self):
        self.update(room_comparison=False, overall_comparison=False)
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=3, previous_hidden=False)); db.session.commit()
        for client in (self.alice, self.guest, self.bob):
            for view in ('room', 'overall', 'projector'):
                self.assertEqual(self.read(client, view).status_code, 403)
            self.assertEqual(client.get(P+'/projector').status_code, 403)
        for client in (self.alice, self.admin):
            data = self.read(client).json['data']
            self.assertEqual(len(data['teams']), 2 if client is self.admin else 1)
        self.assertEqual(self.read(self.guest).json['data']['teams'], [])
        for view in ('room', 'overall', 'projector'):
            self.assertEqual(self.read(self.admin, view).status_code, 200)
        self.assertEqual(self.admin.get(P+'/projector').status_code, 200)
        state = self.admin.get(P+'/admin/state').json['data']
        self.assertEqual(state['standings_visibility'], {'room_comparison':False, 'overall_comparison':False})
        self.assertEqual(len(state['standings']['teams']), 2)

    def test_overall_only_hides_other_room_progress_and_all_room_ranks(self):
        self.update(room_comparison=False)
        data = self.read(view='overall').json['data']
        self.assertEqual(len(data['teams']), 2)
        self.assertEqual(data['room_leaders'], [])
        self.assertTrue(all(p['rank'] is None for t in data['teams'] for p in t['room_progress']))
        self.assertEqual(next(t for t in data['teams'] if t['id']==self.other_id)['room_progress'], [])
        self.assertEqual(next(t for t in data['teams'] if t['id']==self.team_id)['points'], 100)
        self.assertEqual(self.read(view='room').status_code, 403)
        self.assertEqual(self.read(view='projector').status_code, 403)

    def test_rooms_only_remove_overall_scores_and_ranking_order(self):
        self.update(overall_comparison=False)
        data = self.read(view='room').json['data']
        self.assertEqual([t['id'] for t in data['teams']], sorted(t['id'] for t in data['teams']))
        other = next(t for t in data['teams'] if t['id']==self.other_id)
        self.assertIsNone(other['rank'])
        self.assertIsNone(other['overall_elapsed_ms'])
        self.assertEqual(other['points'], 0)
        self.assertEqual(sum(p['points'] for p in other['room_progress']), 200)
        self.assertTrue(any(p['rank']==1 for p in other['room_progress']))
        self.assertEqual(self.read(view='overall').status_code, 403)
        self.assertEqual(self.read(view='projector').status_code, 403)

    def test_controls_require_admin_and_csrf_and_validate_before_mutating(self):
        path = P+'/admin/standings/visibility'
        for client in (self.guest, self.alice):
            self.assertIn(self.post(client,path,{'room_comparison':False},'PATCH').status_code,(401,403))
        self.assertEqual(self.admin.patch(path,json={'room_comparison':False}).status_code,403)
        for data in ({}, {'other':False}, {'room_comparison':0}, {'overall_comparison':'false'}, {'room_comparison':None}):
            self.assertEqual(self.post(self.admin,path,data,'PATCH').status_code,400)
        with self.app.app_context():
            self.assertEqual(standings_access.settings(),standings_access.DEFAULTS)
            self.assertEqual(QuestAudit.query.filter_by(action='standings.configure').count(),0)
        self.assertEqual(self.post(self.admin,'/api/v1/configs/'+standings_access.KEY,{'value':'{}'},'PATCH').status_code,409)
        self.assertEqual(self.read(view='unknown').status_code,400)

    def test_published_toggles_do_not_change_snapshot_scores_or_solve_history(self):
        self.action('close'); self.action('publish')
        with self.app.app_context():
            before=deepcopy(QuestEvent.query.get(1).snapshot)
            solves=[(s.id,s.date) for s in Solves.query.order_by(Solves.id).all()]
        self.update(room_comparison=False,overall_comparison=False)
        self.read(view='team'); self.read(self.admin,'projector')
        self.update(overall_comparison=True)
        self.read(view='overall')
        with self.app.app_context():
            e=QuestEvent.query.get(1)
            self.assertEqual(e.status,'published')
            self.assertEqual(e.snapshot,before)
            self.assertEqual([(s.id,s.date) for s in Solves.query.order_by(Solves.id).all()],solves)
            report=archive.collect_event(e)
            self.assertEqual(report['standings_visibility'],{'room_comparison':False,'overall_comparison':True})
            self.assertEqual(report['results']['teams'],before['teams'])
            self.assertTrue(any(a['detail'].get('room_comparison') is False for a in report['audit'] if a['action']=='standings.configure'))

    def test_changes_refresh_sync_without_cross_worker_config_cache(self):
        old=self.alice.get(P+'/sync').json['data']
        self.assertTrue(old['standings_access']['room_comparison'])
        self.update(room_comparison=False)
        new=self.alice.get(P+'/sync').json['data']
        self.assertFalse(new['standings_access']['room_comparison'])
        self.assertTrue(self.admin.get(P+'/sync').json['data']['standings_access']['room_comparison'])
        self.update(room_comparison=True)
        self.assertTrue(self.alice.get(P+'/sync').json['data']['standings_access']['projector'])

    def test_concurrent_independent_switches_merge_under_event_lock(self):
        def change(key):
            return self.post(self.client(1),P+'/admin/standings/visibility',{key:False},'PATCH').status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(change,standings_access.DEFAULTS)),[200,200])
        with self.app.app_context():
            self.assertEqual(standings_access.settings(),{'room_comparison':False,'overall_comparison':False})
            self.assertEqual(Configs.query.filter_by(key=standings_access.KEY).count(),1)

    def test_banned_admin_cannot_bypass_or_reconfigure_restrictions(self):
        self.update(room_comparison=False)
        with self.app.app_context():
            Users.query.get(1).banned=True;db.session.commit()
        self.assertEqual(self.read(self.admin,'projector').status_code,403)
        self.assertEqual(self.post(self.admin,P+'/admin/standings/visibility',{'room_comparison':True},'PATCH').status_code,403)

    def test_malformed_persisted_policy_fails_closed_and_admin_can_repair(self):
        with self.app.app_context():
            db.session.add(Configs(key=standings_access.KEY,value='malformed-json'));db.session.commit()
        self.assertEqual(self.read(view='room').status_code,403)
        self.assertEqual(self.read(view='overall').status_code,403)
        self.assertEqual(self.update(room_comparison=True),{'room_comparison':True,'overall_comparison':False})

    def test_intentional_round_cleanup_retains_visibility_settings(self):
        self.update(room_comparison=False,overall_comparison=False)
        self.action('close')
        preview=self.admin.get(P+'/admin/round/preview').json['data']
        response=self.post(self.admin,P+'/admin/round/reset',{
            'revision':preview['revision'],'confirm':True,'event_name':preview['event_name'],
            'reason':'Disposable visibility reset regression','mode':'new_round'})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(standings_access.settings(),{'room_comparison':False,'overall_comparison':False})
        self.assertEqual(self.guest.get(P+'/projector').status_code,403)
