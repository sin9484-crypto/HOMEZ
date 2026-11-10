"""
=========================================================
Homez OS

File : app/domains/media_asset/ai_background_concepts.py

2026-11-10 — **AI 배경(L2 생성 배경) 컨셉 6종**. 사용자 결정 "cloudflare로 진행해라"(같은 날 제안: AI는 상품 없는 배경만 그리고, 상품은 HOMEZ가
실제 누끼로 PC 안에서 붙인다). 이 결정이 명세의 `l2_approved`다. 정책 조사(I0, docs/research/20261019_I0_IMAGE_POLICY_RESEARCH.md)는 쿠팡의
AI 이미지 공식 규정을 찾지 못했다 — 그래서 대표(MAIN)에는 쓰지 않고, "생성 배경" 표기는 이미지 밖(화면·기록)에 두며, 정책 적합은 NOT_CHECKED로 알린다.

  - 기존 컨셉 레지스트리(`image_concepts.CONCEPTS`)와 **따로** 둔다 — 로컬 합성 경로(컨셉으로 만들기·배경 합성·상세 페이지)는 이 컨셉을 모른다(알 수 없는 컨셉으로 거절).
  - 장면 글(prompt)은 코드가 정한다. 사람이 쓴 글·상품명·회사 정보는 넣지 않는다. 모든 글은 "상품·글자·로고·사람 없이, 가운데 빈 자리"를 요구한다.
  - 명세는 `image_concepts.validate_spec`을 그대로 통과해야 한다(불러올 때 확인 — 틀리면 쓰지 못한다).
=========================================================
"""

from __future__ import annotations

from app.core.exceptions import BadRequestException
from app.domains.media_asset import image_concepts as ic

MAX_PICK = 3
PURPOSES = (ic.PURPOSE_ADDITIONAL, ic.PURPOSE_DETAIL)

# 모든 장면 글 끝에 붙는 공통 규칙(배경만, 가운데 빈 자리, 글자·사람·상품 없음).
PROMPT_RULES = (
    " Empty background scene only for an e-commerce product photo: leave a clear, empty surface in the centre foreground where a product will be placed later."
    " No products, no bottles, no packages, no people, no hands, no animals."
    " No text, no letters, no numbers, no logos, no labels, no signs, no watermarks."
    " Photorealistic, soft natural light, gentle shallow depth of field, square composition, eye-level camera."
)


def _ai_concept(code: str, label: str, description: str, scene: str, scene_kind: str, *, mood: str, good_for: tuple[str, ...], avoid_for: tuple[str, ...] = ()) -> ic.ImageConcept:
    # 상세 페이지 섹션 색은 밝은 중립색(배경 사진의 평균색을 미리 알 수 없어 글자 대비가 확실한 값을 쓴다).
    return ic.ImageConcept(
        code=code, label_ko=label, description=description, usable_for=PURPOSES, background_kind=ic.BACKGROUND_GENERATED, color_hex=None, color_hex_end=None,
        theme=ic.ConceptTheme(bg=(248, 248, 246), text=(34, 34, 34), muted=(96, 96, 96), shade=(236, 236, 232), accent=(45, 108, 223)),
        processing_level=ic.LEVEL_L2, scene_kind=scene_kind, mood=mood, props_allowed=True, good_for=good_for, avoid_for=avoid_for,
        checks=(ic.CHECK_PRODUCT_PRESERVATION, ic.CHECK_BACKGROUND_CONTRAST, ic.CHECK_NO_TEXT_OVERLAY, ic.CHECK_POLICY_FLAGS),
        needs_generated_label=True, l2_approved=True, generation_prompt=scene + PROMPT_RULES,
    )


AI_CONCEPTS: dict[str, ic.ImageConcept] = {c.code: c for c in (
    _ai_concept("AI_BATHROOM_SHELF", "욕실 선반", "밝은 타일 욕실의 선반 위. 바디워시·위생용품·욕실용품에 어울립니다.",
                "A clean bright bathroom: a light stone shelf in front of white tiles, a folded white towel and a small green plant softly blurred at the sides.",
                ic.SCENE_LIFESTYLE, mood="청결·산뜻", good_for=("욕실", "위생용품", "뷰티", "생활용품")),
    _ai_concept("AI_KITCHEN_COUNTER", "주방 조리대", "햇빛이 드는 주방 조리대. 생수·식품·주방용품에 어울립니다.",
                "A sunny modern kitchen: a light wooden countertop, a blurred window with morning light and a few fresh green leaves at the far edge.",
                ic.SCENE_LIFESTYLE, mood="상쾌·일상", good_for=("식품", "주방", "생수", "음료")),
    _ai_concept("AI_LIVING_TABLE", "거실 원목 테이블", "아늑한 거실의 원목 테이블. 생활용품·인테리어 소품에 어울립니다.",
                "A cosy living room: a natural oak coffee table surface, a soft beige sofa and a linen curtain blurred in the background.",
                ic.SCENE_LIFESTYLE, mood="아늑·따뜻", good_for=("생활용품", "인테리어", "문구", "선물")),
    _ai_concept("AI_OUTDOOR_TRACK", "야외 산책로", "아침 햇살의 공원 산책로. 운동·레저·보호대 상품에 어울립니다.",
                "An outdoor park running path in early morning sunlight: a smooth light concrete ledge in the foreground, green trees and grass blurred behind.",
                ic.SCENE_LIFESTYLE, mood="활동·건강", good_for=("스포츠", "레저", "보호대", "기능성")),
    _ai_concept("AI_BEDROOM_SOFT", "포근한 침실", "부드러운 이불이 있는 밝은 침실. 여성용품·의류·아동 상품에 어울립니다.",
                "A soft bright bedroom: a smooth white bedside table top, light grey bedding and a warm window glow blurred in the background.",
                ic.SCENE_LIFESTYLE, mood="포근·편안", good_for=("여성용품", "패션", "아동", "침구")),
    _ai_concept("AI_STUDIO_MARBLE", "대리석 스튜디오", "흰 대리석 받침과 은은한 조명. 뷰티·선물·소형 가전에 어울립니다.",
                "A minimal photo studio: a white marble plinth top in the foreground, a smooth warm grey backdrop and a soft spotlight from above.",
                ic.SCENE_STUDIO_LIGHT, mood="고급·정돈", good_for=("뷰티", "선물", "전자기기", "주얼리")),
)}


def _assert_valid() -> None:
    problems = {code: ic.validate_spec(c) for code, c in AI_CONCEPTS.items()}
    problems = {code: items for code, items in problems.items() if items}
    clash = set(AI_CONCEPTS) & set(ic.CONCEPTS)
    if clash:
        problems["*"] = [f"기존 컨셉과 코드가 겹친다: {sorted(clash)}"]
    if problems:
        raise ic.ConceptSpecError("AI 배경 컨셉 명세가 규칙에 맞지 않아 쓸 수 없습니다: " + "; ".join(f"{k}: {' / '.join(v)}" for k, v in problems.items()))


def list_ai_concepts() -> list[ic.ImageConcept]:
    return list(AI_CONCEPTS.values())


def get_ai_concept(code: str) -> ic.ImageConcept:
    concept = AI_CONCEPTS.get(code)
    if concept is None:
        raise BadRequestException(f"알 수 없는 AI 배경 컨셉입니다: {code}")
    return concept


def to_summary(concept: ic.ImageConcept) -> dict:
    """화면용 요약 — 장면 글은 보여 준다(무엇을 바깥에 보내는지 사용자가 볼 수 있게)."""
    return {
        "code": concept.code, "label_ko": concept.label_ko, "description": concept.description, "usable_for": list(concept.usable_for),
        "processing_level": concept.processing_level, "scene_kind": concept.scene_kind, "mood": concept.mood, "good_for": list(concept.good_for),
        "needs_generated_label": concept.needs_generated_label, "prompt": concept.generation_prompt,
    }


_assert_valid()

__all__ = ["AI_CONCEPTS", "MAX_PICK", "PURPOSES", "PROMPT_RULES", "list_ai_concepts", "get_ai_concept", "to_summary"]
