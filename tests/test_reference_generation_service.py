"""
=========================================================
Homez OS

File : tests/test_reference_generation_service.py

2026-10-22 — "참고 사진으로 만들기" R3a: 서비스(`reference_generation_service.py`)와 API 경로(`/media-assets/reference-generation*`)를 고정한다. 임시 SQLite·임시 폴더·FAKE 제공자만 —
네트워크·실제 이미지 생성 API 0.

  - 미리보기는 아무것도 쓰지 않는다(자산·작업·결과 행이 늘지 않는다).
  - 권리: 확인 안 된 사진은 확인 기록이 있어야 하고(이미지가 바뀌면 무효), 거부된 사진은 항상 막힌다. 다른 회사 자산은 "없음".
  - 대표(MAIN)는 confirmed_main이 정확히 True여야 실행한다(실행 전에 막혀 작업 행도 남기지 않는다).
  - 결과: 파생 자산(생성물)·참고 사진 연결·권리 상태 승계(격상 없음)·"AI 생성" 파일 이름·작업/결과 기록(검수 전이라 safety는 UNKNOWN).
  - 같은 요청을 반복해도 자산은 늘지 않는다. 제공자 실패·일부 실패·차단은 기록하고 저장하지 않는다.
=========================================================
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import reference_generation as rg
from app.domains.media_asset import schema
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import ImageRightsAcknowledgement
from app.domains.media_asset.model import ImageRightsEvidence
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.providers import ImageGenerationProviderError
from app.domains.media_asset.providers import ImageGenerationProviderResult
from app.domains.media_asset.providers import ImageGenerationResultItem
from app.domains.media_asset.providers import _deterministic_png
from app.domains.media_asset.reference_generation_service import ReferenceGenerationService
from app.domains.media_asset.rights_evidence_service import ImageRightsEvidenceService
from app.domains.media_asset.rights_evidence_service import UserAction
from app.domains.media_asset.rights_evidence_service import WorkflowStage
from app.domains.user.model import User  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SERVICE_SRC = (ROOT / "app/domains/media_asset/reference_generation_service.py").read_text(encoding="utf-8")
AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)
SPEC = dict(purpose="DETAIL", backgrounds=["CAFE", "STREET"], poses=["ARMS_DOWN"], count=2)


class Base_(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, MediaAsset.__table__, ImageGenerationJob.__table__, ImageGenerationResult.__table__,
            ImageGenerationDailyUsage.__table__, ImageRightsEvidence.__table__, ImageRightsAcknowledgement.__table__,
        ])
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.media_root = Path(tempfile.mkdtemp())
        self.queue = ImageGenerationJobQueueService(self.db, media_root=self.media_root)
        self.service = ReferenceGenerationService(self.db, queue=self.queue)
        self.company_id = self.make_company("회사 A", "111-11-11111")
        self.other_company_id = self.make_company("회사 B", "222-22-22222")
        self.reference = self.upload(self.company_id, "reference-photo")
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

    def upload(self, company_id, seed):
        return self.queue.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=7, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png(seed), original_filename="ref.png", company_id=company_id,
        )

    def set_rights(self, asset, status):
        asset.rights_status = status
        self.db.commit()

    def acknowledge(self, asset):
        ImageRightsEvidenceService(self.db).acknowledge(
            company_id=asset.company_id, user_id=1, asset_id=asset.id, workflow_stage=WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
            warning_code="RIGHTS_UNVERIFIED", user_action=UserAction.CONTINUE,
        )

    def counts(self):
        return tuple(self.db.query(func.count(model.id)).scalar() for model in (MediaAsset, ImageGenerationJob, ImageGenerationResult))

    def generate(self, **kwargs):
        args = dict(company_id=self.company_id, requested_by=1, reference_asset_id=self.reference.id, **SPEC)
        args.update(kwargs)
        return self.service.generate(**args)

    def preview(self, **kwargs):
        args = dict(company_id=self.company_id, reference_asset_id=self.reference.id, **SPEC)
        args.update(kwargs)
        return self.service.preview(**args)


class PreviewTests(Base_):

    def test_preview_returns_the_plan_and_writes_nothing(self):
        self.set_rights(self.reference, "VERIFIED")
        before = self.counts()
        result = self.preview()
        self.assertEqual(self.counts(), before)
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["demo"])
        self.assertEqual(result["estimated_cost"], 0.02)
        self.assertEqual(result["reference_asset_id"], self.reference.id)
        self.assertEqual(result["rights_status"], "VERIFIED")
        self.assertEqual(result["label_placement"], "OUTSIDE_IMAGE")
        self.assertTrue(result["no_watermark"])
        self.assertIn("NO_WATERMARK", result["required_checks"])
        self.assertNotIn("image_bytes", str(result))

    def test_preview_of_a_main_image_carries_the_warning_and_the_confirmation_flag(self):
        self.set_rights(self.reference, "VERIFIED")
        result = self.preview(purpose="MAIN", count=1, backgrounds=["STUDIO_PLAIN"])
        self.assertTrue(result["requires_user_confirmation"])
        self.assertEqual(result["warnings"], [rg.MAIN_POLICY_WARNING_KO])
        self.assertIn("MAIN_IMAGE_SPEC", result["required_checks"])

    def test_the_same_preview_has_the_same_fingerprint(self):
        self.set_rights(self.reference, "VERIFIED")
        self.assertEqual(self.preview()["request_fingerprint"], self.preview()["request_fingerprint"])
        self.assertNotEqual(self.preview()["request_fingerprint"], self.preview(backgrounds=["CAFE", "SEASIDE"])["request_fingerprint"])


class RightsAndScopeTests(Base_):

    def test_an_unverified_photo_needs_a_current_acknowledgement(self):
        self.assertEqual(self.reference.rights_status, "RIGHTS_UNVERIFIED")  # 업로드 기본값
        with self.assertRaises(BadRequestException):
            self.preview()
        self.acknowledge(self.reference)
        result = self.preview()
        self.assertTrue(result["rights_acknowledged"])

    def test_the_acknowledgement_stops_counting_when_the_image_changes(self):
        self.acknowledge(self.reference)
        self.preview()
        self.reference.sha256_hex = "f" * 64   # 이미지가 바뀐 것과 같다(지문 변경)
        self.db.commit()
        with self.assertRaises(BadRequestException):
            self.preview()

    def test_a_denied_photo_is_always_blocked(self):
        self.acknowledge(self.reference)
        self.set_rights(self.reference, "RIGHTS_DENIED")
        for call in (self.preview, self.generate):
            with self.assertRaises(BadRequestException):
                call()

    def test_a_cancelled_or_postponed_acknowledgement_does_not_count(self):
        for action in (UserAction.REGISTER_EVIDENCE_LATER, UserAction.CANCELLED):
            ImageRightsEvidenceService(self.db).acknowledge(
                company_id=self.company_id, user_id=1, asset_id=self.reference.id, workflow_stage=WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
                warning_code="RIGHTS_UNVERIFIED", user_action=action,
            )
        with self.assertRaises(BadRequestException):
            self.preview()

    def test_an_acknowledgement_for_another_stage_does_not_count(self):
        ImageRightsEvidenceService(self.db).acknowledge(
            company_id=self.company_id, user_id=1, asset_id=self.reference.id, workflow_stage=WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            warning_code="RIGHTS_UNVERIFIED", user_action=UserAction.CONTINUE,
        )
        with self.assertRaises(BadRequestException):
            self.preview()

    def test_another_companys_asset_and_missing_assets_are_not_found(self):
        foreign = self.upload(self.other_company_id, "foreign-photo")
        self.set_rights(foreign, "VERIFIED")
        for call in (self.preview, self.generate):
            with self.assertRaises(NotFoundException):
                call(reference_asset_id=foreign.id)
            with self.assertRaises(NotFoundException):
                call(reference_asset_id=999999)

    def test_a_missing_file_is_a_bad_request_not_a_crash(self):
        self.set_rights(self.reference, "VERIFIED")
        (self.media_root / self.reference.storage_path).unlink()
        with self.assertRaises(BadRequestException):
            self.preview()


class GenerationTests(Base_):

    def setUp(self):
        super().setUp()
        self.set_rights(self.reference, "VERIFIED")

    def test_generate_stores_derived_assets_linked_to_the_reference(self):
        outcome = self.generate()
        self.assertEqual(len(outcome.assets), 2)
        self.assertTrue(outcome.demo)
        self.assertEqual(outcome.provider_cost, 0.02)
        for index, asset in enumerate(outcome.assets):
            self.assertEqual(asset.asset_role, "GENERATED")
            self.assertEqual(asset.source_asset_id, self.reference.id)
            self.assertEqual(asset.company_id, self.company_id)
            self.assertEqual(asset.owner_id, self.reference.owner_id)
            self.assertRegex(asset.original_filename, r"^aigen-detail-(cafe|street)-arms_down-\d\.png$")
            self.assertTrue((self.media_root / asset.storage_path).exists())

    def test_rights_status_is_inherited_never_upgraded(self):
        self.set_rights(self.reference, "RIGHTS_UNVERIFIED")
        self.acknowledge(self.reference)
        outcome = self.generate()
        self.assertEqual({a.rights_status for a in outcome.assets}, {"RIGHTS_UNVERIFIED"})
        self.set_rights(self.reference, "VERIFIED")
        outcome = self.generate(backgrounds=["SEASIDE"], count=1)
        self.assertEqual({a.rights_status for a in outcome.assets}, {"VERIFIED"})

    def test_the_job_and_results_are_recorded_without_the_photo_bytes(self):
        outcome = self.generate()
        job = self.db.get(ImageGenerationJob, outcome.job_id)
        self.assertEqual((job.status, job.provider_code, job.progress_percent), ("SUCCEEDED", "FAKE", 100))
        self.assertEqual(job.prompt_fingerprint, outcome.plan.request_fingerprint)
        payload = job.request_payload_json
        self.assertIn("REFERENCE_GENERATION", payload)
        self.assertIn(outcome.plan.request_fingerprint, payload)
        self.assertNotIn("image_bytes", payload)
        self.assertNotIn("reference_image", payload)
        results = self.db.query(ImageGenerationResult).filter_by(job_id=job.id).order_by(ImageGenerationResult.sequence_index).all()
        self.assertEqual([(r.sequence_index, r.status) for r in results], [(0, "SUCCEEDED"), (1, "SUCCEEDED")])
        # 사람이 검수하기 전이라 "안전 통과"라고 기록하지 않는다.
        self.assertEqual({r.safety_check_status for r in results}, {"UNKNOWN"})
        self.assertEqual([r.media_asset_id for r in results], [a.id for a in outcome.assets])

    def test_repeating_the_same_request_does_not_add_assets(self):
        first = self.generate()
        assets_after_first = self.db.query(func.count(MediaAsset.id)).scalar()
        second = self.generate()
        self.assertEqual([a.id for a in first.assets], [a.id for a in second.assets])
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets_after_first)
        self.assertEqual(first.plan.request_fingerprint, second.plan.request_fingerprint)
        self.assertEqual(self.db.query(func.count(ImageGenerationJob.id)).scalar(), 2)  # 작업 기록은 실행마다 남는다

    def test_an_existing_asset_with_the_same_bytes_is_reused_and_never_overwritten(self):
        # 같은 바이트의 전혀 다른 출처 자산(사용자가 직접 올린 원본, 사용권 미확인)이 이미 있으면 그 자산의 출처·권리·이름을 바꾸지 않는다(권리 격상 방지).
        from app.domains.media_asset.providers import FakeImageGenerationProvider

        reference = ReferenceGenerationService(self.db, queue=self.queue)._load_reference(self.company_id, self.reference.id)[1]
        plan = rg.build_plan(reference, "DETAIL", ["CAFE"], ["ARMS_DOWN"], 1)
        request = rg.to_provider_request(plan, reference, self.company_id, 0)
        twin_bytes = FakeImageGenerationProvider().generate(request).items[0].image_bytes
        twin = self.queue.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=9, purpose="MAIN", display_order=0, image_bytes=twin_bytes,
            original_filename="mine.png", company_id=self.company_id,
        )
        state = (twin.id, twin.asset_role, twin.source_asset_id, twin.rights_status, twin.original_filename)
        self.assertEqual(twin.rights_status, "RIGHTS_UNVERIFIED")
        outcome = self.generate(backgrounds=["CAFE"], poses=["ARMS_DOWN"], count=1)
        self.assertEqual(outcome.assets[0].id, twin.id)
        self.db.expire_all()
        self.assertEqual((twin.id, twin.asset_role, twin.source_asset_id, twin.rights_status, twin.original_filename), state)

    def test_a_different_reference_photo_gives_different_assets(self):
        first = self.generate()
        other = self.upload(self.company_id, "another-reference")
        self.set_rights(other, "VERIFIED")
        second = self.generate(reference_asset_id=other.id)
        self.assertFalse({a.id for a in first.assets} & {a.id for a in second.assets})

    def test_a_main_image_needs_the_exact_confirmation_and_nothing_is_written_without_it(self):
        before = self.counts()
        for confirmed in (False, None, 1, "true", "yes"):
            with self.assertRaises(BadRequestException, msg=repr(confirmed)):
                self.generate(purpose="MAIN", backgrounds=["STUDIO_PLAIN"], poses=["KEEP_ORIGINAL"], count=1, confirmed_main=confirmed)
        self.assertEqual(self.counts(), before)  # 작업 행도 남기지 않는다
        outcome = self.generate(purpose="MAIN", backgrounds=["STUDIO_PLAIN"], poses=["KEEP_ORIGINAL"], count=1, confirmed_main=True)
        self.assertEqual(len(outcome.assets), 1)
        self.assertIn("aigen-main-", outcome.assets[0].original_filename)
        self.assertTrue(outcome.plan.requires_user_confirmation)

    def test_non_main_purposes_do_not_need_the_confirmation(self):
        for purpose in ("ADDITIONAL", "DETAIL"):
            outcome = self.generate(purpose=purpose, backgrounds=["HOME_INDOOR"], count=1)
            self.assertEqual(len(outcome.assets), 1, purpose)

    def test_a_real_provider_is_refused_before_anything_is_written(self):
        before = self.counts()
        for code in ("OPENAI", "DISABLED", "REAL", ""):
            with self.assertRaises(rg.ReferenceGenerationError, msg=code):
                self.generate(provider_code=code)
        self.assertEqual(self.counts(), before)

    def test_invalid_selections_are_refused_before_anything_is_written(self):
        before = self.counts()
        for bad in (dict(backgrounds=["MOON"]), dict(poses=["BREAKDANCE"]), dict(count=0), dict(count=7), dict(count=3, backgrounds=["CAFE"]), dict(purpose="BANNER")):
            with self.assertRaises(Exception, msg=str(bad)):
                self.generate(**bad)
        self.assertEqual(self.counts(), before)

    def test_a_provider_error_fails_the_job_and_stores_nothing(self):
        class Broken:
            def generate(self, request):
                raise ImageGenerationProviderError("제공자 오류")

        assets_before = self.db.query(func.count(MediaAsset.id)).scalar()
        with mock.patch("app.domains.media_asset.reference_generation_service.get_provider", return_value=Broken()):
            with self.assertRaises(BadRequestException):
                self.generate()
        job = self.db.query(ImageGenerationJob).one()
        self.assertEqual(job.status, "FAILED")
        self.assertIn("제공자 오류", job.error_reason)
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets_before)

    def test_an_unexpected_provider_crash_is_reported_without_leaking_details(self):
        class Crash:
            def generate(self, request):
                raise RuntimeError("secret-token-123")

        with mock.patch("app.domains.media_asset.reference_generation_service.get_provider", return_value=Crash()):
            with self.assertRaises(BadRequestException) as ctx:
                self.generate()
        self.assertNotIn("secret-token-123", str(ctx.exception.detail))
        self.assertEqual(self.db.query(ImageGenerationJob).one().status, "FAILED")
        self.assertNotIn("secret-token-123", self.db.query(ImageGenerationJob).one().error_reason)

    def test_a_partial_failure_keeps_the_good_item_and_records_the_bad_one(self):
        class Half:
            def generate(self, request):
                good = ImageGenerationResultItem(0, "DETAIL", "SUCCEEDED", _deterministic_png("half-good"), "image/png", "PASSED")
                bad = ImageGenerationResultItem(1, "DETAIL", "FAILED", None, None, "UNKNOWN", "시간 초과")
                return ImageGenerationProviderResult(items=(good, bad), provider_cost=0.01)

        with mock.patch("app.domains.media_asset.reference_generation_service.get_provider", return_value=Half()):
            outcome = self.generate()
        self.assertEqual(len(outcome.assets), 1)
        job = self.db.get(ImageGenerationJob, outcome.job_id)
        self.assertEqual(job.status, "PARTIAL")
        results = self.db.query(ImageGenerationResult).filter_by(job_id=job.id).order_by(ImageGenerationResult.sequence_index).all()
        self.assertEqual([(r.status, r.media_asset_id is None) for r in results], [("SUCCEEDED", False), ("FAILED", True)])
        self.assertEqual(results[1].error_reason, "시간 초과")

    def test_a_failed_item_is_not_stored_even_if_the_provider_attached_bytes(self):
        class Odd:
            def generate(self, request):
                bad = ImageGenerationResultItem(0, "DETAIL", "FAILED", _deterministic_png("odd-bytes"), "image/png", "PASSED", "부분 실패")
                return ImageGenerationProviderResult(items=(bad,), provider_cost=0.01)

        assets_before = self.db.query(func.count(MediaAsset.id)).scalar()
        with mock.patch("app.domains.media_asset.reference_generation_service.get_provider", return_value=Odd()):
            with self.assertRaises(BadRequestException):
                self.generate(backgrounds=["CAFE"], count=1)
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets_before)
        self.assertEqual(self.db.query(ImageGenerationResult).one().status, "FAILED")

    def test_a_blocked_item_is_never_stored(self):
        class Blocked:
            def generate(self, request):
                item = ImageGenerationResultItem(0, "DETAIL", "SUCCEEDED", _deterministic_png("blocked"), "image/png", "BLOCKED")
                return ImageGenerationProviderResult(items=(item,), provider_cost=0.01)

        assets_before = self.db.query(func.count(MediaAsset.id)).scalar()
        with mock.patch("app.domains.media_asset.reference_generation_service.get_provider", return_value=Blocked()):
            with self.assertRaises(BadRequestException):
                self.generate(backgrounds=["CAFE"], count=1)
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets_before)
        self.assertEqual(self.db.query(ImageGenerationJob).one().status, "FAILED")
        self.assertEqual(self.db.query(ImageGenerationResult).one().safety_check_status, "BLOCKED")


class RouteTests(Base_):

    def setUp(self):
        super().setUp()
        # 경로 함수는 서비스를 기본 설정으로 만든다 — 기본 미디어 폴더(실제 저장소)에 쓰지 않도록 임시 폴더로 돌린다.
        patcher = mock.patch("app.desktop.paths.get_media_dir", return_value=self.media_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, name):
        import sys

        import app.domains.media_asset.router  # noqa: F401 — sys.modules에 올린다

        return getattr(sys.modules["app.domains.media_asset.router"], name)

    def user(self):
        return SimpleNamespace(id=1, company_id=self.company_id)

    def request(self, **kwargs):
        return schema.ReferenceGenerationRequestSchema(reference_asset_id=self.reference.id, **{**SPEC, **kwargs})

    def test_the_three_routes_exist_with_the_expected_methods(self):
        import sys

        import app.domains.media_asset.router  # noqa: F401

        routes = {(r.path, tuple(sorted(r.methods))) for r in sys.modules["app.domains.media_asset.router"].router.routes}
        self.assertIn(("/media-assets/reference-generation/presets", ("GET",)), routes)
        self.assertIn(("/media-assets/reference-generation/preview", ("POST",)), routes)
        self.assertIn(("/media-assets/reference-generation", ("POST",)), routes)

    def test_the_routes_are_admin_guarded(self):
        import sys

        import app.domains.media_asset.router as _  # noqa: F401

        module = sys.modules["app.domains.media_asset.router"]
        for route in module.router.routes:
            if "reference-generation" in route.path:
                names = {getattr(dep.call, "__name__", type(dep.call).__name__) for dep in route.dependant.dependencies}
                self.assertTrue({"admin_guard", "AdminGuard"} & names, (route.path, names))

    def test_presets_route_states_the_rules(self):
        result = self.route("reference_generation_presets")(current_user=self.user())
        self.assertEqual(result["purposes"], ["MAIN", "ADDITIONAL", "DETAIL"])
        self.assertEqual(result["provider_codes"], ["FAKE"])
        self.assertTrue(result["no_watermark"])
        self.assertEqual(result["label_placement"], "OUTSIDE_IMAGE")
        self.assertEqual(result["generated_label"], "AI 생성 이미지")
        self.assertIn("반려", result["main_policy_warning"])
        self.assertEqual(len(result["backgrounds"]), 6)
        self.assertEqual(len(result["poses"]), 6)

    def test_preview_and_run_routes_work_end_to_end(self):
        self.set_rights(self.reference, "VERIFIED")
        before = self.counts()
        preview = self.route("reference_generation_preview")(data=self.request(), current_user=self.user(), db=self.db)
        self.assertEqual(self.counts(), before)
        run = self.route("reference_generation_run")(data=self.request(), current_user=self.user(), db=self.db)
        self.assertEqual(run["plan"]["request_fingerprint"], preview["request_fingerprint"])
        self.assertEqual(len(run["assets"]), 2)
        self.assertEqual(run["assets"][0].source_asset_id, self.reference.id)
        self.assertTrue(run["demo"])
        self.assertEqual((run["generated_label"], run["label_placement"]), ("AI 생성 이미지", "OUTSIDE_IMAGE"))
        self.assertEqual(run["provider_cost"], 0.02)

    def test_the_main_route_refuses_without_confirmation(self):
        self.set_rights(self.reference, "VERIFIED")
        data = self.request(purpose="MAIN", backgrounds=["STUDIO_PLAIN"], poses=["KEEP_ORIGINAL"], count=1)
        with self.assertRaises(BadRequestException):
            self.route("reference_generation_run")(data=data, current_user=self.user(), db=self.db)
        confirmed = self.request(purpose="MAIN", backgrounds=["STUDIO_PLAIN"], poses=["KEEP_ORIGINAL"], count=1, confirmed_main=True)
        run = self.route("reference_generation_run")(data=confirmed, current_user=self.user(), db=self.db)
        self.assertEqual(len(run["assets"]), 1)

    def test_the_routes_use_the_callers_company(self):
        foreign = self.upload(self.other_company_id, "foreign-route")
        self.set_rights(foreign, "VERIFIED")
        data = schema.ReferenceGenerationRequestSchema(reference_asset_id=foreign.id, **SPEC)
        with self.assertRaises(NotFoundException):
            self.route("reference_generation_run")(data=data, current_user=self.user(), db=self.db)

    def test_request_schema_limits(self):
        for bad in (dict(count=0), dict(count=7), dict(backgrounds=[]), dict(poses=[]), dict(backgrounds=["CAFE", "STREET", "SEASIDE", "HOME_INDOOR"]), dict(purpose="BANNER")):
            with self.assertRaises(ValidationError, msg=str(bad)):
                self.request(**bad)
        self.assertFalse(schema.ReferenceGenerationRequestSchema(reference_asset_id=1, backgrounds=["CAFE"], poses=["WALKING"]).confirmed_main)
        self.assertEqual(schema.ReferenceGenerationRequestSchema(reference_asset_id=1, backgrounds=["CAFE"], poses=["WALKING"]).purpose, "ADDITIONAL")


class PurityTests(unittest.TestCase):

    def test_the_service_makes_no_network_calls_and_never_marks_results_as_passed(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SERVICE_SRC, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "subprocess", "aiohttp", "boto3"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)
        self.assertNotIn("ImageSafetyCheckStatus.PASSED", SERVICE_SRC)
        self.assertNotIn("PASSED", SERVICE_SRC.replace("제공자가", ""))

    def test_the_main_confirmation_is_enforced_by_the_service_not_only_the_screen(self):
        self.assertIn("confirmed_main is not True", SERVICE_SRC)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (PreviewTests, RightsAndScopeTests, GenerationTests, RouteTests, PurityTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
