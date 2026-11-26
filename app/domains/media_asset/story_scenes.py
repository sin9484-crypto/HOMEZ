"""
=========================================================
Homez OS

File : app/domains/media_asset/story_scenes.py

2026-11-26 — **상황 장면 스토리**(사용자 "상황 장면 5개을 보여주고 선택할수있게 만들어라 / 사용자가 5개 모두라고 혹은 몇몇 선택하고
만들라하면 그것에 맞춰서 스토리 있게 장면을 이미지로 만들어라"). 순수 함수 — DB·네트워크·AI 없음(그리기·저장은 `story_service`).

  - 장면 5개는 **이야기 순서**가 정해져 있다: 어떻게 쓰나(수건 스튜디오) → 돌봄(침대 곁) → 운동 뒤 → 캠핑 → 여행.
    사람이 몇 개를 고르든 이 순서로 놓는다(고른 순서가 아니다) — 첫 장은 언제나 상품·사용법이 먼저 보이게.
  - 장면마다 **한 줄 제목·설명**(기본 문구)이 있고 사람이 고칠 수 있다. 글은 AI가 아니라 이 PC의 글꼴로 스토리 사진에만 넣는다
    (추가 이미지 자체에는 글자를 넣지 않는다 — 이미지 정책 I0의 "글자 금지" 원칙).
  - 사진에 새긴 글은 나중에 고칠 수 없으므로, 주장 점검기(`listing_claim_check`)의 **의료·효능·절대 표현이 있으면 만들지 않는다**
    (상세 설명은 경고만 하지만 그림에 굳는 글은 더 엄격하게 — 2026-11-26 제미나이 협의 "살균·치료 등 의약품 오인 표현 금지").
  - 스토리 사진: 가로 780(쿠팡 상세 폭), 세로는 상세 페이지 한도(4,096) 안. 머리 제목 → 장면마다 [번호·제목·설명 → 장면 사진].
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from PIL import ImageDraw

from app.core.exceptions import BadRequestException
from app.domains.media_asset import detail_page_generator as dpg

TITLE_MAX = 24
LINE_MAX = 48
HEADER_MAX = 40
SUBTITLE = "이럴 때 쓰세요"
WIDTH = dpg.DETAIL_IMAGE_WIDTH
MAX_HEIGHT = dpg.DETAIL_IMAGE_MAX_HEIGHT
SCENE_SIDE = 600          # 장면 사진 한 변(다섯 장이 세로 한도 안에 들어가게)
ACCENT = (45, 108, 223)


@dataclass(frozen=True)
class StoryScene:
    code: str
    role_ko: str
    title: str
    line: str


# 이야기 순서 그대로(고친 순서가 곧 스토리 순서). 문구는 상품정보제공고시·용기 표기의 사실 범위(물 없이 뿌리고 닦기·헹굼 없음·끈적임 없음).
STORY_SCENES: tuple[StoryScene, ...] = (
    StoryScene("AI_STUDIO_TOWEL", "사용법", "뿌리고, 닦으면 끝", "물 없이 피부에 뿌린 뒤 수건으로 닦아 내세요"),
    StoryScene("AI_CARE_BEDSIDE", "돌봄", "샤워가 어려운 날, 침대 곁에서", "물을 받지 않고도 닦아 줄 수 있어요"),
    StoryScene("AI_GYM_BENCH", "운동 뒤", "운동 뒤 샤워실이 없을 때", "땀을 닦고 끈적임 없이 산뜻하게"),
    StoryScene("AI_CAMPING_TABLE", "캠핑", "씻을 곳이 마땅치 않은 캠핑장에서", "캠핑·차박 가방에 하나 챙겨 두세요"),
    StoryScene("AI_TRAVEL_DESK", "여행", "여행·출장 숙소에서", "물 없이 간편하게 마무리하세요"),
)
STORY_CODES = tuple(s.code for s in STORY_SCENES)


class StoryError(BadRequestException):
    """사람에게 그대로 보여 줄 수 있는 거절 사유."""


@dataclass(frozen=True)
class StoryStep:
    order: int            # 1부터
    code: str
    role_ko: str
    title: str
    line: str


def _banned_words(text: str) -> list[str]:
    from app.domains.marketplace_listing.listing_claim_check import ABSOLUTE_WORDS
    from app.domains.marketplace_listing.listing_claim_check import MEDICAL_WORDS

    squeezed = "".join(text.split())
    return [w for w in MEDICAL_WORDS + ABSOLUTE_WORDS if "".join(w.split()) and "".join(w.split()) in squeezed]


def _clean(value, limit: int, what: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise StoryError(f"{what}을(를) 비울 수 없습니다.")
    if len(text) > limit:
        raise StoryError(f"{what}은(는) {limit}자 이하여야 합니다.")
    banned = _banned_words(text)
    if banned:
        raise StoryError(f"{what}에 사진에 넣을 수 없는 표현이 있습니다: {', '.join(banned)} (의료·효능·과장 표현은 근거가 있어도 그림에 새기지 않습니다)")
    return text


def plan_story(codes, captions: dict | None = None) -> list[StoryStep]:
    """고른 장면(1~5개, 중복 없음)을 이야기 순서로 놓고 문구를 정한다. captions = {코드: {"title":…, "line":…}}(없으면 기본 문구)."""

    if not isinstance(codes, (list, tuple)) or not codes:
        raise StoryError("스토리에 넣을 장면을 하나 이상 고르세요.")
    if len(set(codes)) != len(codes):
        raise StoryError("같은 장면을 두 번 고를 수 없습니다.")
    unknown = [c for c in codes if c not in STORY_CODES]
    if unknown:
        raise StoryError(f"상황 장면이 아닙니다: {', '.join(map(str, unknown))}")
    captions = captions or {}
    if not isinstance(captions, dict) or any(k not in codes for k in captions):
        raise StoryError("문구는 고른 장면에만 줄 수 있습니다.")
    steps: list[StoryStep] = []
    for scene in STORY_SCENES:
        if scene.code not in codes:
            continue
        given = captions.get(scene.code) or {}
        if not isinstance(given, dict):
            raise StoryError("문구 형식이 올바르지 않습니다.")
        title = _clean(given.get("title", scene.title), TITLE_MAX, f"'{scene.role_ko}' 제목")
        line = _clean(given.get("line", scene.line), LINE_MAX, f"'{scene.role_ko}' 설명")
        steps.append(StoryStep(len(steps) + 1, scene.code, scene.role_ko, title, line))
    return steps


def clean_header(value) -> str:
    return _clean(value, HEADER_MAX, "스토리 제목")


def render_story_page(header: str, steps: list[StoryStep], scene_images: list[bytes]) -> bytes:
    """머리 제목 + 장면마다 [번호·제목·설명 → 장면 사진]. 세로 한도를 넘으면 자르지 않고 거절한다."""

    if len(steps) != len(scene_images) or not steps:
        raise StoryError("장면과 사진 수가 맞지 않습니다.")
    title_font = dpg._load_font(dpg._FONT_BOLD_CANDIDATES, 34)        # noqa: SLF001 — 상세 페이지와 같은 글꼴
    sub_font = dpg._load_font(dpg._FONT_REGULAR_CANDIDATES, 22)       # noqa: SLF001
    step_font = dpg._load_font(dpg._FONT_BOLD_CANDIDATES, 28)         # noqa: SLF001
    line_font = dpg._load_font(dpg._FONT_REGULAR_CANDIDATES, 21)      # noqa: SLF001
    pad = 32
    text_width = WIDTH - pad * 2
    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    wrap = lambda text, font: dpg._wrap_text(measure, text, font, text_width)   # noqa: E731,SLF001
    lh = lambda font, extra: font.getbbox("가")[3] + extra                      # noqa: E731

    header_lines = wrap(header, title_font)
    blocks = []
    for step in steps:
        blocks.append((wrap(f"{step.order}. {step.title}", step_font), wrap(step.line, line_font)))
    height = pad + len(header_lines) * lh(title_font, 10) + lh(sub_font, 8) + pad // 2
    for title_lines, line_lines in blocks:
        height += pad + len(title_lines) * lh(step_font, 8) + len(line_lines) * lh(line_font, 6) + 14 + SCENE_SIDE
    height += pad
    if height > MAX_HEIGHT:
        raise StoryError(f"스토리 사진이 너무 깁니다({height}px > {MAX_HEIGHT}px). 장면 수나 글을 줄이세요.")

    page = Image.new("RGB", (WIDTH, height), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    y = pad
    for text in header_lines:
        draw.text((pad, y), text, font=title_font, fill=ACCENT)
        y += lh(title_font, 10)
    draw.text((pad, y), SUBTITLE, font=sub_font, fill=(110, 110, 110))
    y += lh(sub_font, 8) + pad // 2
    for (title_lines, line_lines), data in zip(blocks, scene_images):
        y += pad
        for text in title_lines:
            draw.text((pad, y), text, font=step_font, fill=(34, 34, 34))
            y += lh(step_font, 8)
        for text in line_lines:
            draw.text((pad, y), text, font=line_font, fill=(96, 96, 96))
            y += lh(line_font, 6)
        y += 14
        scene = Image.open(io.BytesIO(data)).convert("RGB").resize((SCENE_SIDE, SCENE_SIDE), Image.LANCZOS)
        page.paste(scene, ((WIDTH - SCENE_SIDE) // 2, y))
        y += SCENE_SIDE
    out = io.BytesIO()
    page.save(out, format="PNG")
    return out.getvalue()


def summaries() -> list[dict]:
    """장면 목록. 2026-11-26(사용자 "프롬프트로 정리해서 이미지 생성까지 맞춰라"): 장면마다 AI에 보내는 그림 지시문(장면 글 + 공통 규칙)을
    그대로 붙인다 — 화면에서 펼쳐 보고, 정리 문서(docs/HOMEZ_STORY_PROMPTS_20261126.md)와 같은 글인지 시험이 확인한다."""

    from app.domains.media_asset import ai_background_concepts as abc

    return [{"order": i + 1, "code": s.code, "role_ko": s.role_ko, "title": s.title, "line": s.line,
             "prompt": abc.get_ai_concept(s.code).generation_prompt} for i, s in enumerate(STORY_SCENES)]


__all__ = ["STORY_SCENES", "STORY_CODES", "StoryScene", "StoryStep", "StoryError", "plan_story", "clean_header", "render_story_page", "summaries",
           "TITLE_MAX", "LINE_MAX", "HEADER_MAX", "SCENE_SIDE"]
