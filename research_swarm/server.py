"""Loopback HTTP transport, static frontend and service entry point."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import signal
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Keep the original public imports available to standalone callers.
from .runtime import APP_ROOT, ResearchApplication, utc_now
from .exports import export_bundle


DEFAULT_SOURCE = Path(os.environ.get('RESEARCH_SWARM_SOURCE') or APP_ROOT / 'vendor' / 'ai-access').expanduser()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ResearchSwarm/0.1'

        def log_message(self, fmt, *args):
            # Never log bodies, provider credentials, or URLs with query parameters.
            if args and str(args[0]).startswith('GET /api/events'):
                return

        def _allowed_host(self):
            authority = self.headers.get('Host', '')
            try:
                parsed = urllib.parse.urlsplit('http://' + authority)
                return parsed.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.port == self.server.server_port
            except ValueError:
                return False

        def send_data(self, status, data, content_type='application/json; charset=utf-8', headers=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _error(self, error, status=400):
            message = app.settings.safe_error(error) if hasattr(app, 'settings') else str(error)
            from .v2_contracts import DomainError
            if isinstance(error, DomainError):
                self.send_data(error.status, {'error': message, 'code': error.code})
                return
            if '版本' in message or '状态已变化' in message or 'revision' in message.lower():
                status = 409
            self.send_data(status, {'error': message})

        def do_POST(self):
            if not self._allowed_host():
                self.send_data(403, {'error': '不允许的主机'})
                return
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://{self.headers.get("Host")}', f'http://localhost:{self.server.server_port}', f'http://127.0.0.1:{self.server.server_port}'):
                self.send_data(403, {'error': '只允许本机同源操作'})
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                self.send_data(415, {'error': '请求必须为 application/json'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 2 * 1024 * 1024:
                    self.send_data(413, {'error': '请求过大'})
                    return
                payload = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(payload, dict):
                    raise ValueError('请求体必须为对象')
                result = app.post(urllib.parse.urlsplit(self.path).path, payload)
                self.send_data(200, result)
            except (ValueError, KeyError, RuntimeError, TypeError) as exc:
                self._error(exc)
            except Exception as exc:
                self._error(RuntimeError('操作失败：' + str(exc)), 500)

        def do_GET(self):
            if not self._allowed_host():
                self.send_data(403, {'error': '不允许的主机'})
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            try:
                from .workspace_events import serve_events
                if serve_events(self, app, path, parsed.query):
                    return
                if hasattr(app, 'read_api') and path.startswith('/api/'):
                    response = app.read_api(path, parsed.query)
                    if response is None:
                        self.send_data(404, {'error': 'API 不存在'})
                    else:
                        self.send_data(response[0], response[1], response[2], response[3])
                    return
                if path == '/api/state':
                    self.send_data(200, app.snapshot() if hasattr(app, 'snapshot') else app.engine.snapshot())
                elif path == '/api/health':
                    self.send_data(200, {'ok': True, 'service': 'research-swarm', 'version': '0.1.0'})
                elif path == '/api/settings':
                    result = app.settings.public()
                    result['mode'] = app.engine.snapshot()['project']['mode']
                    self.send_data(200, result)
                elif path == '/api/events':
                    self._events()
                elif path == '/api/export':
                    state = app.snapshot()
                    if not state['report']['approved']:
                        self._error(ValueError('请先完成最终输出确认'), 409)
                        return
                    state['executionHistory'] = app.engine.export_audit()
                    if urllib.parse.parse_qs(parsed.query).get('format') == ['json']:
                        self.send_data(200, state, headers={'Content-Disposition': 'attachment; filename="research-data.json"'})
                    else:
                        self.send_data(200, export_bundle(state, app.state_dir), 'application/zip', {'Content-Disposition': 'attachment; filename="research-results.zip"'})
                elif path.startswith('/api/nodes/') and path.endswith('/history'):
                    node_id = path[len('/api/nodes/'):-len('/history')]
                    self.send_data(200, {'runs': app.engine.node_history(node_id)})
                elif path.startswith('/api/papers/') and path.endswith('/pdf'):
                    paper_id = path.split('/')[-2]
                    pdf = app.library.pdf_path(paper_id)
                    if not pdf:
                        self._error(ValueError('这篇论文暂无已验证的本地 PDF'), 404)
                        return
                    self.send_data(200, Path(pdf).read_bytes(), 'application/pdf', {'Content-Disposition': 'inline; filename="paper-' + str(int(paper_id)) + '.pdf"'})
                elif path.startswith('/api/'):
                    self.send_data(404, {'error': 'API 不存在'})
                else:
                    self._static(path)
            except (ValueError, KeyError, RuntimeError, TypeError) as exc:
                self._error(exc)
            except Exception as exc:
                self._error(RuntimeError('读取失败：' + str(exc)), 500)

        def _events(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            previous = None
            try:
                for _ in range(120):
                    state = app.engine.snapshot()
                    version = (state['revision'], getattr(app, 'operation_revision', 0))
                    if version != previous:
                        self.wfile.write(('event: change\ndata: ' + json.dumps({'revision': version[0]}) + '\n\n').encode('utf-8'))
                        previous = version
                    else:
                        self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
                    time.sleep(.75)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        def _static(self, path):
            root = app.static_dir.resolve()
            file = (root / path.lstrip('/')).resolve()
            if not file.is_relative_to(root):
                self.send_data(403, {'error': '路径不允许'})
                return
            if not file.is_file():
                if Path(path).suffix:
                    self.send_data(404, {'error': '文件不存在'})
                    return
                file = root / 'index.html'
            if not file.is_file():
                self.send_data(503, b'Frontend not built. Run npm ci && npm run build.', 'text/plain; charset=utf-8')
                return
            content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if file.suffix in ('.js', '.mjs'):
                content_type = 'text/javascript'
            self.send_data(200, file.read_bytes(), content_type + ('; charset=utf-8' if content_type.startswith(('text/', 'application/javascript')) else ''))

    return Handler


def main():
    parser = argparse.ArgumentParser(description='科研蜂群 · 本地研究协作工作台')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--state-dir', type=Path, default=APP_ROOT / '.research-state')
    parser.add_argument('--port', type=int, default=4381)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    from .workspace import WorkspaceApplication
    app = WorkspaceApplication(args.source, args.state_dir, max_workers=max(1, min(args.workers, 8)))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    server.daemon_threads = True
    previous_terminate_handler = None
    terminate_requested = False

    def terminate_service(signum, frame):
        nonlocal terminate_requested
        if not terminate_requested:
            terminate_requested = True
            raise KeyboardInterrupt

    if os.name == 'posix' and threading.current_thread() is threading.main_thread():
        previous_terminate_handler = signal.signal(signal.SIGTERM, terminate_service)
    print(f'科研蜂群：http://127.0.0.1:{server.server_port}  |  source={args.source}', flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
            app.close()
        finally:
            if previous_terminate_handler is not None:
                signal.signal(signal.SIGTERM, previous_terminate_handler)


if __name__ == '__main__':
    main()
