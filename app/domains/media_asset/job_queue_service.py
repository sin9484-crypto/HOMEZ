"""
=========================================================
Homez OS

File : app/domains/media_asset/job_queue_service.py

이미지 생성 Job Queue — 2026-08-02 CTO 보안 보완 Gate R5부터
`submit_job()`/`retry_job()`은 PENDING 행을 커밋한 뒤 **곧바로
반환한다**(더 이상 같은 요청 스레드에서 동기 실행하지 않는다).
실제 실행은 `app/domains/media_asset/worker.py::
ImageGenerationJobWorker`가 별도 백그라운드 스레드에서 이 서비스의
`_execute_job()`을 폴링 호출해 처리한다 — Worker는 새로운 claim
로직을 만들지 않고, `_execute_job()`이 원래 갖고 있던 조건부 UPDATE
(PENDING→RUNNING, rowcount==1만 성공)를 그대로 재사용한다. 상태 기계
자체는 PENDING→RUNNING→(SUCCEEDED|PARTIAL|FAILED) 전이를 각각 독립된
Transaction으로 커밋한다(외부 Provider 호출 도중에는 열린
Transaction을 유지하지 않는다 — RUNNING 전이를 커밋한 뒤에 Provider를
부르고, 그 결과를 받은 다음에 새 Transaction으로 결과를 기록한다).

recover_stalled_jobs()(company 단위)/recover_all_companies_stalled_
running_jobs()(전체 회사, Worker 시작 시 호출)는 "앱 재시작 후
미완료 작업 복구"를 처리한다 — 실제 Worker가 죽으면 RUNNING인 채로
영원히 멈춘 행이 생기는데, started_at 기준 타임아웃을 넘은 RUNNING
행을 조건부 UPDATE로 FAILED(TIMEOUT_RECOVERED)로 되돌린다. PENDING은
Worker가 있는 한 별도 "정지" 판정이 필요 없다 — 폴링 루프가 그대로
claim하면 된다.

일일 비용/개수 한도는 app/domains/automation_safety의 원자적 카운터
패턴을 그대로 복제한다(레코드 예약 → 조건부 UPDATE, 초과 시 rowcount
0 → Provider를 아예 호출하지 않고 FAILED 처리) — 실제 비용이 예약한
추정치와 다를 수 있으나, 이번 Phase는 추정치를 그대로 사용한다(정산
로직은 범위 밖, 최종 보고에 명시한다).
=========================================================
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.domains.media_asset.constants import ImageJobStatus
from app.domains.media_asset.constants import ImageResultStatus
from app.domains.media_asset.constants import ImageSafetyCheckStatus
from app.domains.media_asset.constants import DAILY_IMAGE_COST_LIMIT_PER_COMPANY
from app.domains.media_asset.constants import (
    DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY,
)
from app.domains.media_asset.constants import JOB_STALL_TIMEOUT_SECONDS
from app.domains.media_asset.constants import (
    MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE,
)
from app.domains.media_asset.constants import MAX_IMAGES_PER_JOB
from app.domains.media_asset.constants import MAX_REGENERATIONS_PER_PACKAGE
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetRole
from app.domains.media_asset.fingerprint import canonical_json
from app.domains.media_asset.fingerprint import sha256_hex
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import compose_with_background
from app.domains.media_asset.detail_page_generator import DetailPageContent
from app.domains.media_asset.detail_page_generator import (
    DetailPageFontUnavailableError,
)
from app.domains.media_asset.detail_page_generator import FeatureHighlight
from app.domains.media_asset.detail_page_generator import SpecRow
from app.domains.media_asset.detail_page_generator import (
    generate_detail_page_image,
)
from app.domains.media_asset.image_processing_providers import (
    BackgroundRemovalProviderError,
)
from app.domains.media_asset.image_processing_providers import (
    BackgroundRemovalRequest,
)
from app.domains.media_asset.image_processing_providers import (
    get_background_removal_provider,
)
from app.domains.media_asset.image_split import split_tall_image
from app.domains.media_asset.image_validation import build_storage_path
from app.domains.media_asset.image_validation import validate_image_bytes
from app.domains.media_asset.image_validation import (
    validate_long_detail_image_bytes,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.providers import ImageGenerationProviderError
from app.domains.media_asset.providers import ImageGenerationRequest
from app.domains.media_asset.providers import ImageGenerationRequestItem
from app.domains.media_asset.providers import get_provider
from app.domains.media_asset.repository import MediaAssetRepository
from app.domains.media_asset.schema import ImageGenerationJobSubmitRequest
from app.domains.media_asset.storage import read_media_file
from app.domains.media_asset.storage import write_media_file

_ESTIMATED_COST_PER_IMAGE = 0.01
_KST = timezone(timedelta(hours=9))

_JOB_NOT_FOUND = "ImageGenerationJob을 찾을 수 없습니다."


def _today_kst(now: datetime | None = None) -> str:

    now = now or datetime.utcnow()

    return (now.replace(tzinfo=timezone.utc)).astimezone(_KST).strftime("%Y-%m-%d")


class ImageGenerationJobQueueService:

    def __init__(
        self, db: Session, media_root: Path | None = None,
        on_job_completed: Callable[[ImageGenerationJob], None] | None = None,
    ):
        """
        `on_job_completed`: 2026-08-02 Gate R5 — 실행이 더 이상 요청
        스레드에서 동기로 일어나지 않으므로(별도 Worker가 처리), Job이
        listing_package_id를 갖고 있을 때 그 패키지의 fingerprint를
        다시 계산해야 할 시점(이미지가 실제로 준비된 시점)을 이
        서비스가 직접 알 방법이 없다 — 그 시점을 호출자(주로
        ListingPackageService)에게 콜백으로 알려준다. `_execute_job()`
        이 최종 상태(SUCCEEDED/PARTIAL/FAILED)를 커밋한 직후에만
        호출되며, listing_package_id가 없는 Job(예: 미리보기 생성)에는
        호출되지 않는다.
        """

        self.db = db
        self.repository = MediaAssetRepository(db)
        self._on_job_completed = on_job_completed

        if media_root is None:
            from app.desktop.paths import get_media_dir

            media_root = get_media_dir()

        self.media_root = media_root

    # ------------------------------------------------
    # 제출
    # ------------------------------------------------

    def submit_job(
        self, data: ImageGenerationJobSubmitRequest, requested_by: int,
        company_id: int,
    ) -> tuple[ImageGenerationJob, bool]:

        # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        # 강제(IMAGE_PROCESSING). 이 메서드가 Job을 실제로 큐에 넣는
        # 유일한 진입점이다 — 미등록·비활성 capability는 Job 자체가
        # 생성되지 않는다(fail-closed).
        require_active_capability(CapabilityCode.IMAGE_PROCESSING)

        # 2026-08-02 CTO 보안 보완 Gate R3: 요청 fingerprint를 existing
        # 조회보다 먼저 계산한다 — 같은 (company_id, idempotency_key)를
        # 다른 payload로 재사용하는 경우를 구분하지 못하고 그냥 기존
        # Job을 반환해버리던 결함을 막는다. fingerprint 입력에는 생성
        # 결과에 영향을 주는 모든 필드(listing_package_id/provider_code/
        # model_name 포함, 순서가 결과에 영향을 주는 items도 순서
        # 그대로)를 포함한다.
        request_payload = {
            "product_candidate_id": data.product_candidate_id,
            "listing_package_id": data.listing_package_id,
            "provider_code": data.provider_code,
            "model_name": data.model_name,
            "items": [item.purpose for item in data.items],
            "style_params": data.style_params,
        }
        request_fingerprint = sha256_hex(canonical_json(request_payload))

        existing = self.repository.get_job_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            if existing.prompt_fingerprint == request_fingerprint:
                return existing, True

            raise ConflictException(
                "같은 idempotency_key가 이미 다른 요청 내용으로 사용 "
                "중입니다 — 같은 key로는 동일한 요청만 재제출할 수 "
                "있습니다.",
            )

        if len(data.items) > MAX_IMAGES_PER_JOB:
            raise BadRequestException(
                f"한 Job당 요청 가능한 이미지 수는 최대 {MAX_IMAGES_PER_JOB}장입니다.",
            )

        if data.listing_package_id is not None:
            job_count = self.repository.count_jobs_for_package(
                data.listing_package_id, company_id,
            )
            if job_count >= MAX_REGENERATIONS_PER_PACKAGE:
                raise BadRequestException(
                    "이 상품 패키지의 이미지 재생성 횟수 한도"
                    f"({MAX_REGENERATIONS_PER_PACKAGE}회)를 초과했습니다.",
                )

        active_count = self.repository.count_active_media_assets_for_owner(
            company_id, MediaAssetOwnerType.PRODUCT_CANDIDATE,
            data.product_candidate_id,
        )
        if active_count + len(data.items) > MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE:
            raise BadRequestException(
                "이 상품에 대해 활성 이미지 수 한도"
                f"({MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE}장)를 초과합니다.",
            )

        job = ImageGenerationJob(
            company_id=company_id,
            listing_package_id=data.listing_package_id,
            product_candidate_id=data.product_candidate_id,
            provider_code=data.provider_code,
            model_name=data.model_name,
            prompt_fingerprint=request_fingerprint,
            request_payload_json=canonical_json(request_payload),
            status=ImageJobStatus.PENDING,
            idempotency_key=data.idempotency_key,
            requested_by=requested_by,
        )

        try:
            job = self.repository.add_job_no_commit(job)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            # 동시 요청 경쟁의 패자 — 승자가 커밋한 행을 그대로 신뢰하지
            # 않고, 승자도 같은 요청 내용이었는지 fingerprint로 다시
            # 확인한다(경쟁자가 다른 payload로 같은 key를 썼을 수 있음).
            winner = self.repository.get_job_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            if winner.prompt_fingerprint != request_fingerprint:
                raise ConflictException(
                    "같은 idempotency_key가 이미 다른 요청 내용으로 사용 "
                    "중입니다 — 같은 key로는 동일한 요청만 재제출할 수 "
                    "있습니다.",
                )

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        # 2026-08-02 Gate R5: PENDING 저장 후 즉시 반환한다 — 실행은
        # 같은 요청 스레드가 아니라 별도 백그라운드 Worker
        # (app/domains/media_asset/worker.py::ImageGenerationJobWorker)가
        # 폴링해 처리한다. `_execute_job()` 자체는 그대로 남아있고
        # Worker가 그것을 호출한다(재시작 후 PENDING 복구용
        # recover_stalled_jobs()에서도 동일하게 재사용).
        return job, False

    # ------------------------------------------------
    # 실행
    # ------------------------------------------------

    def _execute_job(self, job_id: int, company_id: int) -> None:

        job = self.repository.get_job_for_company(job_id, company_id)
        if job is None or job.status != ImageJobStatus.PENDING:
            return

        now = datetime.utcnow()
        usage_date = _today_kst(now)
        item_count = len(_parse_items(job.request_payload_json))
        estimated_cost = _ESTIMATED_COST_PER_IMAGE * item_count

        self._ensure_daily_usage_row(company_id, usage_date)

        reserved = self.repository.reserve_daily_usage_conditional(
            company_id, usage_date, item_count, estimated_cost,
            DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY,
            DAILY_IMAGE_COST_LIMIT_PER_COMPANY,
        )
        if reserved != 1:
            self.repository.update_job_fields_conditional(
                job_id, company_id, (ImageJobStatus.PENDING,),
                {
                    "status": ImageJobStatus.FAILED,
                    "error_reason": "DAILY_BUDGET_EXCEEDED: 일일 이미지 "
                    "생성 개수 또는 비용 한도를 초과했습니다.",
                    "completed_at": now,
                },
            )
            self.db.commit()
            self._notify_job_completed(job_id, company_id)

            return

        rowcount = self.repository.update_job_fields_conditional(
            job_id, company_id, (ImageJobStatus.PENDING,),
            {
                "status": ImageJobStatus.RUNNING,
                "started_at": now,
                "estimated_cost": estimated_cost,
            },
        )
        if rowcount != 1:
            self.db.rollback()

            return

        self.db.commit()

        # ---- 여기서부터 Provider 호출(열린 Transaction 없음) ----

        try:
            provider = get_provider(job.provider_code)
            request = ImageGenerationRequest(
                company_id=company_id,
                product_candidate_id=job.product_candidate_id,
                prompt_fingerprint=job.prompt_fingerprint,
                style_params=_parse_style_params(job.request_payload_json),
                items=tuple(
                    ImageGenerationRequestItem(
                        purpose=purpose, sequence_index=index,
                    )
                    for index, purpose in enumerate(
                        _parse_items(job.request_payload_json),
                    )
                ),
            )
            provider_result = provider.generate(request)

        except ImageGenerationProviderError as exc:
            self.repository.update_job_fields_conditional(
                job_id, company_id, (ImageJobStatus.RUNNING,),
                {
                    "status": ImageJobStatus.FAILED,
                    "error_reason": str(exc),
                    "completed_at": datetime.utcnow(),
                },
            )
            self.db.commit()
            self._notify_job_completed(job_id, company_id)

            return

        except Exception as exc:  # noqa: BLE001
            self.repository.update_job_fields_conditional(
                job_id, company_id, (ImageJobStatus.RUNNING,),
                {
                    "status": ImageJobStatus.FAILED,
                    "error_reason": f"Provider 예외: {type(exc).__name__}",
                    "completed_at": datetime.utcnow(),
                },
            )
            self.db.commit()
            self._notify_job_completed(job_id, company_id)

            return

        # ---- 결과 기록(새 Transaction) ----

        succeeded = 0
        failed = 0

        try:
            for item in provider_result.items:
                if item.status != ImageResultStatus.SUCCEEDED:
                    self.repository.add_result_no_commit(ImageGenerationResult(
                        company_id=company_id,
                        job_id=job_id,
                        media_asset_id=None,
                        sequence_index=item.sequence_index,
                        purpose=item.purpose,
                        status=ImageResultStatus.FAILED,
                        safety_check_status=item.safety_check_status,
                        error_reason=item.error_reason or "알 수 없는 실패",
                    ))
                    failed += 1

                    continue

                media_asset = self._persist_generated_asset(
                    company_id=company_id,
                    owner_type=(
                        MediaAssetOwnerType.LISTING_PACKAGE
                        if job.listing_package_id is not None
                        else MediaAssetOwnerType.PRODUCT_CANDIDATE
                    ),
                    owner_id=(
                        job.listing_package_id
                        if job.listing_package_id is not None
                        else job.product_candidate_id
                    ),
                    purpose=item.purpose,
                    display_order=item.sequence_index,
                    image_bytes=item.image_bytes,
                    self_reported_mime_type=item.mime_type,
                )

                self.repository.add_result_no_commit(ImageGenerationResult(
                    company_id=company_id,
                    job_id=job_id,
                    media_asset_id=media_asset.id,
                    sequence_index=item.sequence_index,
                    purpose=item.purpose,
                    status=ImageResultStatus.SUCCEEDED,
                    safety_check_status=item.safety_check_status,
                ))
                succeeded += 1

            if succeeded > 0 and failed == 0:
                final_status = ImageJobStatus.SUCCEEDED
            elif succeeded > 0 and failed > 0:
                final_status = ImageJobStatus.PARTIAL
            else:
                final_status = ImageJobStatus.FAILED

            self.repository.update_job_fields_conditional(
                job_id, company_id, (ImageJobStatus.RUNNING,),
                {
                    "status": final_status,
                    "progress_percent": 100,
                    "actual_cost": provider_result.provider_cost,
                    "completed_at": datetime.utcnow(),
                },
            )
            self.db.commit()
            self._notify_job_completed(job_id, company_id)

        except Exception:
            self.db.rollback()
            raise

    def _notify_job_completed(self, job_id: int, company_id: int) -> None:
        """
        2026-08-02 Gate R5 — `on_job_completed` 콜백을 안전하게 호출한다.
        콜백 자체의 실패(예: ListingPackage fingerprint 재계산 중 예외)가
        이미 커밋된 Job의 최종 상태를 되돌리거나 Worker 루프를 죽여서는
        안 되므로, 여기서 예외를 잡고 최소한만 기록한다(로깅은 Worker의
        예외 격리와 동일한 원칙 — 이 서비스는 로거를 직접 갖지 않으므로
        `print` 대신 조용히 흡수하지 않고 그대로 재raise하지도 않는
        중간 지점으로, 실제 배포에서는 호출자가 필요하면 자체 로깅을
        콜백 안에서 하면 된다).
        """

        if self._on_job_completed is None:
            return

        job = self.repository.get_job_for_company(job_id, company_id)
        if job is None or job.listing_package_id is None:
            return

        try:
            self._on_job_completed(job)
        except Exception:  # noqa: BLE001
            # 콜백 실패는 Job 자체의 완료 상태에 영향을 주지 않는다 —
            # 이미 위에서 commit된 상태 그대로 유지한다.
            pass

    def _persist_generated_asset(
        self, company_id: int, owner_type: str, owner_id: int,
        purpose: str, display_order: int, image_bytes: bytes,
        self_reported_mime_type: str | None,
    ) -> MediaAsset:

        validated = validate_image_bytes(image_bytes)
        digest = hashlib.sha256(image_bytes).hexdigest()
        storage_path = build_storage_path(
            company_id, digest, validated.mime_type,
        )

        existing = self.repository.get_media_asset_by_company_storage_path(
            company_id, storage_path,
        )
        if existing is not None:
            return existing

        write_media_file(self.media_root, storage_path, image_bytes)

        asset = MediaAsset(
            company_id=company_id,
            owner_type=owner_type,
            owner_id=owner_id,
            asset_role=MediaAssetRole.GENERATED,
            source_asset_id=None,
            channel_code=None,
            purpose=purpose,
            display_order=display_order,
            storage_path=storage_path,
            original_filename=None,
            mime_type=validated.mime_type,
            file_size_bytes=len(image_bytes),
            sha256_hex=digest,
            width=validated.width,
            height=validated.height,
            status="ACTIVE",
        )

        try:
            asset = self.repository.add_media_asset_no_commit(asset)

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_media_asset_by_company_storage_path(
                company_id, storage_path,
            )
            if winner is None:
                raise

            return winner

        return asset

    def _ensure_daily_usage_row(self, company_id: int, usage_date: str) -> None:

        existing = self.repository.get_daily_usage(company_id, usage_date)
        if existing is not None:
            return

        try:
            self.repository.add_daily_usage_no_commit(ImageGenerationDailyUsage(
                company_id=company_id, usage_date=usage_date,
            ))
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

        except Exception:
            self.db.rollback()
            raise

    # ------------------------------------------------
    # 업로드 (사용자가 직접 보유한 실제 제품 이미지)
    # ------------------------------------------------

    def upload_original_asset(
        self, owner_type: str, owner_id: int, purpose: str,
        display_order: int, image_bytes: bytes,
        original_filename: str | None, company_id: int,
    ) -> MediaAsset:
        """
        AI 생성이 아니라 사용자가 직접 올린 원본 이미지를 저장한다.
        실제 브랜드 제품(예: 샤프란)은 AI로 용기·라벨을 다시 그릴 수
        없으므로(요구사항), 이 경로가 유일한 정식 등록 이미지 확보
        수단이다 — 지금까지 이 메서드가 없어서 "선택 가능한 이미지가
        없음" 결함이 실제로 발생했다(2026-08-19 실제 샤프란 제품
        테스트에서 재현·확인).

        `_persist_generated_asset()`와 동일한 검증·저장 파이프라인을
        재사용하되, asset_role은 항상 ORIGINAL이고 source_asset_id는
        항상 None(원본에는 계보가 없다).
        """

        if owner_type not in MediaAssetOwnerType.ALL:
            raise BadRequestException(f"알 수 없는 owner_type입니다: {owner_type}")

        active_count = self.repository.count_active_media_assets_for_owner(
            company_id, owner_type, owner_id,
        )
        if active_count + 1 > MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE:
            raise BadRequestException(
                "이 상품에 대해 활성 이미지 수 한도"
                f"({MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE}장)를 초과합니다.",
            )

        validated = validate_image_bytes(image_bytes)
        digest = hashlib.sha256(image_bytes).hexdigest()
        storage_path = build_storage_path(
            company_id, digest, validated.mime_type,
        )

        existing = self.repository.get_media_asset_by_company_storage_path(
            company_id, storage_path,
        )
        if existing is not None:
            return existing

        write_media_file(self.media_root, storage_path, image_bytes)

        asset = MediaAsset(
            company_id=company_id,
            owner_type=owner_type,
            owner_id=owner_id,
            asset_role=MediaAssetRole.ORIGINAL,
            source_asset_id=None,
            channel_code=None,
            purpose=purpose,
            display_order=display_order,
            storage_path=storage_path,
            original_filename=original_filename,
            mime_type=validated.mime_type,
            file_size_bytes=len(image_bytes),
            sha256_hex=digest,
            width=validated.width,
            height=validated.height,
            status="ACTIVE",
        )

        try:
            asset = self.repository.add_media_asset_no_commit(asset)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_media_asset_by_company_storage_path(
                company_id, storage_path,
            )
            if winner is None:
                raise

            return winner

        except Exception:
            self.db.rollback()
            raise

        return asset

    # ------------------------------------------------
    # 배경 제거(누끼) / 배경 합성
    # ------------------------------------------------
    #
    # 별도 Job Queue 테이블을 새로 만들지 않는다(신규 Migration
    # 금지, 2026-08-20 지시) — 기존 ImageGenerationJob/Result를
    # 재사용해 provider_code/model_name/request_payload_json에 처리
    # Provider·버전·원본 Asset id를 기록한다. 처리 자체는 동기(즉시
    # 완료)로 수행하고 Job 행은 완료 상태로 곧바로 커밋한다 — AI
    # 생성처럼 오래 걸리는 외부 호출이 아니라 로컬 처리이므로
    # 백그라운드 Worker를 거칠 필요가 없다.

    def remove_background(
        self, source_asset_id: int, provider_code: str,
        requested_by: int, company_id: int,
    ) -> MediaAsset:
        """원본(ORIGINAL) 이미지에서 배경을 제거한 파생 자산(GENERATED)을
        만든다. 실패하면 원본은 그대로 두고(이 메서드는 원본 행을
        절대 수정하지 않는다) 예외를 던진다."""

        source = self.repository.get_media_asset_for_company(
            source_asset_id, company_id,
        )
        if source is None:
            raise NotFoundException("원본 이미지를 찾을 수 없습니다.")

        source_bytes = read_media_file(self.media_root, source.storage_path)

        provider = get_background_removal_provider(provider_code)
        request = BackgroundRemovalRequest(
            company_id=company_id,
            source_image_bytes=source_bytes,
            source_mime_type=source.mime_type,
        )

        job = self._record_processing_job(
            operation="BACKGROUND_REMOVAL",
            provider_code=provider_code,
            model_name=None,
            source_asset_id=source.id,
            requested_by=requested_by,
            company_id=company_id,
            product_candidate_id=(
                source.owner_id
                if source.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE
                else 0
            ),
        )

        try:
            result = provider.remove_background(request)
        except BackgroundRemovalProviderError as exc:
            self._finish_processing_job(job.id, company_id, success=False, error_reason=str(exc))
            raise BadRequestException(str(exc)) from exc

        if result.status != "SUCCEEDED" or result.image_bytes is None:
            self._finish_processing_job(
                job.id, company_id, success=False,
                error_reason=result.error_reason or "알 수 없는 실패",
            )
            raise BadRequestException(
                result.error_reason or "배경 제거에 실패했습니다(원본은 그대로 유지됩니다).",
            )

        asset = self._persist_generated_asset(
            company_id=company_id,
            owner_type=source.owner_type,
            owner_id=source.owner_id,
            purpose=source.purpose,
            display_order=source.display_order,
            image_bytes=result.image_bytes,
            self_reported_mime_type="image/png",
        )
        asset.source_asset_id = source.id
        asset.rights_status = source.rights_status
        self.db.commit()

        self._finish_processing_job(
            job.id, company_id, success=True,
            model_version=result.model_version, media_asset_id=asset.id,
        )

        return asset

    def save_manual_edit(
        self, source_asset_id: int, image_bytes: bytes,
        requested_by: int, company_id: int,
    ) -> MediaAsset:
        """
        2026-08-29 Phase 5(Fabric.js 수동 편집기 MVP) — 브라우저
        캔버스(자르기/회전/밝기·대비/도형·화살표/되돌리기)에서 편집이
        전부 끝난 최종 결과 이미지 1장만 서버로 받는다. 편집 로직
        자체는 서버에 없다 — remove_background()와 동일한 패턴으로
        원본(source)은 절대 수정하지 않고 새 파생 자산(GENERATED)을
        만든다. 외부 Provider를 호출하지 않으므로 provider_code는
        "LOCAL_MANUAL_EDIT"로 고정한다(운영 비용·호출 로그와 구분).
        """

        source = self.repository.get_media_asset_for_company(
            source_asset_id, company_id,
        )
        if source is None:
            raise NotFoundException("원본 이미지를 찾을 수 없습니다.")

        job = self._record_processing_job(
            operation="MANUAL_EDIT",
            provider_code="LOCAL_MANUAL_EDIT",
            model_name=None,
            source_asset_id=source.id,
            requested_by=requested_by,
            company_id=company_id,
            product_candidate_id=(
                source.owner_id
                if source.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE
                else 0
            ),
        )

        try:
            asset = self._persist_generated_asset(
                company_id=company_id,
                owner_type=source.owner_type,
                owner_id=source.owner_id,
                purpose=source.purpose,
                display_order=source.display_order,
                image_bytes=image_bytes,
                self_reported_mime_type=None,
            )
        except BadRequestException as exc:
            self._finish_processing_job(
                job.id, company_id, success=False, error_reason=str(exc),
            )
            raise

        asset.source_asset_id = source.id
        asset.rights_status = source.rights_status
        self.db.commit()

        self._finish_processing_job(
            job.id, company_id, success=True, media_asset_id=asset.id,
        )

        return asset

    def confirm_rights_verified(
        self, asset_id: int, company_id: int, basis: str, confirmed_by: int,
    ) -> MediaAsset:
        """사용자가 사용권을 직접 확인했다는 명시적 조치(2026-08-20
        3차 지시) — 근거 유형(RightsVerificationBasis) 명시가 필수다.
        확인 방식·시각·사용자·근거 유형만 감사로그에 남기고, 이미지
        원문·Credential·개인정보는 절대 남기지 않는다. 파생 자산까지
        일괄 갱신하지 않는다(각 파생물은 확인 시점에 개별적으로
        확정하는 것이 더 안전 — 자동 전파는 하지 않는다는 명시적
        선택)."""

        from app.domains.media_asset.constants import RightsVerificationBasis

        if basis not in RightsVerificationBasis.ALL:
            raise BadRequestException(f"알 수 없는 확인 근거 유형입니다: {basis}")

        asset = self.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            raise NotFoundException("이미지를 찾을 수 없습니다.")

        self.repository.update_rights_status_conditional(
            asset_id, company_id, "VERIFIED",
        )

        # 2026-08-20 4차 지시 — 실제 격리 서버 브라우저 E2E에서 실제로
        # 재현된 결함 수정: write_audit_log()는 commit()하지 않는다
        # (app/core/audit_db.py). 이 메서드가 commit()을 먼저 하고
        # write_audit_log()를 그 뒤에 호출했더니, FastAPI의
        # get_db()(app/database/session.py)가 요청 종료 시 session을
        # commit 없이 close()만 하면서 감사로그 INSERT가 조용히
        # rollback됐다(같은 세션 안에서의 유닛 테스트 조회는 autoflush로
        # 이 문제를 가려서 그동안 드러나지 않았다). 이 도메인의 기존
        # 확립된 패턴(예: store_connection/service.py::_insert_
        # connection())과 동일하게, write_audit_log()를 먼저 호출하고
        # commit()을 단 한 번만 그 뒤에 실행해 상태 변경과 감사로그를
        # 원자적으로 함께 커밋한다.
        write_audit_log(
            self.db, company_id=company_id, user_id=confirmed_by,
            action="MEDIA_ASSET_RIGHTS_VERIFIED", entity="MediaAsset",
            entity_id=str(asset_id),
            description=f"사용권 확인(근거={basis})",
        )
        self.db.commit()

        return self.repository.get_media_asset_for_company(asset_id, company_id)

    def revoke_rights_verification(
        self, asset_id: int, company_id: int, revoked_by: int, reason: str,
    ) -> MediaAsset:
        """VERIFIED를 RIGHTS_UNVERIFIED로 되돌린다(2026-08-20 3차
        지시) — 이미 만들어진 제출 스냅샷/기존 승인 이력은 이 호출이
        변조하지 않는다(이 메서드는 MediaAsset.rights_status 한
        컬럼만 바꾼다 — 다른 도메인의 append-only 이력을 절대 건드리지
        않는 이 코드베이스 전체 원칙 그대로)."""

        asset = self.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            raise NotFoundException("이미지를 찾을 수 없습니다.")

        self.repository.update_rights_status_conditional(
            asset_id, company_id, "RIGHTS_UNVERIFIED",
        )

        # 2026-08-20 4차 지시 — confirm_rights_verified()와 동일한 이유로
        # write_audit_log()를 commit() 이전에 호출한다(자세한 배경은
        # confirm_rights_verified()의 주석 참고).
        write_audit_log(
            self.db, company_id=company_id, user_id=revoked_by,
            action="MEDIA_ASSET_RIGHTS_VERIFICATION_REVOKED", entity="MediaAsset",
            entity_id=str(asset_id), description=f"사용권 확인 취소(사유={reason})",
        )
        self.db.commit()

        return self.repository.get_media_asset_for_company(asset_id, company_id)

    def deny_rights(
        self, asset_id: int, company_id: int, denied_by: int, reason: str,
    ) -> MediaAsset:
        """2026-08-28 사용자 결정(권리 정책 반전 재감사) — 명시적 사용
        금지·권리 철회(사유 있음)·삭제 요청·신고/분쟁 상태를 나타내는
        RIGHTS_DENIED로 전환한다. 이 상태는 증빙 미제출(RIGHTS_
        UNVERIFIED)과 다르다 — 미제출은 이제 경고만 하고 차단하지
        않지만, RIGHTS_DENIED는 이번 정책 반전으로도 우회되지 않고
        계속 하드 차단된다(공개 업로드·이미지 선택·8단계 승인 전부).
        RIGHTS_DENIED에서 되돌리려면 반드시 이 메서드가 아니라 새로운
        명시적 사용자 판단(예: 분쟁 해소 확인)을 거쳐야 한다 — 이
        메서드는 되돌리기 경로를 제공하지 않는다."""

        asset = self.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            raise NotFoundException("이미지를 찾을 수 없습니다.")

        self.repository.update_rights_status_conditional(
            asset_id, company_id, "RIGHTS_DENIED",
        )

        write_audit_log(
            self.db, company_id=company_id, user_id=denied_by,
            action="MEDIA_ASSET_RIGHTS_DENIED", entity="MediaAsset",
            entity_id=str(asset_id), description=f"사용 금지 처리(사유={reason})",
        )
        self.db.commit()

        return self.repository.get_media_asset_for_company(asset_id, company_id)

    def get_media_asset_file_bytes(
        self, asset_id: int, company_id: int,
    ) -> tuple[bytes, str]:
        """UI 미리보기(썸네일/세그먼트 미리보기)용 원본 바이트 조회.
        회사 경계는 Repository의 company_id 필터로만 강제한다(다른
        조회 경로와 동일)."""

        asset = self.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            raise NotFoundException("이미지를 찾을 수 없습니다.")

        return read_media_file(self.media_root, asset.storage_path), asset.mime_type

    def upload_and_split_long_detail_image(
        self, owner_type: str, owner_id: int, image_bytes: bytes,
        original_filename: str | None, segment_height: int,
        requested_by: int, company_id: int,
    ) -> tuple[MediaAsset, list[MediaAsset]]:
        """
        "상세페이지용 긴 이미지"로 사용자가 명시적으로 선택한 업로드
        경로(2026-08-20 CTO 지시) — 일반 upload_original_asset()의
        MAX_IMAGE_HEIGHT(4096) 한도를 그대로 두고, 이 경로에서만
        더 넓은 한도(validate_long_detail_image_bytes)를 적용한다.

        메모리 안전 순서: (1) 헤더만 읽는 검증으로 픽셀 수 상한을
        먼저 확인 → (2) 통과한 것만 실제 PIL 디코딩(split_tall_image)
        → (3) 원본을 ORIGINAL로 그대로 보존 → (4) 조각들을 GENERATED로
        저장하며 source_asset_id·순서·크기·checksum을 기록한다(전부
        기존 MediaAsset 컬럼 — 신규 컬럼 없음). 조각은 기존
        MAX_IMAGE_HEIGHT(4096) 이내로 리사이즈되므로 일반
        validate_image_bytes를 그대로 통과한다(일반 이미지 한도를
        전역으로 올리지 않는다는 원칙 유지).
        """

        if owner_type not in MediaAssetOwnerType.ALL:
            raise BadRequestException(f"알 수 없는 owner_type입니다: {owner_type}")

        # (1) 헤더만 읽는 검증 — 긴 이미지 전용의 더 넓은(그러나 여전히
        # 유한한) 한도. 일반 upload_original_asset()/validate_image_
        # bytes()의 MAX_IMAGE_HEIGHT=4096은 이 경로에서 절대 쓰지
        # 않는다(그 한도를 쓰면 이 기능 자체가 불가능해진다 — 실제
        # 15,548px 샤프란 파일로 이 자리에서 재현·확인함).
        validated = validate_long_detail_image_bytes(image_bytes)

        active_count = self.repository.count_active_media_assets_for_owner(
            company_id, owner_type, owner_id,
        )
        # 원본 1장 + 예상 조각 수만큼 활성 이미지 한도를 미리 넉넉히
        # 확인한다(정확한 조각 수는 분할 후에나 알 수 있으므로 여기서는
        # "최소 원본 1장" 만 우선 확인 — 최종 저장 단계에서 한도 초과
        # 조각은 조용히 버리지 않고 명확히 차단한다).
        if active_count + 1 > MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE:
            raise BadRequestException(
                "이 상품에 대해 활성 이미지 수 한도"
                f"({MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE}장)를 초과합니다.",
            )

        digest = hashlib.sha256(image_bytes).hexdigest()
        storage_path = build_storage_path(company_id, digest, validated.mime_type)

        existing = self.repository.get_media_asset_by_company_storage_path(
            company_id, storage_path,
        )
        if existing is not None:
            original = existing
        else:
            write_media_file(self.media_root, storage_path, image_bytes)

            original = MediaAsset(
                company_id=company_id,
                owner_type=owner_type,
                owner_id=owner_id,
                asset_role=MediaAssetRole.ORIGINAL,
                source_asset_id=None,
                channel_code=None,
                purpose="DETAIL_SOURCE",
                display_order=0,
                storage_path=storage_path,
                original_filename=original_filename,
                mime_type=validated.mime_type,
                file_size_bytes=len(image_bytes),
                sha256_hex=digest,
                width=validated.width,
                height=validated.height,
                status="ACTIVE",
                # 참고자료 성격의 긴 상세 이미지는 사용권 미확인 상태로
                # 시작한다(2026-08-20 CTO 지시) — 실제 등록 선택은
                # listing_wizard_service.py::update_media()가 차단한다.
                rights_status="RIGHTS_UNVERIFIED",
            )
            try:
                original = self.repository.add_media_asset_no_commit(original)
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                winner = self.repository.get_media_asset_by_company_storage_path(
                    company_id, storage_path,
                )
                if winner is None:
                    raise
                original = winner

        # (2) 헤더 검증을 통과한 것만 실제 PIL 디코딩한다(메모리 안전
        # 순서 — 픽셀 수 상한을 이미 확인한 뒤에만 전체 디코드).
        segment_bytes_list = split_tall_image(
            image_bytes, segment_height=segment_height,
        )

        created: list[MediaAsset] = []
        last_digest: str | None = None
        for segment_bytes in segment_bytes_list:
            digest = hashlib.sha256(segment_bytes).hexdigest()
            if digest == last_digest:
                # 직전 조각과 완전히 동일한 바이트 — 의미 없는 중복
                # 조각을 만들지 않는다(요구사항).
                continue
            last_digest = digest

            asset = self._persist_generated_asset(
                company_id=company_id,
                owner_type=owner_type,
                owner_id=owner_id,
                purpose="DETAIL",
                display_order=len(created),
                image_bytes=segment_bytes,
                self_reported_mime_type="image/png",
            )
            asset.source_asset_id = original.id
            asset.rights_status = "RIGHTS_UNVERIFIED"
            created.append(asset)

        self.db.commit()

        return original, created

    def split_long_image(
        self, source_asset_id: int, segment_height: int, requested_by: int,
        company_id: int,
    ) -> list[MediaAsset]:
        """긴 상세 이미지(원본) 1장을 고정 높이 조각 여러 장(파생
        자산, GENERATED)으로 나눈다. 원본은 수정하지 않는다 — 의미
        단위 경계는 사용자가 육안으로 확인해야 한다(기계적 높이
        분할일 뿐, 콘텐츠 인식 분할이 아님)."""

        source = self.repository.get_media_asset_for_company(
            source_asset_id, company_id,
        )
        if source is None:
            raise NotFoundException("원본 이미지를 찾을 수 없습니다.")

        source_bytes = read_media_file(self.media_root, source.storage_path)
        segments = split_tall_image(source_bytes, segment_height=segment_height)

        created = []
        for index, segment_bytes in enumerate(segments):
            asset = self._persist_generated_asset(
                company_id=company_id,
                owner_type=source.owner_type,
                owner_id=source.owner_id,
                purpose="DETAIL",
                display_order=index,
                image_bytes=segment_bytes,
                self_reported_mime_type="image/png",
            )
            asset.source_asset_id = source.id
            asset.rights_status = source.rights_status
            created.append(asset)

        self.db.commit()

        return created

    def compose_background(
        self, cutout_asset_id: int, background_spec: BackgroundSpec,
        requested_by: int, company_id: int,
    ) -> MediaAsset:
        """누끼(GENERATED, 배경 제거 결과) 위에 배경을 합성한 파생
        자산을 만든다. Pillow 기반 순수 로컬 연산 — 네트워크 없음."""

        cutout = self.repository.get_media_asset_for_company(
            cutout_asset_id, company_id,
        )
        if cutout is None:
            raise NotFoundException("누끼 이미지를 찾을 수 없습니다.")

        cutout_bytes = read_media_file(self.media_root, cutout.storage_path)

        job = self._record_processing_job(
            operation="BACKGROUND_COMPOSITION",
            provider_code="PILLOW_LOCAL",
            model_name=background_spec.kind,
            source_asset_id=cutout.id,
            requested_by=requested_by,
            company_id=company_id,
            product_candidate_id=(
                cutout.owner_id
                if cutout.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE
                else 0
            ),
        )

        try:
            composed_bytes = compose_with_background(cutout_bytes, background_spec)
        except Exception as exc:  # noqa: BLE001
            self._finish_processing_job(
                job.id, company_id, success=False,
                error_reason=f"배경 합성 실패: {type(exc).__name__}",
            )
            raise BadRequestException(
                "배경 합성에 실패했습니다(원본은 그대로 유지됩니다).",
            ) from exc

        asset = self._persist_generated_asset(
            company_id=company_id,
            owner_type=cutout.owner_type,
            owner_id=cutout.owner_id,
            purpose=cutout.purpose,
            display_order=cutout.display_order,
            image_bytes=composed_bytes,
            self_reported_mime_type="image/png",
        )
        asset.source_asset_id = cutout.id
        asset.rights_status = cutout.rights_status
        self.db.commit()

        self._finish_processing_job(
            job.id, company_id, success=True,
            model_version=background_spec.kind, media_asset_id=asset.id,
        )

        return asset

    def generate_detail_page(
        self,
        hero_asset_id: int,
        brand_name: str,
        tagline: str | None,
        feature_highlights: list[tuple[str, str]],
        spec_rows: list[tuple[str, str]],
        usage_text: str | None,
        accent_color_hex: str,
        requested_by: int,
        company_id: int,
    ) -> MediaAsset:
        """실제 상품 사진(hero_asset_id) + 텍스트를 조합해 쿠팡
        상세이미지를 자동 생성한다. Pillow 기반 순수 로컬 연산 —
        네트워크 없음, 제품 자체를 AI로 다시 그리지 않는다
        (app/domains/media_asset/detail_page_generator.py 원칙과
        동일, compose_background()와 같은 패턴)."""

        hero = self.repository.get_media_asset_for_company(
            hero_asset_id, company_id,
        )
        if hero is None:
            raise NotFoundException("대표 이미지를 찾을 수 없습니다.")

        hero_bytes = read_media_file(self.media_root, hero.storage_path)

        job = self._record_processing_job(
            operation="DETAIL_PAGE_GENERATION",
            provider_code="PILLOW_LOCAL",
            model_name=None,
            source_asset_id=hero.id,
            requested_by=requested_by,
            company_id=company_id,
            product_candidate_id=(
                hero.owner_id
                if hero.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE
                else 0
            ),
        )

        try:
            content = DetailPageContent(
                brand_name=brand_name,
                hero_image_bytes=hero_bytes,
                tagline=tagline,
                feature_highlights=[
                    FeatureHighlight(t, d) for t, d in feature_highlights
                ],
                spec_rows=[SpecRow(k, v) for k, v in spec_rows],
                usage_text=usage_text,
                accent_color_hex=accent_color_hex,
            )
            detail_bytes = generate_detail_page_image(content)
        except (BadRequestException, DetailPageFontUnavailableError) as exc:
            self._finish_processing_job(
                job.id, company_id, success=False,
                error_reason=f"상세이미지 생성 실패: {exc}",
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self._finish_processing_job(
                job.id, company_id, success=False,
                error_reason=f"상세이미지 생성 실패: {type(exc).__name__}",
            )
            raise BadRequestException(
                "상세이미지 생성에 실패했습니다(원본은 그대로 유지됩니다).",
            ) from exc

        asset = self._persist_generated_asset(
            company_id=company_id,
            owner_type=hero.owner_type,
            owner_id=hero.owner_id,
            purpose="DETAIL",
            display_order=hero.display_order + 1,
            image_bytes=detail_bytes,
            self_reported_mime_type="image/jpeg",
        )
        asset.source_asset_id = hero.id
        asset.rights_status = hero.rights_status
        self.db.commit()

        self._finish_processing_job(
            job.id, company_id, success=True,
            media_asset_id=asset.id,
        )

        return asset

    def _record_processing_job(
        self, operation: str, provider_code: str, model_name: str | None,
        source_asset_id: int, requested_by: int, company_id: int,
        product_candidate_id: int,
    ) -> ImageGenerationJob:

        job = ImageGenerationJob(
            company_id=company_id,
            listing_package_id=None,
            product_candidate_id=product_candidate_id,
            provider_code=provider_code,
            model_name=model_name,
            prompt_fingerprint=sha256_hex(canonical_json({
                "operation": operation, "source_asset_id": source_asset_id,
            })),
            request_payload_json=canonical_json({
                "operation": operation, "source_asset_id": source_asset_id,
            }),
            status=ImageJobStatus.RUNNING,
            idempotency_key=f"{operation}-{source_asset_id}-{datetime.utcnow().timestamp()}",
            requested_by=requested_by,
            started_at=datetime.utcnow(),
        )
        job = self.repository.add_job_no_commit(job)
        self.db.commit()

        return job

    def _finish_processing_job(
        self, job_id: int, company_id: int, success: bool,
        error_reason: str | None = None, model_version: str | None = None,
        media_asset_id: int | None = None,
    ) -> None:

        self.repository.update_job_fields_conditional(
            job_id, company_id, (ImageJobStatus.RUNNING,),
            {
                "status": ImageJobStatus.SUCCEEDED if success else ImageJobStatus.FAILED,
                "progress_percent": 100 if success else 0,
                "error_reason": error_reason,
                "completed_at": datetime.utcnow(),
            },
        )
        self.repository.add_result_no_commit(ImageGenerationResult(
            company_id=company_id,
            job_id=job_id,
            media_asset_id=media_asset_id,
            sequence_index=0,
            purpose="PROCESSED",
            status=ImageResultStatus.SUCCEEDED if success else ImageResultStatus.FAILED,
            safety_check_status=ImageSafetyCheckStatus.PASSED if success else ImageSafetyCheckStatus.UNKNOWN,
            error_reason=error_reason,
        ))
        self.db.commit()

    # ------------------------------------------------
    # 취소 / 재시도 / 복구
    # ------------------------------------------------

    def cancel_job(
        self, job_id: int, idempotency_key: str, company_id: int,
    ) -> ImageGenerationJob:

        job = self.repository.get_job_for_company(job_id, company_id)
        if job is None:
            raise NotFoundException(_JOB_NOT_FOUND)

        if job.status in ImageJobStatus.TERMINAL:
            return job

        rowcount = self.repository.update_job_fields_conditional(
            job_id, company_id,
            (ImageJobStatus.PENDING, ImageJobStatus.RUNNING),
            {
                "status": ImageJobStatus.CANCELLED,
                "completed_at": datetime.utcnow(),
            },
        )
        if rowcount != 1:
            raise ConflictException(
                "Job 상태가 동시에 변경되어 취소를 반영할 수 없습니다.",
            )

        self.db.commit()

        return self.repository.get_job_for_company(job_id, company_id)

    def retry_job(
        self, job_id: int, idempotency_key: str, requested_by: int,
        company_id: int,
    ) -> tuple[ImageGenerationJob, bool]:

        # 2026-08-02 Gate R3: 재시도의 fingerprint는 원본 Job의 것을
        # 그대로 물려받는다(재시도는 새 payload를 받지 않는다) — 이
        # 값을 existing 조회보다 먼저 확보해, 같은 idempotency_key가
        # 다른 원본 Job의 재시도로 재사용되는 경우를 구분한다.
        original = self.repository.get_job_for_company(job_id, company_id)
        if original is None:
            raise NotFoundException(_JOB_NOT_FOUND)

        expected_fingerprint = original.prompt_fingerprint

        existing = self.repository.get_job_by_company_idempotency_key(
            company_id, idempotency_key,
        )
        if existing is not None:
            if existing.prompt_fingerprint == expected_fingerprint:
                return existing, True

            raise ConflictException(
                "같은 idempotency_key가 이미 다른 요청 내용으로 사용 "
                "중입니다 — 같은 key로는 동일한 요청만 재제출할 수 "
                "있습니다.",
            )

        if original.status not in (ImageJobStatus.FAILED, ImageJobStatus.PARTIAL):
            raise BadRequestException(
                "FAILED 또는 PARTIAL 상태의 Job만 재시도할 수 있습니다. "
                f"(현재: {original.status})",
            )

        if original.retry_count >= original.max_retries:
            raise BadRequestException(
                f"재시도 횟수 한도({original.max_retries}회)를 초과했습니다.",
            )

        retry_job_row = ImageGenerationJob(
            company_id=company_id,
            listing_package_id=original.listing_package_id,
            product_candidate_id=original.product_candidate_id,
            provider_code=original.provider_code,
            model_name=original.model_name,
            prompt_fingerprint=original.prompt_fingerprint,
            request_payload_json=original.request_payload_json,
            status=ImageJobStatus.PENDING,
            retry_count=original.retry_count + 1,
            max_retries=original.max_retries,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )

        try:
            retry_job_row = self.repository.add_job_no_commit(retry_job_row)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_job_by_company_idempotency_key(
                company_id, idempotency_key,
            )
            if winner is None:
                raise

            if winner.prompt_fingerprint != expected_fingerprint:
                raise ConflictException(
                    "같은 idempotency_key가 이미 다른 요청 내용으로 사용 "
                    "중입니다 — 같은 key로는 동일한 요청만 재제출할 수 "
                    "있습니다.",
                )

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        # 2026-08-02 Gate R5: submit_job()과 동일하게 PENDING 저장 후
        # 즉시 반환한다 — 실행은 백그라운드 Worker가 폴링해 처리한다.
        return retry_job_row, False

    def recover_stalled_jobs(
        self, company_id: int, now: datetime | None = None,
        stall_timeout_seconds: int = JOB_STALL_TIMEOUT_SECONDS,
    ) -> list[ImageGenerationJob]:
        """
        앱 재시작 후 미완료(PENDING/RUNNING인 채로 멈춘) Job을
        복구한다.

        - RUNNING: 타임아웃을 넘은 행을 FAILED(TIMEOUT_RECOVERED)로
          조건부 전이시킨다 — Provider 호출이 진행 중이었을 수 있어
          안전하게 재개할 수 없으므로 실패 처리 후 사용자가 retry_job()
          으로 새 Job을 만들게 한다.
        - PENDING: submit_job()/retry_job()이 행을 PENDING으로 commit한
          직후, _execute_job() 호출 전에 프로세스가 죽으면 이 상태로
          남는다 — 외부 Provider 호출이 아직 시작되지 않았으므로
          그대로 _execute_job()을 재호출해 안전하게 재개한다(그
          내부의 조건부 UPDATE...WHERE status=PENDING이 여전히 이중
          실행을 막는다).

        이미 종결 상태인 행은 건드리지 않는다.
        """

        now = now or datetime.utcnow()
        threshold = now - timedelta(seconds=stall_timeout_seconds)

        stalled_running = self.repository.list_stalled_running_jobs_for_company(
            company_id, threshold,
        )

        recovered = []
        for job in stalled_running:
            rowcount = self.repository.update_job_fields_conditional(
                job.id, company_id, (ImageJobStatus.RUNNING,),
                {
                    "status": ImageJobStatus.FAILED,
                    "error_reason": "TIMEOUT_RECOVERED: 재시작 복구로 인해 "
                    "정지된 Job을 실패 처리했습니다.",
                    "completed_at": now,
                },
            )
            if rowcount == 1:
                self.db.commit()
                recovered.append(
                    self.repository.get_job_for_company(job.id, company_id),
                )
            else:
                self.db.rollback()

        stalled_pending = self.repository.list_stalled_pending_jobs_for_company(
            company_id, threshold,
        )

        for job in stalled_pending:
            self._execute_job(job.id, company_id)
            recovered.append(
                self.repository.get_job_for_company(job.id, company_id),
            )

        return recovered

    def recover_all_companies_stalled_running_jobs(
        self, now: datetime | None = None,
        stall_timeout_seconds: int = JOB_STALL_TIMEOUT_SECONDS,
    ) -> list[ImageGenerationJob]:
        """
        2026-08-02 Gate R5 — `ImageGenerationJobWorker` 시작 시 호출하는
        전체 회사 대상 일괄 복구. PENDING은 여기서 다루지 않는다 —
        Worker가 있는 한 PENDING은 폴링 루프가 그대로 claim하므로
        "정지" 판정 자체가 필요 없다(외부 Provider 호출이 아직 시작되지
        않았으므로 언제 claim되든 안전하다). RUNNING만 대상이다
        (Provider 호출이 진행 중이었을 수 있어 안전하게 재개할 수
        없으므로 FAILED 처리 후 명시적 재시도가 필요하다).
        """

        now = now or datetime.utcnow()
        threshold = now - timedelta(seconds=stall_timeout_seconds)

        stalled_running = self.repository.list_stalled_running_jobs_all_companies(
            threshold,
        )

        recovered = []
        for job in stalled_running:
            rowcount = self.repository.update_job_fields_conditional(
                job.id, job.company_id, (ImageJobStatus.RUNNING,),
                {
                    "status": ImageJobStatus.FAILED,
                    "error_reason": "TIMEOUT_RECOVERED: 재시작 복구로 인해 "
                    "정지된 Job을 실패 처리했습니다.",
                    "completed_at": now,
                },
            )
            if rowcount == 1:
                self.db.commit()
                recovered.append(
                    self.repository.get_job_for_company(job.id, job.company_id),
                )
            else:
                self.db.rollback()

        return recovered

    # ------------------------------------------------
    # 조회
    # ------------------------------------------------

    def list_results(
        self, job_id: int, company_id: int,
    ) -> list[ImageGenerationResult]:

        return self.repository.list_results_for_job(job_id, company_id)

    def list_active_assets(
        self, company_id: int, owner_type: str, owner_id: int,
    ) -> list[MediaAsset]:

        return self.repository.list_active_media_assets_for_owner(
            company_id, owner_type, owner_id,
        )

    def mark_orphaned_assets(
        self, company_id: int, active_owner_ids: dict,
    ) -> list[MediaAsset]:
        """
        더 이상 어떤 살아있는 소유 객체(active_owner_ids)에도 속하지
        않는 GENERATED/CHANNEL_VARIANT 자산을 ORPHANED로 조건부
        전이시킨다(실제 파일 삭제는 하지 않는다 — 별도 정리 배치의
        몫). ORIGINAL은 대상에서 제외된다(원본 불변).
        """

        candidates = self.repository.list_orphan_candidate_assets(
            company_id, active_owner_ids,
        )

        orphaned = []
        for asset in candidates:
            rowcount = self.repository.update_media_asset_status_conditional(
                asset.id, company_id, ("ACTIVE",), "ORPHANED",
            )
            if rowcount == 1:
                self.db.commit()
                orphaned.append(
                    self.repository.get_media_asset_for_company(
                        asset.id, company_id,
                    ),
                )
            else:
                self.db.rollback()

        return orphaned


def _parse_items(request_payload_json: str) -> list[str]:

    import json

    return json.loads(request_payload_json)["items"]


def _parse_style_params(request_payload_json: str) -> dict:

    import json

    return json.loads(request_payload_json)["style_params"]


__all__ = [
    "ImageGenerationJobQueueService",
]
