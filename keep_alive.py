from env import loglevel
from env import log

from contextlib import asynccontextmanager
from traceback import format_exc
from pathlib import Path

import asyncio
import time
import abc
import os

start_time = time.time()

async def check_alive():
    # simulate 10 seconds of startup time
    delta = time.time() - start_time
    if delta < 3:
        return False
    if delta < 5:
        return True
    if delta < 7:
        return False
    return False

class BGTask(abc.ABC):
    @abc.abstractmethod
    async def start(self):
        """Start the background task."""
        pass

    @abc.abstractmethod
    async def stop(self):
        """Stop the background task."""
        pass

    @abc.abstractmethod
    async def read_output(self, label):
        """Read output from the background task."""
        pass

class KeepAlive(BGTask):
    def __init__(self, check_alive, interval=10):
        self.interval = interval
        self.check_alive = check_alive
        self.started = False

    @asynccontextmanager
    async def keep_alive(self):
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._keep_alive())
        yield
        print("Stopping keep_alive...")
        task.cancel()

    async def _keep_alive(self):
        self.started = False
        print("Starting keep_alive...")
        while True:
            try:
                await self.start()
                await self.keep_alive_inner(lambda: not self.started)
                log("Got a live signal, starting keep alive loop.")
                await self.keep_alive_inner(lambda: self.started)
                log("Live signal lost, restarting background task.")
                await self.stop()
            except asyncio.CancelledError:
                print("Keep alive cancelled.")
                return
            except KeyboardInterrupt:
                print("Keep alive interrupted by user.")
                return
            except Exception as e:
                print(f"Keep alive encountered an error: {e}\n{format_exc()}")
                await asyncio.sleep(self.interval)

    async def keep_alive_inner(self, condition):
        while condition():
            print(f"Keep alive: {self.started}")
            if condition():
                await asyncio.sleep(self.interval)
            self.started = await self.check_alive()

class BGProcess(KeepAlive):
    def __init__(self, cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **kwargs):
        super().__init__(**kwargs)
        log(f"Initializing background process with command: {cmd}")
        self.path = Path(cmd[0]).parent
        log(f"Process path set to: {self.path}")
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
        log(f"Started background process with PID {self.process.pid}")
        os.chdir(old_path)

    async def stop(self):
        if self.process and self.process.returncode is None:
            log(f"Terminating background process with PID {self.process.pid}")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError | asyncio.CancelledError:
                log(loglevel.error, "Force killing process")
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
                    log(f"[{label}] {line.decode().rstrip()}")
                else:
                    await asyncio.sleep(0.1)  # Avoid busy waiting
        except asyncio.CancelledError:
            log(f"[{label}] Reading output cancelled.")
        except KeyboardInterrupt:
            log(f"[{label}] Reading output interrupted by user.")
        except Exception as e:
            log(f"[{label}] Exception while reading output: {e}\n{format_exc()}")
            log(f"[{label}] Stream closed.")
