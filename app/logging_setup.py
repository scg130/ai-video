"""应用日志：默认 LOG_DIR/app.log 轮转；可选同步输出到控制台。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def setup_app_logging() -> None:
    """幂等；为 `app` 及其子 logger 配置文件与/或控制台 handler。"""
    global _configured
    if _configured:
        return

    from app.config import settings

    level_name = (getattr(settings, "log_level", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)

    to_file = getattr(settings, "log_to_file", True)
    to_console = getattr(settings, "log_to_console", True)

    if to_file:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / getattr(settings, "log_filename", "app.log")
        max_bytes = int(getattr(settings, "log_file_max_bytes", 10 * 1024 * 1024))
        backup = int(getattr(settings, "log_file_backup_count", 5))
        fh = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        app_logger.addHandler(fh)

    if to_console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(level)
        ch.setFormatter(fmt)
        app_logger.addHandler(ch)

    if app_logger.handlers:
        app_logger.propagate = False

    _configured = True
