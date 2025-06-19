from env import data_dir
from env import loglevel
from env import log
from env import dot
from keep_alive import BGProcess

from pathlib import Path
from pprint import pp
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any, overload
from typing import AsyncContextManager
import contextlib
import platform
import hashlib
import tarfile
import asyncio
import aiohttp
import shutil
import os

def kill_node():
    """Kill the Massa node process if it is running using pkill."""
    import subprocess
    try:
        subprocess.run(["pkill", "-f", "massa-node"], check=True)
        log("Massa node process killed successfully.")
    except subprocess.CalledProcessError:
        log("No Massa node process found to kill.")

kill_node()  # Ensure any previous node is killed before starting a new one

platforms = {
    "aarch64": "linux_arm64",
    "x86_64": "linux",
}

async def get_platform() -> str:
    """Get the platform name for the Massa node."""
    arch = platform.machine()
    if arch in platforms:
        return platforms[arch]
    else:
        raise ValueError(f"Unsupported architecture: {arch}")

async def massa_get_latest_release():
    platform_name = await get_platform()
    url = "https://api.github.com/repos/massalabs/massa/releases"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession() as session:
        data = await session.get(url, headers=headers)
        releases = {d['name']: [a["name"] for a in d["assets"]] for d in await data.json() if d['name'].startswith('MAIN.')}
    for version, files in sorted(releases.items(), key=lambda x: tuple(map(int, x[0].split('.')[1:])), reverse=True):
        for file in files:
            if file.endswith(platform_name + ".tar.gz"):
                return (version, file)
    else:
        raise ValueError(f"No suitable release found for platform {platform_name}")

async def get_checksum(checksum_url: str, file: str) -> str:
    """Get the checksum for the specified file from the checksums URL."""
    async with aiohttp.ClientSession() as session:
        async with session.get(checksum_url) as response:
            if response.status != 200:
                raise ValueError(f"Failed to fetch checksums: {response.status}")
            checksums = await response.text()
            for line in checksums.splitlines():
                if file in line:
                    return line.split()[0]
    raise ValueError(f"Checksum for {file} not found in {checksum_url}")

async def download_massa_node() -> tuple[Path, bool]:
    (version, file_name) = await massa_get_latest_release()
    log(f"Latest version: {version}, file: {file_name}")
    file_url = f"https://github.com/massalabs/massa/releases/download/{version}/{file_name}"
    checksum_url = f"https://github.com/massalabs/massa/releases/download/{version}/checksums.txt"
    expected_file_hash = await get_checksum(checksum_url, file_name)
    log(f"Checksum for {file_name}: {expected_file_hash}")
    file = data_dir / file_name
    if file.exists():
        file_hash = hashlib.sha256(file.read_bytes()).hexdigest()
        if file_hash == expected_file_hash:
            log(f"File {file_name} already exists and is verified with checksum {file_hash}.")
            return file, False
    # Otherwise, download the file
    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as response:
            if response.status != 200:
                raise ValueError(f"Failed to download file: {response.status}")
            content = await response.read()
            file_hash = hashlib.sha256(content).hexdigest()
            if file_hash != expected_file_hash:
                raise ValueError(f"Checksum mismatch: expected {expected_file_hash}, got {file_hash}")
            log(f"Downloaded and verified {file_name} successfully.")
            with file.open("wb") as f:
                f.write(content)
                return file, True

async def unpack(targz: Path, dest: Path):
    if not targz.exists():
        raise ValueError(f"File {targz} does not exist.")
    with tarfile.open(targz, "r:gz") as tar:
        tar.extractall(path=dest)
        log(f"Unpacked {targz} to {dest}")

async def configure_massa_node():
    config_files = [
        (dot/"node_config.toml", data_dir/"massa"/"massa-node"/"config"/"config.toml"),
    ]
    log("Deploying configuration files for Massa node.")
    for src, dest in config_files:
        if not src.exists():
            raise ValueError(f"Source file {src} does not exist.")
        log(f"Copying {str(src)} to {str(dest)}")
        shutil.copy(src, dest)

async def install_massa_node():
    targz, install = await download_massa_node()
    if install:
        await unpack(targz, data_dir)
    await configure_massa_node()

async def massa_api(method: str, *params: str):
    async with aiohttp.ClientSession() as session:
        url = "http://localhost:33035"
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": [*params]
        }
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                raise ValueError(f"Failed to get addresses info: {response.status}")
            data = await response.json()
            result = data.get("result", None)
            return result

async def check_massa_alive() -> bool:
    """Check if the Massa node is alive by querying its API."""
    try:
        result = await massa_api("get_status")
        if not result:
            log("Massa node is not running or returned no status.")
            return False
        if "connected_nodes" in result:
            return len(result["connected_nodes"]) > 0
        return False
    except Exception as e:
        log(f"Error checking Massa node status: {e}")
        return False

@contextlib.asynccontextmanager
async def run_massa_node():
    await install_massa_node()
    massa_node_path = data_dir / "massa" / "massa-node" / "massa-node"
    if not massa_node_path.exists():
        raise ValueError(f"Massa node executable not found at {massa_node_path}")
    log(f"Running Massa node from {massa_node_path}")
    async with BGProcess([str(massa_node_path), "-a", "-p", "password"],
                         stdin=asyncio.subprocess.DEVNULL,
                         stdout=asyncio.subprocess.PIPE,
                         stderr=asyncio.subprocess.PIPE,
                         check_alive=check_massa_alive).keep_alive():
        log("Massa node is running. Press Ctrl+C to stop.")
        # Keep the main task running to allow background process to run
        yield
