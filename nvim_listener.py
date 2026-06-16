import pynvim
import asyncio
import logging

logger = logging.getLogger("app_logger")

class NvimListener():
    def __init__(self):
        self.nvim = None

    # TODO: handle connecting locally to Windows nvim as well with a named pipe syntax (\\.\pipe, or whatever)
    async def listen_to_nvim(self, async_loop: asyncio.AbstractEventLoop, socket_path: str, callback):
        try:
            self.nvim = pynvim.attach('child', argv=['wsl.exe', '-e', 'nc', '-U', socket_path])
            logger.info(f"Connected to nvim at {socket_path}")

            def on_notification(method, args):
                if method == "mode_change":
                    async_loop.call_soon_threadsafe(callback, args[0])

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
            self.nvim.close()
