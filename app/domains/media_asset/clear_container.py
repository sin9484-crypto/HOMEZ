"""
=========================================================
Homez OS

File : app/domains/media_asset/clear_container.py

2026-11-26 — **투명 용기의 몸통이 뒤 장면을 비추게 하기**(사용자 "부 자연스럽다" — 상황 장면 #175~#179). 흰 배경에서 찍은 투명 병은
몸통 안에 그 흰 배경이 들어 있어, 장면 위에 붙이면 우유처럼 하얀 병이 된다(진짜 투명 병이면 뒤의 탁자·침대가 비쳐야 한다).

방법(Pillow만, 바깥 호출 없음, AI가 다시 그리지 않음 — 원본 픽셀에서 계산만):
  - 사람이 고른 **높이 범위**(상품 높이 대비 0~1, 예: 흰 뚜껑 아래 몸통)만 바꾼다. 뚜껑·분사 머리처럼 불투명한 흰 부분은 범위 밖에 둔다.
  - 범위 안 픽셀마다 진하기 d = 255 − (가장 어두운 채널). 투명한 몸통(옅음)은 불투명도를 낮추고(GLASS_ALPHA_MIN까지),
    글씨·물방울 무늬·윤곽처럼 진한 곳(d ≥ PRINT_DARKNESS)은 그대로 불투명하게 둔다.
  - 색은 '흰 배경 위에서 찍힌 반투명 물체'로 되돌린다: 원본 P = a·C + (1−a)·255 → C = (P − (1−a)·255) / a.
    그래서 흰 배경 위에 다시 놓으면 원본과 똑같고, 장면 위에 놓으면 P − (1−a)·(255 − 장면) — 장면이 비친다.
  - 원래 알파보다 높이지 않는다(가장자리 부드러움 유지).
=========================================================
"""

from __future__ import annotations

import io

from PIL import Image
from PIL import ImageChops
from PIL import ImageFilter

MODEL_VERSION = "see-through-v2"
GLASS_ALPHA_MIN = 0.52      # 가장 투명한 곳도 이만큼은 보인다(투명 플라스틱·액체의 옅은 막). 장면 합성이 지우는 옅은 안개(알파 64 미만)보다 짙어야 한다.
                            # 2026-11-26 0.34→0.52: 사용자 "제품이 부각이 안된다" — 너무 비쳐 밝은 장면에 묻혔다
RIM_ALPHA_MIN = 0.88        # 용기 가장자리 띠(RIM_SHARE)는 거의 불투명하게 — 윤곽이 살아야 상품이 도드라진다
RIM_SHARE = 0.012           # 가장자리 띠 두께(누끼 짧은 변 대비, 최소 2픽셀)
PRINT_DARKNESS = 70         # 이 진하기 이상(글씨·무늬·윤곽)은 그대로 불투명(110은 작은 글씨가 흐려졌다)
MIN_BAND_SHARE = 0.05


class SeeThroughError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 실패 사유."""


def see_through(cutout_png: bytes, y_from: float, y_to: float) -> bytes:
    if not (isinstance(y_from, (int, float)) and isinstance(y_to, (int, float))):
        raise SeeThroughError("높이 범위가 올바르지 않습니다.")
    if not (0.0 <= y_from < y_to <= 1.0) or (y_to - y_from) < MIN_BAND_SHARE:
        raise SeeThroughError(f"높이 범위는 0~1 사이, {int(MIN_BAND_SHARE * 100)}% 이상이어야 합니다.")
    image = Image.open(io.BytesIO(cutout_png))
    if image.mode not in ("RGBA", "LA") and not (image.mode == "P" and "transparency" in image.info):
        raise SeeThroughError("배경을 지운 사진(누끼)에서만 쓸 수 있습니다.")
    rgba = image.convert("RGBA")
    box = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox()
    if box is None:
        raise SeeThroughError("사진에서 상품을 찾지 못했습니다.")
    height = box[3] - box[1]
    top = box[1] + int(round(y_from * height))
    bottom = box[1] + int(round(y_to * height))

    solid = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    rim_px = max(2, int(min(rgba.size) * RIM_SHARE))
    inner = solid.filter(ImageFilter.MinFilter(2 * rim_px + 1))
    rim = ImageChops.subtract(solid, inner).load()                    # 255 = 가장자리 띠
    pixels = rgba.load()
    changed = 0
    for y in range(top, bottom):
        for x in range(box[0], box[2]):
            r, g, b, a0 = pixels[x, y]
            if a0 == 0:
                continue
            darkness = 255 - min(r, g, b)
            floor = RIM_ALPHA_MIN if rim[x, y] else GLASS_ALPHA_MIN
            glass = floor + (1.0 - floor) * min(1.0, darkness / PRINT_DARKNESS)
            a = min(a0 / 255.0, glass)
            if a >= a0 / 255.0:
                continue
            colour = tuple(max(0, min(255, int(round((v - (1.0 - a) * 255.0) / a)))) for v in (r, g, b))
            pixels[x, y] = colour + (max(1, int(round(a * 255))),)
            changed += 1
    if changed == 0:
        raise SeeThroughError("고른 범위에 비치게 할 투명한 부분이 없습니다.")
    out = io.BytesIO()
    rgba.save(out, format="PNG")
    return out.getvalue()


__all__ = ["see_through", "SeeThroughError", "MODEL_VERSION", "GLASS_ALPHA_MIN", "PRINT_DARKNESS", "RIM_ALPHA_MIN", "RIM_SHARE"]
