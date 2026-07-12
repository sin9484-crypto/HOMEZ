"""
HOMEZ AI Commerce OS
Build 06

startup.py

프로그램 시작 초기화
"""

from app.core.version import version
from app.core.config import settings


class Startup:

    def run(self) -> None:

        print("=" * 60)
        print(settings.PROJECT_NAME)
        print(f"Version : {version.VERSION}")
        print(f"Release : {version.RELEASE}")
        print(f"Build   : {version.BUILD}")
        print("=" * 60)

        print("Loading Configuration...")

        print("Configuration Loaded")

        print("System Ready")


startup = Startup()