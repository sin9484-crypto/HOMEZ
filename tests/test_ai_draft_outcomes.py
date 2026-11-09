"""
=========================================================
Homez OS

File : tests/test_ai_draft_outcomes.py

2026-11-09 — AI 초안 평가 고리(사용자 승인 "1번 진행"): Migration `20261109_00`(Model DDL과 같은지·임시 DB 적용·재적용 거절·rollback 주석),
차이 계산(순수 함수), 기록 서비스(가림·표 없음·실패 시 본 기능 유지·보낸 글 한 번만·회사/기능 범위), 등록 도우미·고객 응대 초안 연결,
답변 보내기 뒤 기록(성공일 때만), 'AI 초안 기록' 화면 함수를 고정한다. 임시 SQLite·가짜 AI — 네트워크·실제 DB 0.
=========================================================
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.core.exceptions import ServiceUnavailableException
from app.database.base import Base
from app.domains.ai_learning import draft_outcome_diff as diff
from app.domains.ai_learning.draft_outcome_service import FEATURE_CS
from app.domains.ai_learning.draft_outcome_service import FEATURE_LISTING
from app.domains.ai_learning.draft_outcome_service import DraftOutcomeService
from app.domains.ai_learning.draft_outcome_service import prompt_fingerprint
from app.domains.ai_learning.model import AiDraftOutcomeRecord
from app.domains.marketplace_listing.listing_wizard_schema import WizardDraftUpdateRequest
from app.domains.marketplace_listing.llm_draft_provider import SYSTEM_PROMPT
from app.domains.order import cs_reply_draft as cs_rules
from app.domains.purchase_task.coupang_call_center_reply_schema import CoupangCallCenterReplyRequest
from app.domains.purchase_task.coupang_inquiry_reply_schema import CoupangInquiryReplyRequest
from tests import test_ai_draft_service as listing_tests
from tests import test_cs_reply_draft as cs_tests

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261109_00_create_ai_draft_outcome_record_schema.sql"
NOW = datetime(2026, 11, 9, 13, 0, 0)


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace(",\n", ", ")).replace("( ", "(").replace(" )", ")").strip()


class MigrationTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_the_sql_is_exactly_the_models_canonical_ddl(self):
        dialect = sqlite_dialect.dialect()
        table = AiDraftOutcomeRecord.__table__
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
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(ai_draft_outcome_records)")], [c.name for c in AiDraftOutcomeRecord.__table__.columns])
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        finally:
            conn.close()

    def test_the_rollback_note_is_only_a_comment_and_removes_everything(self):
        text_ = MIGRATION.read_text(encoding="utf-8")
        tail = text_.split("COMMIT;")[1]
        for line in tail.splitlines():
            if line.strip():
                self.assertTrue(line.startswith("--"), line)
        rollback = "\n".join(line[3:] for line in tail.splitlines() if line.startswith("-- DROP"))
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(text_)
            conn.executescript(rollback)
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE '%ai_draft_outcome_records%'").fetchone()[0], 0)
        finally:
            conn.close()


class DiffTests(unittest.TestCase):

    def test_same_text_is_zero_and_whitespace_does_not_count(self):
        out = diff.field_diff("안녕하세요  고객님", " 안녕하세요 고객님 ")
        self.assertEqual((out["same"], out["changed_percent"], out["added_chars"], out["removed_chars"]), (True, 0, 0, 0))

    def test_a_small_change_is_never_shown_as_zero(self):
        long = "가" * 400
        out = diff.field_diff(long, long + "나")
        self.assertFalse(out["same"])
        self.assertGreaterEqual(out["changed_percent"], 1)
        self.assertEqual((out["added_chars"], out["removed_chars"]), (1, 0))

    def test_sentences_removed_and_added_are_listed(self):
        out = diff.field_diff("배송 중입니다. 내일 도착합니다.", "배송 중입니다. 확인 후 안내드리겠습니다.")
        self.assertEqual(out["removed_sentences"], ["내일 도착합니다."])
        self.assertEqual(out["added_sentences"], ["확인 후 안내드리겠습니다."])
        self.assertEqual(diff.field_diff("", "새 글")["changed_percent"], 100)

    def test_lists_are_compared_as_joined_text(self):
        self.assertTrue(diff.field_diff(["a", "b"], "a, b")["same"])
        self.assertFalse(diff.field_diff(["a", "b"], ["a", "c"])["same"])

    def test_compare_weighs_by_length_and_needs_a_final(self):
        self.assertEqual(diff.compare({"reply": "x"}, None), {"compared": False, "fields": {}, "changed_percent": None})
        out = diff.compare({"product_name": "짧은 이름", "description": "가" * 300}, {"product_name": "완전히 다른", "description": "가" * 300})
        self.assertTrue(out["compared"])
        self.assertGreaterEqual(out["changed_percent"], 1)
        self.assertLess(out["changed_percent"], out["fields"]["product_name"]["changed_percent"])
        self.assertEqual(diff.compare({"reply": "같음"}, {"reply": "같음"})["changed_percent"], 0)

    def test_mask_hides_personal_info_like_the_cs_draft_does(self):
        masked = diff.mask("받는 분: 김영준 010-1234-5678 a@b.co")
        for leaked in ("김영준", "010-1234-5678", "a@b.co"):
            self.assertNotIn(leaked, masked)
        self.assertEqual(diff.mask(None), "")
        self.assertEqual(len(diff.mask("가" * 9000)), diff.MAX_FIELD_CHARS)


class StoreBase(unittest.TestCase):

    create_table = True

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        if self.create_table:
            Base.metadata.create_all(self.engine, tables=[AiDraftOutcomeRecord.__table__])
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def svc(self):
        return DraftOutcomeService(self.db, now_factory=lambda: NOW)

    def rows(self):
        return self.db.query(AiDraftOutcomeRecord).order_by(AiDraftOutcomeRecord.id).all()


class WithoutTableTests(StoreBase):

    create_table = False

    def test_recording_is_skipped_quietly_and_no_table_is_created(self):
        self.assertIsNone(self.svc().record_draft(1, 7, FEATURE_CS, "CS", "m", "sys", {"reply": "답변"}))
        self.assertFalse(self.svc().record_sent_reply(1, 1, "답변", "INQ1"))
        self.assertFalse(inspect(self.engine).has_table("ai_draft_outcome_records"))

    def test_the_list_says_it_is_not_ready(self):
        with self.assertRaises(ServiceUnavailableException) as ctx:
            self.svc().list(1)
        self.assertIn("Migration 승인", str(ctx.exception.detail if hasattr(ctx.exception, "detail") else ctx.exception))


class RecordTests(StoreBase):

    def test_a_cs_draft_is_stored_masked_with_a_prompt_fingerprint(self):
        rid = self.svc().record_draft(1, 7, FEATURE_CS, "CS", "claude-test", "시스템 프롬프트", {"reply": "김영준 고객님 010-1234-5678로 연락드리겠습니다."})
        row = self.rows()[0]
        self.assertEqual(rid, row.id)
        self.assertEqual((row.company_id, row.feature, row.ref, row.model, row.created_by, row.final_json), (1, FEATURE_CS, "CS", "claude-test", 7, None))
        self.assertEqual(row.prompt_version, prompt_fingerprint("시스템 프롬프트"))
        self.assertNotEqual(prompt_fingerprint("시스템 프롬프트"), prompt_fingerprint("시스템 프롬프트 v2"))
        self.assertNotIn("010-1234-5678", row.draft_json)
        self.assertEqual(set(json.loads(row.draft_json)), {"reply"})

    def test_only_known_features_and_non_empty_drafts_are_stored(self):
        svc = self.svc()
        self.assertIsNone(svc.record_draft(1, 7, "OTHER", "x", None, "s", {"reply": "답"}))
        self.assertIsNone(svc.record_draft(1, 7, FEATURE_CS, "CS", None, "s", {"reply": "   "}))
        self.assertIsNone(svc.record_draft(1, 7, FEATURE_CS, "CS", None, "s", "not a dict"))
        # 등록 도우미는 상품명·검색어·상세 설명만 담고, 다른 키는 버린다.
        rid = svc.record_draft(1, 7, FEATURE_LISTING, "WZ5", None, "s", {"product_name": "이름", "keywords": ["가", " ", "나"], "price": 9900})
        self.assertEqual(json.loads(self.rows()[0].draft_json), {"product_name": "이름", "keywords": ["가", "나"]})
        self.assertEqual(len(self.rows()), 1)
        self.assertIsNotNone(rid)

    def test_a_storage_failure_returns_none_and_never_raises(self):
        with mock.patch.object(self.db, "commit", side_effect=RuntimeError("disk full")):
            self.assertIsNone(self.svc().record_draft(1, 7, FEATURE_CS, "CS", None, "s", {"reply": "답변"}))
        self.assertEqual(self.rows(), [])

    def test_the_sent_reply_is_kept_once_for_the_right_company_and_feature(self):
        svc = self.svc()
        rid = svc.record_draft(1, 7, FEATURE_CS, "CS", None, "s", {"reply": "초안 답변입니다."})
        listing_id = svc.record_draft(1, 7, FEATURE_LISTING, "WZ1", None, "s", {"product_name": "이름"})
        self.assertFalse(svc.record_sent_reply(2, rid, "다른 회사", "INQ9"))          # 다른 회사
        self.assertFalse(svc.record_sent_reply(1, listing_id, "등록 기록", "INQ9"))    # 등록 도우미 기록에는 답변을 붙이지 않는다
        for bad_id in (True, 0, -1, "1", None):
            self.assertFalse(svc.record_sent_reply(1, bad_id, "글", "INQ9"), bad_id)
        for bad_ref in ("", "X1", "INQ", "INQ1a", "CC 1"):
            self.assertFalse(svc.record_sent_reply(1, rid, "글", bad_ref), bad_ref)
        self.assertFalse(svc.record_sent_reply(1, rid, "  ", "INQ9"))
        self.assertTrue(svc.record_sent_reply(1, rid, "고친 답변입니다. 010-9999-8888", "INQ9"))
        self.assertFalse(svc.record_sent_reply(1, rid, "두 번째", "INQ9"))           # 한 번만
        row = self.db.get(AiDraftOutcomeRecord, rid)
        self.db.refresh(row)
        self.assertEqual((row.final_source, row.final_ref, row.finalized_at), ("SENT", "INQ9", NOW))
        self.assertIn("고친 답변입니다.", row.final_json)
        self.assertNotIn("010-9999-8888", row.final_json)


class ListTests(StoreBase):

    def test_cs_records_compare_with_the_sent_reply_and_list_newest_first(self):
        svc = self.svc()
        first = svc.record_draft(1, 7, FEATURE_CS, "CS", "m", "s", {"reply": "배송 중입니다. 내일 도착합니다."})
        svc.record_draft(1, 7, FEATURE_CS, "CS", "m", "s", {"reply": "두 번째 초안"})
        svc.record_draft(2, 7, FEATURE_CS, "CS", "m", "s", {"reply": "다른 회사"})
        svc.record_sent_reply(1, first, "배송 중입니다. 확인 후 안내드리겠습니다.", "CC77")
        out = svc.list(1)
        self.assertEqual([r["id"] for r in out["records"]], [first + 1, first])
        newest, oldest = out["records"]
        self.assertEqual((newest["final_state"], newest["comparison"]["compared"]), ("NOT_SENT", False))
        self.assertEqual((oldest["final_state"], oldest["final_ref"]), ("SENT", "CC77"))
        self.assertEqual(oldest["comparison"]["fields"]["reply"]["removed_sentences"], ["내일 도착합니다."])
        self.assertEqual(out["summary"][FEATURE_CS], {"total": 2, "sent": 1})
        self.assertEqual(out["summary"][FEATURE_LISTING], {"total": 0, "sent": None})
        self.assertEqual(out["review_target"], 30)
        self.assertEqual(svc.list(1, FEATURE_LISTING)["records"], [])

    def test_bad_feature_and_limit_are_handled(self):
        from app.core.exceptions import BadRequestException

        with self.assertRaises(BadRequestException):
            self.svc().list(1, "NOPE")
        for i in range(3):
            self.svc().record_draft(1, 7, FEATURE_CS, "CS", None, "s", {"reply": f"초안 {i}"})
        self.assertEqual(len(self.svc().list(1, limit=2)["records"]), 2)
        self.assertEqual(len(self.svc().list(1, limit=10**6)["records"]), 3)

    def test_reading_writes_nothing(self):
        self.svc().record_draft(1, 7, FEATURE_CS, "CS", None, "s", {"reply": "초안"})
        before = self.db.execute(text("SELECT * FROM ai_draft_outcome_records")).fetchall()
        self.svc().list(1)
        self.assertEqual(self.db.execute(text("SELECT * FROM ai_draft_outcome_records")).fetchall(), before)


class ListingDraftLinkTests(listing_tests.AiDraftServiceTests):
    """등록 도우미 초안 → 기록 → 위저드의 지금 값과 비교(기존 시험 고정 장치를 그대로 쓴다)."""

    def setUp(self):
        super().setUp()
        AiDraftOutcomeRecord.__table__.create(bind=self.engine, checkfirst=True)

    def test_a_validated_proposal_is_recorded_and_compared_with_the_current_wizard(self):
        self.configure()
        self.grant()
        out = self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        rid = out["outcome_record_id"]
        self.assertIsInstance(rid, int)
        row = self.db.get(AiDraftOutcomeRecord, rid)
        self.assertEqual((row.feature, row.ref, row.model, row.prompt_version), (FEATURE_LISTING, f"WZ{self.wizard.id}", "claude-test", prompt_fingerprint(SYSTEM_PROMPT)))
        self.assertEqual(json.loads(row.draft_json), out["fields"])
        # 사람이 위저드에서 상품명만 고쳐 저장했다.
        current = self.service.get(self.wizard.id, self.company_id)
        draft = json.loads(current.draft_json)
        self.service.update_draft(self.wizard.id, self.company_id, WizardDraftUpdateRequest(
            expected_version=current.version, product_name="사람이 고친 상품명", brand=None, category=draft.get("category"),
            description=out["fields"]["description"], options=draft["options"], sku_list=draft["sku_list"], keywords=out["fields"]["keywords"],
        ))
        listed = DraftOutcomeService(self.db).list(self.company_id, FEATURE_LISTING)["records"][0]
        self.assertEqual(listed["final_state"], "CURRENT_WIZARD")
        fields = listed["comparison"]["fields"]
        self.assertFalse(fields["product_name"]["same"])
        self.assertTrue(fields["description"]["same"])
        self.assertTrue(fields["keywords"]["same"])
        # 다른 회사에서는 이 위저드를 읽지 않는다.
        self.assertIsNone(DraftOutcomeService(self.db)._current_listing(self.company_id + 1, f"WZ{self.wizard.id}", {}))

    def test_a_rejected_proposal_is_not_recorded(self):
        self.configure()
        self.grant()
        bad = listing_tests.FakeClient(text="not json")
        out = self.svc(bad).propose(self.wizard.id, self.company_id, self.user_id)
        self.assertIsNone(out["outcome_record_id"])
        self.assertEqual(self.db.query(AiDraftOutcomeRecord).count(), 0)

    def test_without_the_table_the_draft_still_works(self):
        AiDraftOutcomeRecord.__table__.drop(bind=self.engine)
        self.configure()
        self.grant()
        out = self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertIsNone(out["outcome_record_id"])
        self.assertTrue(out["fields"])


class CsDraftLinkTests(cs_tests.ServiceBase):

    def setUp(self):
        super().setUp()
        AiDraftOutcomeRecord.__table__.create(bind=self.engine, checkfirst=True)

    def test_the_masked_reply_draft_is_recorded_but_never_the_inquiry(self):
        self.grant()
        out = self.draft(facts={"배송 상태": "배송 중"})
        row = self.db.get(AiDraftOutcomeRecord, out["outcome_record_id"])
        self.assertEqual((row.feature, row.ref, row.prompt_version), (FEATURE_CS, "CS", prompt_fingerprint(cs_rules.SYSTEM_PROMPT)))
        self.assertEqual(json.loads(row.draft_json), {"reply": cs_tests.GOOD_DRAFT["draft"]})
        dump = " ".join(str(v) for r in self.db.execute(text("SELECT * FROM ai_draft_outcome_records")).fetchall() for v in r)
        for fragment in ("김영준", "010-1234-5678", "아직 안 왔어요", "오목로", "a@b.co"):
            self.assertNotIn(fragment, dump, fragment)

    def test_a_rejected_draft_is_not_recorded(self):
        self.grant()
        self.client.payload = {"draft": "<b>죄송합니다</b>", "needs_check": []}
        out = self.draft()
        self.assertIsNone(out["draft"])
        self.assertIsNone(out["outcome_record_id"])
        self.assertEqual(self.db.query(AiDraftOutcomeRecord).count(), 0)


class SendLinkTests(StoreBase):
    """답변 보내기 뒤 기록 — 등록 성공(SUCCEEDED)일 때만, 결과는 바꾸지 않는다."""

    def setUp(self):
        super().setUp()
        from app.domains.purchase_task.router import _with_ai_draft_sent

        self.helper = _with_ai_draft_sent
        self.rid = self.svc().record_draft(1, 7, FEATURE_CS, "CS", None, "s", {"reply": "초안"})

    def test_success_is_recorded_and_flagged(self):
        out = self.helper(self.db, 1, self.rid, "보낸 답변\r\n", "INQ5", {"outcome": "SUCCEEDED", "executed": True})
        self.assertEqual(out, {"outcome": "SUCCEEDED", "executed": True, "ai_draft_record_saved": True})
        row = self.db.get(AiDraftOutcomeRecord, self.rid)
        self.db.refresh(row)
        self.assertEqual(json.loads(row.final_json), {"reply": "보낸 답변"})

    def test_other_outcomes_record_nothing(self):
        for outcome in ("UNKNOWN", "ACTION_REQUIRED", "ALREADY_ANSWERED", "FAILED"):
            out = self.helper(self.db, 1, self.rid, "보낸 답변", "INQ5", {"outcome": outcome})
            self.assertEqual(out["ai_draft_record_saved"], False, outcome)
        row = self.db.get(AiDraftOutcomeRecord, self.rid)
        self.db.refresh(row)
        self.assertIsNone(row.final_json)

    def test_without_a_record_number_the_response_is_untouched(self):
        result = {"outcome": "SUCCEEDED"}
        self.assertIs(self.helper(self.db, 1, None, "글", "INQ5", result), result)

    def test_the_request_schemas_accept_only_a_positive_integer(self):
        for model, base in ((CoupangInquiryReplyRequest, {"content": "답변입니다"}), (CoupangCallCenterReplyRequest, {"content": "답변입니다"})):
            self.assertIsNone(model(**base).ai_draft_record_id)
            self.assertEqual(model(**base, ai_draft_record_id=3).ai_draft_record_id, 3)
            self.assertIsNone(model(**base, ai_draft_record_id=None).ai_draft_record_id)
            for bad in (0, -1, "3", 1.5, True):
                with self.assertRaises(ValidationError, msg=str(bad)):
                    model(**base, ai_draft_record_id=bad)


class WiringSourceTests(unittest.TestCase):

    EASY = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
    ROUTER = (ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")

    def test_both_send_buttons_pass_the_draft_record_number(self):
        self.assertEqual(self.EASY.count("ai_draft_record_id: aiDraftRecordId(textEl)"), 2)
        self.assertIn("textEl.dataset.aiDraftRecord = String(res.outcome_record_id)", self.EASY)
        self.assertIn("delete textEl.dataset.aiDraftRecord", self.EASY)

    def test_both_send_routes_record_after_the_send(self):
        for ref in ('f"INQ{inquiry_id}"', 'f"CC{inquiry_id}"'):
            self.assertIn(ref, self.ROUTER)
        self.assertEqual(self.ROUTER.count("return _with_ai_draft_sent("), 2)


NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def run_js(self, expression):
        from tests.test_ai_tools_ui import HARNESS
        from tests.test_ai_tools_ui import KO
        from tests.test_ai_tools_ui import table
        from tests.test_reference_generation_ui import function_source

        sources = "\n".join(function_source(n) for n in ("atKstMinute", "atDraftOutcomeHtml"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_times_are_shown_in_korean_time(self):
        self.assertEqual(self.run_js('atKstMinute("2026-11-09T15:30:00")'), "2026-11-10 00:30")
        self.assertEqual(self.run_js('atKstMinute("2026-11-09T15:30:00Z")'), "2026-11-10 00:30")
        for bad in ('null', '""', '"어제"'):
            self.assertEqual(self.run_js(f"atKstMinute({bad})"), "—")

    def test_the_table_escapes_text_and_does_not_judge(self):
        result = {
            "review_target": 30,
            "summary": {"LISTING_DRAFT": {"total": 1, "sent": None}, "CS_REPLY": {"total": 2, "sent": 1}},
            "records": [
                {"id": 2, "feature": "CS_REPLY", "ref": "CS", "final_ref": "INQ5", "final_state": "SENT", "created_at": "2026-11-09T01:00:00",
                 "model": "m", "prompt_version": "sp-abc", "draft": {"reply": "<b>초안</b>"}, "final": {"reply": "보낸 답변"},
                 "comparison": {"compared": True, "changed_percent": 40, "fields": {}}},
                {"id": 1, "feature": "CS_REPLY", "ref": "CS", "final_ref": None, "final_state": "NOT_SENT", "created_at": "2026-11-09T00:00:00",
                 "model": None, "prompt_version": "sp-abc", "draft": {"reply": "아직"}, "final": None, "comparison": {"compared": False, "changed_percent": None, "fields": {}}},
                {"id": 0, "feature": "<x>", "ref": "WZ1", "final_state": "???", "created_at": None, "draft": {"weird": "값"}, "final": None, "comparison": {"compared": False}},
            ],
        }
        html = self.run_js(f"atDraftOutcomeHtml({json.dumps(result, ensure_ascii=False)})")
        self.assertNotIn("<b>초안</b>", html)
        self.assertIn("&lt;b&gt;초안&lt;/b&gt;", html)
        self.assertIn("등록 도우미 1건 · 고객 응대 2건(보낸 답변 1건) · 함께 읽기 시작 기준 30건", html)
        self.assertIn("아래 3건 중 비교한 1건의 평균 바뀐 정도 40%", html)
        for word in ("보낸 답변과 비교", "아직 보내지 않음", "알 수 없는 기능", "알 수 없음", "2026-11-09 10:00", "판정하지 않습니다"):
            self.assertIn(word, html, word)
        # '잘했다'는 "잘한 것은 아닙니다"·"잘했다·못했다를 판정하지 않습니다"처럼 부정하는 문장에만 나온다.
        self.assertIn("낮다고 AI가 잘한 것은 아닙니다", html)
        self.assertEqual(html.count("잘했"), 1)
        for word in ("성공률", "정확도", "적중"):
            self.assertNotIn(word, html)
        self.assertIn("아직 기록이 없습니다", self.run_js('atDraftOutcomeHtml({"records": [], "summary": {}, "review_target": 30})'))


if __name__ == "__main__":
    unittest.main()


def load_tests(loader, tests, pattern):
    """다른 시험 파일의 고정 장치를 물려받은 클래스는 이 파일에서 새로 정의한 시험만 돌린다(원래 시험을 두 번 돌리지 않는다)."""

    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase)) or obj.__module__ != __name__:
            continue
        own = [n for n, v in vars(obj).items() if n.startswith("test") and callable(v)]
        for n in sorted(own):
            suite.addTest(obj(n))
    return suite
