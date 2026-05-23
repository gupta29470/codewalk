"""
=============================================================================
 config.py — Central Configuration & LLM Factory
=============================================================================

WHAT THIS FILE DOES:
    1. Defines ALL settings for the entire app (LLM provider, model names,
       API keys, repo path, etc.) in one place.
    2. Reads settings from environment variables or a .env file.
    3. Provides a factory function get_llm() that returns the correct
       LLM client based on the configured provider.

REAL-WORLD ANALOGY:
    Think of this as the "Settings app" on your phone. Every feature in the
    app checks this single place for configuration. Want to switch from
    Ollama to OpenAI? Change ONE environment variable — the entire app adapts.

WHY THIS DESIGN?
    - Single source of truth: no hardcoded API keys scattered across files
    - Environment-based: works in dev (.env file) and production (env vars)
    - Factory pattern: get_llm() hides provider complexity — callers just
      say "give me an LLM" without caring if it's Ollama, OpenAI, etc.

DEPENDENCIES:
    - pydantic_settings: Auto-loads from .env files + validates types
    - langchain: Provides the BaseChatModel interface all LLMs implement
=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

# os.getenv() reads environment variables from the system
# Example: os.getenv("HOME") → "/Users/amadhavl"
import os

# BaseSettings from pydantic auto-validates settings AND reads .env files
# It's like a smart config loader — catches type errors at startup, not runtime
from pydantic_settings import BaseSettings

# BaseChatModel is the INTERFACE all LLM clients implement.
# Think of it like a USB port — any device (Ollama, OpenAI, Claude) can plug in
# because they all follow the same shape (BaseChatModel).
from langchain_core.language_models.chat_models import BaseChatModel


# =============================================================================
# Settings Class — Every configurable value lives here
# =============================================================================

class Settings(BaseSettings):
    """All app settings. Each field reads from an environment variable.

    HOW IT WORKS:
        os.getenv("KEY", "default") → reads env var KEY, falls back to "default".
        Users override settings by:
          1. Setting env vars: export LLM_PROVIDER=openai
          2. Adding to .env file: LLM_PROVIDER=openai
          3. Both work — env vars take priority over .env file.

    REAL-WORLD ANALOGY:
        Like a restaurant menu with defaults. The chef (app) uses the menu
        settings, but the customer (user) can say "actually, make it spicy"
        (override via env var).
    """

    # ── LLM Settings ─────────────────────────────────────────────────

    # Which LLM SERVICE to use.
    # "ollama" = runs locally on your Mac/PC (free, private, no internet needed)
    # "openai" = cloud GPT-4 (fast, costs money, needs API key)
    # Others: anthropic, gemini, groq, openrouter
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")

    # Which specific MODEL within that provider.
    # "qwen3.5:27b" = Alibaba's 27-billion parameter model running locally via Ollama.
    # If using openai, you'd set this to "gpt-4o" or "gpt-4o-mini".
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.5:27b")

    # ── Embedding Model ──────────────────────────────────────────────

    # WHAT ARE EMBEDDINGS?
    #   They convert text → a list of numbers (vector).
    #   Similar code → similar vectors → findable via similarity search.
    #   Example: embed("async function login") ≈ embed("authenticate user") 
    #            because they're semantically similar.
    #
    # WHY JINA CODE EMBEDDINGS?
    #   General-purpose models (like OpenAI's) treat "def main()" like English text.
    #   Jina's CODE model understands programming structures — it knows that
    #   "class AuthService" relates to "login()" even without shared words.
    #
    # "1.5b" = 1.5 billion parameters. Downloads ~1.5GB on first run.
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "jinaai/jina-code-embeddings-1.5b")

    # ── API Keys ─────────────────────────────────────────────────────
    # Only needed if using cloud providers. Empty string = not configured.
    # SECURITY: Never hardcode these. Always use env vars or .env file.
    # .env file should be in .gitignore so keys never reach GitHub.
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")

    # ── Repository Path ──────────────────────────────────────────────

    # WHAT: Which folder of source code to analyze.
    # "." → current directory (default: analyze the project you're in)
    # "src/codewalk" → relative path (analyze a subdirectory)
    # "/Users/you/other-project/src" → absolute path (analyze ANY repo)
    #
    # HOW IT'S USED: scanner.py reads this to know WHERE to look for files.
    repo_path: str = os.getenv("REPO_PATH", ".")

    # ── Exclude Paths ────────────────────────────────────────────────

    # Comma-separated list of extra paths to skip during indexing.
    # This is IN ADDITION to the built-in skip patterns in file_filter.py.
    # Example: "tests,docs,scripts/legacy" → these folders won't be embedded.
    # Useful for project-specific exclusions without editing source code.
    exclude_paths: str = os.getenv("EXCLUDE_PATHS", "")

    # ── LLM Filter Toggle ────────────────────────────────────────────

    # WHAT: Should we ask an LLM to filter files before embedding?
    # True: After scanning, LLM reviews file list → "skip README, keep src/*.py"
    #       Smarter (won't waste tokens on non-code) but slower + costs LLM tokens.
    # False: Use only regex/pattern matching from file_filter.py. Fast but may
    #        include files that aren't useful code (config, docs).
    #
    # The .lower() in ("true", "1", "yes") trick:
    #   Converts env var string to boolean. Handles: "True", "TRUE", "true", "1", "yes"
    use_llm_filter: bool = os.getenv("USE_LLM_FILTER", "true").lower() in ("true", "1", "yes")

    # ── Review Guidelines Path ───────────────────────────────────────

    # Path to a folder containing team coding standards documents.
    # During code review, these docs are embedded and used as context so
    # the reviewer can say "this violates our team's error handling convention."
    review_guidelines_path: str = os.getenv("REVIEW_GUIDELINES_PATH", "review_guidelines")

    class Config:
        """Pydantic config: tells BaseSettings to also read from a .env file.

        The .env file lives at project root and looks like:
            LLM_PROVIDER=openai
            OPENAI_API_KEY=sk-abc123...
            REPO_PATH=/Users/you/some-project
        """
        env_file = ".env"


# ─── Singleton Instance ──────────────────────────────────────────────

# Created ONCE when this module is first imported.
# Every other file does: from src.codewalk.config import settings
# They all share this SAME instance — no re-reading env vars.
settings = Settings()


# =============================================================================
# get_llm() — Factory Function
# =============================================================================

def get_llm(temperature: float = 0, **kwargs) -> BaseChatModel:
    """Create and return the correct LLM client based on the configured provider.

    WHAT IS A FACTORY FUNCTION?
        A function that CREATES objects. Instead of every file knowing HOW to
        create each LLM type (import paths, API keys, configs), they just call:
            llm = get_llm()
        This function handles all the messy details internally.

    REAL-WORLD ANALOGY:
        Like a car rental counter. You say "I need a car." You don't care
        if they give you a Toyota or BMW — you just need something that drives.
        get_llm() is the rental counter for language models.

    Args:
        temperature: Controls randomness of LLM responses (float 0.0 to 1.0).
            0.0 = always pick the most likely next word (deterministic)
                  Best for code analysis — you want precise, repeatable answers.
            1.0 = more random/creative selection
                  Good for brainstorming, bad for code review.

        **kwargs: Extra provider-specific arguments passed through.
            Example: reasoning=False → Ollama-specific flag that disables
                     qwen3.5's <think>...</think> tags in output.

    Returns:
        A LangChain chat model instance. Regardless of provider, it has:
            .invoke([messages]) → response
            .stream([messages]) → chunks
        All providers implement BaseChatModel, so the rest of the app
        doesn't care which one is behind the scenes.

    EXAMPLE TRACE (provider = "ollama"):
        settings.llm_provider = "ollama"
        settings.llm_model = "qwen3.5:27b"
        provider = "ollama"

        → from langchain_ollama import ChatOllama
        → returns ChatOllama(model="qwen3.5:27b", temperature=0, reasoning=False)

    EXAMPLE TRACE (provider = "openai"):
        settings.llm_provider = "openai"
        settings.llm_model = "gpt-4o"
        settings.openai_api_key = "sk-abc123..."
        provider = "openai"

        → from langchain_openai import ChatOpenAI
        → returns ChatOpenAI(model="gpt-4o", temperature=0, api_key="sk-abc123...")
    """
    # Read which provider was configured
    provider = settings.llm_provider.lower()  # .lower() handles "Ollama", "OLLAMA", "ollama"

    # ── Provider-Specific Key Filtering ──────────────────────────────
    # Problem: "reasoning=False" only makes sense for Ollama's qwen models.
    #          If we pass it to OpenAI's API, it crashes with "unknown parameter".
    # Solution: Define which kwargs are Ollama-only, strip them for other providers.
    ollama_only_keys = {"reasoning"}

    # ── Ollama (Local LLM) ───────────────────────────────────────────
    # WHAT: Runs LLMs on YOUR computer. No internet needed after model download.
    # SETUP: 1) Install ollama  2) ollama serve  3) ollama pull qwen3.5:27b
    # PROS: Free, private (code never leaves your machine), fast for small models
    # CONS: Needs good hardware (GPU or M-series Mac), large models are slow
    if provider == "ollama":
        from langchain_ollama import ChatOllama  # Lazy import: only loads library if actually needed
        return ChatOllama(
            model=settings.llm_model,       # e.g. "qwen3.5:27b"
            temperature=temperature,         # 0 for code analysis
            **kwargs,                        # Pass ALL kwargs including reasoning=False
        )

    # For all CLOUD providers, remove Ollama-specific kwargs to prevent errors
    # dict comprehension: keeps only keys that are NOT in ollama_only_keys
    filtered = {k: v for k, v in kwargs.items() if k not in ollama_only_keys}

    # ── OpenAI (GPT-4, GPT-3.5) ─────────────────────────────────────
    # WHAT: Cloud-based. The OG commercial LLM API.
    # PROS: Fast, reliable, great at code, huge context windows
    # CONS: Costs money per token, data sent to OpenAI's servers
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model,        # e.g. "gpt-4o", "gpt-4o-mini"
            temperature=temperature,
            api_key=settings.openai_api_key,  # Required: set OPENAI_API_KEY env var
            **filtered,
        )

    # ── Anthropic (Claude) ───────────────────────────────────────────
    # WHAT: Cloud-based. Made by ex-OpenAI researchers.
    # PROS: Excellent at following complex instructions, large context (200K tokens)
    # CONS: Can be slower than GPT-4, costs money
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.llm_model,            # e.g. "claude-sonnet-4-20250514"
            temperature=temperature,
            api_key=settings.anthropic_api_key,   # Required: ANTHROPIC_API_KEY
            **filtered,
        )

    # ── Google Gemini ────────────────────────────────────────────────
    # WHAT: Google's multimodal AI model (text, images, code).
    # PROS: Good free tier, can process images alongside code
    # CONS: Newer, less battle-tested for code tasks than GPT-4/Claude
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,          # e.g. "gemini-pro"
            temperature=temperature,
            google_api_key=settings.google_api_key,  # Required: GOOGLE_API_KEY
            **filtered,
        )

    # ── Groq (Ultra-Fast Inference) ──────────────────────────────────
    # WHAT: Cloud service with custom hardware for blazing fast LLM inference.
    # PROS: 10-50x faster than other cloud providers for same models
    # CONS: Limited model selection, newer service
    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=settings.llm_model,          # e.g. "llama-3.1-70b-versatile"
            temperature=temperature,
            api_key=settings.groq_api_key,      # Required: GROQ_API_KEY
            **filtered,
        )

    # ── OpenRouter (Model Gateway) ───────────────────────────────────
    # WHAT: ONE API key gives you access to 100+ models (Claude, Llama, Mistral, etc.)
    # HOW: Uses OpenAI's API format but routes requests to different model providers.
    # PROS: Try many models with one account, compare quality/speed easily
    # CONS: Adds a middleman (slightly slower), pricing varies by model
    elif provider == "openrouter":
        from langchain_openai import ChatOpenAI  # Same class as OpenAI!
        return ChatOpenAI(
            model=settings.llm_model,          # e.g. "anthropic/claude-3-opus"
            temperature=temperature,
            api_key=settings.openrouter_api_key,
            # KEY TRICK: Same ChatOpenAI class, but pointed at OpenRouter's URL
            # instead of OpenAI's. Works because OpenRouter mimics OpenAI's API format.
            base_url="https://openrouter.ai/api/v1",
            **filtered,
        )
    elif provider == "deepseek": 
        from langchain_openai import ChatOpenAI 
        return ChatOpenAI(
            model=settings.llm_model, 
            temperature=temperature, 
            api_key=settings.deepseek_api_key, 
            base_url="https://api.deepseek.com", 
            **filtered,
        )

    # ── Unknown Provider ─────────────────────────────────────────────
    # If someone typos their LLM_PROVIDER env var, fail with a clear message
    # listing all valid options. Better than a cryptic ImportError.
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Supported: ollama, openai, anthropic, gemini, groq, openrouter"
        )
