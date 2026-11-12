"""Real source reload regression; no network or credentials."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
import sys

ROOT=Path(__file__).resolve().parents[1]
class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'plugin'
        shutil.copytree(ROOT,self.root,ignore=shutil.ignore_patterns('__pycache__'))
    def tearDown(self):self.tmp.cleanup()
    def load(self):
        spec=importlib.util.spec_from_file_location('reload_test_'+uuid.uuid4().hex,self.root/'dashboard/plugin_api.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    def test_actual_core_and_settings_reload(self):
        core=self.root/'core.py';settings=self.root/'settings.py'
        core.write_text(core.read_text()+"\nAUDIT_MARKER='old'\n")
        settings.write_text(settings.read_text()+"\nAUDIT_MARKER='old'\n")
        a=self.load();same=self.load()
        self.assertIs(a.core,same.core)
        core.write_text(core.read_text().replace("AUDIT_MARKER='old'","AUDIT_MARKER='new'"))
        settings.write_text(settings.read_text().replace("AUDIT_MARKER='old'","AUDIT_MARKER='new'"))
        b=self.load()
        self.assertIsNot(a.core,b.core);self.assertIsNot(a.settings,b.settings)
        self.assertEqual(a.core.AUDIT_MARKER,'old');self.assertEqual(b.core.AUDIT_MARKER,'new')
        self.assertEqual(b.settings.AUDIT_MARKER,'new')
        self.assertNotEqual(a.BUSINESS_BUILD,b.BUSINESS_BUILD)
        self.assertEqual(b.settings.PluginError,b.core.PluginError)
    def test_failed_generation_does_not_damage_old(self):
        a=self.load();core=self.root/'core.py';original=core.read_text()
        core.write_text(original+'\nraise RuntimeError("test")\n')
        with self.assertRaises(RuntimeError):self.load()
        self.assertIs(sys.modules[a.PACKAGE].core,a.core)
        core.write_text(original)
        self.assertIs(self.load().core,a.core)
    def test_settings_failure_retry_and_cleanup(self):
        a=self.load();path=self.root/'settings.py';original=path.read_text()
        path.write_text(original+'\nraise RuntimeError("late")\n')
        with self.assertRaises(RuntimeError):self.load()
        self.assertIs(sys.modules[a.PACKAGE].core,a.core)
        path.write_text(original+'\nAUDIT_MARKER="fixed"\n')
        b=self.load();self.assertEqual(b.settings.AUDIT_MARKER,'fixed')
    def test_distinct_install_namespaces(self):
        a=self.load()
        with tempfile.TemporaryDirectory() as other:
            target=Path(other)/'plugin';shutil.copytree(self.root,target)
            original=self.root;self.root=target
            try:b=self.load()
            finally:self.root=original
            self.assertEqual(a.BUSINESS_BUILD,b.BUSINESS_BUILD)
            self.assertNotEqual(a.PACKAGE,b.PACKAGE)
            self.assertIsNot(a.core,b.core)
