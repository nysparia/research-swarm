"""ASGI adapter: expose research_swarm as a FastAPI app for uvicorn."""
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Research Swarm (ASGI)")


@app.get("/health")
def health():
    return {"ok": True, "service": "research-swarm", "version": "0.1.0"}


@app.get("/ready")
def ready():
    return PlainTextResponse("ready")


# Minimal wrapper endpoints can be added later. This module intentionally
# does not attempt to reimplement the existing HTTP server; it only provides
# a small ASGI entrypoint so the project can be served by uvicorn if desired.
