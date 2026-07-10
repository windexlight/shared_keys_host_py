import asyncio
import ctypes
import ctypes.wintypes
import win32gui
import win32process
import psutil
from dataclasses import dataclass
from collections.abc import Callable
import logging

logger = logging.getLogger("app_logger")

@dataclass(frozen=True, kw_only=True)
class process_info:
    title: str
    process: str
    pid: int|None

user32 = ctypes.windll.user32
OLE32 = ctypes.windll.ole32

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
OBJID_WINDOW = 0

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
        if hwnd and idObject == OBJID_WINDOW:
            async_loop.call_soon_threadsafe(event_queue.put_nowait, hwnd)
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
    last_info = get_process_info(win32gui.GetForegroundWindow())
    callback(last_info)
    while True:
        await event_queue.get()
        await asyncio.sleep(0.1)
        while not event_queue.empty():
            event_queue.get_nowait()
            event_queue.task_done()
        actual_hwnd = win32gui.GetForegroundWindow()
        info = get_process_info(actual_hwnd)
        # if info.process and info.process.lower() == "explorer.exe" and not info.title:
        #     event_queue.task_done()
        #     continue
        if info.pid != last_info.pid or info.title != last_info.title:
            callback(info)
            last_info = info
        event_queue.task_done()

# TODO -- This will stop finding nvim at a descendant if :restart is used. May be able to try using GetConsoleProcessList, but that's probably brittle as well.
# :restart doesn't work in Neovide anyway (just exits), and is new as of nvim 0.12, so it's probably not the end of the world to just not use it.
def find_nvim_pid(terminal_pid):
    nvims = []
    try:
        terminal_process = psutil.Process(terminal_pid)
        descendants = terminal_process.children(recursive=True)
        for process in descendants:
            try:
                if process.name().lower() == 'nvim.exe':
                    nvims.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except psutil.NoSuchProcess:
        logger.error(f"No running process found with PID {terminal_pid}")
    except psutil.AccessDenied:
        logger.error(f"Access denied to process {terminal_pid}")
    return nvims or None
