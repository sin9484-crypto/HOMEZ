"""
=========================================================
Homez OS

File : app/domains/scheduler/jobs.py

2026-09-10 Phase 6(HOMEZ_USER_OPERATION_SETTINGS.md — "5분마다 주문
수집", "주 1회 가격 검토", "DB 복구 가능 여부를 매주 시험" 등 주기
실행 전반) — APScheduler(`app/core/scheduler_service.py::
SchedulerService`)에 실제로 등록되는 Job 함수들.

이 파일의 함수는 FastAPI 요청 컨텍스트 밖(스케줄러 자체 스레드)에서
실행되므로 `Depends(get_db)`를 재사용할 수 없다 — 각 함수가 직접
`SessionLocal()`을 열고 `finally`에서 닫는다.

**2026-09-10 Phase 6 시점에는 "백업 복구 리허설" 하나만 등록했다.**
감사에서 이미 확인됐듯("시스템 전체에 스케줄러가 없음", Critical #3)
문서가 요구하는 주기 실행 대상은 트렌드 갱신·주문 수집·가격/재고
모니터링·알림 정기 스윕까지 더 있지만, 이번 Phase에서는 다음 이유로
백업 복구 리허설만 연결했다(자세한 조사 결과는
docs/HOMEZ_PROJECT_STATE.md Phase 6 절 참고):

- **주문 수집**(`app.domains.order.multi_channel_collection_service.
  OrderMultiChannelCollectionService.run_all()`)은 실제 코드가 이미
  있지만, 어떤 호출부도 `SafetyService.is_function_automatic()`
  (Phase 3의 기능별 자동화 모드 게이트)을 통과하지 않는다 — 지금
  그대로 스케줄러에 연결하면 자동화 모드 게이트가 없는 새 자동 실행
  경로가 생긴다. 게이트를 이 Job 안에서 새로 추가하려면 "시스템이
  자동 실행했다"는 감사 주체(actor_user_id) 표현 방식을 결정해야
  하는데(Phase 4의 `set_by=0` 패턴을 이 도메인에도 그대로 쓸 수
  있는지 `order` 도메인 코드를 더 깊이 봐야 함), 이번 Phase 범위에서
  서두르지 않기로 했다.
- **트렌드 갱신**은 후보 1건 단위 수동 갱신만 존재하고
  ("전체 후보를 훑는 배치/스케줄러는 범위 아님"이라고 그 서비스
  자신의 docstring이 명시), 전체 후보를 도는 배치 자체가 아직
  없다 — 스케줄러 연결이 아니라 새 기능 개발이 필요하다.
- **가격/재고 모니터링**은 HTTP/헤드리스 브라우저 Adapter만 있고
  Service/Model/Repository/조회 대상(Watch) 저장소 자체가 없다 —
  스케줄러 연결 이전에 그 계층부터 새로 설계해야 한다.
- **알림 정기 스윕**(`NotificationDeliveryRepository.
  list_pending_unconfirmed()`/`list_failed_retryable()` + 그 결과를
  `NotificationDeliveryService.escalate_unconfirmed_to_email()`/
  `retry_failed_notification()`으로 연결하는 오케스트레이터)은 조회
  메서드는 있지만 그 둘을 잇는 코드가 없다 — 상대적으로 작은
  작업이지만 이메일이 실제로 발송되는 경로라 별도로 신중히 검토할
  가치가 있어 이번 Phase에서 함께 서두르지 않았다.

백업 복구 리허설은 반대로 (1) 실행 함수(`RestoreService.
run_weekly_rehearsal()`)가 Phase 5에서 이미 완성·단독 테스트됐고,
(2) 실제 외부 API를 전혀 호출하지 않으며(로컬 파일 시스템만
다룸), (3) 원본 DB는 읽기만 한다 — 그래서 안전하게 지금 연결할 수
있었다.

2026-09-15 전면 감사 후속(Phase 9I, 10-17) — 리콜/판매중지 매일 확인
Job을 두 번째로 등록했다(`RECALL_NOTICE_CHECK_JOB_ID`, 매일
05:00(Asia/Seoul)). 이 Job은 항상 등록되지만, 실제로 무언가를 하려면
두 조건이 모두 필요하다: (1) `recall_check_job_states`의 최신 모드가
ACTIVE여야 한다(기본값 PAUSED — 등록만 하고 사용자가 켜기 전까지는
매일 트리거돼도 즉시 반환한다), (2) 그리고 실제 리콜/판매중지 데이터
소스 Provider가 선정돼야 한다(`app/domains/recall_notice/provider.py::
get_real_provider()`는 아직 항상 NotImplementedError를 던진다 — 어느
소스를 공식으로 쓸지 결정되지 않았다). 둘 중 하나라도 아니면 Job은
조용히 건너뛰고 경고 로그만 남긴다 — 실제 외부 호출은 이번 Phase에
전혀 없다.
=========================================================
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.core.scheduler_service import SchedulerService
from app.core.windows_credential_store import CredentialStore
from app.domains.company.model import Company
from app.domains.restore.service import RestoreService

logger = logging.getLogger(__name__)

BACKUP_REHEARSAL_JOB_ID = "weekly_backup_restore_rehearsal"
RECALL_NOTICE_CHECK_JOB_ID = "daily_recall_notice_check"


def run_backup_rehearsal_job(
    *,
    source_db_path: Path,
    backups_dir: Path,
    rehearsal_dir: Path,
    session_factory: Callable[[], Session] | None = None,
    credential_store: CredentialStore | None = None,
) -> None:
    """
    활성 회사마다(개인 베타 현재는 1개) 백업 복구 리허설을 1회씩
    실행한다. `source_db_path`(실제 homez.db)는 읽기만 한다 —
    `RestoreService.run_weekly_rehearsal()` 자체가 원본을 쓰지 않는
    설계다(Phase 5, docs/HOMEZ_PROJECT_STATE.md 참고).

    한 회사에서 예상치 못한 예외가 나도 나머지 회사 처리를 막지
    않는다("부분 실패 격리" — Phase 6 요구사항). `run_weekly_
    rehearsal()` 자신은 이미 예외를 던지지 않고 항상 결과를
    반환하므로(RehearsalResult), 여기서 잡는 예외는 그 계약을 벗어난
    진짜 예상 밖 상황(예: DB 세션 자체가 깨짐)만 해당한다.

    `session_factory`는 기본값 없이 지연 import한다(모듈 import
    시점에 `app.database.session.SessionLocal`을 평가하면, 이
    함수를 테스트가 단 한 번이라도 import하는 순간 실제 운영
    DATABASE_URL로 엔진이 만들어질 위험이 있다 — 이 파일 전체가
    이 세션 내내 지켜온 "임시 DB만 사용" 원칙과 정면으로 어긋난다).
    테스트는 항상 이 인자를 명시적으로 넘겨 격리된 임시 DB를
    쓴다.
    """

    if session_factory is None:
        from app.database.session import SessionLocal
        session_factory = SessionLocal

    if credential_store is None:
        from app.core.windows_credential_store import WindowsCredentialStore
        credential_store = WindowsCredentialStore()

    db = session_factory()
    try:
        # company.id만 미리 리스트로 뽑아둔다(ORM 인스턴스 자체를
        # 들고 있지 않는다) — RestoreService.run_weekly_rehearsal()이
        # 내부적으로 호출하는 restore()가 Windows 파일 잠금 회피를
        # 위해 이 루프 도중 self.db.close()+bind.dispose()를
        # 실행한다(app/domains/restore/service.py의 V7 Live Gate 4
        # 주석 참고) — Company ORM 인스턴스를 계속 들고 있으면 그
        # 시점 이후 `company.id` 같은 속성 접근이
        # DetachedInstanceError로 깨진다. 정수 id는 세션과 무관하므로
        # 안전하다.
        company_ids = [
            c.id
            for c in db.query(Company).filter(Company.active.is_(True))
        ]
        service = RestoreService(db, credential_store)

        for company_id in company_ids:
            try:
                result = service.run_weekly_rehearsal(
                    company_id=company_id,
                    source_db_path=source_db_path,
                    backups_dir=backups_dir,
                    rehearsal_dir=rehearsal_dir,
                )
                if result.success:
                    logger.info(
                        "주간 백업 복구 리허설 성공: company_id=%s",
                        company_id,
                    )
                else:
                    logger.warning(
                        "주간 백업 복구 리허설 실패: company_id=%s "
                        "reason=%s",
                        company_id, result.error_message,
                    )
            except Exception:  # noqa: BLE001 — 한 회사 실패가 나머지를 막지 않는다
                logger.exception(
                    "주간 백업 복구 리허설 Job에서 예외 발생: "
                    "company_id=%s",
                    company_id,
                )
    finally:
        db.close()


def run_recall_notice_check_job(
    *, session_factory: Callable[[], Session] | None = None,
) -> None:
    """
    2026-09-15 전면 감사 후속(Phase 9I, HOMEZ_USER_OPERATION_SETTINGS.md
    10-17) — 리콜/판매중지 매일 확인 Job. 기본 모드는 PAUSED다(공통
    규칙 15) — 사용자가 명시적으로 ACTIVE로 바꾸기 전까지는 아무 일도
    하지 않는다. ACTIVE로 바뀌어도, 실제 Provider가 아직 선정되지
    않았으므로(app/domains/recall_notice/provider.py::get_real_provider())
    여전히 아무 외부 호출도 하지 않는다 — 이 상태를 조용히 성공한
    것처럼 남기지 않고 경고 로그로 남긴다.
    """

    if session_factory is None:
        from app.database.session import SessionLocal
        session_factory = SessionLocal

    db = session_factory()
    try:
        from app.domains.recall_notice.constants import RecallCheckJobMode
        from app.domains.recall_notice.provider import get_real_provider
        from app.domains.recall_notice.service import RecallNoticeService

        service = RecallNoticeService(db)
        if service.get_job_mode() != RecallCheckJobMode.ACTIVE:
            logger.info(
                "리콜/판매중지 확인 Job — 모드가 ACTIVE가 아니어서 "
                "건너뜁니다.",
            )
            return

        try:
            provider = get_real_provider()
        except NotImplementedError:
            logger.warning(
                "리콜/판매중지 확인 Job — ACTIVE 상태이지만 실제 "
                "Provider가 아직 선정되지 않아 이번 실행은 건너뜁니다.",
            )
            return

        result = service.run_daily_check(provider)
        logger.info(
            "리콜/판매중지 확인 Job 완료: status=%s found=%s new=%s",
            result.status, result.notices_found_count,
            result.new_notices_count,
        )
    except Exception:  # noqa: BLE001 — 한 번의 Job 실패가 스케줄러를 죽이지 않는다
        logger.exception("리콜/판매중지 확인 Job에서 예외 발생")
    finally:
        db.close()


def register_all_jobs() -> None:
    """
    2026-09-10 Phase 6 — 현재 등록하는 Job은 백업 복구 리허설
    하나뿐이다(사유는 모듈 docstring 참고). 매주 일요일
    04:00(Asia/Seoul)에 실행한다 — 문서에 구체적 시각 지정은 없고,
    사용자 활동이 적을 새벽 시간대를 고른 기술 결정이다.

    실제 homez.db 경로는 `get_homez_db_path(confirm=True)`로만
    얻는다 — Desktop 공식 부팅 흐름에서 호출되는 것이 이 함수의
    유일한 용도이므로 confirm=True가 정당하다(app/desktop/paths.py
    문서 참고). 이 함수 자체는 어떤 DB도 읽거나 쓰지 않는다 — Job을
    "등록"만 할 뿐, 실제 실행은 스케줄러가 트리거할 때(또는
    call_now=True로 즉시 호출할 때)에만 일어난다.
    """

    from app.desktop.paths import get_backups_dir
    from app.desktop.paths import get_homez_db_path

    source_db_path = get_homez_db_path(confirm=True)
    backups_dir = get_backups_dir()
    rehearsal_dir = backups_dir / "rehearsal"

    def _job() -> None:
        run_backup_rehearsal_job(
            source_db_path=source_db_path,
            backups_dir=backups_dir,
            rehearsal_dir=rehearsal_dir,
        )

    SchedulerService.add_cron_job(
        _job,
        day_of_week="sun", hour=4, minute=0,
        job_id=BACKUP_REHEARSAL_JOB_ID,
    )

    # 2026-09-15 전면 감사 후속(Phase 9I, 10-17) — 매일
    # 05:00(Asia/Seoul) 리콜/판매중지 확인. 등록은 항상 하지만
    # 실행되는지 여부는 run_recall_notice_check_job() 내부의 모드
    # 검사(기본 PAUSED)가 결정한다 — "Job을 만들지 않음"과 "Job은
    # 있지만 기본 비활성"은 서로 다르다(전자는 사용자가 켤 방법 자체가
    # 없다).
    SchedulerService.add_cron_job(
        run_recall_notice_check_job,
        day_of_week="*", hour=5, minute=0,
        job_id=RECALL_NOTICE_CHECK_JOB_ID,
    )

    logger.info(
        "Scheduler jobs registered: %s, %s",
        BACKUP_REHEARSAL_JOB_ID, RECALL_NOTICE_CHECK_JOB_ID,
    )


__all__ = [
    "BACKUP_REHEARSAL_JOB_ID",
    "RECALL_NOTICE_CHECK_JOB_ID",
    "run_backup_rehearsal_job",
    "run_recall_notice_check_job",
    "register_all_jobs",
]
