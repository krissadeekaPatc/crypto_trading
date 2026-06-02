"""
Rule-based trading decision — no AI, no API key, no per-cycle cost.

A drop-in replacement for claude_strategy.decide(). Same output shape
(action / confidence / reason) so the bot doesn't care which engine it uses.

Strategy (classic mean-reversion + trend filter):
  BUY  when RSI is oversold (< RSI_BUY) — stronger if the trend is up.
  SELL when RSI is overbought (> RSI_SELL) — only if currently holding.
  HOLD otherwise.

Tune the thresholds below to change behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

RSI_BUY = 35      # buy when RSI drops below this (oversold)
RSI_SELL = 68     # sell when RSI rises above this (overbought)


@dataclass
class Decision:
    action: str        # "BUY" | "SELL" | "HOLD"
    confidence: int    # 0-100
    reason: str


def _confidence(distance: float, span: float) -> int:
    """Map how far past a threshold we are (0..span) to a 50-100 confidence."""
    pct = max(0.0, min(distance / span, 1.0))
    return int(round(50 + pct * 50))


def decide(symbol: str, market: dict, position: dict,
           recent_actions: list | None = None) -> Decision:
    rsi = market.get("rsi_14")
    trend = market.get("trend")
    holding = position.get("holding", False)

    if rsi is None:
        return Decision("HOLD", 0, "Not enough data to compute RSI yet.")

    # --- SELL: overbought while holding -----------------------------------
    if holding and rsi >= RSI_SELL:
        conf = _confidence(rsi - RSI_SELL, 100 - RSI_SELL)
        return Decision(
            "SELL", conf,
            f"RSI {rsi} is overbought (>= {RSI_SELL}); taking profit.",
        )

    # --- BUY: oversold while flat -----------------------------------------
    if not holding and rsi <= RSI_BUY:
        conf = _confidence(RSI_BUY - rsi, RSI_BUY)
        # Uptrend (SMA20 > SMA50) adds conviction; downtrend trims it.
        if trend == "up":
            conf = min(100, conf + 15)
            note = "trend up"
        elif trend == "down":
            conf = max(0, conf - 15)
            note = "trend down (mean-reversion)"
        else:
            note = "trend unclear"
        return Decision(
            "BUY", conf,
            f"RSI {rsi} is oversold (<= {RSI_BUY}), {note}.",
        )

    # --- otherwise hold ---------------------------------------------------
    return Decision(
        "HOLD", 0,
        f"RSI {rsi} is neutral ({RSI_BUY}-{RSI_SELL}); no edge.",
    )
