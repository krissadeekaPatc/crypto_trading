"""
Backtester for the rule-based strategy.

Replays historical Binance candles through the SAME logic the live bot uses
(rule_strategy + stop-loss / take-profit / confidence guardrails) and reports
win rate, P&L, and how it compares to simply buying and holding.

No API key needed — it uses public market data.

Run:
    python backtest.py
    python backtest.py --symbol ETHUSDT --interval 1h --candles 2000 --min-confidence 45
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from binance_client import BinanceClient
import indicators
import rule_strategy

# Binance allows max 1000 candles per request; we page to get more.
PAGE = 1000


def fetch_history(client: BinanceClient, symbol: str, interval: str,
                  want: int) -> list:
    """Fetch `want` most-recent candles, paging backwards."""
    collected: list = []
    end_time = None
    while len(collected) < want:
        batch = client.klines(symbol, interval=interval, limit=PAGE,
                              end_time=end_time)
        if not batch:
            break
        collected = batch + collected
        end_time = batch[0][0] - 1     # just before the earliest candle we have
        if len(batch) < PAGE:
            break
    return collected[-want:]


@dataclass
class Sim:
    """One backtest run's accounting."""
    cash: float
    fee_pct: float
    qty: float = 0.0
    entry: float = 0.0
    holding: bool = False
    trades: list = field(default_factory=list)   # closed round-trips
    equity_curve: list = field(default_factory=list)   # (ts_ms, equity, price)

    def buy(self, price: float, quote: float) -> None:
        spend = min(quote, self.cash)
        if spend <= 0:
            return
        fee = spend * self.fee_pct
        self.qty = (spend - fee) / price
        self.cash -= spend
        self.entry = price
        self.holding = True

    def sell(self, price: float, reason: str) -> None:
        if not self.holding:
            return
        proceeds = self.qty * price
        fee = proceeds * self.fee_pct
        self.cash += proceeds - fee
        pnl_pct = (price / self.entry - 1) * 100
        self.trades.append({"entry": self.entry, "exit": price,
                            "pnl_pct": round(pnl_pct, 2), "reason": reason})
        self.qty = 0.0
        self.entry = 0.0
        self.holding = False

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price


@dataclass
class Params:
    trade_quote_amount: float = 50.0
    min_confidence: int = 45
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 5.0
    fee_pct: float = 0.001          # 0.1% per side (Binance spot taker)
    initial_cash: float = 1000.0


def run(klines: list, p: Params) -> Sim:
    sim = Sim(cash=p.initial_cash, fee_pct=p.fee_pct)
    warmup = 50  # need 50 closes for SMA50

    for i in range(warmup, len(klines)):
        window = klines[: i + 1]
        candle = klines[i]
        high, low, close = float(candle[2]), float(candle[3]), float(candle[4])

        # 1. Risk exits first — check intrabar high/low against entry.
        if sim.holding:
            sl_price = sim.entry * (1 - p.stop_loss_pct / 100)
            tp_price = sim.entry * (1 + p.take_profit_pct / 100)
            if low <= sl_price:                 # assume SL hits first (conservative)
                sim.sell(sl_price, "stop_loss")
                sim.equity_curve.append((candle[0], sim.equity(close), close))
                continue
            if high >= tp_price:
                sim.sell(tp_price, "take_profit")
                sim.equity_curve.append((candle[0], sim.equity(close), close))
                continue

        # 2. Strategy decision (same code the live bot calls).
        market = indicators.snapshot(window)
        decision = rule_strategy.decide(
            "BACKTEST", market, {"holding": sim.holding}
        )

        # 3. Guardrails + execute at close.
        if (decision.action == "BUY" and not sim.holding
                and decision.confidence >= p.min_confidence):
            sim.buy(close, p.trade_quote_amount)
        elif (decision.action == "SELL" and sim.holding
                and decision.confidence >= p.min_confidence):
            sim.sell(close, "signal")

        sim.equity_curve.append((candle[0], sim.equity(close), close))

    # Close any open position at the last price (mark-to-market).
    if sim.holding:
        sim.sell(float(klines[-1][4]), "end_of_test")
    return sim


def max_drawdown(equity_values: list[float]) -> float:
    peak = equity_values[0] if equity_values else 0
    mdd = 0.0
    for v in equity_values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return round(mdd * 100, 2)


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def report(klines: list, sim: Sim, p: Params, symbol: str, interval: str) -> None:
    first, last = klines[0], klines[-1]
    start_px, end_px = float(first[4]), float(last[4])
    final_eq = sim.equity(end_px)

    wins = [t for t in sim.trades if t["pnl_pct"] > 0]
    losses = [t for t in sim.trades if t["pnl_pct"] <= 0]
    n = len(sim.trades)
    win_rate = (len(wins) / n * 100) if n else 0.0

    strat_ret = (final_eq / p.initial_cash - 1) * 100
    hold_ret = (end_px / start_px - 1) * 100

    reasons: dict[str, int] = {}
    for t in sim.trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    print("=" * 60)
    print(f" BACKTEST — {symbol} {interval} (rules engine)")
    print("=" * 60)
    print(f" Period      : {_fmt_ts(first[0])} → {_fmt_ts(last[0])} "
          f"({len(klines)} candles)")
    print(f" Settings    : conf>={p.min_confidence}  SL={p.stop_loss_pct}%  "
          f"TP={p.take_profit_pct}%  fee={p.fee_pct*100:.2f}%/side  "
          f"${p.trade_quote_amount}/trade")
    print("-" * 60)
    print(f" Trades      : {n}   (wins {len(wins)} / losses {len(losses)})")
    print(f" Win rate    : {win_rate:.1f}%")
    if wins:
        print(f" Avg win     : +{sum(t['pnl_pct'] for t in wins)/len(wins):.2f}%")
    if losses:
        print(f" Avg loss    : {sum(t['pnl_pct'] for t in losses)/len(losses):.2f}%")
    print(f" Exit reasons: " + ", ".join(f"{k}={v}" for k, v in reasons.items())
          if reasons else " Exit reasons: none")
    print("-" * 60)
    print(f" Start cash  : ${p.initial_cash:,.2f}")
    print(f" Final equity: ${final_eq:,.2f}")
    print(f" Strategy ret: {strat_ret:+.2f}%")
    print(f" Buy & hold  : {hold_ret:+.2f}%   <-- benchmark")
    print(f" Max drawdown: {max_drawdown([e for _, e, _ in sim.equity_curve]):.2f}%")
    print("=" * 60)
    verdict = "BEAT" if strat_ret > hold_ret else "LOST TO"
    print(f" Verdict: strategy {verdict} buy & hold "
          f"by {abs(strat_ret - hold_ret):.2f} pts.")
    if n == 0:
        print(" (No trades — try a lower --min-confidence or longer history.)")
    print("=" * 60)


def _svg_chart(curve: list, initial_cash: float, start_px: float,
               width: int = 860, height: int = 280) -> str:
    """Inline SVG: strategy equity vs buy & hold, both normalised to start cash."""
    if len(curve) < 2:
        return "<p>Not enough data to chart.</p>"
    pad = 36
    strat = [e for _, e, _ in curve]
    hold = [initial_cash * (px / start_px) for _, _, px in curve]
    lo = min(min(strat), min(hold))
    hi = max(max(strat), max(hold))
    span = (hi - lo) or 1
    n = len(curve)

    def pts(series: list[float]) -> str:
        out = []
        for i, v in enumerate(series):
            x = pad + (width - 2 * pad) * i / (n - 1)
            y = height - pad - (height - 2 * pad) * (v - lo) / span
            out.append(f"{x:.1f},{y:.1f}")
        return " ".join(out)

    base_y = height - pad - (height - 2 * pad) * (initial_cash - lo) / span
    return f"""<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMidYMid meet">
  <line x1="{pad}" y1="{base_y:.1f}" x2="{width-pad}" y2="{base_y:.1f}"
        stroke="#2a3343" stroke-dasharray="4 4"/>
  <text x="{pad}" y="{base_y-6:.1f}" fill="#9aa7b8" font-size="11">start ${initial_cash:,.0f}</text>
  <polyline fill="none" stroke="#58a6ff" stroke-width="2" points="{pts(hold)}"/>
  <polyline fill="none" stroke="#f0b90b" stroke-width="2.5" points="{pts(strat)}"/>
  <text x="{width-pad}" y="16" fill="#f0b90b" font-size="12" text-anchor="end">strategy</text>
  <text x="{width-pad}" y="32" fill="#58a6ff" font-size="12" text-anchor="end">buy &amp; hold</text>
</svg>"""


def build_html(klines: list, sim: Sim, p: Params, symbol: str,
               interval: str) -> str:
    first, last = klines[0], klines[-1]
    start_px, end_px = float(first[4]), float(last[4])
    final_eq = sim.equity(end_px)
    wins = [t for t in sim.trades if t["pnl_pct"] > 0]
    losses = [t for t in sim.trades if t["pnl_pct"] <= 0]
    n = len(sim.trades)
    win_rate = (len(wins) / n * 100) if n else 0.0
    strat_ret = (final_eq / p.initial_cash - 1) * 100
    hold_ret = (end_px / start_px - 1) * 100
    mdd = max_drawdown([e for _, e, _ in sim.equity_curve])
    avg_win = (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0
    avg_loss = (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0

    def cls(v):
        return "pos" if v >= 0 else "neg"

    rows = "".join(
        f"<tr><td>{i+1}</td><td>{t['entry']:.2f}</td><td>{t['exit']:.2f}</td>"
        f"<td class='{cls(t['pnl_pct'])}'>{t['pnl_pct']:+.2f}%</td>"
        f"<td>{t['reason']}</td></tr>"
        for i, t in enumerate(sim.trades)
    ) or "<tr><td colspan='5'>No trades</td></tr>"

    def stat(label, value, klass=""):
        return (f"<div class='stat'><div class='lbl'>{label}</div>"
                f"<div class='val {klass}'>{value}</div></div>")

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest — {symbol} {interval}</title>
<style>
  body{{margin:0;background:#0e1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.6}}
  .wrap{{max-width:920px;margin:0 auto;padding:28px 24px 60px}}
  h1{{font-size:1.6rem;margin:0 0 2px}} .sub{{color:#9aa7b8;margin:0 0 20px;font-family:ui-monospace,monospace;font-size:.85rem}}
  .card{{background:#161b22;border:1px solid #2a3343;border-radius:12px;padding:18px 20px;margin:16px 0}}
  .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}}
  .stat{{background:#1c2230;border:1px solid #2a3343;border-radius:10px;padding:12px 14px}}
  .lbl{{color:#9aa7b8;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}}
  .val{{font-size:1.35rem;font-weight:700;font-family:ui-monospace,monospace}}
  .pos{{color:#3fb950}} .neg{{color:#f85149}} .accent{{color:#f0b90b}}
  table{{width:100%;border-collapse:collapse;font-size:.9rem;font-family:ui-monospace,monospace}}
  th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #2a3343}}
  th{{color:#9aa7b8;font-size:.75rem;text-transform:uppercase}}
  .verdict{{font-size:1.05rem;padding:12px 16px;border-radius:10px;margin-top:8px}}
  .beat{{background:rgba(63,185,80,.1);border:1px solid rgba(63,185,80,.4)}}
  .lost{{background:rgba(248,81,73,.1);border:1px solid rgba(248,81,73,.4)}}
</style></head><body><div class="wrap">
  <h1>📊 Backtest Report — {symbol} <span class="accent">{interval}</span></h1>
  <p class="sub">{_fmt_ts(first[0])} → {_fmt_ts(last[0])} · {len(klines)} candles ·
     conf≥{p.min_confidence} · SL {p.stop_loss_pct}% / TP {p.take_profit_pct}% ·
     fee {p.fee_pct*100:.2f}%/side · ${p.trade_quote_amount}/trade</p>

  <div class="card"><div class="stats">
    {stat("Strategy return", f"{strat_ret:+.2f}%", cls(strat_ret))}
    {stat("Buy &amp; hold", f"{hold_ret:+.2f}%", cls(hold_ret))}
    {stat("Win rate", f"{win_rate:.1f}%")}
    {stat("Trades", str(n))}
    {stat("Avg win", f"+{avg_win:.2f}%", "pos")}
    {stat("Avg loss", f"{avg_loss:.2f}%", "neg")}
    {stat("Max drawdown", f"{mdd:.2f}%", "neg")}
    {stat("Final equity", f"${final_eq:,.2f}")}
  </div></div>

  <div class="card">
    <h3 style="margin:0 0 8px">Equity curve vs buy &amp; hold</h3>
    {_svg_chart(sim.equity_curve, p.initial_cash, start_px)}
  </div>

  <div class="verdict {'beat' if strat_ret > hold_ret else 'lost'}">
    {'✅' if strat_ret > hold_ret else '❌'} Strategy
    {'beat' if strat_ret > hold_ret else 'lost to'} buy &amp; hold by
    <b>{abs(strat_ret-hold_ret):.2f} pts</b>.
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">All trades ({n})</h3>
    <table><thead><tr><th>#</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </div>

  <p style="color:#9aa7b8;font-size:.8rem">⚠️ Past performance does not predict future results.
     Backtests assume perfect fills and no slippage — real trading will differ.</p>
</div></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest the rule strategy.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--candles", type=int, default=1500)
    ap.add_argument("--min-confidence", type=int, default=45)
    ap.add_argument("--stop-loss", type=float, default=3.0)
    ap.add_argument("--take-profit", type=float, default=5.0)
    ap.add_argument("--trade-amount", type=float, default=50.0)
    ap.add_argument("--html", nargs="?", const="backtest_report.html",
                    help="Write an HTML report (default: backtest_report.html)")
    args = ap.parse_args()

    client = BinanceClient(testnet=False)  # public data; live exchange has full history
    print(f"Fetching {args.candles} {args.interval} candles for {args.symbol}…")
    klines = fetch_history(client, args.symbol, args.interval, args.candles)
    if len(klines) < 60:
        raise SystemExit("Not enough history returned to backtest.")

    p = Params(
        trade_quote_amount=args.trade_amount,
        min_confidence=args.min_confidence,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
    )
    sim = run(klines, p)
    report(klines, sim, p, args.symbol, args.interval)

    if args.html:
        from pathlib import Path
        out = Path(args.html)
        out.write_text(build_html(klines, sim, p, args.symbol, args.interval))
        print(f"\nHTML report written to: {out.resolve()}")
        print(f"Open it with:  open {out}")


if __name__ == "__main__":
    main()
