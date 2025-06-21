from keep_alive import BGProcess
from env import data_dir
from env import log
from env import dot

from collections.abc import Coroutine
from collections.abc import Callable
from traceback import format_exc
from pathlib import Path
import contextlib
import platform
import hashlib
import tarfile
import asyncio
import aiohttp
import shutil

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
        j = await data.json()
        releases = {d['name']: [a["name"] for a in d["assets"]] for d in j if d['name'].startswith('MAIN.')}
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

async def massa_api(method: str, *params):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            url = "http://localhost:33035"
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": [*params]
            }
            async with session.post(url, json=payload, headers=headers, timeout=5) as response:
                if response.status != 200:
                    raise ValueError(f"Failed to get addresses info: {response.status}")
                data = await response.json()
                result = data.get("result", None)
                return result
    except Exception as e:
        log(f"Error in massa_api call: {e}")
        return None

async def check_massa_alive() -> bool:
    """Check if the Massa node is alive by querying its API."""
    for _ in range(3):
        try:
            result = await massa_api("get_status")
            if not result:
                log("Massa node is not running or returned no status.")
                return False
            if "connected_nodes" in result:
                return len(result["connected_nodes"]) > 0
            return False
        except asyncio.TimeoutError:
            log("Massa node API request timed out.")
            await asyncio.sleep(5)  # Wait before retrying
        except Exception as e:
            log(f"Error checking Massa node status: {e}\n{format_exc()}")
            return False
    return False

@contextlib.asynccontextmanager
async def run_massa_node(*background_tasks: Callable[[], Coroutine], on_disconnect: Callable[[], Coroutine] | None = None):
    await install_massa_node()
    massa_node_path = data_dir / "massa" / "massa-node" / "massa-node"
    if not massa_node_path.exists():
        raise ValueError(f"Massa node executable not found at {massa_node_path}")
    log(f"Running Massa node from {massa_node_path}")
    async with BGProcess([str(massa_node_path), "-a", "-p", "password"],
                         check_alive=check_massa_alive,
                         debug="Massa Node",
                         interval=60, background_tasks=background_tasks,
                         on_disconnect=on_disconnect,
                         ).keep_alive():
        log("Massa node is running. Press Ctrl+C to stop.")
        # Keep the main task running to allow background process to run
        yield
