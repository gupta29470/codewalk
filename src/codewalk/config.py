from pydantic_settings import BaseSettings

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


    class Config:
        env_file = ".env"


settings = Settings()