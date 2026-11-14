"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/attribute_suggest_service.py

2026-11-14 — 위저드 '상세 글에서 속성 뽑기'(조사 문서 3-6). 계산은 `attribute_extract.py`(순수)가 하고, 이 서비스는 위저드·후보의
온채널 고시 근거를 읽어 넘긴다. **위저드를 바꾸지 않는다** — 제안과 대조 결과만 돌려주고, 칸에 넣기·저장은 화면에서 사람이 한다.

AI로 더 뽑기(`use_ai`)는 사용자가 고를 때만, 기존 AI 초안과 같은 통제(월 한도·승인 센터 허용 `LLM`/`AI_ANALYSIS`·사용 기록)를 거친다.
보내는 것은 붙여 넣은 글·상품명·옵션뿐이다. 응답은 근거가 원문에 그대로 있고 값이 근거 안에 있을 때만 받는다.
=========================================================
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.external_call.constants import CallPurpose
from app.domains.marketplace_listing import attribute_extract as ax
from app.domains.marketplace_listing.listing_wizard_service import ListingWizardService

USAGE_PURPOSE_ATTRIBUTE = "ATTRIBUTE"
AI_MAX_OUTPUT_TOKENS = 800
MAX_CURRENT_LABELS = 80


class AttributeSuggestService:

    def __init__(self, db: Session, credential_store=None, *, ai_service_factory=None):
        self.db = db
        self._credential_store = credential_store
        self._ai_service_factory = ai_service_factory

    def _ai(self):
        if self._ai_service_factory is not None:
            return self._ai_service_factory()
        from app.domains.marketplace_listing.ai_draft_service import AiDraftService

        return AiDraftService(self.db, self._credential_store)

    def _supplier_notice(self, candidate_id: int | None) -> list[tuple[str, str]]:
        if not candidate_id:
            return []
        try:
            from app.domains.marketplace_listing.registration_assistant_service import RegistrationAssistantService

            return RegistrationAssistantService(self.db)._onch_rows(candidate_id)
        except Exception:  # noqa: BLE001 — 근거 자료일 뿐이다. 못 읽어도 붙여 넣은 글로는 뽑는다.
            return []

    @staticmethod
    def _unit_counts(channel_selections_json: str | None) -> list[int]:
        try:
            entries = json.loads(channel_selections_json or "[]")
        except ValueError:
            return []
        counts: list[int] = []
        for entry in entries if isinstance(entries, list) else []:
            items = ((entry or {}).get("required_fields") or {}).get("items") if isinstance(entry, dict) else None
            for item in items if isinstance(items, list) else []:
                value = item.get("unitCount") if isinstance(item, dict) else None
                if isinstance(value, int) and not isinstance(value, bool):
                    counts.append(value)
        return counts

    def suggest(self, wizard_id: int, company_id: int, user_id: int, *, pasted: str, current: dict[str, str], use_ai: bool = False) -> dict:
        wizard = ListingWizardService(self.db).get(wizard_id, company_id)
        try:
            draft = json.loads(wizard.draft_json or "{}")
        except ValueError:
            draft = {}
        title = str(draft.get("product_name") or "")
        options = [
            f"{o.get('name') or ''} {v}".strip()
            for o in (draft.get("options") or []) if isinstance(o, dict)
            for v in (o.get("values") or [])
        ]
        supplier_notice = self._supplier_notice(wizard.product_candidate_id)
        found = ax.extract(pasted=pasted, supplier_notice=supplier_notice, title=title, options=options, description=str(draft.get("description") or ""))

        ai_info: dict = {"used": False}
        if use_ai:
            if not str(pasted or "").strip():
                raise BadRequestException("AI로 뽑으려면 공급처 상세 글을 먼저 붙여 넣어 주세요.")
            prompt, source_text = ax.ai_user_prompt(pasted=pasted, title=title, options=options)
            service = self._ai()
            result, cost, meta = service.call_llm(
                company_id, user_id, target=f"WZ{wizard_id}:ATTR", system=ax.AI_SYSTEM, user_prompt=prompt,
                ext_purpose=CallPurpose.AI_ANALYSIS, max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            )
            kept, refused = ax.validate_ai_attributes(result.text, source_text)
            usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_krw": cost, "ok": bool(kept), "purpose": USAGE_PURPOSE_ATTRIBUTE}
            month = service.record_usage(company_id, user_id, f"WZ{wizard_id}", result.model, usage)
            seen = {(f.kind, ax.norm_value(f.value), f.source) for f in found}
            found += [f for f in kept if (f.kind, ax.norm_value(f.value), f.source) not in seen]
            ai_info = {"used": True, "kept": len(kept), "refused": refused[:20], "usage": {**usage, "model": result.model, **month, "monthly_limit_krw": meta.get("limit")}}

        labels = {str(k)[:60]: str(v or "")[:300] for k, v in list((current or {}).items())[:MAX_CURRENT_LABELS]}
        return {
            "rows": ax.compare(found, labels),
            "found": [f.as_dict() for f in found],
            "quantity_check": ax.quantity_check(found, self._unit_counts(wizard.channel_selections_json)),
            "sources": {"pasted": bool(str(pasted or "").strip()), "supplier_notice": len(supplier_notice), "title": bool(title), "options": len(options)},
            "ai": ai_info,
        }


__all__ = ["AttributeSuggestService"]
