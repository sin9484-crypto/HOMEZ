"""
=========================================================
Homez OS

File : app/domains/user_settings/service.py

2026-08-14 Gate F-2 — 사용자 설정(로케일/가이드 진행 상태/UI·알림
환경설정) Service. 허용목록·크기 제한·금지 필드명을 여기서 강제한다
(라우터는 예외를 HTTP 상태 코드로만 변환한다).
=========================================================
"""

from __future__ import annotations

import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ValidationException
from app.domains.user_settings.model import UserSetting
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.schema import ALLOWED_SETTING_KEYS
from app.domains.user_settings.schema import FORBIDDEN_VALUE_FIELD_TOKENS
from app.domains.user_settings.schema import MAX_SETTING_VALUE_CHARS


def _normalized_token(field_name: str) -> str:

    return field_name.lower().replace("_", "").replace("-", "")


def _reject_forbidden_value_fields(value: object) -> None:
    """
    값(JSON) 안에 재귀적으로 등장하는 모든 dict key 이름을 검사한다.
    이 도메인에는 password/token/credential/recovery_code/
    invitation_code/secret/api_key류 필드가 애초에 저장될 수 없다는
    설계 원칙을 스키마 단계뿐 아니라 런타임에도 강제한다(방어적
    이중 장치 — 이 도메인이 다루는 값은 원래 opaque JSON blob이라
    구조적으로는 발생하기 어렵지만, 실수로 넓은 dict를 그대로 저장하는
    호출을 막는다).
    """

    if isinstance(value, dict):

        for key, nested in value.items():

            normalized = _normalized_token(str(key))

            if any(
                token in normalized for token in FORBIDDEN_VALUE_FIELD_TOKENS
            ):
                raise ValidationException(
                    f"설정 값에 허용되지 않는 필드명이 포함되어 있습니다: {key}",
                )

            _reject_forbidden_value_fields(nested)

    elif isinstance(value, list):

        for item in value:

            _reject_forbidden_value_fields(item)


def _serialize_value(value: object) -> str:

    _reject_forbidden_value_fields(value)

    serialized = json.dumps(value, ensure_ascii=False)

    if len(serialized) > MAX_SETTING_VALUE_CHARS:
        raise ValidationException(
            f"설정 값이 너무 큽니다(최대 {MAX_SETTING_VALUE_CHARS}자).",
        )

    return serialized


def _to_response_dict(row: UserSetting | None, setting_key: str) -> dict:

    if row is None:
        return {
            "key": setting_key,
            "value": None,
            "schema_version": 1,
            "version": 0,
            "updated_at": None,
        }

    return {
        "key": row.setting_key,
        "value": json.loads(row.setting_value),
        "schema_version": row.schema_version,
        "version": row.version,
        "updated_at": row.updated_at,
    }


class UserSettingService:

    def __init__(self, repository: UserSettingRepository):

        self.repository = repository

    def _require_allowed_key(self, setting_key: str) -> None:

        if setting_key not in ALLOWED_SETTING_KEYS:
            raise NotFoundException("지원하지 않는 설정 키입니다.")

    def get(self, user_id: int, company_id: int, setting_key: str) -> dict:

        self._require_allowed_key(setting_key)

        row = self.repository.get_for_user(user_id, company_id, setting_key)

        return _to_response_dict(row, setting_key)

    def put(
        self,
        user_id: int,
        company_id: int,
        setting_key: str,
        value: object,
        expected_version: int,
        schema_version: int,
    ) -> dict:

        self._require_allowed_key(setting_key)

        serialized = _serialize_value(value)

        db: Session = self.repository.db

        if expected_version == 0:

            existing = self.repository.get_for_user(
                user_id, company_id, setting_key,
            )

            if existing is not None:
                raise ConflictException(
                    "이미 저장된 값이 있습니다 — 최신 version으로 다시 "
                    "시도하세요.",
                )

            row = UserSetting(
                user_id=user_id,
                company_id=company_id,
                setting_key=setting_key,
                setting_value=serialized,
                schema_version=schema_version,
                version=1,
            )

            try:
                self.repository.create_no_commit(row)
                db.commit()

            except IntegrityError:
                # 동시에 같은 (user_id, setting_key)로 최초 생성을
                # 시도한 다른 요청이 UNIQUE 제약으로 먼저 이겼다.
                db.rollback()
                raise ConflictException(
                    "동시에 저장된 값이 있습니다 — 최신 version으로 "
                    "다시 시도하세요.",
                )

            return self.get(user_id, company_id, setting_key)

        rowcount = self.repository.update_value_conditional(
            user_id, company_id, setting_key, expected_version,
            serialized, schema_version,
        )

        if rowcount == 0:
            db.rollback()
            raise ConflictException(
                "버전이 일치하지 않습니다(다른 곳에서 먼저 저장했습니다). "
                "최신 값을 다시 조회한 뒤 재시도하세요.",
            )

        db.commit()

        return self.get(user_id, company_id, setting_key)


__all__ = ["UserSettingService"]
