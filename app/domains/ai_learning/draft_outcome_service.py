"""
=========================================================
Homez OS

File : app/domains/ai_learning/draft_outcome_service.py

2026-11-09 — AI 초안 평가 고리(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-1, 사용자 승인 "1번 진행").
AI 초안과 사람이 실제로 쓴 글을 함께 남겨, 30건이 모이면 사람이 읽고 문제를 분류(오류 분석)할 수 있게 한다.

  - **기록이 본 기능을 막지 않는다**: 표가 없거나(Migration 승인 전) 저장에 실패해도 AI 초안·답변 보내기는 그대로 동작한다.
    기록 함수(`record_draft`, `record_sent_reply`)는 예외를 밖으로 내지 않고 None/False를 돌려준다.
  - **표를 몰래 만들지 않는다**: 표는 Migration `20261109_00`이 만든다(앱의 Migration 승인 화면, 백업 후).
  - **가리고 저장한다**: 글은 `draft_outcome_diff.mask`로 개인정보를 가린 뒤 저장한다. 고객 문의 원문은 저장하지 않는다.
  - **보낸 글은 한 번만**: 고객 응대 답변은 쿠팡 등록이 성공(SUCCEEDED)했을 때만, 아직 최종 글이 없는 기록에만 남긴다(조건부 UPDATE —
    같은 기록에 두 번 들어오면 두 번째는 바꾸지 않는다). 회사·기능이 다른 기록 번호는 무시한다.
  - 등록 도우미 초안의 '최종'은 저장하지 않고, 읽을 때 위저드의 지금 값(상품명·검색어·상세 설명)과 비교한다(위저드가 곧 최종 기록이다).
  - **판정하지 않는다**: 바뀐 정도만 보여 준다. 고친 비율이 낮다고 자율을 올리지 않는다.
=========================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ServiceUnavailableException
from app.domains.ai_learning import draft_outcome_diff as diff
from app.domains.ai_learning.model import AiDraftOutcomeRecord

logger = logging.getLogger(__name__)

FEATURE_LISTING = "LISTING_DRAFT"
FEATURE_CS = "CS_REPLY"
FEATURES = (FEATURE_LISTING, FEATURE_CS)
LISTING_FIELDS = ("product_name", "keywords", "description")
CS_FIELD = "reply"
FINAL_SOURCE_SENT = "SENT"
# 사람이 직접 읽고 문제를 분류하기 시작할 기록 수(Hamel Husain의 '최소 30건 직접 읽기' — 조사 문서 1-4).
REVIEW_TARGET = 30
MAX_LIST = 200
NOT_READY_MESSAGE = "AI 초안 기록 저장소가 아직 준비되지 않았습니다 — 앱을 다시 시작해 Migration 승인 화면에서 승인해 주세요(승인 전 자동 백업)."
_WIZARD_REF = re.compile(r"^WZ(\d{1,12})$")
_FINAL_REF = re.compile(r"^(INQ|CC)\d{1,20}$")


def prompt_fingerprint(system_prompt: str) -> str:
    """시스템 프롬프트의 짧은 지문. 프롬프트를 바꾸면 값이 바뀐다(전·후 비교용)."""

    return "sp-" + hashlib.sha256((system_prompt or "").encode("utf-8")).hexdigest()[:12]


def _masked_fields(fields: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = fields.get(key)
        if isinstance(value, (list, tuple)):
            out[key] = [diff.mask(str(v)) for v in value if str(v).strip()][:30]
        elif value is not None:
            out[key] = diff.mask(str(value))
    return out


def _loads(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class DraftOutcomeService:

    def __init__(self, db: Session, *, now_factory=None):
        self.db = db
        self._now = now_factory or datetime.utcnow

    def installed(self) -> bool:
        try:
            return inspect(self.db.get_bind()).has_table(AiDraftOutcomeRecord.__tablename__)
        except Exception:  # noqa: BLE001 — 확인하지 못하면 없는 것으로 본다(기록하지 않음)
            return False

    # ------------------------------------------------------------
    # 기록 (본 기능을 막지 않는다)
    # ------------------------------------------------------------

    def record_draft(
        self, company_id: int, user_id: int | None, feature: str, ref: str, model: str | None, system_prompt: str, fields: dict[str, Any],
    ) -> int | None:
        """초안 1건을 남기고 번호를 돌려준다. 표가 없거나 실패하면 None(예외 없음)."""

        if feature not in FEATURES or not isinstance(fields, dict):
            return None
        keys = LISTING_FIELDS if feature == FEATURE_LISTING else (CS_FIELD,)
        masked = _masked_fields(fields, keys)
        if not any(diff.field_text(v) for v in masked.values()):
            return None
        if not self.installed():
            return None
        try:
            row = AiDraftOutcomeRecord(
                company_id=company_id, feature=feature, ref=str(ref or "")[:40] or "-", model=(str(model)[:80] if model else None),
                prompt_version=prompt_fingerprint(system_prompt), draft_json=json.dumps(masked, ensure_ascii=False),
                created_by=user_id, created_at=self._now(),
            )
            self.db.add(row)
            self.db.commit()
            return row.id
        except Exception:  # noqa: BLE001 — 기록 실패가 AI 초안을 막지 않는다
            self.db.rollback()
            logger.warning("AI 초안 기록 저장 실패(초안은 그대로 돌려줌)", exc_info=True)
            return None

    def record_sent_reply(self, company_id: int, record_id: Any, content: Any, final_ref: str) -> bool:
        """쿠팡에 보낸 답변을 그 초안 기록에 남긴다. 아직 최종 글이 없고 회사·기능이 맞을 때만(조건부 UPDATE). 실패해도 예외 없음."""

        if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0:
            return False
        if not isinstance(content, str) or not content.strip() or not _FINAL_REF.match(str(final_ref or "")):
            return False
        if not self.installed():
            return False
        try:
            result = self.db.execute(
                update(AiDraftOutcomeRecord)
                .where(AiDraftOutcomeRecord.id == record_id)
                .where(AiDraftOutcomeRecord.company_id == company_id)
                .where(AiDraftOutcomeRecord.feature == FEATURE_CS)
                .where(AiDraftOutcomeRecord.final_json.is_(None))
                .values(
                    final_json=json.dumps({CS_FIELD: diff.mask(content)}, ensure_ascii=False), final_source=FINAL_SOURCE_SENT,
                    final_ref=final_ref, finalized_at=self._now(),
                )
            )
            self.db.commit()
            return result.rowcount == 1
        except Exception:  # noqa: BLE001 — 기록 실패가 이미 끝난 답변 등록 결과를 바꾸지 않는다
            self.db.rollback()
            logger.warning("보낸 답변 기록 저장 실패(답변 등록 결과는 그대로)", exc_info=True)
            return False

    # ------------------------------------------------------------
    # 읽기
    # ------------------------------------------------------------

    def _current_listing(self, company_id: int, ref: str, cache: dict) -> dict[str, Any] | None:
        match = _WIZARD_REF.match(ref or "")
        if not match:
            return None
        wizard_id = int(match.group(1))
        if wizard_id not in cache:
            from app.domains.marketplace_listing.model import ListingWizard

            wizard = self.db.query(ListingWizard).filter(ListingWizard.id == wizard_id, ListingWizard.company_id == company_id).first()
            draft = _loads(wizard.draft_json) if wizard is not None else None
            cache[wizard_id] = _masked_fields(draft, LISTING_FIELDS) if draft is not None else None
        return cache[wizard_id]

    def list(self, company_id: int, feature: str | None = None, limit: int = 50) -> dict[str, Any]:
        if feature is not None and feature not in FEATURES:
            raise BadRequestException("기능 이름이 올바르지 않습니다.")
        if not self.installed():
            raise ServiceUnavailableException(NOT_READY_MESSAGE)
        limit = max(1, min(MAX_LIST, int(limit)))
        query = self.db.query(AiDraftOutcomeRecord).filter(AiDraftOutcomeRecord.company_id == company_id)
        if feature:
            query = query.filter(AiDraftOutcomeRecord.feature == feature)
        rows = query.order_by(AiDraftOutcomeRecord.id.desc()).limit(limit).all()
        cache: dict[int, Any] = {}
        records = []
        for row in rows:
            draft = _loads(row.draft_json) or {}
            if row.feature == FEATURE_LISTING:
                final = self._current_listing(company_id, row.ref, cache)
                final_state = "CURRENT_WIZARD" if final is not None else "WIZARD_MISSING"
            else:
                final = _loads(row.final_json)
                final_state = "SENT" if final is not None else "NOT_SENT"
            records.append({
                "id": row.id, "feature": row.feature, "ref": row.ref, "model": row.model, "prompt_version": row.prompt_version,
                "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
                "finalized_at": row.finalized_at.isoformat(timespec="seconds") if row.finalized_at else None,
                "final_state": final_state, "final_ref": row.final_ref, "draft": draft, "final": final,
                "comparison": diff.compare(draft, final),
            })
        return {"records": records, "summary": self._summary(company_id), "review_target": REVIEW_TARGET}

    def _summary(self, company_id: int) -> dict[str, Any]:
        """기능별 전체 기록 수(목록 상한과 무관). 비교 요약은 목록에 나온 기록으로 화면이 계산한다."""

        out: dict[str, Any] = {}
        for feature in FEATURES:
            base = self.db.query(AiDraftOutcomeRecord).filter(AiDraftOutcomeRecord.company_id == company_id, AiDraftOutcomeRecord.feature == feature)
            total = base.count()
            sent = base.filter(AiDraftOutcomeRecord.final_json.isnot(None)).count() if feature == FEATURE_CS else None
            out[feature] = {"total": total, "sent": sent}
        return out


__all__ = [
    "FEATURES", "FEATURE_CS", "FEATURE_LISTING", "NOT_READY_MESSAGE", "REVIEW_TARGET",
    "DraftOutcomeService", "prompt_fingerprint",
]
