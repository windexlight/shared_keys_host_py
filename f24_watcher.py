import asyncio
import keyboard
import logging
import sys

down = set()

def setup_logger():
    logger = logging.getLogger("app_logger")
    logger.setLevel(logging.DEBUG)
    log_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(log_format)
    file_handler = logging.FileHandler("f24_presses.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_format)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger

logger = setup_logger()

async def on_f24_event(event):
    global down
    if event.event_type == 'down':
        if event.name not in down:
            down.add(event.name)
            logger.info(f"{event.name.upper()} was PRESSED!")
    elif event.event_type == 'up':
        if event.name in down:
            down.remove(event.name)
        logger.info(f"{event.name.upper()} was RELEASED!")

async def main():
    print("Listening for keypresses... Press Ctrl+C to exit.")
    loop = asyncio.get_running_loop()
    keyboard.hook(
        lambda e: loop.call_soon_threadsafe(lambda: asyncio.create_task(on_f24_event(e))) if e.name in ['f22', 'f24', 'f23'] else None
    )
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program stopped.")
    except Exception:
        logger.info("...?")
    finally:
        logger.info("Program exiting")
