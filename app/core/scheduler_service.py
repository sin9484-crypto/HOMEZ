"""
=========================================================
Homez OS

File : app/core/scheduler_service.py

2026-09-06 "가격·재고 자동 모니터링" — 주기 실행 인프라. 이 저장소에
스케줄러 자체가 없었다(감사로 확인된 기존 공백)는 것을 메우는
최소 구현이다.

APScheduler BackgroundScheduler를 데스크톱 앱 프로세스 안에서 단일
인스턴스로 운영한다 — 별도 워커 프로세스나 메시지 브로커를 두지
않는다(homez-core의 Modular Monolith 원칙 유지). 등록되는 Job은
FastAPI 요청 컨텍스트 밖에서 실행되므로 Depends(get_db)를 재사용할
수 없다 — Job 함수가 직접 SessionLocal()을 열고 finally에서 닫아야
한다(이 파일 자체는 그 책임을 지지 않는다, 순수 스케줄러 생명주기
관리만 담당).
=========================================================
"""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class SchedulerService:
    """프로세스 전역 단일 인스턴스 — 데스크톱 앱은 항상 한 프로세스만
    실행되므로(멀티프로세스 워커 없음) 클래스 레벨 상태로 충분하다."""

    _scheduler: BackgroundScheduler | None = None

    @classmethod
    def start(cls) -> BackgroundScheduler:
        """이미 시작돼 있으면 그대로 반환한다(재시작 시 중복 기동
        방지 — FastAPI lifespan이 재실행돼도 안전)."""

        if cls._scheduler is not None:
            return cls._scheduler

        scheduler = BackgroundScheduler(timezone="Asia/Seoul")
        scheduler.start()
        cls._scheduler = scheduler
        logger.info("SchedulerService started (Asia/Seoul)")
        return scheduler

    @classmethod
    def get(cls) -> BackgroundScheduler | None:

        return cls._scheduler

    @classmethod
    def add_interval_job(
        cls, func: Callable, *, minutes: int, job_id: str,
    ) -> None:
        """같은 job_id로 다시 호출하면 기존 Job을 교체한다(중복 등록
        방지 — replace_existing=True)."""

        scheduler = cls.start()
        scheduler.add_job(
            func, "interval", minutes=minutes, id=job_id,
            replace_existing=True, coalesce=True, max_instances=1,
        )

    @classmethod
    def add_cron_job(
        cls, func: Callable, *,
        day_of_week: str, hour: int, minute: int, job_id: str,
    ) -> None:
        """
        2026-09-10 Phase 6 — "매주"처럼 특정 요일·시각 기준 주기가
        필요한 Job용(예: 백업 복구 리허설, HOMEZ_USER_OPERATION_
        SETTINGS.md 11번). add_interval_job과 동일하게 같은 job_id로
        다시 호출하면 기존 Job을 교체하고(replace_existing=True),
        중복 실행을 막는다(coalesce=True — 프로세스가 오래 멈춰
        있다 재기동해도 밀린 실행을 몰아서 여러 번 하지 않고 1회로
        합친다, max_instances=1 — 이전 실행이 아직 끝나지 않았으면
        다음 트리거를 건너뛴다).

        day_of_week는 APScheduler CronTrigger 문법 그대로 받는다
        (예: "sun", "mon-fri").
        """

        scheduler = cls.start()
        scheduler.add_job(
            func,
            CronTrigger(
                day_of_week=day_of_week, hour=hour, minute=minute,
                timezone="Asia/Seoul",
            ),
            id=job_id,
            replace_existing=True, coalesce=True, max_instances=1,
        )

    @classmethod
    def shutdown(cls) -> None:
        """예외를 밖으로 던지지 않는다 — 앱 종료 경로에서 스케줄러
        정리 실패가 정상 종료 자체를 막으면 안 된다."""

        if cls._scheduler is None:
            return
        try:
            cls._scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            logger.exception("SchedulerService shutdown failed")
        finally:
            cls._scheduler = None


__all__ = ["SchedulerService"]
