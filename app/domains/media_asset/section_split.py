"""
=========================================================
Homez OS

File : app/domains/media_asset/section_split.py

섹션 경계 분할(이미지 형식 학습·응용 AI 설계 P3, 2026-10-14 사용자 요청).

세로로 긴 상세 이미지를 **고정 높이로 자르지 않고**, 섹션 사이의 빈 여백 한가운데에서 자른다 — 글자·표·사진이
중간에서 잘리지 않는다. 실측(7개 상품)에서 기존 고정 높이(1,200px) 분할이 38/59를 차지했고 의미 경계를 보지
않는다는 한계가 확인됐다(설계 문서 6절).

규칙:
- 조각의 가로는 `DETAIL_IMAGE_WIDTH`(780px)로 맞춘다(기존 `image_split.split_tall_image`와 동일).
- 조각 높이는 `min_height`~`max_height`(기본 600~1,500px) 안에서 `segment_height`(선호 높이)에 가장 가까운 섹션
  경계를 고른다. 자르고 남는 부분도 `min_height` 이상이 되게 범위를 제한하므로 작은 자투리 조각이 생기지 않는다
  (이미지 전체가 `min_height`보다 낮으면 한 조각).
- 경계가 될 만한 빈 여백이 범위 안에 없으면 가장 "빈" 줄에서 자르고 `cut_end="FORCED"`로 표시한다 — 몰래
  자르지 않고 사용자가 확인할 수 있게 알린다.
- **픽셀을 바꾸거나 버리지 않는다** — 조각을 이어 붙이면 리사이즈된 원본과 정확히 같다(테스트로 보장).
- 순수 Pillow, 외부 호출·DB 없음.
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from app.core.exceptions import BadRequestException
from app.domains.media_asset.format_analyzer import BLANK_TOLERANCE
from app.domains.media_asset.format_analyzer import MIN_GAP_PX
from app.domains.media_asset.image_split import DEFAULT_SEGMENT_HEIGHT
from app.domains.media_asset.image_split import DETAIL_IMAGE_WIDTH
from app.domains.media_asset.image_split import MAX_SEGMENT_HEIGHT
from app.domains.media_asset.image_split import MIN_SEGMENT_HEIGHT

CUT_SECTION = "SECTION"   # 충분히 넓은 빈 여백에서 잘랐다
CUT_THIN_GAP = "THIN_GAP"  # 좁은 빈 줄에서 잘랐다(여백이 40px 미만)
CUT_FORCED = "FORCED"     # 빈 줄이 없어 가장 비어 있는 줄에서 잘랐다 — 사용자 확인 필요
CUT_END = "END"           # 이미지 끝


@dataclass(frozen=True)
class SplitSegment:
    image_bytes: bytes  # PNG
    start: int          # 리사이즈된(780px 폭) 이미지 기준 시작 y
    end: int            # 끝 y(배타)
    cut_end: str        # 이 조각의 아래쪽 경계가 어떻게 정해졌는가

    @property
    def height(self) -> int:
        return self.end - self.start


def _row_ranges(image: Image.Image) -> list[int]:
    """각 줄의 (최대 밝기 - 최소 밝기). 작을수록 비어 있는 줄이다."""

    gray = image.convert("L")
    data = gray.tobytes()
    width = gray.width
    return [max(data[r * width:(r + 1) * width]) - min(data[r * width:(r + 1) * width]) for r in range(gray.height)]


def _blank_band_centers(ranges: list[int]) -> list[int]:
    """`MIN_GAP_PX` 이상 이어진 빈 줄 묶음의 가운데 줄 번호(섹션 경계 후보)."""

    centers = []
    run_start = None
    for r, value in enumerate(ranges + [BLANK_TOLERANCE + 1]):   # 끝 표지로 마지막 묶음을 닫는다
        if value <= BLANK_TOLERANCE:
            if run_start is None:
                run_start = r
        else:
            if run_start is not None and r - run_start >= MIN_GAP_PX:
                centers.append((run_start + r - 1) // 2)
            run_start = None
    # 맨 위·맨 아래의 여백은 경계 후보가 아니다(자르면 빈 조각이 생긴다).
    return [c for c in centers if 0 < c < len(ranges) - 1]


def plan_cuts(
    ranges: list[int], *, segment_height: int = DEFAULT_SEGMENT_HEIGHT,
    min_height: int = MIN_SEGMENT_HEIGHT, max_height: int = MAX_SEGMENT_HEIGHT,
) -> list[tuple[int, str]]:
    """(자를 줄 번호, 종류) 목록. 마지막 항목은 (전체 높이, END)."""

    total = len(ranges)
    bands = _blank_band_centers(ranges)
    cuts: list[tuple[int, str]] = []
    cursor = 0
    while total - cursor > max_height:
        # 자르고 남는 부분도 `min_height` 이상이 되게 범위의 위쪽을 제한한다 — 너무 작은 자투리 조각을 만들지 않는다.
        # (남는 부분이 max_height를 넘을 때만 이 반복이 도므로 low <= high가 항상 성립한다.)
        low, high = cursor + min_height, min(cursor + max_height, total - min_height)
        in_window = [c for c in bands if low <= c <= high]
        if in_window:
            cut = min(in_window, key=lambda c: (abs(c - (cursor + segment_height)), c))
            cuts.append((cut, CUT_SECTION))
        else:
            # 넓은 여백이 없다 — 범위 안에서 가장 빈 줄(같으면 선호 높이에 가까운 줄)에서 자른다.
            cut = min(range(low, high + 1), key=lambda r: (ranges[r], abs(r - (cursor + segment_height)), r))
            cuts.append((cut, CUT_THIN_GAP if ranges[cut] <= BLANK_TOLERANCE else CUT_FORCED))
        cursor = cut

    cuts.append((total, CUT_END))
    return cuts


def split_by_sections(
    image_bytes: bytes, segment_height: int = DEFAULT_SEGMENT_HEIGHT,
    target_width: int = DETAIL_IMAGE_WIDTH, *,
    min_height: int = MIN_SEGMENT_HEIGHT, max_height: int = MAX_SEGMENT_HEIGHT,
) -> list[SplitSegment]:
    """긴 상세 이미지를 섹션 경계에서 나눈 PNG 조각 목록(`SplitSegment`)."""

    if not (min_height <= segment_height <= max_height):
        raise BadRequestException(
            f"segment_height는 {min_height}~{max_height} 사이여야 합니다: {segment_height}",
        )
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise BadRequestException(f"이미지를 읽을 수 없습니다({type(exc).__name__}).") from exc

    scale = target_width / image.width
    scaled = image.resize((target_width, max(1, int(image.height * scale))), Image.LANCZOS)

    cuts = plan_cuts(
        _row_ranges(scaled), segment_height=segment_height, min_height=min_height, max_height=max_height,
    )
    segments: list[SplitSegment] = []
    top = 0
    for bottom, kind in cuts:
        piece = scaled.crop((0, top, target_width, bottom))
        buffer = io.BytesIO()
        piece.save(buffer, format="PNG")
        segments.append(SplitSegment(image_bytes=buffer.getvalue(), start=top, end=bottom, cut_end=kind))
        top = bottom
    return segments


__all__ = [
    "CUT_END", "CUT_FORCED", "CUT_SECTION", "CUT_THIN_GAP", "SplitSegment", "plan_cuts", "split_by_sections",
]
