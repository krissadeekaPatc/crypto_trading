#!/usr/bin/env bash
# Run the trading bot (activates the venv first). Use this for launchd/systemd
# or just `bash scripts/run-bot.sh`.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec python trading_bot.py
