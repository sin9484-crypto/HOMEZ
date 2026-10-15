"""
=========================================================
Homez OS

File : tests/test_listing_wizard_list_enrichment.py

위저드 목록 보강(2026-10-15, UI 개선 3단계)을 고정한다 — 상품명·대표 사진·공급처·채널·단계 번호·확인사항.
읽기 전용이며 저장된 값에서만 계산한다(없으면 비움). 임시 SQLite만 쓰고 외부 호출은 없다.
=========================================================
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_list_enrichment as enrich
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.constants import WizardStep
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.model import ProductCandidate
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)


def fake(status, step, **_):
    return SimpleNamespace(status=status, current_step=step)


class PureTests(unittest.TestCase):

    def test_step_numbers_follow_the_ten_stage_order(self):
        self.assertEqual([enrich.step_number(s) for s in ("SOURCE", "FULFILLMENT", "RESULTS")], [1, 5, 10])
        self.assertIsNone(enrich.step_number("NOPE"))

    def codes(self, status, step, selected=None, selections=None, economics=None):
        return enrich.attention_codes(
            fake(status, step), selected_media=selected or [], channel_selections=selections or [], economics=economics or [],
        )

    def test_a_skipped_stage_with_no_result_is_flagged(self):
        self.assertEqual(self.codes(WizardStatus.DRAFT, WizardStep.CHANNELS), ["NO_IMAGE_SELECTED"])
        self.assertEqual(self.codes(WizardStatus.DRAFT, WizardStep.FULFILLMENT, selected=[1]), ["NO_CHANNEL"])
        self.assertEqual(
            self.codes(WizardStatus.DRAFT, WizardStep.PRECHECK, selected=[1], selections=[{"marketplace_account_id": 1}]),
            ["NO_ECONOMICS"],
        )

    def test_the_current_stage_itself_is_not_flagged_yet(self):
        self.assertEqual(self.codes(WizardStatus.DRAFT, WizardStep.MEDIA), [])
        self.assertEqual(self.codes(WizardStatus.DRAFT, WizardStep.ECONOMICS, selected=[1], selections=[{"x": 1}]), [])

    def test_state_based_codes(self):
        self.assertEqual(self.codes(WizardStatus.NEEDS_CORRECTION, WizardStep.MEDIA), ["NEEDS_CORRECTION"])
        self.assertEqual(self.codes(WizardStatus.FAILED, WizardStep.RESULTS), ["SUBMIT_FAILED"])
        self.assertEqual(self.codes(WizardStatus.PARTIALLY_SUCCEEDED, WizardStep.RESULTS), ["PARTIALLY_SUCCEEDED"])
        self.assertEqual(self.codes(WizardStatus.APPROVED, WizardStep.EXECUTION), ["READY_TO_SUBMIT"])
        self.assertEqual(
            self.codes(WizardStatus.READY_FOR_APPROVAL, WizardStep.APPROVAL, selected=[1], selections=[{"x": 1}], economics=[{"x": 1}]),
            ["AWAITING_APPROVAL"],
        )

    def test_a_finished_wizard_has_nothing_to_check(self):
        self.assertEqual(self.codes(WizardStatus.SUCCEEDED, WizardStep.RESULTS), [])


class DatabaseTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.engine = create_engine(f"sqlite:///{self.path}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, ProductCandidate.__table__, MarketplaceChannel.__table__, MarketplaceAccount.__table__,
            MediaAsset.__table__, ListingWizard.__table__,
        ])
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)

    def _company(self, name):
        company = Company(name=name, business_number="1", ceo="c", phone="0", email=f"{name}@x.com", address="a")
        self.db.add(company)
        self.db.commit()
        return company.id

    def _candidate(self, company_id, name, source="ONCHANNEL:CH1"):
        candidate = ProductCandidate(
            candidate_key=f"k-{name}-{company_id}", source_type="PRIVATE_MANUAL", source_reference=source, market="COUPANG",
            product_name=name, status="DISCOVERED", visibility="PRIVATE", owner_company_id=company_id,
        )
        self.db.add(candidate)
        self.db.commit()
        return candidate.id

    _asset_seq = 0

    def _asset(self, company_id, candidate_id, purpose="MAIN", role="ORIGINAL", status="ACTIVE"):
        DatabaseTests._asset_seq += 1
        n = DatabaseTests._asset_seq
        asset = MediaAsset(
            company_id=company_id, owner_type="PRODUCT_CANDIDATE", owner_id=candidate_id, asset_role=role, purpose=purpose,
            display_order=1, storage_path=f"p{n}", mime_type="image/png", file_size_bytes=1, sha256_hex=f"{n:064x}", status=status,
            rights_status="RIGHTS_UNVERIFIED",
        )
        self.db.add(asset)
        self.db.commit()
        return asset.id

    def _wizard(self, company_id, candidate_id, **kw):
        wizard = ListingWizard(
            company_id=company_id, created_by_user_id=1, current_step=kw.get("step", "MEDIA"), status=kw.get("status", "DRAFT"),
            source_type="MANUAL", product_candidate_id=candidate_id, draft_json=json.dumps(kw.get("draft", {}), ensure_ascii=False),
            selected_media_asset_ids_json=json.dumps(kw.get("selected", [])),
            channel_selections_json=json.dumps(kw.get("selections", [])), economics_input_json=json.dumps(kw.get("economics", [])),
            version=1, creation_idempotency_key=f"k-{candidate_id}-{kw.get('key', 0)}",
        )
        self.db.add(wizard)
        self.db.commit()
        return wizard

    def test_name_supplier_thumbnail_and_channel_are_filled_from_stored_values(self):
        company = self._company("a")
        cand = self._candidate(company, "후보 상품명")
        asset = self._asset(company, cand)
        channel = MarketplaceChannel(code="COUPANG", name="쿠팡")
        self.db.add(channel)
        self.db.commit()
        account = MarketplaceAccount(company_id=company, channel_id=channel.id, account_code="c1", account_name="에브리홈즈")
        self.db.add(account)
        self.db.commit()
        wizard = self._wizard(company, cand, step="FULFILLMENT", draft={"product_name": "초안 상품명"}, selected=[asset],
                              selections=[{"marketplace_account_id": account.id}])
        result = enrich.enrich_list_items(self.db, company, [wizard])[wizard.id]
        self.assertEqual(result["product_name"], "초안 상품명")  # 초안 이름이 후보 이름보다 우선
        self.assertEqual(result["supplier_label"], "온채널")
        self.assertEqual(result["thumbnail_asset_id"], asset)
        self.assertEqual(result["channels"], [{"code": "COUPANG", "account_name": "에브리홈즈"}])
        self.assertEqual((result["step_number"], result["step_total"]), (5, 10))

    def test_the_candidate_name_and_first_original_image_are_the_fallback(self):
        company = self._company("b")
        cand = self._candidate(company, "후보 상품명", source="URL:example.com:abc")
        first = self._asset(company, cand)
        self._asset(company, cand)
        wizard = self._wizard(company, cand, step="SOURCE", draft={})
        result = enrich.enrich_list_items(self.db, company, [wizard])[wizard.id]
        self.assertEqual(result["product_name"], "후보 상품명")
        self.assertIsNone(result["supplier_label"])
        self.assertEqual(result["thumbnail_asset_id"], first)

    def test_another_companys_or_inactive_image_is_never_used(self):
        mine, other = self._company("c"), self._company("d")
        cand = self._candidate(mine, "상품")
        foreign = self._asset(other, cand)           # 다른 회사 소유
        inactive = self._asset(mine, cand, status="DELETED")
        wizard = self._wizard(mine, cand, step="DRAFT", selected=[foreign, inactive])
        result = enrich.enrich_list_items(self.db, mine, [wizard])[wizard.id]
        self.assertIsNone(result["thumbnail_asset_id"])

    def test_a_wizard_with_nothing_stored_gets_empty_values_not_guesses(self):
        company = self._company("e")
        wizard = self._wizard(company, None, step="SOURCE")
        result = enrich.enrich_list_items(self.db, company, [wizard])[wizard.id]
        self.assertIsNone(result["product_name"])
        self.assertEqual(result["channels"], [])
        self.assertIsNone(result["thumbnail_asset_id"])

    def test_many_wizards_are_enriched_with_a_fixed_number_of_queries(self):
        company = self._company("f")
        wizards = []
        for i in range(12):
            cand = self._candidate(company, f"상품{i}")
            self._asset(company, cand)
            wizards.append(self._wizard(company, cand, key=i))
        for w in wizards:  # 커밋으로 만료된 객체를 미리 읽어 두어, 측정이 보강 코드의 조회만 세게 한다.
            self.db.refresh(w)
        statements = []
        from sqlalchemy import event
        event.listen(self.engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
        result = enrich.enrich_list_items(self.db, company, wizards)
        self.assertEqual(len(result), 12)
        self.assertLessEqual(len(statements), 4, "행마다 조회하지 않아야 한다")


class WiringTests(unittest.TestCase):

    def test_the_list_route_enriches_without_changing_the_item_builder(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        router = (root / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
        body = router.split("def list_wizards(")[1].split('@router.get("/export.csv")')[0]
        self.assertIn("enrich_list_items(db, current_user.company_id, wizards)", body)
        self.assertIn("model_copy(update=", body)
        schema = (root / "app/domains/marketplace_listing/listing_wizard_schema.py").read_text(encoding="utf-8")
        item = schema.split("class WizardListItem")[1].split("class WizardDetailResponse")[0]
        for field in ("product_name", "supplier_label", "channels", "step_number", "thumbnail_asset_id", "attention"):
            self.assertRegex(item, field + r"\b.*=", f"{field}에 기본값이 있어야 기존 호출자가 깨지지 않는다")


if __name__ == "__main__":
    unittest.main()
