import os
import json
import logging
import discord
from discord import app_commands
from discord.ext import commands

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('tournament_bot')

# Load Paths and Configuration
DATA_PATH = "/tmp/tournaments.json"
ROLES_PATH = "/tmp/nexplay_roles.json"
CHANNELS_PATH = "/tmp/nexplay_channels.json"

# Load Bot configuration from environment variables
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID_STR = os.environ.get("DISCORD_GUILD_ID")

if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID_STR:
    raise ValueError("Missing DISCORD_BOT_TOKEN or DISCORD_GUILD_ID environment variables.")

DISCORD_GUILD_ID = int(DISCORD_GUILD_ID_STR)
MY_GUILD = discord.Object(id=DISCORD_GUILD_ID)

# Helpers for loading JSON metadata
def load_roles():
    try:
        with open(ROLES_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load roles: {e}")
        return {}

def load_channels():
    try:
        with open(CHANNELS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load channels: {e}")
        return {}

def load_tournaments():
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load tournament data: {e}")
            return {}
    return {}

def save_tournaments(data):
    try:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save tournament data: {e}")

# Discord Bot subclass
class TournamentBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.reactions = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Syncing slash commands for fast availability in NexPlay Guild
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)
        logger.info(f"Slash commands synced to guild {DISCORD_GUILD_ID}")

bot = TournamentBot()

# Fetch metadata IDs safely
roles_db = load_roles()
channels_db = load_channels()

def get_role_id(role_name):
    return int(roles_db[role_name]) if role_name in roles_db else None

def get_channel_id(channel_name):
    return int(channels_db[channel_name]) if channel_name in channels_db else None

# Group definition for /tournament
class TournamentGroup(app_commands.Group, name="tournament", description="NexPlay Tournament administration commands"):
    pass

tournament_group = TournamentGroup()
bot.tree.add_command(tournament_group)

# 1. /tournament create
@tournament_group.command(name="create", description="Creates a tournament and dedicated channel")
@app_commands.describe(
    name="Name of the tournament",
    game="Game for the tournament (e.g., Free Fire, Minecraft)",
    date="Tournament match date and time",
    prize="Tournament prize pool"
)
async def tournament_create(interaction: discord.Interaction, name: str, game: str, date: str, prize: str):
    await interaction.response.defer()
    guild = interaction.guild
    
    # Check if tournament already exists
    tournaments = load_tournaments()
    if name in tournaments:
        await interaction.followup.send(f"❌ Tournament named `{name}` already exists!", ephemeral=True)
        return

    # Create Dedicated Text Channel under 'TOURNAMENTS' category or general if category not found
    category_name = "TOURNAMENTS"
    category = discord.utils.get(guild.categories, name=category_name)
    if not category:
        category = await guild.create_category(category_name)
    
    # Formulate channel name: lowercase, letters/numbers/hyphens only
    channel_safe_name = "".join(c if c.isalnum() or c == "-" else "" for c in name.replace(" ", "-")).lower()
    tourney_channel = await guild.create_text_channel(name=f"🏆│{channel_safe_name}", category=category)

    # Post full details Embed to #tourney-announcements
    announcements_channel_id = get_channel_id("📢│tourney-announcements")
    announcements_channel = guild.get_channel(announcements_channel_id) if announcements_channel_id else None
    
    embed = discord.Embed(
        title=f"🏆 NEW TOURNAMENT: {name}",
        description=f"NexPlay is proud to announce the upcoming **{game}** tournament! Get ready to compete!",
        color=discord.Color.gold()
    )
    embed.add_field(name="🎮 Game", value=game, inline=True)
    embed.add_field(name="📅 Date & Time", value=date, inline=True)
    embed.add_field(name="🎁 Prize Pool", value=prize, inline=True)
    embed.add_field(name="📈 Status", value="🟢 Registration Open", inline=True)
    embed.add_field(
        name="📍 Roadmap", 
        value="👉 **Registration Open** (React with ✅ to join)\n👉 **Brackets** (Matchups Auto-Generated)\n👉 **Match Day** (Fight for Glory)\n👉 **Results** (Hall of Champions)", 
        inline=False
    )
    embed.set_footer(text="🇳🇵 NexPlay Org")

    announcement_msg = None
    if announcements_channel:
        announcement_msg = await announcements_channel.send(embed=embed)
    
    # Save Tournament Data
    tournaments[name] = {
        "name": name,
        "game": game,
        "date": date,
        "prize": prize,
        "status": "open",
        "participants": [],
        "channel_id": tourney_channel.id,
        "message_id": announcement_msg.id if announcement_msg else None
    }
    save_tournaments(tournaments)

    await interaction.followup.send(f"✅ Tournament **{name}** created successfully! Dedicated channel: {tourney_channel.mention}")

# 2. /tournament register
@tournament_group.command(name="register", description="Opens registration in the current channel")
async def tournament_register(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📝 Tournament Registration",
        description="To register for the current tournament, react with ✅ to this message!\n\n**Note:** Ensure your DMs are open so the bot can send you confirmation details.",
        color=discord.Color.blue()
    )
    embed.set_footer(text="🇳🇵 NexPlay Org")
    
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    await message.add_reaction("✅")

# Helper to find tournament by current text channel ID
def get_tournament_by_channel(channel_id):
    tournaments = load_tournaments()
    for name, data in tournaments.items():
        if data.get("channel_id") == channel_id:
            return data
    return None

# 3. /tournament close_registration
@tournament_group.command(name="close_registration", description="Closes registration and lists participants")
@app_commands.describe(tournament_name="Name of the tournament")
async def tournament_close_registration(interaction: discord.Interaction, tournament_name: str):
    await interaction.response.defer()
    tournaments = load_tournaments()
    
    if tournament_name not in tournaments:
        await interaction.followup.send(f"❌ Tournament `{tournament_name}` not found.", ephemeral=True)
        return

    tourny = tournaments[tournament_name]
    tourny["status"] = "closed"
    save_tournaments(tournaments)

    # Format participants list
    participants_list = tourny.get("participants", [])
    if participants_list:
        mentions_list = [f"<@{user_id}>" for user_id in participants_list]
        participants_str = "\n".join(f"• {m}" for m in mentions_list)
    else:
        participants_str = "No participants registered yet."

    embed = discord.Embed(
        title=f"🔒 Registration Closed: {tournament_name}",
        description=f"Registration has been officially closed! Here are the battle-ready participants:\n\n{participants_str}",
        color=discord.Color.red()
    )
    embed.set_footer(text="🇳🇵 NexPlay Org")

    # Update announcement if available
    guild = interaction.guild
    announcements_channel_id = get_channel_id("📢│tourney-announcements")
    if announcements_channel_id:
        announcements_channel = guild.get_channel(announcements_channel_id)
        if announcements_channel and tourny.get("message_id"):
            try:
                msg = await announcements_channel.fetch_message(tourny["message_id"])
                ann_embed = msg.embeds[0]
                ann_embed.set_field_at(3, name="📈 Status", value="🔴 Registration Closed", inline=True)
                await msg.edit(embed=ann_embed)
            except Exception as e:
                logger.error(f"Failed to update original announcement message: {e}")

    await interaction.followup.send(embed=embed)

# 4. /tournament bracket
@tournament_group.command(name="bracket", description="Generates and displays brackets for the tournament")
@app_commands.describe(tournament_name="Name of the tournament")
async def tournament_bracket(interaction: discord.Interaction, tournament_name: str):
    await interaction.response.defer()
    tournaments = load_tournaments()
    
    if tournament_name not in tournaments:
        await interaction.followup.send(f"❌ Tournament `{tournament_name}` not found.", ephemeral=True)
        return

    tourny = tournaments[tournament_name]
    participants = tourny.get("participants", [])

    if not participants:
        await interaction.followup.send("❌ Cannot generate brackets without participants!", ephemeral=True)
        return

    # Simple Matchup Generation
    matchups = []
    import random
    temp_participants = list(participants)
    random.shuffle(temp_participants)

    match_num = 1
    while len(temp_participants) >= 2:
        p1 = temp_participants.pop(0)
        p2 = temp_participants.pop(0)
        matchups.append(f"**Match {match_num}:** <@{p1}> 🆚 <@{p2}>")
        match_num += 1

    if temp_participants:
        bye_player = temp_participants.pop(0)
        matchups.append(f"**Match {match_num}:** <@{bye_player}> (BYE - Advances directly)")

    bracket_str = "\n".join(matchups)

    embed = discord.Embed(
        title=f"📊 Tournament Bracket: {tournament_name}",
        description=f"Here are the tournament matchups generated for today:\n\n{bracket_str}",
        color=discord.Color.purple()
    )
    embed.set_footer(text="🇳🇵 NexPlay Org")

    # Post in brackets channel if possible
    brackets_channel_id = get_channel_id("📊│brackets-results")
    brackets_channel = interaction.guild.get_channel(brackets_channel_id) if brackets_channel_id else None
    
    if brackets_channel:
        await brackets_channel.send(embed=embed)
        await interaction.followup.send(f"✅ Bracket generated and posted to {brackets_channel.mention}!")
    else:
        await interaction.followup.send(embed=embed)

# 5. /tournament result
@tournament_group.command(name="result", description="Announces results, updates roles and records")
@app_commands.describe(
    tournament_name="Name of the tournament",
    winner="Winner member",
    second="Second place member",
    third="Third place member"
)
async def tournament_result(
    interaction: discord.Interaction, 
    tournament_name: str, 
    winner: discord.Member, 
    second: discord.Member, 
    third: discord.Member
):
    await interaction.response.defer()
    tournaments = load_tournaments()
    
    if tournament_name not in tournaments:
        await interaction.followup.send(f"❌ Tournament `{tournament_name}` not found.", ephemeral=True)
        return

    tourny = tournaments[tournament_name]
    tourny["status"] = "completed"
    save_tournaments(tournaments)

    # Award Champion Role
    champion_role_id = get_role_id("🏆 Champion")
    if champion_role_id:
        champion_role = interaction.guild.get_role(champion_role_id)
        if champion_role:
            try:
                await winner.add_roles(champion_role)
            except Exception as e:
                logger.error(f"Failed to award Champion role: {e}")

    # Build Results Embed
    embed = discord.Embed(
        title=f"🏁 Tournament Results: {tournament_name}",
        description=f"Congratulations to the winners of the **{tourny.get('game', 'Game')}** tournament! You played wonderfully!",
        color=discord.Color.gold()
    )
    embed.add_field(name="🥇 1st Place", value=f"{winner.mention} - NexPlay Champion!", inline=False)
    embed.add_field(name="🥈 2nd Place", value=f"{second.mention}", inline=False)
    embed.add_field(name="🥉 3rd Place", value=f"{third.mention}", inline=False)
    embed.set_footer(text="🇳🇵 NexPlay Org")

    # Send to #brackets-results and #hall-of-champions
    brackets_channel_id = get_channel_id("📊│brackets-results")
    hall_channel_id = get_channel_id("🏆│hall-of-champions")

    guild = interaction.guild
    b_chan = guild.get_channel(brackets_channel_id) if brackets_channel_id else None
    h_chan = guild.get_channel(hall_channel_id) if hall_channel_id else None

    if b_chan:
        await b_chan.send(embed=embed)
    if h_chan:
        await h_chan.send(embed=embed)

    await interaction.followup.send(f"✅ Results posted for `{tournament_name}`! Champion role granted to {winner.mention}.")

# 6. /tournament schedule
@tournament_group.command(name="schedule", description="Displays match schedule for the tournament")
@app_commands.describe(tournament_name="Name of the tournament")
async def tournament_schedule(interaction: discord.Interaction, tournament_name: str):
    tournaments = load_tournaments()
    if tournament_name not in tournaments:
        await interaction.response.send_message(f"❌ Tournament `{tournament_name}` not found.", ephemeral=True)
        return

    tourny = tournaments[tournament_name]
    embed = discord.Embed(
        title=f"📅 Match Schedule: {tournament_name}",
        description=f"Prepare your squads! Here is the official roadmap of events on **{tourny.get('date')}**:",
        color=discord.Color.teal()
    )
    embed.add_field(name="⏱️ Warm-up & Roll-call", value="15 minutes before Match Start", inline=False)
    embed.add_field(name="🔥 Round 1 (Qualifiers)", value="Match Day Start Time", inline=False)
    embed.add_field(name="⚔️ Round 2 (Semi-Finals)", value="45 minutes after start", inline=False)
    embed.add_field(name="🏆 Grand Finals", value="90 minutes after start", inline=False)
    embed.set_footer(text="🇳🇵 NexPlay Org")

    await interaction.response.send_message(embed=embed)

# 7. /tournament announce
@tournament_group.command(name="announce", description="Sends a notification embed and pings participants")
@app_commands.describe(message="Announcement text message")
async def tournament_announce(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    guild = interaction.guild

    # Load Role ID for Tournament Participant
    role_id = get_role_id("📋 Tournament Participant")
    role_mention = f"<@&{role_id}>" if role_id else "@Tournament Participant"

    embed = discord.Embed(
        title="📢 NexPlay Tournament Announcement",
        description=message,
        color=discord.Color.orange()
    )
    embed.set_footer(text="🇳🇵 NexPlay Org")

    announcements_channel_id = get_channel_id("📢│tourney-announcements")
    announcements_channel = guild.get_channel(announcements_channel_id) if announcements_channel_id else None

    if announcements_channel:
        await announcements_channel.send(content=role_mention, embed=embed)
        await interaction.followup.send(f"✅ Announcement sent to {announcements_channel.mention}!")
    else:
        await interaction.followup.send(embed=embed)

# 8. /tournament list
@tournament_group.command(name="list", description="Lists all active and completed tournaments")
async def tournament_list(interaction: discord.Interaction):
    tournaments = load_tournaments()
    if not tournaments:
        await interaction.response.send_message("ℹ️ No tournaments recorded yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏆 NexPlay Tournament Board",
        description="Here are the active and past tournaments hosted by NexPlay:",
        color=discord.Color.blue()
    )

    for name, data in tournaments.items():
        status_emoji = "🟢" if data.get("status") == "open" else ("🔴" if data.get("status") == "closed" else "🏁")
        status_text = data.get("status", "unknown").capitalize()
        participants_count = len(data.get("participants", []))
        
        details = (
            f"**Game:** {data.get('game')}\n"
            f"**Date:** {data.get('date')}\n"
            f"**Prize:** {data.get('prize')}\n"
            f"**Participants:** {participants_count}\n"
            f"**Status:** {status_emoji} {status_text}"
        )
        embed.add_field(name=name, value=details, inline=False)

    embed.set_footer(text="🇳🇵 NexPlay Org")
    await interaction.response.send_message(embed=embed)

# 9. /tournament delete
@tournament_group.command(name="delete", description="Deletes/Archives a tournament record and its dedicated channel")
@app_commands.describe(tournament_name="Name of the tournament")
async def tournament_delete(interaction: discord.Interaction, tournament_name: str):
    await interaction.response.defer()
    tournaments = load_tournaments()

    if tournament_name not in tournaments:
        await interaction.followup.send(f"❌ Tournament `{tournament_name}` not found.", ephemeral=True)
        return

    tourny = tournaments[tournament_name]
    guild = interaction.guild

    # Attempt to delete dedicated text channel
    chan_id = tourny.get("channel_id")
    if chan_id:
        chan = guild.get_channel(chan_id)
        if chan:
            try:
                await chan.delete()
            except Exception as e:
                logger.error(f"Failed to delete channel {chan_id}: {e}")

    # Remove from DB
    del tournaments[tournament_name]
    save_tournaments(tournaments)

    await interaction.followup.send(f"✅ Tournament `{tournament_name}` and its channel have been deleted/archived successfully.")


# ================= AUTO-FEATURES & EVENTS =================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    print(f"Connected to Guild ID: {DISCORD_GUILD_ID}")

@bot.event
async def on_member_join(member):
    # Auto assign NexPlay Member role
    role_id = get_role_id("🎮 NexPlay Member")
    if role_id:
        role = member.guild.get_role(role_id)
        if role:
            try:
                await member.add_roles(role)
                logger.info(f"Assigned NexPlay Member role to {member.name}")
            except Exception as e:
                logger.error(f"Failed to auto-assign member role: {e}")

    # Send Welcome DM
    try:
        welcome_embed = discord.Embed(
            title="🇳🇵 Welcome to NexPlay Community!",
            description=f"Namaste {member.name}! Welcome to Nepal's premier gaming community! Feel free to hang out and check out our regular tournaments.",
            color=discord.Color.green()
        )
        welcome_embed.set_footer(text="🇳🇵 NexPlay Org")
        await member.send(embed=welcome_embed)
    except Exception as e:
        logger.error(f"Failed to send welcome DM to {member.name}: {e}")

@bot.event
async def on_raw_reaction_add(payload):
    # Ignore reactions from the bot itself
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return

    # 1. Reaction roles in #self-roles
    self_roles_channel_id = get_channel_id("🎭│self-roles")
    if payload.channel_id == self_roles_channel_id:
        role_name = None
        if str(payload.emoji) == "🔥":
            role_name = "🔥 Free Fire Player"
        elif str(payload.emoji) == "⛏️":
            role_name = "⛏️ Minecraft Player"

        if role_name:
            role_id = get_role_id(role_name)
            if role_id:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role)
                        logger.info(f"Reaction Role: Added {role_name} to {member.name}")
                    except Exception as e:
                        logger.error(f"Failed to assign reaction role {role_name}: {e}")

    # 2. Reaction ✅ in registration embed
    if str(payload.emoji) == "✅":
        # Find which tournament this message corresponds to or check if it's the current channel's registration embed
        tournaments = load_tournaments()
        matched_tourny_name = None
        
        # Check if the registration message matches registered tournament channel or specific registration message
        for name, data in tournaments.items():
            if data.get("channel_id") == payload.channel_id or data.get("message_id") == payload.message_id:
                matched_tourny_name = name
                break
        
        # Fallback: if they react in the general registration channel, find the first open tournament
        if not matched_tourny_name:
            reg_channel_id = get_channel_id("📝│tourney-registration")
            if payload.channel_id == reg_channel_id:
                # Find first open tournament
                for name, data in tournaments.items():
                    if data.get("status") == "open":
                        matched_tourny_name = name
                        break

        if matched_tourny_name:
            tourny = tournaments[matched_tourny_name]
            if tourny.get("status") == "open":
                if payload.user_id not in tourny["participants"]:
                    tourny["participants"].append(payload.user_id)
                    save_tournaments(tournaments)

                    # Assign Tournament Participant role
                    part_role_id = get_role_id("📋 Tournament Participant")
                    if part_role_id:
                        role = guild.get_role(part_role_id)
                        if role:
                            try:
                                await member.add_roles(role)
                            except Exception as e:
                                logger.error(f"Failed to assign participant role: {e}")

                    # Send confirmation DM
                    try:
                        dm_embed = discord.Embed(
                            title="✅ Registration Confirmed!",
                            description=f"Namaste! You have successfully registered for **{matched_tourny_name}** ({tourny.get('game')})!\n\n📅 **Date:** {tourny.get('date')}\n🎁 **Prize:** {tourny.get('prize')}",
                            color=discord.Color.green()
                        )
                        dm_embed.set_footer(text="🇳🇵 NexPlay Org")
                        await member.send(embed=dm_embed)
                    except Exception as e:
                        logger.warning(f"Could not send DM to {member.name}: {e}")


# Run the bot
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
