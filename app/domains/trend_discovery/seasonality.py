"""
=========================================================
Homez OS

File : app/domains/trend_discovery/seasonality.py

2026-11-13 — **트렌드 계절 보정**(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-4, 사용자 지시 "다음 작업 진행해라").
네이버 검색어 트렌드(주 단위)를 2년 받아, 지금 오르는 것이 **매년 이맘때 오르는 계절**인지 **작년에 없던 새 움직임**인지 가른다.
계절 효과를 빼지 않으면 '뜨는 키워드'를 잘못 잡는다는 지적이 공통이다(조사 1-7). AI 없음, 외부 호출 없음(순수 계산).

계산(모두 날짜로 맞춘다 — 점의 순서만으로 52주 전을 찾지 않는다. 날짜를 모르면 '비교 불가'):
  - 지금 수준   = 마지막 주 기준 최근 4주 평균
  - 작년 수준   = 그 4주에서 정확히 364일(52주) 앞의 4주 평균
  - 지금 오름   = 지금 수준 ÷ 9~12주 전 4주 평균 − 1
  - 작년 오름   = 같은 계산을 364일 앞에서
  - 작년 대비   = 지금 수준 ÷ 작년 수준
판정(규칙 초안 — 숫자는 상수로 분리, 사용자가 바꿀 수 있다):
  - SEASONAL         지금 오름 ≥ 30% 이고 작년 같은 때도 ≥ 30% 올랐고, 수준이 작년의 1.5배 미만 → "계절(작년 이맘때도 올랐음)"
  - EMERGING         지금 오름 ≥ 30% 인데 작년 같은 때는 오르지 않았거나, 작년 수준이 0 → "새 움직임"
  - ABOVE_LAST_YEAR  오름은 없지만 작년 같은 때의 1.5배 이상 → "작년보다 높음"
  - BELOW_LAST_YEAR  작년 같은 때의 0.67배 이하 → "작년보다 낮음"
  - STEADY           그 밖 → "작년과 비슷"
  - NOT_ENOUGH       1년 전 구간 자료가 없음·날짜 모름·값 이상 → "비교 불가"
값은 키워드마다 기간 최대값을 100으로 둔 상대 지수라, 같은 키워드 안의 비율만 의미가 있다(키워드끼리 비교하지 않는다).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import timedelta

SEASON_LOOKBACK_DAYS = 735          # 약 2년 + 여유(1년 전 구간의 기준 4주까지 들어오게)
SCORE_LOOKBACK_DAYS = 90            # 기존 점수(첫 주 → 마지막 주)는 지금처럼 최근 90일로만 계산한다
YEAR_DAYS = 364
RECENT_DAYS = 28                    # 최근 4주
BASE_FROM_DAYS, BASE_TO_DAYS = 84, 56   # 기준: 12~9주 전(4주)
RISE = 0.30
YOY_HIGH = 1.5
YOY_LOW = 0.67

SEASONAL = "SEASONAL"
EMERGING = "EMERGING"
ABOVE_LAST_YEAR = "ABOVE_LAST_YEAR"
BELOW_LAST_YEAR = "BELOW_LAST_YEAR"
STEADY = "STEADY"
NOT_ENOUGH = "NOT_ENOUGH"
ALL_LABELS = (SEASONAL, EMERGING, ABOVE_LAST_YEAR, BELOW_LAST_YEAR, STEADY, NOT_ENOUGH)


@dataclass(frozen=True)
class SeasonResult:
    label: str
    rise: float | None = None          # 지금 오름(비율, 0.3 = +30%)
    last_year_rise: float | None = None
    yoy: float | None = None           # 작년 대비(배)

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        return {"label": self.label, "rise": r(self.rise), "last_year_rise": r(self.last_year_rise), "yoy": r(self.yoy)}


def _points(series, periods) -> list[tuple[date, float]] | None:
    if not isinstance(series, (list, tuple)) or not isinstance(periods, (list, tuple)) or len(series) != len(periods) or not series:
        return None
    out: list[tuple[date, float]] = []
    for value, period in zip(series, periods):
        try:
            day = date.fromisoformat(str(period)[:10])
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number < 0:          # NaN·음수는 네이버 값이 아니다
            return None
        out.append((day, number))
    out.sort(key=lambda p: p[0])
    return out


def _mean(points: list[tuple[date, float]], start: date, end: date) -> float | None:
    """start < 날짜 ≤ end 인 점의 평균. 점이 없으면 None."""

    values = [v for d, v in points if start < d <= end]
    return sum(values) / len(values) if values else None


def _rise(points: list[tuple[date, float]], latest: date) -> tuple[float | None, float | None]:
    """(수준, 오름). 기준이 0이면 오름은 None(나눌 수 없다)."""

    level = _mean(points, latest - timedelta(days=RECENT_DAYS), latest)
    base = _mean(points, latest - timedelta(days=BASE_FROM_DAYS), latest - timedelta(days=BASE_TO_DAYS))
    if level is None or base is None:
        return level, None
    if base == 0:
        return level, (None if level == 0 else float("inf"))
    return level, level / base - 1


def recent_slice(series, periods, latest_days: int = SCORE_LOOKBACK_DAYS) -> tuple[list[float], list[str]]:
    """긴 자료에서 기존 점수용 최근 구간만 남긴다(날짜를 모르면 그대로 돌려준다 — 기존 동작)."""

    points = _points(series, periods)
    if points is None:
        return list(series or []), list(periods or [])
    latest = points[-1][0]
    kept = [(d, v) for d, v in points if d > latest - timedelta(days=latest_days)]
    return [v for _, v in kept], [d.isoformat() for d, _ in kept]


def judge(series, periods) -> SeasonResult:
    points = _points(series, periods)
    if points is None:
        return SeasonResult(NOT_ENOUGH)
    latest = points[-1][0]
    last_year = latest - timedelta(days=YEAR_DAYS)
    if points[0][0] > last_year - timedelta(days=BASE_FROM_DAYS):
        return SeasonResult(NOT_ENOUGH)                      # 1년 전 기준 구간까지 자료가 닿지 않는다
    level, rise = _rise(points, latest)
    ly_level, ly_rise = _rise(points, last_year)
    if level is None or ly_level is None:
        return SeasonResult(NOT_ENOUGH)
    finite = lambda v: None if v is None or v == float("inf") else v  # noqa: E731
    if ly_level == 0:
        if level == 0:
            return SeasonResult(STEADY, finite(rise), finite(ly_rise), None)
        return SeasonResult(EMERGING, finite(rise), finite(ly_rise), None)    # 작년 이맘때 검색이 없었다
    yoy = level / ly_level
    rising = rise is not None and rise >= RISE
    rose_last_year = ly_rise is not None and ly_rise >= RISE
    if rising and rose_last_year and yoy < YOY_HIGH:
        label = SEASONAL
    elif rising:
        label = EMERGING
    elif yoy >= YOY_HIGH:
        label = ABOVE_LAST_YEAR
    elif yoy <= YOY_LOW:
        label = BELOW_LAST_YEAR
    else:
        label = STEADY
    return SeasonResult(label, finite(rise), finite(ly_rise), yoy)


__all__ = [
    "SEASON_LOOKBACK_DAYS", "SCORE_LOOKBACK_DAYS", "RISE", "YOY_HIGH", "YOY_LOW", "ALL_LABELS", "SEASONAL", "EMERGING", "ABOVE_LAST_YEAR",
    "BELOW_LAST_YEAR", "STEADY", "NOT_ENOUGH", "SeasonResult", "judge", "recent_slice",
]
