"""Anthropic Messages ↔ Mistral chat completions translation."""
import json
import uuid


class Unsupported(ValueError):
    pass


def system_text(content):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(not isinstance(block, dict) or block.get('type') != 'text' for block in content):
            raise Unsupported('Only text system blocks are supported')
        return '\n'.join(block['text'] for block in content)
    raise Unsupported('System content must be text or text blocks')


def translate(body, model):
    if not isinstance(body, dict) or not isinstance(body.get('messages'), list):
        raise Unsupported('messages must be an array')
    messages = []
    system = system_text(body.get('system', ''))
    if system:
        messages.append({'role': 'system', 'content': system})
    for message in body['messages']:
        role = message['role']
        if role == 'system':
            # Recent Claude Code sends system entries in messages, including
            # empty entries carrying per-turn output_config metadata. Mistral
            # receives actual instructions in order, without empty messages.
            content = system_text(message.get('content', ''))
            if content:
                messages.append({'role': 'system', 'content': content})
            continue
        content = message['content']
        if role not in ('user', 'assistant'):
            raise Unsupported('Only system, user and assistant messages are supported')
        if isinstance(content, str):
            messages.append({'role': role, 'content': content})
            continue
        text, calls, results = [], [], []
        for block in content:
            kind = block.get('type')
            if kind == 'text':
                text.append(block['text'])
            elif kind == 'tool_use' and role == 'assistant':
                calls.append({'id': block['id'], 'type': 'function', 'function': {
                    'name': block['name'], 'arguments': json.dumps(block['input'])}})
            elif kind == 'tool_result' and role == 'user':
                result = block.get('content', '')
                if isinstance(result, list):
                    if any(b.get('type') != 'text' for b in result):
                        raise Unsupported('Only text tool results are supported')
                    result = '\n'.join(b['text'] for b in result)
                results.append({'role': 'tool', 'tool_call_id': block['tool_use_id'], 'content': result})
            elif kind in ('thinking', 'redacted_thinking'):
                continue
            else:
                raise Unsupported(f'Unsupported content block: {kind}')
        messages.extend(results)
        if text or calls:
            item = {'role': role, 'content': '\n'.join(text) or None}
            if calls:
                item['tool_calls'] = calls
            messages.append(item)
    result = {'model': model, 'messages': messages, 'stream': bool(body.get('stream'))}
    for key in ('max_tokens', 'temperature', 'top_p'):
        if key in body:
            result[key] = body[key]
    if body.get('stop_sequences'):
        result['stop'] = body['stop_sequences']
    if body.get('tools'):
        tools = []
        for tool in body['tools']:
            if tool.get('type', 'custom') != 'custom':
                raise Unsupported('Provider-hosted tools are unsupported')
            tools.append({'type': 'function', 'function': {
                'name': tool['name'], 'description': tool.get('description', ''),
                'parameters': tool['input_schema']}})
        result['tools'] = tools
    choice = body.get('tool_choice', {})
    if choice:
        kind = choice.get('type')
        if kind == 'tool':
            result['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
        elif kind in ('auto', 'none', 'any'):
            result['tool_choice'] = {'any': 'required'}.get(kind, kind)
        else:
            raise Unsupported('Unsupported tool_choice')
        if choice.get('disable_parallel_tool_use'):
            result['parallel_tool_calls'] = False
    return result


def stop_reason(reason):
    return {'tool_calls': 'tool_use', 'length': 'max_tokens'}.get(reason, 'end_turn')


def visible_text(content):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if block.get('type') == 'text':
                parts.append(block['text'])
            elif block.get('type') not in ('thinking', 'reasoning'):
                raise Unsupported('Unsupported upstream content block')
        return ''.join(parts)
    raise Unsupported('Unsupported upstream content')


def response(data, model):
    choice = data['choices'][0]
    message = choice['message']
    content = []
    text = visible_text(message.get('content'))
    if text:
        content.append({'type': 'text', 'text': text})
    for call in message.get('tool_calls') or []:
        args = call['function']['arguments']
        content.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'],
                        'input': json.loads(args) if isinstance(args, str) else args})
    usage = data.get('usage', {})
    return {'id': 'msg_' + uuid.uuid4().hex, 'type': 'message', 'role': 'assistant',
            'model': model, 'content': content, 'stop_reason': stop_reason(choice.get('finish_reason')),
            'stop_sequence': None, 'usage': {'input_tokens': usage.get('prompt_tokens', 0),
                                          'output_tokens': usage.get('completion_tokens', 0)}}


def event(kind, **fields):
    return f'event: {kind}\ndata: {json.dumps({"type": kind, **fields})}\n\n'


class Stream:
    def __init__(self, model):
        self.model, self.blocks, self.reason = model, {}, 'end_turn'
        self.usage = {'input_tokens': 0, 'output_tokens': 0}

    def start(self):
        return event('message_start', message={'id': 'msg_' + uuid.uuid4().hex,
            'type': 'message', 'role': 'assistant', 'model': self.model, 'content': [],
            'stop_reason': None, 'stop_sequence': None, 'usage': dict(self.usage)})

    def feed(self, data):
        out = []
        usage = data.get('usage') or {}
        self.usage.update(input_tokens=usage.get('prompt_tokens', self.usage['input_tokens']),
                          output_tokens=usage.get('completion_tokens', self.usage['output_tokens']))
        for choice in data.get('choices', []):
            if choice.get('finish_reason'):
                self.reason = stop_reason(choice['finish_reason'])
            delta = choice.get('delta', {})
            text = visible_text(delta.get('content'))
            if text:
                if 'text' not in self.blocks:
                    self.blocks['text'] = len(self.blocks)
                    out.append(event('content_block_start', index=self.blocks['text'], content_block={'type': 'text', 'text': ''}))
                out.append(event('content_block_delta', index=self.blocks['text'], delta={'type': 'text_delta', 'text': text}))
            for call in delta.get('tool_calls') or []:
                key = ('tool', call.get('index', 0))
                function = call.get('function', {})
                if key not in self.blocks:
                    if not call.get('id') or not function.get('name'):
                        raise Unsupported('Upstream tool stream omitted initial ID or name')
                    self.blocks[key] = len(self.blocks)
                    out.append(event('content_block_start', index=self.blocks[key], content_block={
                        'type': 'tool_use', 'id': call['id'], 'name': function['name'], 'input': {}}))
                if function.get('arguments'):
                    out.append(event('content_block_delta', index=self.blocks[key], delta={
                        'type': 'input_json_delta', 'partial_json': function['arguments']}))
        return ''.join(out)

    def finish(self):
        return ''.join(event('content_block_stop', index=i) for i in self.blocks.values()) + event(
            'message_delta', delta={'stop_reason': self.reason, 'stop_sequence': None}, usage=self.usage) + event('message_stop')
