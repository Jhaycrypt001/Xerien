"""Xerien Scout API - streams the research agent's steps to the browser over SSE
and keeps a per-workspace history of reports with public share links."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator

import anthropic
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .agent import MODEL, run_research

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "20"))
WORKSPACE_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")

app = FastAPI(title="Xerien Scout", version="1.1.0")
store.init()

_hits: dict[str, deque[float]] = defaultdict(deque)


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    depth: str = Field(default="quick", pattern="^(quick|deep)$")


def _configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _workspace(value: str | None) -> str:
    if not value or not WORKSPACE_RE.match(value):
        raise HTTPException(400, "Missing or invalid X-Workspace header")
    return value


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _rate_limit(ip: str) -> None:
    now, window = time.time(), _hits[ip]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_HOUR:
        raise HTTPException(429, f"Limit of {RATE_LIMIT_PER_HOUR} research runs per hour reached")
    window.append(now)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _event_stream(req: ResearchRequest, workspace: str) -> AsyncIterator[str]:
    started = time.monotonic()
    trace: list[dict[str, Any]] = []
    text = ""  # narration between tool calls becomes a trace note; the tail is the report

    try:
        async for event in run_research(req.question, req.depth):
            yield _sse(event)
            etype = event["type"]
            if etype == "token":
                text += event["text"]
                continue
            if etype == "tool_pending" and text.strip():
                trace.append({"type": "note", "text": text.strip()})
                text = ""
            if etype != "tool_pending":
                trace.append(event)
            if etype == "done" and text.strip():
                rid = store.save(
                    workspace=workspace, question=req.question, depth=req.depth, report=text.strip(),
                    trace=trace, model=event.get("model"), usage=event.get("usage"),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                yield _sse({"type": "saved", "id": rid})
    except anthropic.AuthenticationError:
        yield _sse({"type": "error", "text": "The server's Anthropic API key is invalid."})
    except anthropic.RateLimitError:
        yield _sse({"type": "error", "text": "Scout is at capacity right now. Try again in a minute."})
    except anthropic.APIStatusError as e:
        yield _sse({"type": "error", "text": f"Model API error {e.status_code}: {e.message}"})
    except anthropic.APIConnectionError:
        yield _sse({"type": "error", "text": "Couldn't reach the model API."})
    except asyncio.CancelledError:
        raise  # client disconnected
    except Exception as e:  # noqa: BLE001 - surface anything else to the UI
        yield _sse({"type": "error", "text": f"Agent error: {e}"})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL, "configured": _configured()}


@app.post("/api/research")
async def research(
    req: ResearchRequest, request: Request, x_workspace: str | None = Header(default=None),
) -> StreamingResponse:
    workspace = _workspace(x_workspace)
    if not _configured():
        raise HTTPException(503, "Scout isn't configured yet: ANTHROPIC_API_KEY is not set on the server.")
    _rate_limit(_client_ip(request))
    return StreamingResponse(
        _event_stream(req, workspace),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/reports")
async def list_reports(x_workspace: str | None = Header(default=None)) -> list[dict[str, Any]]:
    return store.list_for(_workspace(x_workspace))


@app.get("/api/reports/{rid}")
async def get_report(rid: str, x_workspace: str | None = Header(default=None)) -> dict[str, Any]:
    report = store.get(rid)
    if not report:
        raise HTTPException(404, "Report not found")
    report["owner"] = bool(x_workspace) and report.pop("workspace") == x_workspace
    report.pop("workspace", None)
    return report


@app.delete("/api/reports/{rid}")
async def delete_report(rid: str, x_workspace: str | None = Header(default=None)) -> dict[str, bool]:
    if not store.delete(rid, _workspace(x_workspace)):
        raise HTTPException(404, "Report not found")
    return {"ok": True}


@app.get("/")
async def landing() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/app")
@app.get("/r/{rid}")
async def dashboard(rid: str | None = None) -> FileResponse:
    return FileResponse(FRONTEND / "app.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
