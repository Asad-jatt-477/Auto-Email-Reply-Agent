"""
step7_logger.py
-----------------
Phase 10 — Logging & Error Handling.

setup_logger() ek configured Python logger return karta hai jo:
- console pe bhi print karta hai (taake terminal mein live dekh sako)
- logs/agent.log mein rotate hoti file mein bhi likhta hai (Golden Rule:
  size/backup limits config.py se aate hain, hardcode nahi)
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from src.config import LOG_PATH, LOGS_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT


def setup_logger(name: str = "email_agent") -> logging.Logger:
    """
    Logger return karta hai. Agar pehle se configure ho chuka hai
    (jaise multiple modules isay import karte hain), dobara handlers
    add nahi karta — warna log lines duplicate ho jatin.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


if __name__ == "__main__":
    # Standalone test: python -m src.step7_logger
    logger = setup_logger()
    logger.info("Test log message - INFO level")
    logger.warning("Test log message - WARNING level")
    logger.error("Test log message - ERROR level")
    print(f"\nCheck {LOG_PATH} to confirm these lines were written to file too.")
