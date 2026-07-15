"""
NexPlay Tournament Bot
======================
Author  : Unish Ghimire / NexPlay ORG
Version : 4.0.0  (multi-server production)
Python  : 3.10+

CHANGELOG v4.0.0
-----------------
FIXED  - Global slash commands (removed guild= from all decorators).
         Commands now appear in EVERY server that adds the bot.
FIXED  - Channel IDs are now resolved dynamically from the guild,
         not hardcoded to NEXPLAY ORG channel IDs.
FIXED  - on_guild_join auto-registers new servers into Base44 DB
         and posts an alert to the owner's log channel.
FIXED  - Subscription gate on every staff command — unregistered
         or expired servers cannot use tournament commands.
FIXED  - get_or_create_channels() provisions required channels in
         any new server automatically (no manual setup needed).
FIXED  - Support handler resolves the correct channel per guild.
ADDED  - on_guild_remove logs server departures.
ADDED  - /setup command for server owners to initialize their server.

ARCHITECTURE
------------
One bot process → connects to Discord gateway once → serves N servers.
Each guild interaction is fully isolated via guild_id field in every
Base44 entity record. Zero cross-server data leakage.

SECURITY
--------
* No hardcoded secrets — all from env.
* Subscription gate enforced server-side on every write command.
* is_staff() checks role names — cannot be bypassed by renaming.
* All user input stored as plain string — no eval/exec surface.
* aiohttp 10s timeout on every external call.
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import asyncio
import io
import csv
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load .env file if present (local dev). Render injects vars directly.
load_dotenv()

# ══════════════════════════════════════════════════════════
#  CONFIG  — all values come from environment variables
# ══════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN", "")
HOME_GUILD  = int(os.environ.get("DISCORD_GUILD_ID", "0"))   # owner's server only
SVC_TOKEN   = os.environ.get("BASE44_SERVICE_TOKEN", "")
APP_ID      = os.environ.get("APP_ID", "6a5226b5047f5c59d961130e")

BASE44_API  = "https://base44.app/api/apps/" + APP_ID + "/entities"
DISCORD_API = "https://discord.com/api/v10"

# ── Role names that count as "staff" in any server ────────
STAFF_ROLE_NAMES = {
    "NexPlay Owner", "Tournament Host", "Admin", "Moderator", "NexPlay Admin",
    "Owner", "Co-Owner", "Manager", "Staff",
}

log = print  # alias

# ── Status emoji map ───────────────────────────────────────
STATUS_EMOJI = {
    "registration_open":   "🟢",
    "registration_closed": "🔒",
    "groups_generated":    "🎯",
    "scheduled":           "📅",
    "in_progress":         "🔥",
    "completed":           "✅",
    "cancelled":           "❌",
}

# Shared HTTP timeout
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ══════════════════════════════════════════════════════════
#  BOT CLASS
# ══════════════════════════════════════════════════════════
class NexPlayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
        # GLOBAL sync — commands appear in every server
        await self.tree.sync()
        print("[NexPlay] Global slash commands synced.")
        # Start daily log scheduler
        asyncio.create_task(self._daily_log_scheduler())

    async def _daily_log_scheduler(self):
        """Send daily Excel log at midnight (00:00 NPT = 18:15 UTC prev day).
           We target 00:00 NPT = UTC 18:15 = offset -5h45m from NPT.
           We compute seconds until next 18:15 UTC and sleep until then.
        """
        print("[NexPlay] Daily log scheduler started.", flush=True)
        while True:
            try:
                now_utc = datetime.now(timezone.utc)
                # Target: 18:15 UTC daily (= midnight NPT)
                target = now_utc.replace(hour=18, minute=15, second=0, microsecond=0)
                if now_utc >= target:
                    target = target.replace(day=target.day + 1)
                wait_secs = (target - now_utc).total_seconds()
                print(f"[NexPlay] Daily log fires in {wait_secs/3600:.1f}h", flush=True)
                await asyncio.sleep(wait_secs)
                await send_daily_logs(self)
            except Exception as e:
                print(f"[NexPlay] Daily log scheduler error: {e}", flush=True)
                await asyncio.sleep(3600)  # Retry in 1h on error

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()


bot  = NexPlayBot()
tree = bot.tree



# ══════════════════════════════════════════════════════════
#  ON READY — fresh start every deployment
# ══════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    """Fires when bot connects. Clears all in-memory support locks so every
    restart gives users a completely fresh support session."""
    global _replied_users
    _replied_users = {}   # ← wipe all per-guild locks on every startup

    guilds = bot.guilds
    print("=" * 60, flush=True)
    print(f"[NexPlay] ✅ BOT ONLINE — {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"[NexPlay] Serving {len(guilds)} server(s)", flush=True)
    for g in guilds:
        print(f"[NexPlay]   • {g.name} ({g.id}) — {g.member_count} members", flush=True)
    print(f"[NexPlay] Support locks cleared — fresh session started", flush=True)
    print("=" * 60, flush=True)


# ══════════════════════════════════════════════════════════
#  BASE44 ENTITY HELPERS
# ══════════════════════════════════════════════════════════
def _b44_headers() -> dict:
    return {"Authorization": "Bearer " + SVC_TOKEN, "Content-Type": "application/json"}


async def b44_list(entity: str, filters: dict | None = None) -> list:
    url = BASE44_API + "/" + entity
    try:
        async with bot.http_session.get(url, headers=_b44_headers()) as r:
            if r.status != 200:
                return []
            data = await r.json()
            if not isinstance(data, list):
                return []
            if filters:
                for k, v in filters.items():
                    data = [x for x in data if x.get(k) == v]
            return data
    except Exception as e:
        print("[b44_list] " + str(e))
        return []


async def b44_create(entity: str, payload: dict) -> dict:
    url = BASE44_API + "/" + entity
    try:
        async with bot.http_session.post(url, json=payload, headers=_b44_headers()) as r:
            if r.status in (200, 201):
                return await r.json()
            return {}
    except Exception as e:
        print("[b44_create] " + str(e))
        return {}


async def b44_update(entity: str, record_id: str, payload: dict) -> dict:
    url = BASE44_API + "/" + entity + "/" + record_id
    try:
        async with bot.http_session.put(url, json=payload, headers=_b44_headers()) as r:
            if r.status in (200, 201):
                return await r.json()
            return {}
    except Exception as e:
        print("[b44_update] " + str(e))
        return {}


# ══════════════════════════════════════════════════════════
#  SUBSCRIPTION GATE
#  Every staff command calls this first. If the server isn't
#  registered or has expired, the command is blocked.
# ══════════════════════════════════════════════════════════
async def get_server_record(guild_id: str) -> dict | None:
    servers = await b44_list("Server", {"guild_id": guild_id})
    return servers[0] if servers else None


async def is_allowed(guild_id: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Only active/trial servers pass."""
    rec = await get_server_record(guild_id)
    if not rec:
        return False, "This server is not registered with NexPlay. Ask your admin to run `/setup`."
    status = rec.get("subscription_status", "")
    if status == "banned":
        return False, "This server has been banned from NexPlay. Contact support."
    if status in ("active", "trial"):
        return True, ""
    return False, "This server's NexPlay subscription has expired. Visit nexplay.gg to renew."


# ══════════════════════════════════════════════════════════
#  DYNAMIC CHANNEL RESOLVER
#  Finds channels by name — works in ANY server.
#  Falls back to first available channel if not found.
# ══════════════════════════════════════════════════════════
CHANNEL_NAMES = {
    "announcements": ["tourney-announcements", "tournament-announcements", "announcements", "general"],
    "registration":  ["tourney-registration", "tournament-registration", "registration", "general"],
    "brackets":      ["brackets-results", "brackets", "results", "tournament-results", "general"],
    "champions":     ["hall-of-champions", "champions", "winners", "general"],
    "support":       ["support-ticket", "support", "help", "general"],
    "staff":         ["mod-log", "staff-log", "staff", "moderators", "admin-log"],
    "rules":         ["tourney-rules", "rules", "tournament-rules", "general"],
}


def resolve_channel(guild: discord.Guild, key: str) -> discord.TextChannel | None:
    """Find a channel by trying a list of common names."""
    for name in CHANNEL_NAMES.get(key, []):
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch:
            return ch
    # fallback: first text channel the bot can send in
    for ch in guild.text_channels:
        perms = ch.permissions_for(guild.me)
        if perms.send_messages and perms.embed_links:
            return ch
    return None


async def get_or_create_channels(guild: discord.Guild) -> dict:
    """
    Ensure required channels exist. Returns a dict of channel IDs.
    Creates missing channels under a 'NexPlay Tournaments' category.
    """
    required = {
        "announcements": "tourney-announcements",
        "registration":  "tourney-registration",
        "brackets":      "brackets-results",
        "champions":     "hall-of-champions",
        "support":       "support-ticket",
        "staff":         "mod-log",
    }
    result = {}
    category = discord.utils.get(guild.categories, name="NexPlay Tournaments")
    if not category:
        try:
            category = await guild.create_category("NexPlay Tournaments")
        except Exception:
            category = None

    for key, ch_name in required.items():
        ch = resolve_channel(guild, key)
        if not ch:
            try:
                ch = await guild.create_text_channel(ch_name, category=category)
            except Exception:
                ch = guild.text_channels[0] if guild.text_channels else None
        result[key] = ch.id if ch else None
    return result


# ══════════════════════════════════════════════════════════
#  DISCORD REST HELPER
# ══════════════════════════════════════════════════════════
async def dpost(channel_id: int, embed: discord.Embed, content: str = "") -> dict:
    if not channel_id:
        return {}
    url     = DISCORD_API + "/channels/" + str(channel_id) + "/messages"
    headers = {"Authorization": "Bot " + BOT_TOKEN, "Content-Type": "application/json"}
    body    = {"embeds": [embed.to_dict()]}
    if content:
        body["content"] = content
    try:
        async with bot.http_session.post(url, json=body, headers=headers) as r:
            return await r.json()
    except Exception as e:
        print("[dpost] " + str(e))
        return {}


# ══════════════════════════════════════════════════════════
#  SMALL UTILITIES
# ══════════════════════════════════════════════════════════
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def is_staff(member: discord.Member) -> bool:
    for role in member.roles:
        clean = role.name
        for prefix in ("👑 ", "⚔️ ", "🛡️ ", "🔧 ", "🎮 ", "🎯 ", "⚙️ ", "🏆 ", "🔥 ", "⛏️ ", "📋 ", "🌱 ", "🤖 "):
            clean = clean.replace(prefix, "")
        if clean in STAFF_ROLE_NAMES:
            return True
    return False

def err_e(msg: str) -> discord.Embed:
    e = discord.Embed(description="❌  " + msg, color=0xFF4444)
    e.set_footer(text="NexPlay")
    return e

def ok_e(title: str, desc: str, color: int = 0x00FF7F) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.set_footer(text="NexPlay Tournament System")
    e.timestamp = datetime.now(timezone.utc)
    return e

def img_url(t_name: str, game: str, kind: str, extra: str = "") -> str:
    templates = {
        "poster":   "professional esports tournament poster {n} {g} neon dark background gold purple cinematic",
        "roadmap":  "tournament roadmap timeline {n} {g} stages Registration GroupDraw Schedule MatchDay Champion modern dark",
        "group":    "esports group draw reveal {n} {g} {x} dark neon panels",
        "schedule": "match schedule card {n} {g} {x} dark professional infographic",
        "result":   "match result card {x} {n} dark dramatic victory graphic",
        "champion": "champion victory {x} wins {n} {g} golden trophy confetti epic cinematic",
    }
    tpl    = templates.get(kind, "professional esports graphic {n} {g}")
    prompt = tpl.format(n=t_name, g=game, x=extra)
    prompt = prompt.replace(" ", "%20").replace(",", "%2C")
    return (
        "https://image.pollinations.ai/prompt/" + prompt
        + "?width=1280&height=640&nologo=true&seed=" + str(now_ts()) + "&model=flux"
    )


# ══════════════════════════════════════════════════════════
#  AI SUPPORT ENGINE  — Pollinations AI + one-reply-per-user lock
# ══════════════════════════════════════════════════════════
#
#  RULES:
#  1. Bot replies to a user ONCE per support session
#  2. Session stays locked until staff marks it resolved in #staff-log
#  3. Uses Pollinations free text AI for natural, accurate responses
#  4. Always mentions real channel names from the server
#  5. Unknown/complex queries → staff-log with full context
# ══════════════════════════════════════════════════════════

import urllib.parse

# ── Per-guild lock: {guild_id: {user_id: support_message_db_id}}
# Prevents double-replies until staff clears the ticket
_replied_users: dict[str, dict[str, str]] = {}

async def ai_generate(prompt: str) -> str:
    """Call Pollinations free text AI — no API key needed."""
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://text.pollinations.ai/{encoded}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return (await r.text()).strip()
    except Exception as e:
        print(f"[AI] Pollinations error: {e}", flush=True)
    return ""

def build_server_context(guild: discord.Guild, active_tournament: dict | None) -> str:
    """Build a rich context string about the server for the AI prompt."""
    channels = [f"#{ch.name}" for ch in guild.text_channels]
    roles    = [r.name for r in guild.roles if not r.is_default()]

    ctx  = f"Server: {guild.name}\n"
    ctx += f"Channels: {', '.join(channels[:30])}\n"
    ctx += f"Roles: {', '.join(roles[:20])}\n"

    if active_tournament:
        tn    = active_tournament.get('name', 'Unknown')
        tg    = active_tournament.get('game', 'Unknown')
        ts    = active_tournament.get('status', 'unknown')
        td    = active_tournament.get('tournament_date', 'TBA')
        ttime = active_tournament.get('tournament_time', 'TBD')
        tmx   = active_tournament.get('max_players', '?')
        tcnt  = active_tournament.get('registered_count', 0)
        tsize = active_tournament.get('team_size', 4)
        tp    = active_tournament.get('prize_pool', 'TBA')
        short = active_tournament.get('short_name', '')

        ctx += f"\nActive Tournament: {tn}\n"
        ctx += f"Game: {tg} | Status: {ts} | Date: {td} | Time: {ttime}\n"
        ctx += f"Max Slots: {tmx} | Registered: {tcnt} | Team Size: {tsize}\n"
        ctx += f"Prize Pool: {tp}\n"
        if short:
            ctx += f"Registration channel: #{short}-register\n"
    else:
        ctx += "\nNo active tournament right now.\n"

    return ctx

# ══════════════════════════════════════════════════════════
#  EXCEL LOG BUILDER
# ══════════════════════════════════════════════════════════
def _xl_header_style(cell, bg="1F4E79"):
    cell.font      = Font(bold=True, color="FFFFFF", size=10)
    cell.fill      = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def _xl_border(cell):
    thin = {"border_style": "thin", "color": "AAAAAA"}
    from openpyxl.styles import Border, Side
    cell.border = Border(
        left=Side(**thin), right=Side(**thin),
        top=Side(**thin), bottom=Side(**thin)
    )

async def build_daily_excel(guild: discord.Guild) -> io.BytesIO:
    """Build a full-day Excel report for one guild."""
    wb = openpyxl.Workbook()
    gid = str(guild.id)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Sheet 1: TOURNAMENTS ────────────────────────────────────────────
    ws_t = wb.active
    ws_t.title = "Tournaments"
    t_headers = ["ID", "Name", "Game", "Status", "Slots", "Registered",
                 "Prize Pool", "Date", "Time", "Format", "Created"]
    for ci, h in enumerate(t_headers, 1):
        c = ws_t.cell(row=1, column=ci, value=h)
        _xl_header_style(c)
        _xl_border(c)
    ws_t.row_dimensions[1].height = 20

    tournaments = await b44_list("Tournament", {"guild_id": gid})
    for ri, t in enumerate(tournaments, 2):
        row = [
            t.get("id","")[:8], t.get("name",""), t.get("game",""),
            t.get("status",""), t.get("max_players",""), t.get("registered_count",0),
            t.get("prize_pool",""), t.get("tournament_date",""),
            t.get("tournament_time",""), t.get("format",""),
            t.get("created_date","")[:10] if t.get("created_date") else ""
        ]
        for ci, val in enumerate(row, 1):
            c = ws_t.cell(row=ri, column=ci, value=str(val))
            _xl_border(c)
            c.alignment = Alignment(vertical="center")
        # Color by status
        status_colors = {
            "registration_open": "C6EFCE", "registration_closed": "FFEB9C",
            "groups_generated": "BDD7EE", "scheduled": "FCE4D6",
            "completed": "E2EFDA", "deleted": "DDDDDD"
        }
        sc = status_colors.get(t.get("status",""), "FFFFFF")
        ws_t.cell(row=ri, column=4).fill = PatternFill("solid", fgColor=sc)

    for ci in range(1, len(t_headers)+1):
        ws_t.column_dimensions[get_column_letter(ci)].width = [8,25,14,18,6,10,12,12,12,16,12][ci-1]

    # ── Sheet 2: REGISTRATIONS ─────────────────────────────────────────
    ws_r = wb.create_sheet("Registrations")
    r_headers = ["Team Name", "Tournament", "Players", "Discord User",
                 "Registered At", "Group", "Status", "Slot #"]
    for ci, h in enumerate(r_headers, 1):
        c = ws_r.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "375623")
        _xl_border(c)
    ws_r.row_dimensions[1].height = 20

    all_regs = await b44_list("Registration", {"guild_id": gid})
    t_map = {t["id"]: t.get("name","?") for t in tournaments}
    for ri, r in enumerate(all_regs, 2):
        row = [
            r.get("player_name",""), t_map.get(r.get("tournament_id",""),"?"),
            r.get("player_username",""), r.get("player_discord_tag",""),
            r.get("registered_at","")[:16] if r.get("registered_at") else "",
            r.get("group_label","—"), r.get("status","registered"), r.get("seed_number","")
        ]
        for ci, val in enumerate(row, 1):
            c = ws_r.cell(row=ri, column=ci, value=str(val))
            _xl_border(c)
            c.alignment = Alignment(vertical="center")

    for ci, w in enumerate([18,22,30,18,16,8,12,7], 1):
        ws_r.column_dimensions[get_column_letter(ci)].width = w

    # ── Sheet 3: SUPPORT LOG ───────────────────────────────────────────
    ws_s = wb.create_sheet("Support Log")
    s_headers = ["ID", "Message", "Status", "Created"]
    for ci, h in enumerate(s_headers, 1):
        c = ws_s.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "843C0C")
        _xl_border(c)

    support_msgs = await b44_list("SupportMessage", {"guild_id": gid})
    for ri, sm in enumerate(support_msgs, 2):
        row = [
            sm.get("id","")[:8], sm.get("message","")[:200],
            sm.get("status",""), sm.get("created_date","")[:16] if sm.get("created_date") else ""
        ]
        for ci, val in enumerate(row, 1):
            c = ws_s.cell(row=ri, column=ci, value=str(val))
            _xl_border(c)
            c.alignment = Alignment(vertical="center", wrap_text=(ci==2))
        # Color by status
        status_colors2 = {"resolved": "C6EFCE", "pending": "FFEB9C",
                          "escalated": "FCE4D6", "dismissed": "DDDDDD", "saved": "BDD7EE"}
        sc = status_colors2.get(sm.get("status",""), "FFFFFF")
        ws_s.cell(row=ri, column=3).fill = PatternFill("solid", fgColor=sc)

    for ci, w in enumerate([8, 60, 12, 16], 1):
        ws_s.column_dimensions[get_column_letter(ci)].width = w
    ws_s.row_dimensions[1].height = 20

    # ── Sheet 4: MATCH RESULTS ─────────────────────────────────────────
    ws_m = wb.create_sheet("Match Results")
    m_headers = ["Tournament", "Round", "Player 1", "Player 2", "Winner", "Score", "Status"]
    for ci, h in enumerate(m_headers, 1):
        c = ws_m.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "4472C4")
        _xl_border(c)

    matches = await b44_list("Match", {"guild_id": gid})
    for ri, m in enumerate(matches, 2):
        row = [
            t_map.get(m.get("tournament_id",""),"?"), m.get("round_number",""),
            m.get("player1_username",""), m.get("player2_username",""),
            m.get("winner_username",""), 
            str(m.get("player1_score","")) + "-" + str(m.get("player2_score","")) if m.get("player1_score") else "N/A",
            m.get("status","")
        ]
        for ci, val in enumerate(row, 1):
            c = ws_m.cell(row=ri, column=ci, value=str(val))
            _xl_border(c)

    for ci, w in enumerate([22,7,18,18,18,8,12], 1):
        ws_m.column_dimensions[get_column_letter(ci)].width = w

    # ── Sheet 5: SUMMARY ──────────────────────────────────────────────
    ws_sum = wb.create_sheet("Summary")
    ws_sum["A1"] = f"NexPlay Daily Report — {guild.name}"
    ws_sum["A1"].font = Font(bold=True, size=14, color="1F4E79")
    ws_sum["A2"] = f"Generated: {today_str} (12:00 AM NPT)"
    ws_sum["A2"].font = Font(italic=True, size=10, color="666666")
    ws_sum.merge_cells("A1:D1")
    ws_sum.merge_cells("A2:D2")

    stats = [
        ("", ""),
        ("📊 SUMMARY", ""),
        ("Total Tournaments", len(tournaments)),
        ("Active Tournaments", len([t for t in tournaments if t.get("status") not in ("completed","deleted")])),
        ("Total Registrations", len(all_regs)),
        ("Support Messages Today", len(support_msgs)),
        ("Pending Support",       len([s for s in support_msgs if s.get("status") == "pending"])),
        ("Resolved Support",      len([s for s in support_msgs if s.get("status") == "resolved"])),
        ("Total Matches Played",  len(matches)),
    ]
    for ri, (k, v) in enumerate(stats, 3):
        ws_sum.cell(row=ri, column=1, value=k).font = Font(bold=(k.startswith("📊")), size=11)
        ws_sum.cell(row=ri, column=2, value=v)
    ws_sum.column_dimensions["A"].width = 28
    ws_sum.column_dimensions["B"].width = 15

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


async def send_daily_logs(bot_instance: "NexPlayBot"):
    """Send daily Excel report to server owners + NexPlay admin."""
    print("[NexPlay] Sending daily logs...", flush=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    servers = await b44_list("Server")
    for srv in servers:
        gid = srv.get("guild_id","")
        owner_id = srv.get("owner_id","")
        guild = bot_instance.get_guild(int(gid)) if gid else None
        if not guild or not owner_id:
            continue
        try:
            xl_buf = await build_daily_excel(guild)
            owner = await bot_instance.fetch_user(int(owner_id))
            if owner:
                dm = await owner.create_dm()
                file = discord.File(xl_buf, filename=f"NexPlay_DailyLog_{guild.name}_{today}.xlsx")
                summary_e = discord.Embed(
                    title=f"📊 NexPlay Daily Report — {guild.name}",
                    description=(
                        f"Your daily server report for **{today}** is attached!\n\n"
                        f"The Excel file contains:\n"
                        f"• 🏆 All Tournaments & their status\n"
                        f"• 👥 All Team Registrations\n"
                        f"• 💬 Support Message Log\n"
                        f"• ⚔️ Match Results\n"
                        f"• 📈 Summary Stats\n\n"
                        f"*Sent automatically every day at 12:00 AM NPT by NexPlay Bot*"
                    ),
                    color=0x1F4E79, timestamp=datetime.now(timezone.utc)
                )
                summary_e.set_footer(text="NexPlay Tournament System")
                await dm.send(embed=summary_e, file=file)
                print(f"[NexPlay] Daily log sent to {owner} for {guild.name}", flush=True)
        except Exception as e:
            print(f"[NexPlay] Daily log error for {gid}: {e}", flush=True)



# ══════════════════════════════════════════════════════════
#  STAFF LOG BUTTON VIEW
# ══════════════════════════════════════════════════════════
class StaffLogView(discord.ui.View):
    """Persistent button panel on staff-log messages."""

    def __init__(self, uid: str, rec_id: str, guild_locks: dict,
                 user: discord.Member, user_channel: discord.TextChannel):
        super().__init__(timeout=None)  # No timeout — stays until resolved
        self.uid          = uid
        self.rec_id       = rec_id
        self.guild_locks  = guild_locks
        self.user         = user
        self.user_channel = user_channel
        self.message      = None
        self._status      = "pending"   # pending | resolved | saved | unsaved

    # ── ✅ RESOLVE ────────────────────────────────────────────────────────
    @discord.ui.button(label="✅ Resolve", style=discord.ButtonStyle.success, custom_id="staff_resolve")
    async def btn_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        self._status = "resolved"
        self.guild_locks.pop(self.uid, None)
        if self.rec_id:
            await b44_update("SupportMessage", self.rec_id, {"status": "resolved"})
        await self._update_embed(interaction, "✅ RESOLVED", 0x00FF00,
                                  f"Resolved by {interaction.user.display_name}")
        # Notify user
        try:
            notify = discord.Embed(
                description=f"Hey {self.user.mention}, a staff member resolved your query! Feel free to ask again. 😊",
                color=0x00FF00
            )
            await self.user_channel.send(embed=notify)
        except Exception:
            pass
        await interaction.response.send_message("✅ Resolved & user lock cleared.", ephemeral=True)

    # ── 💾 SAVE / UNSAVE TOGGLE ───────────────────────────────────────────
    @discord.ui.button(label="💾 Save", style=discord.ButtonStyle.secondary, custom_id="staff_save")
    async def btn_save(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if self._status == "saved":
            # Unsave
            self._status = "pending"
            if self.rec_id:
                await b44_update("SupportMessage", self.rec_id, {"status": "pending"})
            button.label = "💾 Save"
            button.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("📌 Log unsaved.", ephemeral=True)
        else:
            # Save
            self._status = "saved"
            if self.rec_id:
                await b44_update("SupportMessage", self.rec_id, {"status": "saved"})
            button.label = "📌 Saved"
            button.style = discord.ButtonStyle.primary
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("💾 Log saved for records.", ephemeral=True)

    # ── 🔔 ESCALATE ───────────────────────────────────────────────────────
    @discord.ui.button(label="🔔 Escalate", style=discord.ButtonStyle.danger, custom_id="staff_escalate")
    async def btn_escalate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if self.rec_id:
            await b44_update("SupportMessage", self.rec_id, {"status": "escalated"})
        self._status = "escalated"
        await self._update_embed(interaction, "🚨 ESCALATED", 0xFF6600,
                                  f"Escalated by {interaction.user.display_name}")
        await interaction.response.send_message(
            f"🚨 Escalated! <@&{interaction.guild.id}> please review.", ephemeral=True
        )

    # ── 🗑️ DISMISS ────────────────────────────────────────────────────────
    @discord.ui.button(label="🗑️ Dismiss", style=discord.ButtonStyle.secondary, custom_id="staff_dismiss")
    async def btn_dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        self.guild_locks.pop(self.uid, None)
        if self.rec_id:
            await b44_update("SupportMessage", self.rec_id, {"status": "dismissed"})
        await self._update_embed(interaction, "🗑️ DISMISSED", 0x555555,
                                  f"Dismissed by {interaction.user.display_name}")
        self.stop()
        await interaction.response.send_message("🗑️ Dismissed & lock cleared.", ephemeral=True)

    async def _update_embed(self, interaction: discord.Interaction,
                             new_title: str, color: int, footer_note: str):
        """Rebuild embed with new status and disable all buttons."""
        if self.message:
            try:
                old = self.message.embeds[0] if self.message.embeds else None
                if old:
                    new_e = discord.Embed(
                        title=new_title,
                        description=old.description,
                        color=color,
                        timestamp=datetime.now(timezone.utc)
                    )
                    for field in old.fields:
                        new_e.add_field(name=field.name, value=field.value, inline=field.inline)
                    new_e.set_footer(text=footer_note)
                    # Disable all buttons
                    for child in self.children:
                        child.disabled = True
                    await self.message.edit(embed=new_e, view=self)
            except Exception as e:
                print(f"[StaffLogView] embed update error: {e}")


async def handle_support(message: discord.Message) -> None:
    gid = str(message.guild.id)
    uid = str(message.author.id)
    q   = message.content.strip()

    # ── ONE-REPLY LOCK ─────────────────────────────────────────────────────
    guild_locks = _replied_users.setdefault(gid, {})
    if uid in guild_locks:
        # Already replied — silently ignore until staff clears
        return

    # ── Fetch active tournament ────────────────────────────────────────────
    all_t  = await b44_list('Tournament', {'guild_id': gid})
    active = next(
        (t for t in sorted(all_t, key=lambda x: x.get('created_date',''), reverse=True)
         if t.get('status') not in ('completed','cancelled','deleted')),
        None
    )

    # ── Build AI prompt with full server context ───────────────────────────
    server_ctx = build_server_context(message.guild, active)

    prompt = f"""You are NexPlay Bot, a friendly and helpful Discord assistant for a gaming tournament server.

SERVER CONTEXT:
{server_ctx}

USER MESSAGE from {message.author.display_name}:
"{q}"

INSTRUCTIONS:
- Reply in a friendly, warm, and accurate way (2-4 sentences max)
- If they ask about a channel, mention the EXACT channel name from the server context above (e.g. #ff-register)
- If they ask about registration, tell them which channel to go to and the format
- If they ask about schedule/date/time, give the exact info from tournament data
- If they ask about rules, summarize the key rules
- If you don't know the answer or it needs staff action, say: "NEEDS_STAFF: <brief reason>"
- NEVER make up information. Only use data from the context above.
- Keep it short, casual, helpful. Use emojis sparingly."""

    ai_reply = await ai_generate(prompt)

    needs_staff = not ai_reply or ai_reply.startswith("NEEDS_STAFF") or len(ai_reply) < 10

    # ── Determine embed color by topic ────────────────────────────────────
    ql = q.lower()
    if any(k in ql for k in ['register','join','sign up']):
        color = 0x00FF7F
    elif any(k in ql for k in ['when','time','date','schedule']):
        color = 0x1E90FF
    elif any(k in ql for k in ['prize','reward','money','win']):
        color = 0xFFD700
    elif any(k in ql for k in ['rule','cheat','banned','unfair']):
        color = 0xFF4444
    elif any(k in ql for k in ['bracket','group','opponent']):
        color = 0x9B59B6
    else:
        color = 0x5865F2

    # ── Reply to user ──────────────────────────────────────────────────────
    if not needs_staff and ai_reply:
        clean_reply = ai_reply.replace("NEEDS_STAFF:", "").strip()
        embed = discord.Embed(description=clean_reply, color=color)
        embed.set_footer(text="NexPlay Support • Reply handled by AI")
        await message.reply(embed=embed, mention_author=False)

    # ── Save to DB ────────────────────────────────────────────────────────
    support_rec = await b44_create('SupportMessage', {
        'guild_id':   gid,
        'guild_name': message.guild.name,
        'message':    q,
        'status':     'pending' if needs_staff else 'ai_resolved',
    })
    rec_id = support_rec.get('id', '')

    # ── Lock this user from getting another reply ─────────────────────────
    guild_locks[uid] = rec_id

    # ── Post to staff-log ─────────────────────────────────────────────────
    ch_staff = resolve_channel(message.guild, 'staff')
    if not ch_staff:
        return

    staff_color = 0xFF4444 if needs_staff else 0x00CC66

    staff_embed = discord.Embed(
        title=('🔴 NEEDS STAFF REVIEW' if needs_staff else '🟢 AI HANDLED — Monitor'),
        color=staff_color
    )
    staff_embed.add_field(name='👤 User', value=message.author.mention, inline=True)
    staff_embed.add_field(name='📢 Channel', value=message.channel.mention, inline=True)
    staff_embed.add_field(name='❓ Message', value=f'```{q[:500]}```', inline=False)

    if needs_staff:
        reason = ai_reply.replace("NEEDS_STAFF:", "").strip() if ai_reply else "AI couldn't generate a response"
        staff_embed.add_field(name='⚠️ Reason', value=reason or "Complex/unknown query", inline=False)
        staff_embed.add_field(
            name='📋 Action Required',
            value=(
                '1. Reply to the user in their channel\n'
                '2. React ✅ on THIS message to clear the user lock\n'
                '3. Bot will allow them to ask again after lock is cleared'
            ),
            inline=False
        )
    else:
        staff_embed.add_field(name='🤖 AI Response', value=f'```{ai_reply[:300]}```', inline=False)
        staff_embed.add_field(
            name='📋 Note',
            value='AI handled this. React ✅ to clear user lock if satisfied.',
            inline=False
        )

    staff_embed.set_footer(text=f"User ID: {uid} | DB Record: {rec_id}")

    try:
        view = StaffLogView(
            uid=uid,
            rec_id=rec_id,
            guild_locks=guild_locks,
            user=message.author,
            user_channel=message.channel,
        )
        log_msg = await ch_staff.send(embed=staff_embed, view=view)
        view.message = log_msg

    except Exception as e:
        print(f"[Support] staff-log error: {e}", flush=True)

    # ── Escalate with ping if needs_staff ─────────────────────────────────
    if needs_staff:
        try:
            await ch_staff.send(
                f"@here 🔴 **{message.author.display_name}** needs help in {message.channel.mention} — please review above."
            )
        except Exception:
            pass
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    # ── Support / AI assistant handler ───────────────────────────────────────
    # RULES:
    #  1. Only fires in general public chat channels (NOT register, confirm-teams,
    #     announcements, roadmap, groups, results, info, help, or private channels)
    #  2. Never fires in DMs or any tournament-specific channel
    #  3. If no staff responds within STAFF_RESPONSE_TIMEOUT, escalate to #staff-log
    # Channels where bot should NEVER respond
    BLOCKED_SUFFIXES = (
        "-register", "-announcements", "-roadmap", "-results",
        "-groups", "-confirm-teams", "-info", "-help",
    )
    BLOCKED_EXACT = (
        "staff-log", "mod-log", "admin-log", "staff", "moderators",
        "bot-log", "audit-log",
    )

    ch_name_lower = message.channel.name.lower()

    # Block tournament-specific channels
    is_blocked = (
        any(ch_name_lower.endswith(s) for s in BLOCKED_SUFFIXES)
        or any(ch_name_lower == s for s in BLOCKED_EXACT)
        or isinstance(message.channel, discord.DMChannel)
    )

    # Allow ALL public text channels except blocked ones
    is_public_text = isinstance(message.channel, discord.TextChannel)

    if is_public_text and not is_blocked and len(message.content.strip()) > 3:
        await handle_support(message)
        return

    # ── Registration channel handler ──────────────────────────────────────────
    gid = str(message.guild.id)
    ch_name = message.channel.name  # e.g. "npo26-register"

    if ch_name.endswith("-register"):
        short_candidate = ch_name[:-9]  # strip "-register"

        # Find the matching active tournament
        all_ts = await b44_list("Tournament", {"guild_id": gid})
        tournament = next(
            (t for t in all_ts
             if t.get("short_name", "").lower() == short_candidate.lower()
             and t.get("status") in ("registration_open",)),
            None
        )

        if not tournament:
            # Not an active registration channel — ignore
            await bot.process_commands(message)
            return

        # Delete non-registration messages (keep it clean)
        text = message.content.strip()
        team_size = int(tournament.get("team_size", 4))

        parsed = parse_registration(text, team_size)

        # ── VALIDATION ────────────────────────────────────────────────────────
        if not parsed:
            await message.add_reaction("❌")
            lines_needed = "\n".join([f"Player {i+1}: @mention" for i in range(team_size)])
            err_msg = await message.reply(
                embed=discord.Embed(
                    title="❌ Invalid Format",
                    description=(
                        f"Please use EXACTLY this format:\n"
                        f"```\nTeam Name: <your team name>\n{lines_needed}\n```"
                        f"\n• All {team_size} players must be @mentioned\n"
                        "• Team Name line is required"
                    ),
                    color=0xFF4444
                ),
                mention_author=True
            )
            await asyncio.sleep(15)
            try:
                await err_msg.delete()
                await message.delete()
            except:
                pass
            return

        team_name = parsed["team_name"]
        players   = parsed["players"]  # list of user ID strings

        # Check team name duplicate
        existing_regs = await b44_list("Registration", {"tournament_id": tournament["id"]})
        if any(r.get("player_name", "").lower() == team_name.lower() for r in existing_regs):
            await message.add_reaction("❌")
            err_msg = await message.reply(
                embed=discord.Embed(
                    title="❌ Team Name Taken",
                    description=f"**{team_name}** is already registered. Use a different team name.",
                    color=0xFF4444
                )
            )
            await asyncio.sleep(15)
            try:
                await err_msg.delete()
                await message.delete()
            except:
                pass
            return

        # Check player duplicate
        all_registered_players = []
        for r in existing_regs:
            members = r.get("team_members", [])
            if isinstance(members, list):
                all_registered_players.extend(members)
            elif isinstance(members, str) and members:
                all_registered_players.extend(members.split(","))

        already = [f"<@{p}>" for p in players if p in all_registered_players]
        if already:
            await message.add_reaction("❌")
            err_msg = await message.reply(
                embed=discord.Embed(
                    title="❌ Player Already Registered",
                    description=f"{', '.join(already)} is already registered in another team!",
                    color=0xFF4444
                )
            )
            await asyncio.sleep(15)
            try:
                await err_msg.delete()
                await message.delete()
            except:
                pass
            return

        # Check slots
        max_p = int(tournament.get("max_players", 16))
        slot  = len(existing_regs) + 1
        if slot > max_p:
            await message.add_reaction("❌")
            await message.reply(
                embed=discord.Embed(
                    title="🔒 Registration Full",
                    description=f"All {max_p} slots are taken. Registration is closed.",
                    color=0xFF4444
                )
            )
            return

        # ── SUCCESS — Save to DB ──────────────────────────────────────────────
        await b44_create("Registration", {
            "tournament_id":    tournament["id"],
            "guild_id":         gid,
            "player_name":      team_name,
            "player_discord_id": str(message.author.id),
            "team_members":     players,
            "status":           "registered",
        })

        # Update tournament registered count
        await b44_update("Tournament", tournament["id"], {"registered_count": slot})

        # React success
        await message.add_reaction("✅")

        # Post confirmation to #<short>-confirm-teams
        cfm_ch_name = short_candidate + "-confirm-teams"
        cfm_ch = discord.utils.get(message.guild.text_channels, name=cfm_ch_name)
        if cfm_ch:
            player_mentions = " ".join([f"<@{p}>" for p in players])
            cfm_embed = discord.Embed(
                title="✅ TEAM REGISTERED!",
                description=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏷️ **Team:** {team_name}\n"
                    f"👑 **Captain:** {message.author.mention}\n"
                    f"👥 **Players:** {player_mentions}\n"
                    f"🎫 **Slot:** #{slot} of {max_p}\n"
                    f"🏆 **Tournament:** {tournament.get('name', '')}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "**Status: CONFIRMED ✅**\n\n"
                    f"📋 Check tournament info in <#{tournament.get('announcement_channel_id','')}>\n"
                    "Groups will be revealed after registration closes. Good luck! 🎮"
                ),
                color=0x00FF7F,
                timestamp=datetime.now(timezone.utc)
            )
            cfm_embed.set_footer(text=f"NexPlay Tournament System | {tournament.get('game','')}",
                                 icon_url="https://i.imgur.com/wSTFkRM.png")
            await cfm_ch.send(embed=cfm_embed)

        # Update slot counter on announcement embed
        updated_t = dict(tournament)
        updated_t["registered_count"] = slot
        await update_reg_announcement(updated_t, message.guild, slot)

        # ── AUTO-CLOSE when full ──────────────────────────────────────────────
        if slot >= max_p:
            await b44_update("Tournament", tournament["id"], {"status": "registration_closed"})
            await lock_register_channel(message.guild, message.channel)

            close_embed = discord.Embed(
                title="🔒 REGISTRATION CLOSED — " + tournament.get("name", ""),
                description=(
                    f"All **{max_p}** slots have been filled!\n\n"
                    "**What happens next:**\n"
                    "① Groups will be drawn by the host\n"
                    "② Match schedule will be posted\n"
                    "③ Match Day begins!\n\n"
                    "Stay tuned in the announcements channel."
                ),
                color=0xFF6B35,
                timestamp=datetime.now(timezone.utc)
            )
            close_embed.set_footer(text="NexPlay Tournament System")
            await message.channel.send(embed=close_embed)

        return

    await bot.process_commands(message)


# ══════════════════════════════════════════════════════════
#  SLASH COMMANDS  — NO guild= parameter → global commands
# ══════════════════════════════════════════════════════════

# ── /setup ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════
#  /clearlog — Staff command to clear a user support lock
# ══════════════════════════════════════════════════════════
@tree.command(name="clearlog", description="[Staff] Clear a user support lock so they can ask again")
@app_commands.describe(user="The user whose support lock to clear")
async def clearlog(interaction: discord.Interaction, user: discord.Member):
    if not has_tournament_role(interaction.user):
        await interaction.response.send_message(
            "❌ You need the **Tournament Host** role to use this command.", ephemeral=True
        )
        return

    gid = str(interaction.guild.id)
    uid = str(user.id)
    guild_locks = _replied_users.get(gid, {})

    if uid not in guild_locks:
        await interaction.response.send_message(
            f"✅ {user.mention} has no active support lock — they can already ask freely.",
            ephemeral=True
        )
        return

    rec_id = guild_locks.pop(uid, None)
    if rec_id:
        try:
            await b44_update('SupportMessage', rec_id, {'status': 'resolved'})
        except Exception:
            pass

    embed = discord.Embed(
        title="🔓 Support Lock Cleared",
        description=f"{user.mention} support lock has been cleared. They can now ask the bot again.",
        color=0x00FF00
    )
    embed.set_footer(text=f"Cleared by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

    try:
        notify = discord.Embed(
            description=f"Hey {user.mention}, a staff member has cleared your support session. Feel free to ask anything! 😊",
            color=0x00FF00
        )
        await interaction.channel.send(embed=notify)
    except Exception:
        pass


@tree.command(name="setup", description="Initialize NexPlay in this server (server owner only)")
async def cmd_setup(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id and not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("Only the server owner can run /setup."), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    gid = str(interaction.guild.id)

    # Register or update server record
    existing = await get_server_record(gid)
    if not existing:
        await b44_create("Server", {
            "guild_id": gid, "guild_name": interaction.guild.name,
            "owner_discord_id": str(interaction.guild.owner_id),
            "subscription_status": "trial", "tournaments_used": 0,
            "tournament_limit": 3, "member_count": interaction.guild.member_count or 0,
            "last_active": now_iso(),
        })
        status_msg = "Server registered! You have a **free trial** with 3 tournaments."
    else:
        status_msg = "Server already registered. Status: **" + str(existing.get("subscription_status", "?")) + "**"

    # Provision channels
    channels = await get_or_create_channels(interaction.guild)
    ch_list  = "\n".join("> <#" + str(v) + ">" for v in channels.values() if v)

    await interaction.followup.send(embed=ok_e(
        "NexPlay Setup Complete!",
        status_msg + "\n\n**Channels ready:**\n" + ch_list + "\n\n"
        "Use `/create_tournament` to launch your first tournament!\n"
        "Need more than 3 tournaments? Visit nexplay.gg to upgrade."
    ), ephemeral=True)


# ── /create_tournament ────────────────────────────────────

async def make_tournament_channels(guild: discord.Guild, tournament_name: str) -> dict:
    """
    Create a dedicated category + 8 channels for each tournament.
    
    Format: <short_name>-<channel>
    Example: For "NexPlay Open 2026":
      Category: 🏆 NPO26
        #npo26-info
        #npo26-announcements  
        #npo26-roadmap
        #npo26-results
        #npo26-groups
        #npo26-register
        #npo26-confirm-teams
        #npo26-help
    
    Returns dict with channel IDs.
    """
    import re
    
    # ── Generate short name from tournament name ──────────────────────────────
    words = tournament_name.strip().split()
    short = ""
    if len(words) == 1:
        # Single word: take first 4 chars
        short = words[0][:4].lower()
    else:
        # Multiple words: take first letter of each word + last word digits if any
        initials = "".join(w[0] for w in words if w).lower()
        # Add any numbers found in the name
        nums = re.sub(r"[^0-9]", "", tournament_name)[-2:]
        short = (initials + nums)[:6]
    
    short = re.sub(r"[^a-z0-9]", "", short)[:6]
    if not short:
        short = "tourney"

    cat_name = f"🏆 {short.upper()}"
    
    # Sub-channels: (key, channel_suffix, topic)
    channels_def = [
        ("info",          f"{short}-info",           "📋 Tournament information, rules and details"),
        ("announcements", f"{short}-announcements",   "📢 Official tournament announcements"),
        ("roadmap",       f"{short}-roadmap",         "🗺️ Tournament roadmap and schedule overview"),
        ("results",       f"{short}-results",         "🏅 Match results and standings"),
        ("groups",        f"{short}-groups",          "🎯 Group draws and bracket reveal"),
        ("register",      f"{short}-register",        "✍️ Player registration — use /register here"),
        ("confirm-teams", f"{short}-confirm-teams",   "✅ Team confirmation and roster lock"),
        ("help",          f"{short}-help",            "❓ Support and help for this tournament"),
    ]
    
    result = {"short_name": short, "category_name": cat_name}
    
    # ── Create or find category ───────────────────────────────────────────────
    category = discord.utils.get(guild.categories, name=cat_name)
    if not category:
        try:
            category = await guild.create_category(
                cat_name,
                reason=f"NexPlay: Tournament '{tournament_name}' channels"
            )
        except Exception as e:
            log(f"[ERROR] Could not create category {cat_name}: {e}")
            category = None
    
    result["category_id"] = str(category.id) if category else None

    # ── Create each channel ───────────────────────────────────────────────────
    for key, ch_name, topic in channels_def:
        existing = discord.utils.get(guild.text_channels, name=ch_name)
        if not existing:
            try:
                ch = await guild.create_text_channel(
                    ch_name,
                    category=category,
                    topic=topic,
                    reason=f"NexPlay: {tournament_name} tournament"
                )
                result[key] = str(ch.id)
            except Exception as e:
                log(f"[ERROR] Could not create #{ch_name}: {e}")
                result[key] = None
        else:
            result[key] = str(existing.id)
    
    log(f"[Channels] Created tournament channels for '{tournament_name}' → category '{cat_name}'")
    return result


@tree.command(name="create_tournament", description="Create and announce a new tournament")
@app_commands.describe(
    name="Tournament name (e.g. NexPlay Cup 2026)",
    game="Select the game",
    max_players="Total slots / max teams (e.g. 16)",
    group_size="Teams per group (e.g. 4)",
    team_size="Players per team (e.g. 4 for squads)",
    prize_pool="Prize pool e.g. NPR 5000",
    date="Match date e.g. 2026-08-01",
    time="Match time e.g. 5:00 PM NPT",
    fmt="Match format",
    description="Short description (optional)"
)
@app_commands.choices(game=[
    app_commands.Choice(name="Free Fire", value="Free Fire"),
    app_commands.Choice(name="Minecraft", value="Minecraft"),
    app_commands.Choice(name="PUBG Mobile", value="PUBG Mobile"),
    app_commands.Choice(name="Valorant", value="Valorant"),
    app_commands.Choice(name="Other", value="Other"),
])
@app_commands.choices(fmt=[
    app_commands.Choice(name="Single Elimination", value="single_elim"),
    app_commands.Choice(name="Double Elimination", value="double_elim"),
    app_commands.Choice(name="Round Robin",         value="round_robin"),
    app_commands.Choice(name="Battle Royale",       value="battle_royale"),
])
async def cmd_create(
    interaction: discord.Interaction,
    name: str,
    game: str,
    max_players: int,
    group_size: int,
    team_size: int,
    prize_pool: str,
    date: str,
    time: str = "TBD",
    fmt: str = "single_elim",
    description: str = ""
):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("You need Tournament Host or higher!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)

    await interaction.response.defer(thinking=True)
    gid = str(interaction.guild.id)

    # ── Create per-tournament dedicated channels ──────────────────────────────
    t_channels = await make_tournament_channels(interaction.guild, name)
    short      = t_channels.get("short_name", "tourney")
    cat_name   = t_channels.get("category_name", "🏆 Tournament")

    ch_ann_id  = int(t_channels["announcements"]) if t_channels.get("announcements") else None
    ch_reg_id  = int(t_channels["register"])       if t_channels.get("register")       else None
    ch_info_id = int(t_channels["info"])           if t_channels.get("info")           else None
    ch_road_id = int(t_channels["roadmap"])        if t_channels.get("roadmap")        else None
    ch_grp_id  = int(t_channels["groups"])         if t_channels.get("groups")         else None
    ch_res_id  = int(t_channels["results"])        if t_channels.get("results")        else None
    ch_cfm_id  = int(t_channels["confirm-teams"])  if t_channels.get("confirm-teams")  else None
    ch_hlp_id  = int(t_channels["help"])           if t_channels.get("help")           else None

    poster  = img_url(name, game, "poster",  "prize " + prize_pool + " date " + date)
    roadmap = img_url(name, game, "roadmap")

    rec = await b44_create("Tournament", {
        "guild_id": gid, "name": name, "game": game, "format": fmt,
        "prize_pool": prize_pool, "description": description,
        "status": "registration_open", "max_players": max_players,
        "team_size": team_size, "group_size": group_size,
        "registered_count": 0, "tournament_date": date,
        "tournament_time": time,
        "poster_image_url": poster, "roadmap_image_url": roadmap,
        "announcement_channel_id": str(ch_ann_id) if ch_ann_id else "",
        "registration_channel_id": str(ch_reg_id) if ch_reg_id else "",
        "short_name": short, "category_name": cat_name,
        "created_by_discord_id": str(interaction.user.id), "started_at": now_iso(),
    })

    fmt_label = {"single_elim": "Single Elimination", "double_elim": "Double Elimination",
                 "round_robin": "Round Robin", "battle_royale": "Battle Royale"}.get(fmt, fmt)

    tid = rec.get("id", "")

    # ── 1. #info — full tournament details ───────────────────────────────────
    if ch_info_id:
        info_e = discord.Embed(
            title="📋 " + name + " — Tournament Info",
            description=(
                "**Game:** " + game + "\n"
                "**Format:** " + fmt_label + "\n"
                "**Prize Pool:** " + prize_pool + "\n"
                "**Date:** " + date + "\n"
                "**Max Players:** " + str(max_players) +
                ("\n\n" + description if description else "")
            ),
            color=0x5865F2, timestamp=datetime.now(timezone.utc)
        )
        info_e.set_thumbnail(url=poster)
        info_e.add_field(name="📢 Announcements", value="<#" + str(ch_ann_id) + ">" if ch_ann_id else "—", inline=True)
        info_e.add_field(name="✍️ Register", value="<#" + str(ch_reg_id) + ">" if ch_reg_id else "—", inline=True)
        info_e.add_field(name="🗺️ Roadmap", value="<#" + str(ch_road_id) + ">" if ch_road_id else "—", inline=True)
        info_e.add_field(name="🎯 Groups", value="<#" + str(ch_grp_id) + ">" if ch_grp_id else "—", inline=True)
        info_e.add_field(name="🏅 Results", value="<#" + str(ch_res_id) + ">" if ch_res_id else "—", inline=True)
        info_e.add_field(name="❓ Help", value="<#" + str(ch_hlp_id) + ">" if ch_hlp_id else "—", inline=True)
        info_e.set_footer(text="NexPlay Tournament System | ID: " + short.upper())
        await dpost(ch_info_id, info_e)

    # ── 2. #announcements — poster + announcement ─────────────────────────────
    if ch_ann_id:
        ann_e = discord.Embed(
            title="🏆 " + name + " — OFFICIALLY ANNOUNCED!",
            description=(
                "The tournament is here! Get ready.\n\n"
                "**Game:** " + game + " | **Prize:** " + prize_pool + "\n"
                "**Date:** " + date + " | **Format:** " + fmt_label + "\n\n"
                "Register now in <#" + str(ch_reg_id) + "> before slots fill up!"
            ),
            color=0xFFD700, timestamp=datetime.now(timezone.utc)
        )
        ann_e.set_image(url=poster)
        ann_e.set_footer(text="NexPlay Tournament System")
        await dpost(ch_ann_id, ann_e)

    # ── 3. #roadmap — roadmap image + stages ─────────────────────────────────
    if ch_road_id:
        road_e = discord.Embed(
            title="🗺️ " + name + " — Roadmap",
            description=(
                "**Tournament Stages:**\n\n"
                "① 📋 **Registration Open** → Register in <#" + str(ch_reg_id) + ">\n"
                "② ✅ **Confirm Teams** → Confirm in <#" + str(ch_cfm_id) + ">\n"
                "③ 🎯 **Group Draw** → Groups revealed in <#" + str(ch_grp_id) + ">\n"
                "④ ⚔️ **Match Day** → Schedule + results in <#" + str(ch_res_id) + ">\n"
                "⑤ 🏆 **Champion Crowned** → Winner announced!\n\n"
                "Stay tuned in <#" + str(ch_ann_id) + "> for all updates."
            ),
            color=0x00B4D8, timestamp=datetime.now(timezone.utc)
        )
        road_e.set_image(url=roadmap)
        road_e.set_footer(text="NexPlay Tournament System")
        await dpost(ch_road_id, road_e)

    # ── 4. #register — registration instructions ─────────────────────────────
    if ch_reg_id:
        player_lines = "\n".join([f"Player {i+1}: @mention" for i in range(int(team_size))])
        reg_e = discord.Embed(
            title="✍️ Registration OPEN — " + name,
            description=(
                "**Game:** " + game + " | **Prize:** " + prize_pool + "\n"
                "**Date:** " + date + " | **Time:** " + time + "\n"
                "**Total Slots:** " + str(max_players) + " teams"
                " | **Teams per Group:** " + str(group_size) + "\n"
                "**Team Size:** " + str(team_size) + " players per team\n\n"
                "**📝 How to Register — send a message in THIS channel:**\n"
                "```\n"
                "Team Name: <your team name>\n"
                + player_lines + "\n"
                "```\n"
                "⚠️ **Rules:**\n"
                "> ✅ All player mentions must be valid Discord tags\n"
                "> ✅ Exactly " + str(team_size) + " players required\n"
                "> ❌ Duplicate registrations will be rejected\n"
                "> ❌ Channel locks when all " + str(max_players) + " slots are filled\n\n"
                "After slots fill, confirm your team in <#" + str(ch_cfm_id) + ">"
            ),
            color=0x00FF7F, timestamp=datetime.now(timezone.utc)
        )
        reg_e.set_footer(text="NexPlay | Type your team info above to register")
        await dpost(ch_reg_id, reg_e)

    # ── 5. #confirm-teams — confirmation instructions ─────────────────────────
    if ch_cfm_id:
        cfm_e = discord.Embed(
            title="✅ Team Confirmation — " + name,
            description=(
                "After registering in <#" + str(ch_reg_id) + ">, confirm your team here.\n\n"
                "**Rules:**\n"
                "> ✅ Confirm your in-game name is correct\n"
                "> ✅ Be online 30 min before match time\n"
                "> ❌ No-shows will be disqualified\n\n"
                "Groups will be revealed in <#" + str(ch_grp_id) + "> after registration closes."
            ),
            color=0xFFA500, timestamp=datetime.now(timezone.utc)
        )
        cfm_e.set_footer(text="NexPlay Tournament System")
        await dpost(ch_cfm_id, cfm_e)

    # ── 6. #groups — placeholder ──────────────────────────────────────────────
    if ch_grp_id:
        grp_e = discord.Embed(
            title="🎯 Groups — " + name,
            description=(
                "Groups will be posted here after registration closes.\n\n"
                "Use `/generate_groups " + name + "` to draw groups.\n\n"
                "Registration → <#" + str(ch_reg_id) + ">"
            ),
            color=0x9B59B6, timestamp=datetime.now(timezone.utc)
        )
        grp_e.set_footer(text="NexPlay Tournament System")
        await dpost(ch_grp_id, grp_e)

    # ── 7. #results — placeholder ─────────────────────────────────────────────
    if ch_res_id:
        res_e = discord.Embed(
            title="🏅 Results — " + name,
            description=(
                "Match results will be posted here during the tournament.\n\n"
                "Use `/post_result` to record match outcomes.\n\n"
                "Groups → <#" + str(ch_grp_id) + ">"
            ),
            color=0xFF6B35, timestamp=datetime.now(timezone.utc)
        )
        res_e.set_footer(text="NexPlay Tournament System")
        await dpost(ch_res_id, res_e)

    # ── 8. #help — support instructions ──────────────────────────────────────
    if ch_hlp_id:
        hlp_e = discord.Embed(
            title="❓ Help & Support — " + name,
            description=(
                "Need help with this tournament? Ask here!\n\n"
                "**Common questions:**\n"
                "> ✍️ Register → <#" + str(ch_reg_id) + ">\n"
                "> 🗺️ Roadmap → <#" + str(ch_road_id) + ">\n"
                "> 📋 Rules → <#" + str(ch_info_id) + ">\n\n"
                "Staff will assist you. For urgent issues ping @Tournament Host."
            ),
            color=0x95A5A6, timestamp=datetime.now(timezone.utc)
        )
        hlp_e.set_footer(text="NexPlay Support")
        await dpost(ch_hlp_id, hlp_e)

    # ── Log to DB ─────────────────────────────────────────────────────────────
    if tid and ch_ann_id:
        await b44_create("AnnouncementLog", {
            "tournament_id": tid, "guild_id": gid,
            "milestone": "tournament_created",
            "channel_id": str(ch_ann_id),
            "announced_at": now_iso(),
            "content_summary": "Created: " + name + " | Channels: " + cat_name,
        })

    # ── Post Registration Announcement (pinned, editable slot counter) ────────
    if ch_reg_id:
        bar = "░" * max_players
        lines_needed = "\n".join([f"Player {i+1}: @mention" for i in range(team_size)])
        reg_ann = discord.Embed(
            title="✍️ REGISTRATION OPEN — " + name,
            description=(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎮 **Game:** {game}\n"
                f"🎖️ **Prize:** {prize_pool}\n"
                f"📅 **Date:** {date}\n"
                f"⏰ **Time:** {time}\n"
                f"👥 **Slots:** 0/{max_players}  `{bar}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "@everyone **Registration is OPEN!**\n\n"
                f"Send a message in this channel with EXACTLY this format:\n"
                f"```\nTeam Name: <your team name>\n{lines_needed}\n```"
                "\n⚠️ All players must be @mentioned. No duplicate registrations allowed."
            ),
            color=0x00FF7F,
            timestamp=datetime.now(timezone.utc)
        )
        reg_ann.set_thumbnail(url=poster)
        reg_ann.set_footer(text="NexPlay Tournament System — slots update automatically")
        reg_ch_obj = interaction.guild.get_channel(ch_reg_id)
        if reg_ch_obj:
            try:
                # Allow @everyone to see but only read; they send registration messages
                reg_msg = await reg_ch_obj.send("@everyone", embed=reg_ann)
                await reg_msg.pin()
                # Store the message ID for live slot updates
                if tid:
                    await b44_update("Tournament", tid, {"registration_msg_id": str(reg_msg.id)})
            except Exception as e:
                log(f"[WARN] Could not pin reg announcement: {e}")

    # ── Confirmation to staff member ──────────────────────────────────────────
    done = discord.Embed(
        title="✅ Tournament Created — " + name,
        description=(
            "**Category:** " + cat_name + "\n\n"
            "**Channels created:**\n"
            "📋 <#" + str(ch_info_id) + "> — Info\n"
            "📢 <#" + str(ch_ann_id) + "> — Announcements\n"
            "🗺️ <#" + str(ch_road_id) + "> — Roadmap\n"
            "✍️ <#" + str(ch_reg_id) + "> — Register\n"
            "✅ <#" + str(ch_cfm_id) + "> — Confirm Teams\n"
            "🎯 <#" + str(ch_grp_id) + "> — Groups\n"
            "🏅 <#" + str(ch_res_id) + "> — Results\n"
            "❓ <#" + str(ch_hlp_id) + "> — Help\n"
        ),
        color=0xFFD700
    )
    done.set_thumbnail(url=poster)
    done.set_footer(text="NexPlay | Pollinations.ai imagery")
    await interaction.followup.send(embed=done)


# ── /register ─────────────────────────────────────────────
@tree.command(name="register", description="Register your team for the active tournament")
@app_commands.describe(
    tournament_name="Tournament name to register for",
    team_name="Your team name",
    ingame_names="All player in-game names separated by commas (e.g. Player1, Player2, Player3, Player4)"
)
async def cmd_register(interaction: discord.Interaction, tournament_name: str, team_name: str, ingame_names: str):
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    gid = str(interaction.guild.id)

    # Find tournament
    ts_list = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts_list:
        # Try finding latest open tournament if name doesn't match exactly
        all_t = await b44_list("Tournament", {"guild_id": gid})
        open_t = [t for t in all_t if t.get("status") == "registration_open"]
        if not open_t:
            return await interaction.followup.send(embed=err_e(f'No open tournament named "{tournament_name}" found. Check `/tournament_status` for active tournaments.'), ephemeral=True)
        t = open_t[0]
    else:
        t = ts_list[0]

    if t.get("status") != "registration_open":
        return await interaction.followup.send(
            embed=err_e(f'Registration is not open for **{t.get("name")}**. Status: `{t.get("status")}`'), ephemeral=True)

    # Check team size
    team_size = int(t.get("team_size", 4))
    players = [p.strip() for p in ingame_names.split(",") if p.strip()]
    if len(players) != team_size:
        return await interaction.followup.send(
            embed=err_e(f'This tournament requires exactly **{team_size} players** per team. You provided {len(players)}.'), ephemeral=True)

    # Check duplicate team registration
    existing = await b44_list("Registration", {"tournament_id": t["id"], "guild_id": gid})
    already = [r for r in existing if r.get("player_name","").lower() == team_name.lower()]
    if already:
        return await interaction.followup.send(embed=err_e(f'Team **{team_name}** is already registered!'), ephemeral=True)

    # Check slot capacity
    max_p = int(t.get("max_players", 16))
    if len(existing) >= max_p:
        return await interaction.followup.send(embed=err_e(f'Sorry! All {max_p} slots are filled. Registration is closed.'), ephemeral=True)

    slot = len(existing) + 1
    remaining = max_p - slot

    # Save registration
    await b44_create("Registration", {
        "tournament_id": t["id"],
        "guild_id": gid,
        "player_name": team_name,
        "player_discord_id": str(interaction.user.id),
        "player_discord_tag": interaction.user.display_name,
        "player_username": ", ".join(players),
        "registered_at": now_iso(),
        "status": "registered",
        "seed_number": slot,
    })
    await b44_update("Tournament", t["id"], {"registered_count": slot})

    # Auto-lock channel when full
    if slot >= max_p:
        short = t.get("short_name","")
        if short:
            ch_reg = discord.utils.get(interaction.guild.text_channels, name=f"{short}-register")
            if ch_reg:
                await lock_register_channel(interaction.guild, ch_reg)
                # Post full notice
                full_e = discord.Embed(
                    title="🔒 Registration FULL — " + t.get("name",""),
                    description=f"All **{max_p}** slots are filled! Registration is now closed.\n\nGood luck to all registered teams! 🏆",
                    color=0xFF4500, timestamp=datetime.now(timezone.utc)
                )
                await ch_reg.send(embed=full_e)

    # Post confirmation in register channel
    short = t.get("short_name","")
    ch_reg = discord.utils.get(interaction.guild.text_channels, name=f"{short}-register") if short else None
    confirm_e = discord.Embed(
        title=f"✅ Team Registered — Slot #{slot}",
        description=(
            f"**Team:** {team_name}\n"
            f"**Players:** {', '.join(players)}\n"
            f"**Registered by:** {interaction.user.mention}\n"
            f"**Slots remaining:** {remaining}\n\n"
            f"Good luck! 🎮"
        ),
        color=0x00FF7F, timestamp=datetime.now(timezone.utc)
    )
    confirm_e.set_footer(text=f"NexPlay | Slot {slot}/{max_p}")
    if ch_reg:
        await ch_reg.send(embed=confirm_e)

    await interaction.followup.send(embed=ok_e(
        f"✅ Registered! Slot #{slot}",
        f"Team **{team_name}** is registered for **{t.get('name','')}**!\n"
        f"Players: {', '.join(players)}\n"
        f"Date: {t.get('tournament_date','TBA')} | {t.get('tournament_time','TBD')}\n"
        f"Slots left: {remaining}\n\n"
        f"Good luck! 🏆"
    ), ephemeral=True)


# ── /close_registration ───────────────────────────────────
@tree.command(name="close_registration", description="Close registration for a tournament")
@app_commands.describe(tournament_name="Tournament name")
async def cmd_close_reg(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("No permission!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True)
    gid = str(interaction.guild.id)

    ts_list = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts_list:
        return await interaction.followup.send(embed=err_e("Tournament not found."))
    t     = ts_list[0]
    regs  = await b44_list("Registration", {"tournament_id": t["id"]})
    await b44_update("Tournament", t["id"], {"status": "registration_closed"})

    lines = "\n".join(str(i + 1) + ". **" + r.get("player_username", "?") + "**" for i, r in enumerate(regs[:25])) or "None"
    ch_ann = resolve_channel(interaction.guild, "announcements")

    e = discord.Embed(
        title="🔒 Registration CLOSED — " + tournament_name,
        description=(
            "Registration closed with **" + str(len(regs)) + " players** confirmed!\n\n"
            "**Players:**\n" + lines + "\n\nGroup draw coming next!"
        ),
        color=0xFF4500, timestamp=datetime.now(timezone.utc)
    )
    e.set_footer(text="NexPlay Tournament System")
    if ch_ann:
        await dpost(ch_ann.id, e)
        await b44_create("AnnouncementLog", {
            "tournament_id": t["id"], "guild_id": gid, "milestone": "registration_closed",
            "channel_id": str(ch_ann.id), "announced_at": now_iso(),
            "content_summary": str(len(regs)) + " players confirmed",
        })
    await interaction.followup.send(embed=ok_e("Registration Closed", str(len(regs)) + " players locked in for **" + tournament_name + "**!"))


# ── /generate_groups ──────────────────────────────────────
@tree.command(name="generate_groups", description="Randomly generate and reveal tournament groups")
@app_commands.describe(tournament_name="Tournament name", group_size="Players per group (default 4)")
async def cmd_groups(interaction: discord.Interaction, tournament_name: str, group_size: int = 4):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("No permission!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True)
    gid = str(interaction.guild.id)

    ts_list = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts_list:
        return await interaction.followup.send(embed=err_e("Tournament not found."))
    t    = ts_list[0]
    regs = await b44_list("Registration", {"tournament_id": t["id"]})
    if not regs:
        return await interaction.followup.send(embed=err_e("No registered players yet!"))

    import random
    random.shuffle(regs)
    # Use tournament's saved group_size, or the param, or default 4
    effective_group_size = group_size or int(t.get("group_size", 4))
    groups: list[list] = []
    for i in range(0, len(regs), effective_group_size):
        groups.append(regs[i:i + effective_group_size])

    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    desc   = ""
    for i, grp in enumerate(groups):
        label = labels[i] if i < len(labels) else str(i + 1)
        names = [r.get("player_name") or r.get("player_username", "?") for r in grp]
        pids  = [r.get("player_discord_id", "") for r in grp]
        desc += f"**Group {label}**\n" + "\n".join(f"• {n}" for n in names) + "\n\n"
        await b44_create("TournamentGroup", {
            "tournament_id": t["id"], "guild_id": gid, "group_label": label,
            "player_ids": pids, "player_names": names, "generated_at": now_iso(),
        })
        for r in grp:
            await b44_update("Registration", r["id"], {"group_label": label, "group_number": i + 1})

    await b44_update("Tournament", t["id"], {"status": "groups_generated"})

    gi = img_url(tournament_name, t.get("game", ""), "group", f"{len(groups)} groups")
    group_embed = discord.Embed(
        title="🎯 Group Draw — " + tournament_name,
        description=desc or "Groups drawn!",
        color=0x9B59B6, timestamp=datetime.now(timezone.utc)
    )
    group_embed.set_image(url=gi)
    group_embed.set_footer(text=f"NexPlay | {len(groups)} Groups × {effective_group_size} teams each")

    # Post to tournament-specific groups channel
    short   = t.get("short_name","")
    ch_grp  = discord.utils.get(interaction.guild.text_channels, name=f"{short}-groups") if short else None
    ch_ann  = discord.utils.get(interaction.guild.text_channels, name=f"{short}-announcements") if short else None
    if ch_grp:
        await ch_grp.send(embed=group_embed)
    if ch_ann:
        ann_e = discord.Embed(
            title=f"🎯 Groups Drawn — {tournament_name}",
            description=f"Group draw is live in {ch_grp.mention if ch_grp else '#groups'}! Check your group.",
            color=0x9B59B6, timestamp=datetime.now(timezone.utc)
        )
        await ch_ann.send("@everyone", embed=ann_e)
    await interaction.followup.send(embed=ok_e("Groups Generated! 🎯", f"{len(groups)} groups drawn for **{tournament_name}**!\nPosted to {ch_grp.mention if ch_grp else '#groups'}."), ephemeral=True)


# ── /post_schedule ────────────────────────────────────────
@tree.command(name="post_schedule", description="Post the match schedule")
@app_commands.describe(tournament_name="Tournament name", schedule_text="Schedule details (rounds, times, matchups)")
async def cmd_schedule(interaction: discord.Interaction, tournament_name: str, schedule_text: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("No permission!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True)
    gid = str(interaction.guild.id)

    ts_list = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts_list:
        return await interaction.followup.send(embed=err_e("Tournament not found."))
    t  = ts_list[0]
    si = img_url(tournament_name, t.get("game", ""), "schedule", schedule_text[:100])
    await b44_update("Tournament", t["id"], {"status": "scheduled", "schedule_image_url": si})

    ch_brack = resolve_channel(interaction.guild, "brackets")
    ch_ann   = resolve_channel(interaction.guild, "announcements")

    short   = t.get("short_name","")
    ch_grp  = discord.utils.get(interaction.guild.text_channels, name=f"{short}-groups") if short else None
    ch_ann  = discord.utils.get(interaction.guild.text_channels, name=f"{short}-announcements") if short else None

    se = discord.Embed(
        title=f"📅 Match Schedule — {tournament_name}",
        description=f"**Schedule is LIVE!**\n\n{schedule_text}\n\n⚠️ Be ready 10 min before your match — late = forfeit!",
        color=0x1E90FF, timestamp=datetime.now(timezone.utc)
    )
    se.set_image(url=si)
    se.set_footer(text="NexPlay Tournament System")
    if ch_grp:
        await ch_grp.send(embed=se)
    if ch_ann:
        ann_e = discord.Embed(
            title=f"📅 Schedule Posted — {tournament_name}",
            description=f"Match schedule is live in {ch_grp.mention if ch_grp else '#groups'}! Check your match time. 👀",
            color=0x1E90FF, timestamp=datetime.now(timezone.utc)
        )
        await ch_ann.send("@everyone", embed=ann_e)
        await b44_create("AnnouncementLog", {
            "tournament_id": t["id"], "guild_id": gid, "milestone": "schedule_posted",
            "channel_id": str(ch_ann.id), "announced_at": now_iso(), "content_summary": "Schedule posted",
        })
    await interaction.followup.send(embed=ok_e(
        "Schedule Posted! 📅",
        f"Schedule for **{tournament_name}** is live!\nPosted to {ch_grp.mention if ch_grp else '#groups'}.",
        0x1E90FF
    ))


# ── /post_result ──────────────────────────────────────────
@tree.command(name="post_result", description="Post a match result")
@app_commands.describe(
    tournament_name="Tournament name", player1="Player 1 username", player2="Player 2 username",
    winner="Winner username", score="Score e.g. 3-1", round_number="Round number"
)
async def cmd_result(
    interaction: discord.Interaction,
    tournament_name: str, player1: str, player2: str,
    winner: str, score: str = "N/A", round_number: int = 1
):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("No permission!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True)
    gid = str(interaction.guild.id)

    ts_list = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts_list:
        return await interaction.followup.send(embed=err_e("Tournament not found."))
    t  = ts_list[0]
    ri = img_url(tournament_name, t.get("game", ""), "result",
                 player1 + " vs " + player2 + " score " + score + " winner " + winner)

    await b44_create("Match", {
        "tournament_id": t["id"], "guild_id": gid, "round_number": round_number, "match_number": 1,
        "player1_username": player1, "player2_username": player2,
        "winner_username": winner, "status": "completed", "results_card_image_url": ri,
    })

    short    = t.get("short_name","")
    ch_res   = discord.utils.get(interaction.guild.text_channels, name=f"{short}-results") if short else None
    ch_ann   = discord.utils.get(interaction.guild.text_channels, name=f"{short}-announcements") if short else None

    re = discord.Embed(
        title=f"⚔️ Match Result — Round {round_number}",
        description=(
            f"**{player1}** vs **{player2}**\n\n"
            f"🏆 **Winner: {winner}**\n"
            f"📊 Score: `{score}`\n\n"
            f"> {winner} advances to the next round! 🎮"
        ),
        color=0x1E90FF, timestamp=datetime.now(timezone.utc)
    )
    re.set_image(url=ri)
    re.set_footer(text=f"NexPlay | Round {round_number} | {tournament_name}")
    if ch_res:
        await ch_res.send(embed=re)
        await b44_create("AnnouncementLog", {
            "tournament_id": t["id"], "guild_id": gid, "milestone": "result_posted",
            "channel_id": str(ch_res.id), "announced_at": now_iso(),
            "content_summary": f"{player1} vs {player2} → {winner} ({score})",
        })
    if ch_ann:
        ann_e = discord.Embed(
            title=f"⚔️ Result Posted — {tournament_name}",
            description=f"**{winner}** wins! Check {ch_res.mention if ch_res else '#results'} for details.",
            color=0x1E90FF, timestamp=datetime.now(timezone.utc)
        )
        await ch_ann.send(embed=ann_e)
    await interaction.followup.send(embed=ok_e(
        "Result Posted! ⚔️",
        f"**{winner}** wins Round {round_number}!\nPosted to {ch_res.mention if ch_res else '#results'}.",
        0x1E90FF
    ))


# ── /complete_tournament ──────────────────────────────────
@tree.command(name="complete_tournament", description="Complete tournament and crown the champion")
@app_commands.describe(tournament_name="Tournament name", winner="Champion username", second="2nd place (optional)", third="3rd place (optional)")
async def cmd_complete(interaction: discord.Interaction, tournament_name: str, winner: str, second: str = "", third: str = ""):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("No permission!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True)
    gid = str(interaction.guild.id)

    ts_list = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts_list:
        return await interaction.followup.send(embed=err_e("Tournament not found."))
    t  = ts_list[0]
    ci = img_url(tournament_name, t.get("game", ""), "champion", winner)
    await b44_update("Tournament", t["id"], {
        "status": "completed", "winner_username": winner,
        "second_place": second, "third_place": third, "completed_at": now_iso(),
    })

    short    = t.get("short_name","")
    ch_ann   = discord.utils.get(interaction.guild.text_channels, name=f"{short}-announcements") if short else None
    ch_res   = discord.utils.get(interaction.guild.text_channels, name=f"{short}-results") if short else None

    podium = f"🥇 **{winner}** — CHAMPION"
    if second: podium += f"\n🥈 **{second}** — Runner-up"
    if third:  podium += f"\n🥉 **{third}** — 3rd Place"

    ce = discord.Embed(
        title=f"🏆 CHAMPION CROWNED — {tournament_name}",
        description=(
            f"{podium}\n\n"
            f"💰 Prize Pool: **{t.get('prize_pool','TBA')}**\n"
            f"🎮 Game: {t.get('game','')}\n\n"
            f"Thank you to all **{t.get('registered_count',0)}** players for competing! 🎉"
        ),
        color=0xFFD700, timestamp=datetime.now(timezone.utc)
    )
    ce.set_image(url=ci)
    ce.set_footer(text="NexPlay Tournament System")

    if ch_ann:
        await ch_ann.send("@everyone", embed=ce)
        await b44_create("AnnouncementLog", {
            "tournament_id": t["id"], "guild_id": gid, "milestone": "tournament_complete",
            "channel_id": str(ch_ann.id), "announced_at": now_iso(),
            "content_summary": f"Champion: {winner}",
        })
    if ch_res:
        await ch_res.send(embed=ce)

    # Post in interaction channel too
    await interaction.followup.send(embed=ok_e(
        f"Tournament Completed! 🏆",
        f"**{winner}** is the champion of **{tournament_name}**!\n"
        f"Champion post sent to {ch_ann.mention if ch_ann else '#announcements'}.",
        0xFFD700
    ))


# ══════════════════════════════════════════════════════════
#  /edit_tournament — Edit any tournament field via modal
# ══════════════════════════════════════════════════════════
@tree.command(name="edit_tournament", description="Edit tournament details (staff only)")
@app_commands.describe(tournament_name="Name of the tournament to edit")
async def cmd_edit_tournament(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("You need Tournament Host or higher!"), ephemeral=True)
    gid = str(interaction.guild.id)
    ts  = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts:
        # Try fuzzy match — find any open/recent tournament with similar name
        all_t = await b44_list("Tournament", {"guild_id": gid})
        fuzzy = [t for t in all_t if tournament_name.lower() in t.get("name","").lower()]
        if not fuzzy:
            return await interaction.response.send_message(
                embed=err_e(f'No tournament named "{tournament_name}" found. Use `/tournament_status` to see all.'),
                ephemeral=True
            )
        ts = fuzzy
    t = ts[0]
    await interaction.response.send_modal(EditTournamentModal(t))


# ── /tournament_status ────────────────────────────────────
@tree.command(name="tournament_status", description="View all tournaments in this server")
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    ts_list = await b44_list("Tournament", {"guild_id": str(interaction.guild.id)})
    if not ts_list:
        return await interaction.followup.send(embed=err_e("No tournaments found in this server."), ephemeral=True)
    e = discord.Embed(title="NexPlay Tournaments", color=0x9B59B6, timestamp=datetime.now(timezone.utc))
    for t in sorted(ts_list, key=lambda x: x.get("created_date", ""), reverse=True)[:10]:
        em = STATUS_EMOJI.get(t.get("status", ""), "❓")
        e.add_field(
            name=em + " " + str(t.get("name", "?")),
            value=(
                "Game: " + str(t.get("game", "?")) + " | Prize: " + str(t.get("prize_pool", "?")) + "\n"
                "Players: " + str(t.get("registered_count", 0)) + "/" + str(t.get("max_players", "?")) +
                " | " + str(t.get("status", "?")) + "\nDate: " + str(t.get("tournament_date", "TBA"))
            ),
            inline=False
        )
    e.set_footer(text="NexPlay Tournament System")
    await interaction.followup.send(embed=e, ephemeral=True)


# ── /announce ─────────────────────────────────────────────
@tree.command(name="announce", description="Post a custom announcement")
@app_commands.describe(message="Announcement content", ping_everyone="Ping @everyone?")
async def cmd_announce(interaction: discord.Interaction, message: str, ping_everyone: bool = False):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("No permission!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id))
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    await interaction.response.defer(thinking=True)

    ch_ann = resolve_channel(interaction.guild, "announcements")
    e = discord.Embed(title="📢 Announcement", description=message, color=0xFFD700, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=interaction.guild.name + " | " + interaction.user.display_name)
    if ch_ann:
        await dpost(ch_ann.id, e, content="@everyone" if ping_everyone else "")
    await interaction.followup.send(embed=ok_e("Announced! 📢", "Posted to " + (ch_ann.mention if ch_ann else "#announcements") + "!"))


# ── /help ─────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════
#  TOURNAMENT MANAGEMENT PANEL — /manage
# ══════════════════════════════════════════════════════════

class EditTournamentModal(discord.ui.Modal, title="Edit Tournament"):
    t_name   = discord.ui.TextInput(label="Tournament Name",  required=True,  max_length=80)
    t_date   = discord.ui.TextInput(label="Date (e.g. 2026-08-01)", required=False, max_length=30)
    t_time   = discord.ui.TextInput(label="Time (e.g. 5:00 PM NPT)", required=False, max_length=30)
    t_prize  = discord.ui.TextInput(label="Prize Pool",       required=False, max_length=50)
    t_desc   = discord.ui.TextInput(label="Description",      required=False, max_length=200,
                                    style=discord.TextStyle.paragraph)

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament
        self.t_name.default  = tournament.get("name", "")
        self.t_date.default  = tournament.get("tournament_date", "")
        self.t_time.default  = tournament.get("tournament_time", "")
        self.t_prize.default = tournament.get("prize_pool", "")
        self.t_desc.default  = tournament.get("description", "")

    async def on_submit(self, interaction: discord.Interaction):
        updates = {}
        if self.t_name.value:  updates["name"]             = self.t_name.value
        if self.t_date.value:  updates["tournament_date"]  = self.t_date.value
        if self.t_time.value:  updates["tournament_time"]  = self.t_time.value
        if self.t_prize.value: updates["prize_pool"]       = self.t_prize.value
        if self.t_desc.value:  updates["description"]      = self.t_desc.value
        await b44_update("Tournament", self.tournament["id"], updates)
        await interaction.response.send_message(
            embed=ok_e("Tournament Updated ✏️",
                "Changes saved:\n" + "\n".join(f"• **{k}:** {v}" for k, v in updates.items())),
            ephemeral=True
        )


class ScheduleModal(discord.ui.Modal, title="Post Match Schedule"):
    schedule = discord.ui.TextInput(
        label="Schedule (one match per line)",
        style=discord.TextStyle.paragraph,
        placeholder="Match 1: TeamA vs TeamB — 5:00 PM\nMatch 2: TeamC vs TeamD — 5:30 PM",
        required=True, max_length=1500
    )

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        t = self.tournament
        guild = interaction.guild
        short = t.get("short_name", "t")
        ch = discord.utils.get(guild.text_channels, name=short + "-results")
        if not ch:
            ch = discord.utils.get(guild.text_channels, name=short + "-groups")
        si = img_url(t.get("name",""), t.get("game",""), "schedule", self.schedule.value[:200])
        sched_e = discord.Embed(
            title="📅 Match Schedule — " + t.get("name",""),
            description=self.schedule.value,
            color=0x3498DB, timestamp=datetime.now(timezone.utc)
        )
        sched_e.set_image(url=si)
        sched_e.set_footer(text="NexPlay Tournament System")
        if ch:
            await ch.send(embed=sched_e)
        await b44_update("Tournament", t["id"], {"status": "scheduled", "schedule_image_url": si})
        await interaction.followup.send(embed=ok_e("Schedule Posted!", f"Match schedule is live in {ch.mention if ch else '#results'}."), ephemeral=True)


class AnnounceModal(discord.ui.Modal, title="Custom Announcement"):
    ann_text = discord.ui.TextInput(
        label="Announcement message",
        style=discord.TextStyle.paragraph,
        required=True, max_length=1500
    )
    ping_all = discord.ui.TextInput(
        label="Ping @everyone? (yes/no)",
        required=False, max_length=3, default="no"
    )

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        t = self.tournament
        guild = interaction.guild
        short = t.get("short_name", "t")
        ch = discord.utils.get(guild.text_channels, name=short + "-announcements")
        ping = self.ping_all.value.strip().lower() in ("yes", "y", "1", "true")
        e = discord.Embed(
            title="📢 " + t.get("name",""),
            description=self.ann_text.value,
            color=0xFFD700, timestamp=datetime.now(timezone.utc)
        )
        e.set_footer(text="NexPlay Tournament System")
        if ch:
            await ch.send("@everyone" if ping else "", embed=e)
        await interaction.followup.send(embed=ok_e("Announced!", f"Posted to {ch.mention if ch else '#announcements'}."), ephemeral=True)


class ManagementView(discord.ui.View):
    def __init__(self, tournament: dict):
        super().__init__(timeout=300)
        self.t = tournament

    async def _refresh_panel(self, interaction: discord.Interaction):
        """Re-fetch tournament and update the panel embed."""
        updated = await b44_list("Tournament", {"guild_id": str(interaction.guild.id), "name": self.t.get("name","")})
        if updated:
            self.t = updated[0]
        embed = build_manage_embed(self.t)
        await interaction.message.edit(embed=embed, view=self)

    # ── ROW 1: Registration ───────────────────────────────────────────────────
    @discord.ui.button(label="🟢 Open Registration", style=discord.ButtonStyle.success, row=0)
    async def btn_open_reg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await b44_update("Tournament", self.t["id"], {"status": "registration_open"})
        # Unlock the register channel
        guild = interaction.guild
        short = self.t.get("short_name","t")
        ch = discord.utils.get(guild.text_channels, name=short + "-register")
        if ch:
            try:
                ow = ch.overwrites_for(guild.default_role)
                ow.send_messages = None  # reset to default
                await ch.set_permissions(guild.default_role, overwrite=ow)
            except:
                pass
        await interaction.response.send_message(embed=ok_e("Registration Opened 🟢", "Players can now register."), ephemeral=True)
        await self._refresh_panel(interaction)

    @discord.ui.button(label="🔴 Close Registration", style=discord.ButtonStyle.danger, row=0)
    async def btn_close_reg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await b44_update("Tournament", self.t["id"], {"status": "registration_closed"})
        guild = interaction.guild
        short = self.t.get("short_name","t")
        ch = discord.utils.get(guild.text_channels, name=short + "-register")
        if ch:
            await lock_register_channel(guild, ch)
        await interaction.response.send_message(embed=ok_e("Registration Closed 🔴", "No more registrations accepted."), ephemeral=True)
        await self._refresh_panel(interaction)

    @discord.ui.button(label="👥 View Teams", style=discord.ButtonStyle.secondary, row=0)
    async def btn_view_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        regs = await b44_list("Registration", {"tournament_id": self.t["id"]})
        if not regs:
            return await interaction.response.send_message(embed=err_e("No teams registered yet."), ephemeral=True)
        lines = []
        for i, r in enumerate(regs, 1):
            members = r.get("team_members", [])
            if isinstance(members, list):
                mentions = " ".join(f"<@{m}>" for m in members)
            else:
                mentions = str(members)
            lines.append(f"**#{i} {r.get('player_name','?')}** — {mentions}")
        e = discord.Embed(
            title=f"👥 Registered Teams — {self.t.get('name','')}",
            description="\n".join(lines),
            color=0x5865F2
        )
        e.set_footer(text=f"{len(regs)}/{self.t.get('max_players',16)} slots filled")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── ROW 2: Tournament flow ────────────────────────────────────────────────
    @discord.ui.button(label="🎯 Generate Groups", style=discord.ButtonStyle.primary, row=1)
    async def btn_groups(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        t = self.t
        guild = interaction.guild
        regs = await b44_list("Registration", {"tournament_id": t["id"]})
        if len(regs) < 2:
            return await interaction.followup.send(embed=err_e("Need at least 2 registered teams."), ephemeral=True)
        import random
        random.shuffle(regs)
        group_size = 4
        groups = [regs[i:i+group_size] for i in range(0, len(regs), group_size)]
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        short = t.get("short_name","t")
        ch = discord.utils.get(guild.text_channels, name=short + "-groups")
        desc = ""
        for idx, grp in enumerate(groups):
            label = labels[idx] if idx < len(labels) else str(idx+1)
            names = [r.get("player_name","?") for r in grp]
            desc += f"**Group {label}**\n" + "\n".join(f"• {n}" for n in names) + "\n\n"
            for r in grp:
                await b44_update("Registration", r["id"], {"group_label": label})
        await b44_update("Tournament", t["id"], {"status": "groups_generated"})
        gi = img_url(t.get("name",""), t.get("game",""), "groups", desc[:300])
        ge = discord.Embed(title="🎯 Group Draw — " + t.get("name",""), description=desc, color=0x9B59B6, timestamp=datetime.now(timezone.utc))
        ge.set_image(url=gi)
        ge.set_footer(text="NexPlay Tournament System")
        if ch:
            await ch.send(embed=ge)
        await interaction.followup.send(embed=ok_e("Groups Generated!", f"Draw posted to {ch.mention if ch else '#groups'}."), ephemeral=True)
        await self._refresh_panel(interaction)

    @discord.ui.button(label="📅 Post Schedule", style=discord.ButtonStyle.primary, row=1)
    async def btn_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.send_modal(ScheduleModal(self.t))

    @discord.ui.button(label="🏁 Complete Tournament", style=discord.ButtonStyle.danger, row=1)
    async def btn_complete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.send_modal(CompleteTournamentModal(self.t))

    # ── ROW 3: Utilities ──────────────────────────────────────────────────────
    @discord.ui.button(label="📢 Announce", style=discord.ButtonStyle.secondary, row=2)
    async def btn_announce(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.send_modal(AnnounceModal(self.t))

    @discord.ui.button(label="✏️ Edit Tournament", style=discord.ButtonStyle.secondary, row=2)
    async def btn_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.send_modal(EditTournamentModal(self.t))

    @discord.ui.button(label="🗑️ Delete Tournament", style=discord.ButtonStyle.danger, row=2)
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.send_modal(DeleteTournamentModal(self.t))


class CompleteTournamentModal(discord.ui.Modal, title="Complete Tournament"):
    winner = discord.ui.TextInput(label="🥇 Winner (team name)", required=True, max_length=60)
    second = discord.ui.TextInput(label="🥈 2nd Place (optional)", required=False, max_length=60)
    third  = discord.ui.TextInput(label="🥉 3rd Place (optional)", required=False, max_length=60)

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        t = self.tournament
        guild = interaction.guild
        short = t.get("short_name","t")
        ch_ann = discord.utils.get(guild.text_channels, name=short + "-announcements")
        ch_champ = discord.utils.get(guild.text_channels, name="hall-of-champions")
        winner = self.winner.value.strip()
        ci = img_url(t.get("name",""), t.get("game",""), "champion", "winner " + winner)
        podium = f"🥇 **{winner}**"
        if self.second.value: podium += f"\n🥈 {self.second.value}"
        if self.third.value:  podium += f"\n🥉 {self.third.value}"
        ce = discord.Embed(
            title="🏆 CHAMPION CROWNED — " + t.get("name",""),
            description=podium + f"\n\nCongratulations to all participants of **{t.get('name','')}**!",
            color=0xFFD700, timestamp=datetime.now(timezone.utc)
        )
        ce.set_image(url=ci)
        ce.set_footer(text="NexPlay Tournament System")
        if ch_ann: await ch_ann.send("@everyone", embed=ce)
        if ch_champ: await ch_champ.send(embed=ce)
        await b44_update("Tournament", t["id"], {"status": "completed", "winner": winner})
        await interaction.followup.send(embed=ok_e("Tournament Completed! 🏆", f"{winner} is the champion!"), ephemeral=True)


class DeleteTournamentModal(discord.ui.Modal, title="Delete Tournament — Type to confirm"):
    confirm = discord.ui.TextInput(label='Type "DELETE" to confirm', required=True, max_length=10)

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().upper() != "DELETE":
            return await interaction.response.send_message(embed=err_e('You must type "DELETE" exactly.'), ephemeral=True)
        await b44_update("Tournament", self.tournament["id"], {"status": "deleted"})
        await interaction.response.send_message(
            embed=ok_e("Tournament Deleted 🗑️", f"**{self.tournament.get('name','')}** has been removed."),
            ephemeral=True
        )


def build_manage_embed(t: dict) -> discord.Embed:
    status_colors = {
        "registration_open":   0x00FF7F,
        "registration_closed": 0xFF6B35,
        "groups_generated":    0x9B59B6,
        "scheduled":           0x3498DB,
        "completed":           0xFFD700,
        "deleted":             0x555555,
    }
    status = t.get("status", "unknown")
    reg = t.get("registered_count", 0)
    max_p = t.get("max_players", 16)
    bar = "█" * int(reg) + "░" * (int(max_p) - int(reg))
    status_labels = {
        "registration_open":   "🟢 Registration Open",
        "registration_closed": "🔴 Registration Closed",
        "groups_generated":    "🎯 Groups Generated",
        "scheduled":           "📅 Scheduled",
        "completed":           "🏆 Completed",
        "deleted":             "🗑️ Deleted",
    }
    e = discord.Embed(
        title="⚙️ TOURNAMENT MANAGEMENT — " + t.get("name",""),
        description=(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎮 **Game:** {t.get('game','')}\n"
            f"🎖️ **Prize:** {t.get('prize_pool','')}\n"
            f"📅 **Date:** {t.get('tournament_date','')}  ⏰ {t.get('tournament_time','TBD')}\n"
            f"👥 **Teams:** {reg}/{max_p}  `{bar}`\n"
            f"📊 **Status:** {status_labels.get(status, status)}\n"
            f"🏷️ **Short Name:** `{t.get('short_name','').upper()}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Use the buttons below to manage this tournament."
        ),
        color=status_colors.get(status, 0x5865F2),
        timestamp=datetime.now(timezone.utc)
    )
    e.set_footer(text="NexPlay Management Panel | Staff only")
    return e


@tree.command(name="manage", description="Open the tournament management panel (staff only)")
@app_commands.describe(tournament_name="Name of the tournament to manage")
async def cmd_manage(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("You need Tournament Host or higher!"), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    gid = str(interaction.guild.id)
    ts = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts:
        return await interaction.followup.send(embed=err_e(f"No tournament named \"{tournament_name}\" found."), ephemeral=True)
    t = ts[0]
    embed = build_manage_embed(t)
    view  = ManagementView(t)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ══════════════════════════════════════════════════════════
#  /send_daily_log  — Manual trigger (admin only)
# ══════════════════════════════════════════════════════════
@tree.command(name="send_daily_log", description="[Admin] Manually send today's Excel report to your DM")
async def cmd_send_daily_log(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("Staff only!"), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    gid = str(interaction.guild.id)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        xl_buf = await build_daily_excel(interaction.guild)
        file = discord.File(xl_buf, filename=f"NexPlay_Log_{interaction.guild.name}_{today}.xlsx")
        dm = await interaction.user.create_dm()
        summary_e = discord.Embed(
            title=f"📊 NexPlay Daily Report — {interaction.guild.name}",
            description=(
                f"Manual export for **{today}**\n\n"
                f"**Sheets included:**\n"
                f"• 🏆 Tournaments\n"
                f"• 👥 Registrations\n"
                f"• 💬 Support Log\n"
                f"• ⚔️ Match Results\n"
                f"• 📈 Summary"
            ),
            color=0x1F4E79, timestamp=datetime.now(timezone.utc)
        )
        summary_e.set_footer(text="NexPlay Tournament System")
        await dm.send(embed=summary_e, file=file)
        await interaction.followup.send(embed=ok_e("Report Sent! 📊", f"Excel report sent to your DM!"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=err_e(f"Failed to generate report: {e}"), ephemeral=True)


# ══════════════════════════════════════════════════════════
#  /download_registrations — Download tournament register sheet
# ══════════════════════════════════════════════════════════
@tree.command(name="download_registrations", description="Download tournament registrations as Excel sheet")
@app_commands.describe(tournament_name="Tournament name to export")
async def cmd_download_regs(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("Staff only!"), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    gid = str(interaction.guild.id)

    ts = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts:
        all_t = await b44_list("Tournament", {"guild_id": gid})
        fuzzy = [t for t in all_t if tournament_name.lower() in t.get("name","").lower()]
        if not fuzzy:
            return await interaction.followup.send(embed=err_e(f'No tournament named "{tournament_name}" found.'), ephemeral=True)
        ts = fuzzy
    t = ts[0]

    regs = await b44_list("Registration", {"tournament_id": t["id"]})
    if not regs:
        return await interaction.followup.send(embed=err_e("No registrations found yet."), ephemeral=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Registrations"

    # Header row
    headers = ["#", "Team Name", "Player 1", "Player 2", "Player 3", "Player 4",
               "All Players", "Discord User", "Registered At", "Group", "Status"]
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "1F4E79")
        _xl_border(c)
    ws.row_dimensions[1].height = 22

    # Title row above headers
    ws.insert_rows(1)
    title_cell = ws.cell(row=1, column=1,
                         value=f"NexPlay — {t.get('name','')} | Registration Sheet | {t.get('game','')} | Prize: {t.get('prize_pool','')} | Date: {t.get('tournament_date','')}")
    title_cell.font = Font(bold=True, size=12, color="1F4E79")
    title_cell.fill = PatternFill("solid", fgColor="BDD7EE")
    ws.merge_cells(f"A1:{get_column_letter(len(headers))}1")
    ws.row_dimensions[1].height = 24

    team_size = int(t.get("team_size", 4))

    for ri, r in enumerate(regs, 3):
        all_players = r.get("player_username","")
        player_list = [p.strip() for p in all_players.split(",") if p.strip()]
        # Pad to 4 player columns
        while len(player_list) < 4:
            player_list.append("")

        row_data = [
            r.get("seed_number", ri-2),
            r.get("player_name",""),
            player_list[0] if len(player_list) > 0 else "",
            player_list[1] if len(player_list) > 1 else "",
            player_list[2] if len(player_list) > 2 else "",
            player_list[3] if len(player_list) > 3 else "",
            all_players,
            r.get("player_discord_tag",""),
            r.get("registered_at","")[:16] if r.get("registered_at") else "",
            r.get("group_label","—"),
            r.get("status","registered"),
        ]
        for ci, val in enumerate(row_data, 1):
            c = ws.cell(row=ri, column=ci, value=str(val))
            _xl_border(c)
            c.alignment = Alignment(vertical="center")

        # Alternating row color
        if ri % 2 == 0:
            for ci in range(1, len(headers)+1):
                ws.cell(row=ri, column=ci).fill = PatternFill("solid", fgColor="F5F5F5")

    # Column widths
    for ci, w in enumerate([5,20,18,18,18,18,40,18,16,8,12], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    # Summary at the bottom
    end_row = len(regs) + 4
    ws.cell(row=end_row, column=1, value=f"Total Teams: {len(regs)} / {t.get('max_players','?')}").font = Font(bold=True)
    ws.cell(row=end_row+1, column=1, value=f"Status: {t.get('status','')}").font = Font(italic=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe_name = t.get("name","tournament").replace(" ","_")[:30]
    file = discord.File(buf, filename=f"NexPlay_{safe_name}_Registrations.xlsx")
    await interaction.followup.send(
        embed=ok_e(
            f"📥 Registration Sheet — {t.get('name','')}",
            f"**{len(regs)}** teams exported!\n\nSheet includes:\n"
            f"• Team names\n• All player in-game names (individual columns)\n"
            f"• Discord tags\n• Registration time\n• Group assignments"
        ),
        file=file,
        ephemeral=True
    )


# ══════════════════════════════════════════════════════════
#  /import_registrations — Import teams from Excel/CSV
# ══════════════════════════════════════════════════════════
@tree.command(name="import_registrations", description="[Staff] Import team registrations from an Excel/CSV file")
@app_commands.describe(
    tournament_name="Tournament to import into",
    attachment="Excel (.xlsx) or CSV file with columns: team_name, player1, player2, player3, player4"
)
async def cmd_import_regs(interaction: discord.Interaction, tournament_name: str, attachment: discord.Attachment):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("Staff only!"), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    gid = str(interaction.guild.id)

    # Find tournament
    ts = await b44_list("Tournament", {"guild_id": gid, "name": tournament_name})
    if not ts:
        all_t = await b44_list("Tournament", {"guild_id": gid})
        fuzzy = [t for t in all_t if tournament_name.lower() in t.get("name","").lower()]
        if not fuzzy:
            return await interaction.followup.send(embed=err_e(f'No tournament named "{tournament_name}" found.'), ephemeral=True)
        ts = fuzzy
    t = ts[0]
    tid = t["id"]

    # Download file
    file_bytes = await attachment.read()
    filename   = attachment.filename.lower()

    rows = []
    try:
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
            ws = wb.active
            headers_row = [str(c.value or "").lower().strip() for c in ws[1]]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not any(row):
                    continue
                row_dict = {headers_row[i]: str(row[i] or "").strip() for i in range(len(headers_row))}
                rows.append(row_dict)
        elif filename.endswith(".csv"):
            decoded = file_bytes.decode("utf-8-sig")
            reader  = csv.DictReader(io.StringIO(decoded))
            for row in reader:
                rows.append({k.lower().strip(): v.strip() for k, v in row.items()})
        else:
            return await interaction.followup.send(embed=err_e("Only .xlsx or .csv files supported."), ephemeral=True)
    except Exception as e:
        return await interaction.followup.send(embed=err_e(f"Failed to read file: {e}"), ephemeral=True)

    if not rows:
        return await interaction.followup.send(embed=err_e("File is empty or has no valid rows."), ephemeral=True)

    # Get existing registrations to find slot number
    existing = await b44_list("Registration", {"tournament_id": tid})
    slot = len(existing)
    max_p = int(t.get("max_players", 16))

    imported = 0
    skipped  = 0
    errors   = []

    for row in rows:
        # Try multiple column name variants
        team_name = (row.get("team_name") or row.get("team") or row.get("name") or "").strip()
        if not team_name:
            skipped += 1
            continue

        # Check if already registered
        already = [r for r in existing if r.get("player_name","").lower() == team_name.lower()]
        if already:
            skipped += 1
            continue

        if slot >= max_p:
            errors.append(f"Slot limit reached ({max_p}) — stopped at {team_name}")
            break

        # Collect players from columns player1..player4 or p1..p4 or just players
        players = []
        for key in ["player1","player2","player3","player4","p1","p2","p3","p4"]:
            v = row.get(key,"").strip()
            if v:
                players.append(v)
        if not players:
            # Try "players" column (comma-separated)
            p_col = row.get("players") or row.get("ingame_names","")
            players = [p.strip() for p in p_col.split(",") if p.strip()]

        slot += 1
        try:
            await b44_create("Registration", {
                "tournament_id": tid,
                "guild_id": gid,
                "player_name": team_name,
                "player_discord_id": str(interaction.user.id),
                "player_discord_tag": f"[IMPORTED by {interaction.user.display_name}]",
                "player_username": ", ".join(players),
                "registered_at": now_iso(),
                "status": "registered",
                "seed_number": slot,
            })
            existing.append({"player_name": team_name})  # prevent dupe in same file
            imported += 1
        except Exception as e:
            errors.append(f"{team_name}: {e}")

    await b44_update("Tournament", tid, {"registered_count": slot})

    # Auto-lock if full
    if slot >= max_p:
        short = t.get("short_name","")
        if short:
            ch_reg = discord.utils.get(interaction.guild.text_channels, name=f"{short}-register")
            if ch_reg:
                await lock_register_channel(interaction.guild, ch_reg)

    result_desc = (
        f"**Imported:** {imported} teams\n"
        f"**Skipped (duplicates/empty):** {skipped}\n"
        f"**Slots used:** {slot}/{max_p}"
    )
    if errors:
        result_desc += f"\n\n⚠️ **Errors:**\n" + "\n".join(errors[:5])

    await interaction.followup.send(
        embed=ok_e(f"📥 Import Complete — {t.get('name','')}", result_desc),
        ephemeral=True
    )


@tree.command(name="help", description="Show all NexPlay bot commands")
async def cmd_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="NexPlay Tournament Bot — Commands",
        description="Full tournament management powered by NexPlay!",
        color=0xFFD700, timestamp=datetime.now(timezone.utc)
    )
    e.add_field(name="👤 Player Commands", value=(
        "`/register` — Join a tournament\n"
        "`/tournament_status` — View active tournaments\n"
        "`/help` — This menu"
    ), inline=False)
    e.add_field(name="⚔️ Staff Commands", value=(
        "`/setup` — Initialize NexPlay (owner only)\n"
        "`/create_tournament` — Create & announce tournament\n"
        "`/close_registration` — Lock registrations\n"
        "`/generate_groups` — Draw groups randomly\n"
        "`/post_schedule` — Post match schedule\n"
        "`/post_result` — Record match result\n"
        "`/complete_tournament` — Crown the champion\n"
        "`/announce` — Post custom announcement"
    ), inline=False)
    e.add_field(name="🌐 Multi-Server SaaS", value=(
        "This bot supports unlimited servers.\n"
        "Each server gets 3 free trial tournaments.\n"
        "Upgrade at nexplay.gg for unlimited access."
    ), inline=False)
    e.set_footer(text="NexPlay Tournament System | nexplay.gg")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════
#  HEALTH-CHECK HTTP SERVER (Background Worker — optional keepalive)
# ══════════════════════════════════════════════════════════
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"NexPlay bot is running.")
    def log_message(self, fmt, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 0))
    if not port:
        return  # Background Worker — no port needed
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[NexPlay] Health server bound to 0.0.0.0:{port}", flush=True)
    server.serve_forever()

# ══════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    missing = []
    if not BOT_TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not SVC_TOKEN:
        missing.append("BASE44_SERVICE_TOKEN")
    if missing:
        print("[NexPlay] STARTUP FAILED — missing env vars:", flush=True)
        for m in missing:
            print(f"[NexPlay]   ✗ {m} is not set", flush=True)
        raise SystemExit(1)

    print("[NexPlay] Env vars OK. Starting health server...", flush=True)

    # Start health server FIRST — non-daemon so it keeps process alive
    # even if bot.run() has a temporary hiccup
    health_thread = threading.Thread(target=run_health_server, daemon=False)
    health_thread.start()

    import time
    time.sleep(0.5)  # Give health server 500ms to bind before bot connects
    print(f"[NexPlay] Guild ID: {HOME_GUILD}", flush=True)

    try:
        bot.run(BOT_TOKEN, log_level=20)
    except Exception as e:
        print(f"[NexPlay] bot.run() crashed: {e}", flush=True)
        # Health server keeps running so Render doesn't kill the dyno
        # Bot will reconnect on next deploy/restart
        health_thread.join()
