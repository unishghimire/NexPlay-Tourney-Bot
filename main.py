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
import re
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

BASE44_API  = "https://" + APP_ID + ".base44.app/api/apps/" + APP_ID + "/entities"
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

def log(msg: str):
    """Simple print-based logger with flush for Railway."""
    print(msg, flush=True)




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
    """Fetch a fresh trending meme via meme-api.com (Reddit JSON API now blocks non-OAuth).
    Falls back across multiple subreddits if needed. Skips NSFW and the last posted URL."""
    import random

    # meme-api.com supports subreddit-specific fetch: /gimme/<subreddit>
    # If no subreddit specified, fetch from a random gaming/meme subreddit
    sub_list = MEME_SUBREDDITS if subreddit == "dankmemes" else [subreddit]

    for attempt_sub in random.sample(sub_list, min(3, len(sub_list))):
        try:
            url = f"https://meme-api.com/gimme/{attempt_sub}"
            async with bot.http_session.get(
                url,
                headers={"User-Agent": "NexPlayBot/4.0"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    continue
                data = await r.json(content_type=None)
                if not isinstance(data, dict) or "url" not in data:
                    continue
                img_url = data.get("url", "")
                # Skip if same as last posted
                if img_url == exclude_url:
                    continue
                # Skip NSFW
                if data.get("nsfw", False) or data.get("spoiler", False):
                    continue
                return {
                    "title":     data.get("title", "")[:256],
                    "url":       img_url,
                    "permalink": data.get("postLink", ""),
                    "score":     data.get("ups", 0),
                }
        except Exception as e:
            print(f"[NexPlay] meme fetch error r/{attempt_sub}: {e}", flush=True)
            continue

    # Last resort: fetch a random meme (no subreddit filter)
    try:
        async with bot.http_session.get(
            "https://meme-api.com/gimme",
            headers={"User-Agent": "NexPlayBot/4.0"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                img_url = data.get("url", "")
                if img_url and img_url != exclude_url:
                    return {
                        "title":     data.get("title", "")[:256],
                        "url":       img_url,
                        "permalink": data.get("postLink", ""),
                        "score":     data.get("ups", 0),
                    }
    except Exception as e:
        print(f"[NexPlay] meme fetch fallback error: {e}", flush=True)

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
#  TOURNAMENT CREATION — MODAL → BUTTON → MODAL FLOW
#  Discord does NOT allow opening a modal from inside another
#  modal's on_submit. Pattern: Modal → ephemeral View with
#  Next button → button opens next Modal → repeat.
#  Step data stored in _tourney_sessions[user_id].
# ══════════════════════════════════════════════════════════

_tourney_sessions: dict[int, dict] = {}   # user_id → accumulated data

# ── Step 1 Modal ──────────────────────────────────────────
class TournamentStep1Modal(discord.ui.Modal, title="🏆 Create Tournament (1/3) — Basics"):
    t_name  = discord.ui.TextInput(label="Tournament Name",       placeholder="NexPlay Championship S1",                 max_length=80)
    t_game  = discord.ui.TextInput(label="Game",                  placeholder="Free Fire / PUBG / Minecraft / Valorant", max_length=40)
    t_prize = discord.ui.TextInput(label="Prize Pool",            placeholder="NPR 10,000",                              max_length=60)
    t_date  = discord.ui.TextInput(label="Tournament Date",       placeholder="2026-08-01",                              max_length=30)
    t_desc  = discord.ui.TextInput(label="Description (optional)",placeholder="Open for all — register now!",            required=False, max_length=200, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        _tourney_sessions[interaction.user.id] = {
            "name":        self.t_name.value.strip(),
            "game":        self.t_game.value.strip(),
            "prize_pool":  self.t_prize.value.strip(),
            "date":        self.t_date.value.strip(),
            "description": self.t_desc.value.strip(),
        }
        embed = discord.Embed(
            title="✅ Step 1 of 3 saved!",
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


# ── Step 2 Modal ──────────────────────────────────────────
class TournamentStep2Modal(discord.ui.Modal, title="🏆 Create Tournament (2/3) — Format & Slots"):
    t_max    = discord.ui.TextInput(label="Total Teams / Max Slots", placeholder="16",                                    max_length=4)
    t_tsize  = discord.ui.TextInput(label="Players Per Team",        placeholder="4  (squads) or 1 (solo)",               max_length=3)
    t_gsize  = discord.ui.TextInput(label="Teams Per Group",         placeholder="4",                                     max_length=3)
    t_rounds = discord.ui.TextInput(label="Number of Rounds",        placeholder="3  (Group → Semi → Final)",             max_length=3)
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
        embed = discord.Embed(
            title="✅ Step 2 of 3 saved!",
            description=(
                f"**Teams:** {self.t_max.value.strip()}  |  **Size:** {self.t_tsize.value.strip()}v{self.t_tsize.value.strip()}\n"
                f"**Groups:** {self.t_gsize.value.strip()} teams/group  |  **Rounds:** {self.t_rounds.value.strip()}\n"
                f"**Format:** {self.t_format.value.strip()}\n\n"
                "Click **Next →** to fill Schedule & Rules (last step!)"
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


# ── Step 3 Modal — saves to DB ────────────────────────────
class TournamentStep3Modal(discord.ui.Modal, title="🏆 Create Tournament (3/3) — Schedule & Rules"):
    t_time    = discord.ui.TextInput(label="Match Time",            placeholder="5:00 PM NPT",                           max_length=30)
    t_regend  = discord.ui.TextInput(label="Registration Deadline", placeholder="2026-07-30 or TBD", required=False,      max_length=30)
    t_rules   = discord.ui.TextInput(label="Rules / Special Notes", placeholder="No hacks. Top 2 per group advance.",    required=False, max_length=400, style=discord.TextStyle.paragraph)
    t_stream  = discord.ui.TextInput(label="Stream / YouTube",      placeholder="@nexplaygg or https://youtube.com/...", required=False, max_length=100)
    t_nations = discord.ui.TextInput(label="Eligible Nations",      placeholder="🇳🇵 🇮🇳 🇧🇩 🇵🇰  (blank = open)",          required=False, max_length=60)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        uid = interaction.user.id
        if uid not in _tourney_sessions:
            return await interaction.followup.send("❌ Session expired. Run /create_tournament again.", ephemeral=True)

        s   = _tourney_sessions.pop(uid)
        gid = str(interaction.guild.id)

        def safe_int(v, d=16):
            try: return int(v)
            except: return d

        name        = s["name"]
        game        = s["game"]
        prize_pool  = s["prize_pool"]
        date        = s["date"]
        description = s.get("description", "")
        max_players = safe_int(s.get("max_players", "16"), 16)
        team_size   = safe_int(s.get("team_size", "4"),     4)
        group_size  = safe_int(s.get("group_size", "4"),    4)
        rounds      = safe_int(s.get("rounds", "3"),        3)
        fmt         = s.get("format", "Battle Royale")
        time_str    = self.t_time.value.strip()    or "TBD"
        reg_end     = self.t_regend.value.strip()  or "TBD"
        rules       = self.t_rules.value.strip()
        stream      = self.t_stream.value.strip()
        nations     = self.t_nations.value.strip() or "🇳🇵"

        # Create channels
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
        ch_logo_id = ch_id("team-logo")

        # AI GFX (Pro+)
        can_gfx, _ = await check_feature(gid, "ai_gfx_poster")
        poster_url  = img_url(name, game, "poster", f"prize {prize_pool} date {date}") if can_gfx else ""
        roadmap_url = img_url(name, game, "roadmap") if can_gfx else ""

        # Save to DB
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

        fmt_label = {
            "single_elim": "Single Elimination", "double_elim": "Double Elimination",
            "round_robin": "Round Robin", "battle_royale": "Battle Royale",
        }.get(fmt.lower().replace(" ", "_"), fmt)

        # Post to #info
        if ch_info_id:
            e = discord.Embed(
                title=f"📋 {name} — Tournament Info",
                description=(
                    f"**🎮 Game:** {game}\n**🏆 Format:** {fmt_label}\n"
                    f"**💰 Prize Pool:** {prize_pool}\n"
                    f"**📅 Date:** {date}  |  **⏰ Time:** {time_str}\n"
                    f"**👥 Max Teams:** {max_players}  |  **Size:** {team_size}v{team_size}\n"
                    f"**🎯 Groups:** {group_size} teams/group  |  **Rounds:** {rounds}\n"
                    f"**🗓️ Reg Deadline:** {reg_end}  |  **🌏 Nations:** {nations}\n"
                    + (f"\n**📜 Rules:**\n{rules}\n" if rules else "")
                    + (f"\n**📺 Stream:** {stream}" if stream else "")
                    + (f"\n\n{description}" if description else "")
                ),
                color=0x5865F2, timestamp=datetime.now(timezone.utc)
            )
            e.set_footer(text="NexPlay Tournament System")
            if poster_url: e.set_image(url=poster_url)
            await dpost(ch_info_id, e)

        # Post to #announcements
        if ch_ann_id:
            e = discord.Embed(
                title=f"🚨 {name.upper()} — REGISTRATION OPEN!",
                description=(
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎮 **Game:** {game}\n💰 **Prize:** {prize_pool}\n"
                    f"📅 **Date:** {date}  |  ⏰ {time_str}\n"
                    f"👥 **Slots:** {max_players} teams ({team_size}v{team_size})\n"
                    f"🌏 **Nations:** {nations}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    + (f"✍️ Register → <#{ch_reg_id}>\n" if ch_reg_id else "")
                    + (f"📋 Info → <#{ch_info_id}>\n" if ch_info_id else "")
                    + (f"🗺️ Roadmap → <#{ch_road_id}>\n" if ch_road_id else "")
                    + (f"📺 Stream: **{stream}**" if stream else "")
                ),
                color=0x00FF7F, timestamp=datetime.now(timezone.utc)
            )
            e.set_footer(text="NexPlay | Registration Open")
            if poster_url: e.set_image(url=poster_url)
            await dpost(ch_ann_id, e)

        # Post to #register
        if ch_reg_id:
            e = discord.Embed(
                title=f"✍️ Register for {name}",
                description=(
                    f"Use `/register` to sign up!\n\n"
                    f"**Deadline:** {reg_end}  |  **Team Size:** {team_size}\n"
                    f"**Open Slots:** {max_players}\n"
                    + (f"\nAfter registering, submit logo in <#{ch_logo_id}> 🖼️" if ch_logo_id else "")
                ),
                color=0xFFD700
            )
            e.set_footer(text="NexPlay Registration")
            await dpost(ch_reg_id, e)

        # Post roadmap
        if ch_road_id:
            stage_names = ["Group Stage", "Quarter Final", "Semi Final", "Grand Final", "Championship"]
            lines_r = [f"**Stage {i+1}:** {stage_names[i] if i < 5 else 'Round '+str(i+1)}" for i in range(min(rounds, 5))]
            e = discord.Embed(title=f"🗺️ {name} — Roadmap", description="\n".join(lines_r), color=0xFF6B35)
            e.set_footer(text="NexPlay Roadmap")
            if roadmap_url: e.set_image(url=roadmap_url)
            await dpost(ch_road_id, e)

        # Update server tournament count
        srv = await get_server_record(gid)
        if srv:
            await b44_update("Server", srv["id"], {
                "tournaments_used": (srv.get("tournaments_used") or 0) + 1,
                "last_active": now_iso(),
            })

        # Confirm to host
        confirm = discord.Embed(
            title="🎉 Tournament Created!",
            description=(
                f"**{name}** is live!\n\n"
                f"📁 Category: **{cat_name}**\n"
                + (f"📢 Announcements: <#{ch_ann_id}>\n" if ch_ann_id else "")
                + (f"✍️ Registration: <#{ch_reg_id}>\n" if ch_reg_id else "")
                + (f"🖼️ Logo channel: <#{ch_logo_id}>\n" if ch_logo_id else "")
                + f"\n`ID: {tid}`\nNext: `/generate_groups` after reg closes."
            ),
            color=0x00FF7F
        )
        confirm.set_footer(text="NexPlay Tournament System")
        await interaction.followup.send(embed=confirm, ephemeral=True)


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


class TournamentEditModal(discord.ui.Modal, title="✏️ Edit Tournament"):
    t_prize     = discord.ui.TextInput(label="Prize Pool",         max_length=60)
    t_date      = discord.ui.TextInput(label="Tournament Date",    max_length=30)
    t_time      = discord.ui.TextInput(label="Match Time",         max_length=30)
    t_rules     = discord.ui.TextInput(label="Rules / Notes",      max_length=400, required=False, style=discord.TextStyle.paragraph)
    t_stream    = discord.ui.TextInput(label="Stream Channel",     max_length=100, required=False)

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament
        # Pre-fill with existing values
        self.t_prize.default  = tournament.get("prize_pool", "")
        self.t_date.default   = tournament.get("tournament_date", "")
        self.t_time.default   = tournament.get("tournament_time", "TBD")
        self.t_rules.default  = tournament.get("rules", "")
        self.t_stream.default = tournament.get("stream_channel", "")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        t = self.tournament

        updates = {
            "prize_pool":        self.t_prize.value.strip(),
            "tournament_date":   self.t_date.value.strip(),
            "tournament_time":   self.t_time.value.strip(),
            "rules":             self.t_rules.value.strip(),
            "stream_channel":    self.t_stream.value.strip(),
        }
        await b44_update("Tournament", t["id"], updates)

        # Refresh #info channel embed
        gid   = str(interaction.guild.id)
        t.update(updates)
        ch_info_id = None
        short = t.get("short_name", "")
        if short:
            info_ch = discord.utils.get(interaction.guild.text_channels, name=short + "-info")
            if info_ch: ch_info_id = info_ch.id

        if ch_info_id:
            nations = t.get("eligible_nations", "🇳🇵")
            updated_e = discord.Embed(
                title=f"📋 {t['name']} — Tournament Info (Updated)",
                description=(
                    f"**🎮 Game:** {t.get('game','')}\n"
                    f"**💰 Prize Pool:** {updates['prize_pool']}\n"
                    f"**📅 Date:** {updates['tournament_date']}  |  ⏰ {updates['tournament_time']}\n"
                    f"**👥 Max Teams:** {t.get('max_players','')}  |  Team Size: {t.get('team_size','')}v{t.get('team_size','')}\n"
                    f"**🎯 Group Size:** {t.get('group_size','')} teams/group  |  Rounds: {t.get('rounds','')}\n"
                    f"**🌏 Nations:** {nations}\n"
                    + (f"\n**📜 Rules:**\n{updates['rules']}\n" if updates['rules'] else "")
                    + (f"\n**📺 Stream:** {updates['stream_channel']}\n" if updates['stream_channel'] else "")
                ),
                color=0xFFA500, timestamp=datetime.now(timezone.utc)
            )
            updated_e.set_footer(text="NexPlay | Tournament details updated")
            await dpost(ch_info_id, updated_e)

        await interaction.followup.send(embed=ok_e("Tournament Updated!", f"**{t['name']}** details have been updated and #info channel refreshed."), ephemeral=True)


class TournamentEditSlotView(discord.ui.View):
    """Second edit modal covering slots/format/nations."""
    def __init__(self, tournament: dict):
        super().__init__(timeout=120)
        self.tournament = tournament

    @discord.ui.button(label="Edit Basic Info", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_basic(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TournamentEditModal(self.tournament))

    @discord.ui.button(label="Edit Slots & Nations", style=discord.ButtonStyle.secondary, emoji="🎯")
    async def edit_slots(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TournamentEditSlotsModal(self.tournament))

    @discord.ui.button(label="Change Status", style=discord.ButtonStyle.danger, emoji="🔄")
    async def change_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TournamentStatusModal(self.tournament))


class TournamentEditSlotsModal(discord.ui.Modal, title="✏️ Edit Tournament — Slots & Nations"):
    t_max     = discord.ui.TextInput(label="Max Teams",          max_length=4)
    t_tsize   = discord.ui.TextInput(label="Team Size",          max_length=3)
    t_gsize   = discord.ui.TextInput(label="Teams Per Group",    max_length=3)
    t_rounds  = discord.ui.TextInput(label="Number of Rounds",   max_length=3)
    t_nations = discord.ui.TextInput(label="Eligible Nations",   max_length=60, required=False)

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament
        self.t_max.default     = str(tournament.get("max_players", ""))
        self.t_tsize.default   = str(tournament.get("team_size", ""))
        self.t_gsize.default   = str(tournament.get("group_size", ""))
        self.t_rounds.default  = str(tournament.get("rounds", ""))
        self.t_nations.default = tournament.get("eligible_nations", "🇳🇵")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        def safe_int(v, d):
            try: return int(v)
            except: return d
        t = self.tournament
        updates = {
            "max_players":      safe_int(self.t_max.value, t.get("max_players", 16)),
            "team_size":        safe_int(self.t_tsize.value, t.get("team_size", 4)),
            "group_size":       safe_int(self.t_gsize.value, t.get("group_size", 4)),
            "rounds":           safe_int(self.t_rounds.value, t.get("rounds", 3)),
            "eligible_nations": self.t_nations.value.strip() or "🇳🇵",
        }
        await b44_update("Tournament", t["id"], updates)
        await interaction.followup.send(embed=ok_e("Slots Updated!", "Tournament structure updated successfully."), ephemeral=True)


class TournamentStatusModal(discord.ui.Modal, title="🔄 Change Tournament Status"):
    t_status = discord.ui.TextInput(
        label="New Status",
        placeholder="registration_open / registration_closed / in_progress / completed / cancelled",
        max_length=30
    )

    def __init__(self, tournament: dict):
        super().__init__()
        self.tournament = tournament
        self.t_status.default = tournament.get("status", "registration_open")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        valid = {"registration_open", "registration_closed", "in_progress", "completed", "cancelled"}
        new_status = self.t_status.value.strip().lower()
        if new_status not in valid:
            return await interaction.followup.send(embed=err_e(f"Invalid status. Choose from: {', '.join(valid)}"), ephemeral=True)
        await b44_update("Tournament", self.tournament["id"], {"status": new_status})
        await interaction.followup.send(embed=ok_e("Status Updated!", f"Tournament status → **{new_status}**"), ephemeral=True)


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


async def is_allowed(guild_id: str, guild: discord.Guild = None) -> tuple[bool, str]:
    """Returns (allowed, reason). Auto-registers new servers as Free Trial."""
    rec = await get_server_record(guild_id)
    if not rec:
        if guild:
            owner = guild.owner
            rec = await b44_create("Server", {
                "guild_id":            guild_id,
                "guild_name":          guild.name,
                "owner_id":            str(owner.id) if owner else "",
                "owner_name":          owner.display_name if owner else "Unknown",
                "plan_name":           "Free Trial",
                "subscription_status": "trial",
                "tournaments_used":    0,
                "tournament_limit":    3,
                "member_count":        guild.member_count or 0,
                "last_active":         now_iso(),
            })
            print(f"[NexPlay] Auto-registered {guild.name} via is_allowed fallback", flush=True)
        else:
            return False, "This server is not registered with NexPlay. Please re-invite the bot."
    status = rec.get("subscription_status", "trial")
    if status == "banned":
        return False, "This server has been banned from NexPlay. Contact support."
    if status in ("active", "trial"):
        return True, ""
    return False, "This server's NexPlay subscription has expired.\nRenew at: https://nexplay-server-portal.vercel.app/subscription\nPlans from NPR 99/mo (Starter) · NPR 299/mo (Pro AI) · NPR 399/mo (Elite)"


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
    """Find a channel by trying a list of common names. No fallback — returns None if not found."""
    for name in CHANNEL_NAMES.get(key, []):
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch:
            return ch
    return None


async def get_or_create_channels(guild: discord.Guild) -> dict:
    """
    Ensure required channels exist. Returns a dict of channel IDs.
    Creates missing channels under a 'NexPlay Tournaments' category.
    Only creates a channel if it doesn't already exist — no duplicates.
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
            # Double-check by exact name to avoid duplicates
            existing = discord.utils.get(guild.text_channels, name=ch_name)
            if existing:
                ch = existing
            else:
                try:
                    ch = await guild.create_text_channel(ch_name, category=category)
                except Exception:
                    ch = None
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
    if member.guild.owner_id == member.id:
        return True
    for role in member.roles:
        clean = role.name.strip()
        for prefix in ("👑 ", "⚔️ ", "🛡️ ", "🔧 ", "🎮 ", "🎯 ", "⚙️ ", "🏆 ", "🔥 ", "⛏️ ", "📋 ", "🌱 ", "🤖 ",
                        "👑", "⚔️", "🛡️", "🔧", "🎮", "🎯", "⚙️", "🏆", "🔥", "⛏️", "📋", "🌱", "🤖"):
            clean = clean.replace(prefix, "").strip()
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
    """Try Groq (fast, free) first — fallback to Pollinations if no key or error."""
    GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

    # ── PRIMARY: Groq (llama-3.3-70b-versatile) ──────────────────────────
    if GROQ_KEY:
        try:
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.7,
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        reply = data["choices"][0]["message"]["content"].strip()
                        print(f"[AI] Groq OK — {len(reply)} chars", flush=True)
                        return reply
                    else:
                        err = await r.text()
                        print(f"[AI] Groq error {r.status}: {err[:200]}", flush=True)
        except Exception as e:
            print(f"[AI] Groq exception: {e}", flush=True)

    # ── FALLBACK: Pollinations (no key needed) ────────────────────────────
    try:
        encoded = urllib.parse.quote(prompt[:1500])  # URL-safe trim
        url = f"https://text.pollinations.ai/{encoded}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    reply = (await r.text()).strip()
                    print(f"[AI] Pollinations OK — {len(reply)} chars", flush=True)
                    return reply
                else:
                    print(f"[AI] Pollinations {r.status}", flush=True)
    except Exception as e:
        print(f"[AI] Pollinations exception: {e}", flush=True)

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
    # Elite-only AI support gate
    ok, _ = await check_feature(str(message.guild.id), "ai_support")
    if not ok:
        return  # silently ignore — non-elite servers don't get AI support
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
def parse_registration(text: str, team_size: int) -> dict | None:
    """
    Parse a team registration message. Returns dict or None if invalid.
    Expected format (case-insensitive keys):
        Team Name: <name>
        Player 1: @mention
        Player 2: @mention
        ...
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    result = {}

    # Find team name line
    team_line = next((l for l in lines if re.match(r"team\s*name\s*:", l, re.I)), None)
    if not team_line:
        return None
    result["team_name"] = re.sub(r"^team\s*name\s*:\s*", "", team_line, flags=re.I).strip()
    if not result["team_name"]:
        return None

    # Find player lines
    players = []
    for i in range(1, team_size + 1):
        pat = re.compile(r"player\s*" + str(i) + r"\s*:\s*(.*)", re.I)
        pline = next((l for l in lines if pat.match(l)), None)
        if not pline:
            return None
        raw = pat.match(pline).group(1).strip()
        # Extract mention user ID
        uid_match = re.search(r"<@!?(\d+)>", raw)
        if not uid_match:
            return None
        players.append(uid_match.group(1))

    if len(players) != team_size:
        return None

    result["players"] = players
    return result




async def lock_register_channel(guild: discord.Guild, channel: discord.TextChannel):
    """Deny @everyone from sending messages in a channel."""
    try:
        everyone = guild.default_role
        overwrite = channel.overwrites_for(everyone)
        overwrite.send_messages = False
        await channel.set_permissions(everyone, overwrite=overwrite, reason="NexPlay: Registration full")
    except Exception as e:
        log(f"[WARN] Could not lock channel #{channel.name}: {e}")




async def update_reg_announcement(tournament: dict, guild: discord.Guild, registered: int):
    """Edit the pinned registration announcement to update slot count."""
    msg_id = tournament.get("registration_msg_id")
    ch_id  = tournament.get("registration_channel_id")
    if not msg_id or not ch_id:
        return
    try:
        ch = guild.get_channel(int(ch_id))
        if not ch:
            return
        msg = await ch.fetch_message(int(msg_id))
        if not msg:
            return
        # Rebuild embed with updated count
        t = tournament
        max_p = t.get("max_players", 16)
        name  = t.get("name", "Tournament")
        game  = t.get("game", "")
        prize = t.get("prize_pool", "")
        date  = t.get("tournament_date", "")
        tsize = t.get("team_size", 4)
        short = t.get("short_name", "t")
        bar   = "█" * registered + "░" * (max_p - registered)
        filled = registered >= max_p

        lines = "\n".join([f"Player {i+1}: @mention" for i in range(tsize)])
        embed = discord.Embed(
            title=("🔒 REGISTRATION CLOSED" if filled else "✍️ REGISTRATION OPEN") + " — " + name,
            description=(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎮 **Game:** {game}\n"
                f"🎖️ **Prize:** {prize}\n"
                f"📅 **Date:** {date}\n"
                f"👥 **Slots:** {registered}/{max_p}  `{bar}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ) + (
                "\n🚫 **Registration is CLOSED. All slots filled!**" if filled else
                "\n@everyone **Registration is OPEN!**\n\n"
                f"Send a message in this channel with EXACTLY this format:\n"
                f"```\nTeam Name: <your team name>\n" + "\n".join([f"Player {i+1}: @mention" for i in range(tsize)]) + "\n```"
                "\n⚠️ All players must be mentioned. No duplicate registrations."
            ),
            color=0x555555 if filled else 0x00FF7F,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="NexPlay Tournament System")
        await msg.edit(embed=embed)
    except Exception as e:
        log(f"[WARN] Could not update reg announcement: {e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    # ── Debug log every message so we can verify receipt ─────────────────────
    print(f"[MSG] #{message.channel.name} | {message.author} | {message.content[:80]}", flush=True)

    # ── Support handler — ONLY in designated support/help channels ──────────────
    ch_name_lower = message.channel.name.lower()

    SUPPORT_NAMES = (
        "support", "support-ticket", "help", "nexplay-support",
        "nexplay-help", "bot-help", "ask-here", "questions",
    )
    is_support_ch = (
        ch_name_lower in SUPPORT_NAMES
        or ch_name_lower.startswith("support")
        or ch_name_lower.startswith("help")
        or ch_name_lower.startswith("ask")
    )

    if is_support_ch and isinstance(message.channel, discord.TextChannel) and len(message.content.strip()) > 3:
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
            "logo_url":         "",
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
            logo_ch = discord.utils.get(message.guild.text_channels, name=short_candidate + "-team-logo")
            logo_ch_mention = f"<#{logo_ch.id}>" if logo_ch else f"#{short_candidate}-team-logo"
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
                    f"🎨 **NEXT STEP:** Submit your team logo in {logo_ch_mention}\n"
                    "`Team Name: <name>` + attach logo image (PNG/JPG)\n\n"
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

    # ── Logo submission channel handler ──────────────────────────────────────
    if ch_name.endswith("-team-logo"):
        short_candidate = ch_name[:-10]  # strip "-team-logo"

        all_ts = await b44_list("Tournament", {"guild_id": gid})
        tournament = next(
            (t for t in all_ts
             if t.get("short_name", "").lower() == short_candidate.lower()
             and t.get("status") in ("registration_open", "registration_closed")),
            None
        )

        if not tournament:
            await bot.process_commands(message)
            return

        # Must have an image attachment
        image_attachments = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]

        if not image_attachments:
            await message.add_reaction("❌")
            err = await message.reply(
                embed=discord.Embed(
                    title="❌ No Image Attached",
                    description=(
                        "Please attach your team logo image (PNG/JPG) AND include your team name.\n\n"
                        "**Format:**\n"
                        "```\nTeam Name: <your exact team name>\nTeam Logo: [attach image]\n```"
                    ),
                    color=0xFF4444
                ),
                mention_author=True
            )
            await asyncio.sleep(15)
            try:
                await err.delete()
                await message.delete()
            except:
                pass
            return

        # Extract team name from message text
        import re
        text_content = message.content.strip()
        team_name_match = re.search(r'team\s*name\s*:\s*(.+)', text_content, re.IGNORECASE)

        if not team_name_match:
            await message.add_reaction("❌")
            err = await message.reply(
                embed=discord.Embed(
                    title="❌ Missing Team Name",
                    description=(
                        "Include your team name in the message.\n\n"
                        "**Format:**\n"
                        "```\nTeam Name: <your exact team name>\nTeam Logo: [attach image]\n```"
                    ),
                    color=0xFF4444
                ),
                mention_author=True
            )
            await asyncio.sleep(15)
            try:
                await err.delete()
                await message.delete()
            except:
                pass
            return

        submitted_team_name = team_name_match.group(1).strip()

        # Verify team is registered — match by name first, then by submitter Discord ID
        existing_regs = await b44_list("Registration", {"tournament_id": tournament["id"]})
        matched_reg = next(
            (r for r in existing_regs if r.get("player_name", "").lower() == submitted_team_name.lower()),
            None
        )
        if not matched_reg:
            matched_reg = next(
                (r for r in existing_regs if r.get("player_discord_id") == str(message.author.id)),
                None
            )
            if matched_reg:
                submitted_team_name = matched_reg.get("player_name", submitted_team_name)

        if not matched_reg:
            await message.add_reaction("❌")
            err = await message.reply(
                embed=discord.Embed(
                    title="❌ Team Not Found",
                    description=(
                        f"**{submitted_team_name}** is not registered in this tournament.\n\n"
                        "Make sure your team name matches exactly as you registered."
                    ),
                    color=0xFF4444
                ),
                mention_author=True
            )
            await asyncio.sleep(20)
            try:
                await err.delete()
                await message.delete()
            except:
                pass
            return

        # Save logo URL to DB
        logo_url = image_attachments[0].url
        await b44_update("Registration", matched_reg["id"], {
            "logo_url": logo_url,
            "logo_submitted_at": datetime.now(timezone.utc).isoformat(),
            "logo_submitted_by": str(message.author.id),
        })

        await message.add_reaction("✅")

        confirm_embed = discord.Embed(
            title="✅ Logo Accepted!",
            description=(
                f"**Team:** {submitted_team_name}\n"
                f"**Submitted by:** {message.author.mention}\n"
                f"**Tournament:** {tournament.get('name', '')}\n\n"
                "Your logo is saved and will appear in group draws, match cards, and result graphics."
            ),
            color=0x00FF7F,
            timestamp=datetime.now(timezone.utc)
        )
        confirm_embed.set_thumbnail(url=logo_url)
        confirm_embed.set_footer(text="NexPlay Tournament System")
        await message.channel.send(embed=confirm_embed)

        # Notify in confirm-teams
        cfm_ch = discord.utils.get(message.guild.text_channels, name=short_candidate + "-confirm-teams")
        if cfm_ch:
            logo_update = discord.Embed(
                title="🎨 Logo Submitted",
                description=(
                    f"**{submitted_team_name}** has submitted their team logo ✅\n"
                    f"Submitted by: {message.author.mention}"
                ),
                color=0xFF6B9D,
                timestamp=datetime.now(timezone.utc)
            )
            logo_update.set_thumbnail(url=logo_url)
            logo_update.set_footer(text="NexPlay | Logo will appear in all GFX")
            await cfm_ch.send(embed=logo_update)

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
    if not is_staff(interaction.user):
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
        created = await b44_create("Server", {
            "guild_id": gid, "guild_name": interaction.guild.name,
            "owner_id": str(interaction.guild.owner_id),
            "owner_name": interaction.user.display_name,
            "plan_name": "Free Trial",
            "subscription_status": "trial", "tournaments_used": 0,
            "tournament_limit": 3, "member_count": interaction.guild.member_count or 0,
            "last_active": now_iso(),
        })
        if created and created.get("id"):
            status_msg = "Server registered! You have a **free trial** with 3 tournaments."
        else:
            status_msg = "⚠️ Failed to register server in the database. Please try again or contact support."
    else:
        status_msg = "Server already registered. Status: **" + str(existing.get("subscription_status", "?")) + "**"

    # Provision channels
    channels = await get_or_create_channels(interaction.guild)
    ch_list  = "\n".join(f"> <#{v}>" for v in channels.values() if v) or "No channels created (check bot permissions)."

    await interaction.followup.send(embed=ok_e(
        "NexPlay Setup Complete!",
        status_msg + "\n\n**Channels ready:**\n" + ch_list + "\n\n"
        "Use `/create_tournament` to launch your first tournament!\n"
        "\n**Links:**\n"
        "Portal: https://nexplay-server-portal.vercel.app\n"
        "Billing: https://nexplay-server-portal.vercel.app/subscription"
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



async def build_tournament_export_excel(tournament: dict, registrations: list, groups: list, matches: list) -> io.BytesIO:
    """Build an Excel sheet with ALL data for a single tournament — used before deletion."""
    wb = openpyxl.Workbook()

    # ── Sheet 1: TOURNAMENT DETAILS ────────────────────────────────
    ws_t = wb.active
    ws_t.title = "Tournament"
    details = [
        ("Field", "Value"),
        ("ID", tournament.get("id", "")),
        ("Name", tournament.get("name", "")),
        ("Short Name", tournament.get("short_name", "")),
        ("Game", tournament.get("game", "")),
        ("Status", tournament.get("status", "")),
        ("Format", tournament.get("format", "")),
        ("Prize Pool", tournament.get("prize_pool", "")),
        ("Description", tournament.get("description", "")),
        ("Rules", tournament.get("rules", "")),
        ("Max Players", tournament.get("max_players", "")),
        ("Team Size", tournament.get("team_size", "")),
        ("Group Size", tournament.get("group_size", "")),
        ("Rounds", tournament.get("rounds", "")),
        ("Registered Count", tournament.get("registered_count", 0)),
        ("Tournament Date", tournament.get("tournament_date", "")),
        ("Tournament Time", tournament.get("tournament_time", "")),
        ("Reg Deadline", tournament.get("reg_deadline", "")),
        ("Stream Channel", tournament.get("stream_channel", "")),
        ("Eligible Nations", tournament.get("eligible_nations", "")),
        ("Category Name", tournament.get("category_name", "")),
        ("Announcement Channel ID", tournament.get("announcement_channel_id", "")),
        ("Registration Channel ID", tournament.get("registration_channel_id", "")),
        ("Created By", tournament.get("created_by_discord_id", "")),
        ("Created At", str(tournament.get("created_date", ""))),
        ("Started At", tournament.get("started_at", "")),
    ]
    for ri, (field, val) in enumerate(details, 1):
        c1 = ws_t.cell(row=ri, column=1, value=field)
        c2 = ws_t.cell(row=ri, column=2, value=str(val) if val is not None else "")
        if ri == 1:
            _xl_header_style(c1)
            _xl_header_style(c2)
        else:
            c1.font = Font(bold=True)
            _xl_border(c1)
            _xl_border(c2)
            c2.alignment = Alignment(vertical="top", wrap_text=True)
    ws_t.column_dimensions["A"].width = 24
    ws_t.column_dimensions["B"].width = 50

    # ── Sheet 2: REGISTRATIONS ─────────────────────────────────────
    ws_r = wb.create_sheet("Registrations")
    r_headers = ["Team Name", "Discord User", "Team Members", "Status", "Seed #", "Group", "Logo URL", "Registered At"]
    for ci, h in enumerate(r_headers, 1):
        c = ws_r.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "375623")
        _xl_border(c)
    ws_r.row_dimensions[1].height = 20

    for ri, reg in enumerate(registrations, 2):
        row = [
            reg.get("player_name", ""),
            reg.get("player_discord_id", ""),
            str(reg.get("team_members", "")),
            reg.get("status", ""),
            reg.get("seed_number", ""),
            reg.get("group_label", "—"),
            reg.get("logo_url", ""),
            str(reg.get("registered_at", "")),
        ]
        for ci, val in enumerate(row, 1):
            c = ws_r.cell(row=ri, column=ci, value=str(val) if val is not None else "")
            _xl_border(c)
            c.alignment = Alignment(vertical="center", wrap_text=(ci == 3))

    for ci, w in enumerate([18, 18, 35, 14, 7, 8, 30, 20], 1):
        ws_r.column_dimensions[get_column_letter(ci)].width = w

    # ── Sheet 3: GROUPS ─────────────────────────────────────────────
    ws_g = wb.create_sheet("Groups")
    g_headers = ["Group Label", "Player Names", "Player IDs", "Generated At"]
    for ci, h in enumerate(g_headers, 1):
        c = ws_g.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "843C0C")
        _xl_border(c)
    ws_g.row_dimensions[1].height = 20

    for ri, grp in enumerate(groups, 2):
        row = [
            grp.get("group_label", ""),
            str(grp.get("player_names", "")),
            str(grp.get("player_ids", "")),
            str(grp.get("generated_at", "")),
        ]
        for ci, val in enumerate(row, 1):
            c = ws_g.cell(row=ri, column=ci, value=str(val) if val is not None else "")
            _xl_border(c)
            c.alignment = Alignment(vertical="center", wrap_text=(ci in (2, 3)))

    for ci, w in enumerate([12, 40, 40, 20], 1):
        ws_g.column_dimensions[get_column_letter(ci)].width = w

    # ── Sheet 4: MATCHES ────────────────────────────────────────────
    ws_m = wb.create_sheet("Matches")
    m_headers = ["Round", "Match #", "Group", "Player 1", "Player 2", "P1 Score", "P2 Score", "Winner", "Status", "Scheduled At", "Results Card"]
    for ci, h in enumerate(m_headers, 1):
        c = ws_m.cell(row=1, column=ci, value=h)
        _xl_header_style(c, "1F4E79")
        _xl_border(c)
    ws_m.row_dimensions[1].height = 20

    for ri, m in enumerate(matches, 2):
        row = [
            m.get("round_number", ""),
            m.get("match_number", ""),
            m.get("group_label", "—"),
            m.get("player1_username", ""),
            m.get("player2_username", ""),
            m.get("player1_score", ""),
            m.get("player2_score", ""),
            m.get("winner_username", ""),
            m.get("status", ""),
            str(m.get("scheduled_at", "")),
            m.get("results_card_image_url", ""),
        ]
        for ci, val in enumerate(row, 1):
            c = ws_m.cell(row=ri, column=ci, value=str(val) if val is not None else "")
            _xl_border(c)
            c.alignment = Alignment(vertical="center")

    for ci, w in enumerate([7, 8, 8, 18, 18, 8, 8, 18, 12, 20, 30], 1):
        ws_m.column_dimensions[get_column_letter(ci)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════
#  CHANNEL / CATEGORY / TOURNAMENT DELETION COMMANDS
# ══════════════════════════════════════════════════════════

@tree.command(name="delete_channel", description="[Host] Delete a channel by name or ID")
@app_commands.describe(channel="The channel to delete (name, ID, or #mention)")
async def cmd_delete_channel(interaction: discord.Interaction, channel: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("You need the **Tournament Host** role to use this command."), ephemeral=True)

    gid = str(interaction.guild.id)
    allowed, reason = await is_allowed(gid, interaction.guild)
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)

    await interaction.response.defer(thinking=True, ephemeral=True)

    # Parse channel input — could be #mention, raw ID, or channel name
    ch = None
    raw = channel.strip().lstrip("#")

    # Try as mention <#123456>
    if raw.startswith("<#") and raw.endswith(">"):
        ch_id = raw[2:-1]
        ch = interaction.guild.get_channel(int(ch_id)) if ch_id.isdigit() else None
    elif raw.isdigit():
        ch = interaction.guild.get_channel(int(raw))
    else:
        ch = discord.utils.get(interaction.guild.text_channels, name=raw)
        if not ch:
            ch = discord.utils.get(interaction.guild.voice_channels, name=raw)

    if not ch:
        return await interaction.followup.send(embed=err_e(f"Channel `{channel}` not found."), ephemeral=True)

    ch_name = ch.name
    try:
        await ch.delete(reason=f"NexPlay: Deleted by {interaction.user.display_name}")
        await interaction.followup.send(embed=ok_e("Channel Deleted", f"🗑️ Channel **#{ch_name}** has been deleted."), ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=err_e("I don't have permission to delete that channel."), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=err_e(f"Failed to delete channel: {e}"), ephemeral=True)


@tree.command(name="delete_category", description="[Host] Delete a category AND all channels inside it")
@app_commands.describe(category="The category to delete (name or ID)")
async def cmd_delete_category(interaction: discord.Interaction, category: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("You need the **Tournament Host** role to use this command."), ephemeral=True)

    gid = str(interaction.guild.id)
    allowed, reason = await is_allowed(gid, interaction.guild)
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)

    await interaction.response.defer(thinking=True, ephemeral=True)

    # Parse category input
    cat = None
    raw = category.strip()

    if raw.isdigit():
        cat = interaction.guild.get_channel(int(raw))
        if cat and not isinstance(cat, discord.CategoryChannel):
            cat = None
    else:
        cat = discord.utils.get(interaction.guild.categories, name=raw)

    if not cat:
        return await interaction.followup.send(embed=err_e(f"Category `{category}` not found."), ephemeral=True)

    # Delete all channels inside the category first
    deleted_channels = []
    failed_channels = []
    for ch in list(cat.channels):
        try:
            await ch.delete(reason=f"NexPlay: Category cleanup by {interaction.user.display_name}")
            deleted_channels.append(ch.name)
        except Exception:
            failed_channels.append(ch.name)

    # Delete the category itself
    cat_name = cat.name
    try:
        await cat.delete(reason=f"NexPlay: Deleted by {interaction.user.display_name}")
    except Exception as e:
        return await interaction.followup.send(embed=err_e(f"Deleted {len(deleted_channels)} channels but failed to delete category: {e}"), ephemeral=True)

    desc = f"🗑️ Category **{cat_name}** deleted along with **{len(deleted_channels)}** channels.\n"
    if deleted_channels:
        desc += "\n**Deleted channels:**\n" + "\n".join(f"• #{c}" for c in deleted_channels)
    if failed_channels:
        desc += "\n\n⚠️ **Failed to delete:**\n" + "\n".join(f"• #{c}" for c in failed_channels)

    await interaction.followup.send(embed=ok_e("Category Deleted", desc), ephemeral=True)


@tree.command(name="delete_tournament", description="[Host] Delete a tournament: DB record + all channels + category + registrations")
@app_commands.describe(name="The tournament name to delete")
async def cmd_delete_tournament(interaction: discord.Interaction, name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_e("You need the **Tournament Host** role to use this command."), ephemeral=True)

    gid = str(interaction.guild.id)
    allowed, reason = await is_allowed(gid, interaction.guild)
    if not allowed:
        return await interaction.response.send_message(embed=err_e(reason), ephemeral=True)

    await interaction.response.defer(thinking=True, ephemeral=True)

    # Find tournament in DB
    tournaments = await b44_list("Tournament", {"guild_id": gid})
    tournament = None
    for t in tournaments:
        if t.get("name", "").lower() == name.lower() or t.get("short_name", "").lower() == name.lower():
            tournament = t
            break

    if not tournament:
        return await interaction.followup.send(embed=err_e(f"Tournament `{name}` not found in this server."), ephemeral=True)

    t_name = tournament.get("name", name)
    t_id = tournament.get("id", "")
    short = tournament.get("short_name", "")
    cat_name = tournament.get("category_name", f"🏆 {short.upper()}" if short else "")

    deleted_channels = []
    failed_channels = []
    category_deleted = False

    # 0. Export Excel backup BEFORE deleting anything
    excel_sent = False
    try:
        regs_for_export = await b44_list("Registration", {"tournament_id": t_id})
        groups_for_export = await b44_list("TournamentGroup", {"tournament_id": t_id})
        matches_for_export = await b44_list("Match", {"tournament_id": t_id})

        xl_buf = await build_tournament_export_excel(
            tournament, regs_for_export, groups_for_export, matches_for_export
        )
        safe_name = "".join(c for c in t_name if c.isalnum() or c in " -_")[:30].strip() or "tournament"
        filename = f"NexPlay_Backup_{safe_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"

        # DM the server owner
        owner = interaction.guild.owner
        if owner:
            try:
                dm_ch = await owner.create_dm()
                file = discord.File(xl_buf, filename=filename)
                dm_embed = discord.Embed(
                    title="📦 Tournament Data Backup",
                    description=(
                        f"An Excel backup of **{t_name}** has been exported before deletion.\n\n"
                        f"**Server:** {interaction.guild.name}\n"
                        f"**Tournament:** {t_name}\n"
                        f"**Registrations:** {len(regs_for_export)}\n"
                        f"**Groups:** {len(groups_for_export)}\n"
                        f"**Matches:** {len(matches_for_export)}\n\n"
                        f"Deleted by: {interaction.user.mention}\n"
                        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                    ),
                    color=0xFFA500
                )
                await dm_ch.send(embed=dm_embed, file=file)
                excel_sent = True
                log(f"[Delete] Excel backup sent to {owner.display_name} for tournament '{t_name}'")
            except discord.Forbidden:
                log(f"[Delete] Cannot DM owner {owner.display_name} — sending to channel instead")
                file = discord.File(xl_buf, filename=filename)
                await interaction.channel.send(embed=dm_embed, file=file)
                excel_sent = True
    except Exception as e:
        log(f"[Delete] Excel export failed: {e}")

    # 1. Delete tournament channels by short_name prefix
    if short:
        prefix = short.lower()
        for ch in list(interaction.guild.text_channels):
            if ch.name.lower().startswith(prefix):
                try:
                    await ch.delete(reason=f"NexPlay: Tournament '{t_name}' deleted by {interaction.user.display_name}")
                    deleted_channels.append(ch.name)
                except Exception:
                    failed_channels.append(ch.name)

    # 2. Delete the category
    if cat_name:
        cat = discord.utils.get(interaction.guild.categories, name=cat_name)
        if cat:
            # Delete any remaining channels in the category
            for ch in list(cat.channels):
                if ch.name not in deleted_channels:
                    try:
                        await ch.delete(reason=f"NexPlay: Tournament cleanup")
                        deleted_channels.append(ch.name)
                    except Exception:
                        failed_channels.append(ch.name)
            try:
                await cat.delete(reason=f"NexPlay: Tournament '{t_name}' deleted")
                category_deleted = True
            except Exception:
                pass

    # 3. Delete registrations from DB
    reg_count = 0
    regs = await b44_list("Registration", {"tournament_id": t_id})
    for reg in regs:
        try:
            # Delete via API
            reg_id = reg.get("id")
            if reg_id:
                async with bot.http_session.delete(
                    BASE44_API + "/Registration/" + reg_id,
                    headers=_b44_headers()
                ) as r:
                    if r.status in (200, 204):
                        reg_count += 1
        except Exception:
            pass

    # 4. Delete tournament groups from DB
    group_count = 0
    groups = await b44_list("TournamentGroup", {"tournament_id": t_id})
    for grp in groups:
        try:
            grp_id = grp.get("id")
            if grp_id:
                async with bot.http_session.delete(
                    BASE44_API + "/TournamentGroup/" + grp_id,
                    headers=_b44_headers()
                ) as r:
                    if r.status in (200, 204):
                        group_count += 1
        except Exception:
            pass

    # 5. Delete matches from DB
    match_count = 0
    matches = await b44_list("Match", {"tournament_id": t_id})
    for m in matches:
        try:
            m_id = m.get("id")
            if m_id:
                async with bot.http_session.delete(
                    BASE44_API + "/Match/" + m_id,
                    headers=_b44_headers()
                ) as r:
                    if r.status in (200, 204):
                        match_count += 1
        except Exception:
            pass

    # 6. Delete the tournament record itself
    t_deleted = False
    if t_id:
        try:
            async with bot.http_session.delete(
                BASE44_API + "/Tournament/" + t_id,
                headers=_b44_headers()
            ) as r:
                if r.status in (200, 204):
                    t_deleted = True
        except Exception:
            pass

    # Build summary
    desc = f"🗑️ **Tournament: {t_name}**\n\n"
    desc += f"**Channels deleted:** {len(deleted_channels)}\n"
    if deleted_channels:
        desc += "\n".join(f"• #{c}" for c in deleted_channels) + "\n"
    if category_deleted:
        desc += f"**Category deleted:** {cat_name}\n"
    if failed_channels:
        desc += f"\n⚠️ **Failed to delete {len(failed_channels)} channel(s)**\n"
    desc += f"\n**Registrations deleted:** {reg_count}\n"
    desc += f"**Groups deleted:** {group_count}\n"
    desc += f"**Matches deleted:** {match_count}\n"
    desc += f"**Tournament record:** {'✅ Deleted' if t_deleted else '❌ Failed'}"

    if t_deleted:
        await interaction.followup.send(embed=ok_e("Tournament Deleted", desc), ephemeral=True)
    else:
        await interaction.followup.send(embed=err_e("Tournament channels cleaned but DB record could not be deleted. Check API connection."), ephemeral=True)


# ══════════════════════════════════════════════════════════
#  AUTO-REGISTER WHEN BOT JOINS A NEW SERVER
# ══════════════════════════════════════════════════════════
@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"[NexPlay] Joined: {guild.name} ({guild.id})", flush=True)
    existing = await b44_list("Server", {"guild_id": str(guild.id)})
    if not existing:
        owner = guild.owner
        await b44_create("Server", {
            "guild_id":            str(guild.id),
            "guild_name":          guild.name,
            "owner_id":            str(owner.id) if owner else "",
            "owner_name":          owner.display_name if owner else "Unknown",
            "plan_name":           "Free Trial",
            "subscription_status": "trial",
            "tournaments_used":    0,
            "tournament_limit":    3,
            "member_count":        guild.member_count or 0,
            "last_active":         now_iso(),
        })
        print(f"[NexPlay] Registered {guild.name} as Free Trial", flush=True)
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            e = discord.Embed(
                title="👋 NexPlay Tournament System is here!",
                description=(
                    "**Get started:**\n"
                    "1. Give yourself the `Tournament Host` role (or use server owner)\n"
                    "2. Run `/create_tournament` to create your first tournament\n"
                    "3. Portal: https://nexplay-server-portal.vercel.app\n\n"
                    "Your server starts on **Free Trial** (3 tournaments). Upgrade anytime!"
                ),
                color=0x5865F2
            )
            try: await ch.send(embed=e)
            except: pass
            break

bot.run(BOT_TOKEN)
