"""
=========================================================
Homez OS

File : tools/packaged_guard_hook.py

검증용 PyInstaller 빌드 전용 런타임 훅(2026-10-06, 68차). 개발용 회귀 가드
(`tests/support/regression_guard/sitecustomize.py`)는 `PYTHONPATH`로 `sitecustomize`를 자동 import하는
방식인데, PyInstaller로 만든 exe는 `site`를 import하지 않고(`sys.flags.no_site=1`) `PYTHON*` 환경변수를
무시(`sys.flags.ignore_environment=1`)하므로 **같은 환경변수로 실행해도 가드가 활성화되지 않는다**
(68차 탐침 앱으로 실측). 이 훅은 환경변수 `HOMEZ_PACKAGED_GUARD_FILE`이 가드 파일을 가리킬 때만 그
파일을 앱 시작 직후(앱 코드보다 먼저) 실행해 같은 감사 훅을 설치한다.

- 환경변수가 없으면 아무 일도 하지 않는다(릴리스 빌드에는 이 훅을 넣지 않는다 — 검증용 spec 전용).
- 가드가 설치되지 않았는데도 환경변수가 지정돼 있으면 **시작을 막는다**(보호 없이 계속 실행하지 않는다).
=========================================================
"""

import importlib.util
import os
import sys

_path = os.environ.get("HOMEZ_PACKAGED_GUARD_FILE", "").strip()
if _path:
    if not os.path.isfile(_path):
        sys.stderr.write("HOMEZ_PACKAGED_GUARD_FILE이 가리키는 가드 파일이 없어 시작을 중단합니다.\n")
        raise SystemExit(97)
    _spec = importlib.util.spec_from_file_location("homez_packaged_regression_guard", _path)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    # 설치 확인: NO_INSTALL이 꺼져 있고 보호 대상이 실제로 구성돼야 한다(빈 가드로 "보호 중"인 척하지 않는다).
    _protected = len(getattr(_module, "_P_FILES", ())) + len(getattr(_module, "_P_DIRS", ()))
    if os.environ.get("HOMEZ_GUARD_NO_INSTALL") == "1" or _protected == 0:
        sys.stderr.write("가드가 설치되지 않았거나 보호 대상이 없어 시작을 중단합니다.\n")
        raise SystemExit(98)
