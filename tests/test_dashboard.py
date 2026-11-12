"""Plugin-route tests only. Production authentication is supplied by Hermes mount."""
import importlib.util
from pathlib import Path
import unittest
import types
import sys
import json
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pt_dashboard_test',ROOT/'dashboard'/'plugin_api.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)

class DashboardTests(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(api.router,prefix='/api/plugins/mteam-qbit');self.client=TestClient(app)
    def test_get_never_exposes_secret(self):
        with patch.object(api.settings,'read_settings',return_value=api.settings.DEFAULTS),patch.object(api.settings,'secret_presence',return_value={'mteam_api_key':True}):
            res=self.client.get('/api/plugins/mteam-qbit/settings')
            self.assertEqual(res.status_code,200)
            self.assertEqual(res.json()['backend_version'],'0.4.1')
            self.assertTrue(res.json()['secrets_set']['mteam_api_key'])
            self.assertNotIn('value',res.json()['secrets_set'])
    def test_save_route_delegates(self):
        with patch.object(api.settings,'save_settings',return_value={'ok':True}) as save:
            res=self.client.put('/api/plugins/mteam-qbit/settings',json={'settings':{},'secrets':{'qbit_password':'FAKE'}})
            self.assertEqual(res.json(),{'ok':True});save.assert_called_once_with({}, {'qbit_password':'FAKE'})
    def test_bad_request_no_echo(self):
        res=self.client.put('/api/plugins/mteam-qbit/settings',content='{"secret":"DO_NOT_ECHO"}',headers={'Content-Type':'application/json'})
        self.assertEqual(res.status_code,400);self.assertNotIn('DO_NOT_ECHO',res.text)
    def test_exception_no_echo(self):
        with patch.object(api.settings,'read_settings',side_effect=RuntimeError('DO_NOT_ECHO')):
            res=self.client.get('/api/plugins/mteam-qbit/settings')
            self.assertEqual(res.status_code,500);self.assertNotIn('DO_NOT_ECHO',res.text)
    def test_probe_errors_visible_but_sanitized(self):
        cases=[(api.core.PluginError('Service HTTP error 403 (URL/body omitted).'),'http_403'),
               (api.core.PluginError('Required credential environment variable is unset.'),'credential_missing'),
               (RuntimeError('https://secret.test/?key=DO_NOT_ECHO'),'probe_failed')]
        for error,code in cases:
            with patch.object(api,'probe',side_effect=error):
                res=self.client.post('/api/plugins/mteam-qbit/test/mteam')
                self.assertEqual(res.json()['code'],code)
                self.assertFalse(res.json()['ok'])
                self.assertNotIn('DO_NOT_ECHO',res.text)
    def test_credential_comparison_only_booleans(self):
        for effective,saved,match in [('SECRET_A','SECRET_A',True),('SECRET_B','SECRET_A',False),('', 'SECRET_A',None)]:
            scope=types.ModuleType('agent.secret_scope')
            scope.get_secret=lambda key:effective
            scope.load_env_file=lambda path:{'MTEAM_API_KEY':saved}
            with patch.dict(sys.modules,{'agent':types.ModuleType('agent'),'agent.secret_scope':scope}),patch.object(api.settings,'config_path',return_value=Path('/profile/mteam-qbit/config.json')):
                data=api.credential_diagnostics('mteam')
                self.assertEqual(data['effective_matches_saved'],match)
                self.assertTrue(all(v is None or type(v) is bool for v in data.values()))
                self.assertNotIn('SECRET',json.dumps(data))
    def test_http_diagnostics_safe_classification(self):
        err=api.core.ServiceHTTPError(403,{'Content-Type':'text/html; secret=DO_NOT_ECHO','Server':'cloudflare','cf-ray':'DO_NOT_ECHO','cf-mitigated':'challenge','Set-Cookie':'DO_NOT_ECHO'})
        with patch.object(api,'probe',side_effect=err),patch.object(api,'credential_diagnostics',return_value=None):
            result=self.client.post('/api/plugins/mteam-qbit/test/mteam').json()
            self.assertEqual(result['http_diagnostics']['response_type'],'html')
            self.assertTrue(result['http_diagnostics']['challenge_header'])
            self.assertNotIn('DO_NOT_ECHO',json.dumps(result))
    def test_no_http_diagnostics_from_arbitrary_exception(self):
        err=RuntimeError('DO_NOT_ECHO');err.http_diagnostics={'secret':'DO_NOT_ECHO'}
        with patch.object(api,'probe',side_effect=err),patch.object(api,'credential_diagnostics',return_value=None):
            result=self.client.post('/api/plugins/mteam-qbit/test/mteam').json()
            self.assertNotIn('http_diagnostics',result)
            self.assertNotIn('DO_NOT_ECHO',json.dumps(result))
    def test_business_failure_classification(self):
        err=api.core.MTeamResponseError('business_rejected',200,'123')
        with patch.object(api,'probe',side_effect=err),patch.object(api,'credential_diagnostics',return_value=None):
            data=self.client.post('/api/plugins/mteam-qbit/test/mteam').json()
            self.assertEqual(data['code'],'business_rejected')
            self.assertEqual(data['response_diagnostics'],{'http_status':200,'code_type':'string','numeric_code':'123','upstream_message':None})
    def test_upstream_message_real_http_wrapper_through_route(self):
        from io import BytesIO
        secret='SECRET_CURRENT_KEY'
        payload={'code':'403','message':'API rejected '+secret+' https://host.test/?token=abc user@example.com', 'data':{'email':'PRIVATE_PROFILE'}}
        class Response(BytesIO):
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        http=api.core.HTTP()
        http.opener.open=lambda *a,**k:Response(json.dumps(payload).encode())
        mt=api.core.MTeam({'base_url':'https://example.com'},http)
        with patch.object(api.core,'secret',return_value=secret),patch.object(api,'probe',side_effect=lambda target:mt.test_connection()),patch.object(api,'credential_diagnostics',return_value=None):
            res=self.client.post('/api/plugins/mteam-qbit/test/mteam')
        self.assertEqual(res.json()['response_diagnostics']['http_status'],200)
        message=res.json()['response_diagnostics']['upstream_message']
        self.assertTrue('API rejected' in message or 'message omitted' in message)
        for value in [secret,'host.test','user@example.com','PRIVATE_PROFILE']:
            self.assertNotIn(value,res.text)
    def test_message_sanitization_bounds_and_objects(self):
        sanitize=api.core.sanitized_upstream_message
        self.assertEqual(sanitize({'message':'Invalid API key'}),'Invalid API key')
        self.assertIsNone(sanitize({'message':{'account':'PRIVATE'}}))
        self.assertNotIn('PRIVATE',sanitize({'message':'<script>PRIVATE</script>'}))
        self.assertLessEqual(len(sanitize({'msg':'正常错误'*200})),301)
        self.assertNotIn('abc',sanitize({'message':'token=abc'}))
        self.assertNotIn('abc',sanitize({'message':'Bearer abc'}))
    def test_quoted_and_reassembled_secret_regressions(self):
        sanitize=api.core.sanitized_upstream_message
        for msg in ['password="short secret words"', 'token: "abc xyz"', '"password": "short secret words"']:
            value=sanitize({'message':msg})
            self.assertNotIn('words',value);self.assertNotIn('xyz',value)
        key='0123456789abcdef0123456789abcdef'
        for msg in [key[:16]+'<b></b>'+key[16:],key[:16]+'\n'+key[16:],key[:16]+'&lt;b&gt;&lt;/b&gt;'+key[16:]]:
            self.assertNotIn(key,sanitize({'message':msg},(key,)))
    def test_probe_route_no_download(self):
        with patch.object(api,'probe',return_value={'ok':True,'message':'test only'}) as probe:
            res=self.client.post('/api/plugins/mteam-qbit/test/qbit')
            self.assertEqual(res.status_code,200);probe.assert_called_once_with('qbit')

if __name__=='__main__':unittest.main()
