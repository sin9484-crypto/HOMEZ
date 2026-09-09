"""
=========================================================
Homez OS

File : app/core/api_usage_tracker.py

2026-09-07 "네이버 API HUB 실연동" — 무료 할당량 강제용 파일 기반
사용량 카운터. 새 Model/Migration을 만들지 않기 위해(가격·재고
모니터링과 달리 이번 건은 단순 카운터라 관계형 스키마가 필요 없다)
`app/desktop/paths.py::get_config_dir()`(기존에 이미 "사용자별 설정
저장 위치"로 계약돼 있던 자리) 아래 JSON 파일 하나로 관리한다.

호출 순서 계약: 호출자는 반드시 `is_within_limit()`으로 먼저 확인한
뒤(False면 실제 API를 절대 호출하지 않는다), 실제 네트워크 요청을
보내기 직전에 `record_call()`을 호출해야 한다 — 성공/실패와 무관하게
"시도했다"는 사실 자체를 기록한다(네이버도 실패 응답을 포함해
호출 횟수를 집계하므로 동일한 기준). 재시도 로직 자체가 이
저장소의 모든 외부 Adapter에 없으므로(봇 탐지 회피 금지 원칙과
동일 이유로 재시도하지 않음) 이중 집계 위험이 없다.

단일 프로세스·저빈도 호출(운영자가 수동으로 트리거하는 화면 액션)
전제로 설계했다 — 파일 잠금(flock 등)은 걸지 않는다(동시 다중
프로세스 기록 경합은 이번 범위 밖으로 명시).
=========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class UsageLimitCheck:

    within_limit: bool
    daily_count: int
    monthly_count: int
    daily_limit: int | None
    monthly_limit: int | None


class ApiUsageTracker:

    def __init__(self, storage_path: Path):

        self.storage_path = storage_path

    def _today(self) -> date:

        return date.today()

    def _month_key(self, d: date) -> str:

        return f"{d.year:04d}-{d.month:02d}"

    def _load(self) -> dict:

        if not self.storage_path.exists():
            return {}
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # 손상된 파일을 읽지 못한다고 해서 호출을 무한 허용하면
            # 안 된다 — 그러나 손상 파일 때문에 앱 전체가 죽어도
            # 안 되므로, 빈 상태로 안전하게 시작한다(카운트가
            # 0부터 다시 시작되는 것은 "한도를 더 못 씀"보다 훨씬
            # 안전한 실패 방향 — fail-closed가 아니라 fail-safe로
            # 완화. 실제 위험은 낮다: 파일 손상 자체가 드물고, 손상
            # 시 최악의 경우도 "이번 달 카운트를 다시 센다"는 것뿐).
            return {}

    def _save(self, data: dict) -> None:

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.storage_path)

    def _current_counts(self, data: dict, bucket: str) -> tuple[int, int]:
        """(daily_count, monthly_count) — 날짜/월이 바뀌었으면 그
        시점 기준으로 0으로 취급한다(저장된 값을 갱신하지는 않는다,
        읽기 전용 조회이므로)."""

        today = self._today()
        bucket_data = data.get(bucket, {})

        daily = bucket_data.get("daily", {})
        daily_count = daily.get("count", 0) if daily.get("date") == today.isoformat() else 0

        monthly = bucket_data.get("monthly", {})
        monthly_count = (
            monthly.get("count", 0)
            if monthly.get("month") == self._month_key(today) else 0
        )

        return daily_count, monthly_count

    def is_within_limit(
        self, bucket: str, *, daily_limit: int | None, monthly_limit: int | None,
    ) -> UsageLimitCheck:

        data = self._load()
        daily_count, monthly_count = self._current_counts(data, bucket)

        within = True
        if daily_limit is not None and daily_count >= daily_limit:
            within = False
        if monthly_limit is not None and monthly_count >= monthly_limit:
            within = False

        return UsageLimitCheck(
            within_limit=within, daily_count=daily_count,
            monthly_count=monthly_count, daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )

    def record_call(self, bucket: str) -> None:
        """호출 시도 1회를 기록한다 — 반드시 is_within_limit()이
        True를 반환한 뒤에만, 그리고 실제 네트워크 요청 직전에
        호출해야 한다(계약은 이 파일 docstring 참고)."""

        today = self._today()
        month_key = self._month_key(today)
        data = self._load()

        bucket_data = data.setdefault(bucket, {})

        daily = bucket_data.setdefault("daily", {})
        if daily.get("date") != today.isoformat():
            daily["date"] = today.isoformat()
            daily["count"] = 0
        daily["count"] = daily.get("count", 0) + 1

        monthly = bucket_data.setdefault("monthly", {})
        if monthly.get("month") != month_key:
            monthly["month"] = month_key
            monthly["count"] = 0
        monthly["count"] = monthly.get("count", 0) + 1

        self._save(data)


__all__ = ["ApiUsageTracker", "UsageLimitCheck"]
