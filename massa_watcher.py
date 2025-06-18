from collections.abc import Iterable
from massa_node_manager import massa_node
from env import data_dir
from env import bot
from env import command
from env import TG_USERNAME
from env import TG_ADMIN
from env import build_default_commands
from env import log
from env import loglevel

from datetime import datetime
from datetime import timedelta
from itertools import batched
import contextlib
import asyncio
import aiohttp
import time
import csv

time_offset = timedelta(minutes=5)

class Watched:
    def __init__(self, address: str, *users: int):
        self.address = address
        self.users: set[int] = set(users)
        self.timestamp: int = int(datetime.now().timestamp() - time_offset.total_seconds())
    def __contains__(self, user: int) -> bool:
        return user in self.users
    async def notify(self, info):
        for user in self.users:
            message = message_notification(info)
            if message is not None:
                await bot.send_message(user, message, parse_mode="html")
        self.timestamp = int(datetime.now().timestamp())

type Watching = dict[str, Watched]
type RevWatching = dict[int, list[str]]

def read_csv(file_path) -> tuple[Watching, RevWatching]:
    """Read a CSV file and return a list of dictionaries."""
    if not file_path.exists():
        write_csv(file_path, {})
    log(f"Reading CSV file: {file_path}")
    with file_path.open("r") as f:
        reader = csv.DictReader(f)
        res: Watching = {}
        rev: RevWatching = {}
        for row in reader:
            key = row["address"]
            if key not in res:
                res[key] = Watched(key)
            user = int(row["user"])
            if user not in rev:
                rev[user] = []
            rev[user].append(key)
            if user not in res[key]:
                res[key].users.add(user)
        return res, rev

def write_csv(file_path, data: Watching):
    """Write a list of dictionaries to a CSV file."""
    log(f"Writing {len(data)} entries to {file_path}")
    with file_path.open("w+", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["address", "user"])
        writer.writeheader()
        for address, watched in data.items():
            log(f"Writing address: {address} with users: {watched.users}")
            for user in watched.users:
                writer.writerow({"address": address, "user": str(user)})

watching_file = data_dir / "watching.csv"
watching, rev_watching = read_csv(watching_file)

address_pat = r"AU[1-9A-HJ-NP-Za-km-z]+"
@command(address=address_pat)
async def watch(event, address: str):
    """\
    Start watching a staking address.
    Usage: /watch <address>
    args:
    - address: Your Massa address.
      pattern: AU[1-9A-HJ-NP-Za-km-z]+
    """
    user = event.sender_id
    # if not api_started:
    #     return await event.reply("API is still starting. Please try again in a few minutes.")
    # info = await get_addresses_info((address,))
    # if not info:
    #     return await event.reply(f"I could not find any information for this address. Please check if it is a valid staking address.\n\nIf you think this is an error, please contact @{TG_ADMIN}.")
    if address not in watching:
        watching[address] = Watched(address)
    if user not in rev_watching:
        rev_watching[user] = []
    if user in watching[address]:
        return await event.reply(f"You are already watching address: {address}")
    watching[address].users.add(user)
    rev_watching[user].append(address)
    await event.reply(f"Started watching address: {address}")

@command(address=address_pat)
async def unwatch(event, address):
    """\
    Stop watching a staking address.
    Usage: /unwatch <address>
    args:
    - address: Your Massa address.
      pattern: AU[1-9A-HJ-NP-Za-km-z]+
    """
    user = event.sender_id
    if address not in watching:
        return await event.reply("You are not watching any addresses.")
    if user not in watching[address]:
        return await event.reply(f"You are not watching address: {address}")
    if user in rev_watching and address in rev_watching[user]:
        rev_watching[user].remove(address)
    watching[address].users.remove(user)
    await event.reply(f"Stopped watching address: {address}")

@command
async def status(event):
    """\
    Show the status of your watched addresses.
    Usage: /status
    """
    user = event.sender_id
    addresses = rev_watching.get(user, [])
    if not addresses:
        return await event.reply("You are not watching any addresses.\nUse /watch <address> to start watching a staking address.")
    msg = []
    info = await get_addresses_info(addresses)
    if not api_started:
        return await event.reply("API is still starting. Please try again in a few minutes.")
    if not info:
        return await event.reply("No information available for your watched addresses.")
    for i in info:
        msg.append(message_notification(i) or "No information available for this address.")
    return await event.reply("\n".join(msg) if msg else "No address found?.", parse_mode="html")

api_started = False
async def get_addresses_info(addresses: Iterable[str]):
    global api_started
    try:
        async with aiohttp.ClientSession() as session:
            url = "http://localhost:33035"
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "get_addresses",
                "params": [list(addresses)]
            }
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    raise ValueError(f"Failed to get addresses info: {response.status}")
                data = await response.json()
                result = data.get("result", [])
                if not api_started:
                    log("API started successfully.")
                    await bot.send_message(TG_ADMIN, "API started successfully.")
                api_started = True
                return result
    except Exception as e:
        if api_started:
            log(loglevel.error, f"Error fetching addresses info: {e}")
            await bot.send_message(TG_ADMIN, f"Error fetching addresses info: {e}")
            raise
        else:
            log(loglevel.warn, "API not started yet, retrying in 5 seconds...")

def should_notify(info) -> bool:
    """Check if the address has missed blocks."""
    if not info:
        return False
    address = info["address"]
    watched = watching[address]
    if watched.timestamp > int(datetime.now().timestamp() - time_offset.total_seconds()):
        # If the address was recently checked, do not notify
        return False
    for cycle in info.get("cycle_infos", []):
        if cycle.get("nok_count", 0) > 0:
            return True
    return False

def message_notification(info) -> (str | None):
    """Format a notification message for a user watching an address."""
    address = info.get("address", None)
    if not address:
        return None
    message = [
        f"<b>Address:</b> <code>{address}</code>:",
        f"<b>Balance:</b> <code>{info.get('final_balance', "0")}</code>MAS, candidate: <code>{info.get('candidate_balance', '0')}</code>MAS",
        f"<b>Rolls:</b> final: <code>{info['final_roll_count'] or 'Unknown'}</code>, candidate: <code>{info.get('candidate_roll_count', '0')}</code>",
        "",
    ]
    for cycle in info.get("cycle_infos", []):
        id = cycle["cycle"]
        is_final = cycle["is_final"]
        ok_count = cycle["ok_count"]
        nok_count = cycle["nok_count"]
        active_rolls = cycle["active_rolls"]
        message.append(f"<b>Cycle {id}:</b> ({'Final' if is_final else 'Not yet Final'})")
        message.append(f"  - <b>Active Rolls:</b> <code>{active_rolls}</code>")
        message.append(f"  - <b>✅ Blocks:</b> <code>{ok_count}</code>, <b>❌ Blocks:</b> <code>{nok_count}</code>")
        message.append("")
    return "\n  ".join(message)

async def notify_missed_blocks():
    for addresses in batched(watching, 1000):
        info = await get_addresses_info(addresses)
        if not info:
            log(loglevel.warn, "No addresses info returned.")
            continue
        for i in info:
            if not should_notify(i):
                continue
            address = i["address"]
            await watching[address].notify(i)
        await asyncio.sleep(1)  # Rate limit to avoid overwhelming the node

async def watch_loop():
    """Main loop to check for new blocks and notify users."""
    back_off = 5  # Initial backoff time in seconds
    while True:
        try:
            log("Checking for new blocks...")
            await notify_missed_blocks()
            await asyncio.sleep(10)  # Check every 10 seconds
            back_off = 1  # Reset backoff on successful check
        except asyncio.CancelledError | KeyboardInterrupt:
            log(loglevel.warn, "Watch loop cancelled.")
            break
        except Exception as e:
            msg = f"Error in watch loop: {e}\nBackoff time: {back_off} seconds"
            log(loglevel.error, msg)
            await bot.send_message(TG_ADMIN, msg)
            await asyncio.sleep(back_off)
            back_off = min(back_off * 1.5, 60*10)  # Cap backoff at 10 minutes

@contextlib.asynccontextmanager
async def watch_blocks():
    loop = asyncio.get_event_loop()
    task = loop.create_task(watch_loop())
    log("Started watch_blocks task.")
    yield
    task.cancel()

async def main():
    log("Connected to Telegram as", TG_USERNAME)
    try:
        async with massa_node(), watch_blocks():
            await bot.send_message(TG_ADMIN, f"Bot started successfully as {TG_USERNAME}.")
            await bot.run_until_disconnected()  # type: ignore
    except KeyboardInterrupt:
        log(loglevel.warn, "Bot stopped by user.")
        await bot.send_message(TG_ADMIN, "Bot stopped by user.")
        write_csv(watching_file, watching)
    except Exception as e:
        log(loglevel.error, f"Error in main: {e}")
        await bot.send_message(TG_ADMIN, f"Error in main: {e}")
        write_csv(watching_file, watching)

if __name__ == "__main__":
    build_default_commands()  # Register commands with the bot
    with bot:
        back_off = 10  # Initial backoff time in seconds
        last_exception = datetime.now() - timedelta(minutes=5)
        while True:
            try:
                bot.loop.run_until_complete(main())
            except KeyboardInterrupt:
                write_csv(watching_file, watching)
                log(loglevel.warn, "Bot stopped by user.")
                break
            except Exception as e:
                write_csv(watching_file, watching)
                if datetime.now() - last_exception < timedelta(minutes=5):
                    back_off = min(back_off * 1.5, 60*10)  # Cap backoff at 10 minutes
                log(loglevel.error, f"Error in main loop: {e}")
                bot.loop.run_until_complete(bot.send_message(TG_ADMIN, f"Error in main loop: {e}"))
                log("Restarting bot...")
                time.sleep(back_off)
