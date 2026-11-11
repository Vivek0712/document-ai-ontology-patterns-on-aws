"""One-line JSON logs (CloudWatch Logs Insights friendly)."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from .masking import mask_for_display


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "rp", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return mask_for_display(json.dumps(payload, default=str))


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(h.formatter, _JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.handlers = [handler]
        logger.propagate = False
    logger.setLevel(level)
    return logger


def log_kv(logger: logging.Logger, msg: str, **fields: Any) -> None:
    logger.info(msg, extra={"rp": fields})
