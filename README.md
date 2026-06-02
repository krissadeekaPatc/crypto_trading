# Binance Spot API — Python

A minimal, dependency-light client for the Binance Spot REST API.
Supports public market data, account/balances, and spot trading.

## Setup

```bash
cd /Users/krissadeeka/crypto_trading
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Get API keys

1. Go to https://www.binance.com/en/my/settings/api-management
2. Create an API key (you'll need 2FA).
3. **Permissions**: enable *Enable Reading* and *Enable Spot Trading*.
   Leave *withdrawals* OFF.
4. **Restrict access to trusted IPs** and add your IP. This is the single most
   important safety control — an unrestricted key is far more dangerous if leaked.
5. Copy the key and secret. **The secret is shown only once.**

```bash
cp .env.example .env
# then edit .env and paste your key + secret
```

## Test the connection (places no orders)

```bash
python check_connection.py
```

Expected: connectivity OK, a BTC price, then `canTrade=True` and your balances.

## Trading

`example_trade.py` shows placing and cancelling a LIMIT order. It runs in
**DRY_RUN** mode by default (`test_order` — validates only, no money moves).
Set `DRY_RUN = False` and use a tiny quantity to place a real order.

## Using the client in your own code

```python
from dotenv import load_dotenv
from binance_client import BinanceClient
load_dotenv()

c = BinanceClient()                       # reads keys from environment
print(c.price("ETHUSDT"))
print(c.balances())
order = c.create_order("BTCUSDT", "BUY", "MARKET", quote_order_qty=10)  # buy $10 of BTC
```

## Safety notes (live funds)

- `.env` is git-ignored — never commit keys.
- Whitelist your IP on the API key.
- Don't enable withdrawal permission.
- Test order logic with `test_order()` or on the Spot Testnet
  (`BINANCE_TESTNET=true`, keys from https://testnet.binance.vision) first.
- If you get error `-1021`, your system clock is out of sync — fix system time.
- If you get error `-2015`, the key/secret/IP-whitelist/permissions are wrong.

## The trading bot (Claude-driven)

`trading_bot.py` runs a 24/7 loop that, every cycle, asks **Claude** for a
BUY / SELL / HOLD call on one pair, then enforces hard risk limits before acting.

```
binance_client.py   → talks to Binance (data + orders)
indicators.py       → computes RSI / SMA / trend locally
claude_strategy.py  → Claude makes the directional decision (structured output)
trading_bot.py      → the loop: data → exits → Claude → guardrails → execute → journal
```

### Setup

1. Add your Anthropic key to `.env` (`ANTHROPIC_API_KEY=`), key from
   https://console.anthropic.com/settings/keys
2. Keep `BINANCE_TESTNET=true` and add **testnet** keys from
   https://testnet.binance.vision

### Run it

```bash
source .venv/bin/activate
python trading_bot.py     # Ctrl-C to stop
```

Out of the box it is **double-safe**: Testnet (fake funds) **and** `DRY_RUN`
(decides + journals but places no orders). Watch it for a while:

- `trade_journal.jsonl` — every cycle's decision, reason, and any fill
- `bot_state.json` — current position + daily trade count

### Tunable knobs (top of `trading_bot.py`, `Config`)

| Field | Meaning |
|-------|---------|
| `symbol` / `interval` | pair and candle size (e.g. BTCUSDT, 15m) |
| `cycle_seconds` | how often a cycle runs (900 = 15 min) |
| `trade_quote_amount` | how much USDT to spend per BUY |
| `min_confidence` | ignore Claude trades below this (0-100) |
| `max_trades_per_day` | circuit breaker |
| `stop_loss_pct` / `take_profit_pct` | bot-enforced exits (run before Claude) |
| `dry_run` | `True` = no real orders |

### Going live (only after testnet looks right)

1. Paper-trade on testnet with `dry_run=False` first — real testnet orders, fake money.
2. Then switch `BINANCE_TESTNET=false`, use live keys (Spot Trading, IP-whitelisted),
   set a small `trade_quote_amount`, and only then flip `dry_run=False`.

> ⚠️ Autonomous trading bots commonly lose money. The risk limits here reduce
> damage; they don't make it profitable. Start tiny and watch it.

## Live dashboard

Two ways to watch the bot:

**Local (instant):**
```bash
python dashboard.py          # then open http://localhost:8000
```
Live price via Binance WebSocket + position/P&L from the journal.

**Public (GitHub Pages):** the static dashboard in `docs/` reads `docs/state.json`.
- The bot writes `docs/state.json` every cycle (no secrets in it).
- `scripts/publish.sh` commits + pushes that file on a loop so Pages stays fresh.
- Enable Pages: repo **Settings → Pages → Branch: main, Folder: /docs**.
- Live price always streams client-side from Binance's public WebSocket.

## Deployment

A trading bot is a long-running process that holds secrets — it **cannot** run on
Vercel or GitHub Pages (serverless / static only). Split it:

| Piece | Where | How |
|-------|-------|-----|
| The **bot** | Your Mac or a VPS | `scripts/run-bot.sh`, or launchd / systemd templates in `scripts/` |
| **state.json** | GitHub (committed) | `scripts/publish.sh` pushes it on a loop |
| **Dashboard** | GitHub Pages (`/docs`) | static; reads state.json + live WS price |

**Run the bot 24/7 on macOS:** edit paths in `scripts/com.cryptobot.plist`, then
`cp` it to `~/Library/LaunchAgents/` and `launchctl load` it.
**On a Linux VPS:** edit `scripts/cryptobot.service`, copy to `/etc/systemd/system/`,
`systemctl enable --now cryptobot`.

> The bot keeps your keys in `.env` on the machine it runs on. Never deploy the
> `.env` anywhere public. `docs/state.json` is safe to publish — it has no secrets.

## Reference

- API docs: https://developers.binance.com/docs/binance-spot-api-docs
- API management: https://www.binance.com/en/my/settings/api-management
- Claude API keys: https://console.anthropic.com/settings/keys
