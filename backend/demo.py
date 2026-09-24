"""Demo mode: replays a scripted run when no API key is configured,
so the UI can be shown (and developed) without spending tokens."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

REPORT = """## TL;DR
This is **demo mode** - no `ANTHROPIC_API_KEY` is set on the server, so Scout replayed a scripted run for "{q}". Set the key and restart to get live, cited research.

## Key Findings
- Scout plans, searches and reads sources on its own using Claude's server-side [web search](https://docs.anthropic.com) and web fetch tools.
- Every step streams to this page live over Server-Sent Events.
- Reports always use the same structure: TL;DR, findings, analysis, risks, confidence and follow-ups.

## Analysis
In live mode, the agent decides how many searches to run, which pages deserve a full read, and when the evidence is strong enough to stop. That loop, and not a fixed pipeline, is what makes it an agent.

## Risks & Unknowns
- Demo output is static and not about your question.

## Confidence
Confidence: 100/100 - this is a canned response.

## Follow-up Questions
- What are the top 3 AI agent frameworks in 2026 and how do they compare?
- Which Solana projects gained the most developer activity this quarter?
- What is the current consensus on AI regulation in the EU?
"""


async def demo_run(question: str) -> AsyncIterator[dict[str, Any]]:
    async def pause(s: float = 0.6) -> None:
        await asyncio.sleep(s)

    yield {"type": "status", "text": "Demo mode - set ANTHROPIC_API_KEY for live research"}
    await pause()
    for chunk in "Plan: find recent sources, read the best two, then synthesize.".split(" "):
        yield {"type": "token", "text": chunk + " "}
        await asyncio.sleep(0.03)
    yield {"type": "thinking", "text": "The question is broad, so I'll start wide and then narrow down to primary sources."}
    await pause()
    yield {"type": "tool_pending", "tool": "web_search"}
    await pause()
    yield {"type": "step", "kind": "search", "label": question}
    await pause()
    yield {"type": "sources", "sources": [
        {"title": "Anthropic - Web search tool", "url": "https://docs.anthropic.com"},
        {"title": "FastAPI documentation", "url": "https://fastapi.tiangolo.com"},
        {"title": "MDN - Server-sent events", "url": "https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events"},
    ]}
    for chunk in "Good overview results. Reading the primary source in full.".split(" "):
        yield {"type": "token", "text": chunk + " "}
        await asyncio.sleep(0.03)
    await pause()
    yield {"type": "tool_pending", "tool": "web_fetch"}
    await pause()
    yield {"type": "step", "kind": "read", "label": "https://docs.anthropic.com"}
    await pause(0.9)
    yield {"type": "fetched", "title": "Anthropic - Web search tool", "url": "https://docs.anthropic.com"}
    await pause()
    for line in REPORT.format(q=question).splitlines(keepends=True):
        yield {"type": "token", "text": line}
        await asyncio.sleep(0.05)
    yield {"type": "done", "model": "demo", "usage": {"input": 0, "output": 0, "searches": 1, "fetches": 1}}
