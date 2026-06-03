"""Configuration management via environment variables."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env file if it exists
if os.path.exists(".env"):
    load_dotenv(".env")


@dataclass
class Config:
    """Bot configuration loaded from environment variables."""

    # Telegram Bot API token
    bot_token: str
    # Telegram MTProto API credentials (required by Kurigram)
    api_id: int
    api_hash: str

    # Database
    database_url: str

    # Admin
    owner_id: int

    # Optional settings
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        bot_token = os.environ.get("BOT_TOKEN")
        if not bot_token:
            raise ValueError("BOT_TOKEN environment variable is required")

        api_id = os.environ.get("API_ID")
        if not api_id:
            raise ValueError("API_ID environment variable is required")

        api_hash = os.environ.get("API_HASH")
        if not api_hash:
            raise ValueError("API_HASH environment variable is required")

        owner_id = os.environ.get("OWNER_ID")
        if not owner_id:
            raise ValueError("OWNER_ID environment variable is required")

        # Database URL is required
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise ValueError(
                "DATABASE_URL environment variable is required.\n"
                "Example: postgresql://user:password@localhost:5432/famtree"
            )

        return cls(
            bot_token=bot_token,
            api_id=int(api_id),
            api_hash=api_hash,
            database_url=database_url,
            owner_id=int(owner_id),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


# Global config instance (initialized in __main__)
config: Config | None = None


def get_config() -> Config:
    """Get the global config instance."""
    global config
    if config is None:
        config = Config.from_env()
    return config
