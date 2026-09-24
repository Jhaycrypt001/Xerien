"""Xerien Scout API - streams the research agent's steps to the browser over SSE."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

import anthropic
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import MODEL, run_research
from .demo import demo_run

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Xerien Scout", version="1.0.0")


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    depth: str = Field(default="quick", pattern="^(quick|deep)$")


def _has_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _event_stream(req: ResearchRequest) -> AsyncIterator[str]:
    demo = not _has_credentials()
    source = demo_run(req.question) if demo else run_research(req.question, req.depth)
    try:
        async for event in source:
            yield _sse(event)
    except anthropic.AuthenticationError:
        yield _sse({"type": "error", "text": "Invalid Anthropic API key on the server."})
    except anthropic.RateLimitError:
        yield _sse({"type": "error", "text": "Rate limited by the API - wait a moment and try again."})
    except anthropic.APIStatusError as e:
        yield _sse({"type": "error", "text": f"API error {e.status_code}: {e.message}"})
    except anthropic.APIConnectionError:
        yield _sse({"type": "error", "text": "Couldn't reach the Anthropic API."})
    except asyncio.CancelledError:
        raise  # client disconnected
    except Exception as e:  # noqa: BLE001 - surface anything else to the UI
        yield _sse({"type": "error", "text": f"Agent crashed: {e}"})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL, "mode": "live" if _has_credentials() else "demo"}


@app.post("/api/research")
async def research(req: ResearchRequest) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
