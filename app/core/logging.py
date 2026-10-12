from __future__ import annotations

import logging
import os
from logging.config import dictConfig
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Логи пишутся и в консоль, и в файл: окно сервера закрывают, а разбирать
# медленный или упавший поиск нужно потом. Папку можно переопределить через
# SMARTJOB_LOG_DIR; по умолчанию — logs/ в корне проекта.
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_FILE_NAME = "smartjob.log"
_LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
_LOG_FILE_BACKUPS = 5
_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
# Логгеры uvicorn не передают записи корневому логгеру — файл им подключается отдельно.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")


def log_file_path() -> Path:
    return Path(os.environ.get("SMARTJOB_LOG_DIR") or _DEFAULT_LOG_DIR) / _LOG_FILE_NAME


def setup_logging() -> None:
    """Configure structured logging to the console and a rotating log file."""
    path = log_file_path()
    handlers: dict[str, dict[str, object]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": "INFO",
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "level": "INFO",
            "filename": str(path),
            "maxBytes": _LOG_FILE_MAX_BYTES,
            "backupCount": _LOG_FILE_BACKUPS,
            "encoding": "utf-8",
        }
    except OSError:
        # Нет прав на папку — работаем только с консолью, запуск не ломаем.
        pass

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": _FORMAT}},
            "handlers": handlers,
            "root": {"handlers": list(handlers), "level": "INFO"},
        }
    )

    file_handler = next(
        (handler for handler in logging.getLogger().handlers if isinstance(handler, RotatingFileHandler)), None
    )
    if file_handler is not None:
        for name in _UVICORN_LOGGERS:
            uvicorn_logger = logging.getLogger(name)
            if file_handler not in uvicorn_logger.handlers:
                uvicorn_logger.addHandler(file_handler)


logger = logging.getLogger(__name__)
