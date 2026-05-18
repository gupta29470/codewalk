import asyncio
import atexit
import os
import sys
import tempfile
import subprocess
import edge_tts

DEFAULT_VOICE = "en-US-AriaNeural"

# ── Track the current playback process so it can be killed on exit ──
_current_playback: subprocess.Popen | None = None
_current_tmp: str | None = None

async def _synthesize_async(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Generate MP3 audio bytes from text using edge-tts."""
    if len(text) > 5000:
        text = text[:5000] + "... response truncated for voice."

    communicate = edge_tts.Communicate(text, voice)
    # Collect audio chunks into bytes
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks)

def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Generate MP3 audio bytes from text (sync wrapper).

    Safe to call from inside an existing event loop (e.g. MCP server)
    — runs TTS in a separate thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
        # Inside an event loop (MCP server) — run in a new thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _synthesize_async(text, voice)).result()
    except RuntimeError:
        # No loop running — safe to call directly
        return asyncio.run(_synthesize_async(text, voice))

def speak(text: str, voice: str = DEFAULT_VOICE):
    """Generate audio and play it immediately (CLI use).

    Saves to temp file → plays with macOS afplay (built-in).
    Tracks the afplay process so stop_speaking() or process exit kills it.
    Listens for Escape key to interrupt playback.
    """
    global _current_playback, _current_tmp

    # Kill any already-playing audio first
    stop_speaking()

    audio_bytes = synthesize(text, voice)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
        tmp_file.write(audio_bytes)
        tmp_path = tmp_file.name

    _current_tmp = tmp_path
    # Launch afplay as a tracked subprocess (can be killed)
    _current_playback = subprocess.Popen(["afplay", tmp_path])

    # Wait for playback to finish, checking for stop signal
    import select
    import termios
    import tty

    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while _current_playback and _current_playback.poll() is None:
                # Check if a key was pressed (non-blocking)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':  # Escape key
                        stop_speaking()
                        break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except (OSError, termios.error):
        # Not a terminal (e.g. MCP stdio) — just wait normally
        if _current_playback:
            _current_playback.wait()

    _current_playback = None

    # Clean up temp file
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    _current_tmp = None


def stop_speaking():
    """Kill any currently-playing audio. Safe to call anytime."""
    global _current_playback, _current_tmp
    if _current_playback is not None:
        try:
            _current_playback.terminate()
            _current_playback.wait(timeout=2)
        except Exception:
            try:
                _current_playback.kill()
            except Exception:
                pass
        _current_playback = None
    if _current_tmp is not None:
        try:
            os.unlink(_current_tmp)
        except OSError:
            pass
        _current_tmp = None


# Kill afplay when the Python process exits (e.g. MCP cancelled)
atexit.register(stop_speaking)
