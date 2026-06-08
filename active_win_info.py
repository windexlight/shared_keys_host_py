import asyncio
import ctypes
import ctypes.wintypes
import win32gui
import win32process
import psutil
from dataclasses import dataclass
from collections.abc import Callable

@dataclass(frozen=True, kw_only=True)
class process_info:
    title: str
    process: str
    pid: int|None

user32 = ctypes.windll.user32
OLE32 = ctypes.windll.ole32

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

def get_process_info(hwnd) -> process_info:
    try:
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        return process_info(title=title, process=process.name(), pid=pid)
    except Exception:
        return process_info(title="Unknown", process="Unknown", pid=None)

def windows_event_worker(async_loop, event_queue: asyncio.Queue):
    OLE32.CoInitialize(None)
    def callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        if hwnd:
            info = get_process_info(hwnd)
            async_loop.call_soon_threadsafe(event_queue.put_nowait, info)
    WinEventProcType = ctypes.WINFUNCTYPE(
        None, ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.HWND,
        ctypes.wintypes.LONG, ctypes.wintypes.LONG, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD
    )
    callback_pointer = WinEventProcType(callback)
    hook = user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
        0, callback_pointer, 0, 0, WINEVENT_OUTOFCONTEXT
    )
    if not hook:
        return
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

async def process_window_events(event_queue: asyncio.Queue, callback: Callable[[process_info], None]):
    while True:
        callback(await event_queue.get())
        event_queue.task_done()
