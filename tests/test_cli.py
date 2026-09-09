import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from minieval.__main__ import main
from minieval.sandbox import DEFAULT_IMAGE, DEFAULT_MODAL_IMAGE
from minieval.types import Task


class CliTests(unittest.TestCase):
    def test_run_uses_vercel_without_a_backend_flag(self):
        with patch('minieval.__main__.env_loader.load_env'), \
             patch.dict('os.environ', {'VERCEL_TOKEN': 'test', 'VERCEL_PROJECT_ID': 'project'}, clear=True), \
             patch('minieval.__main__.discover_tasks', return_value=[Task('task', '', 'prompt')]), \
             patch('minieval.__main__.discover_agent'), \
             patch('minieval.__main__.run_suite') as run_suite, redirect_stdout(io.StringIO()):
            main(['run', '--model', 'model'])
        self.assertEqual(run_suite.call_args.kwargs['sandbox_kwargs'], {'backend': 'vercel', 'image': DEFAULT_IMAGE})
        self.assertNotIn('backend', run_suite.call_args.kwargs)

    def test_missing_vercel_credentials_fail_before_creating_a_suite(self):
        errors = io.StringIO()
        with patch('minieval.__main__.env_loader.load_env'), \
             patch.dict('os.environ', {}, clear=True), \
             patch('minieval.__main__.discover_tasks', return_value=[Task('task', '', 'prompt')]), \
             patch('minieval.__main__.discover_agent'), \
             patch('minieval.__main__.run_suite') as run_suite, redirect_stderr(errors), \
             self.assertRaises(SystemExit) as exited:
            main(['run', '--model', 'model'])
        self.assertEqual(exited.exception.code, 2)
        self.assertIn('VERCEL_TOKEN, VERCEL_PROJECT_ID', errors.getvalue())
        run_suite.assert_not_called()

    def test_modal_selection_and_image_defaults(self):
        for environment, flags, expected_image in (
            ({}, ['--sandbox', 'modal'], DEFAULT_MODAL_IMAGE),
            ({'MINIEVAL_SANDBOX': 'modal'}, [], DEFAULT_MODAL_IMAGE),
            ({'MINIEVAL_SANDBOX': 'vercel'}, ['--sandbox', 'modal', '--image', 'custom:tag'], 'custom:tag'),
        ):
            with self.subTest(flags=flags), patch('minieval.__main__.env_loader.load_env'), \
                 patch.dict('os.environ', environment, clear=True), \
                 patch('minieval.__main__.discover_tasks', return_value=[Task('task', '', 'prompt')]), \
                 patch('minieval.__main__.discover_agent'), \
                 patch('minieval.__main__.load_modal') as sdk, \
                 patch('minieval.__main__.run_suite') as run_suite, redirect_stdout(io.StringIO()):
                main(['run', '--model', 'model', *flags])
                sdk.return_value.Client.from_env.assert_called_once()
                self.assertEqual(run_suite.call_args.kwargs['sandbox_kwargs'],
                                 {'backend': 'modal', 'image': expected_image})

    def test_modal_auth_failure_does_not_create_suite(self):
        with patch('minieval.__main__.env_loader.load_env'), \
             patch('minieval.__main__.discover_tasks', return_value=[Task('task', '', 'prompt')]), \
             patch('minieval.__main__.discover_agent'), \
             patch('minieval.__main__.load_modal') as sdk, \
             patch('minieval.__main__.run_suite') as run_suite, redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit):
            sdk.return_value.Client.from_env.side_effect = ValueError('missing token')
            main(['run', '--sandbox', 'modal', '--model', 'model'])
        run_suite.assert_not_called()
