"""
=========================================================
Homez OS

File : tests/test_image_edit_record.py

2026-11-13 — 이미지 AI 편집 기록(조사 문서 3-3): `image_edit_record.py`, `GET /media-assets/owners/{type}/{id}/edit-records`, 사전검사 경고 3종,
위저드 사진 카드의 한 줄 기록을 고정한다. 임시 SQLite·임시 폴더 — 네트워크·AI 0.

  - 실제 서비스(누끼 Fake·배경 합성·긴 이미지 나누기·컨셉 만들기·일괄 보정)로 만든 사진의 단계가 원본까지 순서대로 나온다.
  - 처리 기록 하나하나의 분류(작업별), AI가 그린 배경·AI 생성은 'AI 생성 포함', 장면·다른 사진 배경·AI는 '연출'.
  - 모르는 것은 모름: 기록 없는 생성 사진은 UNKNOWN, 다른 회사 출처·고리는 잘린 기록으로. 읽기 전용(DB 쓰기 0).
  - 사전검사: AI 생성 사진, 연출 사진이 대표(첫 번째), 기록 없는 사진을 **경고만**(차단 없음). 기록을 못 읽어도 사전검사는 계속.
=========================================================
"""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_precheck as pre
from app.domains.media_asset import image_edit_record as ier
from app.domains.media_asset.batch_adjust_service import BatchAdjustService
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.concept_run_service import ConceptRunService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.user.model import User  # noqa: F401
from tests.test_reference_generation_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
    "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50))"
)


def png(color=(200, 80, 40), size=(400, 400), alpha=False):
    image = Image.new("RGBA" if alpha else "RGB", size, (0, 0, 0, 0) if alpha else (240, 240, 240))
    ImageDraw.Draw(image).ellipse((80, 80, size[0] - 80, size[1] - 80), fill=color + ((255,) if alpha else ()))
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
        self.service = ier.ImageEditRecordService(self.db)
        self.company_id = self.make_company("회사 A", "111-11-11111")
        self.other_company_id = self.make_company("회사 B", "222-22-22222")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)
        shutil.rmtree(self.media_root, ignore_errors=True)

    def make_company(self, name, number):
        company = Company(name=name, business_number=number, ceo="테스트", phone="02-000-0000", email="x@test.com", address="서울")
        self.db.add(company)
        self.db.commit()
        return company.id

    def upload(self, data, purpose="MAIN", company_id=None):
        asset = self.queue.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=7, purpose=purpose, display_order=0,
            image_bytes=data, original_filename="p.png", company_id=company_id or self.company_id,
        )
        asset.rights_status = "VERIFIED"
        self.db.commit()
        return asset

    def derived(self, source, operation, provider="PILLOW_LOCAL", model=None, color=(10, 20, 30), record=True, company_id=None):
        """서비스가 쓰는 같은 기록 규칙(_record_processing_job → _persist_generated_asset → _finish_processing_job)으로 파생 사진을 만든다."""

        company_id = company_id or self.company_id
        job = self.queue._record_processing_job(operation=operation, provider_code=provider, model_name=model, source_asset_id=source.id,  # noqa: SLF001
                                                requested_by=1, company_id=company_id, product_candidate_id=7) if record else None
        asset = self.queue._persist_generated_asset(company_id=company_id, owner_type=source.owner_type, owner_id=source.owner_id,  # noqa: SLF001
                                                    purpose="ADDITIONAL", display_order=0, image_bytes=png(color), self_reported_mime_type="image/png")
        asset.source_asset_id = source.id
        self.db.commit()
        if job is not None:
            self.queue._finish_processing_job(job.id, company_id, success=True, media_asset_id=asset.id)  # noqa: SLF001
        return asset

    def kinds(self, asset):
        return self.service.records(self.company_id, [asset.id])[asset.id].kinds


class ClassifyTests(unittest.TestCase):

    def job(self, operation=None, provider="PILLOW_LOCAL", model=None):
        payload = {"source_asset_id": 1} if operation is None else {"operation": operation, "source_asset_id": 1}
        return ImageGenerationJob(provider_code=provider, model_name=model, request_payload_json=json.dumps(payload))

    def test_each_operation_has_its_kind(self):
        cases = [
            (("BACKGROUND_REMOVAL", "REMBG", None), ier.CUTOUT), (("MANUAL_EDIT", "LOCAL_MANUAL_EDIT", None), ier.MANUAL_EDIT),
            (("BATCH_ADJUST", "PILLOW_LOCAL", "b+10_w0"), ier.ADJUST),
            (("BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "WHITE"), ier.BACKGROUND_SWAP), (("BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "GRADIENT"), ier.BACKGROUND_SWAP),
            (("BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "SOLID_COLOR"), ier.BACKGROUND_SWAP), (("BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "SCENE"), ier.SCENE),
            (("BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "EXISTING_ASSET"), ier.BACKGROUND_PHOTO), (("BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "WHAT"), ier.UNKNOWN),
            (("ADAPTIVE_BACKGROUND_COMPOSITION", "PILLOW_LOCAL", "ADAPT_WHITE"), ier.BACKGROUND_SWAP),
            (("CONCEPT_RUN", "PILLOW_LOCAL", "CLEAN_WHITE"), ier.BACKGROUND_SWAP), (("CONCEPT_RUN", "PILLOW_LOCAL", "SOFT_BEIGE"), ier.BACKGROUND_SWAP),
            (("CONCEPT_RUN", "PILLOW_LOCAL", "WOOD_WARM"), ier.SCENE), (("CONCEPT_RUN", "PILLOW_LOCAL", "NO_SUCH"), ier.UNKNOWN),
            (("AI_BACKGROUND_RUN", "CLOUDFLARE", "AI_KITCHEN_COUNTER-v2"), ier.AI_BACKGROUND),
            (("REFERENCE_GENERATION", "FAKE", "abc"), ier.AI_GENERATED), (("DETAIL_PAGE_GENERATION", "PILLOW_LOCAL", None), ier.DETAIL_PAGE),
            ((None, "GEMINI", None), ier.AI_GENERATED), ((None, "PILLOW_LOCAL", None), ier.UNKNOWN), (("SOMETHING_NEW", "PILLOW_LOCAL", None), ier.UNKNOWN),
        ]
        for (operation, provider, model), expected in cases:
            self.assertEqual(ier.classify_job(self.job(operation, provider, model))[0], expected, (operation, provider, model))

    def test_ai_background_detail_is_the_concept_code(self):
        self.assertEqual(ier.classify_job(self.job("AI_BACKGROUND_RUN", "CLOUDFLARE", "AI_KITCHEN_COUNTER-v2"))[2], "AI_KITCHEN_COUNTER")

    def test_a_broken_payload_is_not_a_crash(self):
        job = ImageGenerationJob(provider_code="PILLOW_LOCAL", model_name=None, request_payload_json="{not json")
        self.assertEqual(ier.classify_job(job)[0], ier.UNKNOWN)
        job = ImageGenerationJob(provider_code="PILLOW_LOCAL", model_name=None, request_payload_json="[1,2]")
        self.assertEqual(ier.classify_job(job)[0], ier.UNKNOWN)

    def test_flags(self):
        rec = lambda *kinds: ier.ImageEditRecord(1, tuple(ier.EditStep(k, i) for i, k in enumerate(kinds)))  # noqa: E731
        self.assertTrue(rec(ier.ORIGINAL, ier.CUTOUT, ier.AI_BACKGROUND).generative)
        self.assertTrue(rec(ier.ORIGINAL, ier.AI_GENERATED).staged)
        self.assertTrue(rec(ier.ORIGINAL, ier.AI_GENERATED).generative)
        self.assertFalse(rec(ier.ORIGINAL, ier.CUTOUT, ier.SCENE).generative)
        self.assertTrue(rec(ier.ORIGINAL, ier.CUTOUT, ier.SCENE).staged)
        self.assertFalse(rec(ier.ORIGINAL, ier.CUTOUT, ier.BACKGROUND_SWAP).staged)
        self.assertTrue(rec(ier.UNKNOWN).unknown)
        self.assertTrue(ier.ImageEditRecord(1, (ier.EditStep(ier.CUTOUT, 1),), truncated=True).unknown)
        self.assertEqual(rec(ier.ORIGINAL, ier.CUTOUT).to_dict()["steps"][1], {"kind": "CUTOUT", "asset_id": 1, "operation": None, "detail": None})


class ServiceTests(Base_):

    def test_real_services_give_the_steps_in_order(self):
        original = self.upload(png())
        cutout = self.queue.remove_background(original.id, "FAKE", 1, self.company_id)
        white = self.queue.compose_background(cutout.id, BackgroundSpec(kind=BackgroundKind.WHITE), 1, self.company_id)
        self.assertEqual(self.kinds(original), [ier.ORIGINAL])
        self.assertEqual(self.kinds(cutout), [ier.ORIGINAL, ier.CUTOUT])
        self.assertEqual(self.kinds(white), [ier.ORIGINAL, ier.CUTOUT, ier.BACKGROUND_SWAP])
        rec = self.service.records(self.company_id, [white.id])[white.id]
        self.assertEqual([s.asset_id for s in rec.steps], [original.id, cutout.id, white.id])
        self.assertFalse(rec.generative or rec.staged or rec.unknown)

    def test_concept_run_scene_is_staged_but_not_ai(self):
        cutout = self.upload(png(alpha=True))
        outcome = ConceptRunService(self.db, queue=self.queue).run(self.company_id, 1, cutout.id, ["WOOD_WARM", "SOFT_BEIGE"], "ADDITIONAL")
        scene, plain = outcome[0].asset, outcome[1].asset
        self.assertEqual(self.kinds(scene), [ier.ORIGINAL, ier.SCENE])
        self.assertEqual(self.kinds(plain), [ier.ORIGINAL, ier.BACKGROUND_SWAP])
        rec = self.service.records(self.company_id, [scene.id])[scene.id]
        self.assertTrue(rec.staged)
        self.assertFalse(rec.generative)

    def test_batch_adjust_on_top_keeps_the_ai_flag_from_further_back(self):
        cutout = self.upload(png(alpha=True))
        ai = self.derived(cutout, "AI_BACKGROUND_RUN", provider="CLOUDFLARE", model="AI_KITCHEN_COUNTER-v1")
        ai.rights_status = "VERIFIED"
        self.db.commit()
        adjusted = BatchAdjustService(self.db, queue=self.queue).adjust(self.company_id, 1, [ai.id], 10, 0)
        new_id = adjusted[0].asset.id
        rec = self.service.records(self.company_id, [new_id])[new_id]
        self.assertEqual(rec.kinds, [ier.ORIGINAL, ier.AI_BACKGROUND, ier.ADJUST])
        self.assertTrue(rec.generative)
        self.assertTrue(rec.staged)

    def test_long_image_pieces_are_split_without_a_record(self):
        long_original = self.upload(png(size=(300, 1900)), purpose="DETAIL_SOURCE")
        pieces = self.queue.split_long_image(long_original.id, 600, 1, self.company_id)
        self.assertGreaterEqual(len(pieces), 2)
        for piece in pieces:
            self.assertEqual(self.kinds(piece), [ier.ORIGINAL, ier.SPLIT])

    def test_a_generated_photo_without_any_record_is_unknown(self):
        original = self.upload(png())
        mystery = self.derived(original, "X", record=False)
        rec = self.service.records(self.company_id, [mystery.id])[mystery.id]
        self.assertEqual(rec.kinds, [ier.ORIGINAL, ier.UNKNOWN])
        self.assertTrue(rec.unknown)

    def test_a_split_signature_needs_both_purposes(self):
        main_original = self.upload(png(), purpose="MAIN")
        piece = self.derived(main_original, "X", record=False)
        piece.purpose = "DETAIL"
        self.db.commit()
        self.assertEqual(self.kinds(piece), [ier.ORIGINAL, ier.UNKNOWN])
        long_original = self.upload(png(color=(4, 4, 4)), purpose="DETAIL_SOURCE")
        not_detail = self.derived(long_original, "X", record=False, color=(5, 4, 4))      # 용도가 DETAIL이 아니면 조각으로 보지 않는다
        self.assertEqual(self.kinds(not_detail), [ier.ORIGINAL, ier.UNKNOWN])

    def test_a_photo_with_only_a_failed_job_is_unknown(self):
        original = self.upload(png(color=(8, 1, 1)))
        job = self.queue._record_processing_job(operation="AI_BACKGROUND_RUN", provider_code="CLOUDFLARE", model_name="AI_KITCHEN_COUNTER-v1",  # noqa: SLF001
                                                source_asset_id=original.id, requested_by=1, company_id=self.company_id, product_candidate_id=7)
        asset = self.derived(original, "X", record=False, color=(8, 2, 2))
        self.queue._finish_processing_job(job.id, self.company_id, success=False, media_asset_id=asset.id)  # noqa: SLF001
        self.assertEqual(self.kinds(asset), [ier.ORIGINAL, ier.UNKNOWN])

    def test_a_failed_job_is_not_used(self):
        original = self.upload(png())
        job = self.queue._record_processing_job(operation="AI_BACKGROUND_RUN", provider_code="CLOUDFLARE", model_name="AI_KITCHEN_COUNTER-v1",  # noqa: SLF001
                                                source_asset_id=original.id, requested_by=1, company_id=self.company_id, product_candidate_id=7)
        asset = self.derived(original, "BACKGROUND_REMOVAL", provider="REMBG")
        self.queue._finish_processing_job(job.id, self.company_id, success=False, media_asset_id=asset.id)  # noqa: SLF001
        self.assertEqual(self.kinds(asset), [ier.ORIGINAL, ier.CUTOUT])

    def test_another_companys_source_cuts_the_record(self):
        foreign = self.upload(png(color=(1, 2, 3)), company_id=self.other_company_id)
        mine = self.derived(foreign, "BACKGROUND_REMOVAL", provider="REMBG")
        rec = self.service.records(self.company_id, [mine.id])[mine.id]
        self.assertEqual(rec.kinds, [ier.CUTOUT])
        self.assertTrue(rec.truncated)
        self.assertTrue(rec.unknown)
        self.assertEqual(self.service.records(self.other_company_id, [mine.id]), {})      # 다른 회사는 이 사진을 모른다

    def test_a_loop_in_sources_stops(self):
        a = self.upload(png(color=(5, 5, 5)))
        b = self.derived(a, "BACKGROUND_REMOVAL", provider="REMBG", color=(6, 6, 6))
        c = self.derived(b, "MANUAL_EDIT", provider="LOCAL_MANUAL_EDIT", color=(7, 7, 7))
        a.asset_role, a.source_asset_id = "GENERATED", c.id
        self.db.commit()
        rec = self.service.records(self.company_id, [c.id])[c.id]
        self.assertTrue(rec.truncated)
        self.assertLessEqual(len(rec.steps), ier.MAX_DEPTH)

    def test_read_only_and_bad_ids_are_ignored(self):
        original = self.upload(png())
        cutout = self.queue.remove_background(original.id, "FAKE", 1, self.company_id)
        before = tuple(self.db.query(func.count(m.id)).scalar() for m in (MediaAsset, ImageGenerationJob, ImageGenerationResult))
        out = self.service.records(self.company_id, [cutout.id, cutout.id, 999999, True, "x", original.id])
        self.assertEqual(sorted(out), sorted([cutout.id, original.id]))
        after = tuple(self.db.query(func.count(m.id)).scalar() for m in (MediaAsset, ImageGenerationJob, ImageGenerationResult))
        self.assertEqual(before, after)
        self.assertEqual(self.service.records(self.company_id, []), {})


class PrecheckTests(Base_):

    def issues(self, ids):
        return {i.code: i for i in pre._media_edit_issues(self.db, self.company_id, ids)}  # noqa: SLF001

    def test_ai_generated_main_staged_and_unknown_are_warnings_only(self):
        original = self.upload(png())
        ai = self.derived(original, "AI_BACKGROUND_RUN", provider="CLOUDFLARE", model="AI_LIVING_TABLE-v1", color=(1, 1, 1))
        scene = self.derived(original, "CONCEPT_RUN", model="WOOD_WARM", color=(2, 2, 2))
        mystery = self.derived(original, "X", record=False, color=(3, 3, 3))
        found = self.issues([scene.id, ai.id, mystery.id, original.id])
        self.assertEqual(set(found), {"MEDIA_AI_GENERATED", "MEDIA_MAIN_STAGED", "MEDIA_EDIT_UNKNOWN"})
        for issue in found.values():
            self.assertFalse(issue.blocking)
            self.assertEqual(issue.severity, "WARNING")
            self.assertEqual(issue.step, "MEDIA")
        self.assertEqual(found["MEDIA_AI_GENERATED"].params, {"count": 1, "ids": f"#{ai.id}"})
        self.assertEqual(found["MEDIA_MAIN_STAGED"].params, {"id": f"#{scene.id}"})
        self.assertEqual(found["MEDIA_EDIT_UNKNOWN"].params, {"ids": f"#{mystery.id}"})

    def test_plain_photos_give_no_warning_and_staged_later_is_fine(self):
        original = self.upload(png())
        cutout = self.queue.remove_background(original.id, "FAKE", 1, self.company_id)
        white = self.queue.compose_background(cutout.id, BackgroundSpec(kind=BackgroundKind.WHITE), 1, self.company_id)
        scene = self.derived(cutout, "CONCEPT_RUN", model="WOOD_WARM", color=(9, 9, 9))
        self.assertEqual(self.issues([white.id, scene.id, original.id]), {})
        self.assertEqual(self.issues([]), {})

    def test_a_record_failure_does_not_stop_the_precheck(self):
        original = self.upload(png())
        real = ier.ImageEditRecordService.records
        ier.ImageEditRecordService.records = lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(pre._media_edit_issues(self.db, self.company_id, [original.id]), [])  # noqa: SLF001
        finally:
            ier.ImageEditRecordService.records = real

    def test_the_precheck_wires_it_in_after_the_existence_check(self):
        source = (ROOT / "app/domains/marketplace_listing/listing_wizard_precheck.py").read_text(encoding="utf-8")
        block = source[source.index('"MEDIA_ASSET_NOT_FOUND", WizardStep.MEDIA, True,'):source.index("# --- 4/5단계")]
        self.assertIn("issues.extend(_media_edit_issues(db, company_id, media_ids))", block)
        self.assertNotIn("True", source[source.index("def _media_edit_issues"):source.index("def run_precheck")].split('"""', 2)[2].replace("is True", ""))


class RouterTests(unittest.TestCase):

    def test_route_is_a_get_with_the_wizard_view_guard(self):
        from app.domains.media_asset.router import router
        routes = [r for r in router.routes if getattr(r, "path", "") == "/media-assets/owners/{owner_type}/{owner_id}/edit-records"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {"GET"})
        source = (ROOT / "app/domains/media_asset/router.py").read_text(encoding="utf-8")
        block = source[source.index('@router.get("/owners/{owner_type}/{owner_id}/edit-records")'):]
        block = block[:block.index("@router.", 1)]
        self.assertIn("ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)", block)
        self.assertIn("current_user.company_id", block)


# ---------------- 화면 ----------------

def lw_table(source, prefix):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(' + re.escape(prefix) + r'[a-z_0-9.]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key) { return TABLE[key] !== undefined ? TABLE[key] : key; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class UiBehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        const = re.search(r"^  const LW_EDIT_KINDS = [^\n]+$", JS, re.M).group(0)
        script = HARNESS.replace("__SOURCES__", const + "\n" + function_source("lwEditRecordHtml")).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(lw_table(KO, "lw.edit_"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_steps_read_in_order_with_badges(self):
        html = self.run_js('lwEditRecordHtml({steps: [{kind: "ORIGINAL"}, {kind: "CUTOUT"}, {kind: "AI_BACKGROUND"}], generative: true, staged: true, unknown: false})')
        self.assertIn("편집 기록:", html)
        self.assertIn("원본 → 배경 지움 → AI가 그린 배경 합성", html)
        self.assertIn('class="lw-edit-badge is-ai"', html)
        self.assertIn("AI 생성 포함", html)
        self.assertNotIn("연출 사진", html)                     # AI 표시가 있으면 연출 표시는 겹쳐 달지 않는다
        html = self.run_js('lwEditRecordHtml({steps: [{kind: "ORIGINAL"}, {kind: "SCENE"}], generative: false, staged: true, unknown: true})')
        self.assertIn("연출 사진", html)
        self.assertIn("기록 일부 없음", html)
        html = self.run_js('lwEditRecordHtml({steps: [{kind: "ORIGINAL"}], generative: false, staged: false, unknown: false})')
        self.assertNotIn("lw-edit-badge", html)

    def test_unknown_kinds_and_empty_records(self):
        html = self.run_js('lwEditRecordHtml({steps: [{kind: "<img src=x onerror=1>"}, null], generative: "yes"})')
        self.assertNotIn("<img", html)
        self.assertEqual(html.count("알 수 없는 단계"), 2)
        self.assertNotIn("AI 생성 포함", html)                  # true가 아니면 표시하지 않는다
        for empty in ("null", "undefined", "{}", "{steps: []}", '{steps: "x"}'):
            self.assertEqual(self.run_js(f"lwEditRecordHtml({empty})"), "")


class UiSourceTests(unittest.TestCase):

    def test_cards_have_the_line_and_it_is_filled_from_the_owner_route(self):
        self.assertIn('<p class="lw-edit-record" id="lw-edit-${a.id}"></p>', JS)
        self.assertIn("apiFetch(`/media-assets/owners/PRODUCT_CANDIDATE/${encodeURIComponent(w.product_candidate_id)}/edit-records`)", JS)
        fill = JS[JS.index("/edit-records`)"):]
        fill = fill[:fill.index("}).catch(() => {});") + 20]
        self.assertIn("line.innerHTML = lwEditRecordHtml(records[String(a.id)]);", fill)
        self.assertIn(".lw-edit-record:empty { display: none; }", CSS)

    def test_kind_list_matches_the_server_and_every_key_exists_in_both_languages(self):
        kinds = re.search(r"const LW_EDIT_KINDS = \[([^\]]+)\]", JS).group(1).replace('"', "").replace(" ", "").split(",")
        self.assertEqual(kinds, list(ier.ALL_KINDS))
        ko, en = lw_table(KO, "lw.edit_"), lw_table(EN, "lw.edit_")
        needed = {f"lw.edit_kind_{k.lower()}" for k in kinds} | {"lw.edit_kind_other", "lw.edit_label", "lw.edit_badge_ai", "lw.edit_badge_staged", "lw.edit_badge_unknown"}
        self.assertEqual(set(ko), needed)
        self.assertEqual(set(en), needed)

    def test_precheck_texts_exist_in_both_languages_with_the_same_placeholders(self):
        ko, en = lw_table(KO, "listing_wizard.precheck.media_"), lw_table(EN, "listing_wizard.precheck.media_")
        for key, params in (("media_ai_generated", ["count", "ids"]), ("media_main_staged", ["id"]), ("media_edit_unknown", ["ids"])):
            full = f"listing_wizard.precheck.{key}"
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[full])), sorted(params), key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", en[full])), sorted(params), key)
            self.assertIn("차단하지 않습니다", ko[full])


if __name__ == "__main__":
    unittest.main()
