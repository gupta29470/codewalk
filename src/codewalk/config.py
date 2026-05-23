import os
from pydantic_settings import BaseSettings
from langchain_core.language_models.chat_models import BaseChatModel

class Settings(BaseSettings):
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

    # Relative path for self-analysis: "src/codewalk"
    # Absolute path for external repos: "/Users/you/Development/django-app/src"
    # Override via .env: REPO_PATH=/path/to/any/repo/source
    repo_path: str = os.getenv("REPO_PATH", ".")

    # Comma-separated paths to exclude from scanning/indexing
    # e.g. "tests,docs,scripts/legacy,*.generated.*"
    exclude_paths: str = os.getenv("EXCLUDE_PATHS", "")

    # LLM-based file filtering during indexing
    # True = LLM decides which files to embed (smarter, slower)
    # False = pattern matching only (faster, keeps all source files)
    use_llm_filter: bool = os.getenv("USE_LLM_FILTER", "true").lower() in ("true", "1", "yes")

    review_guidelines_path: str = os.getenv("REVIEW_GUIDELINES_PATH", "review_guidelines")


    class Config:
        env_file = ".env"


settings = Settings()

def get_llm(temperature: float = 0, **kwargs) -> BaseChatModel:
    """Factory: returns the right LLM based on settings.llm_provider.

    Args:
        temperature: Creativity (0=deterministic, 1=creative). Default 0.
        **kwargs: Extra args passed to the specific provider (e.g. reasoning=False).

    Returns:
        A LangChain chat model instance (ChatOllama, ChatOpenAI, etc.)
    """
    provider = settings.llm_provider.lower()

    # reasoning=False is Ollama-specific (disables qwen3.5 <think> tags).
    # Strip it for non-Ollama providers so they don't choke on it.
    ollama_only_keys = {"reasoning"}

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.llm_model,
            temperature=temperature,
            **kwargs,
        )

    # Remove Ollama-specific kwargs for all other providers
    filtered = {k: v for k, v in kwargs.items() if k not in ollama_only_keys}

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.openai_api_key,
            **filtered,
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.anthropic_api_key,
            **filtered,
        )

    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            temperature=temperature,
            google_api_key=settings.google_api_key,
            **filtered,
        )

    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.groq_api_key,
            **filtered,
        )

    elif provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.openrouter_api_key,
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

    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Supported: ollama, openai, anthropic, gemini, groq, openrouter"
        )