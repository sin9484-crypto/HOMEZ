"""
=========================================================
Homez OS

File : app/domains/store_connection/service.py

판매채널 연결 — 연결 테스트와 저장을 분리한다.

흐름(생성): 서버 형식 검증 → verification_token 재확인(fingerprint
불일치 시 차단) → 자연키 중복 확인 → fixture 재검증(신뢰 경계를
넘기 전에 서비스가 직접 한 번 더 확인) → 성공 시 Credential Manager에
먼저 저장 → DB 저장(실패하면 방금 저장한 Credential을 보상 삭제) →
감사 로그.

Secret 원문은 요청 처리 중 메모리에서만 존재하고 어떤 DB row·로그·
audit_log·예외 메시지에도 남지 않는다.

2026-08-01 CTO 재심사 반영 — 회사 소유권 격리(Critical/High): 이
파일의 단건 조회·변경 메서드(get/verify_existing/rotate_credential/
disable/delete_credential)는 전부 company_id를 필수 인자로 받는다.
내부적으로 항상 `repository.get_connection_for_company(id, company_id)`
만 사용하며, id만으로 조회하는 경로는 없다. 다른 회사의 connection_id
로 접근하면 "존재하지 않는 것"과 완전히 동일하게 NotFoundException
(404)을 던진다 — 403이나 상세 메시지로 타사 객체의 존재 자체를
알려주지 않는다(요청 그대로 반영: "다른 회사 레코드는 존재 여부를
노출하지 않도록 404로 처리하라").

2026-08-01 CTO 2차 재심사 반영 — idempotency 요청 본문 fingerprint:
create()는 이제 credential_fields를 먼저 검증한 뒤 이번 요청의
fingerprint(company_id/marketplace_code/seller_identifier/
display_name/credential fingerprint)를 계산하고, 같은 idempotency_
key의 기존 행이 있으면 그 fingerprint와 비교한다. 같으면 진짜
재호출(replay)로 보고 기존 행을 그대로 반환하고, 다르면 같은 key가
다른 내용으로 재사용된 것이므로 ConflictException(409)으로 명시적
거부한다(app/domains/store_connection/idempotency_fingerprint.py).
IntegrityError 경쟁 승자 복구 경로(_insert_connection)에서도 winner의
fingerprint를 동일하게 재검사한다 — 다르면 winner를 반환하지 않고
409를 던진다.
=========================================================
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ServiceUnavailableException,
)
from app.core.logger import logger
from app.core.windows_credential_store import (
    CredentialNotFoundError,
    CredentialStore,
    CredentialStoreError,
)
from app.domains.company.model import Company
from app.domains.store_connection.adapters import get_adapter
from app.domains.store_connection.constants import ConnectionErrorCode
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.idempotency_fingerprint import (
    compute_creation_request_fingerprint,
)
from app.domains.store_connection.model import StoreConnection
from app.domains.store_connection.repository import StoreConnectionRepository
from app.domains.store_connection.schema import (
    StoreConnectionCreateRequest,
    StoreConnectionDeleteCredentialRequest,
    StoreConnectionDisableRequest,
    StoreConnectionRotateCredentialRequest,
    StoreConnectionVerifyRequest,
    StoreConnectionVerifyResponse,
    get_credential_fields_schema,
)
from app.domains.store_connection.verification_token import (
    InvalidVerificationTokenError,
    issue_token,
    verify_token,
)
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)

_IDENTIFYING_FIELD_BY_MARKETPLACE = {
    "COUPANG": "vendor_id",
    "NAVER_SMARTSTORE": "client_id",
}

_NOT_FOUND_MESSAGE = "StoreConnection을 찾을 수 없습니다."


def _credential_namespace() -> str:
    """
    2026-08-24 패키징 격리 재작업 — Windows Credential Manager의
    target_name 접두사. `HOMEZ_DATA_ROOT`(app/desktop/paths.py의
    격리 테스트 신호와 동일한 환경변수)가 설정된 동안에만 접두사를
    "HOMEZ_TEST"로 바꿔, 격리 테스트가 실제 운영 Credential Manager
    항목과 절대 같은 이름 규칙을 쓰지 않게 한다. 이 환경변수가
    없는 일반 운영 경로에서는 항상 기존 "HOMEZ" 접두사 그대로다 —
    동작이 전혀 바뀌지 않는다.
    """

    import os

    if os.environ.get("HOMEZ_DATA_ROOT", "").strip():
        return "HOMEZ_TEST"

    return "HOMEZ"


def _mask_identifier(value: str) -> str:

    if not value:
        return "••••"

    tail = value[-4:] if len(value) > 4 else value

    return f"••••{tail}"


class StoreConnectionService:

    def __init__(self, db: Session, credential_store: CredentialStore):

        self.db = db
        self.repository = StoreConnectionRepository(db)
        self.credential_store = credential_store

    # --------------------------------------------------
    # 조회 — 항상 company_id 범위 안에서만
    # --------------------------------------------------

    def get(self, connection_id: int, company_id: int) -> StoreConnection:

        connection = self.repository.get_connection_for_company(
            connection_id, company_id,
        )
        if connection is None:
            # 존재하지 않음 / 다른 회사 소유 — 둘 다 동일하게 404.
            raise NotFoundException(_NOT_FOUND_MESSAGE)

        return connection

    def list_for_company(self, company_id: int) -> list[StoreConnection]:

        return self.repository.list_connections_for_company(company_id)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _validate_credential_fields(
        self, marketplace_code: str, credential_fields: dict,
    ) -> dict:

        schema_cls = get_credential_fields_schema(marketplace_code)
        if schema_cls is None:
            raise BadRequestException("지원하지 않는 판매채널입니다.")

        try:
            validated = schema_cls.model_validate(credential_fields)
        except ValidationError as exc:
            raise BadRequestException(
                f"자격증명 형식이 올바르지 않습니다: {exc.error_count()}건의 "
                "검증 오류.",
            ) from exc

        return validated.model_dump(mode="json")

    def _require_company(self, company_id: int) -> Company:
        """
        company_id는 논리 참조다(FK 없음) — 존재하지 않는 회사에는
        연결을 생성할 수 없다(orphan 방지).
        """

        company = (
            self.db.query(Company).filter(Company.id == company_id).first()
        )
        if company is None:
            raise BadRequestException("유효하지 않은 company_id입니다.")

        return company

    # --------------------------------------------------
    # 1) 연결 테스트(저장과 분리, 아무것도 저장하지 않음)
    # --------------------------------------------------

    def verify_new(
        self, data: StoreConnectionVerifyRequest, company_id: int,
    ) -> StoreConnectionVerifyResponse:

        credential_dict = self._validate_credential_fields(
            data.marketplace_code, data.credential_fields,
        )

        adapter = get_adapter(data.marketplace_code)
        result = adapter.verify_connection(credential_dict)

        identifying_field = _IDENTIFYING_FIELD_BY_MARKETPLACE[
            data.marketplace_code
        ]
        masked_hint = _mask_identifier(credential_dict.get(identifying_field, ""))

        if not result.success:
            return StoreConnectionVerifyResponse(
                success=False, verification_token=None, token_expires_at=None,
                masked_credential_hint=masked_hint,
                error_code=result.error_code, error_summary=result.error_summary,
                expiration_status=result.expiration_status,
            )

        token, expires_at = issue_token(
            company_id, data.marketplace_code, data.seller_identifier,
            credential_dict,
        )

        return StoreConnectionVerifyResponse(
            success=True, verification_token=token, token_expires_at=expires_at,
            masked_credential_hint=masked_hint, error_code=None,
            error_summary=None, expiration_status=result.expiration_status,
        )

    # --------------------------------------------------
    # 2) 연결 생성(저장) — verification_token 필수
    # --------------------------------------------------

    def create(
        self, data: StoreConnectionCreateRequest, company_id: int,
        created_by: int,
    ) -> tuple[StoreConnection, bool]:

        # fingerprint 계산에 credential_dict(canonical화된 필드)가
        # 필요하므로, idempotency 재호출 판단보다 먼저 형식을 검증한다.
        credential_dict = self._validate_credential_fields(
            data.marketplace_code, data.credential_fields,
        )
        request_fingerprint = compute_creation_request_fingerprint(
            company_id, data.marketplace_code, data.seller_identifier,
            data.display_name, credential_dict,
        )

        existing = self.repository.get_connection_by_creation_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            if existing.creation_request_fingerprint == request_fingerprint:
                return existing, True
            raise ConflictException(
                "동일한 idempotency key가 이전과 다른 요청 내용으로 "
                "이미 사용되었습니다 — 새로운 idempotency key를 사용하세요.",
            )

        self._require_company(company_id)

        try:
            verify_token(
                data.verification_token, company_id, data.marketplace_code,
                data.seller_identifier, credential_dict,
            )
        except InvalidVerificationTokenError as exc:
            raise BadRequestException(str(exc)) from exc

        duplicate = self.repository.get_connection_by_natural_key(
            company_id, data.marketplace_code, data.seller_identifier,
        )
        if duplicate is not None:
            raise ConflictException(
                "이미 등록된 연결입니다 — 자격증명 교체(rotate-credential)를 "
                "사용하세요.",
            )

        # High: verification_token은 "값이 안 바뀌었다"는 증표일 뿐,
        # 저장 시점의 실제 유효성은 서비스가 다시 fixture로 확인한다
        # (토큰 발급 이후 fixture 상태가 바뀌었을 가능성에 대한
        # defense-in-depth).
        adapter = get_adapter(data.marketplace_code)
        result = adapter.verify_connection(credential_dict)

        identifying_field = _IDENTIFYING_FIELD_BY_MARKETPLACE[
            data.marketplace_code
        ]
        masked_hint = _mask_identifier(credential_dict.get(identifying_field, ""))
        now = datetime.utcnow()

        if not result.success:
            connection = StoreConnection(
                company_id=company_id, marketplace_code=data.marketplace_code,
                display_name=data.display_name,
                seller_identifier=data.seller_identifier,
                credential_reference=None, masked_credential_hint=masked_hint,
                connection_status=ConnectionStatus.ERROR, credential_version=0,
                expires_at=result.expires_at, last_verified_at=now,
                last_success_at=None, last_error_code=result.error_code,
                last_error_summary=result.error_summary,
                created_by=created_by,
                creation_idempotency_key=data.idempotency_key,
                creation_request_fingerprint=request_fingerprint,
            )

            return self._insert_connection(
                connection, company_id, data.idempotency_key,
                request_fingerprint,
            )

        target_name = f"{_credential_namespace()}:store_connection:{uuid.uuid4()}"

        try:
            self.credential_store.save(target_name, credential_dict)
        except CredentialStoreError as exc:
            from app.domains.platform_alert.hooks import (
                notify_credential_store_error,
            )

            notify_credential_store_error(
                self.db, detail=f"save() 실패: {type(exc).__name__}: {exc}",
                entity_ref=f"company:{company_id}",
                idempotency_key=f"credential_store_error:save:{target_name}",
            )
            raise ServiceUnavailableException(
                "자격증명 보안 저장소를 사용할 수 없어 저장을 차단했습니다.",
            ) from exc

        connection = StoreConnection(
            company_id=company_id, marketplace_code=data.marketplace_code,
            display_name=data.display_name,
            seller_identifier=data.seller_identifier,
            credential_reference=target_name,
            masked_credential_hint=masked_hint,
            connection_status=ConnectionStatus.CONNECTED, credential_version=1,
            expires_at=result.expires_at, last_verified_at=now,
            last_success_at=now, last_error_code=None, last_error_summary=None,
            created_by=created_by, creation_idempotency_key=data.idempotency_key,
            creation_request_fingerprint=request_fingerprint,
        )

        try:
            return self._insert_connection(
                connection, company_id, data.idempotency_key,
                request_fingerprint,
            )

        except Exception:
            # DB 저장 실패 — 방금 저장한 Credential을 보상 삭제한다.
            try:
                self.credential_store.delete(target_name)
            except CredentialStoreError:
                logger.error(
                    "store_connection: DB 저장 실패 이후 Credential 보상 "
                    "삭제도 실패했습니다(target 식별자만 기록, Secret "
                    "미포함).",
                )
            raise

    def _insert_connection(
        self, connection: StoreConnection, company_id: int,
        idempotency_key: str, request_fingerprint: str,
    ) -> tuple[StoreConnection, bool]:
        """
        IntegrityError 경쟁 승자 복구 경로도 winner의
        creation_request_fingerprint를 지금 이 요청의 request_fingerprint와
        비교한다 — 같으면(진짜 동시에 들어온 동일 요청 중 하나가
        이겼을 뿐) winner를 그대로 반환하지만, 다르면(우연히 같은
        idempotency_key를 쓴 다른 내용의 요청) winner를 반환하지 않고
        409로 거부한다.
        """

        try:
            connection = self.repository.add_connection_no_commit(connection)
            write_audit_log(
                self.db, user_id=connection.created_by,
                action="STORE_CONNECTION_CREATED",
                entity="StoreConnection", entity_id=str(connection.id),
                company_id=connection.company_id,
                description=(
                    f"{connection.marketplace_code} 연결 생성 "
                    f"(status={connection.connection_status})"
                ),
            )
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_connection_by_creation_idempotency_key(
                company_id, idempotency_key,
            )
            if winner is not None:
                if winner.creation_request_fingerprint == request_fingerprint:
                    return winner, True
                raise ConflictException(
                    "동일한 idempotency key가 이전과 다른 요청 내용으로 "
                    "이미 사용되었습니다 — 새로운 idempotency key를 "
                    "사용하세요.",
                ) from None

            raise ConflictException(
                "이미 등록된 연결과 충돌합니다 — 다시 시도하세요.",
            ) from None

        return connection, False

    # --------------------------------------------------
    # 3) 기존 연결 재검증 — 서버가 Credential Manager에서 직접 조회
    # --------------------------------------------------

    def verify_existing(
        self, connection_id: int, company_id: int,
        actor_user_id: int | None = None,
    ) -> StoreConnectionVerifyResponse:

        connection = self.get(connection_id, company_id)

        if connection.credential_reference is None:
            raise BadRequestException(
                "저장된 자격증명이 없습니다 — 먼저 연결을 생성하세요.",
            )

        try:
            credential_dict = self.credential_store.read(
                connection.credential_reference,
            )
        except CredentialNotFoundError as exc:
            raise ServiceUnavailableException(
                "저장된 자격증명을 보안 저장소에서 찾을 수 없습니다.",
            ) from exc
        except CredentialStoreError as exc:
            from app.domains.platform_alert.hooks import (
                notify_credential_store_error,
            )

            notify_credential_store_error(
                self.db, detail=f"read() 실패: {type(exc).__name__}: {exc}",
                entity_ref=f"store_connection:{connection_id}",
                idempotency_key=(
                    f"credential_store_error:read:{connection.credential_reference}"
                ),
            )
            raise ServiceUnavailableException(
                "자격증명 보안 저장소를 사용할 수 없습니다.",
            ) from exc

        adapter = get_adapter(connection.marketplace_code)
        result = adapter.verify_connection(credential_dict)

        now = datetime.utcnow()
        expected_version = connection.credential_version

        if result.success:
            rowcount = self.repository.apply_verification_success_conditional(
                connection.id, company_id,
                expected_credential_version=expected_version,
                credential_reference=connection.credential_reference,
                masked_credential_hint=connection.masked_credential_hint,
                expires_at=result.expires_at, now=now,
                mutation_idempotency_key=f"verify-existing-{uuid.uuid4()}",
            )
        else:
            rowcount = self.repository.apply_verification_failure_conditional(
                connection.id, company_id,
                expected_credential_version=expected_version,
                error_code=result.error_code,
                error_summary=result.error_summary, now=now,
                mutation_idempotency_key=f"verify-existing-{uuid.uuid4()}",
            )

        # 2026-08-04 V6 Gate 2 재보완: 이전에는 이 조건부 UPDATE의
        # rowcount를 확인하지 않아, 조회(self.get)와 UPDATE 사이에
        # 동시 rotate_credential 등이 credential_version을 먼저 바꾸면
        # 이 UPDATE가 조용히 0행에 적용되는데도(WHERE 절이 더 이상
        # 맞지 않으므로) 클라이언트에는 마치 실제로 반영된 것처럼
        # success/failure 응답을 그대로 돌려주는 결함이 있었다 —
        # rotate_credential()과 동일하게 rowcount!=1이면 409로 명시
        # 거부한다(데이터 손상은 이미 WHERE 절이 막고 있었지만, 클라이언트가
        # "저장되지 않은 검증 결과"를 성공으로 오인할 수 있었다).
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "다른 요청이 먼저 이 연결을 변경했습니다 — 새로고침 후 "
                "다시 시도하세요.",
            )

        self.db.commit()

        if (
            not result.success
            and result.error_code in (
                ConnectionErrorCode.CREDENTIAL_EXPIRED,
                ConnectionErrorCode.UNAUTHORIZED,
            )
        ):
            dispatch_operational_event(
                self.db, "CHANNEL_CREDENTIAL_EXPIRED",
                company_id=company_id, user_id=actor_user_id,
                idempotency_key=(
                    f"channel-credential:{connection.id}:{expected_version}:"
                    f"{result.error_code}"
                ),
                title="판매채널 재인증이 필요합니다",
                message=f"판매채널 연결 #{connection.id}의 인증을 확인하세요.",
                link_path="store-connections",
                entity_ref=f"store_connection:{connection.id}",
                reason=result.error_summary or result.error_code,
                entity_summary=f"판매채널 연결 #{connection.id}",
            )

        return StoreConnectionVerifyResponse(
            success=result.success, verification_token=None,
            token_expires_at=None,
            masked_credential_hint=connection.masked_credential_hint,
            error_code=result.error_code, error_summary=result.error_summary,
            expiration_status=result.expiration_status,
        )

    # --------------------------------------------------
    # 4) 자격증명 교체(rotate) — 새 target으로 저장 후 원자적 전환
    # --------------------------------------------------

    def rotate_credential(
        self, connection_id: int, company_id: int,
        data: StoreConnectionRotateCredentialRequest,
    ) -> StoreConnection:

        connection = self.get(connection_id, company_id)

        credential_dict = self._validate_credential_fields(
            connection.marketplace_code, data.credential_fields,
        )

        try:
            verify_token(
                data.verification_token, company_id,
                connection.marketplace_code, connection.seller_identifier,
                credential_dict,
            )
        except InvalidVerificationTokenError as exc:
            raise BadRequestException(str(exc)) from exc

        adapter = get_adapter(connection.marketplace_code)
        result = adapter.verify_connection(credential_dict)

        now = datetime.utcnow()
        expected_version = connection.credential_version

        if not result.success:
            rowcount = self.repository.apply_verification_failure_conditional(
                connection.id, company_id,
                expected_credential_version=expected_version,
                error_code=result.error_code,
                error_summary=result.error_summary, now=now,
                mutation_idempotency_key=data.idempotency_key,
            )
            if rowcount != 1:
                self.db.rollback()
                raise ConflictException(
                    "다른 요청이 먼저 이 연결을 변경했습니다 — 새로고침 후 "
                    "다시 시도하세요.",
                )
            self.db.commit()
            return self.get(connection_id, company_id)

        identifying_field = _IDENTIFYING_FIELD_BY_MARKETPLACE[
            connection.marketplace_code
        ]
        masked_hint = _mask_identifier(credential_dict.get(identifying_field, ""))
        new_target_name = f"{_credential_namespace()}:store_connection:{uuid.uuid4()}"
        old_target_name = connection.credential_reference

        try:
            self.credential_store.save(new_target_name, credential_dict)
        except CredentialStoreError as exc:
            from app.domains.platform_alert.hooks import (
                notify_credential_store_error,
            )

            notify_credential_store_error(
                self.db, detail=f"rotate save() 실패: {type(exc).__name__}: {exc}",
                entity_ref=f"store_connection:{connection_id}",
                idempotency_key=f"credential_store_error:rotate:{new_target_name}",
            )
            raise ServiceUnavailableException(
                "자격증명 보안 저장소를 사용할 수 없어 교체를 차단했습니다.",
            ) from exc

        try:
            rowcount = self.repository.apply_verification_success_conditional(
                connection.id, company_id,
                expected_credential_version=expected_version,
                credential_reference=new_target_name,
                masked_credential_hint=masked_hint,
                expires_at=result.expires_at, now=now,
                mutation_idempotency_key=data.idempotency_key,
            )

            if rowcount != 1:
                raise ConflictException(
                    "다른 요청이 먼저 이 연결을 변경했습니다 — 새로고침 후 "
                    "다시 시도하세요.",
                )

            write_audit_log(
                self.db, user_id=connection.created_by,
                action="STORE_CONNECTION_CREDENTIAL_ROTATED",
                entity="StoreConnection", entity_id=str(connection.id),
                description=f"{connection.marketplace_code} 자격증명 교체",
                company_id=connection.company_id,
            )
            self.db.commit()

        except Exception:
            self.db.rollback()
            try:
                self.credential_store.delete(new_target_name)
            except CredentialStoreError:
                logger.error(
                    "store_connection: rotate 실패 이후 신규 Credential "
                    "보상 삭제도 실패했습니다.",
                )
            raise

        if old_target_name is not None:
            try:
                self.credential_store.delete(old_target_name)
            except CredentialStoreError:
                logger.warning(
                    "store_connection: rotate 성공 이후 이전 Credential "
                    "정리에 실패했습니다(신규 Credential은 정상 동작).",
                )

        return self.get(connection_id, company_id)

    # --------------------------------------------------
    # 5) 비활성화 — Credential은 그대로 둔다(삭제와 별개)
    # --------------------------------------------------

    def disable(
        self, connection_id: int, company_id: int,
        data: StoreConnectionDisableRequest,
    ) -> StoreConnection:

        connection = self.get(connection_id, company_id)

        rowcount = self.repository.disable_conditional(
            connection.id, company_id, now=datetime.utcnow(),
            mutation_idempotency_key=data.idempotency_key,
        )

        if rowcount == 1:
            write_audit_log(
                self.db, user_id=connection.created_by,
                action="STORE_CONNECTION_DISABLED",
                entity="StoreConnection", entity_id=str(connection.id),
                description=f"{connection.marketplace_code} 연결 비활성화",
                company_id=connection.company_id,
            )

        self.db.commit()

        return self.get(connection_id, company_id)

    # --------------------------------------------------
    # 6) Credential 삭제 — 명시적 확인 필수(비활성화보다 위험한 작업)
    # --------------------------------------------------

    def delete_credential(
        self, connection_id: int, company_id: int,
        data: StoreConnectionDeleteCredentialRequest,
    ) -> StoreConnection:

        if not data.confirm:
            raise BadRequestException(
                "Credential 삭제는 명시적 확인(confirm=true)이 필요합니다.",
            )

        connection = self.get(connection_id, company_id)
        target_name = connection.credential_reference

        rowcount = self.repository.delete_credential_conditional(
            connection.id, company_id, now=datetime.utcnow(),
            mutation_idempotency_key=data.idempotency_key,
        )

        if rowcount == 1:
            write_audit_log(
                self.db, user_id=connection.created_by,
                action="STORE_CONNECTION_CREDENTIAL_DELETED",
                entity="StoreConnection", entity_id=str(connection.id),
                description=f"{connection.marketplace_code} 자격증명 삭제",
                company_id=connection.company_id,
            )

        self.db.commit()

        if target_name is not None:
            try:
                self.credential_store.delete(target_name)
            except CredentialStoreError:
                logger.error(
                    "store_connection: DB에서는 Credential 참조를 지웠지만 "
                    "실제 보안 저장소 삭제에 실패했습니다 — 수동 정리 필요"
                    "(target 식별자만 기록, Secret 미포함).",
                )

        return self.get(connection_id, company_id)


__all__ = [
    "StoreConnectionService",
]
