"""
=========================================================
Homez OS

File : app/database/session.py
Version : 2.1.0

Database Session
=========================================================
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.core.config import (
    settings,
)

# --------------------------------------------------
# Engine
# --------------------------------------------------

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# --------------------------------------------------
# Diagnostics (V7 Live Gate 4 재작업 — 결함 1)
# --------------------------------------------------

def get_engine_db_path() -> Path | None:
    """
    이 모듈의 전역 `engine`이 실제로 물리는 sqlite 파일의 절대경로.

    sqlite가 아니거나(향후 다른 DBMS) in-memory DB면 None을 반환한다.
    순수 조회 전용이다 — 엔진을 재생성하거나 어떤 연결도 새로 열지
    않는다(engine.url은 이미 구성된 URL 객체를 읽을 뿐).

    공식 Desktop 부팅 흐름(app/desktop/main.py)이 bootstrap/
    MigrationRunner가 사용한 DB 경로와 이 값이 정확히 일치하는지
    fail-fast로 비교하는 데 사용한다 — 두 경로가 갈라지면(예: 개발자가
    DATABASE_URL 환경변수만 바꾸고 bootstrap 경로 계약은 그대로인 경우)
    앱이 절대 기동하지 않아야 한다.
    """

    url = engine.url

    if url.get_backend_name() != "sqlite":
        return None

    database = url.database

    if not database or database == ":memory:":
        return None

    return Path(database).resolve()


# --------------------------------------------------
# Dependency
# --------------------------------------------------

def get_db() -> Generator[
    Session,
    None,
    None,
]:

    db = SessionLocal()

    try:

        yield db

    finally:

        db.close()