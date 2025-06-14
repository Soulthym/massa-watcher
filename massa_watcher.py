from massa_node_manager import massa_node
from env import data_dir
from env import bot
from env import command
from env import TG_USERNAME
from env import TG_ADMIN

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
@command(cmd="watch", address=address_pat)
async def watch(event, address):
    # Here you would add the logic to start watching the address
    await event.reply(f"Started watching address: {address}")

async def main():
    print("Connected to Telegram as", TG_USERNAME)
    async with massa_node():
        print("Massa node is running. Press Ctrl+C to stop.")
        await bot.send_message(TG_ADMIN, f"Bot started successfully as {TG_USERNAME}.")
        await bot.run_until_disconnected()

if __name__ == "__main__":
    with bot:
        bot.loop.run_until_complete(main())
