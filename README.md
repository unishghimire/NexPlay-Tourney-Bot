# NexPlay Tourney Bot

Full-featured Discord tournament management bot for **NexPlay ORG** — Nepal's biggest gaming community.

## Features

- **Full Tournament Lifecycle** — Create → Register → Groups → Schedule → Results → Champion
- **AI Support Chat** — Smart keyword-based AI in `#support-ticket` that answers questions automatically
- **Staff Log Alerts** — Escalations sent to `#mod-log` with suggested staff actions + jump links
- **AI Image Generation** — Pollinations.ai generates posters, group draw cards, result cards and champion graphics at every stage
- **Auto-Role** — Members automatically get the `NexPlay Member` role on join
- **Welcome DM** — Every new member receives a welcome direct message
- **Base44 Entity Backend** — All data (tournaments, registrations, matches, support logs) saved to Base44

## Slash Commands

| Command | Who | Description |
|---|---|---|
| `/create_tournament` | Staff | Create & announce a tournament |
| `/close_registration` | Staff | Lock player signups |
| `/generate_groups` | Staff | Random group draw + announce |
| `/post_schedule` | Staff | Post match schedule |
| `/post_result` | Staff | Post a match result card |
| `/complete_tournament` | Staff | Crown the champion |
| `/announce` | Staff | Custom announcement |
| `/register` | Player | Register for a tournament |
| `/tournament_status` | Player | View all tournaments |
| `/help` | Player | Show all commands |

## Setup

1. Clone the repo
2. Copy `.env.example` to `.env` and fill in your tokens
3. Install dependencies:
   ```bash
   pip install discord.py aiohttp
   ```
4. Run with auto-restart:
   ```bash
   bash run_bot.sh
   ```

## Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your bot token from Discord Developer Portal |
| `DISCORD_GUILD_ID` | Your server's Guild ID |
| `BASE44_SERVICE_TOKEN` | Base44 service token for entity API |

## Requirements

- Python 3.10+
- `discord.py >= 2.0`
- `aiohttp`
- Bot requires **Server Members Intent** and **Message Content Intent** enabled in Discord Developer Portal

## Stack

- **Discord.py 2.x** — Bot framework
- **Base44** — Entity database (tournaments, registrations, matches)
- **Pollinations.ai** — Free AI image generation for all tournament graphics

---

Built by **Unish Ghimire** for NexPlay ORG
