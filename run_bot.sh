#!/bin/bash
# NexPlay Tournament Bot — Auto-restart production runner
# Keeps the bot online indefinitely. Any crash restarts in 5 seconds.

set -e
cd "$(dirname "$0")"

# Load secrets
if [ -f .agents/.env ]; then
    source .agents/.env
elif [ -f .env ]; then
    source .env
fi

echo "[$(date)] ═══════════════════════════════════"
echo "[$(date)] NexPlay Tournament Bot — STARTING"
echo "[$(date)] ═══════════════════════════════════"

while true; do
    echo "[$(date)] Bot process starting..."
    python3 -u tournament_bot.py >> /tmp/nexplay_bot.log 2>&1
    EXIT=$?
    echo "[$(date)] Bot exited (code $EXIT). Restarting in 5s..." >> /tmp/nexplay_bot.log
    sleep 5
done
