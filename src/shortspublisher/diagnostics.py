"""Entry-point logging configuration. No user data or credentials in log messages."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(directory: Path) -> logging.Logger:
    logger = logging.getLogger("shortspublisher")
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file = RotatingFileHandler(directory / "application.log", maxBytes=1_000_000, backupCount=3)
        file.setFormatter(formatter)
        logger.addHandler(file)
    except OSError:
        logger.warning("File logging unavailable; using stderr", exc_info=True)
    return logger
