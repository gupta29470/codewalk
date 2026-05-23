"""
=============================================================================
 errors.py - User-Friendly Error Handling
=============================================================================

WHAT THIS FILE DOES:
    Maps raw Python exceptions to actionable, user-friendly error messages.
    Instead of showing users cryptic tracebacks, this translates common
    failures into messages that tell you WHAT WENT WRONG and HOW TO FIX IT.

HOW IT WORKS:
    1. CodewalkError - base exception with a user_message field
    2. _ERROR_PATTERNS - lookup table of (substring -> friendly message)
    3. classify_error() - matches any exception against the pattern table

REAL-WORLD ANALOGY:
    Like a customer service rep who translates "ERROR 0x80070005" into
    "You need to run this as administrator."

WHERE IT'S CALLED:
    - api/main.py: wraps endpoint errors into HTTP error responses
    - mcp/server.py: wraps tool errors into MCP error responses

=============================================================================
"""


class CodewalkError(Exception):
    """Base error with a user-facing message.

    All codewalk-specific errors inherit from this.
    user_message = what the user sees
    detail = optional technical detail for logs
    """
    def __init__(self, user_message: str, detail: str = ""):
        self.user_message = user_message
        self.detail = detail
        super().__init__(user_message)


# =============================================================================
# Error Classification Table
# =============================================================================
# Each entry: (substring to match in exception message, friendly message)
# The classify_error() function scans this list top-to-bottom.

_ERROR_PATTERNS = [
    # --- No data indexed yet ---
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

    # --- LLM connection issues ---
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

    # --- API key issues ---
    (
        "api_key",
        "API key not set or invalid. Check your environment variables "
        "(OPENAI_API_KEY, ANTHROPIC_API_KEY, etc)."
    ),
    (
        "AuthenticationError",
        "API key is invalid. Check your environment variables."
    ),

    # --- Rate limits ---
    (
        "RateLimitError",
        "Rate limit hit. Wait a moment and try again, or switch to a local model (Ollama)."
    ),

    # --- Model not found ---
    (
        "model_not_found",
        "LLM model not found. If using Ollama, run `ollama pull <model_name>`. "
        "If using a cloud provider, check the model name in your config."
    ),

    # --- Embedding/vector issues ---
    (
        "embedding",
        "Embedding model error. It downloads automatically (~1.5GB) on first run. "
        "Check your internet connection."
    ),
    (
        "chromadb",
        "Vector database error. Try deleting `.codewalk/chroma/` and re-analyzing."
    ),

    # --- File system ---
    (
        "Permission denied",
        "Permission denied. Check file/directory permissions for the repo path."
    ),
    (
        "No such file or directory",
        "File or directory not found. Check that the path exists and is accessible."
    ),

    # --- Hardware (voice) ---
    (
        "microphone",
        "Microphone access denied. Grant permission in System Settings -> Privacy -> Microphone."
    ),
]


# =============================================================================
# classify_error() - The Main Function
# =============================================================================

def classify_error(exception: Exception) -> str:
    """Convert a raw exception to a user-friendly error message.

    Scans _ERROR_PATTERNS for a substring match against the exception's
    string representation or type name.

    Returns:
        Friendly message if a pattern matches.
        Generic "Something went wrong: <error>" otherwise.

    EXAMPLES:
        classify_error(ConnectionRefusedError("Connection refused"))
          error_str = "connection refused"
          error_type = "connectionrefusederror"
          pattern "Connection refused" → "connection refused" in error_str → True
          returns "Can't reach the LLM. If using Ollama, run `ollama serve`..."

        classify_error(ValueError("No codebase indexed"))
          error_str = "no codebase indexed"
          pattern "No codebase indexed" → match
          returns "No codebase analyzed yet. Run the analyze endpoint first..."

        classify_error(RuntimeError("disk full"))
          error_str = "disk full"
          No pattern matches → fallback
          returns "Something went wrong: disk full"
    """
    error_str = str(exception).lower()
    error_type = type(exception).__name__.lower()

    for pattern, message in _ERROR_PATTERNS:
        if pattern.lower() in error_str or pattern.lower() in error_type:
            return message

    # Generic fallback
    return f"Something went wrong: {exception}"
