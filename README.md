# Xerien Scout

Xerien Scout is a crypto research agent. Ask about any token, protocol or topic: it pulls live market data, plans the research, searches the web, reads the best sources and writes a report with citations and a confidence score. The whole process streams to your screen as it happens. You sign in with a Solana or EVM wallet, and any connected wallet can have its holdings scanned and researched across chains.

Built for the Orion Agents Hackathon.

## Why it's an agent

Scout runs a model-driven loop rather than a fixed pipeline. Claude decides what to search, which pages deserve a full read, and when the evidence is strong enough to stop. Each decision streams to the dashboard as a trace line: search, read, note, done.

Every report has the same structure: TL;DR, Key Findings (each with a citation), Analysis, Risks & Unknowns, a 0–100 confidence score, and three follow-up questions you can click to start a new run.

## What sets it apart

General research agents (ChatGPT, Gemini and Perplexity deep research) start from web search alone. Crypto research tools (Messari Copilot, Nansen) start from their own proprietary data. Scout combines both for free:

- **Live market data comes first.** Tokens (`$SOL`, `ETH`, contract addresses) and protocols (`Jupiter`, `Aave`) named in the question are looked up on DexScreener and DefiLlama *before* the model runs. The model receives the numbers as data and checks them against the news.
- **Multi-chain wallet scan.** The signed-in wallet's holdings are read, priced and researched position by position: Solana through RPC, and Ethereum, Base, Arbitrum, Optimism and Polygon through Blockscout's free public API. Unpriced tokens (usually spam airdrops) are skipped.
- **Any chain for research.** Market lookups cover every chain DexScreener and DefiLlama track, and EVM sign-in works with any EVM wallet.
- **You see how it worked.** Every data pull, search and page read appears in the trace, and the report scores its own confidence.

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
- Model output is rendered with `marked` and sanitized with DOMPurify, and only http(s) links are rendered. The API docs endpoints are turned off.
- Third-party market data is stripped to plain text, length-limited and passed as untrusted data. The model is told never to follow instructions inside it. Addresses are validated before they go into any URL.
- Internal errors are logged on the server and shown to users as a generic message.
- Behind a proxy (Render), the client IP comes from uvicorn's `--proxy-headers`. If you run without a proxy, drop `--forwarded-allow-ips "*"` so clients can't spoof their IP.

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
| `backend/agent.py` | Orchestrator: wallet holdings and market data first, then the chosen provider |
| `backend/market.py` | DexScreener, DefiLlama, Blockscout and Solana RPC lookups, sanitized before they reach the model |
| `backend/claude_agent.py` | Claude provider: server-side web tools, `pause_turn` handling, and a one-time fallback to the standard tool versions |
| `backend/gemini_agent.py` | Gemini provider: Google Search grounding with citations inserted at the grounded sentences |
| `backend/prompts.py` | Shared report format and research instructions |
| `backend/app.py` | API, authentication, rate limits, security headers and page routes |
| `backend/auth.py` | Challenges, signature checks for Solana and EVM, sessions |
| `backend/store.py` | Report storage in SQLite |
| `frontend/` | `index.html` (landing), `app.html` (dashboard), `wallet.js` (wallet discovery and sign-in). No build step |

## Model providers

Set either key. If both are set, Claude is used; `LLM_PROVIDER` overrides that choice.

| | Gemini (free tier) | Claude |
|---|---|---|
| Key | `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey) | `ANTHROPIC_API_KEY` |
| Default model | `gemini-2.5-flash` (on the free tier, only 2.5 models get Google Search) | `claude-opus-5` |
| Web research | Google Search grounding + URL context | `web_search` + `web_fetch` tools |
| Trace | Thinking summaries, then searches and sources | Each search and page read as it happens |
| Limits | Free tier: a few requests per minute, about 500 searched requests a day, and prompts may be used to improve Google products | Paid, per-token |

Gemini's free tier is good for demos and light use. For real traffic, use a paid Gemini key or Claude.

## Run locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...        # or ANTHROPIC_API_KEY=sk-ant-...
uvicorn backend.app:app --reload
# http://localhost:8000
```

With Claude, your Anthropic organization needs **web search** enabled in the Claude Console. With Gemini, nothing extra is needed.

## Deploy (Render)

1. On render.com, choose New → Blueprint and select this repository. `render.yaml` configures the service.
2. When Render asks, set `GEMINI_API_KEY` (free) or `ANTHROPIC_API_KEY`. Leave the other empty.
3. For history that survives redeploys, attach a persistent disk and set `DATA_DIR` to its mount path. On the free plan the disk is wiped on every deploy.

Docker: `docker build -t scout . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... scout`

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | none | Gemini provider (one of the two keys is required) |
| `ANTHROPIC_API_KEY` | none | Claude provider |
| `LLM_PROVIDER` | `auto` | `auto`, `gemini` or `anthropic` |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model id |
| `CLAUDE_MODEL` | `claude-opus-5` | Claude model id |
| `SOLANA_RPC_URL` | public mainnet RPC | Use a dedicated RPC (Helius, Triton, etc.) for reliable wallet scans |
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

SSE event types: `status`, `market`, `holdings`, `thinking`, `tool_pending`, `step`, `sources`, `fetched`, `token`, `report`, `done`, `saved`, `error`.

`POST /api/research` also accepts `"scan_wallet": true`, which researches the signed-in wallet's holdings (Solana, or EVM across Ethereum, Base, Arbitrum, Optimism and Polygon).
