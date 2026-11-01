"""
2026-11-01 — 사용자 결정 D4: 온채널 상품 등록일을 그대로 출시일로 쓴다(가짜 온채널, 바깥 호출 0).

고정하는 약속
  - 출시일이 빈, 이 회사 소유의 온채널 후보에만 넣는다. 사람이 적은 출시일·다른 회사·온채널이 아닌 후보는 건드리지 않는다.
  - 목록에 없는 상품은 '모름' 그대로. 날짜를 읽을 수 없거나 미래면 저장하지 않는다(자동 보정 없음).
  - 목록 한 쪽마다 승인 센터 허용 1회. 허용·연결이 없으면 호출하지 않고 이유를 돌려준다. 후보가 없으면 묻지도 않는다.
  - 출처는 근거 기록에 남기고 탐색 흐름에 '온채널 등록일 기준'으로 보인다. 자동 실행은 채운 뒤 15일 규칙까지 이어 간다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
import json
from datetime import date
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — 모든 모델을 메타데이터에 올린다
from app.database.base import Base
from app.domains.automation_safety.service import SafetyService
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.product_candidate import release_date_fill_service as fill_mod
from app.domains.product_candidate.discovery_auto_run_service import DiscoveryAutoRunService
from app.domains.product_candidate.discovery_flow_service import DiscoveryFlowService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.release_date_fill_service import ReleaseDateFillService
from app.domains.product_candidate.release_date_fill_service import parse_created_at
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.purchase_task.channel_adapter import ChannelProductSummary
from app.domains.purchase_task.channel_adapter import OnchannelChannelAdapter
from app.domains.purchase_task.channel_adapter import ProductListLookupResult
from app.domains.purchase_task.onchannel_client import OnchannelApiClient
from tests.test_easy_briefing_ui import HARNESS
from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import NOW
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

A, B = 1, 2
TODAY_UTC = datetime(2026, 11, 1, 3, 0)  # 한국 시각 11-01 12:00

_TEMPLATE = {}


def _template_db() -> str:
    if "path" not in _TEMPLATE:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "template.db")
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at DATETIME)"))
        engine.dispose()
        _TEMPLATE["path"], _TEMPLATE["folder"] = path, folder
    return _TEMPLATE["path"]


def tearDownModule():
    if "folder" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["folder"], ignore_errors=True)
    _TEMPLATE.clear()


class FakeConnections:
    """온채널 연결 서비스 흉내 — 쪽마다 정해 둔 (코드, 등록일) 목록을 돌려주고 호출을 기록한다."""

    def __init__(self, pages=(), active=True, fail_on_page=None):
        self.pages, self.active, self.fail_on_page = list(pages), active, fail_on_page
        self.calls = []

    def list_connections(self, company_id, mall_code=None):
        self.asked = (company_id, mall_code)
        return [SimpleNamespace(id=5, is_active=True)] if self.active else [SimpleNamespace(id=5, is_active=False)]

    def list_products(self, connection_id, company_id, *, page, page_size, triggered_by=None):
        self.calls.append({"connection": connection_id, "company": company_id, "page": page, "size": page_size, "by": triggered_by})
        if page == self.fail_on_page:
            raise ConnectionError("비밀 응답 원문")
        rows = self.pages[page - 1] if page <= len(self.pages) else []
        items = tuple(ChannelProductSummary(external_product_id=code, title="t", created_at=at) for code, at in rows)
        return ProductListLookupResult(support="SUPPORTED", total_returned=len(items), items=items, detail="")


def filler(count):
    """쪽을 가득 채우는 다른 상품들(다음 쪽을 읽게 한다)."""

    return [(f"OTHER{i}", "2020-01-01 00:00:00") for i in range(count)]


class DbCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copyfile(_template_db(), f"{self.tmp}/t.db")
        self.engine = create_engine(f"sqlite:///{self.tmp}/t.db", connect_args={"check_same_thread": False})
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self, code, company_id=A, release=None, reference=None, name=None):
        candidate, _ = ProductCandidateService(self.db).register_private_candidate(
            ProductCandidatePrivateCreate(source_reference=reference or f"ONCHANNEL:{code}", market="COUPANG", product_name=name or f"상품 {code}", release_date=release),
            company_id=company_id, correlation_id="c",
        )
        return candidate.id

    def allow(self, count=10, company_id=A):
        return ExternalCallPolicyService(self.db).grant(
            company_id, system=ExternalSystem.ONCH, kind=CallKind.READ, purpose=CallPurpose.SUPPLIER_RELEASE_DATE,
            mode=AllowanceMode.COUNTED, count=count, user_id=1)

    def service(self, connections):
        return ReleaseDateFillService(self.db, connections, now_factory=lambda: TODAY_UTC)

    def release(self, candidate_id):
        self.db.expire_all()
        return self.db.get(ProductCandidate, candidate_id).release_date

    def marks(self, candidate_id):
        return [r.payload_summary for r in self.db.query(ProductCandidateEvidence).filter_by(candidate_id=candidate_id).all() if r.payload_summary.startswith(fill_mod.SOURCE_MARK)]


class ParseTests(unittest.TestCase):

    def test_only_a_real_date_is_read(self):
        self.assertEqual(parse_created_at("2022-08-09 15:48:26"), date(2022, 8, 9))
        self.assertEqual(parse_created_at("2022-08-09"), date(2022, 8, 9))
        self.assertEqual(parse_created_at(" 2022-08-09T15:48:26 "), date(2022, 8, 9))
        for bad in (None, "", "어제", "2022-13-01 00:00:00", "2022-02-30", "20220809", "2022-08-09x", "0000-00-00 00:00:00"):
            self.assertIsNone(parse_created_at(bad), bad)


class ClientTests(unittest.TestCase):

    def test_the_list_keeps_the_registration_date_and_detail_stays_empty(self):
        client = OnchannelApiClient("k" * 20)
        body = {"result": {"items": [
            {"product_code": "CH1", "name": "a", "status": 1, "created_at": "2022-08-09 15:48:26", "options": []},
            {"product_code": "CH2", "name": "b", "status": 1, "created_at": 20220809, "options": []},
            {"product_code": "CH3", "name": "c", "status": 1, "options": []},
        ]}}
        with mock.patch.object(OnchannelApiClient, "_get", return_value=body):
            products = client.list_products(page=1, page_size=100)
        self.assertEqual([p.created_at for p in products], ["2022-08-09 15:48:26", None, None])

    def test_the_adapter_passes_it_on(self):
        adapter = OnchannelChannelAdapter.__new__(OnchannelChannelAdapter)
        product = SimpleNamespace(product_code="CH1", title="a", created_at="2022-08-09 15:48:26")
        with mock.patch.object(OnchannelChannelAdapter, "_require_client", return_value=SimpleNamespace(list_products=lambda **kw: (product,))):
            result = adapter.list_products(page=1, page_size=100)
        self.assertEqual(result.items[0].created_at, "2022-08-09 15:48:26")


class FillTests(DbCase):

    def test_the_date_is_filled_with_its_source_recorded(self):
        one = self.seed("CH1")
        allowance = self.allow()
        fake = FakeConnections([[("CH1", "2026-10-25 09:00:00")]])
        out = self.service(fake).run(A, actor_user_id=7)
        self.assertEqual(self.release(one), date(2026, 10, 25))
        self.assertEqual((out["filled"], out["filled_ids"], out["pages_read"], out["blocked"], out["not_found"]), (1, [one], 1, None, 0))
        self.assertEqual(fake.calls, [{"connection": 5, "company": A, "page": 1, "size": 100, "by": 7}])
        self.assertEqual(fake.asked, (A, "ONCHANNEL"))
        self.assertEqual(self.marks(one), [f"{fill_mod.SOURCE_MARK} code=CH1 created_at=2026-10-25 09:00:00"])
        self.db.expire_all()
        self.assertEqual(self.db.get(ExternalCallAllowance, allowance.id).remaining_count, 9)
        log = self.db.query(ExternalCallLog).filter_by(outcome="SUCCEEDED").one()
        self.assertEqual((log.system, log.kind, log.purpose, log.target), ("ONCH", "READ", "SUPPLIER_RELEASE_DATE", "seller/product?page=1"))
        audit = self.db.execute(text("SELECT action, company_id, user_id FROM audit_logs WHERE action = 'RELEASE_DATE_FILLED'")).fetchall()
        self.assertEqual([tuple(r) for r in audit], [("RELEASE_DATE_FILLED", A, 7)])

    def test_two_candidates_of_one_product_both_fill(self):
        one, two = self.seed("CH1", name="가"), self.seed("CH1", name="나", reference="ONCHANNEL:CH1 ")
        self.allow()
        out = self.service(FakeConnections([[("CH1", "2026-10-25 09:00:00")]])).run(A)
        self.assertEqual(sorted(out["filled_ids"]), sorted([one, two]))

    def test_a_written_date_other_companies_and_other_sources_are_left_alone(self):
        written = self.seed("CH1", release=date(2020, 1, 1))
        other_company = self.seed("CH2", company_id=B)
        not_onchannel = self.seed("CH3", reference="URL:CH3")
        mine = self.seed("CH4")
        self.allow()
        rows = [("CH1", "2026-10-25 00:00:00"), ("CH2", "2026-10-25 00:00:00"), ("CH3", "2026-10-25 00:00:00"), ("CH4", "2026-10-25 00:00:00")]
        out = self.service(FakeConnections([rows])).run(A)
        self.assertEqual(out["filled_ids"], [mine])
        self.assertEqual(self.release(written), date(2020, 1, 1))
        self.assertIsNone(self.release(other_company))
        self.assertIsNone(self.release(not_onchannel))
        self.assertEqual(self.marks(written), [])

    def test_a_product_not_in_the_list_stays_unknown(self):
        one = self.seed("CH1")
        self.allow()
        out = self.service(FakeConnections([[("CH9", "2026-10-25 00:00:00")]])).run(A)
        self.assertIsNone(self.release(one))
        self.assertEqual((out["filled"], out["not_found"]), (0, 1))

    def test_a_future_or_unreadable_date_is_not_saved_or_fixed(self):
        future, tomorrow_kst, bad, none, today_kst = self.seed("CH1"), self.seed("CH2"), self.seed("CH3"), self.seed("CH4"), self.seed("CH5")
        self.allow()
        rows = [("CH1", "2027-01-01 00:00:00"), ("CH2", "2026-11-02 00:00:00"), ("CH3", "어제"), ("CH4", None), ("CH5", "2026-11-01 11:00:00")]
        out = self.service(FakeConnections([rows])).run(A)
        for candidate_id in (future, tomorrow_kst, bad, none):
            self.assertIsNone(self.release(candidate_id))
        self.assertEqual(self.release(today_kst), date(2026, 11, 1))
        self.assertEqual((out["future"], out["unreadable"], out["filled"], out["not_found"]), (2, 2, 1, 0))

    def test_the_kst_day_decides_what_is_future(self):
        one = self.seed("CH1")
        self.allow()
        late_utc = ReleaseDateFillService(self.db, FakeConnections([[("CH1", "2026-11-02 01:00:00")]]), now_factory=lambda: datetime(2026, 11, 1, 16, 0))  # 한국 11-02 01:00
        self.assertEqual(late_utc.run(A)["filled"], 1)
        self.assertEqual(self.release(one), date(2026, 11, 2))

    def test_pages_are_read_until_every_product_is_found_or_the_list_ends(self):
        one, two = self.seed("CH1"), self.seed("CH2")
        allowance = self.allow()
        fake = FakeConnections([filler(99) + [("CH1", "2026-10-25 00:00:00")], [("CH2", "2026-10-26 00:00:00")] + filler(99), filler(100)])
        out = self.service(fake).run(A)
        self.assertEqual([c["page"] for c in fake.calls], [1, 2])  # 다 찾으면 멈춘다
        self.assertEqual((self.release(one), self.release(two)), (date(2026, 10, 25), date(2026, 10, 26)))
        self.db.expire_all()
        self.assertEqual(self.db.get(ExternalCallAllowance, allowance.id).remaining_count, 8)

        three = self.seed("CH3")
        fake = FakeConnections([filler(100), filler(3)])
        out = self.service(fake).run(A)
        self.assertEqual([c["page"] for c in fake.calls], [1, 2])  # 100개보다 적은 쪽이 마지막
        self.assertEqual((out["not_found"], out["truncated"]), (1, False))
        self.assertIsNone(self.release(three))

    def test_at_most_ten_pages_are_read_at_once_and_the_rest_is_not_called_missing(self):
        self.seed("CH1")
        self.allow(20)
        fake = FakeConnections([filler(100) for _ in range(12)])
        out = self.service(fake).run(A)
        self.assertEqual(len(fake.calls), 10)
        self.assertEqual((out["truncated"], out["not_found"]), (True, 0))

    def test_without_an_allowance_nothing_is_called_and_a_request_is_raised(self):
        one = self.seed("CH1")
        fake = FakeConnections([[("CH1", "2026-10-25 00:00:00")]])
        out = self.service(fake).run(A, actor_user_id=7)
        self.assertEqual(out["blocked"], fill_mod.BLOCKED_ALLOWANCE)
        self.assertEqual(fake.calls, [])
        self.assertEqual(out["not_found"], 0)
        self.assertIsNone(self.release(one))
        pending = self.db.query(ExternalCallAllowance).filter_by(status="PENDING").one()
        self.assertEqual((pending.system, pending.kind, pending.purpose, pending.mode, pending.requested_count, pending.requested_by),
                         ("ONCH", "READ", "SUPPLIER_RELEASE_DATE", "COUNTED", 10, 7))

    def test_running_out_part_way_keeps_what_was_filled(self):
        one, two = self.seed("CH1"), self.seed("CH2")
        self.allow(1)
        fake = FakeConnections([filler(99) + [("CH1", "2026-10-25 00:00:00")], [("CH2", "2026-10-26 00:00:00")]])
        out = self.service(fake).run(A)
        self.assertEqual((out["blocked"], out["filled"], out["not_found"]), (fill_mod.BLOCKED_ALLOWANCE, 1, 0))
        self.assertEqual(self.release(one), date(2026, 10, 25))
        self.assertIsNone(self.release(two))

    def test_emergency_stop_blocks_even_with_an_allowance(self):
        self.seed("CH1")
        self.allow()
        SafetyService(self.db).activate_emergency_stop("시험", set_by=1, is_admin=True)
        fake = FakeConnections([[("CH1", "2026-10-25 00:00:00")]])
        self.assertEqual(self.service(fake).run(A)["blocked"], fill_mod.BLOCKED_ESTOP)
        self.assertEqual(fake.calls, [])

    def test_no_candidate_means_no_question_and_no_call(self):
        self.seed("CH1", release=date(2020, 1, 1))
        fake = FakeConnections([])
        out = self.service(fake).run(A)
        self.assertEqual((out["candidates"], out["blocked"], fake.calls), (0, None, []))
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)

    def test_a_candidate_from_elsewhere_alone_means_no_question_and_no_call(self):
        self.seed("x", reference="URL:shop.example.test:abcdef123456")  # 접두사만 다른 긴 참조 — 코드로 잘못 읽으면 호출이 나간다
        fake = FakeConnections([])
        out = self.service(fake).run(A)
        self.assertEqual((out["candidates"], out["blocked"], fake.calls), (0, None, []))
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)

    def test_no_active_connection_means_no_question_and_no_call(self):
        self.seed("CH1")
        fake = FakeConnections([], active=False)
        self.assertEqual(self.service(fake).run(A)["blocked"], fill_mod.BLOCKED_NO_CONNECTION)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)

    def test_a_failed_page_is_recorded_and_keeps_what_was_filled_without_the_raw_error(self):
        one, two = self.seed("CH1"), self.seed("CH2")
        self.allow()
        fake = FakeConnections([filler(99) + [("CH1", "2026-10-25 00:00:00")]], fail_on_page=2)
        with self.assertLogs("homez", level="WARNING") as logs:
            out = self.service(fake).run(A)
        self.assertEqual((out["blocked"], out["filled"], out["not_found"]), (fill_mod.BLOCKED_ERROR, 1, 0))
        self.assertEqual(self.release(one), date(2026, 10, 25))
        self.assertIsNone(self.release(two))
        self.assertEqual(self.db.query(ExternalCallLog).filter_by(outcome="FAILED").count(), 1)
        self.assertNotIn("비밀 응답 원문", "".join(logs.output))
        self.assertNotIn("비밀 응답 원문", json.dumps(out, ensure_ascii=False))

    def test_a_second_run_does_nothing(self):
        one = self.seed("CH1")
        self.allow()
        self.service(FakeConnections([[("CH1", "2026-10-25 00:00:00")]])).run(A)
        fake = FakeConnections([[("CH1", "2020-01-01 00:00:00")]])
        out = self.service(fake).run(A)
        self.assertEqual((out["candidates"], fake.calls), (0, []))
        self.assertEqual(self.release(one), date(2026, 10, 25))
        self.assertEqual(len(self.marks(one)), 1)

    def test_a_date_written_while_listing_is_not_overwritten(self):
        one = self.seed("CH1")
        service = self.service(FakeConnections([]))
        self.db.execute(text("UPDATE product_candidates SET release_date = '2020-01-01' WHERE id = :i"), {"i": one})
        self.db.commit()
        self.assertEqual(service._save([one], "CH1", "2026-10-25", date(2026, 10, 25)), [])
        self.assertEqual(self.release(one), date(2020, 1, 1))
        self.assertEqual(self.marks(one), [])


class JudgeTests(DbCase):

    def test_the_button_path_judges_what_it_filled(self):
        fresh, old = self.seed("CH1"), self.seed("CH2")
        self.allow()
        out = self.service(FakeConnections([[("CH1", "2026-10-25 09:00:00"), ("CH2", "2022-08-09 15:48:26")]])).run_and_judge(A, now=TODAY_UTC)
        self.assertEqual((out["filled"], out["judged_new"], out["judged_old"], out["judge_failed"]), (2, 1, 1, 0))
        self.db.expire_all()
        self.assertIs(self.db.get(ProductCandidate, fresh).is_new_product, True)
        self.assertIs(self.db.get(ProductCandidate, old).is_new_product, False)

    def test_a_failed_judgement_keeps_the_date(self):
        one = self.seed("CH1")
        self.allow()
        with mock.patch("app.domains.product_candidate.new_product_analysis_refresh_service.NewProductAnalysisRefreshService.refresh", side_effect=RuntimeError("x")):
            with self.assertLogs("homez", level="ERROR"):
                out = self.service(FakeConnections([[("CH1", "2026-10-25 09:00:00")]])).run_and_judge(A, now=TODAY_UTC)
        self.assertEqual((out["filled"], out["judge_failed"]), (1, 1))
        self.assertEqual(self.release(one), date(2026, 10, 25))

    def test_a_judgement_that_did_not_apply_is_not_counted_as_old(self):
        from app.domains.product_candidate.new_product_analysis_refresh_service import NewProductRefreshResult

        one = self.seed("CH1")
        self.allow()
        not_applied = NewProductRefreshResult(status="FUTURE_DATE", candidate_id=one, reason="x")
        with mock.patch("app.domains.product_candidate.new_product_analysis_refresh_service.NewProductAnalysisRefreshService.refresh", return_value=not_applied):
            out = self.service(FakeConnections([[("CH1", "2026-10-25 09:00:00")]])).run_and_judge(A, now=TODAY_UTC)
        self.assertEqual((out["judged_new"], out["judged_old"], out["judge_failed"]), (0, 0, 1))

    def test_the_flow_shows_the_source_only_for_filled_candidates(self):
        filled, written = self.seed("CH1"), self.seed("CH2", release=date(2026, 10, 20))
        self.allow()
        self.service(FakeConnections([[("CH1", "2026-10-25 09:00:00")]])).run(A)
        rows = {r["candidate_id"]: r for r in DiscoveryFlowService(self.db).list_flow(A)["rows"]}
        self.assertEqual(rows[filled]["release_date_basis"], "ONCHANNEL_CREATED_AT")
        self.assertEqual(rows[filled]["release_date"], "2026-10-25")
        self.assertIsNone(rows[written]["release_date_basis"])


class AutoRunTests(DbCase):

    def auto(self, filler_fn):
        return DiscoveryAutoRunService(
            self.db, adapter_factory=lambda db, company_id: None, key_check=lambda: True,
            now_factory=lambda: TODAY_UTC, release_filler=filler_fn,
        )

    def test_the_morning_run_fills_then_judges_with_the_new_date(self):
        one = self.seed("CH1", name="주말 캠핑 의자 접이식 초경량 튼튼한 야외용 상품")
        self.allow()
        fake = FakeConnections([[("CH1", "2026-10-25 09:00:00")]])
        summary = self.auto(lambda company_id: ReleaseDateFillService(self.db, fake, now_factory=lambda: TODAY_UTC).run(company_id)).run(A)
        self.assertEqual((summary["release_filled"], summary["release_blocked"], summary["new_saved"]), (1, None, 1))
        self.db.expire_all()
        self.assertIs(self.db.get(ProductCandidate, one).is_new_product, True)

    def test_no_allowance_is_reported_and_the_rest_goes_on(self):
        written = self.seed("CH2", release=date(2026, 10, 25), reference="URL:x", name="주말 캠핑 의자 접이식 초경량 튼튼한 야외용 상품")
        fake = FakeConnections([])
        self.seed("CH1", name="주말 캠핑 탁자 접이식 초경량 튼튼한 야외용 상품")
        summary = self.auto(lambda company_id: ReleaseDateFillService(self.db, fake, now_factory=lambda: TODAY_UTC).run(company_id)).run(A)
        self.assertEqual((summary["release_filled"], summary["release_blocked"], summary["new_saved"]), (0, fill_mod.BLOCKED_ALLOWANCE, 1))
        self.db.expire_all()
        self.assertIs(self.db.get(ProductCandidate, written).is_new_product, True)

    def test_a_crash_while_filling_does_not_stop_the_run(self):
        self.seed("CH2", release=date(2026, 10, 25), reference="URL:x", name="주말 캠핑 의자 접이식 초경량 튼튼한 야외용 상품")

        def boom(company_id):
            raise RuntimeError("x")

        with self.assertLogs("homez", level="ERROR"):
            summary = self.auto(boom).run(A)
        self.assertEqual((summary["failed"], summary["new_saved"], summary["release_filled"]), (1, 1, 0))

    def test_emergency_stop_stops_before_filling(self):
        called = []
        self.seed("CH1")
        SafetyService(self.db).activate_emergency_stop("시험", set_by=1, is_admin=True)
        self.auto(lambda company_id: called.append(company_id) or {"filled": 0, "blocked": None}).run(A)
        self.assertEqual(called, [])


class RouteTests(unittest.TestCase):

    def test_the_route_is_an_admin_only_post_with_no_input_and_runs_before_the_id_routes(self):
        from app.core.guard import admin_guard

        paths = [getattr(r, "path", "") for r in app.main.app.routes]
        route = next(r for r in app.main.app.routes if getattr(r, "path", "") == "/product-candidates/fill-release-dates")
        self.assertEqual(route.methods, {"POST"})
        self.assertIn(admin_guard, [d.call for d in route.dependant.dependencies])
        self.assertEqual((route.dependant.query_params, route.dependant.body_params), ([], []))
        self.assertEqual(paths.count("/product-candidates/fill-release-dates"), 1)

    def test_the_new_purpose_is_enforced_and_named(self):
        self.assertIn(CallPurpose.SUPPLIER_RELEASE_DATE, CallPurpose.ENFORCED)
        self.assertIn(CallPurpose.SUPPLIER_RELEASE_DATE, CallPurpose.ALL)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.SUPPLIER_RELEASE_DATE], "온채널 상품 등록일 조회")


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def disc_texts():
    block = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.disc\.[a-z_]+|easy\.error)": ("(?:[^"\\]|\\.)*")', block)}


def fill_constants():
    return re.search(r"  const FLOW_FILL_BLOCKED = \{.*?\};", JS, re.S).group(0)


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenFunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([fill_constants(), 'const chip = (tone, text) => `<i class="${tone}">${text}</i>`;'] + [function_source(n) for n in ("flowFillNote", "flowNewCell")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__NOW__", str(NOW)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(disc_texts())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_result_note_says_what_happened(self):
        ok = self.run_js("flowFillNote({filled: 2, judged_new: 1, judged_old: 1, not_found: 3, future: 1, unreadable: 1, judge_failed: 1, blocked: null, truncated: false})")
        self.assertEqual(ok["tone"], "ok")
        self.assertIn("2건 채웠어요", ok["text"])
        self.assertIn("신제품 1건", ok["text"])
        self.assertIn("모름으로 둔 후보 3건", ok["text"])
        self.assertIn("판정하지 못한 후보 3건", ok["text"])
        self.assertEqual(self.run_js("flowFillNote({filled: 0, blocked: null, truncated: false})")["tone"], "warn")

    def test_being_blocked_is_not_shown_as_done(self):
        blocked = self.run_js("flowFillNote({filled: 0, blocked: 'ALLOWANCE_REQUIRED'})")
        self.assertEqual(blocked["tone"], "bad")
        self.assertIn("온채널 상품 등록일 조회", blocked["text"])
        self.assertNotIn("0건 채웠어요", blocked["text"])
        partial = self.run_js("flowFillNote({filled: 1, blocked: 'LOOKUP_ERROR'})")
        self.assertEqual(partial["tone"], "warn")
        self.assertIn("1건 채웠어요", partial["text"])
        self.assertIn("읽지 못했어요", partial["text"])
        more = self.run_js("flowFillNote({filled: 1, blocked: null, truncated: true})")
        self.assertIn("한 번 더 누르면", more["text"])

    def test_the_cell_names_the_source_of_the_date(self):
        done = self.run_js("flowNewCell({new_product_state: 'DONE', is_new_product: true, release_date: '2026-10-25', release_date_basis: 'ONCHANNEL_CREATED_AT'})")
        self.assertIn("온채널 등록일 기준 2026-10-25", done)
        todo = self.run_js("flowNewCell({new_product_state: 'TODO', release_date: '2026-10-25', release_date_basis: 'ONCHANNEL_CREATED_AT'})")
        self.assertIn("온채널 등록일 기준", todo)
        self.assertNotIn("온채널 등록일", self.run_js("flowNewCell({new_product_state: 'DONE', is_new_product: true, release_date: '2026-10-25', release_date_basis: null})"))
        self.assertEqual(self.run_js("flowNewCell({new_product_state: 'UNKNOWN_DATE'})"), '<i class="gray">출시일 모름</i>')


class ScreenWiringTests(unittest.TestCase):

    def test_the_button_confirms_then_posts_and_warns_the_date_may_differ(self):
        fill = function_source("flowFillDates")
        self.assertLess(fill.index("await ezDialog("), fill.index("api("))
        self.assertIn("if (!ok) return false;", fill)
        self.assertIn('api("/product-candidates/fill-release-dates", { method: "POST" })', fill)
        body = disc_texts()["easy.disc.fill_body"]
        for word in ("승인 센터", "실제 출시일과 다를 수", "최대 10번"):
            self.assertIn(word, body)

    def test_the_button_shows_only_when_a_date_is_unknown_and_is_bound(self):
        render = function_source("renderFlow")
        self.assertIn('rows.some((r) => r.new_product_state === "UNKNOWN_DATE")', render)
        self.assertIn("data-ez-flow-fill", render)
        self.assertIn("await flowFillDates()", render)

    def test_the_auto_run_card_reports_fills_and_blocks(self):
        auto = function_source("flowAutoHtml")
        self.assertIn("last.release_filled", auto)
        self.assertIn("FLOW_FILL_BLOCKED[last.release_blocked]", auto)


if __name__ == "__main__":
    unittest.main()
