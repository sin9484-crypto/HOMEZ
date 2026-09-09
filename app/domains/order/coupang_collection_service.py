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
    ) -> CoupangCollectionRunResult:
        if channel_status not in ALLOWED_STATUSES:
            raise BadRequestException("지원하지 않는 쿠팡 주문 상태입니다.")
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
        try:
            provider = self.provider_factory(credential)
            result = provider.collect(
                created_at_from=lease.created_at_from,
                created_at_to=lease.created_at_to,
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

        if normalized_orders:
            try:
                materializer = CoupangOrderMaterializationService(self.db)
                materializer.ensure_orders(
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
            error_codes=tuple(dict.fromkeys(error_codes)),
        )


__all__ = ["CoupangCollectionRunResult", "CoupangOrderCollectionService"]
