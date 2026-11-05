import json
import os
import secrets
import uuid

import httpx2
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from . import auth
from .protocol import Stream, Unsupported, event, response, translate

BASE = 'https://api.mistral.ai/v1'
LIMIT = 16 * 1024 * 1024


def error(message, status=400, kind='invalid_request_error'):
    return JSONResponse({'type': 'error', 'error': {'type': kind, 'message': message}}, status_code=status)


def create_app(model, token, *, thinking=None, catalog=None, get_headers=auth.headers, transport=None):
    from .model_limits import is_removed_model
    if is_removed_model(model):
        raise ValueError('DeepSeek has been removed. Choose another model.')
    if not token:
        raise ValueError('A local gateway token is required')

    from .catalog import native_models
    routes, options = native_models(catalog) if catalog is not None else ({}, [])
    session_id = str(uuid.uuid4())

    def authorized(request):
        candidates = (request.headers.get('x-api-key', ''),
                      request.headers.get('authorization', '').removeprefix('Bearer '))
        return any(secrets.compare_digest(value.encode(), token.encode()) for value in candidates)

    async def health(request):
        if not authorized(request):
            return error('Local gateway authentication required', 401, 'authentication_error')
        return JSONResponse({'status': 'ok', 'model': model, 'auth': 'mistral-vibe-browser-plan',
                             'thinking': thinking, 'call_source': 'vibe_code', 'billing_verified': False})

    async def list_models(request):
        if not authorized(request):
            return error('Local gateway authentication required', 401, 'authentication_error')
        rows = [{'id': option['model'], 'type': 'model', 'display_name': option['label'],
                 'created_at': '2026-01-01T00:00:00Z'} for option in options]
        if catalog is not None:
            from .catalog import context_window
            from .model_limits import output_limit
            for row, option in zip(rows, options):
                entry = next(item for item in catalog if item['id'] == routes[option['model']][0])
                window, maximum = context_window(entry), output_limit(entry)
                if window:
                    row['max_input_tokens'] = window
                if maximum:
                    row['max_tokens'] = maximum
        if not rows:
            rows = [{'id': model, 'type': 'model', 'display_name': model,
                     'created_at': '2026-01-01T00:00:00Z'}]
        return JSONResponse({'data': rows, 'has_more': False, 'first_id': rows[0]['id'], 'last_id': rows[-1]['id']})

    async def messages(request: Request):
        if not authorized(request):
            return error('Local gateway authentication required', 401, 'authentication_error')
        if request.headers.get('origin'):
            return error('Browser requests are not supported', 403)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > LIMIT:
                    return error('Request exceeds 16 MiB', 413)
            body = json.loads(raw)
            selected_model = model
            if catalog is not None:
                requested = body.get('model')
                if requested is not None:
                    if requested not in routes:
                        raise Unsupported('Unknown Mistral model. Choose a model from /model.')
                    selected_model = routes[requested][0]
            from .thinking import request_effort, validate
            selected_thinking = request_effort(body, thinking if selected_model == model else None)
            if catalog is not None and selected_thinking is not None:
                entry = next(item for item in catalog if item['id'] == selected_model)
                try:
                    validate(selected_model, selected_thinking, entry)
                except ValueError as exc:
                    raise Unsupported(str(exc) + '. Change it with /effort.') from None
            payload = translate(body, selected_model)
            # Vibe attributes completions separately from OIDC authentication.
            # Keep the connector identified while using Vibe Code's call source.
            payload['metadata'] = {
                'call_source': 'vibe_code', 'client_name': 'claude-mistral',
                'host_kind': 'local', 'session_id': session_id,
            }
            if selected_thinking is not None:
                payload['reasoning_effort'] = selected_thinking
            if catalog is not None:
                from .model_limits import output_limit
                entry = next(item for item in catalog if item['id'] == selected_model)
                capabilities = entry.get('capabilities') or {}
                if capabilities.get('vision') is False and any(
                    isinstance(message.get('content'), list) and any(
                        part.get('type') == 'image_url' for part in message['content'])
                    for message in payload['messages']
                ):
                    raise Unsupported(f'{selected_model} does not support images. Choose a vision-capable model with /model.')
                maximum = output_limit(entry)
                if maximum is not None:
                    if payload.get('max_tokens', maximum) > maximum:
                        raise Unsupported(f'{selected_model} supports a maximum output of {maximum:,} tokens.')
                    payload.setdefault('max_tokens', maximum)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return error(str(exc) if isinstance(exc, Unsupported) else 'Malformed Messages request')
        try:
            upstream_headers = await get_headers(BASE + '/chat/completions')
            upstream_headers = dict(upstream_headers)
            upstream_headers['x-affinity'] = session_id
        except Exception:
            return error('Vibe browser credential missing or unreadable. Run claude-mistral login.', 401, 'authentication_error')
        client = httpx2.AsyncClient(timeout=httpx2.Timeout(300, connect=20), follow_redirects=False,
                                    trust_env=False, transport=transport)
        try:
            upstream = await client.send(client.build_request('POST', BASE + '/chat/completions',
                                                               json=payload, headers=upstream_headers), stream=True)
        except Exception:
            await client.aclose()
            return error('Could not connect to Mistral', 502, 'api_error')
        if upstream.status_code != 200:
            code = upstream.status_code
            retry = upstream.headers.get('retry-after')
            await upstream.aclose()
            await client.aclose()
            # Never echo upstream bodies, which can contain credential details.
            message = {401: 'Mistral rejected the session. Run claude-mistral login.',
                       402: 'Mistral reports that usage allowance or credits are exhausted.',
                       403: 'Mistral denied access to this model or subscription.',
                       429: 'Mistral rate or usage limit reached.'}.get(code, f'Mistral returned HTTP {code}')
            result = error(message, code if 400 <= code < 600 else 502, 'api_error')
            if retry:
                result.headers['retry-after'] = retry
            return result
        if not payload['stream']:
            try:
                data = json.loads(await upstream.aread())
                return JSONResponse(response(data, selected_model))
            except Exception:
                return error('Invalid response from Mistral', 502, 'api_error')
            finally:
                await upstream.aclose()
                await client.aclose()

        async def generate():
            state, complete = Stream(selected_model), False
            try:
                yield state.start()
                async for line in upstream.aiter_lines():
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        complete = True
                        break
                    if data:
                        chunk = json.loads(data)
                        if chunk.get('error'):
                            raise ValueError('upstream error')
                        yield state.feed(chunk)
                if not complete:
                    raise ValueError('truncated stream')
                yield state.finish()
            except Exception:
                yield event('error', error={'type': 'api_error', 'message': 'Mistral stream failed or ended prematurely'})
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(generate(), media_type='text/event-stream', headers={'cache-control': 'no-cache'})

    return Starlette(routes=[Route('/health', health), Route('/v1/models', list_models), Route('/v1/messages', messages, methods=['POST'])])
