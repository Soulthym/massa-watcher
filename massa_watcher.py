from massa_node_manager import massa_node
from env import data_dir
from env import bot
from env import command
from env import TG_USERNAME
from env import TG_ADMIN

def read_csv(file_path):
    """Read a CSV file and return a list of dictionaries."""
    import csv
    with file_path.open("r") as f:
        reader = csv.DictReader(f)
        res = {}
        for row in reader:
            key = int(row["user"])
            if key not in res:
                res[key] = []
            res[key].append(row["address"])
        return res

def write_csv(file_path, data):
    """Write a list of dictionaries to a CSV file."""
    import csv
    with file_path.open("w", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["user", "address"])
        writer.writeheader()
        for user, addresses in data.items():
            for address in addresses:
                writer.writerow({"user": int(user), "address": address})

watching_file = data_dir / "watching.csv"
watching: dict[int, list[str]] = read_csv(watching_file)
if not watching_file.exists():
    write_csv(watching_file, {})

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
        f"Available commands:",
        "/start - Start the bot",
        "/help - Show available commands",
        "/watch <address> - Track a staking massa address",
        "/unwatch <address> - Stop tracking a staking massa address",
    ])
    await event.reply("\n".join(msg))

address_pat = r"AU[1-9A-HJ-NP-Za-hj-np-z]{51}"
@command(address=address_pat)
async def watch(event, address):
    # Here you would add the logic to start watching the address
    user = event.sender_id
    if user not in watching:
        watching[user] = []
    if address in watching[user]:
        return await event.reply(f"You are already watching address: {address}")
    watching[user].append(address)
    await event.reply(f"Started watching address: {address}")

async def main():
    print("Connected to Telegram as", TG_USERNAME)
    try:
        async with massa_node():
            print("Massa node is running. Press Ctrl+C to stop.")
            await bot.send_message(TG_ADMIN, f"Bot started successfully as {TG_USERNAME}.")
            await bot.run_until_disconnected()  # type: ignore
    except Exception as e:
        write_csv(watching_file, watching)

if __name__ == "__main__":
    with bot:
        bot.loop.run_until_complete(main())
