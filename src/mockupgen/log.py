"""Central logging for mockupgen — easy to turn up/down while debugging."""

from __future__ import annotations

import logging
import sys

LOG_NAME = "mockupgen"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under the mockupgen namespace."""
    full = LOG_NAME if name is None else f"{LOG_NAME}.{name}"
    return logging.getLogger(full)


def setup_logging(level: int | str = logging.INFO, stream: bool = True) -> None:
    """Configure root mockupgen logging.

    Call once at program start, e.g.::

        from mockupgen.log import setup_logging
        setup_logging("DEBUG")   # or logging.DEBUG
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(level)

    # Avoid duplicate handlers if called twice
    if logger.handlers:
        for h in logger.handlers:
            h.setLevel(level)
        return

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if stream:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    logger.debug("Logging initialised at level %s", logging.getLevelName(level))
