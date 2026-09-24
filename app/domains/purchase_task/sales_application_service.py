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

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
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
        override_unresolved_status: bool = False, adapter=None,
    ) -> PurchaseSalesApplicationAttempt:
        """이미 SUBMITTED로 접수 확인된 행이 있으면 재호출 없이 그대로
        반환한다. 없거나 REJECTED면 `confirm_real_submission=True`가
        명시적으로 있을 때만 실제 온채널 판매신청을 1회 시도한다.

        2026-09-23 후속(자동 재신청 방지 라운드) — 기존 행이
        `SalesApplicationStatus.BLOCKS_AUTO_RETRY`(`RESULT_UNKNOWN`/
        `NEEDS_REVIEW`)에 해당하면 `confirm_real_submission=True`만으로는
        더 이상 자동 재시도하지 않는다(결과를 아직 모르는 상태에서
        말없이 다시 쏘지 않는다는 원칙) — `order_submission_service.py`의
        자동 호출 경로는 이 새 플래그를 절대 넘기지 않으므로, 발주
        시도도 이 상태에서는 함께 막힌다. 사람이 직접 현재 상태를
        확인한 뒤 재시도하려면 `override_unresolved_status=True`를
        별도로, 명시적으로 넘겨야 한다.

        `adapter`는 선택 인자다 — order_submission_service.py처럼
        이미 같은 연결의 Adapter를 만들어 둔 호출자는 그 인스턴스를
        그대로 넘겨 자격증명을 두 번 읽지 않게 할 수 있다. 넘기지
        않으면(예: 이 서비스를 단독으로 호출하는 향후 화면) 이 메서드가
        직접 만든다."""

        existing = self.get_attempt(connection_id, company_id, product_code)
        if existing is not None and existing.status in SalesApplicationStatus.SATISFIES_ORDER_GATE:
            return existing

        if (
            existing is not None
            and existing.status in SalesApplicationStatus.BLOCKS_AUTO_RETRY
            and not override_unresolved_status
        ):
            detail = f" 참고: {existing.failure_detail}" if existing.failure_detail else ""
            raise ConflictException(
                f"이 상품·연결의 판매신청 결과를 아직 확신할 수 없습니다"
                f"(현재 상태: {existing.status}) — 자동으로 다시 신청하지 "
                "않습니다. 온채널 공식 화면이나 공급처 문의로 현재 판매신청 "
                "상태를 사람이 먼저 확인하세요. 확인 후 재신청이 필요하면 "
                "그 결과를 근거로 명시적으로 다시 진행해야 합니다."
                + detail,
            )

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

    # ---------------- 외부 증거 수동 반영(실행 아님) ----------------

    def record_unconfirmed_prior_evidence(
        self, connection_id: int, company_id: int, product_code: str, *,
        mall_code: str, evidence_summary: str, recorded_by: int,
        event_occurred_at: datetime | None = None,
    ) -> PurchaseSalesApplicationAttempt:
        """2026-09-23 후속(자동 재신청 방지 라운드) — 이 서비스가 실제로
        실행한 적 없는, 이 서비스 밖의 정황 증거(예: 과거 감사 문서
        기록)를 사람이 검토해 `NEEDS_REVIEW`로 표시할 때만 쓴다.
        `apply_for_sale()`을 절대 호출하지 않는다 — "신청을 새로
        실행했다"가 아니라 "확인이 필요한 상태로 표시해 자동 재신청을
        막는다"는 뜻뿐이다. 이미 SUBMITTED로 확인된 행은 덮어쓰지
        않는다(그럴 필요가 없다 — 이미 더 강한 증거가 있다).

        `event_occurred_at`(실제 사건이 있었다고 추정되는 시각)과 이
        메서드가 실행되는 시각(기록/확인 시각)을 섞지 않는다 — 전자를
        모르면 `None`으로 그대로 남긴다(추정 금지)."""

        existing = self.get_attempt(connection_id, company_id, product_code)
        if existing is not None and existing.status in SalesApplicationStatus.SATISFIES_ORDER_GATE:
            raise BadRequestException(
                "이미 SUBMITTED로 접수 확인된 행이 있어 정황 증거로 덮어쓸 "
                "필요가 없습니다 — 기존 확인 결과를 그대로 유지합니다.",
            )

        recorded_at = datetime.utcnow()
        note = (
            "[정황 증거 반영 — 실제 판매신청 실행 아님] "
            f"사건 발생 추정 시각={event_occurred_at.isoformat() if event_occurred_at else '미상'}; "
            f"기록(확인) 시각={recorded_at.isoformat()}; "
            f"기록자=user#{recorded_by}; 근거={evidence_summary}"
        )

        if existing is None:
            attempt = PurchaseSalesApplicationAttempt(
                company_id=company_id, connection_id=connection_id,
                mall_code=mall_code, product_code=product_code,
                status=SalesApplicationStatus.NEEDS_REVIEW,
                failure_detail=note, triggered_by=recorded_by,
                started_at=recorded_at, finished_at=recorded_at,
            )
            self.db.add(attempt)
        else:
            attempt = existing
            attempt.status = SalesApplicationStatus.NEEDS_REVIEW
            attempt.failure_detail = note
            attempt.triggered_by = recorded_by
            attempt.finished_at = recorded_at

        self.db.commit()
        self.db.refresh(attempt)

        # 근거 확인·권한(호출 자체가 이미 관리자 권한을 전제) 다음으로
        # 감사기록을 남긴다 — 이 정황 반영이 "누가·언제·무슨 근거로"
        # 이뤄졌는지 attempt 행 자체(failure_detail)와 별개로
        # audit_logs에도 남겨 이중으로 추적 가능하게 한다.
        try:
            write_audit_log(
                self.db,
                company_id=company_id, user_id=recorded_by,
                action="SALES_APPLICATION_PRIOR_EVIDENCE_RECORDED",
                entity="purchase_sales_application_attempt",
                entity_id=str(attempt.id),
                description=(
                    f"connection_id={connection_id}; product_code={product_code}; "
                    f"event_occurred_at="
                    f"{event_occurred_at.isoformat() if event_occurred_at else '미상'}; "
                    f"evidence_summary={evidence_summary}"
                ),
            )
            self.db.commit()
        except Exception:  # noqa: BLE001 — 감사 기록 실패가 이 작업 자체를 막지 않는다
            self.db.rollback()

        return attempt

    # ---------------- 검토 해제(실행 아님, 사람의 확인 기록) ----------------

    def resolve_needs_review_as_confirmed_submitted(
        self, connection_id: int, company_id: int, product_code: str, *,
        confirmation_source: str, confirmation_summary: str, confirmed_by: int,
        confirmed_at: datetime | None = None,
    ) -> PurchaseSalesApplicationAttempt:
        """2026-09-24 후속(최초 신청 UI·검토 해제 흐름 완성 라운드) —
        `NEEDS_REVIEW`/`RESULT_UNKNOWN`으로 막힌 행을, 사람이 온채널
        판매자센터 화면을 직접 열어 실제로 확인했거나 공급처의 공식
        문의 회신을 받아 "이 상품·연결의 판매신청이 실제로 접수/승인된
        상태임을 확인했을 때만" 쓴다. **이 메서드 자체는 `apply_for_
        sale()`을 절대 호출하지 않는다**(외부 호출 0회) — 사람이 이미
        확인한 사실을 기록할 뿐이다.

        `override_unresolved_status=True`(기존, `ensure_sales_
        application_submitted()`)와의 차이: 그 플래그는 "다시 API를
        호출해도 된다"는 재시도 승인이고, 이 메서드는 "이미 확인된
        결과가 있으니 다시 호출할 필요가 없다"는 해제다 — 서로 다른
        상황에 쓴다(재시도할 필요가 있으면 여전히 `override_
        unresolved_status=True` + `confirm_real_submission=True`를
        쓴다, 이 메서드가 그 경로를 대신하지 않는다).

        `SATISFIES_ORDER_GATE`에 포함되는 `SUBMITTED`로 전환하지만,
        이것만으로 발주(`submit_order()`)가 자동 실행되지는 않는다 —
        발주는 여전히 별도의 `confirm_real_submission=True` 승인과
        포인트·배송비 승인 게이트를 통과해야 한다(이 메서드가 여는
        것은 판매신청 게이트 하나뿐이다).

        원문 응답이 없으므로 `applied_product_code`를 생성하지
        않는다(실제 API가 돌려준 값처럼 보이는 값을 지어내지 않는다) —
        `product_code` 자체가 이미 식별자다. 기존 `failure_detail`
        (최초 정황 기록)은 지우지 않고 그 아래에 이어 붙인다 — 사건
        발생시각(과거 기록)과 이번 확인시각을 한 행 안에서도 구분해
        볼 수 있게 한다."""

        existing = self.get_attempt(connection_id, company_id, product_code)
        if existing is None:
            raise BadRequestException(
                "해제할 검토 대상 행이 없습니다 — 이 상태는 검토가 필요한 "
                "기록이 아직 없다는 뜻입니다.",
            )
        if existing.status not in SalesApplicationStatus.BLOCKS_AUTO_RETRY:
            raise BadRequestException(
                f"이 행은 검토 대기 상태가 아닙니다(현재 상태: {existing.status}) "
                "— 해제할 필요가 없습니다.",
            )
        if not confirmation_source or not confirmation_summary:
            raise BadRequestException(
                "확인 출처(예: 온채널 판매자센터 화면 직접 확인, 공급처 공식 "
                "문의 회신)와 실제로 확인한 내용을 반드시 함께 기록해야 "
                "합니다 — 근거 없는 해제는 허용되지 않습니다.",
            )

        confirmed_at = confirmed_at or datetime.utcnow()
        resolution_note = (
            "[검토 해제 — 사람이 실제로 확인, 판매신청 API 재호출 아님] "
            f"확인 출처={confirmation_source}; "
            f"확인 시각={confirmed_at.isoformat()}; "
            f"확인자=user#{confirmed_by}; "
            f"확인 내용={confirmation_summary}"
        )
        existing.status = SalesApplicationStatus.SUBMITTED
        existing.finished_at = confirmed_at
        existing.failure_detail = (
            f"{existing.failure_detail}\n{resolution_note}"
            if existing.failure_detail else resolution_note
        )
        self.db.commit()
        self.db.refresh(existing)

        try:
            write_audit_log(
                self.db,
                company_id=company_id, user_id=confirmed_by,
                action="SALES_APPLICATION_NEEDS_REVIEW_RESOLVED",
                entity="purchase_sales_application_attempt",
                entity_id=str(existing.id),
                description=(
                    f"connection_id={connection_id}; product_code={product_code}; "
                    f"confirmation_source={confirmation_source}; "
                    f"confirmation_summary={confirmation_summary}"
                ),
            )
            self.db.commit()
        except Exception:  # noqa: BLE001 — 감사 기록 실패가 이 작업 자체를 막지 않는다
            self.db.rollback()

        return existing

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
