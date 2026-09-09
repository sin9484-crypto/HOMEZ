"""
=========================================================
Homez OS

File : app/domains/guides/interactive_guidance_service.py

Gate AI-F2(2026-08-22 CTO 지시) — 대화형 사용자 안내. 기존 정적 가이드
목록 조회(app/domains/guides/service.py::list_guides)는 전혀 건드리지
않는다 — 이 서비스는 그 목록을 그대로 재사용해 사용자 질문 문자열과
매칭할 뿐, 새 가이드 콘텐츠를 만들거나 파일시스템에 없는 내용을
지어내지 않는다.

생성형 AI를 호출하지 않는다 — 정적 키워드 사전 기반 매칭(RULE_ENGINE)
이다. 그래서 항상 결정적이고, 실제로 존재가 확인된(파일시스템
재검증까지 마친) 가이드만 추천한다.

capability_code: USER_GUIDANCE.

안전 요구사항(마켓·판매 도메인과 달리 이 서비스만의 고유 제약,
capability_catalog.py의 forbidden_operations와 동일):
  - 비밀번호·Credential을 절대 요청하지 않는다 — 이 서비스는 질문
    문자열을 받아 가이드 id만 돌려줄 뿐, 어떤 입력 필드도 요구하지
    않는다(GuidanceMatch/응답 스키마 어디에도 credential류 필드가
    없다).
  - 존재하지 않는 UI/버튼을 안내하지 않는다 — list_guides()가 이미
    파일시스템 존재 여부까지 확인한 GuideSummary만 반환하므로, 이
    서비스가 추천하는 가이드는 항상 실제로 서빙 가능한 것만이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.ai_governance.constants import AIResultType
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.schema import AIResultEnvelope
from app.domains.ai_governance.service import build_ai_result_envelope
from app.domains.ai_governance.service import require_active_capability
from app.domains.guides.schema import GuideSummary
from app.domains.guides.service import list_guides

MIN_QUERY_LENGTH = 2

# 정적 키워드 사전(운영 편의 목적, 공식 분류 체계가 아님) —
# GUIDE_REGISTRY의 실제 id에만 대응한다. 새 가이드가 추가되면 이
# 사전에도 함께 추가해야 매칭 대상이 된다(자동 추론하지 않음).
_GUIDE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "quick-start": (
        "시작", "처음", "가입", "로그인", "대시보드", "둘러보기",
        "start", "login", "dashboard", "overview", "getting started",
    ),
    "admin-setup": (
        "관리자", "스토어 연동", "매장 연동", "쇼핑몰 연동", "초대",
        "권한", "계정 설정", "admin", "store connection", "invite",
        "permission", "account setup",
    ),
    "product-registration": (
        "상품 등록", "상품등록", "상품", "리스팅", "마켓플레이스",
        "쿠팡", "네이버", "product", "listing", "register", "coupang",
        "naver",
    ),
    "mobile-usage": (
        "모바일", "휴대폰", "핸드폰", "앱", "mobile", "phone", "app",
    ),
    "troubleshooting": (
        "오류", "에러", "안됨", "안돼요", "실패", "비밀번호 오류",
        "문제", "안 열려", "error", "trouble", "fail", "issue", "broken",
    ),
}


@dataclass(frozen=True)
class GuidanceMatch:

    guide_id: str
    score: int
    matched_keywords: list[str]


class InteractiveGuidanceService:

    def ask(
        self, query: str, *, locale: str = "ko-KR",
    ) -> tuple[list[GuideSummary], AIResultEnvelope]:

        require_active_capability(CapabilityCode.USER_GUIDANCE)

        normalized = (query or "").strip()
        guides = list_guides()

        matches: list[GuidanceMatch] = []

        if len(normalized) >= MIN_QUERY_LENGTH:
            lowered = normalized.lower()
            for guide_id, keywords in _GUIDE_KEYWORDS.items():
                matched_keywords = [
                    kw for kw in keywords if kw.lower() in lowered
                ]
                if matched_keywords:
                    matches.append(GuidanceMatch(
                        guide_id=guide_id,
                        score=len(matched_keywords),
                        matched_keywords=matched_keywords,
                    ))

        matches.sort(key=lambda m: -m.score)
        score_by_id = {m.guide_id: m.score for m in matches}

        suggested = sorted(
            (g for g in guides if g.id in score_by_id),
            key=lambda g: -score_by_id[g.id],
        )

        envelope = build_ai_result_envelope(
            capability_code=CapabilityCode.USER_GUIDANCE,
            result_type=(
                AIResultType.CALCULATED_RESULT if suggested
                else AIResultType.EVIDENCE_REQUIRED
            ),
            decision="GUIDE_MATCHED" if suggested else "NO_MATCH",
            confirmed_facts={
                "query_length": len(normalized),
                "guide_count_total": len(guides),
            },
            calculated_values={"matched_guide_count": len(suggested)},
            assumptions=[
                "생성형 AI가 아니라 정적 키워드 사전 기반 매칭입니다 — "
                "문장을 실제로 이해해서 답하지 않습니다.",
                "실제로 존재가 확인된(파일시스템 재검증 완료) 가이드만 "
                "추천합니다 — 존재하지 않는 화면/버튼을 지어내지 "
                "않습니다.",
            ],
            missing_evidence=(
                []
                if suggested
                else [
                    "질문과 일치하는 가이드 키워드를 찾지 못했습니다 — "
                    "더 구체적으로 질문해 주세요.",
                ]
            ),
            confidence=(0.5 if suggested else 0.0),
            recommended_actions=[f"guide:{g.id}" for g in suggested],
            execution_allowed=False,
        )

        return suggested, envelope


__all__ = ["InteractiveGuidanceService", "GuidanceMatch"]
