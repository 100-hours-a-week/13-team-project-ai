import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_USER: str = os.getenv("DB_USER", "appuser")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "freestyle13")
    DB_NAME: str = os.getenv("DB_NAME", "matchimban")
    DB_PORT: str = os.getenv("DB_PORT", "15432")

    # Qdrant
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://54.180.163.237:6333")
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "UbKF4wRjaqTqvNarEBP-8KXIcvt5BAy8bqkTFpg6iew")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "restaurants_docs")

    # SQLite (Chat History)
    SQLITE_DB_PATH: str = "chat_history.db"

    # Timeout
    AGENT_TIMEOUT_SEC: int = 200
    LLM_TIMEOUT_SEC: int = 120    # LLM 호출 타임아웃
    MAX_CONCURRENT_REQUESTS: int = 5  # 동시 처리 요청 수 제한
    HISTORY_LIMIT: int = 10  # 대화 맥락 유지할 턴 수

    # LLM (vLLM)
    VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:11434/v1")
    VLLM_MODEL: str = os.getenv("VLLM_MODEL", "Qwen2.5-7B-Instruct-AWQ:latest")

    # LangSmith (표준 환경변수명 사용)
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "moyeobab_RAGchatbot")
    LANGCHAIN_ENDPOINT: str = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
    LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "true")

    # 임베딩 (KR-SBERT 768차원)
    EMBEDDING_MODEL_NAME: str = os.getenv(
        "EMBEDDING_MODEL_NAME", "snunlp/KR-SBERT-V40K-klueNLI-augSTS"
    )
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    EMBEDDING_DIM: int = 768

    # 파이프라인
    DEFAULT_RADIUS_M: int = 1000
    HYDE_SCORE_THRESHOLD: float = 0.50  # review_evidence 기준 (review doc보다 낮게 설정)
    HYDE_TOP_K: int = 20

    # Reranker
    RERANKER_MODEL_NAME: str = os.getenv(
        "RERANKER_MODEL_NAME", "BAAI/bge-reranker-v2-m3"
    )

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def DB_TYPE(self) -> str:
        return self.DATABASE_URL.split('+')[0] if '+' in self.DATABASE_URL else self.DATABASE_URL.split(':')[0]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# LangSmith 환경변수 명시적 동기화 (SDK가 초기화 시점에 읽을 수 있도록)
os.environ["LANGCHAIN_TRACING_V2"] = settings.LANGCHAIN_TRACING_V2
os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGCHAIN_ENDPOINT