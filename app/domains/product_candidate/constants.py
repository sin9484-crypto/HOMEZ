"""
=========================================================
Homez OS

File : app/domains/product_candidate/constants.py

V3 ProductCandidate 고정 상수
=========================================================
"""


class CandidateStatus:
    """
    2026-08-14 테넌트 격리 감사(Gate R13) 이후: DISCOVERED/ANALYZED/
    RECOMMENDED/EXPIRED만 ProductCandidate.status(전역 워크플로우)의
    실제 값이다. APPROVED/HELD/REJECTED는 이제 회사별
    ProductCandidateSelection.status 전용 값이다(같은 문자열 상수를
    공유하되 저장되는 테이블이 다르다).

    [정책 A — 상태 의미 공식 계약, 2026-08-19 CTO 정책 결정]
    기존 상태값·DB 컬럼·API 계약과의 호환성을 유지하기 위해 상태
    "이름"은 그대로 두고, 각 상태의 의미를 아래와 같이 명시적으로
    고정한다. 이 계약은 model.py docstring, service.py의 관련 메서드,
    API 응답 설명, ko-KR/en-US 문구, 테스트 이름·assertion 설명에서
    일관되게 재사용해야 한다 — 새로 지어내지 않는다.

    - DISCOVERED: 후보가 생성·수집됨. 아직 시스템 검토가 완료되지
      않아 운영자 결정을 내릴 수 없다.
    - ANALYZED: **시스템 검토 단계가 완료되어 관리자가 승인/보류/
      거절을 판단할 수 있는 상태**다. 이름과 달리 반드시 AI 분석이나
      시장 분석을 의미하지 않는다 — 실제 AI 분석(apply_trend_analysis/
      apply_new_product_analysis, TREND_ANALYSIS/NEW_PRODUCT_ANALYSIS
      evidence)을 거쳐 도달할 수도 있고, 수동 등록 후보의 기본 정보
      확인(verify_private_candidate_info, MANUAL_INFO_CHECK evidence)
      만으로 도달할 수도 있다. "어떤 근거로 ANALYZED가 됐는가"는
      evidence_type과 trend_score/novelty_score의 존재 여부로
      구분한다(상태 이름 자체로는 구분하지 않는다).
    - RECOMMENDED: 실제 점수 또는 검증된 추천 근거(trend_score 또는
      novelty_score)가 존재하고, 시스템이 그것을 근거로 추천 판단을
      완료한 상태다. 추천 근거 없이 이 상태로 전환되어서는 안 된다
      (service.py::recommend()가 API 레벨에서 강제).
    - APPROVED / HELD / REJECTED: 권한 있는 관리자의 회사별 결정
      (ProductCandidateSelection, Gate R13).
    - EXPIRED: 유효기간 또는 정책에 의해 더 이상 사용할 수 없음
      (계약에만 존재, 만료 배치는 미구현 — 기존 상태 그대로).
    """

    DISCOVERED = "DISCOVERED"
    ANALYZED = "ANALYZED"
    RECOMMENDED = "RECOMMENDED"
    APPROVED = "APPROVED"
    HELD = "HELD"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    ALL = (
        DISCOVERED,
        ANALYZED,
        RECOMMENDED,
        APPROVED,
        HELD,
        REJECTED,
        EXPIRED,
    )

    # ProductCandidate.status(전역)가 실제로 도달 가능한 값의 전체 집합.
    GLOBAL_REACHABLE = (DISCOVERED, ANALYZED, RECOMMENDED, EXPIRED)

    # 회사가 아직 이 후보를 결정한 적이 없을 때, "최초 결정"이 가능한
    # 전역 워크플로우 상태(ProductCandidate.status 기준).
    DECIDABLE = (ANALYZED, RECOMMENDED)

    # 회사가 이미 HOLD로 결정했다면, 같은 회사가 재결정(APPROVE/REJECT로
    # 전환)할 수 있는 ProductCandidateSelection.status 값.
    SELECTION_REDECIDABLE = (HELD,)

    # 회사별 최종(더 이상 재결정 불가) 선택 상태.
    TERMINAL = (APPROVED, REJECTED)


class CandidateVisibility:
    """
    ProductCandidate.visibility 값(2026-08-15 V7 Gate 2) — GLOBAL은
    전역 공유 발견 카탈로그, PRIVATE는 owner_company_id만 조회·취급할
    수 있는 회사 전용 후보.
    """

    GLOBAL = "GLOBAL"
    PRIVATE = "PRIVATE"

    ALL = (GLOBAL, PRIVATE)


class EvidenceType:
    """
    NEW_PRODUCT_ANALYSIS/TREND_ANALYSIS는 실제 AI 파이프라인
    (apply_new_product_analysis/apply_trend_analysis)이 계산한 점수를
    동반하는 근거 전용이다. MANUAL_INFO_CHECK(2026-08-19 CTO 보완
    지시)는 그 반대 — 시장 데이터나 외부 AI 없이 "사용자가 입력한
    기본 정보가 등록되어 있다"는 사실만 기록하는 근거이며, score/
    confidence를 절대 동반하지 않는다(둘 다 항상 None).
    """

    RAW_SOURCE = "RAW_SOURCE"
    TREND_ANALYSIS = "TREND_ANALYSIS"
    NEW_PRODUCT_ANALYSIS = "NEW_PRODUCT_ANALYSIS"
    MANUAL_INFO_CHECK = "MANUAL_INFO_CHECK"


class DecisionAction:

    APPROVE = "APPROVE"
    HOLD = "HOLD"
    REJECT = "REJECT"


__all__ = [
    "CandidateStatus",
    "CandidateVisibility",
    "EvidenceType",
    "DecisionAction",
]
