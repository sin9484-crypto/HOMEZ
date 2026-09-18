"""2026-09-18 D1/D5 항목 — `_reserve_test_budget_call()`이 스레드가
아니라 별도 OS 프로세스 두 개가 동시에 같은 DB 파일을 두드려도
정확히 하나만 성공시키는지 검증하기 위한 독립 워커.

Windows의 `multiprocessing` 기본 시작 방식(spawn)은 자식 프로세스가
대상 함수를 다시 import해서 실행한다 — 그래서 이 함수는 테스트
파일 안의 지역 함수가 아니라 이렇게 최상위(top-level) 모듈 함수여야
pickle이 가능하다."""

from __future__ import annotations


def reserve_once(
    db_path: str, company_id: int, store_connection_id: int,
    channel_status: str,
) -> bool:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.domains.order.auto_collection_scheduler import (
        _reserve_test_budget_call,
    )

    # timeout: 다른 프로세스가 짧게 쓰기 잠금을 쥐고 있어도 즉시
    # "database is locked"로 실패하지 않고 잠깐 기다렸다가 재시도한다
    # (SQLite 자체 재시도 — 애플리케이션 레벨 재시도가 아니다, 예산을
    # 두 번 소모하지 않는다).
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"timeout": 10},
    )
    session = sessionmaker(bind=engine)()
    try:
        return _reserve_test_budget_call(
            session, company_id, store_connection_id, channel_status,
        )
    finally:
        session.close()
        engine.dispose()
