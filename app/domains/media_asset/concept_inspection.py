"""
=========================================================
Homez OS

File : app/domains/media_asset/concept_inspection.py

2026-10-23 — 이미지 AI I2: 컨셉으로 만든 사진이 **규칙을 지켰는지 검사**하는 순수 함수(Pillow만, 외부 호출·DB·파일 없음). 컨셉 명세(I1)가 "이 컨셉은 어떤 검사를 요구한다"고 선언한 항목(`checks`)만 돌린다.

검사 결과의 상태(`status`):
  - PASS  기준을 넘었다 / FAIL 기준에 못 미쳤다(이 사진은 쓰지 않는 것이 좋다) / WARN 쓸 수는 있으나 눈으로 확인할 것이 있다
  - BY_CONSTRUCTION  코드가 그런 일을 아예 하지 않는다(예: 이 합성은 글자를 그리지 않는다) — **사진을 들여다본 검사가 아니다**
  - NOT_CHECKED  이 단계에는 검사 수단이 없다(예: 쿠팡 정책 적합 — 정책 조사 I0가 끝나지 않았다)
"통과"로 보이지 않게 한다: 검사하지 못한 것은 NOT_CHECKED이고, 하나라도 FAIL이면 그 사진은 `usable=False`다.

검사 항목(I1 `CHECK_*` 코드):
  - PRODUCT_PRESERVATION  합성 결과에서 제품 불투명 픽셀(알파 255)이 **원본 누끼와 같은 값 그대로**인 비율 ≥ 0.99 — 제품을 다시 그리지 않는다는 원칙을 숫자로 확인한다.
                          합성 규칙(`composition.compose_with_background`: 확대 없이 축소만, 중앙 정렬)을 그대로 따라가며, 그림자가 제품 위에 얹히지 않는다는 가정이 깨지면 FAIL로 드러난다.
  - BACKGROUND_CONTRAST   제품 평균 색과 배경(가장자리 띠) 평균 색의 밝기 대비 ≥ 1.3(PASS), 그 미만은 WARN("제품이 배경에 묻힐 수 있어요" — 사람이 보고 판단).
  - MAIN_IMAGE_SPEC       용도가 대표(MAIN)일 때만: 정사각형·한 변 1,000px 이상·배경 가장자리가 흰색(각 채널 ≥ 250). 아니면 FAIL. 대표가 아니면 SKIPPED(상태 PASS가 아님 — 해당 없음).
  - NO_TEXT_OVERLAY       BY_CONSTRUCTION(합성이 글자를 그리지 않는다) — 컨셉이 요구한 경우에만 표시.
  - POLICY_FLAGS          NOT_CHECKED(쿠팡 정책 적합 검사는 아직 없다 — I0 미확인).
=========================================================
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image

PRESERVATION_MIN = 0.99
CONTRAST_MIN = 1.3
MAIN_MIN_SIDE = 1000
WHITE_MIN_CHANNEL = 250
EDGE_BAND_FRACTION = 0.06   # 배경 평균을 재는 가장자리 띠의 두께(짧은 변 대비)

PASS, FAIL, WARN, BY_CONSTRUCTION, NOT_CHECKED, SKIPPED = "PASS", "FAIL", "WARN", "BY_CONSTRUCTION", "NOT_CHECKED", "SKIPPED"


@dataclass(frozen=True)
class CheckResult:
    code: str
    status: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "status": self.status, "detail": dict(self.detail)}


def _linear(channel: int) -> float:
    value = channel / 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_between(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _placement(cutout: Image.Image, canvas: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """`composition.compose_with_background`와 같은 규칙: 확대 없이 축소만, 중앙 정렬."""
    scale = min(canvas[0] / cutout.width, canvas[1] / cutout.height, 1.0)
    size = (max(1, int(cutout.width * scale)), max(1, int(cutout.height * scale)))
    offset = ((canvas[0] - size[0]) // 2, (canvas[1] - size[1]) // 2)
    return size, offset


def product_preservation(cutout_png: bytes, composed_png: bytes) -> dict[str, Any]:
    """제품의 불투명 픽셀이 합성 결과에서 같은 값으로 남은 비율. 반환: {"ratio": 0~1, "pixels": 불투명 픽셀 수}."""

    cutout = Image.open(io.BytesIO(cutout_png)).convert("RGBA")
    composed = Image.open(io.BytesIO(composed_png)).convert("RGB")
    size, offset = _placement(cutout, composed.size)
    resized = cutout.resize(size, Image.LANCZOS)
    region = composed.crop((offset[0], offset[1], offset[0] + size[0], offset[1] + size[1]))
    original_pixels, composed_pixels, alpha_pixels = resized.convert("RGB").getdata(), region.getdata(), resized.getchannel("A").getdata()
    same = total = 0
    for original, got, alpha in zip(original_pixels, composed_pixels, alpha_pixels):
        if alpha == 255:
            total += 1
            if original == got:
                same += 1
    return {"ratio": (same / total) if total else 0.0, "pixels": total}


def _edge_average(image: Image.Image) -> tuple[int, int, int]:
    width, height = image.size
    band = max(1, int(min(width, height) * EDGE_BAND_FRACTION))
    pieces = [image.crop((0, 0, width, band)), image.crop((0, height - band, width, height)), image.crop((0, band, band, height - band)), image.crop((width - band, band, width, height - band))]
    totals, count = [0, 0, 0], 0
    for piece in pieces:
        for r, g, b in piece.getdata():
            totals[0] += r
            totals[1] += g
            totals[2] += b
            count += 1
    return tuple(int(t / count + 0.5) for t in totals)  # type: ignore[return-value]


def _product_average(cutout_png: bytes) -> tuple[int, int, int] | None:
    cutout = Image.open(io.BytesIO(cutout_png)).convert("RGBA")
    totals, count = [0, 0, 0], 0
    for r, g, b, a in cutout.getdata():
        if a == 255:
            totals[0] += r
            totals[1] += g
            totals[2] += b
            count += 1
    if not count:
        return None
    return tuple(int(t / count + 0.5) for t in totals)  # type: ignore[return-value]


def inspect(cutout_png: bytes, composed_png: bytes, required_checks: tuple[str, ...], purpose: str) -> list[CheckResult]:
    """컨셉이 요구한 검사만 돌린다(요구한 순서 그대로). 알 수 없는 검사 코드는 NOT_CHECKED로 남긴다(조용히 빼지 않는다)."""

    composed = Image.open(io.BytesIO(composed_png)).convert("RGB")
    results: list[CheckResult] = []
    for code in required_checks:
        if code == "PRODUCT_PRESERVATION":
            measured = product_preservation(cutout_png, composed_png)
            ok = measured["pixels"] > 0 and measured["ratio"] >= PRESERVATION_MIN
            results.append(CheckResult(code, PASS if ok else FAIL, {"ratio": round(measured["ratio"], 4), "pixels": measured["pixels"], "min": PRESERVATION_MIN}))
        elif code == "BACKGROUND_CONTRAST":
            product = _product_average(cutout_png)
            if product is None:
                results.append(CheckResult(code, FAIL, {"reason": "NO_OPAQUE_PIXELS"}))
                continue
            edge = _edge_average(composed)
            ratio = contrast_between(product, edge)
            results.append(CheckResult(code, PASS if ratio >= CONTRAST_MIN else WARN, {"ratio": round(ratio, 2), "min": CONTRAST_MIN}))
        elif code == "MAIN_IMAGE_SPEC":
            if purpose != "MAIN":
                results.append(CheckResult(code, SKIPPED, {"reason": "NOT_MAIN_PURPOSE"}))
                continue
            width, height = composed.size
            edge = _edge_average(composed)
            ok = width == height and width >= MAIN_MIN_SIDE and min(edge) >= WHITE_MIN_CHANNEL
            results.append(CheckResult(code, PASS if ok else FAIL, {"width": width, "height": height, "edge_min_channel": min(edge), "min_side": MAIN_MIN_SIDE}))
        elif code == "NO_TEXT_OVERLAY":
            results.append(CheckResult(code, BY_CONSTRUCTION, {"note": "합성이 글자를 그리지 않는다(사진을 들여다본 검사가 아니다)"}))
        elif code == "POLICY_FLAGS":
            results.append(CheckResult(code, NOT_CHECKED, {"note": "쿠팡 정책 적합 검사는 아직 없다(I0 미확인)"}))
        else:
            results.append(CheckResult(code, NOT_CHECKED, {"note": "알 수 없는 검사 항목"}))
    return results


def usable(results: list[CheckResult]) -> bool:
    """FAIL이 하나도 없어야 쓸 수 있다(WARN·NOT_CHECKED·BY_CONSTRUCTION은 막지 않지만 화면이 그대로 알린다)."""
    return not any(r.status == FAIL for r in results)


__all__ = ["CheckResult", "inspect", "usable", "product_preservation", "contrast_between", "PASS", "FAIL", "WARN", "BY_CONSTRUCTION", "NOT_CHECKED", "SKIPPED",
           "PRESERVATION_MIN", "CONTRAST_MIN", "MAIN_MIN_SIDE", "WHITE_MIN_CHANNEL"]
