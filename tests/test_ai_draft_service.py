"""
=========================================================
Homez OS

File : tests/test_ai_draft_service.py

2026-10-18 — 등록 도우미 AI 초안(Phase B) 서비스(`ai_draft_service.py`) 규칙을 고정한다(가짜 AI 클라이언트, 바깥 호출 0).

  - 설정이 없거나 키가 없으면 호출하지 않는다. API 키는 자격증명 저장소에만 있고 DB·응답·감사 기록에 남지 않는다.
  - 이번 달 사용액 + 이번 호출의 최악 추정액이 월 한도를 넘으면 호출하지 않는다. 호출하면 사용 기록(토큰·비용)을 남긴다(검증 탈락이어도).
  - 승인 센터 허용(`LLM`/`AI_DRAFT`, 횟수형)이 없으면 호출하지 않고 허용 요청이 올라간다. 허용이 있으면 1회 차감한다.
  - 제안은 위저드를 바꾸지 않는다. 적용은 사용자가 고른 필드만, 서버가 같은 검증을 다시 한 뒤 저장하고 나머지 초안(옵션·SKU 등)은 보존한다.
=========================================================
"""

import json
import unittest
from datetime import datetime

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.marketplace_listing.ai_draft_service import CREDENTIAL_TARGET
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.marketplace_listing.listing_wizard_schema import WizardDraftUpdateRequest
from app.domains.marketplace_listing.llm_client import LlmError
from app.domains.marketplace_listing.llm_client import LlmResult
from app.domains.user_settings.model import UserSetting
from tests.test_listing_wizard_service import ListingWizardServiceTestCase as Base

KEY = "sk-ant-TEST-0123456789abcdefghij"
GOOD_TEXT = json.dumps({
    "product_name": "테스트 상품 본품",
    "keywords": ["테스트", "상품 본품"],
    "description": "테스트 상품에 대한 설명 초안입니다. 용량은 입력된 값만 사용했고 필요한 내용만 담았습니다.",
}, ensure_ascii=False)


class FakeClient:
    def __init__(self, text=GOOD_TEXT, input_tokens=1000, output_tokens=500, error=None):
        self.text, self.input_tokens, self.output_tokens, self.error = text, input_tokens, output_tokens, error
        self.calls = []

    def complete(self, system, user, **kw):
        self.calls.append((system, user, kw))
        if self.error:
            raise self.error
        return LlmResult(self.text, self.input_tokens, self.output_tokens, "claude-test")


class AiDraftServiceTests(Base):

    NOW = datetime(2026, 10, 18, 9, 0, 0)

    def setUp(self):
        super().setUp()
        for table in (UserSetting.__table__, ExternalCallAllowance.__table__, ExternalCallLog.__table__):
            table.create(bind=self.engine, checkfirst=True)
        self.store = InMemoryCredentialStore()
        self.client = FakeClient()
        self.user_id = 7
        self.wizard = self._wizard()

    def _wizard(self):
        wizard = self._create_wizard()
        return self.service.update_draft(wizard.id, self.company_id, WizardDraftUpdateRequest(
            expected_version=wizard.version, product_name="테스트 상품", brand=None, category="생활용품",
            description="기존 설명", options=[{"name": "수량", "values": ["1개"]}],
            sku_list=[{"sku_code": "SKU-1", "option_values": {"수량": "1개"}}], keywords=["기존검색어"],
        ))

    def svc(self, client=None):
        client = client or self.client
        return AiDraftService(self.db, self.store, client_factory=lambda k, m: client, now_factory=lambda: self.NOW)

    def configure(self, limit=1000, key=KEY):
        return self.svc().save_settings(
            self.user_id, self.company_id, model="claude-test", monthly_limit_krw=limit, in_price=4000.0, out_price=20000.0, api_key=key,
        )

    def grant(self, count=3):
        ExternalCallPolicyService(self.db).grant(
            self.company_id, system=ExternalSystem.LLM, kind=CallKind.WRITE, purpose=CallPurpose.AI_DRAFT,
            mode=AllowanceMode.COUNTED, count=count, user_id=self.user_id,
        )

    def usage_rows(self):
        return self.db.execute(text("SELECT entity_id, description FROM audit_logs WHERE action = 'AI_DRAFT_USAGE'")).fetchall()

    # ---------------- 설정 ----------------

    def test_without_settings_or_key_nothing_is_called(self):
        with self.assertRaises(BadRequestException):
            self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(self.svc().status(self.user_id, self.company_id)["configured"])
        self.configure(key=None)  # 키 없이 설정만 저장
        with self.assertRaises(BadRequestException):
            self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(self.client.calls, [])

    def test_the_key_lives_only_in_the_credential_store(self):
        status = self.configure()
        self.assertTrue(status["configured"] and status["key_saved"])
        self.assertEqual(self.store.read(CREDENTIAL_TARGET), {"api_key": KEY})
        dump = " ".join(str(v) for row in self.db.execute(text("SELECT * FROM user_settings")).fetchall() for v in row)
        dump += " ".join(str(v) for row in self.db.execute(text("SELECT * FROM audit_logs")).fetchall() for v in row)
        self.assertNotIn(KEY, dump)
        self.assertNotIn(KEY, json.dumps(status))
        self.assertNotIn("api_key", json.dumps(status))

    def test_saving_again_without_a_key_keeps_the_stored_key_and_keeps_other_saved_defaults(self):
        self.db.execute(text("INSERT INTO user_settings (user_id, company_id, setting_key, setting_value, schema_version, version, created_at, updated_at) VALUES (:u, :c, 'listing_defaults', :v, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                        {"u": self.user_id, "c": self.company_id, "v": json.dumps({"wing_user_id": "abc123"})})
        self.db.commit()
        self.configure()
        again = self.svc().save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=2000, in_price=1.0, out_price=2.0)
        self.assertEqual((again["monthly_limit_krw"], again["key_saved"]), (2000, True))
        self.assertEqual(self.store.read(CREDENTIAL_TARGET), {"api_key": KEY})
        value = json.loads(self.db.execute(text("SELECT setting_value FROM user_settings WHERE setting_key = 'listing_defaults'")).scalar())
        self.assertEqual(value["wing_user_id"], "abc123")

    def test_bad_settings_are_rejected(self):
        s = self.svc()
        base = dict(model="claude-test", monthly_limit_krw=1000, in_price=1.0, out_price=1.0)
        for override in ({"model": "bad model!"}, {"monthly_limit_krw": 0}, {"monthly_limit_krw": 10**9}, {"monthly_limit_krw": True},
                         {"in_price": 0}, {"out_price": -1}, {"in_price": 10**9}, {"api_key": "short"}, {"api_key": "has space in the key 0123456789"}):
            with self.assertRaises(BadRequestException, msg=str(override)):
                s.save_settings(self.user_id, self.company_id, **{**base, **override})

    # ---------------- 제안 ----------------

    def test_a_proposal_calls_once_records_usage_and_does_not_touch_the_wizard(self):
        self.configure()
        self.grant()
        before = self.service.get(self.wizard.id, self.company_id)
        result = self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(set(result["fields"]), {"product_name", "keywords", "description"})
        # 1000*4000/1e6 + 500*20000/1e6 = 4 + 10 = 14원
        self.assertAlmostEqual(result["usage"]["cost_krw"], 14.0, places=2)
        self.assertEqual((result["usage"]["month_calls"], result["usage"]["monthly_limit_krw"]), (1, 1000))
        rows = self.usage_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0].startswith("202610:WZ"))
        self.assertEqual(json.loads(rows[0][1])["c"], 14.0)
        after = self.service.get(self.wizard.id, self.company_id)
        self.assertEqual((after.version, after.draft_json), (before.version, before.draft_json))
        self.assertEqual(result["current"]["product_name"], "테스트 상품")

    def test_what_is_sent_is_only_product_information(self):
        self.configure()
        self.grant()
        self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        system, user, kw = self.client.calls[0]
        self.assertIn("테스트 상품", user)
        self.assertIn("수량", user)
        for word in ("SKU-1", KEY, "price", "cost", "company"):
            self.assertNotIn(word, user)
        self.assertLessEqual(kw["max_output_tokens"], 1500)

    def test_a_failed_validation_still_costs_and_is_recorded(self):
        self.configure()
        self.grant()
        client = FakeClient(text="그냥 글입니다")
        result = self.svc(client).propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(result["fields"], {})
        self.assertIn("all", result["rejected"])
        self.assertFalse(json.loads(self.usage_rows()[0][1])["ok"])

    def test_a_client_failure_is_an_error_without_a_usage_record_but_the_allowance_log_says_failed(self):
        self.configure()
        self.grant()
        with self.assertRaises(ServiceUnavailableException):
            self.svc(FakeClient(error=LlmError("AI 서비스 요청 실패(ConnectionError)"))).propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(self.usage_rows(), [])
        self.assertEqual(self.db.query(ExternalCallLog).filter(ExternalCallLog.system == "LLM").one().outcome, "FAILED")

    # ---------------- 한도 ----------------

    def test_the_monthly_limit_blocks_before_calling(self):
        self.configure(limit=5)  # 최악 추정이 5원을 넘는다
        self.grant()
        with self.assertRaises(BadRequestException) as ctx:
            self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertIn("한도", str(ctx.exception))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.db.query(ExternalCallLog).count(), 0)  # 허용도 소모하지 않는다

    def test_this_months_usage_counts_against_the_limit_and_last_month_does_not(self):
        self.configure(limit=100)
        self.grant(5)
        self.db.execute(text("INSERT INTO audit_logs (action, entity, entity_id, description) VALUES ('AI_DRAFT_USAGE', 'ai_draft', '202609:WZ1', :d)"),
                        {"d": json.dumps({"c": 99999})})
        self.db.commit()
        self.assertEqual(self.svc().status(self.user_id, self.company_id)["month_used_krw"], 0)
        self.svc().propose(self.wizard.id, self.company_id, self.user_id)  # 지난달 사용액은 세지 않는다
        self.db.execute(text("INSERT INTO audit_logs (action, entity, entity_id, description) VALUES ('AI_DRAFT_USAGE', 'ai_draft', '202610:WZ9', :d)"),
                        {"d": json.dumps({"c": 95})})
        self.db.commit()
        calls = len(self.client.calls)
        with self.assertRaises(BadRequestException):
            self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(len(self.client.calls), calls)

    # ---------------- 승인 센터 허용 ----------------

    def test_without_an_allowance_nothing_is_called_and_a_request_is_raised(self):
        self.configure()
        with self.assertRaises(ExternalCallNotAllowed):
            self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(self.client.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter(ExternalCallAllowance.system == "LLM", ExternalCallAllowance.status == "PENDING").all()
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0].purpose, pending[0].kind, pending[0].mode), ("AI_DRAFT", "WRITE", "COUNTED"))

    def test_each_call_spends_one_allowance_and_an_exhausted_one_blocks(self):
        self.configure()
        self.grant(1)
        self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(self.db.query(ExternalCallAllowance).filter(ExternalCallAllowance.system == "LLM").first().remaining_count, 0)
        with self.assertRaises(ExternalCallNotAllowed):
            self.svc().propose(self.wizard.id, self.company_id, self.user_id)
        self.assertEqual(len(self.client.calls), 1)

    def test_a_standing_allowance_cannot_be_given_to_the_ai(self):
        with self.assertRaises(Exception):
            ExternalCallPolicyService(self.db).grant(
                self.company_id, system=ExternalSystem.LLM, kind=CallKind.WRITE, purpose=CallPurpose.AI_DRAFT,
                mode=AllowanceMode.STANDING, per_hour_limit=10, hours=24, user_id=1,
            )

    # ---------------- 적용 ----------------

    def test_apply_saves_only_the_chosen_fields_and_keeps_the_rest_of_the_draft(self):
        self.configure()
        wizard = self.service.get(self.wizard.id, self.company_id)
        result = self.svc().apply(
            wizard.id, self.company_id, self.user_id,
            {"product_name": "테스트 상품 본품", "description": "테스트 상품에 대한 설명 초안입니다. 필요한 내용만 담았습니다 읽어 보세요."}, wizard.version,
        )
        self.assertEqual(result["applied"], ["description", "product_name"])
        draft = json.loads(self.service.get(wizard.id, self.company_id).draft_json)
        self.assertEqual(draft["product_name"], "테스트 상품 본품")
        self.assertIn("설명 초안", draft["description"])
        self.assertEqual(draft["keywords"], ["기존검색어"])          # 고르지 않은 필드는 그대로
        self.assertEqual(draft["options"][0]["values"], ["1개"])     # 옵션·SKU 보존
        self.assertEqual(draft["sku_list"][0]["sku_code"], "SKU-1")
        self.assertEqual(draft["category"], "생활용품")

    def test_apply_revalidates_on_the_server(self):
        wizard = self.service.get(self.wizard.id, self.company_id)
        bad = {"product_name": "테스트 상품 1개", "description": "테스트 상품은 피부 질환 예방에 아주 좋은 제품이라고 설명하는 글입니다 읽어 보세요."}
        with self.assertRaises(BadRequestException):
            self.svc().apply(wizard.id, self.company_id, self.user_id, bad, wizard.version)
        draft = json.loads(self.service.get(wizard.id, self.company_id).draft_json)
        self.assertEqual(draft["product_name"], "테스트 상품")
        mixed = self.svc().apply(
            wizard.id, self.company_id, self.user_id,
            {"product_name": "테스트 상품 1개", "keywords": ["테스트"]}, wizard.version,
        )
        self.assertEqual(mixed["applied"], ["keywords"])
        self.assertIn("product_name", mixed["refused"])

    def test_apply_needs_something_and_the_current_version(self):
        wizard = self.service.get(self.wizard.id, self.company_id)
        with self.assertRaises(BadRequestException):
            self.svc().apply(wizard.id, self.company_id, self.user_id, {}, wizard.version)
        from app.core.exceptions import ConflictException

        with self.assertRaises(ConflictException):
            self.svc().apply(wizard.id, self.company_id, self.user_id, {"keywords": ["테스트"]}, wizard.version + 5)


def load_tests(loader, standard_tests, pattern):
    """기반 클래스(위저드 서비스 시험 환경)의 수백 개 시험을 물려받아 함께 돌리지 않도록, 이 모듈이 직접 정의한 시험만 싣는다."""

    suite = unittest.TestSuite()
    names = [f"{AiDraftServiceTests.__name__}.{name}" for name in AiDraftServiceTests.__dict__ if name.startswith("test_")]
    suite.addTests(loader.loadTestsFromNames(names, __import__(__name__, fromlist=["x"])))
    return suite


if __name__ == "__main__":
    unittest.main()
