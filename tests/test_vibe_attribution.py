import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx2

from ccmistral import auth
from ccmistral.server import create_app


class AttributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_auth_retains_vibe_user_agent_and_connector_identity(self):
        from vibe.utils.http import get_user_agent
        with patch('ccmistral.auth._load_credential', return_value='test-plan-credential'):
            headers = await auth.headers('https://api.mistral.ai/v1/chat/completions')
        self.assertEqual(headers['user-agent'], get_user_agent('mistral') + ' CC-Mistral/0.1')
        self.assertEqual(headers['Authorization'], 'Bearer test-plan-credential')
        self.assertNotIn('x-external-workspace-id', headers)

    async def test_all_completion_modes_carry_vibe_attribution_and_stable_affinity(self):
        requests = []
        async def headers(url):
            return {'authorization': 'Bearer test-session'}
        def upstream(request):
            payload = json.loads(request.content)
            requests.append((payload, request.headers['x-affinity']))
            if payload['stream']:
                return httpx2.Response(200, content='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return httpx2.Response(200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
        app = create_app('zai-glm-5-3-flash', 'local', get_headers=headers, transport=httpx2.MockTransport(upstream))
        async with httpx2.AsyncClient(base_url='http://localhost', transport=httpx2.ASGITransport(app=app)) as client:
            for stream in (False, True):
                result = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                    'stream': stream, 'metadata': {'call_source': 'api'}, 'messages': [{'role': 'user', 'content': 'hi'}]})
                self.assertEqual(result.status_code, 200)
            health = (await client.get('/health', headers={'x-api-key': 'local'})).json()
            self.assertEqual(health['call_source'], 'vibe_code')
            self.assertFalse(health['billing_verified'])
        self.assertEqual(requests[0][1], requests[1][1])
        for payload, affinity in requests:
            self.assertEqual(payload['metadata'], {'call_source': 'vibe_code', 'client_name': 'claude-mistral',
                                                   'host_kind': 'local', 'session_id': affinity})
