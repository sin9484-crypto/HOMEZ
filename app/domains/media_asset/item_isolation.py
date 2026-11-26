"""
=========================================================
Homez OS

File : app/domains/media_asset/item_isolation.py

2026-11-26 — **묶음 사진에서 파는 상품 하나만 떼어 내기**(사용자 "이전 사진과 제품 정보를 확인하고 사진을 만든것인가?? … 상품판매성이 있는 것을
원하는것이다"). 레이펄스 상품(후보 #3)은 스프레이 바디워시 200ml **한 병**을 파는데, 공급처 사진(#1)은 같은 상표 네 병이 붙어 찍힌 600×600
흰 배경 사진 한 장뿐이었다. 그 사진을 통째로 쓰면 고객이 네 병 묶음으로 오해한다(쿠팡 "고객이 받는 모습과 같게", 제미나이 협의 결론도 같음).

방법(흰 배경 사진 전용, Pillow만, 바깥 호출 없음, 상품 색은 원본 그대로 — 알파만 정한다):
  1. 사진 전체의 흰 배경 누끼(`white_background_cutout`)를 먼저 만든다(바닥 그림자 꼬리·옆 그림자 띠가 이미 빠진 모양).
  2. 사람이 고른 **가로 범위** 안에서, 순백이 아닌 픽셀 중 범위 한가운데와 이어진 덩어리를 잡는다(틈 메우기 전의 모양이라 흰 틈으로 떨어진 옆 병 뚜껑은 안 붙는다).
  3. 병·통·상자처럼 **좌우 대칭인 용기**라고 보고, 그 덩어리를 세로축으로 뒤집은 모양과 가장 많이 겹치는 축을 찾는다.
     덩어리 ∧ 뒤집은 덩어리만 남긴다 — 한쪽에만 걸친 옆 병 조각(뒤쪽 병의 어깨·뚜껑)은 반대쪽에 짝이 없어 빠진다.
  4. 안쪽 구멍을 채우고 작게 정리한 뒤, 1의 알파와 겹친다(밑 꼬리 자르기·가장자리 부드럽게는 1의 결과를 그대로 따른다).

한계: 손잡이·옆 주둥이처럼 한쪽으로 튀어나온 모양은 잘린다(대칭 가정). 흰 배경 사진이 아니면 None — 다른 방법으로 넘기지 않고 쓰지 않는다.
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from PIL import ImageChops
from PIL import ImageFilter

from app.domains.media_asset import white_background_cutout as wbc

MODEL_VERSION = "item-isolation-v1"
MIN_RANGE_SHARE = 0.05       # 고른 가로 범위는 사진 너비의 5% 이상
MIN_ITEM_SHARE = 0.005       # 떼어 낸 상품이 사진의 0.5%보다 작으면 실패로 본다
AXIS_SEARCH_SHARE = 0.15     # 대칭 축은 고른 범위 가운데에서 범위 너비의 ±15% 안에서 찾는다
TIDY_SIZE = 5


class ItemIsolationError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 실패 사유."""


@dataclass(frozen=True)
class ItemIsolation:
    png_bytes: bytes
    axis_x: float                     # 찾은 대칭 축(원본 픽셀)
    box: tuple[int, int, int, int]    # 떼어 낸 상품의 범위(원본 픽셀)


def _mirror(mask: Image.Image, twice_axis: int) -> Image.Image:
    """세로축 x = twice_axis/2 로 뒤집는다(정수 픽셀끼리 정확히 대응 — 보간 없음)."""

    return mask.transform(mask.size, Image.AFFINE, (-1, 0, twice_axis, 0, 1, 0), resample=Image.NEAREST, fillcolor=0)


def _count(mask: Image.Image) -> int:
    return mask.histogram()[255]


def _component(mask: Image.Image, seed: tuple[int, int]) -> Image.Image:
    return wbc.flood_region(mask, seed)


def _seed(mask: Image.Image, center_x: int, top: int, bottom: int, half_width: int) -> tuple[int, int] | None:
    """범위 한가운데에서 가장 가까운 상품 픽셀(가운데 줄부터 위아래로, 가운데 열부터 좌우로)."""

    mid = (top + bottom) // 2
    rows = sorted(range(top, bottom), key=lambda y: abs(y - mid))
    for dx in range(0, half_width + 1):
        for x in (center_x - dx, center_x + dx):
            if not 0 <= x < mask.width:
                continue
            for y in rows:
                if mask.getpixel((x, y)) == 255:
                    return x, y
    return None


def _axis_twice(piece: Image.Image, seed: tuple[int, int], left: int, right: int, reach: int) -> int:
    """대칭 축 ×2(반 픽셀 단위 정수). 줄마다 씨앗 열을 지나는 덩어리 구간을 보고, **양쪽 끝이 모두 고른 범위 안에서 흰 틈으로 끝나는 줄**
    (옆 병에 닿지 않은 줄 — 보통 뚜껑·분사 머리·목)의 가운데 값들의 중앙값을 쓴다. 범위 끝에 닿은 줄은 사람이 고른 경계가 섞여 있어 쓰지 않는다
    (범위를 옆 병 쪽으로 넓게 잡으면 축이 그쪽으로 끌려가 옆 병이 붙었다). 그런 줄이 없으면 덩어리와 뒤집은 덩어리가 가장 많이 겹치는 축."""

    pixels = piece.load()
    width, height = piece.size
    x0 = seed[0]
    votes: list[int] = []
    for y in range(height):
        if pixels[x0, y] != 255:
            continue
        a = x0
        while a - 1 >= left and pixels[a - 1, y] == 255:
            a -= 1
        b = x0
        while b + 1 < right and pixels[b + 1, y] == 255:
            b += 1
        if a > left and b < right - 1:
            votes.append(a + b)
    if len(votes) >= 3:
        votes.sort()
        return votes[len(votes) // 2]

    center_x = (left + right) // 2
    box = piece.getbbox() or (0, 0, width, height)
    band = piece.crop((0, box[1], width, box[3]))

    def overlap(twice: int) -> int:
        return _count(ImageChops.multiply(band, _mirror(band, twice)))

    best = max(range(2 * (center_x - reach), 2 * (center_x + reach) + 1, 4), key=overlap)
    return max(range(best - 3, best + 4), key=overlap)


def isolate_item(image_bytes: bytes, x_from: float, x_to: float) -> ItemIsolation:
    """흰 배경 묶음 사진에서 가로 범위(사진 너비 대비 0~1) 안의 상품 하나만 남긴 투명 PNG. 실패하면 ItemIsolationError."""

    if not (isinstance(x_from, (int, float)) and isinstance(x_to, (int, float))):
        raise ItemIsolationError("가로 범위가 올바르지 않습니다.")
    if not (0.0 <= x_from < x_to <= 1.0) or (x_to - x_from) < MIN_RANGE_SHARE:
        raise ItemIsolationError(f"가로 범위는 0~1 사이, 너비 {int(MIN_RANGE_SHARE * 100)}% 이상이어야 합니다.")

    whole_png = wbc.cut_white_background(image_bytes)
    if whole_png is None:
        raise ItemIsolationError("흰 배경 사진에서만 상품 하나를 떼어 낼 수 있습니다(테두리가 순백이 아니거나 상품 영역이 이상합니다).")
    whole = Image.open(io.BytesIO(whole_png)).convert("RGBA")
    rgb = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = rgb.size

    limit = 255 - wbc.WHITE_MIN
    blocked = wbc._darkness(rgb).point(lambda v: 0 if v <= limit else 255)
    left = int(x_from * width)
    right = min(width, int(round(x_to * width)))
    bounded = Image.new("L", (width, height), 0)
    bounded.paste(blocked.crop((left, 0, right, height)), (left, 0))
    whole_solid = whole.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    bounded = ImageChops.multiply(bounded, whole_solid.filter(ImageFilter.MaxFilter(9)))   # 꼬리·그림자 띠 쪽 순백 아닌 픽셀은 덩어리를 잇지 못하게

    span = whole_solid.getbbox()
    if span is None:
        raise ItemIsolationError("사진에서 상품을 찾지 못했습니다.")
    center_x = (left + right) // 2
    seed = _seed(bounded, center_x, span[1], span[3], (right - left) // 2)
    if seed is None:
        raise ItemIsolationError("고른 범위 안에 상품이 없습니다.")
    piece = _component(bounded, seed)

    best_twice = _axis_twice(piece, seed, left, right, max(2, int((right - left) * AXIS_SEARCH_SHARE)))
    item = ImageChops.multiply(piece, _mirror(piece, best_twice))

    item = wbc._pad_filter(item, TIDY_SIZE, close=True)                   # 자른 경계의 들쭉날쭉 메우기
    item = ImageChops.invert(wbc._outside(item))                          # 투명 용기 안쪽의 흰 빛 반사 구멍 채우기
    item = wbc._pad_filter(item, TIDY_SIZE, close=False)
    if item.getpixel(seed) != 255:
        raise ItemIsolationError("상품 모양을 정하지 못했습니다(범위를 상품 양옆 가장자리에 맞춰 다시 고르세요).")
    item = _component(item, seed)                                         # 떨어진 조각은 버린다
    if _count(item) < MIN_ITEM_SHARE * width * height:
        raise ItemIsolationError("떼어 낸 상품이 너무 작습니다(범위를 다시 고르세요).")

    soft = item.filter(ImageFilter.GaussianBlur(1.0)).point(lambda v: 0 if v < 64 else (255 if v >= 192 else (v - 64) * 2))
    alpha = ImageChops.darker(whole.getchannel("A"), soft)
    box = alpha.point(lambda v: 255 if v >= 128 else 0).getbbox()
    if box is None:
        raise ItemIsolationError("떼어 낸 상품이 비었습니다.")
    out_image = rgb.convert("RGBA")
    out_image.putalpha(alpha)
    out = io.BytesIO()
    out_image.save(out, format="PNG")
    return ItemIsolation(out.getvalue(), best_twice / 2, box)


__all__ = ["isolate_item", "ItemIsolation", "ItemIsolationError", "MODEL_VERSION", "MIN_RANGE_SHARE", "MIN_ITEM_SHARE"]
