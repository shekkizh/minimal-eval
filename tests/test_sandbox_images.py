import unittest
from unittest.mock import patch

from minieval.sandbox import DEFAULT_IMAGE, VercelSandbox
from minieval.types import CommandResult


class SandboxImageTests(unittest.TestCase):
    def test_vercel_creates_managed_image_via_v3_and_prepares_workspace(self):
        with patch.object(VercelSandbox, '_request', return_value={'session': {'id': 'test-session'}}) as request, \
             patch.object(VercelSandbox, 'exec', return_value=CommandResult(0, '', '')) as execute:
            sandbox = VercelSandbox(token='test', project_id='project', timeout_ms=9000)
        request.assert_called_once_with('POST', '/v3/sandboxes', json_body={
            'image': DEFAULT_IMAGE, 'timeout': 9000, 'persistent': False, 'projectId': 'project'})
        execute.assert_called_once_with(['mkdir', '-p', '/workspace'], cwd='/')
        self.assertEqual(sandbox.image, DEFAULT_IMAGE)
        self.assertEqual(sandbox.workdir, '/workspace')

    def test_vercel_preserves_explicit_digest(self):
        image = 'vcr.vercel.com/team/project/image@sha256:' + 'a' * 64
        with patch.object(VercelSandbox, '_request', return_value={'session': {'id': 'test-session'}}) as request, \
             patch.object(VercelSandbox, 'exec', return_value=CommandResult(0, '', '')):
            VercelSandbox(image=image, token='test')
        self.assertEqual(request.call_args.kwargs['json_body']['image'], image)

    def test_workspace_failure_stops_created_vercel_sandbox(self):
        with patch.object(VercelSandbox, '_request', return_value={'session': {'id': 'test-session'}}), \
             patch.object(VercelSandbox, 'exec', return_value=CommandResult(1, '', 'denied')), \
             patch.object(VercelSandbox, 'stop') as stop, \
             self.assertRaisesRegex(RuntimeError, 'workspace setup failed'):
            VercelSandbox(token='test')
        stop.assert_called_once()

    def test_vercel_commands_use_api_sudo(self):
        sandbox = object.__new__(VercelSandbox)
        sandbox._session_id = 'session'
        sandbox.workdir = '/workspace'
        with patch.object(VercelSandbox, '_request', side_effect=[
            {'command': {'id': 'command'}}, {'command': {'exitCode': 0}},
            b'{"stream":"stdout","data":"ok"}\n',
        ]) as request:
            result = sandbox.exec(['node', '--version'])
        body = request.call_args_list[0].kwargs['json_body']
        self.assertTrue(body['sudo'])
        self.assertEqual(body['command'], 'node')
        self.assertEqual(body['args'], ['--version'])
        self.assertEqual(result.stdout, 'ok')
