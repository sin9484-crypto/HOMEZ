"""
=========================================================
Homez OS

File : app/domains/trend_discovery/adapter.py

V3 Trend Discovery AI — Adapter 계약

FixtureTrendAdapter는 외부 네트워크를 호출하지 않는 테스트/시연 전용
Adapter다. NaverDataLabTrendAdapter(2026-09-06 최초 구현, 2026-09-07
NAVER API HUB 실 스펙으로 재작성)는 네이버 "검색어 트렌드" 공식
API(NAVER Cloud Platform이 운영하는 API HUB — 구 developers.naver.com
오픈API는 2026-07-31부로 신규 발급이 종료돼 API HUB로 이관됨)를
실제로 호출하는 실 Adapter다. Adapter를 교체해도 TrendDiscoveryService
의 점수 로직은 바뀌지 않는다.

2026-09-07 재작성 근거: 구현 당시(2026-09-06) 사용한 엔드포인트
(openapi.naver.com, X-Naver-Client-Id/Secret 헤더)는 구버전 개발자
센터 스펙이었다 — 사용자가 실제로 발급받은 키는 NAVER API HUB
방식(네이버클라우드플랫폼 콘솔에서 발급, X-NCP-APIGW-API-KEY-ID/
X-NCP-APIGW-API-KEY 헤더)이라 그대로는 호출이 실패했을 것이다.
공식 문서(api.ncloud-docs.com/docs/naver-api-hub-search-trend)
기준으로 엔드포인트·헤더·요청 경로를 전면 수정했다. 응답 JSON
구조(results[].data[].{period,ratio})는 구버전과 동일하게 유지된다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Callable

from app.core.api_usage_tracker import ApiUsageTracker
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability


@dataclass(frozen=True)
class TrendSignal:
    """공식 API 형식을 흉내낸 Adapter 출력 계약."""

    keyword: str
    market: str
    # 최근 기간 순으로 정렬된 관심도/검색량 지표(오래된 것 → 최신 순)
    search_volume_series: list[float]
    price_series: list[float] = field(default_factory=list)
    competitor_count: int = 0
    observed_at: str = ""  # ISO 8601 문자열
    source: str = "fixture"
    is_banned_category: bool = False


class TrendSourceAdapter:
    """실제 Adapter가 구현해야 하는 최소 계약."""

    def fetch(self, keyword: str) -> TrendSignal | None:

        raise NotImplementedError


class FixtureTrendAdapter(TrendSourceAdapter):
    """
    외부 네트워크를 사용하지 않는 Fake/Fixture Adapter.

    fixtures: {keyword: TrendSignal} 형태의 고정 데이터.
    """

    def __init__(
        self,
        fixtures: dict[str, TrendSignal],
    ):

        self._fixtures = fixtures

    def fetch(
        self,
        keyword: str,
    ) -> TrendSignal | None:
        """
        Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        강제(PRODUCT_DISCOVERY). 이 Domain도 현재 실 HTTP 진입점이
        없다(재확인 완료) — 향후 연결 시점부터 강제되도록 미리
        연결한다.
        """

        require_active_capability(CapabilityCode.PRODUCT_DISCOVERY)

        return self._fixtures.get(keyword)


class NaverDataLabTrendAdapter(TrendSourceAdapter):
    """
    네이버 데이터랩 "통합 검색어 트렌드" 공식 Open API 기반 실
    Adapter — https://openapi.naver.com/v1/datalab/search.

    developers.naver.com에서 애플리케이션 등록 후 발급받은 Client
    ID/Secret이 필요하다(계정 가입·키 발급은 사용자가 직접 해야
    한다). 자격증명은 이 클래스가 직접 들고 있지 않고
    CredentialStore(Windows Credential Manager, store_connection과
    동일한 패턴)에서 매 호출마다 읽는다 — 평문 보관 금지 원칙을
    그대로 따른다.

    네트워크 실패·자격증명 없음·응답 형식 이상 등 어떤 이유로든
    실패하면 예외를 던지지 않고 None을 반환한다(fail-closed —
    실패했다고 가짜 데이터를 만들어내지 않는다. 호출자는 기존
    FixtureTrendAdapter와 동일하게 None을 "이 키워드에 대한 신호
    없음"으로 처리한다).
    """

    # NAVER API HUB(네이버클라우드플랫폼) 기준 — 구 openapi.naver.com/
    # v1/datalab/search 아님(2026-09-07 확인, api.ncloud-docs.com/
    # docs/naver-api-hub-search-trend).
    ENDPOINT = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
    REQUEST_TIMEOUT_SECONDS = 10
    DEFAULT_LOOKBACK_DAYS = 90
    # Windows Credential Manager target name — 회사별이 아니라 설치
    # 전체가 공유하는 시스템 레벨 자격증명이다. "하나의 API 키로
    # 검색어 트렌드·이미지 검색을 모두 호출할 수 있다"(API HUB 공식
    # 안내)는 사실에 맞춰 NaverImageSearchProvider와 이 이름을
    # 공유한다(2026-09-07, 이전 이름 homez_naver_datalab_api에서
    # 개명 — 아직 아무도 등록하지 않은 시점이라 안전하게 변경).
    DEFAULT_CREDENTIAL_REFERENCE = "homez_naver_api_hub"
    # 무료 할당량(2026-09-07 사용자 확정, API HUB 기본 무료 등급) —
    # 검색어 트렌드는 월 30,000회, 일일 한도는 없음.
    USAGE_BUCKET = "naver_search_trend"
    MONTHLY_CALL_LIMIT = 30000
    DAILY_CALL_LIMIT = None

    def __init__(
        self,
        credential_store: CredentialStore,
        credential_reference: str,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        http_post: Callable | None = None,
        usage_tracker: ApiUsageTracker | None = None,
    ):
        self.credential_store = credential_store
        self.credential_reference = credential_reference
        self.lookback_days = lookback_days
        if http_post is not None:
            self._http_post = http_post
        else:
            import requests

            self._http_post = requests.post
        if usage_tracker is not None:
            self.usage_tracker = usage_tracker
        else:
            from app.desktop.paths import get_config_dir

            self.usage_tracker = ApiUsageTracker(
                get_config_dir() / "naver_api_hub_usage.json",
            )

    def fetch(self, keyword: str) -> TrendSignal | None:
        """
        Audit(2026-08-21, CTO 후속 지시)와 동일한 이유로 AI Capability
        Registry를 여기서도 강제한다(FixtureTrendAdapter.fetch()와
        동일 원칙 계승). 무료 할당량(월 30,000회)을 넘기면 호출
        자체를 하지 않는다 — 유료 구간으로 자동 전환하지 않는다
        (사용자 확정 요구사항).
        """

        require_active_capability(CapabilityCode.PRODUCT_DISCOVERY)

        if not keyword or not keyword.strip():
            return None

        limit_check = self.usage_tracker.is_within_limit(
            self.USAGE_BUCKET,
            daily_limit=self.DAILY_CALL_LIMIT,
            monthly_limit=self.MONTHLY_CALL_LIMIT,
        )
        if not limit_check.within_limit:
            return None

        try:
            credential = self.credential_store.read(self.credential_reference)
        except (CredentialNotFoundError, CredentialStoreError):
            return None

        client_id = str(credential.get("client_id", "")).strip()
        client_secret = str(credential.get("client_secret", "")).strip()
        if not client_id or not client_secret:
            return None

        end_date = date.today()
        start_date = end_date - timedelta(days=self.lookback_days)

        # 실제 호출을 시도하기 직전에 기록한다 — 이 아래에서 네트워크
        # 예외가 나거나 응답이 실패여도 네이버 쪽에는 이미 호출
        # 시도가 접수된 것이므로(재시도하지 않으므로 이중 집계 없음).
        self.usage_tracker.record_call(self.USAGE_BUCKET)

        try:
            response = self._http_post(
                self.ENDPOINT,
                headers={
                    "X-NCP-APIGW-API-KEY-ID": client_id,
                    "X-NCP-APIGW-API-KEY": client_secret,
                    "Content-Type": "application/json",
                },
                json={
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "timeUnit": "week",
                    "keywordGroups": [
                        {"groupName": keyword, "keywords": [keyword]},
                    ],
                },
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — 네트워크 실패는 fail-closed로 None
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
            results = payload.get("results") or []
            if not results:
                return None
            data_points = results[0].get("data") or []
            series = [float(point["ratio"]) for point in data_points]
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

        if not series:
            return None

        return TrendSignal(
            keyword=keyword,
            market="NAVER_SHOPPING",
            search_volume_series=series,
            observed_at=datetime.utcnow().isoformat() + "Z",
            source="naver_api_hub_search_trend",
        )


__all__ = [
    "TrendSignal",
    "TrendSourceAdapter",
    "FixtureTrendAdapter",
    "NaverDataLabTrendAdapter",
]
