"""
Live web dashboard for the trading bot.

Reads bot_state.json + trade_journal.jsonl (written by trading_bot.py) and the
current market price, and serves an auto-refreshing web page showing:
  - current position, entry, live price
  - unrealized + realized P&L
  - recent decisions
  - a live price sparkline

Stdlib only — no extra dependencies, no API key (uses public price data).

Run (in a second terminal, while the bot runs in the first):
    python dashboard.py
    # then open http://localhost:8000

Options:
    python dashboard.py --symbol ETHUSDT --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

from binance_client import BinanceClient, BinanceError

load_dotenv()

STATE_FILE = Path("bot_state.json")
JOURNAL_FILE = Path("trade_journal.jsonl")

SYMBOL = "BTCUSDT"
TESTNET = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
_client = BinanceClient(testnet=TESTNET)


# ----- data assembly ------------------------------------------------------

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


def build_data() -> dict:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    journal = read_journal()

    try:
        price = float(_client.price(SYMBOL)["price"])
    except (BinanceError, Exception):
        # Fall back to the last journaled price if the API call fails.
        prices = [e.get("price") for e in journal if e.get("price")]
        price = float(prices[-1]) if prices else 0.0

    holding = state.get("holding", False)
    entry = float(state.get("entry_price", 0) or 0)
    base_qty = float(state.get("base_qty", 0) or 0)
    unreal_pct = ((price / entry - 1) * 100) if (holding and entry) else 0.0
    unreal_usd = (base_qty * (price - entry)) if (holding and entry) else 0.0

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
        "symbol": SYMBOL,
        "mode": "TESTNET" if TESTNET else "LIVE",
        "price": price,
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


# ----- HTTP server --------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence per-request logging
        pass

    def do_GET(self):
        if self.path.startswith("/api/data"):
            body = json.dumps(build_data()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Bot — Live Dashboard</title>
<style>
  body{margin:0;background:#0e1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:920px;margin:0 auto;padding:24px}
  .top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
  h1{font-size:1.4rem;margin:0}
  .pill{font-family:ui-monospace,monospace;font-size:.75rem;padding:4px 10px;border-radius:999px;border:1px solid #2a3343;background:#1c2230;color:#9aa7b8}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
  .card{background:#161b22;border:1px solid #2a3343;border-radius:12px;padding:16px 18px}
  .lbl{color:#9aa7b8;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
  .val{font-size:1.5rem;font-weight:700;font-family:ui-monospace,monospace;margin-top:2px}
  .pos{color:#3fb950}.neg{color:#f85149}.accent{color:#f0b90b}.mut{color:#9aa7b8}
  table{width:100%;border-collapse:collapse;font-size:.85rem;font-family:ui-monospace,monospace}
  th,td{text-align:left;padding:6px 9px;border-bottom:1px solid #2a3343;white-space:nowrap}
  th{color:#9aa7b8;font-size:.7rem;text-transform:uppercase}
  td.reason{white-space:normal;color:#9aa7b8}
  .tag{font-size:.7rem;padding:1px 6px;border-radius:5px;border:1px solid #2a3343}
  .b-BUY{color:#3fb950;border-color:#3fb95066}.b-SELL{color:#f85149;border-color:#f8514966}
  .b-HOLD{color:#9aa7b8}.blocked{color:#d29922}
  h3{margin:18px 0 6px;font-size:1rem}
  .foot{color:#9aa7b8;font-size:.78rem;margin-top:18px}
</style></head><body><div class="wrap">
  <div class="top">
    <h1>🤖 Trading Bot <span class="accent" id="sym"></span></h1>
    <div>
      <span class="pill" id="mode">—</span>
      <span class="pill" id="upd">connecting…</span>
    </div>
  </div>

  <div class="grid">
    <div class="card"><div class="lbl">Live price <span id="livedot" class="mut" style="font-size:.7rem">○ poll</span></div><div class="val" id="price">—</div></div>
    <div class="card"><div class="lbl">Position</div><div class="val" id="pos">—</div></div>
    <div class="card"><div class="lbl">Unrealized P&amp;L</div><div class="val" id="unreal">—</div></div>
    <div class="card"><div class="lbl">Realized P&amp;L</div><div class="val" id="realized">—</div></div>
    <div class="card"><div class="lbl">Win / Loss</div><div class="val" id="wl">—</div></div>
    <div class="card"><div class="lbl">Trades today</div><div class="val" id="today">—</div></div>
  </div>

  <div class="card">
    <h3 style="margin-top:0">Price (recent decisions)</h3>
    <svg id="spark" viewBox="0 0 860 160" width="100%" preserveAspectRatio="none"></svg>
  </div>

  <h3>Recent decisions</h3>
  <div class="card" style="padding:6px 10px">
    <table><thead><tr><th>Time</th><th>Action</th><th>Conf</th><th>Price</th><th>Status</th><th>Reason</th></tr></thead>
    <tbody id="rows"><tr><td colspan="6" class="mut">Waiting for the bot to write its first cycle…</td></tr></tbody></table>
  </div>

  <p class="foot">Auto-refreshes every 5s. Run the bot with <code>python trading_bot.py</code> in another terminal.
     P&amp;L is computed from the journal (works in dry-run too — simulated).</p>
</div>
<script>
const money = (v)=> (v>=0?'+$':'-$')+Math.abs(v).toFixed(2);
const cls = (v)=> v>=0?'val pos':'val neg';

function spark(series){
  const svg=document.getElementById('spark');
  if(!series || series.length<2){svg.innerHTML='<text x="10" y="80" fill="#9aa7b8" font-size="12">no data yet</text>';return;}
  const ps=series.map(s=>s.price), lo=Math.min(...ps), hi=Math.max(...ps), span=(hi-lo)||1, n=ps.length;
  const pts=ps.map((p,i)=>{const x=10+840*i/(n-1);const y=150-140*(p-lo)/span;return x.toFixed(1)+','+y.toFixed(1);}).join(' ');
  svg.innerHTML='<polyline fill="none" stroke="#f0b90b" stroke-width="2" points="'+pts+'"/>'+
    '<text x="10" y="14" fill="#9aa7b8" font-size="11">'+hi.toLocaleString()+'</text>'+
    '<text x="10" y="150" fill="#9aa7b8" font-size="11">'+lo.toLocaleString()+'</text>';
}

// --- Binance public WebSocket: real-time price, straight from the browser ---
let ws=null, wsConnected=false;
function connectWS(symbol, mode){
  if(ws) return;
  const host = mode==='TESTNET' ? 'wss://stream.testnet.binance.vision'
                                : 'wss://stream.binance.com:9443';
  ws = new WebSocket(host+'/ws/'+symbol.toLowerCase()+'@trade');
  ws.onopen = ()=>{ wsConnected=true;
    document.getElementById('livedot').innerHTML='<span class="pos">● live</span>'; };
  ws.onclose = ()=>{ wsConnected=false; ws=null;
    document.getElementById('livedot').innerHTML='○ poll';
    setTimeout(()=>connectWS(symbol,mode), 3000); };   // auto-reconnect
  ws.onerror = ()=>{ try{ws.close();}catch(e){} };
  ws.onmessage = (m)=>{ const t=JSON.parse(m.data);
    if(t.p) document.getElementById('price').textContent='$'+Number(t.p).toLocaleString(); };
}

async function tick(){
  try{
    const d=await (await fetch('/api/data')).json();
    document.getElementById('sym').textContent=d.symbol;
    document.getElementById('mode').textContent=d.mode;
    document.getElementById('upd').textContent='updated '+new Date().toLocaleTimeString();
    connectWS(d.symbol, d.mode);                       // start live price stream
    if(!wsConnected) document.getElementById('price').textContent='$'+Number(d.price).toLocaleString();
    document.getElementById('pos').innerHTML = d.holding
      ? '<span class="pos">IN</span> <span class="mut" style="font-size:.8rem">@ '+d.entry.toLocaleString()+'</span>'
      : '<span class="mut">FLAT</span>';
    const u=document.getElementById('unreal');
    u.className=cls(d.unreal_usd); u.textContent = d.holding ? money(d.unreal_usd)+'  ('+d.unreal_pct+'%)' : '—';
    const r=document.getElementById('realized'); r.className=cls(d.realized_usd); r.textContent=money(d.realized_usd);
    document.getElementById('wl').innerHTML='<span class="pos">'+d.wins+'</span> / <span class="neg">'+d.losses+'</span>';
    document.getElementById('today').textContent=d.trades_today;

    spark(d.series);

    const rows=d.recent.map(e=>{
      const a=e.action||'—'; const status=e.blocked?'<span class="blocked">blocked: '+e.blocked+'</span>':'<span class="pos">ok</span>';
      const conf=e.confidence==null?'—':e.confidence;
      const price=e.price?Number(e.price).toLocaleString():'—';
      return '<tr><td>'+e.ts+'</td><td><span class="tag b-'+a+'">'+a+'</span></td><td>'+conf+'</td><td>'+price+'</td><td>'+status+'</td><td class="reason">'+(e.reason||'')+'</td></tr>';
    }).join('');
    document.getElementById('rows').innerHTML = rows || '<tr><td colspan="6" class="mut">No decisions yet.</td></tr>';
  }catch(err){
    document.getElementById('upd').textContent='bot offline / no data';
  }
}
tick(); setInterval(tick,5000);
</script>
</body></html>"""


def main() -> None:
    global SYMBOL
    ap = argparse.ArgumentParser(description="Live trading-bot dashboard.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    SYMBOL = args.symbol

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Dashboard running → http://localhost:{args.port}")
    print(f"Mode: {'TESTNET' if TESTNET else 'LIVE'} | Symbol: {SYMBOL}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


if __name__ == "__main__":
    main()
