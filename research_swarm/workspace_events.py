"""Resumable task event transport over the existing loopback HTTP server."""
import json
import re
import time
from urllib.parse import parse_qs


def serve_events(handler, app, path, query):
    match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})/events/stream', path)
    if not match or not hasattr(app, 'backend'):
        return False
    cursor = handler.headers.get('Last-Event-ID') or parse_qs(query).get('after', ['0'])[0]
    event_page = getattr(app, 'event_page', app.backend.event_page)
    page = event_page(match[1], cursor, 100)
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Connection', 'close')
    handler.end_headers()
    handler.close_connection = True
    deadline = time.monotonic() + 25
    heartbeat = time.monotonic()
    try:
        while not app._closed:
            for event in page['events']:
                cursor = event['id']
                data = json.dumps(event, ensure_ascii=False, allow_nan=False)
                handler.wfile.write(f'id: {cursor}\nevent: research\ndata: {data}\n\n'.encode('utf-8'))
            handler.wfile.flush()
            if time.monotonic() >= deadline:
                break
            if time.monotonic() - heartbeat > 5:
                handler.wfile.write(b': heartbeat\n\n')
                handler.wfile.flush()
                heartbeat = time.monotonic()
            if app._stop_event.wait(.25):
                break
            page = event_page(match[1], cursor, 100)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    return True
