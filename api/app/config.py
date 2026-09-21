"""Single source of truth for configuration. Everything comes from env / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
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

    # embeddings -- an OpenAI-compatible /embeddings endpoint, not a local model.
    # Defaults to the same provider as the chat model; set these only to split them.
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "baai/bge-base-en-v1.5"
    # Immutable once an index exists. bge-base is 768.
    embedding_dim: int = 768
    # Texts per embeddings request. Measured against this provider: flat from 32 to 128
    # (~3.3s for a batch either way), then it starts to cost -- 256 took 5.3s.
    embed_batch_size: int = 128
    # Chunks embedded and upserted per slice. Sets how often ingestion reports progress
    # and how much work one failed request costs; it is no longer a memory bound.
    ingest_batch_size: int = 128
    embed_timeout: float = 60.0
    # Ingestion is network-bound now, so a transient 429 or 502 must not fail a document.
    embed_max_retries: int = 4

    # Run ingestion inside this process instead of a separate arq worker. Removes the
    # need for Redis and a second container, at the cost of ingestion competing with
    # request handling. Intended for small single-box hosts.
    inline_ingestion: bool = False

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

    # storage / app
    upload_dir: Path = API_DIR / "storage" / "uploads"
    max_upload_mb: int = 50

    # Kept as a plain string: pydantic-settings tries to JSON-decode list-typed
    # env vars, which makes a bare comma-separated value a validation error.
    cors_origins: str = "http://localhost:5173"
    # Vercel gives every preview deploy its own hostname, so a fixed list can never
    # cover them. Empty disables the pattern match entirely.
    cors_origin_regex: str = ""

    @property
    def embedding_url(self) -> str:
        return self.embedding_base_url or self.openai_base_url

    @property
    def embedding_key(self) -> str:
        return self.embedding_api_key or self.openai_api_key

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
    return s


settings = get_settings()
