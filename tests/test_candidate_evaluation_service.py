"""
=========================================================
Homez OS

File : tests/test_candidate_evaluation_service.py

2026-10-18 — 상품 서칭 AI S2: 후보 사전 평가 서비스(`candidate_evaluation_service.py`)와 라우트를 고정한다. 외부 호출 0, 읽기 전용, AI 없음.

  - 이 회사가 볼 수 있는 후보만 평가한다(다른 회사 비공개 후보는 나오지 않는다). 후보 가능 → 보류 → 제외 권고 순, 같으면 최근 후보 먼저.
  - 후보를 만들 때 보관한 고시 표와 URL 분석 스냅샷(가장 나중 것)을 쓴다. 읽을 수 없는 스냅샷은 앞선 정상 스냅샷을 지우지 않는다.
  - 같은 상품이 둘이면 먼저 만들어진 쪽이 원본이고 나중 것이 중복으로 표시된다. 구성만 다른 후보는 정보일 뿐이다.
  - 후보·근거·결정·감사 기록은 바뀌지 않는다. 라우트는 `/{candidate_id}`보다 앞에 있고 관리자 전용이다.
=========================================================
"""

import json
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.product_candidate.candidate_evaluation_service import CandidateEvaluationService
from app.domains.product_candidate.constants import EvidenceType
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_import.notice_suggestions import encode_notice_rows
from app.domains.user.model import User  # noqa: F401

SNAP_OK = {"v": 1, "verdict": "CANDIDATE", "min_margin_rate": "0.18", "scenarios": [{"t": "0.18", "p": "12790", "m": "0.1802"}], "missing": []}
SNAP_HOLD = {"v": 1, "verdict": "HOLD", "min_margin_rate": "0.18", "missing": ["VAT_STATE", "CHANNEL_FEE"], "reasons": ["계산에 필요한 값이 확인되지 않았다"]}


def snapshot_text(payload):
    return "AI_ANALYSIS_REPORT:(화면 스냅샷) " + json.dumps(payload, ensure_ascii=False)


class Base_(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, ProductCandidate.__table__, ProductCandidateEvidence.__table__,
            ProductCandidateDecision.__table__, ProductCandidateSelection.__table__,
        ])
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), ip_address VARCHAR(50))"))
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.company_id = self._company("회사A", "111-11-11111")
        self.other_company_id = self._company("회사B", "222-22-22222")
        self.service = CandidateEvaluationService(self.db)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _company(self, name, number):
        company = Company(name=name, business_number=number, ceo="t", phone="02", email=f"{number}@b.c", address="서울")
        self.db.add(company)
        self.db.commit()
        return company.id

    def add(self, name, reference, *, company_id=None, snapshot=None, notice=None, category_hint=None):
        candidate, _events = ProductCandidateService(self.db).register_private_candidate(
            ProductCandidatePrivateCreate(source_reference=reference, market="COUPANG", product_name=name, category_hint=category_hint),
            company_id=company_id or self.company_id, correlation_id=f"t-{reference}",
        )
        if notice is not None:
            self.evidence(candidate, encode_notice_rows(notice))
        if snapshot is not None:
            self.evidence(candidate, snapshot_text(snapshot))
        return candidate

    def evidence(self, candidate, summary):
        self.db.add(ProductCandidateEvidence(candidate_id=candidate.id, evidence_type=EvidenceType.RAW_SOURCE, payload_summary=summary))
        self.db.commit()

    def result(self, candidate):
        return next(i for i in self.service.evaluate_all(self.company_id)["items"] if i["candidate_id"] == candidate.id)


class EvaluateAllTests(Base_):

    def test_each_candidate_gets_a_decision_and_the_queue_is_ordered(self):
        excluded = self.add("총기 모양 라이터", "R1", snapshot=SNAP_OK)
        ready = self.add("레이펄스 워터리스 바디워시", "R2", snapshot=SNAP_OK, notice=[("용량(중량)", "200ml")])
        hold = self.add("러닝 무릎보호대 슬개건", "R3")
        ready2 = self.add("주방 행주 40매", "R4", snapshot=SNAP_OK)
        out = self.service.evaluate_all(self.company_id)
        self.assertEqual([i["candidate_id"] for i in out["items"]], [ready2.id, ready.id, hold.id, excluded.id])  # 후보 가능(최근 먼저) → 보류 → 제외
        self.assertEqual(out["counts"], {"READY": 2, "HOLD": 1, "EXCLUDE": 1})
        self.assertEqual((out["checked"], out["truncated"]), (4, False))
        by_id = {i["candidate_id"]: i for i in out["items"]}
        self.assertEqual(by_id[hold.id]["reasons"][0]["code"], "MARGIN_NOT_ANALYZED")
        self.assertEqual(by_id[ready.id]["analysis_verdict"], "CANDIDATE")

    def test_the_stored_notice_and_the_snapshot_drive_the_reasons(self):
        c = self.add("레이펄스 워터리스 바디워시", "R1", snapshot=SNAP_HOLD, notice=[("상품정보제공고시 구분", "화장품"), ("KC 인증유형", "해당사항없음")])
        item = self.result(c)
        got = {r["code"]: r for r in item["reasons"]}
        self.assertEqual(item["decision"], "HOLD")
        self.assertEqual(got["REGULATED_CATEGORY"]["params"]["kind"], "COSMETIC")
        self.assertEqual(got["MARGIN_ANALYSIS_HOLD"]["params"]["missing"], ["VAT_STATE", "CHANNEL_FEE"])
        self.assertNotIn("KC_CERT", got)

    def test_the_latest_good_snapshot_wins_and_a_broken_one_does_not_erase_it(self):
        c = self.add("레이펄스 워터리스 바디워시", "R1", snapshot=SNAP_HOLD)
        self.evidence(c, snapshot_text(SNAP_OK))
        self.assertEqual(self.result(c)["analysis_verdict"], "CANDIDATE")
        self.evidence(c, "AI_ANALYSIS_REPORT:(화면 스냅샷) 깨진 글")
        self.assertEqual(self.result(c)["analysis_verdict"], "CANDIDATE")
        self.evidence(c, snapshot_text(SNAP_HOLD))
        self.assertEqual(self.result(c)["analysis_verdict"], "HOLD")

    def test_unrelated_evidence_is_ignored(self):
        c = self.add("레이펄스 워터리스 바디워시", "R1")
        self.evidence(c, "REGISTRATION_NOTES:{\"x\":1}")
        self.evidence(c, "아무 근거")
        self.assertEqual(self.result(c)["reasons"][0]["code"], "MARGIN_NOT_ANALYZED")


class DuplicateTests(Base_):

    def test_the_later_duplicate_is_flagged_and_the_original_is_not(self):
        original = self.add("레이펄스 워터리스 바디워시 200ml 3개입", "R1", snapshot=SNAP_OK)
        later = self.add("[무료배송] 레이펄스 바디워시 워터리스 3개입 200ml", "R2", snapshot=SNAP_OK)
        self.assertEqual(self.result(original)["decision"], "READY")
        item = self.result(later)
        found = [r for r in item["reasons"] if r["code"] == "DUPLICATE_OF"]
        self.assertEqual([f["params"]["candidate_id"] for f in found], [original.id])
        self.assertEqual(item["decision"], "HOLD")

    def test_a_variant_is_informational_on_both_sides(self):
        a = self.add("레이펄스 워터리스 바디워시 200ml 3개입", "R1", snapshot=SNAP_OK)
        b = self.add("레이펄스 워터리스 바디워시 500ml 3개입", "R2", snapshot=SNAP_OK)
        for c, other in ((a, b), (b, a)):
            item = self.result(c)
            self.assertEqual(item["decision"], "READY")
            info = next(r for r in item["reasons"] if r["code"] == "VARIANT_EXISTS")
            self.assertEqual(info["params"]["candidate_ids"], [other.id])

    def test_the_duplicates_status_is_reported_for_the_company(self):
        original = self.add("레이펄스 워터리스 바디워시 200ml", "R1", snapshot=SNAP_OK)
        self.db.add(ProductCandidateSelection(candidate_id=original.id, company_id=self.company_id, status="APPROVED"))
        self.db.commit()
        later = self.add("레이펄스 워터리스 바디워시 200ml 3개입", "R2", snapshot=SNAP_OK)
        found = next(r for r in self.result(later)["reasons"] if r["code"] == "DUPLICATE_OF")
        self.assertEqual(found["params"]["status"], "APPROVED")


class IsolationAndReadOnlyTests(Base_):

    def test_another_companys_private_candidates_never_appear(self):
        self.add("레이펄스 워터리스 바디워시", "R1", company_id=self.other_company_id, snapshot=SNAP_OK)
        out = self.service.evaluate_all(self.company_id)
        self.assertEqual((out["checked"], out["items"]), (0, []))
        mine = self.add("레이펄스 워터리스 바디워시", "R2", snapshot=SNAP_OK)
        self.assertEqual([i["candidate_id"] for i in self.service.evaluate_all(self.company_id)["items"]], [mine.id])  # 남의 같은 상품은 중복으로도 안 센다
        self.assertNotIn("DUPLICATE_OF", [r["code"] for r in self.result(mine)["reasons"]])

    def test_nothing_is_written(self):
        self.add("레이펄스 워터리스 바디워시 200ml", "R1", snapshot=SNAP_OK)
        self.add("레이펄스 워터리스 바디워시 200ml 3개입", "R2", snapshot=SNAP_HOLD, notice=[("용량(중량)", "200ml")])
        tables = ("product_candidates", "product_candidate_evidences", "product_candidate_decisions", "product_candidate_selections", "audit_logs")
        before = [self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
        statuses = self.db.execute(text("SELECT id, status FROM product_candidates ORDER BY id")).fetchall()
        self.service.evaluate_all(self.company_id)
        self.assertEqual(before, [self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables])
        self.assertEqual(statuses, self.db.execute(text("SELECT id, status FROM product_candidates ORDER BY id")).fetchall())

    def test_no_candidates_gives_an_empty_queue(self):
        self.assertEqual(self.service.evaluate_all(self.company_id), {"checked": 0, "truncated": False, "counts": {"READY": 0, "HOLD": 0, "EXCLUDE": 0}, "items": []})


class WiringTests(unittest.TestCase):

    def test_the_route_sits_before_the_candidate_id_route_and_is_admin_only(self):
        import sys

        import app.domains.product_candidate.router  # noqa: F401

        module = sys.modules["app.domains.product_candidate.router"]
        paths = [r.path for r in module.router.routes]
        self.assertEqual(paths.count("/product-candidates/evaluations"), 1)
        self.assertLess(paths.index("/product-candidates/evaluations"), paths.index("/product-candidates/{candidate_id}"))
        route = next(r for r in module.router.routes if r.path == "/product-candidates/evaluations")
        self.assertEqual(route.methods, {"GET"})
        self.assertTrue(any("admin" in d.call.__qualname__.lower() for d in route.dependant.dependencies))

    def test_the_service_never_writes_calls_out_or_uses_ai(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app/domains/product_candidate/candidate_evaluation_service.py").read_text(encoding="utf-8")
        for forbidden in (".commit(", ".add(", ".delete(", ".execute(", "requests", "call_llm", "llm_client", "approve(", "reject(", "hold("):
            self.assertNotIn(forbidden, source, forbidden)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for cls in (EvaluateAllTests, DuplicateTests, IsolationAndReadOnlyTests, WiringTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
