"""
=========================================================
Homez OS

Company Model
=========================================================
"""

from sqlalchemy import Boolean
from sqlalchemy import Integer
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base

# Company.marketplaces = relationship("Marketplace", ...) 아래는 문자열로
# 클래스를 참조한다 — Company가 어디서 import되든 Marketplace가 항상
# 함께 로드되도록 명시적으로 import한다(marketplace/model.py는 Company를
# 직접 import하지 않으므로 순환 import 위험이 없다). 2026-07-30, Desktop
# 로그인 mapper 오류 조사 중 발견(User와 동일한 종류의 문제).
from app.domains.marketplace.model import Marketplace  # noqa: F401


class Company(Base):

    __tablename__ = "companies"

    # --------------------------------------------------
    # Primary Key
    # --------------------------------------------------

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # --------------------------------------------------
    # Company Information
    # --------------------------------------------------

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )

    business_number: Mapped[str] = mapped_column(
        String(30),
        nullable=True,
    )

    ceo: Mapped[str] = mapped_column(
        String(100),
        nullable=True,
    )

    phone: Mapped[str] = mapped_column(
        String(30),
        nullable=True,
    )

    email: Mapped[str] = mapped_column(
        String(150),
        nullable=True,
    )

    address: Mapped[str] = mapped_column(
        String(300),
        nullable=True,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # --------------------------------------------------
    # Relationship
    # --------------------------------------------------

    # 2026-07-30: users 관계는 실제 FK(users.company_id)가 있어
    # 정상 동작하도록 app/domains/user/model.py에 company_id/company를
    # 추가해 수정했다.
    users = relationship(
        "User",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    # 2026-07-30 (Desktop 로그인 인증 작업, mapper 오류 조사):
    # roles/categories/brands/suppliers/products 관계는 대응하는 각
    # Model(Role/Category/Brand/Supplier/Product)에 company_id FK나
    # company 역참조가 전혀 선언되어 있지 않아 configure_mappers() 시
    # NoForeignKeysError를 던졌다(Company.users와 동일한 근본 원인).
    # 이 오류는 SQLAlchemy 레지스트리 전체에 영향을 줘 무관한 모든
    # ORM 쿼리까지 실패시켰다 — Desktop 로그인 경로를 막던 Critical
    # 결함의 실체다.
    #
    # 이번 인증 작업 범위는 로그인에 필요한 users 관계 수정까지다.
    # roles/categories/brands/suppliers/products는 각각의 Model 자체를
    # 수정해야 하는 별개 작업이라(User 외 도메인, 이번 작업과 무관한
    # 대규모 변경) 범위 밖으로 두고 여기서는 broken 관계 선언만
    # 제거했다 — 이 필드들을 사용하는 코드가 있다면(현재 발견되지
    # 않음) 별도 승인 하에 각 Model에 FK를 추가해야 한다.

    marketplaces = relationship(
        "Marketplace",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    # --------------------------------------------------
    # Representation
    # --------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<Company("
            f"id={self.id}, "
            f"name='{self.name}'"
            f")>"
        )