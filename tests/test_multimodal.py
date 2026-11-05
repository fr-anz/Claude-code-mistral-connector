import unittest

from ccmistral.protocol import translate


IMAGE = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aGVsbG8='}}
IMAGE_CHUNK = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}}


class MultimodalTests(unittest.TestCase):
    def test_images_preserve_content_order(self):
        result = translate({'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Before'}, IMAGE,
            {'type': 'text', 'text': 'After'},
            {'type': 'image', 'source': {'type': 'url', 'url': 'https://example.com/image.png'}},
        ]}]}, 'vision')
        self.assertEqual(result['messages'][0]['content'], [
            {'type': 'text', 'text': 'Before'}, IMAGE_CHUNK,
            {'type': 'text', 'text': 'After'},
            {'type': 'image_url', 'image_url': {'url': 'https://example.com/image.png'}},
        ])

    def test_parallel_tool_results_with_images_and_references(self):
        body = {'messages': [
            {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': 'abc123xyz', 'name': 'Read', 'input': {}},
                {'type': 'tool_use', 'id': 'def456uvw', 'name': 'ToolSearch', 'input': {}},
            ]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'abc123xyz', 'content': [
                    {'type': 'text', 'text': 'Screenshot'}, IMAGE]},
                {'type': 'tool_result', 'tool_use_id': 'def456uvw', 'content': [
                    {'type': 'tool_reference', 'tool_name': 'mcp__test__read'}]},
                {'type': 'text', 'text': 'Explain'},
            ]}], 'tools': [{'name': 'mcp__test__read', 'defer_loading': True,
                          'input_schema': {'type': 'object'}}]}
        result = translate(body, 'vision')
        self.assertEqual([m['role'] for m in result['messages']], ['assistant', 'tool', 'tool', 'user'])
        self.assertIn(IMAGE_CHUNK, result['messages'][1]['content'])
        self.assertIn('mcp__test__read', result['messages'][2]['content'])
        self.assertEqual(result['tools'][0]['function']['name'], 'mcp__test__read')

    def test_structured_output_mode(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
        result = translate({'messages': [{'role': 'user', 'content': 'status'}],
                            'output_config': {'format': {'type': 'json_schema', 'schema': schema}}}, 'test')
        self.assertEqual(result['response_format']['json_schema']['schema'], schema)

    def test_documents(self):
        blocks = [
            {'type': 'document', 'source': {'type': 'text', 'data': 'plain text'}},
            {'type': 'document', 'source': {'type': 'base64', 'media_type': 'text/plain', 'data': 'aGVsbG8='}},
            {'type': 'document', 'title': 'report.pdf', 'source': {
                'type': 'base64', 'media_type': 'application/pdf', 'data': 'aGVsbG8='}},
            {'type': 'document', 'source': {'type': 'url', 'url': 'https://example.com/report.pdf'}},
            {'type': 'document', 'source': {'type': 'content', 'content': [IMAGE]}},
        ]
        content = translate({'messages': [{'role': 'user', 'content': blocks}]}, 'test')['messages'][0]['content']
        self.assertEqual(content, [
            {'type': 'text', 'text': 'plain text'}, {'type': 'text', 'text': 'hello'},
            {'type': 'document_url', 'document_url': 'data:application/pdf;base64,aGVsbG8=', 'document_name': 'report.pdf'},
            {'type': 'document_url', 'document_url': 'https://example.com/report.pdf'}, IMAGE_CHUNK])

    def test_invalid_sources_rejected_without_echoing_data(self):
        from ccmistral.protocol import Unsupported
        for source in [None, {}, {'type': 'url', 'url': 'file:///private'},
                       {'type': 'base64', 'media_type': 'image/png', 'data': 'private-not-base64'},
                       {'type': 'base64', 'media_type': 'audio/wav', 'data': 'aGVsbG8='}]:
            with self.assertRaises(Unsupported) as error:
                translate({'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': source}]}]}, 'test')
            self.assertNotIn('private', str(error.exception))

    def test_tool_ids_and_error_status_survive_replay(self):
        ids = ['toolu_long_shared_prefix_1', 'toolu_long_shared_prefix_2', 'abc123xyz']
        result = translate({'messages': [
            {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': id, 'name': 'Read', 'input': {}} for id in ids]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': id, 'is_error': True, 'content': 'Missing'} for id in ids]},
        ]}, 'test')
        mapped = [call['id'] for call in result['messages'][0]['tool_calls']]
        self.assertEqual(len(set(mapped)), 3)
        self.assertEqual(mapped[-1], 'abc123xyz')
        for id, message in zip(mapped, result['messages'][1:]):
            self.assertRegex(id, r'^[a-zA-Z0-9]{9}$')
            self.assertEqual(id, message['tool_call_id'])
            self.assertEqual(message['content'], 'Tool execution failed:\nMissing')


class MultimodalGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_and_tool_round_trip_in_both_response_modes(self):
        import json
        import httpx2
        from ccmistral.server import create_app
        async def auth(url):
            return {'authorization': 'Bearer mock'}
        for stream in (False, True):
            seen = []
            def upstream(request):
                payload = json.loads(request.content)
                seen.append(payload)
                if len(seen) == 1:
                    self.assertEqual(payload['messages'][0]['content'], [IMAGE_CHUNK])
                    self.assertEqual(payload['tools'][0]['function']['name'], 'Read')
                    message = {'tool_calls': [{'id': 'abc123xyz', 'type': 'function',
                        'function': {'name': 'Read', 'arguments': '{"file_path":"image.png"}'}}]}
                    reason = 'tool_calls'
                else:
                    self.assertEqual(payload['messages'][1]['tool_calls'][0]['id'], 'abc123xyz')
                    self.assertEqual(payload['messages'][2]['content'], [IMAGE_CHUNK])
                    self.assertEqual(payload['messages'][2]['tool_call_id'], 'abc123xyz')
                    message, reason = {'content': 'A screenshot'}, 'stop'
                if stream:
                    if 'tool_calls' in message:
                        message['tool_calls'][0]['index'] = 0
                    chunk = {'choices': [{'delta': message, 'finish_reason': reason}]}
                    return httpx2.Response(200, content='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
                return httpx2.Response(200, json={'choices': [{'message': message, 'finish_reason': reason}]})
            app = create_app('vision', 'local', get_headers=auth, transport=httpx2.MockTransport(upstream))
            async with httpx2.AsyncClient(base_url='http://local', transport=httpx2.ASGITransport(app=app)) as client:
                body = {'stream': stream, 'messages': [{'role': 'user', 'content': [IMAGE]}],
                        'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}]}
                first = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json=body)
                self.assertEqual(first.status_code, 200)
                if stream:
                    events = [json.loads(line[6:]) for line in first.text.splitlines() if line.startswith('data: ')]
                    call = next(e['content_block'] for e in events if e['type'] == 'content_block_start')
                    call['input'] = json.loads(''.join(e['delta']['partial_json'] for e in events if e['type'] == 'content_block_delta'))
                else:
                    call = first.json()['content'][0]
                self.assertEqual(call['name'], 'Read')
                self.assertEqual(call['input'], {'file_path': 'image.png'})
                body['messages'].extend([{'role': 'assistant', 'content': [call]},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call['id'], 'content': [IMAGE]}]}])
                second = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json=body)
                self.assertEqual(second.status_code, 200)
                self.assertIn('A screenshot', second.text)
                self.assertNotIn('event: error', second.text)
                self.assertEqual(len(seen), 2)

    async def test_text_only_model_has_actionable_image_error(self):
        import httpx2
        from ccmistral.server import create_app
        async def no_auth(url):
            self.fail('Unsupported image must be rejected before authentication')
        app = create_app('text-only', 'local', catalog=[{
            'id': 'text-only', 'capabilities': {'vision': False}}], get_headers=no_auth)
        async with httpx2.AsyncClient(base_url='http://local', transport=httpx2.ASGITransport(app=app)) as client:
            answer = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                'model': 'text-only', 'messages': [{'role': 'user', 'content': [IMAGE]}]})
        self.assertEqual(answer.status_code, 400)
        self.assertIn('vision-capable model', answer.json()['error']['message'])
