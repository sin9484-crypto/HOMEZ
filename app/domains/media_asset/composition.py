"""
=========================================================
Homez OS

File : app/domains/media_asset/composition.py

실제 제품 누끼(투명 PNG) 위에 배경을 합성한다 — 제품 용기 자체를
AI로 다시 그리지 않는다(2026-08-20 CTO 지시). 순수 로컬 Pillow
연산만 사용하며 외부 네트워크 호출이 없다.

지원 배경 종류(BackgroundKind): 전부 코드로 생성하거나(SOLID/
GRADIENT/WHITE) 이미 검증된 기존 MediaAsset을 그대로 재사용하는
경우(EXISTING_ASSET)만 허용한다 — 임의 URL에서 배경 이미지를
내려받지 않는다(무단 이미지 재사용 금지 원칙과 동일하게 적용).
=========================================================
"""

from dataclasses import dataclass

from PIL import Image
from PIL import ImageChops
from PIL import ImageDraw
from PIL import ImageFilter

from app.core.exceptions import BadRequestException

CANVAS_SIZE = (1200, 1200)


class Placement:
    """누끼를 캔버스 어디에 어떻게 놓는가(2026-11-15 추가). 합성(`compose_with_background`)과 검사(`concept_inspection`)가
    같은 `plan_placement()`를 쓰므로, 제품 보존 검사가 언제나 실제로 붙인 자리를 잰다.

      - CENTER    기존 동작 그대로: 누끼 전체(투명 여백 포함)를 확대 없이 맞춰 가운데에 놓는다. 단색·그라데이션·질감 배경용.
      - GROUNDED  장면 배경(AI가 그린 배경)용: 누끼의 투명 여백을 잘라 낸 제품을 가운데에 세우고, 제품 밑면을 화면 아래쪽 바닥선에
                  맞춘다. 그 아래에 부드러운 바닥 그림자를 먼저 그린 뒤 제품을 붙인다 — 제품 픽셀은 그대로다(다시 그리지 않음).
                  확대는 여전히 하지 않는다(원본보다 크게 부풀리지 않는다)."""

    CENTER = "CENTER"
    GROUNDED = "GROUNDED"
    ALL = (CENTER, GROUNDED)


GROUND_LINE = 0.86          # GROUNDED: 제품 밑면이 놓이는 높이(캔버스 높이 대비) — AI 배경 장면 글이 아래쪽 30%를 빈 바닥으로 그리게 한다
GROUNDED_MAX_HEIGHT = 0.62  # GROUNDED: 제품 높이 상한(캔버스 높이 대비)
GROUNDED_MAX_WIDTH = 0.80   # GROUNDED: 제품 너비 상한(캔버스 너비 대비)
SHADOW_CONTACT_ALPHA = 120  # 바닥에 닿는 짙은 그림자(0~255)
SHADOW_SOFT_ALPHA = 46      # 제품 모양을 따라 뒤로 퍼지는 옅은 그림자(0~255)
SOLID_ALPHA_MIN = 250       # GROUNDED: 이 이상 불투명한 누끼 픽셀은 완전 불투명으로 본다(배경 지우기 결과는 제품 안쪽이 250~254로 남아 배경이 비친다)


def prepare_cutout(cutout: Image.Image, placement: str = Placement.CENTER) -> Image.Image:
    """놓기 전에 누끼를 다듬는다(합성과 검사가 함께 쓴다). CENTER는 그대로. GROUNDED는 거의 불투명한 픽셀의 알파만 255로 올린다 —
    제품 색(RGB)은 바꾸지 않으므로 '다시 그리지 않음' 원칙 그대로이고, 제품 보존 검사가 재는 픽셀도 그만큼 많아진다."""

    rgba = cutout.convert("RGBA")
    if placement != Placement.GROUNDED:
        return rgba
    red, green, blue, alpha = rgba.split()
    alpha = alpha.point(lambda a: 255 if a >= SOLID_ALPHA_MIN else a)
    return Image.merge("RGBA", (red, green, blue, alpha))


@dataclass(frozen=True)
class PlacementPlan:
    crop_box: tuple[int, int, int, int]   # 누끼에서 쓸 부분(투명 여백을 자른 상자 — CENTER는 전체)
    size: tuple[int, int]                 # 캔버스에 놓을 크기
    offset: tuple[int, int]               # 캔버스에서 왼쪽 위 자리


def plan_placement(cutout: Image.Image, canvas_size: tuple[int, int], placement: str = Placement.CENTER) -> PlacementPlan:
    """합성과 검사가 함께 쓰는 놓는 자리 계산(순수 계산)."""

    if placement not in Placement.ALL:
        raise BadRequestException(f"알 수 없는 놓는 방식입니다: {placement}")
    canvas_w, canvas_h = canvas_size
    if placement == Placement.CENTER:
        scale = min(canvas_w / cutout.width, canvas_h / cutout.height, 1.0)
        size = (max(1, int(cutout.width * scale)), max(1, int(cutout.height * scale)))
        offset = ((canvas_w - size[0]) // 2, (canvas_h - size[1]) // 2)
        return PlacementPlan((0, 0, cutout.width, cutout.height), size, offset)

    alpha = cutout.convert("RGBA").getchannel("A")
    box = alpha.getbbox() or (0, 0, cutout.width, cutout.height)
    trimmed_w, trimmed_h = box[2] - box[0], box[3] - box[1]
    scale = min(GROUNDED_MAX_WIDTH * canvas_w / trimmed_w, GROUNDED_MAX_HEIGHT * canvas_h / trimmed_h, 1.0)
    size = (max(1, int(trimmed_w * scale)), max(1, int(trimmed_h * scale)))
    base_y = int(canvas_h * GROUND_LINE)
    offset = ((canvas_w - size[0]) // 2, max(0, base_y - size[1]))
    return PlacementPlan(box, size, offset)


def _ground_shadow(canvas_size: tuple[int, int], product: Image.Image, offset: tuple[int, int]) -> Image.Image:
    """제품 아래 바닥 그림자(RGBA 층). 제품이 놓일 자리 밑에만 그린다 — 제품 위에는 제품이 덮는다."""

    width, height = product.size
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    base_y = offset[1] + height
    # 1) 바닥에 닿는 짙고 납작한 타원
    contact = Image.new("L", canvas_size, 0)
    rx, ry = max(2, int(width * 0.47)), max(2, int(height * 0.035))
    cx = offset[0] + width // 2
    ImageDraw.Draw(contact).ellipse((cx - rx, base_y - ry, cx + rx, base_y + ry), fill=SHADOW_CONTACT_ALPHA)
    contact = contact.filter(ImageFilter.GaussianBlur(max(2, int(height * 0.02))))
    # 2) 제품 모양을 납작하게 눌러 뒤쪽으로 눕힌 옅은 그림자
    silhouette = product.getchannel("A").point(lambda a: SHADOW_SOFT_ALPHA if a > 0 else 0)
    flat_h = max(2, int(height * 0.22))
    flat = silhouette.resize((width, flat_h), Image.LANCZOS)
    soft = Image.new("L", canvas_size, 0)
    soft.paste(flat, (offset[0] + int(width * 0.04), base_y - flat_h))
    soft = soft.filter(ImageFilter.GaussianBlur(max(3, int(height * 0.05))))
    layer.putalpha(ImageChops.lighter(contact, soft))   # 두 그림자 중 짙은 쪽
    return layer


class BackgroundKind:

    WHITE = "WHITE"
    SOLID_COLOR = "SOLID_COLOR"
    GRADIENT = "GRADIENT"
    EXISTING_ASSET = "EXISTING_ASSET"
    # 2026-10-23 이미지 AI I3 — 로컬 장면 배경(질감·조명, 생성이 아니다). scene_backgrounds.py가 코드로 그린다.
    SCENE = "SCENE"

    ALL = (WHITE, SOLID_COLOR, GRADIENT, EXISTING_ASSET, SCENE)


@dataclass(frozen=True)
class BackgroundSpec:

    kind: str
    color_hex: str | None = None  # SOLID_COLOR / GRADIENT 시작색
    color_hex_end: str | None = None  # GRADIENT 끝색
    existing_asset_bytes: bytes | None = None  # EXISTING_ASSET
    scene_code: str | None = None  # SCENE(color_hex는 질감의 바탕색, 조명은 색을 쓰지 않는다)


def _parse_hex_color(value: str) -> tuple[int, int, int]:

    v = value.lstrip("#")
    if len(v) != 6 or not all(c in "0123456789abcdefABCDEF" for c in v):
        raise BadRequestException(f"잘못된 색상 코드입니다: {value}")

    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _build_background(spec: BackgroundSpec, size: tuple[int, int]) -> Image.Image:

    if spec.kind == BackgroundKind.WHITE:
        return Image.new("RGB", size, (255, 255, 255))

    if spec.kind == BackgroundKind.SOLID_COLOR:
        if not spec.color_hex:
            raise BadRequestException("SOLID_COLOR 배경은 color_hex가 필요합니다.")

        return Image.new("RGB", size, _parse_hex_color(spec.color_hex))

    if spec.kind == BackgroundKind.GRADIENT:
        if not spec.color_hex or not spec.color_hex_end:
            raise BadRequestException(
                "GRADIENT 배경은 color_hex/color_hex_end가 모두 필요합니다.",
            )

        start = _parse_hex_color(spec.color_hex)
        end = _parse_hex_color(spec.color_hex_end)
        img = Image.new("RGB", size)
        draw = ImageDraw.Draw(img)
        height = size[1]
        for y in range(height):
            t = y / max(height - 1, 1)
            r = int(start[0] + (end[0] - start[0]) * t)
            g = int(start[1] + (end[1] - start[1]) * t)
            b = int(start[2] + (end[2] - start[2]) * t)
            draw.line([(0, y), (size[0], y)], fill=(r, g, b))

        return img

    if spec.kind == BackgroundKind.EXISTING_ASSET:
        if not spec.existing_asset_bytes:
            raise BadRequestException(
                "EXISTING_ASSET 배경은 existing_asset_bytes가 필요합니다.",
            )

        import io

        bg = Image.open(io.BytesIO(spec.existing_asset_bytes)).convert("RGB")

        return bg.resize(size)

    if spec.kind == BackgroundKind.SCENE:
        if not spec.scene_code:
            raise BadRequestException("SCENE 배경은 scene_code가 필요합니다.")
        from app.domains.media_asset import scene_backgrounds

        return scene_backgrounds.render_scene(spec.scene_code, size, spec.color_hex or "#E8DFD0")

    raise BadRequestException(f"알 수 없는 배경 종류입니다: {spec.kind}")


def compose_with_background(
    cutout_png_bytes: bytes, spec: BackgroundSpec,
    canvas_size: tuple[int, int] = CANVAS_SIZE,
    *, placement: str = Placement.CENTER,
) -> bytes:
    """
    투명 PNG(제품 누끼) + 배경 → 합성된 PNG 바이트.

    누끼 이미지는 원본 비율을 유지한 채 캔버스 안에 맞춰 축소만
    한다(확대하지 않음 — 원본보다 큰 합성 이미지를 만들어 화질을
    거짓으로 부풀리지 않기 위함). 기본(CENTER)은 중앙 정렬.
    GROUNDED(장면 배경용, 2026-11-15)는 투명 여백을 자른 제품을 바닥선에 세우고
    제품을 붙이기 **전에** 바닥 그림자를 그린다 — 제품 픽셀은 바뀌지 않는다.
    """

    import io

    cutout = prepare_cutout(Image.open(io.BytesIO(cutout_png_bytes)), placement)

    background = _build_background(spec, canvas_size).convert("RGBA")

    plan = plan_placement(cutout, canvas_size, placement)
    resized = cutout.crop(plan.crop_box).resize(plan.size, Image.LANCZOS)
    if placement == Placement.GROUNDED:
        background.alpha_composite(_ground_shadow(canvas_size, resized, plan.offset))
    background.alpha_composite(resized, dest=plan.offset)

    out = io.BytesIO()
    background.convert("RGB").save(out, format="PNG")

    return out.getvalue()


__all__ = [
    "BackgroundKind",
    "BackgroundSpec",
    "compose_with_background",
    "CANVAS_SIZE",
    "Placement",
    "PlacementPlan",
    "plan_placement",
    "prepare_cutout",
    "GROUND_LINE",
    "SOLID_ALPHA_MIN",
]
