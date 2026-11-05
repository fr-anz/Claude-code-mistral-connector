import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description='Claude Code → Mistral using Vibe browser sign-in')
    sub = parser.add_subparsers(dest='command')
    login = sub.add_parser('login', help='Sign in through Vibe OIDC (experimental)')
    login.add_argument('--no-browser', action='store_true')
    sub.add_parser('status', help='Check for a stored OIDC session without making requests')
    sub.add_parser('models', help='List models available to the signed-in account')
    for command in ('serve', 'run'):
        item = sub.add_parser(command)
        item.add_argument('--model', required=command == 'serve',
                          help='Exact Mistral model ID; omit to start with your last or the recommended model')
        item.add_argument('--port', type=int, default=3456)
        item.add_argument('--thinking', help='Initial thinking level; /effort can change it during the session')
        if command == 'run':
            item.add_argument('claude_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(['run'] if len(sys.argv) == 1 else None)
    from . import auth
    try:
        if args.command == 'login':
            asyncio.run(auth.login(not args.no_browser))
        elif args.command == 'status':
            print('OIDC session stored (not validated online).' if auth.signed_in() else 'No matching OIDC session. Run claude-mistral login.')
        elif args.command == 'models':
            for item in asyncio.run(models()):
                print(item['id'])
        elif args.command == 'serve':
            import uvicorn
            from .server import create_app
            token = os.environ.get('CC_MISTRAL_LOCAL_TOKEN')
            if not token:
                sys.exit('Set CC_MISTRAL_LOCAL_TOKEN to a random local gateway secret, or use run.')
            catalog = json.loads(os.environ['CC_MISTRAL_CATALOG']) if 'CC_MISTRAL_CATALOG' in os.environ else None
            uvicorn.run(create_app(args.model, token, thinking=args.thinking, catalog=catalog),
                        host='127.0.0.1', port=args.port, access_log=False)
        else:
            run(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        sys.exit('Operation failed. Check network access, Vibe OIDC support and system keyring availability. No API-key fallback was attempted.')


async def models():
    import httpx2
    from .auth import headers
    from .server import BASE
    url = BASE + '/model-catalog?surface=vibe-code'
    async with httpx2.AsyncClient(follow_redirects=False, trust_env=False, timeout=30) as client:
        result = await client.get(url, headers=await headers(url))
        if result.status_code in (404, 405):
            url = BASE + '/models'
            result = await client.get(url, headers=await headers(url))
        if result.status_code != 200:
            sys.exit(f'Mistral model listing failed (HTTP {result.status_code}).')
        data = result.json()
        from .model_limits import prepare_catalog
        return prepare_catalog(data['models'] if 'models' in data else data['data'])


def run(args):
    from .auth import signed_in
    if not shutil.which('claude'):
        sys.exit('Claude Code is not installed or not on PATH.')
    if not signed_in():
        sys.exit('No matching Mistral OIDC session. Run claude-mistral login first.')
    from .catalog import context_environment, initial_route, native_models
    from .picker import _selection
    print('Loading Mistral models…', flush=True)
    catalog = asyncio.run(models())
    saved = _selection()
    try:
        args.model, args.thinking, initial = initial_route(
            catalog, args.model, args.thinking, saved.get('model'), saved.get('thinking'))
    except ValueError as exc:
        sys.exit(str(exc))
    _, options = native_models(catalog)
    settings = {'modelPicker': {'replaceBuiltInOptions': True, 'options': options}}
    token = secrets.token_urlsafe(32)
    base = f'http://127.0.0.1:{args.port}'
    env = dict(os.environ, CC_MISTRAL_LOCAL_TOKEN=token, CC_MISTRAL_CATALOG=json.dumps(catalog))
    command = [sys.executable, '-m', 'ccmistral', 'serve', '--model', args.model,
               '--port', str(args.port)]
    if args.thinking is not None:
        command.extend(['--thinking', args.thinking])
    process = subprocess.Popen(command, env=env)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            if process.poll() is not None:
                sys.exit('Gateway exited before startup; check the port and dependencies.')
            try:
                request = urllib.request.Request(base + '/health', headers={'x-api-key': token})
                with opener.open(request, timeout=0.2) as result:
                    if result.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        else:
            sys.exit('Gateway did not become ready.')
        # Scope provider overrides to this child. No persistent Claude settings change.
        child = {k: v for k, v in os.environ.items()
                 if not k.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')) and k not in {
                     'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
                     'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_API_KEY_HELPER_TTL_MS'}}
        child.update(ANTHROPIC_BASE_URL=base, ANTHROPIC_AUTH_TOKEN=token,
                     ANTHROPIC_MODEL=initial, ANTHROPIC_DEFAULT_OPUS_MODEL=initial,
                     ANTHROPIC_DEFAULT_SONNET_MODEL=initial, ANTHROPIC_DEFAULT_HAIKU_MODEL=initial,
                     ANTHROPIC_DEFAULT_FABLE_MODEL=initial,
                     CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY='1',
                     CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        child.update(context_environment(catalog, args.model))
        # A pre-existing 1M opt-out would negate the catalog-derived suffix.
        child.pop('CLAUDE_CODE_DISABLE_1M_CONTEXT', None)
        extra = args.claude_args
        if extra[:1] == ['--']:
            extra = extra[1:]
        print(f'Routing Claude Code to Mistral model {args.model}'
              + (f' · thinking: {args.thinking}' if args.thinking else '')
              + '. Billing allocation is not yet verified.', flush=True)
        print('Use /model to switch models and /effort to change thinking level.', flush=True)
        launch = ['claude', '--permission-mode', 'bypassPermissions',
                  '--settings', json.dumps(settings)]
        if args.thinking is not None:
            launch.extend(['--effort', args.thinking])
        sys.exit(subprocess.call([*launch, *extra], env=child))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == '__main__':
    main()
