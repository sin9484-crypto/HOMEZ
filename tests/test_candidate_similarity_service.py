"""
=========================================================
Homez OS

File : tests/test_candidate_similarity_service.py

2026-10-18 — 상품 서칭 AI S1: 후보 유사도 서비스(`candidate_similarity_service.py`)와 URL 가져오기 알림 배선을 고정한다. 외부 호출 0, 읽기 전용.

  - 이 회사가 볼 수 있는 후보(GLOBAL + 자사 PRIVATE)만 비교한다 — 다른 회사의 비공개 후보는 나타나지 않는다.
  - 가까운 순(관계 → 이름 일치)으로 최대 5개, 이 회사 기준 상태(승인·보류·거절)를 함께 돌려준다. 같은 주소로 가져온 후보는 제외한다.
  - 상품군 묶기는 후보·결정·근거를 바꾸지 않는다(읽기 전용).
  - URL 가져오기 `search` 응답에 `similar_candidates`가 붙고, 비교가 실패해도 가져오기는 막히지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.product_candidate.candidate_similarity_service import CandidateSimilarityService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.user.model import User  # noqa: F401


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
        self.service = CandidateSimilarityService(self.db)
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

    def add(self, name, reference, company_id=None):
        candidate, _events = ProductCandidateService(self.db).register_private_candidate(
            ProductCandidatePrivateCreate(source_reference=reference, market="COUPANG", product_name=name),
            company_id=company_id or self.company_id, correlation_id=f"t-{reference}",
        )
        return candidate


class SimilarToTests(Base_):

    def test_the_closest_candidates_come_first_with_their_relation_and_reasons(self):
        dup = self.add("레이펄스 바디워시 워터리스 200ml 3개입", "R1")
        variant = self.add("레이펄스 워터리스 바디워시 500ml 3개입", "R2")
        self.add("러닝 무릎보호대 슬개건 2P", "R3")
        found = self.service.similar_to(self.company_id, "[무료배송] 레이펄스 워터리스 바디워시 200ml 3개입")
        self.assertEqual([(f["candidate_id"], f["relation"]) for f in found], [(dup.id, "DUPLICATE"), (variant.id, "VARIANT")])
        self.assertIn("SAME_SPECS", found[0]["reasons"])
        self.assertIn("SPEC_DIFFERS:volume_ml", found[1]["reasons"])

    def test_nothing_similar_returns_an_empty_list_and_empty_names_are_safe(self):
        self.add("러닝 무릎보호대 슬개건 2P", "R1")
        self.assertEqual(self.service.similar_to(self.company_id, "레이펄스 바디워시"), [])
        for name in (None, "", "   ", "★☆"):
            self.assertEqual(self.service.similar_to(self.company_id, name), [])

    def test_another_companys_private_candidates_never_appear(self):
        self.add("레이펄스 워터리스 바디워시 200ml", "R1", company_id=self.other_company_id)
        self.assertEqual(self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시 200ml"), [])
        self.assertEqual(self.service.families(self.company_id)["families"], [])

    def test_the_company_status_is_shown_including_rejected_ones(self):
        c = self.add("레이펄스 워터리스 바디워시 200ml", "R1")
        self.db.add(ProductCandidateSelection(candidate_id=c.id, company_id=self.company_id, status="REJECTED"))
        self.db.commit()
        found = self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시 200ml")
        self.assertEqual((found[0]["candidate_id"], found[0]["status"]), (c.id, "REJECTED"))

    def test_the_candidate_imported_from_the_same_address_is_excluded(self):
        self.add("레이펄스 워터리스 바디워시 200ml", "ONCHANNEL:CH1")
        self.assertEqual(self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시 200ml", exclude_reference="ONCHANNEL:CH1"), [])
        self.assertEqual(len(self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시 200ml")), 1)

    def test_at_most_five_are_returned(self):
        for i in range(8):
            self.add(f"레이펄스 워터리스 바디워시 {200 + i}ml", f"R{i}")
        self.assertEqual(len(self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시 100ml")), 5)

    def test_extra_text_supplies_specs_without_changing_the_name_match(self):
        c = self.add("레이펄스 워터리스 바디워시 200ml", "R1")
        found = self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시", "용량 200ml")
        self.assertEqual((found[0]["candidate_id"], found[0]["relation"]), (c.id, "DUPLICATE"))
        self.assertIn("SAME_SPECS", found[0]["reasons"])


class NoticeSpecTests(Base_):
    """후보를 만들 때 보관한 고시 표의 구성·용량이 이름에 없는 규격을 채워 같은 상품과 구성만 다른 상품을 가른다."""

    def keep_notice(self, candidate, rows):
        from app.domains.product_candidate.constants import EvidenceType
        from app.domains.product_import.notice_suggestions import encode_notice_rows

        self.db.add(ProductCandidateEvidence(candidate_id=candidate.id, evidence_type=EvidenceType.RAW_SOURCE, payload_summary=encode_notice_rows(rows)))
        self.db.commit()

    def test_the_stored_notice_volume_separates_a_variant_from_a_duplicate(self):
        c200 = self.add("레이펄스 워터리스 바디워시", "R1")
        c500 = self.add("레이펄스 워터리스 바디워시", "R2")
        self.keep_notice(c200, [("용량(중량)", "200ml"), ("제조국", "대한민국")])
        self.keep_notice(c500, [("용량(중량)", "500ml")])
        found = {f["candidate_id"]: f for f in self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시", self.service.spec_text([("용량(중량)", "200ml")]))}
        self.assertEqual(found[c200.id]["relation"], "DUPLICATE")
        self.assertEqual(found[c500.id]["relation"], "VARIANT")
        self.assertIn("SPEC_DIFFERS:volume_ml", found[c500.id]["reasons"])

    def test_only_spec_rows_are_used_so_other_numbers_never_become_specs(self):
        text = self.service.spec_text([("재질", "나일론 90%"), ("제조국", "중국"), ("용량(중량)", "200ml"), ("크기/중량", "약 15x15cm"), ("KC 인증", "12345")])
        self.assertIn("200ml", text)
        self.assertIn("15x15cm", text)
        self.assertNotIn("90", text)
        self.assertNotIn("12345", text)
        self.assertEqual(self.service.spec_text([]), "")
        self.assertLessEqual(len(self.service.spec_text([("용량", "가" * 5000)])), 600)

    def test_the_latest_notice_for_a_candidate_wins_and_candidates_without_one_use_the_name_only(self):
        c = self.add("레이펄스 워터리스 바디워시", "R1")
        self.keep_notice(c, [("용량(중량)", "100ml")])
        self.keep_notice(c, [("용량(중량)", "200ml")])
        self.assertEqual(self.service._spec_text_by_candidate([c.id]), {c.id: "200ml"})
        d = self.add("러닝 무릎보호대 슬개건", "R2")
        self.assertEqual(self.service._spec_text_by_candidate([d.id]), {})
        self.assertEqual(self.service._spec_text_by_candidate([]), {})

    def test_families_use_the_stored_notice_too(self):
        a = self.add("레이펄스 워터리스 바디워시", "R1")
        b = self.add("레이펄스 워터리스 바디워시", "R2")
        self.keep_notice(a, [("용량(중량)", "200ml")])
        self.keep_notice(b, [("용량(중량)", "500ml")])
        family = self.service.families(self.company_id)["families"][0]
        self.assertEqual(family["duplicate_sets"], [])
        self.assertEqual({p["relation"] for p in family["pairs"]}, {"VARIANT"})


class FamiliesTests(Base_):

    def test_candidates_are_grouped_into_families_with_status_and_specs(self):
        a = self.add("레이펄스 워터리스 바디워시 200ml 3개입", "R1")
        b = self.add("[특가] 워터리스 바디워시 레이펄스 3개입 200ml", "R2")
        c = self.add("레이펄스 워터리스 바디워시 500ml 3개입", "R3")
        self.add("러닝 무릎보호대 슬개건 2P", "R4")
        result = self.service.families(self.company_id)
        self.assertEqual((result["checked"], result["truncated"]), (4, False))
        self.assertEqual(len(result["families"]), 1)
        family = result["families"][0]
        self.assertEqual(sorted(m["candidate_id"] for m in family["members"]), sorted([a.id, b.id, c.id]))
        self.assertEqual([sorted(s) for s in family["duplicate_sets"]], [sorted([a.id, b.id])])
        by_id = {m["candidate_id"]: m for m in family["members"]}
        self.assertEqual(by_id[c.id]["specs"]["volume_ml"], 500.0)
        self.assertTrue(all("status" in m for m in family["members"]))

    def test_the_view_is_read_only(self):
        self.add("레이펄스 워터리스 바디워시 200ml", "R1")
        self.add("레이펄스 워터리스 바디워시 200ml 3개입", "R2")
        before = [
            self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in ("product_candidates", "product_candidate_evidences", "product_candidate_decisions", "product_candidate_selections", "audit_logs")
        ]
        self.service.families(self.company_id)
        self.service.similar_to(self.company_id, "레이펄스 워터리스 바디워시 200ml")
        after = [
            self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in ("product_candidates", "product_candidate_evidences", "product_candidate_decisions", "product_candidate_selections", "audit_logs")
        ]
        self.assertEqual(before, after)

    def test_no_candidates_gives_an_empty_result(self):
        self.assertEqual(self.service.families(self.company_id), {"checked": 0, "truncated": False, "families": []})


class SearchWiringTests(unittest.TestCase):

    def test_the_search_route_attaches_similar_candidates_and_survives_a_failure(self):
        import sys

        from app.domains.product_import import router as pir
        from app.domains.product_import import schema

        response = schema.UrlSearchResponse(source_kind="GENERIC", source_url="https://x.test/1", source_reference="URL:x:1", suggested_product_name="테스트 상품")
        user = mock.Mock(company_id=3, id=9)
        service = mock.Mock()
        service.search.return_value = response
        with mock.patch.object(pir, "ProductImportService", return_value=service), \
                mock.patch("app.domains.product_candidate.candidate_similarity_service.CandidateSimilarityService.similar_to",
                           return_value=[{"candidate_id": 1, "product_name": "테스트 상품", "status": "APPROVED", "relation": "DUPLICATE", "core_score": 1.0, "reasons": ["NAME_NEAR_SAME"]}]) as similar:
            out = pir.search_product_url(schema.UrlSearchRequest(url="https://x.test/1"), current_user=user, db=mock.Mock(), transport=mock.Mock(), connection_service=None)
        self.assertEqual(out.similar_candidates[0].relation, "DUPLICATE")
        self.assertEqual(similar.call_args.kwargs["exclude_reference"], "URL:x:1")
        response2 = schema.UrlSearchResponse(source_kind="GENERIC", source_url="https://x.test/1", source_reference="URL:x:1", suggested_product_name="테스트 상품")
        service.search.return_value = response2
        db = mock.Mock()
        with mock.patch.object(pir, "ProductImportService", return_value=service), \
                mock.patch("app.domains.product_candidate.candidate_similarity_service.CandidateSimilarityService.similar_to", side_effect=RuntimeError("boom")):
            out2 = pir.search_product_url(schema.UrlSearchRequest(url="https://x.test/1"), current_user=user, db=db, transport=mock.Mock(), connection_service=None)
        self.assertEqual(out2.similar_candidates, [])  # 알림이 실패해도 가져오기 결과는 그대로
        db.rollback.assert_called()

    def test_the_duplicates_route_exists_before_the_candidate_id_route_and_is_admin_only(self):
        import sys

        import app.domains.product_candidate.router  # noqa: F401

        module = sys.modules["app.domains.product_candidate.router"]
        paths = [r.path for r in module.router.routes]
        self.assertLess(paths.index("/product-candidates/duplicates"), paths.index("/product-candidates/{candidate_id}"))
        route = next(r for r in module.router.routes if r.path == "/product-candidates/duplicates")
        self.assertTrue(any("admin_guard" in d.call.__qualname__.lower() or "adminguard" in d.call.__qualname__.lower() for d in route.dependant.dependencies))

    def test_the_service_module_never_writes(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app/domains/product_candidate/candidate_similarity_service.py").read_text(encoding="utf-8")
        for forbidden in (".commit(", ".add(", ".delete(", ".execute(", "requests", "llm", "approve(", "reject(", "hold("):
            self.assertNotIn(forbidden, source, forbidden)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for cls in (SimilarToTests, NoticeSpecTests, FamiliesTests, SearchWiringTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
