from __future__ import annotations
import logging
import sys
from typing import Optional


def setup_logging(level: int = logging.INFO) -> None:
    """Configures structured, colored console logging for TrueParse."""
    logger = logging.getLogger("ParsingEngine")
    logger.setLevel(level)

    # Avoid duplicate handlers if already initialized
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Also configure root logger level if not set
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# Automatically initialize default logging on import
setup_logging()
