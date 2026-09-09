import os
import tempfile
import unittest
from pathlib import Path

from minieval.agents import ScriptAgent
from minieval.dependencies import install_dependencies
from minieval.env import load_env
from minieval.sandbox import ModalSandbox, VercelSandbox


class PermissionTests(unittest.TestCase):
    test_env = 'TEST_VERCEL'
    create_sandbox = staticmethod(lambda: VercelSandbox(timeout_ms=120000))

    @classmethod
    def setUpClass(cls):
        if os.environ.get(cls.test_env) != '1':
            raise unittest.SkipTest(f'set {cls.test_env}=1 for live sandbox permission checks')
        load_env(Path(__file__).resolve().parents[1])

    def test_agent_cannot_modify_code_or_read_verifier(self):
        sandbox = self.create_sandbox()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                install_dependencies(sandbox, {}, source / 'setup_output.txt')
                (source / 'run.sh').write_text('exec python3 /agent/check.py "$1"')
                (source / 'helper').write_text('#!/bin/sh\nprintf executable')
                (source / 'helper').chmod(0o755)
                (source / 'check.py').write_text('''import ctypes, os, subprocess, sys
from pathlib import Path
assert os.getuid() == 10000
assert os.getgroups() == []
status = Path('/proc/self/status').read_text()
# Modal's /proc/self/status omits NoNewPrivs; query the kernel flag directly.
assert ctypes.CDLL(None).prctl(39, 0, 0, 0, 0) == 1  # PR_GET_NO_NEW_PRIVS
assert 'CapBnd:\\t0000000000000000' in status
for operation in (
    lambda: Path('/agent/check.py').write_text('changed'),
    lambda: Path('/agent/check.py').chmod(0o777),
    lambda: Path('/agent/check.py').unlink(),
    lambda: Path('/agent/check.py').rename('/agent/moved'),
    lambda: Path('/agent/new').touch(),
    lambda: Path('/agent').rename('/moved'),
    lambda: Path('/verifier/verifier.py').read_text(),
    lambda: os.setuid(0),
):
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('permission boundary failed')
assert subprocess.check_output(['/agent/helper']) == b'executable'
Path('answer').write_text(sys.argv[1])
Path('/artifacts/session').write_text('native trace')
Path.home().joinpath('config').write_text('writable')
''')
                agent = ScriptAgent('test', source, 'test-model')
                agent.setup(sandbox)
                sandbox.write_files({'starter': 'editable'})
                agent.protect(sandbox)
                protected = sandbox.exec(['mkdir', '-m', '0700', '/verifier'])
                self.assertTrue(protected.ok, protected.stderr)
                sandbox.write_files({'verifier.py': """import os
from pathlib import Path
assert os.getuid() == 10001
assert Path('answer').read_text() == 'task prompt'
try:
    Path('/agent/check.py').write_text('changed by solution during grading')
except PermissionError:
    pass
else:
    raise AssertionError('grading must not run as root')
"""}, extract_dir='/verifier')
                result = sandbox.run_agent(agent.command('task prompt'), agent.env(), 30)
                self.assertEqual(result.exit_code, 0, result.stdout + result.stderr)
                self.assertEqual(sandbox.exec(['cat', '/workspace/answer']).stdout, 'task prompt')
                self.assertEqual(sandbox.exec(['stat', '-c', '%u %a', '/agent/check.py']).stdout.strip(), '0 444')
                self.assertEqual(sandbox.exec(['stat', '-c', '%u %a', '/agent/helper']).stdout.strip(), '0 555')
                graded = sandbox.run_verifier('/verifier')
                self.assertEqual(graded.exit_code, 0, graded.stdout + graded.stderr)
        finally:
            sandbox.stop()


class ModalPermissionTests(PermissionTests):
    test_env = 'TEST_MODAL'
    create_sandbox = staticmethod(lambda: ModalSandbox(timeout_s=120))
