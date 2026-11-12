import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pt_web_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
pkg=importlib.util.module_from_spec(spec);sys.modules['pt_web_test']=pkg;spec.loader.exec_module(pkg)
from pt_web_test import settings
from pt_web_test.core import PluginError

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.saved={}
        constants=types.ModuleType('hermes_constants');constants.get_hermes_home=lambda:self.home
        lifecycle=types.ModuleType('hermes_cli.credential_lifecycle')
        lifecycle.save_provider_env_credential=lambda k,v:self.saved.update({k:v})
        scope=types.ModuleType('agent.secret_scope');scope.get_secret=lambda k:self.saved.get(k)
        self.patch=patch.dict(sys.modules,{'hermes_constants':constants,'hermes_cli':types.ModuleType('hermes_cli'),'hermes_cli.credential_lifecycle':lifecycle,'agent':types.ModuleType('agent'),'agent.secret_scope':scope})
        self.patch.start()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_save_and_redaction(self):
        settings.save_settings(settings.DEFAULTS,{'mteam_api_key':'TEST_ONLY_SECRET'})
        text=settings.config_path().read_text()
        self.assertNotIn('TEST_ONLY_SECRET',text)
        self.assertNotIn('TEST_ONLY_SECRET',json.dumps(settings.secret_presence()))
        self.assertTrue(settings.secret_presence()['mteam_api_key'])
        self.assertEqual(os.stat(settings.config_path()).st_mode & 0o777,0o600)
        self.assertEqual(settings.read_settings(),settings.DEFAULTS)
    def test_blank_preserves_secret(self):
        settings.save_settings(settings.DEFAULTS,{'qbit_password':'sample'})
        settings.save_settings(settings.DEFAULTS,{'qbit_password':''})
        self.assertEqual(self.saved['QBIT_PASSWORD'],'sample')
    def test_env_key_allowlist(self):
        with self.assertRaises(PluginError):settings.save_settings(settings.DEFAULTS,{'OPENAI_API_KEY':'never'})
        self.assertEqual(self.saved,{})
    def test_validation_before_secret_write(self):
        with self.assertRaises(PluginError):settings.save_settings({**settings.DEFAULTS,'default_save_path':'/a/../b'},{'qbit_password':'never'})
        self.assertEqual(self.saved,{})
    def test_no_invalid_or_url_secrets(self):
        for values in [{'mteam_url':'http://example.com'}, {'qbit_url':'https://user:pass@example.com'}, {'download_hosts':['*.example.com']}, {'start_paused':'false'}, {'approval_ttl_seconds':0}, {'secret':'no'}]:
            with self.assertRaises((PluginError,TypeError)):settings.validate(values)
    def test_profile_paths_not_cached(self):
        settings.save_settings(settings.DEFAULTS)
        old=settings.config_path()
        self.home=self.home/'other-profile'
        settings.save_settings({**settings.DEFAULTS,'category':'other'})
        self.assertNotEqual(old,settings.config_path())
        self.assertEqual(json.loads(old.read_text())['category'],'hermes')
        self.assertEqual(settings.read_settings()['category'],'other')
    def test_legacy_unknown_secret_not_returned(self):
        settings.save_settings(settings.DEFAULTS)
        path=settings.config_path();obj=json.loads(path.read_text());obj['password']='TEST_SECRET';path.write_text(json.dumps(obj))
        self.assertNotIn('TEST_SECRET',json.dumps(settings.read_settings()))
    def test_legacy_default_visible(self):
        cfg={'destinations':{'tv':'/tv','movies':'/movies'}}
        values=settings.public_values(cfg)
        self.assertEqual(values['default_save_path'],'/tv')
        self.assertNotIn('destinations',values)
        self.assertNotIn('destinations',settings.to_config(values,self.home/'config.json'))
    def test_secret_scope_fail_closed(self):
        from pt_web_test.core import secret
        with patch('agent.secret_scope.get_secret',side_effect=RuntimeError('scope required')):
            with patch.dict(os.environ,{'QBIT_PASSWORD':'should-not-fallback'}):
                with self.assertRaises(RuntimeError):secret('QBIT_PASSWORD')

if __name__=='__main__':unittest.main()
