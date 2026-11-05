import json
import unittest

import httpx2

from ccmistral.catalog import context_environment, native_models
from ccmistral.model_limits import DEEPSEEK_ID, prepare_catalog
from ccmistral.server import create_app


class NativeLimitsTests(unittest.TestCase):
    def test_native_limits_and_deepseek_removal(self):
        catalog = prepare_catalog([{'id': 'zai-glm-5-3'}, {'id': 'zai-glm-5-3-flash'}, {'id': 'mistral-large-4'}])
        self.assertEqual(len(catalog), 3)
        for entry in catalog:
            self.assertEqual(entry['max_context_length'], 1_000_000)
        self.assertEqual(context_environment(catalog, 'zai-glm-5-3-flash'), {
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1000000', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '128000'})
        routes, options = native_models(catalog)
        self.assertNotIn(DEEPSEEK_ID + '[1m]', routes)
        self.assertEqual(len(options), 3)

    def test_live_limits_are_preserved(self):
        catalog = prepare_catalog([{'id': 'zai-glm-5-3-flash', 'max_context_length': 1_048_576, 'max_output_tokens': 131_072}])
        self.assertEqual(len(catalog), 1)
        self.assertEqual(context_environment(catalog, 'zai-glm-5-3-flash'), {
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1048576', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '131072'})

    def test_deepseek_is_filtered_from_live_catalog_and_native_picker(self):
        incoming = [{'id': DEEPSEEK_ID, 'label': 'DeepSeek'}, {'id': 'zai-glm-5-3'}]
        self.assertEqual([entry['id'] for entry in prepare_catalog(incoming)], ['zai-glm-5-3'])
        routes, options = native_models(incoming)
        self.assertNotIn(DEEPSEEK_ID, routes)
        self.assertNotIn(DEEPSEEK_ID + '[1m]', routes)
        self.assertEqual(len(options), 1)
        with self.assertRaises(ValueError):
            create_app(DEEPSEEK_ID, 'local', catalog=incoming)


class OutputLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_glm_native_output_limit_and_discovery(self):
        seen = []
        catalog = prepare_catalog([{'id': 'zai-glm-5-3-flash'}])
        async def auth(url):
            return {'authorization': 'Bearer mock'}
        def upstream(request):
            seen.append(json.loads(request.content)['max_tokens'])
            return httpx2.Response(200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
        app = create_app('zai-glm-5-3-flash', 'local', catalog=catalog, get_headers=auth, transport=httpx2.MockTransport(upstream))
        async with httpx2.AsyncClient(base_url='http://localhost', transport=httpx2.ASGITransport(app=app)) as client:
            for extra in ({}, {'max_tokens': 128_000}, {'max_tokens': 4096}):
                result = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                    'model': 'zai-glm-5-3-flash[1m]', 'messages': [{'role': 'user', 'content': 'hi'}], **extra})
                self.assertEqual(result.status_code, 200)
            result = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                'model': 'zai-glm-5-3-flash', 'messages': [], 'max_tokens': 128_001})
            self.assertEqual(result.status_code, 400)
            discovery = (await client.get('/v1/models', headers={'x-api-key': 'local'})).json()['data'][0]
            self.assertEqual(discovery['max_input_tokens'], 1_000_000)
            self.assertEqual(discovery['max_tokens'], 128_000)
        self.assertEqual(seen, [128_000, 128_000, 4096])
