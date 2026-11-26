"""
=========================================================
Homez OS

File : app/domains/media_asset/info_cards.py

2026-11-26 — **상세 정보 카드**(사용자 "지시를 원하지말고 스스로 생각해라 피카소라고 생각하고"). 상세설명 맨 위에 둘 큰 글씨 카드 두 장:

  1. 핵심 카드  — 상품(실제 누끼) 왼쪽, 오른쪽에 "물 없이 · 헹굼 없이 · 끈적임 없이" 세 줄(번호 동그라미·큰 글씨·한 줄 설명)
  2. 사용법 카드 — "이렇게 쓰세요" 아래 세 칸(뿌리고 → 닦고 → 끝), 칸마다 이 PC에서 그린 단순한 그림(분무·수건·확인 표시)

  - 글은 이 PC 글꼴로 넣는다(상세 이미지 — 추가·대표 이미지에는 글자를 넣지 않는다). 문구는 상품정보제공고시·용기 표기의 사실 범위이고,
    그림에 굳는 글이라 의료·효능·과장 표현(주장 점검기 목록)이 있으면 만들지 않는다(`story_scenes`와 같은 검사).
  - 상품은 다시 그리지 않는다(누끼를 그대로 붙인다). 바깥 호출 없음. 가로 780(쿠팡 상세 폭).
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFilter

from app.domains.media_asset import detail_page_generator as dpg
from app.domains.media_asset import story_scenes as ss
from app.domains.media_asset import studio_shots as st

WIDTH = dpg.DETAIL_IMAGE_WIDTH
AQUA = (64, 160, 205)
AQUA_SOFT = (226, 242, 248)
INK = (34, 40, 46)
MUTED = (96, 106, 114)


@dataclass(frozen=True)
class Point:
    title: str
    line: str


DEFAULT_HEADLINE = "물 없이 닦아 내는 바디워시"
DEFAULT_POINTS = (
    Point("물 없이", "샤워가 어려울 때 뿌리고 닦기만"),
    Point("헹굼 없이", "수건으로 닦아 내면 마무리"),
    Point("끈적임 없이", "용기 표기 NO STICKY"),
)
DEFAULT_STEPS = (
    Point("뿌리고", "피부에 적당량 뿌리기"),
    Point("닦고", "수건으로 닦아 내기"),
    Point("끝", "헹굼 없이 산뜻하게"),
)
DEFAULT_NOTE = "직접 뿌리기 어려우면 손수건에 뿌려 닦아도 됩니다."
DEFAULT_SPECS = ("200ml", "모든 피부 타입", "대한민국 제조")   # 상품정보제공고시(용량·제품 주요 사양·제조국)


class CardError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 거절 사유."""


def _check(*texts: str) -> None:
    for text in texts:
        banned = ss._banned_words(text)                                       # noqa: SLF001 — 스토리 문구와 같은 금지 표현 검사
        if banned:
            raise CardError(f"카드 글에 넣을 수 없는 표현이 있습니다: {', '.join(banned)}")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """띄어쓰기 단위로 줄을 나눈다(낱말 가운데서 끊지 않는다). 한 낱말이 너무 길 때만 글자 단위로 나눈다."""

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if draw.textlength(word, font=font) <= width:
            current = word
        else:
            pieces = dpg._wrap_text(draw, word, font, width)                    # noqa: SLF001
            lines.extend(pieces[:-1])
            current = pieces[-1]
    if current:
        lines.append(current)
    return lines or [""]


def _font(bold: bool, size: int):
    return dpg._load_font(dpg._FONT_BOLD_CANDIDATES if bold else dpg._FONT_REGULAR_CANDIDATES, size)   # noqa: SLF001


def _product(cutout_png: bytes, height: int) -> Image.Image:
    product, _offset, _box = st.place(Image.open(io.BytesIO(cutout_png)), size=(height, height))
    return product


def _paste_with_shadow(card: Image.Image, product: Image.Image, at: tuple[int, int]) -> None:
    shadow = Image.new("L", card.size, 0)
    base_y = at[1] + product.height
    ImageDraw.Draw(shadow).ellipse((at[0] + product.width * 0.1, base_y - 8, at[0] + product.width * 0.9, base_y + 10), fill=120)
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    dark = Image.new("RGBA", card.size, (60, 70, 80, 0))
    dark.putalpha(shadow)
    card.alpha_composite(dark)
    card.alpha_composite(product, dest=at)


def points_card(cutout_png: bytes, headline: str = DEFAULT_HEADLINE, points=DEFAULT_POINTS, specs=DEFAULT_SPECS) -> bytes:
    _check(headline, *[p.title for p in points], *[p.line for p in points], *specs)
    height = 980
    card = Image.new("RGBA", (WIDTH, height), AQUA_SOFT + (255,))
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, WIDTH, 8), fill=AQUA)
    head = _font(True, 40)
    y = 56
    for line in _wrap(draw, headline, head, WIDTH - 80):
        draw.text((40, y), line, font=head, fill=INK)
        y += 54
    product = _product(cutout_png, 900)
    _paste_with_shadow(card, product, (30 + (280 - product.width) // 2, height - 60 - product.height))
    title, small, badge = _font(True, 36), _font(False, 22), _font(True, 26)
    x0, row_y = 330, y + 70
    for number, point in enumerate(points, start=1):
        draw.ellipse((x0, row_y, x0 + 52, row_y + 52), fill=AQUA)
        draw.text((x0 + 26, row_y + 26), str(number), font=badge, fill=(255, 255, 255), anchor="mm")
        draw.text((x0 + 72, row_y - 2), point.title, font=title, fill=INK)
        lines = _wrap(draw, point.line, small, WIDTH - x0 - 112)
        for i, line in enumerate(lines):
            draw.text((x0 + 74, row_y + 50 + i * 30), line, font=small, fill=MUTED)
        row_y += 110 + 30 * len(lines)
        draw.line((x0 + 72, row_y - 30, WIDTH - 40, row_y - 30), fill=(196, 224, 236), width=2)
    chip = _font(True, 22)
    cy = height - 150
    for i, spec in enumerate(specs):                                          # 아래 오른쪽: 고시 사실 꼬리표
        y0 = cy + (i - 1) * 56
        w = int(draw.textlength(spec, font=chip)) + 44
        draw.rounded_rectangle((x0 + 72, y0, x0 + 72 + w, y0 + 42), radius=21, fill=(255, 255, 255), outline=(176, 214, 230), width=2)
        draw.text((x0 + 72 + w // 2, y0 + 21), spec, font=chip, fill=AQUA, anchor="mm")
    out = io.BytesIO()
    card.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _icon(draw: ImageDraw.ImageDraw, kind: str, centre: tuple[int, int], r: int) -> None:
    cx, cy = centre
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255), outline=AQUA, width=4)
    if kind == "spray":
        draw.rounded_rectangle((cx - 16, cy - 10, cx + 12, cy + 44), radius=8, outline=AQUA, width=4)
        draw.rectangle((cx - 10, cy - 28, cx + 6, cy - 10), outline=AQUA, width=4)
        for i, (dx, dy) in enumerate(((-34, -26), (-44, -12), (-38, 2), (-52, -20), (-50, 4))):
            s = 4 if i < 3 else 3
            draw.ellipse((cx + dx - s, cy + dy - s, cx + dx + s, cy + dy + s), fill=AQUA)
    elif kind == "towel":
        for i in range(3):
            top = cy - 22 + i * 16
            draw.rounded_rectangle((cx - 40, top, cx + 40, top + 14), radius=6, outline=AQUA, width=4)
    else:
        draw.line((cx - 26, cy + 2, cx - 6, cy + 22, cx + 30, cy - 20), fill=AQUA, width=8, joint="curve")


def steps_card(steps=DEFAULT_STEPS, note: str = DEFAULT_NOTE, title: str = "이렇게 쓰세요") -> bytes:
    _check(title, note, *[p.title for p in steps], *[p.line for p in steps])
    if len(steps) != 3:
        raise CardError("사용법은 세 단계여야 합니다.")
    height = 620
    card = Image.new("RGB", (WIDTH, height), (255, 255, 255))
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, WIDTH, 8), fill=AQUA)
    draw.text((WIDTH // 2, 70), title, font=_font(True, 40), fill=INK, anchor="mm")
    big, small, num = _font(True, 34), _font(False, 20), _font(True, 22)
    columns = [WIDTH * (i * 2 + 1) // 6 for i in range(3)]
    for i, (cx, step, kind) in enumerate(zip(columns, steps, ("spray", "towel", "check"))):
        _icon(draw, kind, (cx, 220), 78)
        draw.text((cx, 330), f"{i + 1}", font=num, fill=AQUA, anchor="mm")
        draw.text((cx, 372), step.title, font=big, fill=INK, anchor="mm")
        for j, line in enumerate(_wrap(draw, step.line, small, WIDTH // 3 - 30)):
            draw.text((cx, 418 + j * 28), line, font=small, fill=MUTED, anchor="mm")
        if i < 2:
            ax = (cx + columns[i + 1]) // 2
            draw.polygon([(ax - 8, 208), (ax + 8, 220), (ax - 8, 232)], fill=(176, 214, 230))
    draw.rounded_rectangle((60, 500, WIDTH - 60, 570), radius=14, fill=AQUA_SOFT)
    draw.text((WIDTH // 2, 535), note, font=_font(False, 21), fill=MUTED, anchor="mm")
    out = io.BytesIO()
    card.save(out, format="PNG")
    return out.getvalue()


__all__ = ["points_card", "steps_card", "CardError", "Point", "DEFAULT_HEADLINE", "DEFAULT_POINTS", "DEFAULT_STEPS", "DEFAULT_NOTE", "DEFAULT_SPECS", "WIDTH"]
