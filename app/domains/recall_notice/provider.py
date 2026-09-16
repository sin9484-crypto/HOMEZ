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

2026-09-16 갱신(개인 베타 잔여 작업, Phase 4) — 식품의약품안전처
(MFDS) "식품 회수·판매중지 정보" 공식 OpenAPI(공공데이터포털
#15074318, 서비스ID I0490)의 요청/응답 스펙을 문서로 완전히 확인
했고, 그 스펙대로 파싱하는 실제 Provider 클래스
(`mfds_provider.MfdsRecallNoticeProvider`)를 작성했다. **그래도
`get_real_provider()`는 여전히 이 클래스를 반환하지 않는다** — 인증
키 발급(활용신청)과 실제 최초 호출 검증은 사용자의 별도 명시적
승인 대상이다. 일반 공산품 대상 산업통상부 국가기술표준원(KATS)
리콜 API(공공데이터포털 #15116894)도 후보로 조사했으나 상세
요청/응답 스펙은 아직 완전히 확인되지 않았다 — 둘 다
`docs/HOMEZ_RECALL_DATA_SOURCE_RESEARCH_20260916.md` 참고.
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
    한다(조용히 넘어가지 않는다).

    2026-09-16 기준: 식품(MFDS I0490) 공식 API 스펙은 확인됐고 그에
    맞춰 파싱하는 `mfds_provider.MfdsRecallNoticeProvider`도 이미
    작성돼 있다. 그래도 여기서는 그 클래스를 반환하지 않는다 — 실제
    인증키 발급·최초 호출 검증은 사용자의 별도 명시적 실행 승인이
    필요하기 때문이다(이 함수를 고쳐 활성화하는 시점 = 그 승인이
    떨어진 시점)."""

    raise NotImplementedError(
        "실제 리콜/판매중지 조회 Provider가 아직 활성화되지 않았습니다 "
        "— 식품(MFDS I0490) API 스펙은 확인 및 Provider 코드 준비 "
        "완료 상태이나, 실제 인증키 발급·최초 호출 검증에는 사용자의 "
        "별도 명시적 실행 승인이 필요합니다 "
        "(docs/HOMEZ_RECALL_DATA_SOURCE_RESEARCH_20260916.md 참고).",
    )


__all__ = [
    "RecallNoticeRecord",
    "RecallNoticeProvider",
    "FakeRecallNoticeProvider",
    "get_real_provider",
]
