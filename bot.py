import os
import json
import sqlite3
import random
import logging
import shutil
from datetime import datetime, UTC, timedelta
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks

CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
DISCORD_SERVER_ID = int(os.getenv("DISCORD_SERVER_ID"))
DISCORD_APP_ID = int(os.getenv("DISCORD_APP_ID"))

async def send_startup_message(bot):
    # Try cache first, then API fetch if needed
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    await channel.send("Bot is now online in this channel!")

# ---------- Tasks ----------
# The update_countdown task runs every X minutes to check if there is an active entry deadline and updates a countdown message in the configured channel with the time remaining until the deadline. If the deadline has passed, it updates the message to indicate that entries are closed. This allows participants to see how much time they have left to enter the contest and creates a sense of urgency as the deadline approaches.
@tasks.loop(minutes=60)  # Update every 60 minutes
async def update_countdown():
    """Background task to update countdown message"""
    try:
        deadline = get_entry_deadline()
        if not deadline:
            return  # No deadline set
        
        # Prefer configured countdown channel; fall back to default CHANNEL_ID
        channel_id = get_countdown_channel_id() or CHANNEL_ID
        message_id = get_countdown_message_id()    
       
        if not channel_id:
            return  # No channel configured
        
        channel = bot.get_channel(channel_id)
        if not channel:
            return
        
        deadline_dt = datetime.fromisoformat(deadline)
        now = datetime.now(UTC)
        
        # If deadline passed, stop countdown
        if now >= deadline_dt:
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                    embed = discord.Embed(
                        title="⏰ Contest Deadline Reached!",
                        description="Entry deadline has passed. No more entries are being accepted.",
                        color=0xFF0000,  # Red
                    )
                    await message.edit(embed=embed)
                    set_countdown_message_id(None)  # Clear message ID
                except discord.HTTPException as e:
                    log.warning("Failed to update final countdown message %s: %s", message_id, e)
            return
        
        # Calculate time remaining
        time_remaining = deadline_dt - now
        days = time_remaining.days
        hours, remainder = divmod(time_remaining.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        # Create countdown embed
        contest_id = get_current_contest_id()
        embed = discord.Embed(
            title=f"⏰ Contest #{contest_id} Countdown",
            description=f"Time remaining until entry deadline:",
            color=0x00FFAA,
        )
        
        countdown_text = []
        if days > 0:
            countdown_text.append(f"**{days}** day{'s' if days != 1 else ''}")
        if hours > 0 or days > 0:
            countdown_text.append(f"**{hours}** hour{'s' if hours != 1 else ''}")
        countdown_text.append(f"**{minutes}** minute{'s' if minutes != 1 else ''}")
        
        embed.add_field(
            name="Time Remaining",
            value=" ".join(countdown_text),
            inline=False,
        )
        
        discord_timestamp = int(deadline_dt.timestamp())
        
        embed.add_field(
            name="Deadline",
            value=f"<t:{discord_timestamp}:F> (<t:{discord_timestamp}:R>)",
            inline=False,
        )
        
        embed.set_footer(text="Use /enter to submit your guess!")
        
        # Update or create message
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
            except discord.NotFound:
                # Message was deleted, create new one
                message = await channel.send(embed=embed)
                set_countdown_message_id(message.id)
        else:
            # Create new countdown message
            message = await channel.send(embed=embed)
            set_countdown_message_id(message.id)
            
    except Exception as e:
        log.exception("Error in countdown update: %s", e)

# ---------- Logging setup ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("fob-contest-bot")

print("Starting bot.py...")
log.info("Bot starting up")

# ---------- SQLite setup ----------

DB_PATH = "contest.db"

# Note: These helper functions are decorated with @with_db to log any SQLite errors that occur within them.
# normalize_system_name is used to ensure that system name comparisons are consistent regardless of user input formatting (e.g. extra spaces, case differences).
def normalize_system_name(name: str) -> str:
    """Normalize EVE system names so comparisons are consistent."""
    cleaned = " ".join(name.strip().split())
    return cleaned.title()

# ---------- Allowed FOB systems ----------

# Discord autocomplete can return at most 25 choices.
# See: https://discord.com/developers/docs/interactions/application-commands#autocomplete
ALLOWED_FOB_SYSTEMS_RAW = [
    "Aivonen",
    "Akidagi",
    "Aldranette",
    "Alparena",
    "Asakai",
    "Athounon",
    "Aubenall",
    "Brarel",
    "Deven",
    "Eha",
    "Enaluri",
    "Esesier",
    "Evaulon",
    "Frarie",
    "Harroule",
    "Hevrice",
    "Heydieles",
    "Hykanima",
    "Iges",
    "Immuri",
    "Ikoskio",
    "Intaki",
    "Iralaja",
    "Jovainnon",
    "Kedama",
    "Kehjari",
    "Kinakka",
    "Luminaire",
    "Mantenault",
    "Martoh",
    "Melmaniel",
    "Mercomesier",
    "Murethand",
    "Mushikegi",
    "Nikkishina",
    "Nisuwa",
    "Notoras",
    "Ocix",
    "Odamia",
    "Oicx",
    "Oinasiken",
    "Okagaiken",
    "Old Man Star",
    "Olletta",
    "Ostingele",
    "Oto",
    "Prism",
    "Pynekastoh",
    "Raihbaka",
    "Renarelle",
    "Reschard",
    "Sarenemi",
    "Sujarento",
    "Tama",
    "Tannolen",
    "Vaaralen",
    "Vey",
    "Vlillirier",
]

# Normalize once so lookups are consistent
ALLOWED_FOB_SYSTEMS = {normalize_system_name(name) for name in ALLOWED_FOB_SYSTEMS_RAW}

# is_allowed_fob_system checks if a given system name (after normalization) is in the set of allowed FOB systems, which is used to validate user guesses and the final FOB system set by admins.
def is_allowed_fob_system(name: str) -> bool:
    """Return True if the normalized system name is in the allowed FOB list."""
    normalized = normalize_system_name(name)
    return normalized in ALLOWED_FOB_SYSTEMS
# Generates autocomplete choices for system names based on user input. If the user hasn't typed anything yet, it shows a random sample of allowed systems. As the user types, it filters the list to show only matching systems, making it easier for users to find and select valid system names for their guesses.
async def system_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """
    Autocomplete handler for system names.
    Shows a random sample when empty, and filters as the user types.
    """
    # User's partial input (case-insensitive)
    typed = current.strip()

    # Work with display names
    all_names = list(ALLOWED_FOB_SYSTEMS_RAW)

    if typed:
        # Filter by case-insensitive substring, then sort for stable UX
        lowered = typed.lower()
        filtered = [name for name in all_names if lowered in name.lower()]
        filtered.sort()
    else:
        # No input yet → randomize the list
        random.shuffle(all_names)
        filtered = all_names

    # Discord hard limit: max 25 choices
    filtered = filtered[:25]

    return [app_commands.Choice(name=name, value=name) for name in filtered]

def with_db(fn):
    """Decorator to log SQLite errors for DB helper functions."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except sqlite3.Error as e:
            log.exception("SQLite error in %s: %s", fn.__name__, e)
            raise
    return wrapper

# WINNER_PICKED_KEY is used to track whether a winner has already been picked for the current contest, 
# which can prevent admins from accidentally reopening a contest or picking multiple winners for the same contest.
WINNER_PICKED_KEY = "winner_picked"

@with_db
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Per‑contest entries
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS contest_entries (
            contest_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            system_name TEXT NOT NULL,
            entered_at  TEXT NOT NULL,
            PRIMARY KEY (contest_id, user_id),
            UNIQUE (contest_id, system_name)
        )
        """
    )

    # Settings key/value store
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    # Contest tracking
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS contests (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at      TEXT NOT NULL,
            winner_user_id INTEGER,
            winner_system  TEXT
        )
        """
    )

    # Ensure there is a current contest row and setting
    cur.execute("SELECT value FROM settings WHERE key = ?", ("current_contest_id",))
    row = cur.fetchone()

    if row is None:
        # No current_contest_id yet: create initial contest row
        opened_at = datetime.now(UTC).isoformat()
        cur.execute("INSERT INTO contests(opened_at) VALUES(?)", (opened_at,))
        contest_id = cur.lastrowid

        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            ("current_contest_id", str(contest_id)),
        )
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            ("contest_open", "1"),
        )
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            (WINNER_PICKED_KEY, "0"),
        )

    conn.commit()
    conn.close()

# Create tables if they don't exist and bootstrap current_contest_id
init_db()

# ---------- Helper functions ----------

@with_db
# get_current_contest_id is used to determine which contest the user entries belong to, and to track the current contest in the settings table.
def get_current_contest_id() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM settings WHERE key = ?",
        ("current_contest_id",),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return 1
    return int(row[0])


@with_db
# get_contest_open_date is used to show when the current contest was opened, which can be helpful for admins to track contest history and timing.
def get_contest_open_date(contest_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT opened_at FROM contests WHERE id = ?",
        (contest_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


@with_db
# get_prizes_text and set_prizes_text are used to store and retrieve the current prize description for the contest, which can be displayed to users with the /prizes command.
def get_prizes_text() -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", ("prizes_text",))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


@with_db
# set_prizes_text allows admins to update the prize description for the contest, which can be important for keeping the contest information current and engaging for participants.
def set_prizes_text(text: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("prizes_text", text),
    )
    conn.commit()
    conn.close()

def get_prizes_list() -> list[str]:
    """
    Return the current prizes as an ordered list of strings.
    Stored as JSON in the 'prizes_text' setting for flexibility.
    Falls back to treating old plain-text data as a single-element list.
    """
    raw = get_prizes_text()
    if not raw:
        return []

    # First try: interpret as JSON list
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        # Not valid JSON; fall through to treat as legacy text
        pass

    # Legacy format: plain text -> treat as one prize
    return [raw]

    """
    Return the current prizes as an ordered list of strings.
    Stored as JSON in the 'prizes_text' setting for flexibility.
    """
    raw = get_prizes_text()
    if not raw:
        return []

    try:
        data = json.loads(raw)
        # If it’s already a list, return it as list of strings
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        # Old data: treat as single blob
        return [raw]

    return []

def set_prizes_list(prizes: list[str]) -> None:
    """
    Store the given ordered list of prizes as JSON in 'prizes_text'.
    """
    raw = json.dumps(prizes, ensure_ascii=False)
    set_prizes_text(raw)


@with_db
# get_user_entry retrieves the system name that a user has entered for the current contest, which is used in the /myguess command and to check if a user has already entered.
def get_user_entry(user_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    contest_id = get_current_contest_id()
    cur.execute(
        "SELECT system_name FROM contest_entries WHERE contest_id = ? AND user_id = ?",
        (contest_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


@with_db
# set_user_entry inserts or updates a user's entry for the current contest. It uses an UPSERT statement to ensure that if the user has already entered, their entry will be updated with the new system name and timestamp instead of creating a duplicate entry.
def set_user_entry(user_id: int, system_name: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    contest_id = get_current_contest_id()
    entered_at = datetime.now(UTC).isoformat()
    
    cur.execute(
        "INSERT INTO contest_entries(contest_id, user_id, system_name, entered_at) "
        "VALUES(?, ?, ?, ?) "
        "ON CONFLICT(contest_id, user_id) "
        "DO UPDATE SET system_name = excluded.system_name, entered_at = excluded.entered_at",
        (contest_id, user_id, system_name, entered_at),
    )
    conn.commit()
    conn.close()


@with_db
# is_system_taken checks if a given system name has already been entered by another user for the current contest, which is used to enforce the rule that each system can only be guessed by one participant.
def is_system_taken(system_name: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    contest_id = get_current_contest_id()
    cur.execute(
        "SELECT 1 FROM contest_entries WHERE contest_id = ? AND system_name = ?",
        (contest_id, system_name),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


@with_db
# is_contest_open checks the settings table for the "contest_open" key to determine if the contest is currently accepting entries. This is used in the /enter command to prevent users from entering when the contest is closed.
def is_contest_open() -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", ("contest_open",))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return True
    return row[0] == "1"


@with_db
# set_contest_open updates the "contest_open" setting in the database to control whether the contest is accepting entries.
def set_contest_open(open_flag: bool) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("contest_open", "1" if open_flag else "0"),
    )
    conn.commit()
    conn.close()


@with_db
# get_fob_system retrieves the actual FOB system that was set by an admin after the FOB spawns. This is used to determine the winner when picking a winner from the entries.
def get_fob_system() -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", ("fob_system",))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


@with_db
# set_fob_system allows admins to set the actual FOB system after it spawns, which is essential for determining the winner of the contest based on the entries that guessed that system.
def set_fob_system(system_name: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("fob_system", system_name),
    )
    conn.commit()
    conn.close()

@with_db
# is_winner_picked checks if a winner has already been picked for the current contest by looking up the "winner_picked" key in the settings table. This is used to prevent reopening a contest that already has a winner.
def is_winner_picked() -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM settings WHERE key = ?",
        (WINNER_PICKED_KEY,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return False
    return row[0] == "1"


@with_db
# set_winner_picked updates the "winner_picked" setting in the database to indicate whether a winner has been picked for the current contest. This can be used to enforce rules around reopening contests or picking winners.
def set_winner_picked(picked: bool) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (WINNER_PICKED_KEY, "1" if picked else "0"),
    )
    conn.commit()
    conn.close()


@with_db
# get_entry_deadline retrieves the entry deadline timestamp from the settings table, which can be used to automatically close entries when the deadline has passed.
def get_entry_deadline() -> str | None:
    """Get entry deadline timestamp (ISO format)"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", ("entry_deadline",))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

@with_db
# set_entry_deadline allows admins to set the entry deadline for the contest, which can be used to automatically close entries when the deadline has passed.
def set_entry_deadline(deadline_iso: str | None) -> None:
    """Set entry deadline timestamp (ISO format), or clear if None"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if deadline_iso is None:
        cur.execute("DELETE FROM settings WHERE key = ?", ("entry_deadline",))
    else:
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("entry_deadline", deadline_iso),
        )
    conn.commit()
    conn.close()

@with_db
# is_past_deadline checks if the current time is past the entry deadline that may be set by an admin. This can be used to automatically close entries when the deadline has passed, even if an admin forgets to manually close the contest.
def is_past_deadline() -> bool:
    """Check if current time is past entry deadline"""
    deadline = get_entry_deadline()
    if deadline is None:
        return False
    
    deadline_dt = datetime.fromisoformat(deadline)
    now = datetime.now(UTC)
    return now >= deadline_dt

@with_db
# get_countdown_message_id and set_countdown_message_id are used to store the message ID of the countdown message that is posted in the channel. This allows the update_countdown task to edit the existing message instead of posting a new one every time it updates, which keeps the channel cleaner and more organized.
def get_countdown_message_id() -> int | None:
    """Get the message ID of the active countdown"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", ("countdown_message_id",))
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else None

@with_db
# set_countdown_message_id stores the message ID of the countdown message in the settings table. If the message ID is None, it deletes the setting, which can be used to indicate that there is no active countdown message (e.g. if it was deleted).
def set_countdown_message_id(message_id: int | None) -> None:
    """Store the countdown message ID"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if message_id is None:
        cur.execute("DELETE FROM settings WHERE key = ?", ("countdown_message_id",))
    else:
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("countdown_message_id", str(message_id)),
        )
    conn.commit()
    conn.close()

@with_db
# get_countdown_channel_id and set_countdown_channel_id are used to store the channel ID where the countdown message is posted. This allows the bot to know which channel to post the countdown message in and where to edit it when updating.
def get_countdown_channel_id() -> int | None:
    """Get the channel ID for countdown messages"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", ("countdown_channel_id",))
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else None

@with_db
# set_countdown_channel_id stores the channel ID for the countdown messages in the settings table. This allows admins to configure which channel the countdown updates will be posted in.
def set_countdown_channel_id(channel_id: int) -> None:
    """Store the channel ID for countdown messages"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("countdown_channel_id", str(channel_id)),
    )
    conn.commit()
    conn.close()

@with_db
# get_total_entries_for_current_contest counts the total number of entries for the current contest, which can be displayed in the contest status to show how many participants have entered.
def get_total_entries_for_current_contest() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    contest_id = get_current_contest_id()
    cur.execute(
        "SELECT COUNT(*) FROM contest_entries WHERE contest_id = ?",
        (contest_id,),
    )
    (count,) = cur.fetchone()
    conn.close()
    return count

@with_db
# get_current_winner_info retrieves the user ID and system name of the winner for the current contest, which can be used to display the winner information in the contest status or other commands.
def get_current_winner_info() -> tuple[int | None, str | None]:
    """Return (winner_user_id, winner_system) for the current contest."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    contest_id = get_current_contest_id()
    cur.execute(
        "SELECT winner_user_id, winner_system FROM contests WHERE id = ?",
        (contest_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None, None

    return row[0], row[1]

# ---------- Discord bot setup ----------

intents = discord.Intents.default()
intents.message_content = True

# is_contest_admin checks if the user has the "manage_guild" permission, which is used to restrict certain commands to admins only.
def is_contest_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.manage_guild


class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=DISCORD_APP_ID,
        )

    async def setup_hook(self):
        guild = discord.Object(id=DISCORD_SERVER_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("Slash commands synced to guild.")
        update_countdown.start()


bot = MyBot()

# before_countdown is a setup function that runs before the update_countdown task starts. 
# It waits until the bot is fully ready and connected to Discord before allowing the countdown task to run, 
# which ensures that the bot can properly fetch channels and messages when updating the countdown.
@update_countdown.before_loop
async def before_countdown():
    await bot.wait_until_ready()


@bot.event
# on_ready is called when the bot has successfully connected to Discord and is ready to start processing events. 
# It logs the bot's username and ID to confirm that it has logged in correctly.
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    await send_startup_message(bot)

# ---------- Commands ----------

# /ping command for testing if the bot is responsive
@bot.tree.command(name="ping", description="Check if the bot is alive.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

# /rules command to show contest rules
@bot.tree.command(
    name="rules",
    description="Show the rules for the Guristas FOB contest.",
)
async def rules(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Black Rabbits Guristas FOB Contest",
        description=(
            "Predict which EVE system the next Guristas FOB will spawn in "
            "and win ISK prizes."
        ),
        color=0x00FFAA,
    )

    embed.add_field(
        name="How to enter",
        value=(
            "• Use `/allowedsystems` to see the list of valid FOB systems.\n"
            "• Use `/enter system: <system name>` to submit your guess.\n"
            "• You can check your guess with `/myguess`.\n"
            "• Only **one entry per Discord member** is allowed."
        ),
        inline=False,
    )

    embed.add_field(
        name="Prizes",
        value="Use `/prizes` to see the current prize list set by the admins.",
        inline=False,
    )
    
    embed.add_field(
        name="Contest timing",
        value=(
            "• Entries are accepted while the contest is **open**.\n"
            "• Admins can close entries with `/endcontest` and reopen with "
            "`/opencontest`."
        ),
        inline=False,
    )

    embed.add_field(
        name="Winner selection",
        value=(
            "• When the real Guristas FOB spawns, an admin sets the system "
            "with `/setfobsystem`.\n"
            "• `/pickwinner` chooses a random correct guess as the winner."
        ),
        inline=False,
    )

    embed.set_footer(text="Fly dangerous o7")

    await interaction.response.send_message(embed=embed)

# /prizes command to show current prizes
@bot.tree.command(name="prizes", description="Show the prizes for the FOB contest.")
async def prizes(interaction: discord.Interaction):
    prizes_list = get_prizes_list()
    if not prizes_list:
        await interaction.response.send_message(
            "Prizes have not been set yet. An admin needs to use `/setprizes`.",
            ephemeral=True,
        )
        return

    lines = [f"{idx+1}. {p}" for idx, p in enumerate(prizes_list)]
    message = "**Current contest prizes:**\n" + "\n".join(lines)
    await interaction.response.send_message(message)

# /allowedsystems command to show the list of allowed FOB systems for the contest, which helps users know which systems they can guess and ensures that guesses are valid.
@bot.tree.command(
    name="allowedsystems",
    description="Show the list of allowed Guristas FOB systems for this contest.",
)
async def allowedsystems(interaction: discord.Interaction):
    # Use the raw list so formatting is nice and stable
    names = sorted(ALLOWED_FOB_SYSTEMS_RAW)
    systems_text = ", ".join(names)

    # Split into chunks if it ever gets too long; for now it's short enough
    message = (
        "**Allowed Guristas FOB systems (GalMil/CalMil FW space):**\n"
        f"{systems_text}"
    )

    await interaction.response.send_message(message, ephemeral=True)

# setprizes command to set the prize list for the contest. This command is restricted to admins 
# and allows them to specify an ordered list of prizes that will be awarded to the winners of the contest. 
# The prizes are stored in the database and can be displayed to users with the /prizes command.
class SetPrizesModal(discord.ui.Modal, title="Set Contest Prizes"):
    # Discord restricts modals to 5 components total.
    # We use 1 "count" field + 4 prize fields (max 4 prizes per contest via this modal).
    count = discord.ui.TextInput(
        label="How many prizes? (1–4)",
        placeholder="e.g., 3",
        required=True,
        max_length=1,
    )

    prize1 = discord.ui.TextInput(
        label="Prize 1",
        placeholder="e.g., 100M ISK",
        required=False,
        max_length=200,
    )
    prize2 = discord.ui.TextInput(
        label="Prize 2 (optional)",
        placeholder="e.g., A rare ship",
        required=False,
        max_length=200,
    )
    prize3 = discord.ui.TextInput(
        label="Prize 3 (optional)",
        placeholder="e.g., 20 Synth Boosters",
        required=False,
        max_length=200,
    )
    prize4 = discord.ui.TextInput(
        label="Prize 4 (optional)",
        required=False,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Validate count
        try:
            n = int(self.count.value)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a number between 1 and 4 for the prize count.",
                ephemeral=True,
            )
            return

        if not (1 <= n <= 4):
            await interaction.response.send_message(
                "Prize count must be between 1 and 4.",
                ephemeral=True,
            )
            return

        # Collect up to n non-empty prize fields in order
        fields = [self.prize1, self.prize2, self.prize3, self.prize4]
        prizes: list[str] = []
        for i in range(n):
            value = fields[i].value.strip()
            if not value:
                await interaction.response.send_message(
                    f"Prize {i+1} is empty. Please fill in all {n} prizes.",
                    ephemeral=True,
                )
                return
            prizes.append(value)

        # Save to DB as JSON list
        set_prizes_list(prizes)

        # Build ordered list preview
        lines = [f"{idx+1}. {p}" for idx, p in enumerate(prizes)]
        text = "Prizes have been updated:\n" + "\n".join(lines)

        await interaction.response.send_message(text, ephemeral=True)

@bot.tree.command(
    name="setprizes",
    description="Set the ordered prize list for the FOB contest (admins only, up to 4 prizes).",
)
async def setprizes(interaction: discord.Interaction):
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to set prizes.",
            ephemeral=True,
        )
        return

    modal = SetPrizesModal()
    await interaction.response.send_modal(modal)

# /endcontest command to close entries
# Create Modal for FOB System Input
class EndContestModal(discord.ui.Modal, title='End Contest & Set FOB System'):
    fob_system_input = discord.ui.TextInput(
        label='Actual FOB System',
        placeholder='Enter the system where FOB spawned (e.g., Jita)',
        required=True,
        max_length=50,
    )
    
    def __init__(self, contest_id: int, channel_id: int):
        super().__init__()
        self.contest_id = contest_id
        self.channel_id = channel_id
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Normalize the system name
            system_norm = normalize_system_name(self.fob_system_input.value)

            # Validate system against allowed list
            if not is_allowed_fob_system(system_norm):
                await interaction.response.send_message(
                    "That system is not in the list of allowed Guristas FOB systems for this contest.\n"
                    "Please enter a valid GalMil/CalMil system from the approved list.",
                    ephemeral=True,
                )
                return
            
            # Close the contest
            set_contest_open(False)
            
            # Set the FOB system
            set_fob_system(system_norm)
            
            # Find matching entries
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, system_name FROM contest_entries "
                "WHERE contest_id = ? AND system_name = ?",
                (self.contest_id, system_norm),
            )
            rows = cur.fetchall()
            
            if not rows:
                # No winner - no correct guesses
                conn.close()
                
                await interaction.response.send_message(
                    f"❌ Contest #{self.contest_id} has ended.\n"
                    f"🎯 FOB system was: **{system_norm}**\n"
                    f"😢 No entries guessed correctly. Better luck next time!",
                    ephemeral=True,
                )
                
                # Send public announcement
                channel = interaction.channel
                embed = discord.Embed(
                    title="🏁 Contest Ended - No Winner",
                    description=f"The Guristas FOB Contest #{self.contest_id} has concluded.",
                    color=0xFF6B6B,  # Red-ish
                )
                embed.add_field(
                    name="FOB System",
                    value=f"**{system_norm}**",
                    inline=False,
                )
                embed.add_field(
                    name="Result",
                    value="No pilots guessed correctly. Better luck in the next contest!",
                    inline=False,
                )
                embed.set_footer(text="Fly dangerous o7")
                
                await channel.send(embed=embed)
                return
            
            # Pick random winner from correct entries
            winner_user_id, _ = random.choice(rows)
            
            # Mark winner as picked
            set_winner_picked(True)
            
            # Update contest record
            cur.execute(
                "UPDATE contests SET winner_user_id = ?, winner_system = ? WHERE id = ?",
                (winner_user_id, system_norm, self.contest_id),
            )
            conn.commit()
            conn.close()
            
            # Get prize information
            prizes_list = get_prizes_list()
            if not prizes_list:
                prizes_text = "Prize details to be announced by admins."
            else:
                prize_lines = [f"{idx+1}. {p}" for idx, p in enumerate(prizes_list)]
                prizes_text = "\n".join(prize_lines)

            # Send admin confirmation
            await interaction.response.send_message(
                f"✅ Contest #{self.contest_id} has ended.\n"
                f"🎯 FOB system: **{system_norm}**\n"
                f"🏆 Winner: <@{winner_user_id}>\n"
                f"📢 Public announcement sent to channel.",
                ephemeral=True,
            )

            # Send public winner announcement
            channel = interaction.channel
            embed = discord.Embed(
                title="🏆 Contest Winner Announced!",
                description=f"The Guristas FOB Contest #{self.contest_id} has concluded!",
                color=0xFFD700,  # Gold
            )
            embed.add_field(
                name="🎯 FOB System",
                value=f"**{system_norm}**",
                inline=False,
            )
            embed.add_field(
                name="🎉 Winner",
                value=f"<@{winner_user_id}>",
                inline=False,
            )
            embed.add_field(
                name="🎁 Prizes",
                value=prizes_text,
                inline=False,
            )
            embed.set_footer(text="Congratulations to the winner! o7")

            await channel.send(content=f"🎊 <@{winner_user_id}> 🎊", embed=embed)

        except sqlite3.Error as e:
            log.exception("Error ending contest: %s", e)
            await interaction.response.send_message(
                "❌ Failed to end contest due to a database error. Check logs.",
                ephemeral=True,
            )
        except Exception as e:
            log.exception("Unexpected error ending contest: %s", e)
            await interaction.response.send_message(
                f"❌ Unexpected error: {e}",
                ephemeral=True,
            )

@bot.tree.command(
    name="endcontest",
    description="Close contest, set FOB system, and pick winner (admins only).",
)
async def endcontest(interaction: discord.Interaction):
    # 1) Permission check – answer immediately and stop if not admin
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to end the contest.",
            ephemeral=True,
        )
        return
    
    # 2) Prevent ending a contest that already has a winner
    if is_winner_picked():
        await interaction.response.send_message(
            "This contest already has a winner and cannot be ended again.\n"
            "Use `/newcontest` to start a new contest.",
            ephemeral=True,
        )
        return    

    # 3) Make sure contest is currently open
    if not is_contest_open():
        await interaction.response.send_message(
            "The contest is already closed.",
            ephemeral=True,
        )
        return

    # 4) Make sure there is at least one entry
    contest_id = get_current_contest_id()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM contest_entries WHERE contest_id = ?",
        (contest_id,),
    )
    (count,) = cur.fetchone()
    conn.close()

    if count == 0:
        await interaction.response.send_message(
            "⚠️ Cannot end contest - there are no entries yet.\n"
            "Wait for participants to submit their guesses first.",
            ephemeral=True,
        )
        return

    # 5) Everything is OK → show the modal immediately.
    #    This is the ONE and ONLY response for this interaction.
    modal = EndContestModal(contest_id, interaction.channel_id)
    await interaction.response.send_modal(modal)


# /opencontest command to reopen entries if the contest is closed and no winner has been picked yet
@bot.tree.command(
    name="opencontest",
    description="Re-open the contest so entries are accepted again (admins only).",
)
async def opencontest(interaction: discord.Interaction):
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to open the contest.",
            ephemeral=True,
        )
        return

    if is_winner_picked():
        await interaction.response.send_message(
            "This contest already has a winner and cannot be reopened.\n"
            "Use `/newcontest` to start a new contest instead.",
            ephemeral=True,
        )
        return

    if is_contest_open():
        await interaction.response.send_message(
            "The contest is already open.",
            ephemeral=True,
        )
        return

    set_contest_open(True)
    await interaction.response.send_message(
        "The contest has been re-opened. New entries are now accepted.",
        ephemeral=True,
    )

# /listentries command to show all current entries (admin only)
@bot.tree.command(
    name="listentries",
    description="List all contest entries (admins only).",
)
async def listentries(interaction: discord.Interaction):
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to view all entries.",
            ephemeral=True,
        )
        return

    if not is_contest_open():
        await interaction.response.send_message(
            "The current contest is closed; there are no active entries to list.",
            ephemeral=True,
        )
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    contest_id = get_current_contest_id()
    cur.execute(
        "SELECT user_id, system_name FROM contest_entries "
        "WHERE contest_id = ? ORDER BY user_id",
        (contest_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message(
            "There are currently no contest entries.",
            ephemeral=True,
        )
        return

    lines = [f"<@{user_id}>: {system_name}" for user_id, system_name in rows]
    message = "**Current contest entries:**\n" + "\n".join(lines)
    await interaction.response.send_message(
        message,
        ephemeral=True,
    )

@bot.tree.command(
    name="conteststatus",
    description="Show the current contest status.",
)
async def conteststatus(interaction: discord.Interaction):
    contest_id = get_current_contest_id()
    opened_at_raw = get_contest_open_date(contest_id)

    # Format opened_at nicely with date + time
    if opened_at_raw:
        try:
            opened_dt = datetime.fromisoformat(opened_at_raw)
            opened_display = opened_dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            # Fallback for any older rows that only stored a date
            opened_display = opened_at_raw
    else:
        opened_display = "Unknown"

    # Get deadline and open/closed state
    deadline_iso = get_entry_deadline()
    contest_open = is_contest_open()
    winner_picked = is_winner_picked()
    fob_system = get_fob_system()
    total_entries = get_total_entries_for_current_contest()

    # Build status lines
    lines: list[str] = []

    lines.append(f"Status for Contest #{contest_id} [{opened_display}]")

    if not contest_open and deadline_iso:
        # Contest closed because deadline passed
        lines.append("Entries are currently:")
        lines.append("DEADLINE PASSED - FOB system entries are CLOSED")
    elif contest_open:
        lines.append("Entries are currently:")
        lines.append("OPEN for entries")
    else:
        lines.append("Entries are currently:")
        lines.append("CLOSED")

    # Deadline display
    if deadline_iso:
        deadline_dt = datetime.fromisoformat(deadline_iso)
        deadline_ts = int(deadline_dt.timestamp())
        rel = f"<t:{deadline_ts}:R>"
        lines.append(f"Entry deadline: <t:{deadline_ts}:F> ({rel})")
    else:
        lines.append("Entry deadline has not been set yet.")

    # Total entries
    lines.append(f"Total entries: {total_entries}")

    # FOB system / winner info
    if fob_system:
        lines.append(f"FOB system is set to {fob_system}.")
    else:
        lines.append("FOB system has not been set yet.")

    if winner_picked:
        # You likely store winner info in contests table; fetch it here
        winner_user_id, winner_system = get_current_winner_info()
        if winner_user_id is not None:
            lines.append(
                f"Contest Winner is: <@{winner_user_id}> "
                f"with system {winner_system}."
            )
        else:
            lines.append("Winner has been marked, but details are missing.")
    else:
        lines.append("Winner has not been determined yet.")

    await interaction.response.send_message(
        "\n".join(lines),
        ephemeral=True,
    )

# /newcontest command to close current contest and start a new one
# Create a Modal for deadline input
class DeadlineModal(discord.ui.Modal, title='Set Contest Deadline'):
    deadline_input = discord.ui.TextInput(
        label='Deadline (UTC time)',
        placeholder='YYYY-MM-DD HH:MM (e.g., 2026-02-16 14:30 UTC)',
        required=True,
        max_length=16,
        min_length=16,
    )
    
    def __init__(self, new_contest_id: int, opened_at: str, channel_id: int):
        super().__init__()
        self.new_contest_id = new_contest_id
        self.opened_at = opened_at
        self.channel_id = channel_id
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse the datetime
            deadline_dt = datetime.strptime(self.deadline_input.value, "%Y-%m-%d %H:%M")
            deadline_dt = deadline_dt.replace(tzinfo=UTC)
            deadline_iso = deadline_dt.isoformat()
            
            # Validate deadline is in the future
            now = datetime.now(UTC)
            if deadline_dt <= now:
                await interaction.response.send_message(
                    "⚠️ Deadline must be in the future. Please use `/setdeadline` to set a valid deadline.",
                    ephemeral=True,
                )
                return
            
            # Store the deadline
            set_entry_deadline(deadline_iso)
            
            # Set the countdown channel to the channel where command was run
            set_countdown_channel_id(self.channel_id)

            # Clear previous countdown message if it exists
            message_id = get_countdown_message_id()
            if message_id:
                channel_id = get_countdown_channel_id()
                if channel_id:
                    try:
                        channel = bot.get_channel(channel_id)
                        if channel:
                            message = await channel.fetch_message(message_id)
                            await message.delete()
                    except discord.HTTPException as e:
                        log.warning("Failed to delete previous countdown message %s: %s", message_id, e)
                set_countdown_message_id(None)

            # Trigger immediate countdown update so the public embed appears/updates
            if update_countdown.is_running():
                update_countdown.restart()
            
            # Format for display
            discord_timestamp = int(deadline_dt.timestamp())
            
            await interaction.response.send_message(
                f"✅ New contest started: **Contest #{self.new_contest_id} [{self.opened_at}]**\n"
                f"📅 Entry deadline set to: <t:{discord_timestamp}:F> (<t:{discord_timestamp}:R>)\n"
                f"⏰ Countdown will be posted in <#{self.channel_id}>.\n"
                f"FOB system reset and entries are now OPEN.",
                ephemeral=True,
            )
            
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid deadline format. Use: YYYY-MM-DD HH:MM (e.g., 2026-02-16 14:30)\n"
                "Please use `/setdeadline` to set the deadline manually.",
                ephemeral=True,
            )



# /newcontest command to close current contest and start a new one (admin only)
@bot.tree.command(
    name="newcontest",
    description="Close current contest and start a new one (admins only).",
)
async def newcontest(interaction: discord.Interaction):
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to start a new contest.",
            ephemeral=True,
        )
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Close the current contest
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("contest_open", "0"),
        )

        # Insert a new contest row with today's date (UTC)
        opened_at = datetime.now(UTC).isoformat()
        cur.execute(
            "INSERT INTO contests(opened_at) VALUES(?)",
            (opened_at,),
        )
        new_contest_id = cur.lastrowid

        # Clear FOB system for the new contest (settings is global)
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("fob_system", ""),
        )
        cur.execute("DELETE FROM settings WHERE key = ?", ("entry_deadline",))
        # Clear countdown message tracking
        cur.execute("DELETE FROM settings WHERE key = ?", ("countdown_message_id",))
        cur.execute("DELETE FROM settings WHERE key = ?", ("countdown_channel_id",))

        # Update current_contest_id and reopen entries
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("current_contest_id", str(new_contest_id)),
        )
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("contest_open", "1"),
        )

        # Reset winner-picked flag for the new contest
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (WINNER_PICKED_KEY, "0"),
        )

        conn.commit()
        
        # Show modal to set deadline
        modal = DeadlineModal(new_contest_id, opened_at, interaction.channel_id)
        await interaction.response.send_modal(modal)
        
    except sqlite3.Error as e:
        conn.rollback()
        log.exception("Error while starting new contest: %s", e)
        await interaction.response.send_message(
            "Failed to start a new contest due to a database error.",
            ephemeral=True,
        )
        return
    finally:
        conn.close()

# /backupdb command to create a timestamped backup of the database (admin only)
@bot.tree.command(
    name="backupdb",
    description="Create a timestamped backup of the contest database (admins only).",
)
async def backupdb(interaction: discord.Interaction):
    # If user is not an admin, answer immediately and stop
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to back up the database.",
            ephemeral=True,
        )
        return

    # Tell Discord: "I got your command, I'm working on it."
    await interaction.response.defer(ephemeral=True)

    # If the DB file doesn't exist, tell the user and stop
    if not os.path.exists(DB_PATH):
        await interaction.followup.send(
            "No database file found to back up.",
            ephemeral=True,
        )
        return

    try:
        # Use timezone-aware UTC instead of utcnow()
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_name = f"contest-{ts}.db"
        backup_path = os.path.join(os.path.dirname(DB_PATH), backup_name)

        shutil.copy2(DB_PATH, backup_path)
        log.info("Database backed up to %s", backup_path)

        # Send the real result message
        await interaction.followup.send(
            f"Database backup created: `{backup_name}`",
            ephemeral=True,
        )
    except Exception as e:
        log.exception("Failed to back up database: %s", e)
        await interaction.followup.send(
            "Failed to back up the database. See logs for details.",
            ephemeral=True,
        )

# /myguess command to show the user's current entry for the active contest
@bot.tree.command(
    name="myguess",
    description="Show your current entry for the active FOB contest.",
)
async def myguess(interaction: discord.Interaction):
    user_id = interaction.user.id
    system = get_user_entry(user_id)

    if system is None:
        await interaction.response.send_message(
            "You have not entered the current contest yet.\n"
            "Use `/enter` to submit your system guess.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"Your current entry for this contest is: **{system}**.",
        ephemeral=True,
    )

# /contesthistory command to show all contests (including no-winner ones)
@bot.tree.command(
    name="contesthistory",
    description="Show history of all contests, including no-winner contests.",
)
async def contesthistory(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get all contests, regardless of whether they have a winner
    cur.execute(
        """
        SELECT c.id, c.opened_at, c.winner_user_id, c.winner_system 
        FROM contests c
        ORDER BY c.id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message(
            "No contests have been recorded yet.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="📜 Contest History",
        description="All Guristas FOB Contests (including no-winner contests)",
        color=0x00BFFF,  # Blue-ish
    )

    for contest_id, opened_at, winner_user_id, winner_system in rows:
        try:
            opened_dt = datetime.fromisoformat(opened_at)
            opened_display = opened_dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            opened_display = opened_at

        # Human-readable status line
        if winner_user_id is None:
            value = (
                "Status: ❌ No winner (no correct guesses)\n"
                f"Opened at: `{opened_at}`"
            )
        else:
            value = (
                f"Status: ✅ Winner\n"
                f"Winner: <@{winner_user_id}>\n"
                f"System: **{winner_system}**\n"
                f"Opened at: `{opened_display}`"
            )

        embed.add_field(
            name=f"Contest #{contest_id}",
            value=value,
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# /pastwinners command to show all previous contest winners and their winning systems
@bot.tree.command(
    name="pastwinners",
    description="Show previous contest winners.",
)
async def pastwinners(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get all contests that have winners
    cur.execute(
        """
        SELECT c.id, c.opened_at, c.winner_user_id, c.winner_system 
        FROM contests c
        WHERE c.winner_user_id IS NOT NULL
        ORDER BY c.id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        await interaction.response.send_message(
            "No contest winners yet.",
            ephemeral=True,
        )
        return
    
    embed = discord.Embed(
        title="🏆 Past Contest Winners",
        description="History of Guristas FOB Contest winners",
        color=0xFFD700,  # Gold color
    )
    
    for contest_id, opened_at, winner_user_id, winner_system in rows:
        try:
            opened_dt = datetime.fromisoformat(opened_at)
            opened_display = opened_dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            opened_display = opened_at

        embed.add_field(
            name=f"Contest #{contest_id} ({opened_display})",
            value=f"Winner: <@{winner_user_id}>\nSystem: **{winner_system}**",
            inline=False,
        )
  
    await interaction.response.send_message(embed=embed, ephemeral=True)

# /enter command for users to submit their system guess for the contest
@bot.tree.command(
    name="enter",
    description="Enter the Guristas FOB contest with your system guess.",
)
@app_commands.describe(system=
                    "Start typing a system name (2–3 letters) to filter the list, "
                    "then pick from the suggestions where you think the FOB will spawn.")
@app_commands.autocomplete(system=system_autocomplete)
async def enter(interaction: discord.Interaction, system: str):
    user_id = interaction.user.id
    system_norm = normalize_system_name(system)

    # 0) System must be in allowed FOB list
    if not is_allowed_fob_system(system_norm):
        await interaction.response.send_message(
            "That system is not in the list of allowed Guristas FOB systems for this contest.\n"
            "Please choose a system from the approved list of GalMil/CalMil systems.",
            ephemeral=True,
        )
        return

    # 1) Contest must be open
    if not is_contest_open():
        await interaction.response.send_message(
            "The contest is closed. No more entries are being accepted.",
            ephemeral=True,
        )
        return

    # 2) Check if past deadline
    if is_past_deadline():
        deadline = get_entry_deadline()
        if deadline:
            deadline_dt = datetime.fromisoformat(deadline)
            discord_timestamp = int(deadline_dt.timestamp())
            await interaction.response.send_message(
                f"Entry deadline has passed (<t:{discord_timestamp}:R>). "
                "No more entries are being accepted.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Entry deadline has passed. "
                "No more entries are being accepted.",
                ephemeral=True,
            )
        return

    # 3) Check if THIS USER already has an entry in THIS contest
    previous = get_user_entry(user_id)
    if previous is not None:
        # At this point, previous really means "you already had an entry BEFORE this command"
        await interaction.response.send_message(
            f"You already entered the contest with the system: **{previous}**.\n"
            "Only one entry per person is allowed.",
            ephemeral=True,
        )
        return

    # 4) Check if THIS SYSTEM is already taken by someone else in THIS contest
    if is_system_taken(system_norm):
        await interaction.response.send_message(
            f"The system **{system_norm}** has already been picked by another pilot.\n"
            "Please choose a different system.",
            ephemeral=True,
        )
        return

    # 5) All checks passed → store the user's entry now
    set_user_entry(user_id, system_norm)

    # 6) Success message
    await interaction.response.send_message(
        f"Entry recorded! You guessed: **{system_norm}**.\n"
        "Good luck with the Guristas FOB!",
        ephemeral=True,
    )


# /helpcontest command to show all available commands and their descriptions
@bot.tree.command(
    name="helpcontest",
    description="Show all FOB contest bot commands.",
)
async def helpcontest(interaction: discord.Interaction):
    # Defer immediately to prevent timeout issues
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(
        title="FOB Contest Bot Commands",
        description="Commands for the Guristas FOB Contest",
        color=0x00FFAA,
    )
    
    # User commands
    embed.add_field(
        name="📝 User Commands",
        value=(
            "`/allowedsystems` – Show the list of allowed FOB systems\n"
            "`/contesthistory` – Show history of all contests\n"
            "`/enter` – Enter the contest with your system guess\n"
            "`/myguess` – Show your current entry\n"
            "`/pastwinners` – Show previous contest winners\n"
            "`/prizes` – Show prize information\n"
            "`/rules` – Show contest rules\n"
            "`/utcnow` – Show current UTC time and example timestamps\n"
        ),
        inline=False,
    )
    
    # Admin commands
    embed.add_field(
        name="⚙️ Admin Commands",
        value=(
            "`/backupdb` – Back up the database\n"
            "`/cleardeadline` – Remove entry deadline\n"
            "`/conteststatus` – Show current contest status\n"
            "`/endcontest` – Close entries and pick winner\n"
            "`/listentries` – List all entries\n"
            "`/newcontest` – Start a new contest\n"
            "`/opencontest` – Re-open entries\n"
            "`/setdeadline` – Set entry deadline (CST → UTC)\n"
            "`/setprizes` – Set the ordered prize list (1–4 prizes)\n"
        ),
        inline=False,
    )
    
    embed.set_footer(text="Use /helpcontest anytime to see this list")
    
    # Use followup instead of response since we deferred
    await interaction.followup.send(embed=embed, ephemeral=True)

# /setdeadline command for admins to set the entry deadline for the contest
@bot.tree.command(
    name="setdeadline",
    description="Set entry deadline (format YYYY-MM-DD HH:MM) (admins only).",
)
@app_commands.describe(
    datetime_local="Deadline in your local time (CST, YYYY-MM-DD HH:MM), e.g., 2026-02-16 08:30"
)
async def setdeadline(interaction: discord.Interaction, datetime_local: str):
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to set entry deadline.",
            ephemeral=True,
        )
        return

    # Check if winner has been picked
    if is_winner_picked():
        await interaction.response.send_message(
            "Cannot set deadline - winner has already been picked for this contest.\n"
            "Use `/newcontest` to start a new contest.",
            ephemeral=True,
        )
        return

    try:
        from datetime import timedelta

        # Parse the datetime as CST
        deadline_local = datetime.strptime(datetime_local, "%Y-%m-%d %H:%M")

        # Convert CST to UTC (add 6 hours)
        deadline_dt = deadline_local + timedelta(hours=6)
        deadline_dt = deadline_dt.replace(tzinfo=UTC)
        deadline_iso = deadline_dt.isoformat()

        set_entry_deadline(deadline_iso)
        set_countdown_channel_id(interaction.channel_id)

        # Format for display
        discord_timestamp = int(deadline_dt.timestamp())
        await interaction.response.send_message(
            f"Entry deadline set to: <t:{discord_timestamp}:F> (<t:{discord_timestamp}:R>)\n"
            f"Countdown will be posted in this channel.",
            ephemeral=True,
        )

        # Trigger immediate countdown update
        if update_countdown.is_running():
            update_countdown.restart()

    except ValueError:
        await interaction.response.send_message(
            "Invalid datetime format. Use: YYYY-MM-DD HH:MM (e.g., 2026-02-16 14:30)",
            ephemeral=True,
        )


# /cleardeadline command for admins to clear the entry deadline and allow entries until the contest is closed
@bot.tree.command(
    name="cleardeadline",
    description="Remove entry deadline (admins only).",
)
async def cleardeadline(interaction: discord.Interaction):
    if not is_contest_admin(interaction):
        await interaction.response.send_message(
            "You do not have permission to clear entry deadline.",
            ephemeral=True,
        )
        return
    
    set_entry_deadline(None)
    
    # Remove countdown message
    message_id = get_countdown_message_id()
    if message_id:
        channel_id = get_countdown_channel_id()
        if channel_id:
            try:
                channel = bot.get_channel(channel_id)
                if channel:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
            except discord.HTTPException as e:
                log.warning("Failed to delete countdown message %s: %s", message_id, e)
        set_countdown_message_id(None)
    
    await interaction.response.send_message(
        "Entry deadline has been cleared. Entries accepted until contest closes.",
        ephemeral=True,
    )

@bot.tree.command(
    name="utcnow",
    description="Show current UTC time and a sample Discord timestamp.",
)
async def utcnow(interaction: discord.Interaction):
    now_utc = datetime.now(UTC)
    discord_timestamp = int(now_utc.timestamp())

    # Human-readable UTC string
    utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    await interaction.response.send_message(
        "Current UTC time:\n"
        f"• `{utc_str}`\n"
        f"• `<t:{discord_timestamp}:F>` → full date/time\n"
        f"• `<t:{discord_timestamp}:R>` → relative time\n",
        ephemeral=True,
    )
    
# ---------- Run bot ----------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    log.error("DISCORD_BOT_TOKEN environment variable is not set.")
    raise RuntimeError("Please set the DISCORD_BOT_TOKEN environment variable before running the bot.")

log.info("Running bot...")
bot.run(TOKEN)