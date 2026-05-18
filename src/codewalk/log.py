"""Shared logging utility for Codewalk.

Usage:
    from src.codewalk.log import log

    log("message here")  # prints to stderr + writes to codewalk.log
"""
import logging
import sys
from pathlib import Path

_log_dir = Path("data")
_log_dir.mkdir(exist_ok=True)

logger = logging.getLogger("codewalk")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler(_log_dir / "codewalk.log")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)


def log(msg: str):
    """Log to both stderr and file."""
    print(msg, file=sys.stderr)
    logger.info(msg)
