"""
Claude-driven trading decision.

Each cycle we hand Claude a market snapshot + current position and it returns a
structured BUY / SELL / HOLD decision with a confidence and a one-line reason.
The bot (not Claude) then enforces the hard risk limits before acting.

Uses the Anthropic SDK with structured outputs so the response is always a valid,
parseable decision. Model defaults to claude-opus-4-8 with adaptive thinking.
"""

from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-4-8"

# Stable across every cycle → good cache prefix. The volatile market data goes in
# the user message, never here.
SYSTEM_PROMPT = """You are a disciplined crypto spot-trading analyst for a single \
trading pair. Each turn you receive a market snapshot (price, RSI, moving averages, \
trend) and the bot's current position, and you decide a single action.

Rules you must follow:
- Output exactly one action: BUY, SELL, or HOLD.
- Prefer HOLD when the signal is unclear. Do not overtrade.
- Only suggest BUY when there is a genuine edge (e.g. oversold RSI < 35 in an \
uptrend, or a fresh bullish SMA crossover).
- Only suggest SELL when you currently hold the asset and momentum is fading \
(e.g. RSI > 70 overbought, or trend turning down). Never SELL if the position is flat.
- confidence is 0-100: how strong the edge is. The bot ignores any trade below its \
configured threshold, so be honest.
- reason must be one short sentence citing the specific numbers that drove the call.

You are NOT a financial advisor and you do not manage risk sizing — the bot enforces \
position limits, stop-losses, and daily caps. Focus only on the directional call."""


class TradeDecision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: int  # 0-100
    reason: str


def decide(
    client: anthropic.Anthropic,
    symbol: str,
    market: dict,
    position: dict,
    recent_actions: list[dict] | None = None,
) -> TradeDecision:
    """Ask Claude for a decision. Returns a validated TradeDecision."""
    payload = {
        "symbol": symbol,
        "market": market,
        "position": position,  # {"holding": bool, "base_qty": .., "entry_price": ..}
        "recent_actions": recent_actions or [],
    }

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Here is the current state. Decide the action.\n\n"
                    + json.dumps(payload, indent=2)
                ),
            }
        ],
        output_format=TradeDecision,
    )
    return response.parsed_output
