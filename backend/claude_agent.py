"""Claude provider: web_search + web_fetch server tools, streamed as UI events."""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

import anthropic

from .prompts import claude_system

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")
MAX_CONTINUATIONS = 6  # pause_turn resumes (server tool loop hit its limit)
USE_FALLBACKS = os.getenv("CLAUDE_FALLBACKS", "1") != "0"

DEPTHS = {
    # depth: (effort, max web searches, max page reads)
    "quick": ("medium", 4, 3),
    "deep": ("high", 10, 8),
}


def _tools(max_searches: int, max_fetches: int) -> list[dict[str, Any]]:
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_fetches},
    ]


def _block_event(block: Any) -> dict[str, Any] | None:
    """Translate a completed content block into a UI step event."""
    btype = block.type

    if btype == "server_tool_use":
        data = block.input if isinstance(block.input, dict) else {}
        if block.name == "web_search":
            return {"type": "step", "kind": "search", "label": data.get("query", "")}
        if block.name == "web_fetch":
            return {"type": "step", "kind": "read", "label": data.get("url", "")}
        return {"type": "step", "kind": "analyze", "label": "Crunching the results"}

    if btype == "web_search_tool_result":
        content = block.content
        if isinstance(content, list):
            sources = [
                {"title": r.title, "url": r.url}
                for r in content
                if getattr(r, "type", "") == "web_search_result"
            ]
            return {"type": "sources", "sources": sources}
        return {"type": "step", "kind": "error", "label": f"Search failed: {getattr(content, 'error_code', 'unknown')}"}

    if btype == "web_fetch_tool_result":
        content = block.content
        if getattr(content, "type", "") == "web_fetch_result":
            doc = getattr(content, "content", None)
            title = getattr(doc, "title", None) or content.url
            return {"type": "fetched", "title": title, "url": content.url}
        return {"type": "step", "kind": "error", "label": f"Couldn't read page: {getattr(content, 'error_code', 'unknown')}"}

    if btype == "thinking":
        text = (getattr(block, "thinking", "") or "").strip()
        if text:
            return {"type": "thinking", "text": text}

    return None


async def run(user_content: str, depth: str) -> AsyncIterator[dict[str, Any]]:
    effort, max_searches, max_fetches = DEPTHS.get(depth, DEPTHS["quick"])
    client = anthropic.AsyncAnthropic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    request: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 64000,
        "system": claude_system(),
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": effort},
        "tools": _tools(max_searches, max_fetches),
    }
    if USE_FALLBACKS:
        # Server-side refusal fallback: a declined request is re-run on a fallback model.
        request["betas"] = ["server-side-fallback-2026-07-01"]
        request["fallbacks"] = "default"

    usage = {"input": 0, "output": 0, "searches": 0, "fetches": 0}
    compat = False
    for _ in range(MAX_CONTINUATIONS):
        stream_cm = client.beta.messages.stream(messages=messages, **request)
        try:
            stream = await stream_cm.__aenter__()
        except anthropic.BadRequestError:
            # Some orgs/models lack the refusal-fallback beta or the newest web tool
            # versions. Retry once, before any output, with the widely available set.
            if compat:
                raise
            compat = True
            request.pop("fallbacks", None)
            request["betas"] = ["web-fetch-2025-09-10"]
            request["tools"] = [
                {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches},
                {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": max_fetches},
            ]
            stream_cm = client.beta.messages.stream(messages=messages, **request)
            stream = await stream_cm.__aenter__()
        try:
            async for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield {"type": "token", "text": event.delta.text}
                elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                    yield {"type": "tool_pending", "tool": event.content_block.name}
                elif event.type == "content_block_stop":
                    step = _block_event(event.content_block)
                    if step:
                        yield step
            response = await stream.get_final_message()
        finally:
            await stream_cm.__aexit__(None, None, None)

        usage["input"] += response.usage.input_tokens
        usage["output"] += response.usage.output_tokens
        stu = getattr(response.usage, "server_tool_use", None)
        if stu:
            usage["searches"] += getattr(stu, "web_search_requests", 0) or 0
            usage["fetches"] += getattr(stu, "web_fetch_requests", 0) or 0

        if response.stop_reason == "pause_turn":
            # Server-side tool loop hit its iteration cap; resend and it resumes.
            messages.append({"role": "assistant", "content": response.content})
            continue

        if response.stop_reason == "refusal":
            yield {"type": "error", "text": "The model declined this request. Try rephrasing it."}
            return
        if response.stop_reason == "max_tokens":
            yield {"type": "status", "text": "Report hit the length limit and may be cut short."}
        break

    yield {"type": "done", "model": response.model, "usage": usage}
