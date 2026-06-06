from __future__ import annotations

import contextvars
import logging
import logging.handlers
import time
from datetime import datetime
from pathlib import Path

# ── Conversation-ID context variable ──────────────────────────────────────────
# asyncio.create_task copies the current context, so child coroutines and tasks
# spawned inside a request automatically inherit the conversation ID set here.

_conv_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "conversation_id", default="-"
)


def set_conversation_id(conv_id: str) -> None:
    _conv_id_var.set(conv_id)


# ── Filter that injects conversation_id into every log record ─────────────────

class _ConversationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.conversation_id = _conv_id_var.get("-")  # type: ignore[attr-defined]
        return True


# ── Handler that writes to logs/YYYY-MM-DD.log, rotates at midnight ──────────

class DailyFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Creates logs/YYYY-MM-DD.log for the current day; opens a new file at midnight."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(
            filename=str(log_dir / f"{datetime.now():%Y-%m-%d}.log"),
            when="midnight",
            backupCount=90,
            encoding="utf-8",
            delay=False,
        )

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        # Open a new file named for the new day (it's already "tomorrow" now)
        self.baseFilename = str(self._log_dir / f"{datetime.now():%Y-%m-%d}.log")
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(time.time()))


# ── Public setup function ─────────────────────────────────────────────────────

def setup_logging(log_dir: str = "logs") -> None:
    """Configure root logger: daily file + console, both stamped with conversation_id."""
    fmt = "%(asctime)s | %(levelname)-8s | %(conversation_id)-36s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)
    conv_filter = _ConversationIdFilter()

    file_handler = DailyFileHandler(Path(log_dir))
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(conv_filter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(conv_filter)

    root = logging.getLogger()
    # Remove any handlers added by earlier basicConfig calls
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
