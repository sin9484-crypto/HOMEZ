"""
=========================================================
Homez OS

File : tests/test_wizard_improvements_20261012.py

백산수 쿠팡 등록(2026-10-06~12)에서 겪은 문제의 재발 방지·편의 개선 5건을 고정한다.

1) 자동화 모드 한글 표시(상단 칩·안전 화면·변경 확인창)
2) 5단계 GTIN/MPN 입력칸과 저장 연결
3) 브랜드 상품 식별번호(UID) 사전검사
4) 쿠팡 등록 확인에서 승인반려 안내
5) 삭제 차단 사유를 실제 제출 상태로 설명

임시 DB 없이 순수 함수·정적 파일만 검사한다.
=========================================================
"""

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.domains.marketplace_listing.listing_wizard_precheck import (
    entries_missing_brand_uid,
)
from app.domains.marketplace_listing.listing_wizard_service import deletion_eligibility

APP = Path(__file__).resolve().parents[1] / "app"
CONSOLE = (APP / "web" / "console.js").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")


class ModeLabelTests(unittest.TestCase):

    MODES = ("RECOMMEND_ONLY", "OPERATOR_APPROVAL", "LIMITED_AUTOMATION", "DISABLED")

    def test_all_four_modes_have_korean_and_english_labels(self):
        for mode in self.MODES:
            with self.subTest(mode=mode):
                self.assertIn(f'"safety.mode_label.{mode.lower()}"', KO)
                self.assertIn(f'"safety.mode_label.{mode.lower()}"', EN)

    def test_screens_use_the_label_helper_not_the_raw_code(self):
        self.assertIn("function safetyModeLabel(mode)", CONSOLE)
        self.assertIn("escapeHtml(safetyModeLabel(data.mode))", CONSOLE)
        self.assertIn("escapeHtml(safetyModeLabel(m))", CONSOLE)
        self.assertIn('{ mode: safetyModeLabel(safety.mode) }', CONSOLE)
        self.assertIn("from: safetyModeLabel(data.mode), to: safetyModeLabel(newMode)", CONSOLE)
        # 옛 코드 그대로 출력하던 형태가 남아 있으면 안 된다.
        self.assertNotIn("<dd>${escapeHtml(data.mode)}</dd>", CONSOLE)

    def test_option_values_stay_as_codes_for_the_server(self):
        # 표시는 한글이지만 서버로 보내는 값(value)은 코드 그대로여야 한다.
        self.assertIn('<option value="${m}" ${m === data.mode ? "selected" : ""}>', CONSOLE)


class UidInputTests(unittest.TestCase):

    def test_gtin_and_mpn_inputs_are_saved_into_required_fields(self):
        self.assertIn('class="lw-uid-gtin"', CONSOLE)
        self.assertIn('class="lw-uid-mpn"', CONSOLE)
        self.assertIn("requiredFields.globalTradeItemNumber = uidGtin;", CONSOLE)
        self.assertIn("requiredFields.manufacturerPartNumber = uidMpn;", CONSOLE)
        self.assertIn("delete requiredFields.globalTradeItemNumber;", CONSOLE)
        self.assertIn("delete requiredFields.manufacturerPartNumber;", CONSOLE)
        # 미저장 입력 보존 목록에도 포함
        self.assertIn('".lw-uid-gtin", ".lw-uid-mpn"', CONSOLE)

    def test_labels_exist_in_both_locales(self):
        for key in ("lw.uid_gtin", "lw.uid_gtin_placeholder", "lw.uid_hint", "lw.uid_mpn"):
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', KO)
                self.assertIn(f'"{key}"', EN)


class BrandUidPrecheckTests(unittest.TestCase):

    def _entry(self, **fields):
        return {"marketplace_account_id": 1, "required_fields": fields}

    def test_official_brand_without_uid_is_flagged(self):
        self.assertEqual(len(entries_missing_brand_uid([self._entry(brandState="OFFICIAL_BRAND")])), 1)

    def test_official_brand_with_gtin_or_mpn_passes(self):
        for field in ({"globalTradeItemNumber": "8801043022781"}, {"manufacturerPartNumber": "MPN-1"}):
            with self.subTest(field=field):
                self.assertEqual(entries_missing_brand_uid([self._entry(brandState="OFFICIAL_BRAND", **field)]), [])

    def test_blank_strings_count_as_missing(self):
        entry = self._entry(brandState="OFFICIAL_BRAND", globalTradeItemNumber="  ", manufacturerPartNumber="")
        self.assertEqual(len(entries_missing_brand_uid([entry])), 1)

    def test_no_brand_and_unresolved_brand_are_not_targets(self):
        for state in ("NO_BRAND", "UNRESOLVED", None):
            with self.subTest(state=state):
                self.assertEqual(entries_missing_brand_uid([self._entry(brandState=state)]), [])

    def test_empty_input_is_safe(self):
        self.assertEqual(entries_missing_brand_uid([]), [])
        self.assertEqual(entries_missing_brand_uid(None), [])

    def test_message_exists_in_both_locales(self):
        self.assertIn('"listing_wizard.precheck.brand_uid_required"', KO)
        self.assertIn('"listing_wizard.precheck.brand_uid_required"', EN)


class DeniedHintTests(unittest.TestCase):

    def test_denied_status_shows_where_to_read_the_reason(self):
        self.assertIn('/반려|denied/i.test(String(m.status_name || ""))', CONSOLE)
        self.assertIn('HomezI18n.t("lw.regcheck_denied_hint")', CONSOLE)
        self.assertIn("WING", KO.split('"lw.regcheck_denied_hint"')[1][:160])
        self.assertIn('"lw.regcheck_denied_hint"', EN)


class DeletionReasonTests(unittest.TestCase):

    def _wizard(self, status, entries):
        import json
        return SimpleNamespace(status=status, materialized_listing_ids_json=json.dumps(entries))

    def test_submitted_is_described_as_accepted_by_coupang(self):
        ok, reason = deletion_eligibility(self._wizard("SUCCEEDED", [{"status": "SUBMITTED"}]))
        self.assertFalse(ok)
        self.assertIn("접수된 상품", reason)

    def test_unknown_or_submitting_asks_to_check_registration_first(self):
        for status in ("UNKNOWN", "SUBMITTING"):
            with self.subTest(status=status):
                ok, reason = deletion_eligibility(self._wizard("SUCCEEDED", [{"status": status}]))
                self.assertFalse(ok)
                self.assertIn("등록 여부를 먼저 확인", reason)

    def test_pending_or_failed_no_longer_claims_it_is_registered(self):
        for status in ("PENDING", "FAILED"):
            with self.subTest(status=status):
                ok, reason = deletion_eligibility(self._wizard("SUCCEEDED", [{"status": status}]))
                self.assertFalse(ok)  # 삭제 차단(fail-closed)은 그대로
                self.assertNotIn("이미 등록된 상품입니다", reason)
                self.assertIn("아직 등록되지 않았을 수 있습니다", reason)

    def test_draft_with_submission_record_is_still_blocked(self):
        ok, reason = deletion_eligibility(self._wizard("DRAFT", [{"status": "FAILED"}]))
        self.assertFalse(ok)
        self.assertIn("제출 기록", reason)

    def test_clean_draft_is_deletable_and_approved_keeps_its_own_reason(self):
        self.assertEqual(deletion_eligibility(self._wizard("DRAFT", [])), (True, None))
        ok, reason = deletion_eligibility(self._wizard("APPROVED", []))
        self.assertFalse(ok)
        self.assertIn("승인된 항목", reason)


if __name__ == "__main__":
    unittest.main()
