"""Research orchestrator: gathers live market data, then streams the chosen model provider."""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from . import market

WALLET_SCAN_QUESTION = (
    "Review the tokens in my wallet. For each meaningful holding, what are the main risks "
    "(liquidity, concentration, recent news, unlocks, security incidents), and what should I watch next?"
)


def provider() -> str | None:
    """'anthropic' or 'gemini', from LLM_PROVIDER or whichever API key is set."""
    choice = os.getenv("LLM_PROVIDER", "auto").lower()
    has_claude = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    if choice == "anthropic":
        return "anthropic" if has_claude else None
    if choice == "gemini":
        return "gemini" if has_gemini else None
    return "anthropic" if has_claude else "gemini" if has_gemini else None


def model_name() -> str | None:
    p = provider()
    if p == "anthropic":
        return os.getenv("CLAUDE_MODEL", "claude-opus-5")
    if p == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return None


async def run_research(
    question: str, depth: str = "quick", *, wallet: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    p = provider()
    yield {"type": "status", "text": f"Scout deployed on {model_name()} ({depth} mode)"}

    holdings = None
    if wallet:
        try:
            holdings = await market.solana_holdings(wallet)
            count = len(holdings["items"])
            yield {"type": "holdings", **holdings}
            yield {"type": "step", "kind": "market",
                   "label": f"Wallet: {count} priced holdings, {market.usd(holdings['totalUsd'])} total"}
        except Exception:  # noqa: BLE001 - research continues without holdings
            yield {"type": "step", "kind": "error", "label": "Couldn't read wallet holdings from Solana RPC"}

    try:
        items = await market.snapshot(question)
    except Exception:  # noqa: BLE001 - research continues without market data
        items = []
    if items:
        yield {"type": "market", "items": items}
        for m in items:
            label = (f"{m['symbol']} {market.usd(m['priceUsd'])} · liquidity {market.usd(m['liquidityUsd'])}"
                     if m["kind"] == "token" else f"{m['name']} TVL {market.usd(m['tvl'])}")
            yield {"type": "step", "kind": "market", "label": label}

    context = market.as_context(items, holdings)
    user_content = f"{question}\n\n{context}" if context else question

    if p == "anthropic":
        from . import claude_agent as impl
    else:
        from . import gemini_agent as impl
    async for event in impl.run(user_content, depth):
        yield event
