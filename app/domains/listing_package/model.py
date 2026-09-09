"""
=========================================================
Homez OS

File : app/domains/listing_package/model.py

Listing Package Domain — 상품 초안·이미지·채널 정보를 묶은 "최종
검토 단위". 일반 모드/AI 오토 모드 둘 다 이 테이블 하나로 표현한다
(mode 컬럼으로만 구분 — 별도 테이블을 두지 않는다).

ForeignKey 없음(이 코드베이스 전체 컨벤션), company_id 전 테이블
필수, 모든 조회·조건부 UPDATE는 company_id를 WHERE에 포함한다.

ListingPackageApproval은 append-only다 — "사용자 최종 승인 1회"
원칙 그대로, 이미지 생성 등 중간 단계에는 별도 승인 테이블을 두지
않는다(오직 이 최종 승인/거절만). 승인 유효성은
app/domains/marketplace_listing/approval_service.py::
current_valid_approval()과 동일한 철학 — 승인 시점에 스냅샷한
package_fingerprint가 "지금" 다시 계산한 fingerprint와 일치할
때만 유효하다(승인 후 payload가 바뀌면 재계산된 fingerprint가
달라져 자동으로 무효화된다 — 별도 revoke 로직 불필요).
=========================================================
"""

from datetime import datetime

from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Numeric
from sqlalchemy import DateTime
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

MONEY = Numeric(14, 2)


class ListingPackage(Base):

    __tablename__ = "listing_packages"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_listing_packages_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조(product_candidates.id)
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # STANDARD / AI_AUTO_PROPOSAL
    mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # DRAFT / READY_FOR_REVIEW / APPROVED / SUBMITTED / CANCELLED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", index=True,
    )

    # 상품명/설명/키워드/가격/옵션 초안 — canonical JSON.
    draft_payload_json: Mapped[str] = mapped_column(
        String(8000), nullable=False,
    )

    # [{channel_code, fulfillment_mode}, ...] — canonical JSON.
    channel_selection_json: Mapped[str] = mapped_column(
        String(4000), nullable=False,
    )

    # 이미지 생성 요청 옵션(개수·style 등) — canonical JSON.
    image_options_json: Mapped[str] = mapped_column(
        String(2000), nullable=False,
    )

    # 채널별 필수값 누락 현황 스냅샷 — canonical JSON, 없으면 "{}".
    missing_fields_json: Mapped[str] = mapped_column(
        String(4000), nullable=False, default="{}",
    )

    estimated_revenue: Mapped[float | None] = mapped_column(
        MONEY, nullable=True,
    )

    risk_summary: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    recommendation_reason: Mapped[str | None] = mapped_column(
        String(2000), nullable=True,
    )

    # draft+channel+image+가격+mode의 SHA-256 — 현재 상태 지문.
    package_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    # create_listing_package() 호출 시점의 원본 요청(product_candidate_id
    # + channel_selections + image_options + mode)의 SHA-256 — 이
    # idempotency_key로 재호출됐을 때 요청 내용이 같은지 다른지
    # 판정하는 데만 쓰인다(같으면 기존 결과 반환, 다르면 409).
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class ListingPackageApproval(Base):
    """최종 승인/거절 — append-only. 이미지 생성 등 중간 단계용 승인은
    없다(요청 원문: 이미지 생성만을 위한 중복 승인 금지)."""

    __tablename__ = "listing_package_approvals"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_listing_package_approvals_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조(listing_packages.id)
    listing_package_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # APPROVED / REJECTED
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # 승인 시점에 package.package_fingerprint와 일치했던 값 — 나중에
    # 패키지가 바뀌면(재계산된 fingerprint와 불일치) 이 승인은
    # current_valid_approval()에서 자동으로 무효 판정된다.
    package_fingerprint_snapshot: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    # 승인 당시 draft/이미지/가격/채널/판매방식 전체의 불변 스냅샷
    # (canonical JSON) — 승인 이후 실제 제출은 이 스냅샷을 기준으로
    # 한다(패키지 원본이 그 사이 또 바뀌어도 이 스냅샷은 불변).
    payload_snapshot_json: Mapped[str] = mapped_column(
        String(8000), nullable=False,
    )

    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    requested_by: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


__all__ = [
    "ListingPackage",
    "ListingPackageApproval",
]
