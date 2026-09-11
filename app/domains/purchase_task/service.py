"""
=========================================================
Homez OS

File : app/domains/purchase_task/service.py

Gate PT-1(2026-08-22 15차 지시) — A(링크 전달)+E(작업 큐)+F(주문번호·
송장 가져오기) Workflow 오케스트레이션. 소비자 계정 자동 로그인·
DOM 자동화·CAPTCHA/OTP 우회는 이 파일 어디에도 없다 — 사람이 직접
링크를 열고 로그인·결제한다.

예산 예약은 retail_purchase/service.py와 동일한 원자적 조건부
UPDATE 패턴을 재사용하되(FundingHold는 이 도메인과도 keyspace가
맞지 않아 재사용하지 않음 — 동일 이유), 이 도메인 고유의 "사람이
결제하는 동안 무기한 점유되지 않도록" 만료·연장 개념을 추가한다.
=========================================================
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.funding.service import FundingService
from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)
from app.domains.notification_center.service import NotificationService
from app.domains.purchase_task.constants import BudgetReservationStatus
from app.domains.purchase_task.constants import EmailNotificationEventType
from app.domains.purchase_task.constants import PurchaseTaskCreationSource
from app.domains.purchase_task.constants import PurchaseTaskFailureCode
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.email_provider import PurchaseTaskEmailProvider
from app.domains.purchase_task.email_service import PurchaseTaskEmailService
from app.domains.purchase_task.margin_calculator import CandidateCostInput
from app.domains.purchase_task.margin_calculator import calculate_margin
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskBudgetReservation
from app.domains.purchase_task.model import PurchaseTaskCandidate
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyCheckInput
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyDecision
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.purchase_task.search_link_builder import build_search_keyword
from app.domains.purchase_task.search_link_builder import (
    build_search_urls_for_all_malls,
)
from app.domains.purchase_task.url_validation import validate_candidate_url
from app.domains.retail_purchase.product_matching import ProductAttributes
from app.domains.retail_purchase.product_matching import evaluate_same_product


def _audit(
    db: Session, *, company_id: int, user_id: int | None, action: str,
    entity_id: int, description: str,
) -> None:

    write_audit_log(
        db, user_id=user_id, action=action, entity="purchase_task",
        entity_id=str(entity_id), description=description,
        company_id=company_id,
    )


class PurchaseTaskService:

    def __init__(
        self, db: Session, email_provider: PurchaseTaskEmailProvider | None = None,
    ):

        self.db = db
        self.repository = PurchaseTaskRepository(db)
        self.policy = PurchaseTaskPolicyService(db)
        self.safety = SafetyService(db)
        self.funding = FundingService(db)
        self.email_service = PurchaseTaskEmailService(db, provider=email_provider)
        self.notification_service = NotificationService(db)

    # --------------------------------------------------
    # 작업 I/G — 알림(이메일 + 콘솔 알림센터, 단일 진실 공급원).
    # 별도 알림 저장소를 새로 만들지 않고 notification_center를
    # 그대로 재사용한다(topbar 알림종이 이 도메인의 유일한 창구가
    # 되도록). 이메일은 회사의 활성 사용자 전원에게, 그 사람의
    # PurchaseTaskEmailPreference에 따라 선택적으로 발송한다.
    # --------------------------------------------------

    def _notify(
        self, task: PurchaseTask, event_type: str, detail: str,
    ) -> None:

        from app.domains.user.model import User

        try:
            self.notification_service.notify_company(
                company_id=task.company_id, category="purchase_task",
                level=(
                    "error"
                    if event_type in (
                        EmailNotificationEventType.PURCHASE_FAILED,
                        EmailNotificationEventType.RESULT_UNCERTAIN,
                        EmailNotificationEventType.EMERGENCY_STOP_ACTIVE,
                    ) else "info"
                ),
                title=f"[{task.product_title}] {event_type}",
                message=detail,
                link_path=f"purchase-task-detail?id={task.id}",
            )
        except Exception:  # noqa: BLE001 — 알림 실패가 본 작업을 막지 않는다
            self.db.rollback()

        users = (
            self.db.query(User)
            .filter(User.company_id == task.company_id, User.is_active.is_(True))
            .all()
        )
        for user in users:
            if not user.email:
                continue
            self.email_service.send_notification(
                company_id=task.company_id, user_id=user.id,
                to_email=user.email, event_type=event_type,
                purchase_task_id=task.id, product_title=task.product_title,
                detail=detail,
                idempotency_key=f"pt:{task.id}:{event_type}:{user.id}",
            )

    def _demote_price_change_on_increase(
        self, company_id: int, task: "PurchaseTask",
    ) -> None:
        """
        2026-09-09 Phase 4(HOMEZ_USER_OPERATION_SETTINGS.md 3·13번 —
        "가격 또는 재고가 변경되면 새로운 판매와 발주를 중지하고
        사용자에게 알린다", "가격 인상... 항목이 발생하면 관련 자동
        기능만 중지한다") — 매입처 가격 인상이 감지돼 이번 발주가
        BLOCK된 시점에, PRICE_CHANGE 기능을 ERROR로 낮추고 통지한다
        (Phase 3에서 만든 `demote_function_to_error`를 실제로 호출하는
        첫 연결 지점).

        이미 ERROR 상태면 다시 낮추거나 다시 알리지 않는다(멱등) —
        가격 인상 감지는 평가할 때마다 반복될 수 있어(같은 후보를 여러
        번 재평가), 매번 이력을 쌓고 매번 알림을 보내면 감사 기록과
        받은편지함이 의미 없이 불어난다. 관리자가 확인 후 다시
        "반자동"/"자동"으로 되돌리면 그 다음 가격 인상에는 다시
        새로 감지된다.
        """

        safety = SafetyService(self.db)

        if safety.get_function_mode(company_id, FunctionCode.PRICE_CHANGE) == FunctionMode.ERROR:
            return

        try:
            safety.demote_function_to_error(
                company_id, FunctionCode.PRICE_CHANGE,
                reason=(
                    f"매입처 가격 인상 감지(purchase_task id={task.id}, "
                    f"상품: {task.product_title})로 자동 중지됨."
                ),
            )
        except Exception:  # noqa: BLE001 — 강등 실패가 발주 차단 자체를 막지 않는다
            return

        try:
            from app.domains.user.model import User

            for user in (
                self.db.query(User)
                .filter(User.company_id == company_id, User.is_active.is_(True))
                .all()
            ):
                if (user.role or "").strip().upper() != "SUPER_ADMIN":
                    continue
                NotificationDeliveryService(self.db).dispatch(
                    "FUNCTION_AUTOMATION_DEMOTED_TO_ERROR",
                    company_id=company_id, user_id=user.id,
                    idempotency_key=(
                        f"func-error-{FunctionCode.PRICE_CHANGE}-"
                        f"{company_id}-task{task.id}"
                    ),
                    title="가격 변경 기능이 오류 상태로 낮아졌습니다.",
                    message=(
                        f"[{task.product_title}] 매입처 가격 인상이 감지돼 "
                        "가격 변경 자동화가 중지됐습니다. 확인 후 필요하면 "
                        "설정 화면에서 다시 수동으로 전환해 주세요."
                    ),
                    link_path="purchase-task-detail",
                    entity_ref=f"purchase_task:{task.id}",
                    to_email=user.email,
                    reason="price_increase_detected",
                    console_url=f"/console#purchase-task-detail?id={task.id}",
                )
        except Exception:  # noqa: BLE001 — 알림 실패가 발주 차단 자체를 막지 않는다
            pass

    # --------------------------------------------------
    # 작업 A — 구매 작업 생성 + 검색 링크
    # --------------------------------------------------

    def create_task(
        self, company_id: int, *, source_order_id: int,
        source_order_item_id: int | None, product_title: str,
        brand: str | None, manufacturer: str | None, model_name: str | None,
        gtin: str | None, capacity: str | None, quantity: int,
        color_or_scent: str | None, options: list[str] | None = None,
        components: list[str] | None = None,
        shippable_region_note: str | None = None,
        coupang_sale_amount: float | None = None,
        coupang_fee_amount: float | None = None,
        purchase_deadline: datetime | None = None,
        idempotency_key: str, correlation_id: str | None = None,
        created_by: int | None = None,
        creation_source: str = PurchaseTaskCreationSource.MANUAL,
        initial_status: str | None = None,
        initial_caution_reason: str | None = None,
    ) -> PurchaseTask:

        existing = self.repository.get_task_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing

        if source_order_item_id is not None:
            # 2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_
            # LEDGER_AUDIT_20260907.md §3/§8) — idempotency_key만으로는
            # 같은 품목에 대한 중복 작업 생성을 막지 못한다(서로 다른
            # key로 두 번 POST하면 그대로 통과했던 결함). 이 품목에
            # 대해 진행 중이거나 이미 완료된 작업이 있으면 차단한다
            # — SAFE_TO_RECREATE_AFTER에 속한 종결 상태(실패/차단/
            # 재고충분/원주문취소)일 때만 새 작업 생성을 허용한다.
            latest = self.repository.get_task_by_source_order_item(
                company_id, source_order_item_id,
            )
            if (
                latest is not None
                and latest.status not in PurchaseTaskStatus.SAFE_TO_RECREATE_AFTER
            ):
                raise BadRequestException(
                    f"주문 품목 {source_order_item_id}에는 이미 진행 중이거나 "
                    f"완료된 매입 작업(#{latest.id}, 상태: {latest.status})이 "
                    "있습니다 — 중복 매입 방지를 위해 새 작업을 만들 수 "
                    "없습니다.",
                )

        task = PurchaseTask(
            company_id=company_id, source_order_id=source_order_id,
            source_order_item_id=source_order_item_id,
            product_title=product_title, brand=brand,
            manufacturer=manufacturer, model_name=model_name, gtin=gtin,
            capacity=capacity, quantity=quantity,
            color_or_scent=color_or_scent,
            options_json=json.dumps(options or [], ensure_ascii=False),
            components_json=json.dumps(components or [], ensure_ascii=False),
            shippable_region_note=shippable_region_note,
            status=initial_status or PurchaseTaskStatus.SEARCH_REQUIRED,
            caution_reason=initial_caution_reason,
            coupang_sale_amount=coupang_sale_amount,
            coupang_fee_amount=coupang_fee_amount,
            purchase_deadline=purchase_deadline,
            idempotency_key=idempotency_key, correlation_id=correlation_id,
            creation_source=creation_source,
        )
        task = self.repository.add_task(task)

        _audit(
            self.db, company_id=company_id, user_id=created_by,
            action="PURCHASE_TASK_CREATED", entity_id=task.id,
            description=f"구매 작업 생성: {product_title}",
        )

        self.db.commit()
        if task.status == PurchaseTaskStatus.REVIEW_REQUIRED:
            self._notify(
                task, EmailNotificationEventType.REVIEW_REQUIRED,
                f"상품정보 확인이 필요합니다: {product_title}",
            )
        elif task.status not in PurchaseTaskStatus.TERMINAL:
            self._notify(
                task, EmailNotificationEventType.TASK_CREATED,
                f"새 구매 작업이 생성되었습니다: {product_title}",
            )
        return task

    def get_search_links(self, task: PurchaseTask) -> dict:
        """작업 B — DB 쓰기 없는 순수 조회. 검색결과 페이지를 가져와
        파싱하지 않는다(크롤링 아님) — 사람이 열어 볼 URL만 만든다."""

        options = json.loads(task.options_json or "[]")
        keyword = build_search_keyword(
            brand=task.brand, product_title=task.product_title,
            model_name=task.model_name, gtin=task.gtin,
            capacity=task.capacity, quantity=task.quantity,
            color_or_scent=task.color_or_scent, options=options,
        )
        return {
            "keyword": keyword,
            "urls": build_search_urls_for_all_malls(keyword),
        }

    def _get_task_required(self, task_id: int, company_id: int) -> PurchaseTask:

        task = self.repository.get_task(task_id, company_id)
        if task is None:
            raise NotFoundException("구매 작업을 찾을 수 없습니다.")
        return task

    def _advance_version(self, task: PurchaseTask, expected_version: int) -> None:
        """낙관적 동시성 — WHERE version=:expected로 갱신, 불일치 시
        ConflictException(다른 사람이 먼저 바꿨다는 뜻)."""

        result = self.db.execute(
            update(PurchaseTask)
            .where(
                PurchaseTask.id == task.id,
                PurchaseTask.version == expected_version,
            )
            .values(version=PurchaseTask.version + 1),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "다른 곳에서 이미 이 구매 작업을 변경했습니다 — "
                "새로고침 후 다시 시도하세요.",
            )
        task.version = expected_version + 1

    # --------------------------------------------------
    # 작업 B/C — 후보 등록 + 동일상품 판정
    # --------------------------------------------------

    def add_candidate(
        self, task_id: int, company_id: int, *, shopping_mall_code: str,
        product_url: str, candidate_title: str | None = None,
        brand: str | None = None, manufacturer: str | None = None,
        model_name: str | None = None, gtin: str | None = None,
        capacity: str | None = None, color_or_scent: str | None = None,
        options: list[str] | None = None,
        estimated_price: float | None = None,
        estimated_shipping_fee: float | None = None,
        estimated_delivery_days: int | None = None,
        seller_trust_score: float | None = None,
        return_allowed: bool | None = None, added_by: int | None = None,
    ) -> PurchaseTaskCandidate:

        task = self._get_task_required(task_id, company_id)

        if task.status not in (
            PurchaseTaskStatus.SEARCH_REQUIRED,
            PurchaseTaskStatus.CANDIDATES_READY,
            PurchaseTaskStatus.REVIEW_REQUIRED,
        ):
            raise BadRequestException(
                "이 상태에서는 후보를 추가할 수 없습니다. "
                f"(현재: {task.status})",
            )

        validated_url = validate_candidate_url(product_url, shopping_mall_code)

        if self.repository.get_candidate_by_url(task_id, validated_url):
            raise BadRequestException(
                "이미 등록된 후보 URL입니다(중복 등록 방지).",
            )

        candidate = PurchaseTaskCandidate(
            company_id=company_id, purchase_task_id=task_id,
            shopping_mall_code=shopping_mall_code, product_url=validated_url,
            candidate_title=candidate_title, brand=brand,
            manufacturer=manufacturer, model_name=model_name, gtin=gtin,
            capacity=capacity, color_or_scent=color_or_scent,
            options_json=json.dumps(options or [], ensure_ascii=False),
            estimated_price=estimated_price,
            estimated_shipping_fee=estimated_shipping_fee,
            estimated_delivery_days=estimated_delivery_days,
            seller_trust_score=seller_trust_score,
            return_allowed=return_allowed,
        )
        candidate = self.repository.add_candidate(candidate)

        first_candidate = task.status == PurchaseTaskStatus.SEARCH_REQUIRED
        if first_candidate:
            task.status = PurchaseTaskStatus.CANDIDATES_READY

        _audit(
            self.db, company_id=company_id, user_id=added_by,
            action="PURCHASE_TASK_CANDIDATE_ADDED", entity_id=task_id,
            description=f"후보 등록: {shopping_mall_code} {validated_url}",
        )

        self.db.commit()
        if first_candidate:
            self._notify(
                task, EmailNotificationEventType.CANDIDATES_READY,
                f"구매 후보가 준비되었습니다: {shopping_mall_code}",
            )
        return candidate

    @staticmethod
    def _candidate_fingerprint(
        candidate: PurchaseTaskCandidate, source_attrs: dict,
    ) -> str:

        snapshot = {
            "product_url": candidate.product_url,
            "brand": candidate.brand, "manufacturer": candidate.manufacturer,
            "model_name": candidate.model_name, "gtin": candidate.gtin,
            "capacity": candidate.capacity,
            "color_or_scent": candidate.color_or_scent,
            "source": source_attrs,
        }
        return hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        ).hexdigest()

    def run_match_check(
        self, task_id: int, candidate_id: int, company_id: int, *,
        source_attrs: dict, confirmed_by: int,
    ) -> PurchaseTaskCandidate:
        """작업 C — 기존 retail_purchase/product_matching.py를 그대로
        재사용한다(중복 구현하지 않음)."""

        task = self._get_task_required(task_id, company_id)
        candidate = self.repository.get_candidate(candidate_id, company_id)
        if candidate is None or candidate.purchase_task_id != task.id:
            raise NotFoundException("후보를 찾을 수 없습니다.")

        candidate_options = tuple(json.loads(candidate.options_json or "[]"))
        candidate_attrs = ProductAttributes(
            brand=candidate.brand, manufacturer=candidate.manufacturer,
            model_name=candidate.model_name, gtin=candidate.gtin,
            capacity=candidate.capacity, quantity=task.quantity,
            color_or_scent=candidate.color_or_scent, options=candidate_options,
        )
        source_options = tuple(source_attrs.get("options") or [])
        source_product_attrs = ProductAttributes(
            brand=source_attrs.get("brand"),
            manufacturer=source_attrs.get("manufacturer"),
            model_name=source_attrs.get("model_name"),
            gtin=source_attrs.get("gtin"),
            capacity=source_attrs.get("capacity"),
            quantity=task.quantity,
            color_or_scent=source_attrs.get("color_or_scent"),
            options=source_options,
        )

        result = evaluate_same_product(source_product_attrs, candidate_attrs)

        candidate.match_confidence = result.confidence
        candidate.match_tier = result.tier
        candidate.match_evidence_json = json.dumps(
            [
                {
                    "criterion": e.criterion, "source_value": e.source_value,
                    "candidate_value": e.candidate_value, "matched": e.matched,
                }
                for e in result.evidence
            ],
            ensure_ascii=False,
        )
        candidate.match_confirmed_by = confirmed_by
        candidate.match_confirmed_at = datetime.utcnow()
        candidate.match_fingerprint = self._candidate_fingerprint(
            candidate, source_attrs,
        )

        _audit(
            self.db, company_id=company_id, user_id=confirmed_by,
            action="PURCHASE_TASK_MATCH_CHECKED", entity_id=task_id,
            description=(
                f"동일상품 판정: candidate={candidate_id} "
                f"tier={result.tier} confidence={result.confidence:.2f}"
            ),
        )

        self.db.commit()
        if result.tier == "NEEDS_REVIEW":
            self._notify(
                task, EmailNotificationEventType.MATCH_UNCERTAIN,
                f"동일상품 판정이 불확실합니다(신뢰도 {result.confidence:.2f}) "
                f"— candidate={candidate_id}",
            )
        return candidate

    def _match_confirmation_is_stale(
        self, candidate: PurchaseTaskCandidate, source_attrs: dict,
    ) -> bool:

        if candidate.match_fingerprint is None:
            return True
        return candidate.match_fingerprint != self._candidate_fingerprint(
            candidate, source_attrs,
        )

    # --------------------------------------------------
    # 작업 D/H — 마진 계산·구매처 선정·정책/예산/EStop 검사
    # --------------------------------------------------

    def evaluate_and_prepare(
        self, task_id: int, candidate_id: int, company_id: int, *,
        source_attrs: dict, evaluated_by: int,
        additional_shipping_fee: Decimal | None = None,
        return_risk_reserve: Decimal | None = None,
    ):
        """동일상품 비교 → 가격·배송비 비교 → 마진 계산 → 정책·예산·
        EStop 검사까지 한 번에 수행한다(지시문 최종 흐름 그대로).
        반환값은 (PurchaseTask, PurchaseTaskPolicyCheckResult) 튜플."""

        task = self._get_task_required(task_id, company_id)
        candidate = self.repository.get_candidate(candidate_id, company_id)
        if candidate is None or candidate.purchase_task_id != task.id:
            raise NotFoundException("후보를 찾을 수 없습니다.")

        if task.status not in (
            PurchaseTaskStatus.CANDIDATES_READY,
            PurchaseTaskStatus.REVIEW_REQUIRED,
        ):
            raise BadRequestException(
                "CANDIDATES_READY/REVIEW_REQUIRED 상태에서만 후보를 "
                f"선정할 수 있습니다. (현재: {task.status})",
            )

        if self._match_confirmation_is_stale(candidate, source_attrs):
            raise BadRequestException(
                "동일상품 판정이 아직 없거나(또는 후보 속성이 바뀌어) "
                "무효화되었습니다 — 동일상품 판정을 다시 실행하세요.",
            )

        if candidate.match_tier == "BLOCKED":
            task.status = PurchaseTaskStatus.BLOCKED
            task.block_reason = "PRODUCT_MATCH_INSUFFICIENT"
            task.failure_code = PurchaseTaskFailureCode.PRODUCT_MATCH_INSUFFICIENT
            self.db.commit()
            self._notify(
                task, EmailNotificationEventType.PURCHASE_FAILED,
                "동일상품 판정 실패로 차단되었습니다.",
            )
            from app.domains.purchase_task.policy_service import (
                PurchaseTaskPolicyCheckResult,
            )
            return task, PurchaseTaskPolicyCheckResult(
                decision=PurchaseTaskPolicyDecision.BLOCK,
                reasons=("PRODUCT_MATCH_INSUFFICIENT",),
            )

        policy_setting = self.policy.get_or_create_default_settings(company_id)
        add_ship = (
            additional_shipping_fee
            if additional_shipping_fee is not None
            else Decimal(str(policy_setting.default_additional_shipping_fee))
        )
        return_reserve = (
            return_risk_reserve
            if return_risk_reserve is not None
            else Decimal(str(policy_setting.default_return_risk_reserve))
        )

        margin_result = None
        required_budget = None
        if (
            task.coupang_sale_amount is not None
            and task.coupang_fee_amount is not None
            and candidate.estimated_price is not None
            and candidate.estimated_shipping_fee is not None
        ):
            cost_input = CandidateCostInput(
                candidate_id=candidate.id,
                estimated_price=Decimal(str(candidate.estimated_price)),
                estimated_shipping_fee=Decimal(
                    str(candidate.estimated_shipping_fee),
                ),
                confirmed_additional_cost=Decimal(
                    str(candidate.confirmed_additional_cost),
                ),
                confirmed_discount=Decimal(str(candidate.confirmed_discount)),
            )
            margin_result = calculate_margin(
                cost_input,
                coupang_sale_amount=Decimal(str(task.coupang_sale_amount)),
                coupang_fee_amount=Decimal(str(task.coupang_fee_amount)),
                additional_shipping_fee=add_ship,
                return_risk_reserve=return_reserve,
            )
            required_budget = margin_result.actual_purchase_cost + add_ship

            # 2026-09-10 Phase 10 — 이 후보를 처음 평가하는 순간(컬럼이
            # 아직 None)에만 가격 기준선을 확정한다. 이후 재평가마다는
            # 이 최초 기준선과 비교해야 "가격이 올랐는지"를 판정할 수
            # 있다 — 매번 다시 채우면 항상 자기 자신과 비교하게 돼
            # 인상률이 영원히 0이 된다(Phase 4에서 이 컬럼 자체가 없어
            # 판정이 통째로 건너뛰어지던 것과 동일한 결과를 다시
            # 만들게 되는 실수 — 그래서 반드시 "None일 때만" 채운다).
            if candidate.expected_amount_at_creation is None:
                candidate.expected_amount_at_creation = float(required_budget)

        policy_input = PurchaseTaskPolicyCheckInput(
            match_confidence=candidate.match_confidence,
            match_tier=candidate.match_tier, quantity=task.quantity,
            gtin=task.gtin, model_name=task.model_name,
            expected_net_profit=(
                margin_result.expected_net_profit if margin_result else None
            ),
            expected_margin_rate=(
                margin_result.expected_margin_rate if margin_result else None
            ),
            required_budget_amount=required_budget,
            estimated_delivery_days=candidate.estimated_delivery_days,
            return_allowed=candidate.return_allowed,
            expected_amount_at_creation=(
                Decimal(str(candidate.expected_amount_at_creation))
                if candidate.expected_amount_at_creation is not None
                else None
            ),
        )
        result = self.policy.evaluate(company_id, policy_input)

        task.selected_candidate_id = candidate.id
        candidate.is_selected = True
        if margin_result is not None:
            task.expected_net_profit = float(margin_result.expected_net_profit)
            task.expected_margin_rate = float(margin_result.expected_margin_rate)
        task.policy_fingerprint = self.policy.current_policy_fingerprint(
            company_id,
        )

        # 이전 평가(다른 후보 또는 같은 후보의 이전 시도)에서 남은
        # block_reason/caution_reason/failure_code가 이번 결정과 무관하게
        # 화면에 남아있으면 안 된다 — 실 브라우저 검증 중 발견: NEEDS_REVIEW
        # 후보를 REQUIRE_REVIEW로 평가한 뒤 같은 작업을 AUTO_CANDIDATE
        # 후보로 다시 평가해 PURCHASE_READY로 전환해도 이전 caution_reason이
        # 그대로 남아 "확인 필요" 사유가 잘못 표시됐다. 아래 각 분기가
        # 이번 결정에 해당하는 값만 다시 채운다.
        task.block_reason = None
        task.caution_reason = None
        task.failure_code = None

        if result.decision == PurchaseTaskPolicyDecision.BLOCK:
            task.status = PurchaseTaskStatus.BLOCKED
            task.block_reason = ",".join(result.reasons)
            task.failure_code = result.reasons[0] if result.reasons else None
            task.retryable = False
            self.db.commit()
            if "EMERGENCY_STOP_ACTIVE" in result.reasons:
                event = EmailNotificationEventType.EMERGENCY_STOP_ACTIVE
            elif "BUDGET_INSUFFICIENT" in result.reasons:
                event = EmailNotificationEventType.BUDGET_INSUFFICIENT
            elif any(
                r in result.reasons
                for r in ("MIN_PROFIT_NOT_MET", "MIN_MARGIN_RATE_NOT_MET")
            ):
                event = EmailNotificationEventType.MARGIN_INSUFFICIENT
            else:
                event = EmailNotificationEventType.PURCHASE_FAILED
            self._notify(task, event, f"차단됨: {task.block_reason}")

            if "PRICE_INCREASE_RATE_EXCEEDED" in result.reasons:
                self._demote_price_change_on_increase(company_id, task)
            return task, result

        if result.decision == PurchaseTaskPolicyDecision.REQUIRE_REVIEW:
            task.status = PurchaseTaskStatus.REVIEW_REQUIRED
            task.caution_reason = ",".join(result.reasons) or "REQUIRE_REVIEW"
            self.db.commit()
            self._notify(
                task, EmailNotificationEventType.REVIEW_REQUIRED,
                f"확인이 필요합니다: {task.caution_reason}",
            )
            return task, result

        # ALLOW — 예산 예약까지 이어서 수행한다.
        if required_budget is None:
            task.status = PurchaseTaskStatus.REVIEW_REQUIRED
            task.caution_reason = "EVIDENCE_REQUIRED"
            self.db.commit()
            self._notify(
                task, EmailNotificationEventType.REVIEW_REQUIRED,
                "매입비/판매금액 정보가 부족해 확인이 필요합니다.",
            )
            return task, result

        self._reserve_budget(task, required_budget, evaluated_by)

        _audit(
            self.db, company_id=company_id, user_id=evaluated_by,
            action="PURCHASE_TASK_EVALUATED", entity_id=task_id,
            description=(
                f"후보 선정+정책평가: candidate={candidate_id} "
                f"decision={result.decision}"
            ),
        )

        self.db.commit()
        if task.status == PurchaseTaskStatus.PURCHASE_READY:
            self._notify(
                task, EmailNotificationEventType.PAYMENT_REQUIRED,
                f"최종 결제가 필요합니다 — 예상 순이익 "
                f"{task.expected_net_profit:,.0f}원.",
            )
        elif task.status == PurchaseTaskStatus.BLOCKED:
            self._notify(
                task, EmailNotificationEventType.BUDGET_INSUFFICIENT,
                "매입예산이 부족해 차단되었습니다.",
            )
        return task, result

    # --------------------------------------------------
    # 예산 예약(만료·연장 포함) — retail_purchase와 동일한 원자적
    # 조건부 UPDATE 패턴.
    # --------------------------------------------------

    def _reserve_budget_conditional(self, account_id: int, amount: float) -> bool:

        result = self.db.execute(
            update(FundingAccount)
            .where(
                FundingAccount.id == account_id,
                (FundingAccount.total_funding - FundingAccount.held_amount)
                >= amount,
            )
            .values(held_amount=FundingAccount.held_amount + amount),
        )
        return result.rowcount == 1

    def _release_budget(self, account_id: int, amount: float) -> None:

        self.db.execute(
            update(FundingAccount)
            .where(FundingAccount.id == account_id)
            .values(held_amount=FundingAccount.held_amount - amount),
        )

    def _append_ledger(
        self, account_id: int, company_id: int, amount: float,
        entry_type: str, task_id: int, memo: str,
    ) -> None:

        self.db.add(FundingLedger(
            company_id=company_id, account_id=account_id, amount=amount,
            type=entry_type, reference_type="purchase_task",
            reference_id=task_id, memo=memo,
        ))

    def _reserve_budget(
        self, task: PurchaseTask, amount: Decimal, reserved_by: int | None,
    ) -> PurchaseTaskBudgetReservation:

        account = self.funding.repository.get_account_by_company(task.company_id)
        if account is None:
            task.status = PurchaseTaskStatus.BLOCKED
            task.block_reason = PurchaseTaskFailureCode.BUDGET_INSUFFICIENT
            task.failure_code = PurchaseTaskFailureCode.BUDGET_INSUFFICIENT
            return None

        amount_f = float(amount)
        ok = self._reserve_budget_conditional(account.id, amount_f)
        if not ok:
            task.status = PurchaseTaskStatus.BLOCKED
            task.block_reason = PurchaseTaskFailureCode.BUDGET_INSUFFICIENT
            task.failure_code = PurchaseTaskFailureCode.BUDGET_INSUFFICIENT
            return None

        setting = self.policy.get_or_create_default_settings(task.company_id)
        expires_at = datetime.utcnow() + timedelta(
            hours=setting.budget_reservation_hours,
        )
        reservation = PurchaseTaskBudgetReservation(
            company_id=task.company_id, purchase_task_id=task.id,
            account_id=account.id, amount=amount_f,
            status=BudgetReservationStatus.RESERVED, expires_at=expires_at,
        )
        reservation = self.repository.add_reservation(reservation)

        self._append_ledger(
            account.id, task.company_id, amount_f,
            FundingService.TYPE_HOLD_CREATE, task.id, "매입 작업 예산 예약",
        )

        task.budget_reservation_id = reservation.id
        task.status = PurchaseTaskStatus.PURCHASE_READY

        return reservation

    def extend_budget_reservation(
        self, task_id: int, company_id: int, *, extra_hours: int,
        extended_by: int | None = None,
    ) -> PurchaseTask:
        """작업 H — 사용자가 결제를 진행 중임을 표시하는 연장."""

        task = self._get_task_required(task_id, company_id)
        if task.budget_reservation_id is None:
            raise BadRequestException("예산 예약이 없는 작업입니다.")

        reservation = self.repository.get_reservation(
            task.budget_reservation_id, company_id,
        )
        if reservation is None or reservation.status not in (
            BudgetReservationStatus.RESERVED, BudgetReservationStatus.EXTENDED,
        ):
            raise BadRequestException("연장할 수 있는 활성 예산 예약이 없습니다.")

        if extra_hours <= 0:
            raise BadRequestException("연장 시간은 0보다 커야 합니다.")

        reservation.expires_at = reservation.expires_at + timedelta(
            hours=extra_hours,
        )
        reservation.status = BudgetReservationStatus.EXTENDED
        reservation.extended_at = datetime.utcnow()

        _audit(
            self.db, company_id=company_id, user_id=extended_by,
            action="PURCHASE_TASK_BUDGET_EXTENDED", entity_id=task_id,
            description=f"예산 예약 {extra_hours}시간 연장",
        )

        self.db.commit()
        return task

    def open_payment_page(
        self, task_id: int, company_id: int, opened_by: int | None = None,
    ) -> PurchaseTask:
        """작업 E — "구매 페이지 열기" 클릭 시 호출. 실제 주문이
        완료된 것처럼 자동 처리하지 않는다 — 상태만
        USER_PAYMENT_PENDING으로 바꾼다."""

        task = self._get_task_required(task_id, company_id)

        if task.status != PurchaseTaskStatus.PURCHASE_READY:
            raise BadRequestException(
                "PURCHASE_READY 상태에서만 구매 페이지를 열 수 있습니다. "
                f"(현재: {task.status})",
            )

        if self.safety.is_emergency_stop_active():
            raise BadRequestException(
                "Emergency Stop이 활성화되어 있어 진행할 수 없습니다.",
            )

        task.status = PurchaseTaskStatus.USER_PAYMENT_PENDING

        _audit(
            self.db, company_id=company_id, user_id=opened_by,
            action="PURCHASE_TASK_PAYMENT_PAGE_OPENED", entity_id=task_id,
            description="사용자 결제 대기 전환(구매 페이지 열기)",
        )

        self.db.commit()
        return task

    # --------------------------------------------------
    # 작업 F — 주문번호·실제 결제금액 기록
    # --------------------------------------------------

    def record_purchase(
        self, task_id: int, company_id: int, *, shopping_mall_code: str,
        external_order_number: str, actual_amount: float,
        actual_shipping_fee: float | None, purchased_at: datetime,
        selected_option_note: str | None, memo: str | None,
        recorded_by: int, idempotency_key: str,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)

        existing_by_key = self.repository.get_purchase_record_by_idempotency(
            company_id, idempotency_key,
        )
        if existing_by_key is not None:
            return task

        if task.status in PurchaseTaskStatus.NO_AUTO_RETRY:
            raise ConflictException(
                "이 구매 작업은 차단/실패/결과 불명확 상태입니다 — "
                "자동으로 다시 진행하지 않습니다. 쇼핑몰 주문내역을 "
                "먼저 확인하세요.",
            )

        if task.status != PurchaseTaskStatus.USER_PAYMENT_PENDING:
            raise BadRequestException(
                "USER_PAYMENT_PENDING 상태에서만 구매 결과를 등록할 "
                f"수 있습니다. (현재: {task.status})",
            )

        # 등록 직전 재확인(지시문 명시) — 중복 주문번호.
        if self.repository.get_purchase_record_by_order_number(
            company_id, shopping_mall_code, external_order_number,
        ):
            raise BadRequestException(
                f"{PurchaseTaskFailureCode.DUPLICATE_ORDER_NUMBER}: "
                "이미 등록된 구매처 주문번호입니다.",
            )

        # 등록 직전 재확인 — EStop.
        if self.safety.is_emergency_stop_active():
            raise BadRequestException(
                f"{PurchaseTaskFailureCode.EMERGENCY_STOP_ACTIVE}: "
                "Emergency Stop이 활성화되어 있습니다.",
            )

        # 등록 직전 재확인 — 정책 fingerprint.
        current_fp = self.policy.current_policy_fingerprint(company_id)
        if task.policy_fingerprint is not None and current_fp != task.policy_fingerprint:
            raise ConflictException(
                f"{PurchaseTaskFailureCode.POLICY_CHANGED_SINCE_CHECK}: "
                "정책 검사 이후 정책이 변경되어 이전 판정을 더 진행할 "
                "수 없습니다 — 후보 선정을 다시 수행하세요.",
            )

        # 등록 직전 재확인 — 예산(예약이 만료됐으면 실제 결제금액으로
        # 재검증하며 다시 예약한다).
        reservation = None
        if task.budget_reservation_id is not None:
            reservation = self.repository.get_reservation(
                task.budget_reservation_id, company_id,
            )

        account = self.funding.repository.get_account_by_company(company_id)
        if account is None:
            raise BadRequestException("사업 운영자금 계정이 없습니다.")

        if reservation is not None and reservation.status in (
            BudgetReservationStatus.RESERVED, BudgetReservationStatus.EXTENDED,
        ):
            diff = actual_amount - reservation.amount
            if diff != 0:
                if diff > 0 and not self._reserve_budget_conditional(
                    account.id, diff,
                ):
                    raise BadRequestException(
                        f"{PurchaseTaskFailureCode.BUDGET_INSUFFICIENT}: "
                        "실제 결제금액이 예약금액을 초과했고, 초과분을 "
                        "예약할 수 있는 운영 가능 금액이 부족합니다.",
                    )
                if diff < 0:
                    self._release_budget(account.id, -diff)
            reservation.status = BudgetReservationStatus.CONFIRMED
            reservation.confirmed_at = datetime.utcnow()
        else:
            # 예약이 없거나 만료됨 — 실제 결제금액 기준으로 재검증.
            if not self._reserve_budget_conditional(account.id, actual_amount):
                raise BadRequestException(
                    f"{PurchaseTaskFailureCode.BUDGET_INSUFFICIENT}: "
                    "예산 예약이 만료된 상태에서 실제 결제금액을 다시 "
                    "예약할 수 없습니다(운영 가능 금액 부족).",
                )

        self._append_ledger(
            account.id, company_id, actual_amount,
            FundingService.TYPE_HOLD_COMMIT, task.id,
            "매입 구매 완료 — 예산 확정",
        )

        # Gate PT-3(2026-09-08, item 7) — 이 작업에 배정된 연결이
        # 있으면 실제 이 구매에 쓰인 계정으로 그대로 복사해 남긴다.
        # PurchaseRecord는 불변 기록이라 이후 task.channel_connection_id
        # 가 바뀌거나 그 연결이 비활성화돼도 이 값은 절대 바뀌지 않는다.
        record = PurchaseRecord(
            company_id=company_id, purchase_task_id=task.id,
            shopping_mall_code=shopping_mall_code,
            channel_connection_id=task.channel_connection_id,
            external_order_number=external_order_number,
            actual_amount=actual_amount,
            actual_shipping_fee=actual_shipping_fee, purchased_at=purchased_at,
            selected_option_note=selected_option_note, memo=memo,
            recorded_by=recorded_by, idempotency_key=idempotency_key,
        )
        self.repository.add_purchase_record(record)

        task.status = PurchaseTaskStatus.TRACKING_REQUIRED

        _audit(
            self.db, company_id=company_id, user_id=recorded_by,
            action="PURCHASE_TASK_PURCHASE_RECORDED", entity_id=task_id,
            description=(
                f"구매 기록: {shopping_mall_code} "
                f"{external_order_number} amount={actual_amount}"
            ),
        )

        self.db.commit()
        self._notify(
            task, EmailNotificationEventType.PURCHASE_SUCCESS,
            f"구매가 완료 처리되었습니다 — {shopping_mall_code} "
            f"{actual_amount:,.0f}원.",
        )
        self._notify(
            task, EmailNotificationEventType.TRACKING_REQUIRED,
            "송장번호 입력이 필요합니다.",
        )
        return task

    # --------------------------------------------------
    # 작업 F — 송장·배송 상태
    # --------------------------------------------------

    def record_tracking(
        self, task_id: int, company_id: int, *, courier: str | None,
        courier_confirmed: bool, tracking_number: str | None,
        shipped_at: datetime | None, expected_arrival_at: datetime | None,
        is_partial_shipment: bool, recorded_by: int | None = None,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)

        if task.status != PurchaseTaskStatus.TRACKING_REQUIRED:
            raise BadRequestException(
                "TRACKING_REQUIRED 상태에서만 송장을 등록할 수 있습니다. "
                f"(현재: {task.status})",
            )

        tracking = self.repository.get_tracking(task_id, company_id)
        if tracking is None:
            tracking = PurchaseTaskTrackingInfo(
                company_id=company_id, purchase_task_id=task_id,
            )
            self.repository.add_tracking(tracking)

        tracking.courier = courier
        # 송장번호 형식만으로 택배사를 임의 확정하지 않는다 — 사용자가
        # 명시적으로 확인했을 때만 courier_confirmed=True.
        tracking.courier_confirmed = courier_confirmed
        tracking.tracking_number = tracking_number
        tracking.shipped_at = shipped_at
        tracking.expected_arrival_at = expected_arrival_at
        tracking.is_partial_shipment = is_partial_shipment
        tracking.delivery_status = "IN_TRANSIT"

        task.status = PurchaseTaskStatus.SHIPPED

        _audit(
            self.db, company_id=company_id, user_id=recorded_by,
            action="PURCHASE_TASK_TRACKING_RECORDED", entity_id=task_id,
            description=f"송장 등록: {courier or '(미확인)'} {tracking_number or ''}",
        )

        self.db.commit()

        if courier and tracking_number:
            # courier/tracking_number 둘 다 실제 값이 있을 때만
            # 판매 주문 쪽 OrderItem을 연결하고 실제 Shipment를
            # 만든다 — 둘 중 하나라도 미확인이면 실제로 발송됐다는
            # 확증이 부족하므로 이 write-back을 건너뛴다(과장 금지,
            # HOMEZ_V7_PROCUREMENT_LEDGER_AUDIT_20260907.md §2에서
            # 지적된 "PurchaseTask → OrderItem 역방향 연결 없음" 결함의
            # 수정).
            self._link_order_item_and_create_shipment(
                task, company_id, courier=courier,
                tracking_number=tracking_number, triggered_by=recorded_by,
            )

        return task

    def _link_order_item_and_create_shipment(
        self, task: PurchaseTask, company_id: int, *, courier: str,
        tracking_number: str, triggered_by: int | None,
    ) -> None:
        """
        2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_
        LEDGER_AUDIT_20260907.md §8, 사용자 확정 "purchase_task
        중심") — 실제 발송이 확인된 시점에 판매 주문 쪽 OrderItem을
        이 PurchaseTask와 연결하고, 기존 `shipment` 도메인의 실제
        출고 파이프라인을 그대로 재사용해 배송·재고·주문상태 갱신을
        전부 위임한다(새 파이프라인을 따로 만들지 않는다 — 기존
        구조 우선 원칙).

        이 도메인이 산 상품은 HOMEZ 공용 창고 재고에 들어온 적이
        없으므로, `InventoryService.reserve_externally_procured()`
        (개별 조달용 가짜 예약)로 `shipment/service.py`가 요구하는
        "OrderItem.status == RESERVED + 실제 InventoryReservation"
        계약만 형식적으로 충족시킨다 — 공용 재고 수량에는 영향이
        없다.

        `source_order_item_id`가 없는 작업(예: 직접 등록·테스트
        시나리오)은 조용히 건너뛴다 — 연결할 판매 주문 자체가 없다는
        뜻이라 오류가 아니다.
        """

        if task.source_order_item_id is None:
            return

        from app.domains.inventory.schema import InventoryReserveRequest
        from app.domains.inventory.service import InventoryService
        from app.domains.order.constants import OrderItemStatus
        from app.domains.order.repository import OrderRepository
        from app.domains.shipment.schema import ShipmentCreate
        from app.domains.shipment.service import ShipmentService

        order_repository = OrderRepository(self.db)
        order_item = order_repository.get_item_for_company(
            task.source_order_item_id, company_id,
        )

        if order_item is None:
            # 판매 주문 품목이 이미 삭제됐거나 다른 회사 소유라면
            # (있을 수 없는 상태 조합이지만) 조용히 건너뛴다 — 매입
            # 자체는 이미 완료됐으므로 이 write-back 실패로 되돌리지
            # 않는다.
            return

        if order_item.status != OrderItemStatus.OUT_OF_STOCK:
            # 이미 다른 경로(재입고 재예약, 수동 처리 등)로 상태가
            # 바뀐 뒤라면 이 write-back을 건너뛴다 — 이중 예약·이중
            # 출고를 만들지 않는다(멱등성).
            return

        order_item.purchase_task_id = task.id

        inventory_service = InventoryService(self.db)
        reservation = inventory_service.reserve_externally_procured(
            order_item.inventory_sku_id, company_id,
            InventoryReserveRequest(
                quantity=order_item.quantity,
                idempotency_key=(
                    f"purchase_task:{task.id}:external_reserve"
                ),
                reference_id=task.id,
            ),
            triggered_by,
        )

        order_item.status = OrderItemStatus.RESERVED
        order_item.reservation_id = reservation.id
        order_repository.save_item_no_commit(order_item)

        shipment_service = ShipmentService(self.db)
        shipment_service.create_shipment(
            company_id, order_item.order_id,
            ShipmentCreate(
                order_item_ids=[order_item.id],
                courier=courier,
                invoice_number=tracking_number,
                idempotency_key=f"purchase_task:{task.id}:shipment",
            ),
            triggered_by,
        )

    # ---------------- 송장 다시 조회(운영 전 최종 검증 라운드) ----------------

    _TRACKING_REFRESH_MIN_INTERVAL_SECONDS = 10

    def _find_order_code_for_task(
        self, task_id: int, company_id: int,
    ) -> tuple[str, int] | None:
        """이 작업의 "지금 조회할 수 있는" order_code와 연결 ID를
        찾는다. 실제 발주 성공(SUCCEEDED)이 가장 신뢰할 수 있는
        출처이고, 없으면 UNKNOWN을 사람이 ORDER_CONFIRMED로 확정하며
        직접 입력한 order_code를 그 다음으로 신뢰한다. 둘 다 없으면
        None — 조회할 대상 자체가 없다는 뜻이다."""

        from app.domains.purchase_task.constants import OrderSubmissionStatus
        from app.domains.purchase_task.constants import UnknownResolutionStatus
        from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt

        succeeded = (
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(
                PurchaseOrderSubmissionAttempt.purchase_task_id == task_id,
                PurchaseOrderSubmissionAttempt.company_id == company_id,
                PurchaseOrderSubmissionAttempt.status == OrderSubmissionStatus.SUCCEEDED,
                PurchaseOrderSubmissionAttempt.external_order_code.isnot(None),
            )
            .order_by(PurchaseOrderSubmissionAttempt.started_at.desc())
            .first()
        )
        if succeeded is not None:
            return succeeded.external_order_code, succeeded.connection_id

        confirmed = (
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(
                PurchaseOrderSubmissionAttempt.purchase_task_id == task_id,
                PurchaseOrderSubmissionAttempt.company_id == company_id,
                PurchaseOrderSubmissionAttempt.unknown_resolution_status
                == UnknownResolutionStatus.ORDER_CONFIRMED,
                PurchaseOrderSubmissionAttempt.unknown_resolved_order_code.isnot(None),
            )
            .order_by(PurchaseOrderSubmissionAttempt.unknown_resolved_at.desc())
            .first()
        )
        if confirmed is not None:
            return confirmed.unknown_resolved_order_code, confirmed.connection_id
        return None

    def refresh_tracking_live(
        self, task_id: int, company_id: int, *, triggered_by: int | None = None,
    ) -> PurchaseTaskTrackingInfo:
        """실제 매입처 API로 이 작업의 배송·송장 정보를 다시 조회한다
        (지시문 6번). 실제 발주 성공 상태는 절대 바꾸지 않는다 —
        `record_tracking()`과 달리 task.status나 Shipment 연결을 다시
        만들지 않고, `PurchaseTaskTrackingInfo`의 표시용 필드만
        갱신한다. 복수 송장이 감지되면 아무 필드도 덮어쓰지 않고
        예외 상태로만 기록한다(단일 송장 정책)."""

        from app.domains.purchase_task.channel_connection_service import (
            PurchaseChannelConnectionService,
        )
        from app.domains.purchase_task.constants import CapabilitySupport
        from app.domains.purchase_task.constants import TrackingRefreshResult

        task = self._get_task_required(task_id, company_id)

        tracking = self.repository.get_tracking(task_id, company_id)
        if tracking is None:
            tracking = PurchaseTaskTrackingInfo(
                company_id=company_id, purchase_task_id=task_id,
            )
            self.repository.add_tracking(tracking)

        # 반복 클릭 방지 — 마지막 조회로부터 최소 간격이 지나지
        # 않았으면 새 네트워크 호출을 만들지 않고 마지막 결과를
        # 그대로 반환한다.
        if tracking.last_live_refresh_at is not None:
            elapsed = (datetime.utcnow() - tracking.last_live_refresh_at).total_seconds()
            if elapsed < self._TRACKING_REFRESH_MIN_INTERVAL_SECONDS:
                raise ConflictException(
                    "방금 조회했습니다 — "
                    f"{int(self._TRACKING_REFRESH_MIN_INTERVAL_SECONDS - elapsed)}초 "
                    "후 다시 시도하세요.",
                )

        found = self._find_order_code_for_task(task_id, company_id)
        if found is None:
            raise ConflictException(
                "이 작업에는 아직 실제 성공한 발주 또는 확정된 order_code가 "
                "없어 송장을 조회할 대상이 없습니다.",
            )
        order_code, connection_id = found

        connection_service = PurchaseChannelConnectionService(self.db)
        result = connection_service.lookup_tracking(
            connection_id, company_id, order_code, triggered_by=triggered_by,
        )

        tracking.last_live_refresh_at = datetime.utcnow()

        if result.support != CapabilitySupport.SUPPORTED:
            tracking.last_live_refresh_result = TrackingRefreshResult.LOOKUP_FAILED
            self.db.commit()
            self.db.refresh(tracking)
            return tracking

        if result.multiple_deliveries_detected:
            # 조회 실패는 배송조회 실패로만 기록한다 — 기존에 저장된
            # 값은 그대로 둔다(임의 선택 금지).
            tracking.last_live_refresh_result = TrackingRefreshResult.MULTIPLE_DELIVERIES
            self.db.commit()
            self.db.refresh(tracking)
            return tracking

        if not result.tracking_number:
            tracking.last_live_refresh_result = TrackingRefreshResult.NOT_FOUND
            self.db.commit()
            self.db.refresh(tracking)
            return tracking

        changed = (
            tracking.tracking_number != result.tracking_number
            or tracking.courier != result.courier
        )
        if changed:
            old_courier, old_number = tracking.courier, tracking.tracking_number
            tracking.courier = result.courier
            tracking.tracking_number = result.tracking_number
            if result.delivery_status:
                tracking.delivery_status = result.delivery_status
            tracking.last_live_refresh_result = TrackingRefreshResult.UPDATED
            _audit(
                self.db, company_id=company_id, user_id=triggered_by,
                action="PURCHASE_TASK_TRACKING_REFRESH_CHANGED", entity_id=task_id,
                description=(
                    f"송장 재조회로 값 변경: 택배사 {old_courier or '(없음)'}→"
                    f"{result.courier or '(없음)'}, 송장번호 "
                    f"{old_number or '(없음)'}→{result.tracking_number or '(없음)'}"
                ),
            )
        else:
            tracking.last_live_refresh_result = TrackingRefreshResult.UNCHANGED
            if result.delivery_status:
                tracking.delivery_status = result.delivery_status

        self.db.commit()
        self.db.refresh(tracking)
        return tracking

    def mark_delivered(
        self, task_id: int, company_id: int, marked_by: int | None = None,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)
        if task.status != PurchaseTaskStatus.SHIPPED:
            raise BadRequestException(
                f"SHIPPED 상태에서만 배송완료 처리할 수 있습니다. "
                f"(현재: {task.status})",
            )

        task.status = PurchaseTaskStatus.DELIVERED

        tracking = self.repository.get_tracking(task_id, company_id)
        if tracking is not None:
            tracking.delivery_status = "DELIVERED"

        _audit(
            self.db, company_id=company_id, user_id=marked_by,
            action="PURCHASE_TASK_DELIVERED", entity_id=task_id,
            description="배송완료 처리",
        )

        self.db.commit()
        return task

    # --------------------------------------------------
    # 작업 F — 취소·반품·부분환불
    # --------------------------------------------------

    def request_cancel(
        self, task_id: int, company_id: int, reason: str,
        requested_by: int | None = None,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)
        if task.status not in (
            PurchaseTaskStatus.TRACKING_REQUIRED, PurchaseTaskStatus.SHIPPED,
        ):
            raise BadRequestException(
                "TRACKING_REQUIRED/SHIPPED 상태에서만 취소를 요청할 수 "
                f"있습니다. (현재: {task.status})",
            )

        task.status = PurchaseTaskStatus.CANCEL_REQUIRED
        tracking = self.repository.get_tracking(task_id, company_id)
        if tracking is not None:
            tracking.cancel_status = "REQUESTED"

        _audit(
            self.db, company_id=company_id, user_id=requested_by,
            action="PURCHASE_TASK_CANCEL_REQUESTED", entity_id=task_id,
            description=f"취소 요청: {reason}",
        )
        self.db.commit()
        self._notify(
            task, EmailNotificationEventType.CANCEL_RETURN_REFUND_REQUIRED,
            f"취소 처리가 필요합니다: {reason}",
        )
        return task

    def request_return(
        self, task_id: int, company_id: int, reason: str,
        requested_by: int | None = None,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)
        if task.status != PurchaseTaskStatus.DELIVERED:
            raise BadRequestException(
                f"DELIVERED 상태에서만 반품을 요청할 수 있습니다. "
                f"(현재: {task.status})",
            )

        task.status = PurchaseTaskStatus.RETURN_REQUIRED
        tracking = self.repository.get_tracking(task_id, company_id)
        if tracking is not None:
            tracking.return_status = "REQUESTED"

        _audit(
            self.db, company_id=company_id, user_id=requested_by,
            action="PURCHASE_TASK_RETURN_REQUESTED", entity_id=task_id,
            description=f"반품 요청: {reason}",
        )
        self.db.commit()
        self._notify(
            task, EmailNotificationEventType.CANCEL_RETURN_REFUND_REQUIRED,
            f"반품 처리가 필요합니다: {reason}",
        )
        return task

    def record_refund(
        self, task_id: int, company_id: int, *, refund_amount: float,
        memo: str | None = None, recorded_by: int | None = None,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)
        if task.status not in (
            PurchaseTaskStatus.CANCEL_REQUIRED, PurchaseTaskStatus.RETURN_REQUIRED,
        ):
            raise BadRequestException(
                "CANCEL_REQUIRED/RETURN_REQUIRED 상태에서만 환불을 "
                f"기록할 수 있습니다. (현재: {task.status})",
            )

        account = self.funding.repository.get_account_by_company(company_id)
        if account is not None and refund_amount > 0:
            self._release_budget(account.id, refund_amount)
            self._append_ledger(
                account.id, company_id, refund_amount,
                FundingService.TYPE_HOLD_RELEASE, task.id,
                memo or "매입 취소/반품 환불",
            )

        tracking = self.repository.get_tracking(task_id, company_id)
        if tracking is not None:
            tracking.refund_status = "REFUNDED"
            tracking.refund_amount = refund_amount

        task.status = PurchaseTaskStatus.REFUND_PENDING

        _audit(
            self.db, company_id=company_id, user_id=recorded_by,
            action="PURCHASE_TASK_REFUND_RECORDED", entity_id=task_id,
            description=f"환불 기록: {refund_amount}",
        )
        self.db.commit()
        return task

    def complete_task(
        self, task_id: int, company_id: int, completed_by: int | None = None,
    ) -> PurchaseTask:

        task = self._get_task_required(task_id, company_id)
        if task.status not in (
            PurchaseTaskStatus.DELIVERED, PurchaseTaskStatus.REFUND_PENDING,
        ):
            raise BadRequestException(
                "DELIVERED/REFUND_PENDING 상태에서만 완료 처리할 수 "
                f"있습니다. (현재: {task.status})",
            )

        task.status = PurchaseTaskStatus.COMPLETED

        _audit(
            self.db, company_id=company_id, user_id=completed_by,
            action="PURCHASE_TASK_COMPLETED", entity_id=task_id,
            description="구매 작업 완료 처리",
        )
        self.db.commit()
        return task

    # --------------------------------------------------
    # 작업 H — 예산 예약 만료 일괄 처리(읽기 시점 지연 평가 —
    # automation_safety.EligibilityService와 동일 철학, 별도
    # 스케줄러 없음. get_task()/list_tasks() 조회 시점에 호출한다).
    # --------------------------------------------------

    def release_expired_reservations(self, now: datetime | None = None) -> int:

        now = now or datetime.utcnow()
        released = 0

        for reservation in self.repository.list_expiring_active_reservations(now):
            task = self.repository.get_task(
                reservation.purchase_task_id, reservation.company_id,
            )
            if task is None:
                continue

            if task.status == PurchaseTaskStatus.USER_PAYMENT_PENDING and (
                task.purchase_deadline is not None
                and now > task.purchase_deadline
            ):
                # 결제 기한을 넘긴 채 결제 대기 중이었다 — 이미
                # 결제했을 가능성을 배제할 수 없으므로 UNCERTAIN으로
                # 전환하고 예산은 반환하지 않는다(중복결제 방지 우선).
                task.status = PurchaseTaskStatus.UNCERTAIN
                task.failure_code = "PAYMENT_STATUS_UNKNOWN_AFTER_DEADLINE"
                task.retryable = False
                reservation.status = BudgetReservationStatus.EXPIRED
                self.db.commit()
                self._notify(
                    task, EmailNotificationEventType.RESULT_UNCERTAIN,
                    "결제 기한이 지났는데도 결과가 확인되지 않았습니다 "
                    "— 쇼핑몰 주문내역을 직접 확인해 주세요.",
                )
                continue

            account = self.funding.repository.get_account_by_company(
                reservation.company_id,
            )
            if account is not None:
                self._release_budget(account.id, reservation.amount)
                self._append_ledger(
                    account.id, reservation.company_id, reservation.amount,
                    FundingService.TYPE_HOLD_RELEASE, task.id,
                    "예산 예약 만료 — 자동 반환(구매 재시도 아님)",
                )
            reservation.status = BudgetReservationStatus.EXPIRED
            reservation.released_at = now
            released += 1

        self.db.commit()
        return released

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_task(self, task_id: int, company_id: int) -> PurchaseTask:

        return self._get_task_required(task_id, company_id)

    def assign_channel_connection(
        self, task_id: int, company_id: int, connection_id: int,
        *, triggered_by: int | None = None,
    ) -> PurchaseTask:
        """Gate PT-3(2026-09-08, item 7) — 이 작업을 어느 매입처
        연결(PurchaseChannelConnection)로 처리할지 사람이 명시적으로
        선택했을 때만 호출된다. 이 서비스는 절대 "여러 연결 중 하나를
        대신 골라주지" 않는다 — connection_id는 항상 호출자가 준다.

        선택한 연결이 같은 회사 소유의 활성·검증된 연결인지는
        PurchaseChannelConnectionService.select_connection_for_task()가
        검증한다(비활성·미검증이면 여기서 예외로 차단된다) — 재시도
        시에도 매번 이 검증을 다시 거친다."""

        from app.domains.purchase_task.channel_connection_service import (
            PurchaseChannelConnectionService,
        )

        task = self._get_task_required(task_id, company_id)

        candidate = None
        if task.selected_candidate_id is not None:
            candidate = (
                self.db.query(PurchaseTaskCandidate)
                .filter(
                    PurchaseTaskCandidate.id == task.selected_candidate_id,
                    PurchaseTaskCandidate.company_id == company_id,
                )
                .first()
            )
        expected_mall_code = candidate.shopping_mall_code if candidate else None

        connection_service = PurchaseChannelConnectionService(self.db)
        connection = connection_service.select_connection_for_task(
            connection_id, company_id, expected_mall_code=expected_mall_code,
        )

        task.channel_connection_id = connection.id
        self.db.commit()
        self.db.refresh(task)

        _audit(
            self.db, company_id=company_id, user_id=triggered_by,
            action="purchase_task.channel_connection_assigned",
            entity_id=task.id,
            description=f"connection_id={connection.id}, mall_code={connection.mall_code}",
        )
        return task

    def build_order_submission_review(
        self, task_id: int, company_id: int, *,
        external_product_id: str, options: list[dict],
        recent_auth_token: str | None = None,
        triggered_by: int | None = None,
    ) -> dict:
        """2026-09-09 후속("발주 전 최종 검토 화면") — 쿠팡 수집 주문
        → PurchaseTask → 온채널 연결 계정 → 온채널 상품·옵션 →
        수량·가격·배송비 → 수취정보를 한 화면에 모은다. **읽기
        전용이다** — 이 메서드는 `PurchaseOrderSubmissionService.
        submit_order()`를 절대 호출하지 않는다(grep으로 이 사실을
        재확인할 수 있다). 실제 전송은 여전히 별도 승인·별도 배선이
        필요하다 — 이 화면은 그 직전까지만 보여준다.

        각 부분의 출처를 명시적으로 분리한다:
        - 원본 주문(쿠팡): `Order`/`OrderItem`(source_order_id/
          source_order_item_id로 연결) — 이 도메인이 "정답"으로
          여기는 값(품명·수량)이다.
        - 매입처 계정: `PurchaseTask.channel_connection_id`(이미
          assign_channel_connection()으로 배정된 값).
        - 온채널 상품·옵션·가격: 매번 실제 조회(lookup_product, 캐시
          없음) — 그래서 품절·가격 변경이 그대로 반영된다.
        - 배송비: 온채널 공식 견적 API 자체가 확인되지 않았다
          (docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md) —
          추측 대신 "확인 불가"로 정직하게 남긴다.
        - 수취정보: 기본은 마스킹(app/core/sensitive_data). 원문은
          `/orders/{id}/sensitive-detail`과 동일한 재인증
          (X-Recent-Auth-Token) 없이는 절대 내려가지 않는다."""

        from app.domains.order.service import OrderService
        from app.domains.purchase_task.channel_adapter import (
            PurchaseChannelAdapterError,
        )
        from app.domains.purchase_task.channel_connection_service import (
            PurchaseChannelConnectionService,
        )
        from app.domains.purchase_task.onchannel_client import OnchannelApiError
        from app.core.recent_auth import consume_recent_auth_token
        from app.core.sensitive_data import mask_address
        from app.core.sensitive_data import mask_name
        from app.core.sensitive_data import mask_phone
        from app.core.sensitive_data import mask_zipcode

        task = self._get_task_required(task_id, company_id)

        if task.channel_connection_id is None:
            raise BadRequestException(
                "이 작업에는 아직 매입처 연결 계정이 배정되지 않았습니다 — "
                "먼저 연결 계정을 배정하세요.",
            )

        order_service = OrderService(self.db)
        order = order_service.get_order(task.source_order_id, company_id)
        order_item = None
        if task.source_order_item_id is not None:
            for item in order_service.list_items(order.id, company_id):
                if item.id == task.source_order_item_id:
                    order_item = item
                    break

        connection_service = PurchaseChannelConnectionService(self.db)
        connection = connection_service.get_connection_or_404(
            task.channel_connection_id, company_id,
        )

        blocked_reasons: list[str] = []
        try:
            connection_service.verify_connection_ready_for_order_submission(
                connection.id, company_id,
            )
        except (BadRequestException, ConflictException, NotFoundException) as exc:
            blocked_reasons.append(str(exc))

        product_support = "UNKNOWN"
        product_title = None
        product_options: list = []
        product_detail = "조회하지 않았습니다."
        try:
            lookup = connection_service.lookup_product(
                connection.id, company_id, external_product_id,
                triggered_by=triggered_by,
            )
            product_support = lookup.support
            product_title = lookup.title
            product_options = list(lookup.options)
            product_detail = lookup.detail
        except (OnchannelApiError, PurchaseChannelAdapterError) as exc:
            product_detail = str(exc)
            blocked_reasons.append(f"온채널 상품 조회 실패: {exc}")

        selected_by_id = {opt["id"]: opt["qty"] for opt in options}
        selected_option_details = [
            opt for opt in product_options if opt.option_id in selected_by_id
        ]
        any_out_of_stock = any(
            opt.in_stock is False for opt in selected_option_details
        )
        any_price_unknown = (
            len(selected_option_details) != len(selected_by_id)
            or any(opt.price is None for opt in selected_option_details)
        )
        estimated_item_amount = None
        if not any_price_unknown:
            estimated_item_amount = sum(
                (opt.price * selected_by_id[opt.option_id] for opt in selected_option_details),
                start=Decimal("0"),
            )
        if any_out_of_stock:
            blocked_reasons.append("선택한 옵션 중 품절인 항목이 있습니다.")
        if any_price_unknown:
            blocked_reasons.append("선택한 옵션의 가격을 확인할 수 없습니다.")

        source_quantity = order_item.quantity if order_item is not None else task.quantity
        selected_quantity_total = sum(selected_by_id.values()) if selected_by_id else 0
        quantity_mismatch = (
            source_quantity is not None and selected_quantity_total != source_quantity
        )
        title_mismatch = bool(
            product_title and task.product_title
            and product_title.strip() != task.product_title.strip()
        )
        if quantity_mismatch:
            blocked_reasons.append(
                f"원본 주문 수량({source_quantity})과 선택한 옵션 합계 수량"
                f"({selected_quantity_total})이 다릅니다 — 확인이 필요합니다.",
            )

        unmasked = bool(
            recent_auth_token
            and consume_recent_auth_token(recent_auth_token, triggered_by),
        )
        if unmasked:
            recipient = {
                "unmasked": True, "name": order.receiver_name,
                "phone": order.receiver_phone, "zipcode": order.receiver_zipcode,
                "address": order.receiver_address,
            }
        else:
            recipient = {
                "unmasked": False, "name": mask_name(order.receiver_name),
                "phone": mask_phone(order.receiver_phone),
                "zipcode": mask_zipcode(order.receiver_zipcode),
                "address": mask_address(order.receiver_address),
            }

        # 2026-09-09 후속 — 계약 4항목 미확인 시 항상 차단(발주 준비
        # 판정과 동일한 근거를 이 검토 화면에도 그대로 노출한다).
        from app.domains.purchase_task.constants import (
            is_onchannel_order_contract_fully_confirmed,
            unconfirmed_onchannel_order_contract_items,
        )
        if not is_onchannel_order_contract_fully_confirmed():
            remaining = ", ".join(unconfirmed_onchannel_order_contract_items())
            blocked_reasons.append(f"온채널 공식 발주 계약 미확인 항목: {remaining}")

        # 2026-09-10 후속(Phase 3~4 — 판매신청·포인트 잔액 게이트를
        # 이 검토 화면에도 그대로 반영) — submit_order()가 실제로
        # 적용하는 게이트와 이 화면의 예고가 어긋나면(예: 화면은
        # send_blocked=False인데 실제 호출은 여전히 막히는 경우)
        # 사용자를 오도하게 된다 — 그래서 여기서도 같은 사실을 읽기
        # 전용으로 확인해 보여준다. `ensure_sales_application_submitted()`
        # 처럼 실제 상태를 바꾸는 호출은 절대 하지 않는다(읽기 전용
        # 원칙 유지) — 판매신청은 DB 조회만, 포인트는 온채널 진단
        # 조회(check_member_point, 이미 기존에도 이 화면이 lookup_
        # product로 실제 네트워크 조회를 하고 있어 원칙상 새로운
        # 종류의 호출이 아니다)만 수행한다.
        from app.domains.purchase_task.sales_application_service import (
            PurchaseSalesApplicationService,
        )

        sales_application_service = PurchaseSalesApplicationService(self.db)
        sales_application_confirmed = sales_application_service.is_sales_application_confirmed(
            connection.id, company_id, external_product_id,
        )
        if not sales_application_confirmed:
            blocked_reasons.append(
                "이 상품의 판매신청이 아직 접수 확인되지 않았습니다(발주 전 필수).",
            )

        point_support = "UNKNOWN"
        point_value = None
        point_interpretable = False
        point_detail = "조회하지 않았습니다."
        try:
            point_result = connection_service.check_member_point(
                connection.id, company_id, triggered_by=triggered_by,
            )
            point_support = point_result.support
            point_value = point_result.point
            point_interpretable = point_result.point_interpretable
            point_detail = point_result.detail
            if not point_interpretable:
                blocked_reasons.append("포인트(예치금) 응답을 해석할 수 없습니다.")
            elif estimated_item_amount is not None and point_value < estimated_item_amount:
                blocked_reasons.append(
                    f"현재 포인트 잔액({point_value})이 상품가 소계"
                    f"({estimated_item_amount})보다 적습니다.",
                )
        except (OnchannelApiError, PurchaseChannelAdapterError) as exc:
            point_detail = str(exc)
            blocked_reasons.append(f"포인트(예치금) 조회 실패: {exc}")

        # 2026-09-11 후속(반자동 완료 라운드 Phase 5·7) — 온채널에
        # 배송비 사전 확인 API가 없다는 사실은 여전하지만, 이제는
        # 유효한 사용자 최종 승인(PurchaseOrderApproval, status=
        # ACTIVE, 이 상품가와 일치)이 있으면 Gate D와 동일하게 통과
        # 시킨다 — order_submission_service.py::_verify_point_
        # balance_and_shipping_or_block()와 반드시 같은 판정을
        # 내려야 한다(화면이 "보낼 수 있다"고 하고 실제 호출은
        # 막히는 어긋남을 만들지 않기 위해).
        from app.domains.purchase_task.order_approval_service import (
            PurchaseOrderApprovalService,
        )

        approval_service = PurchaseOrderApprovalService(self.db)
        approval = approval_service.get_active_approval_or_none(
            connection.id, company_id, task.id,
        )
        approval_matches_current_price = (
            approval is not None
            and estimated_item_amount is not None
            and approval.item_amount_snapshot == int(estimated_item_amount)
        )
        if not approval_matches_current_price:
            blocked_reasons.append(
                "배송비 사전 확인 API가 없어, 사용자가 직접 확인한 배송비로 "
                "최종 승인을 받기 전까지 실제 발주는 차단됩니다(배송비 확인 "
                "화면에서 진행하세요).",
            )

        order_approval_summary = None
        if approval is not None:
            order_approval_summary = {
                "status": approval.status,
                "shipping_cost_amount": approval.shipping_cost_amount,
                "shipping_cost_is_free_confirmed": approval.shipping_cost_is_free_confirmed,
                "shipping_cost_source": approval.shipping_cost_source,
                "shipping_cost_basis_memo": approval.shipping_cost_basis_memo,
                "required_points": approval.required_points_snapshot,
                "projected_residual_points": (
                    (approval.current_points_snapshot - approval.required_points_snapshot)
                    if approval.current_points_snapshot is not None
                    and approval.required_points_snapshot is not None
                    else None
                ),
                "margin_amount": approval.margin_amount_snapshot,
                "margin_rate": approval.margin_rate_snapshot,
                "expires_at": approval.expires_at,
                "matches_current_price": approval_matches_current_price,
            }

        _audit(
            self.db, company_id=company_id, user_id=triggered_by,
            action="purchase_task.order_submission_review_viewed",
            entity_id=task.id,
            description=(
                f"connection_id={connection.id}, "
                f"external_product_id={external_product_id}, "
                f"recipient_unmasked={unmasked}"
            ),
        )

        return {
            "task_id": task.id,
            "source_order_id": order.id,
            "source_product_title": task.product_title,
            "source_order_quantity": source_quantity,
            "connection_id": connection.id,
            "connection_mall_code": connection.mall_code,
            "connection_account_label": connection.account_label,
            "product": {
                "support": product_support,
                "external_product_id": external_product_id,
                "title": product_title,
                "options": [
                    {
                        "option_id": opt.option_id, "label": opt.label,
                        "price": opt.price, "in_stock": opt.in_stock,
                    }
                    for opt in product_options
                ],
                "selected_options": [
                    {"id": opt_id, "qty": qty} for opt_id, qty in selected_by_id.items()
                ],
                "estimated_item_amount": estimated_item_amount,
                "any_selected_option_out_of_stock": any_out_of_stock,
                "any_selected_option_price_unknown": any_price_unknown,
                "detail": product_detail,
            },
            "recipient": recipient,
            "sales_application": {
                "confirmed": sales_application_confirmed,
                "detail": (
                    "판매신청 접수가 확인됐습니다(승인 여부는 별도로 "
                    "조회할 방법이 없습니다)." if sales_application_confirmed
                    else "판매신청 접수 기록이 없습니다 — 발주 전 필수입니다."
                ),
            },
            "point_balance": {
                "support": point_support,
                "point": point_value,
                "point_interpretable": point_interpretable,
                "detail": point_detail,
            },
            "shipping_fee_known": approval_matches_current_price,
            "shipping_fee_detail": (
                "사용자가 확인한 배송비로 최종 승인됨(아래 order_approval 참고)."
                if approval_matches_current_price else
                "온채널 공식 배송비 견적 방법이 아직 확인되지 않았습니다 — "
                "추정하지 않습니다. 배송비 확인 화면에서 사용자가 직접 확인한 "
                "값을 증거와 함께 입력해야 실제 발주가 열립니다."
            ),
            "order_approval": order_approval_summary,
            "product_title_mismatch_warning": title_mismatch,
            "quantity_mismatch_warning": quantity_mismatch,
            "send_blocked": len(blocked_reasons) > 0,
            "blocked_reasons": blocked_reasons,
            "checked_at": datetime.utcnow(),
        }

    def list_tasks(
        self, company_id: int, *, status: str | None = None,
        source_order_id: int | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[PurchaseTask]:

        if source_order_id is not None:
            tasks = self.repository.list_by_source_order(
                company_id, source_order_id,
            )
            if status is not None:
                tasks = [t for t in tasks if t.status == status]
            return tasks

        return self.repository.list_tasks(
            company_id, status=status, skip=skip, limit=limit,
        )

    def reconcile_orders(
        self, company_id: int, *, limit: int = 200,
        triggered_by: int | None = None,
    ) -> dict:
        """Gate PT-2B — 수동 재조정(백필) 진입점. 자동 생성 경로와
        동일한 로직을 재사용한다(Router가 로직을 복제하지 않는다)."""

        from app.domains.purchase_task.order_sync_service import (
            PurchaseTaskOrderSyncService,
        )
        return PurchaseTaskOrderSyncService(self.db).reconcile_recent_orders(
            company_id, limit=limit, triggered_by=triggered_by,
        )

    def check_deadlines_approaching(
        self, company_id: int, *, within_hours: int = 24,
        now: datetime | None = None,
    ) -> list[int]:
        """Gate PT-2E — DEADLINE_APPROACHING 이메일. 이 저장소에는
        배경 스케줄러가 없으므로(email_service.py와 동일 전제)
        reconcile_orders()처럼 명시적으로 호출해야 하는 읽기 시점
        재확인이다. _notify()의 기존 idempotency_key(`pt:{task_id}:
        {event_type}:{user_id}`, 날짜 미포함)를 그대로 재사용하므로
        같은 작업에 대해 이 이벤트는 사람이 이미 확인하기 전까지
        평생 정확히 한 번만 발송된다(반복 호출해도 매일 다시
        발송되지 않는다 — 다른 12개 기존 이벤트와 동일한 "1회성"
        전제, 재발송이 필요하면 향후 별도 정책으로 확장 가능)."""

        now = now or datetime.utcnow()
        tasks = self.repository.list_tasks_with_deadline_approaching(
            company_id, now=now, within_hours=within_hours,
        )

        notified = []
        for task in tasks:
            self._notify(
                task, EmailNotificationEventType.DEADLINE_APPROACHING,
                f"구매 기한이 다가옵니다: {task.purchase_deadline}",
            )
            notified.append(task.id)

        return notified


__all__ = ["PurchaseTaskService"]
