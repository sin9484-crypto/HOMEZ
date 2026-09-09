"""
=========================================================
Homez OS

File : app/domains/update/model.py

Gate Y-4(2026-08-12) — 업데이트 공지. 이번 단계에서는 서명된
매니페스트를 외부 네트워크에서 자동으로 가져오지 않는다(이 세션은
외부 네트워크 호출이 금지된다) — 대신 관리자가 새 버전이 나왔음을
수동으로 등록하는 "공지" 모델이다. 서명 매니페스트 자동 확인은
`docs/adr/0002-update-manifest-signing-design.md`에 설계만 남기고
V7에서 구현한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

UPDATE_SEVERITY_INFO = "info"
UPDATE_SEVERITY_RECOMMENDED = "recommended"
UPDATE_SEVERITY_REQUIRED = "required"

UPDATE_SEVERITIES = frozenset(
    {
        UPDATE_SEVERITY_INFO,
        UPDATE_SEVERITY_RECOMMENDED,
        UPDATE_SEVERITY_REQUIRED,
    },
)


class UpdateNotice(Base):

    __tablename__ = "update_notices"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # "2.2.0" 형식(major.minor.patch)만 허용 — service.py가 검증한다.
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 실제로 이 URL을 서버가 자동으로 열지 않는다 — 사용자가 클릭해서
    # 직접 이동하는 참고용 링크일 뿐이다(외부 네트워크 자동 호출
    # 금지 원칙 유지).
    release_notes_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # False면 "취소된 공지" — 잘못 등록한 공지를 삭제하지 않고
    # 비활성화만 한다(이력 보존, 다른 append-mostly 도메인과 동일
    # 철학).
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    # 이 코드베이스는 FK를 쓰지 않는다(논리 참조만 유지).
    published_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:

        return (
            f"<UpdateNotice(id={self.id}, version={self.version}, "
            f"active={self.is_active})>"
        )


__all__ = [
    "UpdateNotice",
    "UPDATE_SEVERITY_INFO",
    "UPDATE_SEVERITY_RECOMMENDED",
    "UPDATE_SEVERITY_REQUIRED",
    "UPDATE_SEVERITIES",
]
