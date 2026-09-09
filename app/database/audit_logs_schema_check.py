"""
=========================================================
Homez OS

File : app/database/audit_logs_schema_check.py

Gate U-2(2026-08-10) — `MigrationRunner.diagnose()`는 대상 테이블이
이미 존재하는지(이름 기준)만 확인하고, 이미 존재하면 무조건
backfill 대상으로 분류한다(SQL을 재실행하지 않고 이력만 기록) — 그
테이블의 실제 컬럼 구성이 이 Migration 파일이 만들려는 모양과 정말
같은지는 비교하지 않는다. audit_logs는 이 저장소에서 Migration 파일
없이 이미 실 DB에 존재해 온 유일한 사례이므로(app/core/audit_db.py
상단 주석 참고), "이미 존재하면 무조건 안전"이라는 가정이 이 경우에는
성립하지 않을 수 있다 — 이 모듈은 그 간극을 메우는 사전 확인 1단계를
추가한다: backfill 판단을 내리기 전에, 실제 DB의 현재 DDL과 Migration
파일이 선언하는 DDL을 정규화 비교해 정말 같을 때만 backfill을
허용하고, 다르면 즉시 예외로 중단한다(자동으로 고치지 않는다).
=========================================================
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


class AuditLogsSchemaMismatchError(Exception):
    """실제 DB의 audit_logs DDL이 Migration 파일의 선언과 다르다."""


def _normalize_ddl(sql: str) -> str:
    """탭/개행/연속 공백 차이를 무시하고 비교할 수 있도록 정규화한다."""

    collapsed = re.sub(r"\s+", " ", sql.strip())
    collapsed = collapsed.replace("( ", "(").replace(" )", ")")
    collapsed = collapsed.replace(" ,", ",")

    return collapsed


def _extract_create_table_sql(migration_sql: str, table_name: str) -> str:

    lines = [
        line for line in migration_sql.splitlines()
        if not line.strip().startswith("--")
    ]
    content = "\n".join(lines)

    match = re.search(
        rf"CREATE TABLE {re.escape(table_name)} \(.*?\);",
        content, re.S,
    )
    if match is None:
        raise AuditLogsSchemaMismatchError(
            f"Migration 파일에서 CREATE TABLE {table_name} 문을 찾을 수 "
            "없습니다.",
        )

    return match.group(0)[:-1]  # 끝 세미콜론 제거(sqlite_master DDL과 맞춤)


def get_actual_ddl(
    conn: sqlite3.Connection, table_name: str,
) -> str | None:
    """현재 DB에 실제로 존재하는 테이블의 DDL을 읽기만 한다(쓰기 없음)."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()

    return row[0] if row else None


def verify_matches_migration_or_raise(
    conn: sqlite3.Connection, migration_path: Path, table_name: str,
) -> str:
    """
    대상 테이블이 DB에 이미 존재할 때만 호출한다. 실제 DDL과 Migration
    파일의 CREATE TABLE 선언을 정규화 비교해, 같으면 "MATCH"를
    반환하고(backfill을 진행해도 안전하다는 뜻), 다르면
    AuditLogsSchemaMismatchError를 던진다(자동으로 고치지 않는다 —
    사람이 직접 진단해야 한다). DB에는 어떤 쓰기도 하지 않는다.
    """

    actual_ddl = get_actual_ddl(conn, table_name)
    if actual_ddl is None:
        raise AuditLogsSchemaMismatchError(
            f"{table_name} 테이블이 존재하지 않습니다 — backfill 대상이 "
            "아니라 신규 생성 대상입니다.",
        )

    expected_ddl = _extract_create_table_sql(
        migration_path.read_text(encoding="utf-8"), table_name,
    )

    if _normalize_ddl(actual_ddl) != _normalize_ddl(expected_ddl):
        raise AuditLogsSchemaMismatchError(
            f"{table_name}의 실제 DB 스키마가 Migration 파일의 선언과 "
            f"다릅니다 — 자동으로 고치지 않습니다.\n실제: {actual_ddl}\n"
            f"Migration 파일: {expected_ddl}",
        )

    return "MATCH"


__all__ = [
    "AuditLogsSchemaMismatchError",
    "get_actual_ddl",
    "verify_matches_migration_or_raise",
]
