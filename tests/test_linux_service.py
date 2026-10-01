"""A service manager's SIGTERM must run the normal workspace cleanup."""
import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
from urllib.error import URLError


@unittest.skipUnless(os.name == 'posix', 'SIGTERM service lifecycle is POSIX-specific')
class LinuxServiceTests(unittest.TestCase):
    def test_real_service_serves_bundled_ui_and_health_then_stops(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='swarm-http-') as temp:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            process = subprocess.Popen(
                [sys.executable, '-B', '-X', 'utf8', '-m', 'research_swarm',
                 '--source', str(project / 'vendor' / 'ai-access'),
                 '--state-dir', str(Path(temp) / 'state'), '--port', str(port)],
                cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8')
            try:
                deadline = time.monotonic() + 15
                health = None
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
                            health = json.load(response)
                        break
                    except (URLError, TimeoutError):
                        time.sleep(.05)
                self.assertEqual(health, {'ok': True, 'service': 'research-swarm', 'version': '0.2.0'})
                with urlopen(f'http://127.0.0.1:{port}/', timeout=5) as response:
                    html = response.read().decode('utf-8')
                self.assertIn('<html', html.lower())
                with urlopen(f'http://127.0.0.1:{port}/api/tasks', timeout=5) as response:
                    tasks = json.load(response)
                self.assertIn('tasks', tasks)
                process.terminate()
                _, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)

    def test_sigterm_closes_workspace_before_service_exits(self):
        with tempfile.TemporaryDirectory(prefix='swarm-service-') as temp:
            root = Path(temp)
            script = '''
import sys
from pathlib import Path
from research_swarm import server, workspace
root = Path(sys.argv[1])
class ObservedWorkspace(workspace.WorkspaceApplication):
    def close(self):
        super().close()
        (root / 'closed').write_text('workspace drained', encoding='utf-8')
class ReadyServer(server.ThreadingHTTPServer):
    def serve_forever(self, *args, **kwargs):
        (root / 'ready').write_text('listening', encoding='utf-8')
        return super().serve_forever(*args, **kwargs)
workspace.WorkspaceApplication = ObservedWorkspace
server.ThreadingHTTPServer = ReadyServer
sys.argv = ['research_swarm', '--source', 'vendor/ai-access', '--state-dir', str(root / 'state'), '--port', '0']
server.main()
'''
            process = subprocess.Popen(
                [sys.executable, '-B', '-X', 'utf8', '-c', script, str(root)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            try:
                deadline = time.monotonic() + 10
                while not (root / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.025)
                self.assertTrue((root / 'ready').exists(), 'server did not begin serving')
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertTrue((root / 'closed').is_file(), f'SIGTERM skipped workspace cleanup: {stdout}\n{stderr}')
                self.assertEqual(process.returncode, 0, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
