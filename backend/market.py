"""Live market data that grounds research in real numbers before the model runs.

- Tokens: $TICKERs, bare tickers and contract addresses in the question -> DexScreener
- Protocols: protocol names in the question -> DefiLlama TVL
- Wallet scan: Solana holdings via JSON-RPC; EVM holdings on Ethereum, Base, Arbitrum,
  Optimism and Polygon via Blockscout; liquidity from DexScreener

Every lookup is best-effort: a failing source is skipped, never fatal. All values that
reach the model are sanitized and wrapped as untrusted data.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .auth import b58decode

DEXSCREENER = "https://api.dexscreener.com"
DEFILLAMA = "https://api.llama.fi/protocols"
SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
WSOL = "So11111111111111111111111111111111111111112"
# chain id (DexScreener naming) -> (Blockscout instance, native symbol)
EVM_CHAINS = {
    "ethereum": ("https://eth.blockscout.com", "ETH"),
    "base": ("https://base.blockscout.com", "ETH"),
    "arbitrum": ("https://arbitrum.blockscout.com", "ETH"),
    "optimism": ("https://optimism.blockscout.com", "ETH"),
    "polygon": ("https://polygon.blockscout.com", "POL"),
}

TIMEOUT = httpx.Timeout(6.0, connect=4.0)
MAX_TOKENS = 5
MAX_PROTOCOLS = 4

_EVM_ADDR = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOL_ADDR = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_BASE58_ID = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_DOLLAR_TICKER = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
_BARE_TICKER = re.compile(r"\b([A-Z][A-Z0-9]{1,5})\b")
# Uppercase words that are almost never the token a user means.
_NOT_TICKERS = {
    "AI", "API", "US", "USA", "UK", "EU", "UN", "CEO", "CTO", "CFO", "IPO", "ETF", "ETFS", "TVL", "DEFI", "NFT",
    "NFTS", "DAO", "DEX", "CEX", "L1", "L2", "APY", "APR", "ROI", "FDV", "ATH", "ATL", "KYC", "AML", "SEC",
    "CFTC", "FED", "GDP", "CPI", "USD", "EUR", "GBP", "JPY", "OK", "FAQ", "PDF", "Q1", "Q2", "Q3", "Q4", "VS",
    "IS", "IT", "OR", "AND", "THE", "WHAT", "HOW", "WHY", "WHO", "RWA", "MEV", "LLM", "GPT", "TLDR", "DD",
}

_protocol_cache: dict[str, Any] = {"at": 0.0, "items": []}


def _clean(value: Any, limit: int = 48) -> str:
    """Strip anything that could smuggle markup or instructions into the prompt."""
    s = re.sub(r"[^\w\s.,:%$/()&+'-]", "", str(value or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:.2f}{unit}"
    return f"${v:.6g}" if abs(v) < 1 else f"${v:,.2f}"


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def _is_solana_address(s: str) -> bool:
    try:
        return len(b58decode(s)) == 32
    except Exception:  # noqa: BLE001
        return False


def extract_targets(question: str) -> tuple[list[str], list[str]]:
    """Return (contract addresses, tickers) mentioned in the question."""
    addresses = _EVM_ADDR.findall(question)
    addresses += [a for a in _SOL_ADDR.findall(question) if _is_solana_address(a)]
    tickers = [t.upper() for t in _DOLLAR_TICKER.findall(question)]
    tickers += [t for t in _BARE_TICKER.findall(question) if t not in _NOT_TICKERS and not t.isdigit()]
    seen: set[str] = set()
    tickers = [t for t in tickers if not (t in seen or seen.add(t))]
    return list(dict.fromkeys(addresses))[:MAX_TOKENS], tickers[:MAX_TOKENS]


def _best_pair(pairs: list[dict[str, Any]], symbol: str | None = None, address: str | None = None) -> dict | None:
    def matches(p: dict[str, Any]) -> bool:
        base = p.get("baseToken") or {}
        if address:
            return (base.get("address") or "").lower() == address.lower()
        sym = (base.get("symbol") or "").upper()
        return sym in (symbol, f"W{symbol}")

    candidates = [p for p in pairs if matches(p)]
    return max(candidates, key=lambda p: _num((p.get("liquidity") or {}).get("usd")) or 0, default=None)


def _token_item(pair: dict[str, Any]) -> dict[str, Any]:
    base = pair.get("baseToken") or {}
    created = _num(pair.get("pairCreatedAt"))
    return {
        "kind": "token",
        "symbol": _clean(base.get("symbol"), 16),
        "name": _clean(base.get("name")),
        "chain": _clean(pair.get("chainId"), 20),
        "address": _clean(base.get("address"), 64),
        "priceUsd": _num(pair.get("priceUsd")),
        "change24h": _num((pair.get("priceChange") or {}).get("h24")),
        "liquidityUsd": _num((pair.get("liquidity") or {}).get("usd")),
        "volume24h": _num((pair.get("volume") or {}).get("h24")),
        "fdv": _num(pair.get("fdv")),
        "pairAgeDays": round((time.time() * 1000 - created) / 86_400_000) if created else None,
        "url": pair.get("url") if str(pair.get("url", "")).startswith("https://dexscreener.com/") else None,
    }


async def _search_ticker(client: httpx.AsyncClient, ticker: str) -> dict | None:
    r = await client.get(f"{DEXSCREENER}/latest/dex/search", params={"q": ticker})
    r.raise_for_status()
    pair = _best_pair(r.json().get("pairs") or [], symbol=ticker)
    return _token_item(pair) if pair else None


async def _lookup_address(client: httpx.AsyncClient, address: str) -> dict | None:
    r = await client.get(f"{DEXSCREENER}/latest/dex/tokens/{address}")
    r.raise_for_status()
    pair = _best_pair(r.json().get("pairs") or [], address=address)
    return _token_item(pair) if pair else None


async def _protocols(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    if time.time() - _protocol_cache["at"] < 3600 and _protocol_cache["items"]:
        return _protocol_cache["items"]
    r = await client.get(DEFILLAMA)
    r.raise_for_status()
    items = [p for p in r.json() if (_num(p.get("tvl")) or 0) >= 10_000_000 and len(p.get("name") or "") >= 4]
    _protocol_cache.update(at=time.time(), items=items)
    return items


async def _match_protocols(client: httpx.AsyncClient, question: str) -> list[dict[str, Any]]:
    # Case-sensitive on purpose: "Across" is a protocol, "across" is a word.
    out = []
    for p in await _protocols(client):
        name = p["name"]
        if name in question and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", question):
            out.append({
                "kind": "protocol",
                "name": _clean(name),
                "category": _clean(p.get("category"), 24),
                "tvl": _num(p.get("tvl")),
                "change1d": _num(p.get("change_1d")),
                "change7d": _num(p.get("change_7d")),
                "chains": [_clean(c, 20) for c in (p.get("chains") or [])[:4]],
                "url": f"https://defillama.com/protocol/{_clean(p.get('slug'), 64)}" if p.get("slug") else None,
            })
    out.sort(key=lambda x: x["tvl"] or 0, reverse=True)
    return out[:MAX_PROTOCOLS]


async def snapshot(question: str) -> list[dict[str, Any]]:
    addresses, tickers = extract_targets(question)
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "XerienScout/1.0"}) as client:
        jobs = [_lookup_address(client, a) for a in addresses] + [_search_ticker(client, t) for t in tickers]
        jobs.append(_match_protocols(client, question))
        results = await asyncio.gather(*jobs, return_exceptions=True)

    items: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, list):
            items.extend(res)
        elif isinstance(res, dict):
            items.append(res)
    seen: set[str] = set()
    return [i for i in items if not ((key := f"{i['kind']}:{i.get('address') or i['name']}") in seen or seen.add(key))]


async def wallet_holdings(chain: str, address: str) -> dict[str, Any]:
    if chain == "solana":
        return await solana_holdings(address)
    if chain == "ethereum" and _EVM_ADDR.fullmatch(address):
        return await evm_holdings(address)
    raise ValueError("Unsupported wallet")


async def solana_holdings(owner: str) -> dict[str, Any]:
    """Priced token holdings of a Solana wallet, largest first."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "XerienScout/1.0"}) as client:
        async def rpc(method: str, params: list[Any]) -> Any:
            r = await client.post(SOLANA_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                raise RuntimeError(body["error"].get("message", "RPC error"))
            return body["result"]

        balance, *accounts = await asyncio.gather(
            rpc("getBalance", [owner]),
            *[rpc("getTokenAccountsByOwner", [owner, {"programId": p}, {"encoding": "jsonParsed"}]) for p in TOKEN_PROGRAMS],
        )
        amounts: dict[str, float] = {WSOL: (balance.get("value") or 0) / 1e9}
        for result in accounts:
            for acc in result.get("value") or []:
                info = acc["account"]["data"]["parsed"]["info"]
                ui = _num(info["tokenAmount"].get("uiAmount")) or 0
                if ui > 0:
                    amounts[info["mint"]] = amounts.get(info["mint"], 0) + ui

        mints = [m for m in amounts if _BASE58_ID.fullmatch(m)][:120]
        pairs: list[dict[str, Any]] = []
        for i in range(0, len(mints), 30):  # DexScreener accepts 30 addresses per call
            r = await client.get(f"{DEXSCREENER}/tokens/v1/solana/{','.join(mints[i:i + 30])}")
            if r.status_code == 200 and isinstance(r.json(), list):
                pairs.extend(r.json())

    holdings = []
    for mint, amount in amounts.items():
        pair = _best_pair(pairs, address=mint)
        if not pair:
            continue
        item = _token_item(pair)
        value = (item["priceUsd"] or 0) * amount
        if value >= 1:
            holdings.append({**item, "kind": "holding", "amount": amount, "valueUsd": value,
                             "symbol": "SOL" if mint == WSOL else item["symbol"]})
    holdings.sort(key=lambda h: h["valueUsd"], reverse=True)
    return {"items": holdings[:12], "totalUsd": sum(h["valueUsd"] for h in holdings),
            "chains": ["solana"], "source": "Solana RPC + DexScreener"}


async def _evm_chain(client: httpx.AsyncClient, chain: str, address: str) -> list[dict[str, Any]]:
    base, native = EVM_CHAINS[chain]
    info, tokens = await asyncio.gather(
        client.get(f"{base}/api/v2/addresses/{address}"),
        client.get(f"{base}/api/v2/addresses/{address}/tokens", params={"type": "ERC-20"}),
    )
    out: list[dict[str, Any]] = []
    if info.status_code == 200:
        body = info.json()
        amount = int(body.get("coin_balance") or 0) / 1e18
        price = _num(body.get("exchange_rate"))
        if amount and price:
            out.append({"symbol": native, "name": "native", "address": None, "amount": amount, "price": price})
    if tokens.status_code == 200:
        for it in tokens.json().get("items") or []:
            tok = it.get("token") or {}
            price = _num(tok.get("exchange_rate"))  # unpriced tokens are usually spam airdrops
            addr = tok.get("address_hash") or tok.get("address") or ""
            if not price or not _EVM_ADDR.fullmatch(addr):
                continue
            try:
                amount = int(it.get("value") or 0) / 10 ** int(tok.get("decimals") or 18)
            except (TypeError, ValueError):
                continue
            out.append({"symbol": _clean(tok.get("symbol"), 16), "name": _clean(tok.get("name")),
                        "address": addr, "amount": amount, "price": price})
    return [{**h, "chain": chain} for h in out if h["amount"] * h["price"] >= 1]


async def evm_holdings(address: str) -> dict[str, Any]:
    """Priced holdings of an EVM wallet across the chains in EVM_CHAINS, largest first."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "XerienScout/1.0"}) as client:
        per_chain = await asyncio.gather(*[_evm_chain(client, c, address) for c in EVM_CHAINS], return_exceptions=True)
        raw = [h for res in per_chain if isinstance(res, list) for h in res]
        if not raw and all(isinstance(r, Exception) for r in per_chain):
            raise RuntimeError("No chain explorer responded")

        # Add liquidity and 24h change from DexScreener for ERC-20s (30 addresses per call).
        pairs: list[dict[str, Any]] = []
        for chain in EVM_CHAINS:
            addrs = [h["address"] for h in raw if h["chain"] == chain and h["address"]][:30]
            if addrs:
                try:
                    r = await client.get(f"{DEXSCREENER}/tokens/v1/{chain}/{','.join(addrs)}")
                    if r.status_code == 200 and isinstance(r.json(), list):
                        pairs.extend(r.json())
                except httpx.HTTPError:
                    pass

    holdings = []
    for h in raw:
        pair = _best_pair([p for p in pairs if p.get("chainId") == h["chain"]], address=h["address"]) if h["address"] else None
        item = _token_item(pair) if pair else {}
        holdings.append({
            "kind": "holding", "symbol": h["symbol"], "name": h["name"], "chain": h["chain"],
            "address": h["address"], "amount": h["amount"], "valueUsd": h["amount"] * h["price"],
            "priceUsd": h["price"], "liquidityUsd": item.get("liquidityUsd"), "change24h": item.get("change24h"),
            "url": item.get("url"),
        })
    holdings.sort(key=lambda x: x["valueUsd"], reverse=True)
    return {"items": holdings[:15], "totalUsd": sum(x["valueUsd"] for x in holdings),
            "chains": sorted({x["chain"] for x in holdings}), "source": "Blockscout + DexScreener"}


def as_context(market: list[dict[str, Any]], holdings: dict[str, Any] | None) -> str:
    """Render data blocks for the model. Every string was sanitized by _clean."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks = []
    if market:
        lines = []
        for m in market:
            if m["kind"] == "token":
                lines.append(
                    f"- TOKEN {m['symbol']} ({m['name']}) on {m['chain']}: price {usd(m['priceUsd'])}, "
                    f"24h change {pct(m['change24h'])}, "
                    f"liquidity {usd(m['liquidityUsd'])}, 24h volume {usd(m['volume24h'])}, FDV {usd(m['fdv'])}, "
                    f"top pair age {m['pairAgeDays'] if m['pairAgeDays'] is not None else 'n/a'} days. Source: DexScreener"
                )
            else:
                lines.append(
                    f"- PROTOCOL {m['name']} ({m['category']}, chains: {', '.join(m['chains'])}): TVL {usd(m['tvl'])}, "
                    f"1d change {pct(m['change1d'])}, "
                    f"7d change {pct(m['change7d'])}. Source: DefiLlama"
                )
        blocks.append(f'<market_data retrieved="{stamp}">\n' + "\n".join(lines) + "\n</market_data>")
    if holdings and holdings.get("items"):
        lines = [
            f"- {h['symbol']} ({h['name']}) on {h.get('chain', 'solana')}: {h['amount']:.6g} tokens worth {usd(h['valueUsd'])}, "
            f"liquidity {usd(h['liquidityUsd'])}, 24h change {pct(h['change24h'])}"
            for h in holdings["items"]
        ]
        blocks.append(
            f'<wallet_holdings retrieved="{stamp}" total="{usd(holdings["totalUsd"])}" '
            f'source="{holdings.get("source", "on-chain data")}">\n' + "\n".join(lines) + "\n</wallet_holdings>"
        )
    return "\n\n".join(blocks)
