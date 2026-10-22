"""
=========================================================
Homez OS

File : app/domains/diagnostics/health_summary.py

2026-10-18 — 서버 관리 AI T1: **운영 상태 진단 요약**의 규칙. 앱이 돌고 있는 상태(자동 작업·백업·복구 시험·디스크·긴급 중지)를 읽어 온 값(스냅샷)으로
"이상 후보"를 낸다. 순수 함수, AI 없음, 쓰기·외부 호출 없음. 스냅샷을 모으는 일은 `health_service.py`가 읽기 전용으로 한다.

점검(수준 FAIL/WARN/INFO, 문제가 없는 항목은 `passed`에 코드만 남는다):
  - SCHEDULER      자동 작업 엔진이 꺼져 있다(설정으로 끈 것이 아니라면) → FAIL / 설정으로 꺼 둠 → INFO
  - JOBS           등록돼야 할 자동 작업이 빠졌다 → FAIL / 멈춰 있다(다음 실행 시각 없음) → WARN / 다음 실행 시각이 15분 넘게 지났다(밀림) → WARN
  - BACKUP         백업이 한 번도 없다 → WARN / 7일 넘게 없다 → WARN / 30일 넘게 없다 → FAIL
  - REHEARSAL      복구 시험 기록이 없다 → INFO / 가장 최근 시험이 실패했다 → FAIL / 14일 넘게 시험하지 않았다 → WARN
  - DISK           데이터 디스크 여유가 1GB 미만 → WARN / 300MB 미만 → FAIL
  - ESTOP          긴급 중지가 켜져 있다 → WARN(바깥으로 나가는 일이 모두 멈춰 있다)
  - DETECTION      신규 주문 자동 감지(2026-10-21 반영 A) — 운영 화면이 이미 쓰는 `describe_health` 코드를 그대로 옮긴다: 승인 센터 허용이 없다 → WARN /
                   연속 실패로 오류 모드 → FAIL / 일시 중지·실패 중·연결 정보 확인 불가·환경 제한 → WARN / 꺼 둠(수동) → INFO / 정상으로 보이지만 마지막 시도가
                   30분 넘게 지났다(앱이 꺼져 있었거나 엔진이 멈춤) → WARN
  - READS          주문·배송 상태, 반품·문의·정산, 상품 상태를 읽는 자동 확인의 허용(2026-10-21 반영 A) — 전에 허용이 있었는데 만료·취소·소진됐다 → WARN /
                   한 번도 허용한 적이 없다 → INFO. 허용이 없으면 그 확인이 **조용히 멈추므로** 화면이 "일이 없다"와 "감시를 못 한다"를 구분할 수 있어야 한다
값을 읽지 못한 항목(None)은 점검하지 않고 `unknown`에 코드만 남긴다 — 모르는 것을 정상이라고 하지 않는다.
시각은 모두 UTC naive다. 이 모듈은 원인을 추측하지 않는다(코드와 숫자만) — 원인 설명은 사람이나 이후 AI 단계가 한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"
LEVEL_RANK = {FAIL: 0, WARN: 1, INFO: 2}

JOB_OVERDUE_MINUTES = 15
BACKUP_STALE_DAYS = 7
BACKUP_FAIL_DAYS = 30
REHEARSAL_STALE_DAYS = 14
DISK_WARN_BYTES = 1024 ** 3
DISK_FAIL_BYTES = 300 * 1024 ** 2
DETECTION_STALE_MINUTES = 30  # 감지 주기(5분)의 6배 — 이보다 오래 시도 기록이 없으면 "정상"이라 믿지 않는다

# 운영 화면(`describe_health`)의 상태 코드 → (점검 코드, 수준). 여기 없는 코드는 모르는 상태라 점검하지 않고 unknown에 남긴다.
DETECTION_HEALTH_CODES = {
    "NEEDS_ALLOWANCE": ("DETECTION_NEEDS_ALLOWANCE", WARN),
    "ERROR": ("DETECTION_ERROR", FAIL),
    "PAUSED": ("DETECTION_PAUSED", WARN),
    "FAILING": ("DETECTION_FAILING", WARN),
    "BLOCKED": ("DETECTION_BLOCKED", WARN),
    "ENV_BLOCKED": ("DETECTION_ENV_BLOCKED", WARN),
    "OFF": ("DETECTION_OFF", INFO),
}


def _check(code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "params": params}


def _days(now: datetime, then: datetime) -> int:
    return max(0, (now - then).days)


def evaluate(snapshot: dict[str, Any]) -> dict[str, Any]:
    """스냅샷 → {"overall": FAIL|WARN|INFO|OK, "checks": [이상 후보], "passed": [정상 코드], "unknown": [못 읽은 코드]}.

    스냅샷 키(모두 선택): now(datetime), scheduler_enabled(bool), scheduler(None 또는 {"running": bool, "jobs": [{"id", "next_run"(datetime|None)}]}),
    expected_jobs(list[str]), latest_backup_at, latest_rehearsal({"at", "ok"}|None), rehearsal_known(bool), disk_free_bytes(int|None), estop_active(bool|None),
    detection(None 또는 {"health_code": str, "minutes_since_attempt": int|None}), reads(None 또는 {"lapsed": [목적], "never": [목적]})."""

    now: datetime = snapshot["now"]
    checks: list[dict[str, Any]] = []
    passed: list[str] = []
    unknown: list[str] = []

    # ---- 자동 작업 엔진·작업 ----
    enabled = snapshot.get("scheduler_enabled")
    scheduler = snapshot.get("scheduler")
    if enabled is None:
        unknown.append("SCHEDULER")
    elif not enabled:
        checks.append(_check("SCHEDULER_DISABLED", INFO))
    elif scheduler is None or not scheduler.get("running"):
        checks.append(_check("SCHEDULER_NOT_RUNNING", FAIL))
    else:
        passed.append("SCHEDULER")
        jobs = {job["id"]: job for job in scheduler.get("jobs", [])}
        expected = list(snapshot.get("expected_jobs") or [])
        missing = [job_id for job_id in expected if job_id not in jobs]
        paused = [job_id for job_id in expected if job_id in jobs and jobs[job_id].get("next_run") is None]
        overdue = [
            job_id for job_id in expected
            if job_id in jobs and jobs[job_id].get("next_run") is not None
            and (now - jobs[job_id]["next_run"]).total_seconds() > JOB_OVERDUE_MINUTES * 60
        ]
        if missing:
            checks.append(_check("JOBS_MISSING", FAIL, jobs=missing))
        if paused:
            checks.append(_check("JOBS_PAUSED", WARN, jobs=paused))
        if overdue:
            checks.append(_check("JOBS_OVERDUE", WARN, jobs=overdue))
        if not (missing or paused or overdue):
            passed.append("JOBS")

    # ---- 백업·복구 시험 ----
    if "latest_backup_at" not in snapshot:
        unknown.append("BACKUP")
    else:
        last = snapshot["latest_backup_at"]
        if last is None:
            checks.append(_check("BACKUP_NONE", WARN))
        else:
            age = _days(now, last)
            if age > BACKUP_FAIL_DAYS:
                checks.append(_check("BACKUP_OLD", FAIL, days=age))
            elif age > BACKUP_STALE_DAYS:
                checks.append(_check("BACKUP_STALE", WARN, days=age))
            else:
                passed.append("BACKUP")
    if not snapshot.get("rehearsal_known"):
        unknown.append("REHEARSAL")
    else:
        rehearsal = snapshot.get("latest_rehearsal")
        if rehearsal is None:
            checks.append(_check("REHEARSAL_NONE", INFO))
        elif not rehearsal.get("ok"):
            checks.append(_check("REHEARSAL_FAILED", FAIL, days=_days(now, rehearsal["at"])))
        elif _days(now, rehearsal["at"]) > REHEARSAL_STALE_DAYS:
            checks.append(_check("REHEARSAL_STALE", WARN, days=_days(now, rehearsal["at"])))
        else:
            passed.append("REHEARSAL")

    # ---- 디스크·긴급 중지 ----
    free = snapshot.get("disk_free_bytes")
    if free is None:
        unknown.append("DISK")
    elif free < DISK_FAIL_BYTES:
        checks.append(_check("DISK_CRITICAL", FAIL, free_mb=free // (1024 ** 2)))
    elif free < DISK_WARN_BYTES:
        checks.append(_check("DISK_LOW", WARN, free_mb=free // (1024 ** 2)))
    else:
        passed.append("DISK")
    estop = snapshot.get("estop_active")
    if estop is None:
        unknown.append("ESTOP")
    elif estop:
        checks.append(_check("ESTOP_ACTIVE", WARN))
    else:
        passed.append("ESTOP")

    # ---- 신규 주문 자동 감지 · 읽기 허용(2026-10-21 반영 A) ----
    detection = snapshot.get("detection")
    if not isinstance(detection, dict):
        unknown.append("DETECTION")
    else:
        health_code = detection.get("health_code")
        minutes = detection.get("minutes_since_attempt")
        if health_code in DETECTION_HEALTH_CODES:
            code, level = DETECTION_HEALTH_CODES[health_code]
            params = {"minutes": minutes} if isinstance(minutes, int) and not isinstance(minutes, bool) else {}
            checks.append(_check(code, level, **params))
        elif health_code in ("OK", "WAITING"):
            if health_code == "OK" and isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > DETECTION_STALE_MINUTES:
                checks.append(_check("DETECTION_STALE", WARN, minutes=minutes))
            else:
                passed.append("DETECTION")
        else:
            unknown.append("DETECTION")
    reads = snapshot.get("reads")
    if not isinstance(reads, dict):
        unknown.append("READS")
    else:
        lapsed, never = list(reads.get("lapsed") or []), list(reads.get("never") or [])
        if lapsed:
            checks.append(_check("READS_ALLOWANCE_LAPSED", WARN, purposes=lapsed))
        if never:
            checks.append(_check("READS_NOT_ALLOWED", INFO, purposes=never))
        if not (lapsed or never):
            passed.append("READS")

    checks.sort(key=lambda c: LEVEL_RANK[c["level"]])
    levels = {c["level"] for c in checks}
    overall = FAIL if FAIL in levels else WARN if WARN in levels else INFO if INFO in levels else "OK"
    return {"overall": overall, "checks": checks, "passed": passed, "unknown": unknown}
