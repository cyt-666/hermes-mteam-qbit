"""Plugin-route tests only. Production authentication is supplied by Hermes mount."""
import importlib.util
from pathlib import Path
import unittest
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
            self.assertEqual(res.json()['backend_version'],'0.3.2')
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
    def test_probe_route_no_download(self):
        with patch.object(api,'probe',return_value={'ok':True,'message':'test only'}) as probe:
            res=self.client.post('/api/plugins/mteam-qbit/test/qbit')
            self.assertEqual(res.status_code,200);probe.assert_called_once_with('qbit')

if __name__=='__main__':unittest.main()
