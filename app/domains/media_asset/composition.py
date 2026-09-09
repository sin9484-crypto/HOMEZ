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
from PIL import ImageDraw

from app.core.exceptions import BadRequestException

CANVAS_SIZE = (1200, 1200)


class BackgroundKind:

    WHITE = "WHITE"
    SOLID_COLOR = "SOLID_COLOR"
    GRADIENT = "GRADIENT"
    EXISTING_ASSET = "EXISTING_ASSET"

    ALL = (WHITE, SOLID_COLOR, GRADIENT, EXISTING_ASSET)


@dataclass(frozen=True)
class BackgroundSpec:

    kind: str
    color_hex: str | None = None  # SOLID_COLOR / GRADIENT 시작색
    color_hex_end: str | None = None  # GRADIENT 끝색
    existing_asset_bytes: bytes | None = None  # EXISTING_ASSET


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

    raise BadRequestException(f"알 수 없는 배경 종류입니다: {spec.kind}")


def compose_with_background(
    cutout_png_bytes: bytes, spec: BackgroundSpec,
    canvas_size: tuple[int, int] = CANVAS_SIZE,
) -> bytes:
    """
    투명 PNG(제품 누끼) + 배경 → 합성된 PNG 바이트.

    누끼 이미지는 원본 비율을 유지한 채 캔버스 안에 맞춰 축소만
    한다(확대하지 않음 — 원본보다 큰 합성 이미지를 만들어 화질을
    거짓으로 부풀리지 않기 위함). 항상 중앙 정렬.
    """

    import io

    cutout = Image.open(io.BytesIO(cutout_png_bytes)).convert("RGBA")

    background = _build_background(spec, canvas_size).convert("RGBA")

    scale = min(
        canvas_size[0] / cutout.width, canvas_size[1] / cutout.height, 1.0,
    )
    new_size = (
        max(1, int(cutout.width * scale)),
        max(1, int(cutout.height * scale)),
    )
    resized = cutout.resize(new_size, Image.LANCZOS)

    offset = (
        (canvas_size[0] - new_size[0]) // 2,
        (canvas_size[1] - new_size[1]) // 2,
    )
    background.alpha_composite(resized, dest=offset)

    out = io.BytesIO()
    background.convert("RGB").save(out, format="PNG")

    return out.getvalue()


__all__ = [
    "BackgroundKind",
    "BackgroundSpec",
    "compose_with_background",
    "CANVAS_SIZE",
]
