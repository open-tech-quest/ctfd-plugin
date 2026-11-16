"""Native signup/profile fields provide preferred names without changing login."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from CTFd.cache import cache
from CTFd.models import Configs, UserFieldEntries, UserFields, Users, db
from CTFd.plugins.open_tech_quest import display_names
import test_integration as integration


class DisplayNames(unittest.TestCase):
    setUpClass = classmethod(integration.QuestIntegration.setUpClass.__func__)
    tearDownClass = classmethod(integration.QuestIntegration.tearDownClass.__func__)
    client = integration.QuestIntegration.client
    post = integration.QuestIntegration.post
    configure = integration.QuestIntegration.configure

    def setUp(self):
        integration.QuestIntegration.setUp(self)
        with self.app.app_context():
            self.field_id=display_names.initialize()

    def save_name(self, client, value):
        response=self.post(client,'/api/v1/users/me',{'fields':[{'field_id':self.field_id,'value':value}]},'PATCH')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))

    def test_native_field_is_optional_editable_private_and_idempotent(self):
        with self.app.app_context():
            f=UserFields.query.get(self.field_id)
            self.assertEqual((f.name,f.field_type,f.required,f.public,f.editable),('Display name','text',False,False,True))
            self.assertEqual(display_names.initialize(),self.field_id)
            self.assertEqual(Configs.query.filter_by(key=display_names.KEY).count(),1)
            self.assertEqual(UserFields.query.count(),1)
        for client,path in ((self.guest,'/register'),(self.alice,'/settings')):
            response=client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Display name',response.data)
            self.assertIn(('fields[%s]'%self.field_id).encode(),response.data)

    def test_native_profile_edit_updates_viewer_without_changing_login(self):
        self.save_name(self.alice,'  Alice Example  ')
        data=self.alice.get(integration.PREFIX+'/sync').json['data']
        self.assertEqual((data['viewer']['name'],data['viewer']['display_name']),('Alice','Alice Example'))
        with self.app.app_context():self.assertEqual(Users.query.get(2).name,'Alice')
        self.save_name(self.alice,'')
        self.assertEqual(self.alice.get(integration.PREFIX+'/bootstrap').json['data']['viewer']['display_name'],'Alice')

    def test_signup_stores_display_name_and_username_still_authenticates(self):
        self.guest.get('/register')
        with self.guest.session_transaction() as session:nonce=session['nonce']
        response=self.guest.post('/register',data={'name':'new-login','email':'new-login@example.test','password':'test-only',
            'nonce':nonce,'fields[%s]'%self.field_id:'New Display Name'})
        self.assertIn(response.status_code,(302,303),response.get_data(as_text=True))
        data=self.guest.get(integration.PREFIX+'/bootstrap').json['data']
        self.assertTrue(data['viewer']['authenticated'])
        self.assertEqual((data['viewer']['name'],data['viewer']['display_name']),('new-login','New Display Name'))
        other=self.app.test_client();other.get('/login')
        with other.session_transaction() as session:nonce=session['nonce']
        other.post('/login',data={'name':'new-login','password':'test-only','nonce':nonce})
        self.assertTrue(other.get(integration.PREFIX+'/bootstrap').json['data']['viewer']['authenticated'])

    def test_names_can_repeat_but_other_accounts_entries_cannot_be_overwritten(self):
        self.save_name(self.alice,'Shared Display')
        with self.app.app_context():entry=UserFieldEntries.query.filter_by(user_id=2,field_id=self.field_id).one().id
        response=self.post(self.carol,'/api/v1/users/me',{'fields':[{'id':entry,'field_id':self.field_id,'value':'Shared Display'}]},'PATCH')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(UserFieldEntries.query.filter_by(user_id=2,field_id=self.field_id).one().value,'Shared Display')
            self.assertEqual(UserFieldEntries.query.filter_by(user_id=4,field_id=self.field_id).one().value,'Shared Display')
        public=self.carol.get('/api/v1/users/2').json['data']
        self.assertNotIn(self.field_id,[f['field_id'] for f in public.get('fields',[])])
        self.assertIsNone(self.app.test_client().get(integration.PREFIX+'/bootstrap').json['data']['viewer'].get('display_name'))

    def test_missing_malformed_or_duplicate_entries_do_not_break_sign_in(self):
        with self.app.app_context():
            user=Users.query.get(2);self.assertEqual(display_names.name(user),'Alice')
            db.session.add(UserFieldEntries(user_id=2,field_id=self.field_id,value={'unexpected':'shape'}));db.session.commit()
            self.assertEqual(display_names.name(user),'Alice')
            UserFieldEntries.query.delete();db.session.commit()
            db.session.add_all([UserFieldEntries(user_id=2,field_id=self.field_id,value='First value'),UserFieldEntries(user_id=2,field_id=self.field_id,value='Duplicate value')]);db.session.commit()
            self.assertEqual(display_names.name(user),'First value')
            UserFields.query.filter_by(id=self.field_id).delete();db.session.commit()
            self.assertEqual(display_names.name(user),'Alice')

    def test_concurrent_initialization_creates_only_one_owned_native_field(self):
        with self.app.app_context():
            UserFields.query.delete();Configs.query.filter_by(key=display_names.KEY).delete();db.session.commit();cache.clear()
        def initialize(_):
            with self.app.app_context():return display_names.initialize()
        with ThreadPoolExecutor(max_workers=3) as pool:ids=list(pool.map(initialize,range(3)))
        self.assertEqual(len(set(ids)),1)
        with self.app.app_context():
            self.assertEqual(UserFields.query.count(),1)
            self.assertEqual(Configs.query.filter_by(key=display_names.KEY).count(),1)
