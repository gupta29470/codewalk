"""
=============================================================================
 tts.py - Text-to-Speech (Edge TTS + Audio Playback)
=============================================================================

WHAT THIS FILE DOES:
    1. synthesize(): Converts text to MP3 audio bytes using Microsoft Edge TTS
    2. speak(): Synthesizes + plays audio immediately (with Escape to stop)
    3. stop_speaking(): Kills any in-progress audio playback

HOW IT WORKS:
    - Uses edge-tts library (free, no API key, Microsoft neural voices)
    - Plays audio via macOS `afplay` command
    - Monitors keyboard for Escape key to interrupt
    - Cleans up temp files and processes on exit

WHERE IT'S CALLED:
    - companion.py -> speak() for voice responses
    - api/main.py -> synthesize() for web frontend audio

DEPENDENCIES:
    - edge_tts: Microsoft Edge neural TTS (free)
    - macOS afplay: built-in audio player

=============================================================================
"""

import asyncio
import atexit
import os
import sys
import tempfile
import subprocess
import edge_tts

DEFAULT_VOICE = "en-US-AriaNeural"

# Track current playback so it can be killed
_current_playback: subprocess.Popen | None = None
_current_tmp: str | None = None


async def _synthesize_async(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Generate MP3 audio bytes from text using edge-tts (async)."""
    if len(text) > 5000:
        text = text[:5000] + "... response truncated for voice."

    communicate = edge_tts.Communicate(text, voice)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks)


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Generate MP3 audio bytes from text (sync wrapper).

    Handles being called from within an existing event loop (MCP server)
    by running TTS in a separate thread.

    EXAMPLE TRACE:
        text   = "The analysis module builds structural understanding of the codebase."  (68 chars)
        voice  = "en-US-AriaNeural"
        # No running event loop → asyncio.run() path
        audio_bytes = asyncio.run(_synthesize_async(text, voice))
        # _synthesize_async: edge_tts.Communicate streams 12 audio chunks
        # audio_chunks = [b'\xff\xfb...', b'\xff\xfb...', ...]  (12 chunks)
        return → b'\xff\xfb\x90...'  (24576 bytes, ~1.5s of MP3 audio)
    """
    try:
        asyncio.get_running_loop()
        # Inside event loop - run in thread to avoid blocking
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _synthesize_async(text, voice)).result()
    except RuntimeError:
        # No loop running - call directly
        return asyncio.run(_synthesize_async(text, voice))


def speak(text: str, voice: str = DEFAULT_VOICE):
    """Synthesize and play audio immediately (CLI use).

    Saves to temp file, plays with afplay, monitors for Escape to stop.
    """
    global _current_playback, _current_tmp

    stop_speaking()  # Kill any already-playing audio

    audio_bytes = synthesize(text, voice)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
        tmp_file.write(audio_bytes)
        tmp_path = tmp_file.name

    _current_tmp = tmp_path
    _current_playback = subprocess.Popen(["afplay", tmp_path])

    # Wait for playback, checking for Escape key
    import select
    import termios
    import tty

    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while _current_playback and _current_playback.poll() is None:
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':  # Escape key
                        stop_speaking()
                        break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except (OSError, termios.error):
        # Not a terminal (MCP stdio) - just wait
        if _current_playback:
            _current_playback.wait()

    _current_playback = None
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


# Kill afplay when Python process exits
atexit.register(stop_speaking)