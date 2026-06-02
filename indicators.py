"""
Lightweight technical indicators computed from Binance klines.

We compute these locally rather than asking Claude to do arithmetic — the model
decides based on the numbers, it doesn't calculate them. Keeps decisions cheap
and deterministic on the data side.
"""

from __future__ import annotations


def closes_from_klines(klines: list) -> list[float]:
    """Binance kline format: [open_time, open, high, low, close, volume, ...]."""
    return [float(k[4]) for k in klines]


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int = 14) -> float | None:
    """Classic Wilder's RSI. Returns 0-100, or None if not enough data."""
    if len(values) < period + 1:
        return None

    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change

    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def snapshot(klines: list) -> dict:
    """Bundle the indicators the strategy looks at into one dict."""
    closes = closes_from_klines(klines)
    last = closes[-1]
    sma_fast = sma(closes, 20)
    sma_slow = sma(closes, 50)
    return {
        "price": last,
        "rsi_14": rsi(closes, 14),
        "sma_20": round(sma_fast, 4) if sma_fast else None,
        "sma_50": round(sma_slow, 4) if sma_slow else None,
        "trend": (
            "up" if sma_fast and sma_slow and sma_fast > sma_slow
            else "down" if sma_fast and sma_slow
            else "unknown"
        ),
        "change_24_candles_pct": round((last / closes[-24] - 1) * 100, 2)
        if len(closes) >= 24 else None,
    }
