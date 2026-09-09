"""
=========================================================
Homez OS

File : tests/test_v7_followup_migrations.py

2026-08-30 V7 후속 안정화 Phase 2/5/6 신규 Migration 3건의 fresh-apply
검증:
  - 20260830_00_create_refresh_token_schema.sql
  - 20260830_01_create_marketplace_submission_reconciliation_schema.sql
  - 20260830_02_add_marketplace_submission_provider_warning.sql
    (Phase 6 성공 경고 보존 — 이 파일만 기존 테이블에 컬럼을 추가한다,
    ALTER TABLE ADD COLUMN 2개)

앞의 두 Migration은 완전히 새로운 독립 테이블을 만들 뿐이라 특정
선행 Migration까지만 격리 적용해야 하는 소프트삭제 Migration류보다
검증이 단순하다 — 저장소의 전체 migrations/를 빈 임시 DB에 처음부터
순서대로 적용해 마지막에 이 세 파일이 깨끗하게 들어가는지만 확인한다.
실제 homez.db는 전혀 열지 않는다.

SHA-256 checksum을 이 파일에 고정한다(하드코딩) — MigrationRunner가
내부적으로 재계산하는 값과 비교하는 게 아니라, "지금 저장소에 있는
이 3개 파일의 바이트가 이 값과 정확히 같다"를 이 테스트가 직접
증명한다. 파일 내용이 나중에 실수로(또는 의도치 않게) 바뀌면 이
테스트가 실패해야 한다 — 기존 두 파일(_00/_01)의 checksum은 이번에
바꾸지 않았다(2026-08-30 이전 턴에 이미 존재하던 그대로).
=========================================================
"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database.migration_runner import MigrationRunner

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"

_REFRESH_TOKEN_TARGET = "20260830_00_create_refresh_token_schema.sql"
_RECONCILIATION_TARGET = (
    "20260830_01_create_marketplace_submission_reconciliation_schema.sql"
)
_PROVIDER_WARNING_TARGET = (
    "20260830_02_add_marketplace_submission_provider_warning.sql"
)

# 2026-08-30 후속 지시(Migration 계약 수정) — 세 파일의 SHA-256을
# 여기에 고정한다. _00/_01은 기존 값 그대로이며(이번에 변경하지
# 않음), _02만 새로 추가한다.
_EXPECTED_CHECKSUMS = {
    _REFRESH_TOKEN_TARGET:
        "0a9c97b4626342d86cd3d847cb294e47af0fcdefb742b1ef80b73014165f97b9",
    _RECONCILIATION_TARGET:
        "187deaa0422c9128b87152a8f63c066a2530dc4814f9e7e817a6147939a0e21f",
    _PROVIDER_WARNING_TARGET:
        "3e55228d93cecff222e167a567b1a102eefbec1fa2b6b0808cc60e2e07ff82f3",
}


class V7FollowupMigrationsFreshApplyTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.db_path = Path(path)

        cls.runner = MigrationRunner(cls.db_path, _MIGRATIONS_DIR)
        conn = sqlite3.connect(str(cls.db_path))
        try:
            cls.applied = cls.runner.apply_pending(conn)
            cls.integrity_check = conn.execute(
                "PRAGMA integrity_check",
            ).fetchone()[0]
            cls.fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):

        if cls.db_path.exists():
            os.remove(cls.db_path)

    def _columns(self, table):
        conn = sqlite3.connect(str(self.db_path))
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def test_both_target_files_applied(self):

        self.assertIn(_REFRESH_TOKEN_TARGET, self.applied)
        self.assertIn(_RECONCILIATION_TARGET, self.applied)
        self.assertIn(_PROVIDER_WARNING_TARGET, self.applied)

    def test_checksums_are_pinned_and_unchanged(self):
        # 2026-08-30 후속 지시(Migration 계약 수정) — 세 파일 모두
        # 실제 바이트가 이 파일에 고정해 둔 SHA-256과 정확히 같아야
        # 한다. _00/_01의 값은 이번에 바꾸지 않았다 — 이 어서션이
        # 그대로 통과한다는 것 자체가 "기존 Migration checksum 변경
        # 금지" 요구를 만족한다는 증거다.
        for filename, expected in _EXPECTED_CHECKSUMS.items():
            actual = hashlib.sha256(
                (_MIGRATIONS_DIR / filename).read_bytes(),
            ).hexdigest()
            self.assertEqual(
                actual, expected,
                f"{filename}의 checksum이 고정값과 다릅니다 — 파일이 "
                "변경됐습니다.",
            )

    def test_apply_order_is_00_then_01_then_02(self):
        # "_00 -> _01 -> _02 순서 검증" — MigrationRunner 자신도
        # 사전순 역전을 감지하면 예외를 던지지만(내부 방어), 여기서는
        # 실제 적용 결과(self.applied)에 세 파일이 이 순서 그대로
        # 나타나는지 이 테스트가 직접 다시 증명한다.
        self.assertLess(
            self.applied.index(_REFRESH_TOKEN_TARGET),
            self.applied.index(_RECONCILIATION_TARGET),
        )
        self.assertLess(
            self.applied.index(_RECONCILIATION_TARGET),
            self.applied.index(_PROVIDER_WARNING_TARGET),
        )

    def test_marketplace_submissions_has_new_provider_warning_columns(self):
        # 성공 경고 보존(Phase 6) — fresh DB에서 marketplace_submissions
        # 테이블에 신규 컬럼 2개가 실제로 생겼는지 확인한다. 이 테이블은
        # _02보다 앞선 다른 Migration이 이미 만들어 둔 기존 테이블이라
        # (이 테스트는 전체 migrations/를 처음부터 순서대로 적용한다),
        # 전체 컬럼 목록을 다시 나열하지 않고 신규 컬럼 2개의 존재만
        # 확인한다.
        columns = self._columns("marketplace_submissions")
        self.assertIn("provider_warning_summary", columns)
        self.assertIn("provider_response_code", columns)

    def test_integrity_and_foreign_key_check_pass(self):

        self.assertEqual(self.integrity_check, "ok")
        self.assertEqual(self.fk_violations, [])

    def test_refresh_token_tables_have_expected_columns(self):

        self.assertEqual(
            self._columns("refresh_token_families"),
            {
                "id", "user_id", "family_id", "access_session_jti",
                "status", "revoked_at", "revoked_reason", "created_at",
            },
        )
        self.assertEqual(
            self._columns("refresh_tokens"),
            {
                "id", "family_id", "jti", "token_hash", "issued_at",
                "expires_at", "consumed_at",
            },
        )

    def test_reconciliation_table_has_expected_columns(self):

        self.assertEqual(
            self._columns("marketplace_submission_reconciliations"),
            {
                "id", "company_id", "submission_id",
                "external_submission_ref", "observed_status_name",
                "reconciled_by_user_id", "reconciled_at", "reason",
                "created_at",
            },
        )

    def test_diagnose_reports_both_targets_as_already_applied(self):

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [])
        self.assertIn(_REFRESH_TOKEN_TARGET, diagnosis["already_applied"])
        self.assertIn(_RECONCILIATION_TARGET, diagnosis["already_applied"])
        self.assertIn(_PROVIDER_WARNING_TARGET, diagnosis["already_applied"])

    def test_reapply_fails_explicitly(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            with self.assertRaises(Exception):
                conn.executescript(
                    (_MIGRATIONS_DIR / _REFRESH_TOKEN_TARGET).read_text(
                        encoding="utf-8",
                    ),
                )
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main()
