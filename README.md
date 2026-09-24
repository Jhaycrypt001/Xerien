# ✦ Xerien Scout: an AI research agent

> **Ask anything. Watch it think.**
> Xerien Scout is an AI research agent. It plans its own research, searches the web, reads the best sources in full and writes a structured report with citations. You can watch every step live as it happens.

Built for the **Orion Agents Hackathon**.

## Why it's an agent, not a chatbot

Scout runs on a **model-driven loop**, not a fixed pipeline. Claude decides:

- **what to search**: it writes several queries from different angles,
- **what to read**: it opens full pages with `web_fetch` when search snippets aren't enough,
- **when to stop**: it keeps going until the evidence is strong enough, within a set search and read budget.

Each decision streams to the UI as it happens, so the agent's reasoning is visible rather than hidden.

## Features

| | |
|---|---|
| 🧠 **Live agent trace** | Plan → search → read → reasoning notes, with icons and a running timer |
| 🔗 **Source panel** | Every result it found, with the pages it actually read marked **READ** |
| 📑 **Structured report** | TL;DR, Key Findings (with citations), Analysis, Risks & Unknowns, Confidence, Follow-ups |
| 🎯 **Confidence gauge** | The agent scores its own confidence from 0 to 100 and says why |
| ↳ **Follow-up chips** | Click a suggested follow-up question to start a new research run |
| ⚡/🔬 **Quick vs Deep** | Deep mode raises the effort level and the search and read budgets |
| 📋 **Export** | Copy the report or download it as Markdown |
| 🗂️ **History & share links** | Every report is saved (SQLite). Your history is kept per browser workspace, and each report has a public `/r/{id}` link |
| 🛡️ **Rate limiting** | Runs are limited per IP (`RATE_LIMIT_PER_HOUR`, default 20) to protect the API key |

## Architecture

```
Browser (vanilla JS)  ──POST /api/research──▶  FastAPI  ──stream──▶  Claude API
       ▲                                         │                  (web_search + web_fetch
       └──────── Server-Sent Events ◀────────────┘                   server tools, adaptive
             status · thinking · tool steps ·                        thinking)
             sources · report tokens · done
```

- **`backend/agent.py`**: the agent loop. It streams Claude's response, turns each finished part of the answer (a tool call, a search result, a fetched page, a thinking summary) into a UI event, and continues when the server-side tool loop pauses (`pause_turn`).
- **`backend/app.py`**: the FastAPI server. It exposes an SSE endpoint, reports errors to the page and serves the frontend.
- **`backend/store.py`**: SQLite report storage. The database lives in `DATA_DIR`, default `./data`.
- **`frontend/`**: `index.html` is the landing page and `app.html` is the dashboard (routes `/app` and `/r/{id}`). The design is monochrome with hairline borders in Geist and Geist Mono. There is no build step. Markdown is rendered with `marked` and sanitized with `DOMPurify`, and both are included in the repo.

Model: `claude-opus-5` by default (override it with `CLAUDE_MODEL`), with adaptive thinking and summarized reasoning shown in the trace. Server-side refusal fallbacks are on; set `CLAUDE_FALLBACKS=0` to turn them off.

## Run locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn backend.app:app --reload
# landing: http://localhost:8000   dashboard: http://localhost:8000/app
```

Your Anthropic organization needs **web search** (and **web fetch**) enabled in the Claude Console.

## Deploy

- **Render**: connect the repo. `render.yaml` sets everything up; just add `ANTHROPIC_API_KEY`. On the free plan the disk is wiped on every redeploy, so history is lost. To keep it, attach a persistent disk and point `DATA_DIR` at it.
- **Railway / Heroku-style**: uses the `Procfile`.
- **Docker**: `docker build -t scout . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... scout`

## API

`POST /api/research` with `{"question": "...", "depth": "quick" | "deep"}` returns a `text/event-stream` of JSON events:

| event `type` | payload |
|---|---|
| `status` | `text` |
| `thinking` | `text`: a summary of the model's reasoning |
| `tool_pending` | `tool`: `web_search` / `web_fetch` |
| `step` | `kind` (`search`/`read`/`analyze`/`error`), `label` |
| `sources` | `sources: [{title, url}]` |
| `fetched` | `title`, `url` |
| `token` | `text`: streamed report and narration text |
| `done` | `model`, `usage: {input, output, searches, fetches}` |
| `saved` | `id`: the report's share id |
| `error` | `text` |

All endpoints except `/api/health` and `GET /api/reports/{id}` need an `X-Workspace` header, which the dashboard creates per browser.

- `GET /api/health` → `{ok, model, configured}`
- `GET /api/reports` → the workspace's history
- `GET /api/reports/{id}` → the full report and trace (public)
- `DELETE /api/reports/{id}` → only the report's owner can delete it
