"""
=============================================================================
 stt.py - Speech-to-Text (Microphone Recording + Whisper Transcription)
=============================================================================

WHAT THIS FILE DOES:
    1. record_audio(): Records from microphone until silence detected
    2. transcribe(): Converts audio numpy array to text via faster-whisper
    3. transcribe_bytes(): Converts audio file bytes to text (for web upload)

HOW IT WORKS:
    - Records in 100ms chunks, monitors RMS level
    - Stops after silence_duration seconds of quiet AFTER speech detected
    - Uses faster-whisper (CTranslate2 optimized) with "small" model
    - Model loaded lazily on first call (singleton pattern)

WHERE IT'S CALLED:
    - companion.py -> main loop: record_audio() then transcribe()
    - api/main.py -> /voice/transcribe endpoint uses transcribe_bytes()

DEPENDENCIES:
    - sounddevice: microphone access
    - numpy: audio buffer operations
    - faster_whisper: Whisper speech recognition

=============================================================================
"""

import sys
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

# Singleton Whisper model (lazy-loaded, ~500MB download on first use)
_whisper_model = None


def _get_whisper_model():
    """Get or create the Whisper model (singleton)."""
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel("small", compute_type="int8")
    return _whisper_model


def record_audio(
    sample_rate: int = 16000,
    silence_threshold: float = 0.01,
    silence_duration: float = 5.0,
    max_recording_duration: float = 30.0,
) -> np.ndarray:
    """Record audio from mic until silence is detected.

    ALGORITHM:
        1. Open mic stream at 16kHz mono
        2. Read 100ms chunks in a loop
        3. Calculate RMS (volume) of each chunk
        4. Once speech detected (RMS > threshold), start silence counter
        5. Stop when silence lasts silence_duration seconds
        6. Hard cap at max_recording_duration

    EXAMPLE TRACE (3.2 seconds of speech, then silence):
        chunk_size              = int(16000 * 0.1)  = 1600 samples per chunk
        max_chunks              = int(30.0 / 0.1)   = 300
        silence_chunks_needed   = int(5.0 / 0.1)    = 50

        Chunk loop:
            chunk 1:  rms=0.003  < 0.01 → silent_chunks=1,  heard_speech=False
            chunk 5:  rms=0.045  > 0.01 → silent_chunks=0,  heard_speech=True   # speech starts
            chunk 37: rms=0.038  > 0.01 → silent_chunks=0,  heard_speech=True   # still talking
            chunk 38: rms=0.002  < 0.01 → silent_chunks=1,  heard_speech=True   # silence begins
            chunk 87: silent_chunks=50 >= 50 → break                             # 5s silence → stop

        audio = np.concatenate(chunks).flatten()  → shape=(139200,) = 8.7 seconds
        return → np.ndarray(shape=(139200,), dtype=float32)

    Returns:
        numpy array of audio samples (float32, mono, 16kHz).
        Empty array if no audio captured.
    """
    print("🎤 Recording... (will stop after 5 seconds of silence)", file=sys.stderr)

    chunks = []
    silent_chunks = 0
    chunk_size = int(sample_rate * 0.1)  # 100ms chunks
    max_chunks = int(max_recording_duration / 0.1)
    silence_chunks_needed = int(silence_duration / 0.1)
    heard_speech = False

    stream = sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="float32", blocksize=chunk_size
    )
    stream.start()

    try:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_size)
            chunks.append(chunk.copy())

            rms = np.sqrt(np.mean(chunk ** 2))
            if rms < silence_threshold:
                silent_chunks += 1
            else:
                silent_chunks = 0
                heard_speech = True

            # Only stop after speech was detected (gives time to start talking)
            if heard_speech and silent_chunks >= silence_chunks_needed:
                break
    finally:
        stream.stop()
        stream.close()

    if not chunks:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(chunks).flatten()
    print(f"Recorded {len(audio) / sample_rate:.1f}s of audio", file=sys.stderr)
    return audio


def transcribe(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe audio numpy array to text using faster-whisper.

    EXAMPLE TRACE:
        audio     = np.ndarray(shape=(51200,), dtype=float32)  # 3.2s of speech
        model     = _get_whisper_model()  → WhisperModel("small", compute_type="int8")
        segments  = model.transcribe(audio, language="en")  → [Segment(text=" what does"), Segment(text=" the scanner do")]
        text      = "what does" + " " + "the scanner do"  → "what does the scanner do"
        return → "what does the scanner do"
    """
    if len(audio) == 0:
        return ""
    model = _get_whisper_model()
    segments, _ = model.transcribe(audio, language="en")
    text = " ".join(seg.text.strip() for seg in segments)
    return text.strip()


def transcribe_bytes(audio_bytes: bytes, file_name: str = "audio.webm") -> str:
    """Transcribe audio bytes (from browser upload) to text.

    Writes to temp file because faster-whisper needs a file path.
    Used by the FastAPI /voice/transcribe endpoint.
    """
    import tempfile
    import os

    suffix = "." + file_name.rsplit(".", 1)[-1] if "." in file_name else ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        model = _get_whisper_model()
        segments, _ = model.transcribe(tmp_path)
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
    finally:
        os.unlink(tmp_path)