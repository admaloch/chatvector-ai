import logging
import logging.handlers
from backend.core.config import config
from backend.core.logging_utils import RequestIDFilter, RequestIDFormatter
import sys
import time
from pathlib import Path
import logging




def setup_logging():
    """Configure application logging.

    - Creates a `logs/` directory at the repository root (two parents above this file).
    - Adds a rotating file handler (max 5MB, 5 backups).
    - Adds a console handler (stdout).
    - Wires `uvicorn` loggers separately.
    - Adds request_id only to app-level logs.
    - Timestamps use local time by default; set `LOG_USE_UTC=true` to use UTC.
    """

    # --- Logs directory ---
    repo_root = Path(__file__).resolve().parents[2]
    logs_dir = repo_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)

    # --- Timestamp conversion ---
    if getattr(config, "LOG_USE_UTC", False):
        logging.Formatter.converter = time.gmtime
    else:
        logging.Formatter.converter = time.localtime

    # --- App log format ---
    app_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(request_id_part)s - %(message)s"

    # --- Handlers ---
    # Console
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(app_fmt))
    stream_handler.setLevel(log_level)
    stream_handler.addFilter(RequestIDFilter())

    # Rotating file
    file_path = logs_dir / "app.log"
    file_handler = logging.handlers.RotatingFileHandler(
        str(file_path), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(app_fmt))
    file_handler.setLevel(log_level)
    file_handler.addFilter(RequestIDFilter())

    # --- Root logger (app-level logs only) ---
    logging.basicConfig(level=log_level, handlers=[stream_handler, file_handler], force=True)

    # --- Uvicorn loggers (no request_id filter) ---
    uvicorn_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    uvicorn_formatter = logging.Formatter(uvicorn_fmt)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            h.setFormatter(uvicorn_formatter)  # reformat existing handlers
        lg.setLevel(log_level)
        lg.propagate = False

    logging.getLogger(__name__).debug(f"Logging configured. logs_dir={logs_dir} level={log_level}")

