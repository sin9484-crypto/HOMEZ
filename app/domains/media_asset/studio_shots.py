"""
=========================================================
Homez OS

File : app/domains/media_asset/studio_shots.py

2026-11-26 — **촬영장 화보(스튜디오 컷)**. 사용자 "다시 이게 최선이냐" → "지시를 원하지말고 스스로 생각해라 피카소라고 생각하고".

판단: 투명한 병을 생활 장면(AI 배경)에 붙이면 굴절·반사가 없어 늘 오려 붙인 티가 난다. 반대로 색이 있는 촬영장 배경에서는 투명한 몸통이
그 색을 머금어 가장 깨끗하게 보인다. 그래서 판매 사진의 중심을 촬영장 화보로 옮긴다(생활 장면은 스토리 사진에만).

  - 모두 이 PC 안 Pillow 계산이다(바깥 호출·AI 없음). 상품은 다시 그리지 않는다 — 실제 누끼를 그대로 붙이고(알파 255 픽셀은 원본 그대로),
    반사·그림자·배경만 그린다. 보존 검사를 같이 돌려 1.0이 아니면 쓰지 않는다.
  - 1,000×1,000 캔버스, 상품 높이 67%(최대 1.4배 — 600px 원본의 화질 한도), 밑면은 높이 80%(아래에 반사가 보이게).
  - 화보 세 가지(브랜드 물방울의 하늘빛을 축으로):
      AQUA_REFLECT   하늘빛 그라데이션 벽과 윤나는 바닥, 상품 뒤 은은한 빛무리, 바닥에 비치는 반사
      TWO_TONE_SUN   크림·하늘빛 두 면을 나눈 벽과 상품 뒤 큰 해(원) — 기하학적 구성, 그림자는 오른쪽 앞
      WINDOW_SHADOW  따뜻한 벽에 창살 빛줄기, 빛은 오른쪽 위 — 그림자는 왼쪽 앞으로 길게
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from PIL import ImageChops
from PIL import ImageDraw
from PIL import ImageFilter

from app.domains.media_asset import composition as comp

CANVAS = (1000, 1000)
PRODUCT_HEIGHT = 0.67
PRODUCT_MAX_WIDTH = 0.70
MAX_UPSCALE = comp.GROUNDED_MAX_UPSCALE
FLOOR_LINE = 0.80
REFLECTION_ALPHA = 0.42      # 바닥 반사의 시작 진하기(상품 밑면 바로 아래)
REFLECTION_LENGTH = 0.32     # 반사 길이(상품 높이 대비)

AQUA_REFLECT, TWO_TONE_SUN, WINDOW_SHADOW, PODIUM_ARCH = "AQUA_REFLECT", "TWO_TONE_SUN", "WINDOW_SHADOW", "PODIUM_ARCH"
STYLES = (AQUA_REFLECT, TWO_TONE_SUN, WINDOW_SHADOW, PODIUM_ARCH)
LABELS_KO = {AQUA_REFLECT: "하늘빛 반사", TWO_TONE_SUN: "투톤과 해", WINDOW_SHADOW: "창살 빛", PODIUM_ARCH: "받침대와 아치"}
# 2026-11-26(사용자 "#2#4 다시 만들어라"): 투톤과 해(#206)는 노란 원이 투명 몸통에 비쳐 병이 탁한 노란빛이 되고, 뒤 사선이 시선을 빼앗았다 →
# 화장품 화보의 정석인 둥근 받침대 + 병 전체를 감싸는 하늘빛 아치(몸통이 한 가지 색만 머금는다)로 바꾼다. 받침대 위라 밑면·높이가 다르다.
_PLACE = {PODIUM_ARCH: (0.76, 0.60)}     # 스타일별 (밑면 높이, 상품 높이) — 없으면 (FLOOR_LINE, PRODUCT_HEIGHT)
# 스타일마다 빛(방향·세기) — 그림자 방향을 정한다(상품 색에는 빛 맞춤을 하지 않는다: 촬영장 빛은 중립).
_LIGHT = {AQUA_REFLECT: comp.SceneLight(side=0, strength=0.45), TWO_TONE_SUN: comp.SceneLight(side=-1, strength=0.6),
          WINDOW_SHADOW: comp.SceneLight(side=1, strength=0.9), PODIUM_ARCH: comp.SceneLight(side=0, strength=0.55)}


class StudioError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 실패 사유."""


@dataclass(frozen=True)
class StudioShot:
    style: str
    png_bytes: bytes
    preservation: float
    measured_pixels: int


def _vertical(size, top, bottom) -> Image.Image:
    w, h = size
    column = Image.new("RGB", (1, h))
    column.putdata([tuple(int(round(top[c] + (bottom[c] - top[c]) * (y / max(1, h - 1)))) for c in range(3)) for y in range(h)])
    return column.resize((w, h), Image.NEAREST)


def _glow(base: Image.Image, centre, radius, colour, strength) -> Image.Image:
    mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(mask).ellipse((centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius), fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(radius * 0.45))
    return Image.composite(Image.new("RGB", base.size, colour), base, mask)


def background(style: str, size=CANVAS) -> Image.Image:
    w, h = size
    floor_y = int(h * FLOOR_LINE)
    if style == AQUA_REFLECT:
        image = _vertical(size, (246, 251, 253), (206, 234, 244))
        image.paste(_vertical((w, h - floor_y), (184, 222, 236), (232, 245, 250)), (0, floor_y))
        image = _glow(image, (w // 2, int(h * 0.45)), int(w * 0.36), (255, 255, 255), 0.85)
        line = Image.new("L", size, 0)
        ImageDraw.Draw(line).line((0, floor_y, w, floor_y), fill=90, width=2)
        image = Image.composite(Image.new("RGB", size, (170, 210, 226)), image, line.filter(ImageFilter.GaussianBlur(2)))
        return image
    if style == TWO_TONE_SUN:
        image = Image.new("RGB", size, (242, 233, 218))
        ImageDraw.Draw(image).polygon([(int(w * 0.52), 0), (w, 0), (w, floor_y), (int(w * 0.40), floor_y)], fill=(190, 226, 238))
        sun = Image.new("L", size, 0)
        r = int(w * 0.27)
        ImageDraw.Draw(sun).ellipse((w // 2 - r, int(h * 0.40) - r, w // 2 + r, int(h * 0.40) + r), fill=255)
        image = Image.composite(Image.new("RGB", size, (247, 214, 160)), image, sun.filter(ImageFilter.GaussianBlur(1)))
        floor = Image.new("RGB", (w, h - floor_y), (231, 220, 203))
        ImageDraw.Draw(floor).polygon([(int(w * 0.40), 0), (w, 0), (w, h - floor_y), (int(w * 0.30), h - floor_y)], fill=(168, 208, 223))
        image.paste(floor, (0, floor_y))
        return image
    if style == WINDOW_SHADOW:
        image = _vertical(size, (243, 233, 218), (233, 219, 199))
        light = Image.new("L", size, 0)
        draw = ImageDraw.Draw(light)
        for i in range(4):                                                     # 오른쪽 위 창에서 비스듬히 드는 빛줄기 4개
            x = int(w * (0.30 + i * 0.17))
            draw.polygon([(x, 0), (x + int(w * 0.11), 0), (x - int(w * 0.30), floor_y), (x - int(w * 0.41), floor_y)], fill=150)
        image = Image.composite(Image.new("RGB", size, (255, 248, 234)), image, light.filter(ImageFilter.GaussianBlur(w * 0.012)))
        image.paste(_vertical((w, h - floor_y), (226, 210, 186), (238, 226, 207)), (0, floor_y))
        return image
    if style == PODIUM_ARCH:
        image = _vertical(size, (247, 241, 232), (236, 226, 211))
        arch_w, top, bottom = int(w * 0.44), int(h * 0.11), int(h * 0.86)
        arch = Image.new("L", size, 0)
        ImageDraw.Draw(arch).rounded_rectangle((w // 2 - arch_w // 2, top, w // 2 + arch_w // 2, bottom), radius=arch_w // 2, fill=255)
        image = Image.composite(_vertical(size, (206, 233, 243), (180, 219, 234)), image, arch.filter(ImageFilter.GaussianBlur(1)))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, bottom, w, h), fill=(231, 219, 202))                     # 바닥
        cx, cy, rx, ry = w // 2, int(h * 0.76), int(w * 0.21), int(h * 0.028)
        body_bottom = int(h * 0.93)
        side = _vertical((2 * rx, body_bottom - cy), (250, 248, 244), (226, 220, 211))   # 받침대 앞면(위 밝고 아래 어둡게)
        shade = Image.linear_gradient("L").rotate(90).resize((2 * rx, body_bottom - cy))
        shade = shade.point(lambda v: int(40 * abs(v - 128) / 128))                  # 양옆이 둥글게 어둡다
        side = Image.composite(Image.new("RGB", side.size, (196, 188, 176)), side, shade)
        face = Image.new("L", (2 * rx, body_bottom - cy + ry), 0)
        ImageDraw.Draw(face).rectangle((0, 0, 2 * rx, body_bottom - cy), fill=255)
        ImageDraw.Draw(face).ellipse((0, body_bottom - cy - ry, 2 * rx, body_bottom - cy + ry), fill=255)
        side_full = Image.new("RGB", face.size, (226, 220, 211))
        side_full.paste(side, (0, 0))
        image.paste(side_full, (cx - rx, cy), face)
        floor_shadow = Image.new("L", size, 0)
        ImageDraw.Draw(floor_shadow).ellipse((cx - rx - 20, body_bottom - 10, cx + rx + 30, body_bottom + ry + 22), fill=90)
        image = Image.composite(Image.new("RGB", size, (190, 176, 158)), image, floor_shadow.filter(ImageFilter.GaussianBlur(14)))
        image.paste(side_full, (cx - rx, cy), face)
        ImageDraw.Draw(image).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(252, 250, 247), outline=(232, 226, 218), width=2)   # 받침대 윗면
        return image
    raise StudioError(f"알 수 없는 화보 스타일입니다: {style}")


def place(cutout: Image.Image, size=CANVAS, *, floor: float = FLOOR_LINE, height: float = PRODUCT_HEIGHT) -> tuple[Image.Image, tuple[int, int], tuple[int, int, int, int]]:
    """투명 여백을 잘라 가운데·밑면 floor(기본 80%)에 놓을 크기(높이 height, 최대 1.4배)·자리."""

    rgba = comp.prepare_cutout(cutout, comp.Placement.GROUNDED)
    box = rgba.getchannel("A").getbbox()
    if box is None:
        raise StudioError("누끼에서 상품을 찾지 못했습니다.")
    tw, th = box[2] - box[0], box[3] - box[1]
    scale = min(height * size[1] / th, PRODUCT_MAX_WIDTH * size[0] / tw, MAX_UPSCALE)
    size_px = (max(1, int(tw * scale)), max(1, int(th * scale)))
    product = comp.scaled_product(rgba, comp.PlacementPlan(box, size_px, (0, 0)))    # 키웠으면 같은 선명 처리
    offset = ((size[0] - product.width) // 2, int(size[1] * floor) - product.height)
    return product, offset, box


def _reflection(product: Image.Image) -> Image.Image:
    w, h = product.size
    length = max(2, int(h * REFLECTION_LENGTH))
    flipped = product.transpose(Image.FLIP_TOP_BOTTOM).crop((0, 0, w, length))
    fade = Image.linear_gradient("L").resize((w, length)).point(lambda v: int(255 * REFLECTION_ALPHA * (1 - v / 255) ** 1.6))
    alpha = ImageChops.multiply(flipped.getchannel("A"), fade)
    flipped.putalpha(alpha)
    return flipped.filter(ImageFilter.GaussianBlur(1.2))


def compose(cutout_png: bytes, style: str) -> StudioShot:
    if style not in STYLES:
        raise StudioError(f"알 수 없는 화보 스타일입니다: {style}")
    cutout = Image.open(io.BytesIO(cutout_png))
    floor, height = _PLACE.get(style, (FLOOR_LINE, PRODUCT_HEIGHT))
    product, offset, _box = place(cutout, floor=floor, height=height)
    canvas = background(style).convert("RGBA")
    canvas.alpha_composite(comp._ground_shadow(CANVAS, product, offset, _LIGHT[style]))   # noqa: SLF001 — 장면 합성과 같은 그림자
    if style == AQUA_REFLECT:
        canvas.alpha_composite(_reflection(product), dest=(offset[0], offset[1] + product.height))
    canvas.alpha_composite(product, dest=offset)
    out = canvas.convert("RGB")
    ratio, pixels = preservation(product, offset, out)
    if ratio < 1.0:
        raise StudioError("상품 픽셀이 원본과 달라져 쓰지 않습니다(보존 검사 실패).")
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    return StudioShot(style, buffer.getvalue(), ratio, pixels)


def preservation(product: Image.Image, offset, composed: Image.Image) -> tuple[float, int]:
    """상품의 완전 불투명 픽셀이 결과에서 그대로인지(비율, 잰 픽셀 수)."""

    region = composed.crop((offset[0], offset[1], offset[0] + product.width, offset[1] + product.height))
    same = total = 0
    for original, got, a in zip(product.convert("RGB").getdata(), region.getdata(), product.getchannel("A").getdata()):
        if a == 255:
            total += 1
            same += original == got
    return (same / total if total else 0.0), total


__all__ = ["STYLES", "LABELS_KO", "AQUA_REFLECT", "TWO_TONE_SUN", "WINDOW_SHADOW", "PODIUM_ARCH", "StudioShot", "StudioError", "background", "place", "compose",
           "preservation", "CANVAS"]
