import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux launcher requires POSIX and Bash')
class LinuxLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='research launcher ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.root.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'start-research.sh', self.root / 'start-research.sh')
        (self.root / 'requirements.txt').write_text('pypdf==6.19.0\n', encoding='utf-8')
        (self.root / 'dist').mkdir()
        (self.root / 'dist' / 'index.html').write_text('<html>committed UI</html>', encoding='utf-8')
        self.log = Path(self.temp.name) / 'calls.jsonl'
        self.bin = Path(self.temp.name) / 'fake bin'
        self.bin.mkdir()
        self.python = self.bin / 'python3'
        self.python.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with open(os.environ['LAUNCHER_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps({'interpreter': sys.argv[0], 'args': args, 'cwd': os.getcwd()}) + '\\n')
in_venv = pathlib.Path(sys.argv[0]).parent.parent.name == '.venv'
if args[:1] == ['-c']:
    sys.exit(1 if os.environ.get('LAUNCHER_OLD_PYTHON') else 0)
if args[:2] == ['-m', 'venv']:
    if os.environ.get('LAUNCHER_FAIL_VENV'):
        print('ensurepip unavailable', file=sys.stderr)
        sys.exit(1)
    target = pathlib.Path(args[-1]) / 'bin' / 'python'
    target.parent.mkdir(parents=True)
    shutil.copyfile(sys.argv[0], target)
    target.chmod(0o755)
    sys.exit(0)
if args[:2] == ['-m', 'pip']:
    if '--version' in args:
        sys.exit(1 if os.environ.get('LAUNCHER_NO_PIP') else 0)
    if not in_venv:
        print('system Python pip must never be used', file=sys.stderr)
        sys.exit(99)
    sys.exit(1 if os.environ.get('LAUNCHER_FAIL_PIP') else 0)
if args[:4] == ['-X', 'utf8', '-m', 'research_swarm']:
    print('foreground research server started')
    sys.exit(int(os.environ.get('LAUNCHER_SERVER_EXIT', '0')))
print('unexpected interpreter invocation: ' + repr(args), file=sys.stderr)
sys.exit(98)
''', encoding='utf-8')
        self.python.chmod(0o755)
        self.env = {**os.environ, 'RESEARCH_SWARM_PYTHON': str(self.python), 'LAUNCHER_LOG': str(self.log), 'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', '')}

    def run_launcher(self, *arguments, **environment):
        return subprocess.run(['bash', str(self.root / 'start-research.sh'), *arguments], cwd=self.temp.name, env={**self.env, **environment}, capture_output=True, text=True, timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()] if self.log.exists() else []

    def test_help_exits_without_python_or_dependency_installation(self):
        result = self.run_launcher('--help', RESEARCH_SWARM_PYTHON='/missing/python')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--source', result.stdout)
        self.assertIn('--state-dir', result.stdout)
        self.assertIn('--workers', result.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.root / '.venv').exists())

    def test_prebuilt_ui_starts_in_project_environment_and_preserves_spaced_arguments(self):
        arguments = ['--port', '4790', '--source', '/data/papers with spaces', '--state-dir', '/data/research state', '--workers', '5']
        result = self.run_launcher(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        server = [call for call in calls if call['args'][:4] == ['-X', 'utf8', '-m', 'research_swarm']]
        self.assertEqual(len(server), 1)
        self.assertEqual(server[0]['interpreter'], str(self.root / '.venv' / 'bin' / 'python'))
        self.assertEqual(server[0]['args'][4:], arguments)
        installs = [call for call in calls if call['args'][:2] == ['-m', 'pip'] and 'install' in call['args']]
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0]['interpreter'], str(self.root / '.venv' / 'bin' / 'python'))
        self.assertEqual(installs[0]['args'][installs[0]['args'].index('-r') + 1], str(self.root / 'requirements.txt'))
        self.assertEqual((self.root / 'dist' / 'index.html').read_text(encoding='utf-8'), '<html>committed UI</html>')

    def test_existing_compatible_environment_is_reused_without_base_python(self):
        target = self.root / '.venv' / 'bin' / 'python'
        target.parent.mkdir(parents=True)
        shutil.copyfile(self.python, target)
        target.chmod(0o755)
        result = self.run_launcher(RESEARCH_SWARM_PYTHON='/missing/python')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][:2] == ['-m', 'venv'] for call in self.calls()))
        self.assertTrue(any(call['args'][:4] == ['-X', 'utf8', '-m', 'research_swarm'] for call in self.calls()))

    def test_python_older_than_310_is_rejected_before_environment_creation(self):
        result = self.run_launcher(LAUNCHER_OLD_PYTHON='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('3.10', result.stderr)
        self.assertFalse((self.root / '.venv').exists())

    def test_missing_python_has_an_actionable_error(self):
        result = self.run_launcher(RESEARCH_SWARM_PYTHON='/missing/python')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_venv_failure_explains_required_python_venv_support(self):
        result = self.run_launcher(LAUNCHER_FAIL_VENV='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('venv', result.stderr)
        self.assertFalse(any(call['args'][:4] == ['-X', 'utf8', '-m', 'research_swarm'] for call in self.calls()))

    def test_pip_install_failure_stops_before_server_and_names_requirements(self):
        result = self.run_launcher(LAUNCHER_FAIL_PIP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pip', result.stderr)
        self.assertIn('requirements.txt', result.stderr)
        self.assertFalse(any(call['args'][:4] == ['-X', 'utf8', '-m', 'research_swarm'] for call in self.calls()))

    def test_missing_venv_pip_reports_dependency_bootstrap_problem(self):
        result = self.run_launcher(LAUNCHER_NO_PIP='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pip', result.stderr)
        self.assertIn('ensurepip', result.stderr)

    def test_missing_prebuilt_ui_fails_without_installing_python_dependencies(self):
        (self.root / 'dist' / 'index.html').unlink()
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('dist/index.html', result.stderr)
        self.assertIn('npm run build', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_foreground_server_exit_status_is_preserved(self):
        result = self.run_launcher(LAUNCHER_SERVER_EXIT='7')
        self.assertEqual(result.returncode, 7)

    def test_script_has_valid_bash_syntax(self):
        result = subprocess.run(['bash', '-n', str(self.root / 'start-research.sh')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
