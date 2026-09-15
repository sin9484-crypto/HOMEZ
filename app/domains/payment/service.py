"""
=========================================================
Homez OS

File : app/domains/payment/service.py

2026-09-10 Phase 7(HOMEZ_USER_OPERATION_SETTINGS.md 4·5·11번) —
결제수단 등록·관리 + 자동결제 한도 + 자동결제 허용 여부 판정.

**절대 하지 않는 것**: 실제 결제 실행. `verify_auto_payment_allowed()`
는 "지금 자동으로 결제해도 되는가"를 판정만 할 뿐 실제로 결제를
실행하지 않는다 — 실행은 이 세션 범위 밖(실제 외부 API 호출 금지)
이며, `FakePaymentProvider.charge()`도 호출자가 명시적으로 부를
때만(예: 이 파일의 테스트) 시뮬레이션을 수행한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import CredentialStore
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.payment.constants import PaymentMethodType
from app.domains.payment.gateway import PaymentProvider
from app.domains.payment.model import PaymentAutoLimit
from app.domains.payment.model import PaymentMethod
from app.domains.payment.repository import PaymentRepository


class PaymentService:

    def __init__(
        self,
        db: Session,
        credential_store: CredentialStore,
        gateway: PaymentProvider,
    ):
        self.db = db
        self.repository = PaymentRepository(db)
        self.credential_store = credential_store
        self.gateway = gateway
        self.safety = SafetyService(db)

    # ------------------------------
    # 결제수단 등록·관리
    # ------------------------------

    def register_method(
        self,
        *,
        company_id: int,
        user_id: int,
        is_admin: bool,
        method_type: str,
        raw_details: dict,
        display_name: str,
        make_default: bool = False,
    ) -> PaymentMethod:
        """
        `raw_details`(카드번호 등 원문)는 이 메서드 실행 도중에만
        존재한다 — `self.gateway.tokenize()`에 넘겨 토큰만 받고,
        그 토큰을 CredentialStore에 저장한 뒤에는 이 함수의 지역
        변수를 벗어나 어디에도 전달되지 않는다(DB에는 애초에 넘기지
        않는다 — PaymentMethod Model에 그런 컬럼이 없다).
        """

        if not is_admin:
            raise ForbiddenException(
                "결제수단 등록은 관리자만 가능합니다.",
            )

        if method_type not in PaymentMethodType.ALL:
            raise BadRequestException(
                f"알 수 없는 결제수단 종류: {method_type}",
            )

        if not display_name or not display_name.strip():
            raise BadRequestException(
                "결제수단 표시 이름이 필요합니다.",
            )

        token_ref = self.gateway.tokenize(method_type, raw_details)

        target_name = (
            f"homez_payment_method_{company_id}_"
            f"{method_type.lower()}_{token_ref[-16:]}"
        )
        self.credential_store.save(target_name, {"token_ref": token_ref})

        method = PaymentMethod(
            company_id=company_id,
            method_type=method_type,
            display_name=display_name.strip(),
            credential_target_name=target_name,
            is_default=make_default,
            active=True,
            created_by=user_id,
        )

        # 2026-09-15 전면 감사 후속(Phase 4, IA-011) — Credential
        # Store 저장은 이미 끝났다(위). 그 뒤의 "기본값 해제 + 신규
        # 행 생성"은 한 트랜잭션(commit 1회)으로 묶고, 그 commit이
        # 실패하면 방금 저장한 Credential Store 토큰도 최선노력으로
        # 되돌린다 — 그렇지 않으면 DB에는 없는데 Credential Store에만
        # 남는 고아 토큰이 생긴다(실금전 위험은 아니지만 정리되지
        # 않는 비밀정보가 남는 문제).
        if make_default:
            self.repository.clear_default_for_company(company_id)
        try:
            return self.repository.create_method(method)
        except Exception:
            self.db.rollback()
            try:
                self.credential_store.delete(target_name)
            except Exception:  # noqa: BLE001 — 되돌리기 실패가 원래 예외를 가리면 안 된다
                pass
            raise

    def list_methods(
        self, company_id: int, *, include_inactive: bool = False,
    ) -> list[PaymentMethod]:

        return self.repository.list_methods(
            company_id, include_inactive=include_inactive,
        )

    def deactivate_method(
        self,
        *,
        company_id: int,
        method_id: int,
        is_admin: bool,
    ) -> PaymentMethod:

        if not is_admin:
            raise ForbiddenException(
                "결제수단 비활성화는 관리자만 가능합니다.",
            )

        method = self.repository.get_method(company_id, method_id)

        if method is None:
            raise NotFoundException("해당 결제수단을 찾을 수 없습니다.")

        if not method.active:
            return method

        method.active = False
        method.is_default = False
        method.deactivated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(method)

        try:
            self.credential_store.delete(method.credential_target_name)
        except Exception:  # noqa: BLE001 — Credential 삭제 실패가 비활성화 자체를 막지 않는다
            pass

        return method

    def set_default_method(
        self,
        *,
        company_id: int,
        method_id: int,
        is_admin: bool,
    ) -> PaymentMethod:

        if not is_admin:
            raise ForbiddenException(
                "기본 결제수단 변경은 관리자만 가능합니다.",
            )

        method = self.repository.get_method(company_id, method_id)

        if method is None:
            raise NotFoundException("해당 결제수단을 찾을 수 없습니다.")

        if not method.active:
            raise BadRequestException(
                "비활성화된 결제수단은 기본으로 지정할 수 없습니다.",
            )

        # 2026-09-15 전면 감사 후속(Phase 4, IA-011) — clear_default_
        # for_company()가 더 이상 자체 commit하지 않으므로, 아래
        # commit 1회가 "기존 기본값 해제"와 "새 기본값 지정"을 함께
        # 반영한다. 이전에는 두 개의 별도 commit이라 두 번째가
        # 실패하면 기본 결제수단이 아예 없는 상태로 남을 위험이
        # 있었다. commit 실패 시 명시적으로 rollback해, 세션에 남은
        # 미반영 변경(is_default=True 등)이 다음 호출에 잘못 섞여
        # 들어가지 않게 한다.
        self.repository.clear_default_for_company(company_id)
        method.is_default = True
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(method)

        return method

    # ------------------------------
    # 자동결제 한도
    # ------------------------------

    def get_auto_limit(self, company_id: int) -> PaymentAutoLimit | None:

        return self.repository.get_latest_limit(company_id)

    def set_auto_limit(
        self,
        *,
        company_id: int,
        user_id: int,
        is_admin: bool,
        per_transaction_limit_amount: float,
        daily_limit_amount: float,
        currency: str = "KRW",
    ) -> PaymentAutoLimit:

        if not is_admin:
            raise ForbiddenException(
                "자동결제 한도 변경은 관리자만 가능합니다.",
            )

        if per_transaction_limit_amount <= 0 or daily_limit_amount <= 0:
            raise BadRequestException(
                "한도는 0보다 커야 합니다.",
            )

        if per_transaction_limit_amount > daily_limit_amount:
            raise BadRequestException(
                "건당 한도는 일일 한도를 넘을 수 없습니다.",
            )

        limit = PaymentAutoLimit(
            company_id=company_id,
            per_transaction_limit_amount=per_transaction_limit_amount,
            daily_limit_amount=daily_limit_amount,
            currency=currency,
            set_by=user_id,
        )

        return self.repository.add_limit(limit)

    # ------------------------------
    # 자동결제 허용 여부 판정 (실행하지 않는다 — 판정만)
    # ------------------------------

    def verify_auto_payment_allowed(
        self, company_id: int, amount: float,
    ) -> tuple[bool, str | None]:
        """
        "지금 이 금액을 자동으로 결제해도 되는가"만 판정한다. 실제
        결제는 이 메서드 밖에서, 이 판정이 True일 때만, 그리고 이번
        Phase 범위 밖(실제 외부 API 호출 금지)에서 이뤄져야 한다.

        일일 누적 사용액 집계는 아직 구현하지 않았다 — 실제 결제
        실행 자체가 아직 없어 집계할 대상(어떤 실행이 실제로
        일어났는지 기록하는 원장)이 없기 때문이다. 건당 한도만
        검사한다(정직하게 문서화 — docs/HOMEZ_PROJECT_STATE.md
        Phase 7 절 참고).
        """

        mode = self.safety.get_function_mode(company_id, FunctionCode.PAYMENT)

        if mode != FunctionMode.AUTOMATIC:
            return False, f"결제 기능이 자동 모드가 아닙니다(현재: {mode})."

        limit = self.repository.get_latest_limit(company_id)

        if limit is None:
            return False, "자동결제 한도가 설정되어 있지 않습니다."

        if amount > limit.per_transaction_limit_amount:
            return False, (
                f"건당 한도({limit.per_transaction_limit_amount:,.0f}"
                f"{limit.currency})를 초과했습니다."
            )

        return True, None


__all__ = ["PaymentService"]
