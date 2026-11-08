"""
=========================================================
Homez OS

File : tests/test_health_replaced_links.py

2026-11-09 — 11-08 쿠팡 키를 다시 받으며 새 연결(#2)을 만들자 옛 연결(#1)에 묶인 공급처 연결 3건·SKU 대응 1건이 따라오지 않았고, 어느 화면도 알려 주지
않아 DB를 직접 읽고서야 알았다. 시스템 점검(T1)에 LINKS 항목을 더해 고정한다.

  - 규칙(`health_summary.py`, 순수): 바뀐 연결에 남은 공급처 연결·SKU 대응이 하나라도 있으면 WARN, 없으면 통과, 읽지 못하면 unknown(정상 아님).
  - 수집(`health_service.py`, 읽기만): 꺼졌거나(DISABLED) 자격증명이 지워진(NOT_CONFIGURED) 연결이면서 **같은 회사·같은 마켓에 CONNECTED 연결이 따로 있을 때만**
    센다. 하나뿐인 연결이 잠시 오류(ERROR)인 경우, 다른 마켓·다른 회사 연결, 멈춘(ACTIVE 아님) 공급처 연결은 세지 않는다.
  - AI 설명·쉬운 보기 문구가 새 코드를 가진다.
=========================================================
"""

import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.diagnostics import health_ai_service as ai
from app.domains.diagnostics import health_summary as hs
from app.domains.diagnostics.health_service import HealthService
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.store_connection.model import StoreConnection

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 9, 3, 0, 0)


def snap(**overrides):
    base = {
        "now": NOW, "scheduler_enabled": False, "latest_backup_at": NOW, "rehearsal_known": True,
        "latest_rehearsal": {"at": NOW, "ok": True}, "disk_free_bytes": 50 * 1024 ** 3, "estop_active": False,
        "detection": {"health_code": "OK", "minutes_since_attempt": 3}, "reads": {"lapsed": [], "never": []},
        "replaced_links": {"links": 0, "resolutions": 0},
    }
    base.update(overrides)
    return base


def check_of(result, code):
    found = [c for c in result["checks"] if c["code"] == code]
    return found[0] if found else None


class RuleTests(unittest.TestCase):

    def test_links_left_on_a_replaced_connection_warn_with_counts(self):
        result = hs.evaluate(snap(replaced_links={"links": 3, "resolutions": 1}))
        found = check_of(result, "LINKS_ON_REPLACED_CONNECTION")
        self.assertEqual((found["level"], found["params"]), (hs.WARN, {"links": 3, "resolutions": 1}))
        self.assertEqual(result["overall"], hs.WARN)
        self.assertNotIn("LINKS", result["passed"])

    def test_a_sku_mapping_alone_also_warns(self):
        self.assertIsNotNone(check_of(hs.evaluate(snap(replaced_links={"links": 0, "resolutions": 1})), "LINKS_ON_REPLACED_CONNECTION"))

    def test_nothing_left_behind_passes(self):
        result = hs.evaluate(snap())
        self.assertIn("LINKS", result["passed"])
        self.assertIsNone(check_of(result, "LINKS_ON_REPLACED_CONNECTION"))

    def test_unread_value_is_unknown_not_ok(self):
        for value in (None, "x", 3):
            result = hs.evaluate(snap(replaced_links=value))
            self.assertIn("LINKS", result["unknown"], repr(value))
            self.assertNotIn("LINKS", result["passed"], repr(value))
        without_key = snap()
        del without_key["replaced_links"]
        self.assertIn("LINKS", hs.evaluate(without_key)["unknown"])


class CollectionTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            StoreConnection.__table__, SupplierOptionLink.__table__, OrderSkuResolution.__table__,
        ])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.addCleanup(self._cleanup)
        self.n = 0

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def store(self, status, *, company_id=1, market="COUPANG"):
        self.n += 1
        row = StoreConnection(
            company_id=company_id, marketplace_code=market, display_name=f"연결{self.n}", seller_identifier=f"seller-{self.n}",
            connection_status=status, created_by=1, creation_idempotency_key=f"k-{self.n}", creation_request_fingerprint="f" * 64,
        )
        self.db.add(row)
        self.db.commit()
        return row.id

    def link(self, store_id, sku, *, company_id=1, status="ACTIVE"):
        self.db.add(SupplierOptionLink(
            company_id=company_id, store_connection_id=store_id, channel_sku=sku, purchase_connection_id=4,
            supplier_product_code="P1", supplier_option_id=f"O-{sku}", units_per_sale=1, status=status, version=1,
            confirmed_by=1, confirmed_at=NOW,
        ))
        self.db.commit()

    def resolution(self, store_id, sku, *, company_id=1):
        self.db.add(OrderSkuResolution(company_id=company_id, store_connection_id=store_id, channel_sku=sku, inventory_sku_id=1, created_by=1))
        self.db.commit()

    def counted(self):
        return HealthService(self.db, now_factory=lambda: NOW)._replaced_links()

    def test_the_11_08_case_is_counted(self):
        old = self.store("DISABLED")
        new = self.store("CONNECTED")
        for sku in ("A", "B", "C"):
            self.link(old, sku)
        self.resolution(old, "A")
        self.link(new, "D")                  # 새 연결의 기록은 세지 않는다
        self.assertEqual(self.counted(), {"links": 3, "resolutions": 1})

    def test_credential_deleted_connection_counts_too(self):
        old = self.store("NOT_CONFIGURED")
        self.store("CONNECTED")
        self.link(old, "A")
        self.assertEqual(self.counted(), {"links": 1, "resolutions": 0})

    def test_no_live_replacement_means_nothing_to_report(self):
        # 하나뿐인 연결이 꺼졌거나 오류여도 '바뀐 연결'이 아니다 — 기록은 그 연결이 다시 살아나면 그대로 맞는다.
        only = self.store("DISABLED")
        self.link(only, "A")
        self.assertEqual(self.counted(), {"links": 0, "resolutions": 0})

    def test_a_temporarily_failing_connection_beside_a_live_one_is_not_counted(self):
        failing = self.store("ERROR")
        self.store("CONNECTED")
        self.link(failing, "A")
        self.assertEqual(self.counted(), {"links": 0, "resolutions": 0})

    def test_other_markets_and_other_companies_do_not_count_as_replacements(self):
        old = self.store("DISABLED")
        self.store("CONNECTED", market="NAVER_SMARTSTORE")
        self.store("CONNECTED", company_id=2)
        self.link(old, "A")
        self.assertEqual(self.counted(), {"links": 0, "resolutions": 0})

    def test_stopped_links_are_not_counted(self):
        old = self.store("DISABLED")
        self.store("CONNECTED")
        self.link(old, "A", status="INACTIVE")
        self.assertEqual(self.counted(), {"links": 0, "resolutions": 0})

    def test_unreadable_tables_give_none(self):
        engine = create_engine("sqlite://")
        db = sessionmaker(bind=engine)()
        try:
            self.assertIsNone(HealthService(db, now_factory=lambda: NOW)._replaced_links())
        finally:
            db.close()
            engine.dispose()

    def test_summary_carries_the_warning(self):
        old = self.store("DISABLED")
        self.store("CONNECTED")
        self.link(old, "A")
        service = HealthService(self.db, scheduler_getter=lambda: None, scheduler_enabled=False, disk_path=Path(self.db_path), expected_jobs=[], now_factory=lambda: NOW)
        found = check_of(service.summary(), "LINKS_ON_REPLACED_CONNECTION")
        self.assertEqual(found["params"], {"links": 1, "resolutions": 0})

    def test_collection_only_reads(self):
        src = (ROOT / "app/domains/diagnostics/health_service.py").read_text(encoding="utf-8")
        body = src[src.index("def _replaced_links"):src.index("def snapshot")]
        for forbidden in (r"\.add\(", r"\.commit\(", r"\.execute\(", r"\bupdate\(", r"\bdelete\(", r"\.merge\(", r"\.flush\("):
            self.assertNotRegex(body, forbidden)


class ExplanationAndTextTests(unittest.TestCase):

    def test_ai_meaning_and_only_the_count_is_sent(self):
        self.assertTrue(ai.CODE_MEANING["LINKS_ON_REPLACED_CONNECTION"].strip())
        checks = [{"code": "LINKS_ON_REPLACED_CONNECTION", "level": "WARN", "params": {"links": 3, "resolutions": 1, "names": "<b>x</b>"}}]
        self.assertEqual([c["code"] for c in ai.actionable_checks({"checks": checks})], ["LINKS_ON_REPLACED_CONNECTION"])
        prompt = ai.build_user_prompt(checks)
        self.assertIn('"links_left_behind": 3', prompt)
        self.assertNotIn("<b>", prompt)

    def test_easy_view_has_text_in_both_languages_with_the_count(self):
        view = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
        texts = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
        keys = dict(re.findall(r"([A-Z_]+): \"(easy\.brief\.sys_[a-z_]+)\"", view))
        key = keys["LINKS_ON_REPLACED_CONNECTION"]
        found = re.findall(r'"' + re.escape(key) + r'": "([^"]*)"', texts)
        self.assertEqual(len(found), 2)                     # 한국어·영어 한 번씩
        for text in found:
            self.assertIn("{n}", text)
        self.assertIn("n: val(p.links)", view)              # 문구의 {n}에 실제 개수가 들어간다


if __name__ == "__main__":
    unittest.main()
