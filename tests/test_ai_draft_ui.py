"""
=========================================================
Homez OS

File : tests/test_ai_draft_ui.py

2026-10-18 — 등록 도우미 AI 초안(Phase B)의 화면·라우터·허용 배선을 소스 수준에서 고정한다(바깥 호출 0).

  - 화면: 확인창 → 요청 순서, 키 입력은 비밀번호 칸이고 저장소(localStorage 등)에 남기지 않으며, 고른 항목만 보낸다. 한국어·영어 문구가 짝을 이룬다.
  - 라우터: 제안·적용은 위저드 편집 권한, 설정(키·한도)은 최고관리자 + 비밀번호 재확인 + 감사 기록. 키는 응답에 돌려주지 않는다.
  - 허용: `LLM`/`AI_DRAFT`는 허용이 필요한 목적이고 횟수형 기본 요청이다. 새 Migration·SDK 의존성은 없다.
=========================================================
"""

import re
import unittest
from pathlib import Path

from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
CLIENT = (ROOT / "app/domains/marketplace_listing/llm_client.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/domains/marketplace_listing/ai_draft_service.py").read_text(encoding="utf-8")


def panel_source():
    start = CONSOLE.index("function lwAttachAiDraft(content)")
    return CONSOLE[start:CONSOLE.index("// ---- 3단계: 이미지 ----", start)]


class ConsolePanelTests(unittest.TestCase):

    def test_the_panel_hangs_off_the_draft_step(self):
        draft = CONSOLE[CONSOLE.index("function lwRenderDraftStep"):CONSOLE.index("function lwAttachAiDraft(content)")]
        self.assertIn("lwAttachAiDraft(content);", draft)

    def test_propose_asks_first_and_apply_sends_only_picked_fields_with_the_version(self):
        src = panel_source()
        self.assertLess(src.index("askConfirm("), src.index("/propose"))
        self.assertIn("expected_version: lwState.wizard.version", src)
        self.assertIn("box.checked", src)
        self.assertIn("/apply", src)

    def test_saving_settings_needs_the_password_again_and_the_key_is_a_password_field(self):
        src = panel_source()
        self.assertLess(src.index("promptRecentAuthToken()"), src.index("/settings"))
        self.assertIn('type="password"', src)
        self.assertIn('autocomplete="new-password"', src)
        for storage in ("localStorage", "sessionStorage", "document.cookie"):
            self.assertNotIn(storage, src)
        self.assertIn('field("key").value = ""', src)

    def test_text_comes_from_i18n_in_both_languages(self):
        keys = set(re.findall(r'HomezI18n\.t\("(lw\.ai_[a-z_]+)"', panel_source()))
        self.assertGreaterEqual(len(keys), 25)
        for key in keys:
            self.assertIn(f'"{key}":', KO, key)
            self.assertIn(f'"{key}":', EN, key)
        self.assertEqual(set(re.findall(r'"(lw\.ai_[a-z_]+)":', KO)), set(re.findall(r'"(lw\.ai_[a-z_]+)":', EN)))


class RouterTests(unittest.TestCase):

    def block(self, name):
        start = ROUTER.index(f"def {name}(")
        nxt = ROUTER.find("\n@router.", start)
        return ROUTER[start:nxt if nxt > 0 else len(ROUTER)]

    def test_propose_apply_and_status_use_the_wizard_edit_permission(self):
        for name in ("ai_draft_status", "ai_draft_propose", "ai_draft_apply"):
            self.assertIn("ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)", self.block(name), name)

    def test_settings_need_a_super_admin_a_recent_password_check_and_leave_an_audit_trail(self):
        body = self.block("ai_draft_save_settings")
        self.assertIn("SuperAdminGuard", body)
        self.assertLess(body.index("consume_recent_auth_token"), body.index(".save_settings("))
        self.assertIn("AI_DRAFT_SETTINGS_SAVED", body)
        self.assertNotIn("data.api_key}", body)  # 감사 문구에 키를 넣지 않는다

    def test_the_four_routes_exist(self):
        for path in ("/ai-draft/status", "/ai-draft/settings", "/ai-draft/propose", "/ai-draft/apply"):
            self.assertIn(f'"/{{wizard_id}}{path}"', ROUTER)


class SafetyWiringTests(unittest.TestCase):

    def test_the_ai_call_is_a_permission_gated_counted_purpose(self):
        self.assertIn(ExternalSystem.LLM, ExternalSystem.ALL)
        self.assertIn(CallPurpose.AI_DRAFT, CallPurpose.ENFORCED)
        self.assertIn(CallPurpose.AI_DRAFT, CallPurpose.ALL)
        self.assertIn("AI_DRAFT", CallPurpose.LABELS)

    def test_the_service_checks_the_limit_and_the_allowance_before_it_calls_the_client(self):
        guard = SERVICE[SERVICE.index("def call_llm("):SERVICE.index("def record_usage(")]
        self.assertLess(guard.index("한도"), guard.index(".authorize("))
        self.assertLess(guard.index(".authorize("), guard.index("client.complete("))
        propose = SERVICE[SERVICE.index("def propose("):SERVICE.index("def apply(")]
        self.assertLess(propose.index("self.call_llm("), propose.index("self.record_usage("))
        self.assertNotIn("client.complete(", propose)  # 모든 호출은 한도·허용 확인을 거치는 한 경로(call_llm)로만 나간다

    def test_the_key_is_never_logged_stored_or_returned(self):
        for forbidden in ("print(", "logger.", "logging."):
            self.assertNotIn(forbidden, CLIENT)
            self.assertNotIn(forbidden, SERVICE)
        for source in (SERVICE, CLIENT):
            for line in source.splitlines():
                if "api_key" in line and ('f"' in line or "f'" in line or "format(" in line):
                    self.fail("키가 문자열 조립에 쓰인다: " + line.strip())
        self.assertNotIn('"api_key"', SERVICE.split("def status")[1].split("def save_settings")[0])

    def test_no_new_migration_or_sdk_dependency(self):
        requirements = [p for p in (ROOT / "requirements.txt", ROOT / "pyproject.toml") if p.exists()]
        for path in requirements:
            self.assertNotIn("anthropic", path.read_text(encoding="utf-8").lower(), str(path))
        self.assertFalse(list((ROOT / "migrations").glob("*ai_draft*")) if (ROOT / "migrations").exists() else [])


if __name__ == "__main__":
    unittest.main()
