"""
2026-11-26 — 판매성 있는 사진 만들기(사용자 "이전 사진과 제품 정보를 확인하고 사진을 만든것인가?? … 지미나이와 협의하여 다시 만들어라 컨셉별로 선택할수있는 기능 포함해서").

  - 묶음 사진에서 파는 상품 하나만 떼어 내기(`item_isolation`): 붙어 있는 옆 병 조각은 빠지고, 흰 틈으로 떨어진 뚜껑·분사 머리는 남고,
    범위를 넓게 잡아도 축이 끌려가지 않는다. 투명 용기 안 흰 반사는 구멍으로 남지 않는다. 흰 배경이 아니거나 범위가 이상하면 거절.
  - 서비스·기록: 원본은 그대로, 새 누끼(GENERATED)가 원본을 가리키고, 편집 기록은 '누끼(CUTOUT)'로 읽힌다.
  - 상품 맞춤 장면: 물 없이 쓰는 상품이면 쓰이는 상황 장면 다섯 개가 앞에, 욕실은 이유와 함께 '맞지 않음'.
  - 화면: 범위 검사·요청 본문(노드로 실제 함수 실행), 문구 두 언어.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest

from PIL import Image
from PIL import ImageDraw

from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset import image_edit_record as ier
from app.domains.media_asset import item_isolation as iso
from app.domains.media_asset.concept_preview_service import ConceptPreviewService
from app.domains.media_asset.concept_recommendation import recommend
from app.core.exceptions import BadRequestException
from tests.test_image_edit_record import Base_
from tests.test_image_edit_record import EN
from tests.test_image_edit_record import JS
from tests.test_image_edit_record import KO
from tests.test_image_edit_record import NODE
from tests.test_reference_generation_ui import function_source

W, H = 400, 320
OUTLINE = (120, 120, 120)


def group_photo(*, highlight=True, background=(255, 255, 255)) -> bytes:
    """흰 배경 묶음 사진: 왼쪽 병(흰 틈으로 떨어짐) · 가운데 병(파는 상품, 목은 좁고 흰 뚜껑) · 오른쪽 큰 병(가운데 병 몸통에 붙어 있음)."""

    image = Image.new("RGB", (W, H), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 100, 140, 290), fill=(238, 238, 238), outline=OUTLINE, width=2)        # 왼쪽 병(141~149 흰 틈)
    draw.rectangle((251, 80, 340, 290), fill=(230, 236, 240), outline=OUTLINE, width=2)       # 오른쪽 병 몸통(가운데 병에 붙음)
    draw.rectangle((275, 40, 315, 80), fill=(248, 248, 248), outline=OUTLINE, width=2)        # 오른쪽 병 뚜껑
    draw.rectangle((150, 120, 250, 290), fill=(225, 232, 236), outline=OUTLINE, width=2)      # 가운데 병 몸통
    draw.rectangle((185, 60, 215, 120), fill=(250, 250, 250), outline=OUTLINE, width=2)       # 가운데 병 목·흰 뚜껑
    if highlight:
        draw.rectangle((170, 150, 175, 260), fill=(255, 255, 255))                             # 투명 용기 속 흰 반사
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def alpha_at(png_bytes: bytes, x: int, y: int) -> int:
    return Image.open(io.BytesIO(png_bytes)).getchannel("A").getpixel((x, y))


class IsolateItemTests(unittest.TestCase):

    def test_only_the_middle_bottle_is_kept(self):
        result = iso.isolate_item(group_photo(), 145 / W, 262 / W)
        self.assertEqual(result.axis_x, 200.0)
        for x, y in ((200, 200), (155, 200), (245, 200), (200, 70), (190, 115)):
            self.assertGreaterEqual(alpha_at(result.png_bytes, x, y), 128, (x, y))       # 몸통·목·뚜껑은 남는다
        for x, y in ((253, 200), (258, 200), (258, 100), (290, 60), (120, 200)):
            self.assertLess(alpha_at(result.png_bytes, x, y), 128, (x, y))               # 옆 병(붙은 몸통·뚜껑)·떨어진 왼쪽 병은 빠진다
        self.assertLessEqual(result.box[2], 252)
        self.assertGreaterEqual(result.box[0], 148)

    def test_a_wide_range_does_not_drag_the_axis_toward_the_neighbour(self):
        result = iso.isolate_item(group_photo(), 130 / W, 290 / W)
        self.assertEqual(result.axis_x, 200.0)
        self.assertLess(alpha_at(result.png_bytes, 253, 200), 128)
        self.assertLess(alpha_at(result.png_bytes, 270, 200), 128)

    def test_white_reflections_inside_a_clear_bottle_are_not_holes(self):
        result = iso.isolate_item(group_photo(), 145 / W, 262 / W)
        self.assertGreaterEqual(alpha_at(result.png_bytes, 172, 200), 128)

    def test_colours_are_the_original_pixels(self):
        photo = group_photo()
        result = Image.open(io.BytesIO(iso.isolate_item(photo, 145 / W, 262 / W).png_bytes)).convert("RGBA")
        original = Image.open(io.BytesIO(photo)).convert("RGB")
        self.assertEqual(result.getpixel((200, 200))[:3], original.getpixel((200, 200)))
        self.assertEqual(result.getpixel((200, 70))[:3], original.getpixel((200, 70)))

    def test_bad_input_is_refused_with_a_reason(self):
        with self.assertRaises(iso.ItemIsolationError):
            iso.isolate_item(group_photo(background=(200, 210, 190)), 0.3, 0.7)        # 흰 배경이 아님
        for x_from, x_to in ((0.5, 0.5), (0.6, 0.4), (0.50, 0.53), (-0.1, 0.5), (0.2, 1.2), ("a", 0.5)):
            with self.assertRaises(iso.ItemIsolationError, msg=(x_from, x_to)):
                iso.isolate_item(group_photo(), x_from, x_to)
        with self.assertRaises(iso.ItemIsolationError):
            iso.isolate_item(group_photo(), 0.88, 0.99)                                  # 범위 안에 상품이 없다


class ServiceTests(Base_):

    def test_the_service_keeps_the_original_and_records_a_cutout_step(self):
        original = self.upload(group_photo())
        before = original.storage_path
        asset = self.queue.isolate_item(original.id, 145 / W, 262 / W, 1, self.company_id)
        self.assertEqual(asset.asset_role, "GENERATED")
        self.assertEqual(asset.source_asset_id, original.id)
        self.assertEqual(asset.rights_status, "VERIFIED")
        self.assertEqual(original.storage_path, before)
        self.assertEqual(self.kinds(asset), [ier.ORIGINAL, ier.CUTOUT])
        self.assertEqual(ier.classify_job(type("J", (), {"model_name": None, "provider_code": "LOCAL_ITEM_ISOLATION",
                                                          "request_payload_json": json.dumps({"operation": "ITEM_ISOLATION"})})())[0], ier.CUTOUT)

    def test_a_failure_leaves_no_new_photo(self):
        original = self.upload(group_photo(background=(200, 210, 190)))
        with self.assertRaises(BadRequestException) as caught:
            self.queue.isolate_item(original.id, 0.3, 0.7, 1, self.company_id)
        self.assertIn("원본은 그대로", str(caught.exception.detail))
        self.assertEqual(self.kinds(original), [ier.ORIGINAL])


class PreviewHintTests(Base_):
    """사진 주인 상품(후보)의 분류·상품명으로 견본을 추천 순서로 놓는다 — 실제 미리보기 경로(DB 읽기)."""

    def setUp(self):
        super().setUp()
        from app.domains.product_candidate.model import ProductCandidate
        ProductCandidate.__table__.create(bind=self.engine)
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO product_candidates (id, candidate_key, source_type, source_reference, market, product_name, category_hint, status, visibility, "
                "owner_company_id, created_at, updated_at, discovered_at) VALUES (7, 'k7', 'PRIVATE_MANUAL', 'r', 'COUPANG', '레이펄스 워터리스 바디워시 200ml (본품)', "
                f"'바디워시/샴푸', 'ANALYZED', 'PRIVATE', {self.company_id}, '2026-11-26', '2026-11-26', '2026-11-26')")

    def test_the_photo_owner_decides_the_order(self):
        cutout = self.upload(group_photo())
        self.queue.isolate_item(cutout.id, 145 / W, 262 / W, 1, self.company_id)
        data = ConceptPreviewService(self.db, queue=self.queue).previews(self.company_id, cutout.id, "ADDITIONAL")
        self.assertEqual(data["product_hint"], "바디워시/샴푸 레이펄스 워터리스 바디워시 200ml (본품)")
        ai = [s for s in data["samples"] if s["kind"] == "AI"]
        self.assertEqual([s["code"] for s in ai[:5]], list(ProductSceneTests.USE_SCENES))
        self.assertEqual(ai[-1]["code"], "AI_BATHROOM_SHELF")
        self.assertTrue(ai[-1]["avoid"])
        self.assertNotEqual(data["cutout_asset_id"], cutout.id)              # 방금 떼어 낸 한 상품 누끼로 견본을 만든다

    def test_another_companys_product_gives_no_hint(self):
        other = self.upload(group_photo(), company_id=self.other_company_id)
        self.queue.isolate_item(other.id, 145 / W, 262 / W, 1, self.other_company_id)
        data = ConceptPreviewService(self.db, queue=self.queue).previews(self.other_company_id, other.id, "ADDITIONAL")
        self.assertEqual(data["product_hint"], "")


class MainFillTests(Base_):
    """대표(흰 배경)는 투명 여백을 잘라 제품을 정가운데에 크게 — 떼어 낸 한 병이 원래 자리에 작게 남지 않는다."""

    @staticmethod
    def off_centre_cutout(size=(600, 600), box=(400, 100, 500, 500)) -> Image.Image:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (30, 90, 160, 255)), box[:2])
        return image

    def test_fill_trims_centres_and_caps_the_enlargement(self):
        from app.domains.media_asset import composition as comp
        plan = comp.plan_placement(self.off_centre_cutout(), comp.CANVAS_SIZE, comp.Placement.FILL)
        self.assertEqual(plan.crop_box, (400, 100, 500, 500))
        self.assertEqual(plan.size, (140, 560))                                 # 85%(1020)보다 1.4배 한도가 먼저
        self.assertEqual(plan.offset, ((1200 - 140) // 2, (1200 - 560) // 2))
        big = comp.plan_placement(self.off_centre_cutout((3000, 3000), (1000, 200, 1600, 2800)), comp.CANVAS_SIZE, comp.Placement.FILL)
        self.assertEqual(big.size[1], int(1200 * comp.MAIN_FILL))               # 큰 원본은 85%까지 줄여 채운다

    def test_a_main_concept_run_puts_the_product_in_the_middle(self):
        from app.domains.media_asset.concept_run_service import ConceptRunService
        buffer = io.BytesIO()
        self.off_centre_cutout().save(buffer, format="PNG")
        cutout = self.upload(buffer.getvalue())
        outcome = ConceptRunService(self.db, queue=self.queue).run(self.company_id, 1, cutout.id, ["CLEAN_WHITE"], "MAIN")[0]
        self.assertEqual(outcome.status, "CREATED")
        self.assertTrue(all(c["status"] == "PASS" for c in outcome.checks if c["code"] in ("PRODUCT_PRESERVATION", "MAIN_IMAGE_SPEC")))
        image = Image.open(self.media_root / outcome.asset.storage_path).convert("RGB")
        self.assertEqual(image.getpixel((600, 600)), (30, 90, 160))             # 가운데에 제품
        self.assertEqual(image.getpixel((900, 600)), (255, 255, 255))           # 원래 자리(오른쪽)는 흰 배경


class ProductSceneTests(unittest.TestCase):

    HINT = "바디워시/샴푸 레이펄스 워터리스 바디워시 200ml (본품)"
    USE_SCENES = ("AI_CARE_BEDSIDE", "AI_CAMPING_TABLE", "AI_TRAVEL_DESK", "AI_GYM_BENCH", "AI_STUDIO_TOWEL")

    def test_use_scenes_put_a_towel_beside_an_empty_middle(self):
        for code in self.USE_SCENES:
            concept = abc.get_ai_concept(code)
            self.assertLessEqual(len(concept.generation_prompt), 1000, code)
            scene = concept.generation_prompt.split(" Empty background")[0]
            self.assertTrue("towel" in scene or "washcloth" in scene, code)
            self.assertIn("on its left side", concept.generation_prompt, code)
            self.assertIn("the middle of this empty surface is completely clear", concept.generation_prompt, code)
            for term in abc.WATERLESS_TERMS:
                self.assertIn(term, concept.good_for, code)

    def test_a_waterless_product_gets_use_scenes_first_and_no_bathroom(self):
        result = recommend(self.HINT, "ADDITIONAL", abc.list_ai_concepts())
        self.assertEqual([r["code"] for r in result["recommended"][:5]], list(self.USE_SCENES))
        self.assertEqual([r["code"] for r in result["excluded"]], ["AI_BATHROOM_SHELF"])
        self.assertIn("워터리스", result["excluded"][0]["reasons"][0])
        plain = recommend("주방용품 도마", "ADDITIONAL", abc.list_ai_concepts())
        self.assertEqual(plain["excluded"], [])

    def test_samples_carry_the_suggestion_and_are_ordered(self):
        samples = [{"code": c.code} for c in abc.list_ai_concepts()]
        ranked = ConceptPreviewService._rank(samples, abc.list_ai_concepts(), self.HINT, "ADDITIONAL")   # noqa: SLF001
        self.assertEqual([s["code"] for s in ranked[:5]], list(self.USE_SCENES))
        self.assertTrue(all(s["recommended"] for s in ranked[:5]))
        self.assertEqual(ranked[-1]["code"], "AI_BATHROOM_SHELF")
        self.assertTrue(ranked[-1]["avoid"])
        self.assertTrue(ranked[-1]["reasons"])
        neutral = ConceptPreviewService._rank([{"code": c.code} for c in abc.list_ai_concepts()], abc.list_ai_concepts(), "", "ADDITIONAL")  # noqa: SLF001
        self.assertEqual([s["code"] for s in neutral], [c.code for c in abc.list_ai_concepts()])
        self.assertFalse(any(s["recommended"] or s["avoid"] for s in neutral))


class ScreenTests(unittest.TestCase):

    KEYS = ("sample_badge_good", "sample_badge_avoid", "sample_hint_note", "iso_title", "iso_note", "iso_loading", "iso_from", "iso_to", "iso_btn", "iso_err_range")

    def test_messages_exist_in_both_languages(self):
        for key in self.KEYS:
            self.assertIn(f'"lw.{key}"', KO, key)
            self.assertIn(f'"lw.{key}"', EN, key)
        self.assertIn('"/media-assets/item-isolation"', JS)

    @unittest.skipUnless(NODE, "node 없음")
    def test_range_check_and_request_body(self):
        script = function_source("lwIsoProblem") + function_source("lwIsoBody") + """
const out = [lwIsoProblem({from: 40, to: 60}), lwIsoProblem({from: 40, to: 44}), lwIsoProblem({from: 60, to: 40}), lwIsoProblem({from: -1, to: 50}),
             lwIsoProblem({from: 10, to: 101}), lwIsoProblem({from: "x", to: 50}), lwIsoBody("7", {from: 50, to: 71.5})];
console.log(JSON.stringify(out));"""
        out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out[:6], [None, "iso_err_range", "iso_err_range", "iso_err_range", "iso_err_range", "iso_err_range"])
        self.assertEqual(out[6], {"source_asset_id": 7, "x_from": 0.5, "x_to": 0.715})


if __name__ == "__main__":
    unittest.main()
