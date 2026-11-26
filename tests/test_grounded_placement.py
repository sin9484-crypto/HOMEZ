"""
=========================================================
Homez OS

File : tests/test_grounded_placement.py

2026-11-15 — 사용자 지시 "1번 2번 진행해라 샘플확인했는데 너무 부자연스럽다 다시 만들어라". 임시 SQLite·임시 폴더·가짜 전송 — 실제 네트워크·AI 호출 0.

  1번(합성):
    - 놓는 자리는 `composition.plan_placement()` 하나로 계산하고 합성과 검사가 함께 쓴다. CENTER는 예전 계산과 같다.
    - GROUNDED(AI 배경용): 누끼의 투명 여백을 잘라 낸 제품을 가운데에 세우고 밑면을 바닥선(GROUND_LINE)에 맞춘다. 최대 1.4배까지만 키운다(2026-11-26). 크기 상한이 있다.
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
from PIL import ImageDraw

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
        self.assertEqual(plan.size, (140, 392))                                   # 2026-11-26: 1.4배까지 키운다(크기 상한 744보다 작다)
        self.assertEqual(plan.offset[0], (1200 - 140) // 2)
        self.assertEqual(plan.offset[1] + plan.size[1], int(1200 * comp.GROUND_LINE))

    def test_grounded_caps_the_size_and_enlarges_at_most_1_4_times(self):
        tall = Image.new("RGBA", (3000, 3000), (0, 0, 0, 0))
        tall.paste(Image.new("RGBA", (600, 2800), (9, 9, 9, 255)), (1200, 100))
        plan = comp.plan_placement(tall, comp.CANVAS_SIZE, Placement.GROUNDED)
        self.assertLessEqual(plan.size[1], int(1200 * comp.GROUNDED_MAX_HEIGHT))
        wide = Image.new("RGBA", (3000, 600), (0, 0, 0, 0))
        wide.paste(Image.new("RGBA", (3000, 300), (9, 9, 9, 255)), (0, 100))
        self.assertLessEqual(comp.plan_placement(wide, comp.CANVAS_SIZE, Placement.GROUNDED).size[0], int(1200 * comp.GROUNDED_MAX_WIDTH))
        small = bottle((80, 80), (30, 10, 50, 70))
        self.assertEqual(comp.plan_placement(small, comp.CANVAS_SIZE, Placement.GROUNDED).size, (28, 84))   # 아무리 작아도 1.4배까지만
        self.assertEqual(comp.GROUNDED_MAX_UPSCALE, 1.4)

    def test_unknown_placement_is_refused(self):
        with self.assertRaises(BadRequestException):
            comp.plan_placement(bottle(), comp.CANVAS_SIZE, "FLOATING")

    def test_prepare_cutout_only_raises_nearly_opaque_alpha_and_keeps_colours(self):
        source = Image.new("RGBA", (4, 1))
        source.putdata([(10, 20, 30, 249), (10, 20, 30, 250), (40, 50, 60, 254), (70, 80, 90, 0)])
        grounded = comp.prepare_cutout(source, Placement.GROUNDED)
        self.assertEqual(list(grounded.getdata()), [(10, 20, 30, 249), (10, 20, 30, 255), (40, 50, 60, 255), (70, 80, 90, 0)])
        self.assertEqual(list(comp.prepare_cutout(source, Placement.CENTER).getdata()), list(source.getdata()))


def lit_colour(colour, cutout, image, x):
    """2026-11-26: 바닥 세우기 합성은 장면 빛 색·방향을 입힌다 — 같은 계산(composition.match_scene_light)을 한 제품 색."""

    plan = comp.plan_placement(comp.prepare_cutout(cutout, Placement.GROUNDED), image.size, Placement.GROUNDED)
    gains = comp.scene_light_gains(image, plan.offset[1])
    row = comp.match_scene_light(Image.new("RGBA", (plan.size[0], 1), tuple(colour) + (255,)), gains)
    return row.getpixel((x - plan.offset[0], 0))[:3]


class ComposeTests(unittest.TestCase):

    def compose(self, placement):
        return comp.compose_with_background(png_bytes(bottle()), BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=scene()), placement=placement)

    def test_grounded_keeps_every_product_pixel_and_measures_many_of_them(self):
        cutout = png_bytes(bottle())
        composed = self.compose(Placement.GROUNDED)
        measured = ci.product_preservation(cutout, composed, placement=Placement.GROUNDED)
        self.assertEqual(measured, {"ratio": 1.0, "pixels": 140 * 392})          # 알파 252 제품이 모두 재는 대상이 된다(1.4배로 놓인 크기, 2026-11-26)
        image = Image.open(io.BytesIO(composed)).convert("RGB")
        self.assertEqual(image.getpixel((600, 1032 - 10)), lit_colour((30, 90, 200), bottle(), image, 600))   # 제품 밑부분은 빛 맞춤만 한 원래 색(배경이 비치지 않는다)

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
        self.assertEqual(checks["PRODUCT_PRESERVATION"]["detail"]["pixels"], 140 * 392)
        saved = Image.open(self.media_root / outcome.outcome.asset.storage_path).convert("RGB")
        base_y = int(1200 * comp.GROUND_LINE)
        product = lit_colour((30, 90, 200), Image.open(self.media_root / cutout.storage_path), saved, 600)
        self.assertEqual(saved.getpixel((600, base_y - 10)), product)             # 바닥선 바로 위에 제품(빛 맞춤만)
        self.assertNotEqual(saved.getpixel((600, 600 - 280)), product)            # 예전 가운데 자리의 위쪽은 제품이 아니다

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

    def test_every_scene_asks_for_a_close_eye_level_table_whose_front_edge_is_out_of_frame(self):
        # 2026-11-25 사용자 "상품에 구도나 모든것이 부적합함 다시 만들어라" — 정면에서 찍은 상품에 맞춰 탁자 높이에서 거의 정면으로 본 가까운 탁자,
        # 앞모서리는 화면 밖(상품이 허공에 서지 않게), 뒤쪽은 멀고 아주 흐리게(소파·침대가 가까이 선명해 상품이 거대해 보이지 않게).
        for phrase in ("close-up tabletop shot", "looking almost straight ahead", "fills the whole bottom 40% of the image",
                       "its front edge is not visible", "far away, small and very strongly blurred"):
            self.assertIn(phrase, abc.PROMPT_RULES)
        self.assertNotIn("seen from slightly above", abc.PROMPT_RULES)
        self.assertNotIn("bottom third", abc.PROMPT_RULES)
        self.assertIn("completely clear so a product can be placed there later", abc.PROMPT_RULES)
        for code, concept in abc.AI_CONCEPTS.items():
            self.assertIn("in the foreground", concept.generation_prompt, code)
            self.assertTrue(concept.generation_prompt.endswith(abc.PROMPT_RULES), code)
            for word in ("No products", "no people", "No text"):
                self.assertIn(word, concept.generation_prompt, code)
        self.assertIn("bench seat", abc.AI_CONCEPTS["AI_OUTDOOR_TRACK"].generation_prompt)
        self.assertNotIn("plinth", abc.AI_CONCEPTS["AI_STUDIO_MARBLE"].generation_prompt)

    def test_every_scene_shares_the_warm_morning_look(self):
        # 2026-11-25 사용자 "새로 생성한 이미지랑 알맞게 배경 사진도 수정해라" — 여섯 장면이 같은 빛·색(창가 아침 햇빛·베이지/크림/원목)을 쓴다.
        for phrase in ("warm morning sunlight from the left", "warm beige, cream and light oak palette", "warm white balance"):
            self.assertIn(phrase, abc.LOOK)
        self.assertIn(abc.LOOK, abc.PROMPT_RULES)
        for code, concept in abc.AI_CONCEPTS.items():
            self.assertIn(abc.LOOK, concept.generation_prompt, code)
            self.assertLessEqual(len(concept.generation_prompt), 1000, code)
        for code in ("AI_BATHROOM_SHELF", "AI_BEDROOM_SOFT", "AI_STUDIO_MARBLE"):   # 차가운 회색·흰색 장면을 따뜻한 톤으로 바꿨다
            prompt = abc.AI_CONCEPTS[code].generation_prompt
            self.assertNotIn("light grey", prompt, code)
            self.assertNotIn("warm grey", prompt, code)
        self.assertNotIn("spotlight", abc.AI_CONCEPTS["AI_STUDIO_MARBLE"].generation_prompt)


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


def stepped_bottles():
    """앞줄(왼쪽, 밑면이 더 낮다)·뒷줄(오른쪽, 밑면이 더 높다) 두 용기 — #137처럼 밑면 높이가 다른 묶음 상품."""
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (90, 300), (30, 90, 200, 255)), (100, 60))     # 밑면 y=360
    image.paste(Image.new("RGBA", (90, 240), (200, 60, 40, 255)), (200, 80))     # 밑면 y=320
    return image


class ShadowFollowsTheRealBottomTests(unittest.TestCase):
    # 2026-11-25 사용자 "상품에 구도나 모든것이 부적합함 다시 만들어라" — 예전 그림자는 상자 밑 타원 하나라 뒷줄 용기가 떠 보였다.

    def compose(self, cutout):
        return Image.open(io.BytesIO(comp.compose_with_background(
            png_bytes(cutout), BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=scene()), placement=Placement.GROUNDED))).convert("RGB")

    def test_each_container_gets_a_shadow_where_its_own_bottom_touches(self):
        cutout = stepped_bottles()
        image = self.compose(cutout)
        plan = comp.plan_placement(comp.prepare_cutout(cutout, Placement.GROUNDED), comp.CANVAS_SIZE, Placement.GROUNDED)
        box = plan.crop_box
        right_x = plan.offset[0] + (245 - box[0])                    # 뒷줄 용기의 가운데
        right_bottom = plan.offset[1] + (320 - box[1])               # 뒷줄 용기의 실제 밑면(바닥선보다 40픽셀 위)
        self.assertEqual(image.getpixel((right_x, right_bottom - 3)), lit_colour((200, 60, 40), cutout, image, right_x))   # 제품 픽셀은 빛 맞춤만
        just_under = image.getpixel((right_x, right_bottom + 2))
        self.assertLess(sum(just_under), sum((200, 190, 175)) - 60)  # 뒷줄 용기 바로 밑이 짙다(바닥에 닿아 보인다)
        self.assertEqual(image.getpixel((40, 1180)), (200, 190, 175))  # 먼 구석은 그대로

    def test_the_soft_shadow_falls_away_from_the_light_on_the_left(self):
        image = self.compose(bottle())
        plan = comp.plan_placement(comp.prepare_cutout(bottle(), Placement.GROUNDED), comp.CANVAS_SIZE, Placement.GROUNDED)
        base_y = plan.offset[1] + plan.size[1] - 4
        gap = 14
        left = image.getpixel((plan.offset[0] - gap, base_y))
        right = image.getpixel((plan.offset[0] + plan.size[0] + gap, base_y))
        self.assertLess(sum(right), sum(left) - 6)                    # 오른쪽(빛 반대쪽)이 더 어둡다

    def test_faint_haze_is_dropped_but_edges_and_colours_stay(self):
        source = Image.new("RGBA", (5, 1))
        source.putdata([(250, 250, 250, 20), (250, 250, 250, 63), (10, 20, 30, 64), (10, 20, 30, 200), (10, 20, 30, 251)])
        self.assertEqual(list(comp.prepare_cutout(source, Placement.GROUNDED).getdata()),
                         [(250, 250, 250, 0), (250, 250, 250, 0), (10, 20, 30, 64), (10, 20, 30, 200), (10, 20, 30, 255)])
        self.assertEqual(list(comp.prepare_cutout(source, Placement.CENTER).getdata()), list(source.getdata()))   # 로컬 컨셉은 그대로


def white_photo(*, border=(255, 255, 255), fade=True, floor=False, side_shadow=False):
    """흰 배경 상품 사진: 흰 몸통(윤곽선만 회색)·윤곽선 틈이 있는 흰 뚜껑·파란 라벨, 아래 20줄은 바닥 그림자가 흰색까지 옅어진다."""
    image = Image.new("RGB", (300, 300), border)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 80, 200, 260), fill=(253, 253, 253), outline=(200, 200, 200), width=2)    # 흰 몸통
    draw.rectangle((125, 50, 175, 80), fill=(255, 255, 255), outline=(205, 205, 205), width=2)     # 흰 뚜껑
    draw.rectangle((150, 50, 154, 51), fill=(255, 255, 255))                                       # 뚜껑 윤곽선을 끝까지 끊는 틈(순백)
    draw.rectangle((120, 140, 180, 200), fill=(40, 110, 220))                                      # 라벨
    if side_shadow:            # 몸통 왼쪽에 붙은 옅은 띠: "long"은 몸통 밑선보다 아래로 내려가는 그림자 띠, "short"는 밑선 위에서 끝나는 옅은 몸통 가장자리
        for i, x in enumerate(range(92, 100)):
            draw.line((x, 120, x, 285 if side_shadow == "long" else 255), fill=(250 - i,) * 3)
    if floor:
        draw.rectangle((0, 290, 299, 299), fill=(225, 222, 218))                                   # 바닥 그림자가 테두리에 걸린 사진
    if fade:
        for i, y in enumerate(range(261, 281)):                                                    # 밑에 붙은 바닥 그림자가 흰색까지 옅어진다(진하기 20→1)
            draw.line((100, y, 200, y), fill=(235 + i,) * 3)
    return image


class WhiteBackgroundCutoutTests(unittest.TestCase):
    # 2026-11-25 — 흰 배경 사진 전용 누끼(#137은 학습 모델이 흰 용기 머리·밑을 지웠다).

    def cut(self, image):
        from app.domains.media_asset import white_background_cutout as wbc
        out = wbc.cut_white_background(png_bytes(image))
        return None if out is None else Image.open(io.BytesIO(out)).convert("RGBA")

    def test_white_body_white_cap_and_label_stay_and_the_background_goes(self):
        source = white_photo()
        cut = self.cut(source)
        self.assertIsNotNone(cut)
        alpha = cut.getchannel("A")
        self.assertEqual(alpha.getpixel((5, 5)), 0)                                  # 배경
        self.assertEqual(alpha.getpixel((150, 110)), 255)                            # 흰 몸통 안쪽(순백에 가깝다)
        self.assertEqual(alpha.getpixel((150, 65)), 255)                             # 윤곽선에 틈이 있는 흰 뚜껑 안쪽
        self.assertEqual(alpha.getpixel((150, 170)), 255)                            # 라벨
        for xy in ((150, 110), (150, 65), (150, 170)):
            self.assertEqual(cut.getpixel(xy)[:3], source.getpixel(xy))              # 색은 원본 그대로
        self.assertEqual(alpha.getpixel((150, 266)), 0)                              # 밑에 붙은 바닥 그림자 꼬리는 잘라 낸다
        self.assertLessEqual(alpha.getbbox()[3], 264)

    def test_a_faint_side_shadow_strip_goes_but_the_body_edge_stays(self):
        alpha = self.cut(white_photo(side_shadow="long")).getchannel("A")
        self.assertEqual(alpha.getpixel((95, 200)), 0)                               # 옆 그림자 띠
        self.assertEqual(alpha.getpixel((95, 282)), 0)
        self.assertEqual(alpha.getpixel((103, 200)), 255)                            # 몸통 왼쪽 가장자리 바로 안
        short = self.cut(white_photo(side_shadow="short")).getchannel("A")
        self.assertEqual(short.getpixel((95, 200)), 255)                             # 밑선 위에서 끝나는 옅은 가장자리는 몸통일 수 있어 남긴다

    def test_photos_without_a_pure_white_border_are_left_to_the_model(self):
        self.assertIsNone(self.cut(white_photo(border=(240, 238, 236))))
        self.assertIsNone(self.cut(white_photo(floor=True)))                         # 테두리의 순백이 98%보다 적다
        self.assertIsNone(self.cut(Image.new("RGB", (200, 200), (255, 255, 255))))  # 상품이 없다

    def test_the_cutout_button_uses_it_first_and_records_which_method(self):
        import sys
        import types
        from app.domains.media_asset import image_processing_providers as ipp
        from app.domains.media_asset import white_background_cutout as wbc

        provider = ipp.get_background_removal_provider("REMBG")
        request = ipp.BackgroundRemovalRequest(company_id=1, source_image_bytes=png_bytes(white_photo()), source_mime_type="image/png")
        fake = types.ModuleType("rembg")
        calls = []
        fake.new_session = lambda name: calls.append(name) or name
        fake.remove = lambda data, session=None: png_bytes(Image.new("RGBA", (4, 4), (1, 2, 3, 255)))
        saved = sys.modules.get("rembg")
        sys.modules["rembg"] = fake
        try:
            result = provider.remove_background(request)
            self.assertEqual((result.status, result.model_version), ("SUCCEEDED", wbc.MODEL_VERSION))
            self.assertEqual(calls, [])                                               # 학습 모델은 부르지 않았다
            other = ipp.BackgroundRemovalRequest(company_id=1, source_image_bytes=png_bytes(white_photo(border=(240, 238, 236))), source_mime_type="image/png")
            result = provider.remove_background(other)
            self.assertEqual((result.status, result.model_version), ("SUCCEEDED", "u2netp"))
            self.assertEqual(calls, ["u2netp"])                                       # 흰 배경이 아니면 원래 방법
        finally:
            if saved is None:
                sys.modules.pop("rembg", None)
            else:
                sys.modules["rembg"] = saved


if __name__ == "__main__":
    unittest.main()
