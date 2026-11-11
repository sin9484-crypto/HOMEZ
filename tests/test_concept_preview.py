"""
=========================================================
Homez OS

File : tests/test_concept_preview.py

2026-11-11 — 샘플 보고 고르기(사용자 요청 "이미지 첨부하면 이미지 샘플 나오고 어떤 형식으로 생성할 것인지 샘플 선택할수있게 만들어줘").
`concept_preview_service.py`·`GET /media-assets/concept-previews`와 위저드 화면(`console.js`의 `lwOpenSamplePanel` 등)을 고정한다.
임시 SQLite·임시 폴더 — 네트워크·AI 0.

  - 읽기 전용: 샘플을 만들어도 자산·처리 작업·파일이 늘지 않는다. 바깥 호출 0(AI 배경은 저장된 배경이 있을 때만 샘플).
  - 누끼가 아니면 그 사진에서 만든 누끼를 쓰고, 없으면 needs_cutout. 다른 회사 사진은 "없음", 사용 금지 사진·모르는 용도는 거절.
  - 샘플은 실제 누끼를 붙인 JPEG data URI 축소본. 대표(MAIN)는 흰 배경만, AI 배경 샘플 없음.
  - 화면: 사진을 올리면 패널이 자동으로 열리고, 고른 샘플로 기존 실행(컨셉 만들기·AI 배경)을 부른다. 서버 글은 이스케이프, 샘플 이미지는 data URI만.
=========================================================
"""

import base64
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

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset.ai_background_service import _background_path
from app.domains.media_asset.concept_preview_service import PREVIEW_SIDE
from app.domains.media_asset.concept_preview_service import ConceptPreviewService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import write_media_file
from app.domains.user.model import User  # noqa: F401
from tests.test_reference_generation_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
DATA_URI = re.compile(r"^data:image/jpeg;base64,[A-Za-z0-9+/=]+$")


def cutout_png(color=(20, 30, 70, 255), size=(500, 500)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((100, 100, size[0] - 100, size[1] - 100), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def opaque_jpeg(size=(400, 400)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def solid_png(rgb, size=(600, 600)):
    buffer = io.BytesIO()
    Image.new("RGB", size, rgb).save(buffer, format="PNG")
    return buffer.getvalue()


def decode(uri):
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as img:
        img.load()
        copy = img.copy()
        copy.info["source_format"] = img.format
        return copy


class ServiceTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, MediaAsset.__table__, ImageGenerationJob.__table__, ImageGenerationResult.__table__, ImageGenerationDailyUsage.__table__,
        ])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.media_root = Path(tempfile.mkdtemp())
        self.queue = ImageGenerationJobQueueService(self.db, media_root=self.media_root)
        self.service = ConceptPreviewService(self.db, queue=self.queue)
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

    def upload(self, data, company_id=None, rights="VERIFIED", filename="photo.png"):
        asset = self.queue.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=7, purpose="MAIN", display_order=0,
            image_bytes=data, original_filename=filename, company_id=company_id or self.company_id,
        )
        asset.rights_status = rights
        self.db.commit()
        return asset

    def counts(self):
        return tuple(self.db.query(func.count(model.id)).scalar() for model in (MediaAsset, ImageGenerationJob, ImageGenerationResult))

    def files(self):
        return sorted(str(p.relative_to(self.media_root)) for p in self.media_root.rglob("*") if p.is_file())

    # ---- 기본 ----

    def test_a_cutout_gives_one_sample_per_local_and_ai_concept_without_saving_anything(self):
        cutout = self.upload(cutout_png())
        before, files_before = self.counts(), self.files()
        out = self.service.previews(self.company_id, cutout.id, "ADDITIONAL")
        self.assertEqual(self.counts(), before)                 # 자산·작업·결과 0
        self.assertEqual(self.files(), files_before)           # 파일 0
        self.assertFalse(out["needs_cutout"])
        self.assertEqual(out["cutout_asset_id"], cutout.id)
        local = [s for s in out["samples"] if s["kind"] == "LOCAL"]
        ai = [s for s in out["samples"] if s["kind"] == "AI"]
        self.assertEqual([s["code"] for s in local], [c.code for c in ic.list_concepts("ADDITIONAL")])
        self.assertEqual([s["code"] for s in ai], list(abc.AI_CONCEPTS))
        for sample in local:
            self.assertRegex(sample["preview"], DATA_URI)
            img = decode(sample["preview"])
            self.assertEqual(img.size, (PREVIEW_SIDE, PREVIEW_SIDE))
            self.assertEqual(img.info["source_format"], "JPEG")
        for sample in ai:
            self.assertIsNone(sample["preview"])                # 저장된 배경이 없으면 샘플 없음(호출하지 않음)
            self.assertTrue(sample["needs_draw"])
            self.assertTrue(sample["prompt"])

    def test_the_sample_really_contains_the_product_on_the_concept_background(self):
        cutout = self.upload(cutout_png(color=(20, 30, 70, 255)))
        out = self.service.previews(self.company_id, cutout.id, "ADDITIONAL")
        white = next(s for s in out["samples"] if s["code"] == "CLEAN_WHITE")
        img = decode(white["preview"]).convert("RGB")
        centre = img.getpixel((PREVIEW_SIDE // 2, PREVIEW_SIDE // 2))
        corner = img.getpixel((3, 3))
        self.assertTrue(all(abs(a - b) <= 12 for a, b in zip(centre, (20, 30, 70))), centre)   # 상품 색
        self.assertTrue(all(v >= 240 for v in corner), corner)                                  # 흰 배경

    def test_samples_differ_between_concepts(self):
        cutout = self.upload(cutout_png())
        out = self.service.previews(self.company_id, cutout.id, "ADDITIONAL")
        previews = [s["preview"] for s in out["samples"] if s["kind"] == "LOCAL"]
        self.assertGreater(len(set(previews)), len(previews) // 2)

    def test_main_offers_only_the_white_background_and_no_ai(self):
        cutout = self.upload(cutout_png())
        out = self.service.previews(self.company_id, cutout.id, "MAIN")
        self.assertEqual([(s["kind"], s["code"]) for s in out["samples"]], [("LOCAL", c.code) for c in ic.list_concepts("MAIN")])

    # ---- 누끼 찾기 ----

    def test_an_opaque_photo_without_a_cutout_asks_for_one(self):
        original = self.upload(opaque_jpeg(), filename="photo.jpg")
        out = self.service.previews(self.company_id, original.id, "ADDITIONAL")
        self.assertTrue(out["needs_cutout"])
        self.assertIsNone(out["cutout_asset_id"])
        self.assertEqual(out["samples"], [])
        self.assertEqual(out["source_asset_id"], original.id)

    def test_an_opaque_photo_uses_the_latest_cutout_made_from_it(self):
        original = self.upload(opaque_jpeg(), filename="photo.jpg")
        older = self.upload(cutout_png(color=(200, 0, 0, 255)))
        newer = self.upload(cutout_png(color=(0, 200, 0, 255)))
        for asset in (older, newer):
            asset.asset_role, asset.source_asset_id = "GENERATED", original.id
        self.db.commit()
        out = self.service.previews(self.company_id, original.id, "ADDITIONAL")
        self.assertFalse(out["needs_cutout"])
        self.assertEqual(out["cutout_asset_id"], newer.id)

    def test_derived_photos_that_are_not_cutouts_or_are_denied_are_not_used(self):
        original = self.upload(opaque_jpeg(), filename="photo.jpg")
        flat = self.upload(opaque_jpeg(size=(300, 300)), filename="flat.jpg")
        denied = self.upload(cutout_png(), rights="RIGHTS_DENIED")
        for asset in (flat, denied):
            asset.asset_role, asset.source_asset_id = "GENERATED", original.id
        self.db.commit()
        self.assertTrue(self.service.previews(self.company_id, original.id, "ADDITIONAL")["needs_cutout"])

    def test_a_derived_cutout_of_another_company_is_not_used(self):
        original = self.upload(opaque_jpeg(), filename="photo.jpg")
        foreign = self.upload(cutout_png(), company_id=self.other_company_id)
        foreign.asset_role, foreign.source_asset_id = "GENERATED", original.id
        self.db.commit()
        self.assertTrue(self.service.previews(self.company_id, original.id, "ADDITIONAL")["needs_cutout"])

    # ---- AI 배경 ----

    def test_a_saved_ai_background_becomes_a_sample_with_no_call(self):
        cutout = self.upload(cutout_png())
        code = "AI_KITCHEN_COUNTER"
        write_media_file(self.media_root, _background_path(self.company_id, code, 1), solid_png((10, 160, 90)))
        write_media_file(self.media_root, _background_path(self.company_id, code, 2), solid_png((150, 10, 160)))
        out = self.service.previews(self.company_id, cutout.id, "DETAIL")
        sample = next(s for s in out["samples"] if s["code"] == code)
        self.assertFalse(sample["needs_draw"])
        self.assertEqual(sample["background_version"], 2)      # 가장 최근 배경
        corner = decode(sample["preview"]).convert("RGB").getpixel((3, 3))
        self.assertTrue(all(abs(a - b) <= 12 for a, b in zip(corner, (150, 10, 160))), corner)
        other = next(s for s in out["samples"] if s["code"] == "AI_BATHROOM_SHELF")
        self.assertTrue(other["needs_draw"])

    def test_another_companys_ai_background_is_not_shown(self):
        cutout = self.upload(cutout_png())
        write_media_file(self.media_root, _background_path(self.other_company_id, "AI_KITCHEN_COUNTER", 1), solid_png((10, 160, 90)))
        out = self.service.previews(self.company_id, cutout.id, "ADDITIONAL")
        self.assertTrue(next(s for s in out["samples"] if s["code"] == "AI_KITCHEN_COUNTER")["needs_draw"])

    # ---- 거절 ----

    def test_other_company_unknown_purpose_and_denied_photos_are_refused(self):
        cutout = self.upload(cutout_png())
        with self.assertRaises(NotFoundException):
            self.service.previews(self.other_company_id, cutout.id, "ADDITIONAL")
        with self.assertRaises(NotFoundException):
            self.service.previews(self.company_id, 999999, "ADDITIONAL")
        with self.assertRaises(BadRequestException):
            self.service.previews(self.company_id, cutout.id, "BANNER")
        denied = self.upload(cutout_png(color=(1, 2, 3, 255)), rights="RIGHTS_DENIED")
        with self.assertRaises(BadRequestException):
            self.service.previews(self.company_id, denied.id, "ADDITIONAL")

    def test_one_broken_sample_does_not_stop_the_others(self):
        cutout = self.upload(cutout_png())
        from app.domains.media_asset import concept_preview_service as cps
        real = cps.compose_with_background
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        cps.compose_with_background = flaky
        try:
            out = self.service.previews(self.company_id, cutout.id, "ADDITIONAL")
        finally:
            cps.compose_with_background = real
        local = [s for s in out["samples"] if s["kind"] == "LOCAL"]
        self.assertIsNone(local[1]["preview"])
        self.assertTrue(all(DATA_URI.match(s["preview"]) for i, s in enumerate(local) if i != 1))


class RouterTests(unittest.TestCase):

    def test_the_route_is_a_read_only_get_for_admins(self):
        from app.domains.media_asset.router import router
        routes = [r for r in router.routes if getattr(r, "path", "") == "/media-assets/concept-previews"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {"GET"})
        names = [d.call.__name__ if hasattr(d.call, "__name__") else type(d.call).__name__ for d in routes[0].dependant.dependencies]
        source = (ROOT / "app/domains/media_asset/router.py").read_text(encoding="utf-8")
        block = source[source.index('@router.get("/concept-previews")'): source.index('@router.get("/concept-recommendations")')]
        self.assertIn("Depends(admin_guard)", block)
        self.assertTrue(names)


# ---------------- 화면 ----------------

def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.sample_[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


def all_lw(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const LW_CONCEPT_MAX_PICK = 6;
const LW_AIBG_MAX_PICK = 3;
let lwConceptNotice = null;
function lwConceptSummaryHtml(result) { return `[local:${result.created}]`; }
function lwAiBgSummaryHtml(result) { return `[ai:${result.created}]`; }
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

PANEL = function_source("lwOpenSamplePanel")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        # 샘플 이미지 허용 규칙은 실제 소스의 상수를 그대로 쓴다(시험이 따로 정의하면 소스가 느슨해져도 모른다).
        uri_const = re.search(r"^  const LW_SAMPLE_DATA_URI = [^\n]+$", JS, re.M).group(0)
        sources = "\n".join([uri_const] + [function_source(n) for n in ("lwSampleProblem", "lwSampleBodies", "lwSampleCardHtml", "lwConceptNoticeHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(all_lw(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def state(self, **kw):
        base = {"purpose": "ADDITIONAL", "data": {"cutout_asset_id": 42}, "local": [], "ai": [], "newBg": False, "aiSettings": {"configured": True}}
        base.update(kw)
        return json.dumps(base)

    def test_problems_are_caught_before_any_call(self):
        self.assertEqual(self.run_js(f"lwSampleProblem({self.state()})"), "sample_err_none")
        self.assertEqual(self.run_js(f"lwSampleProblem({self.state(local=list('abcdefg'))})"), "sample_err_max_local")
        self.assertEqual(self.run_js(f"lwSampleProblem({self.state(ai=list('abcd'))})"), "sample_err_max_ai")
        self.assertEqual(self.run_js(f"lwSampleProblem({self.state(ai=['X'], aiSettings={'configured': False})})"), "sample_err_ai_settings")
        self.assertEqual(self.run_js(f"lwSampleProblem({self.state(ai=['X'], aiSettings=None)})"), "sample_err_ai_settings")
        self.assertIsNone(self.run_js(f"lwSampleProblem({self.state(local=['WHITE'], aiSettings=None)})"))   # 로컬만이면 AI 설정 필요 없음
        self.assertIsNone(self.run_js(f"lwSampleProblem({self.state(local=['WHITE'], ai=['X'])})"))

    def test_bodies_use_the_cutout_and_split_local_and_ai(self):
        out = self.run_js(f"lwSampleBodies({self.state(local=['WHITE', 'WOOD_WARM'], ai=['AI_LIVING_TABLE'], newBg=True, purpose='DETAIL')})")
        self.assertEqual(out["local"], {"cutout_asset_id": 42, "concept_codes": ["WHITE", "WOOD_WARM"], "purpose": "DETAIL"})
        self.assertEqual(out["ai"], {"cutout_asset_id": 42, "concept_codes": ["AI_LIVING_TABLE"], "purpose": "DETAIL", "new_background": True})
        out = self.run_js(f"lwSampleBodies({self.state(local=['WHITE'])})")
        self.assertIsNone(out["ai"])
        out = self.run_js(f"lwSampleBodies({self.state(ai=['AI_LIVING_TABLE'], newBg='yes')})")
        self.assertIsNone(out["local"])
        self.assertIs(out["ai"]["new_background"], False)

    def test_cards_escape_server_text_and_show_only_jpeg_data_uris(self):
        good = "data:image/jpeg;base64,QUJD"
        html = self.run_js('lwSampleCardHtml({code: "W\\"<x>", kind: "LOCAL", label_ko: "<b>흰</b>", description: "\\"d", preview: "' + good + '"}, true)')
        self.assertIn(f'src="{good}"', html)
        self.assertIn("&lt;b&gt;흰&lt;/b&gt;", html)
        self.assertIn('data-sample-code="W&quot;&lt;x&gt;"', html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("is-picked", html)
        self.assertNotIn("<b>", html)
        for bad in ("javascript:alert(1)", 'data:image/jpeg;base64,AA" onerror="x', "data:image/svg+xml;base64,PHN2Zz4=", "http://x/y.jpg"):
            html = self.run_js("lwSampleCardHtml(" + json.dumps({"code": "W", "kind": "LOCAL", "label_ko": "흰", "preview": bad}) + ", false)")
            self.assertNotIn("<img", html, bad)
            self.assertIn("샘플을 만들지 못함", html)
            self.assertIn('aria-pressed="false"', html)
        html = self.run_js('lwSampleCardHtml({code: "AI_X", kind: "AI", label_ko: "주방", preview: null, needs_draw: true}, false)')
        self.assertIn("아직 그린 배경 없음", html)
        self.assertIn('data-sample-kind="AI"', html)
        html = self.run_js('lwSampleCardHtml({code: "Q", kind: "SOMETHING", label_ko: "q", preview: null}, false)')
        self.assertIn('data-sample-kind="LOCAL"', html)       # 모르는 종류는 로컬로(AI 실행에 섞이지 않게)

    def test_notice_shows_each_part_once_and_keeps_the_old_single_shape(self):
        out = self.run_js('(() => { lwConceptNotice = {parts: [{result: {created: 2}}, {error: "<i>실패</i>"}, {result: {created: 1}, ai: true}]}; const a = lwConceptNoticeHtml(); const b = lwConceptNoticeHtml(); return [a, b]; })()')
        self.assertIn("[local:2]", out[0])
        self.assertIn("[ai:1]", out[0])
        self.assertIn("&lt;i&gt;실패&lt;/i&gt;", out[0])
        self.assertNotIn("<i>", out[0])
        self.assertEqual(out[1], "")
        out = self.run_js('(() => { lwConceptNotice = {result: {created: 3}, labels: {}}; return lwConceptNoticeHtml(); })()')
        self.assertEqual(out, "[local:3]")
        out = self.run_js('(() => { lwConceptNotice = {result: {created: 4}, labels: {}, ai: true}; return lwConceptNoticeHtml(); })()')
        self.assertEqual(out, "[ai:4]")


class SourceTests(unittest.TestCase):

    def test_every_photo_card_has_the_sample_button_panel_and_binding(self):
        self.assertIn('<button type="button" class="btn btn-secondary btn-sm lw-sample-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.sample_btn")}</button>', JS)
        self.assertIn('<div class="lw-bg-panel" id="lw-sample-panel-${a.id}"></div>', JS)
        self.assertRegex(JS, r'querySelectorAll\("\.lw-sample-btn"\)\.forEach\(\(btn\) => \{\s*btn\.addEventListener\("click", \(\) => lwOpenSamplePanel\(btn\.dataset\.assetId, content\)\)')

    def test_uploading_opens_the_sample_panel_of_the_uploaded_photo_once(self):
        upload = JS[JS.index('el("lw-media-upload-input").addEventListener("change"'):]
        upload = upload[: upload.index("await lwRenderMediaStep(content);")]
        self.assertIn('const uploaded = await apiFetch("/media-assets/upload"', upload)
        self.assertIn("if (uploaded && Number.isInteger(uploaded.id)) lwSampleAutoOpenId = uploaded.id;", upload)
        auto = JS[JS.index("if (lwSampleAutoOpenId !== null) {"):]
        auto = auto[: auto.index("    }\n", auto.index("lwOpenSamplePanel(openId, content)")) + 6]
        self.assertLess(auto.index("lwSampleAutoOpenId = null;"), auto.index("lwOpenSamplePanel(openId, content)"))   # 한 번만
        self.assertIn("scrollIntoView", auto)

    def test_the_panel_calls_the_right_routes_in_the_right_order(self):
        self.assertIn("/media-assets/concept-previews?asset_id=${encodeURIComponent(Number(assetId))}&purpose=${encodeURIComponent(state.purpose)}", PANEL)
        self.assertIn('apiFetch("/ai-background/settings")', PANEL)
        run = PANEL[PANEL.index("lw-sample-run-btn-${assetId}`);"):]
        self.assertLess(run.index("lwSampleProblem(state)"), run.index('"/media-assets/concept-runs"'))
        self.assertLess(run.index('"/media-assets/concept-runs"'), run.index('"/ai-background/runs"'))
        self.assertLess(run.index("lwConceptNotice = { parts };"), run.index("await lwRenderMediaStep(content);"))
        self.assertIn("withButtonGuard", run)
        cut = PANEL[PANEL.index("lw-sample-cutout-btn-${assetId}`);"):PANEL.index("lw-sample-run-btn-${assetId}`);")]
        self.assertIn('"/media-assets/background-removal"', cut)
        self.assertIn('provider_code: "REMBG"', cut)
        self.assertIn("withButtonGuard", cut)                 # 누끼 따기는 사용자가 누를 때만
        self.assertNotIn("background-removal", PANEL[: PANEL.index("function wire()")])

    def test_picking_uses_the_existing_limits_and_changing_purpose_clears_picks(self):
        self.assertIn("const limit = isAi ? LW_AIBG_MAX_PICK : LW_CONCEPT_MAX_PICK;", PANEL)
        self.assertIn("lwRefToggle(list, btn.dataset.sampleCode, limit)", PANEL)
        self.assertRegex(PANEL, r"state\.purpose = radio\.value; state\.local = \[\]; state\.ai = \[\]; load\(\);")
        self.assertIn("state.local = state.local.filter((code) => known.has(`LOCAL:${code}`));", PANEL)

    def test_every_sample_key_exists_in_both_languages_and_none_is_unused(self):
        ko, en = table(KO), table(EN)
        used = set(re.findall(r'"(sample_[a-z_0-9]+)"', JS)) | set(re.findall(r'"lw\.(sample_[a-z_0-9]+)"', JS))
        self.assertTrue(used)
        for key in sorted(used):
            self.assertIn(f"lw.{key}", ko, key)
            self.assertIn(f"lw.{key}", en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[f"lw.{key}"])), sorted(re.findall(r"\{(\w+)\}", en[f"lw.{key}"])), key)
        self.assertEqual(set(ko), set(en))
        self.assertEqual({k[3:] for k in ko} - used, set(), "쓰지 않는 샘플 문구가 있다")

    def test_the_notes_say_what_matters(self):
        ko = table(KO)
        self.assertIn("저장하지 않아요", ko["lw.sample_panel_hint"])
        self.assertIn("비용·외부 전송이 없어요", ko["lw.sample_local_note"])
        self.assertIn("허용 1회", ko["lw.sample_ai_note"])
        self.assertIn("자동으로 확인하지 못해요", ko["lw.sample_policy_note"])
        self.assertIn("내려받을 수 있어요", ko["lw.sample_needs_cutout"])
        self.assertIn("흰 배경", ko["lw.sample_purpose_main"])
        for key in ("sample_policy_note", "sample_ai_note", "sample_local_note", "sample_panel_hint"):
            self.assertIn(f't("{key}")', PANEL)

    def test_cards_have_a_visible_picked_and_focus_state(self):
        for selector in (".lw-sample-grid", ".lw-sample-card.is-picked", ".lw-sample-card:focus-visible", ".lw-sample-thumb img"):
            self.assertIn(selector, CSS)


if __name__ == "__main__":
    unittest.main()
