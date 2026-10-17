"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/llm_client.py

2026-10-18 — 등록 도우미 AI 초안(Phase B)용 Claude(Anthropic Messages API) 클라이언트. 새 SDK 없이 `requests`로 HTTPS만 쓴다.

규칙:
  - 전송 계층(`LlmTransport`)을 주입할 수 있다 — 테스트는 가짜 전송만 쓰고 바깥 호출은 0이다.
  - API 키는 호출하는 쪽이 Windows 자격증명 저장소에서 읽어 넘긴다. 이 파일은 키를 저장·출력·로그에 남기지 않는다(오류 문구에도 넣지 않는다).
  - 응답에서 글(text)과 사용량(입력·출력 토큰 수)만 꺼낸다. 형식이 다르면 `LlmError`다(추측해서 채우지 않는다).
  - 어떤 허용·한도 확인도 여기서 하지 않는다 — 호출 직전 확인은 `ai_draft_service`가 한다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Protocol

import requests

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_OUTPUT_TOKENS_CAP = 4000


class LlmError(Exception):
    """AI 서비스 호출이 실패했거나 응답을 읽을 수 없다. 메시지에는 키·요청 본문을 넣지 않는다."""


@dataclass(frozen=True)
class LlmResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LlmTransport(Protocol):
    def post(self, url: str, *, headers: dict[str, str], json_body: dict[str, Any], timeout: float) -> Any: ...


class RequestsLlmTransport:
    def post(self, url: str, *, headers: dict[str, str], json_body: dict[str, Any], timeout: float):
        return requests.post(url, headers=headers, json=json_body, timeout=timeout)


class AnthropicClient:

    def __init__(self, api_key: str, model: str, *, transport: LlmTransport | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._api_key = api_key
        self.model = model
        self._transport = transport or RequestsLlmTransport()
        self._timeout = timeout

    def complete(self, system: str, user: str, *, max_output_tokens: int = 1500) -> LlmResult:
        if not self._api_key or not self.model:
            raise LlmError("AI 서비스 키 또는 모델이 설정되지 않았다")
        max_output_tokens = max(1, min(int(max_output_tokens), MAX_OUTPUT_TOKENS_CAP))
        body = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        try:
            response = self._transport.post(API_URL, headers=headers, json_body=body, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 — 예외 종류만 남기고 내용(주소·키가 섞일 수 있음)은 버린다
            raise LlmError(f"AI 서비스 요청 실패({type(exc).__name__})") from None
        status = getattr(response, "status_code", None)
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            data = None
        if status != 200 or not isinstance(data, dict):
            detail = ""
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                detail = str(data["error"].get("type") or "")[:60]
            raise LlmError(f"AI 서비스가 오류를 돌려줬다(HTTP {status}{', ' + detail if detail else ''})")
        text = _first_text(data.get("content"))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        if text is None or not isinstance(input_tokens, int) or not isinstance(output_tokens, int) or isinstance(input_tokens, bool):
            raise LlmError("AI 서비스 응답의 모양이 예상과 다르다(글 또는 사용량 없음)")
        return LlmResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens, model=str(data.get("model") or self.model)[:80])


def _first_text(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    parts = [block.get("text") for block in content if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)]
    return "".join(parts) if parts else None
