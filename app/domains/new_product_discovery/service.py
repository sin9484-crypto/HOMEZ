"""
=========================================================
Homez OS

File : app/domains/new_product_discovery/service.py

V3 New Product Discovery AI

신제품 기준(HOMEZ 확정 결정, docs/HOMEZ_DECISIONS.md):
- 출시일(release_date) 기준 0일 이상 15일 이하를 신제품으로 본다.
- 기준 시각/timezone은 Asia/Seoul(KST, UTC+9 고정)로 처리한다.
- 미래 날짜는 오류/검토 대상이며 신제품으로 판정하지 않는다.
- 날짜 불명은 신제품으로 자동 판정하지 않는다.

이 모듈은 실제 상품 등록·구매·주문을 실행하지 않는다. 점수와 근거만
반환한다. 외부 네트워크를 호출하지 않는다.

Windows 환경에 IANA tzdata 패키지가 없어 zoneinfo.ZoneInfo("Asia/Seoul")가
동작하지 않을 수 있다(ZoneInfoNotFoundError). 대한민국 표준시(KST)는
서머타임이 없는 연중 고정 UTC+9이므로, 패키지 설치 없이도 정확한
timezone(timedelta(hours=9))을 대신 사용한다.
=========================================================
"""

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability

KST = timezone(timedelta(hours=9), name="Asia/Seoul")


class NewProductEvaluationError(Exception):
    """release_date가 기준 시각보다 미래일 때 발생 — 자동 보정하지 않는다."""


@dataclass(frozen=True)
class NewProductResult:

    is_new_product: bool
    age_days: int | None
    novelty_score: float
    confidence: float
    evidence: list[str]
    date_basis: str | None
    limitations: list[str]

    def as_dict(self) -> dict:

        return {
            "is_new_product": self.is_new_product,
            "age_days": self.age_days,
            "novelty_score": self.novelty_score,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "date_basis": self.date_basis,
            "limitations": self.limitations,
        }


class NewProductDiscoveryService:
    """출시일 기준 신제품 여부·novelty_score를 결정론적으로 계산한다."""

    NEW_PRODUCT_MAX_AGE_DAYS = 15

    def evaluate(
        self,
        release_date: date | None,
        now: datetime | None = None,
    ) -> NewProductResult:
        """
        release_date: 출시일 또는 공식 등록일(날짜 단위, timezone 정보 없음).
        now: 기준 시각. naive datetime이면 KST로 간주하고, aware datetime이면
             KST로 변환해서 사용한다. 생략하면 현재 시각(KST)을 사용한다.

        Audit(2026-08-21, CTO 후속 지시) — 이 Domain도 trend_discovery
        와 동일하게 현재 실 HTTP 진입점이 없다(재확인 완료). 향후
        연결 시점부터 역할 계약을 강제하기 위해 미리 연결해 둔다.
        """

        require_active_capability(CapabilityCode.PRODUCT_ANALYSIS)

        evaluated_at = now or datetime.now(KST)

        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=KST)

        today_kst = evaluated_at.astimezone(KST).date()

        if release_date is None:
            return NewProductResult(
                is_new_product=False,
                age_days=None,
                novelty_score=0.0,
                confidence=0.0,
                evidence=["release_date 없음"],
                date_basis=None,
                limitations=[
                    "출시일 불명 — 신제품 여부를 자동 판정하지 않음. "
                    "운영자 확인 필요.",
                ],
            )

        age_days = (today_kst - release_date).days

        if age_days < 0:
            raise NewProductEvaluationError(
                f"release_date({release_date})가 기준일(KST, {today_kst})"
                "보다 미래입니다. 데이터 오류이거나 별도 검토가 필요합니다.",
            )

        is_new = 0 <= age_days <= self.NEW_PRODUCT_MAX_AGE_DAYS

        if is_new:
            # 0일에 가까울수록 1.0에 가깝고 15일에 가까울수록 0.5까지 선형 감소.
            novelty_score = round(
                1.0 - (age_days / self.NEW_PRODUCT_MAX_AGE_DAYS) * 0.5, 4,
            )
        else:
            novelty_score = 0.0

        return NewProductResult(
            is_new_product=is_new,
            age_days=age_days,
            novelty_score=novelty_score,
            confidence=0.9,
            evidence=[
                f"release_date={release_date.isoformat()}, "
                f"기준일(KST)={today_kst.isoformat()}, age_days={age_days}",
            ],
            date_basis="release_date",
            limitations=[],
        )


__all__ = [
    "KST",
    "NewProductEvaluationError",
    "NewProductResult",
    "NewProductDiscoveryService",
]
