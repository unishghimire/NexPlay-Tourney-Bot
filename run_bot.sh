#!/bin/bash
# NexPlay Tournament Bot — Auto-restart wrapper
source /app/.agents/.env

echo "[$(date)] Starting NexPlay Tournament Bot..."

while true; do
    echo "[$(date)] Bot starting..."
    python3 /app/tournament_bot.py >> /tmp/nexplay_bot.log 2>&1
    EXIT_CODE=$?
    echo "[$(date)] Bot exited with code $EXIT_CODE. Restarting in 5s..."
    sleep 5
done
