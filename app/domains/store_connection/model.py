"""
=========================================================
Homez OS

File : app/domains/store_connection/model.py

판매채널 연결(StoreConnection) — SQLAlchemy Model.

이 코드베이스의 다른 모든 Domain과 동일하게 FK를 쓰지 않는다 —
company_id는 논리 참조 컬럼(정수 id)이며, 존재·활성 확인은 서비스
레벨에서 수행한다(app/domains/store_connection/service.py).

Secret 원문은 이 테이블 어디에도 없다 — credential_reference는
Windows Credential Manager의 target name일 뿐이다
(app/core/windows_credential_store.py 참고).

중복 정책: (company_id, marketplace_code, seller_identifier) 조합은
UNIQUE다 — 같은 회사가 같은 채널의 같은 판매자 계정을 두 번 연결할 수
없다(같은 채널의 다른 seller_identifier는 각각 별도 연결 허용 —
marketplace_listing의 MarketplaceAccount와 동일한 다중 계정 철학).

creation_idempotency_key는 (company_id, creation_idempotency_key)
복합 UNIQUE다(2026-08-01 CTO 재심사 이후 정책 변경 — 이전에는
creation_idempotency_key 단독 전역 UNIQUE였다). 설계 근거: 클라이언트가
생성하는 idempotency key는 회사(테넌트)마다 독립적인 값 공간이어야
한다. 전역 UNIQUE였을 때는, 서로 다른 두 회사의 클라이언트가 우연히
(또는 추측으로) 같은 key 문자열을 사용하면 두 번째 회사의 create()
호출이 UNIQUE 위반으로 실패한 뒤 "idempotent 재호출"로 오인되어
**첫 번째 회사의 연결 객체를 두 번째 회사에게 그대로 반환**하는 실제
데이터 유출 경로가 있었다 — id 기반 IDOR과 근본 원인이 같다(company_id
없는 조회는 전부 위험하다). 복합 UNIQUE로 바꾸면 같은 key라도 회사가
다르면 서로 다른 네임스페이스로 취급되어 이 경로가 원천 차단된다.
last_mutation_idempotency_key는 이미 존재하는 행에 대한 rotate/
disable/delete-credential 재호출을 같은 결과로 멱등 처리하기 위한
것이다(append-only 감사 테이블을 따로 두지 않는 대신, 각 행 자체의
last_* 컬럼이 마지막 상태를 그대로 보여준다 — 전체 이력은
app/core/audit_db.py의 audit_logs에 남긴다). 이 값은 항상
get_connection_for_company()로 이미 회사 범위가 확인된 행에 대해서만
쓰이므로 별도 UNIQUE 범위 문제가 없다.

creation_request_fingerprint(2026-08-01 CTO 2차 재심사 반영)는
이 idempotency_key로 "처음 성공했던 요청이 정확히 무엇이었는가"를
나타내는 64자 hex HMAC-SHA256 다이제스트다(app/domains/
store_connection/idempotency_fingerprint.py). 이전에는 같은
(company_id, creation_idempotency_key)의 기존 행이 있으면 지금
요청 내용과 비교 없이 그대로 반환했다 — 클라이언트가 같은 key를
다른 marketplace_code/seller_identifier/display_name/Credential로
재사용해도 오류 없이 "성공"으로 위장된 엉뚱한 기존 행을 돌려받는
조용한 데이터 불일치였다. 이제 재호출 시 저장된 fingerprint와 지금
요청의 fingerprint를 비교해, 다르면 409(ConflictException)로
명시적으로 거부한다(app/domains/store_connection/service.py::create).
=========================================================
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class StoreConnection(Base):

    __tablename__ = "store_connections"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "marketplace_code", "seller_identifier",
            name="uq_store_connections_company_marketplace_seller",
        ),
        UniqueConstraint(
            "company_id", "creation_idempotency_key",
            name="uq_store_connections_company_creation_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    marketplace_code: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    seller_identifier: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )

    # Windows Credential Manager target name — Secret 원문이 아니다.
    # 연결 생성 전(NOT_CONFIGURED) 또는 Credential 삭제 후에는 NULL.
    credential_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    masked_credential_hint: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    connection_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NOT_CONFIGURED", index=True,
    )
    credential_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    last_error_summary: Mapped[str | None] = mapped_column(
        String(300), nullable=True,
    )

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)

    creation_idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    creation_request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    last_mutation_idempotency_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "StoreConnection",
]
