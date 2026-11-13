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
from core import StageError, MTeamResponseError, HTTP, ServiceHTTPError, App, MTeam, QBit, PluginError, torrent_hash, endpoint, base_url

TORRENT = b'd4:infod6:lengthi1e4:name1:x6:pieces20:abcdefghijklmnopqrstee'
class MT:
    def details(self,rid): return {'title':'Authorized sample','id':rid}
    def torrent(self,rid): raise AssertionError('Hermes must not fetch torrents')
    def download_url(self,rid): return 'https://files.test/dl?token=FAKE'
class QB:
    def __init__(self): self.tasks=[]; self.calls=[]; self.fail=False
    def login(self): pass
    def info(self,h): return self.tasks
    def info_tag(self,t): return self.tasks
    def add_url(self,t,p,c,paused,*ids):
        self.calls.append((t,p,c,paused))
        if self.fail: raise TimeoutError('secret URL')
        self.tasks=[{'hash':torrent_hash(TORRENT),'save_path':p,'state':'stoppedDL'}]
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
    def test_url_pending_and_cross_request_dedup(self):
        def pending(*args): self.q.calls.append(args)
        self.q.add_url=pending
        rid=self.prepared()
        self.assertEqual(self.app.submit(rid,True)['state'],'submitted_not_yet_visible')
        self.assertEqual(self.app.status(rid)['state'],'submitted')
        self.assertEqual(self.app.submit(rid)['state'],'unknown')
        other=self.prepared()
        self.assertEqual(self.app.submit(other,True)['state'],'resource_already_submitted')
        self.assertEqual(len(self.q.calls),1)
        self.assertNotIn('token=FAKE',Path(self.cfg['state_path']).read_bytes().decode(errors='ignore'))
        self.q.tasks=[{'hash':'a'*40,'save_path':'/downloads/movies'}]
        self.assertEqual(self.app.status(rid)['state'],'present')

    def test_url_generation_failure_safe_to_reprepare(self):
        with patch.object(self.app.mt,'download_url',side_effect=StageError('mteam_download_link',403)):
            rid=self.prepared()
            with self.assertRaises(StageError):self.app.submit(rid,True)
        self.assertEqual(self.app.row(rid)['state'],'failed_before_submission')
        self.assertFalse(self.q.calls)
        self.assertEqual(self.app.submit(self.prepared(),True)['state'],'present')

    def test_url_qbit_fields_and_exact_tag(self):
        import urllib.parse
        for version,field in [(b'v4.6.0','paused'),(b'v5.0.0','stopped')]:
            http=FakeHTTP([version,b'Ok.'])
            qb=QBit({'base_url':'http://example.com'},http)
            qb.add_url('https://files.test/t?token=FAKE','/other','test',True,'a'*32,'123')
            form=urllib.parse.parse_qs(http.calls[1][0][3].decode())
            self.assertEqual(form['urls'],['https://files.test/t?token=FAKE'])
            self.assertEqual(form[field],['true']); self.assertEqual(form['savepath'],['/other'])
            self.assertEqual(form['autoTMM'],['false'])
            self.assertEqual(form['tags'],['mteam-qbit-'+('a'*32)+',mteam-qbit-r123'])
        http=FakeHTTP([json.dumps([{'tags':'mteam-qbit-r1234','name':'wrong'},
                                  {'tags':'other, mteam-qbit-r123','name':'right'}]).encode()])
        tasks=QBit({'base_url':'http://example.com'},http).info_tag('mteam-qbit-r123')
        self.assertEqual([x['name'] for x in tasks],['right'])

    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_url_rejects_multiple_urls(self):
        for url in ['https://files.test/x\nhttps://files.test/y','https://files.test/x\rfoo']:
            mt=MTeam({'base_url':'https://example.com','download_hosts':['files.test']},
                     FakeHTTP([json.dumps({'code':0,'data':url}).encode()]))
            with self.assertRaises(PluginError):mt.download_url('123')

    def test_reused_tagged_task_remains_trackable(self):
        task={'hash':'b'*40,'save_path':'/existing'}
        self.q.info_tag=lambda tag: [task] if tag=='mteam-qbit-r123' else []
        self.q.info=lambda digest: [task] if digest=='b'*40 else []
        rid=self.prepared()
        self.assertEqual(self.app.submit(rid,True)['state'],'present')
        self.assertEqual(self.app.row(rid)['hash'],'b'*40)
        self.assertEqual(self.app.status(rid)['tasks'],[task])
        self.assertEqual(self.app.submit(rid)['tasks'],[task])
        self.assertFalse(self.q.calls)

    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_server_page_size_and_category_projection(self):
        for value,expected in [('422','422'),(422,'422'),(False,None),({'token':'SECRET'},None),('bad',None)]:
            data={'data':[{'id':'1','name':'x','category':value}], 'pageNumber':2,'pageSize':20,'total':35}
            mt=MTeam({'base_url':'https://example.com'},FakeHTTP([json.dumps({'code':0,'data':data}).encode()]))
            result=mt.search('x',page=2,limit=10)
            self.assertFalse(result['has_more']);self.assertEqual(result['limit'],20)
            self.assertEqual(result['requested_limit'],10)
            self.assertEqual(result['items'][0]['category_id'],expected)
            self.assertNotIn('SECRET',json.dumps(result))

    def test_concurrent_resource_reservation(self):
        import threading
        barrier=threading.Barrier(2)
        requests=[self.prepared(),self.prepared()]
        calls=[]; results=[]; errors=[]
        class ConcurrentQB:
            def login(self): barrier.wait(timeout=3)
            def info_tag(self,tag):return []
            def add_url(self,*args):calls.append(args)
        def run(request):
            app=App(self.cfg,MT(),ConcurrentQB())
            try:results.append(app.submit(request,True))
            except Exception as error:errors.append(error)
            finally:app.db.close()
        threads=[threading.Thread(target=run,args=(request,)) for request in requests]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(calls),1)
        self.assertEqual({x['state'] for x in results},{'resource_already_submitted','submitted_not_yet_visible'})

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
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_category_filter_and_pagination(self):
        http=FakeHTTP([json.dumps({'code':0,'data':{'list':[{'id':'902','nameChs':'测试1'},{'id':'903','nameChs':'测试2'}], 'tvshow':['902','903']}}).encode(), json.dumps({'code':0,'data':{'data':[],'pageNumber':'2','totalPages':'4','total':'70'}}).encode()])
        result=MTeam({'base_url':'https://example.com'},http).search('sample',page=2,limit=20,category='tvshow')
        self.assertEqual(json.loads(http.calls[1][0][3])['categories'],['902','903'])
        self.assertTrue(result['has_more']);self.assertEqual(result['next_page'],3);self.assertEqual(result['previous_page'],1)
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_custom_categories_and_unknown_end(self):
        http=FakeHTTP([b'{"code":0,"data":{"data":[]}}'])
        result=MTeam({'base_url':'https://example.com'},http).search('sample',category='movie',category_ids=['434','434'])
        self.assertEqual(result['category_ids'],['434']);self.assertEqual(result['category'],'custom')
        self.assertIsNone(result['has_more']);self.assertIsNone(result['next_page'])
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_custom_category_pagination_replay(self):
        http=FakeHTTP([b'{"code":0,"data":{"data":[],"totalPages":3}}'] * 2)
        mt=MTeam({'base_url':'https://example.com'},http)
        first=mt.search('sample',category_ids=['422'])
        second=mt.search('sample',page=first['next_page'],
                         category=first['category'],category_ids=first['category_ids'])
        body=json.loads(http.calls[1][0][3])
        self.assertEqual(body['pageNumber'],2)
        self.assertEqual(body['categories'],['422'])
        self.assertEqual(second['category'],'custom')
        self.assertEqual(second['next_page'],3)
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_pagination_end_and_invalid_inputs(self):
        http=FakeHTTP([b'{"code":0,"data":{"data":[],"total":20}}'])
        result=MTeam({'base_url':'https://example.com'},http).search('sample')
        self.assertFalse(result['has_more']);self.assertIsNone(result['next_page'])
        mt=MTeam({'base_url':'https://example.com'},FakeHTTP([]))
        for kw in [{'page':True},{'page':0},{'limit':51},{'category_ids':[]},{'category_ids':['x']},{'category_ids':[401]}]:
            with self.assertRaises(PluginError):mt.search('sample',**kw)
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_live_categories_name_tree_and_whitelist(self):
        data={'list':[{'id':'900','nameChs':'新分类','parent':None,'private':'secret'},
                      {'id':'901','nameChs':'新子类','parent':'900'}], 'newGroup':['901']}
        http=FakeHTTP([json.dumps({'code':0,'data':data}).encode(),
                       b'{"code":0,"data":{"data":[]}}'])
        result=MTeam({'base_url':'https://example.com'},http).search('sample',category='新分类')
        self.assertEqual(result['category_ids'],['901'])
        self.assertIsNone(http.calls[0][0][3])
        self.assertTrue(http.calls[0][0][1].endswith('/api/torrent/categoryList'))
        self.assertNotIn('Content-Type',http.calls[0][0][2])
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_live_category_errors_no_static_fallback(self):
        cases=[{}, {'list':'bad'}, {'list':[{'id':'1'}]},
               {'list':[{'id':'1','nameChs':'重复'},{'id':'2','nameChs':'重复'}]},
               {'list':[{'id':'1','nameChs':'循环','parent':'2'},{'id':'2','nameChs':'子','parent':'1'}]}]
        for data in cases:
            http=FakeHTTP([json.dumps({'code':0,'data':data}).encode()])
            with self.assertRaises(PluginError):
                MTeam({'base_url':'https://example.com'},http).search('sample',category='循环' if data==cases[-1] else '重复')
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_live_catalog_returns_only_metadata(self):
        data={'list':[{'id':991,'parent':None,'nameChs':'测试','account':'SECRET'}],
              'movie':[991, 999], 'accountIds':[991], 'token':'SECRET'}
        http=FakeHTTP([json.dumps({'code':0,'data':data}).encode()])
        result=MTeam({'base_url':'https://example.com'},http).categories()
        self.assertEqual(result['categories'],[{'id':'991','parent':None,'nameChs':'测试'}])
        self.assertEqual(result['groups'],{'movie':['991']})
        self.assertNotIn('SECRET',json.dumps(result))
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_large_dynamic_group_pagination_replay(self):
        ids=[str(i) for i in range(100,141)]
        data={'list':[{'id':rid,'nameChs':'类别'+rid} for rid in ids],
              'movie':ids, 'accountIds':ids, 'waterfall':['999999']}
        http=FakeHTTP([json.dumps({'code':0,'data':data}).encode(),
                       b'{"code":0,"data":{"data":[],"totalPages":3}}',
                       b'{"code":0,"data":{"data":[],"totalPages":3}}'])
        mt=MTeam({'base_url':'https://example.com'},http)
        first=mt.search('sample',category='movie')
        self.assertEqual(len(first['category_ids']),41)
        second=mt.search('sample',page=first['next_page'],category=first['category'],
                         category_ids=first['category_ids'])
        self.assertEqual(second['page'],2)
        self.assertEqual(json.loads(http.calls[2][0][3])['categories'],ids)
        with self.assertRaises(PluginError):
            mt.search('sample',category_ids=['1']*2001)
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_unfiltered_pagination_omits_empty_ids(self):
        http=FakeHTTP([b'{"code":0,"data":{"data":[],"totalPages":3}}']*2)
        mt=MTeam({'base_url':'https://example.com'},http)
        first=mt.search('sample')
        self.assertEqual(first['category_ids'],[])
        second=mt.search('sample',page=first['next_page'],category='all')
        self.assertEqual(json.loads(http.calls[1][0][3])['categories'],[])
        self.assertEqual(second['page'],2)
        self.assertEqual(second['next_page'],3)
        skill=(Path(__file__).resolve().parents[1]/'skills/mteam-qbit/SKILL.md').read_text()
        self.assertIn('omit category_ids entirely and pass category=all',skill)
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key','QBIT_USERNAME':'u','QBIT_PASSWORD':'p'})
    def test_download_http_stage_and_user_agent(self):
        cfg={'base_url':'https://example.com','download_hosts':['example.com'],'user_agent':'Configured-UA'}
        signed='https://example.com/dl?token=SECRET'
        for fail_at,stage in [(0,'mteam_download_link'),(1,'mteam_torrent_fetch')]:
            http=FakeHTTP([])
            def request(*args, **kw):
                http.calls.append((args,kw))
                if len(http.calls)-1 == fail_at:
                    raise ServiceHTTPError(403,{})
                return json.dumps({'code':0,'data':signed}).encode()
            http.request=request
            with self.assertRaises(StageError) as ctx:
                MTeam(cfg,http).torrent('479504')
            self.assertEqual(ctx.exception.diagnostics()['stage'],stage)
            self.assertEqual(ctx.exception.http_status,403)
            self.assertNotIn('SECRET',str(ctx.exception))
            self.assertEqual(http.calls[0][0][2]['User-Agent'],'Configured-UA')
            if fail_at:
                self.assertEqual(http.calls[1][0][2],{'User-Agent':'Configured-UA'})
        http=FakeHTTP([])
        def reject(*args,**kw): raise ServiceHTTPError(403,{})
        http.request=reject
        with self.assertRaises(StageError) as ctx:
            QBit({'base_url':'http://example.com'},http).login()
        self.assertEqual(ctx.exception.stage,'qbit_login')
    def test_add_stage_uncertainty_never_resends(self):
        rid=self.prepared()
        def reject(*args): raise StageError('qbit_add',403)
        self.q.add_url=reject
        with self.assertRaises(StageError) as ctx:self.app.submit(rid,True)
        self.assertEqual(ctx.exception.http_status,403)
        self.assertTrue(ctx.exception.uncertain)
        self.assertEqual(self.app.row(rid)['state'],'unknown')
        self.assertEqual(self.app.submit(rid)['state'],'unknown')
    @patch.dict(os.environ,{'MTEAM_API_KEY':'test-key'})
    def test_every_mteam_request_uses_configured_ua_at_http_boundary(self):
        from unittest.mock import MagicMock
        for configured in ('Custom-Browser-UA', None):
            cfg={'base_url':'https://example.com','download_hosts':['files.example.net']}
            if configured: cfg['user_agent']=configured
            replies=[{'code':0,'data':{'id':'fake'}},
                     {'code':0,'data':{'list':[]}},
                     {'code':0,'data':{'data':[]}},
                     {'code':0,'data':{'id':'123','name':'sample'}},
                     {'code':0,'data':'https://files.example.net/dl?token=FAKE'}]
            payloads=[json.dumps(x).encode() for x in replies]+[TORRENT]
            http=HTTP()
            responses=[]
            for payload in payloads:
                response=MagicMock()
                response.status=200
                response.read.return_value=payload
                response.__enter__.return_value=response
                responses.append(response)
            http.opener.open=MagicMock(side_effect=responses)
            mt=MTeam(cfg,http)
            mt.test_connection(); mt.categories(); mt.search('sample')
            mt.details('123'); mt.torrent('123')
            requests=[call.args[0] for call in http.opener.open.call_args_list]
            self.assertEqual([req.full_url.split('?')[0] for req in requests],
                ['https://example.com/api/member/profile',
                 'https://example.com/api/torrent/categoryList',
                 'https://example.com/api/torrent/search',
                 'https://example.com/api/torrent/detail',
                 'https://example.com/api/torrent/genDlToken',
                 'https://files.example.net/dl'])
            for req in requests:
                self.assertEqual(req.get_header('User-agent'),configured or 'MTeamQBit/1.0')
            for req in requests[:-1]:
                self.assertEqual(req.get_header('X-api-key'),'test-key')
            self.assertIsNone(requests[-1].get_header('X-api-key'))
            self.assertEqual(dict(requests[-1].header_items()),{'User-agent':configured or 'MTeamQBit/1.0'})
    def test_post_add_lookup_failure_preserves_submitted(self):
        rid=self.prepared()
        lookups=[]
        def info(digest):
            lookups.append(digest)
            if len(lookups)==1:return []
            raise StageError('qbit_task_lookup',403)
        self.q.info_tag=info
        with self.assertRaises(StageError) as ctx:self.app.submit(rid,True)
        self.assertTrue(ctx.exception.uncertain)
        self.assertEqual(ctx.exception.stage,'qbit_task_lookup')
        self.assertEqual(self.app.row(rid)['state'],'submitted')
        self.assertEqual(len(self.q.calls),1)
    def test_actual_transport_failure_diagnostics_are_safe(self):
        from unittest.mock import MagicMock
        from core import stage_call, NoRedirect, TransportError
        import urllib.error, socket, ssl
        cases=[(urllib.error.URLError(socket.gaierror('SECRET_HOST')),'dns_error'),
               (urllib.error.URLError(ssl.SSLCertVerificationError('SECRET_CERT')),'tls_certificate'),
               (urllib.error.URLError(ssl.SSLError('SECRET_TLS')),'tls_error'),
               (TimeoutError('SECRET_URL'),'timeout'),
               (ConnectionRefusedError('SECRET_IP'),'connection_refused'),
               (ConnectionResetError('SECRET'),'connection_reset'),
               (urllib.error.URLError('SECRET_PROXY'),'network_error')]
        for exception,expected in cases:
            http=HTTP();http.opener.open=MagicMock(side_effect=exception)
            with self.assertRaises(StageError) as ctx:
                stage_call('mteam_torrent_fetch',http.request,'GET','https://example.com/?token=SECRET')
            self.assertEqual(ctx.exception.reason,expected)
            self.assertIsNone(ctx.exception.http_status)
            self.assertNotIn('SECRET',str(ctx.exception)+json.dumps(ctx.exception.diagnostics()))
        with self.assertRaises(StageError) as ctx:
            stage_call('mteam_torrent_fetch',NoRedirect().redirect_request,None,None,302,'SECRET',{},'https://evil/?token=SECRET')
        self.assertEqual(ctx.exception.reason,'redirect_refused')
        self.assertEqual(ctx.exception.http_status,302)
        self.assertNotIn('SECRET',str(ctx.exception))
    def test_response_size_failure_preserves_status_not_body(self):
        from unittest.mock import MagicMock
        from core import stage_call
        response=MagicMock();response.status=200;response.read.return_value=b'SECRET'
        response.__enter__.return_value=response
        http=HTTP();http.opener.open=MagicMock(return_value=response)
        with self.assertRaises(StageError) as ctx:
            stage_call('mteam_torrent_fetch',http.request,'GET','https://example.com',limit=1)
        self.assertEqual(ctx.exception.reason,'response_too_large')
        self.assertEqual(ctx.exception.http_status,200)
        self.assertNotIn('SECRET',str(ctx.exception))
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
        self.assertNotIn('Content-Type',args[2])
        self.assertEqual(args[2]['Accept'],'application/json, text/plain, */*')
        self.assertEqual(set(args[2]),{'Accept','User-Agent','x-api-key'})
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
            self.assertEqual(json.loads(args[3]),{'mode':'normal','keyword':'hermes-connection-test','categories':[],'pageNumber':1,'pageSize':1})
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
        self.assertEqual(len(ctx.tools),7)
        self.assertTrue(ctx.skill["path"].is_file())
        self.assertTrue(all(callable(x['handler']) for x in ctx.tools))
        schema=next(x['schema'] for x in ctx.tools if x['name']=='pt_prepare_download')
        self.assertEqual(schema['parameters']['required'],['resource_id'])
        self.assertIn('save_path',schema['parameters']['properties'])

if __name__=='__main__':unittest.main()
