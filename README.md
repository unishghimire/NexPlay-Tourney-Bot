# NexPlay Tourney Bot

Discord tournament management bot for **NexPlay ORG** — multi-server SaaS platform.

## Structure
```
main.py           ← single async bot (discord.py 2.x)
railway.toml      ← Railway deploy config
requirements.txt  ← Python deps
.env.example      ← env var template
```

## Deploy (Railway)
1. Push to GitHub → Railway auto-deploys
2. Set env vars: `DISCORD_BOT_TOKEN`, `BASE44_SERVICE_TOKEN`, `APP_ID`, `GROQ_API_KEY`
3. Start command: `python main.py`

## Commands
Tournament: `/setup`, `/create_tournament`, `/edit_tournament`, `/delete_tournament`
Music: `/play`, `/skip`, `/stop`, `/queue`, `/loop`, `/pause`, `/resume`, `/leave`, `/volume`
Admin: `/announce`, `/clearlog`, `/host_game`, `/suggest_improvement`
Utility: `/delete_channel`, `/delete_category`

## Architecture
- One bot process → N servers, isolated by `guild_id`
- Base44 API for all DB operations (Server, Tournament, Registration, Match, etc.)
- 4-tier plans: Trial → Starter → Pro → Elite
- Auto-meme posting every 15 min (all servers)
- AI support via Groq (fallback: Pollinations)
- Music via yt-dlp + ffmpeg
