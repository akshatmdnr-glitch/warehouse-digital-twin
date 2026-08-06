"""Structured, rotating logging for the warehouse backend.

Structured JSON lines are written to the configured log file (with
RotatingFileHandler) and human-readable lines to stderr. Levels:
DEBUG / INFO / WARNING / ERROR / CRITICAL.
"""

import json
import logging
import logging.handlers
import os
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload)


def setup_logging(
    level="INFO", log_file=None, max_bytes=5 * 1024 * 1024, backup_count=3
):
    level = getattr(logging, str(level).upper(), logging.INFO)
    root = logging.getLogger("warehouse")
    root.setLevel(level)
    root.handlers.clear()

    fmt = JsonFormatter()

    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    root.addHandler(sh)

    # Avoid duplicate records from child loggers.
    root.propagate = False
    return root


def get_logger(name="warehouse"):
    return logging.getLogger(name)


def log_structured(logger, level, message, **fields):
    record = logging.LogRecord(
        name=logger.name,
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.extra_fields = fields
    logger.handle(record)
