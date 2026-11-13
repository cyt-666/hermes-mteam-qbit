import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import contextlib
import io

spec = importlib.util.spec_from_file_location('probe', Path(__file__).with_name('live_mteam_readonly.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class LiveProbeSafetyTests(unittest.TestCase):
    def test_allowlist_blocks_production_downloads_redirect_queries(self):
        self.assertTrue(probe.allowed_request('POST', 'https://test2.m-team.cc/api/torrent/search'))
        for url in ('https://api.m-team.cc/api/torrent/search',
                    'http://test2.m-team.cc/api/torrent/search',
                    'https://test2.m-team.cc/api/torrent/genDlToken',
                    'https://test2.m-team.cc/api/torrent/search?key=secret',
                    'https://user@test2.m-team.cc/api/torrent/search'):
            self.assertFalse(probe.allowed_request('POST', url))
        self.assertFalse(probe.allowed_request('GET', 'https://test2.m-team.cc/api/torrent/search'))

    def test_private_file_and_nonempty_key_required(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'secret.json'; p.write_text(json.dumps({'MTEAM_TEST_API_KEY':'synthetic'}));p.chmod(0o600)
            self.assertEqual(probe.read_key(p),'synthetic')
            p.chmod(0o644)
            with self.assertRaises(ValueError):probe.read_key(p)
            p.chmod(0o600);p.write_text('{"MTEAM_TEST_API_KEY":""}')
            with self.assertRaises(ValueError):probe.read_key(p)
            link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaises(ValueError):probe.read_key(link)

    def fake_run(self, fail=False, mismatch=False):
        class FakeHTTP:
            def __init__(self, **kwargs):pass
        class FakeMTeam:
            def __init__(self,*args):pass
            def test_connection(self):
                if fail:raise ValueError('DO_NOT_ECHO_SECRET')
                return True
            def categories(self):
                if fail:raise ValueError('DO_NOT_ECHO_SECRET')
                return {'categories':[{'id':'422','nameChs':'Software'}], 'groups':{}}
            def search(self,*args,**kwargs):
                if fail:raise ValueError('DO_NOT_ECHO_SECRET')
                filtered=bool(kwargs)
                return {'items':[{'id':'1','category_id':'999' if filtered and mismatch else '422'}],
                        'page':args[1], 'requested_limit':5,'limit':5,'has_more':False,'next_page':None,
                        'category_ids':['999' if mismatch else '422']}
            def details(self,*args):return {}
        core=SimpleNamespace(HTTP=FakeHTTP,MTeam=FakeMTeam)
        fake_spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda _:None))
        out=io.StringIO()
        with patch.object(probe.importlib.util,'spec_from_file_location',return_value=fake_spec), \
             patch.object(probe.importlib.util,'module_from_spec',return_value=core), \
             patch.object(probe,'read_key',return_value='synthetic'), \
             contextlib.redirect_stdout(out):
            result=probe.run('not-read',delay=0)
        self.assertNotIn('DO_NOT_ECHO_SECRET',out.getvalue())
        return result

    def test_mismatch_is_failure_and_nonzero(self):
        result=self.fake_run(mismatch=True)
        for op in ('category_id_search','category_name_search'):
            row=next(x for x in result if x['operation']==op)
            self.assertFalse(row['ok'])
            self.assertEqual(row['error_type'],'VerificationMismatch')
        self.assertEqual(probe.exit_status(result),1)

    def test_failed_requests_nonzero_and_success_zero(self):
        result=self.fake_run(fail=True)
        self.assertTrue(all(not x['ok'] for x in result))
        self.assertEqual(probe.exit_status(result),1)
        self.assertEqual(probe.exit_status([]),1)
        self.assertEqual(probe.exit_status(self.fake_run()),0)
