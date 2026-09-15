"""
=========================================================
Homez OS

File : app/domains/recall_notice/provider.py

2026-09-15 전면 감사 후속(Phase 9I, HOMEZ_USER_OPERATION_SETTINGS.md
10-17) — 리콜/판매중지 공고 Provider 계약.

**실제 네트워크 Provider는 이 Phase에 만들지 않는다** — 정부·제조사·
판매채널 중 어느 데이터 소스를 공식으로 쓸지 아직 선정되지 않았다
(공통 규칙 15: "외부 계약이 아직 결정되지 않은 부분도 Adapter 계약 +
Fake Provider + 차단 게이트는 구현하고, '외부 검증 대기'로 명확히
분류한다"). `get_real_provider()`는 그 상태를 정직하게 표시하기 위해
항상 NotImplementedError를 던진다 — 조용히 아무 일도 안 하거나
가짜로 성공한 것처럼 보이지 않는다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from typing import Protocol


@dataclass(frozen=True)
class RecallNoticeRecord:

    product_identifier: str
    reason: str
    source: str
    manufacturer: str | None = None
    model: str | None = None
    announcement_date: datetime | None = None


class RecallNoticeProvider(Protocol):

    def fetch_notices(self) -> Iterable[RecallNoticeRecord]: ...


class FakeRecallNoticeProvider:
    """테스트 전용. `fail_after`를 주면 그 개수만큼 정상 발행한 뒤
    예외를 던진다 — "부분 실패"(이미 처리한 항목은 저장되고 이후만
    실패로 기록되는지)를 실제로 검증할 수 있게 한다."""

    def __init__(
        self, notices: Iterable[RecallNoticeRecord], *,
        fail_after: int | None = None,
        error: Exception | None = None,
    ):
        self._notices = list(notices)
        self._fail_after = fail_after
        self._error = error or RuntimeError("FAKE_PROVIDER_FAILURE")

    def fetch_notices(self) -> Iterable[RecallNoticeRecord]:

        for index, notice in enumerate(self._notices):
            if self._fail_after is not None and index >= self._fail_after:
                raise self._error
            yield notice


def get_real_provider() -> RecallNoticeProvider:
    """실제 Provider 선정 전까지는 항상 실패한다 — 호출부(스케줄러
    Job)가 이 상태를 "아직 준비되지 않음"으로 명확히 구분해 처리해야
    한다(조용히 넘어가지 않는다)."""

    raise NotImplementedError(
        "실제 리콜/판매중지 조회 Provider가 아직 선정되지 않았습니다 "
        "(외부 데이터 소스 결정 대기 — HOMEZ_DECISIONS.md 참고).",
    )


__all__ = [
    "RecallNoticeRecord",
    "RecallNoticeProvider",
    "FakeRecallNoticeProvider",
    "get_real_provider",
]
