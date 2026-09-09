"""
=========================================================
Homez OS

File : app/domains/user_settings/schema.py

2026-08-14 Gate F-2 — 사용자 설정 API 계약 + 허용목록/크기 제한 상수.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# --------------------------------------------------
# 허용목록 — 이 4개 외 어떤 key도 저장할 수 없다(임의 key 저장 금지).
# --------------------------------------------------

SETTING_KEY_LOCALE = "locale"
SETTING_KEY_GUIDE_PROGRESS = "guide_progress"
SETTING_KEY_UI_PREFERENCES = "ui_preferences"
SETTING_KEY_NOTIFICATION_PREFERENCES = "notification_preferences"

ALLOWED_SETTING_KEYS = frozenset(
    {
        SETTING_KEY_LOCALE,
        SETTING_KEY_GUIDE_PROGRESS,
        SETTING_KEY_UI_PREFERENCES,
        SETTING_KEY_NOTIFICATION_PREFERENCES,
    },
)

# 직렬화된 JSON 문자열 길이 상한(문자 수) — 과도하게 큰 값 저장 방지.
# guide_progress(6개 과정 progress 스냅샷)가 가장 큰 값인데, 여유 있게
# 잡아도 8000자면 수십 배의 마진이 있다.
MAX_SETTING_VALUE_CHARS = 8000

# 값(JSON) 안에 이런 이름의 key가 있으면(대소문자/구분자 무관 부분
# 일치) 무조건 거부한다 — 이 도메인에는 인증 관련 정보를 절대 두지
# 않는다는 설계 원칙을 스키마 단계부터 강제한다.
FORBIDDEN_VALUE_FIELD_TOKENS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "credential",
        "recoverycode",
        "invitationcode",
        "secret",
        "apikey",
    },
)


# --------------------------------------------------
# Request / Response
# --------------------------------------------------

class UserSettingUpdateRequest(BaseModel):

    value: Any = Field(..., description="JSON 직렬화 가능한 설정 값")

    expected_version: int = Field(
        ...,
        ge=0,
        description="낙관적 동시성 fencing 토큰. 0=아직 존재하지 않는 값을 "
        "새로 생성. 그 외에는 현재 GET 응답의 version과 일치해야 한다.",
    )

    schema_version: int = Field(
        default=1,
        ge=1,
        description="이 값(payload)의 형태 버전(테이블 스키마 버전이 아님).",
    )


class UserSettingResponse(BaseModel):

    model_config = ConfigDict(from_attributes=False)

    key: str
    value: Any | None
    schema_version: int
    version: int
    updated_at: datetime | None


__all__ = [
    "SETTING_KEY_LOCALE",
    "SETTING_KEY_GUIDE_PROGRESS",
    "SETTING_KEY_UI_PREFERENCES",
    "SETTING_KEY_NOTIFICATION_PREFERENCES",
    "ALLOWED_SETTING_KEYS",
    "MAX_SETTING_VALUE_CHARS",
    "FORBIDDEN_VALUE_FIELD_TOKENS",
    "UserSettingUpdateRequest",
    "UserSettingResponse",
]
