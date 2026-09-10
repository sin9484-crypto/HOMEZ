"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/service.py

2026-09-10 Phase 10(HOMEZ_USER_OPERATION_SETTINGS.md 2·7번) — 가상재고
임계값·가격 검토주기 설정 + 가상재고 게이트 판정.

**이번 Phase에서 실제로 연결하지 않은 것(정직하게 기록)**: 이
서비스의 `check_virtual_stock_allows_auto_order()`는 "표시 재고
수치가 임계값 이하인가"만 순수하게 판정한다 — 그 "표시 재고
수치"를 실제로 어디서 읽어올지는 연결하지 않았다. 조사 결과
(docs/HOMEZ_PROJECT_STATE.md Phase 10 절 참고) 판매채널 리스팅에
"표시 재고"를 담는 필드가 이 저장소 어디에도 아직 없다 — 이건
`price_stock_safety` 하나의 문제가 아니라 `marketplace_listing`
도메인에 그 필드 자체를 새로 설계해야 하는 별도 작업이다. 마찬가지로
`review_cycle_days` 설정도 실제로 Phase 6 스케줄러에 "매주 가격
재검토 Job"으로 연결하지는 않았다 — `price_advisory_service`를
스케줄러에 연결하는 것은 그 자체로 신중한 별도 조사가 필요한
작업이라 이번에는 설정값 관리까지만 완성했다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.price_stock_safety.constants import DEFAULT_PRICE_REVIEW_CYCLE_DAYS
from app.domains.price_stock_safety.model import PriceReviewCycleSetting
from app.domains.price_stock_safety.model import VirtualStockThreshold
from app.domains.price_stock_safety.repository import PriceStockSafetyRepository


class PriceStockSafetyService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = PriceStockSafetyRepository(db)

    # ------------------------------
    # 가상재고 임계값
    # ------------------------------

    def get_virtual_stock_threshold(self, company_id: int) -> int | None:
        """설정된 적이 없으면 None(임계값 미설정) — 0으로 추측하지
        않는다. 호출자는 None일 때 게이트를 어떻게 취급할지 스스로
        결정해야 한다(현재 `check_virtual_stock_allows_auto_order()`는
        미설정 시 항상 허용 — "설정한 적 없는 회사에 갑자기 새
        차단이 생기지 않는다"는 안전한 기본값)."""

        record = self.repository.get_latest_virtual_stock_threshold(
            company_id,
        )
        return record.threshold_quantity if record else None

    def set_virtual_stock_threshold(
        self,
        *,
        company_id: int,
        user_id: int,
        is_admin: bool,
        threshold_quantity: int,
    ) -> VirtualStockThreshold:

        if not is_admin:
            raise ForbiddenException(
                "가상재고 임계값 변경은 관리자만 가능합니다.",
            )

        if threshold_quantity < 0:
            raise BadRequestException("임계값은 0 이상이어야 합니다.")

        record = VirtualStockThreshold(
            company_id=company_id, threshold_quantity=threshold_quantity,
            set_by=user_id,
        )

        return self.repository.add_virtual_stock_threshold(record)

    def check_virtual_stock_allows_auto_order(
        self, company_id: int, displayed_stock: int,
    ) -> tuple[bool, str | None]:
        """
        "표시 재고가 임계값 이하면 신규 자동판매·자동발주를 금지한다"
        판정만 한다 — 실행(중지)은 호출자 책임이다. 임계값이 설정된
        적 없으면 항상 허용한다(안전한 기본값 — 모듈 docstring 참고).
        """

        if displayed_stock < 0:
            raise BadRequestException("표시 재고는 0 이상이어야 합니다.")

        threshold = self.get_virtual_stock_threshold(company_id)

        if threshold is None:
            return True, None

        if displayed_stock <= threshold:
            return False, (
                f"표시 재고({displayed_stock})가 가상재고 임계값"
                f"({threshold}) 이하입니다."
            )

        return True, None

    # ------------------------------
    # 가격 검토주기
    # ------------------------------

    def get_review_cycle_days(self, company_id: int) -> int:

        record = self.repository.get_latest_review_cycle_setting(company_id)

        if record is None:
            return DEFAULT_PRICE_REVIEW_CYCLE_DAYS

        return record.review_cycle_days

    def set_review_cycle_days(
        self,
        *,
        company_id: int,
        user_id: int,
        is_admin: bool,
        review_cycle_days: int,
    ) -> PriceReviewCycleSetting:

        if not is_admin:
            raise ForbiddenException(
                "가격 검토주기 변경은 관리자만 가능합니다.",
            )

        if review_cycle_days <= 0:
            raise BadRequestException("검토주기는 0보다 커야 합니다.")

        record = PriceReviewCycleSetting(
            company_id=company_id, review_cycle_days=review_cycle_days,
            set_by=user_id,
        )

        return self.repository.add_review_cycle_setting(record)


__all__ = ["PriceStockSafetyService"]
