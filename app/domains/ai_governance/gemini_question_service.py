"""
=========================================================
Homez OS

File : app/domains/ai_governance/gemini_question_service.py

2026-11-23 — 사용자 지시 "구글 AI 스튜디오 클로드에 플러그인해라"(목적: 사진 만들기·글 질문·사용량 확인 모두).
클로드 연결 장치(`tools/claude_gemini_link`)가 쓰는 **제미나이 글 질문**. 같은 키·같은 월 크레딧을 이미지 생성과 함께 쓴다.

공식 문서(구글 AI 스튜디오 문서, 2026-11-23 확인)만 구현한다:
  POST https://generativelanguage.googleapis.com/v1beta/interactions   헤더 x-goog-api-key
  본문 {"model", "input": 글, "system_instruction": 글, "generation_config": {"max_output_tokens": n}, "store": false}
  응답 {"status": completed|..., "steps": [{"type": "model_output", "content": [{"type": "text", "text": ...}]}],
        "usage": {"total_input_tokens", "total_output_tokens", "total_thought_tokens", ...}}
  가격(유료, 100만 토큰당 달러): gemini-3.5-flash-lite 입력 0.30·출력 2.50(생각 포함), gemini-3.8-flash 0.75·3.75(2026-12-31까지).

지키는 것:
  - **월 크레딧 100%까지만**(사용자 결정 2026-11-22): 부르기 전에 최악 비용(글자 수를 토큰으로 보고 + 출력 상한의 두 배를 생각·출력으로)을
    최근 31일 합계에 더해 월 크레딧을 넘으면 부르지 않는다. 확인 → 호출 → 기록은 이미지와 같은 한 줄(`call_slot`)로 한 번에 하나.
  - 승인 센터 허용(제미나이·변경·`GEMINI_QUESTION`, 횟수형) 1회씩. 허용이 없으면 부르지 않고 허용 요청이 남는다.
  - 보내는 것은 사용자가 준 질문 글(과 선택 지시)뿐이다. `store: false`. 키는 응답·기록·오류 문구에 싣지 않는다.
  - 비용은 응답의 토큰 수로 계산하고, 토큰 수가 없으면 최악 비용으로 센다. 보내기 전 거절(400·401·402·403·404·429)만 0.
=========================================================
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from typing import Callable

import requests
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import CredentialStore
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.media_asset import gemini_image_client as gic
from app.domains.media_asset.image_ai_service import NOT_BILLED_HTTP
from app.domains.media_asset.image_ai_service import CreditLimitReached
from app.domains.media_asset.image_ai_service import ImageAiService
from app.domains.media_asset.image_ai_service import call_slot

ENDPOINT = gic.ENDPOINT
DEFAULT_TEXT_MODEL = "gemini-3.5-flash-lite"
# 100만 토큰당 달러(입력, 출력 — 출력에는 생각 토큰 포함). 2026-11-23 공식 가격표. 여기 없는 모델은 쓰지 않는다(값을 모르면 한도를 지킬 수 없다).
TEXT_PRICES_USD_PER_MTOK = {"gemini-3.5-flash-lite": (0.30, 2.50), "gemini-3.8-flash": (0.75, 3.75)}
MAX_OUTPUT_TOKENS = 2048
MAX_QUESTION_CHARS = 8000
MAX_INSTRUCTION_CHARS = 2000
TIMEOUT_SECONDS = 120.0


def worst_cost_usd(model: str, question: str, instruction: str) -> float:
    """최악 비용: 입력은 글자 하나를 토큰 하나 이상으로(한국어는 글자당 1토큰 이하) + 여유 500, 출력은 상한의 두 배(생각 포함)."""

    price_in, price_out = TEXT_PRICES_USD_PER_MTOK[model]
    input_tokens = len(question) + len(instruction) + 500
    return round((input_tokens * price_in + 2 * MAX_OUTPUT_TOKENS * price_out) / 1_000_000, 6)


def actual_cost_usd(model: str, usage: Any) -> float | None:
    """응답 토큰 수로 계산한 비용. 토큰 수를 읽을 수 없으면 None(그때는 최악 비용으로 센다)."""

    if not isinstance(usage, dict):
        return None
    numbers = [usage.get(k) for k in ("total_input_tokens", "total_output_tokens")]
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in numbers):
        return None
    thought = usage.get("total_thought_tokens")
    thought = thought if isinstance(thought, int) and not isinstance(thought, bool) and thought >= 0 else 0
    price_in, price_out = TEXT_PRICES_USD_PER_MTOK[model]
    return round((numbers[0] * price_in + (numbers[1] + thought) * price_out) / 1_000_000, 6)


def parse_text(body: Any) -> tuple[bool, str, str]:
    """(성공, 답 글, 실패 사유). 완료이고 글이 있어야 성공이다."""

    if not isinstance(body, dict):
        return False, "", "응답을 해석할 수 없다"
    status = body.get("status")
    if status != "completed":
        errors = body.get("errors") if isinstance(body.get("errors"), list) else []
        message = "; ".join(str(e.get("message") or "")[:200] for e in errors if isinstance(e, dict)) or f"상태 {status}"
        return False, "", message[:300]
    parts = []
    for step in body.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    answer = "".join(parts).strip()
    return (True, answer, "") if answer else (False, "", "완료됐지만 답 글이 없다(안전 차단 등)")


class GeminiQuestionService:

    def __init__(self, db: Session, credential_store: CredentialStore, *, transport: Any = None, now_factory: Callable[[], datetime] | None = None):
        self.db = db
        self.image_ai = ImageAiService(db, credential_store, now_factory=now_factory)
        self._transport = transport or gic.RequestsTransport()

    def ask(self, company_id: int, user_id: int, question: str, *, instruction: str | None = None, model: str = DEFAULT_TEXT_MODEL) -> dict:
        question = re.sub(r"[ \t]+", " ", question or "").strip() if isinstance(question, str) else ""
        instruction = (instruction or "").strip() if isinstance(instruction, str) else ""
        if not question:
            raise BadRequestException("질문 글이 비어 있습니다.")
        if len(question) > MAX_QUESTION_CHARS or len(instruction) > MAX_INSTRUCTION_CHARS:
            raise BadRequestException(f"질문은 {MAX_QUESTION_CHARS:,}자, 지시는 {MAX_INSTRUCTION_CHARS:,}자까지입니다.")
        if model not in TEXT_PRICES_USD_PER_MTOK:
            raise BadRequestException(f"가격을 아는 모델만 쓸 수 있습니다: {', '.join(TEXT_PRICES_USD_PER_MTOK)}")
        api_key = self.image_ai.read_key()
        credit = self.image_ai.credit_usd(user_id, company_id)
        if not api_key or credit <= 0:
            raise BadRequestException("제미나이 키와 월 크레딧을 먼저 저장해야 합니다(쉬운 보기 '더보기 · 설정' › 'AI 이미지 설정').")
        if not ExternalCallPolicyService.installed(self.db):
            raise ServiceUnavailableException("승인 센터(바깥 호출 허용) 표가 없어 부르지 않았습니다.")
        policy = ExternalCallPolicyService(self.db)
        worst = worst_cost_usd(model, question, instruction)
        body = {"model": model, "input": question, "generation_config": {"max_output_tokens": MAX_OUTPUT_TOKENS}, "store": False}
        if instruction:
            body["system_instruction"] = instruction

        with call_slot():
            try:
                self.image_ai.ensure_room(credit, worst)
            except CreditLimitReached as exc:
                raise BadRequestException(exc.detail) from None
            # 허용이 없으면 ExternalCallNotAllowed가 그대로 나가고(부르지 않음·기록 없음), 승인 센터에 허용 요청이 남는다.
            ticket = policy.authorize(company_id, ExternalSystem.GEMINI, CallKind.WRITE, CallPurpose.GEMINI_QUESTION,
                                      actor_user_id=user_id, target="ASK", method="POST")
            status_code, parsed = None, None
            try:
                response = self._transport.post(ENDPOINT, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                                                json_body=body, timeout=TIMEOUT_SECONDS)
                status_code = response.status_code
                try:
                    parsed = response.json()
                except ValueError:
                    parsed = None
            except requests.RequestException:
                parsed = None
            if status_code == 200:
                ok, answer, detail = parse_text(parsed)
            else:
                ok, answer = False, ""
                message = ""
                if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                    message = str(parsed["error"].get("message") or "")[:200]
                label = {402: "결제 필요 — 구글 AI 스튜디오 선결제 잔액 없음", 401: "API 키 오류", 403: "권한 없음", 429: "호출 한도 초과"}.get(status_code or 0)
                detail = (f"{label or '요청 실패'}({status_code}) {message}".strip() if status_code else "네트워크 오류")
            usage = parsed.get("usage") if isinstance(parsed, dict) else None
            if status_code in NOT_BILLED_HTTP:
                cost = 0.0
            else:
                measured = actual_cost_usd(model, usage) if status_code == 200 else None
                cost = measured if measured is not None else worst
            try:
                policy.finish(ticket, succeeded=ok, http_status=status_code)
            except Exception:  # noqa: BLE001 — 기록 실패가 결과를 바꾸지 않는다
                pass
            tokens_in = usage.get("total_input_tokens") if isinstance(usage, dict) else None
            tokens_out = usage.get("total_output_tokens") if isinstance(usage, dict) else None
            self.image_ai.record_text_usage(company_id, user_id, "ASK", model, ok, cost, tokens_in, tokens_out)

        used, _calls = self.image_ai.window_usage()
        result = {"ok": ok, "model": model, "cost_usd": cost, "window_used_usd": round(used, 4), "monthly_credit_usd": credit}
        if ok:
            result["answer"] = answer
        else:
            result["detail"] = detail
        return result


__all__ = ["GeminiQuestionService", "DEFAULT_TEXT_MODEL", "TEXT_PRICES_USD_PER_MTOK", "MAX_OUTPUT_TOKENS", "worst_cost_usd", "actual_cost_usd", "parse_text"]
