from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Document Image Enhancement API"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    BASE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    AI_PROJECT_DIR: Path | None = None
    UPLOAD_DIR: Path | None = None
    RESTORED_DIR: Path | None = None
    RESTORED_URL_PREFIX: str = "/restored"

    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    MAX_UPLOAD_MB: int = 50
    PDF_RENDER_DPI: int = 200

    DEFAULT_PATCH_SIZE: int = 512
    DEFAULT_BATCH_SIZE: int = 4
    DEFAULT_THRESHOLD: float = 0.5
    DEFAULT_BINARIZE_OUTPUT: bool = True
    DEFAULT_OVERLAP: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    def model_post_init(self, __context: object) -> None:
        repo_root = self.BASE_DIR.parent
        if self.AI_PROJECT_DIR is None:
            self.AI_PROJECT_DIR = repo_root / "AI-Document-image-enhencement"
        if self.UPLOAD_DIR is None:
            self.UPLOAD_DIR = self.BASE_DIR / "storage" / "uploads"
        if self.RESTORED_DIR is None:
            self.RESTORED_DIR = self.BASE_DIR / "storage" / "restored"

    def ensure_storage_dirs(self) -> None:
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.RESTORED_DIR.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
