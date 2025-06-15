from telethon import TelegramClient
from telethon import Button
from telethon import events
from inspect import signature

from pathlib import Path
import os

dot = Path(__file__).parent
data_dir = dot / "data"
data_dir.mkdir(exist_ok=True, parents=True)
session_dir = data_dir / "sessions"
session_dir.mkdir(exist_ok=True, parents=True)

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_USERNAME = os.environ["TG_USERNAME"].lstrip("@")
TG_ADMIN = os.environ["TG_ADMIN"].lstrip("@")

bot = TelegramClient(session_dir/TG_USERNAME, TG_API_ID, TG_API_HASH).start(bot_token=TG_BOT_TOKEN)

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

def wrap_spaces(**kw):
    result = []
    endings = []
    for i, (key, value) in enumerate(kw.items()):
        if i == len(kw) - 1:
            result.append(fr"(?:\s+(?P<{key}>{value}))?")
            continue
        result.append(fr"(?:\s+(?P<{key}>{value})")
        endings.append(r")?")
    endings.reverse()
    return "".join(result) + "".join(endings)

# def wrap_spaces(arg, *args):
#     if not arg:
#         return arg
#     if not args:
#         return fr"(?:\s+{arg})?"
#     return fr"(?:\s+{arg}{wrap_spaces(*args)})?"
def make_pattern(name, **kw):
    pattern = wrap_spaces(**kw)
    return r"(?im)^/(?P<cmd>{name})(?:@{username})?{pattern}".format(
        name=name, username=TG_USERNAME, pattern=pattern)

commands = {}
def command(f = None, /, cmd=None, **arg_specs):
    if f is None:
        return lambda f: command(f, cmd=cmd, **arg_specs)
    if not cmd:
        cmd = f.__name__
    sig = signature(f)
    arg_specs = {key: arg_specs[key] if key in arg_specs else r"\S+" for key in sig.parameters if key not in ("event", "cmd")}
    pattern = make_pattern(cmd, **arg_specs)
    print(f"Command {cmd!r} registered with pattern r'{pattern}'")
    @bot.on(events.NewMessage(pattern=pattern))
    async def handler(event):
        if not event.is_private:
            return await event.reply("I can only respond to private messages.", buttons=[Button.url("Send me a message", f"https://t.me/{TG_USERNAME}?start=start")])
        f_kw = {k: v for k, v in event.pattern_match.groupdict().items() if k in arg_specs and v is not None}
        if "cmd" not in sig.parameters:
            f_kw.pop("cmd", None)
        for key, param in sig.parameters.items():
            if key == "cmd":
                got_cmd = event.pattern_match.group("cmd")
                if got_cmd is None:
                    raise ValueError(f"Command {cmd!r} not found in event pattern match.")
                f_kw[key] = got_cmd
                continue
            if key == "event":
                continue
            if key not in f_kw:
                if param.default is not param.empty:
                    f_kw[key] = param.default
                    continue
                return await event.reply(f"Missing argument: {key}\nExpected: {arg_specs[key]}")
            if param.annotation is not param.empty:
                f_kw[key] = param.annotation(f_kw[key])
        bound_args = sig.bind(event, **f_kw)
        return await f(*bound_args.args, **bound_args.kwargs)
    if cmd in commands:
        raise ValueError(f"Command {cmd!r} is already registered as {commands[cmd]!r}")
    commands[cmd] = (sig, arg_specs, handler)
    return handler

def build_command_list():
    return [
        "/start - Start the bot",
        "/help - Show available commands",
        "/watch <address> - Track a staking massa address",
        "/unwatch <address> - Stop tracking a staking massa address",
    ]

def build_default_commands():
    @command(cmd="start|help")
    async def start(event, cmd):
        name = get_name(event.sender, prefix=", ")
        msg = []
        if cmd == "start":
            msg.extend([f"Hello{name}!", "I am your Massa node watcher bot.", ""])
        msg.extend([
            "Available commands:",
            *build_command_list(),
        ])
        await event.reply("\n".join(msg))

