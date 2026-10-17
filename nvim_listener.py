import asyncio
import logging
import os
import re
from typing import Callable, Dict, Tuple
from enum import Enum, auto
import msgpack
import keyboard

logger = logging.getLogger("app_logger")

class NvimListenerPlatform(Enum):
    Wsl = 0
    Win = auto()
    Unsupported = auto()

class NvimInstanceEvent(Enum):
    Started = 0
    Stopped = auto()

class NvimEntryMode(Enum):
    Normal = 0
    Insert = auto()

CREATE_NO_WINDOW = 0x08000000

WSL_SOCKET_DIR = "/tmp"
WSL_SOCKET_FILENAME_PATTERN = re.compile(r"^nvim-wsl-([0-9]+)\.sock$")
WSL_SOCKET_FULL_PATH_PATTERN = re.compile(fr"^{WSL_SOCKET_DIR}/nvim-wsl-([0-9]+)\.sock$")

WIN_PIPE_DIR = r"\\.\pipe"
WIN_PIPE_FILENAME_PATTERN = re.compile(r"^nvim-win-([0-9]+)$")
WIN_PIPE_FULL_PATH_PATTERN = re.compile(fr"^{re.escape(WIN_PIPE_DIR)}\\nvim-win-([0-9]+)$")

NvimWinListeners: Dict[int, NvimListener] = {}
NvimWslListenersByWslPid: Dict[int, NvimListener] = {}
NvimWslListenersByWinPid: Dict[int, NvimListener] = {}

def nvim_entry_mode_from_string(mode_string: str):
    if mode_string.startswith(("n", "v", "V", "\x16")):
        return NvimEntryMode.Normal
    else:
        return NvimEntryMode.Insert

class NvimListener():
    def __init__(self, platform: NvimListenerPlatform, socket_path: str,
                 mode_change: Callable[[int, NvimEntryMode | None], None],
                 ack: Callable[[NvimListener], None]):
        self.socket_path = socket_path
        self.platform = platform
        self.mode_change = mode_change
        self.ack = ack
        self.mode = NvimEntryMode.Insert
        self.last_reported_mode = None
        self.win_pid: int | None = None
        self.ancestry = None
        match self.platform:
            case NvimListenerPlatform.Win:
                m = WIN_PIPE_FULL_PATH_PATTERN.search(socket_path)
            case NvimListenerPlatform.Wsl:
                m = WSL_SOCKET_FULL_PATH_PATTERN.search(socket_path)
            case _:
                logger.error(f"Bad platform: {platform}")
                return
        try:
            self.pid = int(m.group(1)) # type: ignore
        except Exception:
            logger.error(f"Pid not found in socket path {socket_path}")
            self.platform = NvimListenerPlatform.Unsupported
            return
        match self.platform:
            case NvimListenerPlatform.Win:
                if self.pid in NvimWinListeners:
                    logger.error(f"Listener already exists: {self.pid}")
                    self.platform = NvimListenerPlatform.Unsupported
                else:
                    NvimWinListeners[self.pid] = self
            case NvimListenerPlatform.Wsl:
                if self.pid in NvimWslListenersByWslPid:
                    logger.error(f"Listener already exists: {self.pid}")
                    self.platform = NvimListenerPlatform.Unsupported
                else:
                    NvimWslListenersByWslPid[self.pid] = self

    async def listen(self, async_loop):
        match self.platform:
            case NvimListenerPlatform.Win:
                await self.listen_win(async_loop)
            case NvimListenerPlatform.Wsl:
                await self.listen_wsl()
            case _:
                logger.error(f"Bad platform: {self.platform}")

    async def listen_win(self, async_loop):
        logger.info(f"Starting listener for {self.socket_path}")
        try:
            def read_pipe():
                with open(self.socket_path, 'r+b', buffering=0) as pipe:
                    self.query_current_mode_win(async_loop, pipe, self.mode_change)
                    logger.info("Sending fish keys in listen_win")
                    # keyboard.send("ctrl+alt+shift+f12") # Attempt to trigger response from nvim
                    unpacker = msgpack.Unpacker(raw=False)
                    while True:
                        chunk = pipe.read(1024)
                        if not chunk:
                            break
                        unpacker.feed(chunk)
                        self.handle_nvim_msgpack(unpacker,
                                                 lambda pid, mode: async_loop.call_soon_threadsafe(self.mode_change, pid, mode),
                                                 lambda listener: async_loop.call_soon_threadsafe(self.ack, listener))
            await asyncio.to_thread(read_pipe)
        except Exception as e:
            logger.error(f"Error in {self.socket_path} listener: {e}")
        del NvimWinListeners[self.pid]
        logger.info(f"Listener for {self.socket_path} stopped")
        async_loop.call_soon_threadsafe(self.mode_change, self.pid, None)

    async def listen_wsl(self):
        try:
            process = await asyncio.create_subprocess_exec(
                "wsl.exe", "-e", "nc", "-U", self.socket_path,
                stdout=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            logger.info(f"Starting listener for {self.socket_path}")
            self.ancestry = await self.query_nvim_ancestry_wsl(process.stdout, process.stdin)
            await self.query_current_mode_wsl(process.stdout, process.stdin, self.mode_change)
            logger.info("Sending fish keys in listen_wsl")
            keyboard.send("ctrl+alt+shift+f12") # Attempt to trigger response from nvim
            unpacker = msgpack.Unpacker(raw=False)
            while True:
                chunk = await process.stdout.read(1024) # type: ignore
                if not chunk:
                    break
                unpacker.feed(chunk)
                self.handle_nvim_msgpack(unpacker, self.mode_change, self.ack)
        except Exception as e:
            logger.info(f"Error in {self.socket_path} listener: {e}")
        del NvimWslListenersByWslPid[self.pid]
        if self.win_pid is not None:
            if self.win_pid in NvimWslListenersByWinPid:
                del NvimWslListenersByWinPid[self.win_pid]
        logger.info(f"Listener for {self.socket_path} stopped")
        self.mode_change(self.pid, None)

    def handle_nvim_msgpack(self, unpacker, mode_change, ack):
        for msg in unpacker:
            if isinstance(msg, list) and len(msg) == 3 and msg[0] == 2:
                method = msg[1]
                args = msg[2]
                if method == "mode_change":
                    logger.info(f"{self.socket_path} mode changed to: {args[0]}")
                    self.mode = nvim_entry_mode_from_string(args[0])
                elif method == "ack": # TODO - Add this to nvim config
                    logging.info(f"got ack from {self.socket_path}")
                    ack(self)
                if self.mode != self.last_reported_mode:
                    self.last_reported_mode = self.mode
                    mode_change(self.win_pid or self.pid, self.mode)

    def query_current_mode_win(self, async_loop, pipe, mode_change: Callable[[int, NvimEntryMode | None], None]):
        try:
            req_id = 1
            request = [0, req_id, "nvim_get_mode", []]
            pipe.write(msgpack.packb(request))
            pipe.flush()
            unpacker = msgpack.Unpacker(raw=False)
            while True:
                chunk = pipe.read(1024)
                if not chunk:
                    break
                unpacker.feed(chunk)
                for msg in unpacker:
                    if isinstance(msg, list) and len(msg) == 4 and msg[0] == 1:
                        if msg[1] == req_id:
                            error = msg[2]
                            result = msg[3]
                            if error:
                                logger.error(f"Failed to get initial mode from {self.socket_path}: {error}")
                            else:
                                current_mode = result.get('mode', 'unknown')
                                logger.info(f"Initial {self.socket_path} mode on startup: {current_mode}")
                                self.mode = nvim_entry_mode_from_string(current_mode)
                                async_loop.call_soon_threadsafe(mode_change, self.pid, self.mode)
                            return
        except Exception as e:
            logger.error(f"Failed during initial startup mode query to {self.socket_path}: {e}")

    async def query_current_mode_wsl(self, reader, writer, mode_change: Callable[[int, NvimEntryMode], None]):
        try:
            req_id = 1
            request = [0, req_id, "nvim_get_mode", []]
            writer.write(msgpack.packb(request))
            unpacker = msgpack.Unpacker(raw=False)
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                unpacker.feed(chunk)
                for msg in unpacker:
                    if isinstance(msg, list) and len(msg) == 4 and msg[0] == 1:
                        if msg[1] == req_id:
                            error = msg[2]
                            result = msg[3]
                            if error:
                                logger.error(f"Failed to get initial mode from {self.socket_path}: {error}")
                            else:
                                current_mode = result.get('mode', 'unknown')
                                logger.info(f"Initial {self.socket_path} mode on startup: {current_mode}")
                                self.mode = nvim_entry_mode_from_string(current_mode)
                                mode_change(self.pid, self.mode)
                            return
        except Exception as e:
            logger.error(f"Failed during initial startup mode query to {self.socket_path}: {e}")

    async def query_nvim_ancestry_wsl(self, reader, writer):
        try:
            req_id = 2
            lua_code = "return require('config.winlink').ancestry()"
            request = [0, req_id, "nvim_exec_lua", [lua_code, []]]
            writer.write(msgpack.packb(request))
            unpacker = msgpack.Unpacker(raw=False)
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                unpacker.feed(chunk)
                for msg in unpacker:
                    if isinstance(msg, list) and len(msg) == 4 and msg[0] == 1:
                        if msg[1] == req_id:
                            error = msg[2]
                            result = msg[3]
                            if error:
                                logger.error(f"Failed to get ancestry from {self.socket_path}: {error}")
                                result = None
                            else:
                                logger.info(f"Got ancestry from {self.socket_path}")
                            return result
        except Exception as e:
            logger.error(f"Failed to get ancestry from {self.socket_path}: {e}")
        return None

async def watch_wsl_sockets(callback: Callable[[NvimListenerPlatform, NvimInstanceEvent, str], None]):
    wsl_cmd = (
        f"find {WSL_SOCKET_DIR} -maxdepth 1 -type s -printf 'RE_EXIST,%f\\n' && "
        f"stdbuf -oL inotifywait -m -e create -e delete --format '%e,%f' {WSL_SOCKET_DIR}"
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
            if WSL_SOCKET_FILENAME_PATTERN.match(filename):
                action = NvimInstanceEvent.Started if "RE_EXIST" in event_type or "CREATE" in event_type else NvimInstanceEvent.Stopped
                callback(NvimListenerPlatform.Wsl, action, WSL_SOCKET_DIR + "/" + filename)
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
            if WIN_PIPE_FILENAME_PATTERN.match(pipe):
                callback(NvimListenerPlatform.Win, NvimInstanceEvent.Started, WIN_PIPE_DIR + "\\" + pipe)
        for pipe in removed:
            if WIN_PIPE_FILENAME_PATTERN.match(pipe):
                callback(NvimListenerPlatform.Win, NvimInstanceEvent.Stopped, WIN_PIPE_DIR + "\\" + pipe)
        current_pipes = latest_pipes
