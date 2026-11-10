"""
=========================================================
Homez OS

File : app/domains/media_asset/cloudflare_image_client.py

2026-11-10 — 사용자 결정 "cloudflare로 진행해라"(Gemini는 비용 때문에 보류 유지). Cloudflare Workers AI의 **배경 그리기** 호출 계약.
공식 문서(developers.cloudflare.com, 2026-11-10 확인)만 구현한다:

  POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell
  헤더 Authorization: Bearer {api_token}, Content-Type: application/json
  본문(입력 규격 schema-input.json) {"prompt": 1~2048자, "steps": 정수 ≤ 8(기본 4)} — 그 밖의 칸은 받지 않는다(additionalProperties false)
  응답(출력 규격 schema-output.json) {"image": Base64}, REST 봉투 {"result": ..., "success": bool, "errors": [...], "messages": [...]}
  토큰 권한: "Workers AI - Read"와 "Workers AI - Edit"(또는 'Workers AI' 토큰 서식).
  무료: 하루 10,000 뉴런(UTC 0시에 다시 채워짐). 다 쓰면 무료 플랜은 HTTP 429·오류 코드 3036으로 실패한다(요금이 나가지 않는다).
  정보 사용: 학습에 쓰지 않고, 보통 요청은 입력·출력을 남기지 않는다(공식 '데이터 사용' 문서).

이 모듈이 지키는 것:
  - **보내는 것은 장면 글 한 줄뿐이다** — 상품 사진·상품명·회사 정보는 보내지 않는다(상품은 HOMEZ가 PC 안에서 붙인다).
  - 응답이 이미지 없이 끝나거나 실패면 **실패로** 돌려준다(빈 성공 없음). 토큰은 오류 문구·기록에 싣지 않는다. 다른 주소로 넘겨주기(redirect)는 따라가지 않는다.
  - 응답 모양(result.image)은 공식 규격으로만 안다(실제로 받아 본 적 없음) — 해석할 수 없으면 실패로 다룬다. 결과 크기·형식은 문서에 없어 받은 뒤 검사한다.
=========================================================
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any
from typing import Protocol

import requests

MODEL = "@cf/black-forest-labs/flux-1-schnell"
ENDPOINT_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/" + MODEL
MAX_PROMPT_CHARS = 2048
DEFAULT_STEPS = 4
MAX_STEPS = 8
TIMEOUT_SECONDS = 90.0
DAILY_FREE_NEURONS = 10_000
FREE_LIMIT_ERROR_CODE = 3036       # "daily free allocation of 10,000 neurons" 다 씀(429)
CAPACITY_ERROR_CODE = 3040         # 잠시 처리 용량 초과(429)
_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,200}$")

FREE_LIMIT = "DAILY_FREE_LIMIT"
CAPACITY = "CAPACITY"


class CloudflareImageError(Exception):
    """호출 준비 문제. 메시지에는 토큰을 싣지 않는다."""


@dataclass(frozen=True)
class CloudflareImageResult:
    ok: bool
    image_bytes: bytes | None = None
    detail: str = ""
    http_status: int | None = None
    error_kind: str | None = None    # FREE_LIMIT / CAPACITY / None


class Transport(Protocol):
    def post(self, url: str, *, headers: dict, json_body: dict, timeout: float) -> requests.Response: ...


class RequestsTransport:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def post(self, url, *, headers, json_body, timeout):
        return self._session.post(url, headers=headers, json=json_body, timeout=timeout, allow_redirects=False)


def valid_account_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ACCOUNT_ID.match(value))


def valid_token(value: Any) -> bool:
    return isinstance(value, str) and bool(_TOKEN.match(value))


def build_body(prompt: str, steps: int = DEFAULT_STEPS) -> dict:
    if not isinstance(prompt, str) or not prompt.strip():
        raise CloudflareImageError("장면 글이 비어 있다")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise CloudflareImageError(f"장면 글은 {MAX_PROMPT_CHARS}자까지다")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS:
        raise CloudflareImageError(f"단계 수는 1~{MAX_STEPS}이어야 한다")
    return {"prompt": prompt, "steps": steps}


def _error_codes(body: Any) -> list[int]:
    codes: list[int] = []
    if isinstance(body, dict):
        for err in body.get("errors") or []:
            if isinstance(err, dict) and isinstance(err.get("code"), int) and not isinstance(err.get("code"), bool):
                codes.append(err["code"])
    return codes


def _error_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    return "; ".join(str(e.get("message") or "")[:200] for e in body.get("errors") or [] if isinstance(e, dict))[:300]


def parse_response(body: Any) -> CloudflareImageResult:
    """HTTP 200 응답 본문 → 결과. success가 참이고 result.image가 Base64로 풀려야만 성공이다."""

    if not isinstance(body, dict):
        return CloudflareImageResult(False, detail="응답을 해석할 수 없다", http_status=200)
    if body.get("success") is not True:
        return CloudflareImageResult(False, detail=_error_text(body) or "실패 응답", http_status=200)
    result = body.get("result")
    image = result.get("image") if isinstance(result, dict) else None
    if not isinstance(image, str) or not image:
        return CloudflareImageResult(False, detail="성공 응답이지만 이미지가 없다", http_status=200)
    try:
        data = base64.b64decode(image, validate=True)
    except (ValueError, TypeError):
        return CloudflareImageResult(False, detail="이미지 데이터를 해석할 수 없다", http_status=200)
    if not data:
        return CloudflareImageResult(False, detail="빈 이미지", http_status=200)
    return CloudflareImageResult(True, data, "", 200)


class CloudflareImageClient:

    def __init__(self, account_id: str, api_token: str, *, transport: Transport | None = None, timeout: float = TIMEOUT_SECONDS):
        if not valid_account_id(account_id):
            raise CloudflareImageError("계정 ID 형식이 올바르지 않다(영문 소문자·숫자 32자)")
        if not valid_token(api_token):
            raise CloudflareImageError("API 토큰 형식이 올바르지 않다")
        self._url = ENDPOINT_TEMPLATE.format(account_id=account_id)
        self._token = api_token
        self._transport = transport or RequestsTransport()
        self._timeout = timeout

    def generate(self, prompt: str, *, steps: int = DEFAULT_STEPS) -> CloudflareImageResult:
        body = build_body(prompt, steps)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            response = self._transport.post(self._url, headers=headers, json_body=body, timeout=self._timeout)
        except requests.Timeout:
            return CloudflareImageResult(False, detail="응답 시간이 초과됐다")
        except requests.RequestException:
            return CloudflareImageResult(False, detail="네트워크 오류")
        status = response.status_code
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if status == 200:
            return parse_response(parsed)
        codes = _error_codes(parsed)
        if status == 429 and FREE_LIMIT_ERROR_CODE in codes:
            return CloudflareImageResult(False, detail="오늘 무료 사용량(10,000 뉴런)을 다 썼다 — UTC 0시(한국 오전 9시)에 다시 채워진다", http_status=429, error_kind=FREE_LIMIT)
        if status == 429 and CAPACITY_ERROR_CODE in codes:
            return CloudflareImageResult(False, detail="Cloudflare 처리 용량이 잠시 넘쳤다 — 잠시 뒤 다시 시도", http_status=429, error_kind=CAPACITY)
        label = {400: "요청 오류", 401: "API 토큰 오류", 403: "권한 없음(토큰 권한·계정 확인)", 404: "모델 또는 계정을 찾을 수 없음", 413: "요청이 너무 큼",
                 429: "호출 한도 초과"}.get(status, "서버 오류" if status >= 500 else "요청 실패")
        message = _error_text(parsed)
        return CloudflareImageResult(False, detail=f"{label}({status}) {message}".strip()[:300], http_status=status)


__all__ = [
    "MODEL", "ENDPOINT_TEMPLATE", "MAX_PROMPT_CHARS", "DEFAULT_STEPS", "MAX_STEPS", "DAILY_FREE_NEURONS", "FREE_LIMIT", "CAPACITY",
    "CloudflareImageError", "CloudflareImageResult", "CloudflareImageClient", "RequestsTransport", "valid_account_id", "valid_token",
    "build_body", "parse_response",
]
