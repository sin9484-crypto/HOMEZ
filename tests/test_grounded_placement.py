"""
=========================================================
Homez OS

File : tests/test_grounded_placement.py

2026-11-15 — 사용자 지시 "1번 2번 진행해라 샘플확인했는데 너무 부자연스럽다 다시 만들어라". 임시 SQLite·임시 폴더·가짜 전송 — 실제 네트워크·AI 호출 0.

  1번(합성):
    - 놓는 자리는 `composition.plan_placement()` 하나로 계산하고 합성과 검사가 함께 쓴다. CENTER는 예전 계산과 같다.
    - GROUNDED(AI 배경용): 누끼의 투명 여백을 잘라 낸 제품을 가운데에 세우고 밑면을 바닥선(GROUND_LINE)에 맞춘다. 확대하지 않는다. 크기 상한이 있다.
      제품 아래에 바닥 그림자를 먼저 그리고 제품을 붙인다 — 제품 픽셀은 그대로(보존 검사 1.0).
    - 거의 불투명한(알파 250 이상) 누끼 픽셀은 GROUNDED에서 완전 불투명으로 본다(색은 그대로) — 배경이 비치지 않고 보존 검사가 재는 픽셀이 늘어난다.
    - 검사가 실제로 붙인 자리를 잰다: GROUNDED 결과를 CENTER 자리로 재면 크게 어긋난다.
    - AI 배경 실행은 GROUNDED로 합성하고, 새 사진의 용도를 요청한 용도(추가·상세)로 저장한다(누끼가 대표여도). 로컬 컨셉은 누끼의 용도 그대로.
    - 샘플 고르기의 AI 샘플은 실제 실행과 같은 크기·같은 놓는 방식으로 만든 뒤 줄인다.
    - 장면 글은 아래쪽 1/3을 빈 바닥으로 그리게 하고, 장면마다 바닥(앞쪽 평면)을 말한다.
  2번(허용 창):
    - 목적마다 실제로 부르는 곳·종류 표(PURPOSE_TARGETS)가 모든 목적을 덮고, 코드가 실제로 허용을 묻는 조합과 운영 DB에 있는 조합은 모두 맞는 조합이다.
    - '직접 허용 만들기'(grant)는 맞지 않는 조합을 거절한다. 기능이 올린 요청의 승인은 이 표와 무관하다.
    - 화면의 표(grantTargets)는 서버 표와 같고, 보내는 곳 7개·목적 전부를 고를 수 있으며, 새 문구가 두 언어에 있다.
=========================================================
"""

import io
import json
import re
import subprocess
import unittest
from pathlib import Path

from PIL import Image

from app.core.exceptions import BadRequestException
from app.domains.external_call import constants as ec
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset import composition as comp
from app.domains.media_asset import concept_inspection as ci
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import Placement
from app.domains.media_asset.concept_preview_service import ConceptPreviewService
from tests import test_ai_background as tab

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")


def png_bytes(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def bottle(size=(400, 400), box=(150, 60, 250, 340), color=(30, 90, 200, 252)):
    """투명 여백이 큰 누끼(제품 상자 바깥은 투명, 제품 알파는 252 — 배경 지우기 결과처럼 거의 불투명)."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), box[:2])
    return image


def scene(color=(200, 190, 175)):
    return png_bytes(Image.new("RGB", (1024, 1024), color))


def _shrunk(data):
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    image.thumbnail((320, 320), Image.LANCZOS)
    return png_bytes(image)


def mean_difference(a, b):
    total = sum(abs(x - y) for p, q in zip(a.getdata(), b.getdata()) for x, y in zip(p, q))
    return total / (a.width * a.height * 3)


def old_center(cutout, canvas):
    scale = min(canvas[0] / cutout.width, canvas[1] / cutout.height, 1.0)
    size = (max(1, int(cutout.width * scale)), max(1, int(cutout.height * scale)))
    return size, ((canvas[0] - size[0]) // 2, (canvas[1] - size[1]) // 2)


class PlacementTests(unittest.TestCase):

    def test_center_is_the_old_rule(self):
        for size in ((400, 400), (2400, 800), (300, 1800), (1200, 1200)):
            cutout = Image.new("RGBA", size, (0, 0, 0, 0))
            plan = comp.plan_placement(cutout, comp.CANVAS_SIZE, Placement.CENTER)
            self.assertEqual((plan.size, plan.offset), old_center(cutout, comp.CANVAS_SIZE), size)
            self.assertEqual(plan.crop_box, (0, 0) + size)
        self.assertEqual(comp.plan_placement(Image.new("RGBA", (10, 10)), (100, 100)), comp.plan_placement(Image.new("RGBA", (10, 10)), (100, 100), Placement.CENTER))

    def test_grounded_trims_the_margin_centres_and_stands_on_the_ground_line(self):
        plan = comp.plan_placement(bottle(), comp.CANVAS_SIZE, Placement.GROUNDED)
        self.assertEqual(plan.crop_box, (150, 60, 250, 340))
        self.assertEqual(plan.size, (100, 280))                                   # 확대하지 않는다(원래 크기)
        self.assertEqual(plan.offset[0], (1200 - 100) // 2)
        self.assertEqual(plan.offset[1] + plan.size[1], int(1200 * comp.GROUND_LINE))

    def test_grounded_caps_the_size_and_never_enlarges(self):
        tall = Image.new("RGBA", (3000, 3000), (0, 0, 0, 0))
        tall.paste(Image.new("RGBA", (600, 2800), (9, 9, 9, 255)), (1200, 100))
        plan = comp.plan_placement(tall, comp.CANVAS_SIZE, Placement.GROUNDED)
        self.assertLessEqual(plan.size[1], int(1200 * comp.GROUNDED_MAX_HEIGHT))
        wide = Image.new("RGBA", (3000, 600), (0, 0, 0, 0))
        wide.paste(Image.new("RGBA", (3000, 300), (9, 9, 9, 255)), (0, 100))
        self.assertLessEqual(comp.plan_placement(wide, comp.CANVAS_SIZE, Placement.GROUNDED).size[0], int(1200 * comp.GROUNDED_MAX_WIDTH))
        small = bottle((80, 80), (30, 10, 50, 70))
        self.assertEqual(comp.plan_placement(small, comp.CANVAS_SIZE, Placement.GROUNDED).size, (20, 60))

    def test_unknown_placement_is_refused(self):
        with self.assertRaises(BadRequestException):
            comp.plan_placement(bottle(), comp.CANVAS_SIZE, "FLOATING")

    def test_prepare_cutout_only_raises_nearly_opaque_alpha_and_keeps_colours(self):
        source = Image.new("RGBA", (4, 1))
        source.putdata([(10, 20, 30, 249), (10, 20, 30, 250), (40, 50, 60, 254), (70, 80, 90, 0)])
        grounded = comp.prepare_cutout(source, Placement.GROUNDED)
        self.assertEqual(list(grounded.getdata()), [(10, 20, 30, 249), (10, 20, 30, 255), (40, 50, 60, 255), (70, 80, 90, 0)])
        self.assertEqual(list(comp.prepare_cutout(source, Placement.CENTER).getdata()), list(source.getdata()))


class ComposeTests(unittest.TestCase):

    def compose(self, placement):
        return comp.compose_with_background(png_bytes(bottle()), BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=scene()), placement=placement)

    def test_grounded_keeps_every_product_pixel_and_measures_many_of_them(self):
        cutout = png_bytes(bottle())
        composed = self.compose(Placement.GROUNDED)
        measured = ci.product_preservation(cutout, composed, placement=Placement.GROUNDED)
        self.assertEqual(measured, {"ratio": 1.0, "pixels": 100 * 280})          # 알파 252 제품이 모두 재는 대상이 된다
        image = Image.open(io.BytesIO(composed)).convert("RGB")
        self.assertEqual(image.getpixel((600, 1032 - 10)), (30, 90, 200))          # 제품 밑부분은 원래 색 그대로(배경이 비치지 않는다)

    def test_a_soft_shadow_lies_under_the_product_and_nowhere_else(self):
        image = Image.open(io.BytesIO(self.compose(Placement.GROUNDED))).convert("RGB")
        base_y = int(1200 * comp.GROUND_LINE)
        under = image.getpixel((600 + 20, base_y + 3))                            # 제품 밑면 바로 아래 바닥(바닥 그림자 안)
        far = image.getpixel((40, 1180))                                          # 먼 구석
        self.assertEqual(far, (200, 190, 175))
        self.assertLess(sum(under), sum(far) - 20)

    def test_center_composition_is_unchanged(self):
        cutout = bottle()
        background = Image.open(io.BytesIO(scene())).convert("RGBA").resize(comp.CANVAS_SIZE)
        size, offset = old_center(cutout, comp.CANVAS_SIZE)
        background.alpha_composite(cutout.resize(size, Image.LANCZOS), dest=offset)
        expected = png_bytes(background.convert("RGB"))
        self.assertEqual(self.compose(Placement.CENTER), expected)

    def test_the_check_measures_where_the_product_really_is(self):
        cutout = png_bytes(bottle(color=(30, 90, 200, 255)))
        composed = comp.compose_with_background(cutout, BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=scene()), placement=Placement.GROUNDED)
        self.assertEqual(ci.product_preservation(cutout, composed, placement=Placement.GROUNDED)["ratio"], 1.0)
        self.assertLess(ci.product_preservation(cutout, composed, placement=Placement.CENTER)["ratio"], 0.5)
        results = {r.code: r.status for r in ci.inspect(cutout, composed, ("PRODUCT_PRESERVATION",), "ADDITIONAL", placement=Placement.GROUNDED)}
        self.assertEqual(results["PRODUCT_PRESERVATION"], ci.PASS)


class AiRunTests(tab.Base_):

    def setUp(self):
        super().setUp()
        self.configure()
        self.grant()

    def upload_main(self):
        asset = self.upload(data=png_bytes(bottle()))
        asset.purpose = "MAIN"
        self.db.commit()
        return asset

    def test_ai_background_stands_the_product_on_the_floor_and_saves_the_requested_purpose(self):
        self.transport = tab.FakeTransport(tab.FakeResponse(200, tab.ok_body(tab.jpeg((220, 215, 205)))))
        cutout = self.upload_main()
        outcome = self.make_run(["AI_KITCHEN_COUNTER"], purpose="ADDITIONAL", cutout=cutout)[0]
        self.assertEqual(outcome.status, "CREATED")
        self.assertEqual(outcome.outcome.asset.purpose, "ADDITIONAL")             # 누끼는 MAIN이어도 AI 배경 사진은 대표로 저장하지 않는다
        checks = {c["code"]: c for c in outcome.outcome.checks}
        self.assertEqual(checks["PRODUCT_PRESERVATION"]["status"], "PASS")
        self.assertEqual(checks["PRODUCT_PRESERVATION"]["detail"]["pixels"], 100 * 280)
        saved = Image.open(self.media_root / outcome.outcome.asset.storage_path).convert("RGB")
        base_y = int(1200 * comp.GROUND_LINE)
        self.assertEqual(saved.getpixel((600, base_y - 10)), (30, 90, 200))       # 바닥선 바로 위에 제품
        self.assertNotEqual(saved.getpixel((600, 600 - 280)), (30, 90, 200))      # 예전 가운데 자리의 위쪽은 제품이 아니다

    def test_local_concepts_still_follow_the_cutout_purpose_and_the_centre(self):
        from app.domains.media_asset.concept_run_service import ConceptRunService

        # 로컬 컨셉(CENTER)의 보존 검사는 예전처럼 완전 불투명(255) 픽셀만 잰다 — 그래서 이 시험의 누끼는 알파 255다.
        cutout = self.upload(data=png_bytes(bottle(color=(30, 90, 200, 255))))
        cutout.purpose = "MAIN"
        self.db.commit()
        outcome = ConceptRunService(self.db, queue=self.queue).run(self.company_id, 7, cutout.id, ["SOFT_BEIGE"], "ADDITIONAL")[0]
        self.assertEqual(outcome.status, "CREATED")
        self.assertEqual(outcome.asset.purpose, "MAIN")

    def test_ai_sample_is_built_like_the_real_run_and_then_shrunk(self):
        self.transport = tab.FakeTransport(tab.FakeResponse(200, tab.ok_body(tab.jpeg((220, 215, 205)))))
        cutout = self.upload_main()
        self.make_run(["AI_KITCHEN_COUNTER"], purpose="ADDITIONAL", cutout=cutout)
        result = ConceptPreviewService(self.db, queue=self.queue).previews(self.company_id, cutout.id, "ADDITIONAL")
        sample = next(s for s in result["samples"] if s["code"] == "AI_KITCHEN_COUNTER")
        data = __import__("base64").b64decode(sample["preview"].split(",", 1)[1])
        small = Image.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(small.size, (320, 320))
        background = (self.media_root / f"ai_backgrounds/{self.company_id}/AI_KITCHEN_COUNTER/v1.png").read_bytes()
        expected = Image.open(io.BytesIO(comp.compose_with_background(png_bytes(bottle()), BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=background),
                                                                       placement=Placement.GROUNDED))).convert("RGB")
        expected.thumbnail((320, 320), Image.LANCZOS)
        old_way = Image.open(io.BytesIO(comp.compose_with_background(_shrunk(png_bytes(bottle())), BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=background),
                                                                      (320, 320)))).convert("RGB")
        # 샘플은 JPEG로 보내므로 한두 단계의 압축 차이는 있다 — 실제 실행과 같은 방식의 축소본에는 아주 가깝고, 예전 방식(작은 누끼를 작은 캔버스에 꽉 채움)과는 다르다.
        self.assertLess(mean_difference(small, expected), 2.0)
        self.assertGreater(mean_difference(small, old_way), 3 * mean_difference(small, expected) + 1.0)


class PromptTests(unittest.TestCase):

    def test_every_scene_asks_for_an_empty_floor_in_the_bottom_third(self):
        self.assertIn("fills the entire bottom third of the image", abc.PROMPT_RULES)
        self.assertIn("completely clear so a product can be placed there later", abc.PROMPT_RULES)
        for code, concept in abc.AI_CONCEPTS.items():
            self.assertIn("in the foreground", concept.generation_prompt, code)
            self.assertTrue(concept.generation_prompt.endswith(abc.PROMPT_RULES), code)
            for word in ("No products", "no people", "No text"):
                self.assertIn(word, concept.generation_prompt, code)
        self.assertIn("bench seat", abc.AI_CONCEPTS["AI_OUTDOOR_TRACK"].generation_prompt)
        self.assertNotIn("plinth", abc.AI_CONCEPTS["AI_STUDIO_MARBLE"].generation_prompt)


class GrantTargetTests(unittest.TestCase):

    CODE_COMBOS = (   # 2026-11-15 운영 DB의 허용 조합 + 코드가 authorize하는 조합
        ("CLOUDFLARE", "WRITE", "AI_BACKGROUND"), ("COUPANG", "READ", "AFTER_SALES_CHECK"), ("COUPANG", "READ", "INVOICE_UPLOAD"), ("COUPANG", "WRITE", "INVOICE_UPLOAD"),
        ("COUPANG", "READ", "ORDER_DETECTION"), ("COUPANG", "READ", "ORDER_TRACKING"), ("COUPANG", "READ", "PRODUCT_OUTBOUND_DAYS"), ("COUPANG", "WRITE", "PRODUCT_OUTBOUND_DAYS"),
        ("COUPANG", "READ", "PRODUCT_STATUS_CHECK"), ("COUPANG", "READ", "RETURN_PROCESS"), ("COUPANG", "READ", "STOCK_UPDATE"), ("COUPANG", "WRITE", "STOCK_UPDATE"),
        ("COUPANG", "WRITE", "PRICE_UPDATE"), ("COUPANG", "WRITE", "PRODUCT_NAME_UPDATE"), ("COUPANG", "WRITE", "INQUIRY_REPLY"), ("COUPANG", "WRITE", "RETURN_PROCESS"),
        ("NAVER", "READ", "TREND_LOOKUP"), ("ONCH", "READ", "ORDER_DETECTION"), ("ONCH", "READ", "SUPPLIER_RELEASE_DATE"),
        ("LLM", "WRITE", "AI_DRAFT"), ("LLM", "WRITE", "AI_ANALYSIS"), ("LLM", "WRITE", "AI_ADVICE"), ("GEMINI", "WRITE", "IMAGE_GENERATION"), ("EMAIL", "WRITE", "BRIEF_EMAIL"),
        ("GEMINI", "WRITE", "GEMINI_QUESTION"),                                    # 2026-11-23 클로드 연결 장치의 제미나이 글 질문
    )

    def test_the_table_covers_every_purpose_and_every_real_combination(self):
        self.assertEqual(set(ec.PURPOSE_TARGETS) | {CallPurpose.ANY}, set(CallPurpose.ALL))
        for combo in self.CODE_COMBOS:
            self.assertTrue(ec.valid_target(*combo), combo)
        for system in ExternalSystem.ALL:
            self.assertTrue(ec.valid_target(system, "READ", "ANY"))

    def test_mismatched_combinations_are_not_valid(self):
        for combo in (("COUPANG", "WRITE", "AI_BACKGROUND"), ("CLOUDFLARE", "READ", "AI_BACKGROUND"), ("ONCH", "READ", "TREND_LOOKUP"),
                      ("COUPANG", "WRITE", "ORDER_DETECTION"), ("LLM", "READ", "AI_DRAFT"), ("LLM", "WRITE", "GEMINI_QUESTION"), ("GEMINI", "READ", "GEMINI_QUESTION"), ("NOWHERE", "READ", "ANY"), ("COUPANG", "READ", "UNKNOWN")):
            self.assertFalse(ec.valid_target(*combo), combo)

    def test_the_screen_table_matches_the_server_table(self):
        source = re.search(r"  function grantTargets\(\) \{.*?\n  \}\n", JS.replace("\r\n", "\n"), re.S).group(0)
        out = subprocess.run(["node", "-e", source + "process.stdout.write(JSON.stringify(grantTargets()));"], capture_output=True, text=True, encoding="utf-8", check=True).stdout
        screen = {k: (tuple(v[0]), tuple(v[1])) for k, v in json.loads(out).items()}
        self.assertEqual(screen, {k: (tuple(v[0]), tuple(v[1])) for k, v in ec.PURPOSE_TARGETS.items()})

    def test_the_dialog_offers_every_destination_and_purpose_with_texts_in_both_languages(self):
        systems = re.findall(r'\["([A-Z]+)", "easy\.sys\.[a-z]+"\]', re.search(r"const GRANT_SYSTEMS = \[(.*?)\];", JS).group(1))
        self.assertEqual(set(systems), set(ExternalSystem.ALL))
        purposes = re.findall(r'\["([A-Z_]+)", "(easy\.purpose\.[a-z_]+)"\]', re.search(r"const GRANT_PURPOSES = \[(.*?)\];", JS, re.S).group(1))
        self.assertEqual({p for p, _ in purposes}, set(CallPurpose.ALL))
        keys = {k for _, k in purposes} | {"easy.ap.d_bad_target"}
        for key in keys:
            self.assertEqual(TEXTS.count(f'"{key}":'), 2, key)
        dialog = re.search(r"async function grantDialog\(preset\) \{.*?\n  \}\r?\n", JS, re.S).group(0)
        self.assertIn("GRANT_SYSTEMS.map", dialog)
        self.assertIn("grantTargetOk(", dialog)
        self.assertLess(dialog.index("grantTargetOk("), dialog.index("promptRecentAuthToken"))   # 비밀번호를 묻기 전에 막는다


class GrantServiceTests(tab.Base_):

    def test_direct_grant_refuses_a_mismatch_and_creates_nothing(self):
        policy = ExternalCallPolicyService(self.db)
        with self.assertRaises(BadRequestException):
            policy.grant(self.company_id, system=ExternalSystem.COUPANG, kind=CallKind.WRITE, purpose=CallPurpose.AI_BACKGROUND, mode=AllowanceMode.COUNTED, count=6, user_id=7)
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)
        made = policy.grant(self.company_id, system=ExternalSystem.CLOUDFLARE, kind=CallKind.WRITE, purpose=CallPurpose.AI_BACKGROUND, mode=AllowanceMode.COUNTED, count=6, user_id=7)
        self.assertEqual((made.status, made.remaining_count), ("ACTIVE", 6))
        anything = policy.grant(self.company_id, system=ExternalSystem.NAVER, kind=CallKind.READ, purpose=CallPurpose.ANY, mode=AllowanceMode.COUNTED, count=1, user_id=7)
        self.assertEqual(anything.status, "ACTIVE")

    def test_approving_a_feature_request_is_not_affected(self):
        policy = ExternalCallPolicyService(self.db)
        request = policy.ensure_pending_request(self.company_id, ExternalSystem.CLOUDFLARE, CallKind.WRITE, CallPurpose.AI_BACKGROUND, requested_by=7)
        self.assertEqual(request.requested_count, 6)
        approved = policy.approve(self.company_id, request.id, user_id=7)
        self.assertEqual((approved.status, approved.remaining_count), ("ACTIVE", 6))


if __name__ == "__main__":
    unittest.main()
