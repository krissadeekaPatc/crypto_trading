"""
24/7 Claude-driven crypto trading bot (Binance spot).

Each cycle:
  1. Fetch market data + compute indicators (RSI, SMA, trend)
  2. Check bot-enforced exits first (stop-loss / take-profit)
  3. Ask Claude for a BUY / SELL / HOLD decision
  4. Apply hard risk guardrails (confidence, daily cap, one position at a time)
  5. Execute on Binance (Testnet by default) and journal everything

Run:
    python trading_bot.py

Safety:
  - Defaults to Testnet (BINANCE_TESTNET=true) with fake funds.
  - DRY_RUN below blocks all real order placement until you flip it.
  - The bot only ever trades ONE_SYMBOL with TRADE_QUOTE_AMOUNT per buy.
Stop it any time with Ctrl-C.
"""

from __future__ import annotations

import json
import math
import os
import signal
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from binance_client import BinanceClient, BinanceError
import rule_strategy
import indicators
import publisher

# anthropic is only needed for engine="claude"; import lazily so the rule-based
# bot runs without the package or an API key.
try:
    import anthropic
    import claude_strategy
except ImportError:
    anthropic = None
    claude_strategy = None

load_dotenv()

# ----- configuration ------------------------------------------------------

@dataclass
class Config:
    engine: str = "rules"            # "rules" (no AI) or "claude" (AI-driven)

    symbol: str = "BTCUSDT"          # the single pair the bot trades
    base_asset: str = "BTC"          # base of the pair (what you buy/sell)
    quote_asset: str = "USDT"        # quote of the pair (what you spend)
    interval: str = "15m"            # candle size for indicators
    cycle_seconds: int = 900         # how often to run a cycle (900s = 15 min)

    trade_quote_amount: float = 50.0  # spend this much quote per BUY
    min_confidence: int = 65          # ignore Claude trades below this
    max_trades_per_day: int = 6       # circuit breaker
    stop_loss_pct: float = 3.0        # force SELL if down this % from entry
    take_profit_pct: float = 5.0      # force SELL if up this % from entry

    dry_run: bool = True              # True = decide + journal, place NO orders


CONFIG = Config()
STATE_FILE = Path("bot_state.json")
JOURNAL_FILE = Path("trade_journal.jsonl")

_running = True


def _stop(*_):
    global _running
    _running = False
    print("\nStopping after this cycle…")


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


# ----- persistent state ---------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "holding": False,
        "base_qty": 0.0,
        "entry_price": 0.0,
        "trades_today": 0,
        "day": _today(),
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def journal(entry: dict) -> None:
    entry["ts"] = _now_iso()
    with JOURNAL_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ----- order helpers ------------------------------------------------------

def round_step(qty: float, step: float) -> float:
    """Round a quantity DOWN to the symbol's lot step size."""
    if step <= 0:
        return qty
    return math.floor(qty / step) * step


def execute_buy(client: BinanceClient, cfg: Config, price: float, step: float) -> dict:
    """Market buy ~trade_quote_amount of quote. Returns fill info."""
    if cfg.dry_run:
        qty = round_step(cfg.trade_quote_amount / price, step)
        return {"dry_run": True, "base_qty": qty, "price": price}
    order = client.create_order(
        symbol=cfg.symbol, side="BUY", type="MARKET",
        quote_order_qty=cfg.trade_quote_amount,
    )
    qty = float(order.get("executedQty", 0)) or round_step(
        cfg.trade_quote_amount / price, step
    )
    return {"order_id": order.get("orderId"), "base_qty": qty, "price": price}


def execute_sell(client: BinanceClient, cfg: Config, qty: float, step: float,
                 price: float) -> dict:
    qty = round_step(qty, step)
    if cfg.dry_run:
        return {"dry_run": True, "base_qty": qty, "price": price}
    order = client.create_order(
        symbol=cfg.symbol, side="SELL", type="MARKET", quantity=qty,
    )
    return {"order_id": order.get("orderId"), "base_qty": qty, "price": price}


# ----- one cycle ----------------------------------------------------------

def run_cycle(client: BinanceClient, claude: anthropic.Anthropic, cfg: Config,
              step: float, state: dict) -> None:
    # Roll the daily trade counter over at UTC midnight.
    if state["day"] != _today():
        state["day"] = _today()
        state["trades_today"] = 0

    klines = client.klines(cfg.symbol, interval=cfg.interval, limit=100)
    market = indicators.snapshot(klines)
    price = market["price"]

    position = {
        "holding": state["holding"],
        "base_qty": state["base_qty"],
        "entry_price": state["entry_price"],
    }
    if state["holding"] and state["entry_price"]:
        position["unrealized_pct"] = round(
            (price / state["entry_price"] - 1) * 100, 2
        )

    # 1. Bot-enforced exits run BEFORE asking Claude — risk management is not
    #    delegated to the model.
    if state["holding"] and state["entry_price"]:
        change = (price / state["entry_price"] - 1) * 100
        forced = None
        if change <= -cfg.stop_loss_pct:
            forced = "stop_loss"
        elif change >= cfg.take_profit_pct:
            forced = "take_profit"
        if forced:
            fill = execute_sell(client, cfg, state["base_qty"], step, price)
            state.update(holding=False, base_qty=0.0, entry_price=0.0)
            state["trades_today"] += 1
            save_state(state)
            journal({"cycle": "exit", "trigger": forced, "price": price,
                     "change_pct": round(change, 2), "fill": fill,
                     "market": market})
            print(f"[{_now_iso()}] {forced.upper()} sell @ {price} ({change:+.2f}%)")
            return

    # 2. Get the directional call from the configured engine.
    if cfg.engine == "claude":
        try:
            decision = claude_strategy.decide(
                claude, cfg.symbol, market, position,
                recent_actions=_recent_journal(),
            )
        except anthropic.APIError as e:
            journal({"cycle": "error", "where": "claude", "error": str(e)})
            print(f"[{_now_iso()}] Claude error: {e}")
            return
    else:
        decision = rule_strategy.decide(cfg.symbol, market, position)

    # 3. Apply hard guardrails.
    action = decision.action
    blocked = None
    if action == "BUY":
        if state["holding"]:
            blocked = "already_holding"
        elif state["trades_today"] >= cfg.max_trades_per_day:
            blocked = "daily_cap"
        elif decision.confidence < cfg.min_confidence:
            blocked = "low_confidence"
    elif action == "SELL":
        if not state["holding"]:
            blocked = "no_position"
        elif decision.confidence < cfg.min_confidence:
            blocked = "low_confidence"

    log = {
        "cycle": "decision", "price": price, "market": market,
        "position": position, "action": action,
        "confidence": decision.confidence, "reason": decision.reason,
        "blocked": blocked, "dry_run": cfg.dry_run,
    }

    # 4. Execute if not blocked / not HOLD.
    if action == "BUY" and not blocked:
        fill = execute_buy(client, cfg, price, step)
        state.update(holding=True, base_qty=fill["base_qty"], entry_price=price)
        state["trades_today"] += 1
        log["fill"] = fill
    elif action == "SELL" and not blocked:
        fill = execute_sell(client, cfg, state["base_qty"], step, price)
        state.update(holding=False, base_qty=0.0, entry_price=0.0)
        state["trades_today"] += 1
        log["fill"] = fill

    save_state(state)
    journal(log)

    flag = f"BLOCKED({blocked})" if blocked else "OK"
    tag = "DRY" if cfg.dry_run else "LIVE"
    print(f"[{_now_iso()}] {tag} {action} conf={decision.confidence} {flag} "
          f"| {price} RSI={market['rsi_14']} trend={market['trend']} "
          f"| {decision.reason}")


def _recent_journal(n: int = 5) -> list[dict]:
    """Last few decisions, so Claude can see what it just did."""
    if not JOURNAL_FILE.exists():
        return []
    lines = JOURNAL_FILE.read_text().splitlines()[-n:]
    out = []
    for line in lines:
        try:
            e = json.loads(line)
            out.append({"ts": e.get("ts"), "action": e.get("action"),
                        "trigger": e.get("trigger"), "price": e.get("price")})
        except json.JSONDecodeError:
            continue
    return out


# ----- main loop ----------------------------------------------------------

def main() -> None:
    cfg = CONFIG
    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    client = BinanceClient(testnet=testnet)

    # Only spin up the Claude client if that engine is selected.
    claude = None
    if cfg.engine == "claude":
        if anthropic is None:
            raise SystemExit("engine='claude' needs the anthropic package: "
                             "pip install anthropic")
        claude = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    # Fail fast on obvious misconfig before the loop starts.
    client.ping()
    if not cfg.dry_run and not client.api_key:
        raise SystemExit("Live trading requires Binance API keys in .env")

    step = client.lot_step_size(cfg.symbol)
    state = load_state()

    mode = "TESTNET" if testnet else "LIVE"
    order_mode = "DRY-RUN (no orders)" if cfg.dry_run else "PLACING ORDERS"
    print(f"Bot starting | {mode} | engine={cfg.engine} | {order_mode}")
    print(f"Pair={cfg.symbol} interval={cfg.interval} every {cfg.cycle_seconds}s | "
          f"{cfg.trade_quote_amount} {cfg.quote_asset}/trade, "
          f"min_conf={cfg.min_confidence}, SL={cfg.stop_loss_pct}% TP={cfg.take_profit_pct}%")
    print("Ctrl-C to stop.\n")

    while _running:
        try:
            run_cycle(client, claude, cfg, step, state)
            publisher.write_public_state(cfg.symbol)  # refresh docs/state.json
        except BinanceError as e:
            journal({"cycle": "error", "where": "binance", "error": str(e)})
            print(f"[{_now_iso()}] Binance error: {e}")
        except Exception as e:  # keep the loop alive on unexpected errors
            journal({"cycle": "error", "where": "loop", "error": repr(e)})
            print(f"[{_now_iso()}] Unexpected error: {e!r}")

        # Sleep in short slices so Ctrl-C is responsive.
        for _ in range(cfg.cycle_seconds):
            if not _running:
                break
            time.sleep(1)

    print("Stopped.")


if __name__ == "__main__":
    main()
