"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/ai_draft_service.py

2026-10-18 — 등록 도우미 AI 초안(Phase B) 서비스: 설정·한도·허용 확인 → 호출 → 사용 기록 → 검증 → 사용자가 고른 필드만 적용.

흐름(모두 사용자가 누르는 단계 — 자동 제출 없음):  AI 초안 받기 → (필드별 선택) 적용 → 등록 도우미 5단계 제안 → 사전 검사 → … 8단계 승인은 그대로 사용자 몫.

비용·안전 통제(세 겹):
  1. 사용자가 정한 **월 한도**(원). 설정이 없거나 0이면 쓸 수 없다. 이번 달 사용액 + 이번 호출의 최악 추정액이 한도를 넘으면 호출하지 않는다.
  2. 승인 센터 허용(`LLM`/`AI_DRAFT`, 횟수형). 허용이 없으면 호출하지 않고 허용 요청이 올라간다.
  3. 호출마다 사용 기록(`AI_DRAFT_USAGE` 감사 기록: 모델·입력/출력 토큰·추정 비용). 실패·검증 탈락이어도 호출했으면 기록한다.

보내는 것은 상품 정보(상품명·브랜드·카테고리·옵션·검색어·고시)뿐이다. API 키는 Windows 자격증명 저장소에만 둔다(DB·감사 기록·응답에 남기지 않는다).
=========================================================
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.marketplace_listing.llm_client import AnthropicClient
from app.domains.marketplace_listing.llm_client import LlmError
from app.domains.marketplace_listing.llm_client import LlmResult
from app.domains.marketplace_listing.llm_draft_provider import SYSTEM_PROMPT
from app.domains.marketplace_listing.llm_draft_provider import AiDraftInput
from app.domains.marketplace_listing.llm_draft_provider import build_user_prompt
from app.domains.marketplace_listing.llm_draft_provider import validate_response
from app.domains.marketplace_listing.listing_wizard_schema import WizardDraftUpdateRequest
from app.domains.marketplace_listing.listing_wizard_service import ListingWizardService
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.service import UserSettingService

CREDENTIAL_TARGET = "HOMEZ/LLM/ANTHROPIC"
SETTINGS_KEY = "listing_defaults"
SETTINGS_SUBKEY = "ai_draft"
USAGE_ACTION = "AI_DRAFT_USAGE"
MAX_OUTPUT_TOKENS = 1500
MAX_MONTHLY_LIMIT_KRW = 1_000_000
MAX_PRICE_PER_MILLION_KRW = 1_000_000
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{3,80}$")
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{20,300}$")  # 보이는 ASCII만, 공백 없음


def _positive_number(value: Any, label: str, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= upper:
        raise BadRequestException(f"{label}은(는) 0보다 크고 {upper:g} 이하의 숫자여야 합니다.")
    return float(value)


def estimate_cost_krw(input_tokens: float, output_tokens: float, in_price: float, out_price: float) -> float:
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


class AiDraftService:

    def __init__(
        self, db: Session, credential_store: CredentialStore, *,
        client_factory: Callable[[str, str], Any] | None = None, now_factory: Callable[[], datetime] | None = None,
    ):
        self.db = db
        self.store = credential_store
        self._client_factory = client_factory or (lambda api_key, model: AnthropicClient(api_key, model))
        self._now = now_factory or datetime.utcnow

    # ------------------------------------------------------------
    # 설정
    # ------------------------------------------------------------

    def _stored_blob(self, user_id: int, company_id: int) -> tuple[dict, int]:
        row = UserSettingRepository(self.db).get_for_user(user_id, company_id, SETTINGS_KEY)
        if row is None:
            return {}, 0
        try:
            value = json.loads(row.setting_value)
        except ValueError:
            return {}, row.version
        return (value if isinstance(value, dict) else {}), row.version

    def _settings(self, user_id: int, company_id: int) -> dict:
        blob, _version = self._stored_blob(user_id, company_id)
        value = blob.get(SETTINGS_SUBKEY)
        return value if isinstance(value, dict) else {}

    def _key_saved(self) -> bool:
        try:
            return bool(self.store.exists(CREDENTIAL_TARGET))
        except CredentialStoreError:
            return False

    def _read_key(self) -> str | None:
        try:
            payload = self.store.read(CREDENTIAL_TARGET)
        except (CredentialNotFoundError, CredentialStoreError):
            return None
        key = payload.get("api_key") if isinstance(payload, dict) else None
        return key if isinstance(key, str) and key else None

    def status(self, user_id: int, company_id: int) -> dict:
        s = self._settings(user_id, company_id)
        used, calls = self._month_usage()
        limit = int(s.get("monthly_limit_krw") or 0)
        key_saved = self._key_saved()
        complete = bool(limit > 0 and s.get("model") and s.get("in_price") and s.get("out_price") and key_saved)
        return {
            "configured": complete, "key_saved": key_saved, "model": s.get("model") or "",
            "monthly_limit_krw": limit, "in_price": s.get("in_price"), "out_price": s.get("out_price"),
            "month_used_krw": round(used, 2), "month_calls": calls,
            "month_remaining_krw": round(max(limit - used, 0), 2) if limit > 0 else 0,
        }

    def save_settings(
        self, user_id: int, company_id: int, *, model: str, monthly_limit_krw: int, in_price: float, out_price: float,
        api_key: str | None = None,
    ) -> dict:
        model = (model or "").strip()
        if not _MODEL_PATTERN.match(model):
            raise BadRequestException("모델 이름 형식이 올바르지 않습니다(영문·숫자·.-_: 3~80자).")
        if isinstance(monthly_limit_krw, bool) or not isinstance(monthly_limit_krw, int) or not 1 <= monthly_limit_krw <= MAX_MONTHLY_LIMIT_KRW:
            raise BadRequestException(f"월 한도는 1~{MAX_MONTHLY_LIMIT_KRW:,}원 사이의 정수여야 합니다.")
        in_price = _positive_number(in_price, "입력 단가(100만 토큰당 원)", MAX_PRICE_PER_MILLION_KRW)
        out_price = _positive_number(out_price, "출력 단가(100만 토큰당 원)", MAX_PRICE_PER_MILLION_KRW)
        if api_key is not None and api_key != "":
            if not _KEY_PATTERN.match(api_key):
                raise BadRequestException("API 키 형식이 올바르지 않습니다.")
            try:
                self.store.save(CREDENTIAL_TARGET, {"api_key": api_key})
            except CredentialStoreError:
                raise ServiceUnavailableException("API 키를 안전 저장소에 저장하지 못했습니다.") from None
        blob, version = self._stored_blob(user_id, company_id)
        blob[SETTINGS_SUBKEY] = {"model": model, "monthly_limit_krw": monthly_limit_krw, "in_price": in_price, "out_price": out_price}
        try:
            UserSettingService(UserSettingRepository(self.db)).put(user_id, company_id, SETTINGS_KEY, blob, version, 1)
        except ConflictException:
            self.db.rollback()
            raise
        return self.status(user_id, company_id)

    # ------------------------------------------------------------
    # 사용 기록
    # ------------------------------------------------------------

    def _month_tag(self) -> str:
        """사용 기록의 월 표식(YYYYMM). 감사 기록 테이블의 시각 열 유무와 무관하게 이번 달 합계를 낼 수 있도록 기록 키에 넣는다."""

        now = self._now()
        return f"{now.year:04d}{now.month:02d}"

    def _month_usage(self) -> tuple[float, int]:
        rows = self.db.execute(
            text("SELECT description FROM audit_logs WHERE action = :a AND entity_id LIKE :p"),
            {"a": USAGE_ACTION, "p": f"{self._month_tag()}:%"},
        ).fetchall()
        total, calls = 0.0, 0
        for (description,) in rows:
            try:
                payload = json.loads(description)
                total += float(payload.get("c") or 0)
                calls += 1
            except (ValueError, TypeError, AttributeError):
                continue
        return total, calls

    def _record_usage(self, company_id: int, user_id: int, ref: str, model: str, usage: dict) -> None:
        payload = json.dumps({"m": model, "i": usage["input_tokens"], "o": usage["output_tokens"], "c": usage["cost_krw"], "ok": usage["ok"], "p": usage.get("purpose", "DRAFT")}, separators=(",", ":"))
        write_audit_log(
            self.db, user_id=user_id, company_id=company_id, action=USAGE_ACTION, entity="ai_draft",
            entity_id=f"{self._month_tag()}:{ref}", description=payload,
        )
        self.db.commit()

    # ------------------------------------------------------------
    # 입력
    # ------------------------------------------------------------

    def build_input(self, wizard_id: int, company_id: int) -> AiDraftInput:
        wizard = ListingWizardService(self.db).get(wizard_id, company_id)
        draft = json.loads(wizard.draft_json or "{}")
        name = str(draft.get("product_name") or "").strip()
        if not name:
            raise BadRequestException("상품명이 있어야 AI 초안을 받을 수 있습니다 — 3단계에서 상품 정보를 먼저 채워 주세요.")
        options = tuple(
            (str(o.get("name") or ""), str(v))
            for o in (draft.get("options") or []) if isinstance(o, dict)
            for v in (o.get("values") or [])
        )
        notice: tuple[tuple[str, str], ...] = ()
        try:
            from app.domains.marketplace_listing.registration_assistant_service import RegistrationAssistantService
            from app.domains.marketplace_listing.registration_assistant_service import rows_from_saved_selection as saved_rows
            from app.domains.marketplace_listing.registration_assistant_service import saved_entry_for_account

            rows = RegistrationAssistantService(self.db)._onch_rows(wizard.product_candidate_id) if wizard.product_candidate_id else []
            if not rows:
                entries = json.loads(wizard.channel_selections_json or "[]")
                if entries:
                    rows = saved_rows(saved_entry_for_account(wizard.channel_selections_json, entries[0].get("marketplace_account_id")))
            notice = tuple((str(a), str(b)) for a, b in rows)
        except Exception:  # noqa: BLE001 — 고시는 있으면 도움이 되는 참고 자료일 뿐이다. 읽지 못해도 초안 요청은 막지 않는다.
            notice = ()
        return AiDraftInput(
            product_name=name, brand=draft.get("brand"), category=draft.get("category"), options=options,
            keywords=tuple(str(k) for k in (draft.get("keywords") or [])), description=draft.get("description"), notice_rows=notice,
        )

    # ------------------------------------------------------------
    # 제안
    # ------------------------------------------------------------

    def call_llm(
        self, company_id: int, user_id: int, *, target: str, system: str, user_prompt: str,
        ext_purpose: str = CallPurpose.AI_DRAFT, max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> tuple[LlmResult, float, dict]:
        """설정 확인 → 월 한도 확인 → 승인 센터 허용 확인 → 호출. 사용 기록은 검증 결과를 알게 된 호출자가 `record_usage`로 남긴다.

        반환: (응답, 추정 비용(원), {"limit": 월 한도}). 한도·허용·설정 문제는 호출 전에 예외로 막힌다."""

        s = self._settings(user_id, company_id)
        limit = int(s.get("monthly_limit_krw") or 0)
        model, in_price, out_price = s.get("model"), s.get("in_price"), s.get("out_price")
        api_key = self._read_key()
        if not (limit > 0 and model and in_price and out_price and api_key):
            raise BadRequestException("AI 기능을 쓰려면 모델·월 한도·단가·API 키를 먼저 저장해야 합니다.")

        # 최악 추정: 입력은 글자 수만큼을 토큰으로 보고(한국어는 글자당 1토큰 이하), 출력은 상한만큼 쓴다고 본다.
        worst = estimate_cost_krw(len(system) + len(user_prompt), max_output_tokens, in_price, out_price)
        used, _calls = self._month_usage()
        if used + worst > limit:
            raise BadRequestException(
                f"이번 달 AI 사용 한도를 넘을 수 있어 호출하지 않았습니다(사용 {used:,.0f}원 + 이번 최대 {worst:,.0f}원 > 한도 {limit:,}원)."
            )

        ticket = None
        if ExternalCallPolicyService.installed(self.db):
            ticket = ExternalCallPolicyService(self.db).authorize(
                company_id, ExternalSystem.LLM, CallKind.WRITE, ext_purpose,
                actor_user_id=user_id, target=target, method="POST",
            )
        policy = ExternalCallPolicyService(self.db) if ticket is not None else None
        client = self._client_factory(api_key, model)
        try:
            result = client.complete(system, user_prompt, max_output_tokens=max_output_tokens)
        except LlmError as exc:
            if policy:
                policy.finish(ticket, succeeded=False)
            raise ServiceUnavailableException(str(exc)) from None
        if policy:
            policy.finish(ticket, succeeded=True, http_status=200)
        cost = round(estimate_cost_krw(result.input_tokens, result.output_tokens, in_price, out_price), 2)
        return result, cost, {"limit": limit}

    def record_usage(self, company_id: int, user_id: int, ref: str, model: str, usage: dict) -> dict:
        """사용 기록을 남기고 이번 달 누계를 돌려준다(`ref`는 위저드 `WZ{id}` 또는 `URL`)."""

        self._record_usage(company_id, user_id, ref, model, usage)
        used_after, calls_after = self._month_usage()
        return {"month_used_krw": round(used_after, 2), "month_calls": calls_after}

    def propose(self, wizard_id: int, company_id: int, user_id: int) -> dict:
        data = self.build_input(wizard_id, company_id)
        user_prompt = build_user_prompt(data)
        result, cost, meta = self.call_llm(
            company_id, user_id, target=f"WZ{wizard_id}", system=SYSTEM_PROMPT, user_prompt=user_prompt,
        )
        proposal = validate_response(result.text, data)
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_krw": cost, "ok": bool(proposal.fields)}
        month = self.record_usage(company_id, user_id, f"WZ{wizard_id}", result.model, usage)
        return {
            **proposal.to_dict(),
            "current": {"product_name": data.product_name, "keywords": list(data.keywords), "description": data.description or ""},
            "sent_fields": ["product_name", "brand", "category", "options", "keywords", "current_description", "notice"],
            "usage": {**usage, "model": result.model, **month, "monthly_limit_krw": meta["limit"]},
        }

    # ------------------------------------------------------------
    # 적용
    # ------------------------------------------------------------

    def apply(self, wizard_id: int, company_id: int, user_id: int, accepted: dict, expected_version: int) -> dict:
        """사용자가 고른 필드만 위저드 초안에 저장한다. 서버가 같은 검증을 한 번 더 한다(화면이 바뀐 값을 보내도 못 통과한다)."""

        allowed = {k: accepted[k] for k in ("product_name", "keywords", "description") if k in accepted}
        if not allowed:
            raise BadRequestException("적용할 항목이 없습니다.")
        data = self.build_input(wizard_id, company_id)
        checked = validate_response(json.dumps({"product_name": allowed.get("product_name") or "x", "keywords": allowed.get("keywords") or [], "description": allowed.get("description") or ""}, ensure_ascii=False), data)
        applied: dict[str, Any] = {}
        refused: dict[str, str] = {}
        for key in allowed:
            if key in checked.fields:
                applied[key] = checked.fields[key]
            else:
                refused[key] = checked.rejected.get(key) or checked.rejected.get("all") or "검증을 통과하지 못했다"
        if "all" in checked.rejected:
            applied = {}
        if not applied:
            raise BadRequestException("검증을 통과한 항목이 없습니다: " + "; ".join(refused.values()))
        service = ListingWizardService(self.db)
        wizard = service.get(wizard_id, company_id)
        draft = json.loads(wizard.draft_json or "{}")
        updated = service.update_draft(wizard_id, company_id, WizardDraftUpdateRequest(
            expected_version=expected_version,
            product_name=applied.get("product_name", draft.get("product_name") or data.product_name),
            brand=draft.get("brand"), category=draft.get("category"),
            description=applied.get("description", draft.get("description")),
            options=draft.get("options") or [], sku_list=draft.get("sku_list") or [],
            certifications=draft.get("certifications") or [],
            keywords=applied.get("keywords", draft.get("keywords") or []),
        ))
        write_audit_log(
            self.db, user_id=user_id, company_id=company_id, action="AI_DRAFT_APPLIED", entity="listing_wizard",
            entity_id=str(wizard_id), description="AI 초안 적용: " + ", ".join(sorted(applied)),
        )
        self.db.commit()
        return {"applied": sorted(applied), "refused": refused, "version": updated.version}
