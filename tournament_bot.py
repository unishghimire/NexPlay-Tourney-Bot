import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import json
from dotenv import load_dotenv

load_dotenv('/app/.agents/.env')

BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GUILD_ID = int(os.environ.get('DISCORD_GUILD_ID', '0'))

# Base44 backend function base URL
BASE44_API = "https://api.base44.com/api/apps/6a5226b5047f5c59d961130e/functions"
SERVICE_TOKEN = os.environ.get('BASE44_SERVICE_TOKEN', '')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)
tree = bot.tree

# ─────────────────────────────────────────
# Helper: call backend function
# ─────────────────────────────────────────
async def call_backend(function_name: str, payload: dict) -> dict:
    url = f"{BASE44_API}/{function_name}"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": SERVICE_TOKEN
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            try:
                return await resp.json()
            except Exception:
                text = await resp.text()
                return {"error": text, "status": resp.status}

# ─────────────────────────────────────────
# Helper: error embed
# ─────────────────────────────────────────
def error_embed(msg: str):
    return discord.Embed(description=f"❌ {msg}", color=0xFF4444)

def success_embed(msg: str):
    return discord.Embed(description=f"✅ {msg}", color=0x00FF7F)

# ─────────────────────────────────────────
# ON READY
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online!")
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Slash commands synced to guild {GUILD_ID}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="🏆 NexPlay Tournaments"
        )
    )

# ─────────────────────────────────────────
# AUTO MEMBER ROLE ON JOIN
# ─────────────────────────────────────────
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    role = discord.utils.get(guild.roles, name="🎮 NexPlay Member")
    if role:
        try:
            await member.add_roles(role, reason="Auto-assigned on join")
            print(f"✅ Gave NexPlay Member to {member.name}")
        except Exception as e:
            print(f"❌ Could not assign role: {e}")

    # Welcome DM
    try:
        embed = discord.Embed(
            title="👋 Welcome to NEXPLAY ORG!",
            description=(
                f"Hey **{member.display_name}**! Welcome to the NexPlay community! 🎮\n\n"
                "We host **Free Fire** & **Minecraft** tournaments with real prizes!\n\n"
                "**Get started:**\n"
                "• Read the rules in <#1525467982631796777>\n"
                "• Check out active tournaments\n"
                "• Type `/help` in any channel for commands\n\n"
                "See you in the arena! 🏆"
            ),
            color=0xFFD700
        )
        embed.set_footer(text="🇳🇵 NexPlay Tournament System")
        await member.send(embed=embed)
    except Exception:
        pass

# ─────────────────────────────────────────
# SUPPORT CHANNEL AUTO-RESPONSE
# ─────────────────────────────────────────
SUPPORT_CHANNEL_ID = 1525468119823421604  # #support-ticket

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Auto-respond in support channel
    if message.channel.id == SUPPORT_CHANNEL_ID and len(message.content) > 5:
        async with message.channel.typing():
            result = await call_backend("supportChat", {
                "guild_id": str(message.guild.id),
                "channel_id": str(message.channel.id),
                "user_discord_id": str(message.author.id),
                "user_username": message.author.display_name,
                "question": message.content
            })
            if result.get("error"):
                print(f"Support chat error: {result['error']}")

    await bot.process_commands(message)

# ─────────────────────────────────────────────────────────────────────────────
#  SLASH COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

# ── /tournament create ──────────────────────────────────────────────────────
@tree.command(
    name="create_tournament",
    description="🏆 Create and announce a new tournament",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    name="Tournament name",
    game="Game (Free Fire / Minecraft)",
    prize_pool="Prize pool (e.g. $500, NPR 5000)",
    date="Tournament date (e.g. 2026-07-20)",
    format="Tournament format",
    max_players="Max players (default: 16)",
    description="Short description (optional)"
)
@app_commands.choices(game=[
    app_commands.Choice(name="Free Fire", value="Free Fire"),
    app_commands.Choice(name="Minecraft", value="Minecraft"),
])
@app_commands.choices(format=[
    app_commands.Choice(name="Single Elimination", value="single_elim"),
    app_commands.Choice(name="Double Elimination", value="double_elim"),
    app_commands.Choice(name="Round Robin", value="round_robin"),
    app_commands.Choice(name="Swiss", value="swiss"),
])
async def create_tournament(
    interaction: discord.Interaction,
    name: str,
    game: str,
    prize_pool: str,
    date: str,
    format: str = "single_elim",
    max_players: int = 16,
    description: str = ""
):
    # Only admins / tournament hosts
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]
    member_roles = [r.name for r in interaction.user.roles]
    if not any(r in member_roles for r in allowed_roles):
        await interaction.response.send_message(embed=error_embed("You need the **Tournament Host** or **Admin** role to create tournaments!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    result = await call_backend("tournamentCommand", {
        "command": "create_tournament",
        "guild_id": str(interaction.guild.id),
        "tournament_name": name,
        "game": game,
        "prize_pool": prize_pool,
        "date": date,
        "format": format,
        "max_players": max_players,
        "description": description,
        "created_by": str(interaction.user.id)
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        embed = discord.Embed(
            title="🏆 Tournament Created!",
            description=f"**{name}** has been announced!\n\nPoster + registration post published to the tournament channels.",
            color=0xFFD700
        )
        if result.get("poster_url"):
            embed.set_image(url=result["poster_url"])
        embed.set_footer(text="🇳🇵 NexPlay Tournament System")
        await interaction.followup.send(embed=embed)

# ── /register ───────────────────────────────────────────────────────────────
@tree.command(
    name="register",
    description="📋 Register yourself for the active tournament",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    tournament_name="Name of the tournament to join",
    ingame_name="Your in-game name/ID"
)
async def register(interaction: discord.Interaction, tournament_name: str, ingame_name: str):
    await interaction.response.defer(thinking=True, ephemeral=True)

    result = await call_backend("tournamentCommand", {
        "command": "register_player",
        "guild_id": str(interaction.guild.id),
        "tournament_name": tournament_name,
        "player_discord_id": str(interaction.user.id),
        "player_username": ingame_name,
        "player_display_name": interaction.user.display_name
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]), ephemeral=True)
    else:
        embed = discord.Embed(
            title="✅ Registration Successful!",
            description=f"You're registered for **{tournament_name}**!\n\n🎮 **In-Game Name:** {ingame_name}\n\nGood luck! 🏆",
            color=0x00FF7F
        )
        embed.set_footer(text="🇳🇵 NexPlay Tournament System")
        await interaction.followup.send(embed=embed, ephemeral=True)

# ── /close_registration ──────────────────────────────────────────────────────
@tree.command(
    name="close_registration",
    description="🔒 Close registration for a tournament",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(tournament_name="Name of the tournament")
async def close_registration(interaction: discord.Interaction, tournament_name: str):
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]
    if not any(r.name in allowed_roles for r in interaction.user.roles):
        await interaction.response.send_message(embed=error_embed("No permission!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    result = await call_backend("tournamentCommand", {
        "command": "close_registration",
        "guild_id": str(interaction.guild.id),
        "tournament_name": tournament_name
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        await interaction.followup.send(embed=success_embed(f"Registration closed for **{tournament_name}**! `{result.get('player_count', 0)}` players confirmed."))

# ── /generate_groups ─────────────────────────────────────────────────────────
@tree.command(
    name="generate_groups",
    description="🎯 Generate and reveal tournament groups",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(tournament_name="Name of the tournament")
async def generate_groups(interaction: discord.Interaction, tournament_name: str):
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]
    if not any(r.name in allowed_roles for r in interaction.user.roles):
        await interaction.response.send_message(embed=error_embed("No permission!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    result = await call_backend("tournamentCommand", {
        "command": "generate_groups",
        "guild_id": str(interaction.guild.id),
        "tournament_name": tournament_name
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        groups = result.get("groups", [])
        embed = discord.Embed(
            title="🎯 Groups Generated!",
            description=f"**{len(groups)} groups** created and posted to brackets channel!\n\n" +
                        "\n".join([f"**Group {g['label']}:** {', '.join(g['players'])}" for g in groups]),
            color=0x9B59B6
        )
        if result.get("group_image_url"):
            embed.set_image(url=result["group_image_url"])
        embed.set_footer(text="🇳🇵 NexPlay Tournament System")
        await interaction.followup.send(embed=embed)

# ── /post_schedule ────────────────────────────────────────────────────────────
@tree.command(
    name="post_schedule",
    description="📅 Post the match schedule",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    tournament_name="Name of the tournament",
    schedule_text="Schedule details (e.g. Round 1: GroupA 5PM, GroupB 6PM...)"
)
async def post_schedule(interaction: discord.Interaction, tournament_name: str, schedule_text: str):
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]
    if not any(r.name in allowed_roles for r in interaction.user.roles):
        await interaction.response.send_message(embed=error_embed("No permission!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    result = await call_backend("tournamentCommand", {
        "command": "post_schedule",
        "guild_id": str(interaction.guild.id),
        "tournament_name": tournament_name,
        "schedule_text": schedule_text
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        embed = discord.Embed(
            title="📅 Schedule Posted!",
            description=f"Match schedule for **{tournament_name}** is live in the brackets channel!",
            color=0x1E90FF
        )
        if result.get("schedule_image_url"):
            embed.set_image(url=result["schedule_image_url"])
        await interaction.followup.send(embed=embed)

# ── /post_result ──────────────────────────────────────────────────────────────
@tree.command(
    name="post_result",
    description="⚔️ Post a match result",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    tournament_name="Tournament name",
    player1="Player 1 username",
    player2="Player 2 username",
    winner="Winner's username",
    score="Score (e.g. 3-1)",
    round_number="Round number"
)
async def post_result(
    interaction: discord.Interaction,
    tournament_name: str,
    player1: str,
    player2: str,
    winner: str,
    score: str = "N/A",
    round_number: int = 1
):
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]
    if not any(r.name in allowed_roles for r in interaction.user.roles):
        await interaction.response.send_message(embed=error_embed("No permission!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    result = await call_backend("tournamentCommand", {
        "command": "post_result",
        "guild_id": str(interaction.guild.id),
        "tournament_name": tournament_name,
        "player1": player1,
        "player2": player2,
        "winner": winner,
        "score": score,
        "round_number": round_number
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        embed = discord.Embed(
            title="⚔️ Result Posted!",
            description=f"**{winner}** wins against **{player1 if winner == player2 else player2}**!\nScore: `{score}`",
            color=0x1E90FF
        )
        if result.get("results_image_url"):
            embed.set_image(url=result["results_image_url"])
        await interaction.followup.send(embed=embed)

# ── /complete_tournament ──────────────────────────────────────────────────────
@tree.command(
    name="complete_tournament",
    description="🏆 Complete a tournament and crown the champion",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    tournament_name="Tournament name",
    winner="Champion's username",
    second="2nd place (optional)",
    third="3rd place (optional)"
)
async def complete_tournament(
    interaction: discord.Interaction,
    tournament_name: str,
    winner: str,
    second: str = "",
    third: str = ""
):
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin"]
    if not any(r.name in allowed_roles for r in interaction.user.roles):
        await interaction.response.send_message(embed=error_embed("No permission!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    result = await call_backend("tournamentCommand", {
        "command": "complete_tournament",
        "guild_id": str(interaction.guild.id),
        "tournament_name": tournament_name,
        "winner": winner,
        "second": second,
        "third": third
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        embed = discord.Embed(
            title="🎊 Tournament Complete!",
            description=f"**{winner}** is the champion of **{tournament_name}**! 🏆\n\nChampion card posted to Hall of Champions!",
            color=0xFFD700
        )
        if result.get("champion_image_url"):
            embed.set_image(url=result["champion_image_url"])
        await interaction.followup.send(embed=embed)

# ── /tournament_status ────────────────────────────────────────────────────────
@tree.command(
    name="tournament_status",
    description="📊 View all tournaments in this server",
    guild=discord.Object(id=GUILD_ID)
)
async def tournament_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    result = await call_backend("tournamentCommand", {
        "command": "get_status",
        "guild_id": str(interaction.guild.id)
    })

    tournaments = result.get("tournaments", [])
    if not tournaments:
        await interaction.followup.send(embed=error_embed("No tournaments found for this server."), ephemeral=True)
        return

    embed = discord.Embed(title="📊 Tournament Status", color=0x9B59B6)
    status_emoji = {
        "draft": "📝", "registration_open": "🟢", "registration_closed": "🔒",
        "groups_generated": "🎯", "scheduled": "📅", "in_progress": "🔥",
        "completed": "✅", "cancelled": "❌"
    }
    for t in tournaments[-10:]:
        emoji = status_emoji.get(t.get("status", ""), "❓")
        embed.add_field(
            name=f"{emoji} {t.get('name', 'Unknown')}",
            value=f"Game: {t.get('game', '?')} | Players: {t.get('registered_count', 0)}/{t.get('max_players', '?')} | Status: `{t.get('status', '?')}`",
            inline=False
        )
    embed.set_footer(text="🇳🇵 NexPlay Tournament System")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /announce ─────────────────────────────────────────────────────────────────
@tree.command(
    name="announce",
    description="📣 Post a custom announcement",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(message="Announcement message")
async def announce(interaction: discord.Interaction, message: str):
    allowed_roles = ["👑 NexPlay Owner", "⚔️ Tournament Host", "🛡️ Admin", "🔧 Moderator"]
    if not any(r.name in allowed_roles for r in interaction.user.roles):
        await interaction.response.send_message(embed=error_embed("No permission!"), ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    result = await call_backend("tournamentCommand", {
        "command": "announce",
        "guild_id": str(interaction.guild.id),
        "message": message
    })

    if result.get("error"):
        await interaction.followup.send(embed=error_embed(result["error"]))
    else:
        await interaction.followup.send(embed=success_embed("Announcement posted! 📣"))

# ── /help ─────────────────────────────────────────────────────────────────────
@tree.command(
    name="help",
    description="❓ Show all NexPlay bot commands",
    guild=discord.Object(id=GUILD_ID)
)
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🇳🇵 NexPlay Tournament Bot — Commands",
        description="Here's everything this bot can do:",
        color=0xFFD700
    )
    embed.add_field(
        name="🏆 Tournament Management (Staff Only)",
        value=(
            "`/create_tournament` — Create & announce a tournament\n"
            "`/close_registration` — Close player signups\n"
            "`/generate_groups` — Shuffle & reveal groups\n"
            "`/post_schedule` — Post the match schedule\n"
            "`/post_result` — Post a match result\n"
            "`/complete_tournament` — Crown the champion\n"
            "`/announce` — Post a custom announcement"
        ),
        inline=False
    )
    embed.add_field(
        name="🎮 Player Commands",
        value=(
            "`/register` — Register for a tournament\n"
            "`/tournament_status` — View active tournaments\n"
            "`/help` — Show this menu"
        ),
        inline=False
    )
    embed.add_field(
        name="🤖 Auto Features",
        value=(
            "• **Support AI** — Auto-answers questions in #support-ticket\n"
            "• **Auto-role** — Members get 🎮 NexPlay Member on join\n"
            "• **Welcome DM** — New members get a welcome message\n"
            "• **AI Images** — Every tournament stage gets an auto-generated image"
        ),
        inline=False
    )
    embed.set_footer(text="🇳🇵 NexPlay Tournament System | Powered by NexPlay")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ─────────────────────────────────────────
# RUN
# ─────────────────────────────────────────
bot.run(BOT_TOKEN)
