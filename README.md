# massa-watcher
A simple monitor for your massa node

## Installation
### WARNING: Do NOT run this bot on the same machine as your Massa node! It will stop your node!
**You can only run one node per machine, and using an existing node is not yet supported!**

### Instructions
On ubuntu:
```bash
git clone https://github.com/Soulthym/massa-watcher.git
cd massa-watcher
./install.sh
```

Then, edit `.envrc` with the following values:
```bash
export VIRTUAL_ENV=".venv"
uv venv
export TG_API_ID="$api_id" # get it from https://my.telegram.org/apps
export TG_API_HASH="$api_hash" # get it from https://my.telegram.org/apps
export TG_BOT_TOKEN="$bot_token" # get it from @BotFather
export TG_USERNAME="$bot_username" # your bot username without @
export TG_ADMIN="$admin" # your own username without @
```

Enable direnv:
```bash
direnv allow
```

Then, run the bot:
```bash
uv run massa_watcher.py
```

### Optional configuration
If your bot will restart often, instead of clogging official bootstrap servers,
you can make your own node the default bootstrap node.

During development, this will help a lot with restarting the bot quickly without waiting for the bootstrap servers to respond, which can take several hours if they are overloaded or if you hit your rate limit.

To do this:
1) find your node id by running `get_status` in your node's client. It will be near the top of the response.
2) find your node's IP address by running `ip a s` in the terminal on the node's machine (in the shell, not in the massa client)
3) find your bot's IP address by running `ip a s` in the terminal on the bot's machine (in the shell)
4) whitelist your bot's IP in your node's client with `node_bootstrap_whitelist add $YOUR_BOT_IP`.
5) edit `node_config.toml` with the following values:

```toml
[api]
    max_addresses_datastore_keys_query = 1000

[bootstrap]
    # list of bootstrap (ip, node id)
    bootstrap_list = [
        ["$YOUR_NODE_IP:31245", "$YOUR_NODE_ID"],
    ]
```

Then, restart the bot with `Ctrl+C` and:
```bash
uv run massa_watcher.py
```

## Features
- `/start|help` - Start menu
- `/watch address` - Start monitoring a Massa address for missed blocks
- `/unwatch address` - Stop monitoring a Massa address for missed blocks
- `/status` - Show the current status of your watched Massa addresses
