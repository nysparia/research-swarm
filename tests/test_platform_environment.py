import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research_swarm.local_tools import ENVIRONMENT_CODE, LocalResearchTools


class PlatformEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = LocalResearchTools(self.root)

    def test_parent_credentials_and_execution_overrides_are_not_inherited(self):
        hostile = {
            'PATH': str(self.root / 'untrusted-executables'),
            'DEEPSEEK_API_KEY': 'test-provider-credential',
            'GITHUB_TOKEN': 'test-github-credential',
            'AWS_SECRET_ACCESS_KEY': 'test-aws-credential',
            'PYTHONPATH': str(self.root / 'untrusted-imports'),
            'PYTHONHOME': str(self.root / 'untrusted-python'),
            'LD_PRELOAD': str(self.root / 'untrusted-library.so'),
            'LD_LIBRARY_PATH': str(self.root / 'untrusted-libraries'),
            'PIP_INDEX_URL': 'https://example.invalid/untrusted-index',
            'HTTP_PROXY': 'http://example.invalid:8080',
            'DISPLAY': ':99',
        }
        with patch.dict(os.environ, hostile):
            environment = self.tools._env(self.root)
        self.assertNotIn(hostile['PATH'], environment['PATH'])
        for name in hostile:
            if name != 'PATH':
                self.assertFalse(name in environment, f'{name} must not enter experiment processes')

    def test_path_uses_native_platform_directories(self):
        environment = self.tools._env(self.root)
        entries = environment['PATH'].split(os.pathsep)
        self.assertIn(str(Path(sys.executable).parent), entries)
        if os.name == 'nt':
            system = Path(os.environ['SYSTEMROOT'])
            self.assertIn(str(system / 'System32'), entries)
            self.assertIn(str(system), entries)
            self.assertEqual(environment['SYSTEMROOT'], os.environ['SYSTEMROOT'])
        else:
            for directory in os.defpath.split(os.pathsep):
                if directory:
                    self.assertIn(directory, entries)
            self.assertNotIn('/System32', entries)
            self.assertNotIn('/', entries)
            self.assertFalse('COMSPEC' in environment)

    def test_explicit_interpreter_directory_is_first_on_path(self):
        interpreter = self.root / 'custom-python' / ('python.exe' if os.name == 'nt' else 'python')
        tools = LocalResearchTools(self.root, python_executable=interpreter)
        self.assertEqual(tools._env(self.root)['PATH'].split(os.pathsep)[0], str(interpreter.parent))

    def test_created_task_venv_precedes_host_python_on_path(self):
        interpreter = self.root / 'python-env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b'controlled-interpreter-fixture')
        self.assertEqual(self.tools._env(self.root)['PATH'].split(os.pathsep)[0], str(interpreter.parent))

    def test_home_and_all_temporary_directories_stay_inside_task_root(self):
        environment = self.tools._env(self.root)
        for name in ('HOME', 'USERPROFILE', 'TEMP', 'TMP', 'TMPDIR'):
            self.assertTrue(name in environment, f'{name} must be configured')
            directory = Path(environment[name])
            self.assertTrue(directory.is_relative_to(self.root))
            self.assertTrue(directory.is_dir())
        self.assertEqual(environment['TMPDIR'], environment['TMP'])
        self.assertEqual(environment['TEMP'], environment['TMP'])
        self.assertEqual(environment['PIP_CONFIG_FILE'], os.devnull)

    def test_plotting_uses_headless_backend_and_task_local_configuration(self):
        with patch.dict(os.environ, {'MPLBACKEND': 'TkAgg', 'MPLCONFIGDIR': str(self.root.parent / 'shared-matplotlib')}):
            environment = self.tools._env(self.root)
        self.assertTrue('MPLBACKEND' in environment, 'plotting backend must be configured')
        self.assertTrue('MPLCONFIGDIR' in environment, 'plotting configuration must be task-local')
        self.assertEqual(environment['MPLBACKEND'], 'Agg')
        self.assertTrue(Path(environment['MPLCONFIGDIR']).is_relative_to(self.root))
        self.assertTrue(Path(environment['MPLCONFIGDIR']).is_dir())

    def test_real_process_receives_isolated_environment_and_finds_standard_commands(self):
        code = '''import json,os,shutil,tempfile
print(json.dumps({"temporary":tempfile.gettempdir(),"tmpdir":os.environ.get("TMPDIR"),"shell":shutil.which("cmd.exe" if os.name=="nt" else "sh"),"credentialInherited":"DEEPSEEK_API_KEY" in os.environ,"backend":os.environ.get("MPLBACKEND")}))
'''
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-provider-credential', 'PATH': str(self.root / 'untrusted-executables')}):
            result = self.tools._process([sys.executable, '-I', '-X', 'utf8', '-c', code],
                self.root, self.root / 'process-check', 10, lambda _: None, lambda: False)
        self.assertEqual(result['status'], 'completed', result['stderr'])
        observation = json.loads(result['stdout'])
        self.assertEqual(observation['temporary'], str(self.root / 'temporary'))
        self.assertEqual(observation['tmpdir'], str(self.root / 'temporary'))
        self.assertFalse(observation['credentialInherited'])
        self.assertIsNotNone(observation['shell'])
        self.assertEqual(observation['backend'], 'Agg')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux hardware detection runs on Linux')
    def test_linux_environment_detects_memory_and_cpu_without_optional_packages(self):
        process = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', ENVIRONMENT_CODE],
            cwd=self.root, env=self.tools._env(self.root), capture_output=True, text=True,
            encoding='utf-8', timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        observation = json.loads(process.stdout)
        self.assertIn('physicalMemoryBytes', observation)
        self.assertGreater(observation['physicalMemoryBytes'], 0)
        self.assertGreater(observation['logicalCpus'], 0)
        self.assertTrue(observation['cpu'].strip())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux local experiment runs on Linux')
    def test_linux_local_experiment_creates_venv_and_records_artifact(self):
        code = '''import json,os,pathlib
pathlib.Path("result.json").write_text(json.dumps({"value": 42, "credentialInherited": "DEEPSEEK_API_KEY" in os.environ}))
print("experiment completed")
'''
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-provider-credential'}):
            result = self.tools._call('python_run', {'code': code}, {'id': 'linux-smoke'},
                                      {'round': 1}, lambda _: None)
        self.assertEqual(result['status'], 'completed', result['stderr'])
        self.assertEqual(result['returnCode'], 0)
        self.assertIn('experiment completed', result['stdout'])
        self.assertTrue((self.root / 'python-env' / 'bin' / 'python').is_file())
        artifact = next(item for item in result['artifacts'] if item['name'] == 'result.json')
        content = json.loads((self.root / artifact['path']).read_text(encoding='utf-8'))
        self.assertEqual(content, {'value': 42, 'credentialInherited': False})
        self.assertTrue((self.root / result['evidence'][0]['locator']).is_file())


if __name__ == '__main__':
    unittest.main()
