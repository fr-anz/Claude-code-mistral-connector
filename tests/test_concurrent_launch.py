import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import AsyncMock, Mock, patch

from ccmistral.__main__ import main, run, start_gateway


class ConcurrentLaunchTests(unittest.TestCase):
    def test_run_defaults_to_auto_port_and_preserves_print_arguments(self):
        for arguments, port in [(['run', '--', '-p', 'Review', '--output-format', 'json'], 0),
                                (['run', '--port', '3457', '--', '-p', 'Review', '--output-format', 'json'], 3457)]:
            with patch('sys.argv', ['claude-mistral', *arguments]), patch('ccmistral.__main__.run') as launch:
                main()
            self.assertEqual(launch.call_args.args[0].port, port)
            self.assertEqual(launch.call_args.args[0].claude_args, ['--', '-p', 'Review', '--output-format', 'json'])

    def test_worker_gets_own_environment_clean_stdout_and_cleanup(self):
        args = argparse.Namespace(model=None, thinking=None, port=0,
                                  claude_args=['--', '-p', 'Review', '--output-format', 'json'])
        process, opener = Mock(), Mock()
        process.poll.return_value = None
        opener.open.return_value.__enter__ = Mock(return_value=Mock(status=200))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        stdout = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'CLAUDECODE': '1', 'ANTHROPIC_AUTH_TOKEN': 'parent-only',
                                                        'ANTHROPIC_BASE_URL': 'http://parent'}))
            stack.enter_context(patch('ccmistral.auth.signed_in', return_value=True))
            stack.enter_context(patch('ccmistral.__main__.shutil.which', return_value='/bin/claude'))
            stack.enter_context(patch('ccmistral.__main__.models', new=AsyncMock(return_value=[{'id': 'worker-model'}])))
            stack.enter_context(patch('ccmistral.picker._selection', return_value={}))
            start = stack.enter_context(patch('ccmistral.__main__.start_gateway', return_value=(process, 'http://127.0.0.1:45678')))
            stack.enter_context(patch('ccmistral.__main__.urllib.request.build_opener', return_value=opener))
            launch = stack.enter_context(patch('ccmistral.__main__.subprocess.call', return_value=7))
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(io.StringIO()))
            with self.assertRaises(SystemExit) as stopped:
                run(args)
            self.assertEqual(stopped.exception.code, 7)
            child = launch.call_args.kwargs['env']
            self.assertNotIn('CLAUDECODE', child)
            self.assertEqual(child['ANTHROPIC_BASE_URL'], 'http://127.0.0.1:45678')
            self.assertEqual(child['ANTHROPIC_AUTH_TOKEN'], start.call_args.args[3]['CC_MISTRAL_LOCAL_TOKEN'])
            self.assertNotEqual(child['ANTHROPIC_AUTH_TOKEN'], 'parent-only')
            self.assertEqual(os.environ['ANTHROPIC_BASE_URL'], 'http://parent')
            self.assertEqual(os.environ['CLAUDECODE'], '1')
            self.assertEqual(launch.call_args.args[0][-4:], ['-p', 'Review', '--output-format', 'json'])
        self.assertEqual(stdout.getvalue(), '')
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_two_live_gateways_have_distinct_ports_and_auth(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with ExitStack() as stack:
            gateways = []
            for token in ('worker-one', 'worker-two'):
                env = dict(os.environ, CC_MISTRAL_LOCAL_TOKEN=token,
                           CC_MISTRAL_CATALOG=json.dumps([{'id': 'worker-model'}]))
                process, base = start_gateway('worker-model', 0, None, env)
                stack.callback(self.stop, process)
                gateways.append((process, base, token))
            self.assertNotEqual(gateways[0][1], gateways[1][1])
            for process, base, token in gateways:
                deadline = time.monotonic() + 10
                while True:
                    self.assertIsNone(process.poll(), 'Gateway exited before becoming ready')
                    try:
                        request = urllib.request.Request(base + '/health', headers={'x-api-key': token})
                        with opener.open(request, timeout=0.2) as reply:
                            self.assertEqual(json.load(reply)['model'], 'worker-model')
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail('Gateway did not become ready')
                        time.sleep(0.05)
                other_token = 'worker-two' if token == 'worker-one' else 'worker-one'
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    opener.open(urllib.request.Request(base + '/health', headers={'x-api-key': other_token}), timeout=1)
                self.assertEqual(rejected.exception.code, 401)
                rejected.exception.close()
            self.stop(gateways[0][0])
            # One worker exiting must leave the other gateway usable.
            with opener.open(urllib.request.Request(gateways[1][1] + '/health',
                             headers={'x-api-key': 'worker-two'}), timeout=1) as reply:
                self.assertEqual(reply.status, 200)

    def test_fixed_port_conflict_does_not_spawn_child(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(('127.0.0.1', 0))
            reserved.listen()
            with patch('ccmistral.__main__.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'Omit --port'):
                    start_gateway('worker-model', reserved.getsockname()[1], None, {})
            spawn.assert_not_called()

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
