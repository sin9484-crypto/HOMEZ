"""
=========================================================
Homez OS

File : app/domains/payment/model.py

2026-09-10 Phase 7(HOMEZ_USER_OPERATION_SETTINGS.md 4·5·11번) —
결제수단·자동결제 한도. Critical 결함 #1("결제 도메인이 통째로 빈
스캐폴딩")을 메우는 첫 단계다.

**카드번호·CVC 원문은 이 Model 어디에도 없다** — 의도적으로 그런
컬럼 자체를 만들지 않았다. `PaymentMethod.credential_target_name`은
`app/core/windows_credential_store.py::CredentialStore`에 저장된
Provider 토큰(Fake Provider가 발급하는 불투명 문자열)을 가리키는
참조 이름일 뿐이다 — `app/domains/store_connection/model.py`가
매입처 로그인 자격증명을 다루는 것과 동일한 패턴(원문은 Credential
Manager, DB는 참조만).

**Domain 경계에 대한 정직한 기록**: CLAUDE.md는 Customer Payment ≠
Marketplace Settlement ≠ Funding Account ≠ Funding Hold ≠ Supplier
Payment ≠ Refund를 명시한다. HOMEZ는 무재고 중개·주문대행 구조라
고객은 판매채널(쿠팡 등)에 결제하고 HOMEZ는 그 대금을 Marketplace
Settlement로 받는다 — 즉 이 저장소에는 "고객이 HOMEZ에 직접 내는
돈"(Customer Payment)이 사실상 존재하지 않을 가능성이 높다.
문서(HOMEZ_USER_OPERATION_SETTINGS.md) 4번의 "국내 매입처는 카드
또는 예치금, 해외 매입처는 카드 또는 PayPal"이라는 문구로 미루어,
이 Model이 실제로 관리하는 결제수단은 **매입처(공급처)에게 HOMEZ가
지불할 때 쓰는 결제수단**(Supplier Payment 쪽에 더 가까움)일
가능성이 크다. 다만 이 판단을 이번 Phase에서 100% 확정하지
않았다 — 어느 Domain에 속하는지와 무관하게 "결제수단·자동결제
한도·재인증"이라는 요구사항 자체는 동일하게 구현 가능해 우선
`app/domains/payment`로 독립 구현했고, 실제 Provider 연동 시점에
Supplier Payment로 재명명하거나 그 Domain에 흡수할지 사용자 확인이
필요하다(docs/HOMEZ_PROJECT_STATE.md Phase 7 절에 기록). 어느 쪽
이든 이 Model은 Funding Account/Funding Hold/Marketplace
Settlement/Refund 테이블을 전혀 참조하지 않는다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class PaymentMethod(Base):
    """
    등록된 결제수단. 활성 여부와 무관하게 삭제(하드 delete)하지
    않는다 — `active=False`로 비활성화만 하고 이력을 보존한다(결제
    수단이 실제 결제에 쓰였을 수 있어 추적 가능해야 한다).
    """

    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # app.domains.payment.constants.PaymentMethodType 중 하나.
    method_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 사용자가 화면에서 보는 이름 — 호출자가 이미 마스킹한 값만
    # 넘겨야 한다(예: "국민카드 **** 1234"). 이 컬럼 자체에도 원문
    # 카드번호를 넣지 않도록 서비스 계층에서 강제한다.
    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # CredentialStore(app/core/windows_credential_store.py)에 저장된
    # Provider 토큰의 target_name. 이 컬럼에는 토큰 원문도, 카드번호
    # 원문도 들어가지 않는다 — 참조 문자열뿐이다.
    credential_target_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    # 논리 참조 (users.id)
    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


class PaymentAutoLimit(Base):
    """
    회사별 자동결제 한도. append-only — 값을 바꿀 때마다 새 행을
    추가하고, 가장 최근 행(id 최대)이 현재 적용되는 한도다
    (function_automation_states와 동일한 감사 패턴). 한도 자체가
    없으면(행이 하나도 없으면) 자동결제는 항상 거부된다 — "한도를
    명시적으로 설정한 적이 없다"를 "무제한"으로 잘못 해석하지 않기
    위한 fail-closed 기본값.
    """

    __tablename__ = "payment_auto_limits"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    per_transaction_limit_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    daily_limit_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="KRW",
    )

    # 논리 참조 (users.id)
    set_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    set_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


__all__ = ["PaymentMethod", "PaymentAutoLimit"]
