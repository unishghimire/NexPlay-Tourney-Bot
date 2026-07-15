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

BASE44_API  = "https://api.base44.com/api/apps/" + APP_ID + "/entities"
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


# ══════════════════════════════════════════════════════════════════════════════
#  PLAN FEATURE GATES
#  Keys match features checked throughout the bot.
#  Plans: trial / starter / pro / elite
# ══════════════════════════════════════════════════════════════════════════════
PLAN_FEATURES = {
    # (plan_name_lower) → set of unlocked feature keys
    "trial":   {"create_tournament", "register", "groups", "results", "export_csv"},
    "starter": {"create_tournament", "register", "groups", "results", "export_csv",
                "schedule_post", "edit_tournament", "roadmap_post", "standings_post"},
    "pro":     {"create_tournament", "register", "groups", "results", "export_csv",
                "schedule_post", "edit_tournament", "roadmap_post", "standings_post",
                "ai_gfx_poster", "ai_gfx_groups", "ai_gfx_results", "ai_gfx_schedule",
                "auto_announce", "daily_report"},
    "elite":   {"create_tournament", "register", "groups", "results", "export_csv",
                "schedule_post", "edit_tournament", "roadmap_post", "standings_post",
                "ai_gfx_poster", "ai_gfx_groups", "ai_gfx_results", "ai_gfx_schedule",
                "auto_announce", "daily_report",
                "ai_support", "host_game", "suggest_improvement", "meme_post",
                "welcome_message", "advanced_analytics"},
}

PLAN_UPGRADE_MSG = {
    "ai_gfx_poster":     "🎨 AI GFX posters require **Pro** or **Elite** plan.",
    "ai_gfx_groups":     "🎨 AI GFX group draw graphics require **Pro** or **Elite** plan.",
    "ai_gfx_results":    "🎨 AI GFX result cards require **Pro** or **Elite** plan.",
    "ai_gfx_schedule":   "🎨 AI GFX schedule cards require **Pro** or **Elite** plan.",
    "auto_announce":     "📢 Auto-announce features require **Pro** or **Elite** plan.",
    "daily_report":      "📊 Daily Excel reports require **Pro** or **Elite** plan.",
    "ai_support":        "🤖 AI Support agent requires **Elite** plan.",
    "host_game":         "🎮 Mini-game hosting requires **Elite** plan.",
    "suggest_improvement":"💡 AI growth advisor requires **Elite** plan.",
    "meme_post":         "😂 Meme/clip sharing requires **Elite** plan.",
    "welcome_message":   "👋 Automated welcome messages require **Elite** plan.",
    "edit_tournament":   "✏️ Tournament editing requires **Starter** or higher plan.",
    "schedule_post":     "📅 Schedule posting requires **Starter** or higher plan.",
    "roadmap_post":      "🗺️ Roadmap posting requires **Starter** or higher plan.",
    "standings_post":    "🏅 Standings posting requires **Starter** or higher plan.",
}

async def check_feature(guild_id: str, feature: str, interaction: discord.Interaction = None) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    If interaction is provided, automatically replies with an ephemeral error on deny.
    """
    rec = await get_server_record(guild_id)
    if not rec:
        msg = "❌ Server not registered. Run `/setup` first."
        if interaction:
            try:
                await interaction.followup.send(embed=err_e(msg), ephemeral=True)
            except:
                pass
        return False, msg

    plan    = (rec.get("plan_name") or rec.get("subscription_status") or "trial").lower()
    status  = rec.get("subscription_status", "trial").lower()

    # Normalize plan names
    if plan in ("free trial", "free_trial"): plan = "trial"
    if status in ("expired", "cancelled"):   plan = "trial"

    allowed_features = PLAN_FEATURES.get(plan, PLAN_FEATURES["trial"])
    if feature in allowed_features:
        return True, ""

    upgrade_msg = PLAN_UPGRADE_MSG.get(feature, f"❌ Feature `{feature}` not available on **{plan.title()}** plan.")
    full_msg = (
        upgrade_msg + "\n\n"
        "**Upgrade at:** https://nexplay-server-portal.vercel.app/subscription\n"
        f"Current plan: **{plan.title()}**"
    )
    if interaction:
        try:
            await interaction.followup.send(embed=err_e(full_msg), ephemeral=True)
        except:
            pass
    return False, full_msg

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
        intents.presences = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
        # GLOBAL sync — commands appear in every server
        await self.tree.sync()
        print("[NexPlay] Global slash commands synced.")
        # Start daily log scheduler
        asyncio.create_task(self._daily_log_scheduler())
        asyncio.create_task(auto_meme_loop())

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
#  MEME / FUNNY VIDEO AUTO-POSTER  (Elite plan only)
# ══════════════════════════════════════════════════════════

MEME_SUBREDDITS = ["dankmemes", "gaming", "freefire", "PUBGMobile", "mildlyinfuriating", "FreeFireBattlegrounds"]
MEME_INTERVAL   = 30 * 60   # post every 30 minutes

# Track last posted URL per guild so we never repeat the same meme
_last_meme_url: dict[int, str] = {}

async def fetch_reddit_meme(subreddit: str = "dankmemes", exclude_url: str = "") -> dict | None:
    """Fetch a fresh trending meme from Reddit — skips NSFW, videos, and the last posted URL."""
    import random
    # Use a random sort (new/hot/top) to get variety each call
    sort = random.choice(["hot", "new", "top"])
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit=50&t=day"
    try:
        async with bot.http_session.get(
            url,
            headers={"User-Agent": "NexPlayBot/4.0 (nexplay-tourney-bot)"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status != 200:
                print(f"[NexPlay] Reddit {r.status} for r/{subreddit}", flush=True)
                return None
            data = await r.json(content_type=None)
            posts = data.get("data", {}).get("children", [])
            random.shuffle(posts)
            for post in posts:
                p = post.get("data", {})
                if p.get("is_video") or p.get("over_18") or p.get("stickied"):
                    continue
                img_url = p.get("url", "")
                # Skip if same as last posted
                if img_url == exclude_url:
                    continue
                if any(img_url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".gifv")):
                    return {
                        "title":     p.get("title", "")[:256],
                        "url":       img_url,
                        "permalink": "https://reddit.com" + p.get("permalink", ""),
                        "score":     p.get("score", 0),
                    }
    except Exception as e:
        print(f"[NexPlay] meme fetch error r/{subreddit}: {e}", flush=True)
    return None

async def auto_meme_loop():
    """Every MEME_INTERVAL seconds, post a DIFFERENT trending meme to each Elite-plan server."""
    await bot.wait_until_ready()
    import random
    print("[NexPlay] auto_meme_loop started.", flush=True)
    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                try:
                    # Gate: Elite plan only
                    ok, _ = await check_feature(str(guild.id), "meme_post")
                    if not ok:
                        continue

                    # Find target channel: memes > funny > media > general
                    target = None
                    for name_hint in ("meme", "funny", "media", "general", "chat"):
                        target = discord.utils.find(
                            lambda c, h=name_hint: (
                                isinstance(c, discord.TextChannel)
                                and h in c.name.lower()
                                and c.permissions_for(guild.me).send_messages
                            ),
                            guild.text_channels
                        )
                        if target:
                            break

                    if not target:
                        print(f"[NexPlay] No postable channel found in {guild.name}", flush=True)
                        continue

                    # Pick a different subreddit per guild using guild ID as seed offset
                    sub = MEME_SUBREDDITS[(guild.id // 1000) % len(MEME_SUBREDDITS)]

                    # Exclude last posted URL for this guild
                    last_url = _last_meme_url.get(guild.id, "")
                    meme = await fetch_reddit_meme(sub, exclude_url=last_url)

                    # Fallback: try a different subreddit if first returned nothing
                    if not meme:
                        alt_sub = random.choice([s for s in MEME_SUBREDDITS if s != sub])
                        meme = await fetch_reddit_meme(alt_sub, exclude_url=last_url)

                    if not meme:
                        print(f"[NexPlay] No meme found for {guild.name}", flush=True)
                        continue

                    # Save so we don't repeat next cycle
                    _last_meme_url[guild.id] = meme["url"]

                    embed = discord.Embed(
                        title=meme["title"],
                        url=meme["permalink"],
                        color=0xFF6B35
                    )
                    embed.set_image(url=meme["url"])
                    embed.set_footer(text=f"r/{sub} • 🔥 Trending on Reddit | NexPlay")
                    await target.send(embed=embed)
                    print(f"[NexPlay] Meme posted to #{target.name} in {guild.name} (score: {meme['score']})", flush=True)

                except Exception as guild_err:
                    print(f"[NexPlay] meme loop guild error ({guild.name}): {guild_err}", flush=True)

        except Exception as e:
            print(f"[NexPlay] auto_meme_loop outer error: {e}", flush=True)

        await asyncio.sleep(MEME_INTERVAL)


# ══════════════════════════════════════════════════════════
#  WELCOME MESSAGE  (Elite plan only)
# ══════════════════════════════════════════════════════════

@bot.event
async def on_member_join(member: discord.Member):
    """Send a warm welcome to new members in #welcome channel (Elite plan)."""
    guild = member.guild
    ok, _ = await check_feature(str(guild.id), "welcome_message")
    if not ok:
        return
    # Find welcome channel
    welcome_ch = discord.utils.find(
        lambda c: isinstance(c, discord.TextChannel) and "welcome" in c.name.lower(),
        guild.text_channels
    )
    if not welcome_ch:
        return
    embed = discord.Embed(
        title=f"👋 Welcome to {guild.name}, {member.display_name}!",
        description=(
            f"Hey {member.mention}, we're glad you're here! 🎮\n\n"
            f"**{guild.name}** is a competitive esports community hosting Free Fire & Battle Royale tournaments.\n\n"
            f"📋 Check out our channels to get started\n"
            f"🏆 Use **/register** in a tournament channel to join a competition\n"
            f"❓ Use **/help** if you need assistance\n\n"
            f"Good luck and have fun! 🔥"
        ),
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Member #{guild.member_count} • NexPlay")
    await welcome_ch.send(embed=embed)


# ══════════════════════════════════════════════════════════
#  ANNOUNCEMENT COMMAND  (all plans)
# ══════════════════════════════════════════════════════════

@tree.command(name="announce", description="[Host] Post a tournament announcement embed")
@app_commands.describe(
    title="Announcement title",
    message="Announcement message body",
    channel="Channel to post in (optional, defaults to current)"
)
async def cmd_announce(
    interaction: discord.Interaction,
    title: str,
    message: str,
    channel: discord.TextChannel = None
):
    if not is_staff(interaction):
        return await interaction.response.send_message(embed=err_e("Staff only."), ephemeral=True)
    await interaction.response.defer(thinking=True, ephemeral=True)
    target = channel or interaction.channel
    embed = discord.Embed(
        title=f"📢 {title}",
        description=message,
        color=0xE74C3C,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"NexPlay Announcement • {interaction.guild.name}")
    await target.send(embed=embed)
    await interaction.followup.send(embed=ok_e("Announced!", f"Posted to {target.mention}"), ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  MULTI-STEP TOURNAMENT CREATION MODALS
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
#  TOURNAMENT CREATION — MODAL-THEN-BUTTON FLOW
#  Discord limitation: cannot open modal FROM a modal submit.
#  Pattern: Modal1 → ephemeral View → Button opens Modal2 → etc.
#  All step data is carried in the View instance.
# ══════════════════════════════════════════════════════════

# ── Shared in-memory session store (keyed by user_id) ────────
_tourney_sessions: dict[int, dict] = {}

# ── Step 1 Modal (5 fields: basics) ─────────────────────────
class TournamentStep1Modal(discord.ui.Modal, title="🏆 Create Tournament (1/3) — Basics"):
    t_name  = discord.ui.TextInput(label="Tournament Name",  placeholder="NexPlay Championship S1", max_length=80)
    t_game  = discord.ui.TextInput(label="Game",             placeholder="Free Fire / PUBG / Minecraft / Valorant", max_length=40)
    t_prize = discord.ui.TextInput(label="Prize Pool",       placeholder="NPR 10,000", max_length=60)
    t_date  = discord.ui.TextInput(label="Tournament Date",  placeholder="2026-08-01", max_length=30)
    t_desc  = discord.ui.TextInput(label="Description (optional)", placeholder="Open for all — register now!", required=False, max_length=200, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        _tourney_sessions[uid] = {
            "name":        self.t_name.value.strip(),
            "game":        self.t_game.value.strip(),
            "prize_pool":  self.t_prize.value.strip(),
            "date":        self.t_date.value.strip(),
            "description": self.t_desc.value.strip(),
            "guild_id":    interaction.guild.id,
        }
        embed = discord.Embed(
            title="✅ Step 1 saved!",
            description=(
                f"**Name:** {self.t_name.value.strip()}\n"
                f"**Game:** {self.t_game.value.strip()}\n"
                f"**Prize:** {self.t_prize.value.strip()}\n"
                f"**Date:** {self.t_date.value.strip()}\n\n"
                "Click **Next →** to fill Format & Slots."
            ),
            color=0x5865F2
        )
        await interaction.response.send_message(embed=embed, view=Step2ButtonView(), ephemeral=True)


class Step2ButtonView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)

    @discord.ui.button(label="Next → Format & Slots", style=discord.ButtonStyle.primary, emoji="➡️")
    async def go_step2(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in _tourney_sessions:
            return await interaction.response.send_message("❌ Session expired. Run /create_tournament again.", ephemeral=True)
        await interaction.response.send_modal(TournamentStep2Modal())

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        _tourney_sessions.pop(interaction.user.id, None)
        await interaction.response.edit_message(content="❌ Tournament creation cancelled.", embed=None, view=None)


# ── Step 2 Modal (5 fields: format) ─────────────────────────
class TournamentStep2Modal(discord.ui.Modal, title="🏆 Create Tournament (2/3) — Format & Slots"):
    t_max    = discord.ui.TextInput(label="Total Teams / Max Slots", placeholder="16", max_length=4)
    t_tsize  = discord.ui.TextInput(label="Players Per Team",        placeholder="4  (squads) or 1 (solo)", max_length=3)
    t_gsize  = discord.ui.TextInput(label="Teams Per Group",         placeholder="4", max_length=3)
    t_rounds = discord.ui.TextInput(label="Number of Rounds",        placeholder="3  (Group → Semi → Final)", max_length=3)
    t_format = discord.ui.TextInput(label="Match Format",            placeholder="Battle Royale / Single Elim / Round Robin", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid not in _tourney_sessions:
            return await interaction.response.send_message("❌ Session expired. Run /create_tournament again.", ephemeral=True)
        _tourney_sessions[uid].update({
            "max_players": self.t_max.value.strip(),
            "team_size":   self.t_tsize.value.strip(),
            "group_size":  self.t_gsize.value.strip(),
            "rounds":      self.t_rounds.value.strip(),
            "format":      self.t_format.value.strip(),
        })
        s = _tourney_sessions[uid]
        embed = discord.Embed(
            title="✅ Step 2 saved!",
            description=(
                f"**Teams:** {self.t_max.value.strip()}  |  **Size:** {self.t_tsize.value.strip()}v{self.t_tsize.value.strip()}\n"
                f"**Groups:** {self.t_gsize.value.strip()} teams/group  |  **Rounds:** {self.t_rounds.value.strip()}\n"
                f"**Format:** {self.t_format.value.strip()}\n\n"
                "Click **Next →** to fill Schedule & Rules (final step)."
            ),
            color=0x5865F2
        )
        await interaction.response.send_message(embed=embed, view=Step3ButtonView(), ephemeral=True)


class Step3ButtonView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)

    @discord.ui.button(label="Next → Schedule & Rules", style=discord.ButtonStyle.primary, emoji="➡️")
    async def go_step3(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in _tourney_sessions:
            return await interaction.response.send_message("❌ Session expired. Run /create_tournament again.", ephemeral=True)
        await interaction.response.send_modal(TournamentStep3Modal())

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        _tourney_sessions.pop(interaction.user.id, None)
        await interaction.response.edit_message(content="❌ Tournament creation cancelled.", embed=None, view=None)


# ── Step 3 Modal (5 fields: schedule + rules) ───────────────
class TournamentStep3Modal(discord.ui.Modal, title="🏆 Create Tournament (3/3) — Schedule & Rules"):
    t_time    = discord.ui.TextInput(label="Match Time",            placeholder="5:00 PM NPT", max_length=30)
    t_regend  = discord.ui.TextInput(label="Registration Deadline", placeholder="2026-07-30 or TBD", required=False, max_length=30)
    t_rules   = discord.ui.TextInput(label="Rules / Special Notes", placeholder="No hacks. Top 2 per group advance.", required=False, max_length=400, style=discord.TextStyle.paragraph)
    t_stream  = discord.ui.TextInput(label="Stream / YouTube",      placeholder="@nexplaygg or https://youtube.com/...", required=False, max_length=100)
    t_nations = discord.ui.TextInput(label="Eligible Nations",      placeholder="🇳🇵 🇮🇳 🇧🇩 🇵🇰  (blank = open)", required=False, max_length=60)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        uid = interaction.user.id
        if uid not in _tourney_sessions:
            return await interaction.followup.send("❌ Session expired. Run /create_tournament again.", ephemeral=True)

        s = _tourney_sessions.pop(uid)   # consume session
        gid = str(interaction.guild.id)

        def safe_int(v, d=16):
            try: return int(v)
            except: return d

        name        = s["name"]
        game        = s["game"]
        prize_pool  = s["prize_pool"]
        date        = s["date"]
        description = s.get("description", "")
        max_players = safe_int(s["max_players"], 16)
        team_size   = safe_int(s["team_size"],   4)
        group_size  = safe_int(s["group_size"],  4)
        rounds      = safe_int(s["rounds"],      3)
        fmt         = s["format"]
        time_str    = self.t_time.value.strip()   or "TBD"
        reg_end     = self.t_regend.value.strip() or "TBD"
        rules       = self.t_rules.value.strip()
        stream      = self.t_stream.value.strip()
        nations     = self.t_nations.value.strip() or "🇳🇵"

        # ── Create channels ────────────────────────────────────────────
        t_channels = await make_tournament_channels(interaction.guild, name)
        short      = t_channels.get("short_name", "tourney")
        cat_name   = t_channels.get("category_name", "🏆 Tournament")

        def ch_id(key):
            v = t_channels.get(key)
            return int(v) if v else None

        ch_ann_id  = ch_id("announcements")
        ch_reg_id  = ch_id("register")
        ch_info_id = ch_id("info")
        ch_road_id = ch_id("roadmap")
        ch_grp_id  = ch_id("groups")
        ch_res_id  = ch_id("results")
        ch_cfm_id  = ch_id("confirm-teams")
        ch_hlp_id  = ch_id("help")
        ch_logo_id = ch_id("team-logo")

        # ── AI GFX ────────────────────────────────────────────────────
        can_gfx, _ = await check_feature(gid, "ai_gfx_poster")
        poster_url  = img_url(name, game, "poster", "prize " + prize_pool + " date " + date) if can_gfx else ""
        roadmap_url = img_url(name, game, "roadmap") if can_gfx else ""

        # ── Save to DB ────────────────────────────────────────────────
        rec = await b44_create("Tournament", {
            "guild_id": gid, "name": name, "game": game, "format": fmt,
            "prize_pool": prize_pool, "description": description,
            "status": "registration_open", "max_players": max_players,
            "team_size": team_size, "group_size": group_size, "rounds": rounds,
            "rules": rules, "stream_channel": stream, "eligible_nations": nations,
            "reg_deadline": reg_end, "registered_count": 0,
            "tournament_date": date, "tournament_time": time_str,
            "poster_image_url": poster_url, "roadmap_image_url": roadmap_url,
            "announcement_channel_id": str(ch_ann_id) if ch_ann_id else "",
            "registration_channel_id": str(ch_reg_id) if ch_reg_id else "",
            "short_name": short, "category_name": cat_name,
            "created_by_discord_id": str(interaction.user.id),
            "started_at": now_iso(),
        })
        tid = rec.get("id", "")

        fmt_label = {"single_elim": "Single Elimination", "double_elim": "Double Elimination",
                     "round_robin": "Round Robin", "battle_royale": "Battle Royale"}.get(fmt.lower().replace(" ", "_"), fmt)

        # ── Post to #info ──────────────────────────────────────────────
        if ch_info_id:
            info_e = discord.Embed(
                title=f"📋 {name} — Tournament Info",
                description=(
                    f"**🎮 Game:** {game}\n"
                    f"**🏆 Format:** {fmt_label}\n"
                    f"**💰 Prize Pool:** {prize_pool}\n"
                    f"**📅 Date:** {date}  |  **⏰ Time:** {time_str}\n"
                    f"**👥 Max Teams:** {max_players}  |  **Size:** {team_size}v{team_size}\n"
                    f"**🎯 Groups:** {group_size} teams/group  |  **Rounds:** {rounds}\n"
                    f"**🗓️ Reg Deadline:** {reg_end}\n"
                    f"**🌏 Nations:** {nations}\n"
                    + (f"\n**📜 Rules:**\n{rules}\n" if rules else "")
                    + (f"\n**📺 Stream:** {stream}\n" if stream else "")
                    + (f"\n{description}" if description else "")
                ),
                color=0x5865F2, timestamp=datetime.now(timezone.utc)
            )
            info_e.set_footer(text="NexPlay Tournament System")
            if poster_url: info_e.set_image(url=poster_url)
            await dpost(ch_info_id, info_e)

        # ── Post to #announcements ─────────────────────────────────────
        if ch_ann_id:
            ann_e = discord.Embed(
                title=f"🚨 {name.upper()} — REGISTRATION OPEN!",
                description=(
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎮 **Game:** {game}\n"
                    f"💰 **Prize Pool:** {prize_pool}\n"
                    f"📅 **Date:** {date}  |  ⏰ {time_str}\n"
                    f"👥 **Slots:** {max_players} teams ({team_size}v{team_size})\n"
                    f"🌏 **Nations:** {nations}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    + (f"✍️ Register → <#{ch_reg_id}>\n" if ch_reg_id else "")
                    + (f"📋 Info → <#{ch_info_id}>\n" if ch_info_id else "")
                    + (f"🗺️ Roadmap → <#{ch_road_id}>\n" if ch_road_id else "")
                    + (f"📺 Stream: **{stream}**\n" if stream else "")
                ),
                color=0x00FF7F, timestamp=datetime.now(timezone.utc)
            )
            ann_e.set_footer(text="NexPlay | Registration Open")
            if poster_url: ann_e.set_image(url=poster_url)
            await dpost(ch_ann_id, ann_e)

        # ── Post registration instructions ─────────────────────────────
        if ch_reg_id:
            reg_e = discord.Embed(
                title=f"📝 Register for {name}",
                description=(
                    f"Use `/register` to sign up your team!\n\n"
                    f"**Deadline:** {reg_end}\n"
                    f"**Team Size:** {team_size} players\n"
                    f"**Slots Left:** {max_players}\n\n"
                    f"After registering, submit your team logo in <#{ch_logo_id}> 🖼️"
                    if ch_logo_id else
                    f"Use `/register` to sign up your team!\n**Deadline:** {reg_end}"
                ),
                color=0xFFD700
            )
            reg_e.set_footer(text="NexPlay Registration")
            await dpost(ch_reg_id, reg_e)

        # ── Post roadmap ──────────────────────────────────────────────
        if ch_road_id:
            stage_names = ["Group Stage", "Quarter Final", "Semi Final", "Grand Final", "Championship"]
            roadmap_lines = [f"**Stage {i+1}:** {stage_names[i] if i < len(stage_names) else 'Round '+str(i+1)}" for i in range(min(rounds, 5))]
            road_e = discord.Embed(
                title=f"🗺️ {name} — Roadmap",
                description="\n".join(roadmap_lines),
                color=0xFF6B35
            )
            road_e.set_footer(text="NexPlay Roadmap")
            if roadmap_url: road_e.set_image(url=roadmap_url)
            await dpost(ch_road_id, road_e)

        # ── Confirm to staff (ephemeral followup) ──────────────────────
        confirm_e = discord.Embed(
            title="🎉 Tournament Created Successfully!",
            description=(
                f"**{name}** is live!\n\n"
                f"📁 Category: **{cat_name}**\n"
                + (f"📢 Announcement: <#{ch_ann_id}>\n" if ch_ann_id else "")
                + (f"✍️ Registration: <#{ch_reg_id}>\n" if ch_reg_id else "")
                + (f"🖼️ Logo Submission: <#{ch_logo_id}>\n" if ch_logo_id else "")
                + f"\n**ID:** `{tid}`\n"
                f"\nNext: `/generate_groups` after registration closes."
            ),
            color=0x00FF7F
        )
        confirm_e.set_footer(text="NexPlay Tournament System")
        await interaction.followup.send(embed=confirm_e, ephemeral=True)

        # ── Update server tournament count ─────────────────────────────
        srv = await get_server_record(gid)
        if srv:
            await b44_update("Server", srv["id"], {
                "tournaments_used": (srv.get("tournaments_used") or 0) + 1,
                "last_active": now_iso(),
            })


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


@tree.command(name="create_tournament", description="[Host] Create a tournament — opens a 3-step form")
async def cmd_create(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("❌ You need the **Tournament Host** role!"), ephemeral=True)
    allowed, reason = await is_allowed(str(interaction.guild.id), interaction.guild)
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)
    # Open step 1 modal
    await interaction.response.send_modal(TournamentStep1Modal())


@tree.command(name="edit_tournament", description="[Host] Edit an existing tournament details")
@app_commands.describe(tournament_name="Name of the tournament to edit")
async def cmd_edit_tournament_new(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("❌ You need the **Tournament Host** role!"), ephemeral=True)

    # Feature gate — Starter+
    await interaction.response.defer(thinking=True, ephemeral=True)
    ok, msg = await check_feature(str(interaction.guild.id), "edit_tournament")
    if not ok:
        return await interaction.followup.send(embed=err_e(msg), ephemeral=True)

    gid = str(interaction.guild.id)
    all_ts = await b44_list("Tournament", {"guild_id": gid})
    tournament = next((t for t in all_ts if tournament_name.lower() in t.get("name","").lower()), None)
    if not tournament:
        return await interaction.followup.send(embed=err_e(f"Tournament **{tournament_name}** not found."), ephemeral=True)

    view = TournamentEditSlotView(tournament)
    info_embed = discord.Embed(
        title=f"✏️ Edit: {tournament['name']}",
        description=(
            f"**Current values:**\n"
            f"🎮 Game: {tournament.get('game','')}\n"
            f"💰 Prize: {tournament.get('prize_pool','')}\n"
            f"📅 Date: {tournament.get('tournament_date','')}  |  ⏰ {tournament.get('tournament_time','')}\n"
            f"👥 Max Teams: {tournament.get('max_players','')}  |  Team Size: {tournament.get('team_size','')}v{tournament.get('team_size','')}\n"
            f"🎯 Group Size: {tournament.get('group_size','')}  |  Rounds: {tournament.get('rounds','')}\n"
            f"🌏 Nations: {tournament.get('eligible_nations','🇳🇵')}\n"
            f"📊 Status: {tournament.get('status','')}\n\n"
            f"Choose what to edit below:"
        ),
        color=0xFFA500
    )
    await interaction.followup.send(embed=info_embed, view=view, ephemeral=True)



# ══════════════════════════════════════════════════════════════════════════════
#  ELITE FEATURE COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="host_game", description="[Elite] Host a mini-game event (Trivia, Prediction, GG Hunt)")
@app_commands.describe(game_type="Type of mini-game to host")
@app_commands.choices(game_type=[
    app_commands.Choice(name="🧠 Trivia Challenge",      value="trivia"),
    app_commands.Choice(name="🎯 Prediction Game",       value="prediction"),
    app_commands.Choice(name="🔍 GG Hunt (hidden word)", value="gg_hunt"),
])
async def cmd_host_game(interaction: discord.Interaction, game_type: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("❌ You need the **Tournament Host** role!"), ephemeral=True)
    await interaction.response.defer(thinking=True)
    ok, msg = await check_feature(str(interaction.guild.id), "host_game")
    if not ok:
        return await interaction.followup.send(embed=err_e(msg), ephemeral=True)

    games = {
        "trivia": {
            "title": "🧠 TRIVIA CHALLENGE",
            "q": "What is the maximum number of players in a Free Fire match?",
            "a": "50",
            "hint": "It's between 40 and 60.",
            "color": 0x3498DB
        },
        "prediction": {
            "title": "🎯 PREDICTION GAME",
            "q": "Who will win the next NexPlay tournament? Reply with your prediction!",
            "a": None,
            "hint": "No wrong answers here — just fun!",
            "color": 0xE74C3C
        },
        "gg_hunt": {
            "title": "🔍 GG HUNT",
            "q": "Find the hidden word in this sentence: 'Great players always GRIND for the Glory!'",
            "a": "GRIND",
            "hint": "It's in ALL CAPS.",
            "color": 0x2ECC71
        },
    }
    g = games.get(game_type, games["trivia"])

    embed = discord.Embed(
        title=g["title"],
        description=(
            f"**❓ {g['q']}**\n\n"
            f"💡 Hint: *{g['hint']}*\n\n"
            + (f"Type your answer below! First correct answer wins 🏆" if g['a'] else "Drop your prediction! 👇")
        ),
        color=g["color"],
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="NexPlay Mini-Game | Hosted by " + interaction.user.display_name)
    await interaction.followup.send("@everyone", embed=embed)


@tree.command(name="suggest_improvement", description="[Elite] Get AI-powered server growth suggestions")
async def cmd_suggest_improvement(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("❌ Staff only!"), ephemeral=True)
    await interaction.response.defer(thinking=True)
    ok, msg = await check_feature(str(interaction.guild.id), "suggest_improvement")
    if not ok:
        return await interaction.followup.send(embed=err_e(msg), ephemeral=True)

    gid = str(interaction.guild.id)
    guild = interaction.guild
    active_t = None
    all_ts = await b44_list("Tournament", {"guild_id": gid})
    if all_ts:
        active_t = next((t for t in all_ts if t.get("status") in ("registration_open","in_progress")), all_ts[0])

    ctx = build_server_context(guild, active_t)
    prompt = (
        f"You are a Discord server growth expert for NexPlay, a Free Fire & Battle Royale esports platform.\n"
        f"Server context:\n{ctx}\n\n"
        f"Give 5 specific, actionable improvement suggestions to grow this server, "
        f"increase engagement, and attract more tournament participants. "
        f"Be specific, practical, and enthusiastic. Format as numbered list."
    )
    suggestions = await ai_generate(prompt)

    embed = discord.Embed(
        title="💡 NexPlay Growth Advisor",
        description=suggestions[:4000],
        color=0xF39C12,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="NexPlay AI Growth Advisor | Elite Plan")
    await interaction.followup.send(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
#  BOT LAUNCH
# ══════════════════════════════════════════════════════════════════════════════

if not BOT_TOKEN:
    print("[ERROR] DISCORD_BOT_TOKEN not set — cannot start bot.", flush=True)
else:
    bot.run(BOT_TOKEN)
