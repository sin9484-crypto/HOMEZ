"""
=========================================================
Homez OS

File : app/domains/funding/repository.py

Funding Repository
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment


class FundingRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Account
    # --------------------------------------------------

    def get_account(
        self,
        account_id: int,
    ) -> FundingAccount | None:

        return (
            self.db.query(FundingAccount)
            .filter(FundingAccount.id == account_id)
            .first()
        )

    def get_account_by_company(
        self,
        company_id: int,
    ) -> FundingAccount | None:

        return (
            self.db.query(FundingAccount)
            .filter(FundingAccount.company_id == company_id)
            .first()
        )

    def get_default_account(
        self,
    ) -> FundingAccount | None:
        """
        MVP: 등록된 첫 FundingAccount를 기본 계정으로 사용.

        2026-08-14 테넌트 격리 감사(Gate R13) — 이 메서드는 회사
        구분 없이 "DB의 첫 FundingAccount"를 반환하는 구조적 결함이
        있다. 호출 체인(FundingService.ensure_supply_hold ←
        PurchaseService.create_purchase / OrderService의 자동발주
        경로)을 추적한 결과, 이 메서드를 호출하는 시점에 company_id
        자체가 상위 어디에서도 알려져 있지 않다 — Order/Purchase
        Model에 company_id 컬럼이 아예 없다(app/domains/order/model.py,
        app/domains/purchase/model.py 확인, grep 결과 0건). 즉
        "company_id가 이미 상위에 있는데 이 메서드만 무시한다"는
        전제가 이 호출 체인에는 성립하지 않는다 — Order/Purchase
        Domain 자체에 회사 스코프가 없는 훨씬 더 넓은 별도의 구조적
        결함이며, 이번 Whitelist(coupang/decision/product_candidate/
        funding 기본계정 조회/settlement) 범위 밖이다.

        회사가 2개 이상 생기는 순간부터는 이 메서드가 실제로 위험해
        진다(어느 회사가 먼저 계정을 만들었느냐에 따라 다른 회사의
        발주가 엉뚱한 계정의 Available Funding을 차감/차단할 수 있다).
        Order/Purchase 전체 재설계 없이 안전하게 적용 가능한 최소·
        보수적 조치로, 이 메서드를 호출하는 FundingService.
        ensure_supply_hold가 호출 직전 count_accounts()로 "여러 회사가
        이미 각자 계정을 갖고 있는" 가장 위험한 시나리오를 fail-closed
        로 차단한다(계정이 2개 이상이면 어떤 계정을 고를지 추측하지
        않고 명시적으로 예외를 던진다 — 자동 보정 금지). 계정이 정확히
        0개 또는 1개일 때만(현재 실제 운영 DB 상태와 일치) 기존 동작을
        그대로 유지한다. 이 메서드 자체는 하위 호환을 위해 그대로 두되,
        호출 전 가드는 Service 책임으로 둔다(이 Repository 레이어는
        조회만 담당하는 기존 컨벤션 유지).

        정책 결정 필요(보류): 진짜 수정은 Order/Purchase Model에
        company_id를 추가하고 ensure_supply_hold가 company_id를 필수
        인자로 받도록 전체 호출 체인을 재설계하는 것이다 — 이는 별도
        승인 대상의 더 큰 작업이다.
        """

        return (
            self.db.query(FundingAccount)
            .order_by(FundingAccount.id.asc())
            .first()
        )

    def count_accounts(
        self,
    ) -> int:
        """
        FundingAccount 총 개수. get_default_account()의 fail-closed
        판단(계정이 2개 이상이면 '기본 계정'을 추측하지 않음)에 사용된다
        — 판단 자체는 Service 레이어(FundingService.ensure_supply_hold)
        의 책임이다(이 Repository는 조회만 담당하는 기존 컨벤션 유지).
        """

        return self.db.query(FundingAccount).count()

    def list_accounts(
        self,
    ) -> list[FundingAccount]:

        return (
            self.db.query(FundingAccount)
            .order_by(FundingAccount.id.asc())
            .all()
        )

    def add_account(
        self,
        account: FundingAccount,
    ) -> FundingAccount:

        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)

        return account

    def save_account(
        self,
        account: FundingAccount,
    ) -> FundingAccount:

        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)

        return account

    def save_account_no_commit(
        self,
        account: FundingAccount,
    ) -> FundingAccount:
        """Settlement 전용: flush만 수행, commit은 호출자(Settlement Service)가 담당."""

        self.db.add(account)
        self.db.flush()

        return account

    # --------------------------------------------------
    # Ledger
    # --------------------------------------------------

    def add_ledger(
        self,
        ledger: FundingLedger,
    ) -> FundingLedger:

        self.db.add(ledger)
        self.db.commit()
        self.db.refresh(ledger)

        return ledger

    def add_ledger_no_commit(
        self,
        ledger: FundingLedger,
    ) -> FundingLedger:
        """Settlement 전용: flush만 수행, commit은 호출자(Settlement Service)가 담당."""

        self.db.add(ledger)
        self.db.flush()

        return ledger

    def list_ledgers(
        self,
        account_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[FundingLedger]:

        return (
            self.db.query(FundingLedger)
            .filter(FundingLedger.account_id == account_id)
            .order_by(FundingLedger.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    # --------------------------------------------------
    # Hold
    # --------------------------------------------------

    def get_active_hold_by_order(
        self,
        order_id: int,
    ) -> FundingHold | None:

        return (
            self.db.query(FundingHold)
            .filter(FundingHold.order_id == order_id)
            .filter(FundingHold.status == "HELD")
            .first()
        )

    def get_held_by_purchase(
        self,
        purchase_id: int,
    ) -> FundingHold | None:

        return (
            self.db.query(FundingHold)
            .filter(FundingHold.purchase_id == purchase_id)
            .filter(FundingHold.status == "HELD")
            .first()
        )

    def get_hold_by_purchase(
        self,
        purchase_id: int,
    ) -> FundingHold | None:
        """purchase 기준 최신 Hold (상태 무관)."""

        return (
            self.db.query(FundingHold)
            .filter(FundingHold.purchase_id == purchase_id)
            .order_by(FundingHold.id.desc())
            .first()
        )

    def get_hold_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> FundingHold | None:
        """
        (company_id, idempotency_key) 복합 조회(2026-08-15 V7 Gate 2) —
        FundingHold.idempotency_key UNIQUE가 이제 회사별 복합이라
        조회도 동일하게 스코프한다.
        """

        return (
            self.db.query(FundingHold)
            .filter(FundingHold.company_id == company_id)
            .filter(
                FundingHold.idempotency_key == idempotency_key
            )
            .first()
        )

    def add_hold(
        self,
        hold: FundingHold,
    ) -> FundingHold:

        self.db.add(hold)
        self.db.commit()
        self.db.refresh(hold)

        return hold

    def save_hold(
        self,
        hold: FundingHold,
    ) -> FundingHold:

        self.db.add(hold)
        self.db.commit()
        self.db.refresh(hold)

        return hold

    def list_holds_by_order(
        self,
        order_id: int,
        company_id: int | None = None,
    ) -> list[FundingHold]:
        """
        2026-08-15 V7 Gate 2 — company_id가 주어지면 그 회사 소유
        Hold만 반환한다(FundingHold.company_id가 이제 항상 채워져
        있어 가능해진 필터 — Order 자체에 company_id가 없어도 Hold는
        생성 시점에 계정에서 company_id를 비정규화해 갖고 있다).
        주어지지 않으면(내부/레거시 호출자) 기존처럼 order_id만으로
        조회한다.
        """

        query = self.db.query(FundingHold).filter(
            FundingHold.order_id == order_id,
        )

        if company_id is not None:
            query = query.filter(FundingHold.company_id == company_id)

        return (
            query
            .order_by(FundingHold.id.desc())
            .all()
        )

    # --------------------------------------------------
    # Supplier Payment
    # --------------------------------------------------

    def get_payment_by_purchase(
        self,
        purchase_id: int,
    ) -> SupplierPayment | None:

        return (
            self.db.query(SupplierPayment)
            .filter(SupplierPayment.purchase_id == purchase_id)
            .first()
        )

    def get_payment_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> SupplierPayment | None:
        """
        (company_id, idempotency_key) 복합 조회(2026-08-15 V7 Gate 2) —
        SupplierPayment.idempotency_key UNIQUE가 이제 회사별 복합이라
        조회도 동일하게 스코프한다.
        """

        return (
            self.db.query(SupplierPayment)
            .filter(SupplierPayment.company_id == company_id)
            .filter(
                SupplierPayment.idempotency_key
                == idempotency_key
            )
            .first()
        )

    def add_payment(
        self,
        payment: SupplierPayment,
    ) -> SupplierPayment:

        self.db.add(payment)
        self.db.commit()
        self.db.refresh(payment)

        return payment


__all__ = [
    "FundingRepository",
]
