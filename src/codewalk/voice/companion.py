import argparse
import json
import sys
import httpx

from src.codewalk.voice.stt import record_audio, transcribe
from src.codewalk.voice.tts import speak
from src.codewalk.voice.router import route, route_with_ollama
from src.codewalk.voice.backends import execute_direct, execute_mcp_sync
import re


def _clean_for_speech(text: str) -> str:
    """Strip markdown, file paths, and code artifacts so TTS sounds natural."""
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove markdown bold/italic
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    # Replace file paths (foo/bar/baz.py) with just the filename without extension
    text = re.sub(r"[\w./\\-]+/(\w+)\.\w+", r"\1", text)
    # Remove standalone .ext from remaining filenames like "scanner.py"
    text = re.sub(r"(\w+)\.\w{1,4}\b", r"\1", text)
    # Remove markdown headers
    text = re.sub(r"#{1,6}\s*", "", text)
    # Remove list markers
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.MULTILINE)
    # Remove section labels
    text = re.sub(r"SECTION \d+:?\s*", "", text, flags=re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def format_voice_response(raw_result: str) -> dict:
    """Take raw tool output and produce a spoken narrative summary via the main LLM.

    Returns:
        {"technical": raw_result, "speech": "narrative teaching explanation ..."}

    The raw result is displayed as-is in Copilot. The speech is a narrative overlay for TTS.
    """
    from src.codewalk.config import get_llm

    # Short results don't need LLM processing
    if len(raw_result) <= 300:
        return {"technical": raw_result, "speech": _clean_for_speech(raw_result)}

    try:
        llm = get_llm(temperature=0.3)
        response = llm.invoke([
            {"role": "system", "content": (
                "You receive raw output from a codebase analysis tool. "
                "Your job: produce ONLY a spoken narrative that will be read aloud by a TTS engine.\n\n"
                "You are giving a live demo to a new team member. Explain what this code DOES, "
                "how the pieces connect, and what pattern/architecture it follows.\n\n"
                "STRICT RULES:\n"
                "- ONLY plain words and numbers. Nothing else.\n"
                "- FORBIDDEN characters: * _ ` # ~ [ ] ( ) { } | / \\ > < @ ! $ % ^ & = + ;\n"
                "- NO markdown, NO emoji, NO code, NO URLs, NO file paths.\n"
                "- NEVER name files directly. Say 'the events layer', 'the state handler', "
                "'the service', 'the entry point'.\n"
                "- Lead with the PATTERN or ARCHITECTURE: 'This follows the BLoC pattern', "
                "'This is a repository layer', 'This uses pub-sub'.\n"
                "- Then explain the FLOW with cause-and-effect: 'when a user does X, "
                "that fires an event, which triggers the handler to update state, "
                "and the UI rebuilds automatically'.\n"
                "- Use connectors: 'first, then, this means, so when, because of that, "
                "which in turn'.\n"
                "- 3-6 sentences. Tell a story, not a list.\n"
                "- Sound like a senior dev walking someone through a whiteboard.\n"
                "- Include enough technical substance that the listener learns something — "
                "mention class roles, design decisions, data flow direction.\n"
                "- NEVER start with 'here is' or 'the output shows' or 'based on the analysis'."
            )},
            {"role": "user", "content": raw_result[:4000]},
        ])

        speech = response.content if hasattr(response, "content") else str(response)
        # Safety net: clean any markdown/paths that slipped through
        speech = _clean_for_speech(speech)

        return {"technical": raw_result, "speech": speech}

    except Exception:
        # LLM unavailable — clean raw result for speech
        return {"technical": raw_result, "speech": _clean_for_speech(raw_result)[:500]}


def summarize_for_speech(text: str) -> str:
    """Convenience wrapper — returns just the speech portion.

    Used by the CLI companion and anywhere that only needs the spoken text.
    """
    return format_voice_response(text)["speech"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend",
        choices=["direct", "mcp"],
        default="direct",
        help="Tool execution backend (default: direct import)",
    )

    args = parser.parse_args()

    execute = execute_direct if args.backend == "direct" else execute_mcp_sync

    print("=" * 50)
    print("  Codewalk Voice Companion")
    print("  Press Enter to speak, Ctrl+C to quit")
    print(f"  Backend: {args.backend}")
    print("=" * 50)

    # Auto-initialize: load existing index + rebuild analysis cache
    print("\n🔄 Loading codebase analysis...")
    init_result = execute("codewalk_analyze_codebase", {})
    if "error" in init_result.lower():
        print(f"⚠️  {init_result}")
    else:
        # Print just the key info lines
        for line in init_result.split("\n"):
            if line.strip() and not line.startswith("⏩"):
                print(f"  {line}")
    print("✅ Ready!\n")

    while True:
        try:
            input("\n⏎  Press Enter to speak...")

            # 1. Record
            audio = record_audio()
            if len(audio) == 0:
                print("❌ No audio captured.")
                continue

            # 2. Transcribe
            transcript = transcribe(audio)
            if not transcript.strip():
                print("❌ Couldn't understand that.")
                continue
            print(f"📝 You said: \"{transcript}\"")

            # 3. Route
            print("🧠 Routing...")
            route_result = route_with_ollama(transcript)
            tool_name = route_result.get("tool")
            arguments = route_result.get("arguments", {})

            if not tool_name:
                msg = "Sorry, I couldn't map that to a Codewalk tool. Try again?"
                print(f"❌ {msg}")
                speak(msg)
                continue

            print(f"⚙️  Calling {tool_name}({arguments})")

            # 4. Execute
            result = execute(tool_name, arguments)
            print(f"📄 Result preview: {result[:200]}...")

            # 5. Summarize for speech if needed
            speech_text = summarize_for_speech(result)

            # 6. Speak
            print("🔊 Speaking...")
            speak(speech_text)

        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            sys.exit(0)
        except Exception as e:
            print(f"❌ Error: {e}")
            speak(f"Sorry, something went wrong: {str(e)[:100]}")

if __name__ == "__main__":
    main()
