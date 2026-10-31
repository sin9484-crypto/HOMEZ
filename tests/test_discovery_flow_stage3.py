"""
=========================================================
Homez OS

File : tests/test_discovery_flow_stage3.py

2026-10-31 — AI 통합 계획 3단계('판정기'를 '탐색기'로, 외부 호출 0 — 가짜 트렌드 Adapter):
  - 검색어 규칙: 상품명의 괄호·상품코드·용량·수량·묶음·판촉·색상·브랜드를 지우고, 핵심어는 고르지 않는다(길면 알림).
  - 트렌드 갱신: 상품명 전체 대신 규칙 검색어(또는 사람이 확인한 검색어)를 보낸다. 검색어가 비면 조회하지 않는다.
  - 신제품 판정: 후보의 출시일만 쓴다. 모르면·미래면 아무것도 저장하지 않는다('모름'을 0점으로 저장하지 않는다).
  - 탐색 흐름: 읽기 전용으로 후보별 단계·다음 할 일을 보여 준다(회사 격리, 최신 평가 기준).
  - 쉬운 보기 '탐색 흐름' 탭: 검색어를 확인 창에서 고친 뒤 조회, 승인은 하지 않는다.
=========================================================
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import date
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.decision.model import DecisionEvaluation
from app.domains.new_product_discovery.service import KST
from app.domains.product_candidate import router as router_module
from app.domains.product_candidate.discovery_flow_service import NEXT_ORDER
from app.domains.product_candidate.discovery_flow_service import DiscoveryFlowService
from app.domains.product_candidate.discovery_flow_service import next_step
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.new_product_analysis_refresh_service import NewProductAnalysisRefreshService
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.schema import TrendAnalysisRefreshRequest
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_candidate.trend_analysis_refresh_service import TrendAnalysisRefreshService
from app.domains.trend_discovery.adapter import TrendSignal
from app.domains.trend_discovery.adapter import TrendSourceAdapter
from app.domains.trend_discovery.keyword_rule import MAX_KEYWORD_CHARS
from app.domains.trend_discovery.keyword_rule import extract_search_keyword
from app.domains.trend_discovery.keyword_rule import normalize_user_keyword

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "web" / "easy" / "easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app" / "web" / "easy" / "easy_texts.js").read_text(encoding="utf-8")

COMPANY_A = 1
COMPANY_B = 2
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=KST)


class StubAdapter(TrendSourceAdapter):

    def __init__(self, known: dict[str, list[float]] | None = None):
        self.known = known or {}
        self.calls: list[str] = []

    def fetch(self, keyword: str):
        self.calls.append(keyword)
        series = self.known.get(keyword)
        if series is None:
            return None
        return TrendSignal(keyword=keyword, market="NAVER_SHOPPING", search_volume_series=series,
                           observed_at=datetime.now().isoformat(), source="stub")


# ---------------------------------------------------------------------------
# 검색어 규칙
# ---------------------------------------------------------------------------

class KeywordRuleTests(unittest.TestCase):

    def test_noise_is_removed_from_real_candidate_names(self):
        cases = [
            ("백산수 생수 500ml x 20PET", "백산수", "생수"),
            ("레이펄스 워터리스 바디워시 200ml (본품)", None, "레이펄스 워터리스 바디워시"),
            ("러닝 무릎보호대 슬개건밴드 블랙 2P", None, "러닝 무릎보호대 슬개건밴드"),
            ("G125669_7부 골지 폴라 니트 티셔츠", None, "골지 폴라 니트 티셔츠"),
            ("AB1234-니트 티셔츠", None, "니트 티셔츠"),
            ("기모레깅스 스타킹 (유발,9부,고리)", None, "기모레깅스 스타킹"),
            ("어린이 양말 반스타킹 1켤레", None, "어린이 양말 반스타킹"),
            ("1+1 무료배송 3단 선반 2.5kg", None, "선반"),
            ("2+1 물티슈 100매 x 10팩", None, "물티슈"),
            ("무선청소기", None, "무선청소기"),
        ]
        for name, brand, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(extract_search_keyword(name, brand).keyword, expected)

    def test_words_that_carry_meaning_are_not_cut(self):
        # 숫자가 단어의 일부인 경우(모델명·규격)는 지우지 않는다 — '65W'를 '6'과 '5W'로 쪼개지 않는다.
        for name in ("USB-C 충전기 65W", "아이폰15 케이스", "A4 용지", "갤럭시S24 케이스"):
            with self.subTest(name=name):
                self.assertEqual(extract_search_keyword(name).keyword, name)
        self.assertEqual(extract_search_keyword("A4 용지 500매").keyword, "A4 용지")

    def test_removed_pieces_are_reported_and_the_brand_only_when_given(self):
        s = extract_search_keyword("백산수 생수 500ml x 20PET", "백산수")
        self.assertIn("500ml", s.removed)
        self.assertIn("백산수", s.removed)
        self.assertEqual(extract_search_keyword("백산수 생수 500ml").keyword, "백산수 생수")

    def test_long_names_are_flagged_not_guessed(self):
        s = extract_search_keyword("부드럽고 가벼운 간절기 슬림 여성 긴팔 니트 티셔츠")
        self.assertTrue(s.needs_shortening)
        self.assertEqual(s.keyword, "부드럽고 가벼운 간절기 슬림 여성 긴팔 니트 티셔츠")  # 핵심어를 고르지 않는다
        self.assertFalse(extract_search_keyword("방수 생리팬티 위생팬티").needs_shortening)

    def test_overlong_result_is_trimmed_by_whole_words_and_flagged(self):
        s = extract_search_keyword(" ".join(["가나다라마바사아"] * 2 + [f"단어{i}가" for i in range(10)]))
        self.assertLessEqual(len(s.keyword), MAX_KEYWORD_CHARS)
        self.assertTrue(s.needs_shortening)
        self.assertTrue(all(w in s.keyword.split() or w in s.removed for w in s.keyword.split()))

    def test_nothing_left_means_empty_and_rule_is_stable(self):
        self.assertEqual(extract_search_keyword("블랙 (본품) 500ml").keyword, "")
        self.assertEqual(extract_search_keyword(None).keyword, "")
        once = extract_search_keyword("레이펄스 워터리스 바디워시 200ml (본품)").keyword
        self.assertEqual(extract_search_keyword(once).keyword, once)

    def test_person_keyword_is_cleaned_but_never_cut(self):
        self.assertEqual(normalize_user_keyword("  워터리스\t바디워시 \n"), "워터리스 바디워시")
        for bad in ("", "   ", None, "가" * (MAX_KEYWORD_CHARS + 1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_user_keyword(bad)
        self.assertEqual(len(normalize_user_keyword("가" * MAX_KEYWORD_CHARS)), MAX_KEYWORD_CHARS)


# ---------------------------------------------------------------------------
# DB 바탕
# ---------------------------------------------------------------------------

class DbCase(unittest.TestCase):

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine, tables=[
            ProductCandidate.__table__, ProductCandidateEvidence.__table__, ProductCandidateDecision.__table__,
            ProductCandidateSelection.__table__, DecisionEvaluation.__table__,
        ])
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()
        self.service = ProductCandidateService(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def seed(self, company_id, name, brand=None, release=None):
        candidate, _ = self.service.register_private_candidate(
            ProductCandidatePrivateCreate(source_reference=f"ref-{company_id}-{name}", market="COUPANG",
                                          product_name=name, brand_hint=brand, release_date=release),
            company_id=company_id, correlation_id="c",
        )
        return candidate

    def evidence(self, candidate_id):
        self.db.expire_all()
        return self.db.query(ProductCandidateEvidence).filter_by(candidate_id=candidate_id).all()

    def add_decision(self, company_id, candidate_id, status, recommendation="RECOMMEND_HOLD", key="k"):
        row = DecisionEvaluation(
            company_id=company_id, candidate_id=candidate_id, policy_version="v1", input_snapshot_json="{}",
            input_fingerprint="f" * 64, total_score=0.5, confidence=0.6, recommendation=recommendation,
            recommendation_reason="r", status=status, idempotency_key=f"{key}-{candidate_id}-{status}",
            evaluator_kind="RULE", evaluator_version="1",
        )
        self.db.add(row)
        self.db.commit()
        return row


# ---------------------------------------------------------------------------
# 트렌드 갱신이 쓰는 검색어
# ---------------------------------------------------------------------------

class TrendKeywordTests(DbCase):

    def test_rule_keyword_is_sent_instead_of_the_full_name(self):
        c = self.seed(COMPANY_A, "백산수 생수 500ml x 20PET", brand="백산수")
        adapter = StubAdapter({"생수": [10, 20, 40]})
        result = TrendAnalysisRefreshService(self.db, adapter).refresh(c.id, COMPANY_A)
        self.assertEqual(adapter.calls, ["생수"])
        self.assertEqual((result.status, result.keyword, result.keyword_basis), ("APPLIED", "생수", "RULE"))
        trend = [e for e in self.evidence(c.id) if e.evidence_type == "TREND_ANALYSIS"]
        self.assertEqual(len(trend), 1)
        self.assertTrue(trend[0].payload_summary.startswith("검색어 '생수'(규칙); "))

    def test_person_keyword_is_used_as_given(self):
        c = self.seed(COMPANY_A, "부드럽고 가벼운 간절기 슬림 여성 긴팔 니트 티셔츠")
        adapter = StubAdapter({"니트 티셔츠": [5, 6, 9]})
        result = TrendAnalysisRefreshService(self.db, adapter).refresh(c.id, COMPANY_A, keyword="  니트   티셔츠 ")
        self.assertEqual(adapter.calls, ["니트 티셔츠"])
        self.assertEqual((result.status, result.keyword_basis), ("APPLIED", "PERSON"))
        self.assertIn("(사람 확인)", self.evidence(c.id)[-1].payload_summary)

    def test_no_signal_reports_the_keyword_and_changes_nothing(self):
        c = self.seed(COMPANY_A, "러닝 무릎보호대 블랙 2P")
        result = TrendAnalysisRefreshService(self.db, StubAdapter()).refresh(c.id, COMPANY_A)
        self.assertEqual((result.status, result.keyword), ("NO_SIGNAL", "러닝 무릎보호대"))
        self.db.expire_all()
        self.assertIsNone(self.db.get(ProductCandidate, c.id).trend_score)

    def test_empty_keyword_is_not_looked_up(self):
        c = self.seed(COMPANY_A, "블랙 (본품) 500ml")
        adapter = StubAdapter({"": [1, 2, 3]})
        result = TrendAnalysisRefreshService(self.db, adapter).refresh(c.id, COMPANY_A)
        self.assertEqual(result.status, "NO_KEYWORD")
        self.assertEqual(adapter.calls, [])
        self.assertEqual([e for e in self.evidence(c.id) if e.evidence_type == "TREND_ANALYSIS"], [])

    def test_bad_person_keyword_is_refused_before_any_lookup(self):
        c = self.seed(COMPANY_A, "무선청소기")
        adapter = StubAdapter({"무선청소기": [1, 2, 3]})
        for bad in ("", "   ", "가" * (MAX_KEYWORD_CHARS + 1)):
            with self.subTest(bad=bad), self.assertRaises(BadRequestException):
                TrendAnalysisRefreshService(self.db, adapter).refresh(c.id, COMPANY_A, keyword=bad)
        self.assertEqual(adapter.calls, [])

    def test_other_company_is_blocked_before_the_keyword_is_even_checked(self):
        c = self.seed(COMPANY_A, "무선청소기")
        adapter = StubAdapter({"무선청소기": [1, 2, 3]})
        with self.assertRaises(NotFoundException):
            TrendAnalysisRefreshService(self.db, adapter).refresh(c.id, COMPANY_B, keyword="")
        self.assertEqual(adapter.calls, [])


# ---------------------------------------------------------------------------
# 신제품 판정 연결
# ---------------------------------------------------------------------------

class NewProductTests(DbCase):

    def run_for(self, candidate, company=COMPANY_A):
        return NewProductAnalysisRefreshService(self.db).refresh(candidate.id, company, now=NOW)

    def test_unknown_release_date_saves_nothing_and_does_not_unlock_recommend(self):
        c = self.seed(COMPANY_A, "날짜없음")
        result = self.run_for(c)
        self.assertEqual(result.status, "UNKNOWN")
        self.db.expire_all()
        fresh = self.db.get(ProductCandidate, c.id)
        self.assertIsNone(fresh.is_new_product)
        self.assertIsNone(fresh.novelty_score)
        self.assertEqual(self.evidence(c.id)[-1].evidence_type, "RAW_SOURCE")
        self.service.verify_private_candidate_info(c.id, COMPANY_A, correlation_id="v")
        with self.assertRaises(BadRequestException):  # '모름'을 0점으로 저장했다면 여기서 통과해 버린다
            self.service.recommend(c.id, COMPANY_A, correlation_id="r")

    def test_future_release_date_is_left_for_a_person(self):
        c = self.seed(COMPANY_A, "미래", release=date(2026, 11, 1))
        result = self.run_for(c)
        self.assertEqual(result.status, "FUTURE_DATE")
        self.assertIn("미래", result.reason)
        self.db.expire_all()
        self.assertIsNone(self.db.get(ProductCandidate, c.id).novelty_score)

    def test_fifteen_day_rule_is_applied_with_evidence(self):
        cases = [(date(2026, 10, 31), True, 0, 1.0), (date(2026, 10, 16), True, 15, 0.5), (date(2026, 10, 15), False, 16, 0.0)]
        for release, is_new, days, score in cases:
            with self.subTest(release=release):
                c = self.seed(COMPANY_A, f"상품{release}", release=release)
                result = self.run_for(c)
                self.assertEqual((result.status, result.is_new_product, result.age_days, result.novelty_score), ("APPLIED", is_new, days, score))
                self.db.expire_all()
                fresh = self.db.get(ProductCandidate, c.id)
                self.assertEqual((fresh.is_new_product, fresh.novelty_score, fresh.status), (is_new, score, "ANALYZED"))
                ev = self.evidence(c.id)[-1]
                self.assertEqual(ev.evidence_type, "NEW_PRODUCT_ANALYSIS")
                self.assertIn(f"age_days={days}", ev.payload_summary)

    def test_other_company_cannot_run_it(self):
        c = self.seed(COMPANY_A, "남의 것", release=date(2026, 10, 30))
        with self.assertRaises(NotFoundException):
            self.run_for(c, company=COMPANY_B)
        self.db.expire_all()
        self.assertIsNone(self.db.get(ProductCandidate, c.id).is_new_product)


# ---------------------------------------------------------------------------
# 탐색 흐름
# ---------------------------------------------------------------------------

class FlowTests(DbCase):

    def test_next_step_rules(self):
        d = date(2026, 10, 20)
        self.assertEqual(next_step("ANALYZED", "APPROVED", None, None, None, None), "DONE_APPROVED")
        self.assertEqual(next_step("ANALYZED", "REJECTED", 0.9, d, True, "PENDING_REVIEW"), "CLOSED")
        self.assertEqual(next_step("EXPIRED", "EXPIRED", None, None, None, None), "CLOSED")
        self.assertEqual(next_step("ANALYZED", "ANALYZED", None, None, None, "PENDING_REVIEW"), "REVIEW")
        self.assertEqual(next_step("ANALYZED", "ANALYZED", None, None, None, "REVIEWED"), "DONE_REVIEWED")
        self.assertEqual(next_step("DISCOVERED", "DISCOVERED", None, d, None, None), "TREND")
        self.assertEqual(next_step("ANALYZED", "ANALYZED", 0.4, d, None, None), "NEW_PRODUCT")
        self.assertEqual(next_step("ANALYZED", "ANALYZED", 0.4, None, None, None), "EVALUATE")  # 출시일 모름은 막지 않는다
        self.assertEqual(next_step("ANALYZED", "ANALYZED", 0.4, d, False, None), "EVALUATE")
        self.assertEqual(next_step("ANALYZED", "ANALYZED", 0.0, None, None, None), "EVALUATE")  # 0점도 점수다

    def test_flow_rows_steps_order_and_isolation(self):
        a1 = self.seed(COMPANY_A, "백산수 생수 500ml x 20PET", brand="백산수")
        a2 = self.seed(COMPANY_A, "출시 확인", release=date(2026, 10, 25))
        a3 = self.seed(COMPANY_A, "검토 대기")
        a4 = self.seed(COMPANY_A, "나중에 들어온 무선청소기")
        b1 = self.seed(COMPANY_B, "다른 회사 상품")
        self.service.apply_trend_analysis(a2.id, COMPANY_A, 0.7, 0.9, "t", "c")
        self.add_decision(COMPANY_A, a3.id, "REVIEWED", key="old")
        newest = self.add_decision(COMPANY_A, a3.id, "PENDING_REVIEW", recommendation="RECOMMEND_APPROVE", key="new")
        self.add_decision(COMPANY_B, a1.id, "PENDING_REVIEW", key="b")  # 다른 회사의 평가는 보이면 안 된다

        flow = DiscoveryFlowService(self.db).list_flow(COMPANY_A)
        rows = {r["candidate_id"]: r for r in flow["rows"]}
        self.assertEqual(set(rows), {a1.id, a2.id, a3.id, a4.id})
        self.assertNotIn(b1.id, rows)
        # 다음 할 일 순(검토 → 신제품 → 트렌드), 같은 단계 안에서는 최근 후보 먼저 — id 순과 다르다
        self.assertEqual([r["candidate_id"] for r in flow["rows"]], [a3.id, a2.id, a4.id, a1.id])
        self.assertEqual(rows[a3.id]["next"], "REVIEW")
        self.assertEqual(rows[a3.id]["decision"]["id"], newest.id)
        self.assertEqual(rows[a3.id]["decision"]["recommendation"], "RECOMMEND_APPROVE")
        self.assertEqual((rows[a2.id]["next"], rows[a2.id]["new_product_state"], rows[a2.id]["trend_score"]), ("NEW_PRODUCT", "TODO", 0.7))
        self.assertEqual(rows[a2.id]["release_date"], "2026-10-25")
        self.assertEqual((rows[a1.id]["next"], rows[a1.id]["new_product_state"]), ("TREND", "UNKNOWN_DATE"))
        self.assertIsNone(rows[a1.id]["decision"])
        self.assertEqual(rows[a1.id]["keyword"]["keyword"], "생수")
        self.assertEqual(flow["counts"]["REVIEW"], 1)
        self.assertEqual(set(flow["counts"]), set(NEXT_ORDER))
        self.assertFalse(flow["truncated"])
        json.dumps(flow)  # 응답으로 그대로 보낼 수 있어야 한다

    def test_flow_reads_only(self):
        self.seed(COMPANY_A, "읽기만")
        before = len(self.db.query(ProductCandidateEvidence).all())
        DiscoveryFlowService(self.db).list_flow(COMPANY_A)
        self.db.expire_all()
        self.assertEqual(len(self.db.query(ProductCandidateEvidence).all()), before)

    def test_truncated_flag(self):
        for i in range(3):
            self.seed(COMPANY_A, f"상품{i}")
        self.assertTrue(DiscoveryFlowService(self.db).list_flow(COMPANY_A, limit=3)["truncated"])


# ---------------------------------------------------------------------------
# 라우터(작은 앱 — 실제 네이버·Windows 자격증명 저장소는 쓰지 않는다)
# ---------------------------------------------------------------------------

class FakeStore:

    def __init__(self, registered):
        self.registered = registered

    def read(self, reference):
        from app.core.windows_credential_store import CredentialNotFoundError

        if not self.registered:
            raise CredentialNotFoundError(reference)
        return {"client_id": "x", "client_secret": "y"}


class RouterTests(DbCase):
    """라우터 함수를 직접 부른다(이 환경에는 TestClient용 httpx가 없다). 경로·본문 규칙은 작은 앱의 OpenAPI로 확인한다."""

    def setUp(self):
        super().setUp()
        self.adapter = StubAdapter({"생수": [10, 20, 30], "니트 티셔츠": [3, 3, 6]})
        self.user = type("U", (), {"id": 7, "company_id": COMPANY_A, "role": "ADMIN"})()

    def flow(self, store):
        return router_module.get_discovery_flow(limit=200, current_user=self.user, db=self.db, credential_store=store)

    def refresh(self, candidate_id, data=None):
        return router_module.refresh_trend_analysis(candidate_id, data=data, current_user=self.user, db=self.db, adapter=self.adapter)

    def test_flow_endpoint_reports_the_naver_key_without_reading_it_out(self):
        self.seed(COMPANY_A, "백산수 생수 500ml x 20PET", brand="백산수")
        body = self.flow(FakeStore(False))
        self.assertFalse(body["naver_key_registered"])
        self.assertEqual(body["rows"][0]["keyword"]["keyword"], "생수")
        body = self.flow(FakeStore(True))
        self.assertTrue(body["naver_key_registered"])
        self.assertNotIn("client_secret", json.dumps(body, ensure_ascii=False))

    def test_static_path_comes_before_the_candidate_id_path_and_the_body_is_optional(self):
        paths = [r.path for r in router_module.router.routes]
        self.assertLess(paths.index("/product-candidates/discovery-flow"), paths.index("/product-candidates/{candidate_id}"))
        app = FastAPI()
        app.include_router(router_module.router)
        op = app.openapi()["paths"]["/product-candidates/{candidate_id}/refresh-trend-analysis"]["post"]
        self.assertFalse(op.get("requestBody", {}).get("required", False))
        self.assertIn("/product-candidates/{candidate_id}/evaluate-new-product", app.openapi()["paths"])

    def test_refresh_works_with_and_without_a_keyword(self):
        c1 = self.seed(COMPANY_A, "백산수 생수 500ml x 20PET", brand="백산수")
        c2 = self.seed(COMPANY_A, "여성 긴팔 니트 티셔츠 블랙")
        r1 = self.refresh(c1.id)
        self.assertEqual((r1.keyword, r1.keyword_basis, r1.status), ("생수", "RULE", "APPLIED"))
        r2 = self.refresh(c2.id, TrendAnalysisRefreshRequest(keyword="니트 티셔츠"))
        self.assertEqual((r2.keyword_basis, r2.status), ("PERSON", "APPLIED"))
        r3 = self.refresh(c2.id, TrendAnalysisRefreshRequest())  # 본문은 있지만 검색어가 없으면 규칙
        self.assertEqual(r3.keyword_basis, "RULE")
        self.assertEqual(self.adapter.calls, ["생수", "니트 티셔츠", "여성 긴팔 니트 티셔츠"])
        with self.assertRaises(ValidationError):
            TrendAnalysisRefreshRequest(keyword="x", extra=1)

    def test_new_product_endpoint(self):
        c = self.seed(COMPANY_A, "날짜없음")
        res = router_module.evaluate_new_product(c.id, current_user=self.user, db=self.db)
        self.assertEqual(res.status, "UNKNOWN")
        other = self.seed(COMPANY_B, "다른 회사", release=date(2026, 10, 30))
        with self.assertRaises(NotFoundException):
            router_module.evaluate_new_product(other.id, current_user=self.user, db=self.db)

    def test_adapter_dependency_still_builds_the_real_naver_adapter(self):
        src = Path(router_module.__file__).read_text(encoding="utf-8")
        block = src[src.index("def get_trend_adapter("):src.index("router = APIRouter(")]
        self.assertIn("NaverDataLabTrendAdapter(", block)
        self.assertIn("NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE", block)
        self.assertIn("Depends(get_trend_credential_store)", block)
        built = router_module.get_trend_adapter(FakeStore(True))
        self.assertEqual(type(built).__name__, "NaverDataLabTrendAdapter")


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def _fn(name, end_marker):
    start = JS.index(name)
    return JS[start:JS.index(end_marker, start)]


class ScreenTests(unittest.TestCase):

    def flow_block(self):
        return JS[JS.index("// ---- 탐색 흐름(2026-10-31"):JS.index("async function loadAnalysis()")]

    def test_texts_exist_in_both_languages(self):
        block = self.flow_block()
        keys = set(re.findall(r'"(easy\.disc\.[a-z_]+)"', block))
        keys |= {f"easy.disc.next_{k}" for k in ("done_reviewed", "done_approved", "closed")}
        keys.add("easy.an.tab_flow")
        self.assertGreaterEqual(len(keys), 30)
        for key in sorted(keys):
            with self.subTest(key=key):
                self.assertEqual(len(re.findall(rf'"{re.escape(key)}":', TEXTS)), 2)

    def test_the_tab_is_wired_and_never_approves(self):
        self.assertIn('["FLOW", "easy.an.tab_flow", ""]', JS)
        self.assertIn('} else if (tab === "FLOW") {\n      renderFlow(host);', JS.replace("\r\n", "\n"))
        block = self.flow_block()
        for forbidden in ("/approve", "/reject", "/recommend", "/decisions/evaluate", "/reviews"):
            self.assertNotIn(forbidden, block)
        self.assertIn("/refresh-trend-analysis", block)
        self.assertIn("/evaluate-new-product", block)
        self.assertIn("/product-candidates/discovery-flow", block)

    def test_trend_lookup_goes_through_a_confirm_dialog_with_the_keyword(self):
        ask = _fn("async function flowTrendAsk", "\n  async function flowNewProduct")
        self.assertLess(ask.index("await ezDialog("), ask.index("refresh-trend-analysis"))
        self.assertIn('if (!picked) return false;', ask)
        self.assertIn("JSON.stringify({ keyword: checked.keyword })", ask)

    def test_server_text_is_escaped(self):
        block = self.flow_block()
        for needle in ("esc(r.product_name)", "esc(kw.keyword)", "esc(n.text)", 'value="${esc(kw.keyword || "")}"'):
            self.assertIn(needle, block)

    def test_screen_rules_in_node(self):
        code = "\n".join([
            "const esc = (s) => String(s);",
            "const T = (k) => k;",
            "const chip = (kind, text) => `<chip ${kind}>${text}</chip>`;",
            "const REC_CHIP = {}; const REC_KEY = {};",
            _fn("const FLOW_KEYWORD_MAX", ";") + ";",
            _fn("const FLOW_DONE_CHIP", ";") + ";",
            _fn("function flowKeywordCheck", "\n  }\n") + "\n  }",
            _fn("function flowActionHtml", "\n  }\n") + "\n  }",
        ])
        script = code + """
console.log(JSON.stringify({
  kw: [flowKeywordCheck("  니트\\t티셔츠 "), flowKeywordCheck("   "), flowKeywordCheck("가".repeat(41)), flowKeywordCheck("가".repeat(40)).keyword.length],
  act: ["TREND", "NEW_PRODUCT", "EVALUATE", "DONE_APPROVED", "CLOSED"].map((n) => flowActionHtml({ candidate_id: 5, next: n })),
  review: flowActionHtml({ candidate_id: 5, next: "REVIEW", decision: { id: 9 } }),
}));
"""
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["kw"][0], {"keyword": "니트 티셔츠"})
        self.assertEqual(got["kw"][1], {"error": "easy.disc.kw_empty"})
        self.assertEqual(got["kw"][2], {"error": "easy.disc.kw_long"})
        self.assertEqual(got["kw"][3], 40)
        trend, new, ev, approved, closed = got["act"]
        self.assertIn('data-ez-flow-trend="5"', trend)
        self.assertIn('data-ez-flow-eval="5"', trend)  # 트렌드 없이도 평가할 수 있다
        self.assertIn('data-ez-flow-new="5"', new)
        self.assertIn('data-ez-flow-eval="5"', ev)
        self.assertNotIn("<button", approved)
        self.assertIn("<chip ok>easy.disc.next_done_approved</chip>", approved)
        self.assertIn("<chip gray>easy.disc.next_closed</chip>", closed)
        self.assertIn('data-ez-flow-review="9"', got["review"])


if __name__ == "__main__":
    unittest.main()
