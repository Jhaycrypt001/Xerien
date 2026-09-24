# Xerien Scout

Xerien Scout is an autonomous research agent. You ask a question, and it plans the research, searches the web, reads the best sources in full and writes a report with citations. The whole process streams to your screen as it happens. You sign in with a Solana or EVM wallet, and every report is saved to that wallet's history with a public share link.

Built for the Orion Agents Hackathon.

## Why it's an agent

Scout runs a model-driven loop rather than a fixed pipeline. Claude decides what to search, which pages deserve a full read, and when the evidence is strong enough to stop. Each decision streams to the dashboard as a trace line: search, read, note, done.

Every report has the same structure: TL;DR, Key Findings (each with a citation), Analysis, Risks & Unknowns, a 0–100 confidence score, and three follow-up questions you can click to start a new run.

## Product

- **Landing page** (`/`): "Get started" opens the wallet sign-in. If only one wallet is installed, its popup opens right away.
- **Dashboard** (`/app`): write a question, choose Quick or Deep, and watch the agent trace, sources, confidence and report fill in live. Your history is in the sidebar.
- **Share links** (`/r/{id}`): anyone can open a report read-only. Only the owner can delete it.

## Wallet sign-in

Wallets are found through standard discovery protocols, so any compliant wallet appears automatically:

- **Solana:** [Wallet Standard](https://github.com/wallet-standard/wallet-standard) (Phantom, Solflare, Backpack and others), with fallbacks for wallets that only inject a legacy provider.
- **EVM:** [EIP-6963](https://eips.ethereum.org/EIPS/eip-6963) (MetaMask, Rabby, Coinbase Wallet and others), with a `window.ethereum` fallback.

The flow follows SIWE/SIWS:
1. `POST /api/auth/challenge` returns a message tied to this domain, with a single-use nonce that expires after 5 minutes.
2. The wallet signs that exact message. Signing is free and sends no transaction.
3. `POST /api/auth/verify` checks the signature (Ed25519 for Solana, secp256k1 `personal_sign` recovery for EVM), burns the nonce and sets a session cookie.

## Security

- Sessions are random 256-bit tokens. The database stores only their SHA-256 hash. Cookies are `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS and last 7 days.
- Nonces are single-use, expire after 5 minutes and are deleted before the signature is checked, so a signed message can't be replayed.
- Requests that change state from another origin are rejected. Security headers are set: HSTS, `X-Frame-Options: DENY`, `nosniff`, and a CSP with `frame-ancestors`/`object-src`.
- Research requires sign-in. There is one active run per wallet, per-wallet and per-IP hourly limits, and a global limit on simultaneous runs, so free wallets can't drain your API key.
- Model output is rendered with `marked` and sanitized with DOMPurify. The API docs endpoints are turned off.

## Architecture

```
Browser ──POST /api/research (session cookie)──▶ FastAPI ──stream──▶ Claude API
   ▲                                              │                 web_search + web_fetch
   └──────────── Server-Sent Events ◀─────────────┘                 (server-side tools)
                                                  │
                                               SQLite: reports, sessions, nonces
```

| File | Role |
|---|---|
| `backend/agent.py` | The agent loop: streams Claude, turns each finished content block into a trace event and continues after `pause_turn`. If the org lacks a beta feature, it falls back once to the widely available tool versions |
| `backend/app.py` | API, authentication, rate limits, security headers and page routes |
| `backend/auth.py` | Challenges, signature checks for Solana and EVM, sessions |
| `backend/store.py` | Report storage in SQLite |
| `frontend/` | `index.html` (landing), `app.html` (dashboard), `wallet.js` (wallet discovery and sign-in). No build step |

The model is `claude-opus-5` with adaptive thinking; the trace shows summaries of its reasoning.

## Run locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn backend.app:app --reload
# http://localhost:8000
```

Your Anthropic organization needs **web search** enabled in the Claude Console. Web fetch is recommended too.

## Deploy (Render)

1. On render.com, choose New → Blueprint and select this repository. `render.yaml` configures the service.
2. Set `ANTHROPIC_API_KEY` when Render asks for it.
3. For history that survives redeploys, attach a persistent disk and set `DATA_DIR` to its mount path. On the free plan the disk is wiped on every deploy.

Docker: `docker build -t scout . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... scout`

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Required |
| `CLAUDE_MODEL` | `claude-opus-5` | Model id |
| `CLAUDE_FALLBACKS` | `1` | Server-side refusal fallback (set `0` to turn off) |
| `DATA_DIR` | `./data` | SQLite location |
| `RATE_LIMIT_PER_HOUR` | `20` | Runs per wallet per hour |
| `IP_RATE_LIMIT_PER_HOUR` | `40` | Runs per IP per hour |
| `MAX_CONCURRENT_RUNS` | `8` | Maximum runs at the same time across all users |

## API

| Method | Path | Auth | |
|---|---|---|---|
| POST | `/api/auth/challenge` | none | `{chain, address}` → `{nonce, message}` |
| POST | `/api/auth/verify` | none | `{nonce, signature}` → sets session cookie |
| GET | `/api/auth/me` | session | current account |
| POST | `/api/auth/logout` | session | ends the session |
| POST | `/api/research` | session | `{question, depth}` → SSE stream |
| GET | `/api/reports` | session | your history |
| GET | `/api/reports/{id}` | public | report and trace |
| DELETE | `/api/reports/{id}` | owner | delete |
| GET | `/api/health` | public | `{ok, model, configured}` |

SSE event types: `status`, `thinking`, `tool_pending`, `step`, `sources`, `fetched`, `token`, `done`, `saved`, `error`.
