"""
=========================================================
Homez OS

File : tests/test_batch_adjust_service.py

2026-10-23 — 이미지 AI R2: 일괄 보정 서비스(`batch_adjust_service.py`)·공통 연결 규칙(`derived_assets.py`)·API(`POST /media-assets/batch-adjust`)를 고정한다. 임시 SQLite·임시 폴더 — 네트워크 0.

  - 원본은 그대로 두고 새 파생 자산을 만든다(출처 연결·권리 상태는 만들 때 한 번 물려받기만, 격상 없음). 같은 값으로 다시 하면 같은 자산(멱등).
  - 한 장의 실패·건너뜀이 나머지를 막지 않는다. 다른 회사·없는 번호는 같은 "없음", 사용 금지 사진은 건너뜀.
  - 같은 바이트가 다른 출처의 자산으로 이미 있으면 그 자산의 출처·권리·이름을 덮어쓰지 않는다(권리 격상 방지).
  - 값 검증(범위·정수·0/0)·한도(20장)·중복 번호 처리.
=========================================================
"""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import schema
from app.domains.media_asset.batch_adjust import Adjustment
from app.domains.media_asset.batch_adjust import adjust_image
from app.domains.media_asset.batch_adjust_service import BatchAdjustService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.derived_assets import adopt_derived
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.user.model import User  # noqa: F401

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)


def png(color, size=(40, 30), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
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
        self.service = BatchAdjustService(self.db, queue=self.queue)
        self.company_id = self.make_company("회사 A", "111-11-11111")
        self.other_company_id = self.make_company("회사 B", "222-22-22222")
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
            image_bytes=data, original_filename="orig.png", company_id=company_id or self.company_id,
        )
        asset.rights_status = rights
        self.db.commit()
        return asset

    def counts(self):
        return tuple(self.db.query(func.count(model.id)).scalar() for model in (MediaAsset, ImageGenerationJob, ImageGenerationResult))

    def run_adjust(self, ids, brightness=10, warmth=5, company_id=None):
        return self.service.adjust(company_id or self.company_id, 1, ids, brightness, warmth)


class AdjustTests(Base_):

    def test_creates_one_derived_asset_per_photo_and_leaves_originals_alone(self):
        originals = [self.upload(png((100 + i * 20, 90, 80))) for i in range(3)]
        before = {o.id: (o.sha256_hex, o.rights_status, o.source_asset_id, (self.media_root / o.storage_path).read_bytes()) for o in originals}
        outcomes = self.run_adjust([o.id for o in originals])
        self.assertEqual([(o.status, o.reason) for o in outcomes], [("CREATED", None)] * 3)
        for original, outcome in zip(originals, outcomes):
            derived = outcome.asset
            self.assertEqual((derived.asset_role, derived.source_asset_id, derived.company_id, derived.owner_id), ("GENERATED", original.id, self.company_id, original.owner_id))
            self.assertEqual(derived.original_filename, f"adj-b+10w+5-{original.id}.png")
            expected_bytes, _ = adjust_image((self.media_root / original.storage_path).read_bytes(), Adjustment(10, 5))
            self.assertEqual((self.media_root / derived.storage_path).read_bytes(), expected_bytes)
        self.db.expire_all()
        for original in originals:
            self.assertEqual((original.sha256_hex, original.rights_status, original.source_asset_id, (self.media_root / original.storage_path).read_bytes()), before[original.id])

    def test_rights_are_inherited_once_and_never_upgraded(self):
        unverified = self.upload(png((100, 100, 100)), rights="RIGHTS_UNVERIFIED")
        verified = self.upload(png((120, 100, 100)), rights="VERIFIED")
        a, b = self.run_adjust([unverified.id, verified.id])
        self.assertEqual((a.asset.rights_status, b.asset.rights_status), ("RIGHTS_UNVERIFIED", "VERIFIED"))
        unverified.rights_status = "VERIFIED"     # 원본이 나중에 확인돼도 이미 만든 파생은 자동으로 따라가지 않는다
        self.db.commit()
        again = self.run_adjust([unverified.id])[0]
        self.assertEqual(again.asset.rights_status, "RIGHTS_UNVERIFIED")

    def test_repeating_gives_the_same_assets_and_reports_reused(self):
        original = self.upload(png((100, 100, 100)))
        first = self.run_adjust([original.id])[0]
        assets_after_first = self.db.query(func.count(MediaAsset.id)).scalar()
        second = self.run_adjust([original.id])[0]
        self.assertEqual((first.status, second.status, second.reason), ("CREATED", "REUSED", None))
        self.assertEqual(first.asset.id, second.asset.id)
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), assets_after_first)
        self.assertEqual(self.db.query(func.count(ImageGenerationJob.id)).scalar(), 2)   # 작업 기록은 실행마다 남는다

    def test_different_values_make_different_assets(self):
        original = self.upload(png((100, 100, 100)))
        a = self.run_adjust([original.id], 10, 0)[0].asset
        b = self.run_adjust([original.id], 11, 0)[0].asset
        self.assertNotEqual(a.id, b.id)

    def test_alpha_is_preserved_for_cutouts(self):
        cutout = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
        for x in range(10, 20):
            for y in range(10, 20):
                cutout.putpixel((x, y), (180, 90, 60, 255))
        buffer = io.BytesIO()
        cutout.save(buffer, format="PNG")
        original = self.upload(buffer.getvalue())
        derived = self.run_adjust([original.id], 20, 0)[0].asset
        result = Image.open(self.media_root / derived.storage_path)
        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((15, 15)), (216, 108, 72, 255))

    def test_job_records_are_local_and_describe_the_adjustment(self):
        original = self.upload(png((100, 100, 100)))
        self.run_adjust([original.id], -20, 15)
        job = self.db.query(ImageGenerationJob).one()
        self.assertEqual((job.provider_code, job.model_name, job.status), ("PILLOW_LOCAL", "b-20w+15", "SUCCEEDED"))
        self.assertIn("BATCH_ADJUST", job.request_payload_json)
        result = self.db.query(ImageGenerationResult).one()
        self.assertEqual((result.status, result.media_asset_id is not None), ("SUCCEEDED", True))


class IsolationTests(Base_):

    def test_one_failure_or_skip_does_not_stop_the_others(self):
        good = self.upload(png((100, 100, 100)))
        broken = self.upload(png((10, 10, 10)))
        (self.media_root / broken.storage_path).write_bytes(b"corrupted")
        denied = self.upload(png((50, 60, 70)), rights="RIGHTS_DENIED")
        foreign = self.upload(png((70, 60, 50)), company_id=self.other_company_id)
        outcomes = self.run_adjust([good.id, broken.id, denied.id, foreign.id, 999999])
        self.assertEqual([(o.asset_id, o.status, o.reason if o.status == "SKIPPED" else None) for o in outcomes],
                         [(good.id, "CREATED", None), (broken.id, "FAILED", None), (denied.id, "SKIPPED", "RIGHTS_DENIED"), (foreign.id, "SKIPPED", "NOT_FOUND"), (999999, "SKIPPED", "NOT_FOUND")])
        self.assertIsNotNone(outcomes[0].asset)
        self.assertTrue(outcomes[1].reason)
        jobs = {j.source_asset_id if hasattr(j, "source_asset_id") else None: j.status for j in self.db.query(ImageGenerationJob).all()}
        self.assertEqual(sorted(j.status for j in self.db.query(ImageGenerationJob).all()), ["FAILED", "SUCCEEDED"])   # 건너뜀은 작업을 만들지 않는다
        self.assertEqual(jobs and len(jobs) >= 1, True)

    def test_an_unexpected_error_is_reported_without_details(self):
        original = self.upload(png((100, 100, 100)))
        with mock.patch("app.domains.media_asset.batch_adjust_service.adjust_image", side_effect=RuntimeError("secret-token-123")):
            outcome = self.run_adjust([original.id])[0]
        self.assertEqual(outcome.status, "FAILED")
        self.assertNotIn("secret-token-123", outcome.reason)
        self.assertEqual(self.db.query(ImageGenerationJob).one().status, "FAILED")
        self.assertNotIn("secret-token-123", self.db.query(ImageGenerationJob).one().error_reason)

    def test_the_other_companys_assets_are_untouched(self):
        foreign = self.upload(png((70, 60, 50)), company_id=self.other_company_id)
        before = self.counts()
        outcome = self.run_adjust([foreign.id])[0]
        self.assertEqual((outcome.status, outcome.reason), ("SKIPPED", "NOT_FOUND"))
        self.assertEqual(self.counts(), before)


class SameContentTests(Base_):

    def test_a_different_origin_asset_with_the_same_bytes_is_never_overwritten(self):
        source = self.upload(png((100, 100, 100)), rights="VERIFIED")
        adjusted, _ = adjust_image((self.media_root / source.storage_path).read_bytes(), Adjustment(10, 5))
        squatter = self.upload(adjusted, rights="RIGHTS_UNVERIFIED")     # 같은 바이트를 사용자가 직접 올린 다른 자산
        squatter_state = (squatter.id, squatter.asset_role, squatter.source_asset_id, squatter.rights_status, squatter.original_filename)
        outcome = self.run_adjust([source.id])[0]
        self.assertEqual((outcome.status, outcome.reason, outcome.asset.id), ("REUSED", "EXISTING_ASSET_WITH_SAME_CONTENT", squatter.id))
        self.db.expire_all()
        self.assertEqual((squatter.id, squatter.asset_role, squatter.source_asset_id, squatter.rights_status, squatter.original_filename), squatter_state)
        self.assertEqual(squatter.rights_status, "RIGHTS_UNVERIFIED")     # 확인된 원본에서 파생했어도 미확인 자산이 확인됨으로 바뀌지 않는다

    def test_a_derived_asset_from_another_source_is_not_relinked(self):
        a = self.upload(png((100, 100, 100)))
        b = self.upload(png((100, 100, 100)), owner_id=8)    # 같은 내용의 다른 업로드(같은 바이트 → 같은 저장 경로)
        same = self.run_adjust([a.id])[0].asset
        self.assertEqual(same.source_asset_id, a.id)
        other = self.run_adjust([b.id])
        if other[0].asset is not None and other[0].asset.id == same.id:
            self.assertEqual(self.db.get(MediaAsset, same.id).source_asset_id, a.id)   # 처음 연결된 출처를 바꾸지 않는다


class DerivedAssetRuleTests(unittest.TestCase):

    def asset(self, **kw):
        base = dict(id=10, asset_role="GENERATED", source_asset_id=None, rights_status="RIGHTS_UNVERIFIED", original_filename=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_a_new_generated_asset_is_linked_once(self):
        derived, source = self.asset(), SimpleNamespace(id=1, rights_status="VERIFIED")
        self.assertTrue(adopt_derived(derived, source, "x.png"))
        self.assertEqual((derived.source_asset_id, derived.rights_status, derived.original_filename), (1, "VERIFIED", "x.png"))

    def test_the_same_source_changes_nothing(self):
        derived, source = self.asset(source_asset_id=1, rights_status="RIGHTS_UNVERIFIED", original_filename="old.png"), SimpleNamespace(id=1, rights_status="VERIFIED")
        self.assertTrue(adopt_derived(derived, source, "new.png"))
        self.assertEqual((derived.rights_status, derived.original_filename), ("RIGHTS_UNVERIFIED", "old.png"))

    def test_other_sources_and_non_generated_assets_are_left_alone(self):
        source = SimpleNamespace(id=1, rights_status="VERIFIED")
        other = self.asset(source_asset_id=2)
        self.assertFalse(adopt_derived(other, source, "x.png"))
        self.assertEqual((other.source_asset_id, other.rights_status, other.original_filename), (2, "RIGHTS_UNVERIFIED", None))
        for role in ("ORIGINAL", "CHANNEL_VARIANT"):
            original = self.asset(asset_role=role)
            self.assertFalse(adopt_derived(original, source, "x.png"))
            self.assertEqual((original.source_asset_id, original.rights_status), (None, "RIGHTS_UNVERIFIED"))

    def test_long_file_names_are_cut(self):
        derived = self.asset()
        adopt_derived(derived, SimpleNamespace(id=1, rights_status="VERIFIED"), "a" * 400)
        self.assertEqual(len(derived.original_filename), 255)


class ValidationTests(Base_):

    def test_invalid_values_are_refused_before_anything_is_written(self):
        original = self.upload(png((100, 100, 100)))
        before = self.counts()
        for brightness, warmth in ((0, 0), (51, 0), (0, -51), (True, 5), (5.5, 5), ("5", 5), (None, 5)):
            with self.assertRaises(BadRequestException, msg=str((brightness, warmth))):
                self.service.adjust(self.company_id, 1, [original.id], brightness, warmth)
        self.assertEqual(self.counts(), before)

    def test_id_list_rules(self):
        original = self.upload(png((100, 100, 100)))
        before = self.counts()
        for ids in ([], None, [0], [-3], [True], ["1"], [1.5]):
            with self.assertRaises(BadRequestException, msg=repr(ids)):
                self.service.adjust(self.company_id, 1, ids, 10, 0)
        self.assertEqual(self.counts(), before)
        outcomes = self.service.adjust(self.company_id, 1, [original.id, original.id, original.id], 10, 0)
        self.assertEqual(len(outcomes), 1)    # 같은 번호는 한 번만

    def test_the_limit_is_twenty(self):
        ids = [self.upload(png((i, 50, 50))).id for i in range(20)]
        self.assertEqual(len(self.service.adjust(self.company_id, 1, ids, 5, 0)), 20)
        with self.assertRaises(BadRequestException):
            self.service.adjust(self.company_id, 1, ids + [10 ** 6], 5, 0)


class RouteTests(Base_):

    def setUp(self):
        super().setUp()
        patcher = mock.patch("app.desktop.paths.get_media_dir", return_value=self.media_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self):
        import sys

        import app.domains.media_asset.router  # noqa: F401

        return sys.modules["app.domains.media_asset.router"]

    def user(self):
        return SimpleNamespace(id=1, company_id=self.company_id)

    def test_route_exists_post_only_and_admin_guarded(self):
        module = self.route()
        routes = [r for r in module.router.routes if r.path == "/media-assets/batch-adjust"]
        self.assertEqual([sorted(r.methods) for r in routes], [["POST"]])
        names = {getattr(d.call, "__name__", type(d.call).__name__) for d in routes[0].dependant.dependencies}
        self.assertTrue({"admin_guard", "AdminGuard"} & names, names)

    def test_route_returns_per_photo_results_and_counts(self):
        a = self.upload(png((100, 100, 100)))
        data = schema.BatchAdjustRequestSchema(asset_ids=[a.id, 999999], brightness=10, warmth=0)
        result = self.route().batch_adjust_run(data=data, current_user=self.user(), db=self.db)
        self.assertEqual((result["created"], result["reused"], result["skipped"], result["failed"]), (1, 0, 1, 0))
        self.assertEqual([r["status"] for r in result["results"]], ["CREATED", "SKIPPED"])
        self.assertEqual(result["results"][0]["asset"].source_asset_id, a.id)
        self.assertIsNone(result["results"][1]["asset"])
        self.assertEqual(result["results"][1]["reason"], "NOT_FOUND")
        again = self.route().batch_adjust_run(data=data, current_user=self.user(), db=self.db)
        self.assertEqual((again["created"], again["reused"]), (0, 1))

    def test_schema_limits(self):
        for bad in (dict(asset_ids=[]), dict(asset_ids=list(range(1, 22))), dict(asset_ids=[1], brightness=51), dict(asset_ids=[1], warmth=-51),
                    dict(asset_ids=[1], brightness=1.5), dict(asset_ids=["x"]), dict(asset_ids=[0]),
                    dict(asset_ids=[1], brightness="5"), dict(asset_ids=[1], warmth="5"), dict(asset_ids=[1], warmth=True)):
            with self.assertRaises(ValidationError, msg=str(bad)):
                schema.BatchAdjustRequestSchema(**{"brightness": 5, "warmth": 0, **bad})
        self.assertEqual(schema.BatchAdjustRequestSchema(asset_ids=[1]).brightness, 0)

    def test_the_route_uses_the_callers_company(self):
        foreign = self.upload(png((100, 100, 100)), company_id=self.other_company_id)
        data = schema.BatchAdjustRequestSchema(asset_ids=[foreign.id], brightness=10, warmth=0)
        result = self.route().batch_adjust_run(data=data, current_user=self.user(), db=self.db)
        self.assertEqual((result["created"], result["skipped"]), (0, 1))

    def test_both_zero_is_a_bad_request_from_the_route(self):
        a = self.upload(png((100, 100, 100)))
        with self.assertRaises(BadRequestException):
            self.route().batch_adjust_run(data=schema.BatchAdjustRequestSchema(asset_ids=[a.id]), current_user=self.user(), db=self.db)


if __name__ == "__main__":
    unittest.main()
