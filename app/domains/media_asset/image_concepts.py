"""
=========================================================
Homez OS

File : app/domains/media_asset/image_concepts.py

이미지 "컨셉" 정의(이미지 형식 학습·응용 AI 설계 P3, 2026-10-14 사용자 요청 — "여러 가지 컨셉으로 만들 수
있게 하고, 컨셉을 선택할 수 있게 섹션을 구분한다").

컨셉 = 배경 + 글자 색 + 포인트 색의 묶음. 같은 실제 제품 사진(누끼)에 컨셉만 바꿔 여러 분위기의 대표·추가·상세
이미지를 만든다. 제품 자체를 AI로 다시 그리지 않는다(2026-08-20 CTO 지시) — 이 모듈은 색·배경 값만 정의하고
기존 `composition.py`·`detail_page_generator.py`가 그 값을 쓴다. 순수 데이터·계산, 외부 호출 없음.

2026-10-19 이미지 AI I1(컨셉 명세 체계, 계획서 §1): 컨셉을 "색 묶음"에서 **명세(Spec)** 로 넓혔다. 기존 6종은 같은 동작·같은 API 모양으로 옮겼고(기존 필드·
함수 그대로, 새 필드는 전부 기본값이 있다), 새로 생긴 것은 아래와 같다 — 장면 종류(`scene_kind`)·분위기, **처리 수준**(`processing_level`), 어울리는/피해야 할 상품(`good_for`/
`avoid_for`), 컨셉이 요구하는 **검사 항목**(`checks`), "생성 배경" 표기 필요 여부, 명세 버전. 명세는 `validate_spec`이 규칙으로 검사하고, 레지스트리는 불러올 때
`validate_registry`를 통과해야만 쓸 수 있다(틀린 명세가 조용히 쓰이지 않게 fail-closed).

처리 수준:
  - L0 로컬 합성(흰 배경·단색·그라데이션) — 지금 6종 전부. 외부 접촉·비용 없음.
  - L1 로컬 보정(그림자·반사·질감) — 생성이 아니다. I3에서 컨셉을 더한다(아직 없음).
  - L2 생성 배경 — 정책·권리 조사(I0)와 별도 승인 뒤에만. 명세가 `l2_approved`와 "생성 배경" 표기를 갖추지 않으면 검증이 거절하고, 대표(MAIN)에는 쓸 수 없다. 지금 L2 컨셉은 없다.
`checks`는 이 컨셉을 쓸 때 **요구되는** 검사 항목의 선언이다(자동 실행·컨셉별 결과 관리는 I2에서 붙인다). `good_for`/`avoid_for`는 카테고리 추천 규칙(I3)이 읽을 기본값이다.

쓰임새(`usable_for`):
- MAIN       : 쿠팡 대표 이미지 — 흰 배경을 권장하므로 흰 배경 컨셉만 허용한다(분석 결과 공급처 대표 이미지는
               흰 배경이 0/10이었다 — docs/HOMEZ_V7_IMAGE_FORMAT_LEARNING_AI_DESIGN_20261014.md 6절).
- ADDITIONAL : 추가 이미지(분위기 컷) — 모든 컨셉 허용.
- DETAIL     : 상세 페이지 섹션 — 모든 컨셉 허용, 섹션마다 다른 컨셉을 고를 수 있다.
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.exceptions import BadRequestException
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec

PURPOSE_MAIN = "MAIN"
PURPOSE_ADDITIONAL = "ADDITIONAL"
PURPOSE_DETAIL = "DETAIL"
PURPOSES = (PURPOSE_MAIN, PURPOSE_ADDITIONAL, PURPOSE_DETAIL)

DEFAULT_CONCEPT_CODE = "CLEAN_WHITE"

LEVEL_L0, LEVEL_L1, LEVEL_L2 = "L0", "L1", "L2"
PROCESSING_LEVELS = (LEVEL_L0, LEVEL_L1, LEVEL_L2)

SCENE_WHITE, SCENE_SOLID, SCENE_GRADIENT = "WHITE", "SOLID", "GRADIENT"
SCENE_MATERIAL_TEXTURE, SCENE_LIFESTYLE, SCENE_STUDIO_LIGHT, SCENE_SEASONAL = "MATERIAL_TEXTURE", "LIFESTYLE_SCENE", "STUDIO_LIGHT", "SEASONAL"
SCENE_KINDS = (SCENE_WHITE, SCENE_SOLID, SCENE_GRADIENT, SCENE_MATERIAL_TEXTURE, SCENE_LIFESTYLE, SCENE_STUDIO_LIGHT, SCENE_SEASONAL)
# L0(로컬 합성)이 만들 수 있는 장면은 이 셋뿐이다. 나머지는 L1(보정) 이상이 있어야 한다.
L0_SCENES = (SCENE_WHITE, SCENE_SOLID, SCENE_GRADIENT)
SCENE_BY_BACKGROUND = {BackgroundKind.WHITE: SCENE_WHITE, BackgroundKind.SOLID_COLOR: SCENE_SOLID, BackgroundKind.GRADIENT: SCENE_GRADIENT}

CHECK_PRODUCT_PRESERVATION, CHECK_BACKGROUND_CONTRAST, CHECK_MAIN_IMAGE_SPEC = "PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST", "MAIN_IMAGE_SPEC"
CHECK_NO_TEXT_OVERLAY, CHECK_POLICY_FLAGS = "NO_TEXT_OVERLAY", "POLICY_FLAGS"
CHECK_CODES = (CHECK_PRODUCT_PRESERVATION, CHECK_BACKGROUND_CONTRAST, CHECK_MAIN_IMAGE_SPEC, CHECK_NO_TEXT_OVERLAY, CHECK_POLICY_FLAGS)

MAX_SUITABILITY_ITEMS = 8
MAX_SUITABILITY_CHARS = 20
MIN_TEXT_CONTRAST, MIN_MUTED_CONTRAST, MIN_ACCENT_CONTRAST = 7.0, 4.5, 3.0
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

Rgb = tuple[int, int, int]


class ConceptSpecError(Exception):
    """컨셉 명세가 규칙에 맞지 않을 때(레지스트리를 불러오는 시점에 막힌다)."""


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
    # --- I1 명세 필드(전부 기본값이 있어 기존 호출은 그대로 동작한다) ---
    version: int = 1
    processing_level: str = LEVEL_L0
    scene_kind: str = ""
    mood: str = ""
    props_allowed: bool = False
    good_for: tuple[str, ...] = ()
    avoid_for: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    needs_generated_label: bool = False
    l2_approved: bool = False

    def background_spec(self) -> BackgroundSpec:
        return BackgroundSpec(kind=self.background_kind, color_hex=self.color_hex, color_hex_end=self.color_hex_end)

    def to_summary(self) -> dict:
        """화면에서 컨셉 목록을 보여 줄 때 쓰는 값(색 미리보기 포함)."""
        return {
            "code": self.code, "label_ko": self.label_ko, "description": self.description,
            "usable_for": list(self.usable_for),
            # I1 추가 필드(기존 필드는 그대로, 새 필드만 더한다)
            "version": self.version, "processing_level": self.processing_level, "scene_kind": self.scene_kind, "mood": self.mood,
            "good_for": list(self.good_for), "avoid_for": list(self.avoid_for), "needs_generated_label": self.needs_generated_label,
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
    mood: str = "", good_for: tuple[str, ...] = (), avoid_for: tuple[str, ...] = (),
) -> ImageConcept:
    # 로컬 합성(L0) 컨셉의 기본 검사: 제품 보존·배경 대비, 대표 이미지에 쓸 수 있으면 규격도.
    checks = (CHECK_PRODUCT_PRESERVATION, CHECK_BACKGROUND_CONTRAST) + ((CHECK_MAIN_IMAGE_SPEC,) if PURPOSE_MAIN in usable else ())
    return ImageConcept(
        code=code, label_ko=label, description=description, usable_for=usable,
        background_kind=kind, color_hex=start, color_hex_end=end,
        theme=ConceptTheme(bg=_rgb(bg), text=_rgb(text), muted=_rgb(muted), shade=_rgb(shade), accent=_rgb(accent)),
        processing_level=LEVEL_L0, scene_kind=SCENE_BY_BACKGROUND.get(kind, ""), mood=mood, good_for=good_for, avoid_for=avoid_for, checks=checks,
    )


_ALL = (PURPOSE_MAIN, PURPOSE_ADDITIONAL, PURPOSE_DETAIL)
_NO_MAIN = (PURPOSE_ADDITIONAL, PURPOSE_DETAIL)

def _build_registry(items) -> dict[str, ImageConcept]:
    registry: dict[str, ImageConcept] = {}
    for item in items:
        if item.code in registry:
            raise ConceptSpecError(f"컨셉 코드가 겹칩니다: {item.code}")   # 딕셔너리 내포는 겹친 코드를 조용히 덮어쓴다
        registry[item.code] = item
    return registry


CONCEPTS: dict[str, ImageConcept] = _build_registry((
    _concept(
        "CLEAN_WHITE", "깔끔한 흰 배경", "쿠팡 대표 이미지에 가장 안전한 흰 배경. 군더더기 없는 기본형.", _ALL,
        BackgroundKind.WHITE, None, None,
        bg="#FFFFFF", text="#222222", muted="#6E6E6E", shade="#F5F6F8", accent="#2D6CDF",
        mood="깔끔·신뢰",
    ),
    _concept(
        "SOFT_BEIGE", "소프트 베이지", "따뜻하고 차분한 베이지 그라데이션. 내추럴·데일리 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.GRADIENT, "#FBF7EF", "#EADFCB",
        bg="#F7F1E6", text="#2B2418", muted="#665A45", shade="#EFE6D6", accent="#8A6D4B",
        mood="따뜻·차분", good_for=("생활용품", "주방", "인테리어", "식품"),
    ),
    _concept(
        "FRESH_MINT", "청량한 민트", "맑고 시원한 민트 톤. 위생·여름·스포츠 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.GRADIENT, "#F2FBF8", "#D8F1EA",
        bg="#EEF9F5", text="#12302A", muted="#3C675C", shade="#DFF1EA", accent="#1F9D82",
        mood="청량·위생", good_for=("욕실", "위생용품", "스포츠", "청소"),
    ),
    _concept(
        "BLUSH_PINK", "은은한 핑크", "부드러운 핑크 톤. 여성·아동 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.GRADIENT, "#FFF6F8", "#FBE3E9",
        bg="#FFF3F6", text="#3A1C26", muted="#774857", shade="#FBE3EA", accent="#D9567A",
        mood="부드러움", good_for=("뷰티", "여성용품", "아동", "문구"), avoid_for=("공구",),
    ),
    _concept(
        "CALM_GRAY", "차분한 그레이", "단정한 회색 단색. 기능성·남성 상품에 어울립니다.", _NO_MAIN,
        BackgroundKind.SOLID_COLOR, "#F1F3F5", None,
        bg="#F1F3F5", text="#1F2933", muted="#4E5B67", shade="#E4E7EB", accent="#374151",
        mood="단정·기능", good_for=("기능성", "남성용품", "전자기기", "공구"),
    ),
    _concept(
        "MIDNIGHT_PREMIUM", "미드나이트 프리미엄", "어두운 배경에 금빛 포인트. 고급스러운 분위기(대표 이미지에는 쓰지 않음).", _NO_MAIN,
        BackgroundKind.SOLID_COLOR, "#14171C", None,
        bg="#14171C", text="#F3F4F6", muted="#B8BEC8", shade="#1E232B", accent="#D4B26A",
        mood="고급·무게감", good_for=("선물", "전자기기", "시계"), avoid_for=("식품", "위생용품", "아동"),
    ),
))


def validate_spec(concept: ImageConcept) -> list[str]:
    """명세 한 개를 규칙으로 검사한다. 문제 목록(비어 있으면 통과). 고쳐 쓰지 않는다."""

    problems: list[str] = []
    c = concept
    if not _CODE.match(c.code or ""):
        problems.append("코드는 영문 대문자·숫자·밑줄 3~40자여야 한다")
    if not (c.label_ko or "").strip() or not (c.description or "").strip():
        problems.append("이름과 설명이 있어야 한다")
    if not isinstance(c.version, int) or isinstance(c.version, bool) or c.version < 1:
        problems.append("버전은 1 이상의 정수여야 한다")

    # 용도
    if not c.usable_for or len(set(c.usable_for)) != len(c.usable_for) or any(p not in PURPOSES for p in c.usable_for):
        problems.append("용도(usable_for)는 비어 있지 않고 중복 없이 MAIN·ADDITIONAL·DETAIL 중에서만 골라야 한다")

    # 처리 수준·장면
    if c.processing_level not in PROCESSING_LEVELS:
        problems.append("처리 수준은 L0·L1·L2 중 하나여야 한다")
    if c.scene_kind not in SCENE_KINDS:
        problems.append("장면 종류가 올바르지 않다")
    if c.processing_level == LEVEL_L0 and c.scene_kind not in L0_SCENES:
        problems.append("L0(로컬 합성)은 흰 배경·단색·그라데이션 장면만 만들 수 있다 — 소재·생활 장면·조명·계절은 L1 이상이 필요하다")
    if c.background_kind not in (BackgroundKind.WHITE, BackgroundKind.SOLID_COLOR, BackgroundKind.GRADIENT):
        problems.append("컨셉의 배경 종류는 흰 배경·단색·그라데이션이어야 한다(기존 이미지 배경은 컨셉이 아니다)")
    elif c.processing_level == LEVEL_L0 and SCENE_BY_BACKGROUND.get(c.background_kind) != c.scene_kind:
        problems.append("장면 종류가 배경 종류와 맞지 않는다")

    # 색: 배경 종류별로 필요한 색만 있어야 한다
    start_ok = bool(c.color_hex) and bool(_HEX.match(c.color_hex or ""))
    end_ok = bool(c.color_hex_end) and bool(_HEX.match(c.color_hex_end or ""))
    if c.background_kind == BackgroundKind.WHITE and (c.color_hex or c.color_hex_end):
        problems.append("흰 배경에는 색을 지정하지 않는다")
    if c.background_kind == BackgroundKind.SOLID_COLOR and not (start_ok and not c.color_hex_end):
        problems.append("단색 배경은 시작 색 하나(#RRGGBB)만 필요하다")
    if c.background_kind == BackgroundKind.GRADIENT and not (start_ok and end_ok):
        problems.append("그라데이션 배경은 시작·끝 색(#RRGGBB)이 둘 다 필요하다")

    # 대표 이미지: 흰 배경만, 생성(L2) 불가
    if PURPOSE_MAIN in c.usable_for:
        if c.background_kind != BackgroundKind.WHITE or c.scene_kind != SCENE_WHITE:
            problems.append("대표 이미지(MAIN)에 쓰는 컨셉은 흰 배경이어야 한다")
        if c.processing_level == LEVEL_L2:
            problems.append("생성 배경(L2)은 대표 이미지에 쓸 수 없다")
        if CHECK_MAIN_IMAGE_SPEC not in c.checks:
            problems.append("대표 이미지에 쓰는 컨셉은 대표 이미지 규격 검사(MAIN_IMAGE_SPEC)를 요구해야 한다")

    # 생성(L2): 승인과 표기를 갖춰야 한다. 비생성은 표기·소품이 없다.
    if c.processing_level == LEVEL_L2:
        if not c.l2_approved:
            problems.append("생성 배경(L2)은 정책·권리 확인과 별도 승인(l2_approved) 뒤에만 쓸 수 있다")
        if not c.needs_generated_label:
            problems.append("생성 배경(L2)은 '생성 배경' 표기가 필요하다")
        for required in (CHECK_POLICY_FLAGS, CHECK_NO_TEXT_OVERLAY):
            if required not in c.checks:
                problems.append(f"생성 배경(L2)은 {required} 검사를 요구해야 한다")
    else:
        if c.needs_generated_label or c.l2_approved:
            problems.append("생성이 아닌 컨셉(L0·L1)은 '생성 배경' 표기나 L2 승인 표시를 가질 수 없다")
        if c.props_allowed:
            problems.append("소품은 생성(L2) 컨셉에서만 허용된다")

    # 검사 항목
    if any(check not in CHECK_CODES for check in c.checks) or len(set(c.checks)) != len(c.checks):
        problems.append("검사 항목은 알려진 코드를 중복 없이 써야 한다")
    if CHECK_PRODUCT_PRESERVATION not in c.checks:
        problems.append("모든 컨셉은 제품 보존 검사(PRODUCT_PRESERVATION)를 요구해야 한다")

    # 어울림
    for label, items in (("good_for", c.good_for), ("avoid_for", c.avoid_for)):
        if len(items) > MAX_SUITABILITY_ITEMS or any(not isinstance(x, str) or not x.strip() or len(x) > MAX_SUITABILITY_CHARS for x in items) or len(set(items)) != len(items):
            problems.append(f"{label}는 {MAX_SUITABILITY_CHARS}자 이하 글 {MAX_SUITABILITY_ITEMS}개 이하를 중복 없이 써야 한다")
    if set(c.good_for) & set(c.avoid_for):
        problems.append("어울리는 상품과 피해야 할 상품이 겹친다")

    # 상세 페이지 글 가독성
    if contrast_ratio(c.theme.text, c.theme.bg) < MIN_TEXT_CONTRAST:
        problems.append("본문 글자와 배경의 대비가 7.0보다 낮다")
    if contrast_ratio(c.theme.muted, c.theme.bg) < MIN_MUTED_CONTRAST:
        problems.append("보조 글자와 배경의 대비가 4.5보다 낮다")
    if contrast_ratio(c.theme.accent, c.theme.bg) < MIN_ACCENT_CONTRAST:
        problems.append("포인트 색과 배경의 대비가 3.0보다 낮다")
    return problems


def validate_registry(concepts: dict[str, ImageConcept] | None = None) -> dict[str, list[str]]:
    """레지스트리 전체 검사. {컨셉 코드 또는 '*': 문제 목록} — 문제가 있는 것만 담긴다(비어 있으면 통과)."""

    registry = CONCEPTS if concepts is None else concepts
    found: dict[str, list[str]] = {}
    for code, concept in registry.items():
        problems = validate_spec(concept)
        if concept.code != code:
            problems.append("레지스트리 키와 컨셉 코드가 다르다")
        if problems:
            found[code] = problems
    default = registry.get(DEFAULT_CONCEPT_CODE)
    if default is None or PURPOSE_MAIN not in default.usable_for:
        found.setdefault("*", []).append("기본 컨셉이 없거나 대표 이미지에 쓸 수 없다")
    if not any(PURPOSE_MAIN in c.usable_for for c in registry.values()):
        found.setdefault("*", []).append("대표 이미지에 쓸 수 있는 컨셉이 하나도 없다")
    return found


def _assert_registry_valid() -> None:
    problems = validate_registry()
    if problems:
        raise ConceptSpecError("이미지 컨셉 명세가 규칙에 맞지 않아 쓸 수 없습니다: " + "; ".join(f"{code}: {' / '.join(items)}" for code, items in problems.items()))


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


_assert_registry_valid()   # 틀린 명세는 불러오는 순간 막힌다(조용히 쓰이지 않게)

__all__ = [
    "CHECK_CODES", "CONCEPTS", "ConceptSpecError", "ConceptTheme", "DEFAULT_CONCEPT_CODE", "ImageConcept", "LEVEL_L0", "LEVEL_L1", "LEVEL_L2",
    "PROCESSING_LEVELS", "PURPOSES", "PURPOSE_ADDITIONAL", "PURPOSE_DETAIL", "PURPOSE_MAIN", "SCENE_KINDS", "contrast_ratio", "get_concept",
    "list_concepts", "require_usable", "validate_registry", "validate_spec",
]
