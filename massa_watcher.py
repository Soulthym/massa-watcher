import contextlib
from pathlib import Path
from pprint import pp
import platform
import tarfile
import asyncio
import aiohttp
import shutil
import signal
import sys
import os

def kill_node():
    """Kill the Massa node process if it is running using pkill."""
    import subprocess
    try:
        subprocess.run(["pkill", "-f", "massa-node"], check=True)
        print("Massa node process killed successfully.")
    except subprocess.CalledProcessError:
        print("No Massa node process found to kill.")

kill_node()  # Ensure any previous node is killed before starting a new one
dot = Path(__file__).parent
data_dir = dot / "data"
# shutil.rmtree(data_dir, ignore_errors=True)
data_dir.mkdir(exist_ok=True, parents=True)
tmp_dir = data_dir / "tmp"
shutil.rmtree(tmp_dir, ignore_errors=True)
tmp_dir.mkdir(exist_ok=True, parents=True)

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
    print(f"Releases for {platform_name}:")
    pp(releases)
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
    (version, file) = await massa_get_latest_release()
    print(f"Latest version: {version}, file: {file}")
    file_url = f"https://github.com/massalabs/massa/releases/download/{version}/{file}"
    checksum_url = f"https://github.com/massalabs/massa/releases/download/{version}/checksums.txt"
    checksum = await get_checksum(checksum_url, file)
    print(f"Checksum for {file}: {checksum}")
    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as response:
            if response.status != 200:
                raise ValueError(f"Failed to download file: {response.status}")
            content = await response.read()
            import hashlib
            sha256 = hashlib.sha256(content).hexdigest()
            if sha256 != checksum:
                raise ValueError(f"Checksum mismatch: expected {checksum}, got {sha256}")
            print(f"Downloaded and verified {file} successfully.")
            file = data_dir / file
            if file.exists():
                return file, False
            with file.open("wb") as f:
                f.write(content)
                return file, True

async def unpack(targz: Path, dest: Path):
    if not targz.exists():
        raise ValueError(f"File {targz} does not exist.")
    with tarfile.open(targz, "r:gz") as tar:
        tar.extractall(path=dest)
        print(f"Unpacked {targz} to {dest}")

async def configure_massa_node():
    # copy the config file to the destination
    shutil.copy(dot/"node_config.toml",
                data_dir/"massa"/"massa-node"/"config"/"config.toml")

async def install_massa_node():
    targz, install = await download_massa_node()
    if install:
        await unpack(targz, data_dir)
    await configure_massa_node()

class BackgroundProcess:
    def __init__(self, cmd):
        print(f"Initializing background process with command: {cmd}")
        self.path = Path(cmd[0]).parent
        print(f"Process path set to: {self.path}")
        self.cmd = cmd
        self.process = None

    async def start(self):
        old_path = os.getcwd()
        os.chdir(self.path)
        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        print(f"Started background process with PID {self.process.pid}")
        os.chdir(old_path)

    async def stop(self):
        if self.process and self.process.returncode is None:
            print(f"Terminating background process with PID {self.process.pid}")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                print("Force killing process")
                self.process.kill()
                await self.process.wait()

    async def read_output(self, label):
        stream = self.process.stdout
        try:
            while True:
                line = await stream.readline()
                if line:
                    print(f"[{label}] {line.decode().rstrip()}")
                else:
                    await asyncio.sleep(0.1)  # Avoid busy waiting
        except Exception as e:
            print(f"[{label}] Exception while reading output: {e}")
            print(f"[{label}] Stream closed.")

@contextlib.asynccontextmanager
async def run_bg(cmd):
    proc = BackgroundProcess(cmd)
    await proc.start()
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        print("Main task running. Press Ctrl+C to stop.")
        await proc.read_output("Massa Node Output")
        await stop_event.wait()
        yield
    finally:
        await proc.stop()
        print("Shutdown complete.")
        kill_node()

async def main():
    # Replace with the executable you want to run, e.g. ["my_program", "arg1"]
    # Handle shutdown
    await install_massa_node()
    massa_node_path = data_dir / "massa" / "massa-node" / "massa-node"
    if not massa_node_path.exists():
        raise ValueError(f"Massa node executable not found at {massa_node_path}")
    print(f"Running Massa node from {massa_node_path}")
    async with run_bg([str(massa_node_path), "-a", "-p", "password"]):
        print("Massa node is running in the background.")
        # Keep the main task running to allow background process to run
        await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
