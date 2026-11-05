import argparse
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx2

from ccmistral.catalog import context_environment, initial_route, native_models
from ccmistral.server import create_app

CATALOG = [
    {'id': 'glm-5.3-flash', 'label': 'GLM 5.3 Flash', 'recommended': True, 'max_context_length': 1_048_576},
    {'id': 'zai-glm-5-3', 'label': 'GLM 5.3', 'max_context_length': 1_000_000},
    {'id': 'mistral-large-4', 'label': 'Mistral Large 4', 'max_context_length': 1_048_576},
]


class NativeMenuTests(unittest.TestCase):
    def test_native_rows_and_defaults(self):
        routes, options = native_models(CATALOG)
        self.assertEqual(len(options), 3)
        self.assertEqual([row['label'] for row in options], [item['label'] for item in CATALOG])
        self.assertEqual(routes['zai-glm-5-3'], ('zai-glm-5-3', None))
        self.assertEqual(initial_route(CATALOG)[:2], ('glm-5.3-flash', None))
        self.assertEqual(initial_route(CATALOG)[2], 'glm-5.3-flash')
        self.assertEqual(initial_route(CATALOG, requested='zai-glm-5-3[1m]')[0], 'zai-glm-5-3')
        self.assertEqual(initial_route(CATALOG, previous='mistral-large-4', saved_thinking={'mistral-large-4': 'medium'})[:2], ('mistral-large-4', None))
        with self.assertRaises(ValueError):
            initial_route(CATALOG, requested='not-in-catalog')
        with self.assertRaises(ValueError):
            initial_route(CATALOG, requested='glm-5.3-flash', thinking='medium')

    def test_launcher_configures_native_menu_without_terminal_picker(self):
        from ccmistral.__main__ import run
        args = argparse.Namespace(model=None, thinking=None, port=3456, claude_args=[])
        process, opener = Mock(), Mock()
        process.poll.return_value = None
        opener.open.return_value.__enter__ = Mock(return_value=Mock(status=200))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('ccmistral.auth.signed_in', return_value=True), patch('ccmistral.__main__.shutil.which', return_value='/bin/claude'), patch(
            'ccmistral.__main__.models', new=AsyncMock(return_value=CATALOG)), patch('ccmistral.picker._selection', return_value={}), patch(
            'ccmistral.__main__.start_gateway', return_value=(process, 'http://127.0.0.1:3456')), patch('ccmistral.__main__.urllib.request.build_opener', return_value=opener), patch(
            'ccmistral.__main__.subprocess.call', return_value=0) as launch:
            with self.assertRaises(SystemExit) as stopped:
                run(args)
        self.assertEqual(stopped.exception.code, 0)
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index('--permission-mode') + 1], 'bypassPermissions')
        settings = json.loads(command[command.index('--settings') + 1])
        self.assertTrue(settings['modelPicker']['replaceBuiltInOptions'])
        self.assertEqual(len(settings['modelPicker']['options']), 3)
        self.assertEqual(launch.call_args.kwargs['env']['ANTHROPIC_MODEL'], 'glm-5.3-flash')
        self.assertEqual(launch.call_args.kwargs['env']['ENABLE_TOOL_SEARCH'], 'false')
        self.assertEqual(launch.call_args.kwargs['env']['CLAUDE_CODE_MAX_CONTEXT_TOKENS'], '1048576')
        process.terminate.assert_called_once()

    def test_selected_native_window_is_not_reduced_by_other_models(self):
        catalog = [*CATALOG, {'id': 'medium', 'max_context_length': 262_144}]
        routes, options = native_models(catalog)
        self.assertEqual(options[0]['model'], 'glm-5.3-flash')
        self.assertEqual(routes[options[0]['model']], ('glm-5.3-flash', None))
        self.assertEqual(options[-1]['model'], 'medium')
        self.assertEqual(context_environment(catalog, 'medium')['CLAUDE_CODE_MAX_CONTEXT_TOKENS'], '262144')
        catalog.append({'id': 'unknown', 'max_context_length': None})
        self.assertNotIn('CLAUDE_CODE_MAX_CONTEXT_TOKENS', context_environment(catalog, 'unknown'))

    def test_invalid_context_metadata_does_not_enable_1m(self):
        for value in (None, True, '1048576', -1):
            _, options = native_models([{'id': 'unknown', 'max_context_length': value}])
            self.assertEqual(options[0]['model'], 'unknown')


class SwitchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_effort_changes_for_same_model_and_per_turn_system_metadata(self):
        seen = []
        async def auth(url):
            return {'authorization': 'Bearer mock'}
        def upstream(request):
            payload = json.loads(request.content)
            seen.append(payload.get('reasoning_effort'))
            self.assertEqual(payload['model'], 'glm-5.3-flash')
            self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'hi'}])
            return httpx2.Response(200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
        app = create_app('glm-5.3-flash', 'local', thinking='high', catalog=CATALOG,
                         get_headers=auth, transport=httpx2.MockTransport(upstream))
        bodies = [
            {'output_config': {'effort': 'low'}},
            {'output_config': {'effort': 'max'}},
            {'output_config': {'effort': 'low'}, 'messages': [
                {'role': 'system', 'content': [], 'output_config': {'effort': 'high'}},
                {'role': 'user', 'content': 'hi'},
                {'role': 'system', 'content': [], 'output_config': {'effort': 'max'}},
            ]},
            {'output_config': {'effort': None}},
        ]
        async with httpx2.AsyncClient(base_url='http://localhost', transport=httpx2.ASGITransport(app=app)) as client:
            for extra in bodies:
                answer = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                    'model': 'glm-5.3-flash', 'messages': [{'role': 'user', 'content': 'hi'}], **extra})
                self.assertEqual(answer.status_code, 200)
        self.assertEqual(seen, ['low', 'max', 'max', None])

    async def test_model_and_effort_switch_without_gateway_restart(self):
        seen = []
        async def auth(url):
            return {'authorization': 'Bearer mock'}
        def upstream(request):
            payload = json.loads(request.content)
            seen.append((payload['model'], payload.get('reasoning_effort')))
            return httpx2.Response(200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
        app = create_app('glm-5.3-flash', 'local', thinking='low', catalog=CATALOG,
                         get_headers=auth, transport=httpx2.MockTransport(upstream))
        async with httpx2.AsyncClient(base_url='http://localhost', transport=httpx2.ASGITransport(app=app)) as client:
            for model, level in [('glm-5.3-flash', 'max'), ('zai-glm-5-3', 'max'), ('mistral-large-4', 'medium')]:
                answer = await client.post('/v1/messages', headers={'authorization': 'Bearer local', 'x-api-key': 'unrelated'},
                    json={'model': model + ('[1m]' if model.startswith('zai-') else ''), 'output_config': {'effort': level}, 'messages': [{'role': 'user', 'content': 'Hi'}]})
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.json()['model'], model)
            listing = await client.get('/v1/models?limit=1000', headers={'x-api-key': 'local'})
            self.assertEqual(len(listing.json()['data']), 3)
            self.assertFalse(listing.json()['has_more'])
            unknown = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                'model': 'glm-5.3-flash', 'output_config': {'effort': 'medium'}, 'messages': []})
            self.assertEqual(unknown.status_code, 400)
            self.assertEqual((await client.get('/v1/models')).status_code, 401)
        self.assertEqual(seen, [('glm-5.3-flash', 'max'), ('zai-glm-5-3', 'max'), ('mistral-large-4', 'medium')])
