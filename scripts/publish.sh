#!/usr/bin/env bash
# Publish the bot's public state to GitHub Pages on a loop.
#
# It rebuilds docs/state.json and commits+pushes it every INTERVAL seconds so the
# static dashboard (GitHub Pages → main /docs) shows fresh P&L. Live price comes
# from the browser's WebSocket, so this only needs to update every minute or so.
#
# Run alongside the bot:  bash scripts/publish.sh
#
# NOTE: docs/state.json contains NO secrets — only position / P&L / decisions.
set -euo pipefail
cd "$(dirname "$0")/.."

INTERVAL="${PUBLISH_INTERVAL:-60}"   # seconds between pushes
source .venv/bin/activate

echo "Publishing docs/state.json every ${INTERVAL}s. Ctrl-C to stop."
while true; do
  python publisher.py >/dev/null 2>&1 || true
  if ! git diff --quiet -- docs/state.json 2>/dev/null; then
    git add docs/state.json
    git commit -m "chore: update dashboard state" >/dev/null 2>&1 || true
    git push origin HEAD >/dev/null 2>&1 || echo "(push failed — check remote/auth)"
    echo "$(date '+%H:%M:%S') pushed state update"
  fi
  sleep "$INTERVAL"
done
