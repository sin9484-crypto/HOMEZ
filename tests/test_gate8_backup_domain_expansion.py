"""
=========================================================
Homez OS

File : tests/test_gate8_backup_domain_expansion.py

V7 Gate 8(2026-08-15) — 목표 1/2 검증.

1) 백업 엔진이 Gate 3~7 신규 도메인(inventory/order/purchase/
   shipment/return_order/pricing)까지 실제로 포함해서 백업하는지
   확인·고정. `BackupService.create_backup()`은 테이블 단위가 아니라
   `sqlite3.Connection.backup()`(SQLite 온라인 백업 API, 파일 전체
   스냅샷)을 쓰므로 스키마 인식이 전혀 없다 — 이 테스트는 그 설계가
   실제로 신규 도메인까지 커버함을 데이터로 직접 증명해 향후 회귀를
   막는다(코드 추가가 필요 없다는 사실 자체를 고정).
2) 보존 정책(retention) 조회가 올바르게 "최신 N개를 제외한 나머지"를
   반환하고, 그 자체로는 어떤 파일도 삭제하지 않는지 확인.

전부 임시 SQLite 파일만 사용한다(실제 homez.db는 이 테스트에서
전혀 열지 않는다).
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — 전 도메인 Model을 Base.metadata에 등록
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.backup.encryption import decrypt_file
from app.domains.backup.encryption import get_or_create_backup_encryption_key
from app.domains.backup.model import BackupRecord
from app.domains.backup.service import BackupService
from app.domains.company.model import Company  # noqa: F401
from app.domains.inventory.model import InventorySku
from app.domains.role.model import Role  # noqa: F401
from app.domains.user.model import User  # noqa: F401


class BackupCoversGate3To7DomainsTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate8_backup_"))
        self.source_db_path = self.tmp_dir / "source.db"
        self.backups_dir = self.tmp_dir / "backups"

        self.source_engine = create_engine(
            f"sqlite:///{self.source_db_path}",
        )
        Base.metadata.create_all(bind=self.source_engine)
        SourceSession = sessionmaker(bind=self.source_engine)
        source_db = SourceSession()

        source_db.add(
            InventorySku(
                company_id=1,
                product_candidate_id=1,
                sku_code="GATE8-PROBE-SKU",
                option_label="probe-option",
                available_qty=17,
                reserved_qty=3,
                safety_stock=1,
            ),
        )
        source_db.commit()
        source_db.close()
        self.source_engine.dispose()

        # BackupRecord 이력을 저장할 별도 임시 앱 DB(백업 대상인
        # source.db와는 다른 파일이어야 한다).
        self.app_db_path = self.tmp_dir / "app.db"
        self.app_engine = create_engine(f"sqlite:///{self.app_db_path}")
        Base.metadata.create_all(bind=self.app_engine)
        self.AppSession = sessionmaker(bind=self.app_engine)
        self.db = self.AppSession()
        self.credential_store = InMemoryCredentialStore()

    def tearDown(self):

        self.db.close()
        self.app_engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_whole_file_backup_includes_all_gate3_to_7_tables_and_data(self):
        """2026-09-15 전면 감사 후속(Phase 5) — 백업 파일은 이제
        암호화되어 있으므로, 같은 키로 복호화한 사본을 열어 테이블·
        데이터가 실제로 포함됐는지 확인한다(암호화 이전에는 파일을
        직접 열어 확인했다)."""

        service = BackupService(self.db, self.credential_store)

        record = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source="manual",
        )

        key = get_or_create_backup_encryption_key(self.credential_store)
        decrypted_path = self.tmp_dir / "decrypted_for_test.db"
        decrypt_file(Path(record.file_path), decrypted_path, key)

        conn = sqlite3.connect(f"file:{decrypted_path}?mode=ro", uri=True)
        try:
            table_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }

            gate3_to_7_tables = {
                "inventory_skus", "inventory_reservations",
                "inventory_ledger_events", "inventory_channel_mappings",
                "orders", "order_items", "order_ingestion_events",
                "order_status_events",
                "purchases", "purchase_items",
                "shipments", "shipment_items", "shipment_status_events",
                "return_orders", "return_order_status_events",
                "product_pricings", "price_change_requests",
                "price_change_status_events", "margin_snapshots",
                "settlement_reconciliations",
            }

            missing = gate3_to_7_tables - table_names
            self.assertEqual(
                missing, set(),
                f"백업 파일에 Gate 3~7 테이블이 빠져 있습니다: {missing}",
            )

            row = conn.execute(
                "SELECT sku_code, available_qty, reserved_qty "
                "FROM inventory_skus WHERE sku_code=?",
                ("GATE8-PROBE-SKU",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row, ("GATE8-PROBE-SKU", 17, 3))
        finally:
            conn.close()


class BackupRetentionTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate8_retain_"))
        self.source_db_path = self.tmp_dir / "source.db"
        self.backups_dir = self.tmp_dir / "backups"

        sqlite3.connect(str(self.source_db_path)).close()

        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(f"sqlite:///{self.app_db_path}")
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.credential_store = InMemoryCredentialStore()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_backup(self, label: str) -> BackupRecord:

        service = BackupService(self.db, self.credential_store)
        record = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source="manual",
            label=label,
        )
        time.sleep(0.02)  # created_at 정렬이 안정적으로 갈리도록
        return record

    def test_retention_returns_only_backups_beyond_keep_count(self):

        for i in range(5):
            self._make_backup(f"backup-{i}")

        service = BackupService(self.db, self.credential_store)

        beyond = service.list_backups_beyond_retention(keep_count=2)

        self.assertEqual(len(beyond), 3)
        # 가장 오래된 것부터 나와야 삭제 후보 우선순위가 자연스럽다.
        labels = [b.label for b in beyond]
        self.assertEqual(labels, ["backup-0", "backup-1", "backup-2"])

    def test_retention_with_keep_count_covering_all_returns_empty(self):

        for i in range(3):
            self._make_backup(f"backup-{i}")

        service = BackupService(self.db, self.credential_store)

        beyond = service.list_backups_beyond_retention(keep_count=10)

        self.assertEqual(beyond, [])

    def test_retention_check_never_deletes_files(self):

        records = [self._make_backup(f"backup-{i}") for i in range(4)]

        service = BackupService(self.db, self.credential_store)
        service.list_backups_beyond_retention(keep_count=1)

        for record in records:
            self.assertTrue(
                Path(record.file_path).exists(),
                f"retention 조회만으로 파일이 삭제되면 안 됩니다: "
                f"{record.file_path}",
            )

        # 이력 테이블 행수도 조회로 인해 줄지 않는다.
        remaining = self.db.query(BackupRecord).count()
        self.assertEqual(remaining, 4)


if __name__ == "__main__":
    unittest.main()
