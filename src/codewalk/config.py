"""Pydantic settings and LLM factory for Codewalk."""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from langchain_core.language_models.chat_models import BaseChatModel


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env."""
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # server-only vars (POSTGRES_PASSWORD, RATE_LIMIT_*, etc.) are OK in .env
    )
    # LLM
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.5:27b")

    # Embeddings
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "jinaai/jina-code-embeddings-1.5b")

    # API keys(optional - only needed for cloud providers)
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")

    # CORS
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")


settings = Settings()

def get_llm(temperature: float = 0, **kwargs) -> BaseChatModel:
    """Factory: returns the right LLM based on settings.llm_provider.

    Args:
        temperature: Creativity (0=deterministic, 1=creative). Default 0.
        **kwargs: Extra args passed to the specific provider (e.g. reasoning=False).

    Returns:
        A LangChain chat model instance with Langfuse tracing attached (if configured).
    """
    provider = settings.llm_provider.lower()

    # reasoning=False is Ollama-specific (disables qwen3.5 <think> tags).
    # Strip it for non-Ollama providers so they don't choke on it.
    ollama_only_keys = {"reasoning"}

    llm: BaseChatModel

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=settings.llm_model,
            temperature=temperature,
            **kwargs,
        )

    else:
        # Remove Ollama-specific kwargs for all other providers
        filtered = {k: v for k, v in kwargs.items() if k not in ollama_only_keys}

        if provider == "openai":
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=settings.llm_model,
                temperature=temperature,
                api_key=settings.openai_api_key,
                **filtered,
            )

        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                model=settings.llm_model,
                temperature=temperature,
                api_key=settings.anthropic_api_key,
                **filtered,
            )

        elif provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model=settings.llm_model,
                temperature=temperature,
                google_api_key=settings.google_api_key,
                **filtered,
            )

        elif provider == "groq":
            from langchain_groq import ChatGroq
            llm = ChatGroq(
                model=settings.llm_model,
                temperature=temperature,
                api_key=settings.groq_api_key,
                **filtered,
            )

        elif provider == "openrouter":
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=settings.llm_model,
                temperature=temperature,
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                **filtered,
            )
        elif provider == "deepseek": 
            from langchain_openai import ChatOpenAI 
            llm = ChatOpenAI(
                model=settings.llm_model, 
                temperature=temperature, 
                api_key=settings.deepseek_api_key, 
                base_url="https://api.deepseek.com",
                extra_body={"thinking": {"type": "disabled"}},
                **filtered,
            )

        else:
            raise ValueError(
                f"Unknown LLM provider: '{provider}'. "
                f"Supported: ollama, openai, anthropic, gemini, groq, openrouter"
            )

    return _attach_langfuse(llm)
    

def get_langfuse_handler():
    """Return a Langfuse CallbackHandler, or None if not configured.

    v3 API: no constructor args. Trace attributes (session_id, user_id, tags)
    go in config.metadata at invoke time, not here.
    """
    if not os.getenv("LANGFUSE_SECRET_KEY"):
        return None
    
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()  # reads LANGFUSE_* env vars
    except ImportError:
        return None


def _attach_langfuse(llm: BaseChatModel) -> BaseChatModel:
    """Attach Langfuse callbacks to an LLM if configured.

    Sets callbacks at the constructor level so they propagate through
    chains (prompt | llm | parser) and with_structured_output() — no
    per-call config needed anywhere in the codebase.
    """
    handler = get_langfuse_handler()
    if handler:
        llm.callbacks = llm.callbacks or []
        llm.callbacks.append(handler)
    return llm


def push_langfuse_scores(scores: dict[str, float | int], comment: str = "") -> None:
    """Push custom numeric scores to the current Langfuse trace.

    No-op if Langfuse is not configured. Never raises.

    Args:
        scores: {name: value} pairs, e.g. {"retrieval_confidence": 0.6}
        comment: Optional comment attached to each score.
    """
    if not os.getenv("LANGFUSE_SECRET_KEY"):
        return
    try:
        from langfuse import get_client
        lf = get_client()
        for name, value in scores.items():
            lf.score_current_trace(
                name=name,
                value=float(value),
                data_type="NUMERIC",
                comment=comment or None,
            )
    except Exception:
        pass  # never crash for metrics
     