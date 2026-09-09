import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from minieval.dependencies import install_dependencies, load_dependencies
from minieval.types import CommandResult


class DependencyTests(unittest.TestCase):
    def test_manifest_is_optional_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dependencies.json'
            self.assertEqual(load_dependencies(path), {})
            for invalid in ('[]', '{"pip": []}', '{"system": []}',
                            '{"system": {"brew": []}}', '{"npm": "foo"}',
                            '{"npm": ["--unsafe-option"]}', '{"npm": [null]}'):
                path.write_text(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    load_dependencies(path)
            path.write_text('{"system": {"apt": ["ripgrep"]}, "npm": ["example@1.2.3"]}')
            self.assertEqual(load_dependencies(path)['npm'], ['example@1.2.3'])

    def test_installs_for_actual_manager_and_preserves_package_arguments(self):
        for manager in ('apt', 'dnf'):
            with self.subTest(manager=manager), tempfile.TemporaryDirectory() as tmp:
                sandbox = Mock()
                sandbox.exec.side_effect = lambda cmd, **kwargs: CommandResult(
                    0, manager if 'echo apt' in ' '.join(cmd) else 'installed', '')
                install_dependencies(sandbox, {'system': {manager: ['ripgrep']},
                                               'npm': ['@scope/cli@1.2.3']}, Path(tmp) / 'log')
                commands = [call.args[0] for call in sandbox.exec.call_args_list]
                self.assertTrue(any('ripgrep' in cmd for cmd in commands))
                self.assertFalse(any('install' in cmd and 'python3' in cmd for cmd in commands))
                self.assertEqual(commands[-1], ['npm', 'install', '--global', '--prefix',
                                                '/usr/local', '--', '@scope/cli@1.2.3'])
                self.assertIn('installed', (Path(tmp) / 'log').read_text())
                self.assertTrue(all(call.kwargs['timeout'] <= 300 for call in sandbox.exec.call_args_list))

    def test_missing_manager_mapping_fails_before_install(self):
        sandbox = Mock()
        sandbox.exec.return_value = CommandResult(0, 'dnf', '')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(RuntimeError, 'no system package list for dnf'):
            install_dependencies(sandbox, {'system': {'apt': ['bfs']}}, Path(tmp) / 'log')
        self.assertEqual(sandbox.exec.call_count, 2)

    def test_failed_install_preserves_output_and_stops(self):
        sandbox = Mock()
        sandbox.exec.side_effect = [CommandResult(0, '', ''), CommandResult(0, 'apt', ''), CommandResult(0, '', ''),
                                    CommandResult(100, '', 'package missing')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'log'
            with self.assertRaisesRegex(RuntimeError, 'exit 100'):
                install_dependencies(sandbox, {'system': {'apt': ['missing']}}, path)
            self.assertIn('package missing', path.read_text())
        self.assertEqual(sandbox.exec.call_count, 4)

    def test_setup_uses_one_total_deadline(self):
        sandbox = Mock()
        sandbox.exec.return_value = CommandResult(0, 'apt', '')
        with tempfile.TemporaryDirectory() as tmp, \
             patch('minieval.dependencies.time.monotonic', side_effect=[0, 1, 301]), \
             self.assertRaisesRegex(RuntimeError, 'timeout'):
            install_dependencies(sandbox, {'npm': ['example@1']}, Path(tmp) / 'log')
        self.assertEqual(sandbox.exec.call_count, 1)

    def test_no_dependencies_only_checks_preinstalled_baseline(self):
        sandbox = Mock()
        sandbox.exec.return_value = CommandResult(0, 'Node and Python ready', '')
        with tempfile.TemporaryDirectory() as tmp:
            install_dependencies(sandbox, {}, Path(tmp) / 'log')
        sandbox.exec.assert_called_once()
        self.assertIn('command -v', sandbox.exec.call_args.args[0][-1])

    def test_missing_baseline_fails_without_installing_anything(self):
        sandbox = Mock()
        sandbox.exec.return_value = CommandResult(127, '', 'node: not found')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(RuntimeError, 'exit 127'):
            install_dependencies(sandbox, {'npm': ['example@1']}, Path(tmp) / 'log')
        sandbox.exec.assert_called_once()
