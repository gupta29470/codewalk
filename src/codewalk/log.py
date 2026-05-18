"""
=============================================================================
 log.py — Shared Logging Utility
=============================================================================

WHAT THIS FILE DOES:
    Provides ONE function — log() — that ALL other files use to print messages.
    Every log message goes to TWO places simultaneously:
      1. stderr (appears in terminal)
      2. data/codewalk.log file (persists for debugging later)

WHY NOT JUST print()?
    - print() goes to stdout, which MCP captures as tool output. If you print
      debug messages to stdout, the MCP client thinks it's part of the response.
    - stderr is visible in the terminal but ignored by MCP protocol.
    - The file logger gives you history — if something broke at 3am, you can
      read data/codewalk.log tomorrow to see what happened.

REAL-WORLD ANALOGY:
    Like a flight's black box recorder. The pilots (developers) see messages
    on their dashboard (stderr), AND everything is recorded to a box (log file)
    for later investigation.

WHO USES THIS:
    Every single module: scanner.py, chunker.py, server.py, etc.
    They all do: from src.codewalk.log import log as _log
    Convention: alias as _log to indicate "internal/debug use only"

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging      # Python's built-in logging framework
import sys          # Needed for sys.stderr
from pathlib import Path  # Object-oriented file path handling

# ─── Setup: Create log directory and logger ──────────────────────────

# Create data/ folder if it doesn't exist.
# This is where codewalk.log will live.
# exist_ok=True means "don't crash if folder already exists"
_log_dir = Path("data")
_log_dir.mkdir(exist_ok=True)

# Get (or create) a logger named "codewalk"
# Python's logging module reuses loggers by name — if another file also
# calls getLogger("codewalk"), they get the SAME logger instance.
logger = logging.getLogger("codewalk")
logger.setLevel(logging.INFO)  # Accept INFO level and above (not DEBUG)

# Only add handlers if they haven't been added yet.
# Without this check, importing this module multiple times would add
# duplicate handlers, causing each message to print 2x, 3x, etc.
if not logger.handlers:
    # FileHandler: writes log messages to a file on disk
    fh = logging.FileHandler(_log_dir / "codewalk.log")
    # Format: "14:32:07 | [scanner] Found 150 files"
    fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)


# =============================================================================
# log() — The Only Function in This File
# =============================================================================

def log(msg: str):
    """Print a message to stderr AND write it to the log file.

    EXECUTION FLOW:
        1. print(msg, file=sys.stderr)
           → appears in your terminal immediately
           → MCP ignores it (MCP only reads stdout)

        2. logger.info(msg)
           → the FileHandler writes it to data/codewalk.log
           → format: "14:32:07 | your message here"

    Args:
        msg: Any string message. Convention:
             "[module_name] What happened"
             Example: "[scanner] Scanned /Users/dev/repo → 150 files"
    """
    print(msg, file=sys.stderr)
    logger.info(msg)
