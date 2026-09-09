"""
=========================================================
Homez OS

File : app/domains/supplier/model.py

Supplier Model
=========================================================
"""

from datetime import datetime

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import Boolean

from sqlalchemy.orm import relationship
from sqlalchemy.orm import validates

from app.core.exceptions import BadRequestException
from app.database.base import Base


class Supplier(Base):

    __tablename__ = "suppliers"


    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )


    name = Column(
        String(150),
        nullable=False,
        index=True,
    )


    code = Column(
        String(100),
        unique=True,
        nullable=True,
        index=True,
    )


    supplier_type = Column(
        String(50),
        nullable=False,
        default="GENERAL",
    )


    description = Column(
        Text,
        nullable=True,
    )


    website = Column(
        String(500),
        nullable=True,
    )
    api_url = Column(
        String(500),
        nullable=True,
    )


    # CA-4(2026-08-21 CTO 지시) — DEPRECATED. `suppliers`는 전역 공개
    # 공급처 디렉터리다(app/domains/source/model.py 2026-08-20 CTO
    # 정정 — 공개 식별정보만). 이 두 컬럼은 그 원칙과 충돌하는 설계
    # 잔재다. 실제 조사 결과:
    #   - 쓰기 경로: `app/domains/supplier/router.py`(이 컬럼을 받는
    #     유일한 엔드포인트)는 `app/main.py`에 마운트되지 않는다(주석
    #     처리됨) — `app/api/router.py`도 이 router를 import하지만
    #     그 파일 자체가 어디에서도 import되지 않는 죽은 코드다. 즉
    #     이 컬럼을 채우는 도달 가능한 HTTP 경로가 현재 전혀 없다.
    #   - 응답 노출: `app/domains/source/schema.py::
    #     SupplierPublicDirectoryResponse`가 이 두 필드를 절대 포함
    #     하지 않는다(response-excluded, 테스트로 검증됨).
    #   - 기존 데이터: 실제 운영·개발 DB 둘 다 suppliers 행 0건.
    # SQLite는 컬럼 DROP에 테이블 재생성이 필요해(운영 위험도 높음)
    # 이번 라운드에서 즉시 제거하지 않는다 — 완전 제거는 별도
    # rebuild Migration 계획(docs/HOMEZ_PROJECT_STATE.md CA-4 절
    # 참고)으로 미루고 사용자 승인 후 진행한다.
    api_key = Column(
        String(500),
        nullable=True,
    )


    api_secret = Column(
        String(500),
        nullable=True,
    )

    # Audit(2026-08-21, CTO 후속 지시) — 위 deprecated 컬럼은 도달
    # 가능한 쓰기 경로가 전혀 없다고 재확인됐지만, "쓰기 경로가 없다"
    # 는 사실만으로는 방어가 아니다(향후 실수로 새 경로가 생기는 것을
    # 막지 못한다). ORM 레벨에서 실제 값 대입 자체를 구조화 오류로
    # 차단한다 — None(미설정)은 그대로 허용하되, 실제 Credential
    # 문자열을 넣으려는 시도는 여기서 즉시 막는다(라우터/서비스가
    # 실수로 다시 연결되더라도 이 마지막 방어선이 남는다).
    @validates("api_key", "api_secret")
    def _reject_credential_value(self, key, value):

        if value is not None:
            raise BadRequestException(
                f"SUPPLIER_CREDENTIAL_WRITE_BLOCKED: '{key}'는 평문 "
                "저장이 금지된 deprecated 컬럼입니다 — 물리적 제거 "
                "전까지 어떤 값도 저장할 수 없습니다(ADR 0005 참고).",
            )

        return value


    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )


    is_verified = Column(
        Boolean,
        nullable=False,
        default=False,
    )


    trust_score = Column(
        Float,
        nullable=False,
        default=0,
    )
    delivery_score = Column(
        Float,
        nullable=False,
        default=0,
    )


    return_rate = Column(
        Float,
        nullable=False,
        default=0,
    )


    order_count = Column(
        Integer,
        nullable=False,
        default=0,
    )


    success_count = Column(
        Integer,
        nullable=False,
        default=0,
    )


    ai_score = Column(
        Float,
        nullable=False,
        default=0,
    )


    search_keywords = Column(
        Text,
        nullable=True,
    )  
    metadata_json = Column(
        Text,
        nullable=True,
    )


    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=True,
    )


    deleted_at = Column(
        DateTime,
        nullable=True,
    )


    products = relationship(
        "Product",
        back_populates="supplier",
    )


    inventories = relationship(
        "Inventory",
        back_populates="supplier",
    )


    def __repr__(
        self,
    ) -> str:

        return (
            f"Supplier("
            f"id={self.id}, "
            f"name='{self.name}', "
            f"active={self.is_active}"
            f")"
        )


__all__ = [
    "Supplier",
]
