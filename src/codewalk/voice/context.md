# `src/codewalk/voice/` — Voice Companion

This package adds speech-to-text and text-to-speech support for hands-free Codewalk interaction.

## Modules

| File | Role |
|------|------|
| `stt.py` | `record_audio()`, `transcribe()`, `transcribe_bytes()` — wraps `faster-whisper` or similar. |
| `tts.py` | `speak()`, `stop_speaking()`, `synthesize()` — wraps `edge-tts` or similar, outputs MP3. |
| `backends.py` | Backend dispatch for direct, MCP, and API execution paths. |

## Data flow

```
microphone audio
    ↓
transcribe() → text question
    ↓
codewalk tool call (via MCP/API/direct)
    ↓
synthesize() → MP3 summary
```

## Connections

- Used by API `/voice/ask` and MCP `codewalk_voice_ask`, `codewalk_speak`.
- Voice deps are optional; lazy-imported where possible.
