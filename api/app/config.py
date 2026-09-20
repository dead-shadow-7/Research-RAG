"""Single source of truth for configuration. Everything comes from env / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent


class Settings(BaseSettings):
    # `protected_namespaces=()` is required: pydantic reserves the `model_` prefix,
    # and `model_cache_dir` would otherwise raise a namespace conflict.
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # datastores
    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag"
    redis_url: str = "redis://localhost:6379"

    # pinecone
    pinecone_api_key: str = ""
    pinecone_index: str = "rag-dev"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    # llm -- any OpenAI-compatible provider, selected by base url
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.2
    # "off" disables hybrid-reasoning models' thinking pass; "auto" leaves the
    # provider's default alone. Grounded QA over retrieved text rarely needs it, and
    # on Qwen3 it costs most of the output budget.
    llm_reasoning: str = "off"

    # embeddings
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_dim: int = 768
    model_cache_dir: Path = API_DIR / "storage" / "models"

    # chunking / retrieval
    chunk_tokens: int = 380
    chunk_overlap: int = 64
    retrieve_k: int = 20
    context_k: int = 6
    # context_k is a ceiling, not a quota. A chunk only reaches the model if it scores
    # within `context_min_ratio` of the best match and clears `context_min_score`;
    # otherwise a question with one good answer drags five irrelevant chunks along.
    context_min_ratio: float = 0.75
    # Calibrated on this corpus with bge-base-en-v1.5: answerable questions scored
    # 0.57-0.85 on their best chunk, unanswerable ones topped out at 0.43. Re-measure
    # with tests/eval_retrieval.py if the embedding model changes -- this number is a
    # property of the model, not a universal constant.
    context_min_score: float = 0.50
    rerank_enabled: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    # Cross-encoder scores are logits, squashed to a 0-1 relevance probability.
    rerank_min_score: float = 0.5

    # storage / app
    upload_dir: Path = API_DIR / "storage" / "uploads"
    max_upload_mb: int = 50

    # Kept as a plain string: pydantic-settings tries to JSON-decode list-typed
    # env vars, which makes a bare comma-separated value a validation error.
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    s.model_cache_dir.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
