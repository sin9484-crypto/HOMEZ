"""
=========================================================
Homez OS

File : app/domains/store_connection/repository.py

쓰기 메서드는 commit하지 않고 flush만 수행한다(*_no_commit).
Transaction 경계(commit/rollback)는 Service가 소유한다
(app/domains/marketplace_listing/repository.py와 동일 패턴).

상태 전이는 전부 조건부 UPDATE + rowcount로 수행한다 — 낙관적
동시성(credential_version 일치 확인)으로 동시 rotate 경쟁을 감지한다.

2026-08-01 CTO 재심사 반영 — 회사 소유권 격리(Critical/High): 단건
조회(get_connection_for_company)와 모든 조건부 UPDATE가
company_id 조건을 반드시 포함한다. "조회만 회사 범위로 제한하고
실제 UPDATE는 id만 사용"하는 절반짜리 수정을 명시적으로 금지받았다
— 이 파일의 모든 WHERE 절이 company_id를 함께 검사한다. 다른
회사의 connection_id를 넣으면 조회든 UPDATE든 항상 아무 일도
일어나지 않는다(None 또는 rowcount=0) — Service 계층이 이를 404로
변환한다(403이나 상세 메시지로 타사 객체의 존재를 알려주지 않는다).

creation_idempotency_key 조회도 company_id로 범위를 제한한다 —
전역 UNIQUE였던 이전 설계는 idempotency key 자체가 회사 간에 우연히
겹치면(혹은 추측하면) 타사 연결 정보를 이 조회 경로로 그대로
반환하는 잠재적 정보 노출 경로였다(id 노출은 아니지만 동일한 근본
문제 — company_id 없는 조회는 전부 위험하다는 것이 이번 재심사의
핵심 지적).
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection


class StoreConnectionRepository:

    def __init__(self, db: Session):

        self.db = db

    def add_connection_no_commit(
        self, connection: StoreConnection,
    ) -> StoreConnection:

        self.db.add(connection)
        self.db.flush()

        return connection

    def get_connection_for_company(
        self, connection_id: int, company_id: int,
    ) -> StoreConnection | None:
        """
        모든 단건 조회의 유일한 진입점이어야 한다 — company_id 없이
        id만으로 조회하는 메서드는 이 파일에 존재하지 않는다(2026-08-01
        재심사에서 지적된 회사 간 IDOR을 구조적으로 막기 위함).
        """

        return (
            self.db.query(StoreConnection)
            .filter(StoreConnection.id == connection_id)
            .filter(StoreConnection.company_id == company_id)
            .first()
        )

    def get_connection_by_natural_key(
        self, company_id: int, marketplace_code: str, seller_identifier: str,
    ) -> StoreConnection | None:

        return (
            self.db.query(StoreConnection)
            .filter(StoreConnection.company_id == company_id)
            .filter(StoreConnection.marketplace_code == marketplace_code)
            .filter(StoreConnection.seller_identifier == seller_identifier)
            .first()
        )

    def get_connection_by_creation_idempotency_key(
        self, company_id: int, key: str,
    ) -> StoreConnection | None:
        """
        creation_idempotency_key는 (company_id, key) 복합 UNIQUE다
        (2026-08-01 정책 변경 — 이전 전역 UNIQUE는 회사 간 idempotency
        namespace 충돌/추측 위험이 있었다). 조회도 반드시 같은 범위를
        써야 한다 — company_id 없이 key만으로 조회하면 다른 회사가
        우연히·의도적으로 같은 key를 쓴 행을 반환할 수 있다.
        """

        return (
            self.db.query(StoreConnection)
            .filter(StoreConnection.company_id == company_id)
            .filter(StoreConnection.creation_idempotency_key == key)
            .first()
        )

    def list_connections_for_company(
        self, company_id: int,
    ) -> list[StoreConnection]:

        return (
            self.db.query(StoreConnection)
            .filter(StoreConnection.company_id == company_id)
            .order_by(StoreConnection.id.asc())
            .all()
        )

    # --------------------------------------------------
    # 상태 전이 — 전부 조건부 UPDATE + rowcount + company_id
    # --------------------------------------------------

    def apply_verification_success_conditional(
        self, connection_id: int, company_id: int, *,
        expected_credential_version: int, credential_reference: str,
        masked_credential_hint: str, expires_at: datetime | None,
        now: datetime, mutation_idempotency_key: str,
    ) -> int:
        """
        rotate_credential 성공 경로 — credential_version이 기대값과
        정확히 같고, company_id도 일치할 때만 전이한다(낙관적
        동시성 + 회사 소유권 이중 확인).
        """

        stmt = (
            update(StoreConnection)
            .where(StoreConnection.id == connection_id)
            .where(StoreConnection.company_id == company_id)
            .where(
                StoreConnection.credential_version
                == expected_credential_version,
            )
            .values(
                connection_status=ConnectionStatus.CONNECTED,
                credential_reference=credential_reference,
                masked_credential_hint=masked_credential_hint,
                credential_version=expected_credential_version + 1,
                expires_at=expires_at,
                last_verified_at=now,
                last_success_at=now,
                last_error_code=None,
                last_error_summary=None,
                last_mutation_idempotency_key=mutation_idempotency_key,
                updated_at=now,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def apply_verification_failure_conditional(
        self, connection_id: int, company_id: int, *,
        expected_credential_version: int, error_code: str,
        error_summary: str, now: datetime, mutation_idempotency_key: str,
    ) -> int:
        """
        fixture 재검증 실패 — 기존 credential_reference/version은 그대로
        둔다(이전에 연결된 적이 있었다면 그 정보를 잃지 않는다).
        """

        stmt = (
            update(StoreConnection)
            .where(StoreConnection.id == connection_id)
            .where(StoreConnection.company_id == company_id)
            .where(
                StoreConnection.credential_version
                == expected_credential_version,
            )
            .values(
                connection_status=ConnectionStatus.ERROR,
                last_verified_at=now,
                last_error_code=error_code,
                last_error_summary=error_summary,
                last_mutation_idempotency_key=mutation_idempotency_key,
                updated_at=now,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def disable_conditional(
        self, connection_id: int, company_id: int, *, now: datetime,
        mutation_idempotency_key: str,
    ) -> int:
        """
        2026-08-04 V6 Gate 2B 재보완: credential_version도 함께
        올린다("펜싱") — 그렇지 않으면 이 UPDATE와 동시에 진행 중이던
        verify_existing()/rotate_credential()이(둘 다 credential_version
        조건부 UPDATE만 쓰고 connection_status는 검사하지 않는다) 아직
        예전 버전을 들고 있어 그대로 통과해버리고, 그 SET 절이
        connection_status를 CONNECTED로 무조건 되돌려 방금 한 비활성화를
        조용히 무효화할 수 있었다. 버전을 올리면 그 경쟁 요청의 WHERE
        절이 더 이상 맞지 않아 rowcount=0 → 409로 명시 거부된다(이미
        고친 verify_existing/rotate_credential의 rowcount 검사 덕분에
        안전하게 막힌다).
        """

        stmt = (
            update(StoreConnection)
            .where(StoreConnection.id == connection_id)
            .where(StoreConnection.company_id == company_id)
            .where(StoreConnection.connection_status != ConnectionStatus.DISABLED)
            .values(
                connection_status=ConnectionStatus.DISABLED,
                credential_version=StoreConnection.credential_version + 1,
                last_mutation_idempotency_key=mutation_idempotency_key,
                updated_at=now,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def delete_credential_conditional(
        self, connection_id: int, company_id: int, *, now: datetime,
        mutation_idempotency_key: str,
    ) -> int:
        """
        Credential 삭제(연결 비활성화와 별개) — credential_reference가
        이미 없으면(NULL) 대상이 없으므로 rowcount=0.

        2026-08-04 V6 Gate 2B 재보완: disable_conditional과 동일한
        이유로 credential_version도 함께 올린다 — 그렇지 않으면 동시에
        진행 중이던 verify_existing()의 SET 절(credential_reference=
        <삭제 전 읽은 값>)이 통과해, 이미 Windows Credential Manager
        에서 실제로 지워진 target name을 DB에 다시 써넣어(가리키는
        실체가 없는) 불일치 상태를 만들 수 있었다.
        """

        stmt = (
            update(StoreConnection)
            .where(StoreConnection.id == connection_id)
            .where(StoreConnection.company_id == company_id)
            .where(StoreConnection.credential_reference.is_not(None))
            .values(
                credential_reference=None,
                masked_credential_hint=None,
                connection_status=ConnectionStatus.NOT_CONFIGURED,
                credential_version=StoreConnection.credential_version + 1,
                last_mutation_idempotency_key=mutation_idempotency_key,
                updated_at=now,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount


__all__ = [
    "StoreConnectionRepository",
]
