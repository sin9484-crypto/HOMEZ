"""
=========================================================
Homez OS

File : tests/test_media_file_missing.py

2026-11-18 — 파일이 사라진 사진 표시(사용자 결정 '다'): 기록은 있는데 저장소에 파일이 없는 사진을
소유자별 사진 목록(`GET /media-assets/owners/{type}/{id}/assets`)에서 `file_missing=True`로 알려 주고,
위저드 사진 카드·상단 안내·등록 패키지 갤러리에 '파일 없음'을 보여 준다. 표시만 한다 — 기록·파일을 고치거나 지우지 않는다.
임시 SQLite·임시 폴더 — 네트워크 0.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import func

from app.domains.media_asset import router as media_router
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.schema import MediaAssetResponse
from tests.test_image_edit_record import Base_
from tests.test_image_edit_record import png
from tests.test_reference_generation_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
I18N = (ROOT / "app/web/i18n/i18n.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
KEYS = ("lw.media_file_missing", "lw.media_missing_notice")


class ServiceTests(Base_):

    def test_only_assets_without_a_file_are_reported(self):
        kept = self.upload(png((10, 20, 30)))
        lost = self.upload(png((200, 20, 30)))
        (self.media_root / lost.storage_path).unlink()
        self.assertEqual(self.queue.missing_file_ids([kept, lost]), {lost.id})
        self.assertEqual(self.queue.missing_file_ids([]), set())

    def test_bad_paths_count_as_missing_and_nothing_is_written(self):
        kept = self.upload(png())
        escaped = self.upload(png((1, 2, 3)))
        escaped.storage_path = "../outside.png"
        absolute = self.upload(png((4, 5, 6)))
        absolute.storage_path = "/abs.png"
        before = {p: p.read_bytes() for p in self.media_root.rglob("*") if p.is_file()}
        self.assertEqual(self.queue.missing_file_ids([kept, escaped, absolute]), {escaped.id, absolute.id})
        after = {p: p.read_bytes() for p in self.media_root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)                               # 파일을 만들거나 지우지 않는다
        self.assertFalse((self.media_root.parent / "outside.png").exists())


class RouteTests(Base_):

    def call(self, owner_id=7, company_id=None):
        user = SimpleNamespace(company_id=company_id or self.company_id, id=1)
        with mock.patch.object(media_router, "ImageGenerationJobQueueService", lambda db: self.queue):
            return media_router.list_assets_for_owner("PRODUCT_CANDIDATE", owner_id, current_user=user, db=self.db)

    def test_list_flags_missing_files_and_keeps_every_other_field(self):
        kept = self.upload(png((10, 20, 30)))
        lost = self.upload(png((200, 20, 30)))
        (self.media_root / lost.storage_path).unlink()
        count_before = self.db.query(func.count(MediaAsset.id)).scalar()
        result = {r.id: r for r in self.call()}
        self.assertEqual(set(result), {kept.id, lost.id})
        self.assertFalse(result[kept.id].file_missing)
        self.assertTrue(result[lost.id].file_missing)
        for asset in (kept, lost):
            expected = MediaAssetResponse.model_validate(asset).model_dump()
            got = result[asset.id].model_dump()
            self.assertEqual({k: v for k, v in got.items() if k != "file_missing"}, {k: v for k, v in expected.items() if k != "file_missing"})
        self.db.expire_all()
        self.assertEqual(self.db.query(func.count(MediaAsset.id)).scalar(), count_before)
        self.assertEqual(self.db.get(MediaAsset, lost.id).status, "ACTIVE")   # 기록은 그대로

    def test_company_scope_is_unchanged(self):
        self.upload(png(), company_id=self.other_company_id)
        self.assertEqual(self.call(), [])

    def test_route_keeps_its_guard_and_response_model(self):
        routes = [r for r in media_router.router.routes if getattr(r, "path", "") == "/media-assets/owners/{owner_type}/{owner_id}/assets"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {"GET"})
        self.assertEqual(routes[0].response_model, list[MediaAssetResponse])
        source = (ROOT / "app/domains/media_asset/router.py").read_text(encoding="utf-8")
        block = source[source.index('"/owners/{owner_type}/{owner_id}/assets",'):]
        block = block[:block.index("@router.", 1)]
        self.assertIn("ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)", block)
        self.assertIn("missing = service.missing_file_ids(assets)", block)

    def test_other_responses_default_to_not_missing(self):
        asset = self.upload(png())
        (self.media_root / asset.storage_path).unlink()
        self.assertFalse(MediaAssetResponse.model_validate(asset).file_missing)


# ---------------- 화면 ----------------

def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.media_(?:file_missing|missing_notice))": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; for (const [k, v] of Object.entries(params || {})) s = s.split("{" + k + "}").join(String(v)); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class UiBehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        sources = function_source("lwMissingFileHtml") + "\n" + function_source("lwMissingNoticeHtml")
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))},
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_card_line_only_for_missing_files(self):
        html = self.run_js("lwMissingFileHtml({id: 5, file_missing: true})")
        self.assertEqual(html, '<p class="lw-media-missing">파일 없음 — 사진 파일이 사라져 열 수 없습니다. 다시 올려 주세요.</p>')
        for value in ("{id: 5, file_missing: false}", "{id: 5}", "{file_missing: 'true'}", "null", "undefined"):
            self.assertEqual(self.run_js(f"lwMissingFileHtml({value})"), "", value)

    def test_notice_counts_missing_files(self):
        html = self.run_js("lwMissingNoticeHtml([{file_missing: true}, {file_missing: false}, {file_missing: true}, {}, null])")
        self.assertTrue(html.startswith('<div class="banner banner-warn">'))
        self.assertIn("이 상품 사진 중 2장은", html)
        self.assertNotIn("{count}", html)
        for value in ("[]", "[{file_missing: false}]", "null", "'x'"):
            self.assertEqual(self.run_js(f"lwMissingNoticeHtml({value})"), "", value)


class UiSourceTests(unittest.TestCase):

    def test_wizard_step_card_thumbnail_and_gallery_use_the_flag(self):
        self.assertIn("${lwMissingNoticeHtml(assets)}", JS)
        self.assertIn('<div class="lw-media-card${a.file_missing === true ? " is-missing" : ""}" data-asset-id="${a.id}">', JS)
        card = JS[JS.index('<div class="lw-media-card${a.file_missing'):]
        self.assertIn("${lwMissingFileHtml(a)}", card[:card.index("</label>")])
        thumbs = JS[JS.index("assets.forEach((a) => {\n      if (a.file_missing === true) return;"):]
        self.assertLess(thumbs.index("if (a.file_missing === true) return;"), thumbs.index("lwLoadThumbUrl(a.id)"))
        self.assertIn('${asset.file_missing === true ? ` · ${escapeHtml(HomezI18n.t("lw.media_file_missing"))}` : ""}', JS)

    def test_texts_exist_in_both_languages_and_styles_exist(self):
        self.assertEqual(set(table(KO)), set(KEYS))
        self.assertEqual(set(table(EN)), set(KEYS))
        self.assertIn("{count}", table(KO)["lw.media_missing_notice"])
        self.assertIn("{count}", table(EN)["lw.media_missing_notice"])
        self.assertIn("function t(key, params)", I18N)
        self.assertIn(".lw-media-missing {", CSS)
        self.assertIn(".lw-media-card.is-missing .lw-media-thumb {", CSS)
        self.assertRegex(CSS, r"\.banner-warn\b")


if __name__ == "__main__":
    unittest.main()
