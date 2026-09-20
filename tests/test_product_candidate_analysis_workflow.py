"""
=========================================================
Homez OS

File : tests/test_product_candidate_analysis_workflow.py

V7 워크플로우 완성(2026-08-19) — 수동(PRIVATE_MANUAL) 후보를 위한
verify_private_candidate_info()/recommend() 사용자 API 계약 검증.

2026-08-19 CTO 보완 지시로 전면 재작성됨 — 이전 버전은
analyze_private_candidate()가 입력 필드 개수로 novelty_score/
confidence를 계산해 저장했다. 이 값은 어떤 공식 데이터·검증된 모델·
확정된 제품 정책에서도 나오지 않은 추측성 점수였다(CTO 지적).
verify_private_candidate_info()는 점수를 전혀 계산하지 않고, "기본
정보가 등록되어 있음을 확인했다"는 사실만 근거로 남긴다.

- 정보 확인 후 novelty_score/confidence는 항상 None(추측 금지)
- 근거는 MANUAL_INFO_CHECK 타입, score/confidence 둘 다 None
- DISCOVERED에서만 정보 확인 가능(그 외 상태는 BadRequestException)
- 동시 정보 확인 실행 중 하나만 성공(진짜 race, sleep으로 숨기지 않음)
- 회사 격리(다른 회사 PRIVATE 후보는 보이지 않음, get()/get_visible_
  for_company() 둘 다에서 검증)
- 정보 확인 완료 후에만 추천 가능(recommend()는 이제 company_id
  필수 — 다른 회사 PRIVATE 후보에 대한 IDOR 재확인 포함)
=========================================================
"""

import os
import tempfile
import threading
import unittest
import unittest.mock as mock
import ast
import inspect
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.constants import EvidenceType
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService

COMPANY_A = 1
COMPANY_B = 2


class ProductCandidateAnalysisWorkflowTestCase(unittest.TestCase):

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
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = ProductCandidateService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                # Windows에서 동시성 테스트가 연 두 번째 SQLite 커넥션이
                # 스레드 종료 직후 아직 완전히 해제되지 않은 경우가 있다
                # — 임시 파일이라 OS가 나중에 정리하며, 테스트 결과에는
                # 영향이 없다(기존 test_product_candidate.py의 동일
                # 패턴에서도 발생 가능한 환경 의존적 현상).
                pass

    def _create_private(self, company_id=COMPANY_A, ref="REF-1", **overrides):

        data = ProductCandidatePrivateCreate(
            source_reference=ref,
            market="COUPANG",
            product_name="테스트 상품",
            **overrides,
        )
        candidate, _events = self.service.register_private_candidate(
            data, company_id=company_id, correlation_id="c1",
        )
        return candidate

    def test_private_candidate_router_passes_only_supported_service_keywords(self):
        """라우터와 Service의 키워드 계약이 어긋나면 UI 생성이 즉시 실패한다."""

        from app.domains.product_candidate import router as candidate_router

        tree = ast.parse(inspect.getsource(candidate_router.register_private_candidate))
        call = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_private_candidate"
        )
        routed_keywords = {keyword.arg for keyword in call.keywords}
        service_parameters = set(
            inspect.signature(
                ProductCandidateService.register_private_candidate,
            ).parameters
        )

        self.assertLessEqual(routed_keywords, service_parameters)

    # --------------------------------------------------
    # 추측성 점수 제거 검증 — 어떤 입력 조합이든 점수는 항상 None
    # --------------------------------------------------

    def test_verify_info_never_writes_novelty_score_with_no_optional_fields(self):

        candidate = self._create_private()
        verified, _events = self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        self.assertEqual(verified.status, CandidateStatus.ANALYZED)
        self.assertIsNone(verified.novelty_score)
        self.assertIsNone(verified.confidence)

    def test_verify_info_never_writes_novelty_score_with_all_optional_fields(self):
        """
        이전 버전은 category_hint/brand_hint/release_date를 모두 채우면
        novelty_score가 1.0으로 올라갔다(입력 완성도를 신제품성으로
        오인). 이제는 필드를 아무리 채워도 점수를 계산하지 않는다 —
        입력 필드 개수는 상품의 시장성과 무관하다는 CTO 지적을 코드로
        강제한다.
        """

        candidate = self._create_private(
            category_hint="생활용품", brand_hint="테스트브랜드",
        )
        candidate.release_date = date(2026, 9, 1)
        self.db.commit()

        verified, _events = self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        self.assertIsNone(verified.novelty_score)
        self.assertIsNone(verified.confidence)

    def test_verify_info_records_manual_info_check_evidence_with_no_score(self):

        candidate = self._create_private()
        self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        evidence_rows = self.service.list_evidence(candidate.id, COMPANY_A)
        info_check_rows = [
            e for e in evidence_rows
            if e.evidence_type == EvidenceType.MANUAL_INFO_CHECK
        ]

        self.assertEqual(len(info_check_rows), 1)
        self.assertIsNone(info_check_rows[0].score)
        self.assertIsNone(info_check_rows[0].confidence)
        # NEW_PRODUCT_ANALYSIS(실제 AI 분석 전용 타입)로 잘못 기록되지
        # 않았는지 — 근거 타입 자체가 "AI 분석 아님"을 드러내야 한다.
        self.assertFalse(
            any(
                e.evidence_type == EvidenceType.NEW_PRODUCT_ANALYSIS
                for e in evidence_rows
            ),
        )

    def test_verify_info_evidence_text_never_implies_market_analysis(self):
        """
        evidence 문구가 "분석"이라는 표현으로 실제 시장 판단이 있었던
        것처럼 보이지 않는지 확인한다 — "시장 분석"이 실행되지
        않았다는 사실을 evidence 자체가 명시해야 한다.
        """

        candidate = self._create_private()
        self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        evidence_rows = self.service.list_evidence(candidate.id, COMPANY_A)
        summaries = [e.payload_summary for e in evidence_rows]

        self.assertTrue(any("시장 분석" in s and "않" in s for s in summaries))
        self.assertTrue(any("외부 AI" in s for s in summaries))

    # --------------------------------------------------
    # 상태 전제조건
    # --------------------------------------------------

    def test_verify_info_rejects_non_discovered_candidate(self):

        candidate = self._create_private()
        self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        with self.assertRaises(BadRequestException):
            self.service.verify_private_candidate_info(
                candidate.id, company_id=COMPANY_A, correlation_id="a2",
            )

    def test_recommend_requires_analyzed_status(self):

        candidate = self._create_private()

        with self.assertRaises(BadRequestException):
            self.service.recommend(
                candidate.id, company_id=COMPANY_A, correlation_id="r1",
            )

    def test_recommend_blocked_without_real_score_even_after_verify_info(self):
        """
        [정책 A, 2026-08-19] ANALYZED만으로는 실제 추천 근거를
        보장하지 않는다 — MANUAL_INFO_CHECK만 거친(점수 없는) 후보는
        ANALYZED 상태라도 recommend()를 직접 호출할 수 없어야 한다.
        UI가 버튼을 숨기는 것과 별개로 API 자체가 차단해야 하는 계약.
        """

        candidate = self._create_private()
        self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        with self.assertRaises(BadRequestException):
            self.service.recommend(
                candidate.id, company_id=COMPANY_A, correlation_id="r1",
            )

        unchanged = self.service.get_visible_for_company(candidate.id, COMPANY_A)
        self.assertEqual(unchanged.status, CandidateStatus.ANALYZED)

    def test_recommend_succeeds_with_real_trend_score(self):
        """
        실제 AI 분석 파이프라인(apply_trend_analysis)을 거쳐 real
        trend_score가 있는 후보는 recommend()가 정상적으로 통과해야
        한다 — 정책 A가 기존 실제 분석 흐름을 깨지 않았는지 확인.
        """

        candidate = self._create_private()
        self.service.apply_trend_analysis(
            candidate.id, company_id=COMPANY_A, trend_score=0.7,
            confidence=0.8, evidence_text="실제 트렌드 분석",
            correlation_id="t1",
        )

        recommended, _events = self.service.recommend(
            candidate.id, company_id=COMPANY_A, correlation_id="r1",
        )

        self.assertEqual(recommended.status, CandidateStatus.RECOMMENDED)

    # --------------------------------------------------
    # 관리자 수동 승인에 RECOMMENDED가 필수가 아님(기존 계약,
    # CandidateStatus.DECIDABLE = (ANALYZED, RECOMMENDED), Gate R13)을
    # 이 워크플로우에서도 그대로 재확인한다 — "추천"과 "관리자 수동
    # 승인"을 같은 의미로 취급하지 않는다는 CTO 지시를 검증한다.
    # --------------------------------------------------

    def test_admin_can_approve_directly_from_analyzed_without_recommend(self):

        candidate = self._create_private()
        self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        # recommend()를 호출하지 않고 바로 승인 — DECIDABLE에 ANALYZED가
        # 이미 포함되어 있으므로 허용되어야 한다(기존 계약, 이번 세션
        # 변경 없음).
        new_status, _events = self.service.approve(
            candidate.id, company_id=COMPANY_A, operator_id=1,
            is_admin=True, memo=None, correlation_id="ap1",
        )

        self.assertEqual(new_status, CandidateStatus.APPROVED)

    # --------------------------------------------------
    # 회사 격리
    # --------------------------------------------------

    def test_verify_info_blocked_for_other_companys_private_candidate(self):

        candidate = self._create_private(company_id=COMPANY_A)

        with self.assertRaises(NotFoundException):
            self.service.verify_private_candidate_info(
                candidate.id, company_id=COMPANY_B, correlation_id="a1",
            )

        # 격리 실패로 상태·점수가 전혀 바뀌지 않았는지 재확인
        untouched = self.service.get(candidate.id)
        self.assertEqual(untouched.status, CandidateStatus.DISCOVERED)
        self.assertIsNone(untouched.novelty_score)

    def test_recommend_blocked_for_other_companys_private_candidate(self):
        """
        recommend()가 이제 company_id를 필수로 받아 내부에서
        get_visible_for_company()를 직접 거친다(2026-08-19 CTO 보완
        지시 — 이전에는 self.get()을 써서 Service 레벨 방어가 전혀
        없었다). 회사 B가 회사 A의 PRIVATE 후보 id를 알아도 추천으로
        전환할 수 없어야 한다.
        """

        candidate = self._create_private(company_id=COMPANY_A)
        self.service.verify_private_candidate_info(
            candidate.id, company_id=COMPANY_A, correlation_id="a1",
        )

        with self.assertRaises(NotFoundException):
            self.service.recommend(
                candidate.id, company_id=COMPANY_B, correlation_id="r1",
            )

        untouched = self.service.get(candidate.id)
        self.assertEqual(untouched.status, CandidateStatus.ANALYZED)

    def test_apply_trend_analysis_blocked_for_other_companys_private_candidate(self):
        """
        2026-08-19 CTO 보완 지시("잔존 위험" 항목 대응) — 이전에는
        apply_trend_analysis()가 self.get()(가시성 검사 없음)을 써서
        Router가 없어도 잠재적 IDOR였다. company_id를 필수로 받아
        get_visible_for_company()를 거치도록 고쳤으므로, 회사 B가
        회사 A의 PRIVATE 후보 id로 이 메서드를 호출해도 차단돼야
        한다.
        """

        candidate = self._create_private(company_id=COMPANY_A)

        with self.assertRaises(NotFoundException):
            self.service.apply_trend_analysis(
                candidate.id, company_id=COMPANY_B, trend_score=0.9,
                confidence=0.9, evidence_text="타사 시도",
                correlation_id="hostile1",
            )

        untouched = self.service.get(candidate.id)
        self.assertEqual(untouched.status, CandidateStatus.DISCOVERED)
        self.assertIsNone(untouched.trend_score)

    # --------------------------------------------------
    # 진짜 동시성 — sleep/재시도로 숨기지 않는다
    # --------------------------------------------------

    def test_concurrent_verify_info_only_one_succeeds(self):
        """두 요청이 모두 status=DISCOVERED를 읽고 쓰기 단계에 들어선 뒤
        조건부 UPDATE(DISCOVERED→ANALYZED)에서 충돌하는 지점을 barrier로
        고정해 재현한다. 이전 버전은 메인 스레드 Session의 (커밋으로 만료된)
        ORM 객체 `candidate.id`를 두 워커가 동시에 읽어, 서비스 코드에
        닿기도 전에 InterfaceError/ObjectDeletedError로 끝나는 경우가 있었다
        (테스트 하네스 결함 — 스레드마다 Session은 따로였지만 인자를 만드는
        과정에서 메인 Session을 공유했다). 여기서는 id를 원시 값으로 먼저
        고정하고, 실제 두 스레드·두 SQLite 커넥션 위에서 정확히 한 요청만
        전이하고 다른 요청은 ConflictException을 받는지 확인한다."""

        candidate = self._create_private()
        candidate_id = candidate.id

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        # 두 요청 모두 사전 상태 검사(DISCOVERED)를 통과한 뒤 쓰기 단계 직전에
        # 만나게 한다 — 이 시점 이후에는 조건부 UPDATE만이 승자를 정한다.
        barrier = threading.Barrier(2)
        results = {}
        winner_events = {}
        dispatched = []

        def recording_dispatch(db, event_type, **kwargs):
            dispatched.append((event_type, kwargs.get("idempotency_key")))

        def worker(key, session_factory):
            thread_db = session_factory()
            service = ProductCandidateService(thread_db)
            real_add_evidence = service.repository.add_evidence_no_commit

            def synced_add_evidence(evidence):
                barrier.wait(timeout=10)
                return real_add_evidence(evidence)

            service.repository.add_evidence_no_commit = synced_add_evidence
            try:
                _candidate, events = service.verify_private_candidate_info(
                    candidate_id, company_id=COMPANY_A,
                    correlation_id=f"race-{key}",
                )
                results[key] = "ok"
                winner_events[key] = list(events)
            except (ConflictException, BadRequestException) as e:
                results[key] = type(e).__name__
            except Exception as e:  # noqa: BLE001
                results[key] = f"error:{type(e).__name__}"
            finally:
                thread_db.close()

        with mock.patch(
            "app.domains.product_candidate.service.dispatch_operational_event",
            recording_dispatch,
        ):
            t1 = threading.Thread(
                target=worker, args=("t1", self.SessionLocal),
            )
            t2 = threading.Thread(
                target=worker, args=("t2", SessionLocal2),
            )
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)

        self.assertFalse(t1.is_alive() or t2.is_alive(), "워커가 끝나지 않았다.")

        engine2.dispose()

        self.assertEqual(sorted(results), ["t1", "t2"])
        outcomes = list(results.values())
        self.assertEqual(outcomes.count("ok"), 1, results)
        self.assertEqual(
            outcomes.count("ConflictException"), 1,
            "패자는 정의된 충돌 결과(ConflictException)를 받아야 한다.",
        )

        # 승자만 이벤트를 만들고, 알림 디스패치도 정확히 1번이다.
        self.assertEqual(len(winner_events), 1)
        self.assertEqual(len(next(iter(winner_events.values()))), 1)
        self.assertEqual(
            dispatched,
            [("CANDIDATE_REVIEW_NEEDED", f"candidate-review:{candidate_id}:ANALYZED")],
        )

        verify_db = self.SessionLocal()
        try:
            verify_db.expire_all()
            final = (
                verify_db.query(ProductCandidate)
                .filter(ProductCandidate.id == candidate_id)
                .first()
            )
            self.assertEqual(final.status, CandidateStatus.ANALYZED)
            self.assertIsNone(final.novelty_score)

            # 등록 시 만들어진 RAW_SOURCE 1건 + 승자의 MANUAL_INFO_CHECK 1건만
            # 있어야 한다 — 패자의 근거 행은 롤백되어 남지 않는다(중복 기록 없음).
            evidence_types = sorted(
                row.evidence_type
                for row in verify_db.query(ProductCandidateEvidence)
                .filter(ProductCandidateEvidence.candidate_id == candidate_id)
                .all()
            )
            self.assertEqual(
                evidence_types,
                sorted([EvidenceType.RAW_SOURCE, EvidenceType.MANUAL_INFO_CHECK]),
            )
        finally:
            verify_db.close()


if __name__ == "__main__":
    unittest.main()
