import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from logging_config.logging_filters import RequestIDFilter


def setup_logging(log_file: str = "logs/app.log") -> None:
    """
    Logging strategy:
    - Application logs -> file (with request_id)
    - Uvicorn logs -> console only
    - No duplicate logs
    """

    logging.captureWarnings(True)

    # Ensure log directory exists
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # -------- FORMATTERS --------
    app_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - "
        "[request_id=%(request_id)s] - %(message)s"
    )

    uvicorn_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    )

    # -------- HANDLERS --------
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10_000_000,
        backupCount=5,
    )
    file_handler.setFormatter(app_formatter)
    file_handler.addFilter(RequestIDFilter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(uvicorn_formatter)

    # -------- ROOT LOGGER (APP LOGS) --------
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)

    # -------- UVICORN LOGGERS --------
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
