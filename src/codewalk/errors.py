"""Codewalk error handling — user-friendly error messages.

Maps raw Python exceptions to actionable messages users can understand.
Used by both REST API and MCP server.
"""


class CodewalkError(Exception):
    """Base error with a user-facing message."""
    def __init__(self, user_message: str, detail: str = ""):
        self.user_message = user_message
        self.detail = detail
        super().__init__(user_message)


# ─── Error classification ────────────────────────────────────────────

_ERROR_PATTERNS = [
    # (substring in exception message, user-friendly message)
    (
        "No codebase indexed",
        "No codebase analyzed yet. Run the analyze endpoint first, "
        "or in MCP: `@codewalk analyze this codebase`."
    ),
    (
        "No analysis data",
        "No codebase analyzed yet. Run the analyze endpoint first."
    ),
    (
        "collection",
        "Index not found. The codebase may not have been analyzed yet. "
        "Try running analyze first."
    ),
    (
        "Connection refused",
        "Can't reach the LLM. If using Ollama, run `ollama serve`. "
        "If using OpenAI/Anthropic, check your API key."
    ),
    (
        "ConnectError",
        "Can't reach the LLM. If using Ollama, run `ollama serve`. "
        "If using OpenAI/Anthropic, check your API key."
    ),
    (
        "api_key",
        "API key not set or invalid. Check your environment variables "
        "(OPENAI_API_KEY, ANTHROPIC_API_KEY, etc)."
    ),
    (
        "AuthenticationError",
        "API key is invalid. Check your environment variables."
    ),
    (
        "RateLimitError",
        "Rate limit hit. Wait a moment and try again, or switch to a local model (Ollama)."
    ),
    (
        "model_not_found",
        "LLM model not found. If using Ollama, run `ollama pull <model_name>`. "
        "If using a cloud provider, check the model name in your config."
    ),
    (
        "embedding",
        "Embedding model error. It downloads automatically (~1.5GB) on first run. "
        "Check your internet connection."
    ),
    (
        "chromadb",
        "Vector database error. Try deleting `.codewalk/chroma/` and re-analyzing."
    ),
    (
        "Permission denied",
        "Permission denied. Check file/directory permissions for the repo path."
    ),
    (
        "No such file or directory",
        "File or directory not found. Check that the path exists and is accessible."
    ),
    (
        "microphone",
        "Microphone access denied. Grant permission in System Settings → Privacy → Microphone."
    ),
]


def classify_error(exception: Exception) -> str:
    """Convert a raw exception to a user-friendly error message.

    Returns the friendly message if a pattern matches, otherwise
    returns a generic message with the original error.
    """
    error_str = str(exception).lower()
    error_type = type(exception).__name__.lower()

    for pattern, message in _ERROR_PATTERNS:
        if pattern.lower() in error_str or pattern.lower() in error_type:
            return message

    # Generic fallback
    return f"Something went wrong: {exception}"
