"""
=========================================================
Homez OS

File : tests/support/real_install_gate.py

실제 설치환경 진단 테스트의 명시적 opt-in 게이트
(tests/support/real_credential_gate.py와 같은 관례).

`python -m unittest discover -s tests`(기본 전체 회귀)는 실제 업무 자원 —
저장소의 `homez.db`, 설치본 `%LOCALAPPDATA%\\HOMEZ\\data\\homez.db`, 실제 백업 —
을 열거나 읽거나 해시하면 안 된다. 그래야 "이 회귀는 임시 DB와 합성 데이터만
썼다"고 사실대로 말할 수 있다.

"이 PC의 실제 설치 상태가 기대와 같은가"를 읽기 전용으로 확인하는 진단 테스트
(테스트 ID 목록은 docs/HOMEZ_V7_OPTION_LINK_APPLY_PLAN_20260921.md §2)는 이 게이트로
기본 회귀에서 제외하고, `HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1`을 명시적으로 설정했을
때만 실행한다 — 삭제나 무력화가 아니라 실행 시점을 사용자가 의식적으로 선택하게 한다.

이 모듈을 import하는 것만으로는 어떤 실제 파일도 열거나 조회하지 않는다
(환경변수만 읽는다). 진단 테스트가 자기 안에서 실제 경로를 읽는 것은 opt-in 이후뿐이다.
=========================================================
"""

from __future__ import annotations

import os
import unittest

REAL_INSTALL_DIAGNOSTICS_ENV_VAR = "HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS"

SKIP_REASON = (
    "실제 설치환경 진단 테스트 — 기본 전체 회귀에서 제외한다(실제 homez.db·백업을 열지 않는다). "
    f"명시적으로 실행하려면 {REAL_INSTALL_DIAGNOSTICS_ENV_VAR}=1 환경변수를 설정한 뒤 "
    "이 테스트만 따로 실행하라."
)


def real_install_diagnostics_enabled() -> bool:

    return os.environ.get(REAL_INSTALL_DIAGNOSTICS_ENV_VAR) == "1"


requires_real_install_diagnostics = unittest.skipUnless(
    real_install_diagnostics_enabled(), SKIP_REASON,
)


__all__ = [
    "REAL_INSTALL_DIAGNOSTICS_ENV_VAR",
    "SKIP_REASON",
    "real_install_diagnostics_enabled",
    "requires_real_install_diagnostics",
]
