import shutil
from pathlib import Path as _P

def atomic_swap(incoming: str, latest: str):
    """Replace latest/ with incoming/ atomically.

    Flow: incoming/ is fully written → backup latest/ → rename incoming → latest
    If step 2 fails, rolls back backup so latest/ is never lost.
    """
    inc = _P(incoming)
    lat = _P(latest)
    old = _P(str(latest) + "_old")

    if not inc.is_dir():
        raise ValueError(f"incoming must be a directory: {incoming}")

    # Step 1: Backup current
    if lat.exists():
        lat.rename(old)

    try:
        # Step 2: Promote incoming
        inc.rename(lat)
    except Exception:
        # Step 2 FAILED — restore backup so latest/ is never missing
        if old.exists():
            old.rename(lat)
        raise
    finally:
        # Step 3: Clean up backup (ignore errors)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)


