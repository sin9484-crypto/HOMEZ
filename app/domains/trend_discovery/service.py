"""
=========================================================
Homez OS

File : app/domains/trend_discovery/service.py

V3 Trend Discovery AI — 점수 산정

규칙·가중치를 명시적으로 분리한 결정론적 점수 계산이다. LLM 등 비결정론적
판단만으로 핵심 점수(trend_score)를 만들지 않는다. 외부 네트워크를
호출하지 않으며 실제 구매·상품 등록을 실행하지 않는다(추천 근거만 생성).
=========================================================
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.domains.trend_discovery.adapter import TrendSignal


@dataclass(frozen=True)
class TrendResult:

    trend_score: float
    confidence: float
    evidence: list[str]
    observed_at: str | None
    source: str | None
    limitations: list[str]

    def as_dict(self) -> dict:

        return {
            "trend_score": self.trend_score,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "observed_at": self.observed_at,
            "source": self.source,
            "limitations": self.limitations,
        }


class TrendDiscoveryService:

    MIN_DATA_POINTS = 3
    STALE_DATA_MAX_AGE_DAYS = 7

    # 가중치 (명시적으로 분리 — 향후 조정 시 이 두 값만 바꾸면 된다)
    GROWTH_WEIGHT = 0.5
    COMPETITION_WEIGHT = 0.5
    MAX_COMPETITION_PENALTY = 0.5

    def evaluate(
        self,
        signal: TrendSignal | None,
        now: datetime | None = None,
    ) -> TrendResult:
        """
        Audit(2026-08-21, CTO 후속 지시) — 이 Domain은 현재 어떤
        router에서도 호출되지 않는다(실 HTTP 진입점 없음, 재확인
        완료). 그럼에도 AI Capability Registry 강제를 여기 연결해
        두는 이유: 향후 실제 파이프라인에 연결되는 순간부터 역할
        계약 없이는 호출될 수 없게 만들기 위함이다(추정 실행 경로가
        아니라 이 함수 자체가 유일한 계산 진입점).
        """

        require_active_capability(CapabilityCode.PRODUCT_ANALYSIS)

        if signal is None:
            return TrendResult(
                trend_score=0.0,
                confidence=0.0,
                evidence=[],
                observed_at=None,
                source=None,
                limitations=["입력 데이터 없음"],
            )

        if signal.is_banned_category:
            return TrendResult(
                trend_score=0.0,
                confidence=0.0,
                evidence=["금지상품 카테고리로 분류됨"],
                observed_at=signal.observed_at,
                source=signal.source,
                limitations=[
                    "금지상품 정책에 의해 차단됨 — 추천 대상이 아님",
                ],
            )

        series = signal.search_volume_series
        limitations: list[str] = []
        evidence: list[str] = []
        confidence = 0.9

        if len(series) < self.MIN_DATA_POINTS:
            return TrendResult(
                trend_score=0.0,
                confidence=0.2,
                evidence=[
                    f"데이터 포인트 {len(series)}개 "
                    f"(최소 {self.MIN_DATA_POINTS}개 미만)",
                ],
                observed_at=signal.observed_at,
                source=signal.source,
                limitations=["데이터 부족으로 신뢰도가 낮음"],
            )

        evaluated_at = now or datetime.now(timezone.utc)
        stale = self._is_stale(signal.observed_at, evaluated_at)

        if stale:
            confidence *= 0.5
            limitations.append(
                f"관측 시각이 {self.STALE_DATA_MAX_AGE_DAYS}일 이상 지남 "
                "— 오래된 데이터",
            )

        is_flat = len(set(series)) == 1

        if is_flat:
            confidence *= 0.3
            limitations.append(
                "모든 데이터 포인트가 동일함 — 중복 근거이거나 변화 없음",
            )

        growth = self._growth_rate(series)
        competition_penalty = min(
            signal.competitor_count / 100.0, self.MAX_COMPETITION_PENALTY,
        )

        trend_score = (
            0.5
            + growth * self.GROWTH_WEIGHT
            - competition_penalty * self.COMPETITION_WEIGHT
        )
        trend_score = max(0.0, min(1.0, trend_score))

        evidence.append(
            f"search_volume: first={series[0]}, last={series[-1]}, "
            f"growth_rate={growth:.4f}",
        )
        evidence.append(f"competitor_count={signal.competitor_count}")

        return TrendResult(
            trend_score=round(trend_score, 4),
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            evidence=evidence,
            observed_at=signal.observed_at,
            source=signal.source,
            limitations=limitations,
        )

    def _is_stale(
        self,
        observed_at_iso: str,
        now: datetime,
    ) -> bool:

        if not observed_at_iso:
            return True

        try:
            observed_at = datetime.fromisoformat(observed_at_iso)
        except ValueError:
            return True

        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)

        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        age = now.astimezone(timezone.utc) - observed_at.astimezone(
            timezone.utc,
        )

        return age.days >= self.STALE_DATA_MAX_AGE_DAYS

    @staticmethod
    def _growth_rate(
        series: list[float],
    ) -> float:

        first, last = series[0], series[-1]

        if first == 0:
            return 1.0 if last > 0 else 0.0

        return (last - first) / first


__all__ = [
    "TrendResult",
    "TrendDiscoveryService",
]
