"""
=========================================================
Homez OS

File : tests/test_product_candidate.py

HOMEZ V3 ProductCandidate 계약 검증
멱등 발견, 근거 append-only, 상태 전이, 운영자 승인 강제.

2026-08-14 테넌트 격리 감사(Gate R13) 반영 — approve/hold/reject는
이제 company_id를 필수로 받고, ProductCandidate.status(전역)가 아니라
ProductCandidateSelection(회사별 투영)에 반영된다. 이 파일은 단일
회사(COMPANY_ID=1) 기준의 기존 계약(멱등 발견/근거/상태 전이/운영자
승인 강제/동시성)을 그대로 검증한다 — 회사 간 격리 시나리오는
tests/test_product_candidate_tenant_isolation.py에서 별도로 검증한다.
=========================================================
"""

import os
import tempfile
import threading
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.database.base import Base
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.repository import ProductCandidateRepository
from app.domains.product_candidate.schema import ProductCandidateDiscover
from app.domains.product_candidate.service import ProductCandidateService

COMPANY_ID = 1


class ProductCandidateTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                ProductCandidateEvidence.__table__,
                ProductCandidateDecision.__table__,
                ProductCandidateSelection.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _discover_data(self, ref="REF-1", name="테스트 상품"):

        return ProductCandidateDiscover(
            source_type="COUPANG_API",
            source_reference=ref,
            market="COUPANG",
            product_name=name,
            category_hint="생활용품",
            brand_hint=None,
        )

    # --------------------------------------------------
    # 1) 멱등 발견 (동일 source+reference 중복 방지)
    # --------------------------------------------------

    def test_discover_is_idempotent_and_preserves_original_evidence(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)

            first, first_events = service.discover(
                self._discover_data(), correlation_id="corr-1",
            )
            self.assertEqual(first.status, CandidateStatus.DISCOVERED)
            self.assertEqual(len(first_events), 2)  # SOURCE_DISCOVERED + CANDIDATE_CREATED

            # 다른 product_name으로 재수집 시도 — 동일 candidate_key.
            second, second_events = service.discover(
                self._discover_data(name="다른 이름으로 재수집"),
                correlation_id="corr-2",
            )

            self.assertEqual(first.id, second.id)
            self.assertEqual(len(second_events), 0)  # 신규 생성 Event 없음
            # 원본 근거(최초 product_name)는 덮어써지지 않는다.
            self.assertEqual(second.product_name, "테스트 상품")

            evidence_rows = service.list_evidence(first.id, COMPANY_ID)
            self.assertEqual(len(evidence_rows), 2)  # 최초 발견 + 재수집 기록
            self.assertIn("최초 발견", evidence_rows[0].payload_summary)
            self.assertIn("재수집", evidence_rows[1].payload_summary)

        finally:
            db.close()

    def test_different_source_reference_creates_separate_candidates(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)

            a, _ = service.discover(
                self._discover_data(ref="REF-A"), correlation_id="c1",
            )
            b, _ = service.discover(
                self._discover_data(ref="REF-B"), correlation_id="c2",
            )

            self.assertNotEqual(a.id, b.id)

        finally:
            db.close()

    # --------------------------------------------------
    # 2) 상태 전이 (DISCOVERED → ANALYZED → RECOMMENDED, 승인은 회사별)
    # --------------------------------------------------

    def test_full_status_transition_requires_operator_approval(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)

            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            self.assertEqual(candidate.status, CandidateStatus.DISCOVERED)

            candidate = service.get(candidate.id)
            self.assertEqual(candidate.status, CandidateStatus.DISCOVERED)

            _events = service.apply_trend_analysis(
                candidate.id,
                company_id=COMPANY_ID,
                trend_score=0.8,
                confidence=0.9,
                evidence_text="상승 추세",
                correlation_id="c2",
            )
            candidate = service.get(candidate.id)
            self.assertEqual(candidate.status, CandidateStatus.ANALYZED)
            self.assertEqual(candidate.trend_score, 0.8)

            service.apply_new_product_analysis(
                candidate.id,
                company_id=COMPANY_ID,
                is_new_product=True,
                novelty_score=0.9,
                confidence=0.9,
                evidence_text="출시 5일차",
                correlation_id="c3",
            )
            candidate = service.get(candidate.id)
            # 이미 ANALYZED였으므로 상태는 그대로 유지(재진입해도 되돌아가지 않음)
            self.assertEqual(candidate.status, CandidateStatus.ANALYZED)
            self.assertTrue(candidate.is_new_product)
            self.assertEqual(candidate.novelty_score, 0.9)

            recommended, events = service.recommend(
                candidate.id, company_id=COMPANY_ID, correlation_id="c4",
            )
            self.assertEqual(recommended.status, CandidateStatus.RECOMMENDED)
            self.assertEqual(len(events), 1)

            # 운영자 승인 없이는 이 시점에서도 이 회사 관점이 APPROVED가 아니다.
            self.assertEqual(
                service.get_effective_status_for_company(
                    candidate.id, COMPANY_ID,
                ),
                CandidateStatus.RECOMMENDED,
            )

            new_status, approve_events = service.approve(
                candidate.id,
                company_id=COMPANY_ID,
                operator_id=99,
                is_admin=True,
                memo="승인합니다",
                correlation_id="c5",
            )
            self.assertEqual(new_status, CandidateStatus.APPROVED)
            self.assertEqual(len(approve_events), 1)

            # 전역 워크플로우 상태는 approve로 바뀌지 않는다(회사별로
            # 옮겨졌다) — RECOMMENDED 그대로 유지.
            unchanged_global = service.get(candidate.id)
            self.assertEqual(
                unchanged_global.status, CandidateStatus.RECOMMENDED,
            )

            self.assertEqual(
                service.get_effective_status_for_company(
                    candidate.id, COMPANY_ID,
                ),
                CandidateStatus.APPROVED,
            )

            decisions = service.list_decisions(candidate.id, COMPANY_ID)
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0].operator_id, 99)
            self.assertEqual(decisions[0].company_id, COMPANY_ID)

        finally:
            db.close()

    def test_evidence_layers_are_never_overwritten(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)

            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )

            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.3, confidence=0.5,
                evidence_text="1차 분석", correlation_id="c2",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.7, confidence=0.8,
                evidence_text="2차 분석(재분석)", correlation_id="c3",
            )

            evidence_rows = service.list_evidence(candidate.id, COMPANY_ID)
            trend_evidence = [
                e for e in evidence_rows if e.evidence_type == "TREND_ANALYSIS"
            ]
            # 두 번의 분석 모두 근거로 남아있어야 한다(덮어쓰기 없음).
            self.assertEqual(len(trend_evidence), 2)
            self.assertEqual(trend_evidence[0].score, 0.3)
            self.assertEqual(trend_evidence[1].score, 0.7)

            # 현재 상태(투영)는 최신 값을 반영한다.
            current = service.get(candidate.id)
            self.assertEqual(current.trend_score, 0.7)

        finally:
            db.close()

    # --------------------------------------------------
    # 3) 운영자 승인 강제
    # --------------------------------------------------

    def test_approve_without_admin_is_blocked(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.5, confidence=0.5,
                evidence_text="분석", correlation_id="c2",
            )

            with self.assertRaises(ForbiddenException):
                service.approve(
                    candidate.id, company_id=COMPANY_ID,
                    operator_id=1, is_admin=False,
                    memo=None, correlation_id="c3",
                )

            self.assertNotEqual(
                service.get_effective_status_for_company(
                    candidate.id, COMPANY_ID,
                ),
                CandidateStatus.APPROVED,
            )

        finally:
            db.close()

    def test_approve_from_discovered_state_is_rejected(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            # 아직 DISCOVERED — DECIDABLE 상태가 아님.
            with self.assertRaises(BadRequestException):
                service.approve(
                    candidate.id, company_id=COMPANY_ID,
                    operator_id=1, is_admin=True,
                    memo=None, correlation_id="c2",
                )

        finally:
            db.close()

    def test_reject_from_recommended_state(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.1, confidence=0.5,
                evidence_text="낮은 점수", correlation_id="c2",
            )
            service.recommend(candidate.id, company_id=COMPANY_ID, correlation_id="c3")

            new_status, events = service.reject(
                candidate.id, company_id=COMPANY_ID,
                operator_id=5, is_admin=True,
                memo="위험 높음", correlation_id="c4",
            )
            self.assertEqual(new_status, CandidateStatus.REJECTED)
            self.assertEqual(len(events), 1)

        finally:
            db.close()

    def test_hold_then_redecide_to_approve(self):
        """회사가 HOLD로 결정한 뒤 같은 회사가 APPROVE로 재결정할 수 있다."""

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.5, confidence=0.5,
                evidence_text="분석", correlation_id="c2",
            )
            service.recommend(candidate.id, company_id=COMPANY_ID, correlation_id="c3")

            new_status, _events = service.hold(
                candidate.id, company_id=COMPANY_ID,
                operator_id=1, is_admin=True,
                memo="보류", correlation_id="c4",
            )
            self.assertEqual(new_status, CandidateStatus.HELD)

            new_status, _events = service.approve(
                candidate.id, company_id=COMPANY_ID,
                operator_id=1, is_admin=True,
                memo="재검토 후 승인", correlation_id="c5",
            )
            self.assertEqual(new_status, CandidateStatus.APPROVED)

            decisions = service.list_decisions(candidate.id, COMPANY_ID)
            self.assertEqual(len(decisions), 2)

        finally:
            db.close()

    def test_redecide_after_terminal_selection_is_rejected(self):
        """이미 APPROVED/REJECTED로 결정한 회사는 재결정할 수 없다."""

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.5, confidence=0.5,
                evidence_text="분석", correlation_id="c2",
            )
            service.recommend(candidate.id, company_id=COMPANY_ID, correlation_id="c3")

            service.approve(
                candidate.id, company_id=COMPANY_ID,
                operator_id=1, is_admin=True,
                memo=None, correlation_id="c4",
            )

            with self.assertRaises(BadRequestException):
                service.reject(
                    candidate.id, company_id=COMPANY_ID,
                    operator_id=1, is_admin=True,
                    memo=None, correlation_id="c5",
                )

        finally:
            db.close()

    # --------------------------------------------------
    # 4) Domain 경계: Funding/Order/Purchase Model 미참조
    # --------------------------------------------------

    def test_product_candidate_does_not_import_funding_or_order_models(self):

        import app.domains.product_candidate.model as pc_model
        import app.domains.product_candidate.service as pc_service

        for module in (pc_model, pc_service):
            with open(module.__file__, encoding="utf-8") as f:
                content = f.read()

            self.assertNotIn("app.domains.funding.model", content)
            self.assertNotIn("app.domains.settlement.model", content)
            self.assertNotIn("app.domains.order.model", content)
            self.assertNotIn("app.domains.purchase.model", content)

    # --------------------------------------------------
    # 5) 부분 성공 rollback — 결정 반영 중간 실패 시 전체 되돌림
    # --------------------------------------------------

    def test_decision_mid_failure_rolls_back_decision_and_status(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.5, confidence=0.5,
                evidence_text="분석", correlation_id="c2",
            )
            service.recommend(candidate.id, company_id=COMPANY_ID, correlation_id="c3")

            with mock.patch.object(
                service.repository,
                "add_selection_no_commit",
                side_effect=RuntimeError("simulated mid-transaction failure"),
            ):
                with self.assertRaises(RuntimeError):
                    service.approve(
                        candidate.id, company_id=COMPANY_ID,
                        operator_id=1, is_admin=True,
                        memo="승인 시도", correlation_id="c4",
                    )

            unchanged = service.get(candidate.id)
            self.assertEqual(unchanged.status, CandidateStatus.RECOMMENDED)

            decisions = service.list_decisions(candidate.id, COMPANY_ID)
            self.assertEqual(
                len(decisions), 0,
                "실패한 결정 시도의 ProductCandidateDecision이 rollback되지 "
                "않고 남아있음",
            )

            self.assertIsNone(
                service.repository.get_selection(candidate.id, COMPANY_ID),
                "실패한 결정 시도의 ProductCandidateSelection이 rollback되지 "
                "않고 남아있음",
            )

        finally:
            db.close()

    def test_recommend_mid_failure_rolls_back_status(self):

        db = self.SessionLocal()

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_ID, trend_score=0.5, confidence=0.5,
                evidence_text="분석", correlation_id="c2",
            )

            with mock.patch.object(
                ProductCandidateRepository,
                "update_status_conditional",
                side_effect=RuntimeError("simulated failure"),
            ):
                with self.assertRaises(RuntimeError):
                    service.recommend(candidate.id, company_id=COMPANY_ID, correlation_id="c3")

            unchanged = service.get(candidate.id)
            self.assertEqual(unchanged.status, CandidateStatus.ANALYZED)

        finally:
            db.close()

    # --------------------------------------------------
    # 6) 동시 approve/reject 중 하나만 성공 (같은 회사)
    # --------------------------------------------------

    def test_concurrent_approve_and_reject_only_one_succeeds(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        setup_db = SessionLocal2()
        setup_service = ProductCandidateService(setup_db)
        candidate, _ = setup_service.discover(
            self._discover_data(), correlation_id="setup",
        )
        setup_service.apply_trend_analysis(
            candidate.id, company_id=COMPANY_ID, trend_score=0.5, confidence=0.5,
            evidence_text="분석", correlation_id="setup2",
        )
        setup_service.recommend(candidate.id, company_id=COMPANY_ID, correlation_id="setup3")
        candidate_id = candidate.id
        setup_db.close()

        results = {}
        barrier = threading.Barrier(2)

        def approve_worker():

            thread_db = SessionLocal2()
            service = ProductCandidateService(thread_db)

            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

            try:
                service.approve(
                    candidate_id, company_id=COMPANY_ID,
                    operator_id=1, is_admin=True,
                    memo="승인 스레드", correlation_id="approve-thread",
                )
                results["approve"] = "ok"
            except ConflictException:
                results["approve"] = "conflict"
            except Exception as e:  # noqa: BLE001
                results["approve"] = f"error:{type(e).__name__}"
            finally:
                thread_db.close()

        def reject_worker():

            thread_db = SessionLocal2()
            service = ProductCandidateService(thread_db)

            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

            try:
                service.reject(
                    candidate_id, company_id=COMPANY_ID,
                    operator_id=2, is_admin=True,
                    memo="거절 스레드", correlation_id="reject-thread",
                )
                results["reject"] = "ok"
            except ConflictException:
                results["reject"] = "conflict"
            except Exception as e:  # noqa: BLE001
                results["reject"] = f"error:{type(e).__name__}"
            finally:
                thread_db.close()

        t1 = threading.Thread(target=approve_worker)
        t2 = threading.Thread(target=reject_worker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        try:
            outcomes = list(results.values())
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(
                outcomes.count("ok"), 1,
                f"정확히 하나만 성공해야 함: {results}",
            )
            self.assertEqual(outcomes.count("conflict"), 1)

            verify_db = SessionLocal2()
            try:
                # 전역 워크플로우 상태는 이제 approve/reject로 바뀌지
                # 않는다 — RECOMMENDED 그대로다.
                final = (
                    verify_db.query(ProductCandidate)
                    .filter(ProductCandidate.id == candidate_id)
                    .first()
                )
                self.assertEqual(final.status, CandidateStatus.RECOMMENDED)

                selections = (
                    verify_db.query(ProductCandidateSelection)
                    .filter(
                        ProductCandidateSelection.candidate_id
                        == candidate_id,
                    )
                    .all()
                )
                self.assertEqual(
                    len(selections), 1,
                    "패자의 Selection 행이 rollback되지 않고 남아있음",
                )
                self.assertIn(
                    selections[0].status,
                    (CandidateStatus.APPROVED, CandidateStatus.REJECTED),
                )

                decisions = (
                    verify_db.query(ProductCandidateDecision)
                    .filter(
                        ProductCandidateDecision.candidate_id
                        == candidate_id,
                    )
                    .all()
                )
                self.assertEqual(
                    len(decisions), 1,
                    "패자의 Decision 행이 rollback되지 않고 남아있음",
                )
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 7) candidate_key 동시 생성 경쟁 → IntegrityError 복구
    # --------------------------------------------------

    def test_concurrent_discover_same_key_creates_exactly_one_candidate(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        results = {}
        barrier = threading.Barrier(2)

        def worker(i):

            thread_db = SessionLocal2()
            service = ProductCandidateService(thread_db)

            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

            try:
                candidate, _events = service.discover(
                    self._discover_data(ref="RACE-REF"),
                    correlation_id=f"race-{i}",
                )
                results[i] = candidate.id
            finally:
                thread_db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=30)

        try:
            self.assertEqual(len(results), 2)
            self.assertEqual(
                results[0], results[1],
                f"동시 discover가 서로 다른 후보를 만들었음: {results}",
            )

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(ProductCandidate)
                    .filter(ProductCandidate.candidate_key.like("%RACE-REF%"))
                    .count()
                )
                self.assertEqual(
                    count, 1,
                    "동시 discover 경쟁으로 candidate_key 중복 행이 생성됨",
                )
            finally:
                verify_db.close()

        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
