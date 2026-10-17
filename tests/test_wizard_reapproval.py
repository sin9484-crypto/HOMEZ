"""
=========================================================
Homez OS

File : tests/test_wizard_reapproval.py

2026-10-17 — 연동 안정화 A3: 만료된 위저드 최종 승인(유효 1시간)을 8단계에서 승인한 그대로의 내용일 때만 다시 받는다.

고정하는 규칙:
- 승인이 유효하면 남은 시간을 알려 주고 다시 받을 수 없다. 만료됐을 때만 다시 받을 수 있다.
- 다시 받으면 새 승인이 1시간 유효하고, 그 뒤 쿠팡 전송 사전점검의 `VALID_APPROVAL_REQUIRED` 차단이 사라진다.
- 8단계 승인 뒤 위저드 내용이 바뀌었거나, 제출이 이미 전송을 시작했거나, 승인이 취소됐으면 다시 받을 수 없다(새 권한을 만들지 않는다).
- 라우터는 비밀번호 재확인이 먼저이고 감사 기록을 남긴다. 화면은 확인창 → 비밀번호 → 요청 순서다.
=========================================================
"""

import json
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from app.core.exceptions import ConflictException
from app.domains.marketplace_listing.constants import ApprovalStatus
from app.domains.marketplace_listing.listing_wizard_live_service import ListingWizardLiveService
from app.domains.marketplace_listing.listing_wizard_reapproval_service import REAPPROVAL_TTL
from app.domains.marketplace_listing.listing_wizard_reapproval_service import ListingWizardReapprovalService
from tests.test_coupang_live_submission import ListingWizardLiveServiceTestCase as LiveBase

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")


class ReapprovalServiceTests(LiveBase):

    def _pending(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        submission = live.marketplace.get_submission_for_company(result.submission_id, self.company_id)
        return wizard, result, live, submission

    def _latest(self, live, submission):
        return live.approvals.repository.get_latest_approval(submission.listing_id, submission.selection_id, self.company_id)

    def _expire(self, live, submission):
        approval = self._latest(live, submission)
        approval.expires_at = datetime.utcnow() - timedelta(seconds=5)
        self.db.commit()

    def test_a_valid_approval_reports_its_minutes_left_and_cannot_be_renewed(self):
        wizard, result, live, submission = self._pending()
        service = ListingWizardReapprovalService(self.db)
        status = service.status(wizard.id, result.submission_id, self.company_id)
        self.assertEqual(status["state"], "VALID")
        self.assertGreater(status["minutes_left"], 50)
        self.assertFalse(status["can_reapprove"])
        with self.assertRaises(ConflictException):
            service.reapprove(wizard.id, result.submission_id, self.company_id, 99)

    def test_an_expired_approval_is_renewed_for_one_hour_and_the_preflight_blocker_goes_away(self):
        wizard, result, live, submission = self._pending()
        self._expire(live, submission)
        self.assertIn("VALID_APPROVAL_REQUIRED", live.preflight(wizard.id, result.submission_id, self.company_id).blockers)
        service = ListingWizardReapprovalService(self.db)
        status = service.status(wizard.id, result.submission_id, self.company_id)
        self.assertEqual((status["state"], status["can_reapprove"]), ("EXPIRED", True))
        renewed = service.reapprove(wizard.id, result.submission_id, self.company_id, 99)
        self.assertEqual(renewed["status"], ApprovalStatus.APPROVED)
        expires = datetime.fromisoformat(renewed["expires_at"])
        self.assertAlmostEqual((expires - datetime.utcnow()).total_seconds(), REAPPROVAL_TTL.total_seconds(), delta=30)
        self.assertNotIn("VALID_APPROVAL_REQUIRED", live.preflight(wizard.id, result.submission_id, self.company_id).blockers)
        self.assertEqual(service.status(wizard.id, result.submission_id, self.company_id)["state"], "VALID")

    def test_a_wizard_changed_after_the_step_8_approval_cannot_be_renewed(self):
        wizard, result, live, submission = self._pending()
        self._expire(live, submission)
        draft = json.loads(wizard.draft_json)
        draft["product_name"] = "승인 이후 바뀐 상품명"
        wizard.draft_json = json.dumps(draft, ensure_ascii=False)
        self.db.commit()
        service = ListingWizardReapprovalService(self.db)
        status = service.status(wizard.id, result.submission_id, self.company_id)
        self.assertFalse(status["can_reapprove"])
        self.assertIn("WIZARD_APPROVAL_CHANGED", status["blockers"])
        with self.assertRaises(ConflictException):
            service.reapprove(wizard.id, result.submission_id, self.company_id, 99)

    def test_a_submission_that_already_started_sending_cannot_be_renewed(self):
        wizard, result, live, submission = self._pending()
        self._expire(live, submission)
        submission.correlation_id = "already-claimed"
        self.db.commit()
        service = ListingWizardReapprovalService(self.db)
        status = service.status(wizard.id, result.submission_id, self.company_id)
        self.assertIn("SUBMISSION_ALREADY_SENT_OR_CLAIMED", status["blockers"])
        with self.assertRaises(ConflictException):
            service.reapprove(wizard.id, result.submission_id, self.company_id, 99)

    def test_a_revoked_approval_is_not_brought_back(self):
        wizard, result, live, submission = self._pending()
        approval = self._latest(live, submission)
        approval.status = ApprovalStatus.REVOKED
        self.db.commit()
        service = ListingWizardReapprovalService(self.db)
        status = service.status(wizard.id, result.submission_id, self.company_id)
        self.assertEqual((status["state"], status["can_reapprove"]), ("OTHER", False))
        with self.assertRaises(ConflictException):
            service.reapprove(wizard.id, result.submission_id, self.company_id, 99)


class WiringTests(unittest.TestCase):

    def test_the_endpoint_checks_the_password_first_and_writes_an_audit_log(self):
        body = ROUTER.split('def reapprove_submission(')[1].split('@router.post(\n    "/{wizard_id}/submissions/{submission_id}/send-live"')[0]
        self.assertIn("Depends(SuperAdminGuard)", body)
        self.assertLess(body.index("consume_recent_auth_token("), body.index(".reapprove("))
        self.assertIn('action="WIZARD_FINAL_APPROVAL_RENEWED"', body)

    def test_the_status_endpoint_is_read_only_and_calls_no_provider(self):
        body = ROUTER.split("def reapproval_status(")[1].split("@router.post")[0]
        self.assertNotIn("provider", body)
        self.assertNotIn("recent_auth", body)

    def test_the_service_makes_no_external_call(self):
        source = (ROOT / "app/domains/marketplace_listing/listing_wizard_reapproval_service.py").read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "provider", "httpx", "send("):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_screen_confirms_then_asks_for_the_password_before_the_request(self):
        body = CONSOLE.split("function lwAttachReapproval(")[1].split("async function lwRenderResultsStep(")[0]
        self.assertLess(body.index("await askConfirm("), body.index("await promptRecentAuthToken()"))
        self.assertLess(body.index("await promptRecentAuthToken()"), body.index("/reapprove`"))
        self.assertIn("X-Recent-Auth-Token", body)
        self.assertIn("lwAttachReapproval(content);", CONSOLE)

    def test_the_server_text_is_escaped_on_the_screen(self):
        body = CONSOLE.split("function lwAttachReapproval(")[1].split("async function lwRenderResultsStep(")[0]
        self.assertNotIn("${err.message}", body)
        self.assertNotIn("innerHTML = status", body)

    def test_the_texts_match_in_both_languages(self):
        import re

        ko = set(re.findall(r'"(lw\.reapprove_[a-z_]+)":', KO))
        en = set(re.findall(r'"(lw\.reapprove_[a-z_]+)":', EN))
        self.assertEqual(ko, en)
        used = set(re.findall(r'HomezI18n\.t\("(lw\.reapprove_[a-z_]+)"', CONSOLE))
        self.assertFalse(used - ko, used - ko)


def load_tests(loader, standard_tests, pattern):
    """기반 클래스(위저드 라이브 시험 환경)의 수백 개 시험을 물려받아 함께 돌리지 않도록, 이 모듈이 직접 정의한 시험만 싣는다."""

    suite = unittest.TestSuite()
    for cls in (ReapprovalServiceTests, WiringTests):
        names = [f"{cls.__name__}.{name}" for name in cls.__dict__ if name.startswith("test_")]
        suite.addTests(loader.loadTestsFromNames(names, __import__(__name__, fromlist=["x"])))
    return suite


if __name__ == "__main__":
    unittest.main()
