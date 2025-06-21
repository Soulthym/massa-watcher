from telethon import TelegramClient
from telethon import Button
from telethon import events

from datetime import timedelta
from datetime import datetime
from inspect import Signature
from inspect import signature
from textwrap import dedent
from typing import Callable
from typing import Literal
from pathlib import Path
from typing import Any

import itertools
import sys
import os

time_offset = timedelta(minutes=5)

dot = Path(__file__).parent
data_dir = dot / "data"
data_dir.mkdir(exist_ok=True, parents=True)
session_dir = data_dir / "sessions"
session_dir.mkdir(exist_ok=True, parents=True)
log_file = data_dir / "log.txt"
log_file.touch(exist_ok=True)

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_USERNAME = os.environ["TG_USERNAME"].lstrip("@")
TG_ADMIN = os.environ["TG_ADMIN"].lstrip("@")

bot = TelegramClient(session_dir/TG_USERNAME, TG_API_ID, TG_API_HASH).start(bot_token=TG_BOT_TOKEN)

class Loglevel:
    debug = "DEBUG"
    info = "INFO"
    warn = "WARNING"
    error = "ERROR"
    critical = "CRITICAL"

loglevel = Loglevel()

def log(*a, level=loglevel.info, **kw):
    """Log messages to the log file."""
    prefix = datetime.now().strftime(f"{level}[%Y-%m-%d %H:%M:%S]")
    default_f = kw.pop("file", sys.stderr)  # Remove file from kwargs, we handle it ourselves
    with open(log_file, "a", encoding="utf-8") as f:
        print(prefix, *a, **kw, file=f, flush=True)
    print(prefix, *a, **kw, file=default_f, flush=True)

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

def wrap_spaces(**kw: str) -> str:
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


def make_pattern(name: str, kind: Literal["new", "btn"], **kw: str) -> str:
    pattern = wrap_spaces(**kw)
    if kind == "new":
        return r"(?im)^/(?P<cmd>{name})(?:@{username})?{pattern}".format(
            name=name, username=TG_USERNAME, pattern=pattern)
    elif kind == "btn":
        return r"(?im)^(?P<cmd>{name}){pattern}".format(
        name=name, pattern=pattern)
    else:
        raise ValueError("Either event_new or event_btn must be True.")

type Func = Callable[..., Any]
type CmdInfo = tuple[Signature, dict[str, str], Func, list[Func]]
commands: dict[str, CmdInfo] = {}
_CMDS_HANDLER_IDX = 3

def register_cmd(cmd, sig, arg_specs, f, handler):
    if cmd not in commands:
        commands[cmd] = (sig, arg_specs, f, [handler])
    else:
        commands[cmd][_CMDS_HANDLER_IDX].append(handler)

def command(f = None, /, cmd=None, event_new=True, event_btn=False, **arg_specs):
    if f is None:
        return lambda f: command(f, cmd=cmd, event_new=event_new, event_btn=event_btn, **arg_specs)
    if not cmd:
        cmd = f.__name__
    sig = signature(f)
    arg_specs = {key: arg_specs[key] if key in arg_specs else r"\S+" for key in sig.parameters if key not in ("event", "cmd")}
    async def bind_args(event, handler):
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
                return await event.reply(f"Missing argument: {key}\n\n{doc(cmd, (sig, arg_specs, f, handler))}")
            if param.annotation is not param.empty:
                f_kw[key] = param.annotation(f_kw[key])
        return sig.bind(event, **f_kw)
    if cmd in commands:
        raise ValueError(f"Command {cmd!r} is already registered as {commands[cmd]!r}")
    if event_new:
        pattern = make_pattern(cmd, kind="new", **arg_specs)
        print(f"Command.NewMessage {cmd!r} registered with pattern r'{pattern}'")
        @bot.on(events.NewMessage(pattern=pattern))
        async def new_handler(event):
            if not event.is_private:
                return await event.reply("I can only respond to private messages.", buttons=[Button.url("Send me a message", f"https://t.me/{TG_USERNAME}?start=start")])
            bound = await bind_args(event, new_handler)
            return await f(*bound.args, **bound.kwargs)
        register_cmd(cmd, sig, arg_specs, f, new_handler)
    if event_btn:
        pattern = make_pattern(cmd, kind="btn", **arg_specs)
        print(f"Command.CallbackQuery {cmd!r} registered with pattern r'{pattern}'")
        @bot.on(events.CallbackQuery(pattern=pattern))
        async def btn_handler(event):
            event.reply = event.edit
            bound = await bind_args(event, btn_handler)
            return await f(*bound.args, **bound.kwargs)
        register_cmd(cmd, sig, arg_specs, f, btn_handler)
    return f

def build_command_usage(cmd: str, info: CmdInfo) -> str:
    sig, _, f, _ = info
    args = " ".join(name for name, param in sig.parameters.items() if name not in ("event", "cmd") if param.default is param.empty)
    opt_args = " ".join(f"[{name}]" for name, param in sig.parameters.items() if name not in ("event", "cmd") if param.default is not param.empty)
    args = " ".join(a for a in (args, opt_args) if a)
    if not args:
        return f"/{cmd}"
    return f"/{cmd} {args}"

def doc(cmd: str, info: CmdInfo) -> str:
    _, _, f, _ = info
    docstr = dedent(f.__doc__ or "").strip()
    return build_command_usage(cmd, info) + (" - " + docstr if docstr else "")

def doc_line(cmd: str, info: CmdInfo) -> str:
    _, _, f, _ = info
    docstr = dedent(f.__doc__ or "").strip().partition("\n")[0]
    return build_command_usage(cmd, info) + (" - " + docstr if docstr else "")

def build_command_list(cmd: str):
    default_commands = [
        "/start - " + ("Show this menu" if cmd == "start" else "Start menu"),
        "/help - " + ("Show this menu" if cmd == "help" else "Show available commands"),
    ]
    return itertools.chain(
        (d for d in default_commands),
        (doc_line(cmd, info) for cmd, info in commands.items() if cmd != "start|help"),
    )

def build_default_commands():
    @command(cmd="start|help")
    async def start(event, cmd):
        name = get_name(event.sender, prefix=", ")
        msg = []
        if cmd == "start":
            msg.extend([f"Hello{name}!", "I am your Massa node watcher bot.", ""])
        msg.extend([
            "<b>Available commands:</b>",
            "<i><b>Format:</b>",
            "/&lt;command&gt; &lt;args&gt; ... [&lt;optional_args&gt; ...] - description</i>",
            "",
            *build_command_list(cmd),
            "",
            f"Made by @{TG_ADMIN}",
            f"Source: https://github.com/Soulthym/massa-watcher",
        ])
        await event.reply("\n".join(msg), parse_mode="html", link_preview=False)

noop_btn = Button.inline(" ", data="noop")
@command(event_new=False, event_btn=True)
async def noop(event):
    raise events.StopPropagation  # This command does nothing, just to prevent errors with empty buttons
