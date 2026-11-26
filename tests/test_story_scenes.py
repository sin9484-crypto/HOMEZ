"""
2026-11-26 — 상황 장면 스토리(사용자 "상황 장면 5개을 보여주고 선택할수있게 만들어라 / 사용자가 5개 모두라고 혹은 몇몇 선택하고 만들라하면
그것에 맞춰서 스토리 있게 장면을 이미지로 만들어라").

  - 고른 순서와 상관없이 이야기 순서(사용법 → 돌봄 → 운동 뒤 → 캠핑 → 여행)로 만든다. 문구는 기본값 또는 사람이 고친 값.
  - 그림에 새기는 글은 의료·효능·과장 표현이 있으면 만들지 않는다(바깥 호출 전에 거절). 길이·빈칸·중복·모르는 장면도 거절.
  - 모든 장면이 만들어졌을 때만 스토리 사진(상세 용도, 780 폭, 세로 4,096 안)을 붙인다. 하나라도 실패하면 스토리 사진 없음.
  - 스토리 사진의 편집 기록은 AI 배경 장면까지 거슬러 올라가 생성 표시가 붙는다.
  - 화면: 입력 검사·요청 본문(노드로 실제 함수 실행), 문구 두 언어.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest

from PIL import Image
from pydantic import ValidationError

from app.domains.media_asset import image_edit_record as ier
from app.domains.media_asset import story_scenes as ss
from app.domains.media_asset.ai_background_concepts import AI_CONCEPTS
from app.domains.media_asset.ai_background_router import StoryRunRequest
from app.domains.media_asset.ai_background_service import AiBackgroundOutcome
from app.domains.media_asset.concept_run_service import ConceptRunOutcome
from app.domains.media_asset.story_service import NOT_ALL_SCENES
from app.domains.media_asset.story_service import StoryService
from tests.test_image_edit_record import Base_
from tests.test_image_edit_record import EN
from tests.test_image_edit_record import JS
from tests.test_image_edit_record import KO
from tests.test_image_edit_record import NODE
from tests.test_image_edit_record import png
from tests.test_reference_generation_ui import function_source

ORDER = ["AI_STUDIO_TOWEL", "AI_CARE_BEDSIDE", "AI_GYM_BENCH", "AI_CAMPING_TABLE", "AI_TRAVEL_DESK"]


def scene_png(color) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1200, 1200), color).save(out, format="PNG")
    return out.getvalue()


class PlanTests(unittest.TestCase):

    def test_the_five_scenes_are_ai_concepts_in_story_order(self):
        self.assertEqual(list(ss.STORY_CODES), ORDER)
        for code in ORDER:
            self.assertIn(code, AI_CONCEPTS)

    def test_any_pick_is_put_in_story_order_with_default_captions(self):
        steps = ss.plan_story(["AI_TRAVEL_DESK", "AI_STUDIO_TOWEL", "AI_CAMPING_TABLE"])
        self.assertEqual([(s.order, s.code) for s in steps], [(1, "AI_STUDIO_TOWEL"), (2, "AI_CAMPING_TABLE"), (3, "AI_TRAVEL_DESK")])
        self.assertEqual(steps[0].title, ss.STORY_SCENES[0].title)
        self.assertEqual([s.code for s in ss.plan_story(list(reversed(ORDER)))], ORDER)

    def test_edited_captions_are_used_and_tidied(self):
        steps = ss.plan_story(["AI_GYM_BENCH"], {"AI_GYM_BENCH": {"title": "  퇴근길   헬스 뒤 ", "line": "땀 닦고 바로 약속 장소로"}})
        self.assertEqual((steps[0].title, steps[0].line), ("퇴근길 헬스 뒤", "땀 닦고 바로 약속 장소로"))

    def test_default_captions_pass_the_word_check(self):
        self.assertEqual(len(ss.plan_story(ORDER)), 5)

    def test_bad_requests_are_refused_before_any_call(self):
        bad = [
            ([], None), (["AI_GYM_BENCH", "AI_GYM_BENCH"], None), (["AI_BATHROOM_SHELF"], None), ("AI_GYM_BENCH", None),
            (["AI_GYM_BENCH"], {"AI_CARE_BEDSIDE": {"title": "x", "line": "y"}}),
            (["AI_GYM_BENCH"], {"AI_GYM_BENCH": {"title": "", "line": "y"}}),
            (["AI_GYM_BENCH"], {"AI_GYM_BENCH": {"title": "가" * (ss.TITLE_MAX + 1), "line": "y"}}),
            (["AI_GYM_BENCH"], {"AI_GYM_BENCH": {"title": "x", "line": "가" * (ss.LINE_MAX + 1)}}),
            (["AI_GYM_BENCH"], {"AI_GYM_BENCH": "문구"}),
        ]
        for codes, captions in bad:
            with self.assertRaises(ss.StoryError, msg=(codes, captions)):
                ss.plan_story(codes, captions)

    def test_medical_and_absolute_words_are_never_burned_into_the_picture(self):
        for title in ("살균되는 바디워시", "항 균 효과", "최고의 세정", "100% 순한"):
            with self.assertRaises(ss.StoryError) as caught:
                ss.plan_story(["AI_CARE_BEDSIDE"], {"AI_CARE_BEDSIDE": {"title": title, "line": "닦아 주세요"}})
            self.assertIn("넣을 수 없는 표현", str(caught.exception.detail))
        with self.assertRaises(ss.StoryError):
            ss.clean_header("완벽한 바디워시")


class RenderTests(unittest.TestCase):

    def test_five_scenes_fit_the_detail_page_limits(self):
        steps = ss.plan_story(ORDER)
        colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30), (30, 200, 200)]
        page = Image.open(io.BytesIO(ss.render_story_page("레이펄스 워터리스 바디워시 200ml", steps, [scene_png(c) for c in colors]))).convert("RGB")
        self.assertEqual(page.width, ss.WIDTH)
        self.assertLessEqual(page.height, ss.MAX_HEIGHT)
        column = [page.getpixel((ss.WIDTH // 2, y)) for y in range(page.height)]
        seen = []
        for c in column:
            if c in colors and (not seen or seen[-1] != c):
                seen.append(c)
        self.assertEqual(seen, colors)                       # 장면 사진이 이야기 순서대로 위에서 아래로

    def test_a_mismatch_or_too_long_story_is_refused(self):
        steps = ss.plan_story(ORDER[:2])
        with self.assertRaises(ss.StoryError):
            ss.render_story_page("제목", steps, [scene_png((1, 2, 3))])
        original = ss.SCENE_SIDE
        ss.SCENE_SIDE = 1000
        try:
            with self.assertRaises(ss.StoryError):
                ss.render_story_page("제목", ss.plan_story(ORDER), [scene_png((1, 2, 3))] * 5)
        finally:
            ss.SCENE_SIDE = original


class FakeBackgrounds:
    """AiBackgroundService 대신 — 같은 기록 규칙으로 장면 사진을 저장하고, 부른 순서를 적는다."""

    def __init__(self, test, fail_code=None):
        self.test = test
        self.queue = test.queue
        self.calls = []
        self.fail_code = fail_code

    def run(self, company_id, requested_by, cutout_asset_id, codes, purpose, new_background=False):
        code = codes[0]
        self.calls.append((code, purpose))
        if code == self.fail_code:
            return [AiBackgroundOutcome(code, "FAILED", None, 1, False, "ALLOWANCE_REQUIRED", "허용 없음")]
        cutout = self.test.queue.repository.get_media_asset_for_company(cutout_asset_id, company_id)
        asset = self.test.derived(cutout, "AI_BACKGROUND_RUN", provider="CLOUDFLARE", model=f"{code}-v1", color=(len(self.calls) * 40, 90, 120))
        return [AiBackgroundOutcome(code, "CREATED", ConceptRunOutcome(code, "CREATED", asset, (), True, None), 1, True)]


class ServiceTests(Base_):

    def test_picked_scenes_run_in_story_order_and_a_story_page_is_added(self):
        cutout = self.upload(png(alpha=True))
        fake = FakeBackgrounds(self)
        out = StoryService(self.db, fake).run(self.company_id, 1, cutout.id, ["AI_TRAVEL_DESK", "AI_CARE_BEDSIDE"], header="레이펄스 바디워시")
        self.assertEqual(fake.calls, [("AI_CARE_BEDSIDE", "ADDITIONAL"), ("AI_TRAVEL_DESK", "ADDITIONAL")])
        self.assertIsNone(out.reason)
        page = out.story_page
        self.assertEqual((page.purpose, page.width), ("DETAIL", ss.WIDTH))
        self.assertEqual(page.source_asset_id, out.scenes[0].outcome.asset.id)
        record = self.service.records(self.company_id, [page.id])[page.id]
        self.assertEqual(record.kinds[-2:], [ier.AI_BACKGROUND, ier.DETAIL_PAGE])
        self.assertTrue(record.generative)

    def test_a_missing_scene_means_no_story_page(self):
        cutout = self.upload(png(alpha=True))
        fake = FakeBackgrounds(self, fail_code="AI_GYM_BENCH")
        out = StoryService(self.db, fake).run(self.company_id, 1, cutout.id, ORDER)
        self.assertEqual(out.reason, NOT_ALL_SCENES)
        self.assertIsNone(out.story_page)
        self.assertEqual([c for c, _ in fake.calls], ORDER)          # 나머지 장면 결과는 그대로 알린다
        from app.domains.media_asset.model import MediaAsset
        self.assertEqual(self.db.query(MediaAsset).filter(MediaAsset.purpose == "DETAIL").count(), 0)

    def test_bad_captions_stop_before_any_scene_is_made(self):
        cutout = self.upload(png(alpha=True))
        fake = FakeBackgrounds(self)
        with self.assertRaises(ss.StoryError):
            StoryService(self.db, fake).run(self.company_id, 1, cutout.id, ["AI_GYM_BENCH"], captions={"AI_GYM_BENCH": {"title": "살균 세정", "line": "닦기"}})
        self.assertEqual(fake.calls, [])

    def test_the_default_header_is_used_without_a_product_table(self):
        cutout = self.upload(png(alpha=True))
        self.assertEqual(StoryService(self.db, FakeBackgrounds(self)).default_header(self.company_id, cutout), "이럴 때 필요해요")

    def test_the_request_allows_one_to_five_scenes(self):
        StoryRunRequest(cutout_asset_id=1, concept_codes=ORDER)
        for codes in ([], ORDER + ["AI_STUDIO_TOWEL"]):
            with self.assertRaises(ValidationError):
                StoryRunRequest(cutout_asset_id=1, concept_codes=codes)


class ScreenTests(unittest.TestCase):

    KEYS = ("story_title", "story_note", "story_order", "story_field_title", "story_field_line", "story_all", "story_clear", "story_header",
            "story_pick_count", "story_run_btn", "story_err_none", "story_err_text", "story_made", "story_not_made")

    def test_messages_exist_in_both_languages_and_the_screen_calls_the_server(self):
        for key in self.KEYS:
            self.assertIn(f'"lw.{key}"', KO, key)
            self.assertIn(f'"lw.{key}"', EN, key)
        self.assertIn('"/ai-background/story-runs"', JS)
        self.assertIn('"/ai-background/story-scenes"', JS)

    @unittest.skipUnless(NODE, "node 없음")
    def test_checks_and_request_body(self):
        script = function_source("lwStoryText") + function_source("lwStoryProblem") + function_source("lwStoryBody") + """
const LW_STORY_TITLE_MAX = 24, LW_STORY_LINE_MAX = 48;
const scenes = [{code: "AI_STUDIO_TOWEL", title: "뿌리고, 닦으면 끝", line: "물 없이"}, {code: "AI_CARE_BEDSIDE", title: "침대 곁", line: "닦아 주세요"},
                {code: "AI_TRAVEL_DESK", title: "숙소", line: "간편하게"}];
const ok = {configured: true};
const story = {scenes, codes: ["AI_TRAVEL_DESK", "AI_STUDIO_TOWEL"], captions: {AI_TRAVEL_DESK: {title: " 출장 숙소 "}}, header: "  "};
const out = [
  lwStoryProblem(story, ok), lwStoryProblem(story, {configured: false}), lwStoryProblem(Object.assign({}, story, {codes: []}), ok),
  lwStoryProblem(Object.assign({}, story, {captions: {AI_TRAVEL_DESK: {title: ""}}}), ok),
  lwStoryProblem(Object.assign({}, story, {captions: {AI_TRAVEL_DESK: {line: "가".repeat(49)}}}), ok),
  lwStoryBody(story, "9"), lwStoryBody(Object.assign({}, story, {header: " 레이펄스 "}), 9).header];
console.log(JSON.stringify(out));"""
        out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
        self.assertEqual(out[:5], [None, "sample_err_ai_settings", "story_err_none", "story_err_text", "story_err_text"])
        self.assertEqual(out[5], {"cutout_asset_id": 9, "concept_codes": ["AI_STUDIO_TOWEL", "AI_TRAVEL_DESK"],
                                  "captions": {"AI_STUDIO_TOWEL": {"title": "뿌리고, 닦으면 끝", "line": "물 없이"},
                                               "AI_TRAVEL_DESK": {"title": "출장 숙소", "line": "간편하게"}}})
        self.assertEqual(out[6], "레이펄스")


if __name__ == "__main__":
    unittest.main()
