import pynvim
import asyncio
import logging
import os
import re
from typing import Callable
from enum import Enum, auto
import msgpack

logger = logging.getLogger("app_logger")

class NvimListenerPlatform(Enum):
    Wsl = 0
    Win = auto()

class NvimInstanceEvent(Enum):
    Started = 0
    Stopped = auto()

CREATE_NO_WINDOW = 0x08000000

WSL_WATCH_DIR = "/tmp"
WSL_PATTERN = re.compile(r"^nvim-win-[0-9]+\.sock$")

WIN_PIPE_DIR = r"\\.\pipe"
WIN_PATTERN = re.compile(r"^nvim-win-[0-9]+$")

class NvimListener():
    def __init__(self, platform: NvimListenerPlatform, socket_path: str):
        self.socket_path = socket_path
        self.platform = platform

    async def listen(self):
        if self.platform == NvimListenerPlatform.Win:
            await self.listen_win()
        if self.platform == NvimListenerPlatform.Wsl:
            await self.listen_wsl()

    async def listen_win(self):
        pass

    async def listen_wsl(self):
        try:
            process = await asyncio.create_subprocess_exec(
                "wsl.exe", "-e", "nc", "-U", self.socket_path,
                stdout=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            logger.info(f"Started listener for {self.socket_path}")
            unpacker = msgpack.Unpacker(raw=False)
            while True:
                chunk = await process.stdout.read(1024) # type: ignore
                if not chunk:
                    break
                unpacker.feed(chunk)
                for msg in unpacker:
                    if isinstance(msg, list) and len(msg) == 3 and msg[0] == 2:
                        method = msg[1]
                        args = msg[2]
                        if method == "mode_change":
                            logger.info(f"{self.socket_path} mode changed to: {args[0]}")
        except Exception as e:
            logger.info(f"Error in {self.socket_path} listener: {e}")
        logger.info(f"Listener for {self.socket_path} stopped")

async def watch_wsl_sockets(callback: Callable[[NvimListenerPlatform, NvimInstanceEvent, str], None]):
    wsl_cmd = (
        f"find {WSL_WATCH_DIR} -maxdepth 1 -type s -printf 'RE_EXIST,%f\\n' && "
        f"stdbuf -oL inotifywait -m -e create -e delete --format '%e,%f' {WSL_WATCH_DIR}"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe", "--", "bash", "-c", wsl_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        logger.error("Error: wsl.exe not found on the Windows PATH.")
        return
    try:
        while True:
            line_bytes = await proc.stdout.readline() # type: ignore
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8").strip()
            if "," not in line:
                continue
            event_type, filename = line.split(",", 1)
            if WSL_PATTERN.match(filename):
                action = NvimInstanceEvent.Started if "RE_EXIST" in event_type or "CREATE" in event_type else NvimInstanceEvent.Stopped
                callback(NvimListenerPlatform.Wsl, action, WSL_WATCH_DIR + "/" + filename)
    except asyncio.CancelledError:
        if proc.returncode is None:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass

async def watch_windows_pipes(callback: Callable[[NvimListenerPlatform, NvimInstanceEvent, str], None], poll_interval: float = 0.05):
    current_pipes = set()
    while True:
        try:
            await asyncio.sleep(poll_interval)
            latest_pipes = set(os.listdir(WIN_PIPE_DIR))
        except asyncio.CancelledError:
            break
        except Exception:
            continue
        added = latest_pipes - current_pipes
        removed = current_pipes - latest_pipes
        for pipe in added:
            if WIN_PATTERN.match(pipe):
                callback(NvimListenerPlatform.Win, NvimInstanceEvent.Started, WIN_PIPE_DIR + "\\" + pipe)
        for pipe in removed:
            if WIN_PATTERN.match(pipe):
                callback(NvimListenerPlatform.Win, NvimInstanceEvent.Stopped, WIN_PIPE_DIR + "\\" + pipe)
        current_pipes = latest_pipes
