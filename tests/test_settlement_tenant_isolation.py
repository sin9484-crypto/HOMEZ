"""
=========================================================
Homez OS

File : tests/test_settlement_tenant_isolation.py

2026-08-14 GATE R2/R3 테넌트 격리 감사 대응 — Settlement 도메인
회사(테넌트) 간 격리 전용 테스트.

배경: MarketplaceSettlement 테이블 자체에는 company_id 컬럼이 없다.
과거 Repository/Service는 settlement_id만으로 조회·조건부 UPDATE를
수행해, admin_guard로 인증된 어떤 회사의 관리자든 다른 회사의 정산
기록(gross_amount/fee_amount/net_amount/memo)을 id 추측만으로 조회할
뿐 아니라 confirm-deposit/cancel/reverse(실제 Funding 자금 이동을
수반하는 조작)까지 실행할 수 있었다.

수정 후에는 FundingAccount.company_id(회사당 계정 1개, UNIQUE)를
Source of Truth로 삼아 Service의 모든 공개 메서드가 회사 범위로
스코프된다(app/domains/settlement/service.py 참고). 이 테스트는
store_connection의 확립된 패턴(tests/
test_store_connection_tenant_isolation.py)과 동일하게 임시 SQLite
DB + router 엔드포인트 함수 직접 호출로 실제 경계를 검증한다.

검증 대상:
1) 회사 A 관리자가 회사 B의 settlement를 GET/confirm-deposit/cancel/
   reverse 시도하면 전부 404(NotFoundException) — 403이 아니다.
2) 위 실패 시 회사 B의 Settlement 행과 FundingAccount 잔액이 전혀
   변경되지 않는다(자금이 실제로 이동하지 않았는지까지 확인).
3) 목록 조회(list)는 자기 회사 소유 Settlement만 반환한다(타사 데이터
   혼입 없음).
4) 회사 A가 회사 B 소유의 account_id를 지정해 새 Settlement 생성을
   시도하면 거부된다(타사 계정에 얹어 생성 불가).
5) 같은 idempotency_key를 회사 A/B가 각각 써도 서로의 결과가 섞이지
   않는다(같은 자연키를 회사별로 독립 사용 가능 + 같은 회사 내 중복은
   idempotent 재사용으로 처리).
=========================================================
"""

import os
import tempfile
import types
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.router import cancel_settlement
from app.domains.settlement.router import confirm_deposit
from app.domains.settlement.router import create_settlement
from app.domains.settlement.router import get_settlement
from app.domains.settlement.router import list_settlements
from app.domains.settlement.router import reverse_settlement
from app.domains.settlement.schema import SettlementCreate
from app.domains.settlement.service import SettlementService
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


def _fake_user(company_id: int, user_id: int = 1):
    """router.py 엔드포인트 함수가 실제로 참조하는 속성(company_id, id)만
    가진 가벼운 대역 — store_connection tenant isolation 테스트와 동일한
    패턴."""

    return types.SimpleNamespace(company_id=company_id, id=user_id)


class SettlementTenantIsolationTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                FundingAccount.__table__,
                FundingLedger.__table__,
                FundingHold.__table__,
                SupplierPayment.__table__,
                MarketplaceSettlement.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="정산 회사 A", business_number="111-11-11111", ceo="에이",
            phone="02-111-1111", email="settlement-a@example.com",
            address="서울 A",
        )
        self.company_b = Company(
            name="정산 회사 B", business_number="222-22-22222", ceo="비",
            phone="02-222-2222", email="settlement-b@example.com",
            address="서울 B",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        funding = FundingService(self.db)
        self.account_a_id = funding.create_account(
            FundingAccountCreate(total_funding=100000.0),
            self.company_a.id,
        )["id"]
        self.account_b_id = funding.create_account(
            FundingAccountCreate(total_funding=50000.0),
            self.company_b.id,
        )["id"]

        self.service = SettlementService(self.db)

        self.user_a = _fake_user(self.company_a.id, user_id=101)
        self.user_b = _fake_user(self.company_b.id, user_id=201)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_company_b_settlement(
        self, net_amount: float = 5000.0,
    ) -> MarketplaceSettlement:
        """회사 B 소유의 PENDING Settlement를 하나 만들어 둔다(공격 대상)."""

        return create_settlement(
            SettlementCreate(
                market="COUPANG",
                market_order_id=_next_key("victim-order"),
                account_id=self.account_b_id,
                gross_amount=net_amount,
                fee_amount=0.0,
                net_amount=net_amount,
                idempotency_key=_next_key("victim-settlement"),
            ),
            current_user=self.user_b, db=self.db,
        )

    def _account_total_funding(self, account_id: int) -> float:

        account = (
            self.db.query(FundingAccount)
            .filter(FundingAccount.id == account_id)
            .first()
        )
        return float(account.total_funding)

    def _settlement_snapshot(self, settlement_id: int) -> dict:

        row = (
            self.db.query(MarketplaceSettlement)
            .filter(MarketplaceSettlement.id == settlement_id)
            .first()
        )
        return {
            "status": row.status,
            "deposited_at": row.deposited_at,
            "funding_ledger_id": row.funding_ledger_id,
            "memo": row.memo,
        }

    # ----------------------------------------------------
    # 1) GET — 회사 A가 회사 B의 settlement를 조회하면 404
    # ----------------------------------------------------

    def test_router_get_settlement_cross_company_returns_404_not_403(self):

        victim = self._create_company_b_settlement()

        with self.assertRaises(NotFoundException) as ctx:
            get_settlement(
                victim.id, current_user=self.user_a, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        not_found_message = str(ctx.exception.detail)

        # 진짜로 존재하지 않는 id를 조회했을 때와 완전히 같은 메시지여야
        # 한다 — 존재 여부 자체를 드러내지 않는다.
        with self.assertRaises(NotFoundException) as ctx2:
            get_settlement(
                999999, current_user=self.user_a, db=self.db,
            )
        self.assertEqual(str(ctx2.exception.detail), not_found_message)

        # 회사 B 본인은 정상 조회 가능.
        own = get_settlement(
            victim.id, current_user=self.user_b, db=self.db,
        )
        self.assertEqual(own.id, victim.id)

    # ----------------------------------------------------
    # 2) list — 타사 데이터가 섞이지 않음
    # ----------------------------------------------------

    def test_router_list_settlements_does_not_leak_other_company(self):

        victim = self._create_company_b_settlement()

        own_a = create_settlement(
            SettlementCreate(
                market="COUPANG",
                market_order_id=_next_key("a-order"),
                account_id=self.account_a_id,
                gross_amount=1000.0,
                fee_amount=0.0,
                net_amount=1000.0,
                idempotency_key=_next_key("a-settlement"),
            ),
            current_user=self.user_a, db=self.db,
        )

        list_a = list_settlements(
            market=None, status_filter=None, order_id=None,
            skip=0, limit=100, current_user=self.user_a, db=self.db,
        )
        ids_a = {row.id for row in list_a}

        self.assertIn(own_a.id, ids_a)
        self.assertNotIn(victim.id, ids_a)

        list_b = list_settlements(
            market=None, status_filter=None, order_id=None,
            skip=0, limit=100, current_user=self.user_b, db=self.db,
        )
        ids_b = {row.id for row in list_b}
        self.assertIn(victim.id, ids_b)
        self.assertNotIn(own_a.id, ids_b)

    # ----------------------------------------------------
    # 3) confirm-deposit — 회사 A가 회사 B의 settlement 입금확인 시도
    #    → 404, 실제 자금 이동 없음
    # ----------------------------------------------------

    def test_router_confirm_deposit_cross_company_returns_404_no_fund_movement(
        self,
    ):

        victim = self._create_company_b_settlement(net_amount=5000.0)
        before_settlement = self._settlement_snapshot(victim.id)
        before_balance = self._account_total_funding(self.account_b_id)

        with self.assertRaises(NotFoundException) as ctx:
            confirm_deposit(
                victim.id, data=None,
                current_user=self.user_a, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after_settlement = self._settlement_snapshot(victim.id)
        after_balance = self._account_total_funding(self.account_b_id)

        self.assertEqual(before_settlement, after_settlement)
        self.assertEqual(
            before_balance, after_balance,
            "회사 A의 실패한 confirm-deposit 시도가 회사 B의 Funding "
            "잔액을 건드렸습니다.",
        )
        self.assertEqual(after_settlement["status"], "PENDING")

    # ----------------------------------------------------
    # 4) cancel — 회사 A가 회사 B의 settlement 취소 시도 → 404, 불변
    # ----------------------------------------------------

    def test_router_cancel_cross_company_returns_404_and_no_side_effect(self):

        victim = self._create_company_b_settlement()
        before = self._settlement_snapshot(victim.id)

        with self.assertRaises(NotFoundException) as ctx:
            cancel_settlement(
                victim.id, data=None,
                current_user=self.user_a, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after = self._settlement_snapshot(victim.id)
        self.assertEqual(before, after)
        self.assertNotEqual(after["status"], "CANCELLED")

    # ----------------------------------------------------
    # 5) reverse — 회사 A가 회사 B의 DEPOSITED settlement 환수 시도
    #    → 404, 실제 자금 이동 없음
    # ----------------------------------------------------

    def test_router_reverse_cross_company_returns_404_no_fund_movement(self):

        victim = self._create_company_b_settlement(net_amount=5000.0)
        confirm_deposit(
            victim.id, data=None, current_user=self.user_b, db=self.db,
        )

        before_settlement = self._settlement_snapshot(victim.id)
        before_balance = self._account_total_funding(self.account_b_id)
        self.assertEqual(before_settlement["status"], "DEPOSITED")

        with self.assertRaises(NotFoundException) as ctx:
            reverse_settlement(
                victim.id, data=None,
                current_user=self.user_a, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after_settlement = self._settlement_snapshot(victim.id)
        after_balance = self._account_total_funding(self.account_b_id)

        self.assertEqual(before_settlement, after_settlement)
        self.assertEqual(
            before_balance, after_balance,
            "회사 A의 실패한 reverse 시도가 회사 B의 Funding 잔액을 "
            "건드렸습니다.",
        )

    # ----------------------------------------------------
    # 6) create — 회사 A가 회사 B 소유 account_id로 생성 시도 → 거부
    # ----------------------------------------------------

    def test_router_create_with_other_company_account_id_rejected(self):

        with self.assertRaises(NotFoundException):
            create_settlement(
                SettlementCreate(
                    market="COUPANG",
                    market_order_id=_next_key("attack-order"),
                    account_id=self.account_b_id,  # 회사 B 소유 계정
                    gross_amount=1000.0,
                    fee_amount=0.0,
                    net_amount=1000.0,
                    idempotency_key=_next_key("attack-settlement"),
                ),
                current_user=self.user_a, db=self.db,
            )

        # 회사 B의 목록에 공격자가 만든 행이 섞이지 않았어야 한다.
        list_b = list_settlements(
            market=None, status_filter=None, order_id=None,
            skip=0, limit=100, current_user=self.user_b, db=self.db,
        )
        self.assertEqual(list_b, [])

    # ----------------------------------------------------
    # 7) idempotency_key — 회사별로 다른 key를 쓰면 정상 격리되고,
    #    같은 회사 안에서는 재호출이 idempotent하다
    # ----------------------------------------------------
    #
    # 주의(스키마 한계, 정책 결정 필요): MarketplaceSettlement.
    # idempotency_key는 (company_id, idempotency_key) 복합이 아니라
    # 여전히 전역 UNIQUE다(model.py의
    # uq_marketplace_settlements_idempotency — company_id 컬럼 자체가
    # 이 테이블에 없다). store_connection/marketplace_listing/
    # media_asset/listing_package는 이미 복합 UNIQUE로 이 문제를
    # 해결했지만, settlement는 스키마 변경(company_id 컬럼 추가)
    # 없이는 동일하게 고칠 수 없다 — 이번 작업은 스키마 변경을
    # 실제 DB에 적용하지 않으므로, 서로 다른 회사가 우연히 같은
    # idempotency_key 문자열을 쓰면(예: 둘 다 "settlement-2026-08-14")
    # 두 번째 회사는 성공하지 못하고 ConflictException(409)을 받는다
    # (아래 test_cross_company_reuse_of_idempotency_key_raises_
    # conflict_not_leak에서 검증) — 타사 행을 반환하는 IDOR로 이어지지는
    # 않지만, 두 번째 회사가 그 key를 영영 쓸 수 없다는 가용성 제약은
    # 남는다. docs/HOMEZ_PROJECT_STATE.md와 감사 보고서에 별도 Migration
    # 승인 필요 항목으로 기록했다.

    def test_different_idempotency_keys_per_company_isolated_and_replay_idempotent(
        self,
    ):

        settlement_a = create_settlement(
            SettlementCreate(
                market="COUPANG", market_order_id=_next_key("order-a"),
                account_id=self.account_a_id,
                gross_amount=1000.0, fee_amount=0.0, net_amount=1000.0,
                idempotency_key="company-a-key",
            ),
            current_user=self.user_a, db=self.db,
        )
        settlement_b = create_settlement(
            SettlementCreate(
                market="COUPANG", market_order_id=_next_key("order-b"),
                account_id=self.account_b_id,
                gross_amount=2000.0, fee_amount=0.0, net_amount=2000.0,
                idempotency_key="company-b-key",
            ),
            current_user=self.user_b, db=self.db,
        )

        self.assertNotEqual(settlement_a.id, settlement_b.id)
        self.assertEqual(settlement_a.account_id, self.account_a_id)
        self.assertEqual(settlement_b.account_id, self.account_b_id)

        # 같은 회사 안에서 같은 key로 재호출하면 idempotent(기존 행
        # 반환) — 격리가 idempotency 자체를 깨면 안 된다.
        replay_a = self.service.create(
            SettlementCreate(
                market="COUPANG", market_order_id=_next_key("order-a-replay"),
                account_id=self.account_a_id,
                gross_amount=1000.0, fee_amount=0.0, net_amount=1000.0,
                idempotency_key="company-a-key",
            ),
            self.company_a.id,
        )
        self.assertEqual(replay_a.id, settlement_a.id)

    def test_cross_company_reuse_of_idempotency_key_creates_independent_rows(
        self,
    ):
        """
        2026-08-14 테넌트 격리 감사(Gate R13) — MarketplaceSettlement에
        company_id를 추가하고 UNIQUE를 (company_id, idempotency_key)
        복합으로 바꿔, 이전에 남아있던 잔존 한계("idempotency_key가
        전역이라 회사 B가 쓴 key를 회사 A는 절대 쓸 수 없었음")를
        해소했다. 이제 같은 문자열의 idempotency_key를 회사 A/B가 각자
        독립적으로 사용할 수 있다 — 서로 다른 행이 생성되고, 어느
        쪽도 상대방의 행을 보거나 충돌을 일으키지 않는다.
        """

        shared_key = "collision-key-cross-company"

        settlement_b = create_settlement(
            SettlementCreate(
                market="COUPANG", market_order_id=_next_key("collision-b"),
                account_id=self.account_b_id,
                gross_amount=2000.0, fee_amount=0.0, net_amount=2000.0,
                idempotency_key=shared_key,
            ),
            current_user=self.user_b, db=self.db,
        )

        settlement_a = create_settlement(
            SettlementCreate(
                market="COUPANG",
                market_order_id=_next_key("collision-a"),
                account_id=self.account_a_id,
                gross_amount=1000.0, fee_amount=0.0, net_amount=1000.0,
                idempotency_key=shared_key,
            ),
            current_user=self.user_a, db=self.db,
        )

        self.assertNotEqual(settlement_a.id, settlement_b.id)
        self.assertEqual(settlement_a.gross_amount, 1000.0)
        self.assertEqual(settlement_b.gross_amount, 2000.0)

        list_a = list_settlements(
            market=None, status_filter=None, order_id=None,
            skip=0, limit=100, current_user=self.user_a, db=self.db,
        )
        self.assertEqual([s.id for s in list_a], [settlement_a.id])

        list_b = list_settlements(
            market=None, status_filter=None, order_id=None,
            skip=0, limit=100, current_user=self.user_b, db=self.db,
        )
        self.assertEqual([s.id for s in list_b], [settlement_b.id])

        # 재요청(같은 회사, 같은 key)은 여전히 멱등 — 기존 행 그대로 반환.
        replay_a = create_settlement(
            SettlementCreate(
                market="COUPANG",
                market_order_id=_next_key("collision-a-replay"),
                account_id=self.account_a_id,
                gross_amount=1000.0, fee_amount=0.0, net_amount=1000.0,
                idempotency_key=shared_key,
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(replay_a.id, settlement_a.id)


if __name__ == "__main__":
    unittest.main()
