"""
=========================================================
Homez OS

File : tests/ai_governance_test_helpers.py

Audit(2026-08-21, CTO 후속 지시) — 12개 도메인 각각의 실제 Service
진입점에 `require_active_capability()`가 실제로 연결됐는지 증명하는
테스트가 반복적으로 필요로 하는 "이 capability_code를 일시적으로
비활성화한다" 패턴을 한 곳에 모은다(각 테스트 파일이 카탈로그
내부 dict를 직접 조작하는 코드를 반복하지 않게 한다).
=========================================================
"""

from contextlib import contextmanager
from dataclasses import replace


@contextmanager
def deactivated_capability(capability_code: str):
    """이 블록 안에서만 `capability_code`를 `active=False`로 바꾼다
    — 블록을 벗어나면(예외 발생 포함) 항상 원래 상태로 복원한다."""

    import app.domains.ai_governance.service as svc_module

    original = svc_module._CATALOG_BY_CODE[capability_code]
    svc_module._CATALOG_BY_CODE[capability_code] = replace(
        original, active=False,
    )
    try:
        yield
    finally:
        svc_module._CATALOG_BY_CODE[capability_code] = original


@contextmanager
def unregistered_capability(capability_code: str):
    """이 블록 안에서만 `capability_code`를 카탈로그에서 완전히
    제거한다 — `require_active_capability()`가 UnknownCapabilityError
    를 던지는 경로(비활성이 아니라 아예 등록되지 않은 경우)를
    검증할 때 쓴다."""

    import app.domains.ai_governance.service as svc_module

    original = svc_module._CATALOG_BY_CODE.pop(capability_code)
    try:
        yield
    finally:
        svc_module._CATALOG_BY_CODE[capability_code] = original


__all__ = ["deactivated_capability", "unregistered_capability"]
