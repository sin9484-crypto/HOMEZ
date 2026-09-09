"""
=========================================================
Homez OS

File : app/core/audit_db.py

실제 `audit_logs` 테이블(id/company_id/user_id/action/entity/
entity_id/description/ip_address)에 대한 최소 쓰기 헬퍼.
`app/domains/audit/**`는 아직 빈 스캐폴딩이라(2026-07-30 확인) 별도
ORM Model이 없다 — 이 헬퍼가 실제 컬럼에 맞춰 직접 INSERT한다. 호출자의
기존 Session(Transaction) 안에서 flush만 하고 commit은 호출자가 한다
(계정 변경 등 상위 작업과 같은 Transaction으로 묶기 위함).

2026-08-15 Gate 1(V6.5 최종 계약 감사) — 이전에는 `company_id`를 항상
NULL로 고정 INSERT했다. 이 헬퍼를 거치는 모든 호출부(회원가입 승인/
거절, 계정 복구, 비밀번호 변경, StoreConnection 변경 등)의 audit_logs
행이 어느 회사 소속인지 전혀 기록되지 않았고, `GET /admin/audit-logs`
(SuperAdminGuard만 확인, 회사 필터 없음)가 그 행 전체를 반환해 회사
A의 SUPER_ADMIN이 회사 B의 사용자 계정 이벤트(비밀번호 재설정,
초대 생성/폐기 등)를 열람할 수 있는 테넌트 격리 우회였다(Critical —
`app/domains/coupang`/`decision`/`settlement`/`product_candidate`에서
이미 수정된 것과 동일한 클래스의 결함). 이제 호출자가 식별 가능한
회사가 있으면 명시적으로 `company_id`를 전달한다 — 익명/미식별 계정
이벤트(예: 존재하지 않는 계정에 대한 아이디 찾기 시도)처럼 애초에
특정 회사를 알 수 없는 경우에만 기본값 None(NULL)을 유지한다.
=========================================================

2026-08-21 작업 6(감사로그 시각) — `created_at` 컬럼은
migrations/20260821_00_add_audit_logs_created_at.sql이 additive로
추가하지만, 이번 라운드는 그 Migration을 운영 DB(그리고 개발 DB)에
적용하지 않는다 — 즉 이 함수는 컬럼이 있는 DB와 없는 DB 양쪽에서
모두 안전하게 동작해야 한다. `PRAGMA table_info`(읽기 전용, 실패하지
않음)로 매 호출마다 컬럼 존재 여부를 확인한다 — 프로세스 전역 캐시는
일부러 두지 않는다(같은 프로세스 안에서 서로 다른 스키마의 임시 DB를
번갈아 쓰는 전체 테스트 스위트에서 캐시가 실제로 새어 오탐/오차단을
만들 수 있다 — audit_logs 쓰기는 핫패스가 아니므로 매번 확인하는
비용은 무시할 만하다). INSERT를 먼저 시도했다가 실패 시 rollback하는
방식도 쓰지 않는다(write_audit_log는 항상 호출자의 기존 Transaction
중간에 호출되므로, 여기서 rollback하면 그 Transaction에 이미 반영된
다른 변경까지 함께 날아간다 — 절대 금지).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def _has_created_at_column(db: Session) -> bool:

    rows = db.execute(text("PRAGMA table_info(audit_logs)")).fetchall()

    return any(row[1] == "created_at" for row in rows)


def write_audit_log(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    entity: str,
    entity_id: str,
    description: str,
    company_id: int | None = None,
) -> None:

    if _has_created_at_column(db):
        db.execute(
            text(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, description, "
                "ip_address, created_at) "
                "VALUES (:company_id, :user_id, :action, :entity, :entity_id, "
                ":description, NULL, :created_at)",
            ),
            {
                "company_id": company_id,
                "user_id": user_id,
                "action": action,
                "entity": entity,
                "entity_id": entity_id,
                "description": description,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return

    db.execute(
        text(
            "INSERT INTO audit_logs "
            "(company_id, user_id, action, entity, entity_id, description, ip_address) "
            "VALUES (:company_id, :user_id, :action, :entity, :entity_id, :description, NULL)",
        ),
        {
            "company_id": company_id,
            "user_id": user_id,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "description": description,
        },
    )


__all__ = ["write_audit_log"]
