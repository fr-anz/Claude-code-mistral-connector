"""Use Vibe's OIDC keyring session; never resolve API keys."""
import os

os.environ['VIBE_CLI_OIDC_ENABLED'] = '1'


def service():
    from vibe.setup.auth._oidc_service import OidcService
    return OidcService.from_env()


def signed_in():
    from vibe.setup.auth._oidc_runtime import stored_oidc_credential
    return stored_oidc_credential() is not None


async def headers(url):
    from vibe.setup.auth._oidc_runtime import oidc_headers
    result = await oidc_headers(url)
    result['user-agent'] = 'CC-Mistral/0.1'
    return result


async def login(open_browser=True):
    import asyncio
    import webbrowser
    instance = service()
    attempt = await instance.start()
    announced = False
    try:
        while attempt.status in ('waiting', 'exchanging'):
            if attempt.authorization_url and not announced:
                print('Sign in with Mistral in your browser:\n' + attempt.authorization_url, flush=True)
                if open_browser:
                    webbrowser.open(attempt.authorization_url)
                announced = True
            await asyncio.sleep(0.2)
        if attempt.status != 'completed':
            raise RuntimeError(attempt.detail or 'Mistral sign-in failed')
        print('Signed in. Credentials are stored by Vibe in your operating system keyring.')
    finally:
        await instance.cancel()
