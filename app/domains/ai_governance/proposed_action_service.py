"""
=========================================================
Homez OS

File : app/domains/ai_governance/proposed_action_service.py

AG-4(2026-08-21 CTO 후속 지시) — ProposedAction 생성·승인·거절·실행
기록. AI는 `create()`로 DRAFT/REVIEW_REQUIRED 상태까지만 만들 수
있다 — `approve()`/`reject()`는 항상 사람의 명시적 호출(현재 사용자
ID는 호출부 Router의 current_user에서만 온다, 요청 바디로 자칭할
수 없다)만 수행한다. 승인 시점에 fingerprint를 다시 계산해 대상
데이터가 그 사이 바뀌었으면 승인을 거부한다.

이 서비스는 실제 실행(Domain Service 호출)을 수행하지 않는다 —
`mark_executed()`는 호출부가 실제 실행을 이미 완료한 뒤 그 사실만
사후 기록하는 감사용 메서드다. AI가 이 서비스를 통해 스스로 실행을
승인하거나 실행 자체를 수행할 방법은 없다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import consume_recent_auth_token
from app.domains.ai_governance.constants import HIGH_RISK_ACTION_TYPES
from app.domains.ai_governance.constants import ProposedActionStatus
from app.domains.ai_governance.fingerprint import (
    compute_proposed_action_fingerprint,
)
from app.domains.ai_governance.model import ProposedAction
from app.domains.ai_governance.repository import ProposedActionRepository


def _audit(
    db: Session, *, company_id: int, user_id: int | None,
    action: str, entity_id: str, description: str,
) -> None:

    write_audit_log(
        db, user_id=user_id, action=action, entity="ai_proposed_action",
        entity_id=entity_id, description=description,
        company_id=company_id,
    )


class ProposedActionService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = ProposedActionRepository(db)

    def create(
        self,
        *,
        company_id: int,
        capability_code: str,
        action_type: str,
        target_entity: str,
        proposed_payload: dict,
        reason: str,
        evidence: list[str],
        risk_level: str,
        approval_required: bool,
        idempotency_key: str,
        status: str = ProposedActionStatus.REVIEW_REQUIRED,
        expires_at: datetime | None = None,
        created_by: int | None = None,
    ) -> tuple[ProposedAction, bool]:
        """
        AI(또는 그 AI를 호출한 서버 로직)가 실행이 필요한 제안을
        만든다. `status`는 `ProposedActionStatus.AI_CREATABLE`
        (DRAFT/REVIEW_REQUIRED)만 허용한다 — 그 외 값을 넘기면
        차단한다(AI가 스스로 APPROVED/EXECUTED를 자칭하는 경로 자체를
        구조적으로 막는다).
        """

        if status not in ProposedActionStatus.AI_CREATABLE:
            raise BadRequestException(
                "PROPOSED_ACTION_INVALID_INITIAL_STATUS: 생성 시점에는 "
                f"{ProposedActionStatus.AI_CREATABLE}만 허용됩니다 "
                f"(요청값: {status}) — AI는 승인·실행 상태를 스스로 "
                "만들 수 없습니다.",
            )

        existing = self.repository.get_by_company_idempotency_key(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing, True

        fingerprint = compute_proposed_action_fingerprint(
            capability_code=capability_code,
            action_type=action_type,
            target_entity=target_entity,
            proposed_payload=proposed_payload,
        )

        action = self.repository.add_no_commit(
            ProposedAction(
                company_id=company_id,
                capability_code=capability_code,
                action_type=action_type,
                target_entity=target_entity,
                proposed_payload_json=json.dumps(
                    proposed_payload, ensure_ascii=False, default=str,
                ),
                reason=reason,
                evidence_json=json.dumps(evidence, ensure_ascii=False),
                risk_level=risk_level,
                approval_required=approval_required,
                input_fingerprint=fingerprint,
                status=status,
                created_by=created_by,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
            ),
        )

        _audit(
            self.db, company_id=company_id, user_id=created_by,
            action="AI_PROPOSED_ACTION_CREATED", entity_id=str(action.id),
            description=(
                f"AI 제안 생성: capability={capability_code}, "
                f"action_type={action_type}, target={target_entity}, "
                f"status={status}"
            ),
        )

        self.db.commit()

        return action, False

    def approve(
        self,
        action_id: int,
        company_id: int,
        *,
        approved_by: int,
        current_payload_for_refingerprint: dict | None = None,
        decision_reason: str | None = None,
        recent_auth_token: str | None = None,
    ) -> ProposedAction:
        """
        사람의 명시적 승인만 이 상태 전이를 수행한다. `approved_by`는
        호출부 Router의 current_user.id에서만 와야 한다 — 이 서비스는
        요청 바디로 자칭된 값을 받지 않는다(파라미터 자체가 키워드
        전용이며, 라우터가 클라이언트 payload를 그대로 흘려보내지
        않아야 한다).

        `current_payload_for_refingerprint`를 넘기면 생성 시점의
        fingerprint와 다시 계산한 값을 비교해, 그 사이 대상 데이터가
        바뀌었으면 승인을 거부한다(fail-closed — 이 코드베이스의 다른
        fingerprint 게이트와 동일한 철학).

        `action_type`이 `HIGH_RISK_ACTION_TYPES`에 속하면
        `recent_auth_token`(현재 비밀번호 재확인 토큰,
        app/core/recent_auth.py — listing_wizard_service.py::approve()
        와 동일한 메커니즘 재사용)이 없거나 유효하지 않으면 승인
        자체를 거부한다.
        """

        action = self.repository.get_for_company(action_id, company_id)
        if action is None:
            raise NotFoundException("ProposedAction을 찾을 수 없습니다.")

        if action.status not in ProposedActionStatus.AI_CREATABLE:
            raise BadRequestException(
                "PROPOSED_ACTION_NOT_PENDING: DRAFT/REVIEW_REQUIRED "
                f"상태만 승인할 수 있습니다(현재: {action.status}).",
            )

        if action.action_type in HIGH_RISK_ACTION_TYPES:
            if not consume_recent_auth_token(recent_auth_token, approved_by):
                raise UnauthorizedException(
                    "PROPOSED_ACTION_RECENT_AUTH_REQUIRED: 이 제안은 "
                    "고위험 작업이라 승인 전 현재 비밀번호로 다시 "
                    "확인해야 합니다.",
                )

        if action.expires_at is not None and action.expires_at < datetime.utcnow():
            self.repository.transition_conditional(
                action_id, company_id,
                from_statuses=ProposedActionStatus.AI_CREATABLE,
                to_status=ProposedActionStatus.EXPIRED,
            )
            _audit(
                self.db, company_id=company_id, user_id=approved_by,
                action="AI_PROPOSED_ACTION_EXPIRED", entity_id=str(action_id),
                description="승인 시도 시점에 만료 확인되어 자동 EXPIRED 전환",
            )
            self.db.commit()
            raise ConflictException(
                "PROPOSED_ACTION_EXPIRED: 이 제안은 만료되었습니다 — "
                "새로 생성해야 합니다.",
            )

        if current_payload_for_refingerprint is not None:
            current_fingerprint = compute_proposed_action_fingerprint(
                capability_code=action.capability_code,
                action_type=action.action_type,
                target_entity=action.target_entity,
                proposed_payload=current_payload_for_refingerprint,
            )
            if current_fingerprint != action.input_fingerprint:
                self.repository.transition_conditional(
                    action_id, company_id,
                    from_statuses=ProposedActionStatus.AI_CREATABLE,
                    to_status=ProposedActionStatus.INVALIDATED,
                    decision_reason="대상 데이터 변경으로 자동 무효화",
                )
                _audit(
                    self.db, company_id=company_id, user_id=approved_by,
                    action="AI_PROPOSED_ACTION_INVALIDATED",
                    entity_id=str(action_id),
                    description="승인 시도 시점 fingerprint 불일치로 자동 무효화",
                )
                self.db.commit()
                raise ConflictException(
                    "PROPOSED_ACTION_STALE: 제안 생성 이후 대상 데이터가 "
                    "바뀌었습니다 — 새로 생성해야 합니다.",
                )

        rowcount = self.repository.transition_conditional(
            action_id, company_id,
            from_statuses=ProposedActionStatus.AI_CREATABLE,
            to_status=ProposedActionStatus.APPROVED,
            decided_by=approved_by,
            decision_reason=decision_reason,
        )
        if rowcount != 1:
            raise ConflictException(
                "PROPOSED_ACTION_CONCURRENT_DECISION: 다른 요청이 먼저 "
                "이 제안을 처리했습니다.",
            )

        _audit(
            self.db, company_id=company_id, user_id=approved_by,
            action="AI_PROPOSED_ACTION_APPROVED", entity_id=str(action_id),
            description=f"AI 제안 승인: {decision_reason or ''}".strip(),
        )

        self.db.commit()

        return self.repository.get_for_company(action_id, company_id)

    def reject(
        self, action_id: int, company_id: int,
        *, rejected_by: int, reason: str,
    ) -> ProposedAction:

        action = self.repository.get_for_company(action_id, company_id)
        if action is None:
            raise NotFoundException("ProposedAction을 찾을 수 없습니다.")

        rowcount = self.repository.transition_conditional(
            action_id, company_id,
            from_statuses=ProposedActionStatus.AI_CREATABLE,
            to_status=ProposedActionStatus.REJECTED,
            decided_by=rejected_by,
            decision_reason=reason,
        )
        if rowcount != 1:
            raise ConflictException(
                "PROPOSED_ACTION_NOT_PENDING: DRAFT/REVIEW_REQUIRED "
                "상태만 거절할 수 있습니다.",
            )

        _audit(
            self.db, company_id=company_id, user_id=rejected_by,
            action="AI_PROPOSED_ACTION_REJECTED", entity_id=str(action_id),
            description=f"AI 제안 거절: {reason}",
        )

        self.db.commit()

        return self.repository.get_for_company(action_id, company_id)

    def mark_executed(
        self, action_id: int, company_id: int, *, executed_reference: str,
    ) -> ProposedAction:
        """
        호출부가 승인된 제안을 실제 기존 Domain Service로 실행한
        *이후에만* 호출하는 사후 기록용 메서드다 — 이 메서드 자체는
        아무것도 실행하지 않는다. EStop이 활성화된 동안에는 호출부가
        먼저 실제 실행 단계에서 차단되므로 여기까지 도달하지 않는다
        (이 서비스가 EStop을 직접 재검사하지 않는 이유 — 실행 주체인
        기존 Domain Service의 기존 게이트가 이미 그 책임을 진다).
        """

        action = self.repository.get_for_company(action_id, company_id)
        if action is None:
            raise NotFoundException("ProposedAction을 찾을 수 없습니다.")

        rowcount = self.repository.transition_conditional(
            action_id, company_id,
            from_statuses=(ProposedActionStatus.APPROVED,),
            to_status=ProposedActionStatus.EXECUTED,
            executed_reference=executed_reference,
        )
        if rowcount != 1:
            raise ConflictException(
                "PROPOSED_ACTION_NOT_APPROVED: APPROVED 상태만 실행 "
                "기록을 남길 수 있습니다.",
            )

        _audit(
            self.db, company_id=company_id, user_id=action.decided_by,
            action="AI_PROPOSED_ACTION_EXECUTED", entity_id=str(action_id),
            description=f"AI 제안 실행 기록: {executed_reference}",
        )

        self.db.commit()

        return self.repository.get_for_company(action_id, company_id)

    def list_for_company(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ProposedAction]:

        return self.repository.list_for_company(company_id, status=status)


__all__ = ["ProposedActionService"]
