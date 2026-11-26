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
from PIL import ImageOps

from app.core.exceptions import BadRequestException

CANVAS_SIZE = (1200, 1200)


class Placement:
    """누끼를 캔버스 어디에 어떻게 놓는가(2026-11-15 추가). 합성(`compose_with_background`)과 검사(`concept_inspection`)가
    같은 `plan_placement()`를 쓰므로, 제품 보존 검사가 언제나 실제로 붙인 자리를 잰다.

      - CENTER    기존 동작 그대로: 누끼 전체(투명 여백 포함)를 확대 없이 맞춰 가운데에 놓는다. 단색·그라데이션·질감 배경용.
      - GROUNDED  장면 배경(AI가 그린 배경)용: 누끼의 투명 여백을 잘라 낸 제품을 가운데에 세우고, 제품 밑면을 화면 아래쪽 바닥선에
                  맞춘다. 그 아래에 부드러운 바닥 그림자를 먼저 그린 뒤 제품을 붙인다 — 제품 픽셀은 그대로다(다시 그리지 않음).
                  2026-11-26: 작은 원본(600px 묶음 사진에서 떼어 낸 한 병은 높이 약 480px)이 화면의 40%에 그쳐 판매성이 떨어졌다 —
                  제미나이 협의(1.3~1.4배까지는 화질 저하가 눈에 띄지 않는다)에 따라 GROUNDED만 최대 GROUNDED_MAX_UPSCALE배까지 키운다.
                  CENTER는 그대로 확대하지 않는다.
      - FILL      대표(흰 배경)용(2026-11-26): 누끼의 투명 여백을 잘라 낸 제품을 정가운데에 놓고, 화면의 MAIN_FILL만큼 채우되 최대
                  GROUNDED_MAX_UPSCALE배까지만 키운다. CENTER는 투명 여백까지 통째로 놓아, 묶음 사진에서 떼어 낸 한 병이 원래 자리(오른쪽)에
                  화면 높이 40%로 작게 남았다."""

    CENTER = "CENTER"
    GROUNDED = "GROUNDED"
    FILL = "FILL"
    ALL = (CENTER, GROUNDED, FILL)


GROUND_LINE = 0.86          # GROUNDED: 제품 밑면이 놓이는 높이(캔버스 높이 대비) — AI 배경 장면 글이 아래쪽 30%를 빈 바닥으로 그리게 한다
GROUNDED_MAX_HEIGHT = 0.56  # GROUNDED: 제품 높이 상한(캔버스 높이 대비) — 2026-11-26 0.62→0.50(수건·침대보다 거대) → 0.56("제품이 부각이 안된다", 1.4배 한도 안)
FOCUS_BLUR = 0.003          # GROUNDED: 배경만 아주 살짝 흐리게(캔버스 너비 대비 반경) — 탁자 결이 상품과 겨루지 않게
FOCUS_VIGNETTE = 0.16       # GROUNDED: 가장자리로 갈수록 배경을 이만큼까지 어둡게 — 시선이 가운데 상품으로
GROUNDED_MAX_WIDTH = 0.80   # GROUNDED: 제품 너비 상한(캔버스 너비 대비)
MAIN_CANVAS_SIZE = (1000, 1000)   # FILL(대표): 2026-11-26 — 600px 원본을 1.4배까지만 키우면 1200 캔버스에선 56%라, 쿠팡 대표 최소 규격(1,000) 캔버스로 67%
MAIN_FILL = 0.85            # FILL: 제품이 채우는 정도(캔버스 대비, 긴 쪽) — 판매자 정리 글의 "대표 이미지 상품 85%"(I0 🟡, 쿠팡 공식 미확인)
GROUNDED_MAX_UPSCALE = 1.4  # GROUNDED: 원본보다 키우는 한도(2026-11-26 제미나이 협의 "1.3~1.4배까지") — 크기 상한이 먼저 걸리면 그만큼만
SHADOW_CONTACT_ALPHA = 120  # 바닥에 닿는 짙은 그림자(0~255)
SHADOW_SOFT_ALPHA = 46      # 제품 모양을 따라 뒤로 퍼지는 옅은 그림자(0~255)
SHADOW_SHIFT = 0.10         # 옅은 그림자를 오른쪽으로 미는 정도(제품 너비 대비) — 장면 글의 빛은 왼쪽에서 온다
LIGHT_TINT = 0.5            # GROUNDED: 장면의 밝은 빛 색을 상품에 입히는 정도(0=그대로, 1=장면 빛 색 그대로) — 2026-11-26 "부 자연스럽다"
LIGHT_FALLOFF = 0.10        # GROUNDED: 빛이 왼쪽에서 오므로 상품 오른쪽 끝을 이만큼 어둡게(왼쪽 끝은 그대로)
LIGHT_GAIN_MIN = 0.85       # 빛 색 맞춤에서 한 채널을 줄이는 한도(상품 색이 크게 바뀌지 않게)
LIGHT_SIDE_BAND = (0.42, 0.58)   # 장면에서 가장 밝은 곳이 이 가로 범위 밖이면 그쪽에서 오는 빛
SHADE_MAX = 0.28            # 빛 반대쪽 끝을 어둡게 하는 최대(장면 명암이 가장 셀 때) — 2026-11-26 "명암 대비가 안맞다"
SHADOW_CAST_HEIGHT = 0.10   # 앞으로 떨어지는 그림자의 길이(제품 높이 대비, 탁자 위 원근으로 납작하게)
SHADOW_CAST_REACH = 0.30    # 빛이 옆에서 올 때 그림자 끝이 옆으로 가는 거리(제품 높이 대비)
LIGHT_EXPOSURE = 0.06       # 역광 장면에서 상품 앞면을 낮추는 최대 밝기(장면 명암 세기에 비례)
HAZE_ALPHA_MAX = 64         # GROUNDED: 이보다 옅은 누끼 픽셀은 지운다(배경 지우기가 남긴 흰 안개 — 장면 위에서 흰 테두리로 보였다, 2026-11-25)
SOLID_ALPHA_MIN = 250       # GROUNDED: 이 이상 불투명한 누끼 픽셀은 완전 불투명으로 본다(배경 지우기 결과는 제품 안쪽이 250~254로 남아 배경이 비친다)


def prepare_cutout(cutout: Image.Image, placement: str = Placement.CENTER) -> Image.Image:
    """놓기 전에 누끼를 다듬는다(합성과 검사가 함께 쓴다). CENTER는 그대로. GROUNDED는 거의 불투명한 픽셀의 알파만 255로 올리고, 아주 옅은(HAZE_ALPHA_MAX 미만) 안개 픽셀은 지운다 —
    제품 색(RGB)은 바꾸지 않으므로 '다시 그리지 않음' 원칙 그대로이고, 제품 보존 검사가 재는 픽셀도 그만큼 많아진다."""

    rgba = cutout.convert("RGBA")
    if placement != Placement.GROUNDED:
        return rgba
    red, green, blue, alpha = rgba.split()
    alpha = alpha.point(lambda a: 255 if a >= SOLID_ALPHA_MIN else (0 if a < HAZE_ALPHA_MAX else a))
    return Image.merge("RGBA", (red, green, blue, alpha))


@dataclass(frozen=True)
class PlacementPlan:
    crop_box: tuple[int, int, int, int]   # 누끼에서 쓸 부분(투명 여백을 자른 상자 — CENTER는 전체)
    size: tuple[int, int]                 # 캔버스에 놓을 크기
    offset: tuple[int, int]               # 캔버스에서 왼쪽 위 자리


def plan_placement(cutout: Image.Image, canvas_size: tuple[int, int], placement: str = Placement.CENTER, *,
                   ground_line: float | None = None, max_height: float | None = None) -> PlacementPlan:
    """합성과 검사가 함께 쓰는 놓는 자리 계산(순수 계산).
    2026-11-26: GROUNDED는 장면마다 바닥선·높이 한도를 따로 줄 수 있다(예: 침대 곁 협탁은 앞모서리가 화면 안에 그려져, 기본 바닥선이면 상품이
    모서리 끝에 서 보였다 — 사용자 "#2#4 다시 만들어라"). 합성과 검사는 같은 값을 받는다."""

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
    if placement == Placement.FILL:
        scale = min(MAIN_FILL * canvas_w / trimmed_w, MAIN_FILL * canvas_h / trimmed_h, GROUNDED_MAX_UPSCALE)
        size = (max(1, int(trimmed_w * scale)), max(1, int(trimmed_h * scale)))
        return PlacementPlan(box, size, ((canvas_w - size[0]) // 2, (canvas_h - size[1]) // 2))
    scale = min(GROUNDED_MAX_WIDTH * canvas_w / trimmed_w, (max_height or GROUNDED_MAX_HEIGHT) * canvas_h / trimmed_h, GROUNDED_MAX_UPSCALE)
    size = (max(1, int(trimmed_w * scale)), max(1, int(trimmed_h * scale)))
    base_y = int(canvas_h * (ground_line or GROUND_LINE))
    offset = ((canvas_w - size[0]) // 2, max(0, base_y - size[1]))
    return PlacementPlan(box, size, offset)


@dataclass(frozen=True)
class SceneLight:
    """장면의 빛(2026-11-26 사용자 "사진에 그림자 및 명암 대비가 안맞다"). 상품 위쪽(그림자·상품이 닿지 않는 곳)만 재므로
    합성(배경)과 검사(합성 결과)가 언제나 같은 값을 낸다.
      - gains: 빛 색(채널별 곱) — 하얗게 날아간 창·하늘을 뺀 밝은 30%의 평균색을 LIGHT_TINT만큼만
      - side: 빛이 오는 쪽(−1 왼쪽, 0 앞·뒤·가운데, +1 오른쪽) — 가장 밝은 10%(창·해 포함)가 모인 가로 위치
      - strength: 장면 명암 차이(밝은 쪽 90% − 어두운 쪽 10% 밝기)를 0.3~1로 — 그림자 진하기·상품 명암 세기에 쓴다"""

    gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    side: int = 0
    strength: float = 0.5


def focus_background(background: Image.Image) -> Image.Image:
    """2026-11-26(사용자 "제품이 부각이 안된다") — 배경(AI가 그린 장면)만 아주 살짝 흐리게 하고 가장자리를 어둡게 해 가운데 상품으로 시선을 모은다.
    상품 픽셀은 건드리지 않는다(상품은 이 뒤에 붙인다). 정해진 계산이라 같은 배경이면 같은 결과다."""

    width, height = background.size
    image = background.convert("RGB").filter(ImageFilter.GaussianBlur(max(1, int(width * FOCUS_BLUR))))
    ring = Image.radial_gradient("L").resize((width, height), Image.BILINEAR)          # 가운데 0 → 가장자리 255
    shade = ring.point(lambda v: int(round(255 * (1.0 - FOCUS_VIGNETTE * (v / 255.0) ** 2))))
    return Image.merge("RGB", [ImageChops.multiply(band, shade) for band in image.split()])


def scene_light(scene: Image.Image, product_top: int) -> SceneLight:
    width, height = scene.size
    top = product_top - int(height * 0.04)
    if top < 16:
        return SceneLight()
    region = scene.convert("RGB").crop((0, 0, width, top)).resize((max(1, width // 8), max(1, top // 8)), Image.BOX)
    small_w = region.width
    pixels = list(region.getdata())
    coloured = sorted((p for p in pixels if min(p) < 248), key=lambda p: (p[0] + p[1] + p[2], p), reverse=True)
    if coloured:
        count = max(1, len(coloured) * 3 // 10)
        average = [sum(p[c] for p in coloured[:count]) / count for c in range(3)]
        peak = max(average) or 1.0
        gains = tuple(round(1.0 - LIGHT_TINT * (1.0 - max(LIGHT_GAIN_MIN, a / peak)), 4) for a in average)
    else:
        gains = (1.0, 1.0, 1.0)
    lum = [(p[0] * 299 + p[1] * 587 + p[2] * 114) // 1000 for p in pixels]
    order = sorted(range(len(lum)), key=lambda i: (lum[i], i))
    bright = order[-max(1, len(order) // 10):]
    centre = sum((i % small_w) + 0.5 for i in bright) / len(bright) / small_w
    side = -1 if centre < LIGHT_SIDE_BAND[0] else (1 if centre > LIGHT_SIDE_BAND[1] else 0)
    ranked = sorted(lum)
    contrast = (ranked[int(len(ranked) * 0.9) - 1 if len(ranked) > 1 else 0] - ranked[int(len(ranked) * 0.1)]) / 255.0
    strength = round(min(1.0, max(0.3, (contrast - 0.15) / 0.45)), 3)
    return SceneLight(gains, side, strength)  # type: ignore[arg-type]


def scene_light_gains(scene: Image.Image, product_top: int) -> tuple[float, float, float]:
    """빛 색만(예전 이름 그대로)."""

    return scene_light(scene, product_top).gains


def _shade_factor(t: float, side: int, falloff: float) -> float:
    """상품 가로 위치 t(0=왼쪽, 1=오른쪽)의 밝기 곱. 빛 반대쪽일수록 어둡고, 앞에서 오는 빛이면 양쪽 가장자리가 조금 어둡다(둥근 몸통)."""

    if side < 0:
        return 1.0 - falloff * (t ** 1.2)
    if side > 0:
        return 1.0 - falloff * ((1.0 - t) ** 1.2)
    return 1.0 - falloff * 0.6 * (abs(2.0 * t - 1.0) ** 1.5)


def _exposure(falloff: float) -> float:
    """역광(빛이 뒤에 보이는 장면)에서 카메라를 향한 앞면이 조금 어둡게 — 명암 세기에 비례."""

    return 1.0 - LIGHT_EXPOSURE * (falloff / SHADE_MAX if SHADE_MAX else 0.0)


def match_scene_light(product: Image.Image, gains: tuple[float, float, float], *, side: int = -1, falloff: float = LIGHT_FALLOFF) -> Image.Image:
    """상품(RGBA, 놓을 크기)에 장면 빛 색과 빛 방향에 따른 좌우 명암을 입힌다. 모양·글씨는 그대로(채널별 곱만), 알파는 그대로.
    결과 = 원래 값 × 채널 곱 × 명암 곱(가로 위치) — 정해진 계산이라 검사가 같은 값을 다시 만든다."""

    width, height = product.size
    red, green, blue, alpha = product.convert("RGBA").split()
    exposure = _exposure(falloff) if falloff > LIGHT_FALLOFF else 1.0     # 예전 기본값(왼쪽 빛 10%)은 그대로
    shade = [_shade_factor(x / max(1, width - 1), side, falloff) * exposure for x in range(width)]
    out = []
    for channel, gain in zip((red, green, blue), gains):
        row = Image.new("L", (width, 1))
        row.putdata([max(0, min(255, int(round(255 * gain * f)))) for f in shade])
        out.append(ImageChops.multiply(channel, row.resize((width, height), Image.NEAREST)))
    return Image.merge("RGBA", (*out, alpha))


def light_product(product: Image.Image, light: SceneLight) -> Image.Image:
    return match_scene_light(product, light.gains, side=light.side, falloff=round(SHADE_MAX * light.strength, 4))


SHARPEN = (1.4, 70, 2)        # 키운 상품에만: 반경·세기(%)·문턱 — 2026-11-26 "평균 90점" 화질 보강(라벨 작은 글씨가 흐렸다)


def scaled_product(prepared: Image.Image, plan: "PlacementPlan") -> Image.Image:
    """놓을 크기로 바꾼 상품(합성·검사가 함께 쓴다). 원본보다 키웠을 때만 색(RGB)에 아주 약한 선명 처리를 한다 — 모양·알파는 그대로,
    정해진 계산이라 보존 검사가 같은 값을 다시 만든다(다시 그리기가 아니다 — 사진 편집의 선명도 보정)."""

    box = plan.crop_box
    resized = prepared.crop(box).resize(plan.size, Image.LANCZOS)
    if plan.size[1] <= (box[3] - box[1]) * 1.05:
        return resized
    rgb = resized.convert("RGB").filter(ImageFilter.UnsharpMask(radius=SHARPEN[0], percent=SHARPEN[1], threshold=SHARPEN[2]))
    rgb.putalpha(resized.getchannel("A"))
    return rgb


def _ground_shadow(canvas_size: tuple[int, int], product: Image.Image, offset: tuple[int, int], light: SceneLight | None = None) -> Image.Image:
    """제품 아래 바닥 그림자(RGBA 층). 제품이 놓일 자리 밑에만 그린다 — 제품 위에는 제품이 덮는다.

    2026-11-25: 열마다 제품의 실제 밑면에 닿는 짙은 그림자(용기마다 따로 닿는다).
    2026-11-26(사용자 "사진에 그림자 및 명암 대비가 안맞다"): 장면의 수건은 진하고 또렷한 그림자를 떨어뜨리는데 병 밑은 거의 비어 떠 보였고,
    빛이 오른쪽·가운데에서 오는 장면에도 그림자를 오른쪽으로만 눕혔다. 이제 장면 빛(`scene_light`)의 방향·세기를 따라
    닿는 그림자를 더 짙게, 빛 반대쪽 뒤로 비스듬히 눕는 그림자를 길게(밑에서 멀어질수록 옅게) 그린다. 빛이 앞에서 오면 바로 뒤로 짧게."""

    light = light or SceneLight()
    width, height = product.size
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    solid = product.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    # 1) 열마다 실제 밑면에 닿는 짙고 얇은 그림자
    contact = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(contact)
    band = max(2, int(height * 0.012))
    contact_alpha = int(SHADOW_CONTACT_ALPHA + 70 * light.strength)
    for x in range(width):
        column = solid.crop((x, 0, x + 1, height)).getbbox()
        if column is None:
            continue
        bottom = offset[1] + column[3]
        draw.line((offset[0] + x, bottom - band, offset[0] + x, bottom + max(1, band // 2)), fill=contact_alpha)
    contact = contact.filter(ImageFilter.GaussianBlur(max(2, int(height * 0.008))))
    # 2) 밑면 둘레의 넓고 옅은 그림자(바닥에 놓인 느낌)
    ambient = contact.filter(ImageFilter.GaussianBlur(max(4, int(height * 0.03)))).point(lambda v: min(255, v * 2))
    # 3) 빛 반대쪽 앞으로 떨어지는 그림자. 장면의 빛(창·해)은 뒤쪽에 보이는 역광이라 그림자는 카메라 쪽(아래)으로, 빛 반대 옆으로 기운다.
    #    제품 모양을 납작하게 눌러 밑면에서 아래로 붙이고, 아래로 갈수록 옆으로 기울이고 옅게 한다.
    flat_h = max(2, int(height * SHADOW_CAST_HEIGHT))
    cast_alpha = int(SHADOW_SOFT_ALPHA + 70 * light.strength)
    flat = solid.transpose(Image.FLIP_TOP_BOTTOM).point(lambda a: cast_alpha if a else 0).resize((width, flat_h), Image.LANCZOS)
    fade = Image.linear_gradient("L").resize((width, flat_h)).point(lambda v: int(255 - v * 175 / 255))   # 밑면 쪽 짙게, 끝 옅게
    flat = ImageChops.multiply(flat, fade)
    lean = -light.side                                   # 그림자는 빛 반대쪽
    reach = int(height * SHADOW_CAST_REACH) if lean else 0
    x0 = offset[0]
    if reach:
        flat = flat.transform((width + reach, flat_h), Image.AFFINE, (1, -reach / flat_h, 0, 0, 1, 0), resample=Image.BILINEAR)
        if lean < 0:
            flat = ImageOps.mirror(flat)
            x0 = offset[0] - reach
    cast = Image.new("L", canvas_size, 0)
    cast.paste(flat, (x0, offset[1] + height - int(height * 0.015)))
    cast = cast.filter(ImageFilter.GaussianBlur(max(3, int(height * 0.015))))
    layer.putalpha(ImageChops.lighter(ImageChops.lighter(contact, ambient), cast))   # 세 그림자 중 짙은 쪽
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
    *, placement: str = Placement.CENTER, ground_line: float | None = None, max_height: float | None = None,
) -> bytes:
    """
    투명 PNG(제품 누끼) + 배경 → 합성된 PNG 바이트.

    누끼 이미지는 원본 비율을 유지한 채 캔버스 안에 맞춘다. 기본(CENTER)은
    축소만 하고(확대하지 않음 — 화질을 거짓으로 부풀리지 않기 위함) 중앙 정렬.
    GROUNDED는 최대 GROUNDED_MAX_UPSCALE(1.4)배까지만 키운다(2026-11-26).
    GROUNDED(장면 배경용, 2026-11-15)는 투명 여백을 자른 제품을 바닥선에 세우고
    제품을 붙이기 **전에** 바닥 그림자를 그린다 — 제품 픽셀은 바뀌지 않는다.
    """

    import io

    cutout = prepare_cutout(Image.open(io.BytesIO(cutout_png_bytes)), placement)

    background = _build_background(spec, canvas_size).convert("RGBA")

    plan = plan_placement(cutout, canvas_size, placement, ground_line=ground_line, max_height=max_height)
    resized = scaled_product(cutout, plan)
    if placement == Placement.FILL:
        # 2026-11-26: 대표 흰 배경에서도 밑면에 닿는 옅은 그림자(떠 보이지 않게). 빛 맞춤은 하지 않는다(흰 배경 그대로).
        background.alpha_composite(_ground_shadow(canvas_size, resized, plan.offset))
    if placement == Placement.GROUNDED:
        background = focus_background(background).convert("RGBA")   # 2026-11-26: 배경만 살짝 흐리게·가장자리 어둡게(상품 부각)
        light = scene_light(background, plan.offset[1])          # 2026-11-26: 장면 빛 색·방향·세기(검사도 합성 결과의 같은 자리를 잰다)
        resized = light_product(resized, light)
        background.alpha_composite(_ground_shadow(canvas_size, resized, plan.offset, light))
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
    "GROUNDED_MAX_UPSCALE",
    "MAIN_FILL",
    "MAIN_CANVAS_SIZE",
    "scaled_product",
    "SOLID_ALPHA_MIN",
    "HAZE_ALPHA_MAX",
    "LIGHT_TINT",
    "LIGHT_FALLOFF",
    "scene_light_gains",
    "scene_light",
    "light_product",
    "SceneLight",
    "focus_background",
    "FOCUS_VIGNETTE",
    "match_scene_light",
    "SHADOW_SHIFT",
]
