from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a .env file.

    All fields can be overridden via environment variables (case-insensitive).
    Lychee integration is disabled when lychee_url, lychee_username, or
    lychee_password are left empty.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    access_token: str = "wesele2026"
    printer_api_key: str = "printer-secret-key"

    database_url: str = "sqlite+aiosqlite:////app/data/weeding.db"
    upload_dir: str = "/tmp/uploads"

    # Set True when running behind HTTPS (Cloudflare tunnel)
    secure_cookies: bool = False
    max_file_size_mb: int = 20
    photos_per_hour: int = 3

    # Lychee gallery integration (leave empty to disable)
    lychee_url: str = ""
    lychee_username: str = ""
    lychee_password: str = ""
    lychee_album_id: str = ""


settings = Settings()
