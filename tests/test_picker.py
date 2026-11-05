import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ccmistral import picker


class PickerTests(unittest.TestCase):
    def test_catalog_filters_non_chat_and_non_tool_models(self):
        self.assertEqual(picker.choices([
            {'id': 'coding', 'name': 'Coding Model'},
            {'id': 'coding'},
            {'id': 'embed', 'capabilities': {'completion_chat': False}},
            {'id': 'no-tools', 'capabilities': {'function_calling': False}},
            {'id': None},
        ]), [('coding', 'Coding Model')])

    def test_picker_restores_then_remembers_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings' / 'selection.json'
            with patch.object(picker, 'selection_path', return_value=path):
                picker.remember('old')
                with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), patch.object(
                    picker.curses, 'wrapper', return_value='new') as screen:
                    self.assertEqual(picker.pick([{'id': 'new'}, {'id': 'old'}]), 'new')
                self.assertEqual(screen.call_args.args[2], 1)
                self.assertEqual(picker.remembered_model(), 'new')
                self.assertEqual(json.loads(path.read_text()), {'model': 'new'})

    def test_cancel_does_not_launch_or_change_selection(self):
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), patch.object(
            picker, 'remembered_model', return_value=None), patch.object(picker.curses, 'wrapper', return_value=None), patch.object(
            picker, 'remember') as remember:
            with self.assertRaises(SystemExit) as stopped:
                picker.pick([{'id': 'model'}])
            self.assertEqual(stopped.exception.code, 0)
            remember.assert_not_called()

    def test_bare_command_dispatches_to_run_with_picker(self):
        from ccmistral.__main__ import main
        with patch('sys.argv', ['claude-mistral']), patch('ccmistral.__main__.run') as run:
            main()
        self.assertIsNone(run.call_args.args[0].model)
        self.assertEqual(run.call_args.args[0].command, 'run')


if __name__ == '__main__':
    unittest.main()
