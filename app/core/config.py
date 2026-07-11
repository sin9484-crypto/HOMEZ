"""
HOMEZ AI Commerce OS
Build 06

config.py

프로젝트 전체 환경설정을 관리하는 모듈
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import ConfigDict


# ==========================================================
# ROOT PATH
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ==========================================================
# SETTINGS
# ==========================================================

class Settings(BaseSettings):
    """
    Homez 전체 설정
    """

    # ------------------------------------------------------
    # Project
    # ------------------------------------------------------

    PROJECT_NAME: str = "HOMEZ AI Commerce OS"

    VERSION: str = "0.1.0"

    DEBUG: bool = True

    # ------------------------------------------------------
    # Database
    # ------------------------------------------------------

    DATABASE_URL: str = "sqlite:///./homez.db"

    # ------------------------------------------------------
    # Security
    # ------------------------------------------------------

    SECRET_KEY: str = "CHANGE_THIS_SECRET_KEY"

    ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ------------------------------------------------------
    # AI
    # ------------------------------------------------------

    AI_ENABLED: bool = True

    # ------------------------------------------------------
    # Logging
    # ------------------------------------------------------

    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------
    # Build
    # ------------------------------------------------------

    BUILD: int = 6

    RELEASE: int = 1

    # ------------------------------------------------------
    # Environment
    # ------------------------------------------------------

    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=True,
    )


settings = Settings()