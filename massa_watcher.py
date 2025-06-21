from telethon import Button

from massa_node_manager import run_massa_node
from massa_node_manager import massa_api
from env import build_default_commands
from env import TG_USERNAME
from env import TG_ADMIN
from env import noop_btn
from env import loglevel
from env import data_dir
from env import command
from env import bot
from env import log

from traceback import format_exc
from datetime import timedelta
from datetime import datetime
from itertools import batched

import asyncio
import time
import csv

time_offset = timedelta(minutes=5)
api_started = False

class User:
    def __init__(self, user_id, notify_ok = False, notify_nok = True):
        self.id: int = user_id
        self.notify_ok: bool = notify_ok
        self.notify_nok: bool = notify_nok

    def __str__(self):
        return f"User(id={self.id}, notify_ok={self.notify_ok}, notify_nok={self.notify_nok})"

class Watched:
    def __init__(self, address: str, *users: User):
        self.address = address
        self.users: dict[int, User] = {user.id: user for user in users}
        self.timestamp: int = int(datetime.now().timestamp() - time_offset.total_seconds())
    def __contains__(self, uid: int) -> bool:
        return uid in self.users
    async def notify_nok(self, info):
        for uid, user in self.users.items():
            address_status = message_notification(info)
            if address_status is not None:
                await bot.send_message(uid, address_status, parse_mode="html")
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
            uid = int(row["user"])
            notify_ok: bool = row.get("notify_ok", "False").lower() == "true"
            notify_nok: bool = row.get("notify_nok", "True").lower() == "true"
            user = User(uid, notify_ok=notify_ok, notify_nok=notify_nok)
            if uid not in rev:
                rev[uid] = []
            rev[uid].append(key)
            if uid not in res[key]:
                res[key].users[uid] = user
        return res, rev

def write_csv(file_path, data: Watching):
    """Write a list of dictionaries to a CSV file."""
    log(f"Writing {len(data)} entries to {file_path}")
    with file_path.open("w+", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["address", "user", "notify_ok", "notify_nok"])
        writer.writeheader()
        for address, watched in data.items():
            log(f"Writing address: {address} with users: {watched.users}")
            for uid, user in watched.users.items():
                writer.writerow({"address": address, "user": str(uid), "notify_ok": str(user.notify_ok), "notify_nok": str(user.notify_nok)})

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
    uid = event.sender_id
    info = await get_addresses_info(address)
    if not api_started:
        return await event.reply("API is still starting. Please try again in a few minutes.")
    if not info:
        return await event.reply(f"I could not find any information for this address. Please check if it is a valid staking address.\n\nIf you think this is an error, please contact @{TG_ADMIN}.")
    if address not in watching:
        watching[address] = Watched(address)
    if uid not in rev_watching:
        rev_watching[uid] = []
    if uid in watching[address]:
        return await event.reply(f"You are already watching address: {address}")
    watching[address].users[uid] = User(uid)
    rev_watching[uid].append(address)
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
    uid = event.sender_id
    if address not in watching:
        return await event.reply("You are not watching any addresses.")
    if uid not in watching[address]:
        return await event.reply(f"You are not watching address: {address}")
    if uid in rev_watching and address in rev_watching[uid]:
        rev_watching[uid].remove(address)
    watching[address].users.pop(uid, None)
    await event.reply(f"Stopped watching address: {address}")

@command(index=r"\d+", event_btn=True)
async def status(event, index: int = 0):
    """\
    Show the status of your watched addresses.
    Usage: /status [index]
    """
    uid = event.sender_id
    addresses = rev_watching.get(uid, [])
    if not addresses:
        return await event.reply("You are not watching any addresses.\nUse /watch <address> to start watching a staking address.")
    index = min(index, len(addresses) - 1)
    info = await get_addresses_info(addresses[index])
    if not api_started:
        return await event.reply("API is still starting. Please try again in a few minutes.")
    if not info:
        return await event.reply("No information available for your watched addresses.")
    assert len(info) == 1, f"Expected exactly one address info, got {len(info)}"
    msg = message_notification(info[0])
    buttons = []
    if len(addresses) > 1:
        prev_idx = max(0, index - 1)
        next_idx = min(index + 1, len(addresses) - 1)
        max_idx = len(addresses) - 1
        buttons.append([
            Button.inline(f"⏮️ 1", data=f"status 0") if index > 0 else noop_btn,
            Button.inline(f"◀️ {prev_idx + 1}", data=f"status {prev_idx}") if index > 0 else noop_btn,
            Button.inline(f"{index + 1}/{max_idx + 1}", data=f"noop"),
            Button.inline(f"{next_idx + 1} ▶️", data=f"status {next_idx}") if index < max_idx else noop_btn,
            Button.inline(f"{max_idx + 1} ⏭️", data=f"status {max_idx}") if index < max_idx else noop_btn,
        ])
    return await event.reply(
        msg if msg else "No address found?.",
        buttons=buttons or None,
        parse_mode="html")

async def get_addresses_info(*addresses: str):
    global api_started
    if len(addresses) < 10:
        log("Fetching addresses info for:", addresses)
    else:
        log(f"Fetching info for {len(addresses)} addresses, this may take a while...")
    result = await massa_api("get_addresses", list(addresses))
    if not result:
        log(loglevel.error, "No addresses info returned from API.")
        return None
    print(f"API started: {api_started}")
    if not api_started:
        log("API started successfully.")
        await bot.send_message(TG_ADMIN, "API started successfully.")
        api_started = True
    return result

def should_notify_nok(info) -> bool:
    """Check if the address has missed blocks."""
    if not info:
        return False
    address = info["address"]
    watched = watching[address]
    if watched.timestamp > int(datetime.now().timestamp() - time_offset.total_seconds()):
        # If the address was recently checked, do not notify
        return False
    for cycle in sorted(info.get("cycle_infos", []), key=lambda x: x["cycle"])[-2:]:  # Check the last two cycles
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
    cutoff = int(datetime.now().timestamp() - time_offset.total_seconds())
    filtered = {k: v for k, v in watching.items() if v.timestamp < cutoff}
    for addresses in batched(filtered, 1000):
        info = await get_addresses_info(*addresses)
        if not info:
            log(loglevel.warn, "No addresses info returned.")
            continue
        for i in info:
            if not should_notify_nok(i):
                continue
            address = i["address"]
            await watching[address].notify_nok(i)
        await asyncio.sleep(1)  # Rate limit to avoid overwhelming the node

async def on_disconnect():
    global api_started
    api_started = False  # Reset API status on disconnect

async def main():
    log("Connected to Telegram as", TG_USERNAME)
    try:
        async with run_massa_node(notify_missed_blocks, on_disconnect=on_disconnect):
            await bot.send_message(TG_ADMIN, f"Bot started successfully as {TG_USERNAME}.")
            await bot.run_until_disconnected()  # type: ignore
    except KeyboardInterrupt:
        log(loglevel.warn, "Bot stopped by user.")
        await bot.send_message(TG_ADMIN, "Bot stopped by user.")
        write_csv(watching_file, watching)
    except Exception as e:
        log(loglevel.error, f"Error in main: {e}\n{format_exc()}")
        await bot.send_message(TG_ADMIN, f"Error in main: {e}\n{format_exc()}")
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
                log(loglevel.error, f"Error in main loop: {e}\n{format_exc()}")
                bot.loop.run_until_complete(bot.send_message(TG_ADMIN, f"Error in main loop: {e}\n{format_exc()}"))
                log("Restarting bot...")
                time.sleep(back_off)
