"""
=========================================================
Homez OS

File : tests/test_funding_company_scope_hardening.py

V7 Gate 2(2026-08-15) — Funding 회사 스코프 강화(요구사항 3) 전용
회귀 테스트.

배경(실제 코드 감사로 확인한 Critical 결함): 기존
FundingAccountCreate.company_id가 요청 바디 필드였고 Router가 그대로
Service에 전달했다 — 인증된 어떤 admin이든 임의의 company_id로 계정을
생성(타사 명의 사칭)하거나, account_id만 알면 다른 회사의 계정을
조회/증액/감액할 수 있었다. 이번 하드닝으로 Account 관련 전 Service
메서드가 company_id를 필수로 받고, Router는 항상 current_user.
company_id만 사용한다(요청 바디로 company_id를 받지 않는다).

검증 대상:
1) FundingAccountCreate에는 이제 company_id 필드가 없다(요청 바디로
   타사 명의 사칭 자체가 불가능).
2) get_account/add_funding/remove_funding/list_ledgers — 다른 회사
   소유 계정은 전부 404(부작용 없음).
3) list_accounts — 자기 회사 소유 계정만 반환.
4) Router 레벨 — current_user.company_id가 실제로 Service까지
   전달되는지 라우터 함수 직접 호출로 확인.
5) ensure_supply_hold(company_id=...) — 명시적으로 주어지면 계정이
   2개 이상이어도 그 회사의 계정으로 결정론적으로 처리된다(기존
   Gate R13의 "2개 이상이면 무조건 차단" 보수적 조치를 실제 정상
   흐름으로 교체한 것을 검증).
6) ensure_supply_hold(company_id 없음) — 여전히 Gate R13의 보수적
   fail-closed(계정 0/1개 정상, 2개 이상 차단)를 그대로 유지한다
   (order/purchase 도메인 회귀 방지).
7) FundingHold/SupplierPayment.idempotency_key가 이제 (company_id,
   idempotency_key) 복합 UNIQUE라 다른 회사는 같은 문자열의 key를
   써도 서로 간섭하지 않는다.
8) confirm_supplier_payment(company_id=...)가 주어지면 계정 소유
   여부를 검증한다(다른 회사 소유 Hold는 404).
=========================================================
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.router import add_funding as router_add_funding
from app.domains.funding.router import (
    confirm_supplier_payment as router_confirm_supplier_payment,
)
from app.domains.funding.router import (
    create_funding_account as router_create_funding_account,
)
from app.domains.funding.router import (
    get_funding_account as router_get_funding_account,
)
from app.domains.funding.router import (
    list_funding_accounts as router_list_funding_accounts,
)
from app.domains.funding.router import (
    list_holds_by_order as router_list_holds_by_order,
)
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.schema import FundingAmountUpdate
from app.domains.funding.service import FundingService

COMPANY_A = 1
COMPANY_B = 2


def _fake_user(company_id: int, user_id: int = 1):

    return SimpleNamespace(company_id=company_id, id=user_id)


class FundingCompanyScopeHardeningTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                FundingAccount.__table__,
                FundingLedger.__table__,
                FundingHold.__table__,
                SupplierPayment.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.service = FundingService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # --------------------------------------------------
    # 1) 요청 바디에 company_id 필드 자체가 없다
    # --------------------------------------------------

    def test_funding_account_create_schema_has_no_company_id_field(self):

        self.assertNotIn(
            "company_id", FundingAccountCreate.model_fields,
        )

    # --------------------------------------------------
    # 2) 계정 CRUD — 타사 소유는 전부 404, 부작용 없음
    # --------------------------------------------------

    def test_get_account_cross_company_is_404(self):

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=10000.0), COMPANY_A,
        )

        with self.assertRaises(NotFoundException):
            self.service.get_account(account_a["id"], COMPANY_B)

    def test_add_funding_cross_company_is_404_no_side_effect(self):

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=10000.0), COMPANY_A,
        )

        with self.assertRaises(NotFoundException):
            self.service.add_funding(
                account_a["id"],
                FundingAmountUpdate(amount=5000.0),
                COMPANY_B,
            )

        unchanged = self.service.get_account(account_a["id"], COMPANY_A)
        self.assertEqual(unchanged["total_funding"], 10000.0)

    def test_remove_funding_cross_company_is_404_no_side_effect(self):

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=10000.0), COMPANY_A,
        )

        with self.assertRaises(NotFoundException):
            self.service.remove_funding(
                account_a["id"],
                FundingAmountUpdate(amount=1000.0),
                COMPANY_B,
            )

        unchanged = self.service.get_account(account_a["id"], COMPANY_A)
        self.assertEqual(unchanged["total_funding"], 10000.0)

    def test_list_ledgers_cross_company_is_404(self):

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=10000.0), COMPANY_A,
        )

        with self.assertRaises(NotFoundException):
            self.service.list_ledgers(account_a["id"], COMPANY_B)

    def test_list_accounts_returns_only_own_company(self):

        self.service.create_account(
            FundingAccountCreate(total_funding=10000.0), COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=20000.0), COMPANY_B,
        )

        list_a = self.service.list_accounts(COMPANY_A)
        list_b = self.service.list_accounts(COMPANY_B)

        self.assertEqual(len(list_a), 1)
        self.assertEqual(len(list_b), 1)
        self.assertEqual(list_a[0]["company_id"], COMPANY_A)
        self.assertEqual(list_b[0]["company_id"], COMPANY_B)

    def test_create_account_duplicate_for_same_company_rejected(self):

        self.service.create_account(
            FundingAccountCreate(total_funding=10000.0), COMPANY_A,
        )

        with self.assertRaises(BadRequestException):
            self.service.create_account(
                FundingAccountCreate(total_funding=5000.0), COMPANY_A,
            )

    # --------------------------------------------------
    # 4) Router 레벨 — current_user.company_id가 실제로 전달된다
    # --------------------------------------------------

    def test_router_create_and_get_account_uses_current_user_company(self):

        user_a = _fake_user(COMPANY_A)
        user_b = _fake_user(COMPANY_B)

        created = router_create_funding_account(
            FundingAccountCreate(total_funding=30000.0),
            current_user=user_a, db=self.db,
        )
        self.assertEqual(created["company_id"], COMPANY_A)

        # 회사 B는 이 계정을 볼 수 없다(다른 인증 컨텍스트).
        with self.assertRaises(NotFoundException):
            router_get_funding_account(
                created["id"], current_user=user_b, db=self.db,
            )

        # 회사 A 본인은 정상 조회.
        fetched = router_get_funding_account(
            created["id"], current_user=user_a, db=self.db,
        )
        self.assertEqual(fetched["id"], created["id"])

    def test_router_list_accounts_does_not_leak_other_company(self):

        user_a = _fake_user(COMPANY_A)
        user_b = _fake_user(COMPANY_B)

        router_create_funding_account(
            FundingAccountCreate(total_funding=1000.0),
            current_user=user_a, db=self.db,
        )
        router_create_funding_account(
            FundingAccountCreate(total_funding=2000.0),
            current_user=user_b, db=self.db,
        )

        list_a = router_list_funding_accounts(
            current_user=user_a, db=self.db,
        )
        self.assertEqual(len(list_a), 1)
        self.assertEqual(list_a[0]["company_id"], COMPANY_A)

    def test_router_add_funding_cross_company_is_404(self):

        user_a = _fake_user(COMPANY_A)
        user_b = _fake_user(COMPANY_B)

        created = router_create_funding_account(
            FundingAccountCreate(total_funding=1000.0),
            current_user=user_a, db=self.db,
        )

        with self.assertRaises(NotFoundException):
            router_add_funding(
                created["id"], FundingAmountUpdate(amount=500.0),
                current_user=user_b, db=self.db,
            )

    # --------------------------------------------------
    # 5/6) ensure_supply_hold — company_id 유무에 따른 계정 결정
    # --------------------------------------------------

    def test_ensure_supply_hold_with_company_id_resolves_deterministically(
        self,
    ):
        """
        계정이 2개 이상이어도 company_id가 명시적으로 주어지면 그
        회사의 계정으로 정확히 결정된다 — Gate R13의 "2개 이상이면
        무조건 차단" 임시 조치를 실제 정상 흐름으로 교체했음을
        검증한다(요청 원문 핵심 요구사항).
        """

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=50000.0), COMPANY_B,
        )

        hold = self.service.ensure_supply_hold(
            order_id=1, amount=5000.0, company_id=COMPANY_A,
        )

        self.assertEqual(hold.account_id, account_a["id"])
        self.assertEqual(hold.company_id, COMPANY_A)
        self.assertEqual(hold.status, "HELD")

        # 회사 B의 계정은 건드리지 않았어야 한다.
        account_b = self.service.get_account(
            self.service.repository.get_account_by_company(COMPANY_B).id,
            COMPANY_B,
        )
        self.assertEqual(account_b["held_amount"], 0.0)

    def test_ensure_supply_hold_with_unknown_company_id_is_404(self):

        self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )

        with self.assertRaises(NotFoundException):
            self.service.ensure_supply_hold(
                order_id=1, amount=5000.0, company_id=999,
            )

    def test_ensure_supply_hold_without_company_id_still_fail_closed(self):
        """회귀 방지 — company_id 없이 호출하는 기존(order/purchase)
        경로는 Gate R13 보수적 동작을 그대로 유지해야 한다."""

        self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=50000.0), COMPANY_B,
        )

        with self.assertRaises(BadRequestException) as ctx:
            self.service.ensure_supply_hold(order_id=1, amount=1000.0)

        self.assertIn("2개 이상", str(ctx.exception.detail))

    # --------------------------------------------------
    # 7) idempotency_key 회사별 독립(Model UNIQUE 제약 직접 검증)
    # --------------------------------------------------

    def test_funding_hold_idempotency_key_independent_per_company(self):

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )
        account_b = self.service.create_account(
            FundingAccountCreate(total_funding=50000.0), COMPANY_B,
        )

        hold_a = FundingHold(
            company_id=COMPANY_A, account_id=account_a["id"],
            order_id=1, amount=1000.0, status="HELD",
            idempotency_key="shared-key",
        )
        hold_b = FundingHold(
            company_id=COMPANY_B, account_id=account_b["id"],
            order_id=2, amount=500.0, status="HELD",
            idempotency_key="shared-key",
        )
        self.db.add(hold_a)
        self.db.add(hold_b)
        self.db.commit()

        count = (
            self.db.query(FundingHold)
            .filter(FundingHold.idempotency_key == "shared-key")
            .count()
        )
        self.assertEqual(count, 2)

        # 같은 회사가 같은 key를 재사용하면 UNIQUE 위반.
        dup = FundingHold(
            company_id=COMPANY_A, account_id=account_a["id"],
            order_id=3, amount=200.0, status="HELD",
            idempotency_key="shared-key",
        )
        self.db.add(dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    # --------------------------------------------------
    # 8) confirm_supplier_payment(company_id=...) 소유 검증
    # --------------------------------------------------

    def test_confirm_supplier_payment_with_company_id_rejects_other_owner(
        self,
    ):

        self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=50000.0), COMPANY_B,
        )

        hold = self.service.ensure_supply_hold(
            order_id=1, amount=5000.0, purchase_id=1,
            company_id=COMPANY_A,
        )
        self.assertEqual(hold.company_id, COMPANY_A)

        from unittest import mock

        fake_purchase = SimpleNamespace(
            id=1, order_id=1, supplier_id=1,
            purchase_price=5000.0, shipping_price=0.0, total_price=5000.0,
        )
        real_query = self.db.query

        def _query_side_effect(entity, *args, **kwargs):

            from app.domains.purchase.model import Purchase

            if entity is Purchase:
                stub = mock.MagicMock()
                stub.filter.return_value.first.return_value = fake_purchase
                return stub

            return real_query(entity, *args, **kwargs)

        with mock.patch.object(
            self.db, "query", side_effect=_query_side_effect,
        ):
            with self.assertRaises(NotFoundException):
                self.service.confirm_supplier_payment(
                    1, company_id=COMPANY_B,
                )

        # 회사 A(정당한 소유자)는 성공해야 한다.
        with mock.patch.object(
            self.db, "query", side_effect=_query_side_effect,
        ):
            payment = self.service.confirm_supplier_payment(
                1, company_id=COMPANY_A,
            )

        self.assertEqual(payment.company_id, COMPANY_A)
        self.assertEqual(payment.status, FundingService.PAYMENT_PAID)

    # --------------------------------------------------
    # Router — /holds, /confirm-payment도 current_user.company_id를
    # 실제로 사용한다(2026-08-15 V7 Gate 2, 추가 강화).
    # --------------------------------------------------

    def test_router_list_holds_by_order_scoped_to_current_user_company(
        self,
    ):

        user_a = _fake_user(COMPANY_A)
        user_b = _fake_user(COMPANY_B)

        self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=50000.0), COMPANY_B,
        )

        self.service.ensure_supply_hold(
            order_id=42, amount=1000.0, company_id=COMPANY_A,
        )

        holds_for_a = router_list_holds_by_order(
            order_id=42, current_user=user_a, db=self.db,
        )
        holds_for_b = router_list_holds_by_order(
            order_id=42, current_user=user_b, db=self.db,
        )

        self.assertEqual(len(holds_for_a), 1)
        self.assertEqual(len(holds_for_b), 0)

    def test_router_confirm_supplier_payment_uses_current_user_company(
        self,
    ):

        from unittest import mock

        user_a = _fake_user(COMPANY_A)
        user_b = _fake_user(COMPANY_B)

        self.service.create_account(
            FundingAccountCreate(total_funding=100000.0), COMPANY_A,
        )

        self.service.ensure_supply_hold(
            order_id=77, amount=3000.0, purchase_id=7,
            company_id=COMPANY_A,
        )

        fake_purchase = SimpleNamespace(
            id=7, order_id=77, supplier_id=1,
            purchase_price=3000.0, shipping_price=0.0, total_price=3000.0,
        )
        real_query = self.db.query

        def _query_side_effect(entity, *args, **kwargs):

            from app.domains.purchase.model import Purchase

            if entity is Purchase:
                stub = mock.MagicMock()
                stub.filter.return_value.first.return_value = fake_purchase
                return stub

            return real_query(entity, *args, **kwargs)

        # 회사 B가 라우터로 직접 호출하면 다른 회사의 Hold를 확정할
        # 수 없어야 한다(current_user.company_id가 실제로 검증에
        # 쓰이는지 라우터 레벨에서 확인).
        with mock.patch.object(
            self.db, "query", side_effect=_query_side_effect,
        ):
            with self.assertRaises(NotFoundException):
                router_confirm_supplier_payment(
                    7, data=None, current_user=user_b, db=self.db,
                )

        # 회사 A(정당한 소유자)는 라우터로 성공해야 한다.
        with mock.patch.object(
            self.db, "query", side_effect=_query_side_effect,
        ):
            payment = router_confirm_supplier_payment(
                7, data=None, current_user=user_a, db=self.db,
            )

        self.assertEqual(payment.company_id, COMPANY_A)


if __name__ == "__main__":
    unittest.main()
