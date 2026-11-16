"""Physical logistics are persisted per team, exclusive, reviewed and admin-only."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest

from CTFd.models import Teams, Users, db
from CTFd.plugins.open_tech_quest import locations, onboarding
from CTFd.plugins.open_tech_quest.location_models import QuestVenueRoom, QuestTeamLocation
from CTFd.plugins.open_tech_quest.models import QuestAudit, QuestEvent, QuestMaintainer
import test_integration as integration
import test_rounds as rounds_tests

P = integration.PREFIX


class LocationIntegration(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    setUp = integration.QuestIntegration.setUp
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure
    action = integration.QuestIntegration.action
    preview = rounds_tests.RoundIntegration.preview
    reset = rounds_tests.RoundIntegration.reset

    def checked(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['data']

    def state(self):
        return self.checked(self.admin.get(P+'/admin/locations'))

    def layout(self, rooms=None, revision=None):
        rooms = rooms if rooms is not None else [dict(id=None, name='2.0234 titanium 3', table_count=3, unavailable_tables=[]),
                                                 dict(id=None, name='Second venue', table_count=2, unavailable_tables=[])]
        return self.post(self.admin, P+'/admin/locations/layout', {'revision': revision or self.state()['revision'], 'rooms': rooms}, 'PUT')

    def assign(self, team_id, rid=None, table=None, swap=False, revision=None):
        return self.post(self.admin, P+f'/admin/locations/teams/{team_id}', {
            'revision': revision or self.state()['revision'], 'venue_room_id': rid, 'table_number': table, 'swap': swap}, 'PATCH')

    def distribute(self, apply=False, strategy='spread', revision=None, reassign_existing=False):
        if apply and revision is None:
            revision = self.checked(self.distribute(False, strategy, reassign_existing=reassign_existing))['revision']
        return self.post(self.admin, P+'/admin/locations/distribution', {
            'revision': revision or self.state()['revision'], 'strategy': strategy, 'apply': apply,
            'reassign_existing': reassign_existing})

    def test_empty_install_and_permissions_and_csrf(self):
        self.assertEqual(self.admin.get(P+'/admin/locations').headers['Cache-Control'], 'private, no-store')
        self.assertEqual(self.state()['rooms'], [])
        with self.app.app_context():
            db.session.add(QuestMaintainer(user_id=4, previous_hidden=False)); db.session.commit()
        for client in (self.guest, self.alice, self.carol):
            self.assertIn(client.get(P+'/admin/locations').status_code, (401,403))
            for path, method in (('/admin/locations/layout','PUT'),('/admin/locations/distribution','POST'),(f'/admin/locations/teams/{self.team_id}','PATCH')):
                self.assertIn(self.post(client, P+path, {}, method).status_code,(401,403))
        response = self.admin.put(P+'/admin/locations/layout', json={'revision': self.state()['revision'], 'rooms': []})
        self.assertIn(response.status_code, (400,403))

    def test_additive_migration_preserves_populated_native_and_quest_history(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        import importlib
        self.action('start')
        integration.QuestIntegration.start(self)
        self.checked(integration.QuestIntegration.solve(self, self.alice, 1))
        with self.app.app_context():
            old_tables = [t for t in db.metadata.sorted_tables if t.name not in ('quest_venue_rooms','quest_team_locations')]
            def rows():
                return {t.name: [tuple(r) for r in db.session.execute(t.select().order_by(*t.primary_key.columns))] for t in old_tables}
            before=rows();db.session.rollback()
            QuestTeamLocation.__table__.drop(db.engine)
            QuestVenueRoom.__table__.drop(db.engine)
            migration=importlib.import_module('CTFd.plugins.open_tech_quest.migrations.0014_locations')
            try:
                with db.engine.begin() as connection:
                    op=Operations(MigrationContext.configure(connection))
                    migration.upgrade(op);migration.upgrade(op)
                self.assertEqual(before,rows())
                self.assertEqual(QuestVenueRoom.query.count(),0)
                self.assertEqual(QuestTeamLocation.query.count(),0)
            finally:
                db.session.rollback()
                with db.engine.begin() as connection:
                    migration.upgrade(Operations(MigrationContext.configure(connection)))

    def test_one_team_assignment_shared_by_members_and_hidden_before_roster_release(self):
        room = self.checked(self.layout())['rooms'][0]
        self.checked(self.assign(self.team_id, room['id'], 2))
        for client in (self.alice, self.bob):
            boot = self.checked(client.get(P+'/bootstrap'))
            self.assertEqual(boot['viewer']['team_location']['label'], '2.0234 titanium 3 · Table 2')
            self.assertEqual(boot['onboarding']['team']['location'], boot['viewer']['team_location'])
        self.assertIsNone(self.checked(self.carol.get(P+'/bootstrap'))['viewer']['team_location'])
        admin_teams=self.checked(self.admin.get(P+'/admin/state'))['teams']
        self.assertEqual(next(t for t in admin_teams if t['id']==self.team_id)['location']['table_number'],2)
        with self.app.app_context():
            e=QuestEvent.query.get(1);e.rosters_release_at=datetime.utcnow()+timedelta(hours=1);db.session.commit()
        boot=self.checked(self.alice.get(P+'/bootstrap'))
        self.assertIsNone(boot['viewer']['team_location']);self.assertIsNone(boot['onboarding']['team'])

    def test_draft_repeated_layout_and_assignment_after_finalization_without_progress_changes(self):
        room = self.checked(self.layout())['rooms'][0]
        self.checked(self.assign(self.team_id, room['id'], 1))
        saved=self.state();room=dict(saved['rooms'][0]);room.pop('position');room['name']='Renamed venue';room['table_count']=5
        second=dict(saved['rooms'][1]);second.pop('position')
        self.checked(self.layout([room,second]))
        self.assertEqual(self.checked(self.alice.get(P+'/bootstrap'))['viewer']['team_location']['room_name'],'Renamed venue')
        for n in (2,3,1):self.checked(self.assign(self.team_id, room['id'], n))
        self.checked(self.assign(self.team_id))
        with self.app.app_context():
            self.assertEqual(QuestEvent.query.get(1).status,'draft')
            self.assertEqual(QuestTeamLocation.query.count(),0)
        self.assertEqual(self.checked(self.admin.get(P+'/admin/state'))['onboarding']['phase'],'finalized')

    def test_layout_rejects_invalid_rooms_tables_and_removing_occupied_capacity(self):
        for count in (0,True,501,1.5):
            self.assertEqual(self.layout([dict(id=None,name='Room',table_count=count,unavailable_tables=[])]).status_code,400)
        for unavailable in ([0],[3],[1,1],[True]):
            self.assertEqual(self.layout([dict(id=None,name='Room',table_count=2,unavailable_tables=unavailable)]).status_code,400)
        room=self.checked(self.layout())['rooms'][0];self.checked(self.assign(self.team_id,room['id'],3))
        self.assertEqual(self.layout([]).status_code,409)
        for count,blocked in ((2,[]),(3,[3])):
            self.assertEqual(self.layout([dict(id=room['id'],name=room['name'],table_count=count,unavailable_tables=blocked)]).status_code,409)
        self.assertEqual(self.state()['teams'][0]['location']['table_number'],3)

    def test_manual_assignment_prevents_double_booking_and_explicit_swap_is_atomic(self):
        rid=self.checked(self.layout())['rooms'][0]['id']
        self.checked(self.assign(self.team_id,rid,1));self.checked(self.assign(self.other_id,rid,2))
        self.assertEqual(self.assign(self.team_id,rid,2).status_code,409)
        self.checked(self.assign(self.team_id,rid,2,True))
        with self.app.app_context():
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).table_number,2)
            self.assertEqual(QuestTeamLocation.query.get(self.other_id).table_number,1)
        original=locations.audit
        def fail(*args,**kwargs):raise RuntimeError('Injected swap audit failure')
        with patch.object(locations,'audit',side_effect=fail):
            with self.assertRaises(RuntimeError):self.assign(self.team_id,rid,1,True)
        with self.app.app_context():
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).table_number,2)
            self.assertEqual(QuestTeamLocation.query.get(self.other_id).table_number,1)

    def test_stale_layout_manual_and_distribution_reviews_never_overwrite(self):
        rid=self.checked(self.layout())['rooms'][0]['id'];revision=self.state()['revision']
        self.checked(self.assign(self.team_id,rid,1))
        self.assertEqual(self.assign(self.other_id,rid,2,revision=revision).status_code,409)
        self.assertEqual(self.distribute(True,revision=revision).status_code,409)
        self.assertEqual(self.layout([],revision=revision).status_code,409)

    def test_distribution_preserves_manual_assignments_and_repeat_is_idempotent(self):
        saved=self.checked(self.layout());rid=saved['rooms'][1]['id']
        self.checked(self.assign(self.team_id,rid,2))
        first=self.checked(self.distribute());again=self.checked(self.distribute())
        self.assertEqual(first,again);self.assertEqual(first['preserved'],1)
        self.checked(self.distribute(True,revision=first['revision']))
        self.assertEqual(self.checked(self.distribute())['assignments'],[])
        self.checked(self.distribute(True))
        with self.app.app_context():
            self.assertEqual(QuestTeamLocation.query.count(),2)
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).table_number,2)
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).venue_room_id,rid)

    def test_shortage_unavailable_and_duplicate_table_numbers_across_rooms(self):
        saved=self.checked(self.layout([dict(id=None,name='Only room',table_count=2,unavailable_tables=[2])]))
        self.assertEqual(self.checked(self.distribute())['shortage'],1)
        self.assertEqual(self.distribute(True).status_code,409)
        self.assertEqual(self.assign(self.team_id,saved['rooms'][0]['id'],2).status_code,400)
        with self.app.app_context():self.assertEqual(QuestTeamLocation.query.count(),0)
        self.checked(self.layout())
        self.checked(self.distribute(True))
        with self.app.app_context():
            rows=QuestTeamLocation.query.all();self.assertEqual([r.table_number for r in rows],[1,1]);self.assertNotEqual(rows[0].venue_room_id,rows[1].venue_room_id)

    def test_full_draft_redistribution_is_explicit_reviewed_and_atomic(self):
        rooms=self.checked(self.layout())['rooms']
        self.checked(self.assign(self.team_id,rooms[1]['id'],2))
        self.checked(self.assign(self.other_id,rooms[0]['id'],1))
        preview=self.checked(self.distribute(strategy='fill',reassign_existing=True))
        self.assertEqual(preview['preserved'],0)
        self.assertEqual(len(preview['assignments']),2)
        with patch.object(locations,'audit',side_effect=RuntimeError('Injected bulk reassignment failure')):
            with self.assertRaises(RuntimeError):self.distribute(True,'fill',preview['revision'],True)
        with self.app.app_context():
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).venue_room_id,rooms[1]['id'])
            self.assertEqual(QuestTeamLocation.query.get(self.other_id).table_number,1)
        self.checked(self.distribute(True,'fill',preview['revision'],True))
        with self.app.app_context():
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).venue_room_id,rooms[0]['id'])
            self.assertEqual(QuestTeamLocation.query.get(self.team_id).table_number,1)
            self.assertEqual(QuestTeamLocation.query.get(self.other_id).table_number,2)

    def test_distribution_confirmations_bind_strategy_scope_and_current_phase(self):
        self.checked(self.layout())
        preview=self.checked(self.distribute())
        self.assertEqual(self.distribute(True,'fill',preview['revision']).status_code,409)
        self.assertEqual(self.distribute(True,'spread',preview['revision'],True).status_code,409)
        self.assertEqual(self.distribute(True,revision=preview['source_revision']).status_code,409)
        self.action('start')
        self.assertEqual(self.distribute(reassign_existing=True).status_code,409)
        self.checked(self.distribute(True))

    def test_large_location_projection_has_bounded_queries_and_matching_roster_eligibility(self):
        from sqlalchemy import event as sql_event
        with self.app.app_context():
            # Core inserts avoid password hashing for these non-login fixtures.
            base=max(1000000, db.session.query(db.func.max(Teams.id)).scalar() or 0)+1
            teams=[dict(id=base+i,name=f'Location team {i}',hidden=i==48,banned=False) for i in range(50)]
            db.session.execute(Teams.__table__.insert(),teams)
            db.session.execute(Users.__table__.insert(),[
                dict(name=f'Location member {i}_{n}',email=f'location{i}_{n}@example.test',password='non-login-fixture',
                     type='user',banned=i==49,team_id=base+i,verified=True) for i in range(50) for n in range(8)])
            for user in Users.query.filter_by(team_id=base):db.session.add(QuestMaintainer(user_id=user.id,previous_hidden=False))
            db.session.commit()
            expected={t.id for t in onboarding.competing_teams()}
            e=QuestEvent.query.get(1);queries=[]
            def capture(*args):queries.append(args[2])
            sql_event.listen(db.engine,'after_cursor_execute',capture)
            try:saved=locations.snapshot(e)
            finally:sql_event.remove(db.engine,'after_cursor_execute',capture)
            self.assertEqual({t['id'] for t in saved['teams'] if t['active']},expected)
            self.assertLessEqual(len(queries),6,queries)
            self.assertEqual(next(t['members'] for t in saved['teams'] if t['id']==base+1),8)

    def test_concurrent_same_table_assignments_or_preview_allow_only_one_winner(self):
        rid=self.checked(self.layout())['rooms'][0]['id'];revision=self.state()['revision']
        def assign(team_id):return self.post(self.client(1),P+f'/admin/locations/teams/{team_id}',dict(revision=revision,venue_room_id=rid,table_number=1,swap=False),'PATCH').status_code
        with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(assign,[self.team_id,self.other_id]))
        self.assertEqual(sorted(codes),[200,409])
        with self.app.app_context():self.assertEqual(QuestTeamLocation.query.count(),1)

    def test_live_manual_changes_do_not_require_pause_layout_is_draft_only(self):
        rid=self.checked(self.layout())['rooms'][0]['id'];self.action('start')
        self.checked(self.assign(self.team_id,rid,1));self.assertEqual(self.layout([]).status_code,409)
        self.action('pause');self.checked(self.assign(self.team_id,rid,2))
        self.action('close');self.assertEqual(self.assign(self.team_id,rid,3).status_code,409)

    def test_membership_moves_and_renames_keep_team_locations_and_users_follow_destination(self):
        rid=self.checked(self.layout())['rooms'][0]['id']
        self.checked(self.assign(self.team_id,rid,1));self.checked(self.assign(self.other_id,rid,2))
        self.checked(self.post(self.admin,P+'/admin/onboarding',{'allow_team_naming':True},'PATCH'))
        self.checked(self.post(self.alice,P+'/onboarding/name',{'name':'New team name','confirm':True,'team_id':self.team_id,'expected_name':'Red team'}))
        with self.app.app_context():fp=onboarding.fingerprint(QuestEvent.query.get(1))
        self.checked(self.post(self.admin,P+'/admin/onboarding/move',{'user_id':2,'team_id':self.other_id,'fingerprint':fp,'reason':'Move for event rehearsal'}))
        self.assertEqual(self.checked(self.alice.get(P+'/bootstrap'))['viewer']['team_location']['table_number'],2)
        self.assertEqual(self.checked(self.bob.get(P+'/bootstrap'))['viewer']['team_location']['table_number'],1)

    def test_archives_capture_locations_reset_clears_assignments_and_defaults_remove_venue(self):
        for mode in ('new_round','defaults'):
            if mode=='defaults':
                # The first reset retained inventory but removed teams/accounts.
                with self.app.app_context():db.session.add(Teams(name='Next team'));db.session.commit()
            else:
                rid=self.checked(self.layout())['rooms'][0]['id'];self.checked(self.assign(self.team_id,rid,1))
            preview=self.checked(self.admin.get(P+'/admin/round/preview?mode='+mode));self.checked(self.reset(preview))
            with self.app.app_context():
                saved=QuestAudit.query.filter_by(action='event.archive').order_by(QuestAudit.id.desc()).first().detail['document']['data']
                self.assertTrue(saved['venue_rooms'])
                if mode=='new_round':self.assertEqual(len(saved['team_locations']),1)
                self.assertEqual(QuestTeamLocation.query.count(),0)
                self.assertEqual(QuestVenueRoom.query.count(),2 if mode=='new_round' else 0)


class DistributionPolicy(unittest.TestCase):
    def sample(self):
        return {'rooms':[{'id':i+1,'name':str(i),'position':i,'table_count':n,'unavailable_tables':[]} for i,n in enumerate((20,10,5))],
                'teams':[{'id':i,'name':str(i),'active':True,'location':None} for i in range(1,22)]}

    def test_spread_respects_smaller_room_then_balances_remaining(self):
        self.assertEqual([r['teams'] for r in locations.distribution(self.sample(),'spread')['occupancy']],[8,8,5])

    def test_fill_uses_configured_room_order(self):
        self.assertEqual([r['teams'] for r in locations.distribution(self.sample(),'fill')['occupancy']],[20,1,0])
