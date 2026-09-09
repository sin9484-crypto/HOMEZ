"""
=========================================================
Homez OS

File : tests/test_settlement_hardening.py

Phase 2-B-1-2 Marketplace Settlement Hardening
Transaction / 부분 유일 인덱스 / 멱등성 / rollback 검증

표준 라이브러리 unittest만 사용. 신규 패키지 없음.
homez.db는 사용하지 않고, 테스트 전용 임시 SQLite 파일 DB를 사용한다.
=========================================================
"""

import itertools
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.repository import FundingRepository
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService
from app.domains.purchase.model import Purchase
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.repository import SettlementRepository
from app.domains.settlement.schema import SettlementCreate
from app.domains.settlement.service import SettlementService
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)


class SettlementHardeningTestCase(unittest.TestCase):
    """
    각 테스트마다 독립된 임시 SQLite 파일 DB를 새로 만든다
    (:memory: 대신 파일 DB를 쓰는 이유: 여러 Session/연결이
    동일한 DB 상태를 안정적으로 공유하도록 하기 위함).
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        @event.listens_for(self.engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        # Settlement Hardening 검증에 실제로 필요한 5개 테이블만 생성한다.
        # FundingAccount/FundingLedger/FundingHold/SupplierPayment/
        # MarketplaceSettlement는 전부 ForeignKey가 없는 "논리 참조"
        # 컬럼만 사용하므로 이 5개만으로 FK 문제 없이 생성 가능하다.
        #
        # Purchase(purchases)는 orders.id/suppliers.id를 실제
        # ForeignKey로 참조하고, orders/products/suppliers/brands/
        # categories/inventories가 relationship() 문자열로 서로
        # 얽혀 있어(configure_mappers 시 전부 함께 등록되어야 함)
        # 이 테이블만 별도로 만들려면 Product 도메인 전체(7개 모듈)를
        # 끌어들여야 한다. 이는 이번 Settlement Hardening Whitelist
        # 범위를 벗어나므로, purchases 테이블은 생성하지 않고
        # Supplier Payment 회귀 테스트에서 Purchase 조회 지점만
        # 한정적으로 mock 처리한다(아래 test_hold_and_supplier_payment_
        # flow_regression 참고). 가짜 orders/suppliers 대체 테이블이나
        # SQLite FK 검사 우회는 하지 않는다.
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

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

        self._counter = itertools.count(1)

        # 2026-08-14 테넌트 격리 감사 — Settlement 접근은 이제
        # FundingAccount.company_id를 거쳐 강제되므로, 기존 Hardening
        # 테스트도 명시적 Company 하나를 만들어 그 회사 소유의 계정을
        # 쓴다(교차 테넌트 시나리오는 별도
        # test_settlement_tenant_isolation.py에서 검증한다).
        setup_db = self.SessionLocal()
        try:
            company = Company(
                name=f"정산 테스트 회사 {next(self._counter)}",
                business_number="000-00-00000",
                ceo="테스트",
                phone="02-000-0000",
                email="settlement-hardening@example.com",
                address="서울",
            )
            setup_db.add(company)
            setup_db.commit()
            self.company_id = company.id
        finally:
            setup_db.close()

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # --------------------------------------------------
    # 헬퍼
    # --------------------------------------------------

    def _create_company(self) -> int:
        """FundingAccount.company_id는 UNIQUE라 회사당 계정 1개뿐이다 —
        한 테스트에서 계정을 2개 이상 만들어야 하면 회사도 그만큼
        따로 만든다."""

        db = self.SessionLocal()

        try:
            company = Company(
                name=f"정산 테스트 회사 {next(self._counter)}",
                business_number="000-00-00000",
                ceo="테스트",
                phone="02-000-0000",
                email=f"settlement-hardening-{next(self._counter)}@example.com",
                address="서울",
            )
            db.add(company)
            db.commit()

            return company.id

        finally:
            db.close()

    def _create_account(
        self, total_funding: float, company_id: int | None = None,
    ) -> int:

        db = self.SessionLocal()

        try:
            service = FundingService(db)
            result = service.create_account(
                FundingAccountCreate(total_funding=total_funding),
                company_id or self.company_id,
            )

            return result["id"]

        finally:
            db.close()

    def _create_settlement(
        self,
        account_id: int,
        net_amount: float,
        fee_amount: float = 0.0,
        market: str = "COUPANG",
        company_id: int | None = None,
    ) -> int:

        gross_amount = net_amount + fee_amount
        market_order_id = f"ORDER-{next(self._counter)}"

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            settlement = service.create(
                SettlementCreate(
                    market=market,
                    market_order_id=market_order_id,
                    account_id=account_id,
                    gross_amount=gross_amount,
                    fee_amount=fee_amount,
                    net_amount=net_amount,
                ),
                company_id or self.company_id,
            )

            return settlement.id

        finally:
            db.close()

    def _settlement_ledgers(
        self,
        db,
        settlement_id: int,
        ledger_type: str,
    ) -> list:

        return (
            db.query(FundingLedger)
            .filter(FundingLedger.reference_type == "settlement")
            .filter(FundingLedger.reference_id == settlement_id)
            .filter(FundingLedger.type == ledger_type)
            .all()
        )

    # --------------------------------------------------
    # 1) confirm_deposit 성공 + 단일 Ledger
    # --------------------------------------------------

    def test_confirm_deposit_success_single_ledger(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            result = service.confirm_deposit(settlement_id, self.company_id)

            self.assertEqual(result.status, SettlementService.STATUS_DEPOSITED)
            self.assertIsNotNone(result.funding_ledger_id)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_ADD,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 105000.0)

        finally:
            db.close()

    def test_confirm_deposit_still_works_when_settlement_capability_deactivated(self):
        """Audit(2026-08-21, AG-0) — 입금 확인은 운영자가 직접
        수행하는 핵심 재무 업무다. SETTLEMENT AI Capability가
        비활성이어도 정상 동작해야 한다(이전 라운드의 잘못된 게이트를
        되돌린 회귀 방지 테스트)."""

        from tests.ai_governance_test_helpers import deactivated_capability

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()
        try:
            service = SettlementService(db)
            with deactivated_capability("SETTLEMENT"):
                result = service.confirm_deposit(settlement_id, self.company_id)
            self.assertEqual(result.status, SettlementService.STATUS_DEPOSITED)
        finally:
            db.close()

    # --------------------------------------------------
    # 2) DEPOSITED 재호출 멱등성
    # --------------------------------------------------

    def test_confirm_deposit_idempotent_when_deposited(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            first = service.confirm_deposit(settlement_id, self.company_id)
            second = service.confirm_deposit(settlement_id, self.company_id)

            self.assertEqual(first.status, SettlementService.STATUS_DEPOSITED)
            self.assertEqual(second.status, SettlementService.STATUS_DEPOSITED)
            self.assertEqual(first.funding_ledger_id, second.funding_ledger_id)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_ADD,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 105000.0)

        finally:
            db.close()

    # --------------------------------------------------
    # 3) reverse 성공 + 단일 Ledger
    # --------------------------------------------------

    def test_reverse_success_single_ledger(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            service.confirm_deposit(settlement_id, self.company_id)
            result = service.reverse(settlement_id, self.company_id)

            self.assertEqual(result.status, SettlementService.STATUS_REVERSED)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_REMOVE,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

        finally:
            db.close()

    # --------------------------------------------------
    # 4) REVERSED 재호출 멱등성
    # --------------------------------------------------

    def test_reverse_idempotent_when_reversed(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            service.confirm_deposit(settlement_id, self.company_id)
            first = service.reverse(settlement_id, self.company_id)
            second = service.reverse(settlement_id, self.company_id)

            self.assertEqual(first.status, SettlementService.STATUS_REVERSED)
            self.assertEqual(second.status, SettlementService.STATUS_REVERSED)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_REMOVE,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

        finally:
            db.close()

    # --------------------------------------------------
    # 5) 잘못된 상태에서 부수효과 없음
    # --------------------------------------------------

    def test_invalid_state_transitions_have_no_side_effects(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            service.cancel(settlement_id, self.company_id)  # PENDING -> CANCELLED

            with self.assertRaises(BadRequestException):
                service.confirm_deposit(settlement_id, self.company_id)

            ledgers = db.query(FundingLedger).filter(
                FundingLedger.reference_type == "settlement",
                FundingLedger.reference_id == settlement_id,
            ).all()
            self.assertEqual(len(ledgers), 0)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

            with self.assertRaises(BadRequestException):
                service.reverse(settlement_id, self.company_id)

            ledgers = db.query(FundingLedger).filter(
                FundingLedger.reference_type == "settlement",
                FundingLedger.reference_id == settlement_id,
            ).all()
            self.assertEqual(len(ledgers), 0)

        finally:
            db.close()

    # --------------------------------------------------
    # 6) Settlement 부분 UNIQUE가 중복만 차단
    # --------------------------------------------------

    def test_partial_unique_index_blocks_duplicate_settlement_ledger(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            repo = FundingRepository(db)

            first = FundingLedger(
                company_id=self.company_id,
                account_id=account_id,
                amount=5000.0,
                type=FundingService.TYPE_ADD,
                reference_type="settlement",
                reference_id=settlement_id,
            )
            repo.add_ledger_no_commit(first)
            db.commit()

            second = FundingLedger(
                company_id=self.company_id,
                account_id=account_id,
                amount=5000.0,
                type=FundingService.TYPE_ADD,
                reference_type="settlement",
                reference_id=settlement_id,
            )

            # add_ledger_no_commit 자체가 내부에서 flush()를 수행하므로
            # 위반은 이 호출 안에서 즉시 발생한다.
            with self.assertRaises(IntegrityError):
                repo.add_ledger_no_commit(second)

            db.rollback()

            count = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_ADD,
            )
            self.assertEqual(len(count), 1)

        finally:
            db.close()

    # --------------------------------------------------
    # 7) 다른 reference_type의 반복 Ledger는 차단하지 않음
    # --------------------------------------------------

    def test_partial_unique_index_does_not_block_other_reference_types(self):

        account_id = self._create_account(100000.0)
        order_id = 4242

        db = self.SessionLocal()

        try:
            repo = FundingRepository(db)

            first = FundingLedger(
                company_id=self.company_id,
                account_id=account_id,
                amount=1000.0,
                type=FundingService.TYPE_HOLD_CREATE,
                reference_type="order",
                reference_id=order_id,
            )
            repo.add_ledger_no_commit(first)
            db.commit()

            # 동일 (reference_id, type) 조합이지만 reference_type='order'.
            # Hold 재활성화 시나리오에서 반복 발생할 수 있으므로
            # 부분 유일 인덱스가 이를 차단하면 안 된다.
            second = FundingLedger(
                company_id=self.company_id,
                account_id=account_id,
                amount=1000.0,
                type=FundingService.TYPE_HOLD_CREATE,
                reference_type="order",
                reference_id=order_id,
            )
            repo.add_ledger_no_commit(second)
            db.commit()

            count = (
                db.query(FundingLedger)
                .filter(FundingLedger.reference_type == "order")
                .filter(FundingLedger.reference_id == order_id)
                .filter(FundingLedger.type == FundingService.TYPE_HOLD_CREATE)
                .count()
            )
            self.assertEqual(count, 2)

        finally:
            db.close()

    # --------------------------------------------------
    # 8) Transaction 중간 실패 시 전체 rollback
    # --------------------------------------------------

    def test_transaction_rolls_back_fully_on_mid_failure(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)

            # Ledger INSERT flush + Account 반영까지는 실제로 수행되고,
            # 그 다음 조건부 상태 UPDATE 단계에서 강제로 실패시켜
            # 전체(Account/Ledger/Settlement) rollback을 검증한다.
            # 인스턴스 레벨 patch를 사용해(클래스 레벨 patch의 self
            # 바인딩 문제를 피함) 이 세션의 repository만 영향받는다.
            with mock.patch.object(
                service.repository,
                "confirm_deposit_conditional",
                side_effect=RuntimeError("simulated mid-transaction failure"),
            ):
                with self.assertRaises(RuntimeError):
                    service.confirm_deposit(settlement_id, self.company_id)

            settlement = db.query(MarketplaceSettlement).filter(
                MarketplaceSettlement.id == settlement_id,
            ).first()
            self.assertEqual(settlement.status, SettlementService.STATUS_PENDING)
            self.assertIsNone(settlement.funding_ledger_id)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

            ledgers = db.query(FundingLedger).filter(
                FundingLedger.reference_type == "settlement",
                FundingLedger.reference_id == settlement_id,
            ).all()
            self.assertEqual(len(ledgers), 0)

        finally:
            db.close()

    # --------------------------------------------------
    # 9) IntegrityError 경쟁 패자 복구 경로
    # --------------------------------------------------
    #
    # 주의(미검증 한계): 이 테스트는 두 세션이 "동시에" SQLite 파일 잠금을
    # 다투는 상황을 실스레드/프로세스로 재현하지 않는다. 이 프로젝트의
    # SQLite 연결은 기본 rollback-journal 모드라, 한 세션이 읽기
    # 트랜잭션을 연 채로 다른 세션이 커밋을 시도하면 잠금 대기/타임아웃이
    # 발생해 테스트가 느려지거나 환경에 따라 실패할 수 있다(진짜 동시성
    # 재현이 신뢰할 수 없음). 따라서:
    #   - "경쟁 시작 시점의 stale PENDING 스냅샷"과
    #   - "flush 시점에 부분 유일 인덱스 위반으로 IntegrityError 발생"
    #   두 지점만 결정적으로 구성(get()의 반환값 고정 / apply_settlement_*
    #   가 IntegrityError를 던지도록 지정)하고, 그 이후의 rollback →
    #   재조회 → 기존 결과 반환 로직은 100% 실제 프로덕션 코드 경로로
    #   검증한다. 실제 다중 프로세스/스레드 기반의 진짜 동시 요청
    #   재현은 이 unittest 스위트로는 검증하지 못했다(별도 부하 테스트 필요).

    def test_confirm_deposit_integrity_error_race_loser_returns_existing(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        pre_race_db = self.SessionLocal()
        stale_settlement = SettlementRepository(pre_race_db).get(settlement_id)
        self.assertEqual(stale_settlement.status, SettlementService.STATUS_PENDING)
        pre_race_db.expunge(stale_settlement)
        pre_race_db.close()

        winner_db = self.SessionLocal()
        winner_result = SettlementService(winner_db).confirm_deposit(settlement_id, self.company_id)
        self.assertEqual(winner_result.status, SettlementService.STATUS_DEPOSITED)
        winner_db.close()

        loser_db = self.SessionLocal()
        loser_service = SettlementService(loser_db)

        try:
            with mock.patch.object(
                SettlementService,
                "get",
                return_value=stale_settlement,
            ), mock.patch.object(
                FundingService,
                "apply_settlement_deposit",
                side_effect=IntegrityError(
                    "INSERT INTO funding_ledgers ...",
                    {},
                    Exception(
                        "UNIQUE constraint failed: "
                        "uq_funding_ledger_settlement_type",
                    ),
                ),
            ):
                loser_result = loser_service.confirm_deposit(settlement_id, self.company_id)

            self.assertEqual(loser_result.id, settlement_id)
            self.assertEqual(
                loser_result.status, SettlementService.STATUS_DEPOSITED,
            )

            ledgers = self._settlement_ledgers(
                loser_db, settlement_id, FundingService.TYPE_ADD,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(loser_db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 105000.0)

        finally:
            loser_db.close()

    def test_reverse_integrity_error_race_loser_returns_existing(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        setup_db = self.SessionLocal()
        SettlementService(setup_db).confirm_deposit(settlement_id, self.company_id)
        setup_db.close()

        pre_race_db = self.SessionLocal()
        stale_settlement = SettlementRepository(pre_race_db).get(settlement_id)
        self.assertEqual(
            stale_settlement.status, SettlementService.STATUS_DEPOSITED,
        )
        pre_race_db.expunge(stale_settlement)
        pre_race_db.close()

        winner_db = self.SessionLocal()
        winner_result = SettlementService(winner_db).reverse(settlement_id, self.company_id)
        self.assertEqual(winner_result.status, SettlementService.STATUS_REVERSED)
        winner_db.close()

        loser_db = self.SessionLocal()
        loser_service = SettlementService(loser_db)

        try:
            with mock.patch.object(
                SettlementService,
                "get",
                return_value=stale_settlement,
            ), mock.patch.object(
                FundingService,
                "apply_settlement_reverse",
                side_effect=IntegrityError(
                    "INSERT INTO funding_ledgers ...",
                    {},
                    Exception(
                        "UNIQUE constraint failed: "
                        "uq_funding_ledger_settlement_type",
                    ),
                ),
            ):
                loser_result = loser_service.reverse(settlement_id, self.company_id)

            self.assertEqual(
                loser_result.status, SettlementService.STATUS_REVERSED,
            )

            ledgers = self._settlement_ledgers(
                loser_db, settlement_id, FundingService.TYPE_REMOVE,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(loser_db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

        finally:
            loser_db.close()

    # --------------------------------------------------
    # 11) 조건부 상태 UPDATE의 rowcount 동작 직접 검증
    # --------------------------------------------------

    def test_conditional_update_methods_rowcount(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            repo = SettlementRepository(db)

            rows = repo.confirm_deposit_conditional(
                settlement_id=settlement_id,
                account_id=account_id,
                funding_ledger_id=999,
                deposited_at=datetime.utcnow(),
            )
            self.assertEqual(rows, 1)
            db.commit()

            settlement = repo.get(settlement_id)
            self.assertEqual(
                settlement.status, SettlementService.STATUS_DEPOSITED,
            )
            self.assertEqual(settlement.funding_ledger_id, 999)

            # 이미 DEPOSITED인 상태에서 다시 시도하면 WHERE status='PENDING'이
            # 매치되지 않아 rowcount가 0이어야 한다.
            rows_again = repo.confirm_deposit_conditional(
                settlement_id=settlement_id,
                account_id=account_id,
                funding_ledger_id=999,
                deposited_at=datetime.utcnow(),
            )
            self.assertEqual(rows_again, 0)
            db.rollback()

            # account_id가 어긋나면(다른 계정 소유로 위장) rowcount가
            # 0이어야 한다 — 2026-08-14 테넌트 격리 감사에서 추가.
            rows_wrong_account = repo.confirm_deposit_conditional(
                settlement_id=settlement_id,
                account_id=account_id + 999999,
                funding_ledger_id=999,
                deposited_at=datetime.utcnow(),
            )
            self.assertEqual(rows_wrong_account, 0)
            db.rollback()

            rows_reverse = repo.reverse_conditional(
                settlement_id=settlement_id,
                account_id=account_id,
            )
            self.assertEqual(rows_reverse, 1)
            db.commit()

            settlement = repo.get(settlement_id)
            self.assertEqual(
                settlement.status, SettlementService.STATUS_REVERSED,
            )

            rows_reverse_again = repo.reverse_conditional(
                settlement_id=settlement_id,
                account_id=account_id,
            )
            self.assertEqual(rows_reverse_again, 0)
            db.rollback()

        finally:
            db.close()

    # --------------------------------------------------
    # 12) confirm 중 상태 경쟁 변경 → 전체 rollback
    # --------------------------------------------------
    #
    # 주의(미검증 한계): 진짜 별도 커넥션이 우리 세션의 트랜잭션이
    # 열려 있는 동안 동시에 같은 행을 커밋하는 상황은 SQLite의 단일
    # writer 잠금 때문에 이 프로세스 안에서 신뢰성 있게 재현할 수
    # 없다(별도 커넥션이 쓰기 잠금을 얻으려 시도하면 우리 세션이 이미
    # 보유한 잠금으로 인해 blocking/timeout된다). 따라서 "조건부
    # UPDATE 실행 직전에 status가 더 이상 PENDING이 아니게 됨"이라는
    # 결과만 같은 세션 안에서 재현해, rowcount!=1 → 전체 rollback
    # 경로가 실제로 동작하는지 검증한다. 진짜 다중 커넥션 경쟁은
    # 별도 부하 테스트가 필요하다.

    def test_confirm_deposit_rolls_back_when_status_changes_concurrently(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            real_conditional = service.repository.confirm_deposit_conditional

            def _side_effect(*args, **kwargs):

                db.execute(
                    update(MarketplaceSettlement)
                    .where(MarketplaceSettlement.id == settlement_id)
                    .values(status=SettlementService.STATUS_CANCELLED)
                )

                return real_conditional(*args, **kwargs)

            with mock.patch.object(
                service.repository,
                "confirm_deposit_conditional",
                side_effect=_side_effect,
            ):
                with self.assertRaises(ConflictException):
                    service.confirm_deposit(settlement_id, self.company_id)

            settlement = db.query(MarketplaceSettlement).filter(
                MarketplaceSettlement.id == settlement_id,
            ).first()
            self.assertEqual(settlement.status, SettlementService.STATUS_PENDING)
            self.assertIsNone(settlement.funding_ledger_id)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_ADD,
            )
            self.assertEqual(len(ledgers), 0)

        finally:
            db.close()

    # --------------------------------------------------
    # 13) reverse 중 상태 경쟁 변경 → 전체 rollback
    # --------------------------------------------------
    # 주의(미검증 한계): 12번과 동일한 이유로 실제 별도 커넥션 간
    # 경쟁이 아니라 같은 세션 안에서 결과만 재현한다.

    def test_reverse_rolls_back_when_status_changes_concurrently(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        setup_db = self.SessionLocal()
        SettlementService(setup_db).confirm_deposit(settlement_id, self.company_id)
        setup_db.close()

        db = self.SessionLocal()

        try:
            service = SettlementService(db)
            real_conditional = service.repository.reverse_conditional

            def _side_effect(*args, **kwargs):

                db.execute(
                    update(MarketplaceSettlement)
                    .where(MarketplaceSettlement.id == settlement_id)
                    .values(status=SettlementService.STATUS_CANCELLED)
                )

                return real_conditional(*args, **kwargs)

            with mock.patch.object(
                service.repository,
                "reverse_conditional",
                side_effect=_side_effect,
            ):
                with self.assertRaises(ConflictException):
                    service.reverse(settlement_id, self.company_id)

            settlement = db.query(MarketplaceSettlement).filter(
                MarketplaceSettlement.id == settlement_id,
            ).first()
            self.assertEqual(
                settlement.status, SettlementService.STATUS_DEPOSITED,
            )

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 105000.0)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_REMOVE,
            )
            self.assertEqual(len(ledgers), 0)

        finally:
            db.close()

    # --------------------------------------------------
    # 14) 기존 Ledger만 있고 Settlement 상태가 미완료인 불일치 차단
    # --------------------------------------------------

    def test_confirm_deposit_blocks_when_ledger_exists_but_status_pending(self):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        db = self.SessionLocal()

        try:
            # 과거 버그로 인한 "부분 commit" 잔재를 흉내낸다: Settlement는
            # 아직 PENDING인데 동일 settlement_id에 대한 FUNDING_ADD
            # Ledger가 이미 존재하는(불일치) 상태.
            historical_ledger = FundingLedger(
                company_id=self.company_id,
                account_id=account_id,
                amount=5000.0,
                type=FundingService.TYPE_ADD,
                reference_type="settlement",
                reference_id=settlement_id,
            )
            db.add(historical_ledger)
            db.commit()

            service = SettlementService(db)

            with self.assertRaises(IntegrityError):
                service.confirm_deposit(settlement_id, self.company_id)

            settlement = db.query(MarketplaceSettlement).filter(
                MarketplaceSettlement.id == settlement_id,
            ).first()
            self.assertEqual(settlement.status, SettlementService.STATUS_PENDING)
            self.assertIsNone(settlement.funding_ledger_id)

            ledgers = self._settlement_ledgers(
                db, settlement_id, FundingService.TYPE_ADD,
            )
            self.assertEqual(len(ledgers), 1)

            account = FundingService(db)._get_account_required(account_id)
            self.assertEqual(float(account.total_funding), 100000.0)

        finally:
            db.close()

    # --------------------------------------------------
    # 15) IntegrityError 복구 시 Ledger 금액/계좌 불일치면 반환 금지
    # --------------------------------------------------

    def test_confirm_deposit_integrity_error_recovery_rejects_amount_mismatch(
        self,
    ):

        account_id = self._create_account(100000.0)
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        pre_race_db = self.SessionLocal()
        stale_settlement = SettlementRepository(pre_race_db).get(settlement_id)
        pre_race_db.expunge(stale_settlement)
        pre_race_db.close()

        winner_db = self.SessionLocal()
        winner_result = SettlementService(winner_db).confirm_deposit(
            settlement_id,
            self.company_id,
        )
        self.assertEqual(winner_result.status, SettlementService.STATUS_DEPOSITED)
        winner_db.close()

        # 실제 커밋된 winner Ledger 금액을 손상시켜(과거 버그로 잘못
        # 기록된 상황을 흉내) Settlement.net_amount와 어긋나게 만든다.
        corrupt_db = self.SessionLocal()
        corrupt_db.execute(
            update(FundingLedger)
            .where(FundingLedger.reference_type == "settlement")
            .where(FundingLedger.reference_id == settlement_id)
            .where(FundingLedger.type == FundingService.TYPE_ADD)
            .values(amount=9999.0)
        )
        corrupt_db.commit()
        corrupt_db.close()

        loser_db = self.SessionLocal()
        loser_service = SettlementService(loser_db)

        try:
            with mock.patch.object(
                SettlementService,
                "get",
                return_value=stale_settlement,
            ), mock.patch.object(
                FundingService,
                "apply_settlement_deposit",
                side_effect=IntegrityError(
                    "INSERT INTO funding_ledgers ...",
                    {},
                    Exception(
                        "UNIQUE constraint failed: "
                        "uq_funding_ledger_settlement_type",
                    ),
                ),
            ):
                with self.assertRaises(IntegrityError):
                    loser_service.confirm_deposit(settlement_id, self.company_id)

        finally:
            loser_db.close()

    def test_confirm_deposit_integrity_error_recovery_rejects_account_mismatch(
        self,
    ):

        account_id = self._create_account(100000.0)
        other_company_id = self._create_company()
        other_account_id = self._create_account(
            50000.0, company_id=other_company_id,
        )
        settlement_id = self._create_settlement(account_id, net_amount=5000.0)

        pre_race_db = self.SessionLocal()
        stale_settlement = SettlementRepository(pre_race_db).get(settlement_id)
        pre_race_db.expunge(stale_settlement)
        pre_race_db.close()

        winner_db = self.SessionLocal()
        winner_result = SettlementService(winner_db).confirm_deposit(
            settlement_id,
            self.company_id,
        )
        self.assertEqual(winner_result.status, SettlementService.STATUS_DEPOSITED)
        winner_db.close()

        # 실제 커밋된 winner Ledger의 account_id를 손상시켜 Settlement의
        # account_id와 어긋나게 만든다.
        corrupt_db = self.SessionLocal()
        corrupt_db.execute(
            update(FundingLedger)
            .where(FundingLedger.reference_type == "settlement")
            .where(FundingLedger.reference_id == settlement_id)
            .where(FundingLedger.type == FundingService.TYPE_ADD)
            .values(account_id=other_account_id)
        )
        corrupt_db.commit()
        corrupt_db.close()

        loser_db = self.SessionLocal()
        loser_service = SettlementService(loser_db)

        try:
            with mock.patch.object(
                SettlementService,
                "get",
                return_value=stale_settlement,
            ), mock.patch.object(
                FundingService,
                "apply_settlement_deposit",
                side_effect=IntegrityError(
                    "INSERT INTO funding_ledgers ...",
                    {},
                    Exception(
                        "UNIQUE constraint failed: "
                        "uq_funding_ledger_settlement_type",
                    ),
                ),
            ):
                with self.assertRaises(IntegrityError):
                    loser_service.confirm_deposit(settlement_id, self.company_id)

        finally:
            loser_db.close()

    # --------------------------------------------------
    # 10) Hold / Supplier Payment 회귀 여부
    # --------------------------------------------------
    #
    # 주의(미검증 한계): Purchase(purchases)는 orders.id/suppliers.id를
    # 실제 ForeignKey로 참조하고, orders/products/suppliers/brands/
    # categories/inventories가 relationship() 문자열로 서로 등록되어야
    # SQLAlchemy mapper 설정이 성립한다. 이 7개 모듈을 전부 테스트에
    # 끌어들이는 것은 이번 Settlement Hardening Whitelist 범위(Product
    # 도메인 전체)를 초과하므로, 가짜 orders/suppliers 대체 테이블을
    # 만들거나 SQLite FK 검사를 끄는 대신 — purchases 테이블 생성 자체를
    # 하지 않고 confirm_supplier_payment 내부의 "Purchase 단건 조회"
    # 지점만, 이 테스트에서 쓰는 db 세션 인스턴스 하나에 한정해 대체한다
    # (클래스 전역 patch 아님 — 다른 Session/스레드에는 영향 없음).
    # ensure_supply_hold는 Purchase 테이블을 전혀 조회하지 않으므로
    # (purchase_id는 FK 없는 논리 참조 정수로만 저장됨) mock 없이 100%
    # 실제 DB/실제 코드로 검증한다. Purchase 실제 스키마·조인 무결성
    # 자체는 이 테스트로 검증되지 않는다(별도 Product/Order 통합 테스트
    # 필요).

    def test_hold_and_supplier_payment_flow_regression(self):

        account_id = self._create_account(50000.0)

        db = self.SessionLocal()

        try:
            funding_service = FundingService(db)
            order_id = 9001

            # ensure_supply_hold는 Purchase 테이블을 조회하지 않는다 —
            # 실제 프로덕션 코드 경로 그대로, mock 없이 검증한다.
            hold = funding_service.ensure_supply_hold(
                order_id=order_id,
                amount=10000.0,
                purchase_id=1,
            )
            self.assertEqual(hold.status, FundingService.STATUS_HELD)

            account = funding_service._get_account_required(account_id)
            self.assertEqual(float(account.held_amount), 10000.0)

            fake_purchase = SimpleNamespace(
                id=1,
                order_id=order_id,
                supplier_id=1,
                purchase_price=10000.0,
                shipping_price=0.0,
                total_price=10000.0,
            )

            # 이 테스트의 db 인스턴스에서만 query()를 가로챈다.
            # entity가 Purchase일 때만 가짜 결과를 반환하고, 그 외
            # (FundingHold/FundingAccount/SupplierPayment/FundingLedger
            # 등) 모든 조회는 원래의 실제 query()로 그대로 위임한다.
            real_query = db.query

            def _query_side_effect(entity, *args, **kwargs):

                if entity is Purchase:
                    stub = mock.MagicMock()
                    stub.filter.return_value.first.return_value = (
                        fake_purchase
                    )
                    return stub

                return real_query(entity, *args, **kwargs)

            with mock.patch.object(
                db,
                "query",
                side_effect=_query_side_effect,
            ):
                payment = funding_service.confirm_supplier_payment(
                    fake_purchase.id,
                )

            self.assertEqual(payment.status, FundingService.PAYMENT_PAID)
            self.assertEqual(float(payment.amount), 10000.0)

            # 중복 지급 방지: 동일 purchase_id 재확정 시도는 차단되어야 한다.
            with mock.patch.object(
                db,
                "query",
                side_effect=_query_side_effect,
            ):
                with self.assertRaises(BadRequestException):
                    funding_service.confirm_supplier_payment(
                        fake_purchase.id,
                    )

            account = funding_service._get_account_required(account_id)
            self.assertEqual(float(account.held_amount), 0.0)
            self.assertEqual(float(account.total_funding), 40000.0)

            payments = (
                db.query(SupplierPayment)
                .filter(SupplierPayment.purchase_id == fake_purchase.id)
                .all()
            )
            self.assertEqual(len(payments), 1)

            payout_ledgers = (
                db.query(FundingLedger)
                .filter(
                    FundingLedger.reference_type == "supplier_payment",
                )
                .filter(FundingLedger.reference_id == payment.id)
                .filter(FundingLedger.type == FundingService.TYPE_FUNDING_PAYOUT)
                .all()
            )
            self.assertEqual(len(payout_ledgers), 1)

        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
