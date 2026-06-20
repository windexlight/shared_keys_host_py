import pynvim
import asyncio
import logging
import os
import re
from typing import Callable
from enum import Enum, auto

logger = logging.getLogger("app_logger")

class NvimListener():
    def __init__(self):
        self.nvim = None

    # TODO: handle connecting locally to Windows nvim as well with a named pipe syntax (\\.\pipe, or whatever)
    async def listen_to_nvim(self, async_loop: asyncio.AbstractEventLoop, socket_path: str, callback):
        try:
            self.nvim = await asyncio.to_thread(pynvim.attach, 'child', argv=['wsl.exe', '-e', 'nc', '-U', socket_path])
            logger.info(f"Connected to nvim at {socket_path}")

            # def worker(*args):
            #     asyncio.set_event_loop(asyncio.new_event_loop())
            #     self.nvim.run_loop(*args) # type: ignore

            def on_notification(method, args):
                if method == "mode_change":
                    async_loop.call_soon_threadsafe(callback, args[0])

            # thread = threading.Thread(
            #     target=worker,
            #     args=(None, on_notification),
            #     daemon=True
            # )
            # thread.start()
            # await asyncio.to_thread(thread.join)
            await asyncio.to_thread(self.nvim.run_loop, None, on_notification)

            self.nvim = None
            logger.info(f"Disconnected from nvim at {socket_path}")

            # while not stop_event.is_set():
            #     msg = await asyncio.to_thread(nvim.next_message)
            #     if msg.type == "notification" and msg.name == "mode_change":
            #         callback(msg.args[0])
            # nvim.stop_loop
            # nvim.close()

        except Exception as e:
            logger.error(f"Failed to connect to nvim at {socket_path}. Error: {e}")
            self.nvim = None

    def stop(self):
        if self.nvim != None:
            def stop_pynvim():
                self.nvim.stop_loop() # type: ignore
                self.nvim.close() # type: ignore
            self.nvim.async_call(stop_pynvim)



# consider making something like this work instead:

# import asyncio
# import msgpack

# WSL_SOCKET_PATH = "/tmp/nvim.sock"

# async def nvim_notification_listener():
#     """Asynchronously reads Msgpack-RPC notifications from the Neovim tunnel."""
#     command = ["wsl.exe", "-e", "socat", "-", f"UNIX-CONNECT:{WSL_SOCKET_PATH}"]
    
#     try:
#         # Open the async subprocess with piped read/write streams
#         process = await asyncio.create_subprocess_exec(
#             *command,
#             stdout=asyncio.subprocess.PIPE,
#             stdin=asyncio.subprocess.PIPE
#         )
#         print("Tunnel established! Listening for mode changes asynchronously...")

#         # Msgpack Unpacker allows us to feed bytes into it incrementally
#         unpacker = msgpack.Unpacker(raw=False)

#         while True:
#             # Read a chunk of data from the socket stream (non-blocking yield)
#             chunk = await process.stdout.read(1024)
#             if not chunk:
#                 print("Tunnel connection closed by remote host.")
#                 break
            
#             unpacker.feed(chunk)
            
#             # Process all fully parsed msgpack messages in the buffer
#             for msg in unpacker:
#                 # Msgpack-RPC notification format: [type (2), method, args]
#                 if isinstance(msg, list) and len(msg) == 3 and msg[0] == 2:
#                     method = msg[1]
#                     args = msg[2]
                    
#                     # Handle your notification
#                     if method == "mode_change":
#                         print(f"[Windows] Mode changed to: {args[0]}")
                        
#     except Exception as e:
#         print(f"Error in Neovim listener: {e}")

# async def main():
#     # 1. Start your Neovim notification listener in the background
#     listener_task = asyncio.create_task(nvim_notification_listener())
    
#     # 2. Simulate the rest of your existing asyncio application running
#     print("Main app logic running smoothly alongside Neovim listener...")
#     for i in range(1, 6):
#         await asyncio.sleep(2)
#         print(f"[App Log] Doing other async work... ({i*2}s elapsed)")

#     # Clean up the background task when main ends (optional)
#     listener_task.cancel()

# if __name__ == "__main__":
#     asyncio.run(main())

class NvimListenerPlatform(Enum):
    Wsl = 0
    Win = auto()

class NvimInstanceEvent(Enum):
    Started = 0
    Stopped = auto()

WSL_WATCH_DIR = "/tmp"
WSL_PATTERN = re.compile(r"^nvim-win-[0-9]+\.sock$")

WIN_PIPE_DIR = r"\\.\pipe"
WIN_PATTERN = re.compile(r"^nvim-win-[0-9]+$")

def on_file_event(platform: NvimListenerPlatform, event: NvimInstanceEvent, socket_name: str):
    """
    Your custom callback handler. Plug your application logic here.
    platform: 'wsl' or 'windows'
    event:   'CREATED' or 'REMOVED'
    socket_name: The name of the socket or named pipe
    """
    logger.info(f"[EVENT] [{platform}] {event}: {socket_name}")


async def watch_wsl_sockets(callback: Callable[[NvimListenerPlatform, NvimInstanceEvent, str], None]):
    # TODO --- not working... for some reason, it not only won't catch one already started, but it also won't see when it stops
    # it does catch existing closes without the -t 0 line.
    wsl_cmd = (
        # f"stdbuf -oL inotifywait -t 0 -r -e create --format 'RE_EXIST,%f' {WSL_WATCH_DIR} && "
        f"stdbuf -oL inotifywait -m -e create -e delete --format '%e,%f' {WSL_WATCH_DIR}"
    )
    CREATE_NO_WINDOW = 0x08000000
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
            line = line_bytes.decode('utf-8').strip()
            if ',' not in line:
                continue
            event_type, filename = line.split(',', 1)
            if WSL_PATTERN.match(filename):
                action = NvimInstanceEvent.Started if "RE_EXIST" in event_type or "CREATE" in event_type else NvimInstanceEvent.Stopped
                callback(NvimListenerPlatform.Wsl, action, filename)
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
                callback(NvimListenerPlatform.Win, NvimInstanceEvent.Started, pipe)
        for pipe in removed:
            if WIN_PATTERN.match(pipe):
                callback(NvimListenerPlatform.Win, NvimInstanceEvent.Stopped, pipe)
        current_pipes = latest_pipes


# # ==========================================
# # Main Event Loop Coordination
# # ==========================================
# async def main():
#     # Gather both background monitoring tasks
#     wsl_task = asyncio.create_task(watch_wsl_sockets(on_file_event))
#     win_task = asyncio.create_task(watch_windows_pipes(on_file_event))
    
#     try:
#         # Run until explicitly interrupted (Ctrl+C)
#         await asyncio.gather(wsl_task, win_task)
#     except KeyboardInterrupt:
#         print("\nShutting down tasks...")
#     finally:
#         wsl_task.cancel()
#         win_task.cancel()
#         # Allow tasks to clean up their process handles/loops
#         await asyncio.gather(wsl_task, win_task, return_exceptions=True)

# if __name__ == "__main__":
#     # Ensure proper proactor event loop setup on Windows for subprocess piping
#     if sys.platform == 'win32':
#         asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         print("Exited cleanly.")