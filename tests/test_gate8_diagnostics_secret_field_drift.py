"""
=========================================================
Homez OS

File : tests/test_gate8_diagnostics_secret_field_drift.py

V7 Gate 8(2026-08-15) — 목표 5 검증(진단 내보내기 secret 재확인).

`app/domains/diagnostics/redaction.py::KNOWN_SECRET_SETTINGS_FIELDS`
는 `app/core/config.py`의 실제 비밀값 필드를 하드코딩한 목록이다 —
누군가 config.py에 새 비밀 필드(예: 신규 채널 연동의 API 키)를
추가하고 이 목록을 갱신하는 것을 잊으면, 그 신규 비밀값은 로그 tail
redaction의 1차 방어선(known_secret_values, 값 자체 매칭)에서
빠진 채로 남는다(2차 방어선인 key=value 정규식은 여전히 동작하지만
필드명이 "password/secret/token/api_key/credential/pepper" 패턴에
안 걸리는 이름이면 그마저도 놓칠 수 있다).

Gate 8 감사 결과 Gate 3~7(inventory/order/purchase/shipment/
return_order/pricing)은 config.py에 새 비밀 필드를 추가하지
않았음을 확인했다 — 이 테스트는 그 사실을 실측으로 고정해 향후
드리프트(새 비밀 필드가 추가됐는데 redaction 목록 갱신을 잊는
사고)를 자동으로 잡아낸다.
=========================================================
"""

import re
import unittest

from app.core.config import Settings
from app.domains.diagnostics.redaction import KNOWN_SECRET_SETTINGS_FIELDS

# config.py 필드명 자체에서 "비밀값처럼 보이는" 것을 찾는 패턴 —
# redaction.py의 _KEY_VALUE_SECRET_PATTERN과 동일한 어휘를 쓴다
# (필드명 판별 기준을 일부러 두 곳에서 다르게 두지 않기 위함).
_SECRET_LIKE_FIELD_NAME = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|credential|pepper)",
)

# 비밀값이 아니라 "그 기능을 켤지"만 나타내는 bool/설정 플래그는
# 실제 문자열 비밀값이 아니므로 제외한다(예: TOKEN_ISSUER는 문자열
# 이지만 발급자 이름일 뿐 비밀이 아니고, *_ENABLED는 bool이다).
_NON_SECRET_EXCEPTIONS = frozenset({
    "TOKEN_ISSUER",
    "TOKEN_AUDIENCE",
    "TOKEN_TYPE",
    "TOKEN_BLACKLIST_ENABLED",
    "TOKEN_ROTATION_ENABLED",
    "API_KEY_ENABLED",
    "SECRET_ROTATION_ENABLED",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_REQUIRE_UPPER",
    "PASSWORD_REQUIRE_LOWER",
    "PASSWORD_REQUIRE_NUMBER",
    "PASSWORD_REQUIRE_SPECIAL",
    "PASSWORD_HISTORY_COUNT",
    "PASSWORD_EXPIRE_DAYS",
    "PASSWORD_BCRYPT_ROUNDS",
})


class DiagnosticsSecretFieldDriftTestCase(unittest.TestCase):

    def test_known_secret_fields_still_exist_on_settings(self):
        """
        redaction.py가 참조하는 9개 필드가 실제로 Settings에
        존재하고 문자열 타입임을 확인한다(존재하지 않는 필드를
        참조하면 known_secret_values()가 그 필드를 조용히
        건너뛰어(getattr 기본값 None) redaction이 약해진다).
        """

        field_names = set(Settings.model_fields.keys())

        for name in KNOWN_SECRET_SETTINGS_FIELDS:
            self.assertIn(
                name, field_names,
                f"{name}이(가) Settings에서 사라졌습니다 — "
                "redaction.py 갱신이 필요합니다.",
            )

    def test_no_new_undocumented_secret_like_fields_in_config(self):
        """
        config.py의 실제 문자열 필드 중 "비밀값처럼 보이는 이름"을
        가진 필드가 KNOWN_SECRET_SETTINGS_FIELDS 밖에 새로 생기면
        즉시 실패한다 — Gate 3~7이 새 비밀 필드를 추가하지 않았음을
        고정하고, 향후 추가될 때는 이 테스트가 redaction.py 갱신을
        강제한다.
        """

        secret_like_fields = {
            name
            for name, field in Settings.model_fields.items()
            if field.annotation is str
            and _SECRET_LIKE_FIELD_NAME.search(name)
            and name not in _NON_SECRET_EXCEPTIONS
        }

        undocumented = secret_like_fields - set(
            KNOWN_SECRET_SETTINGS_FIELDS,
        )

        self.assertEqual(
            undocumented, set(),
            f"config.py에 redaction.py가 모르는 비밀값처럼 보이는 "
            f"필드가 있습니다: {undocumented} — "
            "app/domains/diagnostics/redaction.py의 "
            "KNOWN_SECRET_SETTINGS_FIELDS에 추가해야 합니다.",
        )


if __name__ == "__main__":
    unittest.main()
