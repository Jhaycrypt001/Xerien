"""Gemini provider: Grounding with Google Search (+ URL context), streamed as UI events.

Gemini runs its searches server-side inside one call, so the trace shows its thinking
summaries first and the search queries and sources once grounding metadata arrives.
Citations are attached afterwards from the grounding supports.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from google import genai
from google.genai import errors, types

from .prompts import gemini_system

# Free tier: only the 2.5 models include Google Search grounding.
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _config(deep: bool, with_url_context: bool) -> types.GenerateContentConfig:
    tools = [types.Tool(google_search=types.GoogleSearch())]
    if with_url_context:
        tools.append(types.Tool(url_context=types.UrlContext()))
    return types.GenerateContentConfig(
        system_instruction=gemini_system(deep),
        tools=tools,
        thinking_config=types.ThinkingConfig(include_thoughts=True),
        max_output_tokens=16384,
    )


def add_citations(text: str, metadata: types.GroundingMetadata | None) -> str:
    """Insert [n](url) links after each grounded segment. Segment offsets are UTF-8 bytes."""
    if not metadata or not metadata.grounding_supports or not metadata.grounding_chunks:
        return text
    chunks = metadata.grounding_chunks
    raw = text.encode()
    inserts: dict[int, list[int]] = {}
    for support in metadata.grounding_supports:
        end = support.segment.end_index if support.segment else None
        if end is None or end > len(raw) or not support.grounding_chunk_indices:
            continue
        inserts.setdefault(end, [])
        for i in support.grounding_chunk_indices:
            if i < len(chunks) and chunks[i].web and chunks[i].web.uri and i not in inserts[end]:
                inserts[end].append(i)
    for end in sorted(inserts, reverse=True):
        links = "".join(f" [{i + 1}]({chunks[i].web.uri})" for i in inserts[end])
        raw = raw[:end] + links.encode() + raw[end:]
    return raw.decode(errors="ignore")


async def run(user_content: str, depth: str) -> AsyncIterator[dict[str, Any]]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    deep = depth == "deep"

    try:
        stream = await client.aio.models.generate_content_stream(
            model=MODEL, contents=user_content, config=_config(deep, with_url_context=True),
        )
    except errors.ClientError as e:
        if e.code != 400:
            raise
        # URL context isn't available for every model or tier; search alone still works.
        stream = await client.aio.models.generate_content_stream(
            model=MODEL, contents=user_content, config=_config(deep, with_url_context=False),
        )

    text, thought = "", ""
    queries: set[str] = set()
    sources: dict[str, dict[str, str]] = {}
    fetched: set[str] = set()
    grounding: types.GroundingMetadata | None = None
    usage = {"input": 0, "output": 0, "searches": 0, "fetches": 0}
    finish = None

    async for chunk in stream:
        if chunk.usage_metadata:
            um = chunk.usage_metadata
            usage["input"] = um.prompt_token_count or 0
            usage["output"] = (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
        if not chunk.candidates:
            continue
        cand = chunk.candidates[0]
        finish = cand.finish_reason or finish

        for part in (cand.content.parts if cand.content and cand.content.parts else []):
            if not part.text:
                continue
            if part.thought:
                thought += part.text
                continue
            if thought.strip():
                yield {"type": "thinking", "text": thought.strip()}
                thought = ""
            text += part.text
            yield {"type": "token", "text": part.text}

        gm = cand.grounding_metadata
        if gm:
            grounding = gm
            for q in gm.web_search_queries or []:
                if q not in queries:
                    queries.add(q)
                    yield {"type": "step", "kind": "search", "label": q}
            new = []
            for c in gm.grounding_chunks or []:
                if c.web and c.web.uri and c.web.uri not in sources:
                    src = {"title": c.web.title or c.web.domain or "Source", "url": c.web.uri,
                           "domain": c.web.domain or c.web.title or ""}
                    sources[c.web.uri] = src
                    new.append(src)
            if new:
                yield {"type": "sources", "sources": new}

        ucm = cand.url_context_metadata
        for meta in (ucm.url_metadata if ucm and ucm.url_metadata else []):
            url = meta.retrieved_url
            if url and url not in fetched:
                fetched.add(url)
                yield {"type": "step", "kind": "read", "label": url}
                yield {"type": "fetched", "title": url, "url": url}

    if thought.strip():
        yield {"type": "thinking", "text": thought.strip()}
    usage["searches"], usage["fetches"] = len(queries), len(fetched)

    if finish and str(finish).endswith(("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST")):
        yield {"type": "error", "text": "Gemini declined this request. Try rephrasing it."}
        return
    if finish and str(finish).endswith("MAX_TOKENS"):
        yield {"type": "status", "text": "Report hit the length limit and may be cut short."}

    cited = add_citations(text, grounding)
    if cited != text:
        yield {"type": "report", "text": cited}
    yield {"type": "done", "model": MODEL, "usage": usage}
