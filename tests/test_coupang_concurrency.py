"""
=========================================================
Homez OS

File : tests/test_coupang_concurrency.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation
동시성/Transaction 검증: 조건부 상태 전이, rowcount 검증, 동시 승인
경쟁, 멱등성 경쟁(idempotency_key UNIQUE + IntegrityError 복구).

실스레드 + 별도 커넥션으로 검증한다(product_candidate/settlement
하드닝과 동일한 패턴).
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.coupang.constants import IntegrationStatus
from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangProductNoticeInput
from app.domains.coupang.schema import CoupangProductOptionInput
from app.domains.coupang.schema import CoupangProfitEstimateRequest
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection



COMPANY_ID = 1
class CoupangConcurrencyTestCase(unittest.TestCase):
    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                CoupangMarketplaceProduct.__table__,
                CoupangProductOption.__table__,
                CoupangPolicySet.__table__,
                CoupangPolicyRule.__table__,
                CoupangProductNotice.__table__,
                CoupangProfitEstimate.__table__,
                CoupangDryRunAttempt.__table__,
                CoupangIntegrationDecision.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self._seed_baseline_verified_sellable_policy()

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_baseline_verified_sellable_policy(self):

        db = self.SessionLocal()
        try:
            policy_set = CoupangPolicySet(
                policy_set_id="test-verified-baseline",
                policy_version="test-v1",
                source_reference="test-source",
                status=PolicySetStatus.VERIFIED,
                is_complete=True,
                is_active=True,
                checked_at=datetime.utcnow() - timedelta(days=1),
                effective_at=datetime.utcnow() - timedelta(days=1),
                expires_at=None,
            )
            db.add(policy_set)
            db.commit()

            rule = CoupangPolicyRule(
                policy_set_id=policy_set.id,
                match_field=PolicyMatchField.DISPLAY_CATEGORY_CODE,
                match_value="cat-001",
                risk_level=RiskLevel.SELLABLE,
                reason="테스트 기본 판매 가능 카테고리",
            )
            db.add(rule)
            db.commit()
        finally:
            db.close()

    def _threaded_engine(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        return engine2, sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

    def _dry_run_passed_product_id(self, SessionLocal2, idempotency_key):

        setup_db = SessionLocal2()

        try:
            candidate = ProductCandidate(
                candidate_key=f"COUPANG_API:COUPANG:{idempotency_key}",
                source_type="COUPANG_API",
                market="COUPANG",
                source_reference=idempotency_key,
                product_name="동시성 테스트 상품",
                status=CandidateStatus.APPROVED,
            )
            setup_db.add(candidate)
            setup_db.commit()

            service = CoupangIntegrationService(setup_db)

            product, _dup, _events = service.create_draft(
                CoupangDraftCreateRequest(
                    product_candidate_id=candidate.id,
                    sales_method="MARKETPLACE",
                    external_vendor_sku=f"SKU-{idempotency_key}",
                    seller_product_name="동시성 테스트 상품",
                    idempotency_key=f"draft-{idempotency_key}",
                    brand="HOMEZ",
                    display_category_code="CAT-001",
                    gtin="8801234567890",
                    sale_price=Decimal("29900"),
                    shipping_method="COURIER",
                    outbound_shipping_place_code="OUT-1",
                    return_center_code="RET-1",
                    options=[
                        CoupangProductOptionInput(
                            option_name="기본", option_value="기본",
                            vendor_sku=f"SKU-{idempotency_key}-A",
                            price=Decimal("29900"), stock=10,
                        ),
                    ],
                    notices=[
                        CoupangProductNoticeInput(
                            notice_category_name="생활용품",
                            notice_category_detail_name="일반공산품",
                            content="품명: 동시성 테스트 상품",
                            source="test-source",
                            verified_at=datetime.utcnow(),
                            status="VERIFIED",
                        ),
                    ],
                ),
                company_id=COMPANY_ID,
                correlation_id="setup1",
            )
            service.validate_policy(product.id, COMPANY_ID, correlation_id="setup2")
            service.estimate_profit(
                product.id,
                COMPANY_ID,
                CoupangProfitEstimateRequest(
                    consumer_sale_price=Decimal("29900"),
                    supplier_product_cost=Decimal("10000"),
                    supplier_shipping_cost=Decimal("2000"),
                    coupang_sales_fee=Decimal("2000"),
                ),
                correlation_id="setup3",
            )
            service.run_dry_run(
                product.id, COMPANY_ID, idempotency_key=f"dr-{idempotency_key}",
                correlation_id="setup4",
            )

            return product.id

        finally:
            setup_db.close()

    # --------------------------------------------------
    # 동시 최종 승인 경쟁 — 정확히 하나만 성공
    # --------------------------------------------------

    def test_concurrent_approve_for_submission_only_one_succeeds(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            product_id = self._dry_run_passed_product_id(
                SessionLocal2, "approve-race",
            )

            results = {}
            barrier = threading.Barrier(2)

            def worker(name, idempotency_key):

                thread_db = SessionLocal2()
                service = CoupangIntegrationService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    _product, _dup, _events = service.approve_for_submission(
                        product_id, COMPANY_ID, operator_id=1, is_admin=True,
                        idempotency_key=idempotency_key,
                        memo=f"{name} 승인", correlation_id=name,
                    )
                    results[name] = "ok"
                except ConflictException:
                    # 조건부 UPDATE의 rowcount==0으로 패배를 감지한 경우.
                    results[name] = "rejected"
                except BadRequestException:
                    # 패자가 self.get()으로 상태를 읽는 시점이 승자의
                    # commit 이후라면, 조건부 UPDATE에 도달하기도 전에
                    # "이미 DRY_RUN_PASSED가 아니다"로 거부된다 — 이것도
                    # 안전한 거부 경로다(정확히 하나만 성공한다는 속성은
                    # 동일하게 보장된다).
                    results[name] = "rejected"
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(
                target=worker, args=("thread-a", "approve-key-a"),
            )
            t2 = threading.Thread(
                target=worker, args=("thread-b", "approve-key-b"),
            )
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            self.assertEqual(
                outcomes.count("ok"), 1,
                f"정확히 하나만 성공해야 합니다: {results}",
            )
            self.assertEqual(
                outcomes.count("rejected"), 1,
                f"나머지 하나는 안전하게 거부되어야 합니다: {results}",
            )

            verify_db = SessionLocal2()
            try:
                service = CoupangIntegrationService(verify_db)
                product = service.get(product_id, COMPANY_ID)
                self.assertEqual(
                    product.status, IntegrationStatus.READY_FOR_SUBMISSION,
                )

                decisions = service.list_decisions(product_id, COMPANY_ID)
                self.assertEqual(len(decisions), 1)
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 동일 idempotency_key로 승인 재요청 — 중복 실행 없이 동일 결과 반환
    # --------------------------------------------------

    def test_concurrent_same_idempotency_key_approve_creates_one_decision(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            product_id = self._dry_run_passed_product_id(
                SessionLocal2, "idem-race",
            )

            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = CoupangIntegrationService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    _product, duplicate, _events = (
                        service.approve_for_submission(
                            product_id, COMPANY_ID, operator_id=1, is_admin=True,
                            idempotency_key="same-key",
                            memo="동일 키", correlation_id=name,
                        )
                    )
                    results[name] = "duplicate" if duplicate else "created"
                except ConflictException:
                    results[name] = "conflict"
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            # 둘 다 성공적으로 처리되어야 하며(에러/충돌 없이), 그 중
            # 정확히 하나만 "created"(최초 반영), 나머지는 "duplicate"다.
            self.assertNotIn("conflict", outcomes)
            self.assertNotIn(
                True,
                [o.startswith("error:") for o in outcomes],
            )
            self.assertEqual(outcomes.count("created"), 1)
            self.assertEqual(outcomes.count("duplicate"), 1)

            verify_db = SessionLocal2()
            try:
                service = CoupangIntegrationService(verify_db)
                decisions = service.list_decisions(product_id, COMPANY_ID)
                self.assertEqual(len(decisions), 1)
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 동시 초안 생성 경쟁(동일 idempotency_key) — 정확히 하나만 생성
    # --------------------------------------------------

    def test_concurrent_create_draft_same_idempotency_key_creates_one_row(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            candidate = ProductCandidate(
                candidate_key="COUPANG_API:COUPANG:DRAFT-RACE",
                source_type="COUPANG_API",
                market="COUPANG",
                source_reference="DRAFT-RACE",
                product_name="초안 경쟁 테스트 상품",
                status=CandidateStatus.APPROVED,
            )
            setup_db.add(candidate)
            setup_db.commit()
            candidate_id = candidate.id
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = CoupangIntegrationService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    product, duplicate, _events = service.create_draft(
                        CoupangDraftCreateRequest(
                            product_candidate_id=candidate_id,
                            sales_method="MARKETPLACE",
                            external_vendor_sku="SKU-DRAFT-RACE",
                            seller_product_name="초안 경쟁 테스트 상품",
                            idempotency_key="draft-race-key",
                            options=[
                                CoupangProductOptionInput(
                                    option_name="기본", option_value="기본",
                                    vendor_sku="SKU-DRAFT-RACE-A",
                                    price=Decimal("1000"), stock=1,
                                ),
                            ],
                        ),
                        company_id=COMPANY_ID,
                correlation_id=name,
                    )
                    results[name] = product.id
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            product_ids = list(results.values())
            self.assertEqual(len(set(product_ids)), 1)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(CoupangMarketplaceProduct)
                    .filter(
                        CoupangMarketplaceProduct.idempotency_key
                        == "draft-race-key",
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 동시 정책 검증 경쟁 — 정확히 하나만 성공(fail-closed 경로 포함)
    # --------------------------------------------------

    def test_concurrent_validate_policy_only_one_succeeds(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            candidate = ProductCandidate(
                candidate_key="COUPANG_API:COUPANG:POLICY-RACE",
                source_type="COUPANG_API",
                market="COUPANG",
                source_reference="POLICY-RACE",
                product_name="정책 검증 경쟁 테스트 상품",
                status=CandidateStatus.APPROVED,
            )
            setup_db.add(candidate)
            setup_db.commit()

            service = CoupangIntegrationService(setup_db)
            product, _dup, _events = service.create_draft(
                CoupangDraftCreateRequest(
                    product_candidate_id=candidate.id,
                    sales_method="MARKETPLACE",
                    external_vendor_sku="SKU-POLICY-RACE",
                    seller_product_name="정책 검증 경쟁 테스트 상품",
                    idempotency_key="draft-policy-race",
                    brand="HOMEZ",
                    display_category_code="CAT-001",
                    gtin="8801234567890",
                    sale_price=Decimal("29900"),
                    shipping_method="COURIER",
                    outbound_shipping_place_code="OUT-1",
                    return_center_code="RET-1",
                    options=[
                        CoupangProductOptionInput(
                            option_name="기본", option_value="기본",
                            vendor_sku="SKU-POLICY-RACE-A",
                            price=Decimal("29900"), stock=10,
                        ),
                    ],
                ),
                company_id=COMPANY_ID,
                correlation_id="setup1",
            )
            product_id = product.id
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = CoupangIntegrationService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    service.validate_policy(product_id, COMPANY_ID, correlation_id=name)
                    results[name] = "ok"
                except (ConflictException, BadRequestException):
                    results[name] = "rejected"
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            self.assertEqual(
                outcomes.count("ok"), 1,
                f"정확히 하나만 성공해야 합니다: {results}",
            )
            self.assertEqual(
                outcomes.count("rejected"), 1,
                f"나머지 하나는 안전하게 거부되어야 합니다: {results}",
            )

            verify_db = SessionLocal2()
            try:
                service = CoupangIntegrationService(verify_db)
                product = service.get(product_id, COMPANY_ID)
                self.assertNotEqual(product.status, "DRAFT")
            finally:
                verify_db.close()

        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
