"""
Builds a PUBLIC, secret-free snapshot of the bot's state for the web dashboard.

Reads the local bot_state.json + trade_journal.jsonl and writes docs/state.json.
The static dashboard (docs/index.html) fetches that file; live price comes from
Binance's public WebSocket in the browser, so this file never needs API keys.

NOTHING sensitive is included here — no API keys, no secrets. Safe to publish.

Usage:
    python publisher.py                  # write docs/state.json once
    # the bot also calls write_public_state() automatically each cycle
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STATE_FILE = Path("bot_state.json")
JOURNAL_FILE = Path("trade_journal.jsonl")
PUBLIC_FILE = Path("docs/state.json")


def read_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    out = []
    for line in JOURNAL_FILE.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def realized_pnl(journal: list[dict]) -> tuple[float, int, int]:
    """Walk fills to compute realized P&L (USD), wins, losses."""
    pnl, wins, losses = 0.0, 0, 0
    pos_qty, pos_entry = 0.0, 0.0
    for e in journal:
        fill = e.get("fill")
        if not fill:
            continue
        price = float(fill.get("price", 0))
        qty = float(fill.get("base_qty", 0))
        is_open = e.get("action") == "BUY"
        is_close = e.get("action") == "SELL" or e.get("cycle") == "exit"
        if is_open and qty:
            pos_qty, pos_entry = qty, price
        elif is_close and pos_qty:
            trade = pos_qty * (price - pos_entry)
            pnl += trade
            wins += 1 if trade > 0 else 0
            losses += 1 if trade <= 0 else 0
            pos_qty, pos_entry = 0.0, 0.0
    return round(pnl, 2), wins, losses


def build_public_state(symbol: str = "BTCUSDT") -> dict:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    journal = read_journal()

    prices = [e.get("price") for e in journal if e.get("price")]
    last_price = float(prices[-1]) if prices else 0.0

    holding = state.get("holding", False)
    entry = float(state.get("entry_price", 0) or 0)
    base_qty = float(state.get("base_qty", 0) or 0)
    unreal_pct = ((last_price / entry - 1) * 100) if (holding and entry) else 0.0
    unreal_usd = (base_qty * (last_price - entry)) if (holding and entry) else 0.0

    pnl, wins, losses = realized_pnl(journal)
    decisions = [e for e in journal if e.get("cycle") in ("decision", "exit")]
    recent = [
        {
            "ts": e.get("ts", "")[11:19],
            "action": e.get("action") or (e.get("trigger", "").upper()),
            "confidence": e.get("confidence"),
            "price": e.get("price"),
            "blocked": e.get("blocked"),
            "reason": e.get("reason") or e.get("trigger", ""),
        }
        for e in decisions[-25:]
    ][::-1]
    series = [{"ts": e.get("ts", "")[11:16], "price": e.get("price")}
              for e in decisions if e.get("price")][-60:]

    return {
        "symbol": symbol,
        "mode": "TESTNET" if os.getenv("BINANCE_TESTNET", "false").lower() == "true"
                else "LIVE",
        "last_price": last_price,
        "holding": holding,
        "entry": entry,
        "base_qty": base_qty,
        "unreal_pct": round(unreal_pct, 2),
        "unreal_usd": round(unreal_usd, 2),
        "realized_usd": pnl,
        "wins": wins,
        "losses": losses,
        "trades_today": state.get("trades_today", 0),
        "recent": recent,
        "series": series,
        "cycles": len(decisions),
    }


def write_public_state(symbol: str = "BTCUSDT") -> None:
    PUBLIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_FILE.write_text(json.dumps(build_public_state(symbol), indent=2))


if __name__ == "__main__":
    write_public_state()
    print(f"Wrote {PUBLIC_FILE.resolve()}")
