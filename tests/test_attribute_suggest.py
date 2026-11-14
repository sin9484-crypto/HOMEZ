"""
=========================================================
Homez OS

File : tests/test_attribute_suggest.py

2026-11-14 — 상세 글에서 쿠팡 고시 속성 뽑기(조사 문서 3-6). 임시 SQLite·가짜 AI 클라이언트 — 바깥 호출 0.

  - 규칙: 라벨 붙은 값만 뽑는다(콜론 뒤는 쉼표를 품고, 콜론 없으면 쉼표에서 끊는다). 라벨 없는 용량·구성 수량은 상품명·옵션에서만,
    '매'(장 수)·'1개월'은 수량이 아니다. 연락처류 고시 칸은 제안하지 않는다. 모든 제안에 원문 근거가 붙는다.
  - 대조: 같음(공백·대소문자 무시)·다름·비어 있음·값이 여럿. 구성 수량이 판매 단위 수량과 다르면 '확인 필요'.
  - AI 응답은 근거가 원문에 그대로 있고 값이 근거 안에 있을 때만 받는다(지어낸 값 차단).
  - 서비스는 위저드를 바꾸지 않는다. AI는 고를 때만, 허용(LLM/AI_ANALYSIS)·월 한도·사용 기록을 거친다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.marketplace_listing import attribute_extract as ax
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.marketplace_listing.attribute_suggest_service import AttributeSuggestService
from app.domains.marketplace_listing.listing_wizard_schema import WizardDraftUpdateRequest
from app.domains.marketplace_listing.llm_client import LlmResult
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.user_settings.model import UserSetting
from tests import test_listing_wizard_service as wizard_tests     # 모듈째로 — 기반 클래스가 이 파일에서 다시 발견되지 않게

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
ROUTER = (ROOT / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
NODE = shutil.which("node")


def pairs(found):
    return [(f.kind, f.value, f.source) for f in found]


class ExtractTests(unittest.TestCase):

    def test_labeled_values_with_and_without_colon(self):
        found = ax.extract(pasted="소재: 면 / 치수: M, L, XL / 색상: 블랙, 스킨 / 제조국: 중국 / 세탁: 찬물")
        self.assertEqual(pairs(found), [("ORIGIN", "중국", "PASTED"), ("MATERIAL", "면", "PASTED"), ("SIZE", "M, L, XL", "PASTED"), ("COLOR", "블랙, 스킨", "PASTED")])
        found = ax.extract(description="바디워시 200ml 본품입니다. 제조국 대한민국, 사용기한은 3년입니다.")
        self.assertEqual(pairs(found), [("ORIGIN", "대한민국", "DESCRIPTION")])     # 설명은 라벨 값만(200ml는 보지 않는다)
        for f in found:
            self.assertIn(f.value, f.evidence)

    def test_free_text_numbers_are_not_guessed(self):
        found = ax.extract(pasted="원산지 : 중국(OEM)\n구성 : 본품 1개 + 리필 2개\n1개월 사용 가능, 2개 구매시 할인, 물 500ml에 희석")
        self.assertEqual(pairs(found), [("ORIGIN", "중국(OEM)", "PASTED"), ("QUANTITY", "본품 1개 + 리필 2개", "PASTED")])

    def test_title_and_options_give_volume_and_pack_quantity(self):
        self.assertEqual(pairs(ax.extract(title="백산수 2L x 6개", options=["용량 2L", "수량 6병"])),
                         [("VOLUME", "2L", "TITLE"), ("QUANTITY", "6", "TITLE"), ("VOLUME", "2L", "OPTIONS"), ("QUANTITY", "6", "OPTIONS")])
        self.assertEqual(pairs(ax.extract(pasted="구성: 12개입")), [("QUANTITY", "12", "PASTED")])
        self.assertEqual(pairs(ax.extract(title="물티슈 100매 10팩 캡형")), [("QUANTITY", "10", "TITLE")])   # 매 = 장 수
        self.assertEqual(pairs(ax.extract(title="영양제 1개월분")), [])
        self.assertEqual(pairs(ax.extract(title="샴푸 500ml 3입")), [("VOLUME", "500ml", "TITLE"), ("QUANTITY", "3", "TITLE")])
        self.assertEqual(pairs(ax.extract(title="대용량 1000개")), [])                    # 500개 넘는 숫자는 구성 수량으로 보지 않는다
        self.assertEqual(pairs(ax.extract(title="벌크 600개")), [])
        self.assertEqual(pairs(ax.extract(title="벌크 500개")), [("QUANTITY", "500", "TITLE")])

    def test_a_long_value_without_a_colon_is_a_sentence_not_a_value(self):
        sentence = "소재 부드러운 극세사를 써서 피부에 닿는 느낌이 아주 좋고 오래 써도 보풀이 잘 안 생겨요"
        self.assertEqual(pairs(ax.extract(pasted=sentence)), [])
        self.assertEqual(pairs(ax.extract(pasted="소재 극세사 100%")), [("MATERIAL", "극세사 100%", "PASTED")])
        self.assertEqual(pairs(ax.extract(pasted=sentence.replace("소재 ", "소재: ", 1)))[0][:2], ("MATERIAL", sentence[3:]))   # 콜론이 있으면 받는다

    def test_supplier_notice_rows_map_by_label_and_contacts_are_skipped(self):
        rows = [("제조국", "베트남"), ("제품 소재", "폴리에스터 100%"), ("A/S 책임자와 전화번호", "010-1111-2222"), ("품질보증기준", "관련 법")]
        self.assertEqual(pairs(ax.extract(supplier_notice=rows)), [("ORIGIN", "베트남", "SUPPLIER_NOTICE"), ("MATERIAL", "폴리에스터 100%", "SUPPLIER_NOTICE")])
        self.assertIsNone(ax.kind_of_notice_label("A/S 책임자와 전화번호"))
        self.assertIsNone(ax.kind_of_notice_label("소비자상담 관련 전화번호"))
        self.assertIsNone(ax.kind_of_notice_label("수입자 전화번호"))                    # 종류 말이 들어 있어도 연락처 칸은 제안하지 않는다
        self.assertIsNone(ax.kind_of_notice_label("제조사 고객센터"))
        self.assertEqual(pairs(ax.extract(supplier_notice=[("수입자 전화번호", "02-123-4567")])), [])
        self.assertEqual(ax.compare(ax.extract(pasted="수입자: 홈즈상사"), {"수입자 전화번호": ""}), [])
        self.assertEqual(ax.kind_of_notice_label("화장품제조업자, 화장품책임판매업자"), "MANUFACTURER")

    def test_duplicates_within_a_source_collapse(self):
        found = ax.extract(pasted="제조국: 중국\n원산지: 중국", title="")
        self.assertEqual(pairs(found), [("ORIGIN", "중국", "PASTED")])

    def test_the_text_is_capped(self):
        long = "가" * (ax.MAX_TEXT + 100) + " 제조국: 중국"
        self.assertEqual(ax.extract(pasted=long), [])


class CompareTests(unittest.TestCase):

    def test_states(self):
        found = ax.extract(pasted="소재: 면 / 치수: M, L, XL / 색상: 블랙, 스킨 / 제조국: 중국")
        rows = {r["label"]: r for r in ax.compare(found, {"제조국": "대한민국", "제품 소재": "", "치수": "m,l,xl", "색상": "블랙, 스킨", "A/S 책임자와 전화번호": "", "품질보증기준": "x"})}
        self.assertEqual({k: v["state"] for k, v in rows.items()}, {"제조국": "DIFFERENT", "제품 소재": "EMPTY", "치수": "SAME", "색상": "SAME"})
        self.assertEqual(rows["제조국"]["suggestions"][0]["value"], "중국")
        self.assertIn("제조국: 중국", rows["제조국"]["suggestions"][0]["evidence"])

    def test_several_values_are_a_conflict_with_sources(self):
        found = ax.extract(pasted="제조국: 중국", supplier_notice=[("제조국", "베트남")], description="제조국 중국, 소재 면")
        [row] = ax.compare(found, {"제조국": "중국"})
        self.assertEqual(row["state"], "CONFLICT")
        self.assertEqual([(s["value"], s["sources"]) for s in row["suggestions"]], [("중국", ["PASTED", "DESCRIPTION"]), ("베트남", ["SUPPLIER_NOTICE"])])

    def test_quantity_check(self):
        found = ax.extract(title="백산수 2L x 6개")
        self.assertEqual(ax.quantity_check(found, [1])["text_quantities"], [6])
        self.assertIsNone(ax.quantity_check(found, [6]))
        self.assertIsNone(ax.quantity_check(found, []))
        self.assertIsNone(ax.quantity_check(ax.extract(description="구성: 6"), [1]))      # 우리 설명은 근거로 쓰지 않는다
        self.assertIsNone(ax.quantity_check(found, [True]))
        both = ax.extract(title="본품 1개 + 리필 6개")                                      # 글의 수량이 모두 판매 단위에 있어야 넘어간다
        self.assertEqual(ax.quantity_check(both, [1])["text_quantities"], [1, 6])
        self.assertIsNone(ax.quantity_check(both, [6, 1]))


class AiValidationTests(unittest.TestCase):

    SOURCE = "상품명 테스트\n제조국: 중국 / 소재: 면 100% / 구성: 본품 2개"

    def test_grounded_values_are_kept_and_invented_ones_dropped(self):
        raw = json.dumps({"attributes": [
            {"kind": "ORIGIN", "value": "중국", "quote": "제조국: 중국"},
            {"kind": "MATERIAL", "value": "면 100%", "quote": "소재:  면 100%"},
            {"kind": "MANUFACTURER", "value": "홈즈", "quote": "제조사: 홈즈"},          # 원문에 없는 근거
            {"kind": "ORIGIN", "value": "베트남", "quote": "제조국: 중국"},              # 근거에 없는 값
            {"kind": "QUANTITY", "value": "2개", "quote": "구성: 본품 2개"},             # 숫자만
            {"kind": "PRICE", "value": "1000", "quote": "구성"},
            "x",
        ]}, ensure_ascii=False)
        kept, refused = ax.validate_ai_attributes("앞말 " + raw + " 뒷말", self.SOURCE)
        self.assertEqual(pairs(kept), [("ORIGIN", "중국", "AI"), ("MATERIAL", "면 100%", "AI")])
        self.assertEqual([r["reason"] for r in refused], ["QUOTE_NOT_IN_TEXT", "VALUE_NOT_IN_QUOTE", "QUANTITY_NOT_NUMBER", "BAD_ITEM", "BAD_ITEM"])

    def test_bad_responses(self):
        self.assertEqual(ax.validate_ai_attributes("응답 없음", self.SOURCE), ([], [{"reason": "NOT_JSON"}]))
        self.assertEqual(ax.validate_ai_attributes('{"attributes": "x"}', self.SOURCE), ([], [{"reason": "NOT_JSON"}]))
        many = json.dumps({"attributes": [{"kind": "ORIGIN", "value": "중국", "quote": "제조국: 중국"}] * 30}, ensure_ascii=False)
        self.assertEqual(len(ax.validate_ai_attributes(many, self.SOURCE)[0]), ax.MAX_AI_ITEMS)

    def test_the_prompt_sends_only_the_pasted_text_name_and_options(self):
        prompt, source = ax.ai_user_prompt(pasted="제조국: 중국" + "x" * 9000, title="상품", options=["색상 블랙"])
        body = json.loads(prompt)
        self.assertEqual(set(body), {"product_name", "options", "text"})
        self.assertEqual(len(body["text"]), 6000)
        self.assertIn("제조국: 중국", source)


class FakeClient:
    def __init__(self, text):
        self.text, self.calls = text, []

    def complete(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return LlmResult(self.text, 900, 200, "claude-test")


class ServiceTests(wizard_tests.ListingWizardServiceTestCase):

    NOW = datetime(2026, 11, 14, 9, 0, 0)

    def setUp(self):
        super().setUp()
        for table in (UserSetting.__table__, ExternalCallAllowance.__table__, ExternalCallLog.__table__):
            table.create(bind=self.engine, checkfirst=True)
        self.store = InMemoryCredentialStore()
        self.user_id = 7
        wizard = self._create_wizard()
        self.wizard = self.service.update_draft(wizard.id, self.company_id, WizardDraftUpdateRequest(
            expected_version=wizard.version, product_name="백산수 2L x 6개", brand=None, category="생활용품",
            description="제조국 대한민국, 깨끗한 물", options=[{"name": "용량", "values": ["2L"]}],
            sku_list=[{"sku_code": "SKU-1", "option_values": {"용량": "2L"}}], keywords=[],
        ))
        row = self.db.get(ListingWizard, self.wizard.id)
        row.channel_selections_json = json.dumps([{"required_fields": {"items": [{"itemName": "기본", "unitCount": 1}]}}])
        self.db.commit()
        self.client = FakeClient(json.dumps({"attributes": [{"kind": "MANUFACTURER", "value": "농심", "quote": "제조사: 농심"}, {"kind": "ORIGIN", "value": "일본", "quote": "제조국: 일본"}]}, ensure_ascii=False))

    def ai(self):
        return AiDraftService(self.db, self.store, client_factory=lambda k, m: self.client, now_factory=lambda: self.NOW)

    def svc(self):
        return AttributeSuggestService(self.db, self.store, ai_service_factory=self.ai)

    def snapshot(self):
        row = self.db.get(ListingWizard, self.wizard.id)
        self.db.refresh(row)
        return (row.draft_json, row.channel_selections_json, row.version)

    def test_rules_only_read_the_wizard_and_change_nothing(self):
        before = self.snapshot()
        out = self.svc().suggest(self.wizard.id, self.company_id, self.user_id, pasted="원산지: 대한민국\n제조사: 농심",
                                 current={"제조국(원산지)": "", "제조자(수입자)": "", "용량(중량)": "2L"})
        rows = {r["label"]: r for r in out["rows"]}
        self.assertEqual(rows["제조국(원산지)"]["state"], "EMPTY")
        self.assertEqual([s["sources"] for s in rows["제조국(원산지)"]["suggestions"]], [["PASTED", "DESCRIPTION"]])
        self.assertEqual(rows["제조자(수입자)"]["suggestions"][0]["value"], "농심")
        self.assertEqual(rows["용량(중량)"]["state"], "SAME")
        self.assertEqual(out["quantity_check"]["text_quantities"], [6])
        self.assertEqual(out["quantity_check"]["unit_counts"], [1])
        self.assertEqual(out["ai"], {"used": False})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.db.query(ExternalCallLog).count(), 0)

    def test_ai_needs_text_settings_and_the_analysis_allowance(self):
        with self.assertRaises(BadRequestException):
            self.svc().suggest(self.wizard.id, self.company_id, self.user_id, pasted="  ", current={}, use_ai=True)
        self.ai().save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=1000, in_price=4000.0, out_price=20000.0,
                                api_key="sk-ant-TEST-0123456789abcdefghij")
        with self.assertRaises(ExternalCallNotAllowed):
            self.svc().suggest(self.wizard.id, self.company_id, self.user_id, pasted="제조사: 농심", current={}, use_ai=True)
        self.assertEqual(self.client.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(purpose=CallPurpose.AI_ANALYSIS, status="PENDING").count()
        self.assertEqual(pending, 1)

    def test_ai_with_blank_text_is_refused_before_any_call_even_when_allowed(self):
        self.ai().save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=1000, in_price=4000.0, out_price=20000.0,
                                api_key="sk-ant-TEST-0123456789abcdefghij")
        ExternalCallPolicyService(self.db).grant(self.company_id, system=ExternalSystem.LLM, kind=CallKind.WRITE, purpose=CallPurpose.AI_ANALYSIS,
                                                 mode=AllowanceMode.COUNTED, count=2, user_id=self.user_id)
        for blank in ("", " \n\t "):
            with self.assertRaises(BadRequestException):
                self.svc().suggest(self.wizard.id, self.company_id, self.user_id, pasted=blank, current={}, use_ai=True)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.db.query(ExternalCallLog).count(), 0)

    def test_ai_adds_only_grounded_values_and_records_usage(self):
        self.ai().save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=1000, in_price=4000.0, out_price=20000.0,
                                api_key="sk-ant-TEST-0123456789abcdefghij")
        ExternalCallPolicyService(self.db).grant(self.company_id, system=ExternalSystem.LLM, kind=CallKind.WRITE, purpose=CallPurpose.AI_ANALYSIS,
                                                 mode=AllowanceMode.COUNTED, count=2, user_id=self.user_id)
        before = self.snapshot()
        out = self.svc().suggest(self.wizard.id, self.company_id, self.user_id, pasted="제조사: 농심 / 물 맛이 좋아요", current={"제조자(수입자)": "", "제조국": ""}, use_ai=True)
        self.assertEqual(len(self.client.calls), 1)
        system, user, kw = self.client.calls[0]
        self.assertEqual(system, ax.AI_SYSTEM)
        self.assertEqual(set(json.loads(user)), {"product_name", "options", "text"})
        self.assertEqual(kw["max_output_tokens"], 800)
        self.assertEqual((out["ai"]["used"], out["ai"]["kept"]), (True, 1))
        self.assertEqual([r["reason"] for r in out["ai"]["refused"]], ["QUOTE_NOT_IN_TEXT"])         # '제조국: 일본'은 글에 없다
        rows = {r["label"]: r for r in out["rows"]}
        self.assertEqual(rows["제조자(수입자)"]["suggestions"][0]["sources"], ["PASTED", "AI"])
        self.assertEqual([s["value"] for s in rows["제조국"]["suggestions"]], ["대한민국"])
        usage = self.db.execute(text("SELECT description FROM audit_logs WHERE action = 'AI_DRAFT_USAGE'")).fetchall()
        self.assertEqual(json.loads(usage[-1][0])["p"], "ATTRIBUTE")
        self.assertEqual(self.snapshot(), before)

    def test_unit_counts_ignore_odd_values(self):
        self.assertEqual(AttributeSuggestService._unit_counts(json.dumps([{"required_fields": {"items": [{"unitCount": 2}, {"unitCount": True}, {"unitCount": "3"}, "x"]}}, "y"])), [2])
        self.assertEqual(AttributeSuggestService._unit_counts("not json"), [])


# 위저드 서비스 테스트 기반을 setUp만 빌려 쓴다 — 물려받은 기존 테스트까지 다시 돌지 않게 이 클래스에서는 지운다.
for _name in [n for n in dir(wizard_tests.ListingWizardServiceTestCase) if n.startswith("test")]:
    setattr(ServiceTests, _name, None)


class WiringTests(unittest.TestCase):

    def test_route_uses_the_edit_permission_and_a_strict_body(self):
        block = ROUTER.split('@router.post("/{wizard_id}/attribute-suggestions")')[1].split("@router.")[0]
        self.assertIn("ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)", block)
        req = ROUTER.split("class AttributeSuggestRequest")[1].split("@router")[0]
        self.assertIn('extra="forbid"', req)
        self.assertIn("max_length=20000", req)
        self.assertIn("use_ai: bool = False", req)

    def test_screen_puts_values_only_when_pressed_and_asks_before_ai(self):
        body = CONSOLE.split("function lwAttachAttributeSuggest(")[1].split("function lwRenderNoticeFields(")[0]
        self.assertLess(body.index("if (useAi && !(await askConfirm("), body.index("apiFetch(`/listing-wizards/${wizardId}/attribute-suggestions`"))
        self.assertIn('btn.addEventListener("click"', body)
        self.assertIn("target.value = btn.dataset.lwAttrValue;", body)
        self.assertNotIn("/apply", body)
        self.assertIn("lwAttachAttributeSuggest(host, grid);", CONSOLE)

    def test_texts_exist_in_both_languages(self):
        ko = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
        en = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
        keys = set(re.findall(r'"(lw\.attr_[a-z_]+)"', CONSOLE))
        self.assertGreaterEqual(len(keys), 25)
        for key in keys:
            self.assertIn(f'"{key}":', ko, key)
            self.assertIn(f'"{key}":', en, key)


def function_source(name):
    match = re.search(r"function " + re.escape(name) + r"\(", CONSOLE)
    start = match.start()
    i = CONSOLE.index("{", match.end())
    depth = 0
    while True:
        ch = CONSOLE[i]
        depth += ch == "{"
        depth -= ch == "}"
        i += 1
        if depth == 0:
            return CONSOLE[start:i]


HARNESS = r"""
const CATALOG = JSON.parse(process.env.CATALOG_JSON);
const HomezI18n = { t: (key, params) => { let s = CATALOG[key] !== undefined ? CATALOG[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def run_js(self, expression):
        ko = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
        catalog = {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.attr_[a-z_]+)": ("(?:[^"\\]|\\.)*")', ko)}
        consts = [re.search(r"^  const %s = .*?;$" % n, CONSOLE, re.M).group(0) for n in ("LW_ATTR_STATE", "LW_ATTR_SOURCE")]
        script = HARNESS.replace("__SOURCES__", "\n".join(consts + [function_source("lwAttrCount"), function_source("lwAttrResultHtml")])).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "CATALOG_JSON": json.dumps(catalog)}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_rows_states_sources_and_put_buttons(self):
        result = {"rows": [
            {"label": "제조국", "current": "대한민국", "state": "DIFFERENT", "suggestions": [{"value": "중국", "sources": ["PASTED", "AI", "<x>"], "evidence": "제조국: 중국"}]},
            {"label": "제품 소재", "current": "", "state": "EMPTY", "suggestions": [{"value": "면 \"100%\"", "sources": ["SUPPLIER_NOTICE"], "evidence": "<b>소재</b>"}]},
            {"label": "치수", "current": "M", "state": "WHAT", "suggestions": []},
        ], "quantity_check": {"text_quantities": [6], "unit_counts": [1]}, "ai": {"used": True, "kept": 1, "refused": [{"reason": "QUOTE_NOT_IN_TEXT"}]}}
        html = self.run_js(f"lwAttrResultHtml({json.dumps(result, ensure_ascii=False)})")
        self.assertIn('<span class="pill warn">다름</span>', html)
        self.assertIn('<span class="pill neutral">비어 있음</span>', html)
        self.assertIn('<span class="pill warn">값이 여럿 — 골라 주세요</span>', html)       # 모르는 상태는 '골라 주세요'
        self.assertIn("(붙여 넣은 글, AI)", html)
        self.assertIn("(온채널 고시)", html)
        self.assertNotIn("<x>", html)
        self.assertNotIn("<b>소재</b>", html)
        self.assertIn('data-lw-attr-put="제조국" data-lw-attr-value="중국"', html)
        self.assertIn('data-lw-attr-value="면 &quot;100%&quot;"', html)
        self.assertIn("글에는 6개인데 판매 단위 수량은 1이에요", html)
        self.assertIn("근거가 확인된 1개를 더했어요(근거가 글에 없어 버린 것 1개)", html)

    def test_nothing_found(self):
        for value in ("null", "{}", "{rows: []}"):
            self.assertIn("맞는 값을 찾지 못했어요", self.run_js(f"lwAttrResultHtml({value})"))


if __name__ == "__main__":
    unittest.main()
