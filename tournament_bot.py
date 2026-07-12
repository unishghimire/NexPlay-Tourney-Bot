"""
NexPlay Tournament Bot — Full Production Build
Talks directly to Discord + Base44 Entity API
"""
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import json
import random
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get('DISCORD_BOT_TOKEN', '')
GUILD_ID     = int(os.environ.get('DISCORD_GUILD_ID', '0'))
SERVICE_TOKEN = os.environ.get('BASE44_SERVICE_TOKEN', '')
APP_ID       = '6a5226b5047f5c59d961130e'

BASE44_API   = f"https://base44.app/api/apps/{APP_ID}/entities"
DISCORD_API  = "https://discord.com/api/v10"

# Channel IDs (real from server)
CH_ANNOUNCEMENTS = 1525468060193128519   # tourney-announcements
CH_REGISTRATION  = 1525468063036604417   # tourney-registration
CH_BRACKETS      = 1525468065519894580   # brackets-results
CH_CHAMPIONS     = 1525468071085740116   # hall-of-champions
CH_GENERAL       = 1525468088332452010   # general
CH_SUPPORT       = 1525468119823421604   # support-ticket
CH_BOTCMDS       = 1525468112638574592   # bot-commands

# Role names
STAFF_ROLES = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]

# ── Intents ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)
tree = bot.tree

# ── Base44 Entity API ────────────────────────────────────────────────────────
async def b44_list(entity: str, filters: dict = None) -> list:
    """List entity records, optionally filtered."""
    url = f"{BASE44_API}/{entity}"
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers) as r:
            if r.status != 200:
                return []
            data = await r.json()
            if not isinstance(data, list):
                return []
            if filters:
                for k, v in filters.items():
                    data = [x for x in data if x.get(k) == v or x.get('data', {}).get(k) == v]
            return data

async def b44_create(entity: str, payload: dict) -> dict:
    url = f"{BASE44_API}/{entity}"
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers) as r:
            return await r.json() if r.status in (200, 201) else {}

async def b44_update(entity: str, record_id: str, payload: dict) -> dict:
    url = f"{BASE44_API}/{entity}/{record_id}"
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as s:
        async with s.put(url, json=payload, headers=headers) as r:
            return await r.json() if r.status in (200, 201) else {}

# ── Discord helpers ──────────────────────────────────────────────────────────
async def discord_post(channel_id: int, embed: discord.Embed) -> dict:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    body = {"embeds": [embed.to_dict()]}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers) as r:
            try:
                return await r.json()
            except Exception:
                return {}

def image_url(tournament_name: str, game: str, image_type: str, extra: str = "") -> str:
    prompts = {
        "poster":   f'Professional esports tournament poster "{tournament_name}", {game} gaming, neon dark background, gold purple, cinematic art',
        "roadmap":  f'Tournament roadmap timeline "{tournament_name}" {game} esports, stages Registration Group-Draw Schedule Match-Day Results Champion, modern dark design',
        "group":    f'Esports group draw reveal "{tournament_name}" {game}, {extra or "Groups A B C D"}, dark neon colorful panels',
        "schedule": f'Match schedule card "{tournament_name}" {game}, {extra or "match schedule"}, dark professional infographic',
        "result":   f'Match result card {extra}, "{tournament_name}" dark dramatic victory graphic',
        "champion": f'Champion victory "{extra}" wins "{tournament_name}" {game}, golden trophy, confetti, epic cinematic',
    }
    prompt = prompts.get(image_type, f'Professional esports graphic "{tournament_name}" {game}')
    encoded = prompt.replace(" ", "%20").replace(",", "%2C").replace('"', "%22")
    seed = int(datetime.now().timestamp())
    return f"https://image.pollinations.ai/prompt/{encoded}?width=1280&height=640&nologo=true&seed={seed}&model=flux"

def is_staff(member: discord.Member) -> bool:
    return any(r.name in STAFF_ROLES for r in member.roles)

def err_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xFF4444)

def ok_embed(title: str, desc: str, color: int = 0x00FF7F) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.set_footer(text="🇳🇵 NexPlay Tournament System")
    e.timestamp = datetime.utcnow()
    return e

# ────────────────────────────────────────────────────────────────────────────
# EVENTS
# ────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ {bot.user} is ONLINE!")
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Slash commands synced to guild {GUILD_ID}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="🏆 NexPlay Tournaments"
    ))
    # Post online embed to bot-commands
    embed = ok_embed(
        "✅ NexPlay Bot — ONLINE",
        "Tournament bot is live and fully operational!\n\n"
        "Use `/help` to see all commands.\n\n"
        "**Active Features:**\n"
        "• 🏆 Full tournament management\n"
        "• 🖼️ AI-generated posters & cards\n"
        "• 🤖 AI support in #support-ticket\n"
        "• 👋 Auto-role on member join",
        0xFFD700
    )
    await discord_post(CH_BOTCMDS, embed)


@bot.event
async def on_member_join(member: discord.Member):
    # Auto-assign member role
    role = discord.utils.get(member.guild.roles, name="🎮 NexPlay Member")
    if role:
        try:
            await member.add_roles(role, reason="Auto-assigned on join")
        except Exception as e:
            print(f"Role assign failed: {e}")

    # Welcome DM
    try:
        embed = ok_embed(
            f"👋 Welcome to NEXPLAY ORG, {member.display_name}!",
            "We host **Free Fire** & **Minecraft** tournaments with real prizes!\n\n"
            "**Get started:**\n"
            "• Read <#1525467982631796777> for rules\n"
            "• Check <#1525468060193128519> for active tournaments\n"
            "• Type `/help` in any channel for commands\n\n"
            "See you in the arena! 🏆",
            0xFFD700
        )
        await member.send(embed=embed)
    except Exception:
        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # AI Support in support channel
    if message.channel.id == CH_SUPPORT and len(message.content.strip()) > 5:
        async with message.channel.typing():
            await handle_support_message(message)

    await bot.process_commands(message)


async def handle_support_message(message: discord.Message):
    """Keyword-based AI support responder."""
    q = message.content.lower()
    guild_id = str(message.guild.id)

    # Get active tournament for context
    tournaments = await b44_list("Tournament", {"guild_id": guild_id})
    active = next((t for t in tournaments if t.get("status") not in ["completed", "cancelled"]), None)
    t_name = active.get("name", "the current tournament") if active else None
    t_status = active.get("status", "") if active else ""
    t_game = active.get("game", "") if active else ""
    t_prize = active.get("prize_pool", "") if active else ""
    t_date = active.get("tournament_date", "TBA") if active else "TBA"

    has = lambda kw: any(k in q for k in kw)

    if has(["payment","refund","billing","cost","price","subscription","upgrade","plan","buy"]):
        resp = "💳 This involves **billing** — I've notified the admin team!\nA staff member will respond shortly."
        color = 0xFF9900
        routed = True
    elif has(["account","banned","hack","stolen","password","login"]):
        resp = "🔐 Account issues need a **human moderator**. Staff have been notified!"
        color = 0xFF9900
        routed = True
    elif has(["register","sign up","join tournament","how to join","enroll"]):
        if active and t_status == "registration_open":
            resp = f"📋 **Register for {t_name}:**\n1. Type your in-game name here in the registration channel\n2. Wait for confirmation ✅\n\nStatus: **OPEN 🟢** | Game: {t_game} | Prize: {t_prize}"
        elif active:
            resp = f"📋 Registration for **{t_name}** is **CLOSED 🔒** (`{t_status}`)\n\nWatch announcements for the next tournament!"
        else:
            resp = "📋 No active registration right now. Watch **#tourney-announcements** for upcoming events!"
        color = 0x00FF7F
        routed = False
    elif has(["when","schedule","time","date","match time"]):
        if active:
            resp = f"📅 **{t_name}** | Status: `{t_status}` | Game: {t_game} | Date: {t_date}\n\nCheck **#brackets-results** for the full match schedule!"
        else:
            resp = "📅 No active tournaments right now. Watch **#tourney-announcements** for upcoming events!"
        color = 0x00FF7F
        routed = False
    elif has(["prize","reward","cash","winning"]):
        resp = f"🏆 **Prize Pool — {t_name}:** 💰 **{t_prize}**\n\nGive it your all!" if active else "🏆 Prize details are announced with each tournament!"
        color = 0xFFD700
        routed = False
    elif has(["rule","cheat","hack","exploit","regulation","fair"]):
        resp = "📜 **Tournament Rules:**\n1. No cheating/hacking — instant DQ\n2. Be in voice **10 min before** match\n3. Screenshot results and post them\n4. Respect all players & hosts\n5. Host decisions are **final**\n6. No-show = forfeit after 5 min\n\nFull rules: <#1525468068426547323>"
        color = 0x00FF7F
        routed = False
    elif has(["bracket","group","draw","round","opponent","my group","who do i play"]):
        if active and t_status in ["groups_generated","scheduled","in_progress"]:
            resp = f"🎯 **{t_name}:** Check **#brackets-results** for your group and matchups!\nStatus: `{t_status}` — {'LIVE 🔥' if t_status == 'in_progress' else 'coming soon!'}"
        else:
            resp = "🎯 Brackets haven't been generated yet. They'll appear in **#brackets-results** after registration closes!"
        color = 0x00FF7F
        routed = False
    elif has(["result","score","winner","who won","standing","leaderboard"]):
        resp = f"📊 Results are in **#brackets-results** after each match.\n{f'Current: **{t_name}** (`{t_status}`)' if active else 'No active tournament right now.'}"
        color = 0x00FF7F
        routed = False
    else:
        resp = "🤖 Not sure about that — flagged for a **human moderator**!\n\nQuick help:\n• **Registration** → #tourney-registration\n• **Schedule** → #brackets-results\n• **Rules** → #tourney-rules"
        color = 0xFF9900
        routed = True

    embed = discord.Embed(
        description=f"**{message.author.display_name} asked:** {message.content}\n\n{resp}",
        color=color
    )
    embed.set_footer(text="🇳🇵 NexPlay AI Support" + (" — Routed to Staff" if routed else ""))
    await message.channel.send(embed=embed)

    # Log to entity
    await b44_create("SupportMessage", {
        "guild_id": guild_id,
        "tournament_id": active.get("id") if active else "",
        "user_discord_id": str(message.author.id),
        "user_username": message.author.display_name,
        "question": message.content,
        "ai_response": resp,
        "confidence_score": 0.5 if routed else 0.9,
        "routed_to_human": routed,
        "category": "support"
    })


# ────────────────────────────────────────────────────────────────────────────
# SLASH COMMANDS
# ────────────────────────────────────────────────────────────────────────────

@tree.command(name="create_tournament", description="🏆 Create and announce a new tournament", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(name="Tournament name", game="Game", prize_pool="Prize pool (e.g. $500 / NPR 5000)", date="Date (e.g. 2026-07-20)", format="Format", max_players="Max players", description="Short description (optional)")
@app_commands.choices(game=[app_commands.Choice(name="Free Fire", value="Free Fire"), app_commands.Choice(name="Minecraft", value="Minecraft")])
@app_commands.choices(format=[app_commands.Choice(name="Single Elimination", value="single_elim"), app_commands.Choice(name="Double Elimination", value="double_elim"), app_commands.Choice(name="Round Robin", value="round_robin")])
async def cmd_create_tournament(interaction: discord.Interaction, name: str, game: str, prize_pool: str, date: str, format: str = "single_elim", max_players: int = 16, description: str = ""):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("You need **Tournament Host** or **Admin** role!"), ephemeral=True)

    await interaction.response.defer(thinking=True)

    poster   = image_url(name, game, "poster", f"prize {prize_pool} date {date}")
    roadmap  = image_url(name, game, "roadmap")

    # Save to DB
    tournament = await b44_create("Tournament", {
        "guild_id": str(interaction.guild.id),
        "name": name, "game": game, "format": format,
        "prize_pool": prize_pool, "description": description,
        "status": "registration_open", "max_players": max_players,
        "registered_count": 0, "tournament_date": date,
        "poster_image_url": poster, "roadmap_image_url": roadmap,
        "announcement_channel_id": str(CH_ANNOUNCEMENTS),
        "registration_channel_id": str(CH_REGISTRATION),
        "brackets_channel_id": str(CH_BRACKETS),
        "champions_channel_id": str(CH_CHAMPIONS),
        "created_by_discord_id": str(interaction.user.id)
    })

    # Announcement embed
    ann_embed = discord.Embed(
        title=f"🏆 {name} — TOURNAMENT ANNOUNCED!",
        description=(
            f"**🎮 Game:** {game}\n"
            f"**📋 Format:** {format.replace('_',' ').title()}\n"
            f"**🏅 Prize Pool:** {prize_pool}\n"
            f"**📅 Date:** {date}\n"
            f"**👥 Max Players:** {max_players}\n\n"
            + (f"> {description}\n\n" if description else "") +
            "**📍 Roadmap:**\n"
            "✅ Registration Open → 🎯 Group Draw → 📅 Schedule → ⚔️ Match Day → 📊 Results → 🏆 Champion"
        ),
        color=0xFFD700
    )
    ann_embed.set_image(url=poster)
    ann_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    ann_embed.timestamp = datetime.utcnow()
    await discord_post(CH_ANNOUNCEMENTS, ann_embed)

    # Registration embed
    reg_embed = discord.Embed(
        title=f"📋 Registration OPEN — {name}",
        description=(
            f"**Registration is now OPEN!** 🎉\n\n"
            f"**🎮 Game:** {game}\n"
            f"**🏅 Prize Pool:** {prize_pool}\n"
            f"**👥 Slots:** {max_players} max\n"
            f"**📅 Date:** {date}\n\n"
            "**How to Register:**\n"
            "> Use `/register` and type your in-game name!\n\n"
            "*Limited spots — register now!*"
        ),
        color=0x00FF7F
    )
    reg_embed.set_image(url=roadmap)
    reg_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    reg_embed.timestamp = datetime.utcnow()
    await discord_post(CH_REGISTRATION, reg_embed)

    # Log
    if tournament.get("id"):
        await b44_create("AnnouncementLog", {
            "tournament_id": tournament["id"],
            "guild_id": str(interaction.guild.id),
            "milestone": "tournament_created",
            "channel_id": str(CH_ANNOUNCEMENTS),
            "announced_at": datetime.utcnow().isoformat(),
            "content_summary": f'Tournament "{name}" created and announced'
        })

    result_embed = discord.Embed(
        title="🏆 Tournament Created!",
        description=f"**{name}** is now live!\n\nPoster posted to announcements ✅\nRegistration open in #tourney-registration ✅\nAI images generated ✅",
        color=0xFFD700
    )
    result_embed.set_image(url=poster)
    result_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await interaction.followup.send(embed=result_embed)


@tree.command(name="register", description="📋 Register for the active tournament", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(tournament_name="Tournament name", ingame_name="Your in-game name / ID")
async def cmd_register(interaction: discord.Interaction, tournament_name: str, ingame_name: str):
    await interaction.response.defer(thinking=True, ephemeral=True)

    guild_id = str(interaction.guild.id)
    tournaments = await b44_list("Tournament", {"guild_id": guild_id, "name": tournament_name})
    if not tournaments:
        return await interaction.followup.send(embed=err_embed(f'Tournament "{tournament_name}" not found.'), ephemeral=True)
    t = tournaments[0]

    if t.get("status") != "registration_open":
        return await interaction.followup.send(embed=err_embed("Registration is not currently open for this tournament."), ephemeral=True)

    # Check existing
    existing = await b44_list("Registration", {"tournament_id": t["id"], "player_discord_id": str(interaction.user.id)})
    if existing:
        return await interaction.followup.send(embed=err_embed("You're already registered for this tournament!"), ephemeral=True)

    # Check full
    all_regs = await b44_list("Registration", {"tournament_id": t["id"]})
    if len(all_regs) >= (t.get("max_players") or 16):
        return await interaction.followup.send(embed=err_embed("Tournament is full! No more slots available."), ephemeral=True)

    await b44_create("Registration", {
        "tournament_id": t["id"],
        "guild_id": guild_id,
        "player_discord_id": str(interaction.user.id),
        "player_username": ingame_name,
        "player_display_name": interaction.user.display_name,
        "registered_at": datetime.utcnow().isoformat(),
        "checked_in": False,
        "seed_number": len(all_regs) + 1
    })

    await b44_update("Tournament", t["id"], {"registered_count": len(all_regs) + 1})

    embed = ok_embed(
        "✅ Registered!",
        f"You're in for **{tournament_name}**!\n\n"
        f"🎮 **In-Game Name:** {ingame_name}\n"
        f"🔢 **Slot:** {len(all_regs)+1}/{t.get('max_players',16)}\n\n"
        "Good luck! 🏆"
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="close_registration", description="🔒 Close registration for a tournament", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(tournament_name="Tournament name")
async def cmd_close_registration(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No permission!"), ephemeral=True)

    await interaction.response.defer(thinking=True)
    guild_id = str(interaction.guild.id)
    tournaments = await b44_list("Tournament", {"guild_id": guild_id, "name": tournament_name})
    if not tournaments:
        return await interaction.followup.send(embed=err_embed(f'Tournament "{tournament_name}" not found.'))
    t = tournaments[0]

    await b44_update("Tournament", t["id"], {"status": "registration_closed"})

    regs = await b44_list("Registration", {"tournament_id": t["id"]})
    player_list = "\n".join([f"{i+1}. **{r.get('player_username','?')}**" for i, r in enumerate(regs[:30])]) or "*None*"

    embed = discord.Embed(
        title=f"🔒 Registration CLOSED — {tournament_name}",
        description=f"Registration closed with **{len(regs)} players** confirmed!\n\n**Players:**\n{player_list}\n\nGroups & schedule coming soon! 👀",
        color=0xFF4500
    )
    embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    embed.timestamp = datetime.utcnow()
    await discord_post(CH_ANNOUNCEMENTS, embed)

    await b44_create("AnnouncementLog", {
        "tournament_id": t["id"], "guild_id": guild_id, "milestone": "registration_closed",
        "channel_id": str(CH_ANNOUNCEMENTS), "announced_at": datetime.utcnow().isoformat(),
        "content_summary": f"Registration closed: {len(regs)} players"
    })

    await interaction.followup.send(embed=ok_embed("🔒 Registration Closed", f"**{len(regs)} players** confirmed for **{tournament_name}**!\nAnnouncement posted. ✅"))


@tree.command(name="generate_groups", description="🎯 Generate and reveal tournament groups", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(tournament_name="Tournament name")
async def cmd_generate_groups(interaction: discord.Interaction, tournament_name: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No permission!"), ephemeral=True)

    await interaction.response.defer(thinking=True)
    guild_id = str(interaction.guild.id)
    tournaments = await b44_list("Tournament", {"guild_id": guild_id, "name": tournament_name})
    if not tournaments:
        return await interaction.followup.send(embed=err_embed(f'Tournament "{tournament_name}" not found.'))
    t = tournaments[0]

    regs = await b44_list("Registration", {"tournament_id": t["id"]})
    if len(regs) < 2:
        return await interaction.followup.send(embed=err_embed("Need at least 2 players to generate groups."))

    # Shuffle and split
    shuffled = regs[:]
    random.shuffle(shuffled)
    n_groups = max(2, min(8, len(shuffled) // 2))
    group_size = max(2, -(-len(shuffled) // n_groups))  # ceiling division
    labels = list("ABCDEFGH")
    groups = []

    for i in range(0, len(shuffled), group_size):
        chunk = shuffled[i:i+group_size]
        label = labels[len(groups)] if len(groups) < len(labels) else f"G{len(groups)+1}"
        groups.append({"label": label, "players": [p.get("player_username","?") for p in chunk], "records": chunk})

    # Save groups to DB and update player records
    for g in groups:
        await b44_create("TournamentGroup", {
            "tournament_id": t["id"], "guild_id": guild_id,
            "group_label": g["label"],
            "player_names": g["players"],
            "generated_at": datetime.utcnow().isoformat()
        })
        for rec in g["records"]:
            await b44_update("Registration", rec["id"], {"group_label": g["label"]})

    await b44_update("Tournament", t["id"], {"status": "groups_generated"})

    groups_text = " | ".join([f"Group {g['label']}: {', '.join(g['players'])}" for g in groups])
    img = image_url(tournament_name, t.get("game",""), "group", groups_text)

    group_desc = "\n\n".join([f"**Group {g['label']}:**\n" + "\n".join([f"> 🎮 {p}" for p in g["players"]]) for g in groups])

    bracket_embed = discord.Embed(title=f"🎯 Group Draw — {tournament_name}", description=group_desc, color=0x9B59B6)
    bracket_embed.set_image(url=img)
    bracket_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    bracket_embed.timestamp = datetime.utcnow()
    await discord_post(CH_BRACKETS, bracket_embed)

    ann_embed = discord.Embed(
        title=f"📢 Groups Revealed — {tournament_name}!",
        description=f"The group draw is complete! 🎉\nCheck <#{CH_BRACKETS}> to see your group!\n\n⚔️ **{len(regs)} players** | **{len(groups)} groups** — may the best win!",
        color=0x9B59B6
    )
    ann_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await discord_post(CH_ANNOUNCEMENTS, ann_embed)

    await b44_create("AnnouncementLog", {
        "tournament_id": t["id"], "guild_id": guild_id, "milestone": "groups_revealed",
        "channel_id": str(CH_BRACKETS), "announced_at": datetime.utcnow().isoformat(),
        "content_summary": f"{len(groups)} groups for {len(regs)} players"
    })

    result_embed = discord.Embed(
        title="🎯 Groups Generated!",
        description=f"**{len(groups)} groups** revealed!\n\n" + "\n".join([f"**Group {g['label']}:** {', '.join(g['players'])}" for g in groups]),
        color=0x9B59B6
    )
    result_embed.set_image(url=img)
    result_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await interaction.followup.send(embed=result_embed)


@tree.command(name="post_schedule", description="📅 Post the match schedule", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(tournament_name="Tournament name", schedule_text="Schedule details (rounds, times, etc.)")
async def cmd_post_schedule(interaction: discord.Interaction, tournament_name: str, schedule_text: str):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No permission!"), ephemeral=True)

    await interaction.response.defer(thinking=True)
    guild_id = str(interaction.guild.id)
    tournaments = await b44_list("Tournament", {"guild_id": guild_id, "name": tournament_name})
    if not tournaments:
        return await interaction.followup.send(embed=err_embed(f'Tournament "{tournament_name}" not found.'))
    t = tournaments[0]

    img = image_url(tournament_name, t.get("game",""), "schedule", schedule_text)
    await b44_update("Tournament", t["id"], {"status": "scheduled", "schedule_image_url": img})

    sched_embed = discord.Embed(
        title=f"📅 Match Schedule — {tournament_name}",
        description=f"**The match schedule is LIVE!** ⚔️\n\n{schedule_text}\n\n⚠️ Be ready **10 minutes before** your match — late arrivals forfeit!",
        color=0x1E90FF
    )
    sched_embed.set_image(url=img)
    sched_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    sched_embed.timestamp = datetime.utcnow()
    await discord_post(CH_BRACKETS, sched_embed)

    await discord_post(CH_ANNOUNCEMENTS, discord.Embed(
        title=f"📢 Schedule Posted — {tournament_name}",
        description=f"Match schedule is live in <#{CH_BRACKETS}>!\nCheck your match time and be ready. Good luck! 🎮",
        color=0x1E90FF
    ))

    await b44_create("AnnouncementLog", {
        "tournament_id": t["id"], "guild_id": guild_id, "milestone": "schedule_posted",
        "channel_id": str(CH_BRACKETS), "announced_at": datetime.utcnow().isoformat(),
        "content_summary": "Schedule posted"
    })

    await interaction.followup.send(embed=ok_embed("📅 Schedule Posted!", f"Match schedule for **{tournament_name}** is live! ✅"))


@tree.command(name="post_result", description="⚔️ Post a match result", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(tournament_name="Tournament name", player1="Player 1 username", player2="Player 2 username", winner="Winner's username", score="Score (e.g. 3-1)", round_number="Round number")
async def cmd_post_result(interaction: discord.Interaction, tournament_name: str, player1: str, player2: str, winner: str, score: str = "N/A", round_number: int = 1):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No permission!"), ephemeral=True)

    await interaction.response.defer(thinking=True)
    guild_id = str(interaction.guild.id)
    tournaments = await b44_list("Tournament", {"guild_id": guild_id, "name": tournament_name})
    if not tournaments:
        return await interaction.followup.send(embed=err_embed(f'Tournament "{tournament_name}" not found.'))
    t = tournaments[0]

    img = image_url(tournament_name, t.get("game",""), "result", f"{player1} vs {player2} score {score} winner {winner}")

    await b44_create("Match", {
        "tournament_id": t["id"], "guild_id": guild_id,
        "round_number": round_number, "match_number": 1,
        "player1_username": player1, "player2_username": player2,
        "winner_username": winner, "status": "completed",
        "results_card_image_url": img
    })

    result_embed = discord.Embed(
        title=f"⚔️ Match Result — {tournament_name}",
        description=f"**{player1}** vs **{player2}**\n\n🏆 **Winner: {winner}**\n📊 **Score:** {score}\n\n> {winner} advances to the next round!",
        color=0x1E90FF
    )
    result_embed.set_image(url=img)
    result_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    result_embed.timestamp = datetime.utcnow()
    await discord_post(CH_BRACKETS, result_embed)

    await b44_create("AnnouncementLog", {
        "tournament_id": t["id"], "guild_id": guild_id, "milestone": "results_posted",
        "channel_id": str(CH_BRACKETS), "announced_at": datetime.utcnow().isoformat(),
        "content_summary": f"{player1} vs {player2} — Winner: {winner} ({score})"
    })

    await interaction.followup.send(embed=ok_embed("⚔️ Result Posted!", f"**{winner}** wins vs {player1 if winner==player2 else player2}!\nScore: `{score}` — Card posted to brackets ✅", 0x1E90FF))


@tree.command(name="complete_tournament", description="🏆 Complete a tournament and crown the champion", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(tournament_name="Tournament name", winner="Champion's username", second="2nd place (optional)", third="3rd place (optional)")
async def cmd_complete(interaction: discord.Interaction, tournament_name: str, winner: str, second: str = "", third: str = ""):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No permission!"), ephemeral=True)

    await interaction.response.defer(thinking=True)
    guild_id = str(interaction.guild.id)
    tournaments = await b44_list("Tournament", {"guild_id": guild_id, "name": tournament_name})
    if not tournaments:
        return await interaction.followup.send(embed=err_embed(f'Tournament "{tournament_name}" not found.'))
    t = tournaments[0]

    img = image_url(tournament_name, t.get("game",""), "champion", winner)

    await b44_update("Tournament", t["id"], {
        "status": "completed", "winner_username": winner,
        "second_place": second, "third_place": third,
        "completed_at": datetime.utcnow().isoformat()
    })

    champ_embed = discord.Embed(
        title=f"🏆 CHAMPION — {tournament_name}",
        description=(
            f"**Congratulations to {winner}!** 🎉🎊\n\n"
            f"🥇 **1st Place:** {winner}\n"
            + (f"🥈 **2nd Place:** {second}\n" if second else "")
            + (f"🥉 **3rd Place:** {third}\n" if third else "") +
            f"\n**🏅 Prize Pool:** {t.get('prize_pool','TBA')}\n\n"
            "*Thank you to all participants for an amazing tournament!*"
        ),
        color=0xFFD700
    )
    champ_embed.set_image(url=img)
    champ_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    champ_embed.timestamp = datetime.utcnow()
    await discord_post(CH_CHAMPIONS, champ_embed)

    ann_embed = discord.Embed(
        title=f"🎊 {tournament_name} IS COMPLETE!",
        description=(
            f"What an incredible tournament! 🔥\n\n"
            f"🥇 **Champion: {winner}**\n"
            + (f"🥈 **Runner-up: {second}**\n" if second else "")
            + (f"🥉 **3rd Place: {third}**\n" if third else "") +
            f"\n**Prize Pool:** {t.get('prize_pool','TBA')}\n\n"
            "See you at the next tournament! 🎮"
        ),
        color=0xFFD700
    )
    ann_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await discord_post(CH_ANNOUNCEMENTS, ann_embed)

    await b44_create("AnnouncementLog", {
        "tournament_id": t["id"], "guild_id": guild_id, "milestone": "tournament_complete",
        "channel_id": str(CH_ANNOUNCEMENTS), "announced_at": datetime.utcnow().isoformat(),
        "content_summary": f"Champion: {winner}"
    })

    result_embed = discord.Embed(
        title="🎊 Tournament Complete!",
        description=f"🏆 **Champion: {winner}**\nChampion card posted to Hall of Champions! ✅",
        color=0xFFD700
    )
    result_embed.set_image(url=img)
    result_embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await interaction.followup.send(embed=result_embed)


@tree.command(name="tournament_status", description="📊 View all active tournaments", guild=discord.Object(id=GUILD_ID))
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    tournaments = await b44_list("Tournament", {"guild_id": str(interaction.guild.id)})

    if not tournaments:
        return await interaction.followup.send(embed=err_embed("No tournaments found."), ephemeral=True)

    STATUS_EMOJI = {
        "registration_open": "🟢", "registration_closed": "🔒",
        "groups_generated": "🎯", "scheduled": "📅",
        "in_progress": "🔥", "completed": "✅", "cancelled": "❌"
    }

    embed = discord.Embed(title="📊 Tournament Status", color=0x9B59B6)
    for t in tournaments[-10:]:
        emoji = STATUS_EMOJI.get(t.get("status",""), "❓")
        embed.add_field(
            name=f"{emoji} {t.get('name','?')}",
            value=f"🎮 {t.get('game','?')} | 👥 {t.get('registered_count',0)}/{t.get('max_players','?')} | `{t.get('status','?')}`",
            inline=False
        )
    embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="announce", description="📣 Post a custom announcement", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message="Announcement message", ping_everyone="Ping @everyone?")
async def cmd_announce(interaction: discord.Interaction, message: str, ping_everyone: bool = False):
    if not is_staff(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No permission!"), ephemeral=True)

    await interaction.response.defer(thinking=True)

    embed = discord.Embed(title="📢 NexPlay Announcement", description=message, color=0xFFD700)
    embed.set_footer(text=f"🇳🇵 NexPlay | Posted by {interaction.user.display_name}")
    embed.timestamp = datetime.utcnow()

    url = f"{DISCORD_API}/channels/{CH_ANNOUNCEMENTS}/messages"
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    body = {"embeds": [embed.to_dict()]}
    if ping_everyone:
        body["content"] = "@everyone"

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers) as r:
            pass

    await interaction.followup.send(embed=ok_embed("📣 Announced!", "Your announcement has been posted! ✅"))


@tree.command(name="help", description="❓ Show all NexPlay bot commands", guild=discord.Object(id=GUILD_ID))
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(title="🇳🇵 NexPlay Tournament Bot — Commands", color=0xFFD700)
    embed.add_field(name="🏆 Staff Commands", value=(
        "`/create_tournament` — Create & announce a new tournament\n"
        "`/close_registration` — Close player signups\n"
        "`/generate_groups` — Shuffle & reveal groups\n"
        "`/post_schedule` — Post the match schedule\n"
        "`/post_result` — Post a match result\n"
        "`/complete_tournament` — Crown the champion\n"
        "`/announce` — Post a custom announcement"
    ), inline=False)
    embed.add_field(name="🎮 Player Commands", value=(
        "`/register` — Register for a tournament\n"
        "`/tournament_status` — View active tournaments\n"
        "`/help` — Show this menu"
    ), inline=False)
    embed.add_field(name="🤖 Auto Features", value=(
        "• **Support AI** — Auto-answers questions in #support-ticket\n"
        "• **Auto-role** — Members get 🎮 NexPlay Member on join\n"
        "• **Welcome DM** — New members get a welcome message\n"
        "• **AI Images** — Every tournament stage gets an auto-generated card"
    ), inline=False)
    embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ────────────────────────────────────────────────────────────────────────────
# RUN
# ────────────────────────────────────────────────────────────────────────────
bot.run(BOT_TOKEN)
