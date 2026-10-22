"""
=========================================================
Homez OS

File : tests/test_suggestion_record_service.py

2026-10-23 — 마케팅 AI M4-full 저장소: Migration `20261023_00`(Model DDL과 같은지·임시 DB 적용·재적용 거절·rollback 주석), 제안 기록 서비스(기록·채택·옵션 연결·목록·회사 범위),
전·후 비교(읽기가 완전할 때만, 판정 없음)와 API를 고정한다. 임시 SQLite·가짜 매출 읽기 — 네트워크·실제 DB 0.
=========================================================
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.ai_learning import schema
from app.domains.ai_learning.model import MarketingSuggestionRecord
from app.domains.ai_learning.suggestion_record_service import SuggestionRecordService

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261023_00_create_marketing_suggestion_record_schema.sql"
NOW = datetime(2026, 10, 23, 9, 0, 0)


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace(",\n", ", ")).replace("( ", "(").replace(" )", ")").strip()


class MigrationTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_the_sql_is_exactly_the_models_canonical_ddl(self):
        dialect = sqlite_dialect.dialect()
        table = MarketingSuggestionRecord.__table__
        expected = [_normalize(str(CreateTable(table).compile(dialect=dialect)))]
        expected += [_normalize(str(CreateIndex(ix).compile(dialect=dialect))) for ix in sorted(table.indexes, key=lambda i: i.name)]
        statements = [_normalize(m) for m in re.findall(r"(CREATE (?:UNIQUE )?(?:TABLE|INDEX)[^;]*);", MIGRATION.read_text(encoding="utf-8"), flags=re.S)]
        self.assertEqual([s.replace(" ,", ",").rstrip(",") for s in statements], [e.rstrip(";").replace(" ,", ",") for e in expected])

    def test_it_applies_cleanly_keeps_existing_data_and_refuses_a_second_run(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("CREATE TABLE existing_marker (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO existing_marker (v) VALUES ('keep')")
            conn.commit()
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT v FROM existing_marker").fetchall(), [("keep",)])
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(marketing_suggestion_records)")], [c.name for c in MarketingSuggestionRecord.__table__.columns])
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        finally:
            conn.close()

    def test_the_rollback_note_is_only_a_comment_and_removes_everything(self):
        text = MIGRATION.read_text(encoding="utf-8")
        tail = text.split("COMMIT;")[1]
        for line in tail.splitlines():
            if line.strip():
                self.assertTrue(line.startswith("--"), line)
        rollback = "\n".join(line[3:] for line in tail.splitlines() if line.startswith("-- DROP"))
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(text)
            conn.executescript(rollback)
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE '%marketing_suggestion_records%'").fetchone()[0], 0)
        finally:
            conn.close()

    def test_no_draft_copy_is_left_behind(self):
        self.assertFalse((ROOT / "docs" / "migration_drafts" / "20261023_00_create_marketing_suggestion_record_schema.sql.draft").exists())


class FakeSales:

    def __init__(self, status, rows):
        self.status, self.rows, self.calls = status, rows, []

    def read_rows(self, company_id, days):
        self.calls.append((company_id, days))
        return self.status, self.rows


def sale(order, day, vendor="111", kind="SALE"):
    return {"vendor_item_id": vendor, "order_id": order, "sale_type": kind, "quantity": 1, "sale_amount": "10000", "service_fee": "1000",
            "settlement_amount": "9000", "recognition_date": day}


class Base_(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine, tables=[MarketingSuggestionRecord.__table__])
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def service(self, sales=None):
        return SuggestionRecordService(self.db, now_factory=lambda: NOW, sales_factory=(lambda db: sales) if sales else None)


class ServiceTests(Base_):

    def test_create_cleans_and_defaults(self):
        row = self.service().create(1, 7, "PRODUCT_NAME", "  새   상품명  ", None, "백산수", "123456", True)
        self.assertEqual((row["kind"], row["suggested_value"], row["product_ref"], row["vendor_item_id"], row["suggested_at"], row["adopted"]),
                         ("PRODUCT_NAME", "새 상품명", "백산수", "123456", "2026-10-23", True))
        self.assertEqual(row["adopted_at"], "2026-10-23T09:00:00")
        saved = self.db.query(MarketingSuggestionRecord).one()
        self.assertEqual((saved.company_id, saved.created_by), (1, 7))

    def test_create_rejects_bad_values_and_writes_nothing(self):
        svc = self.service()
        for args in (("DISCOUNT", "x"), ("PRICE", "   "), ("PRICE", "x" * 301), ("PRICE", 5)):
            with self.assertRaises(BadRequestException, msg=repr(args)):
                svc.create(1, 7, *args)
        with self.assertRaises(BadRequestException):
            svc.create(1, 7, "PRICE", "9900", date(2026, 10, 24))                    # 내일은 안 된다
        with self.assertRaises(BadRequestException):
            svc.create(1, 7, "PRICE", "9900", "2026-10-01")                           # 날짜 객체만
        for vendor in ("12a", "-1", True, "1" * 41):
            with self.assertRaises(BadRequestException, msg=repr(vendor)):
                svc.create(1, 7, "PRICE", "9900", None, None, vendor)
        with self.assertRaises(BadRequestException):
            svc.create(1, 7, "PRICE", "9900", None, None, None, "yes")
        self.assertEqual(self.db.query(MarketingSuggestionRecord).count(), 0)
        self.assertEqual(svc.create(1, 7, "PRICE", "9900", date(2026, 10, 23))["suggested_at"], "2026-10-23")

    def test_today_is_the_korean_date(self):
        late = SuggestionRecordService(self.db, now_factory=lambda: datetime(2026, 10, 23, 16, 0, 0))     # 한국 시각 10월 24일 01시
        self.assertEqual(late.create(1, 7, "PRICE", "9900")["suggested_at"], "2026-10-24")
        self.assertEqual(late.create(1, 7, "PRICE", "9900", date(2026, 10, 24))["suggested_at"], "2026-10-24")
        with self.assertRaises(BadRequestException):
            late.create(1, 7, "PRICE", "9900", date(2026, 10, 25))
        early = SuggestionRecordService(self.db, now_factory=lambda: datetime(2026, 10, 23, 14, 59, 0))   # 한국 시각 10월 23일 23시 59분
        with self.assertRaises(BadRequestException):
            early.create(1, 7, "PRICE", "9900", date(2026, 10, 24))

    def test_update_adoption_and_link(self):
        svc = self.service()
        rid = svc.create(1, 7, "SEARCH_KEYWORD", "생수 2L")["id"]
        row = svc.update(1, rid, adopted=True)
        self.assertEqual((row["adopted"], row["adopted_at"], row["vendor_item_id"]), (True, "2026-10-23T09:00:00", None))
        row = svc.update(1, rid, vendor_item_id="987")
        self.assertEqual((row["adopted"], row["vendor_item_id"]), (True, "987"))                 # 넘기지 않은 값은 그대로
        row = svc.update(1, rid, vendor_item_id="")
        self.assertIsNone(row["vendor_item_id"])                                                 # 빈 글은 연결 해제
        row = svc.update(1, rid, adopted=False)
        self.assertEqual((row["adopted"], row["adopted_at"]), (False, None))
        with self.assertRaises(BadRequestException):
            svc.update(1, rid, adopted="true")
        with self.assertRaises(BadRequestException):
            svc.update(1, rid, vendor_item_id="abc")

    def test_adoption_time_is_set_once_and_only_when_adopted(self):
        clock = [NOW]
        svc = SuggestionRecordService(self.db, now_factory=lambda: clock[0])
        row = svc.create(1, 7, "PRICE", "9900")
        self.assertEqual((row["adopted"], row["adopted_at"]), (False, None))          # 채택하지 않았으면 채택 시각이 없다
        svc.update(1, row["id"], adopted=True)
        clock[0] = datetime(2026, 10, 24, 9, 0, 0)
        again = svc.update(1, row["id"], adopted=True)                                 # 다시 채택 표시해도 처음 시각을 지킨다
        self.assertEqual(again["adopted_at"], "2026-10-23T09:00:00")

    def test_the_list_is_capped_at_two_hundred(self):
        self.db.add_all([MarketingSuggestionRecord(company_id=1, kind="PRICE", suggested_value=str(i), suggested_at=NOW, adopted=False, created_at=NOW) for i in range(201)])
        self.db.commit()
        self.assertEqual(len(self.service().list(1, limit=9999)), 200)

    def test_company_scope(self):
        svc = self.service()
        rid = svc.create(1, 7, "PRICE", "9900")["id"]
        with self.assertRaises(NotFoundException):
            svc.update(2, rid, adopted=True)
        self.assertEqual(svc.list(2), [])
        self.assertEqual([r["id"] for r in svc.list(1)], [rid])

    def test_list_is_newest_first_and_capped(self):
        svc = self.service()
        ids = [svc.create(1, 7, "PRICE", str(i))["id"] for i in range(3)]
        self.assertEqual([r["id"] for r in svc.list(1)], ids[::-1])
        self.assertEqual(len(svc.list(1, limit=2)), 2)
        self.assertEqual(len(svc.list(1, limit=9999)), 3)


class EvaluateTests(Base_):

    OK = {"ok": True, "blocked": False, "no_connection": False, "detail": "", "days": 30, "calls": 2, "truncated": False, "checked_at": "x"}

    def rows(self):
        out = [sale(f"b{i}", f"2026-09-{(i % 14) + 1:02d}") for i in range(12)]
        return out + [sale(f"a{i}", f"2026-09-{(i % 12) + 15:02d}") for i in range(12)]

    def test_no_records_means_no_coupang_read(self):
        sales = FakeSales(self.OK, self.rows())
        result = self.service(sales).evaluate(1)
        self.assertEqual((result["reason"], result["read"], result["evaluation"]), ("NO_RECORDS", None, None))
        self.assertEqual(sales.calls, [])

    def test_a_complete_read_is_compared_without_judgment(self):
        svc = self.service(FakeSales(self.OK, self.rows()))
        svc.create(1, 7, "PRODUCT_NAME", "새 이름", date(2026, 9, 15), None, "111", True)
        svc.create(1, 7, "PRICE", "9900", date(2026, 9, 15), None, None, True)                   # 연결 안 됨
        result = svc.evaluate(1, 30)
        self.assertIsNone(result["reason"])
        statuses = [(r["kind"], r["status"]) for r in result["evaluation"]["results"]]
        self.assertEqual(statuses, [("PRODUCT_NAME", "OBSERVED"), ("PRICE", "NOT_EVALUATED")])
        self.assertFalse(result["evaluation"]["results"][0]["causal"])
        self.assertEqual(result["read"]["calls"], 2)
        self.assertNotIn("summary", result["read"])

    def test_failed_blocked_or_truncated_reads_are_not_compared(self):
        for status, reason in (({**self.OK, "ok": False, "blocked": True, "detail": "허용 없음"}, "READ_FAILED"),
                               ({**self.OK, "ok": False, "no_connection": True}, "READ_FAILED"),
                               ({**self.OK, "truncated": True}, "READ_TRUNCATED")):
            svc = self.service(FakeSales(status, self.rows()))
            svc.create(1, 7, "PRODUCT_NAME", "새 이름", date(2026, 9, 15), None, "111", True)
            result = svc.evaluate(1)
            self.assertEqual((result["reason"], result["evaluation"]), (reason, None), status)
            self.db.query(MarketingSuggestionRecord).delete()
            self.db.commit()

    def test_only_this_companys_records_are_evaluated(self):
        sales = FakeSales(self.OK, self.rows())
        svc = self.service(sales)
        svc.create(2, 7, "PRODUCT_NAME", "다른 회사", date(2026, 9, 15), None, "111", True)
        self.assertEqual(svc.evaluate(1)["reason"], "NO_RECORDS")
        self.assertEqual(sales.calls, [])


class NotReadyTests(unittest.TestCase):

    def test_before_the_migration_every_call_says_what_to_do_and_creates_nothing(self):
        from app.core.exceptions import ServiceUnavailableException
        from app.domains.ai_learning.suggestion_record_service import NOT_READY_MESSAGE

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        db = sessionmaker(bind=engine)()
        try:
            svc = SuggestionRecordService(db, now_factory=lambda: NOW, sales_factory=lambda d: FakeSales({}, []))
            for call in (lambda: svc.list(1), lambda: svc.create(1, 7, "PRICE", "9900"), lambda: svc.update(1, 1, adopted=True), lambda: svc.evaluate(1)):
                with self.assertRaises(ServiceUnavailableException) as ctx:
                    call()
                self.assertIn("Migration 승인", str(getattr(ctx.exception, "detail", ctx.exception)) + NOT_READY_MESSAGE)
            from sqlalchemy import inspect as sa_inspect

            self.assertFalse(sa_inspect(engine).has_table("marketing_suggestion_records"))        # 표를 몰래 만들지 않는다
        finally:
            db.close()
            engine.dispose()
            os.remove(path)


class RouteTests(Base_):

    @staticmethod
    def module():
        import app.domains.ai_learning.router  # noqa: F401

        return sys.modules["app.domains.ai_learning.router"]

    def test_routes_and_guards(self):
        module = self.module()
        expected = {("/ai-learning/suggestions", "GET"), ("/ai-learning/suggestions", "POST"), ("/ai-learning/suggestions/evaluate", "POST"), ("/ai-learning/suggestions/{record_id}", "PATCH")}
        found = set()
        for route in module.router.routes:
            if "suggestions" in route.path:
                for method in route.methods:
                    found.add((route.path, method))
                names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
                self.assertIn("SuperAdminGuard", names, route.path)                       # 이 라우터의 기존 계약: 모두 SuperAdmin
        self.assertEqual(found, expected)
        paths = [r.path for r in module.router.routes]
        self.assertLess(paths.index("/ai-learning/suggestions/evaluate"), paths.index("/ai-learning/suggestions/{record_id}"))

    def test_route_functions(self):
        module = self.module()
        user = SimpleNamespace(id=7, company_id=1)
        created = module.create_suggestion_record(data=schema.SuggestionRecordCreateRequest(kind="PRICE", suggested_value="9900", adopted=True), current_user=user, db=self.db)
        self.assertEqual(created["kind"], "PRICE")
        updated = module.update_suggestion_record(record_id=created["id"], data=schema.SuggestionRecordUpdateRequest(vendor_item_id="55"), current_user=user, db=self.db)
        self.assertEqual((updated["vendor_item_id"], updated["adopted"]), ("55", True))           # adopted를 넘기지 않으면 그대로
        unlinked = module.update_suggestion_record(record_id=created["id"], data=schema.SuggestionRecordUpdateRequest(adopted=False), current_user=user, db=self.db)
        self.assertEqual((unlinked["vendor_item_id"], unlinked["adopted"]), ("55", False))        # vendor를 넘기지 않으면 그대로
        self.assertEqual(len(module.list_suggestion_records(limit=100, current_user=user, db=self.db)), 1)

    def test_schema_limits(self):
        for bad in (dict(kind="DISCOUNT", suggested_value="x"), dict(kind="PRICE", suggested_value=""), dict(kind="PRICE", suggested_value="x" * 301),
                    dict(kind="PRICE", suggested_value="x", adopted="yes"), dict(kind="PRICE", suggested_value="x", extra=1),
                    dict(kind="PRICE", suggested_value="x", vendor_item_id="1" * 41)):
            with self.assertRaises(ValidationError, msg=str(bad)):
                schema.SuggestionRecordCreateRequest(**bad)
        with self.assertRaises(ValidationError):
            schema.SuggestionRecordUpdateRequest(adopted="yes")


if __name__ == "__main__":
    unittest.main()
