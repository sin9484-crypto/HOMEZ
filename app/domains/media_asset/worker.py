"""
=========================================================
Homez OS

File : app/domains/media_asset/worker.py

Image Generation Job — 실제 영속 Worker(2026-08-02 CTO 보안 보완
Gate R5).

이전 설계는 `submit_job()`이 PENDING 행을 commit한 직후 같은 HTTP
요청 스레드 안에서 `_execute_job()`을 동기 호출했다 — Provider
호출이 오래 걸리면 HTTP 요청이 그만큼 오래 걸리고, 요청 스레드가
죽으면(타임아웃 등) 그 이후 처리가 통째로 유실될 수 있었다.

이 모듈은 별도 백그라운드 스레드에서 PENDING Job을 폴링해 claim하고
실행하는 `ImageGenerationJobWorker`를 제공한다:

  - claim은 새 코드가 아니라 `ImageGenerationJobQueueService.
    _execute_job()`이 이미 내부에서 하던 조건부 UPDATE(PENDING→
    RUNNING, rowcount==1만 성공)를 그대로 재사용한다 — Worker는 그저
    "가장 오래된 PENDING 하나를 골라 `_execute_job()`을 부른다"만
    반복한다. 여러 Worker(또는 Worker와 수동 재시도)가 같은 Job을
    동시에 집으려 해도 그 조건부 UPDATE가 정확히 하나만 통과시킨다.
  - Job 하나 처리마다 새 DB Session을 열고 끝나면 반드시 닫는다
    (SQLAlchemy Session은 스레드 안전하지 않다 — 요청 스레드의
    Session과 절대 공유하지 않는다).
  - 루프 안에서 발생하는 어떤 예외도 Worker 스레드 자체를 죽이지
    않는다(로그만 남기고 다음 폴링으로 넘어간다) — Provider 코드의
    버그가 앱 전체를 중단시켜서는 안 된다.
  - start()/stop()은 멱등이다. stop()은 실행 중인 스레드가 완전히
    끝날 때까지 join한 뒤에만 반환한다 — 앱 종료 후 잔존 스레드가
    남지 않는다.
  - 시작 시 한 번, "오래 멈춰 있던 RUNNING"(이전 실행에서 죽은
    Worker가 남긴 것)을 전체 회사 대상으로 일괄 FAILED(TIMEOUT_
    RECOVERED) 처리한다 — Provider 호출이 진행 중이었을 수 있어
    안전하게 재개할 수 없으므로, 사용자가 명시적으로 retry_job()을
    호출해야 한다. PENDING은 별도 "정지" 판정이 필요 없다(그냥 다음
    폴링에서 claim되면 된다 — Worker가 있는 한 PENDING은 오래
    머물러도 안전하다).
=========================================================
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)

DEFAULT_POLL_INTERVAL_SECONDS = 0.5

_logger = logging.getLogger("homez.media_asset.worker")


class ImageGenerationJobWorker:

    def __init__(
        self,
        session_factory: Callable[[], Session],
        media_root: Path | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        stall_timeout_seconds: int | None = None,
        service_factory: (
            Callable[[Session], ImageGenerationJobQueueService] | None
        ) = None,
    ):
        """
        `service_factory`: 이 Worker는 media_asset 도메인 자체에는
        listing_package를 알 필요가 없다(도메인 경계 유지) — 하지만
        listing_package_id를 가진 Job이 끝났을 때 그 패키지의
        fingerprint를 다시 계산해야 하는 콜백은 `ListingPackageService`
        쪽에만 있다(`ImageGenerationJobQueueService.__init__`의
        `on_job_completed`). 기본값은 콜백 없는 순수
        `ImageGenerationJobQueueService`를 만들지만, 실제 앱 조립부
        (app/desktop/server.py)는 `lambda db: ListingPackageService(db).
        image_queue_service`처럼 콜백까지 미리 연결된 Service를
        만들어주는 factory를 넘길 수 있다 — Worker는 그 반환값을
        그대로 쓴다.
        """

        self._session_factory = session_factory
        self._media_root = media_root
        self._poll_interval_seconds = poll_interval_seconds
        self._stall_timeout_seconds = stall_timeout_seconds
        self._service_factory = service_factory or self._default_service_factory

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _default_service_factory(
        self, db: Session,
    ) -> ImageGenerationJobQueueService:

        return ImageGenerationJobQueueService(db, media_root=self._media_root)

    # ------------------------------------------------
    # 수명주기
    # ------------------------------------------------

    def start(self) -> None:

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._recover_stalled_running_jobs_on_startup()

        self._thread = threading.Thread(
            target=self._run_loop, name="image-generation-worker", daemon=True,
        )
        self._thread.start()
        _logger.info("Image Generation Worker 시작")

    def stop(self, timeout: float = 10.0) -> bool:

        self._stop_event.set()

        if self._thread is None:
            return True

        self._thread.join(timeout=timeout)
        still_alive = self._thread.is_alive()

        if still_alive:
            _logger.warning(
                "Image Generation Worker가 제한 시간(%.1f초) 내에 "
                "종료되지 않음", timeout,
            )
        else:
            _logger.info("Image Generation Worker 정상 종료")
            self._thread = None

        return not still_alive

    def is_running(self) -> bool:

        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------
    # 내부
    # ------------------------------------------------

    def _run_loop(self) -> None:

        while not self._stop_event.is_set():
            try:
                processed = self._process_one()

            except Exception:  # noqa: BLE001
                # Provider/DB 예외가 무엇이든, Worker 스레드 자체는
                # 절대 죽지 않는다 — 로그만 남기고 다음 폴링을 계속한다.
                _logger.exception("Image Generation Worker 처리 중 예외 발생")
                processed = False

            if not processed:
                self._stop_event.wait(self._poll_interval_seconds)

    def _process_one(self) -> bool:
        """
        가장 오래된 PENDING Job 하나를 claim해 실행한다. 처리할 Job이
        없으면 False(호출자가 폴링 간격만큼 대기하게 한다).
        """

        db = self._session_factory()

        try:
            service = self._service_factory(db)

            candidate = service.repository.get_oldest_pending_job_any_company()
            if candidate is None:
                return False

            service._execute_job(candidate.id, candidate.company_id)  # noqa: SLF001

            return True

        finally:
            db.close()

    def _recover_stalled_running_jobs_on_startup(self) -> None:

        db = self._session_factory()

        try:
            service = self._service_factory(db)
            kwargs = {}
            if self._stall_timeout_seconds is not None:
                kwargs["stall_timeout_seconds"] = self._stall_timeout_seconds

            recovered = service.recover_all_companies_stalled_running_jobs(
                now=datetime.utcnow(), **kwargs,
            )

            if recovered:
                _logger.info(
                    "Worker 시작 시 정지된 RUNNING Job %d건을 FAILED로 "
                    "복구함", len(recovered),
                )

        except Exception:  # noqa: BLE001
            _logger.exception("시작 시 정지된 Job 복구 중 예외 발생")

        finally:
            db.close()


__all__ = [
    "ImageGenerationJobWorker",
    "DEFAULT_POLL_INTERVAL_SECONDS",
]
