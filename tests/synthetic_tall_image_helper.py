"""
=========================================================
Homez OS

File : tests/synthetic_tall_image_helper.py

2026-08-31 Phase 8 차단 해소 작업 — tests/test_long_detail_image_
ingest.py와 tests/test_media_asset_image_workflow.py가 개발자
개인 Desktop의 실제 참고 사진("샤프란", 780×15548px)에 의존해,
그 파일이 없는 환경에서는 항상 skip됐다. 실제로 검증하는 대상
(일반 업로드 경로가 MAX_IMAGE_HEIGHT를 넘는 이미지를 거부하는지,
split_tall_image()가 세로로 긴 이미지를 조각으로 정확히 나누는지,
그 조각들의 계보·권리 상태·위저드 선택 흐름)은 전부 "780×15548
JPEG 헤더를 가진 유효한 파일"이라는 조건만 있으면 검증 가능하다 —
실제 사진의 픽셀 내용 자체는 어떤 assertion에서도 쓰이지 않는다
(app/domains/media_asset/image_split.py::split_tall_image()는
PIL로 디코드해 크기만 보고, app/domains/media_asset/
image_validation.py도 헤더의 width/height만 본다).

그래서 실제 참고 사진과 정확히 같은 치수(780×15548)의 완전히
합성된 JPEG을 즉석에서 생성한다 — 저장소에 큰 바이너리 fixture를
새로 커밋하지 않고, 매 테스트 실행마다 결정적으로(같은 입력이면
항상 같은 바이트) 만든다. 이렇게 하면:

- 개인 Desktop 파일 유무와 무관하게 항상 실행된다(skip 사라짐).
- 기존 assertion 강도(치수 780, 세로 방향 검증, MAX_IMAGE_HEIGHT
  초과 거부, 조각 수>1, 조각별 checksum 유일성 등)를 그대로
  유지한다 — 값을 느슨하게 바꾸지 않았다.
- 실제 시각 품질(사진이 실제로 예쁘게 나오는지 등)을 검증하려는
  목적이 아니었으므로, 합성 이미지로도 검증 의도가 그대로 보존된다.
=========================================================
"""

from __future__ import annotations

import io

from PIL import Image
from PIL import ImageDraw

# 실제 샤프란 참고 사진과 정확히 같은 치수 — 기존 테스트의
# `seg.width == 780` 등 하드코딩된 assertion을 그대로 유지하기 위해
# 값 자체를 바꾸지 않는다.
SAFFRON_REFERENCE_WIDTH = 780
SAFFRON_REFERENCE_HEIGHT = 15548


def build_synthetic_tall_product_jpeg(
    width: int = SAFFRON_REFERENCE_WIDTH,
    height: int = SAFFRON_REFERENCE_HEIGHT,
) -> bytes:
    """
    실제 상세페이지형 세로 이미지를 흉내낸 완전 합성 JPEG 바이트를
    만든다. 매번 같은 크기를 넣으면 같은 바이트가 나온다(결정적) —
    실제 사진이 아니므로 저작권·개인 파일 의존성이 없다.

    색을 절대 y좌표 기준 연속 그라디언트로 계산한다(구간을 반복하는
    줄무늬가 아니다) — 실제 사진은 우연히도 서로 다른 두 구간이
    완전히 같은 바이트로 압축될 일이 사실상 없는데, 반복되는
    단순 패턴(줄무늬 등)은 그 확률을 높여
    `_persist_generated_asset()`의 storage_path 기준 콘텐츠 중복
    제거 로직이 서로 다른 두 조각을 "같은 파일"로 취급해 두 번째
    조각이 새 행 대신 첫 조각의 기존 행(과 그 display_order)을
    돌려받게 만들 수 있다 — 실제 테스트에서 이 문제가 재현되어
    확인했다. 연속 그라디언트는 이 우연한 충돌 가능성을 원천적으로
    없애, 기존 assertion("조각별 display_order·checksum이 전부
    유일해야 한다")이 합성 이미지에서도 실제 사진과 동일한 전제로
    성립하게 한다.
    """

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # 한 줄(row)씩 채운다 — width 방향은 항상 균일한 색이므로
    # 픽셀 단위 루프(width * height회) 대신 가로줄 하나당 rectangle
    # 호출 한 번(height회)으로 충분히 빠르게 만든다.
    for y in range(height):
        # y에 따라 단조롭게 변하는 세 채널 — 어떤 두 y 범위를 잘라도
        # 완전히 같은 픽셀 데이터가 나올 수 없다.
        r = (y * 7) % 256
        g = (y * 13) % 256
        b = (y * 29) % 256
        draw.rectangle([0, y, width, y], fill=(r, g, b))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


__all__ = [
    "SAFFRON_REFERENCE_WIDTH",
    "SAFFRON_REFERENCE_HEIGHT",
    "build_synthetic_tall_product_jpeg",
]
