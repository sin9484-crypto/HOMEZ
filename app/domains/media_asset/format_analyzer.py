"""
=========================================================
Homez OS

File : app/domains/media_asset/format_analyzer.py

이미지 "형식" 분석기(이미지 형식 학습·응용 AI 설계 P1, 2026-10-14).
docs/HOMEZ_V7_IMAGE_FORMAT_LEARNING_AI_DESIGN_20261014.md 참고.

이미지 한 장에서 **형식 사실**만 뽑는다 — 크기·종횡비·파일 형식·배경 종류·흰 영역 비율·대표 색·
(세로로 긴 상세 이미지의) 섹션 경계와 섹션별 성격 힌트. 픽셀 자체나 제품의 모습을 복제·학습하지
않고, 결과는 JSON으로 직렬화되는 수치·분류뿐이다.

원칙(기존 media_asset 원칙과 동일):
- 순수 로컬 Pillow 연산, 외부 네트워크·DB 접근 없음, 신규 의존성 없음.
- 읽지 못하거나 해석할 수 없는 이미지는 추측하지 않고 `ImageFormatAnalysisError`로 거부한다.
- 같은 바이트는 항상 같은 결과를 낸다(결정적).
- 쿠팡 규격 점검(`check_coupang_rules`)은 **발견 사항만 알린다** — 자동 보정·재생성은 하지 않는다.
- 이 모듈은 권리 판단을 하지 않는다. 학습·저장에 쓰기 전의 권리 확인(`ImageRightsEvidence`)은 호출부 책임이다.
=========================================================
"""

from __future__ import annotations

import io
import statistics
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Sequence

from PIL import Image
from PIL import UnidentifiedImageError

ANALYZER_VERSION = "format-analyzer/1"

# 분석용 축소 폭(가로). 세로는 비율대로 줄어든다 — 20,000px 상세 이미지도 수천 줄로 줄여 분석한다.
ANALYSIS_WIDTH = 96
MAX_PIXELS = 80_000_000

WHITE_THRESHOLD = 245
BLANK_TOLERANCE = 6          # 한 줄의 (최대-최소) 밝기가 이 값 이하면 "빈 줄"
MIN_GAP_PX = 40              # 원본 기준 이 높이 이상 이어진 빈 줄만 섹션 경계로 본다
MIN_SECTION_PX = 60          # 원본 기준 이보다 낮은 내용 덩어리는 섹션으로 세지 않는다
TALL_RATIO = 3.0             # 세로/가로가 이 값 이상이면 "세로로 긴 상세 이미지"
PHOTO_VARIETY = 120          # 섹션 안 서로 다른 색(4비트 양자화) 수가 이 이상이면 사진 같은 영역 힌트

# 쿠팡 규격(코드 내 기존 값과 동일 — detail_page_generator.py, image_split.py, 2026-10-14 학습 기록)
COUPANG_MIN_SIDE = 500
COUPANG_DETAIL_WIDTH = 780
COUPANG_DETAIL_MAX_HEIGHT = 5000

LOW_SAMPLE_SIZE = 30         # 집계 표본이 이보다 적으면 "참고용"으로만 표시한다

_FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}


class ImageFormatAnalysisError(ValueError):
    """읽을 수 없거나 해석할 수 없는 이미지 — 추측 결과를 만들지 않는다."""


@dataclass(frozen=True)
class SectionProfile:
    start: int            # 원본 기준 시작 y(px)
    end: int              # 원본 기준 끝 y(px, 배타)
    height: int
    content_ratio: float  # 배경이 아닌 픽셀 비율(0~1) — 글자·그림이 얼마나 빽빽한가
    kind_hint: str        # PHOTO_LIKE | GRAPHIC_OR_TEXT — 힌트일 뿐 확정 분류가 아니다


@dataclass(frozen=True)
class ImageFormatProfile:
    analyzer_version: str
    width: int
    height: int
    aspect_ratio: float
    mime_type: str
    file_bytes: int
    background_kind: str            # WHITE | SOLID | COMPLEX
    background_color: str           # #rrggbb(네 모서리 평균)
    white_ratio: float
    palette: tuple[tuple[str, float], ...]
    is_tall: bool
    sections: tuple[SectionProfile, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["palette"] = [list(item) for item in self.palette]
        return data


@dataclass(frozen=True)
class RuleFinding:
    code: str
    severity: str   # ERROR | WARNING
    message: str


# ---------------------------------------------------------------------
# 이미지 열기·축소
# ---------------------------------------------------------------------

def _open(image_bytes: bytes) -> tuple[Image.Image, str]:
    if not image_bytes:
        raise ImageFormatAnalysisError("이미지 데이터가 비어 있습니다.")
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageFormatAnalysisError(f"이미지를 읽을 수 없습니다({type(exc).__name__}).") from exc
    if image.width <= 0 or image.height <= 0:
        raise ImageFormatAnalysisError("이미지 크기를 읽을 수 없습니다.")
    if image.width * image.height > MAX_PIXELS:
        raise ImageFormatAnalysisError("이미지가 분석 한도(픽셀 수)를 넘습니다.")
    mime = _FORMAT_TO_MIME.get((image.format or "").upper())
    if mime is None:
        raise ImageFormatAnalysisError(f"지원하지 않는 이미지 형식입니다: {image.format}")
    return image, mime


def _small_rgb(image: Image.Image) -> Image.Image:
    """투명 영역은 흰색 위에 올려 RGB로 만들고, 분석 폭으로 줄인다(메모리를 아끼려고 먼저 정수 배 축소)."""

    factor = max(1, image.width // (ANALYSIS_WIDTH * 2))
    reduced = image.reduce(factor) if factor > 1 else image
    if reduced.mode in ("RGBA", "LA") or (reduced.mode == "P" and "transparency" in reduced.info):
        rgba = reduced.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        reduced = canvas
    else:
        reduced = reduced.convert("RGB")
    new_height = max(1, round(image.height * ANALYSIS_WIDTH / image.width))
    return reduced.resize((ANALYSIS_WIDTH, new_height), Image.BOX)


# ---------------------------------------------------------------------
# 개별 특징
# ---------------------------------------------------------------------

def _hex(color: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in color)
    return f"#{r:02x}{g:02x}{b:02x}"


def _background(small: Image.Image) -> tuple[str, str]:
    block = max(2, min(small.width, small.height) // 12)
    corners = [
        (0, 0), (small.width - block, 0), (0, small.height - block), (small.width - block, small.height - block),
    ]
    means = []
    for x, y in corners:
        pixels = list(small.crop((x, y, x + block, y + block)).getdata())
        count = len(pixels)
        means.append(tuple(sum(p[i] for p in pixels) / count for i in range(3)))
    average = tuple(sum(m[i] for m in means) / len(means) for i in range(3))
    spread = max(abs(m[i] - average[i]) for m in means for i in range(3))
    if spread > 14:
        return "COMPLEX", _hex(average)
    if min(average) >= WHITE_THRESHOLD:
        return "WHITE", _hex(average)
    return "SOLID", _hex(average)


def _white_ratio(small: Image.Image) -> float:
    pixels = small.getdata()
    white = sum(1 for p in pixels if min(p) >= WHITE_THRESHOLD)
    return round(white / (small.width * small.height), 4)


def _palette(small: Image.Image, top: int = 3) -> tuple[tuple[str, float], ...]:
    bins: dict[tuple[int, int, int], list[float]] = {}
    total = small.width * small.height
    for r, g, b in small.getdata():
        key = (r >> 5, g >> 5, b >> 5)
        slot = bins.setdefault(key, [0, 0.0, 0.0, 0.0])
        slot[0] += 1
        slot[1] += r
        slot[2] += g
        slot[3] += b
    ranked = sorted(bins.values(), key=lambda s: (-s[0], s[1], s[2], s[3]))[:top]
    return tuple((_hex((s[1] / s[0], s[2] / s[0], s[3] / s[0])), round(s[0] / total, 4)) for s in ranked)


def _find_sections(small: Image.Image, original_width: int) -> tuple[SectionProfile, ...]:
    scale = small.width / original_width
    min_gap_rows = max(2, round(MIN_GAP_PX * scale))
    min_section_rows = max(3, round(MIN_SECTION_PX * scale))
    gray = small.convert("L")
    data = gray.tobytes()
    width = gray.width
    rows = gray.height
    blank = [
        (max(data[r * width:(r + 1) * width]) - min(data[r * width:(r + 1) * width])) <= BLANK_TOLERANCE
        for r in range(rows)
    ]

    spans: list[tuple[int, int]] = []
    start = None
    blank_run = 0
    for r in range(rows):
        if blank[r]:
            if start is not None:
                blank_run += 1
                if blank_run >= min_gap_rows:
                    spans.append((start, r - blank_run + 1))
                    start = None
                    blank_run = 0
        else:
            if start is None:
                start = r
            blank_run = 0
    if start is not None:
        spans.append((start, rows - blank_run))

    # 페이지 배경 밝기 — 빈 줄들의 가장 흔한 밝기(빈 줄이 없으면 이미지 전체의 가장 흔한 밝기).
    blank_levels = [data[r * width] for r in range(rows) if blank[r]]
    background_gray = Counter(blank_levels or list(data)).most_common(1)[0][0]

    sections = []
    to_px = original_width / small.width
    for s, e in spans:
        if e - s < min_section_rows:
            continue
        block = small.crop((0, s, small.width, e))
        sections.append(SectionProfile(
            start=int(round(s * to_px)), end=int(round(e * to_px)), height=int(round((e - s) * to_px)),
            content_ratio=_content_ratio(block, background_gray), kind_hint=_kind_hint(block),
        ))
    return tuple(sections)


def _content_ratio(block: Image.Image, background_gray: int) -> float:
    """구역에서 페이지 배경과 크게 다른 픽셀의 비율 — 글자·그림이 얼마나 빽빽한가."""

    gray = list(block.convert("L").getdata())
    differing = sum(1 for g in gray if abs(g - background_gray) > 32)
    return round(differing / len(gray), 4)


def _kind_hint(block: Image.Image) -> str:
    variety = len({(r >> 4, g >> 4, b >> 4) for r, g, b in block.getdata()})
    return "PHOTO_LIKE" if variety >= PHOTO_VARIETY else "GRAPHIC_OR_TEXT"


# ---------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------

def analyze_image_bytes(image_bytes: bytes) -> ImageFormatProfile:
    """이미지 한 장의 형식 프로필을 만든다. 읽지 못하면 `ImageFormatAnalysisError`."""

    image, mime = _open(image_bytes)
    width, height = image.width, image.height
    small = _small_rgb(image)
    kind, color = _background(small)
    is_tall = (height / width) >= TALL_RATIO
    return ImageFormatProfile(
        analyzer_version=ANALYZER_VERSION, width=width, height=height,
        aspect_ratio=round(width / height, 4), mime_type=mime, file_bytes=len(image_bytes),
        background_kind=kind, background_color=color, white_ratio=_white_ratio(small),
        palette=_palette(small), is_tall=is_tall,
        sections=_find_sections(small, width) if is_tall else (),
    )


def check_coupang_rules(profile: ImageFormatProfile, purpose: str) -> list[RuleFinding]:
    """쿠팡 이미지 규격 점검 — 발견 사항만 돌려준다(자동 보정·재생성 없음).
    purpose: MAIN(대표·추가) | DETAIL(상세)."""

    findings: list[RuleFinding] = []
    if purpose == "MAIN":
        if min(profile.width, profile.height) < COUPANG_MIN_SIDE:
            findings.append(RuleFinding(
                "MIN_SIZE", "ERROR",
                f"가로·세로가 {COUPANG_MIN_SIDE}px 이상이어야 합니다({profile.width}x{profile.height}).",
            ))
        if abs(profile.aspect_ratio - 1.0) > 0.02:
            findings.append(RuleFinding("NOT_SQUARE", "WARNING", "대표 이미지는 정사각형(1:1)을 권장합니다."))
        if profile.background_kind != "WHITE":
            findings.append(RuleFinding("BACKGROUND_NOT_WHITE", "WARNING", "대표 이미지는 흰 배경을 권장합니다."))
    elif purpose == "DETAIL":
        if profile.width != COUPANG_DETAIL_WIDTH:
            findings.append(RuleFinding(
                "DETAIL_WIDTH", "WARNING",
                f"상세 이미지 가로는 {COUPANG_DETAIL_WIDTH}px를 권장합니다({profile.width}px) — 분할·리사이즈가 필요합니다.",
            ))
        if profile.height > COUPANG_DETAIL_MAX_HEIGHT:
            findings.append(RuleFinding(
                "DETAIL_TOO_TALL", "ERROR",
                f"상세 이미지 세로는 {COUPANG_DETAIL_MAX_HEIGHT}px 이하여야 합니다({profile.height}px) — 분할이 필요합니다.",
            ))
    else:
        raise ImageFormatAnalysisError(f"알 수 없는 이미지 용도입니다: {purpose}")
    return findings


def summarize_profiles(profiles: Sequence[ImageFormatProfile]) -> dict:
    """여러 프로필의 분포 요약. 표본이 적으면 `low_sample=True`로 표시해 규칙으로 고정하지 않게 한다."""

    count = len(profiles)
    if count == 0:
        return {"sample_size": 0, "low_sample": True}

    def shape(p: ImageFormatProfile) -> str:
        if p.is_tall:
            return "TALL"
        if abs(p.aspect_ratio - 1.0) <= 0.02:
            return "SQUARE"
        return "PORTRAIT" if p.aspect_ratio < 1 else "LANDSCAPE"

    section_heights = [s.height for p in profiles for s in p.sections]
    section_counts = [len(p.sections) for p in profiles if p.is_tall]
    return {
        "analyzer_version": ANALYZER_VERSION,
        "sample_size": count,
        "low_sample": count < LOW_SAMPLE_SIZE,
        "shapes": dict(Counter(shape(p) for p in profiles)),
        "sizes": dict(Counter(f"{p.width}x{p.height}" for p in profiles if not p.is_tall).most_common(10)),
        "mime_types": dict(Counter(p.mime_type for p in profiles)),
        "background_kinds": dict(Counter(p.background_kind for p in profiles)),
        "mean_white_ratio": round(statistics.fmean(p.white_ratio for p in profiles), 4),
        "tall_images": sum(1 for p in profiles if p.is_tall),
        "max_height": max(p.height for p in profiles),
        "sections_per_tall_image": {
            "min": min(section_counts), "median": statistics.median(section_counts), "max": max(section_counts),
        } if section_counts else None,
        "section_height_px": {
            "min": min(section_heights), "median": statistics.median(section_heights), "max": max(section_heights),
        } if section_heights else None,
        "photo_like_share": round(
            sum(1 for p in profiles for s in p.sections if s.kind_hint == "PHOTO_LIKE") / len(section_heights), 4,
        ) if section_heights else None,
    }


__all__ = [
    "ANALYZER_VERSION", "ImageFormatAnalysisError", "ImageFormatProfile", "RuleFinding", "SectionProfile",
    "analyze_image_bytes", "check_coupang_rules", "summarize_profiles",
]
