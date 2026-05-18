"""
=============================================================================
 companion.py - Voice Companion CLI Application
=============================================================================

WHAT THIS FILE DOES:
    Interactive CLI that lets you talk to Codewalk with your voice:
    1. Press Enter to start recording
    2. Speak your question ("what does the scanner do?")
    3. Whisper transcribes it
    4. Router picks the right tool
    5. Tool executes
    6. LLM summarizes result for speech
    7. Edge TTS speaks the answer

    Also provides format_voice_response() and summarize_for_speech() used
    by the API/MCP for adding voice output to tool results.

HOW IT WORKS:
    main() loop: input() -> record_audio() -> transcribe() -> route() ->
                 execute() -> summarize_for_speech() -> speak()

WHERE IT'S CALLED:
    - CLI: `python -m src.codewalk.voice.companion [--backend direct|mcp]`
    - format_voice_response() used by mcp/server.py for voice overlay

DEPENDENCIES:
    - stt.py: record_audio(), transcribe()
    - tts.py: speak()
    - router.py: route_with_ollama()
    - backends.py: execute_direct() or execute_mcp_sync()
    - config.py: get_llm() for narrative summarization

=============================================================================
"""

import argparse
import json
import sys
import httpx
import re

from src.codewalk.voice.stt import record_audio, transcribe
from src.codewalk.voice.tts import speak
from src.codewalk.voice.router import route, route_with_ollama
from src.codewalk.voice.backends import execute_direct, execute_mcp_sync


# =============================================================================
# Text Cleaning for TTS
# =============================================================================

def _clean_for_speech(text: str) -> str:
    """Strip markdown, file paths, and code artifacts so TTS sounds natural.

    Removes: code blocks, inline code, bold/italic, file paths, headers, list markers.
    """
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"[\w./\\-]+/(\w+)\.\w+", r"\1", text)
    text = re.sub(r"(\w+)\.\w{1,4}\b", r"\1", text)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"^\s*[-*]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"SECTION \d+:?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# =============================================================================
# Voice Response Formatting
# =============================================================================

def format_voice_response(raw_result: str) -> dict:
    """Take raw tool output and produce a spoken narrative summary via LLM.

    Returns:
        {"technical": raw_result, "speech": "narrative explanation..."}

    Short results (<300 chars) are just cleaned for speech.
    Long results go through LLM to produce a teaching narrative.
    """
    from src.codewalk.config import get_llm

    if len(raw_result) <= 300:
        return {"technical": raw_result, "speech": _clean_for_speech(raw_result)}

    try:
        llm = get_llm(temperature=0.3)
        response = llm.invoke([
            {"role": "system", "content": (
                "You receive raw output from a codebase analysis tool. "
                "Your job: produce ONLY a spoken narrative for TTS.\n\n"
                "You are giving a live demo to a new team member.\n\n"
                "RULES:\n"
                "- ONLY plain words and numbers. No markdown, no code, no paths.\n"
                "- Lead with the PATTERN or ARCHITECTURE.\n"
                "- Explain the FLOW with cause-and-effect.\n"
                "- 3-6 sentences. Sound like a senior dev at a whiteboard.\n"
                "- NEVER start with 'here is' or 'the output shows'."
            )},
            {"role": "user", "content": raw_result[:4000]},
        ])

        speech = response.content if hasattr(response, "content") else str(response)
        speech = _clean_for_speech(speech)
        return {"technical": raw_result, "speech": speech}

    except Exception:
        return {"technical": raw_result, "speech": _clean_for_speech(raw_result)[:500]}


def summarize_for_speech(text: str) -> str:
    """Convenience wrapper - returns just the speech portion."""
    return format_voice_response(text)["speech"]


# =============================================================================
# CLI Main Loop
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend", choices=["direct", "mcp"], default="direct",
        help="Tool execution backend (default: direct import)",
    )
    args = parser.parse_args()

    execute = execute_direct if args.backend == "direct" else execute_mcp_sync

    print("=" * 50)
    print("  Codewalk Voice Companion")
    print("  Press Enter to speak, Ctrl+C to quit")
    print(f"  Backend: {args.backend}")
    print("=" * 50)

    # Auto-initialize: load existing index
    print("\nLoading codebase analysis...")
    init_result = execute("codewalk_analyze_codebase", {})
    if "error" in init_result.lower():
        print(f"Warning: {init_result}")
    else:
        for line in init_result.split("\n"):
            if line.strip():
                print(f"  {line}")
    print("Ready!\n")

    while True:
        try:
            input("\n  Press Enter to speak...")

            # 1. Record from mic
            audio = record_audio()
            if len(audio) == 0:
                print("No audio captured.")
                continue

            # 2. Transcribe with Whisper
            transcript = transcribe(audio)
            if not transcript.strip():
                print("Couldn't understand that.")
                continue
            print(f"You said: \"{transcript}\"")

            # 3. Route to tool
            print("Routing...")
            route_result = route_with_ollama(transcript)
            tool_name = route_result.get("tool")
            arguments = route_result.get("arguments", {})

            if not tool_name:
                msg = "Sorry, I couldn't map that to a Codewalk tool. Try again?"
                print(msg)
                speak(msg)
                continue

            print(f"Calling {tool_name}({arguments})")

            # 4. Execute tool
            result = execute(tool_name, arguments)
            print(f"Result preview: {result[:200]}...")

            # 5. Summarize for speech
            speech_text = summarize_for_speech(result)

            # 6. Speak the response
            print("Speaking...")
            speak(speech_text)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            sys.exit(0)
        except Exception as e:
            print(f"Error: {e}")
            speak(f"Sorry, something went wrong: {str(e)[:100]}")


if __name__ == "__main__":
    main()