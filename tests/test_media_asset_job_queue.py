"""
=========================================================
Homez OS

File : tests/test_media_asset_job_queue.py

Media Asset / Image Generation Job Queue 재감사 시나리오:
회사 격리, idempotency 충돌, 파일 위장(signature 검증), 경로 공격
(path traversal), 비용/개수 한도, Queue 복구(재시작 시뮬레이션),
부분 성공(PARTIAL), 고아 파일 방지, 실제 외부 호출 0건.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import ImageJobStatus
from app.domains.media_asset.image_validation import build_storage_path
from app.domains.media_asset.image_validation import validate_image_bytes
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.providers import ImageGenerationProvider
from app.domains.media_asset.providers import ImageGenerationProviderResult
from app.domains.media_asset.providers import ImageGenerationResultItem
from app.domains.media_asset.providers import PROVIDERS_BY_CODE
from app.domains.media_asset.schema import ImageGenerationItemRequest
from app.domains.media_asset.schema import ImageGenerationJobSubmitRequest
from app.domains.media_asset.storage import write_media_file
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


class _PartialFailureProvider(ImageGenerationProvider):

    code = "PARTIAL_TEST"

    def generate(self, request):

        items = []
        for index, item in enumerate(request.items):
            if index == 0:
                # 유효한 PNG(FakeProvider와 동일한 방식으로 만든다).
                from app.domains.media_asset.providers import (
                    _deterministic_png,
                )
                items.append(ImageGenerationResultItem(
                    sequence_index=item.sequence_index, purpose=item.purpose,
                    status="SUCCEEDED",
                    image_bytes=_deterministic_png(f"partial-{index}"),
                    mime_type="image/png", safety_check_status="PASSED",
                ))
            else:
                items.append(ImageGenerationResultItem(
                    sequence_index=item.sequence_index, purpose=item.purpose,
                    status="FAILED", image_bytes=None, mime_type=None,
                    safety_check_status="UNKNOWN",
                    error_reason="시뮬레이션된 실패",
                ))

        return ImageGenerationProviderResult(items=tuple(items), provider_cost=0.02)


class MediaAssetJobQueueTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                MediaAsset.__table__,
                ImageGenerationJob.__table__,
                ImageGenerationResult.__table__,
                ImageGenerationDailyUsage.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.media_root = Path(tempfile.mkdtemp())
        self.service = ImageGenerationJobQueueService(
            self.db, media_root=self.media_root,
        )

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        PROVIDERS_BY_CODE["PARTIAL_TEST"] = _PartialFailureProvider()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        PROVIDERS_BY_CODE.pop("PARTIAL_TEST", None)

    def _submit_and_run(self, req, requested_by, company_id):
        """
        2026-08-02 Gate R5: submit_job()이 이제 PENDING만 저장하고
        즉시 반환하므로(실제 실행은 별도 Worker가 폴링해서 한다), 동기
        완료를 기대하는 기존 테스트들은 Worker가 할 일(_execute_job
        호출)을 직접 한 번 대신 해준다 — 실제 ImageGenerationJobWorker
        가 정확히 이렇게 동작한다(app/domains/media_asset/worker.py).
        """

        job, dup = self.service.submit_job(req, requested_by, company_id)
        if not dup:
            self.service._execute_job(job.id, company_id)
        final_job = self.service.repository.get_job_for_company(
            job.id, company_id,
        )

        return final_job, dup

    def _retry_and_run(self, job_id, idempotency_key, requested_by, company_id):

        job, dup = self.service.retry_job(
            job_id, idempotency_key, requested_by, company_id,
        )
        if not dup:
            self.service._execute_job(job.id, company_id)
        final_job = self.service.repository.get_job_for_company(
            job.id, company_id,
        )

        return final_job, dup

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    # ----------------------------------------------------
    # 2026-08-02 Gate R5 — submit_job()이 Provider 완료를 기다리지 않고
    # PENDING을 즉시 반환하는지
    # ----------------------------------------------------

    def test_submit_job_returns_pending_without_executing_provider(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("no-sync-exec"),
        )
        job, dup = self.service.submit_job(req, 1, self.company_a.id)

        self.assertFalse(dup)
        self.assertEqual(job.status, ImageJobStatus.PENDING)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.completed_at)

        assets = self.service.list_active_assets(
            self.company_a.id, MediaAssetOwnerType.PRODUCT_CANDIDATE, 1,
        )
        self.assertEqual(assets, [])

    def test_submit_job_blocked_when_image_processing_capability_deactivated(self):
        """Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        실연결 증거."""

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("capability-blocked"),
        )

        with deactivated_capability("IMAGE_PROCESSING"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.submit_job(req, 1, self.company_a.id)

    def test_retry_job_returns_pending_without_executing_provider(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("retry-no-sync-src"),
        )
        job, _ = self._submit_and_run(req, 1, self.company_a.id)
        self.service.repository.update_job_fields_conditional(
            job.id, self.company_a.id, (ImageJobStatus.SUCCEEDED,),
            {"status": ImageJobStatus.FAILED},
        )
        self.db.commit()

        retried, dup = self.service.retry_job(
            job.id, _next_key("retry-no-sync"), 1, self.company_a.id,
        )

        self.assertFalse(dup)
        self.assertEqual(retried.status, ImageJobStatus.PENDING)
        self.assertIsNone(retried.started_at)

    # ----------------------------------------------------
    # idempotency
    # ----------------------------------------------------

    def test_duplicate_submit_returns_same_job(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key="dup-key",
        )
        job1, dup1 = self.service.submit_job(req, 1, self.company_a.id)
        job2, dup2 = self.service.submit_job(req, 1, self.company_a.id)

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(job1.id, job2.id)

    def test_same_idempotency_key_usable_independently_by_both_companies(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key="shared-key",
        )
        job_a, _ = self.service.submit_job(req, 1, self.company_a.id)
        job_b, _ = self.service.submit_job(req, 1, self.company_b.id)

        self.assertNotEqual(job_a.id, job_b.id)

    # --- 2026-08-02 Gate R3: 같은 key, 다른 payload ---

    def test_same_key_different_payload_returns_409(self):

        key = _next_key("same-key-diff-payload")

        first = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=key,
        )
        self.service.submit_job(first, 1, self.company_a.id)

        second = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="DETAIL")],  # purpose만 다름
            idempotency_key=key,
        )

        with self.assertRaises(ConflictException):
            self.service.submit_job(second, 1, self.company_a.id)

    def test_same_key_different_product_candidate_returns_409(self):

        key = _next_key("same-key-diff-candidate")

        first = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=key,
        )
        self.service.submit_job(first, 1, self.company_a.id)

        second = ImageGenerationJobSubmitRequest(
            product_candidate_id=2,  # product_candidate_id만 다름
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=key,
        )

        with self.assertRaises(ConflictException):
            self.service.submit_job(second, 1, self.company_a.id)

    def test_same_key_different_item_order_returns_409(self):

        key = _next_key("same-key-diff-order")

        first = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[
                ImageGenerationItemRequest(purpose="MAIN"),
                ImageGenerationItemRequest(purpose="DETAIL"),
            ],
            idempotency_key=key,
        )
        self.service.submit_job(first, 1, self.company_a.id)

        second = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[
                ImageGenerationItemRequest(purpose="DETAIL"),
                ImageGenerationItemRequest(purpose="MAIN"),
            ],
            idempotency_key=key,
        )

        with self.assertRaises(ConflictException):
            self.service.submit_job(second, 1, self.company_a.id)

    def test_same_key_same_payload_still_idempotent_after_fix(self):

        key = _next_key("same-key-same-payload")

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=key,
        )
        job1, dup1 = self.service.submit_job(req, 1, self.company_a.id)
        job2, dup2 = self.service.submit_job(req, 1, self.company_a.id)

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(job1.id, job2.id)

    def test_integrity_error_race_winner_fingerprint_mismatch_returns_409(self):
        """
        동시 요청 경쟁에서 진 쪽(IntegrityError)이 승자의 Job을 그대로
        신뢰하지 않고, 승자도 같은 요청 내용이었는지 재확인해야 한다 —
        아니라면 409로 거부해야 한다(승자가 다른 payload를 썼을 수
        있으므로 무조건 승자를 반환하면 요청 내용이 조용히 뒤바뀐다).
        """

        key = _next_key("race-mismatch")

        # "승자" 역할의 Job을 먼저 실제로 만든다(다른 payload).
        winner_req = ImageGenerationJobSubmitRequest(
            product_candidate_id=99,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=key,
        )
        self.service.submit_job(winner_req, 1, self.company_a.id)

        # "패자" 요청 — 다른 payload로 같은 key를 쓰며, add_job_no_commit이
        # IntegrityError를 던지도록 강제해 경쟁 상황을 재현한다.
        from sqlalchemy.exc import IntegrityError

        loser_req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=key,
        )

        with mock.patch.object(
            self.service.repository, "add_job_no_commit",
            side_effect=IntegrityError("stmt", {}, Exception("UNIQUE")),
        ):
            with self.assertRaises(ConflictException):
                self.service.submit_job(loser_req, 1, self.company_a.id)

    def test_retry_job_same_key_different_original_returns_409(self):

        # 첫 번째 원본 Job(실패 처리).
        req1 = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("retry-race-1"),
        )
        job1, _ = self._submit_and_run(req1, 1, self.company_a.id)
        self.service.repository.update_job_fields_conditional(
            job1.id, self.company_a.id, (ImageJobStatus.SUCCEEDED,),
            {"status": ImageJobStatus.FAILED},
        )
        self.db.commit()

        # 두 번째(다른) 원본 Job(역시 실패 처리) — 다른 payload.
        req2 = ImageGenerationJobSubmitRequest(
            product_candidate_id=2,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("retry-race-2"),
        )
        job2, _ = self._submit_and_run(req2, 1, self.company_a.id)
        self.service.repository.update_job_fields_conditional(
            job2.id, self.company_a.id, (ImageJobStatus.SUCCEEDED,),
            {"status": ImageJobStatus.FAILED},
        )
        self.db.commit()

        shared_retry_key = _next_key("retry-race-shared")

        # job1을 이 key로 먼저 재시도(성공적으로 새 Job 생성).
        self._retry_and_run(job1.id, shared_retry_key, 1, self.company_a.id)

        # 같은 key로 job2를 재시도하려 하면(다른 fingerprint) 409여야 한다.
        with self.assertRaises(ConflictException):
            self.service.retry_job(job2.id, shared_retry_key, 1, self.company_a.id)

    # ----------------------------------------------------
    # 회사 격리
    # ----------------------------------------------------

    def test_cross_company_job_access_returns_404(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("job"),
        )
        job, _ = self.service.submit_job(req, 1, self.company_a.id)

        found = self.service.repository.get_job_for_company(
            job.id, self.company_b.id,
        )
        self.assertIsNone(found)

        with self.assertRaises(NotFoundException):
            self.service.cancel_job(job.id, _next_key("cancel"), self.company_b.id)

    def test_cross_company_assets_not_visible(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=42,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("job"),
        )
        self._submit_and_run(req, 1, self.company_a.id)

        assets_a = self.service.list_active_assets(
            self.company_a.id, MediaAssetOwnerType.PRODUCT_CANDIDATE, 42,
        )
        assets_b = self.service.list_active_assets(
            self.company_b.id, MediaAssetOwnerType.PRODUCT_CANDIDATE, 42,
        )
        self.assertEqual(len(assets_a), 1)
        self.assertEqual(len(assets_b), 0)

    # ----------------------------------------------------
    # 파일 위장 / signature 검증
    # ----------------------------------------------------

    def test_fake_extension_with_wrong_signature_is_rejected(self):

        fake_png_bytes = b"GIF89a" + b"\x00" * 100  # PNG 아님(GIF signature)

        with self.assertRaises(BadRequestException):
            validate_image_bytes(fake_png_bytes)

    def test_executable_disguised_as_image_is_rejected(self):

        exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200

        with self.assertRaises(BadRequestException):
            validate_image_bytes(exe_bytes)

    def test_oversized_file_is_rejected(self):

        from app.domains.media_asset.constants import MAX_IMAGE_FILE_SIZE_BYTES

        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (
            MAX_IMAGE_FILE_SIZE_BYTES + 1
        )

        with self.assertRaises(BadRequestException):
            validate_image_bytes(oversized)

    # ----------------------------------------------------
    # 경로 조작(path traversal) 차단
    # ----------------------------------------------------

    def test_storage_path_is_server_computed_not_from_filename(self):

        path = build_storage_path(self.company_a.id, "a" * 64, "image/png")

        self.assertNotIn("..", path)
        self.assertTrue(path.startswith(f"media/{self.company_a.id}/"))

    def test_path_traversal_in_storage_path_is_blocked(self):

        malicious_path = "../../../etc/passwd"

        with self.assertRaises(BadRequestException):
            write_media_file(self.media_root, malicious_path, b"malicious")

    def test_absolute_path_storage_is_blocked(self):

        with self.assertRaises(BadRequestException):
            write_media_file(self.media_root, "/etc/passwd", b"malicious")

    # ----------------------------------------------------
    # 부분 성공(PARTIAL)
    # ----------------------------------------------------

    def test_partial_provider_failure_yields_partial_status_and_keeps_succeeded(
        self,
    ):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=7,
            provider_code="PARTIAL_TEST",
            items=[
                ImageGenerationItemRequest(purpose="MAIN"),
                ImageGenerationItemRequest(purpose="DETAIL"),
            ],
            idempotency_key=_next_key("partial"),
        )
        job, _ = self._submit_and_run(req, 1, self.company_a.id)

        self.assertEqual(job.status, ImageJobStatus.PARTIAL)

        results = self.service.list_results(job.id, self.company_a.id)
        statuses = sorted(r.status for r in results)
        self.assertEqual(statuses, ["FAILED", "SUCCEEDED"])

        assets = self.service.list_active_assets(
            self.company_a.id, MediaAssetOwnerType.PRODUCT_CANDIDATE, 7,
        )
        self.assertEqual(len(assets), 1)

    # ----------------------------------------------------
    # 비용/개수 한도
    # ----------------------------------------------------

    def test_daily_budget_exceeded_blocks_job_without_calling_provider(self):

        with mock.patch(
            "app.domains.media_asset.job_queue_service."
            "DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY", 1,
        ):
            req1 = ImageGenerationJobSubmitRequest(
                product_candidate_id=1,
                items=[ImageGenerationItemRequest(purpose="MAIN")],
                idempotency_key=_next_key("budget"),
            )
            job1, _ = self._submit_and_run(req1, 1, self.company_a.id)
            self.assertEqual(job1.status, ImageJobStatus.SUCCEEDED)

            req2 = ImageGenerationJobSubmitRequest(
                product_candidate_id=1,
                items=[ImageGenerationItemRequest(purpose="MAIN")],
                idempotency_key=_next_key("budget"),
            )
            job2, _ = self._submit_and_run(req2, 1, self.company_a.id)
            self.assertEqual(job2.status, ImageJobStatus.FAILED)
            self.assertIn("DAILY_BUDGET_EXCEEDED", job2.error_reason)

    def test_max_images_per_job_enforced(self):

        from app.domains.media_asset.constants import MAX_IMAGES_PER_JOB

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[
                ImageGenerationItemRequest(purpose="MAIN")
                for _ in range(MAX_IMAGES_PER_JOB + 1)
            ],
            idempotency_key=_next_key("toomany"),
        )
        with self.assertRaises(BadRequestException):
            self.service.submit_job(req, 1, self.company_a.id)

    def test_active_images_per_product_cap_enforced(self):

        with mock.patch(
            "app.domains.media_asset.job_queue_service."
            "MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE", 1,
        ):
            req1 = ImageGenerationJobSubmitRequest(
                product_candidate_id=99,
                items=[ImageGenerationItemRequest(purpose="MAIN")],
                idempotency_key=_next_key("cap"),
            )
            self._submit_and_run(req1, 1, self.company_a.id)

            req2 = ImageGenerationJobSubmitRequest(
                product_candidate_id=99,
                items=[ImageGenerationItemRequest(purpose="DETAIL")],
                idempotency_key=_next_key("cap"),
            )
            with self.assertRaises(BadRequestException):
                self.service.submit_job(req2, 1, self.company_a.id)

    def test_regeneration_cap_per_package_enforced(self):

        from app.domains.media_asset.constants import (
            MAX_REGENERATIONS_PER_PACKAGE,
        )

        for _ in range(MAX_REGENERATIONS_PER_PACKAGE):
            req = ImageGenerationJobSubmitRequest(
                product_candidate_id=5, listing_package_id=500,
                items=[ImageGenerationItemRequest(purpose="MAIN")],
                idempotency_key=_next_key("regen"),
            )
            self.service.submit_job(req, 1, self.company_a.id)

        req_over = ImageGenerationJobSubmitRequest(
            product_candidate_id=5, listing_package_id=500,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("regen"),
        )
        with self.assertRaises(BadRequestException):
            self.service.submit_job(req_over, 1, self.company_a.id)

    # ----------------------------------------------------
    # Queue 복구(재시작 시뮬레이션)
    # ----------------------------------------------------

    def test_recover_stalled_jobs_transitions_running_to_failed(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("stall"),
        )
        job, _ = self._submit_and_run(req, 1, self.company_a.id)

        # 정상 실행되어 SUCCEEDED가 된 행을 강제로 RUNNING+과거
        # started_at으로 되돌려 "재시작 전 멈춘 Job"을 재현한다.
        self.service.repository.update_job_fields_conditional(
            job.id, self.company_a.id, (ImageJobStatus.SUCCEEDED,),
            {
                "status": ImageJobStatus.RUNNING,
                "started_at": datetime.utcnow() - timedelta(hours=1),
            },
        )
        self.db.commit()

        recovered = self.service.recover_stalled_jobs(
            self.company_a.id, stall_timeout_seconds=300,
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].status, ImageJobStatus.FAILED)
        self.assertIn("TIMEOUT_RECOVERED", recovered[0].error_reason)

    def test_recover_stalled_jobs_is_company_scoped(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("stall2"),
        )
        job, _ = self._submit_and_run(req, 1, self.company_a.id)

        self.service.repository.update_job_fields_conditional(
            job.id, self.company_a.id, (ImageJobStatus.SUCCEEDED,),
            {
                "status": ImageJobStatus.RUNNING,
                "started_at": datetime.utcnow() - timedelta(hours=1),
            },
        )
        self.db.commit()

        recovered_wrong_company = self.service.recover_stalled_jobs(
            self.company_b.id, stall_timeout_seconds=300,
        )
        self.assertEqual(recovered_wrong_company, [])

        still_running = self.service.repository.get_job_for_company(
            job.id, self.company_a.id,
        )
        self.assertEqual(still_running.status, ImageJobStatus.RUNNING)

    def test_recover_stalled_jobs_resumes_stuck_pending_jobs(self):
        """
        submit_job()이 Job 행을 PENDING으로 commit한 직후, _execute_job()
        호출 전에 프로세스가 죽는 시나리오를 재현한다 — Provider는 아직
        전혀 호출되지 않았으므로, 복구는 이 Job을 실패 처리하는 게
        아니라 그대로 재개해 정상적으로 완료시켜야 한다.
        """

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("stuck-pending"),
        )

        # 정상 경로(submit_job)를 거치지 않고, "PENDING으로 commit된
        # 직후 죽은" 상태를 직접 재현한다(request_payload_json 등은
        # submit_job과 동일한 형태로 채운다).
        from app.domains.media_asset.repository import MediaAssetRepository
        from app.domains.media_asset.fingerprint import canonical_json
        from app.domains.media_asset.fingerprint import sha256_hex

        repo = MediaAssetRepository(self.db)
        request_payload = {
            "product_candidate_id": 1, "items": ["MAIN"], "style_params": None,
        }
        stuck_job = ImageGenerationJob(
            company_id=self.company_a.id,
            product_candidate_id=1,
            provider_code="FAKE",
            prompt_fingerprint=sha256_hex(canonical_json(request_payload)),
            request_payload_json=canonical_json(request_payload),
            status=ImageJobStatus.PENDING,
            idempotency_key=req.idempotency_key,
            requested_by=1,
        )
        stuck_job = repo.add_job_no_commit(stuck_job)
        self.db.commit()

        # created_at을 과거로 되돌려 "오래 멈춰 있던" 상태를 재현한다.
        repo.update_job_fields_conditional(
            stuck_job.id, self.company_a.id, (ImageJobStatus.PENDING,),
            {"created_at": datetime.utcnow() - timedelta(hours=1)},
        )
        self.db.commit()

        recovered = self.service.recover_stalled_jobs(
            self.company_a.id, stall_timeout_seconds=300,
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].status, ImageJobStatus.SUCCEEDED)

    def test_recover_stalled_jobs_leaves_fresh_pending_jobs_alone(self):
        """
        방금 생성된 PENDING(임계값 이내)은 복구 대상이 아니다 — 정상
        진행 중인 요청을 복구 로직이 가로채면 안 된다.
        """

        from app.domains.media_asset.repository import MediaAssetRepository
        from app.domains.media_asset.fingerprint import canonical_json
        from app.domains.media_asset.fingerprint import sha256_hex

        repo = MediaAssetRepository(self.db)
        request_payload = {
            "product_candidate_id": 1, "items": ["MAIN"], "style_params": None,
        }
        fresh_job = ImageGenerationJob(
            company_id=self.company_a.id,
            product_candidate_id=1,
            provider_code="FAKE",
            prompt_fingerprint=sha256_hex(canonical_json(request_payload)),
            request_payload_json=canonical_json(request_payload),
            status=ImageJobStatus.PENDING,
            idempotency_key=_next_key("fresh-pending"),
            requested_by=1,
        )
        repo.add_job_no_commit(fresh_job)
        self.db.commit()

        recovered = self.service.recover_stalled_jobs(
            self.company_a.id, stall_timeout_seconds=300,
        )

        self.assertEqual(recovered, [])

    # ----------------------------------------------------
    # 취소 / 재시도
    # ----------------------------------------------------

    def test_cancel_pending_job(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("cancel-target"),
        )
        job, _ = self._submit_and_run(req, 1, self.company_a.id)

        # SUCCEEDED가 된 뒤 취소 시도 시 idempotent하게 그대로 반환.
        result = self.service.cancel_job(
            job.id, _next_key("cancel"), self.company_a.id,
        )
        self.assertEqual(result.status, ImageJobStatus.SUCCEEDED)

    def test_retry_failed_job_creates_new_job_and_respects_max_retries(self):

        with mock.patch(
            "app.domains.media_asset.job_queue_service."
            "DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY", 0,
        ):
            req = ImageGenerationJobSubmitRequest(
                product_candidate_id=1,
                items=[ImageGenerationItemRequest(purpose="MAIN")],
                idempotency_key=_next_key("retry-src"),
            )
            job, _ = self._submit_and_run(req, 1, self.company_a.id)
            self.assertEqual(job.status, ImageJobStatus.FAILED)

        with mock.patch(
            "app.domains.media_asset.job_queue_service."
            "DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY", 100,
        ):
            retried, dup = self._retry_and_run(
                job.id, _next_key("retry"), 1, self.company_a.id,
            )
            self.assertFalse(dup)
            self.assertNotEqual(retried.id, job.id)
            self.assertEqual(retried.retry_count, 1)
            self.assertEqual(retried.status, ImageJobStatus.SUCCEEDED)

    def test_retry_exceeding_max_retries_is_blocked(self):

        req = ImageGenerationJob(
            company_id=self.company_a.id, product_candidate_id=1,
            provider_code="FAKE", prompt_fingerprint="x",
            request_payload_json='{"items": ["MAIN"], "style_params": {}}',
            status=ImageJobStatus.FAILED, retry_count=2, max_retries=2,
            idempotency_key=_next_key("maxed"), requested_by=1,
        )
        self.db.add(req)
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.retry_job(
                req.id, _next_key("retry-over"), 1, self.company_a.id,
            )

    # ----------------------------------------------------
    # 고아 파일 방지
    # ----------------------------------------------------

    def test_orphan_marking_excludes_original_and_active_owners(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1, listing_package_id=10,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("orphan"),
        )
        self._submit_and_run(req, 1, self.company_a.id)

        # listing_package_id=10이 더 이상 살아있지 않다고 가정
        # (active_owner_ids에서 제외).
        orphaned = self.service.mark_orphaned_assets(
            self.company_a.id, {"LISTING_PACKAGE": set(), "PRODUCT_CANDIDATE": {1}},
        )
        self.assertEqual(len(orphaned), 1)
        self.assertEqual(orphaned[0].status, "ORPHANED")

    # ----------------------------------------------------
    # 실제 외부 호출 0건
    # ----------------------------------------------------

    def test_no_network_libraries_imported_in_media_asset_domain(self):

        import app.domains.media_asset.job_queue_service as mod
        import app.domains.media_asset.providers as providers_mod

        for module in (mod, providers_mod):
            with open(module.__file__, encoding="utf-8") as f:
                source = f.read()
            self.assertNotIn("import requests", source)
            self.assertNotIn("import httpx", source)
            self.assertNotIn("urllib.request", source)

    def test_disabled_provider_never_silently_succeeds(self):

        req = ImageGenerationJobSubmitRequest(
            product_candidate_id=1,
            provider_code="DISABLED",
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("disabled"),
        )
        job, _ = self._submit_and_run(req, 1, self.company_a.id)

        self.assertEqual(job.status, ImageJobStatus.FAILED)
        self.assertIn("연결되지 않았습니다", job.error_reason)


if __name__ == "__main__":
    unittest.main()
