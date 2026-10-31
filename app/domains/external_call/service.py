"""
=========================================================
Homez OS

File : app/domains/external_call/service.py

2026-10-16 — 바깥 호출 허용 서비스. 기능은 바깥(쿠팡·온채널)을 부르기 **직전에** `authorize()`를 호출한다.

  - 허용이 있으면(횟수형은 1회 차감, 상시형은 시간당 상한 확인) 기록(`external_call_logs`)을 남기고 통과시킨다.
  - 허용이 없으면 `ExternalCallNotAllowed`를 낸다 — 호출하지 않는다. 같은 목적의 "허용 요청"(PENDING)이 없으면 하나 만들어
    승인 센터에 올린다(중복 요청은 합친다).
  - 허용 대상이 아닌 목적(예: 판매중지·주문 취소 같은 안전 장치)은 막지 않고 **기록만** 한다.
  - 비상 정지가 켜져 있으면 허용 대상 호출은 모두 막는다.

테이블이 없으면(Migration 적용 전) `installed()`가 False이고, 호출하는 쪽은 예전 동작을 유지한다(허용 확인 없이 진행).
=========================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.external_call.constants import MAX_COUNTED_GRANT
from app.domains.external_call.constants import MAX_PER_HOUR
from app.domains.external_call.constants import MAX_STANDING_HOURS
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import DEFAULT_ORDER_DETECTION_PER_HOUR
from app.domains.external_call.constants import DEFAULT_ORDER_TRACKING_PER_HOUR
from app.domains.external_call.constants import DEFAULT_AFTER_SALES_PER_HOUR
from app.domains.external_call.constants import DEFAULT_PRODUCT_STATUS_PER_HOUR
from app.domains.external_call.constants import DEFAULT_STANDING_HOURS
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.constants import LogOutcome
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog

logger = logging.getLogger("homez")

# 허용 요청이 처음 만들어질 때 제안하는 값(사용자가 승인 때 바꿀 수 있다).
# 일괄 연장 대상: 만료까지 이 시간 이하로 남았거나 이미 만료됐고(최근 이 일수 안에), 사용자가 취소·거절하지 않은 상시 조회 허용.
DEFAULT_RENEW_WITHIN_HOURS = 12
RENEW_LOOKBACK_DAYS = 7

_DEFAULT_REQUEST = {
    (CallPurpose.ORDER_DETECTION, CallKind.READ): (AllowanceMode.STANDING, None, DEFAULT_ORDER_DETECTION_PER_HOUR),
    (CallPurpose.ORDER_TRACKING, CallKind.READ): (AllowanceMode.STANDING, None, DEFAULT_ORDER_TRACKING_PER_HOUR),
    (CallPurpose.AFTER_SALES_CHECK, CallKind.READ): (AllowanceMode.STANDING, None, DEFAULT_AFTER_SALES_PER_HOUR),
    (CallPurpose.PRODUCT_STATUS_CHECK, CallKind.READ): (AllowanceMode.STANDING, None, DEFAULT_PRODUCT_STATUS_PER_HOUR),
    (CallPurpose.AI_DRAFT, CallKind.WRITE): (AllowanceMode.COUNTED, 5, None),  # AI 호출은 사용료가 들어 횟수로만 허용한다(상시 불가)
    (CallPurpose.AI_ANALYSIS, CallKind.WRITE): (AllowanceMode.COUNTED, 5, None),
    (CallPurpose.AI_ADVICE, CallKind.WRITE): (AllowanceMode.COUNTED, 5, None),
    (CallPurpose.INVOICE_UPLOAD, CallKind.READ): (AllowanceMode.COUNTED, 2, None),
    (CallPurpose.INVOICE_UPLOAD, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    (CallPurpose.PRODUCT_OUTBOUND_DAYS, CallKind.READ): (AllowanceMode.COUNTED, 3, None),
    (CallPurpose.PRODUCT_OUTBOUND_DAYS, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    # 2026-10-24 재고 수량 변경: 한 번 바꾸는 데 읽기 3회(미리보기·전송 직전 재확인·전송 뒤 확인), 쓰기는 1회씩만.
    (CallPurpose.STOCK_UPDATE, CallKind.READ): (AllowanceMode.COUNTED, 3, None),
    (CallPurpose.STOCK_UPDATE, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    # 2026-10-25 판매가 변경: 재고 변경과 같이 읽기 3회(미리보기·전송 직전 재확인·전송 뒤 확인), 쓰기 1회씩만.
    (CallPurpose.PRICE_UPDATE, CallKind.READ): (AllowanceMode.COUNTED, 3, None),
    (CallPurpose.PRICE_UPDATE, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    # 2026-10-25 상품명 수정: 읽기 3회(미리보기·전송 직전 재확인·전송 뒤 확인), 쓰기 1회씩만.
    (CallPurpose.PRODUCT_NAME_UPDATE, CallKind.READ): (AllowanceMode.COUNTED, 3, None),
    (CallPurpose.PRODUCT_NAME_UPDATE, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    # 2026-10-25 문의 답변: 읽기 3회(미리보기·전송 직전 재확인·전송 뒤 확인), 쓰기 1회씩만.
    (CallPurpose.INQUIRY_REPLY, CallKind.READ): (AllowanceMode.COUNTED, 3, None),
    (CallPurpose.INQUIRY_REPLY, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    # 2026-10-26 반품 입고 확인·승인: 한 번 처리에 읽기 3회(미리보기·전송 직전 재확인·전송 뒤 확인), 쓰기 1회씩만.
    (CallPurpose.RETURN_PROCESS, CallKind.READ): (AllowanceMode.COUNTED, 3, None),
    (CallPurpose.RETURN_PROCESS, CallKind.WRITE): (AllowanceMode.COUNTED, 1, None),
    # 2026-10-25 AI 이미지 생성: 한 번 만들 때 최대 6장 — 장마다 1회(사용료가 들어 횟수로만, 상시 불가).
    (CallPurpose.IMAGE_GENERATION, CallKind.WRITE): (AllowanceMode.COUNTED, 6, None),
    # 2026-10-31 네이버 검색어 트렌드: 검색어 하나당 1회. 매일 자동 실행은 한 번에 최대 10건이라 10회를 제안한다(무료 — 사용료 없음).
    (CallPurpose.TREND_LOOKUP, CallKind.READ): (AllowanceMode.COUNTED, 10, None),
}


class ExternalCallNotAllowed(ForbiddenException):
    """허용이 없어 바깥 호출을 하지 않았다. `code`는 화면·호출자가 알아볼 수 있는 고정 문구다."""

    def __init__(self, system: str, kind: str, purpose: str, *, reason: str = "ALLOWANCE_REQUIRED", request_id: int | None = None):
        self.system, self.kind, self.purpose = system, kind, purpose
        self.reason, self.request_id = reason, request_id
        label = f"{ExternalSystem.LABELS.get(system, system)} {CallKind.LABELS.get(kind, kind)}({CallPurpose.LABELS.get(purpose, purpose)})"
        if reason == "ESTOP_ACTIVE":
            message = f"EXTERNAL_CALL_BLOCKED_ESTOP: 비상 정지가 켜져 있어 {label} 호출을 하지 않았다."
        else:
            message = f"EXTERNAL_ALLOWANCE_REQUIRED: {label} 허용이 없어 호출하지 않았다 — 승인 센터에서 허용해 주세요."
        super().__init__(message)


@dataclass
class CallTicket:
    log_id: int | None = None
    allowance_id: int | None = None


class ExternalCallPolicyService:

    def __init__(self, db: Session, *, now_factory=None):
        self.db = db
        self._now = now_factory or datetime.utcnow

    # ------------------------------------------------------------
    # 설치 여부
    # ------------------------------------------------------------

    @staticmethod
    def installed(db: Session) -> bool:
        inspector = sa_inspect(db.get_bind())
        return (
            inspector.has_table(ExternalCallAllowance.__tablename__)
            and inspector.has_table(ExternalCallLog.__tablename__)
        )

    # ------------------------------------------------------------
    # 호출 직전 확인
    # ------------------------------------------------------------

    def authorize(
        self, company_id: int, system: str, kind: str, purpose: str, *,
        actor_user_id: int | None = None, target: str | None = None, method: str | None = None,
        enforce: bool | None = None,
    ) -> CallTicket:
        enforce = (purpose in CallPurpose.ENFORCED) if enforce is None else enforce
        if not enforce:
            log = self._log(company_id, system, kind, purpose, LogOutcome.LOGGED, None, actor_user_id, target, method)
            return CallTicket(log.id, None)

        if self._emergency_stop_active():
            raise ExternalCallNotAllowed(system, kind, purpose, reason="ESTOP_ACTIVE")

        now = self._now()
        candidates = (
            self.db.query(ExternalCallAllowance)
            .filter(
                ExternalCallAllowance.company_id == company_id,
                ExternalCallAllowance.system == system,
                ExternalCallAllowance.kind == kind,
                ExternalCallAllowance.purpose.in_((purpose, CallPurpose.ANY)),
                ExternalCallAllowance.status == AllowanceStatus.ACTIVE,
            )
            .order_by(ExternalCallAllowance.id.asc())
            .all()
        )
        # 정확한 목적을 먼저, 모든 목적(ANY)은 뒤에.
        candidates.sort(key=lambda a: (a.purpose != purpose, a.id))
        for allowance in candidates:
            if allowance.expires_at is not None and allowance.expires_at <= now:
                allowance.status = AllowanceStatus.EXPIRED
                self.db.commit()
                continue
            if allowance.mode == AllowanceMode.COUNTED:
                if self._consume_counted(allowance):
                    log = self._log(company_id, system, kind, purpose, LogOutcome.ALLOWED, allowance.id, actor_user_id, target, method)
                    return CallTicket(log.id, allowance.id)
                continue
            # 상시형 — 최근 1시간 안에 이 허용으로 나간 횟수가 상한 미만이어야 한다.
            if self._standing_has_room(allowance, now):
                log = self._log(company_id, system, kind, purpose, LogOutcome.ALLOWED, allowance.id, actor_user_id, target, method)
                return CallTicket(log.id, allowance.id)

        request = self.ensure_pending_request(company_id, system, kind, purpose, requested_by=actor_user_id)
        raise ExternalCallNotAllowed(system, kind, purpose, request_id=request.id)

    def finish(self, ticket: CallTicket | None, *, succeeded: bool, http_status: int | None = None) -> None:
        if ticket is None or ticket.log_id is None:
            return
        log = self.db.get(ExternalCallLog, ticket.log_id)
        if log is None:
            return
        if log.outcome in (LogOutcome.ALLOWED, LogOutcome.LOGGED):
            log.outcome = LogOutcome.SUCCEEDED if succeeded else LogOutcome.FAILED
        log.http_status = http_status
        self.db.commit()

    def _emergency_stop_active(self) -> bool:
        try:
            from app.domains.automation_safety.service import SafetyService

            return bool(SafetyService(self.db).is_emergency_stop_active())
        except Exception:  # noqa: BLE001 — 확인할 수 없으면 막는다(안전 쪽)
            return True

    def _consume_counted(self, allowance: ExternalCallAllowance) -> bool:
        """횟수형 허용에서 1회를 원자적으로 차감한다(동시 요청이 같은 마지막 1회를 함께 쓰지 못한다)."""

        result = self.db.execute(
            update(ExternalCallAllowance)
            .where(
                ExternalCallAllowance.id == allowance.id,
                ExternalCallAllowance.status == AllowanceStatus.ACTIVE,
                ExternalCallAllowance.remaining_count > 0,
            )
            .values(remaining_count=ExternalCallAllowance.remaining_count - 1)
        )
        if result.rowcount != 1:
            self.db.rollback()
            return False
        self.db.commit()
        self.db.refresh(allowance)
        if allowance.remaining_count <= 0:
            allowance.status = AllowanceStatus.EXHAUSTED
            self.db.commit()
        return True

    def _standing_has_room(self, allowance: ExternalCallAllowance, now: datetime) -> bool:
        limit = allowance.per_hour_limit or 0
        if limit <= 0:
            return False
        used = (
            self.db.query(func.count(ExternalCallLog.id))
            .filter(
                ExternalCallLog.allowance_id == allowance.id,
                ExternalCallLog.created_at > now - timedelta(hours=1),
            )
            .scalar()
        ) or 0
        return used < limit

    def _log(
        self, company_id, system, kind, purpose, outcome, allowance_id, actor_user_id, target, method,
    ) -> ExternalCallLog:
        log = ExternalCallLog(
            company_id=company_id, system=system, kind=kind, purpose=purpose, outcome=outcome,
            allowance_id=allowance_id, actor_user_id=actor_user_id, target=(target or None) and target[:120],
            method=method, created_at=self._now(),
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    # ------------------------------------------------------------
    # 허용 요청·결정
    # ------------------------------------------------------------

    def ensure_pending_request(
        self, company_id: int, system: str, kind: str, purpose: str, *, requested_by: int | None = None,
        reason: str | None = None,
    ) -> ExternalCallAllowance:
        existing = (
            self.db.query(ExternalCallAllowance)
            .filter(
                ExternalCallAllowance.company_id == company_id,
                ExternalCallAllowance.system == system,
                ExternalCallAllowance.kind == kind,
                ExternalCallAllowance.purpose == purpose,
                ExternalCallAllowance.status == AllowanceStatus.PENDING,
            )
            .first()
        )
        if existing is not None:
            existing.updated_at = self._now()
            self.db.commit()
            return existing
        mode, count, per_hour = _DEFAULT_REQUEST.get((purpose, kind), (AllowanceMode.COUNTED, 1, None))
        request = ExternalCallAllowance(
            company_id=company_id, system=system, kind=kind, purpose=purpose, mode=mode,
            status=AllowanceStatus.PENDING, requested_count=count, per_hour_limit=per_hour,
            reason=(reason or f"{CallPurpose.LABELS.get(purpose, purpose)}에 필요한 {CallKind.LABELS.get(kind, kind)} 허용 요청")[:300],
            requested_by=requested_by, created_at=self._now(), updated_at=self._now(),
        )
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        return request

    def grant(
        self, company_id: int, *, system: str, kind: str, purpose: str, mode: str,
        count: int | None = None, per_hour_limit: int | None = None, hours: int | None = None,
        reason: str | None = None, user_id: int | None = None,
    ) -> ExternalCallAllowance:
        """사용자가 직접 허용을 만든다(요청 없이)."""

        allowance = ExternalCallAllowance(
            company_id=company_id, system=system, kind=kind, purpose=purpose, mode=mode,
            status=AllowanceStatus.PENDING, reason=(reason or "")[:300] or None, requested_by=user_id,
            created_at=self._now(), updated_at=self._now(),
        )
        self._validate_terms(allowance, count, per_hour_limit, hours)
        self._activate(allowance, count, per_hour_limit, hours, user_id)
        self.db.add(allowance)
        self.db.commit()
        self.db.refresh(allowance)
        return allowance

    def approve(
        self, company_id: int, allowance_id: int, *, count: int | None = None,
        per_hour_limit: int | None = None, hours: int | None = None, user_id: int | None = None,
    ) -> ExternalCallAllowance:
        allowance = self._get(company_id, allowance_id)
        if allowance.status != AllowanceStatus.PENDING:
            raise ConflictException("이미 결정된 요청입니다.")
        if count is None:
            count = allowance.requested_count
        if per_hour_limit is None:
            per_hour_limit = allowance.per_hour_limit
        self._validate_terms(allowance, count, per_hour_limit, hours)
        self._activate(allowance, count, per_hour_limit, hours, user_id)
        self.db.commit()
        self.db.refresh(allowance)
        return allowance

    def reject(self, company_id: int, allowance_id: int, *, user_id: int | None = None) -> ExternalCallAllowance:
        allowance = self._get(company_id, allowance_id)
        if allowance.status != AllowanceStatus.PENDING:
            raise ConflictException("이미 결정된 요청입니다.")
        allowance.status = AllowanceStatus.REJECTED
        allowance.decided_by, allowance.decided_at = user_id, self._now()
        self.db.commit()
        return allowance

    def revoke(self, company_id: int, allowance_id: int, *, user_id: int | None = None) -> ExternalCallAllowance:
        allowance = self._get(company_id, allowance_id)
        if allowance.status != AllowanceStatus.ACTIVE:
            raise ConflictException("허용 중인 항목만 취소할 수 있습니다.")
        allowance.status = AllowanceStatus.REVOKED
        allowance.decided_by, allowance.decided_at = user_id, self._now()
        self.db.commit()
        return allowance

    def _get(self, company_id: int, allowance_id: int) -> ExternalCallAllowance:
        allowance = (
            self.db.query(ExternalCallAllowance)
            .filter(ExternalCallAllowance.id == allowance_id, ExternalCallAllowance.company_id == company_id)
            .first()
        )
        if allowance is None:
            raise NotFoundException("허용 항목을 찾을 수 없습니다.")
        return allowance

    @staticmethod
    def _validate_terms(allowance, count, per_hour_limit, hours) -> None:
        if allowance.system not in ExternalSystem.ALL:
            raise BadRequestException("지원하지 않는 대상입니다.")
        if allowance.kind not in CallKind.ALL:
            raise BadRequestException("조회/변경 종류가 올바르지 않습니다.")
        if allowance.purpose not in CallPurpose.ALL:
            raise BadRequestException("지원하지 않는 목적입니다.")
        if allowance.mode == AllowanceMode.COUNTED:
            if count is None or isinstance(count, bool) or not 1 <= int(count) <= MAX_COUNTED_GRANT:
                raise BadRequestException(f"허용 횟수는 1~{MAX_COUNTED_GRANT}회여야 합니다.")
        elif allowance.mode == AllowanceMode.STANDING:
            if allowance.kind != CallKind.READ:
                raise BadRequestException("상시 허용은 조회(읽기)에만 줄 수 있습니다 — 변경은 횟수로만 허용합니다.")
            if per_hour_limit is None or not 1 <= int(per_hour_limit) <= MAX_PER_HOUR:
                raise BadRequestException(f"시간당 상한은 1~{MAX_PER_HOUR}회여야 합니다.")
            if hours is None or not 1 <= int(hours) <= MAX_STANDING_HOURS:
                raise BadRequestException(f"상시 허용 기간은 1~{MAX_STANDING_HOURS}시간이어야 합니다.")
        else:
            raise BadRequestException("허용 방식이 올바르지 않습니다.")

    def _activate(self, allowance, count, per_hour_limit, hours, user_id) -> None:
        now = self._now()
        allowance.status = AllowanceStatus.ACTIVE
        allowance.decided_by, allowance.decided_at = user_id, now
        if allowance.mode == AllowanceMode.COUNTED:
            allowance.requested_count = int(count)
            allowance.remaining_count = int(count)
            allowance.per_hour_limit = None
            allowance.expires_at = now + timedelta(hours=int(hours)) if hours else None
        else:
            allowance.requested_count = None
            allowance.remaining_count = None
            allowance.per_hour_limit = int(per_hour_limit)
            allowance.expires_at = now + timedelta(hours=int(hours))

    # ------------------------------------------------------------
    # 만료 임박·만료 상시 허용 일괄 연장(2026-10-17, 연동 안정화 A2)
    #
    # 상시 허용은 기간이 끝나면 자동 감지가 멈추는데(오늘 13:44 사고), 항목마다 따로 비밀번호를 넣어 다시 허용해야 했다. 사용자가 한 번의
    # 비밀번호 확인으로 "같은 한도·같은 목적"을 다시 허용하게 한다. 새 권한을 만들지 않는다: 이미 상시로 허용했던 조회(읽기)만 대상이고,
    # 사용자가 직접 취소·거절한 것과 변경(쓰기) 허용은 건드리지 않는다.
    # ------------------------------------------------------------

    def renewable(self, company_id: int, *, within_hours: int = DEFAULT_RENEW_WITHIN_HOURS) -> list[dict]:
        now = self._now()
        rows = (
            self.db.query(ExternalCallAllowance)
            .filter(
                ExternalCallAllowance.company_id == company_id,
                ExternalCallAllowance.mode == AllowanceMode.STANDING,
                ExternalCallAllowance.kind == CallKind.READ,
            )
            .order_by(ExternalCallAllowance.id.desc())
            .all()
        )
        seen: set[tuple[str, str, str]] = set()
        items: list[dict] = []
        for row in rows:
            key = (row.system, row.kind, row.purpose)
            if key in seen:
                continue  # 같은 목적은 가장 최근 항목만 본다(이미 새로 허용했으면 그것이 최신이다)
            seen.add(key)
            if row.expires_at is None or row.per_hour_limit is None:
                continue
            if row.status not in (AllowanceStatus.ACTIVE, AllowanceStatus.EXPIRED):
                continue  # 취소·거절·대기 중인 것은 사용자의 결정을 존중한다
            hours_left = (row.expires_at - now).total_seconds() / 3600
            if row.expires_at < now - timedelta(days=RENEW_LOOKBACK_DAYS):
                continue  # 오래전에 끝난 허용은 되살리지 않는다(상태가 아직 ACTIVE로 남아 있어도 같다)
            if row.status == AllowanceStatus.ACTIVE and hours_left > within_hours:
                continue
            state = "EXPIRING" if hours_left > 0 else "EXPIRED"
            items.append({
                "allowance_id": row.id, "system": row.system, "kind": row.kind, "purpose": row.purpose,
                "purpose_label": CallPurpose.LABELS.get(row.purpose, row.purpose),
                "system_label": ExternalSystem.LABELS.get(row.system, row.system),
                "per_hour_limit": int(row.per_hour_limit), "expires_at": row.expires_at.isoformat(),
                "hours_left": round(hours_left, 1), "state": state,
            })
        return items

    def renew_expiring(
        self, company_id: int, *, user_id: int | None, within_hours: int = DEFAULT_RENEW_WITHIN_HOURS,
        hours: int = DEFAULT_STANDING_HOURS,
    ) -> list[ExternalCallAllowance]:
        created: list[ExternalCallAllowance] = []
        for item in self.renewable(company_id, within_hours=within_hours):
            created.append(self.grant(
                company_id, system=item["system"], kind=item["kind"], purpose=item["purpose"], mode=AllowanceMode.STANDING,
                per_hour_limit=item["per_hour_limit"], hours=hours, reason="만료 임박·만료 허용 일괄 연장", user_id=user_id,
            ))
        return created

    # ------------------------------------------------------------
    # 조회(승인 센터)
    # ------------------------------------------------------------

    def summary(self, company_id: int) -> dict:
        now = self._now()
        for row in (
            self.db.query(ExternalCallAllowance)
            .filter(
                ExternalCallAllowance.company_id == company_id,
                ExternalCallAllowance.status == AllowanceStatus.ACTIVE,
                ExternalCallAllowance.expires_at.isnot(None),
                ExternalCallAllowance.expires_at <= now,
            )
            .all()
        ):
            row.status = AllowanceStatus.EXPIRED
        self.db.commit()

        def _row(a: ExternalCallAllowance) -> dict:
            return {
                "id": a.id, "system": a.system, "kind": a.kind, "purpose": a.purpose, "mode": a.mode, "status": a.status,
                "requested_count": a.requested_count, "remaining_count": a.remaining_count,
                "per_hour_limit": a.per_hour_limit, "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                "reason": a.reason, "created_at": a.created_at.isoformat() if a.created_at else None,
                "system_label": ExternalSystem.LABELS.get(a.system, a.system),
                "kind_label": CallKind.LABELS.get(a.kind, a.kind),
                "purpose_label": CallPurpose.LABELS.get(a.purpose, a.purpose),
            }

        pending = (
            self.db.query(ExternalCallAllowance)
            .filter(ExternalCallAllowance.company_id == company_id, ExternalCallAllowance.status == AllowanceStatus.PENDING)
            .order_by(ExternalCallAllowance.id.desc()).all()
        )
        active = (
            self.db.query(ExternalCallAllowance)
            .filter(ExternalCallAllowance.company_id == company_id, ExternalCallAllowance.status == AllowanceStatus.ACTIVE)
            .order_by(ExternalCallAllowance.id.desc()).all()
        )
        day_start = self._kst_day_start_utc(now)
        today_rows = (
            self.db.query(ExternalCallLog.system, ExternalCallLog.kind, ExternalCallLog.outcome, func.count(ExternalCallLog.id))
            .filter(ExternalCallLog.company_id == company_id, ExternalCallLog.created_at >= day_start)
            .group_by(ExternalCallLog.system, ExternalCallLog.kind, ExternalCallLog.outcome)
            .all()
        )
        today: dict[str, dict[str, int]] = {}
        for system, kind, outcome, count in today_rows:
            today.setdefault(system, {}).setdefault(kind, 0)
            today[system][kind] += int(count)
        recent = (
            self.db.query(ExternalCallLog)
            .filter(ExternalCallLog.company_id == company_id)
            .order_by(ExternalCallLog.id.desc()).limit(15).all()
        )
        return {
            "installed": True,
            "pending": [_row(a) for a in pending],
            "active": [_row(a) for a in active],
            "renewable": self.renewable(company_id),
            "today": today,
            "recent": [
                {
                    "id": log.id, "system": log.system, "kind": log.kind, "purpose": log.purpose, "outcome": log.outcome,
                    "http_status": log.http_status, "created_at": log.created_at.isoformat() if log.created_at else None,
                    "system_label": ExternalSystem.LABELS.get(log.system, log.system),
                    "kind_label": CallKind.LABELS.get(log.kind, log.kind),
                    "purpose_label": CallPurpose.LABELS.get(log.purpose, log.purpose),
                }
                for log in recent
            ],
        }

    @staticmethod
    def _kst_day_start_utc(now_utc: datetime) -> datetime:
        kst = now_utc + timedelta(hours=9)
        return datetime(kst.year, kst.month, kst.day) - timedelta(hours=9)


__all__ = [
    "CallTicket", "ExternalCallNotAllowed", "ExternalCallPolicyService",
]
