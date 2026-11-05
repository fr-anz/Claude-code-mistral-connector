import json
import unittest

import httpx2

from ccmistral.protocol import Stream, Unsupported, response, translate
from ccmistral.server import create_app


class TranslationTests(unittest.TestCase):
    def test_inline_system_instructions_keep_their_order(self):
        result = translate({'system': 'Base instructions', 'messages': [
            {'role': 'user', 'content': 'First question'},
            {'role': 'assistant', 'content': 'First answer'},
            {'role': 'system', 'content': [
                {'type': 'text', 'text': 'Updated instructions', 'cache_control': {'type': 'ephemeral'}},
                {'type': 'text', 'text': 'Keep this context'},
            ]},
            {'role': 'user', 'content': 'Next question'},
        ]}, 'test')
        self.assertEqual(result['messages'], [
            {'role': 'system', 'content': 'Base instructions'},
            {'role': 'user', 'content': 'First question'},
            {'role': 'assistant', 'content': 'First answer'},
            {'role': 'system', 'content': 'Updated instructions\nKeep this context'},
            {'role': 'user', 'content': 'Next question'},
        ])

    def test_empty_per_turn_system_markers_are_omitted(self):
        markers = [
            {'role': 'system', 'content': '', 'output_config': {'effort': 'high'}},
            {'role': 'system', 'content': [], 'output_config': {'effort': 'high'}},
            {'role': 'system', 'output_config': {'effort': 'high'}},
            {'role': 'system', 'content': None},
        ]
        result = translate({'messages': [*markers, {'role': 'user', 'content': 'hi'}]}, 'test')
        self.assertEqual(result['messages'], [{'role': 'user', 'content': 'hi'}])

    def test_system_blocks_remain_validated(self):
        with self.assertRaises(Unsupported):
            translate({'messages': [{'role': 'system', 'content': [{'type': 'image'}]}]}, 'test')

    def test_full_tool_round_trip(self):
        body = {'system': [{'type': 'text', 'text': 'Code carefully'}], 'messages': [
            {'role': 'user', 'content': 'Read a file'},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'abc123xyz',
              'name': 'Read', 'input': {'path': 'a.py'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'abc123xyz',
              'content': [{'type': 'text', 'text': 'print(1)'}]}, {'type': 'text', 'text': 'Explain it'}]}],
            'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}], 'tool_choice': {'type': 'any'}}
        result = translate(body, 'test-model')
        self.assertEqual(result['messages'][0], {'role': 'system', 'content': 'Code carefully'})
        self.assertEqual(result['messages'][2]['tool_calls'][0]['function']['arguments'], '{"path": "a.py"}')
        self.assertEqual(result['messages'][3], {'role': 'tool', 'tool_call_id': 'abc123xyz', 'content': 'print(1)'})
        self.assertEqual(result['messages'][4]['content'], 'Explain it')
        self.assertEqual(result['tool_choice'], 'required')
        answer = response({'choices': [{'message': {'tool_calls': result['messages'][2]['tool_calls']},
                                        'finish_reason': 'tool_calls'}]}, 'test-model')
        self.assertEqual(answer['content'][0]['input'], {'path': 'a.py'})
        self.assertEqual(answer['stop_reason'], 'tool_use')

    def test_image_without_source_is_rejected(self):
        with self.assertRaises(Unsupported):
            translate({'messages': [{'role': 'user', 'content': [{'type': 'image'}]}]}, 'test')

    def test_split_tool_arguments_and_usage(self):
        stream = Stream('test')
        parts = [stream.start()]
        for delta in [
            {'tool_calls': [{'index': 0, 'id': 'abc123xyz', 'function': {'name': 'Read', 'arguments': '{"path":'}}]},
            {'tool_calls': [{'index': 0, 'function': {'arguments': '"a.py"}'}}]}]:
            parts.append(stream.feed({'choices': [{'delta': delta}]}))
        parts.append(stream.feed({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}],
                                  'usage': {'prompt_tokens': 12, 'completion_tokens': 5}}))
        parts.append(stream.finish())
        events = [json.loads(line[6:]) for line in ''.join(parts).splitlines() if line.startswith('data: ')]
        args = ''.join(e['delta']['partial_json'] for e in events if e['type'] == 'content_block_delta')
        self.assertEqual(json.loads(args), {'path': 'a.py'})
        self.assertEqual(events[-2]['usage'], {'input_tokens': 12, 'output_tokens': 5})
        self.assertEqual(events[-2]['delta']['stop_reason'], 'tool_use')


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, handler, headers=None):
        async def auth(url):
            self.assertEqual(url, 'https://api.mistral.ai/v1/chat/completions')
            return {'authorization': 'Bearer mock-oidc-session'}
        return httpx2.AsyncClient(base_url='http://localhost', transport=httpx2.ASGITransport(
            app=create_app('chosen-model', 'local-only', get_headers=headers or auth,
                           transport=httpx2.MockTransport(handler))))

    async def test_text_and_forced_model(self):
        def upstream(request):
            self.assertEqual(request.headers['authorization'], 'Bearer mock-oidc-session')
            self.assertEqual(json.loads(request.content)['model'], 'chosen-model')
            return httpx2.Response(200, json={'choices': [{'message': {'content': 'Hello'}, 'finish_reason': 'stop'}],
                                             'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})
        async with self.make_client(upstream) as client:
            answer = await client.post('/v1/messages', headers={'x-api-key': 'local-only'}, json={
                'model': 'ignored-claude-model', 'messages': [{'role': 'user', 'content': 'Hi'}]})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()['model'], 'chosen-model')
        self.assertEqual(answer.json()['content'], [{'type': 'text', 'text': 'Hello'}])

    async def test_claude_system_messages_reach_upstream(self):
        for stream in (False, True):
            def upstream(request):
                self.assertEqual(json.loads(request.content)['messages'], [
                    {'role': 'system', 'content': 'You are a coding assistant'},
                    {'role': 'user', 'content': 'hi'},
                ])
                if stream:
                    return httpx2.Response(200, content=(
                        'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":"stop"}]}\n\n'
                        'data: [DONE]\n\n'))
                return httpx2.Response(200, json={
                    'choices': [{'message': {'content': 'Hello'}, 'finish_reason': 'stop'}]})
            async with self.make_client(upstream) as client:
                answer = await client.post('/v1/messages', headers={'x-api-key': 'local-only'}, json={
                    'stream': stream, 'messages': [
                        {'role': 'system', 'content': [{'type': 'text', 'text': 'You are a coding assistant'}]},
                        {'role': 'user', 'content': 'hi'},
                        {'role': 'system', 'content': [], 'output_config': {'effort': 'high'}},
                    ]})
            self.assertEqual(answer.status_code, 200)
            self.assertIn('Hello', answer.text)

    async def test_stream_and_truncation(self):
        for done in (True, False):
            def upstream(request):
                data = {'choices': [{'delta': {'content': 'Hello'}, 'finish_reason': 'stop'}]}
                return httpx2.Response(200, content='data: ' + json.dumps(data) + '\n\n' + ('data: [DONE]\n\n' if done else ''))
            async with self.make_client(upstream) as client:
                answer = await client.post('/v1/messages', headers={'authorization': 'Bearer local-only'},
                                     json={'stream': True, 'messages': [{'role': 'user', 'content': 'Hi'}]})
            self.assertIn('event: message_start', answer.text)
            self.assertIn('event: message_stop' if done else 'event: error', answer.text)
            if not done:
                self.assertNotIn('event: message_stop', answer.text)

    async def test_auth_and_browser_access(self):
        def upstream(request):
            self.fail('Rejected requests must not reach upstream')
        async with self.make_client(upstream) as client:
            self.assertEqual((await client.post('/v1/messages', json={})).status_code, 401)
            self.assertEqual((await client.get('/health')).status_code, 401)
            self.assertEqual((await client.post('/v1/messages', headers={'x-api-key': 'local-only',
                'origin': 'https://example.com'}, json={})).status_code, 403)

    async def test_limits_preserved_without_leaking_upstream(self):
        def upstream(request):
            return httpx2.Response(429, headers={'retry-after': '60'}, json={'secret': 'private-upstream-value'})
        async with self.make_client(upstream) as client:
            answer = await client.post('/v1/messages', headers={'x-api-key': 'local-only'},
                                 json={'messages': [{'role': 'user', 'content': 'Hi'}]})
        self.assertEqual(answer.status_code, 429)
        self.assertEqual(answer.headers['retry-after'], '60')
        self.assertNotIn('private-upstream-value', answer.text)

    async def test_auth_failure_does_not_fall_back(self):
        async def failed(url):
            raise RuntimeError('secret-session-value')
        async with self.make_client(lambda request: self.fail('No fallback allowed'), failed) as client:
            answer = await client.post('/v1/messages', headers={'x-api-key': 'local-only'},
                                 json={'messages': [{'role': 'user', 'content': 'Hi'}]})
        self.assertEqual(answer.status_code, 401)
        self.assertNotIn('secret-session-value', answer.text)


if __name__ == '__main__':
    unittest.main()
