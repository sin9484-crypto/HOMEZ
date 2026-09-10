"""
=========================================================
Homez OS

File : app/domains/purchase_task/sales_application_service.py

2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수" 확정) —
발주(order_submission_service.py) 전에 반드시 통과해야 하는 판매신청
게이트. 이 파일도 order_submission_service.py와 같은 원칙을 따른다:

1. **이 파일에 코드가 있다는 사실 자체는 승인이 아니다** —
   `ensure_sales_application_submitted()`는 `confirm_real_submission=
   True`를 명시적으로 넘기지 않으면 항상 거부한다(fail-closed).
2. 판매신청 접수(SUBMITTED)는 "승인"이 아니다 — 승인 상태를 조회하는
   API 자체가 없다고 공식 답변으로 확정됐다. 이 서비스는 "접수
   확인" 이상을 주장하지 않는다.
3. 발주와 달리 판매신청은 idempotency_key 기반 1회성 잠금이 아니다
   — (company_id, connection_id, product_code) 단위 현재상태 행을
   재시도할 수 있다(model.py의 PurchaseSalesApplicationAttempt
   docstring 참고, 금전·중복 위험이 없다는 스펙 구조 근거).
4. 이미 SUBMITTED로 접수 확인된 상품은 재호출하지 않는다 — 불필요한
   중복 호출을 피한다(온채널이 이미 신청된 상품 재신청을 어떻게
   처리하는지는 미확인이므로, 확인할 필요가 없는 호출은 아예 하지
   않는다).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.purchase_task.channel_adapter import get_purchase_channel_adapter
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import SalesApplicationStatus
from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt


class PurchaseSalesApplicationService:

    def __init__(self, db: Session, credential_store=None):

        self.db = db
        if credential_store is None:
            from app.core.windows_credential_store import WindowsCredentialStore
            credential_store = WindowsCredentialStore()
        self._credential_store = credential_store
        self._connection_service = PurchaseChannelConnectionService(
            db, credential_store=credential_store,
        )

    # ---------------- 조회(부작용 없음) ----------------

    def get_attempt(
        self, connection_id: int, company_id: int, product_code: str,
    ) -> PurchaseSalesApplicationAttempt | None:

        return (
            self.db.query(PurchaseSalesApplicationAttempt)
            .filter(
                PurchaseSalesApplicationAttempt.company_id == company_id,
                PurchaseSalesApplicationAttempt.connection_id == connection_id,
                PurchaseSalesApplicationAttempt.product_code == product_code,
            )
            .first()
        )

    def is_sales_application_confirmed(
        self, connection_id: int, company_id: int, product_code: str,
    ) -> bool:
        """발주 게이트가 참조하는 판정 — SUBMITTED 상태의 행이 있을
        때만 True다. PENDING/IN_FLIGHT/REJECTED/RESULT_UNKNOWN은
        전부 False(추측으로 통과시키지 않는다)."""

        attempt = self.get_attempt(connection_id, company_id, product_code)
        if attempt is None:
            return False
        return attempt.status in SalesApplicationStatus.SATISFIES_ORDER_GATE

    # ---------------- 실제 판매신청 실행 ----------------

    def ensure_sales_application_submitted(
        self, connection_id: int, company_id: int, product_code: str, *,
        triggered_by: int | None = None, confirm_real_submission: bool = False,
        adapter=None,
    ) -> PurchaseSalesApplicationAttempt:
        """이미 SUBMITTED로 접수 확인된 행이 있으면 재호출 없이 그대로
        반환한다. 없거나 REJECTED/RESULT_UNKNOWN이면 `confirm_real_
        submission=True`가 명시적으로 있을 때만 실제 온채널 판매신청을
        1회 시도한다.

        `adapter`는 선택 인자다 — order_submission_service.py처럼
        이미 같은 연결의 Adapter를 만들어 둔 호출자는 그 인스턴스를
        그대로 넘겨 자격증명을 두 번 읽지 않게 할 수 있다. 넘기지
        않으면(예: 이 서비스를 단독으로 호출하는 향후 화면) 이 메서드가
        직접 만든다."""

        existing = self.get_attempt(connection_id, company_id, product_code)
        if existing is not None and existing.status in SalesApplicationStatus.SATISFIES_ORDER_GATE:
            return existing

        if not confirm_real_submission:
            raise BadRequestException(
                "실제 판매신청 실행은 명시적 승인이 필요합니다 "
                "(confirm_real_submission=True) — 코드가 존재한다는 사실 "
                "자체는 승인이 아닙니다.",
            )

        connection = self._connection_service.verify_connection_ready_for_order_submission(
            connection_id, company_id,
        )

        attempt = self._get_or_create_attempt(
            connection_id=connection.id, company_id=company_id,
            mall_code=connection.mall_code, product_code=product_code,
            triggered_by=triggered_by,
        )

        attempt.status = SalesApplicationStatus.IN_FLIGHT
        self.db.commit()
        self.db.refresh(attempt)

        from app.domains.purchase_task.onchannel_client import (
            OnchannelAuthenticationError, OnchannelNetworkError,
            OnchannelNotFoundError, OnchannelPermissionError,
            OnchannelRateLimitedError, OnchannelResponseFormatError,
            OnchannelValidationError,
        )
        from app.domains.purchase_task.channel_adapter import (
            PurchaseChannelAdapterError,
        )

        if adapter is None:
            adapter = get_purchase_channel_adapter(
                connection.mall_code, credential_reference=connection.credential_reference,
                credential_store=self._credential_store,
            )

        try:
            result = adapter.apply_for_sale(product_code)
        except (
            OnchannelAuthenticationError, OnchannelPermissionError,
            OnchannelNotFoundError, OnchannelRateLimitedError,
            OnchannelValidationError,
        ) as exc:
            self._finalize_attempt(
                attempt, status=SalesApplicationStatus.REJECTED,
                failure_detail=str(exc),
            )
            raise
        except PurchaseChannelAdapterError as exc:
            self._finalize_attempt(
                attempt, status=SalesApplicationStatus.REJECTED,
                failure_detail=str(exc),
            )
            raise
        except (OnchannelNetworkError, OnchannelResponseFormatError) as exc:
            self._finalize_attempt(
                attempt, status=SalesApplicationStatus.RESULT_UNKNOWN,
                failure_detail=str(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — 예상 못한 예외도 결과불명으로
            self._finalize_attempt(
                attempt, status=SalesApplicationStatus.RESULT_UNKNOWN,
                failure_detail=f"{type(exc).__name__}: 예상하지 못한 오류",
            )
            raise

        if not result.submitted:
            # SUPPORTED가 아니거나(UNKNOWN Adapter) submitted=False로
            # 돌아온 경우 — 접수 확인이 안 된 것이므로 RESULT_UNKNOWN.
            self._finalize_attempt(
                attempt, status=SalesApplicationStatus.RESULT_UNKNOWN,
                failure_detail=result.detail,
            )
            return attempt

        self._finalize_attempt(
            attempt, status=SalesApplicationStatus.SUBMITTED,
            applied_product_code=result.applied_product_code,
        )
        return attempt

    # ---------------- 내부 ----------------

    def _get_or_create_attempt(
        self, *, connection_id, company_id, mall_code, product_code, triggered_by,
    ) -> PurchaseSalesApplicationAttempt:
        """(company_id, connection_id, product_code) UNIQUE 위에서
        현재상태 행 하나를 재사용한다 — 발주와 달리 재시도가 금전적
        위험이 없으므로 매번 새 행을 만들지 않는다."""

        existing = self.get_attempt(connection_id, company_id, product_code)
        if existing is not None:
            existing.status = SalesApplicationStatus.PENDING
            existing.failure_detail = None
            existing.started_at = datetime.utcnow()
            existing.finished_at = None
            existing.triggered_by = triggered_by
            self.db.commit()
            self.db.refresh(existing)
            return existing

        attempt = PurchaseSalesApplicationAttempt(
            company_id=company_id, connection_id=connection_id,
            mall_code=mall_code, product_code=product_code,
            status=SalesApplicationStatus.PENDING,
            triggered_by=triggered_by, started_at=datetime.utcnow(),
        )
        self.db.add(attempt)
        try:
            self.db.commit()
        except IntegrityError:
            # 동시 요청 경쟁 — 다른 트랜잭션이 먼저 만들었다. 그 행을
            # 다시 조회해 이어서 쓴다(발주와 달리 여기서는 ConflictException
            # 으로 거부할 이유가 없다 — 재시도 자체가 정상 흐름이다).
            self.db.rollback()
            existing = self.get_attempt(connection_id, company_id, product_code)
            if existing is None:
                raise
            return existing
        self.db.refresh(attempt)
        return attempt

    def _finalize_attempt(
        self, attempt: PurchaseSalesApplicationAttempt, *, status: str,
        applied_product_code: str | None = None,
        failure_detail: str | None = None,
    ) -> None:

        attempt.status = status
        attempt.applied_product_code = applied_product_code
        attempt.failure_detail = failure_detail
        attempt.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(attempt)


__all__ = ["PurchaseSalesApplicationService"]
