"""
=========================================================
Homez OS

File : app/domains/recall_notice/mfds_provider.py

2026-09-16 전면 감사 후속(Phase 4, HOMEZ_USER_OPERATION_SETTINGS.md
10-17) — 식품의약품안전처(MFDS) "식품의 회수 및 판매중지 정보"
공식 OpenAPI(공공데이터포털 #15074318, 식품안전나라 서비스ID
I0490)의 실제 Provider. 조사 결과와 응답 필드 전체 목록은
`docs/HOMEZ_RECALL_DATA_SOURCE_RESEARCH_20260916.md` 참고.

**이 세션은 이 클래스를 단 한 번도 실제로 호출하지 않는다.**
인증키(서비스 신청) 발급 자체를 신청하지 않았다 — 공통 규칙
"실제 API 키 등록이나 실제 호출은 수행하지 않는다"를 그대로
지킨다. `fetch_notices()`의 요청 URL 구성·응답 파싱 로직만
공식 문서에 실제로 적힌 필드명 그대로 작성했고, 파싱 로직은
그 스펙과 동일한 구조의 합성(가짜) JSON 텍스트로만 검증한다
(`tests/test_recall_notice_mfds_provider.py`) — 실제 서버 응답을
한 번도 본 적이 없으므로, 실제 필드가 문서와 다를 가능성은 여전히
남아 있다(실제 인증키로 최초 1회 호출해 재검증하기 전까지는
`app.domains.recall_notice.provider.get_real_provider()`가 여전히
이 클래스를 반환하지 않는다).

요청 URL 패턴(공식 문서 확인):
    http://openapi.foodsafetykorea.go.kr/api/{인증키}/I0490/{json|xml}/{시작}/{종료}

응답 필드(공식 문서에 명시된 필드명 그대로):
    PRDTNM(제품명), RTRVLPRVNS(회수사유), BSSHNM(제조업체명),
    BRCDNO(바코드번호), MNFDT(제조일자), CRET_DTM(등록일),
    PRDLST_CD(품목코드), RTRVLDSUSE_SEQ(회수·판매중지 일련번호),
    RTRVL_GRDCD_NM(회수등급), PRDLST_CD_NM(품목유형).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

from app.domains.recall_notice.provider import RecallNoticeRecord

MFDS_BASE_URL = "http://openapi.foodsafetykorea.go.kr/api"
MFDS_SERVICE_ID = "I0490"
MFDS_SOURCE_LABEL = "식품의약품안전처(MFDS) 식품 회수·판매중지 정보(I0490)"


class MfdsResponseFormatError(Exception):
    """MFDS 응답이 예상한 JSON 구조와 다를 때 — 조용히 빈 결과로
    넘기지 않고 명확히 실패시킨다(온채널 Adapter와 동일한 원칙)."""


def _parse_mfds_date(raw: str | None) -> datetime | None:
    """공식 문서는 등록일(CRET_DTM) 형식을 명시하지 않았다(YYYYMMDD
    추정 — 다른 식품안전나라 API들의 관례). 확신할 수 없는 형식은
    추측으로 파싱하지 않고 None을 반환한다(파싱 실패가 전체 레코드를
    버리게 만들지 않는다 — 날짜만 비어 있는 채로 나머지 필드는
    그대로 쓴다)."""

    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_mfds_response(body: dict) -> list[RecallNoticeRecord]:
    """공식 문서가 명시한 응답 필드명(PRDTNM/RTRVLPRVNS/BSSHNM/...)
    그대로 파싱한다. 최상위 키 이름(I0490 등)은 문서에 정확히
    명시되지 않아 여러 후보를 시도한다 — 어느 것도 없으면 명확히
    실패시킨다(빈 리스트로 조용히 넘기지 않는다)."""

    if not isinstance(body, dict):
        raise MfdsResponseFormatError("MFDS 응답이 JSON 객체가 아닙니다.")

    if isinstance(body.get(MFDS_SERVICE_ID), dict):
        container = body[MFDS_SERVICE_ID]
    elif isinstance(body.get("response"), dict):
        container = body["response"]
    elif "row" in body:
        # 최상위에 바로 row가 오는 경우(공식 문서에 정확히 명시되진
        # 않았으나 다른 식품안전나라 API들에서 관측되는 변형) 도 받아
        # 준다 — 그 외에는 컨테이너를 찾지 못한 것으로 명확히 실패시킨다.
        container = body
    else:
        raise MfdsResponseFormatError(
            "MFDS 응답에서 서비스 컨테이너를 찾을 수 없습니다.",
        )

    rows = container.get("row")
    if rows is None:
        # 결과 0건도 정상 응답이다(RESULT.CODE=INFO-200 등) — 문서에
        # "row" 자체가 없을 수 있다고 명시돼 있진 않지만, 0건 조회를
        # 예외로 취급하면 매일 확인 Job이 "공고 없음"인 정상적인 날에도
        # 실패로 기록된다. 빈 리스트로 처리한다.
        return []
    if not isinstance(rows, list):
        raise MfdsResponseFormatError("MFDS 응답의 row가 배열이 아닙니다.")

    records: list[RecallNoticeRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_identifier = row.get("PRDLST_CD") or row.get("PRDTNM")
        reason = row.get("RTRVLPRVNS")
        if not product_identifier or not reason:
            # 필수로 취급하는 두 필드(품목 식별자·회수사유) 중 하나라도
            # 없으면 이 행은 건너뛴다 — 빈 문자열을 그대로 DB에 넣지
            # 않는다(추측으로 채우지 않는다는 이 저장소 전역 원칙).
            continue
        records.append(RecallNoticeRecord(
            product_identifier=str(product_identifier),
            reason=str(reason),
            source=MFDS_SOURCE_LABEL,
            manufacturer=row.get("BSSHNM") or None,
            model=row.get("PRDLST_CD_NM") or None,
            announcement_date=_parse_mfds_date(row.get("CRET_DTM")),
        ))
    return records


class MfdsRecallNoticeProvider:
    """실제 MFDS OpenAPI Provider. **이 클래스는 이 세션에서 한 번도
    인스턴스화·호출되지 않는다** — `get_real_provider()`가 여전히
    이 클래스를 반환하지 않도록 막아 둔다(실제 인증키 발급·최초
    호출 검증은 별도 사용자 승인 대상)."""

    def __init__(
        self, api_key: str, *, base_url: str = MFDS_BASE_URL,
        http_get: Callable | None = None, timeout_seconds: int = 10,
        page_size: int = 100,
    ):

        if not api_key:
            raise ValueError("MFDS 인증키(api_key)가 필요합니다.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._page_size = page_size
        if http_get is not None:
            self._http_get = http_get
        else:
            import requests
            self._http_get = requests.get

    def _fetch_page(self, start: int, end: int) -> dict:

        url = (
            f"{self._base_url}/{self._api_key}/{MFDS_SERVICE_ID}/json/"
            f"{start}/{end}"
        )
        response = self._http_get(url, timeout=self._timeout_seconds)
        try:
            body = response.json()
        except ValueError as exc:
            raise MfdsResponseFormatError(
                f"MFDS 응답이 JSON이 아닙니다(HTTP {getattr(response, 'status_code', '?')}).",
            ) from exc
        return body

    def fetch_notices(self) -> Iterable[RecallNoticeRecord]:
        """페이지 단위로 순차 조회하며 하나씩 내보낸다 — 도중에 실패
        해도 이미 내보낸(=이미 DB에 반영된) 레코드는 그대로 남는다
        (RecallNoticeService.run_daily_check()의 부분 실패 계약과
        일치)."""

        start = 1
        while True:
            end = start + self._page_size - 1
            body = self._fetch_page(start, end)
            page_records = parse_mfds_response(body)
            if not page_records:
                return
            for record in page_records:
                yield record
            if len(page_records) < self._page_size:
                return
            start += self._page_size


__all__ = [
    "MFDS_BASE_URL",
    "MFDS_SERVICE_ID",
    "MFDS_SOURCE_LABEL",
    "MfdsResponseFormatError",
    "parse_mfds_response",
    "MfdsRecallNoticeProvider",
]
