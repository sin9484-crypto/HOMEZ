"""
=========================================================
Homez OS

File : app/domains/media_asset/white_background_cutout.py

2026-11-25 — 사용자 "상품에 구도나 모든것이 부적합함 다시 만들어라". **흰 배경 상품 사진 전용 누끼**(PC 안 Pillow 연산만, 바깥 호출 없음).

왜: 공급처 상품 사진은 대개 순백(255) 배경이다. 작은 학습 모델(u2netp)은 흰 용기·투명 용기를 흰 배경과 구분하지 못해
펌프·분무기 머리를 지우고 밑부분을 반투명으로 남겼다(#137 — 반투명 픽셀 16%, 밑 20여 줄이 흐려져 상품이 떠 보임).
순백 배경은 픽셀 값이 정확히 255라서, 테두리에서 이어진 "거의 순백" 영역만 배경으로 보면 흰 용기의 옅은 윤곽선도 상품으로 남는다.

  - **쓰는 조건**: 사진 테두리의 98% 이상이 순백(가장 어두운 채널 ≥ 254)일 때만. 아니면 None — 부르는 쪽이 원래 방법(학습 모델)을 쓴다.
  - **색은 그대로**: 픽셀 색(RGB)은 원본 그대로이고 투명도(알파)만 정한다 — "상품을 다시 그리지 않음" 원칙.
  - **순서**: 테두리에서 이어진 순백 영역 = 배경 → 윤곽선 틈 메우기(닫기) → 안쪽 구멍 채우기(흰 라벨·흰 뚜껑) → 작은 돌기 정리(열기)
    → 밑에 붙은 바닥 그림자·흰색 흐림 꼬리 잘라 내기(열마다) → 옆에 붙어 몸통 밑선보다 아래로 내려갔던 옅은 그림자 띠 지우기 → 다시 작은 돌기 정리 → 가장자리 1~2픽셀 부드럽게.
=========================================================
"""

from __future__ import annotations

import io

from PIL import Image
from PIL import ImageChops
from PIL import ImageDraw
from PIL import ImageFilter
from PIL import ImageStat

MODEL_VERSION = "white-background-v1"
WHITE_MIN = 252              # 가장 어두운 채널이 이 값 이상이면 "순백"(254는 용기 사이의 옅은 틈을 상품으로 남겼고, 250은 흰 뚜껑을 지웠다)
BORDER_WHITE_SHARE = 0.98    # 테두리 픽셀 중 순백 비율이 이 이상이어야 흰 배경 사진으로 본다
PRODUCT_SHARE_RANGE = (0.03, 0.95)   # 상품 영역이 사진의 3~95%가 아니면 흰 배경 방법을 쓰지 않는다
FADE_END = 6                 # 꼬리의 맨 밑은 이 진하기 이하(거의 흰색)여야 한다
TAIL_NOISE = 2               # 꼬리를 따라 올라갈 때 허용하는 진하기 잡음
TAIL_MIN = 6                 # 이보다 짧은 꼬리(가장자리 번짐)는 자르지 않는다
TAIL_MAX_SHARE = 0.12        # 상품 크기의 12%보다 긴 꼬리는 상품 일부일 수 있어 자르지 않는다
SIDE_FAINT_MAX = 25          # 옆 그림자 띠: 열 전체에서 가장 진한 곳이 이보다 옅다
SIDE_MAX_SHARE = 0.08        # 옆 그림자 띠는 상품 너비의 8%까지만 지운다
TAIL_RISE = 5                # 꼬리는 바깥(거의 흰색)에서 안쪽 끝까지 이만큼 이상 진해져야 한다 — 평평한 흰 몸통은 꼬리가 아니다
CLOSE_FRACTION = 0.028       # 윤곽선 틈 메우기 크기(짧은 변 대비) — 600픽셀이면 17
OPEN_SIZE = 5


def _odd(n: int) -> int:
    n = max(3, int(n))
    return n if n % 2 else n + 1


def _darkness(rgb: Image.Image) -> Image.Image:
    """픽셀마다 255 - (세 채널 중 가장 작은 값). 순백은 0, 진할수록 크다."""

    r, g, b = rgb.split()
    return ImageChops.invert(ImageChops.darker(ImageChops.darker(r, g), b))


def _border_white_share(dark: Image.Image) -> float:
    w, h = dark.size
    strips = [dark.crop((0, 0, w, 1)), dark.crop((0, h - 1, w, h)), dark.crop((0, 0, 1, h)), dark.crop((w - 1, 0, w, h))]
    limit = 255 - WHITE_MIN
    white = total = 0
    for strip in strips:
        hist = strip.histogram()
        white += sum(hist[: limit + 1])
        total += sum(hist)
    return white / total if total else 0.0


def _outside(mask: Image.Image) -> Image.Image:
    """mask(255=막힘, 0=열림)에서 바깥 테두리와 이어진 열린 영역 → 255. 1픽셀 여백을 두고 모서리에서 채운다."""

    w, h = mask.size
    padded = Image.new("L", (w + 2, h + 2), 0)
    padded.paste(mask, (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 128, thresh=0)
    return padded.crop((1, 1, w + 1, h + 1)).point(lambda v: 255 if v == 128 else 0)


def _pad_filter(mask: Image.Image, size: int, *, close: bool) -> Image.Image:
    """닫기(넓혔다 줄이기) 또는 열기(줄였다 넓히기). 가장자리 효과를 막으려고 여백을 두고 한다."""

    pad = size
    w, h = mask.size
    padded = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    padded.paste(mask, (pad, pad))
    first, second = (ImageFilter.MaxFilter(size), ImageFilter.MinFilter(size)) if close else (ImageFilter.MinFilter(size), ImageFilter.MaxFilter(size))
    return padded.filter(first).filter(second).crop((pad, pad, pad + w, pad + h))


def _trim_faded_bottom(mask: Image.Image, dark: Image.Image) -> Image.Image:
    """열마다, 상품 밑에 붙은 **아래로 갈수록 흰색으로 옅어지는 꼬리**(원본 사진의 바닥 그림자·반사·흰색 흐림 처리)를 지운다.

    실제 용기 밑면은 윤곽선에서 바로 끝나지만, 꼬리는 여러 줄에 걸쳐 흰색까지 조금씩 옅어진다. 밑에서 상품 크기의 TAIL_MAX_SHARE만큼의 창에서
    가장 진한 곳(맨 위쪽)을 찾고, 거기서 밑까지 계속 옅어지면(잡음 TAIL_NOISE 허용) 그 아래를 지운다. 다음을 모두 만족할 때만 —
    맨 밑이 거의 흰색(FADE_END 이하) · TAIL_MIN줄 이상 · TAIL_RISE 이상 진해짐. 중간에 다시 진해지는 열(용기 밑 윤곽·글자)은 그대로 둔다."""

    box = mask.getbbox()
    if box is None:
        return mask
    height = box[3] - box[1]
    max_tail = max(TAIL_MIN, int(height * TAIL_MAX_SHARE))
    smooth = dark.filter(ImageFilter.BoxBlur(1))
    trimmed = mask.copy()
    draw = ImageDraw.Draw(trimmed)
    for x in range(box[0], box[2]):
        column = mask.crop((x, 0, x + 1, mask.height)).getbbox()
        if column is None:
            continue
        bottom = column[3] - 1
        values = list(smooth.crop((x, 0, x + 1, bottom + 1)).tobytes())
        if values[bottom] > FADE_END:
            continue
        start = max(column[1], bottom - max_tail + 1)
        window = values[start:bottom + 1]
        peak = max(window)
        y = start + next(i for i, v in enumerate(window) if v >= peak - TAIL_NOISE)      # 창에서 가장 진한 곳(맨 위쪽)
        fading = all(values[k + 1] <= values[k] + TAIL_NOISE for k in range(y, bottom))   # 거기서 밑까지 계속 옅어진다
        if fading and bottom - y + 1 >= TAIL_MIN and values[y] - values[bottom] >= TAIL_RISE:
            draw.line((x, y, x, mask.height), fill=0)
    return trimmed


def _trim_faint_side_strips(mask: Image.Image, dark: Image.Image, untrimmed: Image.Image) -> Image.Image:
    """상품 맨 왼쪽·오른쪽에 붙은 **옅은 그림자 띠**를 지운다. 바깥 가장자리부터 안쪽으로, 다음 둘을 모두 만족하는 열만 연달아 지운다:
    열 전체가 옅고(가장 진한 곳 < SIDE_FAINT_MAX) · 밑 꼬리를 자르기 전(`untrimmed`)에 상품 몸통의 밑선(자른 뒤 열 밑끝의 가운데값)보다
    TAIL_MIN줄 넘게 아래로 내려갔다(그림자 띠는 바닥 그림자와 이어져 있다).
    흰 용기의 실제 옆면은 몸통 밑선에서 끝나므로 남는다. 지우는 폭은 상품 너비의 SIDE_MAX_SHARE까지."""

    box = mask.getbbox()
    if box is None:
        return mask
    smooth = dark.filter(ImageFilter.BoxBlur(1))
    columns = {}
    for x in range(box[0], box[2]):
        span = mask.crop((x, 0, x + 1, mask.height)).getbbox()
        before = untrimmed.crop((x, 0, x + 1, untrimmed.height)).getbbox()
        if span is not None and before is not None:
            columns[x] = (span[3], before[3], max(smooth.crop((x, span[1], x + 1, span[3])).tobytes()))
    if not columns:
        return mask
    bottoms = sorted(b for b, _, _ in columns.values())
    base = bottoms[len(bottoms) // 2]
    limit = max(1, int((box[2] - box[0]) * SIDE_MAX_SHARE))
    trimmed = mask.copy()
    draw = ImageDraw.Draw(trimmed)
    for order in (range(box[0], box[2]), range(box[2] - 1, box[0] - 1, -1)):
        removed = 0
        for x in order:
            if removed >= limit or x not in columns:
                break
            _, bottom_before, darkest = columns[x]
            if darkest >= SIDE_FAINT_MAX or bottom_before <= base + TAIL_MIN:
                break
            draw.line((x, 0, x, mask.height), fill=0)
            removed += 1
    return trimmed


def cut_white_background(image_bytes: bytes) -> bytes | None:
    """흰 배경 상품 사진 → 투명 PNG(색은 원본 그대로). 흰 배경 사진이 아니거나 상품 영역이 이상하면 None."""

    source = Image.open(io.BytesIO(image_bytes))
    rgb = source.convert("RGB")
    dark = _darkness(rgb)
    if _border_white_share(dark) < BORDER_WHITE_SHARE:
        return None

    limit = 255 - WHITE_MIN
    blocked = dark.point(lambda v: 0 if v <= limit else 255)          # 순백=열림(0), 그 밖=막힘(255)
    product = ImageChops.invert(_outside(blocked))                      # 테두리와 이어진 순백만 배경
    product = _pad_filter(product, _odd(min(rgb.size) * CLOSE_FRACTION), close=True)   # 흰 용기 윤곽선의 틈 메우기
    product = ImageChops.invert(_outside(product))                      # 안쪽 구멍(흰 라벨·흰 뚜껑) 채우기
    product = _pad_filter(product, OPEN_SIZE, close=False)              # 작은 돌기·티끌 정리
    before_bottom = product
    product = _trim_faded_bottom(product, dark)
    product = _trim_faint_side_strips(product, dark, before_bottom)     # 옆 그림자 띠: 밑을 자르기 전에 몸통 밑선보다 아래로 내려갔던 옅은 열
    product = _pad_filter(product, OPEN_SIZE, close=False)              # 열마다 자른 밑선의 들쭉날쭉 정리

    share = product.histogram()[255] / (rgb.width * rgb.height)
    if not (PRODUCT_SHARE_RANGE[0] <= share <= PRODUCT_SHARE_RANGE[1]):
        return None

    alpha = product.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: 0 if v < 64 else (255 if v >= 192 else (v - 64) * 2))
    out_image = rgb.convert("RGBA")
    out_image.putalpha(alpha)
    out = io.BytesIO()
    out_image.save(out, format="PNG")
    return out.getvalue()


__all__ = ["cut_white_background", "MODEL_VERSION", "WHITE_MIN", "BORDER_WHITE_SHARE", "FADE_END", "TAIL_MIN"]
