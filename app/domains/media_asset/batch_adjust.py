"""
=========================================================
Homez OS

File : app/domains/media_asset/batch_adjust.py

2026-10-23 — 이미지 AI R2(영상 19:33~20:07의 "선택한 사진 전부에 밝기·색온도를 한 번에 적용"): **로컬 보정**(생성이 아니다). Pillow만 쓰고(새 의존성 없음) 외부 호출·DB·파일 없음.

  - 밝기(`brightness`)와 색온도(`warmth`)를 정수 퍼센트로 받는다. 둘 다 −50~+50이고 0은 변화 없음. 범위를 넘는 값은 조용히 깎지 않고 **거절**한다(사진을 망가뜨리는 값이 몰래 적용되지 않게).
  - 밝기: 모든 색 채널에 같은 배율(1 + 퍼센트/100). 색온도: 양수는 따뜻하게(빨강↑·파랑↓), 음수는 차갑게. 배율은 ±30%까지(퍼센트 × 0.6%).
  - **투명도(알파)는 건드리지 않는다** — 누끼 이미지의 투명 영역과 가장자리가 그대로 남는다. 완전히 투명한 픽셀의 색도 바꾸지 않아 결과가 결정적이다.
  - 같은 입력·같은 값이면 같은 바이트가 나온다(PNG는 같은 설정으로 저장, JPEG는 품질 92 고정). 입력이 알파를 가지면 PNG, 아니면 원래 형식(JPEG 입력은 JPEG, 나머지는 PNG)으로 낸다.
  - **글자·워터마크·표식을 더하지 않는다**(사용자 지시) — 색만 바꾼다.
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from app.core.exceptions import BadRequestException

MIN_PERCENT, MAX_PERCENT = -50, 50
WARMTH_GAIN_PER_PERCENT = 0.006   # 색온도 퍼센트 1당 빨강·파랑 배율 변화(최대 ±30%)
MAX_BATCH = 20
JPEG_QUALITY = 92


@dataclass(frozen=True)
class Adjustment:
    brightness: int = 0
    warmth: int = 0

    def is_noop(self) -> bool:
        return self.brightness == 0 and self.warmth == 0

    def label(self) -> str:
        """파일 이름에 붙이는 짧은 표기(예: b+10w-5)."""
        return f"b{self.brightness:+d}w{self.warmth:+d}"


def validate_adjustment(brightness, warmth) -> Adjustment:
    """정수이고 범위 안인 값만 받는다(bool·실수·문자열은 거절). 둘 다 0이면 보정할 것이 없다."""

    for name, value in (("밝기", brightness), ("색온도", warmth)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadRequestException(f"{name} 값은 정수여야 합니다.")
        if not MIN_PERCENT <= value <= MAX_PERCENT:
            raise BadRequestException(f"{name} 값은 {MIN_PERCENT}~{MAX_PERCENT} 사이여야 합니다.")
    adjustment = Adjustment(brightness=brightness, warmth=warmth)
    if adjustment.is_noop():
        raise BadRequestException("밝기와 색온도가 모두 0이라 보정할 것이 없습니다.")
    return adjustment


def adjust_image(image_bytes: bytes, adjustment: Adjustment) -> tuple[bytes, str]:
    """사진 한 장을 보정한다. 반환: (이미지 바이트, MIME). 읽을 수 없는 이미지는 거절한다."""

    if adjustment.is_noop():
        raise BadRequestException("보정할 것이 없습니다.")
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            source_format = (opened.format or "").upper()
            has_alpha = opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info
            image = opened.convert("RGBA" if has_alpha else "RGB")
    except Exception as exc:  # noqa: BLE001 — 손상·지원하지 않는 형식
        raise BadRequestException("보정할 수 없는 이미지입니다.") from exc

    brightness_gain = 1.0 + adjustment.brightness / 100.0
    warm = adjustment.warmth * WARMTH_GAIN_PER_PERCENT
    gains = (brightness_gain * (1.0 + warm), brightness_gain, brightness_gain * (1.0 - warm))   # 빨강·초록·파랑 배율
    tables = [[min(255, max(0, int(value * gain + 0.5))) for value in range(256)] for gain in gains]
    if has_alpha:
        red, green, blue, alpha = image.split()
        adjusted = Image.merge("RGB", (red.point(tables[0]), green.point(tables[1]), blue.point(tables[2])))
        original = Image.merge("RGB", (red, green, blue))
        adjusted.paste(original, mask=alpha.point([255 if value == 0 else 0 for value in range(256)]))   # 완전 투명 픽셀의 색은 그대로
        adjusted.putalpha(alpha)
        result = adjusted
    else:
        red, green, blue = image.split()
        result = Image.merge("RGB", (red.point(tables[0]), green.point(tables[1]), blue.point(tables[2])))

    buffer = io.BytesIO()
    if not has_alpha and source_format == "JPEG":
        result.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=False, subsampling=0)
        return buffer.getvalue(), "image/jpeg"
    result.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue(), "image/png"


__all__ = ["Adjustment", "validate_adjustment", "adjust_image", "MIN_PERCENT", "MAX_PERCENT", "MAX_BATCH", "WARMTH_GAIN_PER_PERCENT", "JPEG_QUALITY"]
