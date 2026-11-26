"""
=========================================================
Homez OS

File : app/domains/media_asset/brand_detail.py

2026-11-26 — **상표 체계로 통일한 상세 페이지 두 장**(사용자 "평균 90점이 될 정도로 다시 만들어라"). 카드·스토리를 따로 쌓던 것을
하늘빛 물방울을 축으로 한 하나의 디자인(같은 글꼴 단계·색·여백·제목 표시)으로 묶는다. 가로 780(쿠팡 상세 폭), 각 장 세로 4,096 이하.

  1장: 여는 배너(큰 제목 + 받침대 화보) → 핵심 세 가지 → 함유 성분 줄 → 사용법 세 단계
  2장: 이럴 때 꺼내 쓰세요(네 장면 격자와 한 줄) → 제품 정보 표 → 전성분 → 사용할 때 주의사항

  - 판매 문구(제목·핵심·장면 한 줄·사용법)는 그림에 굳으므로 의료·효능·과장 표현이 있으면 만들지 않는다(주장 점검기 목록).
  - 제품 정보·전성분·주의사항은 상품정보제공고시 **원문 그대로**(법정 표시라 문구 검사 대상이 아니다). 상담 전화번호는 넣지 않는다.
  - 상품·장면은 이미 만든 사진을 그대로 붙인다(다시 그리지 않음). 바깥 호출 없음.
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from PIL import ImageDraw

from app.domains.media_asset import detail_page_generator as dpg
from app.domains.media_asset import info_cards as ic

WIDTH = dpg.DETAIL_IMAGE_WIDTH
MAX_HEIGHT = dpg.DETAIL_IMAGE_MAX_HEIGHT
PAD = 48
AQUA = (52, 150, 199)
AQUA_DEEP = (28, 98, 140)
AQUA_SOFT = (231, 244, 249)
CREAM = (250, 246, 240)
INK = (30, 36, 42)
MUTED = (100, 110, 118)
LINE = (214, 230, 238)


class BrandPageError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 거절 사유."""


@dataclass(frozen=True)
class Scene:
    image_png: bytes
    title: str
    line: str


@dataclass(frozen=True)
class Copy:
    brand_line: str = "레이펄스 워터리스 바디워시 200ml"
    headline: tuple[str, str] = ("물 없이,", "뿌리고 닦기만")
    sub: str = "헹굼 없이 수건으로 마무리하는 스프레이형 바디워시"
    points: tuple = ic.DEFAULT_POINTS
    ingredients_line: str = "편백수 · 알로에 · 녹차 · 캐모마일 추출물 함유"
    steps: tuple = ic.DEFAULT_STEPS
    note: str = ic.DEFAULT_NOTE
    scenes_title: str = "이럴 때 꺼내 쓰세요"


def _font(bold: bool, size: int):
    return ic._font(bold, size)                                                 # noqa: SLF001


def _wrap(draw, text, font, width):
    return ic._wrap(draw, text, font, width)                                   # noqa: SLF001


def _check_copy(copy: Copy, scenes: list[Scene]) -> None:
    texts = [copy.brand_line, *copy.headline, copy.sub, copy.ingredients_line, copy.note, copy.scenes_title,
             *[p.title for p in copy.points], *[p.line for p in copy.points], *[p.title for p in copy.steps], *[p.line for p in copy.steps],
             *[s.title for s in scenes], *[s.line for s in scenes]]
    try:
        ic._check(*texts)                                                       # noqa: SLF001
    except ic.CardError as exc:
        raise BrandPageError(str(exc)) from exc


def _section_title(draw, y, text, kicker=None) -> int:
    if kicker:
        draw.text((PAD, y), kicker, font=_font(True, 18), fill=AQUA)
        y += 30
    draw.text((PAD, y), text, font=_font(True, 38), fill=INK)
    y += 54
    draw.rectangle((PAD, y, PAD + 56, y + 5), fill=AQUA)
    return y + 34


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    """가운데를 잘라 칸 비율에 맞춘다(상품이 가운데라 잘리지 않는다)."""

    w, h = image.size
    scale = max(width / w, height / h)
    resized = image.resize((int(w * scale + 0.5), int(h * scale + 0.5)), Image.LANCZOS)
    left, top = (resized.width - width) // 2, (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def page_one(hero_png: bytes, copy: Copy = Copy()) -> bytes:
    _check_copy(copy, [])
    page = Image.new("RGB", (WIDTH, MAX_HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    # 여는 배너
    hero_h = 1060
    band = Image.linear_gradient("L").resize((WIDTH, hero_h))
    page.paste(Image.composite(Image.new("RGB", (WIDTH, hero_h), (202, 230, 241)), Image.new("RGB", (WIDTH, hero_h), (240, 248, 251)), band), (0, 0))
    draw.text((PAD, 56), copy.brand_line, font=_font(True, 22), fill=AQUA_DEEP)
    draw.text((PAD, 98), copy.headline[0], font=_font(True, 66), fill=INK)
    draw.text((PAD, 182), copy.headline[1], font=_font(True, 66), fill=AQUA_DEEP)
    draw.text((PAD, 278), copy.sub, font=_font(False, 24), fill=MUTED)
    hero = _fit(Image.open(io.BytesIO(hero_png)).convert("RGB"), WIDTH - 2 * PAD, hero_h - 360)
    mask = Image.new("L", hero.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, hero.width, hero.height), radius=28, fill=255)
    page.paste(hero, (PAD, 330), mask)
    y = hero_h + 70
    # 핵심 세 가지
    y = _section_title(draw, y, "물 없이 닦아 내는 바디워시", "핵심 세 가지")
    for number, point in enumerate(copy.points, start=1):
        draw.rounded_rectangle((PAD, y, WIDTH - PAD, y + 132), radius=22, fill=AQUA_SOFT)
        draw.ellipse((PAD + 28, y + 36, PAD + 88, y + 96), fill=AQUA)
        draw.text((PAD + 58, y + 66), str(number), font=_font(True, 28), fill=(255, 255, 255), anchor="mm")
        draw.text((PAD + 116, y + 26), point.title, font=_font(True, 34), fill=INK)
        draw.text((PAD + 118, y + 80), point.line, font=_font(False, 22), fill=MUTED)
        y += 152
    # 함유 성분 줄(전성분 기준)
    y += 18
    draw.rounded_rectangle((PAD, y, WIDTH - PAD, y + 74), radius=37, fill=CREAM, outline=LINE, width=2)
    draw.text((WIDTH // 2, y + 37), copy.ingredients_line, font=_font(True, 23), fill=AQUA_DEEP, anchor="mm")
    draw.text((WIDTH // 2, y + 96), "전성분 기준 · 자세한 성분은 아래 제품 정보에서 확인하세요", font=_font(False, 18), fill=MUTED, anchor="mm")
    y += 170
    # 사용법
    y = _section_title(draw, y, "이렇게 쓰세요", "사용법")
    columns = [WIDTH * (i * 2 + 1) // 6 for i in range(3)]
    for i, (cx, step, kind) in enumerate(zip(columns, copy.steps, ("spray", "towel", "check"))):
        ic._icon(draw, kind, (cx, y + 90), 80)                                   # noqa: SLF001
        draw.text((cx, y + 206), f"{i + 1}단계", font=_font(True, 18), fill=AQUA, anchor="mm")
        draw.text((cx, y + 246), step.title, font=_font(True, 34), fill=INK, anchor="mm")
        for j, line in enumerate(_wrap(draw, step.line, _font(False, 20), WIDTH // 3 - 30)):
            draw.text((cx, y + 290 + j * 28), line, font=_font(False, 20), fill=MUTED, anchor="mm")
        if i < 2:
            ax = (cx + columns[i + 1]) // 2
            draw.polygon([(ax - 9, y + 78), (ax + 9, y + 90), (ax - 9, y + 102)], fill=(170, 210, 228))
    y += 360
    draw.rounded_rectangle((PAD, y, WIDTH - PAD, y + 76), radius=16, fill=AQUA_SOFT)
    draw.text((WIDTH // 2, y + 38), copy.note, font=_font(False, 21), fill=MUTED, anchor="mm")
    y += 76 + PAD
    return _finish(page, y)


@dataclass(frozen=True)
class InfoRow:
    label: str
    value: str


def page_two(scenes: list[Scene], rows: list[InfoRow], ingredients: str, cautions: list[str], copy: Copy = Copy()) -> bytes:
    if len(scenes) != 4:
        raise BrandPageError("장면은 네 장이어야 합니다(2×2 격자).")
    _check_copy(copy, scenes)
    page = Image.new("RGB", (WIDTH, MAX_HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    y = _section_title(draw, PAD + 10, copy.scenes_title, "쓰는 순간")
    cell = (WIDTH - 2 * PAD - 24) // 2
    for i, scene in enumerate(scenes):
        x0 = PAD + (i % 2) * (cell + 24)
        y0 = y + (i // 2) * (cell + 118)
        tile = _fit(Image.open(io.BytesIO(scene.image_png)).convert("RGB"), cell, cell)
        mask = Image.new("L", tile.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cell, cell), radius=20, fill=255)
        page.paste(tile, (x0, y0), mask)
        draw.text((x0 + 4, y0 + cell + 16), scene.title, font=_font(True, 24), fill=INK)
        for j, line in enumerate(_wrap(draw, scene.line, _font(False, 19), cell - 8)[:2]):
            draw.text((x0 + 4, y0 + cell + 52 + j * 26), line, font=_font(False, 19), fill=MUTED)
    y += 2 * (cell + 118) + 40
    # 제품 정보 표(고시 원문)
    y = _section_title(draw, y, "제품 정보", "상품정보제공고시")
    label_font, value_font = _font(True, 20), _font(False, 20)
    for k, row in enumerate(rows):
        lines = _wrap(draw, row.value, value_font, WIDTH - 2 * PAD - 220)
        h = 28 * len(lines) + 30
        if k % 2 == 0:
            draw.rectangle((PAD, y, WIDTH - PAD, y + h), fill=AQUA_SOFT)
        draw.text((PAD + 20, y + 15), row.label, font=label_font, fill=AQUA_DEEP)
        for j, line in enumerate(lines):
            draw.text((PAD + 220, y + 15 + j * 28), line, font=value_font, fill=INK)
        y += h
    y += 40
    draw.text((PAD, y), "전성분", font=_font(True, 22), fill=INK)
    y += 40
    for line in _wrap(draw, ingredients, _font(False, 18), WIDTH - 2 * PAD):
        draw.text((PAD, y), line, font=_font(False, 18), fill=MUTED)
        y += 26
    y += 30
    draw.text((PAD, y), "사용할 때 주의사항", font=_font(True, 22), fill=INK)
    y += 40
    for item in cautions:
        for j, line in enumerate(_wrap(draw, item, _font(False, 18), WIDTH - 2 * PAD - 20)):
            draw.text((PAD + (0 if j == 0 else 20), y), line, font=_font(False, 18), fill=MUTED)
            y += 26
        y += 6
    return _finish(page, y + PAD)


# 고시 항목 이름 → 표에 쓰는 짧은 이름(값은 원문 그대로). 전성분·주의사항은 표 밖에 원문으로, 상담 전화번호는 넣지 않는다.
NOTICE_ROWS = (
    ("용량(중량)", "용량"), ("제품 주요 사양", "제품 주요 사양"), ("사용기한 또는 개봉 후 사용기간", "사용기한"), ("제조국", "제조국"),
    ("화장품제조업자, 화장품책임판매업자 및 맞춤형화장품판매업자", "제조·책임판매"),
    ("화장품법에 따른 기능성 화장품 심사(또는 보고)를 필함 해당 유무", "기능성 화장품"), ("품질보증기준", "품질보증기준"),
)
INGREDIENTS_NAME = "화장품법에 따라 기재, 표시하여야 하는 모든 성분"
CAUTIONS_NAME = "사용할 때 주의사항"


def notice_facts(notices: list[dict]) -> tuple[list[InfoRow], str, list[str]]:
    """쿠팡 고시(`notices[].noticeCategoryDetailNames[]`)에서 표·전성분·주의사항을 원문 그대로 뽑는다. 주의사항은 번호(1. 2. …) 앞에서 나눈다."""

    values: dict[str, str] = {}
    for notice in notices or []:
        for detail in notice.get("noticeCategoryDetailNames") or []:
            name, content = str(detail.get("noticeCategoryDetailName") or ""), str(detail.get("content") or "").strip()
            if name and content:
                values[name] = content
    rows = [InfoRow(short, values[name]) for name, short in NOTICE_ROWS if name in values]
    cautions_text = values.get(CAUTIONS_NAME, "")
    import re

    parts = [p.strip() for p in re.split(r"\s(?=\d+\.\s)", cautions_text) if p.strip()]
    return rows, values.get(INGREDIENTS_NAME, ""), parts


def _finish(page: Image.Image, used: int) -> bytes:
    if used > MAX_HEIGHT:
        raise BrandPageError(f"상세 페이지가 너무 깁니다({used}px > {MAX_HEIGHT}px).")
    out = io.BytesIO()
    page.crop((0, 0, WIDTH, used)).save(out, format="PNG")
    return out.getvalue()


__all__ = ["page_one", "page_two", "Scene", "InfoRow", "Copy", "BrandPageError", "WIDTH", "notice_facts", "NOTICE_ROWS"]
