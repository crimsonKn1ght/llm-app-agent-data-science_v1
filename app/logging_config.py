from __future__ import annotations

import contextvars
import logging
import logging.handlers
import time
from datetime import datetime
from pathlib import Path

_conv_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("conversation_id", default="-")


def set_conversation_id(conv_id: str) -> None:
    _conv_id_var.set(conv_id)


class _ConversationIdFilter(logging.Filter):
    def filter(self, record):
        record.conversation_id = _conv_id_var.get("-")
        return True


class DailyFileHandler(logging.handlers.TimedRotatingFileHandler):
    def __init__(self, log_dir: Path):
        self._log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(
            filename=str(log_dir / f"{datetime.now():%Y-%m-%d}.log"),
            when="midnight",
            backupCount=90,
            encoding="utf-8",
        )

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = str(self._log_dir / f"{datetime.now():%Y-%m-%d}.log")
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(time.time()))


def setup_logging(log_dir: str = "logs") -> None:
    fmt = "%(asctime)s | %(levelname)-8s | %(conversation_id)-36s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt)
    conv_filter = _ConversationIdFilter()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addFilter(conv_filter)

    file_handler = DailyFileHandler(Path(log_dir))
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(conv_filter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(conv_filter)
    root.addHandler(console_handler)

    for noisy in ("httpx", "anthropic", "urllib3", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
