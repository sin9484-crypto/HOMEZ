"""
=========================================================
Homez OS

File : tests/test_funding_tenant_isolation.py

2026-08-14 Gate R13 테넌트 격리 감사 — Funding 기본계정 조회 감사
전용 테스트.

배경: FundingRepository.get_default_account()가 회사 구분 없이 "DB의
첫 FundingAccount"를 반환했다. 호출 체인(FundingService.
ensure_supply_hold ← PurchaseService.create_purchase / OrderService의
자동발주 경로)을 추적한 결과, 이 메서드가 호출되는 시점에 company_id
자체가 상위 어디에도 없다 — Order/Purchase Model에 company_id
컬럼이 아예 없기 때문이다(별도의 더 넓은 구조적 결함, 이번 Whitelist
밖으로 보고). 그래서 이번 수정은 "완전한 회사별 분리"가 아니라,
가장 위험한 시나리오(계정이 2개 이상 존재하는데 아무 계정이나 조용히
고르는 것)를 fail-closed로 차단하는 보수적 조치다.

검증 대상:
1) FundingAccount가 정확히 0개 또는 1개일 때는 기존 동작(그 하나를
   반환/사용)이 그대로 유지된다.
2) FundingAccount가 2개 이상 존재하면 ensure_supply_hold()가
   BadRequestException으로 즉시 차단한다(자동으로 아무 계정이나
   골라 다른 회사의 자금을 조용히 사용하지 않는다).
3) get_account_by_company()는 원래도 이미 회사 스코프였다 — 이
   메서드는 그대로 정상 동작함을 재확인한다(회귀 방지).
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService

COMPANY_A = 1
COMPANY_B = 2


class FundingDefaultAccountTenantIsolationTestCase(unittest.TestCase):

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

    def test_zero_accounts_raises_no_account_error(self):

        with self.assertRaises(BadRequestException) as ctx:
            self.service.ensure_supply_hold(order_id=1, amount=1000.0)

        self.assertIn("Funding Account", str(ctx.exception.detail))

    def test_single_account_still_works_as_before(self):

        account = self.service.create_account(
            FundingAccountCreate(total_funding=100000.0),
            COMPANY_A,
        )

        hold = self.service.ensure_supply_hold(order_id=1, amount=5000.0)

        self.assertEqual(hold.account_id, account["id"])
        self.assertEqual(hold.status, "HELD")

    def test_two_accounts_blocks_supply_hold_fail_closed(self):
        """
        회사가 2개 이상 계정을 갖게 되는 순간부터, 이 메서드가 회사
        구분 없이 "첫 계정"을 고르면 다른 회사의 자금이 조용히
        차감될 수 있다 — 그래서 자동으로 고르지 않고 즉시 차단해야
        한다(자동 보정 금지).
        """

        self.service.create_account(
            FundingAccountCreate(total_funding=100000.0),
            COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=50000.0),
            COMPANY_B,
        )

        with self.assertRaises(BadRequestException) as ctx:
            self.service.ensure_supply_hold(order_id=1, amount=1000.0)

        self.assertIn("2개 이상", str(ctx.exception.detail))

        # 실제로 어느 계정도 차감되지 않았어야 한다(부작용 없음).
        account_a = self.service.repository.get_account_by_company(
            COMPANY_A,
        )
        account_b = self.service.repository.get_account_by_company(
            COMPANY_B,
        )
        self.assertEqual(float(account_a.held_amount), 0.0)
        self.assertEqual(float(account_b.held_amount), 0.0)
        self.assertEqual(float(account_a.total_funding), 100000.0)
        self.assertEqual(float(account_b.total_funding), 50000.0)

    def test_get_account_by_company_remains_scoped(self):
        """회귀 방지 — 이 메서드는 원래도 이미 company_id로 스코프돼 있었다."""

        account_a = self.service.create_account(
            FundingAccountCreate(total_funding=10000.0),
            COMPANY_A,
        )
        self.service.create_account(
            FundingAccountCreate(total_funding=20000.0),
            COMPANY_B,
        )

        found = self.service.repository.get_account_by_company(COMPANY_A)
        self.assertEqual(found.id, account_a["id"])
        self.assertEqual(float(found.total_funding), 10000.0)


if __name__ == "__main__":
    unittest.main()
