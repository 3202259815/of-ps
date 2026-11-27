import logging
import queue
import threading
from logging.handlers import QueueHandler
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.shortcuts import print_formatted_text
from colorama import Fore, Style, init as color_init

color_init(wrap=False)

_log_queue = None
_consumer_thread = None
_formatter = None


def _format_record_to_ansi(record):
    if _formatter is not None:
        text = _formatter.format(record)
    else:
        text = f"{record.levelname}: {record.getMessage()}"
    lvl = record.levelno
    if lvl >= logging.ERROR:
        color = Fore.RED
    elif lvl >= logging.WARNING:
        color = Fore.YELLOW
    elif lvl >= logging.INFO:
        color = Fore.GREEN
    else:
        color = Fore.CYAN
    return color + text + Style.RESET_ALL


def _format_record_plain(record):
    if _formatter is not None:
        return _formatter.format(record)
    return f"{record.levelname}: {record.getMessage()}"


_PLAIN_MODE = False


def _emit(record):
    """有真实控制台就用 prompt_toolkit 上色；没有（Docker/重定向）就退化成普通 print。"""
    global _PLAIN_MODE
    if not _PLAIN_MODE:
        try:
            print_formatted_text(ANSI(_format_record_to_ansi(record)))
            return
        except Exception:  # NoConsoleScreenBufferError 等
            _PLAIN_MODE = True
    print(_format_record_plain(record), flush=True)


def _log_consumer_loop(q: queue.Queue, stop_event: threading.Event):
    while not stop_event.is_set():
        if q.empty():
            stop_event.wait(0.05)
            continue
        record = q.get_nowait()
        _emit(record)

    while not q.empty():
        record = q.get_nowait()
        _emit(record)


def start(stop_event: threading.Event = None):
    global _log_queue, _consumer_thread, _formatter
    if stop_event is None:
        stop_event = threading.Event()
    if _log_queue is not None:
        return stop_event
    root = logging.getLogger()
    for h in root.handlers:
        if getattr(h, "formatter", None):
            _formatter = h.formatter
            break
    _log_queue = queue.Queue()
    qh = QueueHandler(_log_queue)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(qh)
    _consumer_thread = threading.Thread(
        target=_log_consumer_loop, args=(_log_queue, stop_event), daemon=True
    )
    _consumer_thread.start()
    return stop_event
