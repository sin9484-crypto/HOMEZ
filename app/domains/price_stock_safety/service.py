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

from datetime import datetime
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.price_stock_safety.constants import DEFAULT_PRICE_CACHE_TTL_MINUTES
from app.domains.price_stock_safety.constants import DEFAULT_PRICE_REVIEW_CYCLE_DAYS
from app.domains.price_stock_safety.constants import DEFAULT_STOCK_CACHE_TTL_MINUTES
from app.domains.notification_center.operational_events import dispatch_operational_event
from app.domains.price_stock_safety.model import PriceCacheTtlSetting
from app.domains.price_stock_safety.model import PriceReviewCycleSetting
from app.domains.price_stock_safety.model import PriceStockQuoteCache
from app.domains.price_stock_safety.model import StockCacheTtlSetting
from app.domains.price_stock_safety.model import VirtualStockThreshold
from app.domains.price_stock_safety.model import VirtualStockZeroProposal
from app.domains.price_stock_safety.model import VirtualStockZeroProposalStatus
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

    # ------------------------------
    # 가격/재고 캐시 유효시간(TTL) — Phase 9D(8-5)/9E(8-6)
    # ------------------------------

    def get_price_cache_ttl_minutes(self, company_id: int) -> int:

        record = self.repository.get_latest_price_cache_ttl_setting(company_id)
        return (
            DEFAULT_PRICE_CACHE_TTL_MINUTES if record is None
            else record.ttl_minutes
        )

    def set_price_cache_ttl_minutes(
        self, *, company_id: int, user_id: int, is_admin: bool,
        ttl_minutes: int,
    ) -> PriceCacheTtlSetting:

        if not is_admin:
            raise ForbiddenException("가격 캐시 유효시간 변경은 관리자만 가능합니다.")
        if ttl_minutes <= 0:
            raise BadRequestException("유효시간(분)은 0보다 커야 합니다.")

        record = PriceCacheTtlSetting(
            company_id=company_id, ttl_minutes=ttl_minutes, set_by=user_id,
        )
        return self.repository.add_price_cache_ttl_setting(record)

    def get_stock_cache_ttl_minutes(self, company_id: int) -> int:

        record = self.repository.get_latest_stock_cache_ttl_setting(company_id)
        return (
            DEFAULT_STOCK_CACHE_TTL_MINUTES if record is None
            else record.ttl_minutes
        )

    def set_stock_cache_ttl_minutes(
        self, *, company_id: int, user_id: int, is_admin: bool,
        ttl_minutes: int,
    ) -> StockCacheTtlSetting:

        if not is_admin:
            raise ForbiddenException("재고 캐시 유효시간 변경은 관리자만 가능합니다.")
        if ttl_minutes <= 0:
            raise BadRequestException("유효시간(분)은 0보다 커야 합니다.")

        record = StockCacheTtlSetting(
            company_id=company_id, ttl_minutes=ttl_minutes, set_by=user_id,
        )
        return self.repository.add_stock_cache_ttl_setting(record)

    # ------------------------------
    # 가격/재고 조회 캐시 — Phase 9A(7-8)
    # ------------------------------

    def get_cached_price(
        self, company_id: int, connection_id: int, product_code: str,
        option_id: str,
    ) -> tuple[float, datetime] | None:
        """만료되지 않은 캐시된 가격이 있으면 (금액, 확인시각)을
        반환한다. 없거나 만료됐으면 None — 호출자가 다시 실제
        조회해야 한다는 뜻이다(이 메서드는 절대 추측값을 만들지
        않는다)."""

        record = self.repository.get_quote_cache(
            company_id, connection_id, product_code, option_id,
        )
        if (
            record is None or record.price_amount is None
            or record.price_expires_at is None
            or record.price_expires_at <= datetime.utcnow()
        ):
            return None
        return record.price_amount, record.price_confirmed_at

    def get_cached_stock(
        self, company_id: int, connection_id: int, product_code: str,
        option_id: str,
    ) -> tuple[bool, datetime] | None:

        record = self.repository.get_quote_cache(
            company_id, connection_id, product_code, option_id,
        )
        if (
            record is None or record.in_stock is None
            or record.stock_expires_at is None
            or record.stock_expires_at <= datetime.utcnow()
        ):
            return None
        return record.in_stock, record.stock_confirmed_at

    def store_price_quote(
        self, *, company_id: int, connection_id: int, product_code: str,
        option_id: str, price_amount: float, confirmed_at: datetime | None = None,
    ) -> PriceStockQuoteCache:
        """SUPPORTED(성공) 조회 결과만 저장해야 한다 — 오류·인증실패·
        RESULT_UNKNOWN 응답은 절대 이 메서드로 넘기지 않는다(호출부
        책임, 이 메서드 자신은 결과의 "성공 여부"를 판단할 방법이
        없다 — 이미 성공한 값만 받는다는 계약)."""

        confirmed_at = confirmed_at or datetime.utcnow()
        ttl_minutes = self.get_price_cache_ttl_minutes(company_id)
        return self.repository.upsert_quote_cache(
            company_id=company_id, connection_id=connection_id,
            product_code=product_code, option_id=option_id,
            price_amount=price_amount, price_confirmed_at=confirmed_at,
            price_expires_at=confirmed_at + timedelta(minutes=ttl_minutes),
        )

    def store_stock_quote(
        self, *, company_id: int, connection_id: int, product_code: str,
        option_id: str, in_stock: bool, confirmed_at: datetime | None = None,
    ) -> PriceStockQuoteCache:

        confirmed_at = confirmed_at or datetime.utcnow()
        ttl_minutes = self.get_stock_cache_ttl_minutes(company_id)
        return self.repository.upsert_quote_cache(
            company_id=company_id, connection_id=connection_id,
            product_code=product_code, option_id=option_id,
            in_stock=in_stock, stock_confirmed_at=confirmed_at,
            stock_expires_at=confirmed_at + timedelta(minutes=ttl_minutes),
        )

    # ------------------------------
    # 가상재고 0 제안 — Phase 9F(8-19)
    # ------------------------------

    def propose_zero_stock(
        self, *, company_id: int, connection_id: int, product_code: str,
        reason: str,
    ) -> VirtualStockZeroProposal:
        """공급처 판매 가능 여부를 확인할 수 없을 때 호출한다. 이미
        같은 상품에 대한 PENDING 제안이 있으면 중복으로 새로 만들지
        않고 기존 제안을 그대로 반환한다(같은 문제를 반복 제안하지
        않는다 — 8-16의 "임계치에 처음 도달할 때만 알림" 원칙과
        같은 정신)."""

        existing = self.repository.get_pending_zero_stock_proposal(
            company_id, connection_id, product_code,
        )
        if existing is not None:
            return existing

        if not reason or not reason.strip():
            raise BadRequestException("제안 사유를 입력해야 합니다.")

        proposal = VirtualStockZeroProposal(
            company_id=company_id, connection_id=connection_id,
            product_code=product_code, reason=reason.strip(),
        )
        proposal = self.repository.add_zero_stock_proposal(proposal)

        self._notify_zero_stock_proposal(proposal)

        return proposal

    def _notify_zero_stock_proposal(
        self, proposal: VirtualStockZeroProposal,
    ) -> None:

        try:
            from app.domains.user.model import User

            for user in (
                self.db.query(User)
                .filter(
                    User.company_id == proposal.company_id,
                    User.is_active.is_(True),
                )
                .all()
            ):
                if (user.role or "").strip().upper() != "SUPER_ADMIN":
                    continue

                dispatch_operational_event(
                    self.db, "VIRTUAL_STOCK_ZERO_PROPOSED",
                    company_id=proposal.company_id, user_id=user.id,
                    idempotency_key=f"virtual-stock-zero-proposal:{proposal.id}",
                    title="가상재고 0 제안이 생성됐습니다",
                    message=(
                        f"상품 {proposal.product_code}의 매입처 판매 가능 "
                        f"여부를 확인할 수 없습니다({proposal.reason}) — "
                        "가상재고를 0으로 낮추는 제안이 만들어졌습니다. "
                        "이미 들어온 주문이 있는지 직접 확인하고 승인/거부를 "
                        "결정해 주세요(자동으로 취소되지 않습니다)."
                    ),
                    link_path="price-stock-safety",
                    entity_ref=f"virtual_stock_zero_proposal:{proposal.id}",
                    reason="SUPPLIER_SELLABILITY_UNCONFIRMED",
                    entity_summary=f"상품 {proposal.product_code}",
                )
        except Exception:  # noqa: BLE001 — 알림 실패가 제안 생성을 되돌리면 안 된다
            pass

    def has_pending_zero_stock_proposal(
        self, company_id: int, product_code: str,
    ) -> bool:

        return self.repository.has_pending_zero_stock_proposal(
            company_id, product_code,
        )

    def list_zero_stock_proposals(
        self, company_id: int, *, status: str | None = None,
    ) -> list[VirtualStockZeroProposal]:

        return self.repository.list_zero_stock_proposals(
            company_id, status=status,
        )

    def resolve_zero_stock_proposal(
        self, proposal_id: int, company_id: int, *, is_admin: bool,
        resolved_by: int, approve: bool, resolution_note: str,
    ) -> VirtualStockZeroProposal:
        """승인(APPROVED)이든 거부(REJECTED)든 항상 사람이 직접
        결정해야 한다 — 재고 확인이 나중에 성공해도 이 메서드를
        거치지 않으면 이 제안은 영원히 PENDING으로 남는다(자동
        복구 없음)."""

        if not is_admin:
            raise ForbiddenException(
                "가상재고 0 제안 승인/거부는 관리자만 가능합니다.",
            )

        proposal = self.repository.get_zero_stock_proposal(
            proposal_id, company_id,
        )
        if proposal is None:
            raise NotFoundException("가상재고 0 제안을 찾을 수 없습니다.")
        if proposal.status != VirtualStockZeroProposalStatus.PENDING:
            raise BadRequestException(
                f"이미 처리된 제안입니다(현재 상태: {proposal.status}).",
            )
        if not resolution_note or not resolution_note.strip():
            raise BadRequestException(
                "처리 사유(무엇을 확인했는지)를 입력해야 합니다.",
            )

        proposal.status = (
            VirtualStockZeroProposalStatus.APPROVED if approve
            else VirtualStockZeroProposalStatus.REJECTED
        )
        proposal.resolved_by = resolved_by
        proposal.resolved_at = datetime.utcnow()
        proposal.resolution_note = resolution_note.strip()
        self.db.commit()
        self.db.refresh(proposal)

        return proposal


__all__ = ["PriceStockSafetyService"]
