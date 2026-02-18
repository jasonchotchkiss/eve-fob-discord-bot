# eve-fob-discord-bot

Discord bot for tracking and announcing EVE Online Forward Operating Bases (FOBs) for the **Black Rabbits Alliance Insurgency** FOB contest.

## Features

- Tracks FOB spawn and destruction events for selected systems or constellations.
- Posts formatted notifications into configured Discord channels.
- Supports per-server configuration via environment variables or a config file.
- Uses the EVE ESI API for game data and the Discord API for messaging.
- Designed to be simple to deploy and run on a small VPS or home server.

> Note: This project is in active development and the API/commands may change.

## Requirements

- Python 3.10+
- A Discord application and bot token
- EVE Online ESI application credentials (if required for your data source)
- Access to a machine or container platform that can run a long-lived Python process

See `requirements.txt` for the full Python dependency list.

## Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/jasonchotchkiss/eve-fob-discord-bot.git
   cd eve-fob-discord-bot
```

2. **Create and activate a virtual environment (optional but recommended)**

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows (PowerShell)
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Configure environment**

Create a `.env` file (or set environment variables in your process manager) with at least:

```env
DISCORD_TOKEN=your-discord-bot-token
DISCORD_GUILD_ID=your-guild-id
FOB_CHANNEL_ID=channel-id-for-fob-notifications
# Add any EVE / ESI configuration you use here
```

Adjust variable names to match what `bot.py` expects.

## Running the bot

Run the bot directly with Python:

```bash
python bot.py
```

If everything is configured correctly, the bot will log into Discord and begin listening for FOB events and commands.

For production use, consider running the bot under a process manager such as `systemd`, `supervisord`, Docker, or a similar solution so it restarts automatically.

## Commands

The exact commands depend on how you implement `bot.py`, but typical examples might include:

## 📝 User Commands

- `/allowedsystems` – Show the list of allowed FOB systems  
- `/contesthistory` – Show history of all contests  
- `/enter` – Enter the contest with your system guess  
- `/myguess` – Show your current entry  
- `/pastwinners` – Show previous contest winners  
- `/prizes` – Show prize information  
- `/rules` – Show contest rules  
- `/utcnow` – Show current UTC time and example timestamps  

## ⚙️ Admin Commands

- `/backupdb` – Back up the database  
- `/cleardeadline` – Remove entry deadline  
- `/conteststatus` – Show current contest status  
- `/endcontest` – Close entries and pick winner  
- `/listentries` – List all entries  
- `/newcontest` – Start a new contest  
- `/opencontest` – Re-open entries  
- `/setdeadline` – Set entry deadline (CST → UTC)  
- `/setprizes` – Set the ordered prize list (1–4 prizes)  

Update this section to reflect the actual commands once they are finalized.

## Development

- Format and lint code before committing.
- Use feature branches for larger changes and open pull requests against `main`.
- Write small, focused commits with clear messages.


## License

Add your chosen license here (for example, MIT, Apache-2.0, or “All rights reserved” if you prefer).

## Credits

- Built for the **Black Rabbits Alliance Insurgency** FOB contest.
- Uses the official EVE Online ESI API and the Discord API.