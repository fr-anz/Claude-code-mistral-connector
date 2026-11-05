import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ccmistral import auth


class PlanAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_flow_provisions_and_stores_without_printing_credential(self):
        instance = Mock()
        attempt = SimpleNamespace(sign_in_url='https://console.mistral.ai/sign-in/test')
        instance.start_attempt = AsyncMock(return_value=attempt)
        instance.complete_attempt = AsyncMock(return_value='private-plan-credential')
        instance.aclose = AsyncMock()
        stored = Mock()
        async def store_in_thread(function, value):
            function(value)
        output = io.StringIO()
        with patch('vibe.setup.auth.browser_sign_in.BrowserSignInService', return_value=instance), patch(
            'vibe.setup.auth.http_browser_sign_in_gateway.HttpBrowserSignInGateway') as gateway, patch(
            'ccmistral.auth._save_credential', stored), patch('asyncio.to_thread', side_effect=store_in_thread), patch(
            'webbrowser.open') as browser, patch('sys.stdout', output):
            await auth.login(open_browser=False)
        stored.assert_called_once_with('private-plan-credential')
        self.assertNotIn('private-plan-credential', output.getvalue())
        self.assertIn(attempt.sign_in_url, output.getvalue())
        browser.assert_not_called()
        instance.complete_attempt.assert_awaited_once_with(attempt)
        instance.aclose.assert_awaited_once()
        self.assertEqual(gateway.call_args.kwargs['api_base_url'], 'https://console.mistral.ai/api')

    async def test_missing_plan_credential_never_uses_environment_or_oidc(self):
        with patch('ccmistral.auth._load_credential', return_value=None), patch.dict(os.environ, {
            'MISTRAL_API_KEY': 'developer-key-must-not-be-used', 'VIBE_CLI_OIDC_ENABLED': '1'}):
            self.assertFalse(auth.signed_in())
            with self.assertRaisesRegex(RuntimeError, 'Vibe browser sign-in required'):
                await auth.headers('https://api.mistral.ai/v1/chat/completions')

    async def test_credential_is_not_loaded_for_untrusted_destinations(self):
        with patch('ccmistral.auth._load_credential') as load:
            for url in ('http://api.mistral.ai/v1/models', 'https://evil.example/v1/models',
                        'https://api.mistral.ai.evil.example/v1/models', 'https://user@api.mistral.ai/v1/models'):
                with self.assertRaises(RuntimeError):
                    await auth.headers(url)
            load.assert_not_called()

    async def test_keyring_failure_aborts_login_without_plaintext_fallback(self):
        instance = Mock()
        instance.start_attempt = AsyncMock(return_value=SimpleNamespace(sign_in_url='https://console.mistral.ai/sign-in/test'))
        instance.complete_attempt = AsyncMock(return_value='private-plan-credential')
        instance.aclose = AsyncMock()
        with patch('vibe.setup.auth.browser_sign_in.BrowserSignInService', return_value=instance), patch(
            'vibe.setup.auth.http_browser_sign_in_gateway.HttpBrowserSignInGateway'), patch(
            'asyncio.to_thread', new=AsyncMock(side_effect=RuntimeError('keyring unavailable'))), patch('sys.stdout', io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'keyring unavailable'):
                await auth.login(open_browser=False)
        instance.aclose.assert_awaited_once()
