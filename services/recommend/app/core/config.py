# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"

    PG_HOST: str
    PG_PORT: int = 5432
    PG_USER: str
    PG_PASSWORD: str
    PG_DB: str

    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10


settings = Settings()  # type: ignore
