"""
=========================================================
Homez OS

File : app/domains/recall_notice/service.py

2026-09-15 전면 감사 후속(Phase 9I/9J, HOMEZ_USER_OPERATION_SETTINGS.md
10-17/10-18).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)
from app.domains.recall_notice.constants import RecallCheckJobMode
from app.domains.recall_notice.constants import RecallCheckRunStatus
from app.domains.recall_notice.constants import RecallProductBlockStatus
from app.domains.recall_notice.model import RecallCheckJobState
from app.domains.recall_notice.model import RecallCheckRun
from app.domains.recall_notice.model import RecallNotice
from app.domains.recall_notice.model import RecallProductBlock
from app.domains.recall_notice.provider import RecallNoticeProvider
from app.domains.recall_notice.repository import RecallNoticeRepository


def _dedupe_key(record) -> str:

    parts = [
        (record.product_identifier or "").strip().casefold(),
        (record.manufacturer or "").strip().casefold(),
        (record.model or "").strip().casefold(),
        record.announcement_date.isoformat() if record.announcement_date else "",
        (record.source or "").strip().casefold(),
    ]
    return "|".join(parts)


class RecallNoticeService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = RecallNoticeRepository(db)

    # ------------------------------
    # Phase 9I — 매일 확인 Job
    # ------------------------------

    def get_job_mode(self) -> str:

        state = self.repository.get_latest_job_state()
        return state.mode if state is not None else RecallCheckJobMode.DEFAULT

    def set_job_mode(
        self, mode: str, *, set_by: int, is_admin: bool,
    ) -> str:

        if not is_admin:
            raise ForbiddenException(
                "리콜/판매중지 확인 Job 모드 변경은 관리자만 가능합니다.",
            )
        if mode not in RecallCheckJobMode.ALL:
            raise BadRequestException(f"알 수 없는 모드입니다: {mode}")

        self.repository.add_job_state(
            RecallCheckJobState(mode=mode, set_by=set_by),
        )
        return mode

    def run_daily_check(self, provider: RecallNoticeProvider) -> RecallCheckRun:
        """Fake Provider든 실제 Provider든 동일하게 동작한다 — 항목을
        하나씩 소비하며 새 공고만 저장하고, 도중에 예외가 나도 이미
        처리한 항목은 그대로 커밋된 채 남는다(부분 실패). 이 메서드
        자체는 Job 모드(PAUSED/ACTIVE)를 보지 않는다 — 모드 판단은
        호출부(스케줄러 Job 함수)의 책임이다."""

        started_at = datetime.utcnow()
        found = 0
        new = 0
        status = RecallCheckRunStatus.SUCCESS
        error_detail: str | None = None

        try:
            for record in provider.fetch_notices():
                found += 1
                key = _dedupe_key(record)
                existing = self.repository.get_notice_by_dedupe_key(key)
                if existing is None:
                    self.repository.add_notice(RecallNotice(
                        product_identifier=record.product_identifier,
                        manufacturer=record.manufacturer,
                        model=record.model,
                        reason=record.reason,
                        announcement_date=record.announcement_date,
                        source=record.source,
                        dedupe_key=key,
                    ))
                    new += 1
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 — 부분 실패를 그대로 기록한다
            self.db.commit()  # 이미 flush된 새 공고는 보존한다.
            status = (
                RecallCheckRunStatus.PARTIAL_FAILURE if found > 0
                else RecallCheckRunStatus.FAILURE
            )
            error_detail = f"{type(exc).__name__}: {exc}"[:500]

        run = RecallCheckRun(
            provider_name=type(provider).__name__,
            status=status,
            notices_found_count=found,
            new_notices_count=new,
            error_detail=error_detail,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        return self.repository.add_check_run(run)

    # ------------------------------
    # Phase 9J — 확인된 문제 상품 차단
    # ------------------------------

    def block_product(
        self, *, company_id: int, product_identifier: str, reason: str,
        recall_notice_id: int | None = None,
    ) -> RecallProductBlock:
        """이미 BLOCKED 상태인 활성 차단이 있으면 그대로 반환한다
        (중복 차단 행을 만들지 않는다). 회사별로 완전히 격리된다."""

        existing = self.repository.get_active_block(
            company_id, product_identifier,
        )
        if existing is not None:
            return existing

        if not reason or not reason.strip():
            raise BadRequestException("차단 사유를 입력해야 합니다.")

        block = RecallProductBlock(
            company_id=company_id, product_identifier=product_identifier,
            recall_notice_id=recall_notice_id, reason=reason.strip(),
            status=RecallProductBlockStatus.BLOCKED,
        )
        block = self.repository.add_block(block)

        self._notify_block(block)

        return block

    def _notify_block(self, block: RecallProductBlock) -> None:
        """사용자 관리자(회사 SUPER_ADMIN)에게는 기존 알림 배선을
        그대로 쓴다. 2026-09-16 개인 베타 잔여 작업(Phase 5, 10-18) —
        "서버 관리자" 전용 채널(`platform_alert`)이 새로 생겨, 아래
        두 통지(회사 관리자용/서버 관리자용)는 서로 완전히 독립된
        전달기록을 남긴다(감사 로그가 알림 전달 성공의 대체물이
        아니듯, 이 둘도 서로의 대체물이 아니다)."""

        from app.domains.platform_alert.hooks import (
            notify_recall_sale_stop_detected,
        )

        notify_recall_sale_stop_detected(
            self.db,
            detail=f"product_identifier={block.product_identifier} reason={block.reason}",
            entity_ref=f"recall_product_block:{block.id}",
            idempotency_key=f"recall-product-block:{block.id}",
        )

        try:
            from app.core.audit_db import write_audit_log
            write_audit_log(
                self.db, company_id=block.company_id, user_id=None,
                action="RECALL_PRODUCT_BLOCKED",
                entity="recall_product_blocks", entity_id=str(block.id),
                description=(
                    f"상품({block.product_identifier}) 리콜/판매중지 "
                    f"확인으로 차단됨: {block.reason}"
                ),
            )
        except Exception:  # noqa: BLE001 — 감사 로그 실패가 차단 자체를 막지 않는다
            pass

        try:
            from app.domains.user.model import User

            for user in (
                self.db.query(User)
                .filter(
                    User.company_id == block.company_id,
                    User.is_active.is_(True),
                )
                .all()
            ):
                if (user.role or "").strip().upper() != "SUPER_ADMIN":
                    continue

                dispatch_operational_event(
                    self.db, "RECALL_PRODUCT_BLOCKED",
                    company_id=block.company_id, user_id=user.id,
                    idempotency_key=f"recall-product-block:{block.id}",
                    title="리콜/판매중지 확인으로 상품이 차단됐습니다",
                    message=(
                        f"상품 {block.product_identifier}: {block.reason} "
                        "— 신규 등록·가격 확대·가상재고 증가·자동발주가 "
                        "모두 막혔습니다. 기존 주문은 자동으로 취소되지 "
                        "않으며, 직접 확인 후 처리해야 합니다."
                    ),
                    link_path="recall-notices",
                    entity_ref=f"recall_product_block:{block.id}",
                    reason="RECALL_OR_STOP_SALE_CONFIRMED",
                    entity_summary=f"상품 {block.product_identifier}",
                )
        except Exception:  # noqa: BLE001
            pass

    def has_active_block(
        self, company_id: int, product_identifier: str,
    ) -> bool:

        return self.repository.get_active_block(
            company_id, product_identifier,
        ) is not None

    def assert_not_blocked(
        self, company_id: int, product_identifier: str,
    ) -> None:

        block = self.repository.get_active_block(company_id, product_identifier)
        if block is not None:
            raise ConflictException(
                f"상품({product_identifier})은 리콜/판매중지가 확인돼 "
                f"차단된 상태입니다({block.reason}) — 신규 등록·가격 "
                "확대·가상재고 증가·자동발주를 진행하지 않습니다. 해제는 "
                "관리자의 사유 입력과 승인이 있어야만 가능합니다.",
            )

    def get_block(self, block_id: int, company_id: int) -> RecallProductBlock:

        block = self.repository.get_block(block_id, company_id)
        if block is None:
            raise NotFoundException("리콜/판매중지 차단 기록을 찾을 수 없습니다.")
        return block

    def list_blocks(
        self, company_id: int, *, status: str | None = None,
    ) -> list[RecallProductBlock]:

        return self.repository.list_blocks(company_id, status=status)

    def unblock_product(
        self, block_id: int, company_id: int, *, is_admin: bool,
        approved_by: int, justification: str,
    ) -> RecallProductBlock:
        """해제는 항상 사유(justification) + 관리자 승인이 있어야
        한다 — 리콜 상태가 실제로 해소됐는지 시스템이 스스로 판단하지
        않는다(사람의 판단이 최종 근거)."""

        if not is_admin:
            raise ForbiddenException("차단 해제는 관리자만 가능합니다.")

        block = self.get_block(block_id, company_id)
        if block.status != RecallProductBlockStatus.BLOCKED:
            raise BadRequestException("이미 해제된(또는 BLOCKED가 아닌) 차단입니다.")
        if not justification or not justification.strip():
            raise BadRequestException("해제 사유(근거)를 입력해야 합니다.")

        block.status = RecallProductBlockStatus.UNBLOCKED
        block.unblock_requested_by = approved_by
        block.unblock_justification = justification.strip()
        block.unblock_approved_by = approved_by
        block.unblock_approved_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(block)

        return block


__all__ = ["RecallNoticeService"]
