"""
=========================================================
Homez OS

File : app/domains/notification_center/unified_service.py

Gate PT-2F(2026-08-22 16차 지시) — topbar 알림 벨과 purchase_task
알림 패널이 공유하는 단일 조회 계약. 새 알림 테이블을 만들지 않는다
— 기존 `notifications` 테이블을 그대로 읽고, purchase_task로 연결된
알림만 읽기 시점에 그 작업의 현재 상태를 재확인해 "조치 필요/완료"를
덧붙인다(automation_safety.EligibilityService와 동일한 "읽기 시점
판단" 철학 — 저장하지 않는다).

범위: notification_center에 실제로 쓰는 도메인(현재는 purchase_task)
까지만 통합한다. retail_purchase 자신의 패널은 클라이언트에서 읽기
시점에 파생하는 별도 구현으로 남아있다 — 그 도메인은 Provider 계약
전제의 미래 확장용으로 보존 중이고, 이 통합이 그 로직을 서버로
그대로 복제할 근거(요구사항)가 없다(중복 구현 금지 원칙).
=========================================================
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.automation_safety.service import SafetyService
from app.domains.notification_center.model import NOTIFICATION_LEVEL_ERROR
from app.domains.notification_center.model import Notification
from app.domains.notification_center.service import NotificationService
from app.domains.purchase_task.constants import PurchaseTaskStatus

_PURCHASE_TASK_LINK_RE = re.compile(r"^purchase-task-detail\?id=(\d+)$")


class UnifiedNotificationService:

    def __init__(self, db: Session):

        self.db = db
        self.notification_service = NotificationService(db)

    def list_unified(
        self, company_id: int, user_id: int, *, unread_only: bool = False,
        limit: int = 50,
    ) -> dict:

        estop_active = SafetyService(self.db).is_emergency_stop_active()

        rows = self.notification_service.list_for_user(
            company_id, user_id, unread_only=unread_only, limit=limit,
        )

        # 동일 이벤트 중복 표시 방지 — 같은 link_path를 가리키는 알림이
        # 여러 건이면 가장 최신 것만 남긴다(link_path가 없는 알림은
        # 중복 판정 대상이 아니므로 전부 유지).
        seen_link_paths: set[str] = set()
        deduped: list[tuple[Notification, bool]] = []
        for notification, is_read in rows:
            if notification.link_path:
                if notification.link_path in seen_link_paths:
                    continue
                seen_link_paths.add(notification.link_path)
            deduped.append((notification, is_read))

        items = [
            self._to_item(notification, is_read, company_id)
            for notification, is_read in deduped
        ]

        if estop_active:
            # Gate PT-3(2026-08-23 17차 지시) 버그수정: 이 항목은 저장된
            # Notification 행이 아니라 매 조회 시 계산되는 실시간 상태
            # 표시다("읽기 시점 판단" 철학, 위 모듈 docstring 참고) —
            # created_at을 None으로 두면 응답 스키마(datetime, not
            # null)와 충돌해 EStop이 활성화된 동안 이 API 전체가 500을
            # 반환한다(실사용 중 발견). "생성 시각"이 아니라 "지금 이
            # 상태를 확인한 시각"으로 채운다.
            items.insert(0, {
                "id": 0, "category": "automation_safety",
                "level": NOTIFICATION_LEVEL_ERROR,
                "title": "Emergency Stop 활성화됨",
                "message": "모든 자동화 실행이 차단되어 있습니다.",
                "link_path": "system",
                "is_read": False, "created_at": datetime.utcnow(),
                "purchase_task_id": None, "purchase_task_status": None,
                "action_status": None,
            })

        return {"emergency_stop_active": estop_active, "items": items}

    def _to_item(
        self, notification: Notification, is_read: bool, company_id: int,
    ) -> dict:

        purchase_task_id = None
        purchase_task_status = None
        action_status = None

        match = (
            _PURCHASE_TASK_LINK_RE.match(notification.link_path)
            if notification.link_path else None
        )
        if match:
            purchase_task_id = int(match.group(1))
            from app.domains.purchase_task.repository import (
                PurchaseTaskRepository,
            )
            task = PurchaseTaskRepository(self.db).get_task(
                purchase_task_id, company_id,
            )
            if task is not None:
                purchase_task_status = task.status
                action_status = (
                    "ACTION_DONE" if task.status in PurchaseTaskStatus.TERMINAL
                    else "ACTION_REQUIRED"
                )

        return {
            "id": notification.id, "category": notification.category,
            "level": notification.level, "title": notification.title,
            "message": notification.message,
            "link_path": notification.link_path, "is_read": is_read,
            "created_at": notification.created_at,
            "purchase_task_id": purchase_task_id,
            "purchase_task_status": purchase_task_status,
            "action_status": action_status,
        }


__all__ = ["UnifiedNotificationService"]
