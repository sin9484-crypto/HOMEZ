"""
=========================================================
Homez OS

File : app/domains/media_asset/detail_page_generator.py

쿠팡 상세이미지(긴 세로형 상세페이지 이미지) 생성. 실제 제품 사진
1장(app/domains/media_asset/composition.py로 배경 합성까지 끝난
결과물)과 사용자가 입력한 텍스트(브랜드명, 특징, 스펙, 사용법)를
조합해 만든다.

composition.py와 동일한 원칙을 그대로 따른다: 제품 자체를 AI로
다시 그리지 않는다(2026-08-20 CTO 지시) — 이 모듈도 순수 Pillow
레이아웃·텍스트 렌더링만 사용하고 외부 네트워크 호출이 없다. 새로운
각도·장면을 생성하지 않으며, 실제 업로드된 사진 그대로를 다른
텍스트·배경 요소와 함께 배치할 뿐이다.

쿠팡 공식 규격(2026-08-27 확인 — developers.coupang.com 카테고리
메타정보 API 문서 및 쿠팡 파트너 공식 블로그 "상품 이미지
가이드라인" 기준): 상세이미지 가로 780px, 세로 최대 5,000px. 이
모듈의 최대 높이는 그보다 더 낮은 4,096px로 제한한다 —
app/domains/media_asset/constants.py::MAX_IMAGE_HEIGHT(일반 이미지
공용 상한)와 맞춰, job_queue_service.py::_persist_generated_asset()의
표준 검증 경로(validate_image_bytes)를 그대로 재사용하기 위함이다
(그 상한을 이 기능만을 위해 전역으로 올리지 않는다는 기존 원칙 —
constants.py 114행 주석 참고). 4,096px는 쿠팡의 5,000px 한도 안에
여전히 넉넉히 들어간다. 섹션을 위에서부터 순서대로 쌓다가 이
상한을 넘기면 잘라내지 않고 예외로 막는다(잘린 상세페이지를 그대로
제출하지 않기 위한 fail-closed) — 호출자가 섹션을 줄이거나 텍스트를
줄여 재시도해야 한다.

전형적 구성 순서(동일 조사 기준): 브랜드 소개 → 대표 이미지 →
주요 특장점(3개 이내) → 상품 상세정보(스펙) → 사용법. 이 모듈의
generate_detail_page_image()도 이 순서를 그대로 따른다.
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from dataclasses import field

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from app.core.exceptions import BadRequestException
from app.domains.media_asset.image_concepts import PURPOSE_DETAIL
from app.domains.media_asset.image_concepts import get_concept
from app.domains.media_asset.image_concepts import require_usable

DETAIL_IMAGE_WIDTH = 780
# app/domains/media_asset/constants.py::MAX_IMAGE_HEIGHT와 동일 —
# 표준 validate_image_bytes() 검증 경로를 그대로 통과하기 위함(모듈
# 상단 docstring 참고). 쿠팡 실제 한도(5,000px)보다 보수적이다.
DETAIL_IMAGE_MAX_HEIGHT = 4096

_MAX_FEATURE_HIGHLIGHTS = 3

_PADDING = 32
_WHITE = (255, 255, 255)
_TEXT_DARK = (34, 34, 34)
_TEXT_MUTED = (110, 110, 110)
_ROW_SHADE = (245, 246, 248)

DEFAULT_ACCENT_HEX = "#2D6CDF"

# 상세 페이지를 이루는 섹션(그리는 순서). 섹션마다 컨셉을 따로 고를 수 있다.
DETAIL_SECTION_KEYS = ("BRAND", "HERO", "FEATURES", "SPECS", "USAGE")
DETAIL_SECTION_LABELS_KO = {
    "BRAND": "브랜드 소개", "HERO": "대표 사진", "FEATURES": "주요 특장점",
    "SPECS": "스펙 표", "USAGE": "사용법",
}

# Windows 표준 폰트(맑은 고딕) — HOMEZ는 Windows 데스크톱 전용이므로
# Vista 이후 모든 지원 버전에 기본 포함되어 있다. 별도 폰트 파일을
# 번들링하지 않는다(라이선스·용량 추가 없이 항상 사용 가능).
_FONT_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\malgunbd.ttf",
    r"C:\Windows\Fonts\malgun.ttf",
]
_FONT_REGULAR_CANDIDATES = [
    r"C:\Windows\Fonts\malgun.ttf",
]


class DetailPageFontUnavailableError(Exception):
    """한글 렌더링 가능한 폰트를 찾지 못했다(Windows 표준 폰트 없음)."""


class DetailPageTooTallError(BadRequestException):
    """조합한 상세이미지 총 높이가 쿠팡 최대 규격(5,000px)을 넘는다."""


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    raise DetailPageFontUnavailableError(
        "한글을 렌더링할 폰트를 찾지 못했습니다 — Windows 표준 폰트"
        "(맑은 고딕)가 설치되어 있어야 합니다.",
    )


def _parse_hex_color(value: str) -> tuple[int, int, int]:

    v = value.lstrip("#")
    if len(v) != 6 or not all(c in "0123456789abcdefABCDEF" for c in v):
        raise BadRequestException(f"잘못된 색상 코드입니다: {value}")

    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """한글은 공백 단위 word-wrap만으로는 긴 조사·복합어에서 폭을
    넘기기 쉬워, 문자 단위로 누적 폭을 확인하며 줄바꿈한다(단순하지만
    항상 max_width를 넘기지 않는다는 것을 보장)."""

    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        width = draw.textlength(candidate, font=font)
        if width > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)

    return lines or [""]


@dataclass(frozen=True)
class SectionTheme:

    bg: tuple[int, int, int]
    text: tuple[int, int, int]
    muted: tuple[int, int, int]
    shade: tuple[int, int, int]


DEFAULT_THEME = SectionTheme(_WHITE, _TEXT_DARK, _TEXT_MUTED, _ROW_SHADE)


@dataclass(frozen=True)
class FeatureHighlight:

    title: str
    description: str


@dataclass(frozen=True)
class SpecRow:

    label: str
    value: str


@dataclass(frozen=True)
class DetailPageContent:

    brand_name: str
    hero_image_bytes: bytes
    tagline: str | None = None
    feature_highlights: list[FeatureHighlight] = field(default_factory=list)
    spec_rows: list[SpecRow] = field(default_factory=list)
    usage_text: str | None = None
    accent_color_hex: str = DEFAULT_ACCENT_HEX
    # 컨셉(image_concepts.py). concept_code는 페이지 전체, section_concepts는 섹션별 덮어쓰기.
    # 둘 다 없으면 기존과 똑같은 흰 배경이다.
    concept_code: str | None = None
    section_concepts: dict[str, str] = field(default_factory=dict)


def _render_brand_section(
    brand_name: str, tagline: str | None, accent_rgb: tuple[int, int, int],
    width: int, theme: SectionTheme = DEFAULT_THEME,
) -> Image.Image:

    title_font = _load_font(_FONT_BOLD_CANDIDATES, 40)
    tagline_font = _load_font(_FONT_REGULAR_CANDIDATES, 22)

    tmp = Image.new("RGB", (width, 10), theme.bg)
    draw = ImageDraw.Draw(tmp)

    max_text_width = width - _PADDING * 2
    brand_lines = _wrap_text(draw, brand_name, title_font, max_text_width)
    tagline_lines = (
        _wrap_text(draw, tagline, tagline_font, max_text_width)
        if tagline else []
    )

    line_height_title = title_font.getbbox("가")[3] + 12
    line_height_tagline = tagline_font.getbbox("가")[3] + 8

    height = (
        _PADDING
        + len(brand_lines) * line_height_title
        + (len(tagline_lines) * line_height_tagline if tagline_lines else 0)
        + _PADDING
    )

    img = Image.new("RGB", (width, height), theme.bg)
    draw = ImageDraw.Draw(img)

    y = _PADDING
    for line in brand_lines:
        draw.text((_PADDING, y), line, font=title_font, fill=accent_rgb)
        y += line_height_title
    for line in tagline_lines:
        draw.text((_PADDING, y), line, font=tagline_font, fill=theme.muted)
        y += line_height_tagline

    return img


def _render_hero_section(hero_image_bytes: bytes, width: int) -> Image.Image:

    hero = Image.open(io.BytesIO(hero_image_bytes)).convert("RGB")
    scale = width / hero.width
    new_size = (width, max(1, int(hero.height * scale)))
    resized = hero.resize(new_size, Image.LANCZOS)

    return resized


def _render_feature_section(
    highlights: list[FeatureHighlight], accent_rgb: tuple[int, int, int],
    width: int, theme: SectionTheme = DEFAULT_THEME,
) -> Image.Image:

    title_font = _load_font(_FONT_BOLD_CANDIDATES, 26)
    desc_font = _load_font(_FONT_REGULAR_CANDIDATES, 20)

    tmp = Image.new("RGB", (width, 10), theme.bg)
    draw = ImageDraw.Draw(tmp)

    bar_width = 6
    text_x = _PADDING + bar_width + 16
    max_text_width = width - text_x - _PADDING

    line_height_title = title_font.getbbox("가")[3] + 8
    line_height_desc = desc_font.getbbox("가")[3] + 6
    block_gap = 28

    blocks: list[tuple[list[str], list[str]]] = []
    total_height = _PADDING
    for h in highlights:
        title_lines = _wrap_text(draw, h.title, title_font, max_text_width)
        desc_lines = _wrap_text(draw, h.description, desc_font, max_text_width)
        blocks.append((title_lines, desc_lines))
        total_height += (
            len(title_lines) * line_height_title
            + len(desc_lines) * line_height_desc
            + block_gap
        )
    total_height += _PADDING - block_gap

    img = Image.new("RGB", (width, max(total_height, 1)), theme.bg)
    draw = ImageDraw.Draw(img)

    y = _PADDING
    for title_lines, desc_lines in blocks:
        block_height = (
            len(title_lines) * line_height_title
            + len(desc_lines) * line_height_desc
        )
        draw.rectangle(
            [(_PADDING, y), (_PADDING + bar_width, y + block_height)],
            fill=accent_rgb,
        )
        ty = y
        for line in title_lines:
            draw.text((text_x, ty), line, font=title_font, fill=theme.text)
            ty += line_height_title
        for line in desc_lines:
            draw.text((text_x, ty), line, font=desc_font, fill=theme.muted)
            ty += line_height_desc
        y += block_height + block_gap

    return img


def _render_spec_table_section(
    spec_rows: list[SpecRow], width: int, theme: SectionTheme = DEFAULT_THEME,
) -> Image.Image:

    label_font = _load_font(_FONT_BOLD_CANDIDATES, 20)
    value_font = _load_font(_FONT_REGULAR_CANDIDATES, 20)

    tmp = Image.new("RGB", (width, 10), theme.bg)
    draw = ImageDraw.Draw(tmp)

    label_x = _PADDING
    value_x = width // 3
    max_value_width = width - value_x - _PADDING
    row_padding_y = 14

    line_height = value_font.getbbox("가")[3]

    rows_rendered: list[tuple[str, list[str], int]] = []
    total_height = _PADDING
    for row in spec_rows:
        value_lines = _wrap_text(draw, row.value, value_font, max_value_width)
        row_height = (
            max(len(value_lines), 1) * (line_height + 4) + row_padding_y * 2
        )
        rows_rendered.append((row.label, value_lines, row_height))
        total_height += row_height
    total_height += _PADDING

    img = Image.new("RGB", (width, max(total_height, 1)), theme.bg)
    draw = ImageDraw.Draw(img)

    y = _PADDING
    for i, (label, value_lines, row_height) in enumerate(rows_rendered):
        if i % 2 == 1:
            draw.rectangle(
                [(0, y), (width, y + row_height)], fill=theme.shade,
            )
        text_y = y + row_padding_y
        draw.text((label_x, text_y), label, font=label_font, fill=theme.text)
        for line in value_lines:
            draw.text(
                (value_x, text_y), line, font=value_font, fill=theme.text,
            )
            text_y += line_height + 4
        y += row_height

    return img


def _render_usage_section(
    usage_text: str, width: int, theme: SectionTheme = DEFAULT_THEME,
) -> Image.Image:

    font = _load_font(_FONT_REGULAR_CANDIDATES, 20)

    tmp = Image.new("RGB", (width, 10), theme.bg)
    draw = ImageDraw.Draw(tmp)

    max_text_width = width - _PADDING * 2
    lines = _wrap_text(draw, usage_text, font, max_text_width)
    line_height = font.getbbox("가")[3] + 8
    height = _PADDING * 2 + len(lines) * line_height

    img = Image.new("RGB", (width, height), theme.bg)
    draw = ImageDraw.Draw(img)

    y = _PADDING
    for line in lines:
        draw.text((_PADDING, y), line, font=font, fill=theme.muted)
        y += line_height

    return img


def _theme_for(content: DetailPageContent, key: str) -> tuple[SectionTheme, tuple[int, int, int]]:
    """섹션의 색 테마와 포인트 색. 컨셉이 없으면 기존 흰 배경·기존 포인트 색 그대로."""

    code = content.section_concepts.get(key) or content.concept_code
    accent = _parse_hex_color(content.accent_color_hex)
    if code is None:
        return DEFAULT_THEME, accent
    concept = require_usable(code, PURPOSE_DETAIL)
    t = concept.theme
    # 사용자가 포인트 색을 직접 바꿨다면(기본값이 아니면) 그 색을 존중한다.
    if content.accent_color_hex.upper() == DEFAULT_ACCENT_HEX.upper():
        accent = t.accent
    return SectionTheme(bg=t.bg, text=t.text, muted=t.muted, shade=t.shade), accent


def render_detail_sections(
    content: DetailPageContent, width: int = DETAIL_IMAGE_WIDTH,
) -> list[tuple[str, Image.Image]]:
    """(섹션 키, 그려진 섹션 이미지) 목록 — 섹션 경계가 곧 이 목록의 경계다."""

    if not content.brand_name.strip():
        raise BadRequestException("brand_name은 비어 있을 수 없습니다.")
    if not content.hero_image_bytes:
        raise BadRequestException("hero_image_bytes는 필수입니다.")
    for key in content.section_concepts:
        if key not in DETAIL_SECTION_KEYS:
            raise BadRequestException(f"알 수 없는 상세 섹션입니다: {key}")
    if content.concept_code is not None:
        get_concept(content.concept_code)

    sections: list[tuple[str, Image.Image]] = []
    theme, accent = _theme_for(content, "BRAND")
    sections.append(("BRAND", _render_brand_section(content.brand_name, content.tagline, accent, width, theme)))
    sections.append(("HERO", _render_hero_section(content.hero_image_bytes, width)))
    if content.feature_highlights:
        theme, accent = _theme_for(content, "FEATURES")
        sections.append(("FEATURES", _render_feature_section(
            content.feature_highlights[:_MAX_FEATURE_HIGHLIGHTS], accent, width, theme,
        )))
    if content.spec_rows:
        theme, _ = _theme_for(content, "SPECS")
        sections.append(("SPECS", _render_spec_table_section(content.spec_rows, width, theme)))
    if content.usage_text:
        theme, _ = _theme_for(content, "USAGE")
        sections.append(("USAGE", _render_usage_section(content.usage_text, width, theme)))
    return sections


def generate_detail_page_image(
    content: DetailPageContent, width: int = DETAIL_IMAGE_WIDTH,
) -> bytes:
    """쿠팡 상세이미지 1장을 JPEG 바이트로 만든다. 섹션 순서는
    브랜드 소개 → 대표 이미지 → 주요 특장점(최대 3개, 초과분은 조용히
    잘라낸다 — 실제 쿠팡 셀러 가이드가 권장하는 상한) → 스펙 표 →
    사용법이다. 섹션마다 컨셉(색 테마)을 고를 수 있다(`concept_code`,
    `section_concepts` — 없으면 기존과 동일한 흰 배경). 총 높이가 5,000px를
    넘으면 DetailPageTooTallError로 막는다(잘린 이미지를 그대로 제출하지
    않기 위한 fail-closed)."""

    sections = [image for _key, image in render_detail_sections(content, width)]

    total_height = sum(s.height for s in sections)
    if total_height > DETAIL_IMAGE_MAX_HEIGHT:
        raise DetailPageTooTallError(
            f"상세이미지 총 높이({total_height}px)가 쿠팡 최대 규격"
            f"({DETAIL_IMAGE_MAX_HEIGHT}px)을 넘습니다 — 특장점·스펙·"
            "사용법 텍스트를 줄이거나 항목 수를 줄여 다시 시도하세요.",
        )

    # 배경 색은 첫 섹션이 아니라 섹션 사이 빈틈 없이 이어 붙이므로 기본은 흰색이다.
    canvas = Image.new("RGB", (width, total_height), _WHITE)
    y = 0
    for section in sections:
        canvas.paste(section, (0, y))
        y += section.height

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=90)

    return out.getvalue()


__all__ = [
    "DETAIL_IMAGE_WIDTH",
    "DETAIL_IMAGE_MAX_HEIGHT",
    "DetailPageFontUnavailableError",
    "DetailPageTooTallError",
    "FeatureHighlight",
    "SpecRow",
    "DetailPageContent",
    "generate_detail_page_image",
    "render_detail_sections",
    "DETAIL_SECTION_KEYS",
    "DETAIL_SECTION_LABELS_KO",
    "SectionTheme",
]
