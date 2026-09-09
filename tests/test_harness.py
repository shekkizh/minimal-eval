import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from minieval.agents import Agent, ScriptAgent
from minieval.runner import _slug, _totals, run_attempt, run_suite
from minieval.sandbox import Sandbox, create_sandbox
from minieval.task import load_task
from minieval.types import CommandResult, RunResult, Task


class LocalSandbox(Sandbox):
    def __init__(self, root):
        self.workdir = str(root / 'workspace')
        Path(self.workdir).mkdir()
        self.stopped = False

    @property
    def sandbox_id(self):
        return 'local-test'

    def exec(self, cmd, cwd=None, env=None, timeout=None):
        import os
        proc = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})},
                              timeout=timeout, capture_output=True, text=True)
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def write_files(self, files, extract_dir=None):
        for name, content in files.items():
            path = Path(extract_dir or self.workdir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode() if isinstance(content, str) else content)

    def run_agent(self, cmd, env, timeout):
        return self.exec(cmd, cwd=self.workdir, env=env, timeout=timeout)

    def run_verifier(self, directory):
        return self.exec(['python3', f'{directory}/verifier.py'], cwd=self.workdir, timeout=120)

    def stop(self):
        self.stopped = True


class HarnessTests(unittest.TestCase):
    def test_infrastructure_failures_do_not_count_as_task_failures(self):
        infra = RunResult('task', 'agent', 'model', 1, False, failure_type='infra')
        passed = RunResult('task', 'agent', 'model', 2, True)
        totals = _totals([infra, passed])
        self.assertEqual(totals['total'], 2)
        self.assertEqual(totals['evaluated'], 1)
        self.assertEqual(totals['pass_rate'], 1.0)
        self.assertEqual(totals['by_failure_type']['infra'], 1)
        self.assertIsNone(_totals([infra])['pass_rate'])

    def setUp(self):
        # Unit sandboxes do not provision a real Linux runtime.
        installer = patch('minieval.runner.install_dependencies')
        self.install_dependencies = installer.start()
        self.addCleanup(installer.stop)

    def test_dependency_failure_skips_agent_and_verifier(self):
        agent = Mock(spec=Agent)
        agent.name, agent.model = 'agent', 'model'
        agent.dependencies = {'npm': ['example@1.0.0']}
        sandbox = Mock(workdir='/workspace')
        self.install_dependencies.side_effect = RuntimeError('package unavailable')
        with tempfile.TemporaryDirectory() as tmp, patch('minieval.runner.create_sandbox', return_value=sandbox):
            result = run_attempt(Task('task', '', 'prompt', verifier='pass'), agent, 1,
                                 10, Path(tmp))
            self.assertEqual(result.failure_type, 'infra')
            self.assertEqual(result.duration, 0)
            self.assertIsNone(result.agent_exit_code)
            agent.setup.assert_not_called()
            sandbox.run_agent.assert_not_called()
            sandbox.run_verifier.assert_not_called()
            sandbox.stop.assert_called_once()
            self.install_dependencies.assert_called_once_with(
                sandbox, {'npm': ['example@1.0.0']}, Path(tmp) / 'setup_output.txt')

    def test_suite_installs_and_records_each_agents_own_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A root manifest must not become a fallback for agents without one.
            (root / 'dependencies.json').write_text('{"npm": ["unrelated"]}')
            agents = []
            expected = [{}, {'system': {'apt': ['ripgrep']}}]
            for name, dependencies in zip(('minimal', 'extra'), expected):
                source = root / 'agents' / name
                source.mkdir(parents=True)
                (source / 'run.sh').write_text('exit 0')
                if dependencies:
                    (source / 'dependencies.json').write_text(json.dumps(dependencies))
                agents.append(ScriptAgent(name, source, 'model'))
            sandbox = Mock(workdir='/workspace')
            sandbox.exec.return_value = CommandResult(0, '', '')
            sandbox.run_agent.return_value = CommandResult(0, '', '')
            sandbox.run_verifier.return_value = CommandResult(0, '', '')
            with patch('minieval.runner.create_sandbox', return_value=sandbox):
                suite = run_suite([Task('task', '', 'prompt', verifier='pass')], agents,
                                  1, 10, root / 'results')
            self.assertEqual([call.args[1] for call in self.install_dependencies.call_args_list], expected)
            for agent, dependencies in zip(agents, expected):
                saved = suite / 'task' / agent.name / 'model' / 'run-1' / 'dependencies.json'
                self.assertEqual(json.loads(saved.read_text()), dependencies)
            self.assertFalse((suite / 'dependencies.json').exists())

    def test_task_json_metadata_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'PROMPT.md').write_text('Fix it')
            (root / 'verifier.py').write_text('pass')
            (root / 'task.json').write_text(
                '{"description": "Fix it", "tags": ["bugfix", "python"], "enabled": true}'
            )
            self.assertEqual(load_task(root).metadata, {
                'description': 'Fix it',
                'tags': ['bugfix', 'python'],
                'enabled': True,
            })

    def test_hidden_directories_and_caches_are_not_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'PROMPT.md').write_text('Fix it')
            (root / 'verifier.py').write_text('pass')
            for name in ('main.py', '.reference.py', '.hidden/answer.py', '__pycache__/x.pyc'):
                path = root / 'environment' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('secret' if name != 'main.py' else 'starter')
            self.assertEqual(load_task(root).environment_files, {'main.py': 'starter'})

    def test_launch_script_receives_literal_prompt_model_and_generic_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            (source / 'run.sh').write_text('printf "%s\\n%s\\n%s\\n%s" "$1" "$2" "$API_KEY" "$BASE_URL"')
            (source / 'tool.bin').write_bytes(bytes(range(256)))
            (source / 'dependencies.json').write_text('{"system": {"apt": ["ripgrep"]}}')
            sandbox = LocalSandbox(root)
            prompt = 'Fix "this"\n$(exit 1); `exit 2`'
            with patch('minieval.agents.AGENT_INSTALL_DIR', str(root / 'installed')), \
                 patch.dict('os.environ', {'API_KEY': 'test-key', 'BASE_URL': 'https://example.test'}, clear=True):
                agent = ScriptAgent('test', source, 'provider/model')
                self.assertEqual(agent.dependencies, {'system': {'apt': ['ripgrep']}})
                agent.setup(sandbox)
                self.assertFalse((root / 'installed/dependencies.json').exists())
                result = sandbox.exec(agent.command(prompt), cwd=sandbox.workdir, env=agent.env())
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout, prompt + '\nprovider/model\ntest-key\nhttps://example.test')
            self.assertEqual((root / 'installed/tool.bin').read_bytes(), bytes(range(256)))

    def test_sandbox_preserves_image_and_reserves_grading_time(self):
        with patch('minieval.sandbox.VercelSandbox') as sandbox:
            create_sandbox(timeout_s=10, image='vcr.vercel.com/team/project/image@sha256:abc')
            sandbox.assert_called_once_with(timeout_ms=190000, image='vcr.vercel.com/team/project/image@sha256:abc')

    def test_cleanup_failure_still_saves_artifacts(self):
        agent = Mock(spec=Agent, name='agent')
        agent.name, agent.model = 'agent', 'model'
        agent.dependencies = {}
        agent.env.return_value = {}
        sandbox = Mock(workdir='/workspace')
        sandbox.exec.return_value = CommandResult(0, 'finished', '')
        sandbox.run_agent.return_value = CommandResult(0, 'finished', '')
        sandbox.run_verifier.return_value = CommandResult(0, '', '')
        sandbox.stop.side_effect = RuntimeError('stop failed')
        with tempfile.TemporaryDirectory() as tmp, patch('minieval.runner.create_sandbox', return_value=sandbox):
            result = run_attempt(Task('task', '', 'prompt', verifier='pass'), agent, 1, 10, Path(tmp))
            self.assertEqual(result.failure_type, 'infra')
            self.assertFalse(result.passed)
            self.assertIn('cleanup failed', result.error)
            self.assertTrue((Path(tmp) / 'result.json').is_file())
            self.assertEqual((Path(tmp) / 'agent_output.txt').read_text(), 'finished')

    def test_agent_runs_once_and_native_artifacts_survive_cleanup(self):
        for exit_code, verifier_exit in ((0, 0), (1, 0), (0, 1), (124, None)):
            with self.subTest(exit_code=exit_code, verifier_exit=verifier_exit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sandbox = LocalSandbox(root)
                agent = Mock(spec=Agent)
                agent.name, agent.model = 'agent', 'model'
                agent.dependencies = {}
                agent.env.return_value = {}
                agent.protect.side_effect = lambda _: (root / 'artifacts').mkdir()
                agent.command.return_value = ['python3', '-c',
                    'import sys; from pathlib import Path; '
                    'assert not Path("' + str(root / 'verifier') + '").exists(); '
                    'Path("answer").write_text(sys.argv[1]); '
                    'p = Path("' + str(root / 'artifacts') + '") / "native"; p.mkdir(); '
                    'p.joinpath("session.bin").write_bytes(bytes(range(256))); '
                    f'p.joinpath("session.bin").unlink() if {exit_code == 1} else None; '
                    f'raise SystemExit({exit_code})', 'done']
                task = Task('task', '', 'done', verifier=
                            'from pathlib import Path; assert Path("answer").read_text() == "done"; '
                            f'raise SystemExit({verifier_exit or 0})')
                with patch('minieval.runner.create_sandbox', return_value=sandbox), \
                     patch('minieval.runner.VERIFIER_DIR', str(root / 'verifier')), \
                     patch('minieval.runner.ARTIFACTS_DIR', str(root / 'artifacts')):
                    result = run_attempt(task, agent, 1, 10, root / 'out')
                agent.command.assert_called_once_with('done')
                self.assertTrue(sandbox.stopped)
                self.assertEqual(result.agent_exit_code, exit_code)
                self.assertEqual(result.verifier_exit_code, verifier_exit)
                self.assertEqual(result.passed, verifier_exit == 0)
                self.assertEqual(result.failure_type,
                                 'timeout' if exit_code == 124 else 'verification' if verifier_exit else None)
                with tarfile.open(root / 'out/agent-artifacts.tar.gz') as archive:
                    if exit_code == 1:
                        self.assertNotIn('./native/session.bin', archive.getnames())
                    else:
                        self.assertEqual(archive.extractfile('./native/session.bin').read(), bytes(range(256)))
                with tarfile.open(root / 'out/workspace.tar.gz') as archive:
                    self.assertEqual(archive.extractfile('./answer').read(), b'done')
                saved = json.loads((root / 'out/result.json').read_text())
                self.assertNotIn('turns', saved)
                self.assertNotIn('usage', saved)

    def test_export_failure_does_not_skip_cleanup_or_other_artifacts(self):
        agent = Mock(spec=Agent)
        agent.name, agent.model = 'agent', 'model'
        agent.dependencies = {}

        agent.env.return_value = {}
        sandbox = Mock(workdir='/workspace')
        sandbox.exec.return_value = CommandResult(0, '', '')
        sandbox.run_agent.return_value = CommandResult(0, '', '')
        sandbox.run_verifier.return_value = CommandResult(0, '', '')
        sandbox.export_directory.side_effect = [RuntimeError('download failed'), None]
        with tempfile.TemporaryDirectory() as tmp, patch('minieval.runner.create_sandbox', return_value=sandbox):
            result = run_attempt(Task('task', '', 'prompt', verifier='pass'), agent, 1, 10, Path(tmp))
            self.assertTrue(result.passed)
            self.assertIn('workspace.tar.gz: download failed', result.error)
            self.assertEqual(sandbox.export_directory.call_count, 2)
            sandbox.stop.assert_called_once()

    def test_result_names_are_distinct_and_cannot_escape_directory(self):
        names = ['a/b', 'a_b', 'a%2Fb', '..', '.', '']
        self.assertEqual(len({_slug(name) for name in names}), len(names))
        self.assertTrue(all(_slug(name) not in ('.', '..') and '/' not in _slug(name) for name in names))

    def test_existing_suite_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / 'saved'
            suite.mkdir()
            marker = suite / 'summary.json'
            marker.write_text('original')
            with self.assertRaises(FileExistsError):
                run_suite([], [], 1, 10, Path(tmp), run_name='saved')
            self.assertEqual(marker.read_text(), 'original')


if __name__ == '__main__':
    unittest.main()
