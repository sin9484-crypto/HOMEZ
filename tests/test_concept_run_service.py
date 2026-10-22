"""
=========================================================
Homez OS

File : tests/test_concept_run_service.py

2026-10-23 — 이미지 AI I2·I3: 컨셉별 독립 작업(`concept_run_service.py`, `POST /media-assets/concept-runs`)과 카테고리 추천(`concept_recommendation.py`, `GET /media-assets/concept-recommendations`)을 고정한다.
임시 SQLite·임시 폴더 — 네트워크·AI 0.

  - 컨셉마다 독립: 한 컨셉의 건너뜀·검사 실패·합성 오류가 다른 컨셉을 막지 않는다. 검사에서 FAIL이 난 컨셉은 저장하지 않는다(CHECK_FAILED).
  - 제품은 그대로(보존 검사 PASS), 같은 입력은 같은 자산(멱등), 원본은 그대로, 권리는 만들 때 한 번 승계(격상 없음), 다른 회사·금지 사진은 거절.
  - 대표(MAIN)는 흰 배경 컨셉만 — 장면(L1) 컨셉은 건너뜀. 검사하지 못한 항목은 통과로 쓰지 않는다.
  - 추천: 어울리는 상품은 앞으로, 피해야 할 상품은 이유와 함께 따로, 모르는 카테고리는 중립.
=========================================================
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image
from PIL import ImageDraw
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import concept_recommendation as cr
from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset import schema
from app.domains.media_asset.concept_run_service import ConceptRunService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.user.model import User  # noqa: F401
from pydantic import ValidationError

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)
L1 = ("LINEN_NATURAL", "WOOD_WARM", "PAPER_SOFT", "SPOTLIGHT_WARM", "SPOTLIGHT_COOL")


def cutout_png(color=(20, 30, 70, 255), size=(500, 500)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((100, 100, size[0] - 100, size[1] - 100), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class Base_(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, MediaAsset.__table__, ImageGenerationJob.__table__, ImageGenerationResult.__table__, ImageGenerationDailyUsage.__table__,
        ])
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.media_root = Path(tempfile.mkdtemp())
        self.queue = ImageGenerationJobQueueService(self.db, media_root=self.media_root)
        self.service = ConceptRunService(self.db, queue=self.queue)
        self.company_id = self.make_company("회사 A", "111-11-11111")
        self.other_company_id = self.make_company("회사 B", "222-22-22222")
        self.cutout = self.upload(cutout_png())
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def make_company(self, name, number):
        company = Company(name=name, business_number=number, ceo="테스트", phone="02-000-0000", email="x@test.com", address="서울")
        self.db.add(company)
        self.db.commit()
        return company.id

    def upload(self, data, company_id=None, rights="VERIFIED", owner_id=7):
        asset = self.queue.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=owner_id, purpose="DETAIL", display_order=0,
            image_bytes=data, original_filename="cutout.png", company_id=company_id or self.company_id,
        )
        asset.rights_status = rights
        self.db.commit()
        return asset

    def counts(self):
        return tuple(self.db.query(func.count(model.id)).scalar() for model in (MediaAsset, ImageGenerationJob, ImageGenerationResult))

    def run_concepts(self, codes, purpose="ADDITIONAL", cutout_id=None, company_id=None):
        return self.service.run(company_id or self.company_id, 1, cutout_id or self.cutout.id, codes, purpose)

    @staticmethod
    def check(outcome, code):
        return {c["code"]: c for c in outcome.checks}[code]


class RunTests(Base_):

    def test_each_concept_is_made_independently_with_its_checks(self):
        outcomes = self.run_concepts(["SOFT_BEIGE", "CALM_GRAY", "LINEN_NATURAL", "SPOTLIGHT_COOL"])
        self.assertEqual([(o.concept_code, o.status, o.usable) for o in outcomes], [(c, "CREATED", True) for c in ("SOFT_BEIGE", "CALM_GRAY", "LINEN_NATURAL", "SPOTLIGHT_COOL")])
        for outcome in outcomes:
            self.assertEqual(self.check(outcome, "PRODUCT_PRESERVATION")["status"], "PASS")
            self.assertEqual(self.check(outcome, "PRODUCT_PRESERVATION")["detail"]["ratio"], 1.0)
            self.assertEqual(outcome.asset.asset_role, "GENERATED")
            self.assertEqual(outcome.asset.source_asset_id, self.cutout.id)
            self.assertEqual(outcome.asset.original_filename, f"concept-{outcome.concept_code.lower()}-additional.png")
            self.assertEqual((outcome.asset.width, outcome.asset.height), (1200, 1200))
        self.assertEqual(len({o.asset.id for o in outcomes}), 4)        # 컨셉마다 다른 사진

    def test_l1_scene_concepts_declare_and_run_their_checks(self):
        outcome = self.run_concepts(["WOOD_WARM"])[0]
        codes = [c["code"] for c in outcome.checks]
        self.assertEqual(codes, ["PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST", "NO_TEXT_OVERLAY"])
        self.assertEqual(self.check(outcome, "NO_TEXT_OVERLAY")["status"], "BY_CONSTRUCTION")
        self.assertEqual(self.check(outcome, "BACKGROUND_CONTRAST")["status"], "PASS")

    def test_the_original_cutout_is_untouched(self):
        before = (self.cutout.sha256_hex, self.cutout.rights_status, self.cutout.source_asset_id, (self.media_root / self.cutout.storage_path).read_bytes())
        self.run_concepts(["SOFT_BEIGE", "WOOD_WARM"])
        self.db.expire_all()
        self.assertEqual((self.cutout.sha256_hex, self.cutout.rights_status, self.cutout.source_asset_id, (self.media_root / self.cutout.storage_path).read_bytes()), before)

    def test_repeating_the_run_reuses_the_same_assets(self):
        first = self.run_concepts(["SOFT_BEIGE", "LINEN_NATURAL"])
        assets = self.db.query(func.count(MediaAsset.id)).scalar()
        second = self.run_concepts(["SOFT_BEIGE", "LINEN_NATURAL"])
        self.assertEqual([o.status for o in second], ["REUSED", "REUSED"])
        self.assertEqual([o.asset.id for o in first], [o.asset.id for o in second])
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets)
        self.assertEqual(self.db.query(func.count(ImageGenerationJob.id)).scalar(), 4)       # 작업 기록은 실행마다 남는다

    def test_rights_are_inherited_once_and_never_upgraded(self):
        unverified = self.upload(cutout_png((90, 20, 20, 255)), rights="RIGHTS_UNVERIFIED")
        asset = self.run_concepts(["SOFT_BEIGE"], cutout_id=unverified.id)[0].asset
        self.assertEqual(asset.rights_status, "RIGHTS_UNVERIFIED")
        unverified.rights_status = "VERIFIED"
        self.db.commit()
        again = self.run_concepts(["SOFT_BEIGE"], cutout_id=unverified.id)[0].asset
        self.assertEqual(again.rights_status, "RIGHTS_UNVERIFIED")      # 원본이 나중에 확인돼도 자동으로 따라가지 않는다

    def test_the_composed_picture_has_the_cutout_unchanged_inside(self):
        outcome = self.run_concepts(["CALM_GRAY"])[0]
        composed = Image.open(self.media_root / outcome.asset.storage_path).convert("RGB")
        self.assertEqual(composed.getpixel((600, 600)), (20, 30, 70))       # 제품 한가운데 색 그대로
        self.assertEqual(composed.getpixel((5, 5)), (241, 243, 245))         # 배경은 컨셉의 단색


class IndependenceTests(Base_):

    def test_unknown_and_unusable_concepts_are_skipped_without_stopping_the_rest(self):
        outcomes = self.run_concepts(["NEON_RAINBOW", "SOFT_BEIGE", "CLEAN_WHITE", "CALM_GRAY"], purpose="MAIN")
        self.assertEqual([(o.concept_code, o.status, o.reason) for o in outcomes], [
            ("NEON_RAINBOW", "SKIPPED", "UNKNOWN_CONCEPT"), ("SOFT_BEIGE", "SKIPPED", "NOT_USABLE_FOR_PURPOSE"),
            ("CLEAN_WHITE", "CREATED", None), ("CALM_GRAY", "SKIPPED", "NOT_USABLE_FOR_PURPOSE"),
        ])
        self.assertEqual(self.db.query(func.count(ImageGenerationJob.id)).scalar(), 1)      # 건너뜀은 작업을 만들지 않는다

    def test_only_the_white_concept_makes_a_main_image_and_it_passes_the_main_spec(self):
        outcome = self.run_concepts(["CLEAN_WHITE"], purpose="MAIN")[0]
        self.assertEqual(self.check(outcome, "MAIN_IMAGE_SPEC")["status"], "PASS")
        for code in L1:
            self.assertEqual(self.run_concepts([code], purpose="MAIN")[0].status, "SKIPPED", code)

    def test_main_spec_is_skipped_not_passed_for_other_purposes(self):
        outcome = self.run_concepts(["CLEAN_WHITE"], purpose="DETAIL")[0]
        self.assertEqual(self.check(outcome, "MAIN_IMAGE_SPEC")["status"], "SKIPPED")

    def test_a_failed_inspection_is_not_saved_and_does_not_stop_others(self):
        from app.domains.media_asset import concept_inspection as ci

        original = ci.inspect

        calls = []

        def picky(cutout_bytes, composed_bytes, checks, purpose):
            calls.append(1)
            results = original(cutout_bytes, composed_bytes, checks, purpose)
            if len(calls) == 2:      # 두 번째 컨셉만 실패시킨다
                results[0] = ci.CheckResult("PRODUCT_PRESERVATION", "FAIL", {"ratio": 0.5, "pixels": 10, "min": 0.99})
            return results

        assets_before = self.db.query(func.count(MediaAsset.id)).scalar()
        with mock.patch("app.domains.media_asset.concept_run_service.concept_inspection.inspect", side_effect=picky):
            outcomes = self.run_concepts(["SOFT_BEIGE", "CALM_GRAY", "FRESH_MINT"])
        self.assertEqual([o.status for o in outcomes], ["CREATED", "CHECK_FAILED", "CREATED"])
        self.assertEqual((outcomes[1].usable, outcomes[1].reason, outcomes[1].asset), (False, "INSPECTION_FAILED", None))
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets_before + 2)         # 실패한 컨셉은 사진을 남기지 않는다
        statuses = sorted(j.status for j in self.db.query(ImageGenerationJob).all())
        self.assertEqual(statuses, ["FAILED", "SUCCEEDED", "SUCCEEDED"])

    def test_a_composition_error_fails_only_that_concept_and_hides_details(self):
        real = __import__("app.domains.media_asset.concept_run_service", fromlist=["compose_with_background"]).compose_with_background
        state = {"n": 0}

        def flaky(cutout_bytes, spec, *args, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("secret-token-123")
            return real(cutout_bytes, spec, *args, **kwargs)

        with mock.patch("app.domains.media_asset.concept_run_service.compose_with_background", side_effect=flaky):
            outcomes = self.run_concepts(["SOFT_BEIGE", "CALM_GRAY"])
        self.assertEqual([(o.status, o.reason) for o in outcomes], [("FAILED", "COMPOSE_FAILED"), ("CREATED", None)])
        job = self.db.query(ImageGenerationJob).filter_by(status="FAILED").one()
        self.assertNotIn("secret-token-123", job.error_reason or "")

    def test_a_same_bytes_asset_from_another_origin_is_reused_and_not_overwritten(self):
        outcome = self.run_concepts(["SOFT_BEIGE"])[0]
        composed_bytes = (self.media_root / outcome.asset.storage_path).read_bytes()
        other_cutout = self.upload(cutout_png((200, 10, 10, 255)), owner_id=9)
        squatter = self.upload(composed_bytes, owner_id=9, rights="RIGHTS_UNVERIFIED")      # 같은 바이트를 직접 올린 다른 자산(이미 위의 결과와 같은 자산으로 합쳐질 수 있다)
        state = (squatter.asset_role, squatter.source_asset_id, squatter.rights_status, squatter.original_filename)
        again = self.run_concepts(["SOFT_BEIGE"])[0]
        self.assertEqual(again.status, "REUSED")
        self.db.expire_all()
        self.assertEqual((squatter.asset_role, squatter.source_asset_id, squatter.rights_status, squatter.original_filename), state)
        self.assertIsNotNone(other_cutout.id)


class SaveFailureTests(Base_):

    def test_a_save_error_fails_only_that_concept_rolls_back_and_hides_details(self):
        real = self.queue._persist_generated_asset
        state = {"n": 0}

        def ghost_then_fail(*args, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                self.db.add(Company(name="유령 회사", business_number="999-99-99999", ceo="x", phone="02-000-0000", email="g@test.com", address="서울"))
                raise RuntimeError("secret-save-detail")
            return real(*args, **kwargs)

        companies_before = self.db.query(func.count(Company.id)).scalar()
        with mock.patch.object(self.queue, "_persist_generated_asset", side_effect=ghost_then_fail):
            outcomes = self.run_concepts(["SOFT_BEIGE", "CALM_GRAY"])
        self.assertEqual([(o.status, o.reason, o.usable) for o in outcomes], [("FAILED", "SAVE_FAILED", True), ("CREATED", None, True)])
        self.assertIsNone(outcomes[0].asset)
        self.assertEqual(self.db.query(func.count(Company.id)).scalar(), companies_before)        # 실패한 컨셉이 남긴 반쪽 변경은 되돌려졌다
        failed = self.db.query(ImageGenerationJob).filter_by(status="FAILED").one()
        self.assertNotIn("secret-save-detail", failed.error_reason or "")
        self.assertEqual(sum(1 for j in self.db.query(ImageGenerationJob).all() if j.status == "SUCCEEDED"), 1)


class ValidationTests(Base_):

    def test_request_rules(self):
        before = self.counts()
        for codes, purpose in (([], "ADDITIONAL"), (None, "ADDITIONAL"), (["SOFT_BEIGE"], "BANNER"), (["SOFT_BEIGE"], ""), ([""], "ADDITIONAL"), ([5], "ADDITIONAL"),
                               (["A1", "A2", "A3", "A4", "A5", "A6", "A7"], "ADDITIONAL")):
            with self.assertRaises(BadRequestException, msg=repr((codes, purpose))):
                self.run_concepts(codes, purpose)
        self.assertEqual(self.counts(), before)

    def test_duplicates_are_made_once_and_six_is_the_limit(self):
        self.assertEqual(len(self.run_concepts(["SOFT_BEIGE", "SOFT_BEIGE", "SOFT_BEIGE"])), 1)
        self.assertEqual(len(self.run_concepts(["SOFT_BEIGE", "FRESH_MINT", "BLUSH_PINK", "CALM_GRAY", "MIDNIGHT_PREMIUM", "LINEN_NATURAL"])), 6)

    def test_other_company_missing_denied_and_unreadable_photos(self):
        foreign = self.upload(cutout_png((10, 10, 10, 255)), company_id=self.other_company_id)
        for cutout_id in (foreign.id, 999999):
            with self.assertRaises(NotFoundException):
                self.run_concepts(["SOFT_BEIGE"], cutout_id=cutout_id)
        denied = self.upload(cutout_png((11, 10, 10, 255)), rights="RIGHTS_DENIED")
        with self.assertRaises(BadRequestException):
            self.run_concepts(["SOFT_BEIGE"], cutout_id=denied.id)
        broken = self.upload(cutout_png((12, 10, 10, 255)))
        (self.media_root / broken.storage_path).write_bytes(b"corrupted")
        with self.assertRaises(BadRequestException):
            self.run_concepts(["SOFT_BEIGE"], cutout_id=broken.id)
        self.assertEqual(self.db.query(func.count(ImageGenerationJob.id)).scalar(), 0)


class RouteTests(Base_):

    def setUp(self):
        super().setUp()
        patcher = mock.patch("app.desktop.paths.get_media_dir", return_value=self.media_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def module(self):
        import app.domains.media_asset.router  # noqa: F401

        return sys.modules["app.domains.media_asset.router"]

    def user(self):
        return SimpleNamespace(id=1, company_id=self.company_id)

    def test_routes_exist_and_are_admin_guarded(self):
        module = self.module()
        for path, method in (("/media-assets/concept-runs", "POST"), ("/media-assets/concept-recommendations", "GET")):
            route = [r for r in module.router.routes if r.path == path]
            self.assertEqual([sorted(r.methods) for r in route], [[method]], path)
            names = {getattr(d.call, "__name__", type(d.call).__name__) for d in route[0].dependant.dependencies}
            self.assertTrue({"admin_guard", "AdminGuard"} & names, (path, names))

    def test_run_returns_per_concept_results_and_counts(self):
        data = schema.ConceptRunRequestSchema(cutout_asset_id=self.cutout.id, concept_codes=["SOFT_BEIGE", "NOPE_CODE", "LINEN_NATURAL"], purpose="ADDITIONAL")
        result = self.module().concept_run(data=data, current_user=self.user(), db=self.db)
        self.assertEqual((result["created"], result["reused"], result["skipped"], result["check_failed"], result["failed"]), (2, 0, 1, 0, 0))
        self.assertEqual([r["status"] for r in result["results"]], ["CREATED", "SKIPPED", "CREATED"])
        self.assertEqual(result["results"][0]["asset"].source_asset_id, self.cutout.id)
        self.assertEqual([c["code"] for c in result["results"][0]["checks"]], ["PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST"])
        self.assertIsNone(result["results"][1]["asset"])
        again = self.module().concept_run(data=data, current_user=self.user(), db=self.db)
        self.assertEqual((again["created"], again["reused"]), (0, 2))

    def test_the_requested_purpose_is_passed_through(self):
        data = schema.ConceptRunRequestSchema(cutout_asset_id=self.cutout.id, concept_codes=["CLEAN_WHITE", "SOFT_BEIGE"], purpose="MAIN")
        result = self.module().concept_run(data=data, current_user=self.user(), db=self.db)
        self.assertEqual([(r["concept_code"], r["status"]) for r in result["results"]], [("CLEAN_WHITE", "CREATED"), ("SOFT_BEIGE", "SKIPPED")])
        self.assertEqual(result["results"][1]["reason"], "NOT_USABLE_FOR_PURPOSE")

    def test_run_uses_the_callers_company(self):
        foreign = self.upload(cutout_png((10, 10, 10, 255)), company_id=self.other_company_id)
        data = schema.ConceptRunRequestSchema(cutout_asset_id=foreign.id, concept_codes=["SOFT_BEIGE"])
        with self.assertRaises(NotFoundException):
            self.module().concept_run(data=data, current_user=self.user(), db=self.db)

    def test_schema_limits(self):
        ok = dict(cutout_asset_id=1, concept_codes=["SOFT_BEIGE"])
        for bad in (dict(concept_codes=[]), dict(concept_codes=["a"] ), dict(concept_codes=["soft_beige"]), dict(concept_codes=["X" * 41]), dict(concept_codes=["SOFT_BEIGE"] * 7),
                    dict(purpose="BANNER"), dict(concept_codes=["ABC", 5])):
            with self.assertRaises(ValidationError, msg=str(bad)):
                schema.ConceptRunRequestSchema(**{**ok, **bad})
        self.assertEqual(schema.ConceptRunRequestSchema(**ok).purpose, "ADDITIONAL")

    def test_recommendations_route(self):
        module = self.module()
        result = module.concept_recommendations(category="식품", purpose="ADDITIONAL", current_user=self.user())
        self.assertTrue(result["category_known"])
        self.assertIn("MIDNIGHT_PREMIUM", [r["code"] for r in result["excluded"]])
        with self.assertRaises(BadRequestException):
            module.concept_recommendations(category="식품", purpose="BANNER", current_user=self.user())


class RecommendationTests(unittest.TestCase):

    CONCEPTS = ic.list_concepts()

    def rec(self, category, purpose="ADDITIONAL"):
        return cr.recommend(category, purpose, self.CONCEPTS)

    def test_good_matches_come_first_and_keep_the_registry_order_otherwise(self):
        result = self.rec("주방용품")
        recommended = [r["code"] for r in result["recommended"]]
        good = [r for r in result["recommended"] if r["score"] == 2]
        self.assertTrue(good)
        self.assertEqual([r["code"] for r in result["recommended"][:len(good)]], [r["code"] for r in good])
        self.assertTrue(all("어울리는 상품" in r["reasons"][0] for r in good))
        rest = [r["code"] for r in result["recommended"][len(good):]]
        registry_order = [c.code for c in self.CONCEPTS if c.code in rest]
        self.assertEqual(rest, registry_order)

    def test_avoided_concepts_are_listed_with_the_reason_not_hidden(self):
        result = self.rec("식품")
        excluded = {r["code"]: r for r in result["excluded"]}
        self.assertIn("MIDNIGHT_PREMIUM", excluded)
        self.assertIn("피해야 할 상품", excluded["MIDNIGHT_PREMIUM"]["reasons"][0])
        self.assertNotIn("MIDNIGHT_PREMIUM", [r["code"] for r in result["recommended"]])
        total = {c.code for c in self.CONCEPTS if "ADDITIONAL" in c.usable_for}
        self.assertEqual({r["code"] for r in result["recommended"]} | set(excluded), total)       # 어느 것도 사라지지 않는다

    def test_unknown_or_empty_category_is_neutral(self):
        for hint in (None, "", "  ", "a", 5, "x" * 3):
            result = self.rec(hint)
            self.assertEqual(result["excluded"], [], repr(hint))
            self.assertTrue(all(r["score"] == 0 and r["reasons"] == [] for r in result["recommended"]), repr(hint))
        self.assertFalse(self.rec(None)["category_known"])
        self.assertFalse(self.rec("a")["category_known"])
        self.assertTrue(self.rec("식품")["category_known"])

    def test_main_only_lists_the_white_concept(self):
        result = self.rec("식품", "MAIN")
        self.assertEqual([r["code"] for r in result["recommended"]], ["CLEAN_WHITE"])
        self.assertEqual(result["excluded"], [])

    def test_matching_ignores_spaces_and_case_and_works_both_ways(self):
        self.assertEqual(self.rec("전 자 기 기")["recommended"][0]["code"], self.rec("전자기기")["recommended"][0]["code"])
        self.assertIn("MIDNIGHT_PREMIUM", [r["code"] for r in self.rec("식품/음료")["excluded"]])      # 힌트가 명세 낱말을 포함
        linen = {r["code"]: r["score"] for r in self.rec("인테")["recommended"]}          # 힌트가 명세 낱말('인테리어')의 일부
        self.assertEqual(linen["LINEN_NATURAL"], 2)
        self.assertFalse(self.rec("식")["category_known"])                                 # 한 글자 힌트는 근거로 삼지 않는다

    def test_the_hint_is_cut_at_sixty_characters(self):
        self.assertEqual(cr.MAX_HINT_CHARS, 60)
        within = self.rec("x" * 58 + "식품")           # 정확히 60자 — 낱말이 살아 있다
        self.assertIn("MIDNIGHT_PREMIUM", [r["code"] for r in within["excluded"]])
        beyond = self.rec("x" * 59 + "식품")           # 61자 — 뒤가 잘려 낱말이 깨진다
        self.assertNotIn("MIDNIGHT_PREMIUM", [r["code"] for r in beyond["excluded"]])

    def test_unknown_purpose_raises(self):
        with self.assertRaises(ValueError):
            self.rec("식품", "BANNER")

    def test_it_is_deterministic(self):
        self.assertEqual(self.rec("뷰티"), self.rec("뷰티"))

    def test_every_row_is_json_friendly(self):
        import json

        json.dumps(self.rec("생활용품"), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
