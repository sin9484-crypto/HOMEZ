"""
=========================================================
Homez OS

File : app/domains/media_asset/background_designer.py

적응형 배경 설계(이미지 형식 학습·응용 AI 설계 P3, 2026-10-14 사용자 요청 — "상품이 부각될 수 있게 배경을 만든다.
상품이 다양하므로 배경을 고정하지 않는다").

제품 누끼(투명 PNG)의 **색을 분석**해 그 제품이 가장 돋보이는 배경을 제안하고, 고른 제안으로 이미지를 만든다.
미리 정한 컨셉(`image_concepts.py`)과 달리 배경 색이 제품마다 달라진다:
- 보색 파스텔, 같은 색 계열의 옅은 톤, 이웃 색, 어두운 대비 배경, 스포트라이트, 중립 회색 등을 만들고
- 제품의 주요 색과 배경의 색 차이(CIE Lab ΔE)를 계산해 **대비가 큰 순서**로 고르며(제품 색이 배경에 묻히는
  후보는 낮은 점수·경고),
- `variation`을 바꾸면 같은 제품에 대해 다른 색조의 후보 묶음이 나온다.

원칙: 제품 자체를 다시 그리지 않는다(2026-08-20 CTO 지시) — 제품 픽셀은 그대로 두고 배경·그림자만 그린다.
대표 이미지(MAIN)는 흰 배경만 허용한다(쿠팡 권장, `image_concepts.py`와 동일). 순수 Pillow, 외부 호출 없음, 결정적.
=========================================================
"""

from __future__ import annotations

import colorsys
import io
import math
from dataclasses import dataclass

from PIL import Image
from PIL import ImageFilter

from app.core.exceptions import BadRequestException
from app.domains.media_asset.image_concepts import PURPOSE_ADDITIONAL
from app.domains.media_asset.image_concepts import PURPOSE_DETAIL
from app.domains.media_asset.image_concepts import PURPOSE_MAIN
from app.domains.media_asset.image_concepts import PURPOSES

ANALYSIS_MAX_SIDE = 200
OPAQUE_ALPHA = 128
LIGHT_L = 0.68
DARK_L = 0.30
NEUTRAL_S = 0.12
LOW_CONTRAST_DELTA_E = 18.0     # 제품의 주요 색과 배경의 ΔE가 이보다 작으면 그 색은 "묻힌다"
BLEND_SHARE_LIMIT = 0.35        # 묻히는 색이 제품의 이 비율 이상일 때만 배경 전체를 "묻힘"으로 경고한다
FULL_SCORE_DELTA_E = 50.0       # 가중 평균 ΔE가 이 값 이상이면 점수 100
MAX_VARIATION = 11
CANVAS_SIZE = (1200, 1200)
DEFAULT_FILL_RATIO = 0.86
MAX_UPSCALE_LIMIT = 2.5

KIND_WHITE = "WHITE"
KIND_SOLID = "SOLID"
KIND_GRADIENT = "GRADIENT"
KIND_RADIAL = "RADIAL"

Rgb = tuple[int, int, int]


# ---------------------------------------------------------------------
# 색 도구
# ---------------------------------------------------------------------

def _hex(rgb: Rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _from_hex(value: str) -> Rgb:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _hls(hue: float, lightness: float, saturation: float) -> Rgb:
    r, g, b = colorsys.hls_to_rgb(hue % 1.0, max(0.0, min(1.0, lightness)), max(0.0, min(1.0, saturation)))
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def _lab(rgb: Rgb) -> tuple[float, float, float]:
    def lin(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: Rgb, b: Rgb) -> float:
    la, lb = _lab(a), _lab(b)
    return math.dist(la, lb)


# ---------------------------------------------------------------------
# 제품 색 분석
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class ProductColorProfile:
    colors: tuple[tuple[str, float], ...]   # (#hex, 점유 비율) — 주요 색 최대 4개
    mean_hex: str
    mean_lightness: float
    mean_saturation: float
    dominant_hue: float
    is_light: bool
    is_dark: bool
    is_neutral: bool

    def to_dict(self) -> dict:
        return {
            "colors": [list(c) for c in self.colors], "mean_hex": self.mean_hex,
            "mean_lightness": self.mean_lightness, "mean_saturation": self.mean_saturation,
            "dominant_hue": self.dominant_hue, "is_light": self.is_light, "is_dark": self.is_dark,
            "is_neutral": self.is_neutral,
        }


def _open_rgba(image_bytes: bytes) -> Image.Image:
    if not image_bytes:
        raise BadRequestException("이미지 데이터가 비어 있습니다.")
    try:
        return Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise BadRequestException(f"이미지를 읽을 수 없습니다({type(exc).__name__}).") from exc


def analyze_product(cutout_png_bytes: bytes) -> ProductColorProfile:
    """투명 PNG(누끼)의 불투명 픽셀만으로 제품의 주요 색·밝기·채도·대표 색조를 구한다."""

    image = _open_rgba(cutout_png_bytes)
    if max(image.size) > ANALYSIS_MAX_SIDE:
        scale = ANALYSIS_MAX_SIDE / max(image.size)
        # NEAREST — 투명한 가장자리와 색이 섞여 제품 색이 어두워지지 않게 한다.
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.NEAREST)

    bins: dict[tuple[int, int, int], list[float]] = {}
    total = 0
    sum_r = sum_g = sum_b = 0.0
    hue_x = hue_y = 0.0
    for r, g, b, a in image.getdata():
        if a < OPAQUE_ALPHA:
            continue
        total += 1
        sum_r += r
        sum_g += g
        sum_b += b
        slot = bins.setdefault((r >> 5, g >> 5, b >> 5), [0, 0.0, 0.0, 0.0])
        slot[0] += 1
        slot[1] += r
        slot[2] += g
        slot[3] += b
        h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s >= 0.2:
            hue_x += math.cos(h * 2 * math.pi) * s
            hue_y += math.sin(h * 2 * math.pi) * s
    if total == 0:
        raise BadRequestException("제품 영역(불투명 픽셀)이 없는 이미지입니다 — 배경 제거 결과인지 확인하세요.")

    ranked = sorted(bins.values(), key=lambda s: (-s[0], s[1], s[2], s[3]))[:4]
    colors = tuple((_hex((int(s[1] / s[0]), int(s[2] / s[0]), int(s[3] / s[0]))), round(s[0] / total, 4)) for s in ranked)
    mean = (sum_r / total, sum_g / total, sum_b / total)
    mean_rgb = (int(round(mean[0])), int(round(mean[1])), int(round(mean[2])))
    _h, lightness, _s = colorsys.rgb_to_hls(*(c / 255 for c in mean_rgb))
    # 채도는 평균색이 아니라 픽셀별 평균(서로 다른 색이 섞이면 평균색은 회색이 된다).
    saturation = sum(s[0] * colorsys.rgb_to_hsv(s[1] / s[0] / 255, s[2] / s[0] / 255, s[3] / s[0] / 255)[1] for s in bins.values()) / total
    if hue_x == 0 and hue_y == 0:
        hue = 0.6      # 무채색 제품 — 차분한 청색 계열을 기준 색조로 쓴다
    else:
        hue = (math.atan2(hue_y, hue_x) / (2 * math.pi)) % 1.0
    return ProductColorProfile(
        colors=colors, mean_hex=_hex(mean_rgb), mean_lightness=round(lightness, 4), mean_saturation=round(saturation, 4),
        dominant_hue=round(hue, 4), is_light=lightness >= LIGHT_L, is_dark=lightness <= DARK_L,
        is_neutral=saturation < NEUTRAL_S,
    )


# ---------------------------------------------------------------------
# 배경 제안
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class BackgroundSuggestion:
    code: str
    label_ko: str
    reason_ko: str
    kind: str
    color_hex: str
    color_hex_end: str | None
    contrast: int                 # 0~100 — 제품 주요 색과 배경의 평균 색 차이(가중)
    min_delta_e: float            # 가장 가까운 제품 색과의 차이(참고값)
    low_contrast: bool            # 배경에 묻히는 색이 제품의 큰 비중(>=35%)일 때만 True
    shadow_recommended: bool
    usable_for: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "code": self.code, "label_ko": self.label_ko, "reason_ko": self.reason_ko, "kind": self.kind,
            "color_hex": self.color_hex, "color_hex_end": self.color_hex_end, "contrast": self.contrast,
            "min_delta_e": self.min_delta_e, "low_contrast": self.low_contrast,
            "shadow_recommended": self.shadow_recommended, "usable_for": list(self.usable_for),
        }


def _score(profile: ProductColorProfile, colors: tuple[Rgb, ...]) -> tuple[int, float, float]:
    """배경 색(그라데이션이면 양 끝)과 제품 주요 색의 ΔE → (가중 평균 점수 0~100, 최소 ΔE, 묻히는 색의 비중)."""

    weights = [(share, _from_hex(hex_color)) for hex_color, share in profile.colors]
    norm = sum(w for w, _ in weights) or 1.0
    weighted = 0.0
    smallest = 1e9
    blended = 0.0
    for share, product_color in weights:
        distance = min(delta_e(product_color, bg) for bg in colors)      # 가장 가까운 배경 부분 기준(보수적)
        weighted += (share / norm) * distance
        if share >= 0.05:
            smallest = min(smallest, distance)
        if distance < LOW_CONTRAST_DELTA_E:
            blended += share / norm
    if smallest == 1e9:
        smallest = weighted
    return int(round(min(100.0, weighted / FULL_SCORE_DELTA_E * 100))), round(smallest, 1), round(blended, 4)


def _make(code: str, label: str, reason: str, kind: str, start: Rgb, end: Rgb | None,
          profile: ProductColorProfile, usable: tuple[str, ...]) -> BackgroundSuggestion:
    colors = (start,) if end is None else (start, end)
    contrast, minimum, blended = _score(profile, colors)
    low = blended >= BLEND_SHARE_LIMIT
    return BackgroundSuggestion(
        code=code, label_ko=label, reason_ko=reason, kind=kind, color_hex=_hex(start),
        color_hex_end=_hex(end) if end is not None else None, contrast=contrast, min_delta_e=minimum,
        low_contrast=low, shadow_recommended=low or profile.is_light, usable_for=usable,
    )


def suggest_backgrounds(
    profile: ProductColorProfile, purpose: str = PURPOSE_ADDITIONAL, count: int = 6, variation: int = 0,
) -> list[BackgroundSuggestion]:
    """제품의 색에 맞춘 배경 제안을 대비가 큰 순서로. `variation`은 같은 제품의 다른 색조 묶음(0~11)."""

    if purpose not in PURPOSES:
        raise BadRequestException(f"알 수 없는 이미지 용도입니다: {purpose}")
    if not (0 <= variation <= MAX_VARIATION):
        raise BadRequestException(f"variation은 0~{MAX_VARIATION} 사이여야 합니다: {variation}")
    if not (1 <= count <= 8):
        raise BadRequestException("count는 1~8 사이여야 합니다.")

    white = _make(
        "ADAPT_WHITE", "흰 배경", "쿠팡 대표 이미지 권장 규격. 제품이 밝으면 묻힐 수 있어 그림자를 함께 쓰면 좋습니다.",
        KIND_WHITE, (255, 255, 255), None, profile, (PURPOSE_MAIN, PURPOSE_ADDITIONAL, PURPOSE_DETAIL),
    )
    if purpose == PURPOSE_MAIN:
        return [white]       # 대표 이미지는 흰 배경만(배경을 바꾸는 제안은 추가·상세 이미지용)

    usable = (PURPOSE_ADDITIONAL, PURPOSE_DETAIL)
    hue = profile.dominant_hue
    shift = 0.045 * variation
    complement = (hue + 0.5 + shift) % 1.0
    candidates: list[BackgroundSuggestion] = []

    candidates.append(_make(
        "ADAPT_COMPLEMENT", "보색 파스텔", "제품 색의 반대 색조를 옅게 깔아 제품이 또렷하게 떠 보입니다.",
        KIND_GRADIENT, _hls(complement, 0.95, 0.45), _hls(complement, 0.86, 0.45), profile, usable,
    ))
    candidates.append(_make(
        "ADAPT_TONAL", "같은 색 톤", "제품과 같은 색 계열의 아주 옅은 톤 — 차분하고 고급스러운 분위기.",
        KIND_GRADIENT, _hls(hue + shift, 0.95, 0.30), _hls(hue + shift, 0.88, 0.30), profile, usable,
    ))
    candidates.append(_make(
        "ADAPT_ANALOGOUS", "이웃 색 파스텔", "제품 색과 이웃한 색조로 조화롭게 받쳐 줍니다.",
        KIND_GRADIENT, _hls(hue + 0.083 + shift, 0.92, 0.38), _hls(hue + 0.083 + shift, 0.84, 0.38), profile, usable,
    ))
    candidates.append(_make(
        "ADAPT_BOLD", "선명한 색 배경", "보색을 또렷하게 깔아 시선을 확 끕니다 — 눈에 띄는 광고 컷용.",
        KIND_RADIAL, _hls(complement, 0.62, 0.55), _hls(complement, 0.46, 0.55), profile, usable,
    ))
    if profile.is_light or (profile.is_neutral and profile.mean_lightness >= 0.5):
        candidates.append(_make(
            "ADAPT_DEEP", "깊은 대비 배경", "밝은 제품을 어두운 톤 위에 올려 윤곽이 선명해집니다.",
            KIND_RADIAL, _hls(complement, 0.26, 0.30), _hls(complement, 0.10, 0.30), profile, usable,
        ))
        candidates.append(_make(
            "ADAPT_NEUTRAL", "짙은 중립 회색", "색을 더하지 않고 제품 색 그대로를 보여 주는 어두운 회색.",
            KIND_SOLID, (42, 45, 51), None, profile, usable,
        ))
    else:
        candidates.append(_make(
            "ADAPT_SPOTLIGHT", "스포트라이트", "가운데가 밝고 가장자리가 옅게 어두워져 제품에 시선이 모입니다.",
            KIND_RADIAL, (255, 255, 255), _hls(complement, 0.86, 0.35), profile, usable,
        ))
        candidates.append(_make(
            "ADAPT_NEUTRAL", "옅은 중립 회색", "색을 더하지 않고 제품 색 그대로를 보여 주는 밝은 회색.",
            KIND_SOLID, (228, 231, 235), None, profile, usable,
        ))
    candidates.append(white)

    # 서로 거의 같은 배경은 하나만 남긴다(대비가 큰 쪽).
    def family(kind: str) -> str:
        return "FLAT" if kind in (KIND_WHITE, KIND_SOLID) else kind

    def same_look(a: BackgroundSuggestion, b: BackgroundSuggestion) -> bool:
        # 같은 종류(단색/세로 그라데이션/방사형)이고 시작색·끝색이 모두 거의 같을 때만 같은 배경으로 본다 —
        # 흰 배경과 흰색에서 시작하는 스포트라이트, 같은 색의 세로 그라데이션과 방사형은 서로 다른 배경이다.
        if family(a.kind) != family(b.kind):
            return False
        a_end, b_end = a.color_hex_end or a.color_hex, b.color_hex_end or b.color_hex
        return (delta_e(_from_hex(a.color_hex), _from_hex(b.color_hex)) < 6
                and delta_e(_from_hex(a_end), _from_hex(b_end)) < 6)

    # 흰 배경은 기본형이라 항상 남기고, 흰색과 비슷한 다른 후보를 지운다(예: 거의 흰색인 연한 회색).
    unique: list[BackgroundSuggestion] = []
    for suggestion in sorted(candidates, key=lambda s: (-s.contrast, s.code)):
        if suggestion.code == white.code or same_look(suggestion, white):
            continue
        if not any(same_look(suggestion, other) for other in unique):
            unique.append(suggestion)
    unique.append(white)
    unique.sort(key=lambda s: (-s.contrast, s.code))
    return unique[:count]


# ---------------------------------------------------------------------
# 그리기
# ---------------------------------------------------------------------

def _build_background(suggestion: BackgroundSuggestion, size: tuple[int, int]) -> Image.Image:
    start = _from_hex(suggestion.color_hex)
    end = _from_hex(suggestion.color_hex_end) if suggestion.color_hex_end else start
    if suggestion.kind in (KIND_WHITE, KIND_SOLID):
        return Image.new("RGB", size, start)
    if suggestion.kind == KIND_GRADIENT:
        mask = Image.linear_gradient("L").resize(size)          # 위(검정) → 아래(흰색)
        return Image.composite(Image.new("RGB", size, end), Image.new("RGB", size, start), mask)
    if suggestion.kind == KIND_RADIAL:
        mask = Image.radial_gradient("L").resize(size)          # 가운데(검정) → 가장자리(흰색)
        return Image.composite(Image.new("RGB", size, end), Image.new("RGB", size, start), mask)
    raise BadRequestException(f"알 수 없는 배경 종류입니다: {suggestion.kind}")


def render_suggestion(
    cutout_png_bytes: bytes, suggestion: BackgroundSuggestion, *, canvas_size: tuple[int, int] = CANVAS_SIZE,
    fill_ratio: float = DEFAULT_FILL_RATIO, shadow: bool = False, max_upscale: float = 1.0,
) -> bytes:
    """제안한 배경 위에 제품을 가운데 놓은 PNG. 제품은 다시 그리지 않는다 — 기본은 확대하지 않고(화질을 부풀리지
    않기 위해), 크롭이 작아 제품이 너무 작아질 때만 `max_upscale`(1.0~2.5)까지 확대를 허용한다. 필요하면 부드러운
    그림자만 더한다."""

    if not (0.3 <= fill_ratio <= 1.0):
        raise BadRequestException("fill_ratio는 0.3~1.0 사이여야 합니다.")
    if not (1.0 <= max_upscale <= MAX_UPSCALE_LIMIT):
        raise BadRequestException(f"max_upscale은 1.0~{MAX_UPSCALE_LIMIT} 사이여야 합니다.")
    product = _open_rgba(cutout_png_bytes)
    box = product.getchannel("A").point(lambda a: 255 if a >= OPAQUE_ALPHA else 0).getbbox()
    if box is None:
        raise BadRequestException("제품 영역(불투명 픽셀)이 없는 이미지입니다.")
    product = product.crop(box)
    scale = min(canvas_size[0] * fill_ratio / product.width, canvas_size[1] * fill_ratio / product.height, max_upscale)
    if scale != 1.0:
        product = product.resize((max(1, int(product.width * scale)), max(1, int(product.height * scale))), Image.LANCZOS)

    canvas = _build_background(suggestion, canvas_size).convert("RGBA")
    offset = ((canvas_size[0] - product.width) // 2, (canvas_size[1] - product.height) // 2)
    if shadow:
        alpha = product.getchannel("A").filter(ImageFilter.GaussianBlur(22)).point(lambda a: int(a * 0.30))
        layer = Image.new("RGBA", product.size, (0, 0, 0, 0))
        layer.putalpha(alpha)
        canvas.alpha_composite(layer, dest=(offset[0], min(canvas_size[1] - product.height, offset[1] + 16)))
    canvas.alpha_composite(product, dest=offset)
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def get_suggestion(
    profile: ProductColorProfile, code: str, purpose: str, variation: int = 0,
) -> BackgroundSuggestion:
    for suggestion in suggest_backgrounds(profile, purpose, count=8, variation=variation):
        if suggestion.code == code:
            return suggestion
    raise BadRequestException(f"이 제품·용도·variation에서는 '{code}' 배경 제안이 없습니다.")


__all__ = [
    "BackgroundSuggestion", "MAX_VARIATION", "ProductColorProfile", "analyze_product", "delta_e",
    "get_suggestion", "render_suggestion", "suggest_backgrounds",
]
