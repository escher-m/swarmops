"""Structured logging for the seeded service.

HolmesGPT reads these logs as its primary evidence, so the format matters more
than it would for a toy app: one event per line, an ISO-8601 UTC timestamp, a
level, a logger name, and a message, with tracebacks indented beneath their
event the way a real service emits them. Anything the agent cannot parse is
evidence it cannot cite.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from .config import LOG_DIR, LOG_PATH


class UTCFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802 - stdlib signature
        return datetime.fromtimestamp(record.created, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"


def configure(name: str = "checkout-api") -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = UTCFormatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
    )
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger
