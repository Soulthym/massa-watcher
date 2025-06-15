from collections.abc import Iterable
from massa_node_manager import massa_node
from env import data_dir
from env import bot
from env import command
from env import TG_USERNAME
from env import TG_ADMIN

from datetime import datetime
from datetime import timedelta
from itertools import batched
from pprint import pp
import contextlib
import asyncio
import aiohttp
import time
import csv

type Watching = dict[str, dict[int, int]]
type RevWatching = dict[int, list[str]]

def initial_timestamp():
    """Return an initial timestamp for a new user watching an address."""
    return int(datetime.now().timestamp()) - 2 * 60  # 2 minutes ago

def read_csv(file_path) -> tuple[Watching, RevWatching]:
    """Read a CSV file and return a list of dictionaries."""
    if not file_path.exists():
        write_csv(file_path, {})
    print(f"Reading CSV file: {file_path}")
    with file_path.open("r") as f:
        reader = csv.DictReader(f)
        res: Watching = {}
        rev: RevWatching = {}
        for row in reader:
            key = row["address"]
            if key not in res:
                res[key] = {}
            user = int(row["user"])
            if user not in rev:
                rev[user] = []
            rev[user].append(key)
            if user not in res[key]:
                res[key][user] = initial_timestamp()
        return res, rev

def write_csv(file_path, data: Watching):
    """Write a list of dictionaries to a CSV file."""
    print(f"Writing {len(data)} entries to {file_path}")
    with file_path.open("w+", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["address", "user"])
        writer.writeheader()
        for address, users in data.items():
            for user in users:
                writer.writerow({"address": address, "user": str(user)})

watching_file = data_dir / "watching.csv"
watching, rev_watching = read_csv(watching_file)

def get_name(user, prefix=""):
    name_parts = []
    if getattr(user, "first_name", None):
        name_parts.append(user.first_name.strip())
    if getattr(user, "last_name", None):
        name_parts.append(user.last_name.strip())
    if not name_parts and getattr(user, "username", None):
        name_parts.append(f"@{user.username.strip()}")
    if not name_parts:
        return ""
    return prefix + " ".join(name_parts)

@command(cmd="start|help")
async def start(event, cmd):
    cmd = event.pattern_match.group("cmd")
    name = get_name(event.sender, prefix=", ")
    msg = []
    if cmd == "start":
        msg.extend([f"Hello{name}!", "I am your Massa node watcher bot.", ""])
    msg.extend([
        "Available commands:",
        "/start - Start the bot",
        "/help - Show available commands",
        "/watch <address> - Track a staking massa address",
        "/unwatch <address> - Stop tracking a staking massa address",
    ])
    await event.reply("\n".join(msg))

address_pat = r"AU[1-9A-HJ-NP-Za-hj-np-z]{51}"
@command(address=address_pat)
async def watch(event, address: str):
    # Here you would add the logic to start watching the address
    user = event.sender_id
    if address not in watching:
        watching[address] = {}
    if user not in rev_watching:
        rev_watching[user] = []
    if user in watching[address]:
        return await event.reply(f"You are already watching address: {address}")
    rev_watching[user].append(address)
    watching[address][user] = initial_timestamp()
    await event.reply(f"Started watching address: {address}")

@command(address=address_pat)
async def unwatch(event, address):
    # Here you would add the logic to start watching the address
    user = event.sender_id
    if address not in watching:
        return await event.reply("You are not watching any addresses.")
    if user not in watching[address]:
        return await event.reply(f"You are not watching address: {address}")
    if user in rev_watching and address in rev_watching[user]:
        rev_watching[user].remove(address)
    watching[address].pop(user, None)
    await event.reply(f"Stoped watching address: {address}")

@command
async def status(event):
    user = event.sender_id
    addresses = rev_watching.get(user, [])
    if not addresses:
        return await event.reply("You are not watching any addresses.\nUse /watch <address> to start watching a staking address.")
    msg = []
    info = await get_addresses_info(addresses)
    if not info:
        return await event.reply("No information available for your watched addresses.")
    for i in info:
        msg.append(message_notification(i) or "No information available for this address.")
    return await event.reply("\n".join(msg) if msg else "No address found?.", parse_mode="html")

started = False
async def get_addresses_info(addresses: Iterable[str]):
    global started
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
                if not started:
                    print("API started successfully.")
                    await bot.send_message(TG_ADMIN, "API started successfully.")
                started = True
                return result
    except Exception as e:
        if started:
            print(f"Error fetching addresses info: {e}")
            await bot.send_message(TG_ADMIN, f"Error fetching addresses info: {e}")
            raise
        else:
            print("API not started yet, retrying in 5 seconds...")

def has_missed_blocks(info) -> bool:
    """Check if the address has missed blocks."""
    if not info:
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
        f"<b>Balance:</b> <code>{info.get('final_balance', "0")}</code>MAS, canditate: <code>{info.get('candidate_balance', '0')}</code>MAS",
        f"<b>Rolls:</b> final: <code>{info['final_roll_count'] or 'Unknown'}</code>, candidate: <code>{info.get('candidate_roll_count', '0')}</code>",
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
        print(f"{addresses=}")
        info = await get_addresses_info(addresses)
        if not info:
            print("No addresses info returned.")
            continue
        pp(info)
        for i in info:
            if not has_missed_blocks(i):
                continue
            address = i["address"]
            users = list(watching[address].keys())
            for user in users:
                message = message_notification(i)
                if message is not None:
                    await bot.send_message(user, message, parse_mode="html")
        await asyncio.sleep(1)  # Rate limit to avoid overwhelming the node

async def watch_loop():
    """Main loop to check for new blocks and notify users."""
    back_off = 5  # Initial backoff time in seconds
    while True:
        try:
            print("Checking for new blocks...")
            await notify_missed_blocks()
            await asyncio.sleep(10)  # Check every 10 seconds
            back_off = 1  # Reset backoff on successful check
        except asyncio.CancelledError | KeyboardInterrupt:
            print("Watch loop cancelled.")
            break
        except Exception as e:
            msg = f"Error in watch loop: {e}\nBackoff time: {back_off} seconds"
            print(msg)
            await bot.send_message(TG_ADMIN, msg)
            await asyncio.sleep(back_off)
            back_off = min(back_off * 1.5, 60*10)  # Cap backoff at 10 minutes

@contextlib.asynccontextmanager
async def watch_blocks():
    loop = asyncio.get_event_loop()
    task = loop.create_task(watch_loop())
    print("Started watch_blocks task.")
    yield
    task.cancel()

async def main():
    print("Connected to Telegram as", TG_USERNAME)
    try:
        async with massa_node(), watch_blocks():
            await bot.send_message(TG_ADMIN, f"Bot started successfully as {TG_USERNAME}.")
            await bot.run_until_disconnected()  # type: ignore
    except KeyboardInterrupt:
        print("Bot stopped by user.")
        await bot.send_message(TG_ADMIN, "Bot stopped by user.")
        write_csv(watching_file, watching)
    except Exception as e:
        print(f"Error in main: {e}")
        await bot.send_message(TG_ADMIN, f"Error in main: {e}")
        write_csv(watching_file, watching)

if __name__ == "__main__":
    with bot:
        back_off = 10  # Initial backoff time in seconds
        last_exception = datetime.now() - timedelta(minutes=5)
        while True:
            try:
                bot.loop.run_until_complete(main())
            except KeyboardInterrupt:
                write_csv(watching_file, watching)
                print("Bot stopped by user.")
                break
            except Exception as e:
                write_csv(watching_file, watching)
                if datetime.now() - last_exception < timedelta(minutes=5):
                    back_off = min(back_off * 1.5, 60*10)  # Cap backoff at 10 minutes
                print(f"Error in main loop: {e}")
                bot.loop.run_until_complete(bot.send_message(TG_ADMIN, f"Error in main loop: {e}"))
                print("Restarting bot...")
                time.sleep(back_off)
