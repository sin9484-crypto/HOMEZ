"""
=========================================================
Homez OS

File : app/domains/account_recovery/model.py

계정 복구(아이디 찾기 / 비밀번호 재설정) — 로그아웃 상태 자기 복구용
일회용 복구 코드(RecoveryCode)와 이메일 재설정 링크/SUPER_ADMIN 발급
공용 토큰(PasswordResetToken) 2개 테이블.

원칙(다른 Domain과 동일):
  - FK 없음 — user_id/company_id는 애플리케이션 레벨 논리 참조 컬럼.
  - 원문은 저장하지 않는다 — code_hash/token_hash만 저장(단방향 해시).
  - append 위주: 재발급/재요청 시 기존 미사용 행은 revoked_at만
    채우고 그대로 남긴다(하드 삭제 없음 — 감사 추적 보존).
=========================================================
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class RecoveryCode(Base):
    """
    로그아웃 상태에서 스스로 비밀번호를 재설정할 수 있는 일회용 복구
    코드 1건. 계정 하나당 한 번에 최대 10개까지 유효(미사용·미폐기)
    상태로 존재한다 — 새 묶음을 생성하면 기존 미사용 코드는 전부
    revoked_at으로 폐기된다.

    expires_at은 의도적으로 nullable이다: 이 시스템은 TOTP 백업 코드와
    동일한 정책을 따른다 — 사용되거나(used_at) 새 묶음 생성으로
    폐기되기(revoked_at) 전까지 시간 만료 없이 유효하다.
    """

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    company_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    code_hash: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    def __repr__(self) -> str:

        return (
            f"<RecoveryCode(id={self.id}, user_id={self.user_id}, "
            f"used={self.used_at is not None}, revoked={self.revoked_at is not None})>"
        )


class PasswordResetToken(Base):
    """
    비밀번호 재설정 1회용 토큰. 두 발급 경로가 이 같은 테이블을
    공유한다:
      1. 이메일 재설정 링크(자기 self-service, 계정 존재 여부 비노출)
      2. SUPER_ADMIN이 같은 회사 사용자를 위해 발급(관리자 화면 1회
         표시, 이메일 발송 아님)

    어느 경로든 저장되는 것은 token_hash(sha256)뿐이며 원문 토큰은
    발급 응답에만 담겨 DB에는 절대 남지 않는다. 10~15분 만료, 1회
    사용, 새 토큰 발급 시 그 계정의 기존 미사용 토큰은 전부
    revoked_at으로 폐기된다.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    company_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )

    issued_by: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    # "EMAIL" | "SUPER_ADMIN" — 값 자체는 발급 경로 구분용일 뿐 민감
    # 정보가 아니다.

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    def __repr__(self) -> str:

        return (
            f"<PasswordResetToken(id={self.id}, user_id={self.user_id}, "
            f"issued_by={self.issued_by}, used={self.used_at is not None})>"
        )


__all__ = ["RecoveryCode", "PasswordResetToken"]
