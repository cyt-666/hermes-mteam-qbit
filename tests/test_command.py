import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pt_command_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
plugin=importlib.util.module_from_spec(spec);sys.modules[spec.name]=plugin;spec.loader.exec_module(plugin)

class CommandTests(unittest.TestCase):
    def test_readonly_mapping(self):
        expected={
            '':(None,{}),'help':(None,{}),
            'categories':('pt_categories',{}),'path':('pt_destinations',{}),
            'list':('pt_list_downloads',{'page':1}),'list 2':('pt_list_downloads',{'page':2}),
            'detail 123':('pt_details',{'resource_id':'123'}),
            'search hello world':('pt_search',{'query':'hello world'}),
            'search --mode movie --page 2 --limit 10 "hello world"':('pt_search',{'mode':'movie','page':2,'limit':10,'query':'hello world'}),
            'search -- --mode':('pt_search',{'query':'--mode'})}
        for raw,result in expected.items():self.assertEqual(plugin.parse_mt_command(raw),result)
        for mode in plugin.TOOLS['pt_search'][1]['mode']['enum']:
            self.assertEqual(plugin.parse_mt_command('search --mode '+mode+' sample')[1]['mode'],mode)

    def test_invalid_write_commands_and_no_echo(self):
        ctx=Mock()
        for raw in ['add 123','delete all','shell echo SECRET','pt_delete_download','detail 1 2',
                    'list 0','list 1001','categories extra','search','search --mode invalid SECRET',
                    'search --limit 0 secret','search --page 1001 secret','search --page 1 --page 2 x',
                    'search --unknown SECRET','search --mode','search "SECRET','path\nSECRET']:
            reply=plugin.run_mt_command(ctx,raw)
            self.assertNotIn('SECRET',reply)
            self.assertIn('格式无效',reply)
        ctx.dispatch_tool.assert_not_called()

    def test_dispatch_uses_host_registry_only(self):
        ctx=Mock();ctx.dispatch_tool.return_value='{"ok":true,"result":[]}'
        with patch.object(plugin,'invoke',side_effect=AssertionError('No direct invocation')):
            self.assertEqual(plugin.run_mt_command(ctx,'search --mode music piano'),ctx.dispatch_tool.return_value)
        ctx.dispatch_tool.assert_called_once_with('pt_search',{'mode':'music','query':'piano'})
        ctx.dispatch_tool.reset_mock()
        self.assertIn('/mt search',plugin.run_mt_command(ctx,''))
        ctx.dispatch_tool.assert_not_called()
        ctx.dispatch_tool.side_effect=RuntimeError('SECRET_TOKEN')
        self.assertNotIn('SECRET_TOKEN',plugin.run_mt_command(ctx,'list'))

    def test_command_registered_as_mt(self):
        ctx=Mock();plugin.register(ctx)
        kwargs=ctx.register_command.call_args.kwargs
        self.assertEqual(kwargs['name'],'mt')
        self.assertIn('/mt search',kwargs['handler']('help'))

    def test_older_host_keeps_tools_without_bypassing_permissions(self):
        from types import SimpleNamespace
        ctx=SimpleNamespace(register_tool=Mock(),register_skill=Mock())
        with self.assertWarnsRegex(RuntimeWarning,'slash command unavailable'):
            plugin.register(ctx)
        self.assertEqual(ctx.register_tool.call_count,len(plugin.TOOLS))
