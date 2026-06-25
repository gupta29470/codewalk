"""Speech-to-text transcription utilities for the voice interface."""
import sys

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

_whisper_model = None

def _get_whisper_model():
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

    Args:
        sample_rate: Audio sample rate (16kHz is what Whisper expects).
        silence_threshold: RMS level below which counts as silence.
        silence_duration: Seconds of silence before stopping.
        max_duration: Maximum recording length in seconds.

    Returns:
        numpy array of audio samples (float32, mono, 16kHz).
    """
    print("🎤 Recording... (will stop after 5 seconds of silence)", file=sys.stderr)

    chunks = []
    silent_chunks = 0
    chunk_size = int(sample_rate * 0.1) # 100ms chunks
    max_chunks = int(max_recording_duration / 0.1)
    silence_chunks_needed = int(silence_duration / 0.1)
    heard_speech = False

    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=chunk_size,
    )

    stream.start()

    try:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_size)
            chunks.append(chunk.copy())

            # Check if this chunk is silence
            rms = np.sqrt(np.mean(chunk ** 2))
            if rms < silence_threshold:
                silent_chunks += 1
            else:
                silent_chunks = 0
                heard_speech = True

            # Stop after enough silence, but ONLY after speech was detected.
            # This gives the user up to max_recording_duration to start talking.
            if heard_speech and silent_chunks >= silence_chunks_needed:
                break
    finally:
        stream.stop()
        stream.close()

    if not chunks:
        return np.array([], dtype=np.float32)
    
    audio = np.concatenate(chunks).flatten()
    print(f"📝 Recorded {len(audio) / sample_rate:.1f}s of audio", file=sys.stderr)
    return audio

def transcribe(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe audio numpy array to text.

    Args:
        audio: Float32 numpy array (mono, 16kHz).
        sample_rate: Sample rate of the audio.

    Returns:
        Transcribed text string.
    """

    if len(audio) == 0:
        return ""
    
    model = _get_whisper_model()
    segments, _ = model.transcribe(audio, language="en")
    text = " ".join(seg.text.strip() for seg in segments)
    return text.strip()

def transcribe_bytes(audio_bytes: bytes, file_name: str = "audio.webm") -> str:
    """Transcribe audio bytes (from browser/file upload) to text.

    Used by the FastAPI endpoint when receiving audio from the frontend.
    Writes to a temp file because faster-whisper needs a file path.
    """
    import tempfile
    import os

    suffix = "." + file_name.rsplit(".", 1)[-1] if "." in file_name else ".webm"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        model = _get_whisper_model()
        segments, _ = model.transcribe(tmp_path)
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass