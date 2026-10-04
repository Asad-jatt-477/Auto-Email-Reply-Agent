"""
step7_logger.py
---------------
Logs to stdout always (what Railway / Task Scheduler capture) and to a
rotating file under logs/ when LOG_TO_FILE is true (local default).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from src import config


class SafeStreamHandler(logging.StreamHandler):
    """
    Console handler that never fails on characters the console cannot show.
    A Windows console / pipe often uses cp1252, while email subjects contain
    emoji, Urdu and typographic characters; those are replaced with '?'
    instead of raising a logging error. The log FILE stays full UTF-8.
    """

    def emit(self, record):
        try:
            msg = self.format(record) + self.terminator
            encoding = getattr(self.stream, "encoding", None) or "utf-8"
            self.stream.write(msg.encode(encoding, errors="replace").decode(encoding, errors="replace"))
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logger(name: str = "email_agent") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    console = SafeStreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if config.LOG_TO_FILE:
        os.makedirs(config.LOGS_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            config.LOG_PATH, maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger