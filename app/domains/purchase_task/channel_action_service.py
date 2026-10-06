"""
=========================================================
Homez OS

File : app/domains/purchase_task/channel_action_service.py

2026-10-05 — 판매채널 외부 변경(쿠팡 옵션 판매중지·고객 주문 취소)의 **작업 장부**와
실행 규칙. 이 모듈이 지키는 것:

  1) 외부 요청 **전에** (회사, 동작, 대상) 행을 REQUESTING으로 확정(commit)한다 —
     유일 제약(`uq_channel_action_request_target`)이 동시 실행·재시작 뒤 재실행의 중복
     호출을 막는다. 외부 성공 직후 프로세스가 죽거나 내부 저장이 실패해도 REQUESTING/
     SUCCEEDED가 남아 있어 같은 요청을 다시 보내지 않는다.
  2) 결과별 처리:  SUCCEEDED / RETRYABLE(적용 안 됨이 확실한 429만, 횟수·간격 제한) /
     ACTION_REQUIRED(요청·권한·미지원·상태 오류 — 원인 해결 전 자동 반복 금지) /
     UNKNOWN(적용 여부 불명 — 자동 재요청 금지, 조회·대조로만 확인).
  3) 결과불명 대조: 읽기 API로 실제 상태를 확인해 SUCCEEDED로 확정하거나, "적용되지
     않았다"가 확인된 경우에만 재시도 가능으로 되돌린다.
  4) 장부 테이블이 없는 DB(Migration 미적용)에서는 외부 요청을 보내지 않는다.

이 모듈은 모드·비상정지 판정을 하지 않는다(호출하는 `SupplierStopSaleService`가 한다).
=========================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.purchase_task.constants import ChannelActionPolicy
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_channel_control_provider import (
    CoupangChannelControlProvider,
)
from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult
from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult
from app.domains.purchase_task.model import ChannelActionRequest

logger = logging.getLogger("homez")

# REQUESTING 행이 이 시간보다 오래 남아 있으면 요청 프로세스가 죽은 것으로 보고 결과불명
# 대조 대상으로 삼는다(요청 시간 제한 20초보다 충분히 길다).
REQUESTING_STALE_SECONDS = 300


class ActionOutcome:
    """작업 장부 실행 결과. `SupplierStopSaleService`의 `StopSaleOutcome`과 같은 문자열을 쓴다."""

    SUCCEEDED = "SUCCEEDED"
    ALREADY_DONE = "ALREADY_DONE"
    RETRY_WAIT = "RETRY_WAIT"            # 적용 안 됨이 확실한 일시 오류 — 한도·간격 안에서 재시도
    ACTION_REQUIRED = "ACTION_REQUIRED"  # 원인 해결 전 자동 반복 금지
    UNKNOWN = "UNKNOWN"                  # 적용 여부 불명 — 자동 재요청 금지
    PREPARED = "PREPARED"
    NOT_APPLICABLE = "NOT_APPLICABLE"    # 대조할 요청 기록이 없다


@dataclass(frozen=True)
class ActionResult:
    outcome: str
    detail: str = ""
    # 이번 호출에서 외부 요청을 실제로 보냈는가. 결과불명(UNKNOWN)이 "이번에 막 났다"인지
    # "이전 요청의 불명 상태를 장부에서 읽었다"인지 구분한다 — 후자만 조회로 대조한다.
    executed: bool = False


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    row: ChannelActionRequest | None
    outcome: str = ""          # claimed=False일 때의 결과
    detail: str = ""


def journal_available(db: Session) -> bool:
    return sa_inspect(db.get_bind()).has_table(ChannelActionRequest.__tablename__)


class ChannelActionJournal:

    def __init__(self, db: Session, *, now_factory: Callable[[], datetime] | None = None):
        self.db = db
        self._now = now_factory or datetime.utcnow

    def get(self, company_id: int, action_type: str, target_key: str) -> ChannelActionRequest | None:
        return (
            self.db.query(ChannelActionRequest)
            .filter(
                ChannelActionRequest.company_id == company_id,
                ChannelActionRequest.action_type == action_type,
                ChannelActionRequest.target_key == target_key,
            )
            .first()
        )

    # ------------------------------------------------------------
    # 선점(외부 요청 전에 반드시 commit)
    # ------------------------------------------------------------

    def claim(
        self, *, company_id: int, store_connection_id: int, action_type: str,
        target_key: str, triggered_by: int | None, retry_action_required: bool = False,
    ) -> ClaimResult:
        now = self._now()
        row = self.get(company_id, action_type, target_key)
        if row is None:
            new = ChannelActionRequest(
                company_id=company_id, store_connection_id=store_connection_id,
                action_type=action_type, target_key=target_key,
                status=ChannelActionStatus.REQUESTING, attempt_count=1,
                triggered_by=triggered_by, requested_at=now,
            )
            self.db.add(new)
            try:
                self.db.commit()
            except IntegrityError:
                # 다른 프로세스가 같은 대상을 먼저 선점했다 — 진행 중으로 본다.
                self.db.rollback()
                return ClaimResult(
                    False, self.get(company_id, action_type, target_key),
                    ActionOutcome.UNKNOWN, "같은 대상의 요청이 이미 진행 중이다",
                )
            return ClaimResult(True, new)

        status = row.status
        if status == ChannelActionStatus.SUCCEEDED:
            return ClaimResult(False, row, ActionOutcome.ALREADY_DONE, row.detail or "")
        if status in (ChannelActionStatus.REQUESTING, ChannelActionStatus.UNKNOWN):
            return ClaimResult(
                False, row, ActionOutcome.UNKNOWN,
                "이전 요청의 적용 여부가 확인되지 않았다 — 자동으로 다시 요청하지 않고 조회·대조로 확인한다",
            )
        if status == ChannelActionStatus.ACTION_REQUIRED and not retry_action_required:
            return ClaimResult(
                False, row, ActionOutcome.ACTION_REQUIRED,
                f"이전 요청이 {row.last_error_class or '오류'}로 거부됐다 — 원인 해결 후 사람이 다시 실행해야 한다",
            )
        if status == ChannelActionStatus.RETRYABLE:
            if row.attempt_count >= ChannelActionPolicy.MAX_ATTEMPTS:
                self._set(row, ChannelActionStatus.ACTION_REQUIRED, "RETRY_EXHAUSTED",
                          f"재시도 {row.attempt_count}회를 모두 사용했다")
                return ClaimResult(
                    False, row, ActionOutcome.ACTION_REQUIRED, "재시도 횟수를 모두 사용했다",
                )
            if row.next_retry_at is not None and now < row.next_retry_at:
                return ClaimResult(
                    False, row, ActionOutcome.RETRY_WAIT,
                    f"다음 재시도 가능 시각 전이다({row.next_retry_at.isoformat(timespec='seconds')} UTC)",
                )

        # RETRYABLE(대기 끝) 또는 사람이 승인한 ACTION_REQUIRED 재실행 — 조건부 갱신으로 선점
        updated = (
            self.db.query(ChannelActionRequest)
            .filter(
                ChannelActionRequest.id == row.id,
                ChannelActionRequest.status == status,
                ChannelActionRequest.attempt_count == row.attempt_count,
            )
            .update(
                {
                    "status": ChannelActionStatus.REQUESTING,
                    "attempt_count": row.attempt_count + 1,
                    "next_retry_at": None, "requested_at": now,
                    "triggered_by": triggered_by, "updated_at": now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if updated != 1:
            return ClaimResult(
                False, row, ActionOutcome.UNKNOWN, "다른 프로세스가 같은 대상을 먼저 선점했다",
            )
        self.db.refresh(row)
        return ClaimResult(True, row)

    # ------------------------------------------------------------
    # 결과 기록
    # ------------------------------------------------------------

    def _set(
        self, row: ChannelActionRequest, status: str, error_class: str | None,
        detail: str, *, http_status: int | None = None,
        next_retry_at: datetime | None = None, expected_status: str | None = None,
    ) -> bool:
        now = self._now()
        query = self.db.query(ChannelActionRequest).filter(ChannelActionRequest.id == row.id)
        if expected_status is not None:
            query = query.filter(ChannelActionRequest.status == expected_status)
        values = {
            "status": status, "last_error_class": error_class, "detail": detail[:300],
            "last_http_status": http_status, "next_retry_at": next_retry_at,
            "updated_at": now,
            "completed_at": now if status == ChannelActionStatus.SUCCEEDED else None,
        }
        updated = query.update(values, synchronize_session=False)
        self.db.commit()
        self.db.refresh(row)
        return updated == 1

    def finish(self, row: ChannelActionRequest, result: ProviderCallResult) -> ActionResult:
        """요청 결과를 장부에 확정한다. REQUESTING일 때만 갱신한다(그 사이 다른 경로가
        대조로 바꾼 상태를 덮어쓰지 않는다)."""

        now = self._now()
        if result.kind == CallKind.SUCCEEDED:
            self._set(row, ChannelActionStatus.SUCCEEDED, None, result.detail,
                      http_status=result.http_status, expected_status=ChannelActionStatus.REQUESTING)
            return ActionResult(ActionOutcome.SUCCEEDED, result.detail)
        if result.kind == CallKind.RETRYABLE:
            if row.attempt_count >= ChannelActionPolicy.MAX_ATTEMPTS:
                self._set(row, ChannelActionStatus.ACTION_REQUIRED, "RETRY_EXHAUSTED",
                          f"재시도 {row.attempt_count}회를 모두 사용했다: {result.detail}",
                          http_status=result.http_status, expected_status=ChannelActionStatus.REQUESTING)
                return ActionResult(ActionOutcome.ACTION_REQUIRED, "재시도 횟수를 모두 사용했다")
            delays = ChannelActionPolicy.RETRY_DELAYS_SECONDS
            wait = delays[min(row.attempt_count - 1, len(delays) - 1)]
            if result.retry_after_seconds:
                wait = max(wait, result.retry_after_seconds)
            self._set(row, ChannelActionStatus.RETRYABLE, result.error_class, result.detail,
                      http_status=result.http_status, next_retry_at=now + timedelta(seconds=wait),
                      expected_status=ChannelActionStatus.REQUESTING)
            return ActionResult(ActionOutcome.RETRY_WAIT, f"{result.error_class}: {wait}초 뒤 재시도 가능")
        if result.kind == CallKind.ACTION_REQUIRED:
            self._set(row, ChannelActionStatus.ACTION_REQUIRED, result.error_class, result.detail,
                      http_status=result.http_status, expected_status=ChannelActionStatus.REQUESTING)
            return ActionResult(
                ActionOutcome.ACTION_REQUIRED, f"{result.error_class}: {result.detail}",
            )
        self._set(row, ChannelActionStatus.UNKNOWN, result.error_class, result.detail,
                  http_status=result.http_status, expected_status=ChannelActionStatus.REQUESTING)
        return ActionResult(ActionOutcome.UNKNOWN, f"{result.error_class}: {result.detail}")

    # ------------------------------------------------------------
    # 결과불명 대조
    # ------------------------------------------------------------

    def is_in_doubt(self, row: ChannelActionRequest) -> bool:
        if row.status == ChannelActionStatus.UNKNOWN:
            return True
        if row.status == ChannelActionStatus.REQUESTING:
            return (self._now() - row.requested_at).total_seconds() >= REQUESTING_STALE_SECONDS
        return False

    def apply_reconciliation(self, row: ChannelActionRequest, applied: bool) -> ActionResult:
        """읽기 조회로 확인한 결과를 반영한다. applied=True면 이미 적용돼 있다(성공으로 확정),
        False면 적용되지 않았음이 확인됐다(재시도 가능으로 되돌리되 한도는 유지)."""

        expected = row.status
        if applied:
            self._set(row, ChannelActionStatus.SUCCEEDED, None,
                      "결과불명 요청을 조회로 확인해 적용된 것으로 확정", expected_status=expected)
            return ActionResult(ActionOutcome.SUCCEEDED, "조회로 적용을 확인했다")
        if row.attempt_count >= ChannelActionPolicy.MAX_ATTEMPTS:
            self._set(row, ChannelActionStatus.ACTION_REQUIRED, "RETRY_EXHAUSTED",
                      "조회로 미적용을 확인했으나 재시도 횟수를 모두 사용했다", expected_status=expected)
            return ActionResult(ActionOutcome.ACTION_REQUIRED, "재시도 횟수를 모두 사용했다")
        self._set(row, ChannelActionStatus.RETRYABLE, "RECONCILED_NOT_APPLIED",
                  "조회로 미적용을 확인해 재시도 가능으로 되돌렸다", next_retry_at=self._now(),
                  expected_status=expected)
        return ActionResult(ActionOutcome.RETRY_WAIT, "조회로 미적용을 확인했다 — 재시도 가능")


class CoupangChannelActions:
    """`SupplierStopSaleService`가 쓰는 쿠팡 실행기: 판매 연결 확인 → 자격증명 → 작업 장부 →
    Provider. 연결·자격증명·장부를 준비하지 못하면 **요청을 보내지 않고** ACTION_REQUIRED."""

    def __init__(
        self, db: Session, *, credential_store=None, provider_factory=None,
        now_factory: Callable[[], datetime] | None = None,
        expected_vendor_id: str | None = None,
        wing_user_id: "str | Callable[[int], str | None] | None" = None,
    ):
        """`expected_vendor_id`: 승인된 시험이 지정한 업체코드 — 지정하면 자격증명의 업체코드가
        다를 때 요청하지 않는다(판매 연결의 `seller_identifier`는 사용자가 입력한 이름이라 업체코드와
        비교하지 않는다). `wing_user_id`: 승인된 시험이 **그 요청에 쓰도록 명시 지정한** WING 로그인
        ID — 저장·복사하지 않으며 자격증명에 `wing_user_id`가 이미 있으면 그 값이 우선한다.
        함수(판매 연결 id → 값 또는 None)를 넘기면 요청 대상 판매 연결마다 그 연결에 대해
        **일치가 확인된** 값만 쓴다(전역 적용 아님)."""

        self.db = db
        self._credential_store = credential_store
        self._provider_factory = provider_factory or CoupangChannelControlProvider
        self._expected_vendor_id = (expected_vendor_id or "").strip() or None
        self._wing_user_id = wing_user_id if callable(wing_user_id) else (
            (wing_user_id or "").strip() or None
        )
        self._journal = ChannelActionJournal(db, now_factory=now_factory)

    # ---- 준비 ----

    def _provider(self, company_id: int, store_connection_id: int):
        """(provider, None) 또는 (None, 실패 ActionResult)."""

        if not journal_available(self.db):
            return None, ActionResult(
                ActionOutcome.ACTION_REQUIRED,
                "작업 장부 테이블(channel_action_requests)이 없어 외부 요청을 보내지 않았다 — Migration 적용 필요",
            )
        from app.domains.store_connection.constants import ConnectionStatus
        from app.domains.store_connection.model import StoreConnection

        connection = (
            self.db.query(StoreConnection)
            .filter(
                StoreConnection.id == store_connection_id,
                StoreConnection.company_id == company_id,
                StoreConnection.marketplace_code == "COUPANG",
            )
            .first()
        )
        if (
            connection is None or connection.connection_status != ConnectionStatus.CONNECTED
            or not connection.credential_reference
        ):
            return None, ActionResult(
                ActionOutcome.ACTION_REQUIRED,
                "이 판매 계정의 쿠팡 연결을 사용할 수 없어 요청하지 않았다",
            )
        try:
            store = self._credential_store
            if store is None:
                from app.core.windows_credential_store import WindowsCredentialStore

                store = WindowsCredentialStore()
            credential = store.read(connection.credential_reference)
            vendor_id = str(credential.get("vendor_id") or "").strip()
            if not vendor_id:
                return None, ActionResult(
                    ActionOutcome.ACTION_REQUIRED, "자격증명에 업체코드가 없어 요청하지 않았다",
                )
            if self._expected_vendor_id is not None and vendor_id != self._expected_vendor_id:
                return None, ActionResult(
                    ActionOutcome.ACTION_REQUIRED,
                    "자격증명의 업체코드가 승인된 시험이 지정한 업체코드와 달라 요청하지 않았다",
                )
            explicit = (
                self._wing_user_id(store_connection_id) if callable(self._wing_user_id)
                else self._wing_user_id
            )
            if explicit and not str(credential.get("wing_user_id") or "").strip():
                credential = {**credential, "wing_user_id": explicit}  # 메모리에서만 합친다
            return self._provider_factory(credential), None
        except Exception as exc:  # noqa: BLE001 — 자격증명 내용은 오류 문구에 싣지 않는다
            logger.warning("쿠팡 자격증명 준비 실패: %s", type(exc).__name__)
            return None, ActionResult(
                ActionOutcome.ACTION_REQUIRED, "쿠팡 자격증명을 준비하지 못해 요청하지 않았다",
            )

    def _execute(
        self, *, company_id: int, store_connection_id: int, action_type: str, target_key: str,
        triggered_by: int | None, retry_action_required: bool,
        call: Callable[[CoupangChannelControlProvider], ProviderCallResult],
        validate: Callable[[CoupangChannelControlProvider], ProviderCallResult | None],
    ) -> ActionResult:
        provider, failure = self._provider(company_id, store_connection_id)
        if failure is not None:
            return failure
        # 로컬 사전검증 실패(WING ID 없음·형식 오류)는 외부 요청이 없었으므로 장부에 남기거나
        # 시도 횟수를 소모하지 않는다 — 원인을 고치면 바로 다시 실행할 수 있다.
        invalid = validate(provider)
        if invalid is not None:
            return ActionResult(
                ActionOutcome.ACTION_REQUIRED, f"{invalid.error_class}: {invalid.detail}",
            )
        claim = self._journal.claim(
            company_id=company_id, store_connection_id=store_connection_id,
            action_type=action_type, target_key=target_key, triggered_by=triggered_by,
            retry_action_required=retry_action_required,
        )
        if not claim.claimed:
            return ActionResult(claim.outcome, claim.detail)
        try:
            result = call(provider)
        except Exception as exc:  # noqa: BLE001 — 보냈는지 모르면 결과불명(자동 재요청 금지)
            logger.warning("쿠팡 채널 변경 호출 예외(결과불명 처리): %s", type(exc).__name__)
            result = ProviderCallResult(
                CallKind.UNKNOWN, None, "EXCEPTION", type(exc).__name__,
            )
        done = self._journal.finish(claim.row, result)
        return ActionResult(done.outcome, done.detail, executed=True)

    def _reconcile(
        self, *, company_id: int, store_connection_id: int, action_type: str, target_key: str,
        read: Callable[[CoupangChannelControlProvider], ProviderReadResult],
        applied_when: bool,
    ) -> ActionResult:
        row = self._journal.get(company_id, action_type, target_key)
        if row is None:
            return ActionResult(ActionOutcome.NOT_APPLICABLE, "대조할 요청 기록이 없다")
        if row.status == ChannelActionStatus.SUCCEEDED:
            return ActionResult(ActionOutcome.ALREADY_DONE, row.detail or "")
        if not self._journal.is_in_doubt(row):
            return ActionResult(ActionOutcome.UNKNOWN if row.status == ChannelActionStatus.REQUESTING
                                else ActionOutcome.ACTION_REQUIRED if row.status == ChannelActionStatus.ACTION_REQUIRED
                                else ActionOutcome.RETRY_WAIT, "대조 대상(결과불명) 상태가 아니다")
        provider, failure = self._provider(company_id, store_connection_id)
        if failure is not None:
            return ActionResult(ActionOutcome.UNKNOWN, failure.detail)
        try:
            read_result = read(provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("쿠팡 대조 조회 예외: %s", type(exc).__name__)
            return ActionResult(ActionOutcome.UNKNOWN, "대조 조회에 실패했다 — 결과불명 유지")
        if not read_result.ok or read_result.value is None:
            return ActionResult(
                ActionOutcome.UNKNOWN, f"대조 조회로 상태를 확인하지 못했다 — 결과불명 유지 {read_result.detail}".strip(),
            )
        return self._journal.apply_reconciliation(row, read_result.value is applied_when)

    # ---- 판매중지 ----

    @staticmethod
    def sale_stop_key(vendor_item_id: str) -> str:
        return f"vi:{str(vendor_item_id).strip()}"

    def stop_sale(
        self, *, company_id: int, store_connection_id: int, vendor_item_id: str,
        triggered_by: int | None = None, retry_action_required: bool = False,
    ) -> ActionResult:
        return self._execute(
            company_id=company_id, store_connection_id=store_connection_id,
            action_type=ChannelActionType.SALE_STOP,
            target_key=self.sale_stop_key(vendor_item_id), triggered_by=triggered_by,
            retry_action_required=retry_action_required,
            call=lambda p: p.stop_vendor_item_sale(vendor_item_id),
            validate=lambda p: p.validate_stop(vendor_item_id),
        )

    def check_sale_stopped(
        self, *, company_id: int, store_connection_id: int, vendor_item_id: str,
    ) -> ActionResult:
        return self._reconcile(
            company_id=company_id, store_connection_id=store_connection_id,
            action_type=ChannelActionType.SALE_STOP,
            target_key=self.sale_stop_key(vendor_item_id),
            read=lambda p: p.read_vendor_item_on_sale(vendor_item_id),
            applied_when=False,  # onSale == False 이면 판매중지가 적용된 것
        )

    # ---- 고객 주문 취소 ----

    @staticmethod
    def order_cancel_key(channel_order_id: str, shipment_box_id: str) -> str:
        return f"order:{str(channel_order_id).strip()}:box:{str(shipment_box_id).strip()}"

    def cancel_order(
        self, *, company_id: int, store_connection_id: int, channel_order_id: str,
        shipment_box_id: str, items: list[tuple[str, int]],
        triggered_by: int | None = None, retry_action_required: bool = False,
    ) -> ActionResult:
        return self._execute(
            company_id=company_id, store_connection_id=store_connection_id,
            action_type=ChannelActionType.ORDER_CANCEL,
            target_key=self.order_cancel_key(channel_order_id, shipment_box_id),
            triggered_by=triggered_by, retry_action_required=retry_action_required,
            call=lambda p: p.cancel_order_items(
                order_id=channel_order_id,
                vendor_item_ids=[i[0] for i in items], receipt_counts=[i[1] for i in items],
            ),
            validate=lambda p: p.validate_cancel(
                channel_order_id, [i[0] for i in items], [i[1] for i in items],
            ),
        )

    def check_order_cancelled(
        self, *, company_id: int, store_connection_id: int, channel_order_id: str,
        shipment_box_id: str, items: list[tuple[str, int]],
    ) -> ActionResult:
        return self._reconcile(
            company_id=company_id, store_connection_id=store_connection_id,
            action_type=ChannelActionType.ORDER_CANCEL,
            target_key=self.order_cancel_key(channel_order_id, shipment_box_id),
            read=lambda p: p.read_order_items_cancelled(shipment_box_id, [i[0] for i in items]),
            applied_when=True,  # 요청 품목이 모두 취소됐으면 적용된 것
        )


__all__ = [
    "ActionOutcome", "ActionResult", "ClaimResult", "ChannelActionJournal",
    "CoupangChannelActions", "journal_available", "REQUESTING_STALE_SECONDS",
]
