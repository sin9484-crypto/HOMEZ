"""
=========================================================
Homez OS

File : app/domains/listing_package/service.py

create_listing_package() — 상품 초안·이미지·채널 정보를 한 번에
묶는 최종 검토 단위 생성. 실제 AI 텍스트 생성 API나 이미지 생성
API를 호출하지 않는다: 초안은 ProductCandidate의 이미 저장된
필드(product_name/evidence_summary/각종 score)로부터 결정론적으로
파생하고, 이미지는 app.domains.media_asset의 FakeImageGenerationProvider
로만 만든다(실제 Provider 연동은 별도 승인 대상 — 이번 Phase 범위
밖임을 최종 보고에 명시한다).

승인(approve_listing_package)은 "사용자 최종 승인 1회" 원칙을
그대로 구현한다 — request/approve 2단계가 아니라 단일 호출이다.
호출자가 자신이 지금 보고 있는 package_fingerprint를 함께 보내야
하고(expected_package_fingerprint), 그 사이 패키지가 바뀌어 현재
fingerprint와 다르면 409(ConflictException)로 거부한다 — "승인 후
payload가 수정되면 승인을 무효화하라"는 요구를 승인 시점 자체에서도
강제한다. 승인 시점의 draft/이미지/가격/채널를 payload_snapshot_json에
불변 스냅샷으로 남긴다.
=========================================================
"""

import hashlib
import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.listing_package.constants import ListingPackageApprovalStatus
from app.domains.listing_package.constants import ListingPackageMode
from app.domains.listing_package.constants import ListingPackageStatus
from app.domains.listing_package.fingerprint import canonical_json
from app.domains.listing_package.fingerprint import compute_package_fingerprint
from app.domains.listing_package.fingerprint import sha256_hex
from app.domains.listing_package.model import ListingPackage
from app.domains.listing_package.model import ListingPackageApproval
from app.domains.listing_package.repository import ListingPackageRepository
from app.domains.listing_package.schema import ChannelReadiness
from app.domains.listing_package.schema import CreateListingPackageRequest
from app.domains.listing_package.schema import ListingPackageApprovalRequest
from app.domains.listing_package.schema import ListingPackageRejectionRequest
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.schema import ImageGenerationItemRequest
from app.domains.media_asset.schema import ImageGenerationJobSubmitRequest
from app.domains.media_asset.storage import media_file_exists
from app.domains.media_asset.storage import read_media_file
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.service import ProductCandidateService

_PACKAGE_NOT_FOUND = "ListingPackage를 찾을 수 없습니다."
_CANDIDATE_NOT_FOUND = "ProductCandidate를 찾을 수 없습니다."


def _build_draft_from_candidate(candidate: ProductCandidate) -> dict:
    """
    실제 AI 텍스트 생성 호출 없이, 이미 저장된 ProductCandidate
    필드로부터 결정론적으로 초안을 파생한다 — 이 Phase의 명시적
    단순화(실제 문구 생성 AI 연동은 범위 밖, 최종 보고에 기록한다).
    """

    keywords = [
        token
        for token in (
            candidate.brand_hint, candidate.category_hint,
            *candidate.product_name.split(),
        )
        if token
    ]

    return {
        "product_name": candidate.product_name,
        "description": (
            candidate.evidence_summary
            or f"{candidate.product_name} — 자동 생성된 초안(검토 필요)."
        ),
        "keywords": keywords[:10],
        "price": None,
        "options": [],
    }


def _build_recommendation(candidate: ProductCandidate) -> dict:

    margin = candidate.margin_score
    risk = candidate.risk_score

    estimated_revenue = None
    if margin is not None:
        estimated_revenue = round(margin * 10000, 2)

    risk_summary = None
    if risk is not None:
        risk_summary = f"위험 점수 {risk:.2f}/1.0(자동 산정, 참고용)"

    recommendation_reason = candidate.evidence_summary or (
        "ProductCandidate 자동 점수 기반 추천(상세 근거 없음)"
    )

    return {
        "estimated_revenue": estimated_revenue,
        "risk_summary": risk_summary,
        "recommendation_reason": recommendation_reason,
    }


class ListingPackageService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = ListingPackageRepository(db)
        self.marketplace_repository = MarketplaceListingRepository(db)
        # 2026-08-02 Gate R5: Image Job이 이제 별도 Worker에서 비동기로
        # 실행되므로, 그 Job이 실제로 끝난 시점(성공이든 실패든)에
        # 이 패키지의 fingerprint를 다시 계산하고 READY 전이를
        # 재확인해야 한다 — on_job_completed 콜백으로 그 시점을 받는다.
        self.image_queue_service = ImageGenerationJobQueueService(
            db, on_job_completed=self._handle_image_job_completed,
        )

    # ------------------------------------------------
    # 생성
    # ------------------------------------------------

    def create_listing_package(
        self, data: CreateListingPackageRequest, created_by: int,
        company_id: int,
    ) -> tuple[ListingPackage, bool]:

        request_payload = {
            "product_candidate_id": data.product_candidate_id,
            "channel_selections": [
                {"channel_code": c.channel_code, "fulfillment_mode": c.fulfillment_mode}
                for c in data.channel_selections
            ],
            "image_options": data.image_options.model_dump(),
            "mode": data.mode,
        }
        request_fingerprint = sha256_hex(canonical_json(request_payload))

        existing = self.repository.get_package_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint == request_fingerprint:
                return existing, True

            raise ConflictException(
                "이 idempotency_key는 이미 다른 요청 내용으로 사용되었습니다 "
                "— 같은 key로 다른 payload를 재사용할 수 없습니다.",
            )

        if data.mode not in ListingPackageMode.ALL:
            raise BadRequestException(f"알 수 없는 mode입니다: {data.mode}")

        candidate = (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.id == data.product_candidate_id)
            .first()
        )
        if candidate is None:
            raise NotFoundException(_CANDIDATE_NOT_FOUND)

        # 2026-08-14 테넌트 격리 감사(Gate R13) — candidate.status(전역)
        # 대신 "이 회사 관점의 승인 상태"를 확인한다(다른 회사의 승인에
        # 무임승차 방지, coupang/marketplace_listing과 동일한 게이트).
        ProductCandidateService(self.db).require_approved_for_company(
            data.product_candidate_id, company_id,
        )

        draft = _build_draft_from_candidate(candidate)
        recommendation = _build_recommendation(candidate)
        channel_readiness = self._compute_channel_readiness(
            data.channel_selections,
        )

        draft_payload_json = canonical_json(draft)
        channel_selection_json = canonical_json(request_payload["channel_selections"])
        image_options_json = canonical_json(request_payload["image_options"])
        missing_fields_json = canonical_json({
            item.channel_code: item.missing_reasons
            for item in channel_readiness
            if not item.ready
        })

        package_fingerprint = compute_package_fingerprint(
            draft_payload_json, channel_selection_json, [], data.mode,
        )
        # (생성 시점에는 아직 이미지가 없으므로 빈 목록 — _recompute_
        # fingerprint()가 이미지 생성 후 실제 descriptor로 다시 계산한다.)

        package = ListingPackage(
            company_id=company_id,
            product_candidate_id=data.product_candidate_id,
            mode=data.mode,
            status=ListingPackageStatus.DRAFT,
            draft_payload_json=draft_payload_json,
            channel_selection_json=channel_selection_json,
            image_options_json=image_options_json,
            missing_fields_json=missing_fields_json,
            estimated_revenue=recommendation["estimated_revenue"],
            risk_summary=recommendation["risk_summary"],
            recommendation_reason=recommendation["recommendation_reason"],
            package_fingerprint=package_fingerprint,
            request_fingerprint=request_fingerprint,
            idempotency_key=data.idempotency_key,
            created_by=created_by,
        )

        try:
            package = self.repository.add_package_no_commit(package)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_package_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise
            if winner.request_fingerprint != request_fingerprint:
                raise ConflictException(
                    "이 idempotency_key는 이미 다른 요청 내용으로 "
                    "사용되었습니다.",
                )

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        image_job_id = self._submit_image_job(
            package, data, created_by, company_id,
        )

        if image_job_id is not None:
            self._recompute_fingerprint(package.id, company_id)

        self._transition_to_ready_if_possible(package.id, company_id)

        final_package = self.repository.get_package_for_company(
            package.id, company_id,
        )

        return final_package, False

    def _handle_image_job_completed(self, job) -> None:
        """
        2026-08-02 Gate R5 — ImageGenerationJobQueueService가 (Worker를
        통해) listing_package_id를 가진 Job의 실행을 마쳤을 때 호출하는
        콜백. 성공/부분성공/실패 어느 쪽이든 이 시점에 패키지의 이미지
        구성이 최종 확정되므로, fingerprint를 다시 계산하고 READY 전이
        조건을 재확인한다.
        """

        self._recompute_fingerprint(job.listing_package_id, job.company_id)
        self._transition_to_ready_if_possible(
            job.listing_package_id, job.company_id,
        )

    def _submit_image_job(
        self, package: ListingPackage, data: CreateListingPackageRequest,
        created_by: int, company_id: int,
    ) -> int | None:

        items = (
            [ImageGenerationItemRequest(purpose="MAIN")]
            * data.image_options.main_count
            + [ImageGenerationItemRequest(purpose="DETAIL")]
            * data.image_options.detail_count
        )

        if not items:
            return None

        job_request = ImageGenerationJobSubmitRequest(
            product_candidate_id=data.product_candidate_id,
            listing_package_id=package.id,
            provider_code=data.image_options.provider_code,
            items=items,
            style_params=data.image_options.style_params,
            idempotency_key=f"{data.idempotency_key}:images",
        )

        job, _duplicate = self.image_queue_service.submit_job(
            job_request, created_by, company_id,
        )

        return job.id

    def compute_channel_readiness_for_package(
        self, package: ListingPackage,
    ) -> list[ChannelReadiness]:

        from app.domains.listing_package.schema import ChannelSelectionItem

        selections = [
            ChannelSelectionItem(**item)
            for item in json.loads(package.channel_selection_json)
        ]

        return self._compute_channel_readiness(selections)

    def _compute_channel_readiness(
        self, channel_selections,
    ) -> list[ChannelReadiness]:

        results = []
        for selection in channel_selections:
            reasons = []

            channel = self.marketplace_repository.get_channel_by_code(
                selection.channel_code,
            )
            if channel is None:
                reasons.append("등록되지 않은 채널입니다.")
            else:
                capability = (
                    self.marketplace_repository.get_capability_for_channel_mode(
                        channel.id, selection.fulfillment_mode,
                    )
                )
                if capability is None:
                    reasons.append("이 채널에서 확인되지 않은 판매 방식입니다.")
                elif capability.status != CapabilityStatus.VERIFIED:
                    reasons.append("판매 방식 캡빌리티가 아직 VERIFIED가 아닙니다.")
                elif not capability.is_supported:
                    reasons.append("이 채널은 이 판매 방식을 지원하지 않습니다.")

            results.append(ChannelReadiness(
                channel_code=selection.channel_code,
                fulfillment_mode=selection.fulfillment_mode,
                ready=len(reasons) == 0,
                missing_reasons=reasons,
            ))

        return results

    @staticmethod
    def _build_image_descriptors(assets: list) -> list[dict]:
        """
        MediaAsset 행을 fingerprint/승인 스냅샷용 descriptor로 변환한다.
        `assets`는 반드시 실제 표시 순서 그대로 전달해야 한다(여기서도
        정렬하지 않는다 — 순서 자체가 지문 입력이다).
        """

        return [
            {
                "asset_id": asset.id,
                "sha256": asset.sha256_hex,
                "purpose": asset.purpose,
                "sequence": asset.display_order,
                "mime_type": asset.mime_type,
                "width": asset.width,
                "height": asset.height,
                "file_size": asset.file_size_bytes,
                "source_asset_id": asset.source_asset_id,
                "storage_path": asset.storage_path,
            }
            for asset in assets
        ]

    def _recompute_fingerprint(self, package_id: int, company_id: int) -> None:

        package = self.repository.get_package_for_company(
            package_id, company_id,
        )
        if package is None:
            return

        assets = self.image_queue_service.list_active_assets(
            company_id, MediaAssetOwnerType.LISTING_PACKAGE, package_id,
        )
        image_descriptors = self._build_image_descriptors(assets)

        new_fingerprint = compute_package_fingerprint(
            package.draft_payload_json, package.channel_selection_json,
            image_descriptors, package.mode,
        )

        self.repository.update_package_fields_conditional(
            package_id, company_id,
            ListingPackageStatus.ALL,
            {"package_fingerprint": new_fingerprint},
        )
        self.db.commit()

    def _transition_to_ready_if_possible(
        self, package_id: int, company_id: int,
    ) -> None:

        package = self.repository.get_package_for_company(
            package_id, company_id,
        )
        if package is None or package.status != ListingPackageStatus.DRAFT:
            return

        missing = json.loads(package.missing_fields_json)
        if missing:
            return

        self.repository.update_package_fields_conditional(
            package_id, company_id, (ListingPackageStatus.DRAFT,),
            {"status": ListingPackageStatus.READY_FOR_REVIEW},
        )
        self.db.commit()

    # ------------------------------------------------
    # 조회
    # ------------------------------------------------

    def get_package(
        self, package_id: int, company_id: int,
    ) -> ListingPackage:

        package = self.repository.get_package_for_company(
            package_id, company_id,
        )
        if package is None:
            raise NotFoundException(_PACKAGE_NOT_FOUND)

        return package

    def is_submit_ready(self, package: ListingPackage, company_id: int) -> bool:

        if package.status not in (
            ListingPackageStatus.READY_FOR_REVIEW, ListingPackageStatus.APPROVED,
        ):
            return False

        approval = self.current_valid_approval(package.id, company_id)

        return approval is not None

    # ------------------------------------------------
    # 승인 / 거절 — 1회 승인 원칙(별도 request 단계 없음)
    # ------------------------------------------------

    def approve_package(
        self, package_id: int, data: ListingPackageApprovalRequest,
        approved_by: int, company_id: int,
    ) -> tuple[ListingPackageApproval, bool]:

        existing = self.repository.get_approval_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True

        package = self.repository.get_package_for_company(
            package_id, company_id,
        )
        if package is None:
            raise NotFoundException(_PACKAGE_NOT_FOUND)

        if package.status not in (
            ListingPackageStatus.READY_FOR_REVIEW, ListingPackageStatus.APPROVED,
        ):
            raise BadRequestException(
                "READY_FOR_REVIEW 상태의 패키지만 승인할 수 있습니다. "
                f"(현재: {package.status})",
            )

        if package.package_fingerprint != data.expected_package_fingerprint:
            raise ConflictException(
                "승인하려는 패키지 상태가 화면에서 본 것과 다릅니다"
                "(그 사이 초안·이미지·채널 구성이 바뀌었습니다) — 새로고침 "
                "후 다시 확인해 승인하세요.",
            )

        snapshot_assets = self.image_queue_service.list_active_assets(
            company_id, MediaAssetOwnerType.LISTING_PACKAGE, package_id,
        )

        snapshot = {
            "draft_payload": json.loads(package.draft_payload_json),
            "channel_selection": json.loads(package.channel_selection_json),
            "image_options": json.loads(package.image_options_json),
            "mode": package.mode,
            # 2026-08-02 Gate R4: asset_id 목록이 아니라 승인 시점의
            # 이미지 내용을 통째로 스냅샷한다(제출 직전 실제 파일
            # SHA-256을 이 스냅샷과 다시 대조하기 위함).
            "images": self._build_image_descriptors(snapshot_assets),
        }

        approval = ListingPackageApproval(
            company_id=company_id,
            listing_package_id=package_id,
            status=ListingPackageApprovalStatus.APPROVED,
            package_fingerprint_snapshot=package.package_fingerprint,
            payload_snapshot_json=canonical_json(snapshot),
            approved_by=approved_by,
            approved_at=datetime.utcnow(),
            requested_by=approved_by,
            idempotency_key=data.idempotency_key,
        )

        try:
            approval = self.repository.add_approval_no_commit(approval)

            self.repository.update_package_fields_conditional(
                package_id, company_id,
                (ListingPackageStatus.READY_FOR_REVIEW, ListingPackageStatus.APPROVED),
                {"status": ListingPackageStatus.APPROVED},
            )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_approval_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        return approval, False

    def reject_package(
        self, package_id: int, data: ListingPackageRejectionRequest,
        rejected_by: int, company_id: int,
    ) -> tuple[ListingPackageApproval, bool]:

        existing = self.repository.get_approval_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True

        package = self.repository.get_package_for_company(
            package_id, company_id,
        )
        if package is None:
            raise NotFoundException(_PACKAGE_NOT_FOUND)

        approval = ListingPackageApproval(
            company_id=company_id,
            listing_package_id=package_id,
            status=ListingPackageApprovalStatus.REJECTED,
            package_fingerprint_snapshot=package.package_fingerprint,
            payload_snapshot_json=canonical_json({
                "draft_payload": json.loads(package.draft_payload_json),
            }),
            approved_by=None,
            approved_at=None,
            requested_by=rejected_by,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
        )

        try:
            approval = self.repository.add_approval_no_commit(approval)

            self.repository.update_package_fields_conditional(
                package_id, company_id, ListingPackageStatus.ALL,
                {"status": ListingPackageStatus.CANCELLED},
            )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_approval_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        return approval, False

    def current_valid_approval(
        self, package_id: int, company_id: int,
    ) -> ListingPackageApproval | None:
        """
        fail-closed — 최신 행이 APPROVED가 아니거나, 그 스냅샷
        fingerprint가 지금의 package.package_fingerprint와 다르면
        무효(None). 승인 이후 패키지가 재계산되면(이미지 재생성,
        초안 변경 등) 자동으로 무효화된다.
        """

        latest = self.repository.get_latest_approval(package_id, company_id)
        if latest is None or latest.status != ListingPackageApprovalStatus.APPROVED:
            return None

        package = self.repository.get_package_for_company(
            package_id, company_id,
        )
        if package is None:
            return None

        if latest.package_fingerprint_snapshot != package.package_fingerprint:
            return None

        return latest

    def _verify_approved_image_files_unchanged(
        self, approval: ListingPackageApproval,
    ) -> None:
        """
        2026-08-02 Gate R4 — package_fingerprint 재계산은 DB 컬럼
        (MediaAsset.sha256_hex 등)이 바뀌었을 때만 승인을 무효화한다.
        하지만 파일이 앱 "외부"에서 직접 바뀌면(디스크 파일 교체 등)
        그 DB 컬럼 자체는 그대로이므로 fingerprint 비교만으로는 잡을
        수 없다 — 그래서 제출 직전에 승인 스냅샷에 저장된 각 이미지의
        실제 파일을 다시 읽어 SHA-256을 새로 계산하고, 스냅샷 값과
        정확히 일치하는지 여기서 별도로 재검증한다. 파일이 없거나,
        읽기 자체가 실패하거나, 해시가 다르면 전부 fail-closed로
        차단한다.
        """

        snapshot = json.loads(approval.payload_snapshot_json)
        images = snapshot.get("images", [])
        media_root = self.image_queue_service.media_root

        for descriptor in images:
            storage_path = descriptor.get("storage_path")
            expected_sha256 = descriptor.get("sha256")

            if not storage_path or not expected_sha256:
                raise ForbiddenException(
                    "승인 스냅샷의 이미지 정보가 손상되었습니다 — 제출을 "
                    "차단합니다.",
                )

            if not media_file_exists(media_root, storage_path):
                raise ForbiddenException(
                    f"승인된 이미지 파일을 찾을 수 없습니다(asset_id="
                    f"{descriptor.get('asset_id')}) — 제출을 차단합니다.",
                )

            try:
                actual_bytes = read_media_file(media_root, storage_path)
            except Exception as exc:  # noqa: BLE001
                raise ForbiddenException(
                    f"승인된 이미지 파일을 읽는 중 오류가 발생했습니다"
                    f"(asset_id={descriptor.get('asset_id')}) — 제출을 "
                    "차단합니다.",
                ) from exc

            actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()

            if actual_sha256 != expected_sha256:
                raise ForbiddenException(
                    "승인 이후 이미지 파일 내용이 변경되었습니다(asset_id="
                    f"{descriptor.get('asset_id')}) — 승인이 더 이상 "
                    "유효하지 않습니다. 다시 승인해야 합니다.",
                )

    def submit_to_channels(
        self, package_id: int, company_id: int,
    ) -> None:
        """
        실제 판매채널 제출 진입점 — 이번 Phase는 여기서 항상
        차단한다(외부 API 호출 금지). 서버가 승인 여부를 강제하는
        지점을 명시적으로 보여주기 위해 남겨둔다(오토 모드에서도
        승인 없는 제출은 이 지점에서 막힌다).

        유효한 승인 fingerprint 확인 뒤에도, 승인 스냅샷에 저장된
        이미지 파일의 실제 SHA-256을 다시 대조한다(Gate R4) — 이
        재검증까지 전부 통과해야 "이번 Phase 범위 밖" 차단에 도달한다.
        """

        approval = self.current_valid_approval(package_id, company_id)
        if approval is None:
            raise ForbiddenException(
                "유효한 최종 승인이 없어 제출할 수 없습니다 — 승인 후 "
                "패키지가 바뀌었다면 다시 승인해야 합니다.",
            )

        self._verify_approved_image_files_unchanged(approval)

        raise BadRequestException(
            "실제 판매채널 제출은 이번 Phase 범위 밖입니다(외부 API "
            "호출 금지) — 별도 승인 후 연결합니다.",
        )


__all__ = [
    "ListingPackageService",
]
