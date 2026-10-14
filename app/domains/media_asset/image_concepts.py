"""
=========================================================
Homez OS

File : app/domains/media_asset/image_concepts.py

이미지 "컨셉" 정의(이미지 형식 학습·응용 AI 설계 P3, 2026-10-14 사용자 요청 — "여러 가지 컨셉으로 만들 수
있게 하고, 컨셉을 선택할 수 있게 섹션을 구분한다").

컨셉 = 배경 + 글자 색 + 포인트 색의 묶음. 같은 실제 제품 사진(누끼)에 컨셉만 바꿔 여러 분위기의 대표·추가·상세
이미지를 만든다. 제품 자체를 AI로 다시 그리지 않는다(2026-08-20 CTO 지시) — 이 모듈은 색·배경 값만 정의하고
기존 `composition.py`·`detail_page_generator.py`가 그 값을 쓴다. 순수 데이터·계산, 외부 호출 없음.

쓰임새(`usable_for`):
- MAIN       : 쿠팡 대표 이미지 — 흰 배경을 권장하므로 흰 배경 컨셉만 허용한다(분석 결과 공급처 대표 이미지는
               흰 배경이 0/10이었다 — docs/HOMEZ_V7_IMAGE_FORMAT_LEARNING_AI_DESIGN_20261014.md 6절).
- ADDITIONAL : 추가 이미지(분위기 컷) — 모든 컨셉 허용.
- DETAIL     : 상세 페이지 섹션 — 모든 컨셉 허용, 섹션마다 다른 컨셉을 고를 수 있다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import BadRequestException
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec

PURPOSE_MAIN = "MAIN"
PURPOSE_ADDITIONAL = "ADDITIONAL"
PURPOSE_DETAIL = "DETAIL"
PURPOSES = (PURPOSE_MAIN, PURPOSE_ADDITIONAL, PURPOSE_DETAIL)

DEFAULT_CONCEPT_CODE = "CLEAN_WHITE"

Rgb = tuple[int, int, int]


def _rgb(hex_color: str) -> Rgb:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@dataclass(frozen=True)
class ConceptTheme:
    """상세 페이지 섹션이 쓰는 색(배경·본문·보조 글자·줄무늬)과 포인트 색."""

    bg: Rgb
    text: Rgb
    muted: Rgb
    shade: Rgb
    accent: Rgb


@dataclass(frozen=True)
class ImageConcept:
    code: str
    label_ko: str
    description: str
    usable_for: tuple[str, ...]
    background_kind: str
    color_hex: str | None
    color_hex_end: str | None
    theme: ConceptTheme

    def background_spec(self) -> BackgroundSpec:
        return BackgroundSpec(kind=self.background_kind, color_hex=self.color_hex, color_hex_end=self.color_hex_end)

    def to_summary(self) -> dict:
        """화면에서 컨셉 목록을 보여 줄 때 쓰는 값(색 미리보기 포함)."""
        return {
            "code": self.code, "label_ko": self.label_ko, "description": self.description,
            "usable_for": list(self.usable_for),
            "preview": {
                "background": [self.color_hex, self.color_hex_end] if self.color_hex_end else [self.color_hex or "#FFFFFF"],
                "text": _hex(self.theme.text), "accent": _hex(self.theme.accent),
            },
        }


def _hex(rgb: Rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _concept(
    code: str, label: str, description: str, usable: tuple[str, ...], kind: str,
    start: str | None, end: str | None, *, bg: str, text: str, muted: str, shade: str, accent: str,
) -> ImageConcept:
    return ImageConcept(
        code=code, label_ko=label, description=description, usable_for=usable,
        background_kind=kind, color_hex=start, color_hex_end=end,
        theme=ConceptTheme(bg=_rgb(bg), text=_rgb(text), muted=_rgb(muted), shade=_rgb(shade), accent=_rgb(accent)),
    )


_ALL = (PURPOSE_MAIN, PURPOSE_ADDITIONAL, PURPOSE_DETAIL)
_NO_MAIN = (PURPOSE_ADDITIONAL, PURPOSE_DETAIL)

CONCEPTS: dict[str, ImageConcept] = {c.code: c for c in (
    _concept(
        "CLEAN_WHITE", "깔끔한 흰 배경", "쿠팡 대표 이미지에 가장 안전한 흰 배경. 군더더기 없는 기본형.", _ALL,
        BackgroundKind.WHITE, None, None,
        bg="#FFFFFF", text="#222222", muted="#6E6E6E", shade="#F5F6F8", accent="#2D6CDF",
    ),
    _concept(
        "SOFT_BEIGE", "소프트 베이지", "따뜻하고 차분한 베이지 그라데이션. 내추럴·데일리 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.GRADIENT, "#FBF7EF", "#EADFCB",
        bg="#F7F1E6", text="#2B2418", muted="#665A45", shade="#EFE6D6", accent="#8A6D4B",
    ),
    _concept(
        "FRESH_MINT", "청량한 민트", "맑고 시원한 민트 톤. 위생·여름·스포츠 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.GRADIENT, "#F2FBF8", "#D8F1EA",
        bg="#EEF9F5", text="#12302A", muted="#3C675C", shade="#DFF1EA", accent="#1F9D82",
    ),
    _concept(
        "BLUSH_PINK", "은은한 핑크", "부드러운 핑크 톤. 여성·아동 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.GRADIENT, "#FFF6F8", "#FBE3E9",
        bg="#FFF3F6", text="#3A1C26", muted="#774857", shade="#FBE3EA", accent="#D9567A",
    ),
    _concept(
        "CALM_GRAY", "차분한 그레이", "단정한 회색 단색. 기능성·남성 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.SOLID_COLOR, "#F1F3F5", None,
        bg="#F1F3F5", text="#1F2933", muted="#4E5B67", shade="#E4E7EB", accent="#374151",
    ),
    _concept(
        "MIDNIGHT_PREMIUM", "미드나이트 프리미엄", "어두운 배경에 금빛 포인트. 고급스러운 분위기(대표 이미지에는 쓰지 않음).", _NO_MAIN,
        BackgroundKind.SOLID_COLOR, "#14171C", None,
        bg="#14171C", text="#F3F4F6", muted="#B8BEC8", shade="#1E232B", accent="#D4B26A",
    ),
)}


def list_concepts(purpose: str | None = None) -> list[ImageConcept]:
    """코드 순서가 아니라 정의 순서(기본형이 맨 앞). purpose가 있으면 그 용도에 쓸 수 있는 컨셉만."""

    if purpose is not None and purpose not in PURPOSES:
        raise BadRequestException(f"알 수 없는 이미지 용도입니다: {purpose}")
    return [c for c in CONCEPTS.values() if purpose is None or purpose in c.usable_for]


def get_concept(code: str) -> ImageConcept:
    concept = CONCEPTS.get(code)
    if concept is None:
        raise BadRequestException(f"알 수 없는 이미지 컨셉입니다: {code}")
    return concept


def require_usable(code: str, purpose: str) -> ImageConcept:
    """컨셉이 그 용도에 허용되는지 확인한다(대표 이미지는 흰 배경 컨셉만)."""

    if purpose not in PURPOSES:
        raise BadRequestException(f"알 수 없는 이미지 용도입니다: {purpose}")
    concept = get_concept(code)
    if purpose not in concept.usable_for:
        raise BadRequestException(
            f"'{concept.label_ko}' 컨셉은 {purpose} 용도에 쓸 수 없습니다 — "
            + ("대표 이미지는 흰 배경 컨셉만 허용합니다." if purpose == PURPOSE_MAIN else "허용되지 않는 조합입니다."),
        )
    return concept


def contrast_ratio(foreground: Rgb, background: Rgb) -> float:
    """WCAG 대비(글자가 읽히는지 판정하는 표준 수식)."""

    def luminance(rgb: Rgb) -> float:
        channels = []
        for value in rgb:
            c = value / 255
            channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


__all__ = [
    "CONCEPTS", "ConceptTheme", "DEFAULT_CONCEPT_CODE", "ImageConcept", "PURPOSES", "PURPOSE_ADDITIONAL",
    "PURPOSE_DETAIL", "PURPOSE_MAIN", "contrast_ratio", "get_concept", "list_concepts", "require_usable",
]
