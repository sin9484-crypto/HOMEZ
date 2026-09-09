from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import object_session
from sqlalchemy.orm import relationship

from app.database.base import Base

# User.company = relationship("Company", ...) 아래는 문자열로 클래스를
# 참조한다. SQLAlchemy 레지스트리가 이 이름을 찾으려면 Company 클래스가
# 프로세스 안에 실제로 import되어 있어야 한다 — 예를 들어
# app/domains/settlement/__init__.py가 router.py를 통해 User만 import하고
# Company는 전혀 건드리지 않는 것처럼, User를 참조하는 곳이 항상 Company를
# 같이 import한다는 보장이 없다(2026-07-30, Desktop 로그인 mapper 오류
# 조사 중 발견). User가 어디서 import되든 Company가 항상 함께 로드되도록
# 여기서 명시적으로 import한다(company/model.py는 User를 직접 import하지
# 않으므로 순환 import 위험이 없다).
from app.domains.company.model import Company  # noqa: F401


class User(Base):
    """
    실제 homez.db의 `users` 테이블과 정확히 일치하도록 유지한다
    (2026-07-30 확인: 당시 컬럼은 id/company_id/role_id/username/
    email/password/name/phone/active뿐이었다 — 이전 모델 정의는
    `created_at`, `updated_at`, `last_login_at`, `nickname`,
    `is_verified`, `is_staff`, `login_count`, `failed_login_count`
    등 실제 DB에 없는 컬럼을 다수 선언하고 있어 단순 SELECT조차
    `OperationalError: no such column`으로 실패했다 — Desktop 로그인
    흐름을 막던 근본 원인 중 하나였다). 이 사고 이후 이 Model에 새
    컬럼을 추가할 때는 반드시 대응하는 Migration을 먼저 만들고
    임시 DB에서 검증한 뒤에만 여기에 선언을 추가한다.

    2026-09-09 Phase 1(로그인 무차별 대입 방어) — 위에서 "당시 없다"고
    했던 `failed_login_count`를 이번에 정식 Migration
    (`migrations/20260909_00_add_login_lockout_columns.sql`, 아직 실제
    homez.db에는 미적용, 별도 승인 대상)과 함께 실제로 추가했다.
    `locked_until`/`last_failed_login_at`도 같은 Migration의 신규
    컬럼이다 — 이번엔 Migration 없이 먼저 선언한 게 아니라 Migration이
    먼저다.

    `password_hash`/`is_active`는 기존 코드 전반(app/domains/auth/**,
    app/core/**)이 이 Python 속성명으로 참조하므로, 물리 컬럼명만
    실제 DB(`password`/`active`)에 맞추고 Python 속성명은 그대로
    유지한다(mapped_column의 명시적 컬럼명 인자로 매핑) — 호출부
    변경을 최소화하기 위함이다.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # 논리적으로는 FK지만 실제 DB에 FK 제약조건은 없다(다른 Model들과
    # 동일하게 ForeignKey()는 애플리케이션 레벨 참조 선언용으로만 쓴다).
    company_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("companies.id"),
        nullable=True,
    )

    role_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("roles.id"),
        nullable=True,
    )

    email: Mapped[str] = mapped_column(
        String(200),
        unique=True,
        nullable=False,
        index=True,
    )

    username: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )

    # Python 속성명은 기존 호출부 호환을 위해 password_hash로 유지하되
    # 실제 컬럼은 "password"다.
    password_hash: Mapped[str] = mapped_column(
        "password",
        String(255),
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        index=True,
    )

    # Python 속성명은 기존 호출부 호환을 위해 is_active로 유지하되
    # 실제 컬럼은 "active"다.
    is_active: Mapped[bool] = mapped_column(
        "active",
        Boolean,
        default=True,
        nullable=False,
    )

    # 2026-09-09 Phase 1(로그인 무차별 대입 방어, migrations/
    # 20260909_00_add_login_lockout_columns.sql) — 로그인 실패 카운트와
    # 잠금 시각. 서버 재시작에도 유지돼야 하므로 in-memory가 아니라
    # DB 컬럼으로 저장한다. locked_until이 과거 시각이거나 NULL이면
    # 잠긴 상태가 아니다(app/domains/user/repository.py::is_locked_out
    # 참고) — 별도 "잠금 해제" 배치 없이 시간 경과만으로 자연 해제된다.
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    last_failed_login_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    company = relationship(
        "Company",
        back_populates="users",
    )

    @property
    def role(self) -> str | None:
        """
        app/domains/user/policy.py, app/core/authorization.py 등 기존
        코드가 문자열 역할 코드를 기대하므로(예: "ADMIN"), role_id로
        Role 행을 조회해 code를 그대로 노출한다.

        의도적으로 SQLAlchemy relationship()이 아니라 현재 Session에서
        수동 조회한다 — Role은 자신도 RolePermission/Permission을
        문자열로 참조하고 있어(app/domains/role/model.py), relationship
        으로 연결하면 이 프로세스에 Role/RolePermission/Permission이
        전부 함께 import되어 있어야만 configure_mappers()가 성공한다.
        각 Domain 패키지의 __init__.py가 서로를 순환 참조하며 eager
        import하는 기존 구조상 이 전체 체인을 항상 안전하게 함께
        불러오는 것이 어려워(2026-07-30 조사 중 여러 순환 import를
        확인), 여기서는 관계를 선언하지 않고 필요한 시점에만 직접
        조회해 그 문제를 완전히 피한다.

        DB 컬럼이 아니라 계산 속성이므로 쿼리 필터(`User.role == ...`)
        에는 사용할 수 없다 — 그런 조회가 필요하면 role_id를 직접
        사용해야 한다.
        """

        if self.role_id is None:
            return None

        session = object_session(self)

        if session is None:
            return None

        from app.domains.role.model import Role

        role_row = session.get(Role, self.role_id)

        return role_row.code if role_row is not None else None


__all__ = [
    "User",
]
