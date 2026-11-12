import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import MTeamResponseError, HTTP, ServiceHTTPError, App, MTeam, QBit, PluginError, torrent_hash, endpoint, base_url

TORRENT = b'd4:infod6:lengthi1e4:name1:x6:pieces20:abcdefghijklmnopqrstee'
class MT:
    def details(self,rid): return {'title':'Authorized sample','id':rid}
    def torrent(self,rid): return TORRENT
class QB:
    def __init__(self): self.tasks=[]; self.calls=[]; self.fail=False
    def login(self): pass
    def info(self,h): return self.tasks
    def add(self,t,p,c,paused):
        self.calls.append((t,p,c,paused))
        if self.fail: raise TimeoutError('secret URL')
        self.tasks=[{'hash':torrent_hash(t),'save_path':p,'state':'stoppedDL'}]
class FakeHTTP:
    def __init__(self, replies): self.replies=list(replies); self.calls=[]
    def request(self,*args,**kw): self.calls.append((args,kw)); return self.replies.pop(0)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg={'default_save_path':'/downloads/movies','state_path':self.tmp.name+'/state.db','mteam':{},'qbittorrent':{}}
        self.q=QB();self.app=App(self.cfg,MT(),self.q)
    def tearDown(self): self.app.db.close();self.tmp.cleanup()
    def prepared(self): return self.app.prepare('123')['request_id']
    def test_approval_required(self):
        rid=self.prepared()
        with self.assertRaises(PluginError): self.app.submit(rid)
        self.assertFalse(self.q.calls)
    def test_submit_and_duplicate(self):
        rid=self.prepared()
        self.assertEqual(self.app.submit(rid, user_confirmed=True)['state'],'present')
        self.app.submit(rid)
        self.assertEqual(len(self.q.calls),1)
        self.assertEqual(self.q.calls[0][1],'/downloads/movies')
        self.assertFalse(self.q.calls[0][3])
    def test_timeout_never_resends(self):
        rid=self.prepared();self.app.approve(rid);self.q.fail=True
        with self.assertRaisesRegex(PluginError,'uncertain'):self.app.submit(rid)
        self.assertEqual(self.app.submit(rid)['state'],'unknown')
        self.assertEqual(len(self.q.calls),1)
    def test_expiry(self):
        rid=self.prepared()
        with patch('core.time.time',return_value=1e12):
            with self.assertRaises(PluginError):self.app.approve(rid)
    def test_changed_default_keeps_snapshot(self):
        rid=self.prepared();self.app.default_save_path='/other'
        self.app.submit(rid,True)
        self.assertEqual(self.q.calls[0][1],'/downloads/movies')
    def test_override_outside_default(self):
        result=self.app.prepare('123','/media/电影/new/')
        self.assertEqual(result['save_path'],'/media/电影/new')
        self.app.submit(result['request_id'],True)
        self.assertEqual(self.q.calls[0][1],'/media/电影/new')
    def test_existing_not_moved(self):
        self.q.tasks=[{'save_path':'/original','hash':torrent_hash(TORRENT)}]
        result=self.app.prepare('123','/other')
        self.assertEqual(self.app.submit(result['request_id'],True)['tasks'][0]['save_path'],'/original')
        self.assertFalse(self.q.calls)
    def test_path_traversal(self):
        for path in ['../../tmp','/downloads/../etc','','//host/share','/x\nheader',False,123]:
            with self.assertRaises(PluginError):self.app.prepare('123',path)
        cfg=dict(self.cfg,default_save_path='/downloads/../etc')
        with self.assertRaises(PluginError):App(cfg,MT(),self.q)
    def test_legacy_request_requires_prepare(self):
        rid=self.prepared()
        self.app.db.execute("UPDATE requests SET destination='movies' WHERE id=?",(rid,));self.app.db.commit()
        with self.assertRaisesRegex(PluginError,'Legacy'):self.app.submit(rid,True)
        self.assertFalse(self.q.calls)
    def test_legacy_config_migration(self):
        cfg=dict(self.cfg);cfg.pop('default_save_path');cfg['destinations']={'tv':'/tv','movies':'/movies'}
        other=App(cfg,MT(),self.q)
        try:self.assertEqual(other.default_save_path,'/tv')
        finally:other.db.close()
    def test_hash(self):
        self.assertEqual(torrent_hash(TORRENT),hashlib.sha1(TORRENT[7:-1]).hexdigest())
        for bad in [b'<html>',b'd4:infodee',TORRENT+b'x',b'd4:info999999:x']:
            with self.assertRaises(PluginError):torrent_hash(bad)
    def test_bad_endpoint(self):
        for path in ['//evil/x','https://evil','/../x','/x?key=abc']:
            with self.assertRaises(PluginError):endpoint('https://example.com',path)
        with self.assertRaises(PluginError):base_url('https://user:password@example.com')
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_search_metadata_sanitized(self):
        http=FakeHTTP([json.dumps({'code':'0','data':{'data':[{'id':1,'name':'sample','downloadUrl':'SECRET'}]}}).encode()])
        mt=MTeam({'base_url':'https://example.com'},http)
        result=mt.search('sample')
        self.assertNotIn('SECRET',json.dumps(result))
        body=json.loads(http.calls[0][0][3]);self.assertEqual(body['keyword'],'sample')
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_profile_connection_no_body_or_data_exposure(self):
        http=FakeHTTP([json.dumps({'code':0,'data':{'email':'DO_NOT_ECHO'}}).encode()])
        mt=MTeam({'base_url':'https://example.com'},http)
        self.assertIs(mt.test_connection(),True)
        args=http.calls[0][0]
        self.assertEqual(args[0],'POST')
        self.assertEqual(args[1],'https://example.com/api/member/profile')
        self.assertIsNone(args[3])
        self.assertNotIn('Accept',args[2])
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_response_categories_and_no_raw_data(self):
        cases=[([], 'response_shape',None),({'message':'DO_NOT_ECHO'},'response_code_missing',None),
               ({'code':'403','message':'token=DO_NOT_ECHO'},'business_rejected','403'),
               ({'code':'DO_NOT_ECHO'},'business_rejected',None),({'code':{'secret':'DO_NOT_ECHO'}},'business_rejected',None)]
        for data,category,numeric in cases:
            http=FakeHTTP([json.dumps(data).encode()]);http.last_status=200
            with self.assertRaises(MTeamResponseError) as ctx:MTeam({'base_url':'https://example.com'},http).test_connection()
            self.assertEqual(str(ctx.exception),category)
            self.assertEqual(ctx.exception.response_diagnostics['http_status'],200)
            self.assertEqual(ctx.exception.response_diagnostics['numeric_code'],numeric)
            self.assertNotIn('DO_NOT_ECHO',json.dumps(ctx.exception.response_diagnostics))
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_profile_empty_data_rejected(self):
        for data in [{},None,[],False]:
            http=FakeHTTP([json.dumps({'code':0,'data':data}).encode()])
            with self.assertRaisesRegex(PluginError,'profile schema'):MTeam({'base_url':'https://example.com'},http).test_connection()
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_user_agent_only_request_change(self):
        for cfg,ua in [({},'MTeamQBit/1.0'),({'user_agent':'CustomUA/1'},'CustomUA/1')]:
            http=FakeHTTP([json.dumps({'code':0,'data':{'data':[]}}).encode()])
            mt=MTeam({'base_url':'https://example.com',**cfg},http)
            mt.search('hermes-connection-test',limit=1)
            args=http.calls[0][0]
            self.assertEqual(args[1],'https://example.com/api/torrent/search')
            self.assertEqual(args[2],{'x-api-key':'test-key','Content-Type':'application/json','User-Agent':ua})
            self.assertEqual(json.loads(args[3]),{'mode':'normal','keyword':'hermes-connection-test','pageNumber':1,'pageSize':1})
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_download_url_restricted(self):
        for url in ['http://localhost/a','https://evil.com/a','https://example.com:444/a']:
            mt=MTeam({'base_url':'https://example.com','download_hosts':['example.com']},FakeHTTP([json.dumps({'code':0,'data':url}).encode()]))
            with self.assertRaises(PluginError):mt.torrent('1')
    def test_qbit_version_fields(self):
        for ver,field in [(b'v4.6.7','paused'),(b'v5.1.0','stopped')]:
            http=FakeHTTP([ver,b'Ok.']);qb=QBit({'base_url':'http://qb:8080'},http)
            qb.add(TORRENT,'/downloads','hermes',True)
            body=http.calls[1][0][3]
            self.assertIn(('name="'+field+'"').encode(),body)
            self.assertIn(b'name="autoTMM"\r\n\r\nfalse',body)
    def test_dashboard_manifest_and_assets(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'dashboard/manifest.json').read_text())
        self.assertEqual(manifest['name'],'mteam-qbit')
        self.assertIn('version: "'+manifest['version']+'"',(root/'plugin.yaml').read_text())
        self.assertEqual(manifest['tab']['path'],'/mteam-qbit')
        for key in ('entry','api'):
            self.assertTrue((root/'dashboard'/manifest[key]).is_file())
    def test_native_registration(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('pt_plugin',root/'__init__.py',submodule_search_locations=[str(root)])
        plugin=importlib.util.module_from_spec(spec);sys.modules['pt_plugin']=plugin;spec.loader.exec_module(plugin)
        class Context:
            def __init__(self):self.tools=[]
            def register_tool(self,**kwargs):self.tools.append(kwargs)
            def register_skill(self,**kwargs):self.skill=kwargs
        ctx=Context();plugin.register(ctx)
        self.assertEqual(len(ctx.tools),6)
        self.assertTrue(ctx.skill["path"].is_file())
        self.assertTrue(all(callable(x['handler']) for x in ctx.tools))
        schema=next(x['schema'] for x in ctx.tools if x['name']=='pt_prepare_download')
        self.assertEqual(schema['parameters']['required'],['resource_id'])
        self.assertIn('save_path',schema['parameters']['properties'])

if __name__=='__main__':unittest.main()
