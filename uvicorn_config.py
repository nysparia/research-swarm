"""uvicorn entrypoint to run the ASGI app.

Usage: python -m uvicorn_config
or:   python -m uvicorn --app-dir . uvicorn_config:app
or:   python -m uvicorn research_swarm.asgi:app
"""
from research_swarm.asgi import app

# Expose `app` for uvicorn. This module intentionally keeps minimal logic so
# the project can be launched via `python -m uvicorn_config` or via uvicorn CLI.
