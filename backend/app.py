"""Xerien Scout API.

- Wallet sign-in with HttpOnly session cookies
- Streams the research agent's steps over SSE (signed-in accounts only)
- Per-account history, public read-only share links
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import anthropic
from google.genai import errors as genai_errors
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, store
from .agent import WALLET_SCAN_QUESTION, model_name, provider, run_research

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
RUNS_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "20"))  # per account
IP_RUNS_PER_HOUR = int(os.getenv("IP_RATE_LIMIT_PER_HOUR", "40"))  # wallets are free; cap per IP too
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "8"))  # protects the API key budget
IP_AUTH_PER_HOUR = 60  # sign-in challenges / verifications per IP
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")

log = logging.getLogger("xerien")

app = FastAPI(title="Xerien Scout", version="2.0.0", docs_url=None, redoc_url=None, openapi_url=None)
store.init()
auth.init()

_hits: dict[str, deque[float]] = defaultdict(deque)
_active: dict[str, float] = {}  # account -> start time of its in-flight run
RUN_LOCK_TTL = 15 * 60  # self-heals if a stream never started


# ---------- security ----------

@app.middleware("http")
async def security(request: Request, call_next):
    # Reject cross-site state-changing requests (defence in depth on top of SameSite cookies).
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    if _is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _is_https(request: Request) -> bool:
    # uvicorn --proxy-headers already maps a trusted X-Forwarded-Proto onto the scheme.
    return request.url.scheme == "https"


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers resolves the real client from the proxy chain; never trust
    # a raw X-Forwarded-For value here, since clients can set it themselves.
    return request.client.host if request.client else "?"


def _limit(key: str, per_hour: int, what: str) -> None:
    now = time.time()
    if len(_hits) > 50_000:  # bound memory: drop idle windows
        for k in [k for k, w in _hits.items() if not w or now - w[-1] > 3600]:
            del _hits[k]
    window = _hits[key]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= per_hour:
        raise HTTPException(429, f"Limit of {per_hour} {what} per hour reached. Try again later.")
    window.append(now)


def current_account(request: Request) -> str | None:
    return auth.session_account(request.cookies.get(auth.COOKIE))


def require_account(request: Request) -> str:
    account = current_account(request)
    if not account:
        raise HTTPException(401, "Sign in with your wallet to continue")
    return account


def _configured() -> bool:
    return provider() is not None


# ---------- auth ----------

class ChallengeRequest(BaseModel):
    chain: str = Field(pattern="^(solana|ethereum)$")
    address: str = Field(min_length=32, max_length=64)


class VerifyRequest(BaseModel):
    nonce: str = Field(pattern="^[0-9a-f]{32}$")
    signature: str = Field(min_length=64, max_length=256)


@app.post("/api/auth/challenge")
async def challenge(body: ChallengeRequest, request: Request) -> dict[str, str]:
    _limit(f"auth:{_client_ip(request)}", IP_AUTH_PER_HOUR, "sign-in attempts")
    host = request.headers.get("host", "")
    if not HOST_RE.match(host):
        raise HTTPException(400, "Invalid Host header")
    origin = f"{'https' if _is_https(request) else 'http'}://{host}"
    try:
        return auth.create_challenge(body.chain, body.address, host, origin)
    except auth.AuthError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/auth/verify")
async def verify(body: VerifyRequest, request: Request, response: Response) -> dict[str, Any]:
    _limit(f"verify:{_client_ip(request)}", IP_AUTH_PER_HOUR, "sign-in attempts")
    try:
        token, account = auth.verify_and_create_session(body.nonce, body.signature)
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True,
        secure=_is_https(request), samesite="lax", path="/",
    )
    return auth.describe(account)


@app.get("/api/auth/me")
async def me(account: str = Depends(require_account)) -> dict[str, Any]:
    return auth.describe(account)


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    auth.end_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# ---------- research ----------

class ResearchRequest(BaseModel):
    question: str = Field(default="", max_length=2000)
    depth: str = Field(default="quick", pattern="^(quick|deep)$")
    scan_wallet: bool = False


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _event_stream(
    req: ResearchRequest, account: str, question: str, wallet: tuple[str, str] | None,
) -> AsyncIterator[str]:
    started = time.monotonic()
    trace: list[dict[str, Any]] = []
    text = ""  # narration between tool calls becomes a trace note; the tail is the report

    try:
        async for event in run_research(question, req.depth, wallet=wallet):
            yield _sse(event)
            etype = event["type"]
            if etype == "token":
                text += event["text"]
                continue
            if etype == "report":  # provider re-sent the final report (e.g. with citations)
                text = event["text"]
                continue
            if etype == "tool_pending" and text.strip():
                trace.append({"type": "note", "text": text.strip()})
                text = ""
            if etype != "tool_pending":
                trace.append(event)
            if etype == "done" and text.strip():
                rid = store.save(
                    workspace=account, question=question, depth=req.depth, report=text.strip(),
                    trace=trace, model=event.get("model"), usage=event.get("usage"),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                yield _sse({"type": "saved", "id": rid})
    except anthropic.AuthenticationError:
        yield _sse({"type": "error", "text": "The server's Anthropic API key is invalid."})
    except anthropic.PermissionDeniedError as e:
        yield _sse({"type": "error", "text": f"The API key can't use this feature: {e.message}"})
    except anthropic.RateLimitError:
        yield _sse({"type": "error", "text": "Scout is at capacity right now. Try again in a minute."})
    except anthropic.APIStatusError as e:
        log.warning("anthropic error %s: %s", e.status_code, e.message)
        yield _sse({"type": "error", "text": f"The model API returned an error ({e.status_code}). Try again shortly."})
    except anthropic.APIConnectionError:
        yield _sse({"type": "error", "text": "Couldn't reach the model API."})
    except genai_errors.APIError as e:
        if e.code == 429:
            text_ = "Gemini rate limit reached (the free tier allows only a few requests per minute). Try again shortly."
        elif e.code in (401, 403):
            text_ = "The server's Gemini API key is invalid or lacks access to this model."
        else:
            log.warning("gemini error %s: %s", e.code, e.message)
            text_ = f"The Gemini API returned an error ({e.code}). Try again shortly."
        yield _sse({"type": "error", "text": text_})
    except asyncio.CancelledError:
        raise  # client disconnected
    except Exception:  # noqa: BLE001 - log details, show a generic message
        log.exception("research run failed")
        yield _sse({"type": "error", "text": "Scout hit an unexpected error. Please try again."})
    finally:
        _active.pop(account, None)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "provider": provider(), "model": model_name(), "configured": _configured()}


@app.post("/api/research")
async def research(
    req: ResearchRequest, request: Request, account: str = Depends(require_account),
) -> StreamingResponse:
    if not _configured():
        raise HTTPException(503, "Scout isn't configured yet: set GEMINI_API_KEY or ANTHROPIC_API_KEY on the server.")
    wallet = None
    if req.scan_wallet:
        chain, address = account.split(":", 1)
        wallet = (chain, address)
    question = req.question.strip() or (WALLET_SCAN_QUESTION if wallet else "")
    if len(question) < 3:
        raise HTTPException(422, "Ask a question of at least 3 characters.")
    now = time.time()
    if now - _active.get(account, 0) < RUN_LOCK_TTL:
        raise HTTPException(409, "You already have a research run in progress.")
    if sum(1 for t in _active.values() if now - t < RUN_LOCK_TTL) >= MAX_CONCURRENT_RUNS:
        raise HTTPException(503, "Scout is busy right now. Try again in a minute.")
    _limit(f"run:{account}", RUNS_PER_HOUR, "research runs")
    _limit(f"runip:{_client_ip(request)}", IP_RUNS_PER_HOUR, "research runs from this network")
    _active[account] = time.time()
    return StreamingResponse(
        _event_stream(req, account, question, wallet),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/reports")
async def list_reports(account: str = Depends(require_account)) -> list[dict[str, Any]]:
    return store.list_for(account)


@app.get("/api/reports/{rid}")
async def get_report(rid: str, request: Request) -> dict[str, Any]:
    report = store.get(rid)
    if not report:
        raise HTTPException(404, "Report not found")
    owner = report.pop("workspace")
    report["owner"] = owner == current_account(request)
    return report


@app.delete("/api/reports/{rid}")
async def delete_report(rid: str, account: str = Depends(require_account)) -> dict[str, bool]:
    if not store.delete(rid, account):
        raise HTTPException(404, "Report not found")
    return {"ok": True}


# ---------- pages ----------

@app.get("/")
async def landing() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/app")
@app.get("/r/{rid}")
async def dashboard(rid: str | None = None) -> FileResponse:
    return FileResponse(FRONTEND / "app.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
