"""
=========================================================
Homez OS

File : app/domains/order/cs_reply_draft_service.py

2026-10-23 — 상품 관리 AI P3: 고객 문의 **답변 초안**(사용자가 눌렀을 때만 AI 호출, `AiDraftService.call_llm`의 같은 보호 경로: 설정 → 월 한도 → 승인 센터 허용(AI_DRAFT, 횟수형) → 호출).
규칙은 `cs_reply_draft.py`가 정한다.

  - **저장하지 않는다**: 문의 글·초안은 DB·감사 기록·파일 어디에도 남기지 않는다(개인정보 최소화). 남는 것은 AI 사용 기록(토큰·비용·성공 여부)뿐이다.
  - **AI에 보내기 전에 가린다**: 전화번호·이메일·주소·URL·긴 숫자·라벨 뒤 이름. 가린 곳 수를 응답에 담아 사용자가 알 수 있게 한다.
  - 응답은 규칙 검증을 통과해야만 보인다(통째로 버림). 비용은 응답마다 알린다. 한 번 누르면 호출 1회 — 캐시가 없다(문의마다 글이 다르다).
  - 한계: 허용 센터의 목적 이름은 기존 `AI_DRAFT`("등록 도우미 AI 초안")를 쓴다 — 새 목적을 만들면 허용 화면·상수·시험을 함께 바꿔야 해서 이번에는 쓰지 않았다(🔴 별도 목적이 필요하면 추가).
=========================================================
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.external_call.constants import CallPurpose
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.order import cs_reply_draft as rules

USAGE_REF = "CS"


class CsReplyDraftService:

    def __init__(self, db: Session, ai: AiDraftService):
        self.db = db
        self.ai = ai

    def draft(self, company_id: int, user_id: int, kind: str, inquiry_text: str, facts: Any = None) -> dict[str, Any]:
        if kind not in rules.KINDS:
            raise BadRequestException("문의 종류가 올바르지 않습니다.")
        text = (inquiry_text or "").strip() if isinstance(inquiry_text, str) else ""
        if not text:
            raise BadRequestException("문의 내용을 붙여 넣어 주세요.")
        if len(text) > rules.MAX_INQUIRY_CHARS:
            raise BadRequestException(f"문의 내용은 {rules.MAX_INQUIRY_CHARS}자까지만 쓸 수 있습니다.")

        masked = rules.mask_personal_info(text)
        cleaned_facts = rules.clean_facts(facts)
        prompt = rules.build_user_prompt(kind, masked.text, cleaned_facts)
        allowed_text = masked.text + "\n" + "\n".join(value for _, value in cleaned_facts)

        result, cost, meta = self.ai.call_llm(
            company_id, user_id, target=USAGE_REF, system=rules.SYSTEM_PROMPT, user_prompt=prompt,
            ext_purpose=CallPurpose.AI_DRAFT, max_output_tokens=rules.MAX_OUTPUT_TOKENS,
        )
        draft, rejected = rules.validate_draft(result.text, allowed_text)
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_krw": cost, "ok": draft is not None, "purpose": "CS_DRAFT"}
        month = self.ai.record_usage(company_id, user_id, USAGE_REF, result.model, usage)
        return {
            "draft": draft, "rejected": rejected, "kind": kind, "masked_count": masked.masked_count, "facts_used": len(cleaned_facts),
            "footer": rules.FOOTER_KO,
            "usage": {**usage, "model": result.model, **month, "monthly_limit_krw": meta["limit"]},
        }


__all__ = ["CsReplyDraftService", "USAGE_REF"]
