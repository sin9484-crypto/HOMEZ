"""
=========================================================
Homez OS

File : audit.py
Version : 5.0.0

Audit Logging
=========================================================
"""

from datetime import datetime
from typing import Any


class AuditLog:

    def __init__(
        self,
        action: str,
        user_id: int | None = None,
        resource: str | None = None,
        resource_id: Any = None,
        detail: dict[str, Any] | None = None,
    ):

        self.action = action
        self.user_id = user_id
        self.resource = resource
        self.resource_id = resource_id
        self.detail = detail or {}
        self.created_at = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:

        return {
            "action": self.action,
            "user_id": self.user_id,
            "resource": self.resource,
            "resource_id": self.resource_id,
            "detail": self.detail,
            "created_at": self.created_at.isoformat(),
        }
def write_audit_logs(
    db: Session,
    company_id: int | None,
    user_id: int | None,
    actions: list[dict],
) -> None:

    logs = []

    for action in actions:

        logs.append(
            AuditLog(
                company_id=company_id,
                user_id=user_id,
                action=action["action"],
                entity=action["entity"],
                entity_id=action["entity_id"],
                description=action.get("description"),
                ip_address=action.get("ip_address"),
            )
        )

    db.add_all(logs)

    db.commit()
def write_audit_log(
    db: Session,
    company_id: int | None,
    user_id: int | None,
    action: str,
    entity: str,
    entity_id: int | None = None,
    description: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:

    log = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        description=description,
        ip_address=ip_address,
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    return log


def audit_to_dict(
    audit: AuditLog,
) -> dict:

    return {
        "id": audit.id,
        "company_id": audit.company_id,
        "user_id": audit.user_id,
        "action": audit.action,
        "entity": audit.entity,
        "entity_id": audit.entity_id,
        "description": audit.description,
        "ip_address": audit.ip_address,
        "created_at": audit.created_at,
    }
__all__ = [
    "AuditLog",
    "write_audit_log",
    "write_audit_logs",
    "audit_to_dict",
]
    