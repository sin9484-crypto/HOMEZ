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

# 모든 장면 글 끝에 붙는 공통 규칙(배경만, 앞쪽 아래 1/3을 채우는 빈 바닥, 글자·사람·상품 없음).
# 2026-11-15: 합성이 제품 밑면을 화면 높이 86% 바닥선에 세운다(composition.Placement.GROUNDED) — 그 자리가 늘 빈 평면이 되도록 구도를 정해 준다.
# 2026-11-25: 사용자 "새로 생성한 이미지랑 알맞게 배경 사진도 수정해라" — 11-25 제미나이 시험 사진(밝은 창가 아침 햇빛·원목 결·베이지/크림 톤·
#   아늑한 생활 사진)과 같은 느낌으로 여섯 장면의 빛·색을 맞춘다(LOOK). 장면 글 한도(1,000자) 안에 들도록 구도 규칙 문장을 줄였다(뜻은 같다).
LOOK = (
    " Look: bright, airy, cozy home lifestyle photo; warm morning sunlight from the left; soft shadows;"
    " warm beige, cream and light oak palette; warm white balance."
)
PROMPT_RULES = (
    " Empty background scene only for an e-commerce product photo."
    " Composition: a wide, empty, flat horizontal surface fills the entire bottom third of the image from edge to edge, seen from slightly above at table height;"
    " its back edge is at about two thirds of the image height;"
    " the middle of this empty surface is completely clear so a product can be placed there later; everything behind it is softly out of focus."
    + LOOK +
    " No products, no bottles, no packages, no people, no hands, no animals."
    " No text, no letters, no numbers, no logos, no labels, no signs, no watermarks."
    " Photorealistic, shallow depth of field, square composition."
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
                "A clean bright bathroom in warm tones: a light beige stone vanity countertop in the foreground, cream tiles behind, a folded white towel and a small green plant softly blurred far in the back.",
                ic.SCENE_LIFESTYLE, mood="청결·산뜻", good_for=("욕실", "위생용품", "뷰티", "생활용품")),
    _ai_concept("AI_KITCHEN_COUNTER", "주방 조리대", "햇빛이 드는 주방 조리대. 생수·식품·주방용품에 어울립니다.",
                "A sunny cozy kitchen: a light oak countertop with visible wood grain in the foreground, a window with a sheer white curtain, a ceramic mug and a few green leaves softly blurred far in the back.",
                ic.SCENE_LIFESTYLE, mood="상쾌·일상", good_for=("식품", "주방", "생수", "음료")),
    _ai_concept("AI_LIVING_TABLE", "거실 원목 테이블", "아늑한 거실의 원목 테이블. 생활용품·인테리어 소품에 어울립니다.",
                "A cosy bright living room: a light oak table top with visible wood grain in the foreground, a sheer white curtain, a beige sofa and a small plant blurred in the background.",
                ic.SCENE_LIFESTYLE, mood="아늑·따뜻", good_for=("생활용품", "인테리어", "문구", "선물")),
    _ai_concept("AI_OUTDOOR_TRACK", "야외 산책로", "아침 햇살의 공원 산책로 옆 벤치 위. 운동·레저·보호대 상품에 어울립니다.",
                "An outdoor park in warm early morning sunlight: the flat top of a light wooden bench seat beside a running path in the foreground, green trees, grass and the path blurred behind.",
                ic.SCENE_LIFESTYLE, mood="활동·건강", good_for=("스포츠", "레저", "보호대", "기능성")),
    _ai_concept("AI_BEDROOM_SOFT", "포근한 침실", "부드러운 이불이 있는 밝은 침실. 여성용품·의류·아동 상품에 어울립니다.",
                "A soft bright bedroom: a smooth light oak bedside table top in the foreground, cream bedding and a sheer curtain glowing with morning light blurred in the background.",
                ic.SCENE_LIFESTYLE, mood="포근·편안", good_for=("여성용품", "패션", "아동", "침구")),
    _ai_concept("AI_STUDIO_MARBLE", "대리석 스튜디오", "크림색 대리석 테이블과 은은한 햇빛. 뷰티·선물·소형 가전에 어울립니다.",
                "A minimal warm photo studio: a wide cream marble table top in the foreground, a smooth warm beige backdrop and soft sunlight from the left.",
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

__all__ = ["AI_CONCEPTS", "MAX_PICK", "PURPOSES", "LOOK", "PROMPT_RULES", "list_ai_concepts", "get_ai_concept", "to_summary"]
