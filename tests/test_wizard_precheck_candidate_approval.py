"""
=========================================================
Homez OS

File : tests/test_wizard_precheck_candidate_approval.py

상품 후보 승인 단계 결함 수정(2026-10-06) 집중 테스트.

1) 위저드 사전검사: 후보가 "이 회사 기준"으로 승인되지 않았으면 8단계 승인 전에
   CANDIDATE_NOT_APPROVED(차단)로 알려 준다. 제출 단계에서야 실패하던 것을 앞당긴다.
2) 후보 상세 화면: 상태 표시와 결정 버튼 활성 여부가 전역 status가 아니라
   이 회사 관점 상태(company_status)를 우선한다.

임시 SQLite 파일 DB만 쓴다 — 실제 homez.db는 사용하지 않는다.
=========================================================
"""

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
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

CONSOLE_JS = (
    Path(__file__).resolve().parents[1] / "app" / "web" / "console.js"
)


class WizardPrecheckCandidateApprovalTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
            ],
        )
        self.db = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )()
        company = Company(
            name="후보 승인 테스트 회사", business_number="666-66-66666",
            ceo="테스트", phone="02-000-0000", email="cand@example.com",
            address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        # 이 테스트는 후보 승인 항목만 본다 — 다른 사전검사 항목은 비어 있는 위저드로 충분하다.
        # 제한 모드·긴급 중지 신호는 이 테스트의 관심사가 아니므로 고정한다(각자의
        # 정확성은 해당 도메인 테스트가 전담한다).
        for patcher in (
            mock.patch.object(
                listing_wizard_precheck, "is_restricted_mode",
                return_value=False,
            ),
            mock.patch.object(
                listing_wizard_precheck.SafetyService,
                "is_emergency_stop_active", return_value=False,
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _candidate(self, status):
        candidate = ProductCandidate(
            candidate_key="precheck:COUPANG:CAND-A", source_type="TREND",
            source_reference="CAND-A", market="COUPANG",
            product_name="후보 승인 테스트 상품", status=status,
        )
        self.db.add(candidate)
        self.db.commit()
        return candidate

    def _wizard(self, candidate_id):
        return ListingWizard(
            company_id=self.company_id, created_by_user_id=1,
            current_step="PRECHECK", status="DRAFT", source_type="CANDIDATE",
            product_candidate_id=candidate_id,
        )

    def _issue_codes(self, wizard):
        result = listing_wizard_precheck.run_precheck(
            wizard, self.db, self.company_id,
        )
        return {issue.code: issue for issue in result.issues}

    def test_unapproved_candidate_is_blocked_before_approval_step(self):
        candidate = self._candidate(CandidateStatus.ANALYZED)
        issues = self._issue_codes(self._wizard(candidate.id))
        issue = issues["CANDIDATE_NOT_APPROVED"]
        self.assertTrue(issue.blocking)
        self.assertEqual(issue.params["status"], CandidateStatus.ANALYZED)
        self.assertEqual(
            issue.localized_message_key,
            "listing_wizard.precheck.candidate_not_approved",
        )

    def test_company_approval_clears_the_block_even_though_global_status_stays(self):
        candidate = self._candidate(CandidateStatus.ANALYZED)
        self.db.add(ProductCandidateSelection(
            candidate_id=candidate.id, company_id=self.company_id,
            status=CandidateStatus.APPROVED, memo="회사 승인",
        ))
        self.db.commit()
        self.db.refresh(candidate)
        # 전역 상태는 그대로 ANALYZED — 회사 관점 승인만 있다.
        self.assertEqual(candidate.status, CandidateStatus.ANALYZED)
        self.assertNotIn(
            "CANDIDATE_NOT_APPROVED", self._issue_codes(self._wizard(candidate.id)),
        )

    def test_held_or_rejected_company_decision_stays_blocked(self):
        for decision in (CandidateStatus.HELD, CandidateStatus.REJECTED):
            with self.subTest(decision=decision):
                candidate = self._candidate(CandidateStatus.ANALYZED)
                candidate.candidate_key = f"precheck:COUPANG:{decision}"
                self.db.add(ProductCandidateSelection(
                    candidate_id=candidate.id, company_id=self.company_id,
                    status=decision, memo="x",
                ))
                self.db.commit()
                issues = self._issue_codes(self._wizard(candidate.id))
                self.assertEqual(
                    issues["CANDIDATE_NOT_APPROVED"].params["status"], decision,
                )

    def test_wizard_without_candidate_is_not_affected(self):
        self.assertNotIn(
            "CANDIDATE_NOT_APPROVED", self._issue_codes(self._wizard(None)),
        )

    def test_candidate_detail_screen_uses_company_status_first(self):
        source = CONSOLE_JS.read_text(encoding="utf-8")
        self.assertIn(
            "const effectiveStatus = candidate.company_status || candidate.status;",
            source,
        )
        self.assertIn(
            '["ANALYZED", "RECOMMENDED", "HELD"].includes(effectiveStatus)',
            source,
        )
        # 전역 status만으로 결정 가능 여부를 판정하는 옛 줄이 남아 있으면 안 된다.
        self.assertNotIn(
            '["ANALYZED", "RECOMMENDED", "HELD"].includes(candidate.status)',
            source,
        )


if __name__ == "__main__":
    unittest.main()
