import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import antigravity_cli as agy


class AntigravityProfileTests(unittest.TestCase):
    def test_chat_requires_explicit_denials_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / 'settings.json'
            for value in (None, {}, {'toolPermission': 'strict'}):
                if value is not None:
                    settings.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(agy.AntigravityError):
                    agy.require_chat_permissions(settings)
            settings.write_text(json.dumps({
                'toolPermission': 'strict',
                'permissions': {'deny': ['write_file(*)', 'command(*)', 'unsandboxed(*)',
                                        'mcp(*)', 'execute_url(*)']},
            }), encoding='utf-8')
            agy.require_chat_permissions(settings)
            settings.write_text('{bad json', encoding='utf-8')
            with self.assertRaises(agy.AntigravityError):
                agy.require_chat_permissions(settings)

    def test_companion_profile_excludes_tools_and_inherited_coding_context(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = agy.prepare_chat_workspace(Path(directory))
            text = (workspace / '.agents/agents/home-companion.md').read_text(encoding='utf-8')
        for option in ('excludeDefaultComponents: true', 'inheritCustomizations: false',
                       'tools: []', 'inheritMcp: false', 'commandExecutionPolicy: off'):
            self.assertIn(option, text)
        self.assertLess(len(text), 1600)
        for identity in ('Ithil', 'Connor'):
            self.assertNotIn(identity, text)

    def test_command_pins_model_and_uses_official_stdin_stream_without_permission_bypass(self):
        command = agy.build_chat_command('agy.exe', 'gemini-3.1-pro-high', '90s')
        self.assertEqual(command[command.index('--model') + 1], 'gemini-3.1-pro-high')
        self.assertEqual(command[command.index('--agent') + 1], 'home-companion')
        self.assertEqual(command[command.index('--input-format') + 1], 'stream-json')
        self.assertEqual(command[command.index('--output-format') + 1], 'stream-json')
        self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertNotIn('--continue', command)

    def test_catalog_uses_cli_model_slugs_and_skips_unavailable_models(self):
        catalog = agy.parse_model_catalog(json.dumps({'models': [
            {'slug': 'gemini-3.1-pro-high', 'name': 'Gemini 3.1 Pro (High)'},
            {'slug': 'claude-opus', 'name': 'Claude Opus', 'available': False},
        ]}))
        self.assertEqual(catalog, [{'model': 'gemini-3.1-pro-high', 'name': 'Gemini 3.1 Pro (High)'}])

    def test_official_text_catalog_keeps_distinct_effort_variants(self):
        self.assertEqual(agy.parse_model_catalog(
            'gemini-3.1-pro-high     Gemini 3.1 Pro (High)\n'
            'gemini-3.1-pro-low      Gemini 3.1 Pro (Low)\n'
        ), [
            {'model': 'gemini-3.1-pro-high', 'name': 'Gemini 3.1 Pro (High)'},
            {'model': 'gemini-3.1-pro-low', 'name': 'Gemini 3.1 Pro (Low)'},
        ])


class FakeProcess:
    def __init__(self, events):
        self.stdin = unittest.mock.Mock()
        self.stdin.drain = AsyncMock()
        self.stdin.wait_closed = AsyncMock()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(''.join(json.dumps(event) + '\n' for event in events).encode())
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode = 0
        self.pid = None
        self.wait = AsyncMock(return_value=0)


class AntigravityStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.permission_patch = patch.object(agy, 'require_chat_permissions', create=True)
        self.permission_patch.start()
        self.addCleanup(self.permission_patch.stop)

    async def test_discovery_uses_supported_tab_separated_models_command(self):
        process = unittest.mock.Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(
            b'Fetching available models...\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n', b''))
        with patch.object(agy, 'find_binary', return_value='agy.exe'), patch.object(
            agy.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)
        ) as spawn:
            rows = await agy.discover_models(force=True)
        self.assertEqual(rows, [{'model': 'gemini-3.8-flash-low', 'name': 'Gemini 3.8 Flash (Low)'}])
        self.assertEqual(spawn.call_args.args, ('agy.exe', 'models'))

    async def test_existing_provider_forwards_model_and_usage_to_home(self):
        import ai_providers
        from stream_safety import StreamActivity

        async def stream(*args, **kwargs):
            self.assertEqual(args[2], 'gemini-3.1-pro-high')
            self.assertIn('[User]\n你好', args[1])
            yield {'kind': 'text', 'text': '自然回复'}
            yield {'kind': 'completed', 'usage': {'input_tokens': 20, 'output_tokens': 4, 'cache_read_tokens': 10}}

        meta = {}
        with patch.object(ai_providers, '_find_antigravity_binary', return_value='agy.exe'), patch.object(
            agy, 'stream_chat', stream
        ):
            chunks = [chunk async for chunk in ai_providers.call_antigravity_cli(
                [{'role': 'user', 'content': '你好'}], 'gemini-3.1-pro-high', meta
            )]
        self.assertEqual([chunk for chunk in chunks if chunk and not chunk.startswith(ai_providers.CLI_STATUS_PREFIX)], ['自然回复'])
        self.assertEqual(meta['prompt_tokens'], 20)
        self.assertEqual(meta['completion_tokens'], 4)
        self.assertTrue(any(isinstance(chunk, StreamActivity) for chunk in chunks))

    async def test_model_selection_refreshes_runtime_catalog_and_stays_visible(self):
        import config
        from routes import settings

        before = dict(config.ANTIGRAVITY_MODELS) if hasattr(config, 'ANTIGRAVITY_MODELS') else {}
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(
                config, 'ANTIGRAVITY_MODELS_PATH', Path(directory) / 'models.json'
            ), patch.object(agy, 'discover_models', AsyncMock(return_value=[
                {'model': 'gemini-3.8-flash-high', 'name': 'Gemini 3.8 Flash (High)'},
                {'model': 'claude-opus-5-5-medium', 'name': 'Claude Opus 5.5 (Medium)'},
                {'model': 'gemini-3.1-pro-high', 'name': 'Gemini 3.1 Pro (High)'}
            ])):
                rows = await settings.list_models()
                self.assertEqual(len(config._read_antigravity_models()), 2)
            route = next(row for row in rows if row['key'] == 'AGY · Gemini 3.8 Flash (High)')
            self.assertEqual(route['provider'], 'antigravity_cli')
            self.assertEqual([row['key'] for row in rows if row['provider'] == 'antigravity_cli'],
                             ['AGY · Gemini 3.8 Flash (High)', 'AGY · Claude Opus 5.5 (Medium)'])
            key = route['key']
            self.assertEqual(config.resolve_model_key(key), key)
            config.refresh_custom_models()
            self.assertEqual(config.MODELS[key]['model'], 'gemini-3.8-flash-high')
            self.assertFalse(config.is_model_deprecated(key))
            self.assertNotIn('AGY · Gemini 3.1 Pro (High)', config.MODELS)
            self.assertFalse(config.MODELS[key]['vision'])
            self.assertFalse(config.MODELS[key]['audio'])
            self.assertTrue(config.is_model_deprecated('AGY-3.1pro'))
        finally:
            if hasattr(config, 'replace_antigravity_models'):
                config.replace_antigravity_models(before)

    async def test_stream_preserves_chinese_deltas_without_repeating_final_response(self):
        process = FakeProcess([
            {'event': 'init', 'init': {'agent': agy.AGENT_NAME, 'tools': ['run_command', 'manage_task']}},
            {'event': 'step_update', 'step_update': {'step_type': 'agent_response', 'text_delta': '你'}},
            {'event': 'step_update', 'step_update': {'step_type': 'agent_response', 'text_delta': '好🌙'}},
            {'event': 'result', 'result': {'status': 'SUCCESS', 'response': '你好🌙', 'usage': {'input_tokens': 12}}},
        ])
        with tempfile.TemporaryDirectory() as directory, patch.object(
            agy.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)
        ) as spawn:
            events = [event async for event in agy.stream_chat('agy.exe', '你好', 'test-model', Path(directory))]
        deltas = [event['text'] for event in events if event['kind'] == 'text']
        self.assertEqual(deltas, ['你', '好🌙'])
        self.assertEqual(events[-1]['usage']['input_tokens'], 12)
        payload = json.loads(process.stdin.write.call_args.args[0])
        self.assertEqual(payload['message']['content'], '你好')
        self.assertEqual(spawn.call_args.kwargs['cwd'], str(Path(directory)))

    async def test_response_only_and_structured_failure_are_not_silently_lost(self):
        for result, expected in [
            ({'status': 'SUCCESS', 'response': '完整回复'}, '完整回复'),
            ({'status': 'ERROR', 'error': 'quota exhausted', 'response': ''}, 'quota exhausted'),
        ]:
            process = FakeProcess([{'event': 'init', 'init': {'agent': agy.AGENT_NAME, 'tools': []}}, {'event': 'result', 'result': result}])
            with tempfile.TemporaryDirectory() as directory, patch.object(
                agy.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)
            ):
                if result['status'] == 'SUCCESS':
                    events = [event async for event in agy.stream_chat('agy.exe', 'hello', '', Path(directory))]
                    self.assertEqual(events[-2]['text'], expected)
                else:
                    with self.assertRaisesRegex(agy.AntigravityError, expected):
                        _ = [event async for event in agy.stream_chat('agy.exe', 'hello', '', Path(directory))]

    async def test_wrong_agent_and_actual_tool_steps_are_rejected(self):
        for events, error in [
            ([{'event': 'init', 'init': {'agent': 'default'}}], '陪伴配置'),
            ([{'event': 'init', 'init': {'agent': agy.AGENT_NAME}},
              {'event': 'step_update', 'step_update': {'step_type': 'tool', 'tool_name': 'manage_task'}}], '工具'),
        ]:
            process = FakeProcess(events)
            with tempfile.TemporaryDirectory() as directory, patch.object(
                agy.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)
            ):
                with self.assertRaisesRegex(agy.AntigravityError, error):
                    _ = [event async for event in agy.stream_chat('agy.exe', 'hello', '', Path(directory))]


if __name__ == '__main__':
    unittest.main()
