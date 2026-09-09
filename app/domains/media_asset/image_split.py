"""
=========================================================
Homez OS

File : app/domains/media_asset/image_split.py

긴 상세 이미지 분할(2026-08-20 CTO 지시) — 여러 옵션(용량/향/리필
등)이 하나의 긴 이미지에 섞여 있을 때, 기계적으로 고정 높이 구간
으로 나눈다(내용 기반 의미 분할이 아니라 높이 기준 분할 — 실제
의미 단위 경계는 사용자가 육안으로 확인해 선택해야 한다, 결과 보고에
명시). 각 조각은 요구 사양(너비 780px, 높이 600~1200px, 최대
1500px)에 맞춰 리사이즈한다. 순수 Pillow 로컬 처리, 네트워크 없음.
=========================================================
"""

import io

from PIL import Image

from app.core.exceptions import BadRequestException

DETAIL_IMAGE_WIDTH = 780
MIN_SEGMENT_HEIGHT = 600
MAX_SEGMENT_HEIGHT = 1500
DEFAULT_SEGMENT_HEIGHT = 1200


def split_tall_image(
    image_bytes: bytes, segment_height: int = DEFAULT_SEGMENT_HEIGHT,
    target_width: int = DETAIL_IMAGE_WIDTH,
) -> list[bytes]:
    """
    세로로 긴 이미지를 `segment_height`(원본 비율 기준, 리사이즈 전)
    단위로 잘라 target_width에 맞춰 리사이즈한 PNG 조각 목록을
    반환한다. 원본 파일 자체는 건드리지 않는다(호출자가 원본을
    별도로 보존해야 한다 — 이 함수는 순수 함수, 파일 I/O 없음).
    """

    if not (MIN_SEGMENT_HEIGHT <= segment_height <= MAX_SEGMENT_HEIGHT):
        raise BadRequestException(
            f"segment_height는 {MIN_SEGMENT_HEIGHT}~{MAX_SEGMENT_HEIGHT} "
            f"사이여야 합니다: {segment_height}",
        )

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    scale = target_width / img.width
    scaled_height = int(img.height * scale)
    scaled = img.resize((target_width, scaled_height), Image.LANCZOS)

    segments = []
    y = 0
    while y < scaled.height:
        bottom = min(y + segment_height, scaled.height)
        # 마지막 조각이 최소 높이보다 작으면 바로 앞 조각에 흡수한다
        # (너무 작은 자투리 조각을 만들지 않기 위함).
        if scaled.height - bottom < MIN_SEGMENT_HEIGHT and bottom < scaled.height:
            bottom = scaled.height

        segment = scaled.crop((0, y, target_width, bottom))
        out = io.BytesIO()
        segment.save(out, format="PNG")
        segments.append(out.getvalue())

        y = bottom

    return segments


__all__ = [
    "split_tall_image",
    "DETAIL_IMAGE_WIDTH",
    "MIN_SEGMENT_HEIGHT",
    "MAX_SEGMENT_HEIGHT",
    "DEFAULT_SEGMENT_HEIGHT",
]
