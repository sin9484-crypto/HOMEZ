"""
=========================================================
Homez OS

File : app/domains/ai_learning/constants.py

2026-09-10 Phase 12(HOMEZ_USER_OPERATION_SETTINGS.md 12번 — "AI가
추천한 상품, 추천 이유와 점수, 사용자 승인·거절, 실제 판매량·마진·
품절·취소·반품·배송 지연과 예측 차이를 저장한다", "학습 가능한
데이터셋을 운영 DB와 분리한다", "초기 학습 검토는 최소 50건의
완료 주문부터 시작", "누적 주문이 1,000건을 넘으면 최소 100건으로
상향").

이 세션 전체(그리고 이 Phase)에서 실제 모델 학습은 실행하지
않는다 — 이 도메인은 "학습 가능한 데이터를 올바르게 모아두고,
후보 모델을 실제로 적용하기 전 반드시 거쳐야 하는 절차(오프라인
평가 → 회귀 비교 → 사용자 승인)를 강제하는 상태기계"만 만든다.
=========================================================
"""

from __future__ import annotations

# 문서 원문 초기값.
INITIAL_MIN_SAMPLE_SIZE = 50
RAISED_MIN_SAMPLE_SIZE = 100
RAISE_THRESHOLD_TOTAL_ORDERS = 1000


class ModelCandidateStatus:
    """
    "현재 데이터만으로 학습됐다고 주장하지 않는다"를 상태 이름
    자체로 강제한다 — 어떤 상태도 "학습 완료"/"적용됨"을 의미하지
    않는다. APPROVED조차 "실제로 라이브에 적용해도 좋다고 사람이
    승인했다"는 뜻일 뿐, 이 상태기계 자신은 그 적용을 실행하지
    않는다(적용 자체는 이 Phase 범위 밖 — 실제 학습 코드가 없다).
    """

    DRAFT = "DRAFT"
    OFFLINE_EVALUATED = "OFFLINE_EVALUATED"
    REGRESSION_COMPARED = "REGRESSION_COMPARED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        DRAFT: {OFFLINE_EVALUATED, REJECTED},
        OFFLINE_EVALUATED: {REGRESSION_COMPARED, REJECTED},
        REGRESSION_COMPARED: {APPROVED, REJECTED},
        APPROVED: set(),
        REJECTED: set(),
    }

    LABELS_KO = {
        DRAFT: "초안",
        OFFLINE_EVALUATED: "오프라인 평가 완료",
        REGRESSION_COMPARED: "회귀 비교 완료",
        APPROVED: "승인됨(실제 적용은 별도)",
        REJECTED: "반려됨",
    }


__all__ = [
    "INITIAL_MIN_SAMPLE_SIZE",
    "RAISED_MIN_SAMPLE_SIZE",
    "RAISE_THRESHOLD_TOTAL_ORDERS",
    "ModelCandidateStatus",
]
