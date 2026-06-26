from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    access_token: str = "wesele2026"
    printer_api_key: str = "printer-secret-key"

    database_url: str = "sqlite+aiosqlite:////app/data/weeding.db"
    upload_dir: str = "/tmp/uploads"

    # Set True when running behind HTTPS (Cloudflare tunnel)
    secure_cookies: bool = False
    max_file_size_mb: int = 20
    photos_per_hour: int = 3


settings = Settings()
