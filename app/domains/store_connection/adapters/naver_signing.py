"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/naver_signing.py

Gate 3(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5, CTO 2차 지적 반영) —
네이버 커머스API센터(commerce-api-naver) OAuth2 Client Credentials
인증에 필요한 client_secret_sign 계산.

**확인 상태(중요, 반드시 그대로 유지)**: apicenter.commerce.naver.com과
api.commerce.naver.com 양쪽 모두 이번 세션(2026-08-01)에서도 WebFetch로
직접 접근이 계속 실패했다("Claude Code is unable to fetch"). 이 파일의
알고리즘은 **1차 문서를 직접 fetch해서 확인한 것이 아니다** — 사용자가
"공식 문서 2.83.0 기준"으로 제시한 스펙과, 네이버가 직접 운영하는
공식 GitHub Discussion 저장소(github.com/commerce-api-naver/commerce-api
— 네이버 커머스API 개발자관계팀이 직접 운영하는 채널)를 포함한 다수의
독립적인 2차 소스가 전부 동일하게 수렴하는 내용을 그대로 구현한
것이다. 즉 "1차 문서 확인 완료"가 아니라 "2차 소스 교차확인 + 사용자
제공 스펙"이다 — 이 차이를 다음 재검토에서도 계속 명시한다.

알고리즘(교차확인된 내용):
1. timestamp = 현재 UTC epoch millisecond를 10진 문자열로.
2. password = f"{client_id}_{timestamp}" (UTF-8 bytes)
3. client_secret_sign = Base64(bcrypt(password, salt=client_secret))
   — client_secret 자체가 bcrypt salt 형식으로 발급된다고 알려져
   있다(네이버가 발급하는 client_secret은 `$2a$...` 형태).
4. timestamp는 발급 시점 기준 짧은 유효시간(약 5분)을 가진다고
   알려져 있다 — 이 파일은 호출 시점에 항상 새로 계산한다.

bcrypt는 이 프로젝트 venv에 이미 설치되어 있다(passlib 종속성,
2026-08-01 확인 — 신규 패키지 설치가 아니다). bcrypt가 없는 환경에서
이 모듈을 import하면 무엇이 없는지 명확한 오류로 즉시 fail-closed
한다(조용히 넘어가지 않는다).

Secret 원문(client_secret)은 이 함수 호출 동안 salt로만 잠깐
쓰이고, 반환값(client_secret_sign, 이미 해시+Base64된 값)에는
원문이 그대로 들어가지 않는다 — 다만 client_secret_sign 자체는
"이번 요청에 쓰라고 서버가 검증할 값"이므로 실제 HTTP 요청 바디에는
당연히 포함된다(그게 이 프로토콜의 목적이다). 로그에는 절대 남기지
않는다(호출자 책임 — naver_production.py 참고).
=========================================================
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

try:
    import bcrypt
except ImportError as exc:  # pragma: no cover - 이 환경에는 이미 설치돼 있음
    raise ImportError(
        "naver_signing.py는 bcrypt 패키지가 필요합니다. 이 프로젝트의 "
        "venv에는 이미 설치되어 있어야 합니다(passlib 종속성) — 만약 "
        "설치되어 있지 않다면, CLAUDE.md의 '신규 패키지 설치 금지' "
        "원칙에 따라 여기서 자동 설치하지 않고 즉시 차단합니다. 사용자가 "
        "직접 설치 여부를 결정해야 합니다.",
    ) from exc

TOKEN_URL = "https://api.commerce.naver.com/external/v1/oauth2/token"

# 교차확인된 값 — 1차 문서로 재확인 전까지 잔존 위험으로 문서화.
TIMESTAMP_VALIDITY_SECONDS = 5 * 60
TOKEN_VALIDITY_SECONDS = 3 * 60 * 60


def current_timestamp_ms(now: datetime | None = None) -> str:

    now = now or datetime.now(timezone.utc)

    return str(int(now.timestamp() * 1000))


def compute_client_secret_sign(
    client_id: str, client_secret: str, timestamp_ms: str,
) -> str:
    """
    client_secret은 bcrypt salt 형식(예: "$2a$04$...")이어야 한다 —
    형식이 아니면 bcrypt.hashpw가 ValueError를 던진다(이 함수가
    그 오류를 삼키지 않고 그대로 전파한다 — fail-closed, 잘못된
    형식의 client_secret을 조용히 성공한 것처럼 처리하지 않는다).
    """

    password = f"{client_id}_{timestamp_ms}".encode("utf-8")
    salt = client_secret.encode("utf-8")

    hashed = bcrypt.hashpw(password, salt)

    return base64.b64encode(hashed).decode("ascii")


__all__ = [
    "TOKEN_URL",
    "TIMESTAMP_VALIDITY_SECONDS",
    "TOKEN_VALIDITY_SECONDS",
    "current_timestamp_ms",
    "compute_client_secret_sign",
]
