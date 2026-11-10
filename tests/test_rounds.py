"""Real native history, atomic archive/reset, and a second playable round."""
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

from CTFd.models import (Awards, ChallengeComments, Challenges, Comments, Flags,
    Hints, Notifications, Ratings, Solves, Submissions, TeamComments,
    TeamFieldEntries, TeamFields, Teams, Unlocks, Users, db)
from CTFd.utils import get_config
from CTFd.plugins.open_tech_quest import archive, rounds
from CTFd.plugins.open_tech_quest.models import (QuestAudit, QuestCorrection,
    QuestExclusion, QuestHintPurchase, QuestMaintainer, QuestOnboarding,
    QuestPause, QuestReadiness, QuestRoom, QuestStart, QuestTeamName)
from CTFd.plugins.open_tech_quest.recovery_models import QuestRecoverySolve
import test_integration as integration

P=integration.PREFIX

class RoundIntegration(unittest.TestCase):
    setUpClass=classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass=classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client=integration.QuestIntegration.client
    post=integration.QuestIntegration.post
    configure=integration.QuestIntegration.configure
    action=integration.QuestIntegration.action
    start=integration.QuestIntegration.start
    solve=integration.QuestIntegration.solve

    def setUp(self):
        integration.QuestIntegration.setUp(self)

    def finish(self, publish=True):
        self.action('start');self.start();self.solve(self.alice,1)
        self.assertEqual(self.post(self.alice,'/api/v1/unlocks',{'type':'hints','target':1}).status_code,200)
        self.solve(self.alice,2)
        with self.app.app_context():
            db.session.add_all([QuestCorrection(team_id=self.team_id,room_id=QuestRoom.query.filter_by(slug='openshift').one().id,adjustment_ms=1000,reason='clock correction'),
                QuestExclusion(team_id=self.other_id,reason='practice'),Ratings(user_id=2,challenge_id=1,value=1,review='Helpful'),
                TeamComments(team_id=self.team_id,author_id=1,content='Team note'),
                ChallengeComments(challenge_id=1,author_id=1,content='Keep this challenge note'),
                Notifications(team_id=self.team_id,title='Round notice',content='Well done')])
            field=TeamFields(name='Team field',field_type='text',description='Keep definition')
            db.session.add(field);db.session.flush()
            db.session.add(TeamFieldEntries(team_id=self.team_id,field_id=field.id,value='Team value'))
            db.session.add(QuestMaintainer(user_id=4,previous_hidden=False))
            db.session.commit()
        self.action('pause');self.action('resume');self.action('close')
        if publish:self.action('publish')

    def preview(self):
        response=self.admin.get(P+'/admin/round/preview')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return response.json['data']

    def reset(self, preview=None, **changes):
        preview=preview or self.preview()
        data={'revision':preview['revision'],'confirm':True,'event_name':preview['event_name'],'reason':'Prepare another practice round','mode':preview.get('mode','new_round')}
        data.update(changes)
        return self.post(self.admin,P+'/admin/round/reset',data)

    def test_archive_privacy_authority_and_immutable_download(self):
        self.finish()
        for client in [self.alice,self.guest,self.carol]:
            self.assertIn(client.get(P+'/admin/archives').status_code,[401,403])
            self.assertIn(client.get(P+'/admin/archives/export').status_code,[401,403])
            self.assertIn(client.get(P+'/admin/round/preview').status_code,[401,403])
            self.assertIn(self.post(client,P+'/admin/round/reset',{}).status_code,[401,403])
        created=self.post(self.admin,P+'/admin/archives',{})
        self.assertEqual(created.status_code,200,created.get_data(as_text=True))
        doc=created.json['data']['document'];aid=created.json['data']['id']
        self.assertNotIn('fixture-answer',json.dumps(doc));self.assertNotIn('admin@example.test',json.dumps(doc))
        self.assertEqual(self.admin.get(P+f'/admin/archives/{aid}').json,doc)
        self.assertEqual(self.admin.get(P+'/admin/archives').json['data']['archives'][0]['id'],aid)
        response=self.admin.get(P+f'/admin/archives/{aid}')
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')
        self.assertIn('attachment;',response.headers['Content-Disposition'])
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        self.assertNotIn('document',json.dumps(self.admin.get(P+'/admin/state').json['data']['audit']))
        self.reset()
        self.assertEqual(self.admin.get(P+f'/admin/archives/{aid}').json,doc)
        self.assertEqual(len(self.admin.get(P+'/admin/archives').json['data']['archives']),2)

    def test_new_round_archives_history_preserves_content_and_keeps_only_acting_admin(self):
        self.finish()
        with self.app.app_context():
            before_users=[(u.id,u.name,u.password,u.type,u.banned,u.hidden) for u in Users.query.filter_by(id=1)]
            before_challenges=[(c.id,c.name,c.value,c.description,c.requirements) for c in Challenges.query.order_by(Challenges.id)]
            before_flags=[(f.id,f.content) for f in Flags.query.order_by(Flags.id)]
            before_hints=[(h.id,h.content,h.cost) for h in Hints.query.order_by(Hints.id)]
        preview=self.preview();self.assertTrue(preview['allowed'])
        self.assertGreater(preview['counts']['submissions'],0)
        response=self.reset(preview)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        aid=response.json['data']['archive_id']
        doc=self.admin.get(P+f'/admin/archives/{aid}').json
        self.assertEqual(doc['data']['event']['status'],'published')
        self.assertGreater(len(doc['data']['attempts']),0)
        self.assertEqual(len(doc['data']['team_comments']),1)
        self.assertEqual(len(doc['data']['team_field_entries']),1)
        with self.app.app_context():
            for model in [Teams,Submissions,Solves,Unlocks,Awards,Ratings,QuestStart,QuestPause,QuestCorrection,QuestExclusion,QuestReadiness,QuestTeamName,QuestHintPurchase,QuestRecoverySolve,TeamComments,TeamFieldEntries,Notifications]:
                self.assertEqual(model.query.count(),0,model.__name__)
            self.assertEqual(before_users,[(u.id,u.name,u.password,u.type,u.banned,u.hidden) for u in Users.query.order_by(Users.id)])
            self.assertEqual(before_challenges,[(c.id,c.name,c.value,c.description,c.requirements) for c in Challenges.query.order_by(Challenges.id)])
            self.assertEqual(before_flags,[(f.id,f.content) for f in Flags.query.order_by(Flags.id)])
            self.assertEqual(before_hints,[(h.id,h.content,h.cost) for h in Hints.query.order_by(Hints.id)])
            self.assertEqual(QuestMaintainer.query.count(),0)
            self.assertEqual(ChallengeComments.query.count(),1)
            self.assertEqual(TeamFields.query.count(),1)
            self.assertTrue(all(u.team_id is None for u in Users.query.all()))
            state=QuestOnboarding.query.get(1)
            self.assertEqual(state.phase,'check_in');self.assertFalse(state.attendance_open)
            self.assertIsNone(state.applied_at);self.assertIsNone(state.finalized_at)
            self.assertEqual(get_config('registration_visibility'),'private')
            self.assertIsNone(get_config('start'));self.assertIsNone(get_config('end'))
        boot=self.alice.get(P+'/bootstrap').json['data']
        self.assertEqual(boot['event']['status'],'draft');self.assertIsNone(boot['viewer']['team_id'])
        self.assertEqual(self.reset(preview).status_code,409)

    def test_live_or_paused_event_cannot_be_cleared_and_invalid_csrf_cannot_store(self):
        for state in ['live','paused']:
            self.action('start' if state=='live' else 'pause')
            preview=self.preview();self.assertFalse(preview['allowed'])
            self.assertEqual(self.reset(preview).status_code,409)
        self.assertIn(self.admin.post(P+'/admin/archives',json={}).status_code,[400,403])
        self.assertIn(self.admin.post(P+'/admin/round/reset',json={}).status_code,[400,403])
        with self.app.app_context():self.assertEqual(QuestAudit.query.filter_by(action=rounds.ARCHIVE_ACTION).count(),0)

    def test_typed_name_reason_revision_and_changed_flag_reject_stale_deletion(self):
        self.finish(False);preview=self.preview()
        for changes,code in [({'event_name':'Wrong'},400),({'confirm':False},400),({'reason':'x'},400),({'revision':'\U0001f9ea'},409),({'revision':'old'},409)]:
            self.assertEqual(self.reset(preview,**changes).status_code,code)
        with self.app.app_context():
            flag=Flags.query.first();flag.content='changed-private-answer';db.session.commit()
        self.assertEqual(self.reset(preview).status_code,409)
        with self.app.app_context():
            self.assertGreater(Submissions.query.count(),0)
            self.assertEqual(QuestAudit.query.filter_by(action=rounds.ARCHIVE_ACTION).count(),0)

    def test_archive_and_clear_roll_back_together_on_write_failure(self):
        self.finish();preview=self.preview()
        with self.app.app_context():
            before=Submissions.query.count();start_count=QuestStart.query.count()
        original=db.session.add
        def fail_final(item,*args,**kwargs):
            if isinstance(item,QuestAudit) and item.action=='event.new_round':raise RuntimeError('injected after deletion')
            return original(item,*args,**kwargs)
        with patch.object(db.session,'add',side_effect=fail_final):
            with self.assertRaises(RuntimeError):self.reset(preview)
        with self.app.app_context():
            self.assertEqual(Submissions.query.count(),before);self.assertEqual(QuestStart.query.count(),start_count)
            self.assertEqual(Teams.query.count(),2)
            self.assertEqual(QuestAudit.query.filter_by(action=rounds.ARCHIVE_ACTION).count(),0)

    def test_second_round_accepts_fresh_attendance_allocation_and_solves(self):
        self.finish();self.assertEqual(self.reset().status_code,200)
        fresh=[]
        for name in ['New Alice','New Bob']:
            response=self.post(self.admin,'/api/v1/users',{'name':name,'email':name.replace(' ','').lower()+'@example.test','password':'new-fixture-password','type':'user','verified':True})
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            fresh.append(self.client(response.json['data']['id']))
        alice,bob=fresh
        response=self.post(self.admin,P+'/admin/onboarding',{'team_size':2},'PATCH');self.assertEqual(response.status_code,200)
        response=self.post(self.admin,P+'/admin/attendance',{'enabled':True},'PATCH');self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        for client in [alice,bob]:self.assertEqual(self.post(client,P+'/onboarding/ready',{}).status_code,200)
        self.assertEqual(self.post(self.admin,P+'/admin/attendance',{'enabled':False},'PATCH').status_code,200)
        response=self.post(self.admin,P+'/admin/onboarding/action',{'action':'close_checkin'})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        # Native reviewed-plan contract remains authoritative.
        plan=self.post(self.admin,P+'/admin/onboarding/optimize',{'preview':True}).json['data']
        response=self.post(self.admin,P+'/admin/onboarding/optimize',{'preview':False,'fingerprint':plan['fingerprint']})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertEqual(self.post(self.admin,P+'/admin/onboarding/action',{'action':'finalize'}).status_code,200)
        self.action('start');self.start(alice);self.assertEqual(self.solve(alice,1).json['data']['status'],'correct')

    def test_concurrent_repeated_clear_creates_exactly_one_archive(self):
        from concurrent.futures import ThreadPoolExecutor
        self.finish();preview=self.preview()
        body={'revision':preview['revision'],'confirm':True,'event_name':preview['event_name'],'reason':'Repeat-safe new round','mode':'new_round'}
        def reset_once(_):
            client=self.client(1)
            return self.post(client,P+'/admin/round/reset',body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(reset_once,range(2)))
        self.assertEqual(sorted(statuses),[200,409])
        with self.app.app_context():
            self.assertEqual(QuestAudit.query.filter_by(action=rounds.ARCHIVE_ACTION).count(),1)
            self.assertEqual(Submissions.query.count(),0)

    def test_cached_native_failure_does_not_report_an_uncommitted_reset(self):
        self.finish();preview=self.preview()
        with patch.object(rounds,'clear_standings',side_effect=RuntimeError('cache unavailable')):
            result=self.reset(preview)
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.json['data']['cache_warning'])
        self.assertEqual(self.reset(preview).status_code,409)
        self.assertEqual(len(self.admin.get(P+'/admin/archives').json['data']['archives']),1)

    def test_default_rooms_restore_archives_content_and_removes_every_challenge(self):
        self.finish()
        with self.app.app_context():
            from CTFd.plugins.open_tech_quest.models import QuestRoom
            room=QuestRoom.query.filter_by(slug='openshift').one();room.name='Customized room';db.session.commit()
        preview=self.admin.get(P+'/admin/round/preview?mode=defaults').json['data']
        self.assertEqual(preview['mode'],'defaults');self.assertTrue(preview['allowed'])
        self.assertEqual(self.reset(preview,mode='new_round').status_code,409)
        response=self.reset(preview)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        doc=self.admin.get(P+'/admin/archives/'+str(response.json['data']['archive_id'])).json
        self.assertTrue(any(r['name']=='Customized room' for r in doc['data']['rooms']))
        self.assertEqual(len(doc['data']['challenges']),3)
        with self.app.app_context():
            from CTFd.plugins.open_tech_quest.models import QuestRoom,QuestStep
            self.assertEqual([(r.slug,r.name) for r in QuestRoom.query.order_by(QuestRoom.position)],
                [('openshift','OpenShift'),('ansible','Ansible'),('rhel','RHEL'),('security','Security'),('ai','AI')])
            for model in [Challenges,Flags,Hints,QuestStep]:self.assertEqual(model.query.count(),0)
            self.assertEqual(Users.query.count(),1)
            self.assertEqual(Users.query.one().id,1)
        self.assertEqual(self.admin.get(P+'/bootstrap').json['data']['event']['status'],'draft')

    def test_unknown_cascading_plugin_tables_block_both_reset_modes(self):
        from sqlalchemy import text
        self.finish()
        with self.app.app_context():
            db.session.execute(text('CREATE TABLE additional_plugin_round_progress (id INTEGER PRIMARY KEY, team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE)'))
            db.session.execute(text('INSERT INTO additional_plugin_round_progress(id,team_id) VALUES (1,:tid)'),{'tid':self.team_id});db.session.commit()
        try:
            for mode in ['new_round','defaults']:
                preview=self.admin.get(P+'/admin/round/preview?mode='+mode).json['data']
                self.assertFalse(preview['allowed']);self.assertIn('additional_plugin_round_progress',preview['reason'])
                self.assertEqual(self.reset(preview).status_code,409)
            with self.app.app_context():self.assertEqual(db.session.execute(text('SELECT count(*) FROM additional_plugin_round_progress')).scalar(),1)
        finally:
            with self.app.app_context():db.session.execute(text('DROP TABLE additional_plugin_round_progress'));db.session.commit()

    def test_other_administrators_and_account_data_removed_old_archives_survive(self):
        from CTFd.models import Tokens, Tracking, UserComments, UserFieldEntries, UserFields
        self.finish()
        response=self.post(self.admin,'/api/v1/users',{'name':'Other administrator','email':'other-admin@example.test','password':'other-fixture-password','type':'admin','verified':True})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        other_id=response.json['data']['id'];other=self.client(other_id)
        saved=self.post(other,P+'/admin/archives',{})
        self.assertEqual(saved.status_code,200)
        aid=saved.json['data']['id'];doc=saved.json['data']['document']
        with self.app.app_context():
            field=UserFields(name='Participant field',field_type='text');db.session.add(field);db.session.flush()
            db.session.add_all([UserFieldEntries(user_id=2,field_id=field.id,value='Private profile'),
                UserComments(user_id=2,author_id=1,content='Participant note'),
                Tokens(type='user',user_id=2,value='disposable-not-a-real-access-token'),
                Tracking(user_id=2,ip='192.0.2.22'),
                ChallengeComments(challenge_id=1,author_id=2,content='Keep authored content')]);db.session.commit()
        self.assertEqual(self.reset().status_code,200)
        self.assertEqual(self.admin.get(P+f'/admin/archives/{aid}').json,doc)
        self.assertIn(other.get(P+'/admin/state').status_code,[401,403])
        with self.app.app_context():
            self.assertEqual(Users.query.count(),1)
            for model in [UserFieldEntries,UserComments,Tokens]:self.assertEqual(model.query.count(),0)
            self.assertEqual(Tracking.query.filter_by(user_id=2).count(),0)
            self.assertIsNone(QuestAudit.query.get(aid).actor_id)
            note=ChallengeComments.query.filter_by(content='Keep authored content').one()
            self.assertIsNone(note.author_id)

    def test_recovery_solve_foreign_keys_are_archived_then_cleared(self):
        self.action('start');self.start();self.solve(self.alice,1)
        identities=self.admin.get(P+'/admin/users').json['data']['users']
        alice=next(row for row in identities if row['id']==2)
        result=self.post(self.admin,P+'/admin/users/2/access',{'identity':alice['identity'],'confirm':True,'active':False},'PATCH')
        self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        result=self.solve(self.bob,1)
        self.assertEqual(result.json['data']['status'],'correct')
        self.action('close');self.action('publish')
        result=self.reset();self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        doc=self.admin.get(P+'/admin/archives/'+str(result.json['data']['archive_id'])).json
        self.assertTrue(any(a['recovery'] for a in doc['data']['attempts']))
        with self.app.app_context():
            self.assertEqual(QuestRecoverySolve.query.count(),0)
            self.assertEqual(Submissions.query.count(),0)
            self.assertEqual(Users.query.count(),1)

    def test_another_round_then_restore_defaults_without_starting_another_event(self):
        self.finish()
        first=self.reset();self.assertEqual(first.status_code,200)
        first_id=first.json['data']['archive_id']
        original=self.admin.get(P+f'/admin/archives/{first_id}').json
        preview=self.admin.get(P+'/admin/round/preview?mode=defaults').json['data']
        self.assertTrue(preview['allowed'])
        self.assertEqual(preview['counts']['accounts_removed'],0)
        second=self.reset(preview)
        self.assertEqual(second.status_code,200,second.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Users.query.count(),1)
            self.assertEqual(Challenges.query.count(),0)
            self.assertEqual(QuestRoom.query.count(),5)
        self.assertEqual(self.admin.get(P+f'/admin/archives/{first_id}').json,original)
        self.assertEqual(len(self.admin.get(P+'/admin/archives').json['data']['archives']),2)
