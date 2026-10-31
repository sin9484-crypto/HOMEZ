"""
=========================================================
Homez OS

File : tests/test_ai_settings_and_cs_draft_screen.py

2026-10-31 — AI 통합 계획 2단계 준비(가짜 AI 클라이언트, 바깥 호출 0):
  - AI 사용 설정을 위저드 밖(`/ai-settings`)에서 보고 저장한다. 저장은 최고 관리자 + 비밀번호 재확인(1회용), 키는 저장소에만 남는다.
  - 이번 달 사용 기록(`month_records`)은 합계와 같은 행을 최근 것부터 보여 주고, 다른 달 기록은 섞지 않는다.
  - AI 답변 초안이 남긴 "[확인 필요 …]" 자리가 채워지지 않은 채로는 상품 문의·고객센터 답변을 보낼 수 없다(서버·화면 같은 규칙).
  - 쉬운 보기: 문의는 오래된 순, 받은 지 몇 시간(한국 시각), AI 초안은 확인 창을 거쳐 답변 칸에 넣기만 하고 보내지 않는다.
=========================================================
"""

import json
import re
import shutil
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.core.exceptions import ForbiddenException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.marketplace_listing import ai_settings_router as router_module
from app.domains.marketplace_listing.ai_draft_service import CREDENTIAL_TARGET
from app.domains.marketplace_listing.ai_draft_service import USAGE_ACTION
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.marketplace_listing.ai_settings_router import AiSettingsRequest
from app.domains.order.cs_reply_draft import validate_draft
from app.domains.order.cs_reply_draft_service import CsReplyDraftService
from app.domains.purchase_task.coupang_call_center_reply_service import call_center_text_problem
from app.domains.purchase_task.coupang_inquiry_reply_service import reply_problem
from tests.test_ai_draft_service import KEY
from tests.test_ai_draft_service import FakeClient
from tests.test_url_analysis_service import DbBase

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "web" / "easy" / "easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app" / "web" / "easy" / "easy_texts.js").read_text(encoding="utf-8")
ROUTER = (ROOT / "app" / "domains" / "marketplace_listing" / "ai_settings_router.py").read_text(encoding="utf-8")
MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

DRAFT_WITH_CHECK = json.dumps({"draft": "고객님, 불편을 드려 죄송합니다. [확인 필요: 발송 상태] 확인되는 대로 안내드리겠습니다.", "needs_check": ["발송 상태"]}, ensure_ascii=False)
DRAFT_CLEAN = json.dumps({"draft": "고객님, 문의 주셔서 감사합니다. 확인되는 대로 안내드리겠습니다.", "needs_check": []}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 서버: 보내기 전 검사
# ---------------------------------------------------------------------------

class ReplyGuardTests(unittest.TestCase):

    def test_an_unfilled_check_spot_blocks_both_reply_kinds(self):
        for left in ("[확인 필요: 발송 상태] 안내드리겠습니다", "고객님 [확인 필요]", "앞 [확인 필요:환불 시점] 뒤"):
            self.assertEqual(reply_problem(left), "DRAFT_MARK_LEFT", left)
            self.assertEqual(call_center_text_problem(left), "DRAFT_MARK_LEFT", left)

    def test_ordinary_replies_still_pass(self):
        for ok in ("고객님, 확인 후 안내드리겠습니다.", "확인 필요한 내용은 없습니다", "[안내] 발송되었습니다"):
            self.assertIsNone(reply_problem(ok), ok)
            self.assertIsNone(call_center_text_problem(ok), ok)

    def test_a_validated_draft_with_a_check_spot_cannot_be_sent_as_is(self):
        draft, rejected = validate_draft(DRAFT_WITH_CHECK, "배송이 언제 되나요")
        self.assertEqual(rejected, {})
        self.assertEqual(draft["open_placeholders"], 1)
        self.assertEqual(reply_problem(draft["draft"]), "DRAFT_MARK_LEFT")
        filled = draft["draft"].replace("[확인 필요: 발송 상태]", "상품은 발송 준비 중입니다.")
        self.assertIsNone(reply_problem(filled))


# ---------------------------------------------------------------------------
# 서버: 사용 기록·설정 경로
# ---------------------------------------------------------------------------

class AiSettingsTests(DbBase):

    NOW = datetime(2026, 10, 18, 9, 0, 0)

    def setUp(self):
        super().setUp()  # 임시 DB(회사·설정·허용·감사 기록 표), 메모리 키 저장소, user_id 7
        reset_recent_auth_state_for_tests()
        self.addCleanup(reset_recent_auth_state_for_tests)
        self.user = SimpleNamespace(id=self.user_id, company_id=self.company_id)

    def svc(self):
        return AiDraftService(self.db, self.store, now_factory=lambda: self.NOW)

    def configure(self, limit=1000, key=KEY):
        return self.svc().save_settings(
            self.user_id, self.company_id, model="claude-test", monthly_limit_krw=limit, in_price=4000.0, out_price=20000.0, api_key=key,
        )

    def cs(self, client):
        ai = AiDraftService(self.db, self.store, client_factory=lambda k, m: client, now_factory=lambda: self.NOW)
        return CsReplyDraftService(self.db, ai)

    def grant_cs(self, count=3):
        ExternalCallPolicyService(self.db).grant(
            self.company_id, system=ExternalSystem.LLM, kind=CallKind.WRITE, purpose=CallPurpose.AI_DRAFT,
            mode=AllowanceMode.COUNTED, count=count, user_id=self.user_id,
        )

    def test_month_records_list_this_months_calls_newest_first(self):
        self.configure()
        self.grant_cs()
        # 다른 달 기록은 섞이지 않는다
        AiDraftService(self.db, self.store, now_factory=lambda: datetime(2026, 9, 30, 9, 0)).record_usage(
            self.company_id, self.user_id, "CS", "claude-test", {"input_tokens": 1, "output_tokens": 1, "cost_krw": 99.0, "ok": True, "purpose": "CS_DRAFT"})
        self.cs(FakeClient(text=DRAFT_CLEAN, input_tokens=300, output_tokens=120)).draft(self.company_id, self.user_id, "DELIVERY", "언제 와요?")
        self.cs(FakeClient(text="JSON 아님", input_tokens=200, output_tokens=50)).draft(self.company_id, self.user_id, "OTHER", "문의합니다")
        records = self.svc().month_records()
        self.assertEqual([r["ok"] for r in records], [False, True])
        self.assertEqual([r["purpose"] for r in records], ["CS_DRAFT", "CS_DRAFT"])
        self.assertEqual((records[1]["input_tokens"], records[1]["output_tokens"], records[1]["ref"]), (300, 120, "CS"))
        self.assertEqual(records[1]["cost_krw"], round((300 * 4000.0 + 120 * 20000.0) / 1_000_000, 2))
        status = self.svc().status(self.user_id, self.company_id)
        self.assertEqual(status["month_calls"], len(records))
        self.assertAlmostEqual(status["month_used_krw"], sum(r["cost_krw"] for r in records), places=2)

    def test_an_unreadable_record_is_shown_as_unknown_not_dropped(self):
        tag = f"{self.NOW.year:04d}{self.NOW.month:02d}"
        self.db.execute(text("INSERT INTO audit_logs (company_id, user_id, action, entity, entity_id, description) VALUES (:c, :u, :a, 'ai_draft', :e, 'x')"),
                        {"c": self.company_id, "u": self.user_id, "a": USAGE_ACTION, "e": f"{tag}:CS"})
        self.db.commit()
        records = self.svc().month_records()
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["ok"])
        self.assertEqual(records[0]["ref"], "CS")

    def test_read_shows_status_and_records_without_the_key(self):
        self.configure()
        AiDraftService(self.db, self.store).record_usage(
            self.company_id, self.user_id, "CS", "claude-test", {"input_tokens": 10, "output_tokens": 5, "cost_krw": 0.14, "ok": True, "purpose": "CS_DRAFT"})
        body = router_module.read_ai_settings(current_user=self.user, db=self.db, credential_store=self.store)
        self.assertTrue(body["configured"] and body["key_saved"])
        self.assertEqual(body["records"][0]["purpose"], "CS_DRAFT")
        self.assertNotIn(KEY, json.dumps(body, ensure_ascii=False, default=str))

    def test_saving_needs_a_one_time_password_check_and_keeps_the_key_out_of_the_db(self):
        data = AiSettingsRequest(model="claude-opus-5-5", monthly_limit_krw=5000, in_price=5800.0, out_price=29000.0, api_key=KEY)
        with self.assertRaises(ForbiddenException):
            router_module.save_ai_settings(data, current_user=self.user, recent_auth_token=None, db=self.db, credential_store=self.store)
        self.assertFalse(self.store.exists(CREDENTIAL_TARGET))
        self.assertFalse(self.svc().status(self.user_id, self.company_id)["configured"])

        token, _ = issue_recent_auth_token(self.user_id)
        result = router_module.save_ai_settings(data, current_user=self.user, recent_auth_token=token, db=self.db, credential_store=self.store)
        self.assertTrue(result["configured"])
        self.assertEqual((result["model"], result["monthly_limit_krw"]), ("claude-opus-5-5", 5000))
        self.assertEqual(self.store.read(CREDENTIAL_TARGET), {"api_key": KEY})
        audit = self.db.execute(text("SELECT description FROM audit_logs WHERE action = 'AI_DRAFT_SETTINGS_SAVED'")).fetchall()
        self.assertEqual(len(audit), 1)
        dump = " ".join(str(v) for row in self.db.execute(text("SELECT * FROM audit_logs")).fetchall() for v in row)
        dump += " ".join(str(v) for row in self.db.execute(text("SELECT * FROM user_settings")).fetchall() for v in row)
        self.assertNotIn(KEY, dump)
        # 같은 확인은 두 번 쓸 수 없다
        with self.assertRaises(ForbiddenException):
            router_module.save_ai_settings(data, current_user=self.user, recent_auth_token=token, db=self.db, credential_store=self.store)

    def test_an_empty_key_keeps_the_saved_key(self):
        self.configure()
        token, _ = issue_recent_auth_token(self.user_id)
        data = AiSettingsRequest(model="claude-haiku-5-5", monthly_limit_krw=2000, in_price=150.0, out_price=750.0)
        router_module.save_ai_settings(data, current_user=self.user, recent_auth_token=token, db=self.db, credential_store=self.store)
        self.assertEqual(self.store.read(CREDENTIAL_TARGET), {"api_key": KEY})
        self.assertEqual(self.svc().status(self.user_id, self.company_id)["model"], "claude-haiku-5-5")


class RouterWiringTests(unittest.TestCase):

    def block(self, name):
        start = ROUTER.index(f"def {name}(")
        nxt = ROUTER.find("\n@router.", start)
        return ROUTER[start:nxt if nxt > 0 else len(ROUTER)]

    def test_guards(self):
        self.assertIn("Depends(AdminGuard)", self.block("read_ai_settings"))
        save = self.block("save_ai_settings")
        self.assertIn("Depends(SuperAdminGuard)", save)
        self.assertLess(save.index("consume_recent_auth_token"), save.index(".save_settings("))
        self.assertNotIn("data.api_key}", save)
        self.assertIn('APIRouter(prefix="/ai-settings"', ROUTER)

    def test_the_router_is_mounted(self):
        self.assertIn("from app.domains.marketplace_listing.ai_settings_router import router as ai_settings_router", MAIN)
        self.assertRegex(MAIN, r"app\.include_router\(\s*ai_settings_router,\s*\)")

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(Exception):
            AiSettingsRequest(model="m-1", monthly_limit_krw=1, in_price=1.0, out_price=1.0, extra="x")


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def _fn(name, end_marker):
    start = JS.index(name)
    return JS[start:JS.index(end_marker, start)]


class ScreenTests(unittest.TestCase):

    def test_new_texts_exist_in_both_languages(self):
        keys = set(re.findall(r'T\(\s*"(easy\.(?:cs|ai)\.[a-zA-Z_]+)"', JS))
        keys |= {f"easy.cs.kind_{k}" for k in ("DELIVERY", "RETURN_EXCHANGE", "PRODUCT", "OTHER")}
        keys |= set(re.findall(r'"(easy\.ai\.p_[a-z]+)"', JS))
        keys |= set(re.findall(r'"(easy\.ai\.f_[a-z_]+)"', JS))
        self.assertGreaterEqual(len(keys), 40)
        ko, en = TEXTS.index('"easy.more.title": "더보기 · 설정"'), TEXTS.index('"easy.more.title": "More · Settings"')
        ko_part, en_part = (TEXTS[:en], TEXTS[en:]) if ko < en else (TEXTS[ko:], TEXTS[:ko])
        for key in sorted(keys):
            self.assertIn(f'"{key}":', ko_part, key)
            self.assertIn(f'"{key}":', en_part, key)

    def test_the_draft_only_fills_the_box_and_never_sends(self):
        ask = _fn("async function csDraftAsk", "\n  }\n")
        self.assertIn('api("/orders/cs-reply-draft"', ask)
        self.assertNotIn("/reply/send", ask)
        self.assertLess(ask.index("await ezDialog("), ask.index('api("/orders/cs-reply-draft"'))
        self.assertIn("textEl.value =", ask)
        # 보내기 버튼 쪽은 여전히 확인 칸·확인 창을 거친다
        send = _fn("async function inquirySend", "\n  }\n")
        self.assertLess(send.index("B.askConfirm("), send.index("/reply/send"))

    def test_saving_settings_asks_for_the_password_and_the_key_is_a_password_field(self):
        edit = _fn("async function aiSettingsEdit", "\n  }\n")
        self.assertLess(edit.index("B.promptRecentAuthToken()"), edit.index('api("/ai-settings", { method: "PUT"'))
        self.assertIn('type="password"', edit)
        self.assertIn('id="ez-ai-box"', JS)
        self.assertNotIn("api_key", _fn("function aiSettingsHtml", "\n  }\n"))

    def test_server_text_is_escaped(self):
        html = _fn("function csDraftResultHtml", "\n  }\n") + _fn("function aiSettingsHtml", "\n  }\n")
        for needle in ("esc(w)", "esc(t)", "esc(r.model", "<b>${esc(v)}</b>", "esc(r.ref"):
            self.assertIn(needle, html)

    @unittest.skipUnless(shutil.which("node"), "node 없음")
    def test_screen_rules_in_node(self):
        code = "\n".join([
            "const INQ_MAX = 1000;",
            _fn("function inquiryReplyProblem", "\n  }\n") + "\n  }",
            _fn("const INQ_OLD_HOURS", ";") + ";",
            _fn("function inquiryAgeHours", "\n  }\n") + "\n  }",
            _fn("function inquiryOldestFirst", "\n  }\n") + "\n  }",
            _fn("const CS_MAX_FACTS", ";") + ";",
            _fn("function csParseFacts", "\n  }\n") + "\n  }",
            _fn("function aiSettingsForm", "\n  }\n") + "\n  }",
        ])
        script = code + """
const now = Date.UTC(2026, 9, 31, 3, 0);  // 한국 시각 2026-10-31 12:00
const get = (o) => (n) => (n in o ? o[n] : "");
console.log(JSON.stringify({
  age: [inquiryAgeHours("2026-10-31T09:30:00", now), inquiryAgeHours("2026-10-30T12:00:00", now), inquiryAgeHours("2026-10-31T13:00:00", now), inquiryAgeHours("", now)],
  order: inquiryOldestFirst([{inquiry_id: 1, inquiry_at: "2026-10-31T09:00:00"}, {inquiry_id: 2, inquiry_at: null}, {inquiry_id: 3, inquiry_at: "2026-10-29T23:00:00"}]).map((r) => r.inquiry_id),
  problems: [inquiryReplyProblem("[확인 필요: 발송일] 안내"), inquiryReplyProblem("확인 후 안내드립니다")],
  facts: [csParseFacts("발송 상태: 어제 발송\\n\\n송장: 있음"), csParseFacts("발송 상태 어제"), csParseFacts([1,2,3,4,5,6,7,8,9].map((i) => "k" + i + ": v").join("\\n")).error,
          csParseFacts(": 어제 발송"), csParseFacts("발송 상태:   "), csParseFacts("송장: 있음\\n발송 상태:")],
  form: [aiSettingsForm(get({model: "claude-opus-5-5", limit: "5,000", in_price: "5800", out_price: "29000", api_key: " "})),
         aiSettingsForm(get({model: "claude-opus-5-5", limit: "5000", in_price: "5800", out_price: "29000", api_key: "sk-x"})).body.api_key,
         aiSettingsForm(get({model: "bad model", limit: "5000", in_price: "1", out_price: "1"})).error,
         aiSettingsForm(get({model: "m-1", limit: "0", in_price: "1", out_price: "1"})).error,
         aiSettingsForm(get({model: "m-1", limit: "1.5", in_price: "1", out_price: "1"})).error,
         aiSettingsForm(get({model: "m-1", limit: "10", in_price: "0", out_price: "1"})).error],
}));
"""
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["age"], [2, 24, 0, None])
        self.assertEqual(got["order"], [3, 1, 2])
        self.assertEqual(got["problems"], ["easy.inq.err_draft", None])
        self.assertEqual(got["facts"][0], {"facts": {"발송 상태": "어제 발송", "송장": "있음"}, "error": None, "line": None})
        self.assertEqual(got["facts"][1]["error"], "easy.cs.err_fact")
        self.assertEqual(got["facts"][2], "easy.cs.err_fact_many")
        self.assertEqual([got["facts"][i]["error"] for i in (3, 4, 5)], ["easy.cs.err_fact"] * 3)  # 이름 없음·값 없음은 고쳐 쓰지 않고 막는다
        self.assertEqual(got["facts"][5]["line"], 2)
        self.assertEqual(got["form"][0], {"body": {"model": "claude-opus-5-5", "monthly_limit_krw": 5000, "in_price": 5800, "out_price": 29000}})
        self.assertEqual(got["form"][1], "sk-x")
        self.assertEqual(got["form"][2:], ["easy.ai.err_model", "easy.ai.err_limit", "easy.ai.err_limit", "easy.ai.err_price"])


if __name__ == "__main__":
    unittest.main()
