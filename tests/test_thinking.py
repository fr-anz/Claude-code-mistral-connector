import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx2

from ccmistral import picker
from ccmistral.protocol import Stream, response
from ccmistral.server import create_app
from ccmistral.thinking import levels_for, validate


class ThinkingTests(unittest.TestCase):
    def test_user_provided_level_sets(self):
        for model in ('glm-5.3', 'glm-5-3', 'glm-5.3-flash', 'glm-5-3-flash'):
            self.assertEqual(levels_for(model), ['low', 'high', 'max'])
        self.assertEqual(levels_for('mistral-large-4'), ['low', 'medium', 'high'])
        with self.assertRaises(ValueError):
            validate('glm-5.3-flash', 'medium')
        self.assertEqual(levels_for('unknown'), [])

    def test_catalog_advertised_levels_and_label(self):
        self.assertEqual(levels_for('opaque-id', {'label': 'GLM-5.3 Flash (Mistral Hosted)'}), ['low', 'high', 'max'])
        self.assertEqual(levels_for('new-model', {'parameters': {'reasoning': ['none', 'high']}}), ['none', 'high'])

    def test_saved_levels_are_per_model(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(picker, 'selection_path', return_value=Path(directory) / 'selection.json'):
            picker.remember('glm-5.3', 'max')
            picker.remember('mistral-large-4', 'medium')
            # Selecting the model again must preserve its thinking setting.
            picker.remember('glm-5.3')
            with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), patch.object(
                picker.curses, 'wrapper', return_value='high') as screen:
                self.assertEqual(picker.pick_thinking('glm-5.3'), 'high')
            self.assertEqual(screen.call_args.args[2], 2)
            self.assertEqual(picker._selection()['thinking'], {'glm-5.3': 'high', 'mistral-large-4': 'medium'})

    def test_structured_reasoning_does_not_become_answer_text(self):
        blocks = [{'type': 'thinking', 'thinking': [{'type': 'text', 'text': 'internal'}]},
                  {'type': 'text', 'text': 'Answer'}]
        answer = response({'choices': [{'message': {'content': blocks}, 'finish_reason': 'stop'}]}, 'glm-5.3')
        self.assertEqual(answer['content'], [{'type': 'text', 'text': 'Answer'}])
        chunk = Stream('glm-5.3').feed({'choices': [{'delta': {'content': blocks}}]})
        self.assertIn('Answer', chunk)
        self.assertNotIn('internal', chunk)


class ThinkingGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_level_reaches_upstream_for_both_response_modes(self):
        for stream in (True, False):
            async def auth(url):
                return {'authorization': 'Bearer mock'}
            def upstream(request):
                payload = json.loads(request.content)
                self.assertEqual(payload['reasoning_effort'], 'low')
                self.assertNotIn('thinking', payload)
                if stream:
                    return httpx2.Response(200, content='data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                return httpx2.Response(200, json={'choices': [{'message': {'content': 'Hi'}, 'finish_reason': 'stop'}]})
            app = create_app('glm-5.3-flash', 'local', thinking='max', get_headers=auth, transport=httpx2.MockTransport(upstream))
            async with httpx2.AsyncClient(base_url='http://localhost', transport=httpx2.ASGITransport(app=app)) as client:
                answer = await client.post('/v1/messages', headers={'x-api-key': 'local'}, json={
                    'stream': stream, 'thinking': {'type': 'disabled'},
                    'output_config': {'effort': 'low'}, 'messages': [{'role': 'user', 'content': 'Hi'}]})
            self.assertEqual(answer.status_code, 200)
            self.assertIn('Hi', answer.text)
