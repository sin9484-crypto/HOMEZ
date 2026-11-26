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
# 2026-11-25(이어서): 사용자 "상품에 구도나 모든것이 부적합함 다시 만들어라" — 위에서 내려다본 탁자에 정면에서 찍은 상품을 세워 시점이 어긋났고,
#   선반 앞모서리가 바닥선(86%)보다 위에 그려지면 상품이 허공에 섰으며, 소파·침대가 가까이 선명해 상품이 거대해 보였다.
#   → 탁자 높이 바로 위에서 거의 정면으로 찍은 가까운 탁자 사진, 앞모서리는 화면 밖, 뒤쪽은 멀고 아주 흐리게.
LOOK = (
    " Look: bright, airy, cozy home lifestyle photo; warm morning sunlight from the left; soft shadows;"
    " warm beige, cream and light oak palette; warm white balance."
)
PROMPT_RULES = (
    " Empty background for a product photo."
    " Composition: close-up tabletop shot, camera just above the table, looking almost straight ahead;"
    " the empty flat surface fills the whole bottom 40% of the image edge to edge and runs past the bottom edge, so its front edge is not visible;"
    " its far edge is a straight horizontal line at about 60% of the image height;"
    " the middle of this empty surface is completely clear so a product can be placed there later;"
    " everything behind the surface is far away, small and very strongly blurred."
    + LOOK +
    " No products, no people, no hands, no animals."
    " No text, no letters, no logos, no labels, no watermarks."
    " Photorealistic, square composition."
)


# 2026-11-26: 물 없이 쓰는 상품을 알아보는 낱말(상품 분류·상품명에서 찾는다). 쓰는 상황 장면은 추천하고 욕실 장면은 빼는 데 쓴다.
WATERLESS_TERMS = ("워터리스", "물없이", "노린스", "드라이샴푸")


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
                "A clean bright bathroom in warm tones: a light beige stone vanity countertop in the foreground, cream tiles, a folded white towel and a small green plant far behind.",
                ic.SCENE_LIFESTYLE, mood="청결·산뜻", good_for=("욕실", "위생용품", "뷰티", "생활용품"),
                avoid_for=WATERLESS_TERMS),   # 2026-11-26: 물 없이 쓰는 상품을 욕실에 두면 파는 이유와 반대다
    _ai_concept("AI_KITCHEN_COUNTER", "주방 조리대", "햇빛이 드는 주방 조리대. 생수·식품·주방용품에 어울립니다.",
                "A sunny cozy kitchen: a light oak countertop with visible wood grain in the foreground, a window with a sheer white curtain and a few green leaves far behind.",
                ic.SCENE_LIFESTYLE, mood="상쾌·일상", good_for=("식품", "주방", "생수", "음료")),
    _ai_concept("AI_LIVING_TABLE", "거실 원목 테이블", "아늑한 거실의 원목 테이블. 생활용품·인테리어 소품에 어울립니다.",
                "A cosy bright living room: a light oak table top with visible wood grain in the foreground, a sheer white curtain, a beige sofa and a small plant far in the background.",
                ic.SCENE_LIFESTYLE, mood="아늑·따뜻", good_for=("생활용품", "인테리어", "문구", "선물")),
    _ai_concept("AI_OUTDOOR_TRACK", "야외 산책로", "아침 햇살의 공원 산책로 옆 벤치 위. 운동·레저·보호대 상품에 어울립니다.",
                "An outdoor park in warm early morning sunlight: the flat top of a light wooden bench seat beside a running path in the foreground, green trees, grass and the path far behind.",
                ic.SCENE_LIFESTYLE, mood="활동·건강", good_for=("스포츠", "레저", "보호대", "기능성")),
    _ai_concept("AI_BEDROOM_SOFT", "포근한 침실", "부드러운 이불이 있는 밝은 침실. 여성용품·의류·아동 상품에 어울립니다.",
                "A soft bright bedroom: a smooth light oak bedside table top in the foreground, cream bedding and a sheer curtain glowing with morning light far behind.",
                ic.SCENE_LIFESTYLE, mood="포근·편안", good_for=("여성용품", "패션", "아동", "침구")),
    # 2026-11-26 — 상품이 실제로 쓰이는 상황(사용자 "상품판매성이 있는 것을 원하는것", 제미나이 협의 docs/HOMEZ_SALES_IMAGE_PLAN_20261126.md).
    # 물 없이 쓰는 세정 상품의 구매 동기(간병·입원, 캠핑·차박, 여행, 운동 뒤)를 장면으로 보여 준다. 사용법(뿌리고 수건으로 닦기)을 알리는 접힌 수건을
    # 빈 자리 왼쪽에 둔다 — 상품 자리(가운데)는 비운다. 수건은 사은품으로 오해되지 않게 상품과 떨어진 배경 소품으로만 둔다.
    _ai_concept("AI_CARE_BEDSIDE", "간병·침대 곁", "침대 곁 협탁 위, 접힌 흰 손수건. 거동이 불편한 가족을 돌볼 때 쓰는 상품(물 없이 닦는 세정제 등)에 어울립니다.",
                "A calm bright bedroom for home care: a light oak bedside table top in the foreground, a folded white washcloth on its left side, a bed with white linen far behind.",
                ic.SCENE_LIFESTYLE, mood="돌봄·청결", good_for=WATERLESS_TERMS + ("간병", "요양", "환자", "어르신")),
    _ai_concept("AI_CAMPING_TABLE", "캠핑 테이블", "숲속 캠핑장의 나무 테이블 위, 접힌 운동 수건. 캠핑·차박·야외에서 쓰는 상품에 어울립니다.",
                "A sunny forest campsite: a light wooden camping table top in the foreground, a folded gray sports towel on its left side, a tent and green trees far behind.",
                ic.SCENE_LIFESTYLE, mood="야외·자유", good_for=WATERLESS_TERMS + ("캠핑", "차박", "아웃도어", "등산")),
    _ai_concept("AI_TRAVEL_DESK", "여행 숙소", "숙소 방의 나무 책상 위, 접힌 흰 수건. 여행·출장에 챙겨 가는 상품에 어울립니다.",
                "A bright hotel room: a light wooden desk top in the foreground, a small folded white towel on its left side, an open suitcase and a window far behind.",
                ic.SCENE_LIFESTYLE, mood="여행·간편", good_for=WATERLESS_TERMS + ("여행", "출장", "휴대용", "여행용")),
    _ai_concept("AI_GYM_BENCH", "운동 뒤", "운동 시설 탈의실의 나무 벤치 위, 접힌 흰 운동 수건. 운동 뒤 빠르게 쓰는 상품에 어울립니다.",
                "A clean bright gym locker room: a light wooden bench top in the foreground, a folded white sports towel on its left side, lockers far behind.",
                ic.SCENE_LIFESTYLE, mood="활동·상쾌", good_for=WATERLESS_TERMS + ("운동", "헬스", "스포츠", "데오드란트")),
    _ai_concept("AI_STUDIO_TOWEL", "수건 스튜디오", "밝은 회색 탁자와 접힌 흰 면 수건만 있는 깔끔한 촬영장. 사용법(뿌리고 닦기)을 보여 주는 사진에 어울립니다.",
                "A minimal bright photo studio: a smooth light gray table top in the foreground, a neatly folded white cotton towel on its left side, a plain warm white backdrop.",
                ic.SCENE_STUDIO_LIGHT, mood="깔끔·설명", good_for=WATERLESS_TERMS + ("바디워시", "클렌징", "스킨케어", "화장품")),
    _ai_concept("AI_STUDIO_MARBLE", "대리석 스튜디오", "크림색 대리석 테이블과 은은한 햇빛. 뷰티·선물·소형 가전에 어울립니다.",
                "A minimal warm photo studio: a wide cream marble table top in the foreground, a smooth warm beige backdrop and soft sunlight from the left.",
                ic.SCENE_STUDIO_LIGHT, mood="고급·정돈", good_for=("뷰티", "선물", "전자기기", "주얼리")),
)}


# 2026-11-26(사용자 "#2#4 다시 만들어라"): 그려진 배경에서 탁자 앞모서리가 화면 안(높이 약 86%)에 있는 장면은 기본 바닥선이면 상품이
# 모서리 끝에 서서 떨어질 듯 보였고, 협탁·침대에 비해 너무 컸다 → 앞모서리에서 물러난 자리(바닥선)와 원근에 맞는 높이 한도.
PLACEMENT_TWEAKS: dict[str, dict[str, float]] = {
    "AI_CARE_BEDSIDE": {"ground_line": 0.80, "max_height": 0.50},
}


def placement_options(code: str) -> dict[str, float]:
    return dict(PLACEMENT_TWEAKS.get(code, {}))


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

__all__ = ["AI_CONCEPTS", "MAX_PICK", "PURPOSES", "LOOK", "PROMPT_RULES", "WATERLESS_TERMS", "PLACEMENT_TWEAKS", "placement_options", "list_ai_concepts", "get_ai_concept", "to_summary"]
