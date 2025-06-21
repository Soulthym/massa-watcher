from env import time_offset
from env import loglevel
from env import log

from contextlib import asynccontextmanager
from traceback import format_exc
from datetime import datetime
from typing import Coroutine
from typing import Callable
from pathlib import Path

import asyncio
import abc
import os

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
    async def read_output(self, label: str, stream_name: str):
        """Read output from the background task."""
        pass

async def atry(f, *args, **kwargs):
    try:
        return await f(*args, **kwargs)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print(f"Cancelled {f.__name__} task.")
        raise
    except Exception as e:
        log(f"Error in {f.__name__}: {e}\n{format_exc()}")

class KeepAlive(BGTask):
    def __init__(self, check_alive, debug="", interval=10, background_tasks=(), on_disconnect=None):
        self.debug = debug
        self.interval = interval
        self.check_alive = check_alive
        self.last_alive = datetime.now() - time_offset
        self.started = False
        self.background_tasks: tuple[Callable[[], Coroutine]] = background_tasks
        self.on_disconnect: Callable[[], Coroutine] | None = on_disconnect

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
                await self.wait_for_live_signal()
                log("Got a live signal, starting keep alive loop.")
                await self.wait_for_lost_signal()
                log("Live signal lost, restarting background task.")
                await self.stop()
            except (asyncio.CancelledError, KeyboardInterrupt):
                print("Keep alive cancelled.")
                await self.stop()
                raise
            except Exception as e:
                print(f"Keep alive encountered an error: {e}\n{format_exc()}")
                await asyncio.sleep(self.interval)

    async def wait_for_live_signal(self):
        while True:
            self.started = await self.check_alive()
            if self.started:
                print("Live signal received.")
                break
            print("Waiting for live signal...")
            await asyncio.sleep(self.interval)
        self.last_alive = datetime.now()

    async def wait_for_lost_signal(self):
        while self.started:
            self.started = await self.check_alive()
            if not self.started:
                print("Lost signal, restarting background tasks...")
                break
            print("Waiting for lost signal...")
            self.last_alive = datetime.now()
            for task in self.background_tasks:
                await task()
            await asyncio.sleep(self.interval)

class BGProcess(KeepAlive):
    def __init__(self, cmd, **kwargs):
        super().__init__(**kwargs)
        log(f"Initializing background process with command: {cmd}")
        self.path = Path(cmd[0]).parent
        log(f"Process path set to: {self.path}")
        self.cmd = cmd
        self.process = None
        self.reader_tasks = []

    async def start(self):
        self.stdin = asyncio.subprocess.DEVNULL
        if self.debug:
            self.stdout, self.stderr = (asyncio.subprocess.PIPE,) * 2
        else:
            self.stdout, self.stderr = (asyncio.subprocess.DEVNULL,) * 2
        old_path = os.getcwd()
        os.chdir(self.path)
        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
        )
        if self.debug:
            self.reader_tasks = [
                    asyncio.create_task(self.read_output(self.debug, "stdout")),
                    asyncio.create_task(self.read_output(self.debug, "stderr"))
            ]
        log(f"Started background process with PID {self.process.pid}")
        os.chdir(old_path)

    async def stop(self):
        await asyncio.sleep(10) # Allow some time for the process to finish if it just crashed
        if self.process and self.process.returncode is None:
            log(f"Terminating background process with PID {self.process.pid}")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                log(loglevel.error, "Force killing process")
                self.process.kill()
                await self.process.wait()
                log(f"Background process with PID {self.process.pid} terminated.")
        if self.debug:
            for task in self.reader_tasks:
                log(f"Cancelling reader task {task.get_name()}")
                task.cancel(f"Stopping reader")
            self.reader_tasks = []
        if self.on_disconnect:
            log("Calling on_disconnect callback.")
            await self.on_disconnect()
        self.process = None
        self.stdin = None
        self.stdout = None
        self.stderr = None

    async def read_output(self, label, stream_name: str):
        print("READING OUTPUT", label, stream_name)
        if not self.process or self.process.returncode is not None:
            raise RuntimeError("Process is not running or has already exited.")

        stream = getattr(self.process, stream_name, None)
        while True:
            try:
                if not stream:
                    raise asyncio.CancelledError(f"{stream_name} stream was closed.")
                line = await stream.readline()
                await asyncio.sleep(.01)  # Yield control to the event loop
                if line:
                    log(f"[{label}] {line.decode().rstrip()}")
                else:
                    raise asyncio.CancelledError("Stream closed.")
            except asyncio.CancelledError as e:
                log(f"[{label}] Reading output cancelled.\n{e}")
                break
            except KeyboardInterrupt:
                log(f"[{label}] Reading output interrupted by user.")
                break
            except Exception as e:
                log(f"[{label}] Exception while reading output: {e}\n{format_exc()}")
                log(f"[{label}] Stream closed.")
