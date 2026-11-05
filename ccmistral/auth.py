"""Vibe browser-provisioned credentials, stored only in the OS keyring."""
from urllib.parse import urlsplit

_KEYRING_SERVICE = 'ai.claude-mistral.vibe-plan'
_KEYRING_ENTRY = 'browser-provisioned:v1'


def _load_credential():
    import keyring
    return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ENTRY)


def _save_credential(credential):
    import keyring
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_ENTRY, credential)


def signed_in():
    try:
        return bool(_load_credential())
    except Exception:
        return False


async def headers(url):
    # Plan credentials must never reach an arbitrary URL or redirect target.
    destination = urlsplit(url)
    if destination.scheme != 'https' or destination.netloc != 'api.mistral.ai':
        raise RuntimeError('Vibe plan credentials can only be sent to api.mistral.ai over HTTPS.')
    from vibe.utils.http import get_user_agent
    credential = _load_credential()
    if not credential:
        raise RuntimeError('Vibe browser sign-in required. Run claude-mistral login.')
    return {'Authorization': f'Bearer {credential}',
            'user-agent': get_user_agent('mistral') + ' CC-Mistral/0.1'}


async def login(open_browser=True):
    import asyncio
    import webbrowser
    from vibe.core.config._defaults import (
        DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
        DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    )
    from vibe.setup.auth.browser_sign_in import BrowserSignInService
    from vibe.setup.auth.http_browser_sign_in_gateway import HttpBrowserSignInGateway

    gateway = HttpBrowserSignInGateway(
        browser_base_url=DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
        api_base_url=DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    )
    instance = BrowserSignInService(gateway)
    try:
        async with asyncio.timeout(300):
            attempt = await instance.start_attempt()
            print('Sign in to provision a Vibe plan credential in your browser:\n' + attempt.sign_in_url, flush=True)
            if open_browser:
                webbrowser.open(attempt.sign_in_url)
            credential = await instance.complete_attempt(attempt)
            if not isinstance(credential, str) or not credential:
                raise RuntimeError('Vibe browser sign-in returned no credential.')
            await asyncio.to_thread(_save_credential, credential)
        print('Vibe browser credential stored in the operating system keyring. No key needs to be pasted.')
        print('Billing allocation still needs to be checked against your Vibe Code usage counter.')
    finally:
        await instance.aclose()
