import asyncio
import hid
import sys
import signal
from dataclasses import dataclass
import logging
import ctypes
from time import time

last_send_time = 0

def is_scroll_lock_on():
    return bool(ctypes.windll.user32.GetKeyState(0x91) & 1)

logging.basicConfig(
    filename="shared_keys_host.log",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

shutdown_event = asyncio.Event()

def windows_shutdown_handler(sig, frame):
    if asyncio.get_event_loop().is_running():
        asyncio.get_event_loop().call_soon_threadsafe(shutdown_event.set)

if sys.platform == "win32":
    signal.signal(signal.SIGBREAK, windows_shutdown_handler)
    signal.signal(signal.SIGINT, windows_shutdown_handler)


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

class SharedKeysHost:
    def __init__(self):
        self.devs = {}
        self.shared_keys = {}
        report_data = [0x00] * (RAW_HID_REPORT_LEN + 1) # First byte is Report ID, it's not sent to the device
        report_data[1] = 0xC0
        self.get_shared_keys_report = bytes(report_data)
        report_data[1] = 0xC1
        self.report_data = report_data

    async def run(self):
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.heartbeat_loop())
            while not shutdown_event.is_set():
                for d in hid.enumerate():
                    path = d['path']
                    if device(vid=d['vendor_id'], pid=d['product_id']) in RAW_HID_DEVICES \
                        and path not in self.devs and d['usage_page'] == RAW_HID_USAGE_PAGE and d['usage'] == RAW_HID_USAGE:
                        dev = hid.device()
                        dev.open_path(path)
                        dev.set_nonblocking(False)
                        self.devs[path] = dev
                        self.send_shared_keys_report()
                        dev.write(self.get_shared_keys_report)
                        tg.create_task(self.read_loop(path, dev))
                        logging.info(f"Connected to {path}")
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=0.5)
                except TimeoutError:
                    pass

    async def read_loop(self, path, device):
        last_report_time = 0
        try:
            while not shutdown_event.is_set():
                if not self.process_raw_hid_report(path, last_report_time, await asyncio.to_thread(device.read, RAW_HID_REPORT_LEN, 500)): # 500ms timeout
                    logging.error(f"Device error: {device.error()}")
                    break
                else:
                    last_report_time = time()
        except Exception as e:
            logging.exception(f"Read loop terminating due to exception (last_report_time: {time()-last_report_time:.2f}, last_send_time: {time()-last_send_time:.2f}): {e}")
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
            logging.info(f"Disconnected from {path}")

    async def heartbeat_loop(self):
        global last_send_time
        while not shutdown_event.is_set():
            for path, dev in self.devs.items():
                try:
                    write_start = time()
                    if dev.write(bytes(self.get_shared_keys_report)) < 0:
                        logging.error(f"Write to {path} returned error (last_send_time: {time()-last_send_time:.2f}): {dev.error()}")
                    last_send_time = time()
                except Exception as e:
                    logging.exception(f"Exception during write to {path} (last_send_time: {time()-last_send_time:.2f}): {e}")
                took = time() - write_start
                if (took) > 0.5:
                    logging.warning(f"Write took a long time (heartbeat_loop): {took}")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=0.2)
            except TimeoutError:
                pass

    def process_raw_hid_report(self, path, last_report_time, report) -> bool:
        if report:
            # s = ""
            # for byte in report[1 : 5]:
            #     s += f"{byte:08b} "
            # logging.info(f"{path}: {s[:-1]}")
            if len(report) == RAW_HID_REPORT_LEN:
                if report[0] == 0xC0:
                    self.process_shared_keys_report(path, report[1:])
                    return True
                else:
                    logging.error(f"Read loop terminating due to non-C0 report (last_report_time: {time()-last_report_time:.2f}, last_send_time: {time()-last_send_time:.2f}): {report}")
            else:
                logging.error(f"Read loop terminating due to non-RAW_HID_REPORT_LEN report (last_report_time: {time()-last_report_time:.2f}, last_send_time: {time()-last_send_time:.2f}): {report}")
        else:
            logging.error(f"Read loop terminating due to no report (last_report_time: {time()-last_report_time:.2f}, last_send_time: {time()-last_send_time:.2f}): {report}")
        return False

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
        logging.info(f"SL: {is_scroll_lock_on()}, {s[:-1]}")
        for path, dev in self.devs.items():
            try:
                write_start = time()
                dev.write(bytes(self.report_data))
                took = time() - write_start
                if (took) > 0.5:
                    logging.warning(f"Write took a long time (send_shared_keys_report): {took}")
            except Exception as e:
                logging.exception(f"Exception during write to {path} (last_send_time: {time()-last_send_time:.2f}): {e}")


if __name__ == "__main__":
    logging.info("Application starting")
    host = SharedKeysHost()
    try:
        asyncio.run(host.run())
    except KeyboardInterrupt:
        pass
    logging.info("Application exiting")
