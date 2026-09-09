"""
=========================================================
Homez OS

File : app/domains/backup/model.py

Gate Y-1(2026-08-12) — 백업 이력 테이블. 백업 파일 자체는
`app/database/migration_runner.py::create_backup()`이 이미 쓰는
`sqlite3.Connection.backup()`(SQLite 온라인 백업 API — 쓰기 중인
DB에도 안전) + `PRAGMA integrity_check` 검증 패턴을 그대로 재사용
한다. 이 테이블은 그 결과를 이력으로 남겨(누가/언제/무엇을 근거로/
어디에) 나중에 "최근 백업이 언제였는지" 화면에서 조회할 수 있게
한다 — 파일 목록만으로는 알 수 없는 트리거 주체(수동/예약 등)와
검증 결과를 함께 기록한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class BackupRecord(Base):

    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # "ok"만 성공적으로 기록된다 — integrity_check가 ok가 아니면
    # service.py가 예외를 던지고 이 행 자체를 만들지 않는다(부분
    # 실패·손상 백업을 "완료"로 보이게 하지 않는다).
    integrity_check_result: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # "manual"(사용자가 지금 눌러서) / "pre_migration"(Migration 적용
    # 직전 자동) 등 — 자유 문자열이 아니라 app/domains/backup/
    # constants.py의 고정 값만 쓴다(service.py가 검증).
    trigger_source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 이 코드베이스는 FK를 쓰지 않는다(모든 도메인 공통 컨벤션,
    # test_no_foreign_keys 계열 테스트로 강제) — 논리 참조만 유지.
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    label: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:

        return f"<BackupRecord(id={self.id}, file_path={self.file_path})>"


__all__ = ["BackupRecord"]
