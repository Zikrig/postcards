"""Bot configuration: settings and env loading."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


@dataclass
class Settings:
    bot_token: str
    user_password: str
    admin_ids: set[int]
    database_url: str
    api_key: str
    api_base_url: str
    image_model: str
    image_size: str
    image_quality: str
    poll_interval_seconds: float
    task_timeout_seconds: int


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    user_password = os.getenv("USER_PASSWORD", "").strip()
    api_key = os.getenv("API_KEY", "").strip()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required in .env")
    # USER_PASSWORD больше не обязателен
    if not api_key:
        raise RuntimeError("API_KEY is required in .env")

    db_host = os.getenv("DB_HOST", "db")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "botdb")
    db_user = os.getenv("DB_USER", "botuser")
    db_password = os.getenv("DB_PASSWORD", "botpassword")
    database_url = os.getenv(
        "DATABASE_URL",
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}",
    )

    return Settings(
        bot_token=bot_token,
        user_password=user_password,
        admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        database_url=database_url,
        api_key=api_key,
        api_base_url=os.getenv("API_BASE_URL", "https://api.evolink.ai"),
        image_model=os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image-preview"),
        image_size=os.getenv("IMAGE_SIZE", "9:16"),
        image_quality=os.getenv("IMAGE_QUALITY", "1K"),
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "3")),
        task_timeout_seconds=int(os.getenv("TASK_TIMEOUT_SECONDS", "120")),
    )
