"""
=========================================================
Homez OS

Marketplace Model
Version : 2.0.0
=========================================================
"""

from sqlalchemy import Boolean
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class Marketplace(Base):

    __tablename__ = "marketplaces"

    # --------------------------------------------------
    # Primary Key
    # --------------------------------------------------

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # --------------------------------------------------
    # Foreign Key
    # --------------------------------------------------

    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"),
        nullable=True,
    )

    # --------------------------------------------------
    # Marketplace Information
    # --------------------------------------------------

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )

    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    api_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    api_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    api_secret: Mapped[str | None] = mapped_column(
        String(500),
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

    company = relationship(
        "Company",
        back_populates="marketplaces",
        lazy="joined",
    )

    # --------------------------------------------------
    # Representation
    # --------------------------------------------------

    def __repr__(self):

        return (
            f"<Marketplace("
            f"id={self.id}, "
            f"name='{self.name}'"
            f")>"
        )

# 2026-10-30 시험 격리 결함 수정 — inventory/model.py 끝의 설명과 같은 이유다. 위 클래스의 relationship("Company")는
# 공유 Base 레지스트리에서 이름으로 늦게(첫 mapper configure 때) 해석되므로, 이 모듈만 import된 프로세스(예: 같은 패키지의
# 다른 모듈을 불러온 시험)에서 첫 조회가 일어나면 매퍼 설정이 실패해 그 프로세스의 뒤 시험이 연쇄로 깨졌다(2026-10-29 회귀:
# 같은 묶음에서 오류 72~196건, 모듈 단독은 통과). 이 모듈이 참조 대상을 항상 함께 등록하도록 여기서 import한다.
# 순환 import를 피하려고 반드시 클래스 정의 '뒤'에 둔다.
# company/model.py가 맨 위에서 이 모듈을 import하는 순환이 있어, 이름이 아니라 모듈을 import한다(순환 중에도 이름을 요구하지 않는다).
import app.domains.company.model  # noqa: F401,E402
