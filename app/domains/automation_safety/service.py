"""
=========================================================
Homez OS

File : app/domains/automation_safety/service.py

V2.4 Commerce Safety Layer — 실행 허용 여부 정책 판단

이 서비스는 실제 주문·구매·Funding 변경을 수행하지 않는다. ALLOW /
REQUIRE_APPROVAL / DENY 판단만 반환하며, 실행 여부와 실제 실행은
호출자(V3 이후 Discovery/Execution 계층)의 책임이다.

Hardening(감사 대응):
  - Emergency Stop은 idempotency 재조회보다 먼저 검사한다(비상 정지
    중에는 과거에 이미 처리된 요청의 재조회조차 ALLOW로 이어지지 않게
    하기 위함 — 사고 대응 중 애매한 경로를 남기지 않는다).
  - 기존에 처리된 idempotency_key 재요청은 ALLOW로 반환하지 않고
    명시적으로 DENY(DUPLICATE_REQUEST)한다 — 호출자가 "다시 ALLOW가
    왔으니 다시 실행해도 된다"고 오판할 여지를 없앤다.
  - 한도 조회·판단·소비 기록을 하나의 Transaction으로 묶고, 한도 소비
    자체는 단일 조건부 UPDATE(ExecutionPeriodUsage)로 원자화한다.
  - idempotency_key UNIQUE 제약 위반(IntegrityError)은 동시 요청 경쟁으로
    간주해 rollback 후 DUPLICATE_REQUEST로 처리한다.
=========================================================
"""

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.constants import SafetyDecision
from app.domains.automation_safety.constants import SafetyReason
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.automation_safety.repository import AutomationSafetyRepository

# 한국 표준시(KST)는 서머타임이 없는 연중 고정 UTC+9. Windows 환경에
# tzdata 패키지가 없어 zoneinfo.ZoneInfo("Asia/Seoul")가 동작하지 않을 수
# 있어(패키지 설치 금지) 고정 offset을 사용한다.
KST = timezone(timedelta(hours=9), name="Asia/Seoul")


class SafetyService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = AutomationSafetyRepository(db)

    # --------------------------------------------------
    # Automation Mode
    # --------------------------------------------------

    def get_current_mode(
        self,
    ) -> str:
        """설정된 적이 없으면 안전한 기본값(RECOMMEND_ONLY)을 반환한다."""

        state = self.repository.get_current_mode_state()

        if state is None:
            return AutomationMode.DEFAULT

        return state.mode

    def set_mode(
        self,
        mode: str,
        set_by: int,
        is_admin: bool,
        reason: str | None = None,
    ) -> AutomationModeState:

        if not is_admin:
            raise ForbiddenException(
                "자동화 모드 변경은 관리자만 가능합니다.",
            )

        if mode not in AutomationMode.ALL:
            raise BadRequestException(
                f"알 수 없는 AutomationMode: {mode}",
            )

        state = AutomationModeState(
            mode=mode,
            set_by=set_by,
            reason=reason,
        )

        try:
            state = self.repository.add_mode_state_no_commit(state)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return state

    # --------------------------------------------------
    # Function Automation State (Phase 3, 회사×기능별)
    # --------------------------------------------------

    def get_function_mode(
        self,
        company_id: int,
        function_code: str,
    ) -> str:
        """설정된 적이 없으면 안전한 기본값(MANUAL)을 반환한다 —
        "자동 모드라는 이유만으로 결제·발주·환불 권한이 확대되지
        않는다"는 원칙을 이 기본값 하나로 지킨다(기능별 예외 없음)."""

        state = self.repository.get_current_function_mode_state(
            company_id, function_code,
        )

        if state is None:
            return FunctionMode.DEFAULT

        return state.mode

    def get_all_function_modes(
        self,
        company_id: int,
    ) -> dict[str, str]:
        """운영 기준이 나열한 10개 기능 전체에 대해, 값이 있으면 그
        값을, 없으면 기본값(MANUAL)을 채운 딕셔너리를 반환한다 —
        호출자가 "이 기능은 아직 한 번도 설정 안 됐다"를 따로
        구분하지 않아도 되게 한다(화면은 항상 10줄을 보여줘야
        하므로)."""

        latest_states = {
            row.function_code: row.mode
            for row in self.repository.get_latest_function_mode_states_for_company(
                company_id,
            )
        }

        return {
            code: latest_states.get(code, FunctionMode.DEFAULT)
            for code in FunctionCode.ALL
        }

    def get_all_function_states(
        self,
        company_id: int,
    ) -> dict[str, FunctionAutomationState | None]:
        """
        2026-09-09 Phase 4(HOMEZ_USER_OPERATION_SETTINGS.md 4번 —
        "중지 원인과 필요한 설정값을 화면에 표시한다") — `get_all_
        function_modes()`는 문자열 모드만 반환해 "왜 이 상태가 됐는지"
        (reason)를 화면에 보여줄 수 없었다. 이 메서드는 각 기능의
        가장 최근 행 전체(없으면 None)를 반환한다 — 기존
        `get_all_function_modes()`의 반환 타입(dict[str, str])을
        바꾸지 않고 그대로 둔 채(다른 호출부·테스트 영향 없음) 화면
        전용으로 추가한 메서드다.
        """

        latest_states = {
            row.function_code: row
            for row in self.repository.get_latest_function_mode_states_for_company(
                company_id,
            )
        }

        return {code: latest_states.get(code) for code in FunctionCode.ALL}

    def set_function_mode(
        self,
        company_id: int,
        function_code: str,
        mode: str,
        set_by: int,
        is_admin: bool,
        reason: str | None = None,
    ) -> FunctionAutomationState:

        if not is_admin:
            raise ForbiddenException(
                "기능별 자동화 모드 변경은 관리자만 가능합니다.",
            )

        if function_code not in FunctionCode.ALL:
            raise BadRequestException(
                f"알 수 없는 기능 코드: {function_code}",
            )

        if mode not in FunctionMode.USER_SELECTABLE:
            # ERROR는 시스템이 스스로 전이시키는 상태다(아래
            # `demote_function_to_error` 참고) — 사용자가 API로 직접
            # 요청해서 만들 수 있는 상태가 아니다.
            raise BadRequestException(
                f"사용자가 직접 설정할 수 없는 상태입니다: {mode}",
            )

        state = FunctionAutomationState(
            company_id=company_id,
            function_code=function_code,
            mode=mode,
            set_by=set_by,
            reason=reason,
        )

        try:
            state = self.repository.add_function_mode_state_no_commit(state)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return state

    def demote_function_to_error(
        self,
        company_id: int,
        function_code: str,
        reason: str,
    ) -> FunctionAutomationState:
        """
        가격 인상, 재고 부족, 인증 만료, API 오류, 스키마 불일치 등
        시스템이 스스로 감지한 문제로 그 기능만 안전한 상태로 낮출 때
        호출한다(Phase 4의 "기능별 중지"가 실제로 쓰는 진입점). 사람이
        아니라 시스템이 거는 것이므로 `is_admin` 게이트가 없다 — 대신
        `set_by`를 항상 0(시스템)으로 고정해, 사람이 건 것과 감사에서
        명확히 구분되게 한다.
        """

        if function_code not in FunctionCode.ALL:
            raise BadRequestException(
                f"알 수 없는 기능 코드: {function_code}",
            )

        state = FunctionAutomationState(
            company_id=company_id,
            function_code=function_code,
            mode=FunctionMode.ERROR,
            set_by=0,
            reason=reason,
        )

        try:
            state = self.repository.add_function_mode_state_no_commit(state)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return state

    def is_function_automatic(
        self,
        company_id: int,
        function_code: str,
    ) -> bool:
        """실행 코드가 "지금 이 기능을 자동으로 진행해도 되는가"를
        묻는 단일 진입점 — MANUAL/SEMI_AUTOMATIC/PAUSED/ERROR는 전부
        False다(자동 실행은 AUTOMATIC일 때만)."""

        return self.get_function_mode(company_id, function_code) == FunctionMode.AUTOMATIC

    # --------------------------------------------------
    # Emergency Stop
    # --------------------------------------------------

    def is_emergency_stop_active(
        self,
    ) -> bool:

        latest = self.repository.get_latest_emergency_stop()

        return latest is not None and latest.is_active

    def activate_emergency_stop(
        self,
        reason: str,
        set_by: int,
        is_admin: bool,
        audit_ref: str | None = None,
    ) -> EmergencyStop:

        if not is_admin:
            raise ForbiddenException(
                "Emergency Stop 활성화는 관리자만 가능합니다.",
            )

        if not reason:
            raise BadRequestException(
                "Emergency Stop 사유는 필수입니다.",
            )

        latest = self.repository.get_latest_emergency_stop()

        if latest is not None and latest.is_active:
            return latest  # 이미 활성 — 재차 활성화하지 않고 기존 반환(멱등)

        stop = EmergencyStop(
            is_active=True,
            reason=reason,
            set_by=set_by,
            audit_ref=audit_ref,
        )

        try:
            stop = self.repository.add_emergency_stop_no_commit(stop)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return stop

    def deactivate_emergency_stop(
        self,
        cleared_by: int,
        is_admin: bool,
    ) -> EmergencyStop | None:

        if not is_admin:
            raise ForbiddenException(
                "Emergency Stop 해제는 관리자만 가능합니다.",
            )

        latest = self.repository.get_latest_emergency_stop()

        if latest is None or not latest.is_active:
            return latest  # 이미 비활성(또는 이력 없음) — 멱등

        cleared = EmergencyStop(
            is_active=False,
            reason=latest.reason,
            set_by=latest.set_by,
            cleared_by=cleared_by,
            cleared_at=datetime.utcnow(),
            audit_ref=latest.audit_ref,
        )

        try:
            cleared = self.repository.add_emergency_stop_no_commit(cleared)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return cleared

    # --------------------------------------------------
    # Execution Limit
    # --------------------------------------------------

    def set_limit(
        self,
        product_id: int | None,
        set_by: int,
        is_admin: bool,
        daily_funding_limit: float | None = None,
        per_product_funding_limit: float | None = None,
        daily_quantity_limit: int | None = None,
        per_product_quantity_limit: int | None = None,
        currency: str = "KRW",
    ) -> ExecutionLimit:

        if not is_admin:
            raise ForbiddenException(
                "실행 한도 설정은 관리자만 가능합니다.",
            )

        limit = ExecutionLimit(
            product_id=product_id,
            daily_funding_limit=daily_funding_limit,
            per_product_funding_limit=per_product_funding_limit,
            daily_quantity_limit=daily_quantity_limit,
            per_product_quantity_limit=per_product_quantity_limit,
            currency=currency,
            active=True,
        )

        try:
            limit = self.repository.add_limit_no_commit(limit)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return limit

    @staticmethod
    def _period_start_date(
        now: datetime | None = None,
    ) -> date:
        """Asia/Seoul(KST) 자정 기준의 "오늘" 날짜."""

        evaluated_at = now or datetime.now(KST)

        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=KST)

        return evaluated_at.astimezone(KST).date()

    # --------------------------------------------------
    # Safety Evaluation
    # --------------------------------------------------

    def evaluate(
        self,
        idempotency_key: str,
        product_id: int | None,
        funding_amount: float,
        quantity: int,
        operator_approved: bool = False,
        now: datetime | None = None,
    ) -> dict:
        """
        실행 요청 하나에 대해 ALLOW / REQUIRE_APPROVAL / DENY를 판단한다.

        판단 순서(고정, 감사 반영):
        1) 입력 검증
        2) Emergency Stop — 활성이면 다른 무엇보다 우선 DENY(idempotency
           재조회보다 먼저 확인한다)
        3) idempotency 재조회 — 이미 처리된 키면 DENY(DUPLICATE_REQUEST),
           ALLOW로 반환하지 않는다
        4) Automation Mode
        5) 한도 조회·판단·소비 기록을 하나의 Transaction으로 처리
           (ExecutionPeriodUsage에 대한 단일 조건부 UPDATE로 원자화)
        """

        if funding_amount < 0 or quantity < 0:
            return self._decision(
                SafetyDecision.DENY,
                [SafetyReason.INVALID_REQUEST],
            )

        try:

            if self.is_emergency_stop_active():
                self.db.rollback()
                return self._decision(
                    SafetyDecision.DENY,
                    [SafetyReason.EMERGENCY_STOP_ACTIVE],
                )

            existing_usage = self.repository.get_usage_by_idempotency(
                idempotency_key,
            )

            if existing_usage is not None:
                self.db.rollback()
                return self._decision(
                    SafetyDecision.DENY,
                    [SafetyReason.DUPLICATE_REQUEST],
                )

            mode = self.get_current_mode()

            if mode in (AutomationMode.DISABLED, AutomationMode.RECOMMEND_ONLY):
                self.db.rollback()
                return self._decision(
                    SafetyDecision.DENY,
                    [SafetyReason.MODE_NOT_ALLOWED],
                )

            if mode == AutomationMode.OPERATOR_APPROVAL and not operator_approved:
                self.db.rollback()
                return self._decision(
                    SafetyDecision.REQUIRE_APPROVAL,
                    [SafetyReason.OPERATOR_APPROVAL_REQUIRED],
                )

            period_start = self._period_start_date(now)
            reasons: list[str] = []

            global_limit = self.repository.get_active_limit(None)
            product_limit = (
                self.repository.get_active_limit(product_id)
                if product_id is not None
                else None
            )

            if global_limit is not None:
                reasons.extend(
                    self._consume_scope(
                        "GLOBAL",
                        period_start,
                        funding_amount,
                        quantity,
                        global_limit.daily_funding_limit,
                        global_limit.daily_quantity_limit,
                    ),
                )

            if product_limit is not None:
                reasons.extend(
                    self._consume_scope(
                        f"PRODUCT:{product_id}",
                        period_start,
                        funding_amount,
                        quantity,
                        product_limit.per_product_funding_limit,
                        product_limit.per_product_quantity_limit,
                    ),
                )

            if reasons:
                self.db.rollback()
                unique_reasons = list(dict.fromkeys(reasons))
                return self._decision(SafetyDecision.DENY, unique_reasons)

            usage = ExecutionUsage(
                product_id=product_id,
                funding_amount=funding_amount,
                quantity=quantity,
                idempotency_key=idempotency_key,
            )
            self.repository.add_usage_no_commit(usage)

            self.db.commit()

            return self._decision(SafetyDecision.ALLOW, [])

        except IntegrityError:

            self.db.rollback()
            # 동시 요청이 같은 idempotency_key로 경쟁한 경우
            # (조회 시점엔 없었지만 그 사이 다른 트랜잭션이 먼저 기록함).
            return self._decision(
                SafetyDecision.DENY,
                [SafetyReason.DUPLICATE_REQUEST],
            )

        except Exception:

            self.db.rollback()
            raise

    def _consume_scope(
        self,
        scope_key: str,
        period_start: date,
        funding_amount: float,
        quantity: int,
        funding_cap: float | None,
        quantity_cap: int | None,
    ) -> list[str]:
        """
        한 scope(GLOBAL 또는 PRODUCT:{id})에 대해 원자적으로 소비를
        시도한다. 실패하면(한도 초과) 소비는 반영되지 않고, 어떤 한도가
        초과됐는지 이유 목록을 반환한다.
        """

        if funding_cap is None and quantity_cap is None:
            return []

        self.repository.ensure_period_usage_row_no_commit(
            scope_key, period_start,
        )

        ok = self.repository.try_consume_period_usage(
            scope_key,
            period_start,
            funding_amount,
            quantity,
            funding_cap,
            quantity_cap,
        )

        if ok:
            return []

        current = self.repository.get_period_usage(scope_key, period_start)
        reasons = []

        if (
            funding_cap is not None
            and float(current.consumed_funding) + funding_amount > funding_cap
        ):
            reasons.append(SafetyReason.FUNDING_LIMIT_EXCEEDED)

        if (
            quantity_cap is not None
            and int(current.consumed_quantity) + quantity > quantity_cap
        ):
            reasons.append(SafetyReason.QUANTITY_LIMIT_EXCEEDED)

        return reasons

    @staticmethod
    def _decision(
        decision: str,
        reasons: list[str],
    ) -> dict:

        return {
            "decision": decision,
            "reasons": reasons,
        }


__all__ = [
    "KST",
    "SafetyService",
]
