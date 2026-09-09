"""
=========================================================
Homez OS

File : app/domains/media_asset/repository.py

no-commit Repository — 커밋/롤백은 항상 호출하는 Service의 책임이다
(app/domains/marketplace_listing/repository.py와 동일 패턴). 단건
조회·조건부 UPDATE는 전부 company_id를 WHERE에 포함한다(예외 없음
— 이 도메인에 전역 테이블이 없다).
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset


class MediaAssetRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------------------------
    # MediaAsset
    # ------------------------------------------------

    def add_media_asset_no_commit(self, asset: MediaAsset) -> MediaAsset:

        self.db.add(asset)
        self.db.flush()

        return asset

    def get_media_asset_for_company(
        self, asset_id: int, company_id: int,
    ) -> MediaAsset | None:

        return (
            self.db.query(MediaAsset)
            .filter(MediaAsset.id == asset_id)
            .filter(MediaAsset.company_id == company_id)
            .first()
        )

    def get_media_asset_by_company_storage_path(
        self, company_id: int, storage_path: str,
    ) -> MediaAsset | None:

        return (
            self.db.query(MediaAsset)
            .filter(MediaAsset.company_id == company_id)
            .filter(MediaAsset.storage_path == storage_path)
            .first()
        )

    def list_active_media_assets_for_owner(
        self, company_id: int, owner_type: str, owner_id: int,
    ) -> list[MediaAsset]:

        return (
            self.db.query(MediaAsset)
            .filter(MediaAsset.company_id == company_id)
            .filter(MediaAsset.owner_type == owner_type)
            .filter(MediaAsset.owner_id == owner_id)
            .filter(MediaAsset.status == "ACTIVE")
            .order_by(MediaAsset.display_order, MediaAsset.id)
            .all()
        )

    def count_active_media_assets_for_owner(
        self, company_id: int, owner_type: str, owner_id: int,
    ) -> int:

        return (
            self.db.query(MediaAsset)
            .filter(MediaAsset.company_id == company_id)
            .filter(MediaAsset.owner_type == owner_type)
            .filter(MediaAsset.owner_id == owner_id)
            .filter(MediaAsset.status == "ACTIVE")
            .count()
        )

    def update_rights_status_conditional(
        self, asset_id: int, company_id: int, new_status: str,
    ) -> int:

        stmt = (
            update(MediaAsset)
            .where(MediaAsset.id == asset_id)
            .where(MediaAsset.company_id == company_id)
            .values(rights_status=new_status)
        )

        return self.db.execute(stmt).rowcount

    def update_media_asset_status_conditional(
        self,
        asset_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
    ) -> int:

        stmt = (
            update(MediaAsset)
            .where(MediaAsset.id == asset_id)
            .where(MediaAsset.company_id == company_id)
            .where(MediaAsset.status.in_(expected_statuses))
            .values(status=new_status)
        )

        return self.db.execute(stmt).rowcount

    def list_orphan_candidate_assets(
        self, company_id: int, active_owner_ids: dict[str, set[int]],
    ) -> list[MediaAsset]:
        """
        owner_type→살아있는 owner_id 집합에 없는 GENERATED/CHANNEL_VARIANT
        ACTIVE 자산을 고아 후보로 반환한다(실제 삭제는 하지 않음 —
        service가 이 목록을 보고 ORPHANED로 조건부 전이만 시킨다).
        ORIGINAL은 절대 고아로 취급하지 않는다(원본 불변 원칙).
        """

        candidates = (
            self.db.query(MediaAsset)
            .filter(MediaAsset.company_id == company_id)
            .filter(MediaAsset.status == "ACTIVE")
            .filter(MediaAsset.asset_role != "ORIGINAL")
            .all()
        )

        return [
            asset
            for asset in candidates
            if asset.owner_id
            not in active_owner_ids.get(asset.owner_type, set())
        ]

    # ------------------------------------------------
    # ImageGenerationJob
    # ------------------------------------------------

    def add_job_no_commit(self, job: ImageGenerationJob) -> ImageGenerationJob:

        self.db.add(job)
        self.db.flush()

        return job

    def get_job_for_company(
        self, job_id: int, company_id: int,
    ) -> ImageGenerationJob | None:

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.id == job_id)
            .filter(ImageGenerationJob.company_id == company_id)
            .first()
        )

    def get_job_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> ImageGenerationJob | None:

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == company_id)
            .filter(ImageGenerationJob.idempotency_key == idempotency_key)
            .first()
        )

    def list_jobs_for_package(
        self, listing_package_id: int, company_id: int,
    ) -> list[ImageGenerationJob]:

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == company_id)
            .filter(
                ImageGenerationJob.listing_package_id == listing_package_id,
            )
            .order_by(ImageGenerationJob.id)
            .all()
        )

    def count_jobs_for_package(
        self, listing_package_id: int, company_id: int,
    ) -> int:

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == company_id)
            .filter(
                ImageGenerationJob.listing_package_id == listing_package_id,
            )
            .count()
        )

    def update_job_fields_conditional(
        self,
        job_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        values: dict,
    ) -> int:

        stmt = (
            update(ImageGenerationJob)
            .where(ImageGenerationJob.id == job_id)
            .where(ImageGenerationJob.company_id == company_id)
            .where(ImageGenerationJob.status.in_(expected_statuses))
            .values(**values)
        )

        return self.db.execute(stmt).rowcount

    def list_stalled_running_jobs_for_company(
        self, company_id: int, older_than: datetime,
    ) -> list[ImageGenerationJob]:

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == company_id)
            .filter(ImageGenerationJob.status == "RUNNING")
            .filter(ImageGenerationJob.started_at.isnot(None))
            .filter(ImageGenerationJob.started_at < older_than)
            .all()
        )

    def list_stalled_pending_jobs_for_company(
        self, company_id: int, older_than: datetime,
    ) -> list[ImageGenerationJob]:
        """
        PENDING인 채로 오래 멈춘 Job — submit_job()/retry_job()이
        Job 행을 PENDING으로 commit한 직후, _execute_job() 호출 전에
        프로세스가 죽으면 이 상태로 남는다(외부 Provider 호출은 아직
        시작되지 않았으므로 그대로 재개해도 안전하다).
        """

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == company_id)
            .filter(ImageGenerationJob.status == "PENDING")
            .filter(ImageGenerationJob.created_at < older_than)
            .all()
        )

    def list_stalled_running_jobs_all_companies(
        self, older_than: datetime,
    ) -> list[ImageGenerationJob]:
        """
        2026-08-02 Gate R5 — Worker 시작 시 전체 회사 대상 일괄 복구용
        (list_stalled_running_jobs_for_company의 company 무관 버전).
        """

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.status == "RUNNING")
            .filter(ImageGenerationJob.started_at.isnot(None))
            .filter(ImageGenerationJob.started_at < older_than)
            .all()
        )

    def get_oldest_pending_job_any_company(self) -> ImageGenerationJob | None:
        """
        2026-08-02 Gate R5 — 백그라운드 Worker가 다음에 처리할 PENDING
        Job 하나를 고른다(가장 오래된 것부터, 회사 무관 — Worker는
        단일 프로세스 전체의 공용 처리기다). 실제 claim(경쟁 방지)은
        이 메서드가 아니라 `ImageGenerationJobQueueService._execute_job()`
        내부의 조건부 UPDATE(PENDING→RUNNING, rowcount==1만 성공)가
        한다 — 이 조회는 단순히 "다음으로 시도해볼 후보"를 고를 뿐이며,
        같은 후보를 여러 Worker가 동시에 골라도 실제로 실행되는 것은
        정확히 하나뿐이다.
        """

        return (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.status == "PENDING")
            .order_by(
                ImageGenerationJob.created_at.asc(), ImageGenerationJob.id.asc(),
            )
            .first()
        )

    # ------------------------------------------------
    # ImageGenerationResult (append-only)
    # ------------------------------------------------

    def add_result_no_commit(
        self, result: ImageGenerationResult,
    ) -> ImageGenerationResult:

        self.db.add(result)
        self.db.flush()

        return result

    def list_results_for_job(
        self, job_id: int, company_id: int,
    ) -> list[ImageGenerationResult]:

        return (
            self.db.query(ImageGenerationResult)
            .filter(ImageGenerationResult.company_id == company_id)
            .filter(ImageGenerationResult.job_id == job_id)
            .order_by(ImageGenerationResult.sequence_index)
            .all()
        )

    # ------------------------------------------------
    # ImageGenerationDailyUsage — 원자적 카운터
    # ------------------------------------------------

    def get_daily_usage(
        self, company_id: int, usage_date: str,
    ) -> ImageGenerationDailyUsage | None:

        return (
            self.db.query(ImageGenerationDailyUsage)
            .filter(ImageGenerationDailyUsage.company_id == company_id)
            .filter(ImageGenerationDailyUsage.usage_date == usage_date)
            .first()
        )

    def add_daily_usage_no_commit(
        self, usage: ImageGenerationDailyUsage,
    ) -> ImageGenerationDailyUsage:

        self.db.add(usage)
        self.db.flush()

        return usage

    def reserve_daily_usage_conditional(
        self,
        company_id: int,
        usage_date: str,
        add_count: int,
        add_cost: float,
        count_limit: int,
        cost_limit: float,
    ) -> int:
        """
        조건부 원자적 증가 — 증가 후에도 두 한도(개수·비용)를 전부
        지킬 때만 실제로 반영된다(app/domains/automation_safety::
        ExecutionPeriodUsage와 동일 패턴). rowcount==0이면 한도 초과로
        예약 실패.
        """

        stmt = (
            update(ImageGenerationDailyUsage)
            .where(ImageGenerationDailyUsage.company_id == company_id)
            .where(ImageGenerationDailyUsage.usage_date == usage_date)
            .where(
                ImageGenerationDailyUsage.consumed_image_count + add_count
                <= count_limit,
            )
            .where(
                ImageGenerationDailyUsage.consumed_cost + add_cost
                <= cost_limit,
            )
            .values(
                consumed_image_count=(
                    ImageGenerationDailyUsage.consumed_image_count + add_count
                ),
                consumed_cost=(
                    ImageGenerationDailyUsage.consumed_cost + add_cost
                ),
                updated_at=datetime.utcnow(),
            )
        )

        return self.db.execute(stmt).rowcount


__all__ = [
    "MediaAssetRepository",
]
