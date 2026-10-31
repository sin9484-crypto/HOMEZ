"""
=========================================================
Homez OS

File : app/domains/external_call/gate.py

2026-10-31 — AI 통합 계획 6단계: 바깥 호출 한 번을 승인 센터에 묻는(직전) / 결과를 남기는(직후) 작은 문. `ExternalCallPolicyService`의
`authorize()`·`finish()`를 그대로 쓴다 — 새 규칙은 없다. 호출 코드를 가진 쪽(예: 네이버 검색어 트렌드 Adapter)이 승인 센터를 몰라도
되도록 이 문만 넘겨받는다.

  - 허용이 없거나 비상 정지면 `open()`이 `ExternalCallNotAllowed`를 낸다 — 호출하지 않는다(승인 센터에 허용 요청이 하나 올라간다).
  - 허용 테이블이 없으면(Migration 적용 전) 예전처럼 통과시킨다(`ExternalCallPolicyService` 문서와 같은 규칙).
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.external_call.service import CallTicket
from app.domains.external_call.service import ExternalCallPolicyService


class ExternalCallGate:

    def __init__(
        self, db: Session, company_id: int, system: str, kind: str, purpose: str, *,
        actor_user_id: int | None = None, method: str | None = None,
    ):
        self.db = db
        self.company_id = company_id
        self.system, self.kind, self.purpose = system, kind, purpose
        self.actor_user_id = actor_user_id
        self.method = method

    def open(self, target: str | None = None) -> CallTicket | None:
        if not ExternalCallPolicyService.installed(self.db):
            return None
        return ExternalCallPolicyService(self.db).authorize(
            self.company_id, self.system, self.kind, self.purpose,
            actor_user_id=self.actor_user_id, target=target, method=self.method,
        )

    def close(self, ticket: CallTicket | None, *, succeeded: bool, http_status: int | None = None) -> None:
        if ticket is None:
            return
        ExternalCallPolicyService(self.db).finish(ticket, succeeded=succeeded, http_status=http_status)


__all__ = ["ExternalCallGate"]
