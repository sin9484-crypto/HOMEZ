"""
=========================================================
Homez OS

File : tests/support/real_credential_gate.py

2026-09-15 전면 감사 후속(Phase 6, 테스트 격리 — IA-012 및 Phase 5
발견 사항). `python -m unittest discover -s tests`(기본 전체 회귀)
는 실제 Windows Credential Manager를 절대 건드리면 안 된다 — 그래야
"이 회귀는 임시 DB/Fake/InMemory만 썼다"고 정직하게 말할 수 있다.

실제 Windows Credential Manager를 실제로 읽고 쓰는 테스트(현재
`tests/test_windows_credential_store.py::WindowsCredentialStoreTestCase`
와 `tests/test_restore_helper.py::
test_full_subprocess_helper_cli_end_to_end`)는 기본 회귀에서
건너뛰고, `HOMEZ_RUN_REAL_CREDENTIAL_TESTS=1`을 명시적으로 설정했을
때만 실행한다 — 삭제나 무력화가 아니라, 실행 시점을 사용자가
의식적으로 선택하게 한다.
=========================================================
"""

from __future__ import annotations

import os
import unittest

REAL_CREDENTIAL_TESTS_ENV_VAR = "HOMEZ_RUN_REAL_CREDENTIAL_TESTS"

_SKIP_REASON = (
    "실제 Windows Credential Manager 통합 테스트 — 기본 전체 회귀에서 "
    f"제외한다(IA-012). 명시적으로 실행하려면 {REAL_CREDENTIAL_TESTS_ENV_VAR}=1 "
    "환경변수를 설정한 뒤 이 테스트만 따로 실행하라."
)


def real_credential_tests_enabled() -> bool:

    return os.environ.get(REAL_CREDENTIAL_TESTS_ENV_VAR) == "1"


requires_real_credential_manager = unittest.skipUnless(
    real_credential_tests_enabled(), _SKIP_REASON,
)


__all__ = [
    "REAL_CREDENTIAL_TESTS_ENV_VAR",
    "real_credential_tests_enabled",
    "requires_real_credential_manager",
]
