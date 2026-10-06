"""승인 대상 판매계정 → 실제 쿠팡 전송에 쓰는 판매 연결(StoreConnection) 해석.

기존 계약(새 스키마 없음): 등록 화면의 판매계정(`MarketplaceAccount`)은
`store_connection_projection.sync_connected_store_connections()`가 연결에서 만든다 —
`account_code == StoreConnection.seller_identifier`, 같은 회사·같은 채널이다. 옵션 연결
서비스(`listing_wizard_option_link_service._store_connection_id`)도 같은 규칙으로 계정에서
연결을 찾는다. 이 모듈은 그 규칙을 한 곳으로 모아, 메타데이터·물류·브랜드·등록 전송·상태
조회·정합화가 **같은 계정에서 같은 연결을 명시적으로** 얻게 한다.

이전에는 이 경로들이 "회사+COUPANG+CONNECTED 연결 중 `.first()` 또는 id가 가장 큰 것"을
골라, 승인한 계정과 실제 자격증명이 다른 연결일 수 있었다(연결이 둘 이상이면 단계마다
다른 계정이 선택될 수도 있었다).

한계(사실): `seller_identifier`는 사용자가 연결할 때 입력한 값이고, 자격증명의 실제 업체
코드(vendor_id)는 Credential Manager 안에만 있다. 이 모듈은 DB가 가진 명시적 연결 관계를
강제할 뿐, 그 자격증명이 실제로 어느 판매자 것인지는 보증하지 않는다(외부 확인 항목).
자격증명·비밀값은 읽지도 반환하지도 않는다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.exceptions import ServiceUnavailableException
from app.domains.marketplace_listing.repository import MarketplaceListingRepository
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

COUPANG_CHANNEL_CODE = "COUPANG"

# preflight 차단 코드
SELLER_CONNECTION_NOT_FOUND = "SELLER_CONNECTION_NOT_FOUND"
SELLER_CONNECTION_AMBIGUOUS = "SELLER_CONNECTION_AMBIGUOUS"
SELLER_CONNECTION_NOT_CONNECTED = "SELLER_CONNECTION_NOT_CONNECTED"
SELLER_CONNECTION_CHANGED_AFTER_APPROVAL = "SELLER_CONNECTION_CHANGED_AFTER_APPROVAL"


class SellerConnectionError(ServiceUnavailableException):
    """승인 대상 계정의 전송 연결을 확정할 수 없다 — 외부 호출 전에 막는다."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def resolve_seller_connection(
    db: Session, company_id: int, marketplace_account_id: int | None,
) -> StoreConnection:
    """지정한 판매계정의 쿠팡 판매 연결을 돌려준다. 확정할 수 없으면 `SellerConnectionError`.

    조건: 같은 회사의 활성 계정, 쿠팡 채널, 같은 회사·`marketplace_code=COUPANG`·
    `seller_identifier == account_code`인 연결이 **정확히 하나**, 그 연결이 CONNECTED이고
    자격증명 참조가 있다. 다른 회사의 계정·연결은 보이지 않는다."""

    if marketplace_account_id is None:
        raise SellerConnectionError(
            SELLER_CONNECTION_NOT_FOUND, "대상 판매계정이 지정되지 않았습니다.",
        )
    marketplace = MarketplaceListingRepository(db)
    account = marketplace.get_account_for_company(marketplace_account_id, company_id)
    if account is None or not account.is_active:
        raise SellerConnectionError(
            SELLER_CONNECTION_NOT_FOUND, "대상 판매계정을 찾을 수 없거나 비활성입니다.",
        )
    channel = marketplace.get_channel(account.channel_id)
    if channel is None or channel.code != COUPANG_CHANNEL_CODE:
        raise SellerConnectionError(
            SELLER_CONNECTION_NOT_FOUND, "대상 판매계정이 쿠팡 계정이 아닙니다.",
        )

    connections = (
        db.query(StoreConnection)
        .filter(
            StoreConnection.company_id == company_id,
            StoreConnection.marketplace_code == COUPANG_CHANNEL_CODE,
            StoreConnection.seller_identifier == account.account_code,
        )
        .all()
    )
    if not connections:
        raise SellerConnectionError(
            SELLER_CONNECTION_NOT_FOUND,
            "이 판매계정에 연결된 쿠팡 판매 연결이 없습니다.",
        )
    if len(connections) > 1:
        raise SellerConnectionError(
            SELLER_CONNECTION_AMBIGUOUS,
            "이 판매계정에 해당하는 쿠팡 판매 연결이 둘 이상이라 하나로 확정할 수 없습니다.",
        )
    connection = connections[0]
    if (
        connection.connection_status != ConnectionStatus.CONNECTED
        or not connection.credential_reference
    ):
        raise SellerConnectionError(
            SELLER_CONNECTION_NOT_CONNECTED,
            "이 판매계정의 쿠팡 판매 연결이 연결됨 상태가 아니거나 자격증명이 없습니다.",
        )
    return connection


def wizard_account_ids(wizard: Any) -> list[int]:
    entries = json.loads(wizard.channel_selections_json or "[]")
    return [
        entry["marketplace_account_id"] for entry in entries
        if entry.get("marketplace_account_id") is not None
    ]


def wizard_target_account_id(wizard: Any, requested_account_id: int | None = None) -> int:
    """위저드 단계의 조회(메타데이터·물류·브랜드)가 쓸 판매계정. 지정했다면 위저드가 선택한
    계정이어야 하고, 지정하지 않았다면 선택한 계정이 정확히 하나일 때만 그 계정이다."""

    accounts = wizard_account_ids(wizard)
    if requested_account_id is not None:
        if requested_account_id not in accounts:
            raise SellerConnectionError(
                SELLER_CONNECTION_NOT_FOUND,
                "지정한 판매계정이 이 상품등록에서 선택한 계정이 아닙니다.",
            )
        return requested_account_id
    if not accounts:
        raise SellerConnectionError(
            SELLER_CONNECTION_NOT_FOUND,
            "먼저 판매계정을 선택해야 쿠팡 조회를 할 수 있습니다.",
        )
    if len(set(accounts)) > 1:
        raise SellerConnectionError(
            SELLER_CONNECTION_AMBIGUOUS,
            "선택한 판매계정이 둘 이상이라 어느 계정으로 조회할지 지정해야 합니다.",
        )
    return accounts[0]


def binding_fingerprint(connection: StoreConnection) -> str:
    return hashlib.sha256(
        (connection.credential_reference or "").encode("utf-8"),
    ).hexdigest()[:16]


def seller_connection_descriptor(
    db: Session, company_id: int, marketplace_account_id: int | None,
) -> dict:
    """승인 패키지(지문)에 넣는 비밀 없는 식별 정보 — 승인 이후 연결이 바뀌거나(다른 연결,
    자격증명 교체로 credential_version 변경) 해석할 수 없게 되면 지문이 달라진다.
    예외를 던지지 않는다(해석 실패는 `store_connection_id=None`). 판매 연결 테이블이 없는
    환경(일부 격리 테스트 DB)에서도 같은 값(None)이다 — 운영 DB에는 항상 있다."""

    unresolved = {
        "marketplace_account_id": marketplace_account_id,
        "store_connection_id": None, "connection_revision": None,
        "connection_binding_fingerprint": None,
    }
    if not inspect(db.get_bind()).has_table(StoreConnection.__tablename__):
        return unresolved
    try:
        connection = resolve_seller_connection(db, company_id, marketplace_account_id)
    except SellerConnectionError:
        return unresolved
    return {
        "marketplace_account_id": marketplace_account_id,
        "store_connection_id": connection.id,
        "connection_revision": connection.credential_version,
        # 자격증명 저장소 대상명의 지문(대상명 자체·비밀값은 담지 않는다). 연결을 지우고
        # 같은 id·같은 버전으로 다시 만들거나 자격증명을 교체하면 대상명이 새로 생겨 달라진다.
        "connection_binding_fingerprint": binding_fingerprint(connection),
    }


__all__ = [
    "SellerConnectionError",
    "SELLER_CONNECTION_NOT_FOUND",
    "SELLER_CONNECTION_AMBIGUOUS",
    "SELLER_CONNECTION_NOT_CONNECTED",
    "SELLER_CONNECTION_CHANGED_AFTER_APPROVAL",
    "resolve_seller_connection",
    "wizard_account_ids",
    "wizard_target_account_id",
    "seller_connection_descriptor",
    "binding_fingerprint",
]
