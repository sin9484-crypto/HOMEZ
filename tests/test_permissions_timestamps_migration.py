"""
=========================================================
Homez OS

File : tests/test_permissions_timestamps_migration.py

V7 Live Gate 4 재작업 결함 2 후속 — 신규 Migration
(migrations/20260816_01_add_permissions_timestamps.sql)이
`permissions.created_at`/`permissions.updated_at`을 안전하게 추가하고
ORM(`app.domains.permission.model.Permission`) 계약과 최종 스키마가
일치하는지, 기존 행이 보존되는지, 완전 신규 설치에서 SUPER_ADMIN
시딩까지 실제로 성공하는지 고정한다.

이 파일 전체에서 실제 운영 homez.db는 전혀 사용하지 않는다(전부
임시 디렉터리/임시 SQLite 파일). 기존 22개 Migration 전체를 순서대로
적용해 실제 homez.db와 동등한 스키마 상태를 임시 DB에 재현한 뒤, 이
신규 Migration을 적용한다(tests/test_gate_r13_tenant_isolation_
migration.py 등 기존 파일과 동일한 패턴).
=========================================================
"""

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.database.bootstrap import bootstrap_environment
from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import compute_checksum
from app.database.seed import DEFAULT_PERMISSIONS
from app.database.seed import DEFAULT_ROLES
from app.database.seed import seed_environment
from app.domains.permission.model import Permission

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# 실제 homez.db의 schema_migrations 이력 순서(2026-08-16 읽기 전용
# 재확인 결과 그대로, 신규 Migration 직전까지 전부) — 신규 Migration을
# 제외한 전체 22개.
PRIOR_MIGRATIONS = [
    "20260727_00_create_funding_settlement_schema.sql",
    "20260727_add_settlement_ledger_unique_index.sql",
    "20260728_00_create_v24_v3_schema.sql",
    "20260729_00_create_coupang_integration_schema.sql",
    "20260730_00_create_decision_ai_schema.sql",
    "20260730_01_create_auth_session_schema.sql",
    "20260730_02_create_store_connection_schema.sql",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
    "20260802_00_create_media_listing_package_schema.sql",
    "20260802_01_create_account_recovery_schema.sql",
    "20260803_00_create_account_registration_schema.sql",
    "20260805_00_add_marketplace_listing_status_sync.sql",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
    "20260808_00_create_listing_wizard_schema.sql",
    "20260810_00_create_audit_logs_schema.sql",
    "20260814_00_create_user_settings_schema.sql",
    "20260814_01_add_tenant_isolation_company_id.sql",
    "20260815_00_gate2_tenant_isolation_hardening.sql",
    "20260815_01_create_v7_gate3_inventory_schema.sql",
    "20260815_02_create_v7_gate4_order_fulfillment_schema.sql",
    "20260815_03_create_v7_gate5_pricing_settlement_schema.sql",
    "20260815_04_create_v7_gate8_operations_schema.sql",
    "20260816_00_create_v7_gate9_core_foundation_schema.sql",
]

NEW_MIGRATION = "20260816_01_add_permissions_timestamps.sql"

# 2026-08-28 V7 종합 감사 Phase 1 — 오늘 정당하게 추가된 신규 Migration
# 3개(전부 실제 운영 DB 미적용, MigrationRunner.diagnose()의 pending
# 목록과 정확히 일치해야 한다). 파일명뿐 아니라 checksum까지 고정해
# "같은 이름, 다른 내용"으로 조용히 바뀌는 것을 잡는다(값은
# compute_checksum()으로 이번 작업 착수 직후 직접 계산).
NEWEST_MIGRATION_CHECKSUMS = {
    "20260828_00_create_image_rights_evidence_schema.sql":
        "226ffe148cd9249d648132c34acfe2458c1a97727c43825e78816730aa046ea4",
    "20260828_01_add_media_asset_source_tracking.sql":
        "10a04081cf9a53b6a21af8b19a15e799fc06823020515792e09e3e53d1b4be01",
    "20260828_02_add_listing_wizard_soft_delete.sql":
        "b624db8de60b4aaf008beb601ddfebb9fd23bc27692ca0ed2f72ec00ed4baf01",
    # 2026-08-30 V7 후속 안정화 — 정당하게 추가된 신규 Migration 2개
    # (전부 실제 운영·개발 DB 미적용, MigrationRunner.diagnose()의
    # pending 목록과 정확히 일치해야 한다). Refresh Token Rotation
    # 스키마와 제출 정합화 스키마 — 둘 다 완전히 새로운 독립 테이블만
    # 만들 뿐(ALTER TABLE 없음) 기존 테이블은 건드리지 않는다.
    "20260830_00_create_refresh_token_schema.sql":
        "0a9c97b4626342d86cd3d847cb294e47af0fcdefb742b1ef80b73014165f97b9",
    "20260830_01_create_marketplace_submission_reconciliation_schema.sql":
        "187deaa0422c9128b87152a8f63c066a2530dc4814f9e7e817a6147939a0e21f",
    # 2026-08-30 후속 지시(성공 경고 보존, Migration 계약 수정) — 앞의
    # 두 파일과 달리 이 파일은 완전히 새로운 테이블이 아니라 기존
    # marketplace_submissions에 컬럼 2개(provider_warning_summary,
    # provider_response_code)를 추가한다(ALTER TABLE ADD COLUMN).
    # 마찬가지로 실제 운영·개발 DB에는 아직 미적용.
    "20260830_02_add_marketplace_submission_provider_warning.sql":
        "3e55228d93cecff222e167a567b1a102eefbec1fa2b6b0808cc60e2e07ff82f3",
    # 2026-08-31 V7 work 3 — read-only Coupang order collection cursor,
    # fulfillment identity, and unresolved-item persistence. Draft only;
    # production/development databases remain untouched.
    "20260831_00_create_coupang_order_collection_schema.sql":
        "bac4f3eecb5c3220963bca445abb086bec8d6d28e04eabdeda2511f8b8deaf45",
    # 2026-09-07 V7 통합 매입 item4 — purchase_task와 OrderItem을 연결하는
    # order_items.purchase_task_id 컬럼(ALTER TABLE ADD COLUMN, 신규
    # 인덱스 1개). 앞의 항목들과 달리 이 파일은 사용자 승인을 받아
    # 실제 운영 DB(homez.db)에 이미 적용 완료됐다(백업:
    # storage/backups/homez_pre_bootstrap_migration_20260907_235850.db).
    "20260907_00_add_order_item_purchase_task_link.sql":
        "2fbc61e3db7004acc947cfef6db95e66236eb231d461c2b9848ed412c2c4b26f",
    # 2026-09-08 V7 item 7("사용자 계정 기반 매입 연결") — 정당하게
    # 추가된 신규 Migration 1개(purchase_channel_connections/_events
    # 신규 테이블 2개 + purchase_tasks/purchase_records ADD COLUMN
    # 2건). 사용자 승인 범위가 "신규 Model·Migration 초안 작성과 임시
    # DB 검증까지"라서 이 파일은 **실제 운영 DB(homez.db)에 아직
    # 미적용**이다(MigrationRunner.diagnose()의 pending 목록과 일치해야
    # 한다). 임시 DB 검증은 tests/test_purchase_channel_connection_
    # migration.py에서 저장소 전체 Migration 이력을 처음부터 재생하는
    # 방식으로 완료했다.
    "20260908_00_create_purchase_channel_connection_schema.sql":
        "a6729c633c9106a0342edbfd5624b6be788552c00e0c57d6e58330f82b512cd8",
    # 2026-09-08 V7 item 7 후속 — 온채널 발주 시도 기록용 신규 테이블
    # 1개(purchase_order_submission_attempts). 이 파일도 실제 운영·개발
    # DB에는 아직 미적용이다(MigrationRunner.diagnose()의 pending 목록과
    # 일치해야 한다). 이 목록에 없어 2026-09-09 전체 회귀에서
    # test_migration_directory_contains_only_expected_files가 실패했고,
    # 그 원인 조사 중 이 항목이 누락됐음을 확인해 추가했다.
    "20260908_01_create_purchase_order_submission_attempts.sql":
        "1c6cb953094d2a1c9d0e5d3de43b48b887291ce9336bd22019c00a11a8bf63e1",
}

GATE9_MIGRATION = "20260816_00_create_v7_gate9_core_foundation_schema.sql"

# 이 파일 작성 시점(2026-08-16, 이번 작업 착수 직후)에 실제로 계산한
# checksum — 이번 작업 내내 20260816_00 파일을 절대 건드리지 않았음을
# 고정한다. 이 값이 바뀌면 그 파일이 이번 세션에서 변조되었다는 뜻이다.
EXPECTED_GATE9_CHECKSUM = (
    "986c3e7965c8ac240bf48855b4aafaa18274aad089857913fd9b88e6f3ce9389"
)


def _read(filename: str) -> str:

    path = MIGRATIONS_DIR / filename
    return path.read_text(encoding="utf-8")


def _non_comment_sql(content: str) -> str:

    lines = [
        line for line in content.splitlines()
        if not line.strip().startswith("--")
    ]
    return "\n".join(lines)


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    # row: (cid, name, type, notnull, dflt_value, pk)
    return {
        row[1]: {"type": row[2], "notnull": bool(row[3])}
        for row in rows
    }


class MigrationStaticContractTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.content = _read(NEW_MIGRATION)
        cls.executed_sql = _non_comment_sql(cls.content)

    def test_exactly_one_begin_and_commit(self):

        self.assertEqual(
            len(re.findall(r"^BEGIN;", self.executed_sql, re.M)), 1,
        )
        self.assertEqual(
            len(re.findall(r"^COMMIT;", self.executed_sql, re.M)), 1,
        )

    def test_touches_only_permissions_table(self):

        altered = set(
            re.findall(
                r"^ALTER TABLE (\w+) ADD COLUMN", self.executed_sql, re.M,
            ),
        )
        updated = set(
            re.findall(r"^UPDATE (\w+) SET", self.executed_sql, re.M),
        )

        self.assertEqual(altered, {"permissions"})
        self.assertEqual(updated, {"permissions"})

    def test_no_ddl_beyond_two_add_columns(self):
        """
        CREATE TABLE/INDEX/TRIGGER, DROP, RENAME 등 예상하지 않은 스키마
        객체 변경이 전혀 없어야 한다 — ALTER ADD COLUMN 2개 + UPDATE
        1개뿐이어야 한다.
        """

        self.assertNotIn("CREATE TABLE", self.executed_sql.upper())
        self.assertNotIn("CREATE INDEX", self.executed_sql.upper())
        self.assertNotIn("CREATE UNIQUE INDEX", self.executed_sql.upper())
        self.assertNotIn("TRIGGER", self.executed_sql.upper())
        self.assertNotIn("DROP", self.executed_sql.upper())
        self.assertNotIn("RENAME", self.executed_sql.upper())

        add_columns = re.findall(
            r"^ALTER TABLE permissions ADD COLUMN (\w+)",
            self.executed_sql, re.M,
        )
        self.assertEqual(add_columns, ["created_at", "updated_at"])

    def test_gate9_migration_file_unchanged(self):
        """
        이전에 이미 실제 homez.db에 BACKFILLED로 기록된
        20260816_00 파일은 내용도 checksum도 이번 작업에서 절대
        건드리지 않았어야 한다.
        """

        path = MIGRATIONS_DIR / GATE9_MIGRATION
        self.assertTrue(path.exists())
        self.assertEqual(compute_checksum(path), EXPECTED_GATE9_CHECKSUM)

    def test_migration_directory_contains_only_expected_files(self):
        """
        (이전 이름: test_exactly_one_new_migration_file_added — "정확히
        1개"라는 이름은 2026-08-21 이후 여러 차례 정당하게 갱신되면서
        더 이상 실제 계약과 맞지 않아 이름을 바꿨다. 이 테스트의 진짜
        의도는 그때나 지금이나 동일하다: "예상치 못한" 파일이 없는지
        확인하는 것이지, 이후 정당한 Migration 추가를 영구히 막는 것이
        아니다.

        2026-08-21 갱신 — 이 파일이 고정하는 시점(Gate, 22개+이 파일)
        이후 정당하게 추가된 Migration 8개(공급처 검색·상품 연결,
        회사별 공급처 거래정보, Purchase 발주 전송 컬럼, media_asset
        rights_status, audit_logs 시각 컬럼 — 작업 6, suppliers 운영
        스키마 드리프트 해소(CP-1), 채널 정책 엔진 스키마(CP-2),
        AI ProposedAction 스키마(AG-4) — 전부 실제 DB 미적용)를 예상
        목록에 반영한다.

        2026-08-23 갱신(Gate PT-3) — 중앙 알림 전달 엔진 스키마
        (notification_email_logs/notification_preferences/
        notification_event_preferences, 실제 DB 미적용) 1개 추가.

        2026-08-27 갱신 — 쿠팡 Live 제출 추적 컬럼(#36), 감사로그 FK
        보정(#37), 상품 이미지 공개 호스팅 캐시 컬럼(#38) 3개 추가.
        전부 실제 운영 DB에 정식 승인 절차로 적용 완료됨.

        2026-08-28 갱신(V7 종합 감사 Phase 1) — 이미지 권리 증빙 스키마
        (#39), media_assets 출처 추적 컬럼(#40), listing_wizards
        soft delete 컬럼(#41) 3개 추가. **전부 실제 운영 DB에 아직
        미적용**(이번 감사 보고서 `docs/HOMEZ_V7_COMPREHENSIVE_STATUS_
        AUDIT_20260828.md` 작성 시점 기준 pending 3건과 정확히 일치).
        """

        SUBSEQUENT_MIGRATIONS = [
            "20260820_00_create_source_supplier_product_links_schema.sql",
            "20260820_01_create_company_supplier_relations_schema.sql",
            "20260820_02_add_purchase_supplier_order_submission_columns.sql",
            "20260820_03_add_media_asset_rights_status.sql",
            "20260821_00_add_audit_logs_created_at.sql",
            "20260821_01_add_supplier_public_directory_columns.sql",
            "20260821_02_create_channel_policy_schema.sql",
            "20260821_03_create_ai_proposed_actions_schema.sql",
            "20260822_00_create_retail_purchase_schema.sql",
            "20260822_01_create_purchase_task_schema.sql",
            "20260823_00_create_notification_delivery_schema.sql",
            "20260826_00_add_marketplace_submission_live_tracking.sql",
            "20260827_00_repair_notification_audit_user_fk.sql",
            "20260827_01_add_media_asset_public_hosting.sql",
            # 2026-09-09~10 갱신 — HOMEZ V7 개인 베타 P0~P9 연속 구현
            # (Phase 1·3·7·8·9·10)이 추가한 8개. 전부 실제 운영 DB에
            # 아직 미적용(임시 SQLite에서만 검증) — 로그인 잠금
            # 컬럼(Phase 1), 기능별 자동화 모드 스키마(Phase 3), 결제/
            # 환불/환율/공급처능력/가격재고안전 도메인 신규 테이블
            # (Phase 7~10), 가격 인상 감지 기준선 컬럼(Phase 10).
            "20260909_00_add_login_lockout_columns.sql",
            "20260909_01_create_function_automation_state_schema.sql",
            "20260910_00_create_payment_domain_schema.sql",
            "20260910_01_create_refund_domain_schema.sql",
            "20260910_02_create_currency_domain_schema.sql",
            "20260910_03_create_supplier_capability_schema.sql",
            "20260910_04_add_purchase_task_candidate_price_baseline.sql",
            "20260910_05_create_price_stock_safety_schema.sql",
            "20260910_06_create_ai_learning_schema.sql",
            # 2026-09-10 Phase 3(판매신청 게이트, 온채널 공식 답변
            # 반영) — 동일한 이유로 추가.
            "20260910_07_create_purchase_sales_application_schema.sql",
            # 2026-09-11 반자동 완료 라운드(Phase 5·7, 사용자 발주
            # 최종 승인 — purchase_order_approvals 신규 테이블 +
            # purchase_task_policy_settings ADD COLUMN 2건) — 동일한
            # 이유로 추가. 전체 회귀(4197개) 중 재발견된 것과 똑같은
            # "새 Migration 추가 시 이 목록도 함께 갱신해야 한다"는
            # 패턴 — 이번 라운드에서만 3개 파일(이 파일,
            # test_purchase_channel_connection_migration.py,
            # test_migration_restricted_mode_schema_error_handling.py)
            # 에서 동일 원인으로 재발했다. 실제 운영 DB에는 아직
            # 미적용이다.
            "20260911_00_create_purchase_order_approval_schema.sql",
            # 2026-09-11 후속(운영 전 최종 검증 라운드) — 동일한
            # 이유로 추가. 실제 운영 DB에는 아직 미적용이다.
            "20260911_01_add_unknown_resolution_and_tracking_refresh.sql",
            # 2026-09-15 전면 감사 후속(Phase 2, 승인-실행 결합
            # 완성) — purchase_order_approvals에 options_snapshot_json
            # 컬럼 1개 추가. 동일한 이유로 추가. 실제 운영 DB에는
            # 아직 미적용이다.
            "20260915_00_add_purchase_order_approval_options_snapshot.sql",
            # 2026-09-15 전면 감사 후속(Phase 3, 업무 주문 단위 중복
            # 방지 강화) — purchase_order_submission_attempts에 부분
            # UNIQUE INDEX 1개 추가(컬럼 변경 없음). 동일한 이유로
            # 추가. 실제 운영 DB에는 아직 미적용이다.
            "20260915_01_add_purchase_order_submission_attempts_active_task_index.sql",
            # 2026-09-15 전면 감사 후속(Phase 4, 결제·환불 Provider
            # 안전 구조) — refunds에 execution_attempt_started_at
            # 컬럼 1개 추가. 동일한 이유로 추가. 실제 운영 DB에는
            # 아직 미적용이다.
            "20260915_02_add_refund_execution_attempt_marker.sql",
            # 2026-09-15 전면 감사 후속(Phase 5, 백업 암호화 실제
            # 연결) — backup_records에 is_encrypted 컬럼 1개 추가.
            # 동일한 이유로 추가. 실제 운영 DB에는 아직 미적용이다.
            "20260915_03_add_backup_records_is_encrypted.sql",
        ]

        all_files = sorted(
            p.name for p in MIGRATIONS_DIR.glob("*.sql")
        )
        expected = sorted(
            PRIOR_MIGRATIONS + [NEW_MIGRATION] + SUBSEQUENT_MIGRATIONS
            + list(NEWEST_MIGRATION_CHECKSUMS.keys()),
        )
        self.assertEqual(all_files, expected)

    def test_does_not_modify_prior_migration_files(self):

        for filename in PRIOR_MIGRATIONS:
            self.assertTrue(
                (MIGRATIONS_DIR / filename).exists(),
                f"{filename} 파일이 없습니다.",
            )

    def test_newest_migration_files_checksum_locked(self):
        """
        2026-08-28 신규 3개는 파일 존재만이 아니라 checksum까지
        고정한다 — 이름은 같지만 내용이 조용히 바뀌는 것을 잡기
        위해서다(운영 DB에는 아직 적용되지 않았으므로
        `schema_migrations` 자체 checksum 검증이 아직 걸리지 않는
        구간이라 이 테스트가 그 공백을 메운다).
        """

        for filename, expected_checksum in NEWEST_MIGRATION_CHECKSUMS.items():
            path = MIGRATIONS_DIR / filename
            self.assertTrue(path.exists(), f"{filename} 파일이 없습니다.")
            self.assertEqual(
                compute_checksum(path), expected_checksum,
                f"{filename} 내용이 2026-08-28 작성 시점과 달라졌습니다.",
            )


class OrmContractTestCase(unittest.TestCase):
    """
    전체 Migration 체인(기존 22개 + 신규 1개) 적용 후 최종 permissions
    스키마가 Permission ORM 모델과 컬럼명/타입/NOT NULL 계약이 일치하는지
    확인한다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_permissions_columns_match_orm_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            for filename in PRIOR_MIGRATIONS + [NEW_MIGRATION]:
                conn.executescript(_read(filename))

            db_cols = _db_columns(conn, "permissions")

            model_cols = {
                col.name: {
                    "nullable": col.nullable,
                }
                for col in Permission.__table__.columns
            }

            # 컬럼 존재 및 NOT NULL 계약(DEFAULT 값 자체는 이 저장소의
            # 기존 드리프트 테스트 관례상 비교 대상이 아니다 — 20260814_01
            # 헤더 주석 참고, ALTER 기반 placeholder DEFAULT는 서비스
            # 계층에서 항상 명시적으로 덮어써 실사용 값에 영향이 없다).
            for name, expected in model_cols.items():
                self.assertIn(
                    name, db_cols,
                    f"permissions 테이블에 ORM 컬럼 '{name}'이 없습니다.",
                )
                self.assertEqual(
                    db_cols[name]["notnull"], not expected["nullable"],
                    f"permissions.{name}의 NOT NULL 계약이 ORM과 다릅니다.",
                )

            self.assertEqual(db_cols["created_at"]["type"], "DATETIME")
            self.assertEqual(db_cols["updated_at"]["type"], "DATETIME")
            self.assertFalse(db_cols["created_at"]["notnull"] is False)
            self.assertTrue(db_cols["updated_at"]["notnull"] is False)

            # 이 Migration이 새로 만들지 않는 기존 드리프트(company_id)는
            # 그대로 남아 있어야 한다 — 이번 작업 범위 밖이므로 손대지
            # 않았음을 재확인(있어도 없어도 실패시키지 않되, 존재를
            # 기록만 한다 — 실제 운영 DB와 동일해야 하므로 있어야 한다).
            self.assertIn("company_id", db_cols)
        finally:
            conn.close()


class ExistingStateUpgradeTestCase(unittest.TestCase):
    """
    필수 테스트 B: 기존 Migration까지 적용된 뒤 permissions에 기존
    행이 있는 상태에서 신규 Migration을 적용해도 안전한지 확인한다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.new_sql = _read(NEW_MIGRATION)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_prior(self, conn: sqlite3.Connection) -> None:

        for filename in PRIOR_MIGRATIONS:
            conn.executescript(_read(filename))

    def test_existing_rows_preserved_and_backfilled(self):

        before = datetime.utcnow() - timedelta(seconds=5)

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior(conn)

            # 기존 행 삽입(신규 Migration 적용 전) — 실제 운영 DB의
            # permissions 스키마(company_id 포함)를 그대로 반영.
            conn.execute(
                "INSERT INTO permissions (id, company_id, name, code, "
                "description, active) VALUES "
                "(9001, NULL, 'Gate4 Test Perm A', 'GATE4_TEST_A', "
                "'desc a', 1)",
            )
            conn.execute(
                "INSERT INTO permissions (id, company_id, name, code, "
                "description, active) VALUES "
                "(9002, NULL, 'Gate4 Test Perm B', 'GATE4_TEST_B', "
                "NULL, 0)",
            )
            conn.commit()

            conn.executescript(self.new_sql)

            after = datetime.utcnow() + timedelta(seconds=5)

            db_cols = _db_columns(conn, "permissions")
            self.assertIn("created_at", db_cols)
            self.assertIn("updated_at", db_cols)

            rows = conn.execute(
                "SELECT id, name, code, description, active, created_at, "
                "updated_at FROM permissions WHERE id IN (9001, 9002) "
                "ORDER BY id",
            ).fetchall()

            self.assertEqual(len(rows), 2, "기존 행이 보존되지 않았습니다.")

            for row in rows:
                (
                    row_id, name, code, description, active,
                    created_at, updated_at,
                ) = row

                self.assertIsNotNone(created_at)
                self.assertIsNone(
                    updated_at,
                    "updated_at은 추측성 backfill을 하지 않으므로 기존 "
                    "행에서 NULL이어야 합니다.",
                )

                # 정확한 초 단위 일치를 강제하지 않는다 — 이 Migration을
                # 적용한 시각(테스트 시작 몇 초 전 ~ 적용 후 몇 초 뒤)
                # 범위 안에 있는지만 확인한다(승인 원문의 비결정성 회피
                # 지시).
                parsed = datetime.strptime(
                    created_at, "%Y-%m-%d %H:%M:%S",
                )
                self.assertGreaterEqual(parsed, before)
                self.assertLessEqual(parsed, after)

            self.assertEqual(rows[0][1], "Gate4 Test Perm A")
            self.assertEqual(rows[1][1], "Gate4 Test Perm B")

            integrity = conn.execute(
                "PRAGMA integrity_check",
            ).fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_reapply_directly_fails_explicitly(self):
        """
        Runner를 거치지 않고 같은 SQL을 두 번 직접 실행하면 SQLite
        자체가 "duplicate column name"으로 명시적으로 실패해야 한다
        (조용히 무시되지 않음 — 20260814_01 등 기존 ALTER 기반
        Migration과 동일한 계약).
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()


class MigrationFailureAtomicityTestCase(unittest.TestCase):
    """
    필수 상태 5: Migration 도중 실패하는 경우 — 두 ALTER 문 중 두 번째
    (updated_at)가 실패해도 첫 번째(created_at)가 부분 반영되지 않고
    전체가 원자적으로 rollback되는지 확인한다.

    실측 확인(이 테스트 작성 전 직접 재현, venv 기준): SQLite는 스크립트
    실행 중 예외가 발생해도 파일 안의 명시적 BEGIN이 연 Transaction을
    커밋하지 않은 채로 남기고, 연결을 명시적으로 rollback()하거나 그냥
    close()하기만 해도(둘 다) 커밋되지 않은 변경은 전부 사라진다 —
    이 저장소의 다른 Migration 파일들과 마찬가지로 별도의 수동 rollback
    로직 없이 SQLite 자체의 Transaction 원자성에 의존하는 설계가 안전함을
    확인했다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_partial_pre_existing_column_causes_full_rollback(self):

        conn = sqlite3.connect(self.db_path)
        try:
            for filename in PRIOR_MIGRATIONS:
                conn.executescript(_read(filename))

            # 외부 경로로 updated_at만 이미 존재하는 상태를 시뮬레이션
            # (예: 이 저장소의 계약 밖에서 누군가 수동으로 컬럼을 추가한
            # 극단적 케이스) — 이 Migration의 두 번째 ALTER가 반드시
            # 실패하도록 만든다.
            conn.execute("ALTER TABLE permissions ADD COLUMN updated_at DATETIME")
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(_read(NEW_MIGRATION))

            # executescript 실패 후 이 연결은 아직 커밋되지 않은
            # Transaction을 들고 있다 — 명시적으로 닫아 SQLite 자신의
            # 원자적 rollback(커밋되지 않은 변경 전체 폐기)을 유도한다
            # (MigrationRunner.apply_pending()도 예외 발생 시 별도
            # rollback 호출 없이 그대로 예외를 전파하므로, 호출자가
            # 연결을 닫거나 재사용하지 않는 것만으로 동일하게 안전하다).
        finally:
            conn.close()

        conn2 = sqlite3.connect(self.db_path)
        try:
            cols = _db_columns(conn2, "permissions")
            self.assertNotIn(
                "created_at", cols,
                "실패한 Migration의 첫 번째 ALTER(created_at)가 "
                "부분적으로 반영된 채 남아 있습니다 — 원자성 위반.",
            )
            # updated_at은 우리가 시뮬레이션을 위해 실패 이전에 별도로
            # 미리 commit한 것이므로 그대로 남아 있는 것이 정상이다.
            self.assertIn("updated_at", cols)

            integrity = conn2.execute(
                "PRAGMA integrity_check",
            ).fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn2.close()


class MigrationRunnerIntegrationTestCase(unittest.TestCase):
    """
    실제 공식 MigrationRunner(app/database/migration_runner.py)를 통해
    전체 체인을 빈 DB에 적용하고, checksum/이력/재실행 멱등성을
    확인한다(빈 DB에서 새로 적용하는 경로 — 상태 1).
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        self.runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)

    def test_full_chain_applies_via_runner_and_is_idempotent(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            applied_first = self.runner.apply_pending(conn)

            self.assertIn(NEW_MIGRATION, applied_first)
            self.assertEqual(
                applied_first.count(NEW_MIGRATION), 1,
                "신규 Migration이 정확히 1회만 적용되어야 합니다.",
            )

            history = self.runner.get_history_readonly(conn)
            self.assertIn(NEW_MIGRATION, history)
            self.assertEqual(history[NEW_MIGRATION].status, "APPLIED")
            self.assertEqual(
                history[NEW_MIGRATION].checksum,
                compute_checksum(MIGRATIONS_DIR / NEW_MIGRATION),
            )

            # 재실행(상태 3에 해당하는 실전 시나리오 — 이미 Runner
            # 이력으로 적용 완료된 DB에 bootstrap을 다시 돌리는 경우)은
            # 조용히 아무 것도 하지 않아야 한다(중복 삽입/오류 없음).
            applied_second = self.runner.apply_pending(conn)
            self.assertEqual(applied_second, [])

            diagnosis = self.runner.diagnose(conn)
            self.assertEqual(diagnosis["pending"], [])
            self.assertIn(NEW_MIGRATION, diagnosis["already_applied"])
        finally:
            conn.close()


class CleanInstallEndToEndTestCase(unittest.TestCase):
    """
    필수 테스트 C: 완전히 새로운 임시 디렉터리와 빈 DB에서 공식
    부트스트랩 → seed_environment() → SUPER_ADMIN 역할·권한 시딩 →
    애플리케이션 startup/import → configure_mappers() → 제한 모드 상태
    확인 → 재시작 후 상태 재확인까지 실제 공식 진입점으로 수행한다.
    """

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate4_perm_e2e_"))
        self.db_path = self.tmp_dir / "data" / "homez.db"
        self.backups_dir = self.tmp_dir / "backups"

    def tearDown(self):

        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_clean_install_bootstrap_seed_super_admin_and_restart(self):

        # 1) 전체 Migration
        result = bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        # 제한 모드(승인 대기) 상태가 아니어야 정상 진행된 것이다.
        self.assertFalse(result.migration_approval_required)
        self.assertTrue(self.db_path.exists())

        # 2) seed_environment() — roles/permissions/role_permissions
        seed_result = seed_environment(self.db_path)
        self.assertEqual(seed_result["roles_added"], len(DEFAULT_ROLES))
        self.assertEqual(
            seed_result["permissions_added"], len(DEFAULT_PERMISSIONS),
        )

        conn = sqlite3.connect(str(self.db_path))
        try:
            # ORM이 조회했던 것과 동일한 컬럼이 실제로 존재하는지 raw
            # SQL로도 재확인(원래 결함의 직접 재현 경로).
            perm_cols = _db_columns(conn, "permissions")
            self.assertIn("created_at", perm_cols)
            self.assertIn("updated_at", perm_cols)

            super_admin_role_id = conn.execute(
                "SELECT id FROM roles WHERE code = 'SUPER_ADMIN'",
            ).fetchone()
            self.assertIsNotNone(
                super_admin_role_id, "SUPER_ADMIN 역할이 시딩되지 않았습니다.",
            )
            super_admin_role_id = super_admin_role_id[0]

            # 4) SUPER_ADMIN 역할·권한 시딩 확인 — 이 Migration의 목적은
            # ORM으로 permissions를 조회/삽입할 때 "no such column:
            # permissions.created_at"이 나던 결함을 없애는 것이다.
            # seed_permissions()가 예외 없이 38개 전부 삽입했는지가
            # 실제 검증 대상이다.
            permission_count = conn.execute(
                "SELECT COUNT(*) FROM permissions",
            ).fetchone()[0]
            self.assertEqual(permission_count, len(DEFAULT_PERMISSIONS))

            # 참고(이번 작업 범위 밖, 새로 발견한 결함 아님 — 이미
            # app/database/seed_role_permission.py의
            # seed_role_permissions() docstring에 기록되어 있던 기존
            # 결함): `role.permissions = permissions` 대입은 Role
            # 모델에 `permissions` 관계가 매핑되어 있지 않아(존재하는
            # 것은 `role_permissions`뿐) 조용한 no-op이다. 그 결과
            # role_permissions 테이블은 seed_environment() 이후에도
            # 계속 0행이다 — 실측 확인(venv 기준). 이 결함은
            # created_at/updated_at 추가와 무관하고, 이 세션이 새로
            # 발견한 것도 아니므로 여기서 수정하지 않는다. 대신 그
            # "알려진 현재 동작"을 그대로 고정해 향후 누군가 이 부분을
            # 조용히 더 악화시키면 회귀로 드러나게 한다.
            linked_count = conn.execute(
                "SELECT COUNT(*) FROM role_permissions WHERE role_id = ?",
                (super_admin_role_id,),
            ).fetchone()[0]
            self.assertEqual(
                linked_count, 0,
                "role_permissions 연결 동작이 바뀌었습니다 — 이 값이 "
                "0에서 달라졌다면 app/database/seed_role_permission.py의 "
                "기존 no-op 결함(Role.permissions 관계 미매핑)이 "
                "다른 변경으로 바뀐 것일 수 있으니 별도로 조사하십시오 "
                "(이번 Migration 범위 밖).",
            )

            # created_at이 실제로 채워졌는지(NULL이 아닌지) 원본 결함
            # (no such column) 재현 여부와는 별개로 데이터 정합성도 확인.
            null_created_at = conn.execute(
                "SELECT COUNT(*) FROM permissions WHERE created_at IS NULL",
            ).fetchone()[0]
            self.assertEqual(null_created_at, 0)
        finally:
            conn.close()

        # 3) + 5) SUPER_ADMIN 역할·권한 시딩 이후 최초 관리자 생성 —
        # 공식 경로(atomic_create_first_admin)로 실제 계정을 만들 수
        # 있어야 한다(테스트 전용 가짜 값만 사용, 실제 비밀번호 아님).
        from app.core.first_admin_setup import (
            FirstAdminSetupStatus,
            atomic_create_first_admin,
        )

        outcome = atomic_create_first_admin(
            str(self.db_path),
            username="gate4_perm_e2e_admin",
            email="gate4_perm_e2e_admin@example.com",
            password_hash="test_only_dummy_hash_not_a_real_password",
            role_id=super_admin_role_id,
            company_name="Gate4 Perm E2E Test Company",
            name="Gate4 Perm E2E Admin",
        )
        self.assertEqual(outcome.status, FirstAdminSetupStatus.SUCCESS)

        # 5) + 6) 애플리케이션 startup/import 및 configure_mappers() —
        # tests/test_homez_auth_login.py와 동일한 방식으로 별도
        # 프로세스에서 전체 app.main import가 mapper 오류 없이
        # 성공하는지 확인한다(이 서브프로세스는 이번 테스트의 임시
        # db_path가 아니라 프로세스 기본 설정을 쓴다 — 목적은 ORM
        # mapper 구성 자체의 성공 여부이지 특정 DB 내용이 아니다).
        proc = subprocess.run(
            [
                sys.executable, "-c",
                "import app.main\n"
                "from sqlalchemy.orm import configure_mappers\n"
                "configure_mappers()\n"
                "print('OK', len(app.main.app.routes))\n",
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            f"app.main import 또는 configure_mappers()가 실패했습니다.\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}",
        )
        self.assertIn("OK", proc.stdout)

        # 8) 재시작 후 상태 재확인 — 두 번째 bootstrap/seed 호출은
        # 완전히 멱등해야 한다(신규 설치로 재취급하지 않고, 추가
        # role/permission도 없어야 한다).
        second_bootstrap = bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertFalse(second_bootstrap.is_new_install)
        self.assertFalse(second_bootstrap.migration_approval_required)

        second_seed = seed_environment(self.db_path)
        self.assertEqual(second_seed["roles_added"], 0)
        self.assertEqual(second_seed["permissions_added"], 0)

        conn = sqlite3.connect(str(self.db_path))
        try:
            permission_count_after_restart = conn.execute(
                "SELECT COUNT(*) FROM permissions",
            ).fetchone()[0]
            self.assertEqual(
                permission_count_after_restart, len(DEFAULT_PERMISSIONS),
                "재시작 후 permissions 행 수가 달라졌습니다(멱등성 위반).",
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
