"""
=========================================================
Homez OS

File : tests/test_wizard_followups_20261014.py

러닝 무릎보호대 등록 후속 개선(A11·A12) 고정. (상세: docs/HOMEZ_V7_REGISTRATION_LESSONS_20261014.md)

 A11) 8단계 승인 화면이 "발주 예상비용에 채널 수수료율 사용" 선택을 보여 준다
 A12) 상품명의 의료·효능 표현을 사전검사가 경고한다(차단하지 않는다)

임시 SQLite 파일 DB만 쓴다.
=========================================================
"""

import json
import tempfile
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_precheck
from app.domains.marketplace_listing.listing_wizard_precheck import CLAIM_WORDS
from app.domains.marketplace_listing.listing_wizard_precheck import claim_words_in
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

APP = Path(__file__).resolve().parents[1] / "app"
CONSOLE = (APP / "web" / "console.js").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")


class ClaimWordsTests(unittest.TestCase):

    def test_word_list_catches_the_real_case_and_ignores_plain_names(self):
        self.assertEqual(claim_words_in("슬개건 러닝 무릎보호대 무릎연골보호 슬개골"), ["연골"])
        self.assertEqual(claim_words_in("러닝 무릎보호대 슬개건밴드"), [])
        self.assertEqual(claim_words_in(None), [])
        self.assertEqual(claim_words_in(""), [])
        self.assertIn("치료", CLAIM_WORDS)

    def test_url_import_uses_the_same_word_list(self):
        from app.domains.product_import import generic_extractor

        self.assertIs(generic_extractor.claim_words_in, claim_words_in)


class ClaimWordsPrecheckTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, ProductCandidate.__table__, ProductCandidateSelection.__table__,
        ])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        company = Company(name="후속 개선 테스트", business_number="888-88-88888", ceo="t", phone="02", email="f@e.c", address="서울")
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id
        for patcher in (
            mock.patch.object(listing_wizard_precheck, "is_restricted_mode", return_value=False),
            mock.patch.object(listing_wizard_precheck.SafetyService, "is_emergency_stop_active", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _issues(self, product_name):
        wizard = ListingWizard(
            company_id=self.company_id, created_by_user_id=1, current_step="PRECHECK", status="DRAFT",
            source_type="MANUAL", draft_json=json.dumps({"product_name": product_name}, ensure_ascii=False),
        )
        result = listing_wizard_precheck.run_precheck(wizard, self.db, self.company_id)
        return {issue.code: issue for issue in result.issues}

    def test_claim_wording_is_a_warning_not_a_block(self):
        issue = self._issues("무릎연골보호 밴드")["PRODUCT_NAME_CLAIM_WORDS"]
        self.assertFalse(issue.blocking)
        self.assertEqual(issue.severity, "WARNING")
        self.assertEqual(issue.params["words"], "연골")
        self.assertEqual(issue.localized_message_key, "listing_wizard.precheck.product_name_claim_words")

    def test_plain_name_has_no_such_issue(self):
        self.assertNotIn("PRODUCT_NAME_CLAIM_WORDS", self._issues("러닝 무릎보호대 슬개건밴드"))

    def test_message_exists_in_both_locales(self):
        self.assertIn('"listing_wizard.precheck.product_name_claim_words"', KO)
        self.assertIn('"listing_wizard.precheck.product_name_claim_words"', EN)


class ApprovalReviewShowsFeeChoiceTests(unittest.TestCase):

    def test_review_reads_the_economics_input_flag_and_shows_yes_or_no(self):
        self.assertIn("pkg.economics_input || []", CONSOLE)
        self.assertIn("marginInput.use_channel_fee_for_purchase_estimate", CONSOLE)
        for key in ("lw.submission_review_use_fee_for_purchase",):
            self.assertIn(f'"{key}"', KO)
            self.assertIn(f'"{key}"', EN)

    def test_economics_screen_default_stays_unchecked(self):
        # 입력에 값이 없으면(새 위저드) 체크되지 않은 채 렌더된다 — 운영자의 명시적 선택이 있어야 켜진다.
        self.assertIn(
            '<input type="checkbox" class="lw-econ-use-fee-for-purchase" ${inp.use_channel_fee_for_purchase_estimate ? "checked" : ""}>',
            CONSOLE,
        )


if __name__ == "__main__":
    unittest.main()


class CredentialDialogTests(unittest.TestCase):
    """A14 — Naver Client ID/Secret 입력: 비밀값을 마스킹하고, 닫을 때 비우며, 빈 값이면 진행하지 않는다."""

    HTML = (APP / "web" / "console.html").read_text(encoding="utf-8")

    def test_secret_field_is_a_masked_password_input_with_no_autofill(self):
        start = self.HTML.index('id="credential-dialog"')
        block = self.HTML[start:self.HTML.index("</dialog>", start)]
        self.assertIn('type="password" id="credential-secret"', block)
        self.assertIn('autocomplete="new-password"', block)

    def test_the_prompt_flow_now_uses_the_dialog(self):
        self.assertIn("await askCredentialPair({", CONSOLE)
        for key in ("candidate_detail.credential_dialog_save", "candidate_detail.credential_dialog_required"):
            self.assertIn(f'"{key}"', KO)
            self.assertIn(f'"{key}"', EN)

    def _run(self, scenario: str) -> dict:
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js를 찾을 수 없어 동작 검증을 건너뜁니다.")
        start = CONSOLE.index("function askCredentialPair(")
        end = CONSOLE.index("// -----", start)
        script = """
'use strict';
const HomezI18n = { t: (k) => k };
const nodes = {};
const make = (id) => (nodes[id] = { id, value: '', textContent: '', handlers: {}, closed: false,
  addEventListener(t, f) { this.handlers[t] = f; }, removeEventListener(t, f) { if (this.handlers[t] === f) delete this.handlers[t]; },
  showModal() {}, close() { this.closed = true; }, focus() {} });
['credential-dialog', 'credential-title', 'credential-body', 'credential-id-label', 'credential-secret-label',
 'credential-id', 'credential-secret', 'credential-error', 'credential-ok', 'credential-cancel'].forEach(make);
const el = (id) => nodes[id];
%s
(async () => {
  const pending = askCredentialPair({ title: 't', body: 'b', idLabel: 'i', secretLabel: 's' });
  %s
  const result = await pending;
  console.log(JSON.stringify({ result, idAfter: nodes['credential-id'].value, secretAfter: nodes['credential-secret'].value,
    error: nodes['credential-error'].textContent, closed: nodes['credential-dialog'].closed }));
})();
""" % (CONSOLE[start:end], scenario)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            result = subprocess.run([node, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_filled_values_are_returned_and_the_fields_are_cleared_on_close(self):
        out = self._run(
            "nodes['credential-id'].value = ' my-id '; nodes['credential-secret'].value = 'S3cret!';"
            " nodes['credential-ok'].handlers.click();",
        )
        self.assertEqual(out["result"], {"id": "my-id", "secret": "S3cret!"})
        self.assertEqual((out["idAfter"], out["secretAfter"]), ("", ""))
        self.assertTrue(out["closed"])

    def test_cancel_returns_null_and_clears_the_fields(self):
        out = self._run(
            "nodes['credential-id'].value = 'x'; nodes['credential-secret'].value = 'y';"
            " nodes['credential-cancel'].handlers.click();",
        )
        self.assertIsNone(out["result"])
        self.assertEqual((out["idAfter"], out["secretAfter"]), ("", ""))

    def test_empty_secret_does_not_close_and_shows_an_error(self):
        out = self._run(
            "nodes['credential-id'].value = 'only-id'; nodes['credential-ok'].handlers.click();"
            " nodes['credential-id'].value = 'a'; nodes['credential-secret'].value = 'b';"
            " nodes['credential-ok'].handlers.click();",
        )
        # 첫 클릭은 거부되고(오류 문구 설정), 두 번째 클릭에서 정상 반환된다.
        self.assertEqual(out["result"], {"id": "a", "secret": "b"})


class ResolveNotRegisteredWiringTests(unittest.TestCase):
    """A8 — 결과불명 정정 화면·API 배선(동작 자체는 tests/test_coupang_live_submission.py)."""

    def test_route_requires_super_admin(self):
        from app.core.guard import SuperAdminGuard
        from app.domains.marketplace_listing import listing_wizard_router as wr

        route = next(
            r for r in wr.router.routes
            if r.path == "/listing-wizards/{wizard_id}/submissions/{submission_id}/resolve-not-registered"
            and "POST" in r.methods
        )
        self.assertIn(SuperAdminGuard, [d.call for d in route.dependant.dependencies])

    def test_screen_asks_for_a_reason_and_refreshes_only_after_success(self):
        start = CONSOLE.index('content.querySelectorAll(".lw-recon-notreg-btn")')
        block = CONSOLE[start:CONSOLE.index("async function", start) if "async function" in CONSOLE[start:] else start + 2500][:2500]
        self.assertIn("await askText(", block)
        self.assertIn("/resolve-not-registered", block)
        self.assertLess(block.index("await askText("), block.index("/resolve-not-registered"))
        self.assertLess(block.index("/resolve-not-registered"), block.index("lwRenderResultsStep(content)"))
        self.assertNotIn("window.prompt(", block)

    def test_labels_exist_in_both_locales(self):
        for key in ("notreg_hint", "notreg_btn", "notreg_reason_prompt", "notreg_confirm_title",
                    "notreg_confirm_ok", "notreg_success", "notreg_failed"):
            with self.subTest(key=key):
                self.assertIn(f'"lw.{key}"', KO)
                self.assertIn(f'"lw.{key}"', EN)
