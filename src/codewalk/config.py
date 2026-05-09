from pydantic_settings import BaseSettings
from langchain_core.language_models.chat_models import BaseChatModel

class Settings(BaseSettings):
    # LLM
    llm_provider: str = "ollama"
    llm_model: str = "qwen3.5:27b"

    # Embeddings
    embedding_model: str = "qwen3-embedding:latest"

    # API keys(optional - only needed for cloud providers)
    groq_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""

    # Relative path for self-analysis: "src/codewalk"
    # Absolute path for external repos: "/Users/you/Development/django-app/src"
    # Override via .env: REPO_PATH=/path/to/any/repo/source
    repo_path: str = "src/codewalk"

    github_token: str = ""


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

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.llm_model,
            temperature=temperature,
            **kwargs,
        )

    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.openai_api_key,
            **kwargs,
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.anthropic_api_key,
            **kwargs,
        )

    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            temperature=temperature,
            google_api_key=settings.google_api_key,
            **kwargs,
        )

    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.groq_api_key,
            **kwargs,
        )

    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Supported: ollama, openai, anthropic, gemini, groq"
        )