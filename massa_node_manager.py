from pathlib import Path
from env import dot
from env import data_dir
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
        print("Massa node process killed successfully.")
    except subprocess.CalledProcessError:
        print("No Massa node process found to kill.")

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
    print(f"Latest version: {version}, file: {file_name}")
    file_url = f"https://github.com/massalabs/massa/releases/download/{version}/{file_name}"
    checksum_url = f"https://github.com/massalabs/massa/releases/download/{version}/checksums.txt"
    expected_file_hash = await get_checksum(checksum_url, file_name)
    print(f"Checksum for {file_name}: {expected_file_hash}")
    file = data_dir / file_name
    if file.exists():
        file_hash = hashlib.sha256(file.read_bytes()).hexdigest()
        if file_hash == expected_file_hash:
            print(f"File {file_name} already exists and is verified with checksum {file_hash}.")
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
            print(f"Downloaded and verified {file_name} successfully.")
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
    config_files = [
        (dot/"node_config.toml", data_dir/"massa"/"massa-node"/"config"/"config.toml"),
    ]
    print("Deplpying configuration files for Massa node.")
    for src, dest in config_files:
        if not src.exists():
            raise ValueError(f"Source file {src} does not exist.")
        print(f"Copying {str(src)} to {str(dest)}")
        shutil.copy(src, dest)

async def install_massa_node():
    targz, install = await download_massa_node()
    if install:
        await unpack(targz, data_dir)
    await configure_massa_node()

class BackgroundProcess:
    def __init__(self, cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL):
        print(f"Initializing background process with command: {cmd}")
        self.path = Path(cmd[0]).parent
        print(f"Process path set to: {self.path}")
        self.cmd = cmd
        self.process = None
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

    async def start(self):
        old_path = os.getcwd()
        os.chdir(self.path)
        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
        )
        print(f"Started background process with PID {self.process.pid}")
        os.chdir(old_path)

    async def stop(self):
        if self.process and self.process.returncode is None:
            print(f"Terminating background process with PID {self.process.pid}")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError | asyncio.CancelledError:
                print("Force killing process")
                self.process.kill()
                await self.process.wait()

    async def read_output(self, label):
        if not self.process or self.process.returncode is not None:
            raise RuntimeError("Process is not running or has already exited.")
        stream = self.process.stdout
        if not stream:
            raise RuntimeError("Process has no stdout stream.")
        try:
            while True:
                line = await stream.readline()
                if line:
                    print(f"[{label}] {line.decode().rstrip()}")
                else:
                    await asyncio.sleep(0.1)  # Avoid busy waiting
        except asyncio.CancelledError:
            print(f"[{label}] Reading output cancelled.")
        except KeyboardInterrupt:
            print(f"[{label}] Reading output interrupted by user.")
        except Exception as e:
            print(f"[{label}] Exception while reading output: {e}")
            print(f"[{label}] Stream closed.")

@contextlib.asynccontextmanager
async def run_bg_shell(cmd, then=None):
    proc = BackgroundProcess(cmd)
    await proc.start()
    yield
    await proc.stop()
    if then:
        if asyncio.iscoroutinefunction(then):
            await then(proc)
        else:
            then(proc)

@contextlib.asynccontextmanager
async def massa_node():
    await install_massa_node()
    massa_node_path = data_dir / "massa" / "massa-node" / "massa-node"
    if not massa_node_path.exists():
        raise ValueError(f"Massa node executable not found at {massa_node_path}")
    print(f"Running Massa node from {massa_node_path}")
    async with run_bg_shell([str(massa_node_path), "-a", "-p", "password"],
                            then=lambda _: kill_node()):
        print("Massa node is running. Press Ctrl+C to stop.")
        # Keep the main task running to allow background process to run
        yield
