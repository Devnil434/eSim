"""
toolcare.logger - Central rotating audit logger for eSim ToolCare operations.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from toolcare.platform_utils import get_esim_config_dir

_LOGGER_INITIALIZED = False
_DEFAULT_LOG_FILE = get_esim_config_dir() / "logs" / "toolcare.log"


def setup_logging(
    log_file: Optional[Path] = None,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """
    Configure the central rotating logger for toolcare.
    Writes persistent audit trails to ~/.esim/logs/toolcare.log.
    """
    global _LOGGER_INITIALIZED
    if _LOGGER_INITIALIZED:
        return

    target_log = log_file or _DEFAULT_LOG_FILE
    try:
        target_log.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fall back to local logs if user home is not writable
        target_log = Path("toolcare.log")

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger("toolcare")
    root_logger.setLevel(level)

    # Avoid duplicate handlers on reloads
    if not root_logger.handlers:
        try:
            file_handler = RotatingFileHandler(
                filename=str(target_log),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            root_logger.addHandler(file_handler)
        except Exception:
            pass  # Fall back to standard stream if file permission denied

    _LOGGER_INITIALIZED = True


def get_logger(name: str = "toolcare") -> logging.Logger:
    """
    Get a namespaced logger instance.
    """
    if not _LOGGER_INITIALIZED:
        setup_logging()
    return logging.getLogger(f"toolcare.{name}" if not name.startswith("toolcare") else name)
