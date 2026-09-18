"""Orchestrate one read-only Coupang order collection run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.domains.order.adapters.coupang_collection import ALLOWED_STATUSES
from app.domains.order.adapters.coupang_collection import MAX_WINDOW
from app.domains.order.adapters.coupang_collection import (
    CoupangOrderCollectionProvider,
)
from app.domains.order.collection_persistence import (
    CoupangOrderCollectionPersistence,
)
from app.domains.order.collection_service import OrderCollectionCursorService
from app.domains.order.coupang_normalizer import CoupangOrderNormalizationError
from app.domains.order.coupang_normalizer import normalize_coupang_order
from app.domains.order.order_materialization import (
    CoupangOrderMaterializationService,
)
from app.domains.order.order_materialization import RecoveryReviewCandidate
from app.domains.store_connection.model import StoreConnection


@dataclass(frozen=True)
class CoupangCollectionRunResult:
    status: str
    channel_status: str
    page_count: int = 0
    received_order_count: int = 0
    saved_fulfillment_count: int = 0
    new_fulfillment_count: int = 0
    updated_fulfillment_count: int = 0
    duplicate_fulfillment_count: int = 0
    new_unresolved_item_count: int = 0
    failed_order_count: int = 0
    # 2026-09-17 Phase 7A 사후 감사 2차(Phase 3/4) — ACCEPT가 아닌
    # 상태로 처음 발견됐지만 HOMEZ에 대응하는 Order가 없는 주문. 자동
    # 생성하지 않고 여기 개수·마스킹된 식별자로만 보고한다(실패로
    # 세지 않음 — 체크포인트는 정상 전진한다).
    recovery_review_count: int = 0
    recovery_candidates: tuple[RecoveryReviewCandidate, ...] = ()
    error_codes: tuple[str, ...] = ()


ProviderFactory = Callable[[dict[str, str]], CoupangOrderCollectionProvider]


class CoupangOrderCollectionService:
    def __init__(
        self,
        db: Session,
        credential_store: CredentialStore,
        *,
        provider_factory: ProviderFactory | None = None,
    ):
        self.db = db
        self.credential_store = credential_store
        self.provider_factory = provider_factory or CoupangOrderCollectionProvider

    @staticmethod
    def validate_connection(
        db: Session, company_id: int, connection_id: int,
    ) -> StoreConnection:
        connection = (
            db.query(StoreConnection)
            .filter(StoreConnection.id == connection_id)
            .filter(StoreConnection.company_id == company_id)
            .first()
        )
        if connection is None:
            raise NotFoundException("쿠팡 판매계정 연결을 찾을 수 없습니다.")
        if connection.marketplace_code != "COUPANG":
            raise BadRequestException("쿠팡 판매계정 연결만 사용할 수 있습니다.")
        if connection.connection_status != "CONNECTED":
            raise BadRequestException("쿠팡 판매계정 연결 상태를 먼저 확인해 주세요.")
        if not connection.credential_reference:
            raise BadRequestException("저장된 쿠팡 연결 정보가 없습니다.")
        if connection.expires_at is not None:
            expires_at = connection.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                raise BadRequestException("쿠팡 연결 정보가 만료되었습니다. 다시 연결해 주세요.")
        return connection

    def run(
        self, company_id: int, connection_id: int, channel_status: str,
        *, actor_user_id: int = 0,
        window_override: tuple[datetime, datetime] | None = None,
    ) -> CoupangCollectionRunResult:
        """2026-09-18 Phase 7B 호출예산 보완(항목 3) — `window_override`가
        주어지면 커서가 자동 계산한 조회 구간 대신 이 구간으로 쿠팡을
        조회한다(동일 주문 중복 재조회 시험 전용 — 두 번째 실행이
        커서 전진 때문에 다른 구간을 보게 되는 문제를 막는다). 커서
        잠금·전진(성공 시 `lease.created_at_to`까지 전진)은 평소와
        동일하게 그대로 수행한다 — 운영 커서를 되돌리거나 DB를 직접
        수정하지 않는다. 서버가 이 구간의 폭을 검증해(`MAX_WINDOW`
        이하, 미래 아님) 호출자가 임의로 범위를 넓히지 못하게 한다."""

        if channel_status not in ALLOWED_STATUSES:
            raise BadRequestException("지원하지 않는 쿠팡 주문 상태입니다.")
        if window_override is not None:
            override_from, override_to = window_override
            if override_from.tzinfo is None or override_to.tzinfo is None:
                raise BadRequestException("조회 구간은 타임존 정보가 있어야 합니다.")
            if override_to <= override_from:
                raise BadRequestException("조회 구간이 올바르지 않습니다.")
            if override_to - override_from >= MAX_WINDOW:
                raise BadRequestException("조회 구간이 허용 범위를 초과했습니다.")
            if override_to > datetime.now(timezone.utc):
                raise BadRequestException("조회 구간이 미래 시각을 포함할 수 없습니다.")
        connection = self.validate_connection(self.db, company_id, connection_id)
        try:
            credential = self.credential_store.read(
                connection.credential_reference,
            )
        except CredentialNotFoundError as exc:
            raise BadRequestException(
                "저장된 쿠팡 연결 정보를 찾을 수 없습니다. 다시 연결해 주세요.",
            ) from exc
        except CredentialStoreError as exc:
            raise BadRequestException(
                "쿠팡 연결 정보 보안 저장소를 사용할 수 없습니다.",
            ) from exc
        required = ("vendor_id", "access_key", "secret_key")
        if any(
            not isinstance(credential.get(key), str)
            or not credential.get(key, "").strip()
            for key in required
        ):
            credential = {}
            raise BadRequestException("저장된 쿠팡 연결 정보 형식이 올바르지 않습니다.")

        position_service = OrderCollectionCursorService(self.db)
        lease = position_service.acquire(
            company_id, connection_id, channel_status,
        )
        query_from, query_to = (
            window_override if window_override is not None
            else (lease.created_at_from, lease.created_at_to)
        )
        try:
            provider = self.provider_factory(credential)
            result = provider.collect(
                created_at_from=query_from,
                created_at_to=query_to,
                status=channel_status,
            )
        except Exception:
            position_service.fail(
                lease.cursor_id, lease.lock_token, "ORDER_PROVIDER_INTERNAL_ERROR",
            )
            return CoupangCollectionRunResult(
                status="FAILED", channel_status=channel_status,
                error_codes=("ORDER_PROVIDER_INTERNAL_ERROR",),
            )
        finally:
            credential = {}

        if not result.success:
            code = result.error_code or "ORDER_COLLECTION_FAILED"
            position_service.fail(lease.cursor_id, lease.lock_token, code)
            return CoupangCollectionRunResult(
                status="FAILED", channel_status=channel_status,
                page_count=len(result.pages), error_codes=(code,),
            )

        persistence = CoupangOrderCollectionPersistence(self.db)
        received = saved_fulfillments = new_unresolved = failed = 0
        new_fulfillments = updated_fulfillments = duplicate_fulfillments = 0
        error_codes: list[str] = []
        normalized_orders = []
        for page in result.pages:
            for raw_order in page.orders:
                received += 1
                try:
                    normalized = normalize_coupang_order(raw_order)
                    normalized_orders.append(normalized)
                    previous = persistence.get_fulfillment(
                        company_id, connection_id,
                        normalized.channel_order_id,
                        normalized.channel_fulfillment_id,
                    )
                    previous_values = (
                        None if previous is None
                        else (previous.raw_status, previous.ordered_at)
                    )
                    fulfillment, created = persistence.upsert_fulfillment(
                        company_id, connection_id, normalized,
                    )
                    if created:
                        new_fulfillments += 1
                    elif previous_values != (
                        normalized.raw_status,
                        normalized.ordered_at.replace(tzinfo=None),
                    ):
                        updated_fulfillments += 1
                    else:
                        duplicate_fulfillments += 1
                    _items, created_items = persistence.preserve_unresolved_items(
                        company_id, fulfillment.id, normalized,
                    )
                    saved_fulfillments += 1
                    new_unresolved += created_items
                except CoupangOrderNormalizationError as exc:
                    failed += 1
                    error_codes.append(str(exc) or "NORMALIZATION_FAILED")
                except Exception:
                    self.db.rollback()
                    failed += 1
                    error_codes.append("ORDER_PERSISTENCE_FAILED")

        recovery_candidates: tuple[RecoveryReviewCandidate, ...] = ()
        if normalized_orders:
            try:
                materializer = CoupangOrderMaterializationService(self.db)
                materialization = materializer.ensure_orders(
                    company_id, connection_id, normalized_orders,
                )
                materializer.auto_resolve(
                    company_id,
                    connection_id,
                    actor_user_id=actor_user_id,
                )
            except Exception:
                self.db.rollback()
                failed += 1
                error_codes.append("ORDER_MATERIALIZATION_FAILED")
            else:
                # 2026-09-17 Phase 7A 사후 감사 2차 — 알 수 없는 상태는
                # fail-closed(실패로 집계, 체크포인트 미전진 → 다음
                # 재시도에서 다시 조회됨). 복구 검토 대상은 정상적으로
                # 예상되는 결과이므로 실패로 세지 않는다(체크포인트는
                # 정상 전진).
                recovery_candidates = materialization.recovery_candidates
                if materialization.rejected_unknown_status_count:
                    failed += materialization.rejected_unknown_status_count
                    error_codes.append("UNKNOWN_ORDER_STATUS")

        if failed:
            position_service.fail(
                lease.cursor_id, lease.lock_token, "PARTIAL_COLLECTION_FAILURE",
            )
            run_status = "PARTIAL"
        else:
            position_service.succeed(
                lease.cursor_id, lease.lock_token, lease.created_at_to,
            )
            run_status = "SUCCEEDED"

        return CoupangCollectionRunResult(
            status=run_status, channel_status=channel_status,
            page_count=len(result.pages), received_order_count=received,
            saved_fulfillment_count=saved_fulfillments,
            new_fulfillment_count=new_fulfillments,
            updated_fulfillment_count=updated_fulfillments,
            duplicate_fulfillment_count=duplicate_fulfillments,
            new_unresolved_item_count=new_unresolved,
            failed_order_count=failed,
            recovery_review_count=len(recovery_candidates),
            recovery_candidates=recovery_candidates,
            error_codes=tuple(dict.fromkeys(error_codes)),
        )


__all__ = ["CoupangCollectionRunResult", "CoupangOrderCollectionService"]
