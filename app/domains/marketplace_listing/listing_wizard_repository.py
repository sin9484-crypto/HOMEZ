"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_repository.py

Gate I(2026-08-08) — 상품등록 통합 마법사 Repository. 기존
repository.py와 분리한다(신규 테이블 전용, 8개 기존 테이블 계약과
섞이지 않게).

이 파일도 기존 repository.py와 동일한 두 원칙을 그대로 따른다:
1) 쓰기 메서드는 commit하지 않는다(*_no_commit) — Transaction 경계는
   Service가 소유한다.
2) company_id 없이 id만으로 단건을 조회·변경하는 메서드를 두지
   않는다.

낙관적 동시성: 모든 조건부 UPDATE는 `WHERE version = expected_version`
을 포함하고, 성공 시 `version`을 +1 한다 — rowcount == 0이면 Service가
409(버전 충돌)로 변환한다. Gate J 자동 저장이 이 원자적 계약 위에
그대로 얹힌다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.marketplace_listing.model import ListingWizard


class ListingWizardRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_for_company(
        self, wizard_id: int, company_id: int,
    ) -> ListingWizard | None:

        return (
            self.db.query(ListingWizard)
            .filter(ListingWizard.id == wizard_id)
            .filter(ListingWizard.company_id == company_id)
            .first()
        )

    def get_by_company_creation_idempotency_key(
        self, company_id: int, creation_idempotency_key: str,
    ) -> ListingWizard | None:

        return (
            self.db.query(ListingWizard)
            .filter(ListingWizard.company_id == company_id)
            .filter(
                ListingWizard.creation_idempotency_key
                == creation_idempotency_key,
            )
            .first()
        )

    def list_for_company(
        self,
        company_id: int,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[ListingWizard]:
        """
        2026-08-28 "대기 상품 정리" — `status`가 명시되면(예:
        status="ARCHIVED") 그 값을 그대로 신뢰하고 별도 제외를 얹지
        않는다. `status`가 없을 때만 `include_archived`가 의미를 갖는다
        — 기본은 ARCHIVED를 뺀 목록(사용자 결정: "목록에서는 기본적으로
        숨기되 '삭제된 대기 상품' 필터에서 조회할 수 있게 한다").
        """

        query = (
            self.db.query(ListingWizard)
            .filter(ListingWizard.company_id == company_id)
        )
        if status is not None:
            query = query.filter(ListingWizard.status == status)
        elif not include_archived:
            query = query.filter(ListingWizard.status != "ARCHIVED")

        return (
            query.order_by(ListingWizard.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def list_ids_for_bulk_archive(
        self,
        company_id: int,
        status: str | None,
        deletable_statuses: tuple[str, ...],
        limit: int,
    ) -> list[int]:
        """
        "전체 필터 삭제" 모드 전용 — 현재 필터(status)에 더해 삭제
        가능한 상태만, 그리고 아직 실제 채널에 아무것도 materialize되지
        않은 행만 id로 반환한다(fail-closed 이중 게이트, archive_
        conditional()의 WHERE와 동일한 조건을 그대로 미리보기에도
        적용해 "미리 보여준 건수"와 "실제 처리 건수"가 어긋나지 않게
        한다). limit+1까지 가져와 상한 초과 여부를 호출자가 판단할 수
        있게 한다(export_csv()와 동일한 정책).
        """

        query = (
            self.db.query(ListingWizard.id)
            .filter(ListingWizard.company_id == company_id)
            .filter(ListingWizard.status.in_(deletable_statuses))
            .filter(ListingWizard.materialized_listing_ids_json == "[]")
        )
        if status is not None:
            query = query.filter(ListingWizard.status == status)

        rows = (
            query.order_by(ListingWizard.id.asc())
            .limit(limit)
            .all()
        )
        return [row[0] for row in rows]

    # --------------------------------------------------
    # 생성
    # --------------------------------------------------

    def add_no_commit(self, wizard: ListingWizard) -> ListingWizard:

        self.db.add(wizard)
        self.db.flush()

        return wizard

    # --------------------------------------------------
    # 낙관적 동시성 조건부 UPDATE — 공용 primitive.
    # --------------------------------------------------

    def _update_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        values: dict,
        expected_statuses: tuple[str, ...] | None = None,
        autosave_client_token: str | None = None,
    ) -> int:

        stmt = (
            update(ListingWizard)
            .where(ListingWizard.id == wizard_id)
            .where(ListingWizard.company_id == company_id)
            .where(ListingWizard.version == expected_version)
        )
        if expected_statuses is not None:
            stmt = stmt.where(ListingWizard.status.in_(expected_statuses))

        now = datetime.utcnow()
        merged_values = dict(values)
        # Gate J(2026-08-08) — 토큰이 있는 저장(자동 저장 하트비트 포함
        # 모든 단계 저장)만 autosave 메타데이터를 갱신한다. 토큰이 없는
        # 호출(현재는 없음, 향후 서버 내부 호출 대비)은 이 두 컬럼을
        # 건드리지 않는다.
        if autosave_client_token is not None:
            merged_values["autosave_client_token"] = autosave_client_token
            merged_values["autosave_saved_at"] = now

        stmt = stmt.values(
            version=expected_version + 1,
            updated_at=now,
            **merged_values,
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def update_source_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        product_candidate_id: int,
        current_step: str,
        autosave_client_token: str | None = None,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "product_candidate_id": product_candidate_id,
                "current_step": current_step,
            },
            expected_statuses=expected_statuses,
            autosave_client_token=autosave_client_token,
        )

    def update_draft_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        draft_json: str,
        current_step: str,
        autosave_client_token: str | None = None,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {"draft_json": draft_json, "current_step": current_step},
            expected_statuses=expected_statuses,
            autosave_client_token=autosave_client_token,
        )

    def update_media_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        selected_media_asset_ids_json: str,
        current_step: str,
        autosave_client_token: str | None = None,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "selected_media_asset_ids_json": (
                    selected_media_asset_ids_json
                ),
                "current_step": current_step,
            },
            expected_statuses=expected_statuses,
            autosave_client_token=autosave_client_token,
        )

    def update_channels_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        channel_selections_json: str,
        current_step: str,
        autosave_client_token: str | None = None,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "channel_selections_json": channel_selections_json,
                "current_step": current_step,
            },
            expected_statuses=expected_statuses,
            autosave_client_token=autosave_client_token,
        )

    def update_economics_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        economics_input_json: str,
        economics_result_json: str,
        current_step: str,
        autosave_client_token: str | None = None,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "economics_input_json": economics_input_json,
                "economics_result_json": economics_result_json,
                "current_step": current_step,
            },
            expected_statuses=expected_statuses,
            autosave_client_token=autosave_client_token,
        )

    def update_validation_result_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        validation_result_json: str,
        new_status: str,
        current_step: str,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "validation_result_json": validation_result_json,
                "status": new_status,
                "current_step": current_step,
            },
            expected_statuses=expected_statuses,
        )

    def update_approval_package_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        approval_package_json: str,
        approval_fingerprint: str,
    ) -> int:
        """
        승인 패키지를 (재)계산해 저장한다 — 이전 fingerprint를 무효화하는
        지점(승인 전 draft/channel/economics 등이 바뀌면 이 메서드가
        다시 호출되어 approval_fingerprint가 갱신되고, 이전 nonce로는
        더 이상 유효하지 않게 된다).
        """

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "approval_package_json": approval_package_json,
                "approval_fingerprint": approval_fingerprint,
            },
            expected_statuses=expected_statuses,
        )

    def mark_approved_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_fingerprint: str,
        approved_by_user_id: int,
        approved_at: datetime,
        approval_history_json: str,
    ) -> int:
        """
        `expected_fingerprint`가 지금 저장된 값과 다르면(승인 미리보기를
        본 뒤 내용이 바뀌었으면) WHERE 자체가 걸리지 않아 rowcount=0으로
        실패한다 — 재계산 없이 옛 패키지를 승인하는 경로를 원천 차단.
        """

        stmt = (
            update(ListingWizard)
            .where(ListingWizard.id == wizard_id)
            .where(ListingWizard.company_id == company_id)
            .where(ListingWizard.version == expected_version)
            .where(ListingWizard.approval_fingerprint == expected_fingerprint)
            .where(ListingWizard.status == "READY_FOR_APPROVAL")
            .values(
                version=expected_version + 1,
                updated_at=datetime.utcnow(),
                status="APPROVED",
                approved_by_user_id=approved_by_user_id,
                approved_at=approved_at,
                approval_history_json=approval_history_json,
            )
        )
        result = self.db.execute(stmt)

        return result.rowcount

    def revoke_approval_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        approval_history_json: str,
    ) -> int:
        """
        Gate Q-1(2026-08-09) — APPROVED 상태에서만 걸리는 조건부
        UPDATE(`WHERE status == 'APPROVED'`)로, 동시에 여러 승인 취소
        요청이 들어와도 정확히 하나만 성공한다(다른 조건부 UPDATE와
        동일한 원자성 계약). `approval_fingerprint`/`approved_by_
        user_id`/`approved_at`은 비워 "현재 유효한 승인이 없음"을
        명확히 하고, 기존 승인 Package(`approval_package_json`)
        자체는 덮어쓰지 않는다 — 호출자가 그 값을 취소 전에 이미
        `approval_history_json`에 스냅샷으로 옮겨 담아 전달한다.
        """

        stmt = (
            update(ListingWizard)
            .where(ListingWizard.id == wizard_id)
            .where(ListingWizard.company_id == company_id)
            .where(ListingWizard.version == expected_version)
            .where(ListingWizard.status == "APPROVED")
            .values(
                version=expected_version + 1,
                updated_at=datetime.utcnow(),
                status="NEEDS_CORRECTION",
                approval_fingerprint=None,
                approved_by_user_id=None,
                approved_at=None,
                approval_history_json=approval_history_json,
            )
        )
        result = self.db.execute(stmt)

        return result.rowcount

    def update_status_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
        **extra_values,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {"status": new_status, **extra_values},
            expected_statuses=expected_statuses,
        )

    def archive_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        deletable_statuses: tuple[str, ...],
        deleted_by_user_id: int,
        delete_reason: str,
        deletion_request_id: str,
    ) -> int:
        """
        2026-08-28 "대기 상품 정리" — soft delete(=ARCHIVED 전이).
        `status_before_archive`에 지금 status를 그대로 옮겨 담아
        restore_conditional()이 정확히 되돌릴 수 있게 한다.
        materialized_listing_ids_json == '[]' 게이트를 여기서도 다시
        확인한다(Service의 사전 판정과 별개로, 이 UPDATE 자체가
        원자적으로 최종 방어선이 되도록 — TOCTOU 방지).
        """

        stmt = (
            update(ListingWizard)
            .where(ListingWizard.id == wizard_id)
            .where(ListingWizard.company_id == company_id)
            .where(ListingWizard.version == expected_version)
            .where(ListingWizard.status.in_(deletable_statuses))
            .where(ListingWizard.materialized_listing_ids_json == "[]")
            .values(
                version=expected_version + 1,
                updated_at=datetime.utcnow(),
                status_before_archive=ListingWizard.status,
                status="ARCHIVED",
                deleted_at=datetime.utcnow(),
                deleted_by_user_id=deleted_by_user_id,
                delete_reason=delete_reason,
                deletion_request_id=deletion_request_id,
                restored_at=None,
                restored_by_user_id=None,
            )
        )
        result = self.db.execute(stmt)

        return result.rowcount

    def restore_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        restored_by_user_id: int,
    ) -> int:
        """
        ARCHIVED에서만 걸린다 — `status_before_archive`가 가리키는
        상태로 정확히 되돌리고, 삭제 스냅샷 필드는 전부 비운다(과거
        기록은 audit_logs에 남아 있으므로 이 행에는 "현재 상태"만
        유지한다는 이 파일 전역 원칙과 동일).
        """

        stmt = (
            update(ListingWizard)
            .where(ListingWizard.id == wizard_id)
            .where(ListingWizard.company_id == company_id)
            .where(ListingWizard.version == expected_version)
            .where(ListingWizard.status == "ARCHIVED")
            .values(
                version=expected_version + 1,
                updated_at=datetime.utcnow(),
                status=ListingWizard.status_before_archive,
                status_before_archive=None,
                deleted_at=None,
                deleted_by_user_id=None,
                delete_reason=None,
                deletion_request_id=None,
                restored_at=datetime.utcnow(),
                restored_by_user_id=restored_by_user_id,
            )
        )
        result = self.db.execute(stmt)

        return result.rowcount

    def append_materialized_listing_ids_conditional(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        materialized_listing_ids_json: str,
        new_status: str,
    ) -> int:

        return self._update_conditional(
            wizard_id, company_id, expected_version,
            {
                "materialized_listing_ids_json": (
                    materialized_listing_ids_json
                ),
                "status": new_status,
                "current_step": "RESULTS",
            },
        )


__all__ = ["ListingWizardRepository"]
