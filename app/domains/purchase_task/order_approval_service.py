"""
=========================================================
Homez OS

File : app/domains/purchase_task/order_approval_service.py

2026-09-11 후속(반자동 완료 라운드, Phase 5·7) — 실제 온채널 발주
직전 "사용자 최종 승인" 1건을 관리한다. 온채널에 배송비를 사전
확정할 공식 API가 없다는 사실이 확정됐으므로(docs/HOMEZ_ONCHANNEL_
OPENAPI_FINDINGS_20260908.md "정정(2026-09-11)" 절), 사용자가 외부
화면에서 직접 확인한 배송비를 근거와 함께 입력하는 반자동 보완책 +
가격/포인트/한도/마진 최종 재확인을 이 서비스가 담당한다.

**이 서비스는 배송비를 추정하지 않는다** — 사용자가 입력한 값을
증거(출처·메모·확인자·확인시각)와 함께 기록할 뿐이다. 이 서비스가
만드는 승인(PurchaseOrderApproval, status=ACTIVE)만이 order_
submission_service.py의 Gate D를 통과시킨다 — 그 외에는 여전히
"배송비 미확인"으로 차단된다.

핵심 원칙(사용자 지시 원문):
1. 배송비 0원도 사용자가 "무료배송 확인"을 명시해야 인정한다 —
   입력을 안 한 것과 0원을 확인한 것은 다른 사실이다.
2. 원 단위 정수만 허용한다 — 음수·bool·float·비정상 문자열은 거부.
3. 배송비가 바뀌면(재확인이든 재조회든) 이전 승인은 즉시 무효화.
4. 승인 유효시간(기본 10분)이 지나면 만료 — 그 시점에 자동으로
   가격·포인트를 다시 조회하지 않는다(호출자가 다시 finalize해야
   한다 — 자동 재승인은 자동 재시도와 같은 위험이다).
5. 자동 모드는 이 승인을 절대 생성하지 않는다 — confirmed_by가
   항상 실제 사용자 ID여야 하므로, 사람의 명시적 행동 없이는 이
   테이블에 행 자체가 생기지 않는다(구조적 방지).
=========================================================
"""

from __future__ import annotations

import functools
import json
import threading
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from sqlalchemy import exists

from app.domains.purchase_task.constants import (
    OrderSubmissionStatus,
    PurchaseOrderApprovalStatus,
    UnknownResolutionStatus,
    RECOMMENDED_APPROVAL_VALIDITY_MINUTES,
    RECOMMENDED_DAILY_PURCHASE_LIMIT_AMOUNT,
    RECOMMENDED_MIN_MARGIN_RATE,
    RECOMMENDED_MIN_NET_PROFIT,
    RECOMMENDED_MIN_RESIDUAL_POINTS,
    RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT,
    RECOMMENDED_PER_ORDER_MAX_AMOUNT,
    ShippingCostConfirmationSource,
)
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt

# 2026-09-12 후속(V7 기준선 정리, Phase 4 잔여 격차 해소) — HOMEZ는
# 단일 프로세스 Modular Monolith다(여러 워커 프로세스로 분리되지
# 않음). `finalize_approval()`이 일간·월간 한도를 "이미 CONSUMED된
# 합계"로 재확인하지만, 그 합계 조회와 이후 결정 사이에 잠금이
# 없었다 — 같은 프로세스 안에서 두 요청(예: 같은 사용자의 두 탭)이
# 동시에 이 메서드에 들어오면 둘 다 서로의 아직 커밋되지 않은
# 변경을 못 본 채 통과할 수 있는 경쟁조건이 이론적으로 존재했다.
# 이 락은 그 경쟁조건을 프로세스 내에서 완전히 닫는다(같은
# 프로세스 안에서는 finalize_approval·mark_consumed가 서로 겹쳐
# 실행되지 않음을 보장) — 다만 finalize_approval()의 승인과 실제
# 온채널 발주(별도 외부 API 호출) 사이의 간격은 이 락의 범위 밖이며,
# 그 간격은 승인 유효시간(기본 10분) 만료로 별도 처리된다.
_finalize_approval_lock = threading.Lock()


def _synchronized(lock: threading.Lock):

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with lock:
                return func(*args, **kwargs)
        return wrapper
    return decorator


class PurchaseOrderApprovalService:

    def __init__(self, db: Session):

        self.db = db

    # ---------------- 조회(부작용 없음) ----------------

    def get_approval(
        self, connection_id: int, company_id: int, purchase_task_id: int,
    ) -> PurchaseOrderApproval | None:

        return (
            self.db.query(PurchaseOrderApproval)
            .filter(
                PurchaseOrderApproval.company_id == company_id,
                PurchaseOrderApproval.connection_id == connection_id,
                PurchaseOrderApproval.purchase_task_id == purchase_task_id,
            )
            .first()
        )

    def get_active_approval_or_none(
        self, connection_id: int, company_id: int, purchase_task_id: int,
    ) -> PurchaseOrderApproval | None:
        """만료됐으면 즉시 EXPIRED로 갱신하고 None을 반환한다 —
        "조회했을 뿐인데 만료된 승인이 계속 ACTIVE로 보이는" 상황을
        만들지 않는다."""

        approval = self.get_approval(connection_id, company_id, purchase_task_id)
        if approval is None:
            return None
        if approval.status != PurchaseOrderApprovalStatus.ACTIVE:
            return None
        if approval.expires_at is not None and datetime.utcnow() >= approval.expires_at:
            approval.status = PurchaseOrderApprovalStatus.EXPIRED
            approval.invalidated_reason = "승인 유효시간(10분) 경과"
            self.db.commit()
            self.db.refresh(approval)
            return None
        return approval

    # ---------------- Phase 5: 배송비 수동 확인 ----------------

    def confirm_shipping_cost(
        self, connection_id: int, company_id: int, purchase_task_id: int,
        product_code: str, *, shipping_cost_amount: int,
        is_free_shipping_confirmed: bool = False,
        source: str, basis_memo: str | None = None, confirmed_by: int,
    ) -> PurchaseOrderApproval:
        """사용자가 외부 화면에서 확인한 배송비를 증거와 함께
        기록한다. 이 호출만으로는 발주가 열리지 않는다 — 반드시
        `finalize_approval()`로 가격·포인트·한도·마진을 다시 확인해
        ACTIVE로 전환해야 한다(이 메서드는 상태를 PENDING_SHIPPING_
        COST로만 둔다)."""

        self._validate_shipping_cost_input(
            shipping_cost_amount, is_free_shipping_confirmed,
        )
        if source not in ShippingCostConfirmationSource.ALL:
            raise BadRequestException(f"알 수 없는 배송비 확인 출처입니다: {source}")
        if confirmed_by is None:
            raise BadRequestException(
                "배송비 확인은 실제 사용자 ID 없이는 기록할 수 없습니다 — "
                "자동 모드는 이 경로를 쓸 수 없습니다.",
            )

        approval = self.get_approval(connection_id, company_id, purchase_task_id)
        if (
            approval is not None
            and approval.status == PurchaseOrderApprovalStatus.CONSUMED
        ):
            raise ConflictException(
                "이미 실제 발주에 사용된 승인은 배송비를 다시 입력해 변경할 수 "
                "없습니다. 기존 발주 결과를 먼저 확인하세요.",
            )
        shipping_changed = (
            approval is not None
            and (
                approval.shipping_cost_amount != shipping_cost_amount
                or approval.shipping_cost_is_free_confirmed != is_free_shipping_confirmed
            )
        )

        if approval is None:
            approval = PurchaseOrderApproval(
                company_id=company_id, connection_id=connection_id,
                purchase_task_id=purchase_task_id, product_code=product_code,
                status=PurchaseOrderApprovalStatus.PENDING_SHIPPING_COST,
                shipping_cost_is_free_confirmed=False,
            )
            self.db.add(approval)

        # 배송비가 바뀌면(최초 입력이 아닌 재입력) 기존 ACTIVE 승인은
        # 즉시 무효화한다 — 같은 행을 갱신하되, 승인 관련 필드는
        # 전부 지운다(이전 승인의 흔적이 새 배송비에 잘못 이어지지
        # 않게).
        if shipping_changed and approval.status == PurchaseOrderApprovalStatus.ACTIVE:
            approval.invalidated_reason = "배송비 재입력으로 이전 승인 무효화"
        approval.status = PurchaseOrderApprovalStatus.PENDING_SHIPPING_COST
        approval.shipping_cost_amount = shipping_cost_amount
        approval.shipping_cost_is_free_confirmed = is_free_shipping_confirmed
        approval.shipping_cost_source = source
        approval.shipping_cost_basis_memo = basis_memo
        approval.shipping_cost_confirmed_by = confirmed_by
        approval.shipping_cost_confirmed_at = datetime.utcnow()
        approval.approved_by = None
        approval.approved_at = None
        approval.expires_at = None

        self.db.commit()
        self.db.refresh(approval)
        return approval

    @staticmethod
    def _validate_shipping_cost_input(
        shipping_cost_amount, is_free_shipping_confirmed: bool,
    ) -> None:

        # bool은 int의 서브클래스라 isinstance(True, int)가 True다 —
        # True/False가 1/0 배송비로 오인되지 않게 먼저 걸러낸다.
        if isinstance(shipping_cost_amount, bool) or not isinstance(
            shipping_cost_amount, int,
        ):
            raise BadRequestException(
                "배송비는 원 단위 정수만 허용합니다(실수·문자열·불리언 불가).",
            )
        if shipping_cost_amount < 0:
            raise BadRequestException("배송비는 음수일 수 없습니다.")
        if shipping_cost_amount == 0 and not is_free_shipping_confirmed:
            raise BadRequestException(
                "배송비를 0원으로 기록하려면 \"무료배송 확인\"을 명시적으로 "
                "체크해야 합니다 — 미입력과 0원 확인은 다른 사실입니다.",
            )

    @staticmethod
    def _normalize_options(options: list[dict] | None) -> list[dict] | None:
        """2026-09-15 후속(전면 감사 Phase 2) — 옵션 목록을 id 기준
        정렬한 {id, qty} 리스트로 정규화한다. "옵션을 고르는 순서만
        다른 같은 요청"과 "실제 옵션 구성이 바뀐 요청"을 구분하기
        위한 유일한 목적이다 — 옵션 자체에는 개인정보가 없다."""

        if options is None:
            return None
        normalized = sorted(
            (
                {"id": str(opt.get("id")), "qty": int(opt.get("qty") or 0)}
                for opt in options
            ),
            key=lambda o: o["id"],
        )
        return normalized

    # ---------------- Phase 7: 최종 승인 ----------------

    @_synchronized(_finalize_approval_lock)
    def finalize_approval(
        self, connection_id: int, company_id: int, purchase_task_id: int,
        *, item_amount: int, current_points: int, triggered_by: int,
        options: list[dict] | None = None,
    ) -> PurchaseOrderApproval:
        """배송비 확인이 끝난 승인 행에 대해, 호출자가 이미 실측한
        최신 상품가(`item_amount`)·포인트 잔액(`current_points`)을
        받아 최종 한도·마진 재확인을 수행한다. 이 메서드 자신은
        온채널에 어떤 네트워크 호출도 하지 않는다(호출자가 이미
        Gate D의 실측 단계에서 받은 값을 그대로 넘긴다 — 같은 값을
        두 번 조회하지 않는다).

        `options`(선택, [{id, qty}, ...])를 넘기면 승인 시점 옵션
        구성을 정규화해 스냅샷으로 저장한다 — 실행 직전
        `revalidate_before_submission()`이 이 스냅샷과 실제 제출
        시점 옵션을 대조해 "승인된 것과 다른 옵션으로 바꿔치기"를
        차단한다(2026-09-15 전면 감사 Phase 2, 승인-실행 결합).
        넘기지 않으면(호출부가 아직 옵션을 모르는 경우) 옵션
        재검증은 생략된다 — 이 사실을 추측으로 메우지 않는다."""

        approval = self.get_approval(connection_id, company_id, purchase_task_id)
        if approval is None or approval.shipping_cost_amount is None:
            raise ConflictException(
                "배송비를 먼저 확인해야 최종 승인을 진행할 수 있습니다.",
            )
        if approval.status in (
            PurchaseOrderApprovalStatus.ACTIVE,
            PurchaseOrderApprovalStatus.CONSUMED,
        ):
            raise ConflictException(
                "이미 활성화됐거나 실제 발주에 사용된 승인은 다시 최종 승인할 "
                "수 없습니다. 조건을 바꾸려면 새 배송비 확인 절차부터 시작하세요.",
            )

        from app.domains.purchase_task.model import PurchaseTask
        from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService

        task = (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.id == purchase_task_id,
                PurchaseTask.company_id == company_id,
            )
            .first()
        )
        if task is None:
            raise NotFoundException("매입 작업을 찾을 수 없습니다.")

        required_points = item_amount + approval.shipping_cost_amount

        blocked_reasons: list[str] = []

        policy = PurchaseTaskPolicyService(self.db)
        setting = policy.get_or_create_default_settings(company_id)

        per_order_max = (
            setting.per_order_max_amount
            if setting.per_order_max_amount is not None
            else RECOMMENDED_PER_ORDER_MAX_AMOUNT
        )
        if required_points > per_order_max:
            blocked_reasons.append(
                f"건당 발주 한도({int(per_order_max)}원) 초과 — 필요 금액 "
                f"{required_points}원.",
            )

        daily_limit = (
            setting.daily_purchase_limit_amount
            if setting.daily_purchase_limit_amount is not None
            else RECOMMENDED_DAILY_PURCHASE_LIMIT_AMOUNT
        )
        spent_today = self._sum_consumed_amount_today(connection_id, company_id)
        if spent_today + required_points > daily_limit:
            blocked_reasons.append(
                f"하루 발주 한도({int(daily_limit)}원) 초과 — 오늘 이미 "
                f"{spent_today}원 발주, 이번 건 {required_points}원 추가 시 초과.",
            )

        # 2026-09-12 후속(V7 기준선 정리, Phase 4) — per_order_max·
        # daily_limit과 같은 이유로 여기서도 재확인해야 하는데 빠져
        # 있었다. PurchaseTask 생성 시점의 PurchaseTaskPolicyService.
        # evaluate()가 monthly_purchase_budget_amount를 한 번 확인하지만,
        # 그 시점과 실제 발주(되돌릴 수 없는 금전 행동) 시점 사이에
        # 이미 다른 작업들이 소비했을 수 있으므로 이 최종 게이트에서
        # 다시 확인하지 않으면 월간 한도가 실질적으로 강제되지 않는다.
        monthly_limit = (
            setting.monthly_purchase_budget_amount
            if setting.monthly_purchase_budget_amount is not None
            else RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT
        )
        spent_this_month = self._sum_consumed_amount_this_month(company_id)
        if spent_this_month + required_points > monthly_limit:
            blocked_reasons.append(
                f"월간 발주 한도({int(monthly_limit)}원) 초과 — 이번 달 이미 "
                f"{spent_this_month}원 발주, 이번 건 {required_points}원 추가 시 초과.",
            )

        min_residual = (
            setting.min_residual_points
            if setting.min_residual_points is not None
            else RECOMMENDED_MIN_RESIDUAL_POINTS
        )
        projected_residual = current_points - required_points
        if projected_residual < min_residual:
            blocked_reasons.append(
                f"발주 후 예상 잔여 포인트({projected_residual})가 최소 잔여 "
                f"기준({min_residual}) 미달.",
            )

        # 2026-09-12 후속(V7 기준선 정리, Phase 4 잔여 격차 해소) —
        # policy_service.evaluate()(작업 생성 시점 평가)는 동시 진행
        # 작업 수를 확인하는데, 이 최종 게이트는 재확인하지 않았다.
        # 작업 생성 이후 다른 작업들이 이미 동시 한도를 소진했을 수
        # 있으므로 여기서도 재확인한다.
        #
        # 펀딩(운영자금) 잔액 재확인은 **이번 라운드에서 의도적으로
        # 보류한다** — 실 homez.db를 읽기전용으로 확인한 결과
        # `funding_accounts` 테이블이 아직 0행이다(회사 1도 계좌가
        # 없음). policy_service.evaluate()가 이미 같은 이유로 계좌가
        # 없으면 BUDGET_INSUFFICIENT로 차단하지만, 그건 작업 "생성"을
        # 막을 뿐이다. 여기(발주 직전 최종 게이트)에 같은 검사를
        # 그대로 추가하면 이미 생성돼 배송비 확인 단계까지 진행된
        # 모든 실제 작업의 최종 승인이 오늘부터 전부 막히는 운영상
        # 결과가 생긴다 — 이건 코드 결함 수정이 아니라 사업 운영
        # 방침(펀딩 계좌를 실제로 언제 만들지) 결정이 필요한
        # 사안이므로, 사실만 기록하고 사용자 판단을 기다린다.
        if setting.max_concurrent_tasks is not None:
            from app.domains.purchase_task.repository import PurchaseTaskRepository

            open_count = PurchaseTaskRepository(self.db).count_open_tasks(company_id)
            if open_count > setting.max_concurrent_tasks:
                blocked_reasons.append(
                    f"동시 진행 작업 한도({setting.max_concurrent_tasks}건) 초과 "
                    f"— 현재 진행 중 {open_count}건.",
                )

        margin_amount = None
        margin_rate = None
        if task.coupang_sale_amount is not None and task.coupang_fee_amount is not None:
            from app.domains.purchase_task.margin_calculator import (
                CandidateCostInput, calculate_margin,
            )

            cost_input = CandidateCostInput(
                candidate_id=0,
                estimated_price=Decimal(str(item_amount)),
                estimated_shipping_fee=Decimal(str(approval.shipping_cost_amount)),
            )
            result = calculate_margin(
                cost_input,
                coupang_sale_amount=Decimal(str(task.coupang_sale_amount)),
                coupang_fee_amount=Decimal(str(task.coupang_fee_amount)),
            )
            margin_amount = int(result.expected_net_profit)
            margin_rate = float(result.expected_margin_rate) / 100

            min_margin_rate = (
                setting.min_margin_rate
                if setting.min_margin_rate is not None else RECOMMENDED_MIN_MARGIN_RATE
            )
            min_net_profit = (
                setting.min_net_profit
                if setting.min_net_profit is not None else RECOMMENDED_MIN_NET_PROFIT
            )
            if margin_rate < min_margin_rate:
                blocked_reasons.append(
                    f"예상 마진율({margin_rate * 100:.1f}%)이 최소 기준"
                    f"({min_margin_rate * 100:.1f}%) 미달.",
                )
            if margin_amount < min_net_profit:
                blocked_reasons.append(
                    f"예상 순이익금({margin_amount}원)이 최소 기준"
                    f"({int(min_net_profit)}원) 미달.",
                )
        else:
            blocked_reasons.append(
                "원 쿠팡 주문의 판매금액·수수료 정보가 없어 마진을 계산할 "
                "수 없습니다 — 0으로 추정하지 않고 차단합니다.",
            )

        approval.item_amount_snapshot = item_amount
        approval.required_points_snapshot = required_points
        approval.current_points_snapshot = current_points
        approval.margin_amount_snapshot = margin_amount
        approval.margin_rate_snapshot = margin_rate
        normalized_options = self._normalize_options(options)
        if normalized_options is not None:
            approval.options_snapshot_json = json.dumps(
                normalized_options, ensure_ascii=False,
            )

        if blocked_reasons:
            approval.status = PurchaseOrderApprovalStatus.PENDING_SHIPPING_COST
            approval.invalidated_reason = " / ".join(blocked_reasons)
            self.db.commit()
            self.db.refresh(approval)
            raise ConflictException(
                f"최종 승인 조건을 만족하지 못했습니다: {approval.invalidated_reason}",
            )

        validity_minutes = (
            setting.order_approval_validity_minutes
            if setting.order_approval_validity_minutes is not None
            else RECOMMENDED_APPROVAL_VALIDITY_MINUTES
        )
        approval.status = PurchaseOrderApprovalStatus.ACTIVE
        approval.approved_by = triggered_by
        approval.approved_at = datetime.utcnow()
        approval.expires_at = approval.approved_at + timedelta(minutes=validity_minutes)
        approval.invalidated_reason = None
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def _sum_reserved_amount(
        self, company_id: int, since: datetime, *,
        exclude_approval_id: int | None = None,
    ) -> int:
        """2026-09-14 전면 감사 후속(Phase 5.1 발견·재현된 결함 수정) —
        기존에는 `CONSUMED`(실제로 발주까지 이어진 건)만 합산했다.
        그러나 아직 `CONSUMED`가 아닌 `ACTIVE` 승인(사람이 이미
        최종 승인해 10분 유효시간 안에 있는, 곧 발주로 이어질 금액)은
        전혀 반영되지 않아, 여러 작업을 짧은 시간 안에 각각 승인하면
        (각 호출이 서로의 아직 `CONSUMED` 안 된 `ACTIVE` 금액을 못 보므로)
        한도를 실제로 넘겨도 전부 통과하는 결함이 있었다 — 임시 DB
        재현 시험으로 실제 확인됨(일간 한도 15만원 설정, 8만원짜리
        3건이 전부 ACTIVE로 통과, 합계 24만원). `CONSUMED` +
        "아직 만료되지 않은 ACTIVE"를 함께 합산해 이 결함을 닫는다."""

        # 2026-09-20 — 결과가 아직 확정되지 않은 발주(PENDING/IN_FLIGHT,
        # 또는 사람이 아직 확정하지 않은 RESULT_UNKNOWN)의 금액도 승인
        # 유효시간(10분)이 지난 뒤에도 한도에서 빠지지 않아야 한다.
        # 외부 성공 직후 내부 저장이 실패했거나 UNKNOWN이면 승인은
        # CONSUMED로 넘어가지 못하고 만료되지만, 실제 주문은 이미
        # 생겼을 수 있다. 같은 작업의 재발주는 _has_blocking_task_
        # attempt가 막고, 다른 작업의 승인은 이 집계가 막는다. 사람이
        # ORDER_NOT_CONFIRMED로 확정하면 빠지고, ORDER_CONFIRMED면
        # 승인이 CONSUMED로 바뀌어 첫 번째 조건으로 계속 집계된다.
        # 같은 행이 한 번만 집계되도록 하나의 OR 조건으로 묶는다.
        unresolved_attempt = exists().where(
            PurchaseOrderSubmissionAttempt.company_id == PurchaseOrderApproval.company_id,
            PurchaseOrderSubmissionAttempt.connection_id == PurchaseOrderApproval.connection_id,
            PurchaseOrderSubmissionAttempt.purchase_task_id == PurchaseOrderApproval.purchase_task_id,
            (
                PurchaseOrderSubmissionAttempt.status.in_(
                    (OrderSubmissionStatus.PENDING, OrderSubmissionStatus.IN_FLIGHT),
                )
                | (
                    (PurchaseOrderSubmissionAttempt.status == OrderSubmissionStatus.RESULT_UNKNOWN)
                    & PurchaseOrderSubmissionAttempt.unknown_resolution_status.notin_(
                        (
                            UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
                            UnknownResolutionStatus.ORDER_CONFIRMED,
                        ),
                    )
                )
            ),
        )
        query = self.db.query(PurchaseOrderApproval).filter(
                PurchaseOrderApproval.company_id == company_id,
                PurchaseOrderApproval.updated_at >= since,
                (
                    (PurchaseOrderApproval.status == PurchaseOrderApprovalStatus.CONSUMED)
                    | (
                        (PurchaseOrderApproval.status == PurchaseOrderApprovalStatus.ACTIVE)
                        & (PurchaseOrderApproval.expires_at.is_not(None))
                        & (PurchaseOrderApproval.expires_at > datetime.utcnow())
                    )
                    | unresolved_attempt
                )
            )
        if exclude_approval_id is not None:
            query = query.filter(PurchaseOrderApproval.id != exclude_approval_id)
        rows = query.all()
        return sum(
            (row.item_amount_snapshot or 0) + (row.shipping_cost_amount or 0)
            for row in rows
        )

    def _sum_consumed_amount_today(self, connection_id: int, company_id: int) -> int:
        """오늘 이미 실제로 소비 확정(CONSUMED)됐거나, 아직 만료되지
        않은 채 승인(ACTIVE)돼 곧 소비될 예정인 금액의 합계 — 하루
        한도 판정 기준. 발주 시도 장부(PurchaseOrderSubmissionAttempt)
        는 금액을 저장하지 않으므로(개인정보·금액 모두 최소화 설계,
        Gate PT-3) 이 승인 테이블이 금액의 유일한 출처다."""

        since = datetime.utcnow() - timedelta(hours=24)
        return self._sum_reserved_amount(company_id, since)

    def _sum_consumed_amount_this_month(self, company_id: int) -> int:
        """2026-09-12 후속(Phase 4 자동결제 한도 실행경로 감사) —
        `_sum_consumed_amount_today()`와 같은 방식으로, 최근 30일간의
        합계를 월간 한도 판정 기준으로 쓴다(policy_service.py::
        evaluate()가 후보 평가 시점에 쓰는 것과 같은 30일 창을
        맞췄다). 2026-09-14 후속으로 `_sum_reserved_amount()`를
        공유해 CONSUMED + 미만료 ACTIVE를 함께 합산한다(위 docstring
        참고)."""

        since = datetime.utcnow() - timedelta(days=30)
        return self._sum_reserved_amount(company_id, since)

    # ---------------- 실제 발주 직전 재대조 ----------------

    def revalidate_before_submission(
        self, connection_id: int, company_id: int, purchase_task_id: int,
        *, current_product_code: str, current_item_amount: int, current_points: int,
        current_shipping_cost_hint: int | None,
        current_options: list[dict] | None = None,
    ) -> PurchaseOrderApproval:
        """실제 온채널 발주 호출 바로 직전에 승인이 여전히 유효한지
        마지막으로 대조한다 — 승인 이후 가격이 바뀌었거나 만료됐으면
        여기서 막는다(화면에 남아있는 낡은 승인으로 발주가 나가지
        않게 하는 마지막 방어선)."""

        approval = self.get_active_approval_or_none(
            connection_id, company_id, purchase_task_id,
        )
        if approval is None:
            raise ConflictException(
                "유효한 발주 승인이 없습니다(만료됐거나 아직 승인되지 "
                "않았습니다) — 배송비 확인부터 다시 진행하세요.",
            )
        if approval.product_code != current_product_code:
            raise ConflictException(
                "승인된 상품과 실제 발주 상품이 다릅니다 — 기존 승인을 다른 "
                "상품에 사용할 수 없습니다.",
            )
        if approval.options_snapshot_json and current_options is not None:
            approved_options = json.loads(approval.options_snapshot_json)
            submitted_options = self._normalize_options(current_options)
            if approved_options != submitted_options:
                approval.status = PurchaseOrderApprovalStatus.INVALIDATED_PRICE_CHANGE
                approval.invalidated_reason = (
                    "승인 시점 옵션 구성과 실제 발주 옵션이 다릅니다 — 승인된 "
                    "옵션 외에는 이 승인을 사용할 수 없습니다."
                )
                self.db.commit()
                raise ConflictException(approval.invalidated_reason)
        if approval.item_amount_snapshot != current_item_amount:
            approval.status = PurchaseOrderApprovalStatus.INVALIDATED_PRICE_CHANGE
            approval.invalidated_reason = (
                f"발주 직전 재조회 가격({current_item_amount})이 승인 시점"
                f"({approval.item_amount_snapshot})과 다릅니다."
            )
            self.db.commit()
            raise ConflictException(approval.invalidated_reason)
        if (
            current_shipping_cost_hint is not None
            and current_shipping_cost_hint != approval.shipping_cost_amount
        ):
            approval.status = PurchaseOrderApprovalStatus.INVALIDATED_SHIPPING_CHANGE
            approval.invalidated_reason = (
                "발주 직전 재확인한 배송비가 승인 시점과 다릅니다."
            )
            self.db.commit()
            raise ConflictException(approval.invalidated_reason)

        from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService

        setting = PurchaseTaskPolicyService(self.db).get_or_create_default_settings(
            company_id,
        )
        required_points = current_item_amount + (approval.shipping_cost_amount or 0)
        if current_points < required_points:
            raise ConflictException(
                f"배송비 포함 최종 필요 포인트({required_points})가 현재 잔액"
                f"({current_points})보다 많습니다 — 발주를 시도하지 않습니다.",
            )
        per_order_max = (
            setting.per_order_max_amount
            if setting.per_order_max_amount is not None
            else RECOMMENDED_PER_ORDER_MAX_AMOUNT
        )
        daily_limit = (
            setting.daily_purchase_limit_amount
            if setting.daily_purchase_limit_amount is not None
            else RECOMMENDED_DAILY_PURCHASE_LIMIT_AMOUNT
        )
        monthly_limit = (
            setting.monthly_purchase_budget_amount
            if setting.monthly_purchase_budget_amount is not None
            else RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT
        )
        min_residual = (
            setting.min_residual_points
            if setting.min_residual_points is not None
            else RECOMMENDED_MIN_RESIDUAL_POINTS
        )
        reserved_today = self._sum_reserved_amount(
            company_id, datetime.utcnow() - timedelta(hours=24),
            exclude_approval_id=approval.id,
        )
        reserved_month = self._sum_reserved_amount(
            company_id, datetime.utcnow() - timedelta(days=30),
            exclude_approval_id=approval.id,
        )
        blocked = []
        if required_points > per_order_max:
            blocked.append("건당 발주 한도 초과")
        if reserved_today + required_points > daily_limit:
            blocked.append("하루 발주 한도 초과")
        if reserved_month + required_points > monthly_limit:
            blocked.append("월간 발주 한도 초과")
        if current_points - required_points < min_residual:
            blocked.append("발주 후 최소 잔여 포인트 미달")
        if blocked:
            raise ConflictException(
                "발주 직전 재검증에서 조건이 변경되었습니다: " + ", ".join(blocked),
            )
        return approval

    @_synchronized(_finalize_approval_lock)
    def mark_consumed(self, approval: PurchaseOrderApproval) -> None:

        approval.status = PurchaseOrderApprovalStatus.CONSUMED
        self.db.commit()


__all__ = ["PurchaseOrderApprovalService"]
