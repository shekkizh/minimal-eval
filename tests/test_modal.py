import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from minieval.runner import run_suite
from minieval.sandbox import DEFAULT_MODAL_IMAGE, ModalSandbox, create_sandbox, load_modal
from minieval.types import CommandResult


class ExecTimeoutError(Exception):
    pass


class ModalTests(unittest.TestCase):
    def setUp(self):
        self.remote = Mock(object_id='sb-test')
        self.sdk = Mock()
        self.sdk.exception.ExecTimeoutError = ExecTimeoutError
        self.sdk.Sandbox.create.return_value = self.remote
        self.process = Mock(stdout=['out'], stderr=['err'])
        self.process.wait.return_value = 0
        self.remote.exec.return_value = self.process

    def sandbox(self, **kwargs):
        with patch('minieval.sandbox.load_modal', return_value=self.sdk):
            return ModalSandbox(**kwargs)

    def test_default_image_and_resources(self):
        sandbox = self.sandbox(timeout_s=19.2)
        self.assertEqual(sandbox.sandbox_id, 'sb-test')
        self.sdk.Image.from_registry.assert_called_once_with(DEFAULT_MODAL_IMAGE)
        self.sdk.Image.from_registry.return_value.apt_install.assert_called_once()
        options = self.sdk.Sandbox.create.call_args.kwargs
        self.assertEqual((options['timeout'], options['cpu'], options['memory']), (20, 1.0, 2048))
        self.assertNotIn('secrets', options)
        sandbox.stop()
        self.remote.terminate.assert_called_once_with(wait=True)

    def test_custom_image(self):
        self.sandbox(image='custom@sha256:abc')
        self.sdk.Image.from_registry.assert_called_once_with('custom@sha256:abc')
        self.sdk.Image.from_registry.return_value.apt_install.assert_not_called()

    def test_setup_failure_terminates_sandbox(self):
        self.process.wait.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'workspace setup failed'):
            self.sandbox()
        self.remote.terminate.assert_called_once_with(wait=True)

    def test_execution_and_timeout(self):
        sandbox = self.sandbox()
        self.process.wait.return_value = 7
        result = sandbox.exec(['sh', '-c', 'exit 7'], cwd='/tmp', env={'X': 'value'}, timeout=2.1)
        self.assertEqual(result, CommandResult(7, 'out', 'err'))
        self.remote.exec.assert_called_with('sh', '-c', 'exit 7', workdir='/tmp', env={'X': 'value'}, timeout=3)
        for failure in (-1, ExecTimeoutError()):
            if isinstance(failure, Exception):
                self.process.wait.side_effect = failure
            else:
                self.process.wait.return_value = failure
            result = sandbox.exec(['sleep', '10'], timeout=1)
            self.assertEqual(result.exit_code, 124)
            self.assertEqual(result.stdout, 'out')
            self.assertIn('timeout after 1s', result.stderr)

    def test_file_upload_preserves_binary_and_absolute_path_contract(self):
        sandbox = self.sandbox()
        sandbox.write_files({'nested/text': 'café', 'binary': b'\x00\xff'})
        sandbox.write_files({'/verifier/verifier.py': 'hidden'}, extract_dir='/')
        write = self.remote.filesystem.write_bytes
        self.assertEqual(write.call_count, 3)
        write.assert_any_call('café'.encode(), '/workspace/nested/text')
        write.assert_any_call(b'\x00\xff', '/workspace/binary')
        write.assert_any_call(b'hidden', '/verifier/verifier.py')
        with self.assertRaises(ValueError):
            sandbox.write_files({'../escape': 'no'})

    def test_factory_reserves_grading_time(self):
        with patch('minieval.sandbox.ModalSandbox') as sandbox:
            create_sandbox(10, backend='modal', image='custom')
        sandbox.assert_called_once_with(timeout_s=190, image='custom')
        with self.assertRaises(ValueError):
            create_sandbox(backend='unknown')

    def test_optional_dependency_error(self):
        with patch('minieval.sandbox.importlib.import_module',
                   side_effect=ModuleNotFoundError("missing", name='modal')):
            with self.assertRaisesRegex(ValueError, 'optional SDK'):
                load_modal()

    def test_summary_records_modal(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            suite = run_suite([], [], 1, 10, Path(tmp), sandbox_kwargs={'backend': 'modal'})
            summary = json.loads((suite / 'summary.json').read_text())
            self.assertEqual(summary['backend'], 'modal')
            self.assertEqual(summary['image'], DEFAULT_MODAL_IMAGE)


@unittest.skipUnless(os.environ.get('TEST_MODAL') == '1', 'set TEST_MODAL=1 for live Modal checks')
class ModalLiveTests(unittest.TestCase):
    def test_files_commands_timeout_and_export(self):
        sandbox = ModalSandbox(timeout_s=120)
        try:
            sandbox.write_files({'nested/text': 'café', 'binary': b'\x00\xff'})
            self.assertEqual(sandbox.exec(['cat', 'nested/text']).stdout, 'café')
            result = sandbox.exec(['sh', '-c', 'printf "$CHECK"; printf err >&2; exit 7'], env={'CHECK': 'out'})
            self.assertEqual(result, CommandResult(7, 'out', 'err'))
            result = sandbox.exec(['sh', '-c', 'echo started; sleep 5; touch /workspace/too-late'], timeout=1)
            self.assertEqual(result.exit_code, 124, result)
            self.assertIn('started', result.stdout)
            sandbox.exec(['sleep', '5'])
            self.assertFalse(sandbox.exec(['test', '-e', 'too-late']).ok)
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'workspace.tar.gz'
                sandbox.export_directory('/workspace', archive)
                import tarfile
                with tarfile.open(archive) as tar:
                    self.assertEqual(tar.extractfile('./binary').read(), b'\x00\xff')
        finally:
            sandbox.stop()
