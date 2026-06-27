import asyncio
import hid
import sys
from dataclasses import dataclass
import logging
from time import time
from active_win_info import process_window_events, windows_event_worker, process_info, find_nvim_pid
from nvim_listener import NvimListener, watch_wsl_sockets, watch_windows_pipes, NvimListenerPlatform, NvimInstanceEvent, NvimEntryMode, NvimListeners

last_send_time = 0

logging.getLogger("asyncio").setLevel(logging.DEBUG)

def setup_logger():
    logger = logging.getLogger("app_logger")
    logger.setLevel(logging.DEBUG)
    log_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(log_format)
    file_handler = logging.FileHandler("shared_keys_host.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_format)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger

logger = setup_logger()

@dataclass(frozen=True, kw_only=True)
class device:
    vid: int
    pid: int

DEV_ADEPT = device(vid=0x5043, pid=0x5C47)
DEV_CANTOR = device(vid=0xFEED, pid=0x0000)
RAW_HID_DEVICES = [ DEV_ADEPT, DEV_CANTOR ]

RAW_HID_USAGE_PAGE = 0xFF60 # Standard QMK RAW HID
RAW_HID_USAGE      = 0x61   # Standard QMK RAW HID
RAW_HID_REPORT_LEN = 32     # Fixed for QMK RAW HID

HOST_KEY = ()

class SharedKeysHost:
    def __init__(self):
        self.devs = {}
        self.shared_keys = {}
        report_data = [0x00] * (RAW_HID_REPORT_LEN + 1) # First byte is Report ID, it's not sent to the device
        report_data[1] = 0xC0
        self.get_shared_keys_report = bytes(report_data)
        report_data[1] = 0xC1
        self.report_data = report_data
        self.down = False
        self.tg = None
        self.active_window_pid = None
        self.active_nvim_pid = None
        self.focused_nvim_mode = None

    async def run(self):
        event_queue = asyncio.Queue()
        current_loop = asyncio.get_running_loop()
        async with asyncio.TaskGroup() as tg:
            self.tg = tg
            tg.create_task(self.heartbeat_loop())
            tg.create_task(process_window_events(event_queue, self.handle_foreground_win_change))
            tg.create_task(asyncio.to_thread(windows_event_worker, current_loop, event_queue))
            tg.create_task(watch_wsl_sockets(self.nvim_instance_event))
            tg.create_task(watch_windows_pipes(self.nvim_instance_event))
            while True:
                if len(self.devs) < len(RAW_HID_DEVICES):
                    start = time()
                    devices = await asyncio.to_thread(hid.enumerate)
                    took = time() - start
                    if (took) > 0.1:
                        logger.warning(f"enumerate took a long time: {took}")
                    for d in devices:
                        path = d['path']
                        if device(vid=d['vendor_id'], pid=d['product_id']) in RAW_HID_DEVICES \
                            and path not in self.devs and d['usage_page'] == RAW_HID_USAGE_PAGE and d['usage'] == RAW_HID_USAGE:
                            dev = hid.device()
                            start = time()
                            dev.open_path(path)
                            took = time() - start
                            if (took) > 0.1:
                                logger.warning(f"open_path took a long time: {took}")
                            dev.set_nonblocking(False)
                            self.devs[path] = dev
                            self.send_shared_keys_report()
                            dev.write(self.get_shared_keys_report)
                            tg.create_task(self.read_loop(path, dev))
                            logger.info(f"Connected to {path}")
                await asyncio.sleep(0.5)

    async def read_loop(self, path, device):
        last_report_time = 0
        try:
            while True:
                self.process_raw_hid_report(path, await asyncio.to_thread(device.read, RAW_HID_REPORT_LEN))
                last_report_time = time()
        except Exception as e:
            logger.exception(f"Read loop terminating due to exception (last_report_time: {time()-last_report_time:.2f}, last_send_time: {time()-last_send_time:.2f}): {e}")
        finally:
            try:
                self.devs[path].close()
            except:
                pass
            if path in self.devs:
                del self.devs[path]
            if path in self.shared_keys:
                self.process_shared_keys_report(path, [0] * 4)
                del self.shared_keys[path]
            logger.info(f"Disconnected from {path}")

    async def heartbeat_loop(self):
        global last_send_time
        while True:
            for path, dev in self.devs.items():
                try:
                    write_start = time()
                    if (cnt := dev.write(bytes(self.get_shared_keys_report))) < 0:
                        logger.error(f"Write to {path} returned error (last_send_time: {time()-last_send_time:.2f}): {dev.error()}")
                    if cnt != len(self.get_shared_keys_report):
                        logger.error(f"Write did not return correct number of bytes written. Returned {cnt}")
                    last_send_time = time()
                except Exception as e:
                    logger.exception(f"Exception during write to {path} (last_send_time: {time()-last_send_time:.2f}): {e}")
                took = time() - write_start
                if (took) > 0.5:
                    logger.warning(f"Write took a long time (heartbeat_loop): {took}")
            await asyncio.sleep(1.0)

    def process_raw_hid_report(self, path, report):
        if report:
            if len(report) == RAW_HID_REPORT_LEN:
                if report[0] == 0xC0:
                    self.process_shared_keys_report(path, report[1:])

    def process_shared_keys_report(self, path, report):
        received_keys = int.from_bytes(report[:4], byteorder='little')
        current = 0
        new = 0
        for shared_keys_path, shared_keys in self.shared_keys.items():
            current |= shared_keys
            if path == shared_keys_path:
                new |= received_keys
            else:
                new |= shared_keys
        if path not in self.shared_keys:
            new |= received_keys
        self.shared_keys[path] = received_keys
        if current != new:
            self.send_shared_keys_report()

    def send_shared_keys_report(self):
        global_keys = 0
        for shared_keys in self.shared_keys.values():
            global_keys |= shared_keys
        self.report_data[2 : 6] = global_keys.to_bytes(4, byteorder='little')
        s = ""
        for byte in self.report_data[2 : 6]:
            s += f"{byte:08b} "
        logger.info(f"{s[:-1]}")
        for path, dev in self.devs.items():
            try:
                write_start = time()
                dev.write(bytes(self.report_data))
                took = time() - write_start
                if (took) > 0.5:
                    logger.warning(f"Write took a long time (send_shared_keys_report): {took}")
            except Exception as e:
                logger.exception(f"Exception during write to {path} (last_send_time: {time()-last_send_time:.2f}): {e}")

    def handle_foreground_win_change(self, process: process_info):
        self.active_window_pid = process.pid
        if process.process != "explorer.exe": # nvim can be found as a descendant of explorer.exe, need to weed that out
            self.active_nvim_pid = find_nvim_pid(process.pid)
        else:
            self.active_nvim_pid = None
        if self.active_nvim_pid is not None:
            logger.info(f"Found nvim {self.active_nvim_pid} as descendant of {process.process}, {process.pid}")
        else:
            logger.info(f"Foreground window changed to: {process.process}, {process.pid}")
        if self.active_window_pid and (listener := NvimListeners.get(self.active_window_pid)):
            new_nvim_mode = listener.mode
        elif self.active_nvim_pid and (listener := NvimListeners.get(self.active_nvim_pid)):
            new_nvim_mode = listener.mode
        else:
            new_nvim_mode = None
        if self.focused_nvim_mode != new_nvim_mode:
            self.focused_nvim_mode = new_nvim_mode
            self.focused_nvim_mode_changed()

    def nvim_instance_event(self, platform: NvimListenerPlatform, event: NvimInstanceEvent, socket_path: str):
        if event == NvimInstanceEvent.Started:
            listener = NvimListener(platform, socket_path)
            self.tg.create_task(listener.listen(asyncio.get_running_loop(), self.nvim_mode_change_event)) # type: ignore

    def nvim_mode_change_event(self, pid: int, mode: NvimEntryMode):
        if pid == self.active_window_pid or pid == self.active_nvim_pid:
            if self.focused_nvim_mode != mode:
                self.focused_nvim_mode = mode
                self.focused_nvim_mode_changed()

    def focused_nvim_mode_changed(self):
        logger.info(f"Focused nvim mode changed to {self.focused_nvim_mode}")
        self.process_shared_keys_report(HOST_KEY, [0, 0, 0, 0x80] if self.focused_nvim_mode == NvimEntryMode.Normal else [0] * 4)


if __name__ == "__main__":
    logger.info("Application starting")
 
    host = SharedKeysHost()
    try:
        asyncio.run(host.run()) #, debug=True)
    except KeyboardInterrupt:
        pass
    logger.info("Application exiting")
