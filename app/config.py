from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token")

    # Database
    DB_PATH: str = "data/bank.db"

    # Receipt
    RECEIPT_FONT: str = "assets/font.ttf"
    RECEIPT_WIDTH: int = 900
    RECEIPT_HEIGHT: int = 500

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
