import atexit
import hid
import qasync
from time import time
from PySide6.QtCore import QTimer, QObject, Signal
from typing import List, Tuple
from datetime import datetime
from dataclasses import dataclass, fields

from keycodes import *
from scancodes import *

@dataclass(frozen=True, kw_only=True)
class device:
    vid: int
    pid: int

DEV_ADEPT = device(vid=0x5043, pid=0x5C47)
DEV_CANTOR = device(vid=0xFEED, pid=0x0000)
RAW_HID_DEVICES = [ DEV_ADEPT, DEV_CANTOR ]
FAKE_REPORT_DEVICE = DEV_CANTOR

RAW_HID_USAGE_PAGE = 0xFF60 # Standard QMK RAW HID
RAW_HID_USAGE      = 0x61   # Standard QMK RAW HID
RAW_HID_REPORT_LEN = 32     # Fixed for QMK RAW HID
RAW_HID_KEY_BYTES  = RAW_HID_REPORT_LEN-2
RAW_HID_KEYS       = RAW_HID_KEY_BYTES*8
RAW_HID_MODS       = 8

class RawHid(QObject):
    keyEvent = Signal(object)
    statusChanged = Signal()

    def __init__(self):
        super().__init__()

        self.devs = {}
        self.shared_keys = {}
        self.mod_bits = 0
        self.key_bits = [0]*RAW_HID_KEY_BYTES
        self.mods = []
        self.keys = []

        self.relevant_keycodes = [i for i in range(RAW_HID_KEYS) if keycodeToScancode(i) is not None]
        report_data = [0x00] * (RAW_HID_REPORT_LEN + 1) # First byte is Report ID
        report_data[1] = 0xBE
        self.fake_reports_start_report = bytes(report_data)
        report_data[1] = 0xBF
        self.fake_reports_stop_report = bytes(report_data)
        report_data[1] = 0xC0
        self.get_shared_keys_report = bytes(report_data)
        report_data[1] = 0xC1
        self.report_data = report_data

        self.last_heartbeat_times = {}
        self.fake_reports_active = False

        atexit.register(self.close)

        self.hid_connect_timer = QTimer(self)
        self.hid_connect_timer.timeout.connect(self.try_connect)
        self.hid_connect_timer.setInterval(500)
        self.hid_connect_timer.start()

        self.hid_poll_timer = QTimer(self)
        self.hid_poll_timer.timeout.connect(self.hid_poll)
        self.hid_poll_timer.setInterval(int(1000/120))
        self.hid_poll_timer.start()

        self.hid_send_timer = QTimer(self)
        self.hid_send_timer.timeout.connect(self.hid_heartbeat)
        self.hid_send_timer.setInterval(200)
        self.hid_send_timer.start()
        self.hid_heartbeat()

        self.try_connect()

    def start_fake_reports(self):
        if (dev := self.devs.get(FAKE_REPORT_DEVICE)) is not None:
            dev.write(self.fake_reports_start_report)

    def stop_fake_reports(self):
        if (dev := self.devs.get(FAKE_REPORT_DEVICE)) is not None:
            dev.write(self.fake_reports_stop_report)

    def try_connect(self):
        for i in hid.enumerate():
            key = device(vid=i['vendor_id'], pid=i['product_id'])
            if key in RAW_HID_DEVICES and key not in self.devs and i['usage_page'] == RAW_HID_USAGE_PAGE and i['usage'] == RAW_HID_USAGE:
                path = i['path']
                dev = hid.device()
                dev.open_path(path)
                dev.set_nonblocking(True)
                self.devs[key] = dev
                dev.write(self.get_shared_keys_report)
                self.last_heartbeat_times[key] = time()

    def for_all_devs(self, action):
        to_close = []
        for key, dev in self.devs.items():
            try:
                if not action(key, dev):
                    to_close.append(key)
            except:
                to_close.append(key)
        for key in to_close:
            self.close_dev(key)

    def close_dev(self, key):
        try:
            self.devs[key].close()
        except:
            pass
        if key in self.devs:
            del self.devs[key]
        if key in self.last_heartbeat_times:
            del self.last_heartbeat_times[key]
        if key in self.shared_keys:
            self.process_shared_keys_report(key, [0] * 4)
            del self.shared_keys[key]


    def hid_read_device(self, key, dev) -> bool:
        if len(report := dev.read(RAW_HID_REPORT_LEN)) > 0: # This apparently can't return a partial report
            self.process_raw_hid_report(key, report)
        if (time() - self.last_heartbeat_times[key]) > 500:
            if key == FAKE_REPORT_DEVICE:
                if self.fake_reports_active:
                    self.fake_reports_active = False
                    self.statusChanged.emit()
            return False
        return True

    def hid_poll(self):
        self.for_all_devs(self.hid_read_device)

    def hid_heartbeat(self):
        self.for_all_devs(lambda key, dev: dev.write(self.get_shared_keys_report))


    def process_raw_hid_report(self, key, report):
        if report is not None:
            if len(report) == RAW_HID_REPORT_LEN:
                if report[0] == 0xC0:
                    self.last_heartbeat_times[key] = time()
                    self.process_shared_keys_report(key, report[1:])
                elif report[0] == 6:
                    self.process_keyboard_report(report[1:])

    def process_shared_keys_report(self, key, report):
        # s = ""
        # for byte in report[:4]:
        #     s += f"{byte:08b} "
        # print(f"{key}: {s[:-1]}")
        received_keys = int.from_bytes(report[:4], byteorder='little')
        current = 0
        new = 0
        for dev_key, shared_keys in self.shared_keys.items():
            current |= shared_keys
            if key == dev_key:
                new |= received_keys
            else:
                new |= shared_keys
        if key not in self.shared_keys:
            new = received_keys
        self.shared_keys[key] = received_keys
        if current != new:
            self.send_shared_keys_report()

    def send_shared_keys_report(self):
        global_keys = 0
        for dev_key, shared_keys in self.shared_keys.items():
            global_keys |= shared_keys
        self.report_data[2 : 6] = global_keys.to_bytes(4, byteorder='little')
        s = ""
        for byte in self.report_data[2 : 6]:
            s += f"{byte:08b} "
        print(s[:-1])
        self.for_all_devs(lambda key, dev: dev.write(bytes(self.report_data)))


    def process_keyboard_report(self, report):
        mods = report[0]
        self.mods = [keycodeToScancode(i+keycode.KC_LEFT_CTRL.value) for i in range(RAW_HID_MODS)
                     if (mods & (1 << i)) > 0]
        keys = report[1:]
        print(datetime.now().isoformat(), self.mods, [keycodeToScancode(i) for i in self.relevant_keycodes if key_bit_set(i, keys)])
        pressed = [keycodeToScancode(i) for i in self.relevant_keycodes
                   if key_bit_set(i, keys) and not key_bit_set(i, self.key_bits)]
        released = [keycodeToScancode(i) for i in self.relevant_keycodes
                    if not key_bit_set(i, keys) and key_bit_set(i, self.key_bits)]
        self.key_bits = keys
        self.keys = [k for k in self.keys if k not in SCANCODE_MODS]
        self.keys = [k for k in self.keys if not any(x in released for x in (k if isinstance(k, tuple) else (k,)))]
        self.keys.extend(pressed if not self.mods else ((*self.mods, x) for x in pressed))
        mods_pressed = [x for x in self.mods if not self.keys or not all(x in (y if isinstance(y, tuple) else (y,)) for y in self.keys)]
        self.keys = [*mods_pressed, *self.keys]
        self.keyEvent.emit(self.keys)

    def close(self):
        self.for_all_devs(lambda key, dev: False)
        if self.fake_reports_active:
            self.fake_reports_active = False
            self.statusChanged.emit()

def key_bit_set(key: int, keys: List[int]) -> bool | None:
    if (key >> 3) < len(keys):
        return (keys[key >> 3] & (1 << (key & 7))) > 0

