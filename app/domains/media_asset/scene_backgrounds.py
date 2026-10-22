"""
=========================================================
Homez OS

File : app/domains/media_asset/scene_backgrounds.py

2026-10-23 — 이미지 AI I3: **로컬 장면 배경(L1 — 생성이 아니다)**. 질감(린넨·나무·종이)과 조명(따뜻한·차가운 스포트라이트)을 Pillow로 **코드가 직접 그린다**. 외부 호출·AI·모델·파일 읽기 없음.

  - **결정적**이다: 같은 코드·같은 크기·같은 색이면 항상 같은 바이트가 나온다(고정 시드의 `random.Random`, 이미지 처리는 Pillow 내장 연산). 같은 사진·같은 컨셉을 다시 만들면 같은 자산으로 합쳐진다.
  - **제품은 다시 그리지 않는다** — 이 모듈은 배경만 만든다. 제품(누끼)을 올리는 일은 `composition.compose_with_background`가 하고, 제품 픽셀이 그대로인지는 `concept_runs`의 검사가 확인한다.
  - 배경에는 **글자·로고·워터마크·표식이 없다**(질감은 무늬일 뿐).
  - 이 질감은 **대표(MAIN) 이미지에 쓰지 않는다**(대표는 흰 배경만 — I1 규칙). 추가·상세 이미지용이다.
=========================================================
"""

from __future__ import annotations

import random

from PIL import Image
from PIL import ImageChops
from PIL import ImageFilter
from PIL import ImageOps

from app.core.exceptions import BadRequestException

LINEN, WOOD, PAPER, SPOTLIGHT_WARM, SPOTLIGHT_COOL = "LINEN", "WOOD", "PAPER", "SPOTLIGHT_WARM", "SPOTLIGHT_COOL"
TEXTURES = (LINEN, WOOD, PAPER)
LIGHTS = (SPOTLIGHT_WARM, SPOTLIGHT_COOL)
SCENE_CODES = TEXTURES + LIGHTS

MIN_SIDE, MAX_SIDE = 16, 4096
_SEED = {LINEN: 11, WOOD: 23, PAPER: 37, SPOTLIGHT_WARM: 41, SPOTLIGHT_COOL: 53}

# 질감 무늬의 세기(밝기 배율의 흔들림 크기). 작을수록 제품이 돋보인다 — 이 값 안에서만 변한다(시험이 모든 픽셀을 이 범위로 확인).
TEXTURE_STRENGTH = {LINEN: 0.14, WOOD: 0.16, PAPER: 0.05}

_LIGHT_COLORS = {
    SPOTLIGHT_WARM: ((255, 244, 225), (205, 185, 160)),    # 가운데, 가장자리
    SPOTLIGHT_COOL: ((240, 246, 255), (170, 185, 205)),
}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = (value or "").lstrip("#")
    if len(v) != 6 or not all(c in "0123456789abcdefABCDEF" for c in v):
        raise BadRequestException(f"잘못된 색상 코드입니다: {value}")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _noise(size: tuple[int, int], seed: int) -> Image.Image:
    width, height = size
    return Image.frombytes("L", size, random.Random(seed).randbytes(width * height))


def _check_size(size: tuple[int, int]) -> tuple[int, int]:
    if not (isinstance(size, tuple) and len(size) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in size)):
        raise BadRequestException("배경 크기는 정수 두 개여야 합니다.")
    if not all(MIN_SIDE <= v <= MAX_SIDE for v in size):
        raise BadRequestException(f"배경 크기는 {MIN_SIDE}~{MAX_SIDE}픽셀 사이여야 합니다.")
    return size


def _modulate(base: tuple[int, int, int], pattern: Image.Image, strength: float) -> Image.Image:
    """바탕색에 무늬(0~255 회색조)를 곱해 얹는다. 배율은 1 − strength ~ 1 + strength 안에서만 변한다."""

    low, high = 1.0 - strength, 1.0 + strength
    # 무늬 값 v(0~255)를 배율 low + (high − low) × v/255로 바꾸는 조회표(채널마다 바탕색을 곱한다)
    bands = []
    for channel in base:
        table = [min(255, max(0, int(channel * (low + (high - low) * v / 255.0) + 0.5))) for v in range(256)]
        bands.append(pattern.point(table))
    return Image.merge("RGB", bands)


def render_scene(code: str, size: tuple[int, int] = (1200, 1200), base_hex: str = "#E8DFD0") -> Image.Image:
    """장면 배경 한 장(RGB). 질감은 `base_hex` 바탕색에 무늬를 얹고, 조명은 정해진 두 색 사이를 가운데에서 가장자리로 칠한다(조명은 `base_hex`를 쓰지 않는다)."""

    if code not in SCENE_CODES:
        raise BadRequestException(f"알 수 없는 장면 배경입니다: {code}")
    width, height = _check_size(size)
    seed = _SEED[code]

    if code in LIGHTS:
        center, edge = _LIGHT_COLORS[code]
        gradient = Image.radial_gradient("L").resize((width, height), Image.BICUBIC)   # 가운데 0 → 가장자리 255
        return Image.composite(Image.new("RGB", (width, height), edge), Image.new("RGB", (width, height), center), gradient)

    base = _hex_to_rgb(base_hex)
    if code == PAPER:
        pattern = _noise((width, height), seed).filter(ImageFilter.GaussianBlur(0.8))
    elif code == LINEN:
        # 가로·세로 실이 엇갈린 천: 가는 줄무늬 두 장을 곱하고 잔 노이즈를 살짝 섞는다.
        period = max(3, width // 200)
        horizontal = Image.new("L", (1, height))
        horizontal.putdata([190 if (y // (period // 2 or 1)) % 2 else 255 for y in range(height)])
        vertical = Image.new("L", (width, 1))
        vertical.putdata([200 if (x // (period // 2 or 1)) % 2 else 255 for x in range(width)])
        weave = ImageChops.multiply(horizontal.resize((width, height)), vertical.resize((width, height)))
        pattern = ImageChops.blend(ImageOps.autocontrast(weave), _noise((width, height), seed).filter(ImageFilter.GaussianBlur(0.6)), 0.35)
    else:  # WOOD — 가로 결: 세로 방향으로 길게 늘린 저해상 노이즈(굵은 결) + 가로로 번진 잔 노이즈
        coarse = _noise((1, max(4, height // 24)), seed).resize((width, height), Image.BICUBIC)
        fine = _noise((width, height), seed + 1).filter(ImageFilter.GaussianBlur(1.2)).resize((width, height))
        fine = fine.resize((max(1, width // 40), height), Image.BILINEAR).resize((width, height), Image.BICUBIC)
        pattern = ImageChops.blend(coarse, fine, 0.45)
    return _modulate(base, ImageOps.autocontrast(pattern), TEXTURE_STRENGTH[code])


def average_color(image: Image.Image) -> tuple[int, int, int]:
    """배경의 평균 색 — 컨셉 명세의 글자 대비 계산과 제품 대비 검사에 쓴다."""
    small = image.convert("RGB").resize((1, 1), Image.BOX)
    return small.getpixel((0, 0))


__all__ = ["render_scene", "average_color", "SCENE_CODES", "TEXTURES", "LIGHTS", "TEXTURE_STRENGTH", "MIN_SIDE", "MAX_SIDE", "LINEN", "WOOD", "PAPER", "SPOTLIGHT_WARM", "SPOTLIGHT_COOL"]
