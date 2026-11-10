"""
=========================================================
Homez OS

File : tests/test_ai_background.py

2026-11-10 — AI 배경(Cloudflare, 사용자 결정 "cloudflare로 진행해라")을 고정한다. 임시 SQLite·임시 폴더·가짜 전송 — 실제 네트워크·AI 호출 0.

  - 호출 계약: 공식 주소·Bearer 토큰·본문은 장면 글과 단계 수뿐(상품 사진 없음), 다른 주소로 넘겨주기를 따라가지 않는다, 무료 한도(429·3036)·용량 초과(429·3040)·
    토큰 오류를 구분한다, 이미지가 없거나 풀리지 않으면 실패다.
  - 컨셉 6종: 생성 배경(L2) 명세를 통과하고, 대표(MAIN)에 쓸 수 없고, 로컬 컨셉 경로는 이 코드를 모른다(알 수 없는 컨셉).
  - 서비스: 설정이 없거나 대표·한도 초과·모르는 코드면 시작 전에 거절(호출 0). 허용이 없으면 부르지 않고 허용 요청을 남긴다. 배경 한 장마다 허용 1회,
    저장된 배경은 다른 상품에 다시 쓴다(호출 0), 새 배경은 다음 번호. 무료 한도가 차면 남은 컨셉은 부르지 않는다. 상품 픽셀 보존 PASS, 글자 섞임·정책은 NOT_CHECKED.
  - 화면: 실행 전 막기·요청 본문·결과 표시(이스케이프)·문구 짝, 쉬운 보기 설정은 비밀번호 재확인과 토큰 숨김.
=========================================================
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests
from PIL import Image
from PIL import ImageDraw
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.company.model import Company
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset import ai_background_service as abs_
from app.domains.media_asset import cloudflare_image_client as cfc
from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset.ai_background_router import AiBackgroundRunRequest
from app.domains.media_asset.ai_background_router import AiBackgroundSettingsRequest
from app.domains.media_asset.concept_run_service import ConceptRunService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.user.model import User  # noqa: F401

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "cf-test-token_ABCDEFGHIJKLMNOPQRSTUV"
ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def png(color=(120, 160, 140), size=(1024, 1024)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg(color=(200, 190, 170), size=(1024, 1024)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def cutout_png(color=(20, 30, 70, 255), size=(500, 500)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((100, 100, size[0] - 100, size[1] - 100), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def ok_body(image=None):
    return {"result": {"image": base64.b64encode(image if image is not None else jpeg()).decode()}, "success": True, "errors": [], "messages": []}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or [FakeResponse(200, ok_body())]
        self.calls = []

    def post(self, url, *, headers, json_body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json_body, "timeout": timeout})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ------------------------------------------------------------------
# 호출 계약
# ------------------------------------------------------------------

class ClientTests(unittest.TestCase):

    def client(self, *outcomes):
        self.transport = FakeTransport(*outcomes)
        return cfc.CloudflareImageClient(ACCOUNT, TOKEN, transport=self.transport)

    def test_request_goes_to_the_official_url_with_bearer_token_and_only_prompt_and_steps(self):
        result = self.client().generate("A bright bathroom shelf.")
        self.assertTrue(result.ok)
        call = self.transport.calls[0]
        self.assertEqual(call["url"], f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell")
        self.assertEqual(call["headers"], {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        self.assertEqual(call["body"], {"prompt": "A bright bathroom shelf.", "steps": 4})        # 입력 규격 밖의 칸(seed 등)·이미지는 보내지 않는다
        self.assertEqual(result.image_bytes, jpeg())

    def test_requests_transport_does_not_follow_redirects(self):
        session = mock.Mock()
        cfc.RequestsTransport(session).post("https://x", headers={}, json_body={}, timeout=1)
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])

    def test_body_rules(self):
        with self.assertRaises(cfc.CloudflareImageError):
            cfc.build_body("   ")
        with self.assertRaises(cfc.CloudflareImageError):
            cfc.build_body("x" * 2049)
        self.assertEqual(cfc.build_body("x" * 2048)["prompt"], "x" * 2048)
        for steps in (0, 9, True, 4.0):
            with self.assertRaises(cfc.CloudflareImageError):
                cfc.build_body("ok", steps)
        self.assertEqual(cfc.build_body("ok", 8), {"prompt": "ok", "steps": 8})

    def test_bad_account_id_or_token_is_refused_before_any_call(self):
        for account, token in (("ABC", TOKEN), (ACCOUNT.upper(), TOKEN), (ACCOUNT, "short"), (ACCOUNT, "bad token with spaces 123456"), (None, TOKEN)):
            with self.assertRaises(cfc.CloudflareImageError):
                cfc.CloudflareImageClient(account, token, transport=FakeTransport())

    def test_success_needs_success_true_and_a_decodable_image(self):
        cases = [
            ({"success": False, "errors": [{"code": 5007, "message": "No such model"}], "result": None}, "No such model"),
            ({"success": True, "result": {}}, "이미지가 없다"),
            ({"success": True, "result": {"image": "!!not base64!!"}}, "해석할 수 없다"),
            ({"success": True, "result": {"image": ""}}, "이미지가 없다"),
            (["not", "a", "dict"], "해석할 수 없다"),
            ({"image": base64.b64encode(jpeg()).decode()}, "실패 응답"),          # 봉투 없이 온 것은 규격과 달라 성공으로 보지 않는다
        ]
        for body, words in cases:
            result = self.client(FakeResponse(200, body)).generate("scene")
            self.assertFalse(result.ok, body)
            self.assertIn(words, result.detail)

    def test_free_limit_and_capacity_are_told_apart_and_the_token_is_never_in_the_detail(self):
        free = self.client(FakeResponse(429, {"success": False, "errors": [{"code": 3036, "message": "You have used up your daily free allocation"}]})).generate("s")
        self.assertEqual((free.ok, free.error_kind, free.http_status), (False, cfc.FREE_LIMIT, 429))
        self.assertIn("무료 사용량", free.detail)
        busy = self.client(FakeResponse(429, {"success": False, "errors": [{"code": 3040, "message": "Capacity temporarily exceeded"}]})).generate("s")
        self.assertEqual(busy.error_kind, cfc.CAPACITY)
        for status, words in ((401, "API 토큰 오류"), (403, "권한 없음"), (404, "찾을 수 없음"), (500, "서버 오류"), (429, "호출 한도 초과")):
            result = self.client(FakeResponse(status, ValueError("no json"))).generate("s")
            self.assertFalse(result.ok)
            self.assertIn(words, result.detail)
            self.assertIsNone(result.error_kind)
            self.assertNotIn(TOKEN, result.detail)

    def test_network_problems_are_failures(self):
        self.assertIn("시간이 초과", self.client(requests.Timeout()).generate("s").detail)
        self.assertIn("네트워크 오류", self.client(requests.ConnectionError()).generate("s").detail)


# ------------------------------------------------------------------
# 컨셉 명세
# ------------------------------------------------------------------

class ConceptTests(unittest.TestCase):

    def test_six_generated_concepts_pass_the_spec_and_never_serve_the_main_image(self):
        self.assertEqual(len(abc.AI_CONCEPTS), 6)
        for code, concept in abc.AI_CONCEPTS.items():
            self.assertEqual(ic.validate_spec(concept), [], code)
            self.assertEqual((concept.processing_level, concept.background_kind), (ic.LEVEL_L2, ic.BACKGROUND_GENERATED))
            self.assertTrue(concept.needs_generated_label and concept.l2_approved)
            self.assertNotIn(ic.PURPOSE_MAIN, concept.usable_for)
            self.assertIn(ic.CHECK_PRODUCT_PRESERVATION, concept.checks)
            self.assertIn(ic.CHECK_POLICY_FLAGS, concept.checks)
            for words in ("No products", "No text", "no logos", "no people", "empty surface"):
                self.assertIn(words.lower(), concept.generation_prompt.lower(), code)
            with self.assertRaises(BadRequestException):
                concept.background_spec()                        # 로컬 합성 경로에서 쓸 수 없다

    def test_ai_concepts_are_not_in_the_local_registry(self):
        self.assertFalse(set(abc.AI_CONCEPTS) & set(ic.CONCEPTS))
        self.assertEqual(ic.validate_registry(), {})
        for code in abc.AI_CONCEPTS:
            with self.assertRaises(BadRequestException):
                ic.get_concept(code)

    def test_spec_rules_for_generated_backgrounds(self):
        base = abc.AI_CONCEPTS["AI_BATHROOM_SHELF"]
        from dataclasses import replace

        self.assertTrue(any("L2" in p for p in ic.validate_spec(replace(base, processing_level=ic.LEVEL_L1, l2_approved=False, needs_generated_label=False, props_allowed=False))))
        self.assertTrue(any("장면 글" in p for p in ic.validate_spec(replace(base, generation_prompt=""))))
        self.assertTrue(any("장면 글" in p for p in ic.validate_spec(replace(base, generation_prompt="x" * 1001))))
        self.assertTrue(any("색" in p for p in ic.validate_spec(replace(base, color_hex="#FFFFFF"))))
        self.assertTrue(any("승인" in p for p in ic.validate_spec(replace(base, l2_approved=False))))
        self.assertTrue(any("대표" in p for p in ic.validate_spec(replace(base, usable_for=(ic.PURPOSE_MAIN, ic.PURPOSE_DETAIL)))))
        local = ic.CONCEPTS["SOFT_BEIGE"]
        self.assertTrue(any("generation_prompt" in p for p in ic.validate_spec(replace(local, generation_prompt="scene"))))

    def test_summary_shows_the_prompt_that_is_sent(self):
        summary = abc.to_summary(abc.AI_CONCEPTS["AI_KITCHEN_COUNTER"])
        self.assertEqual(summary["prompt"], abc.AI_CONCEPTS["AI_KITCHEN_COUNTER"].generation_prompt)
        self.assertTrue(summary["needs_generated_label"])


# ------------------------------------------------------------------
# 서비스
# ------------------------------------------------------------------

class Base_(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, MediaAsset.__table__, ImageGenerationJob.__table__, ImageGenerationResult.__table__, ImageGenerationDailyUsage.__table__,
            ExternalCallAllowance.__table__, ExternalCallLog.__table__, FunctionAutomationState.__table__, EmergencyStop.__table__,
        ])
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                                 "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50))")
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.media_root = Path(tempfile.mkdtemp())
        self.queue = ImageGenerationJobQueueService(self.db, media_root=self.media_root)
        self.company_id = self.make_company("회사 A", "111-11-11111")
        self.other_company_id = self.make_company("회사 B", "222-22-22222")
        self.store = InMemoryCredentialStore()
        self.transport = FakeTransport()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)
        shutil.rmtree(self.media_root, ignore_errors=True)

    def make_company(self, name, number):
        company = Company(name=name, business_number=number, ceo="t", phone="02-000-0000", email="x@test.com", address="서울")
        self.db.add(company)
        self.db.commit()
        return company.id

    def service(self):
        return abs_.AiBackgroundService(self.db, self.store, queue=self.queue,
                                        client_factory=lambda account, token: cfc.CloudflareImageClient(account, token, transport=self.transport))

    def configure(self):
        return self.service().save_settings(account_id=ACCOUNT, api_token=TOKEN)

    def grant(self, count=6, company_id=None):
        ExternalCallPolicyService(self.db).grant(company_id or self.company_id, system=ExternalSystem.CLOUDFLARE, kind=CallKind.WRITE,
                                                 purpose=CallPurpose.AI_BACKGROUND, mode=AllowanceMode.COUNTED, count=count, user_id=7)

    def remaining(self):
        rows = self.db.query(ExternalCallAllowance).filter_by(system="CLOUDFLARE", purpose="AI_BACKGROUND").filter(ExternalCallAllowance.status.in_(("ACTIVE", "EXHAUSTED"))).all()
        return sum(r.remaining_count or 0 for r in rows)

    def upload(self, data=None, company_id=None, rights="VERIFIED", owner_id=7):
        asset = self.queue.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=owner_id, purpose="DETAIL", display_order=0,
            image_bytes=data or cutout_png(), original_filename="cutout.png", company_id=company_id or self.company_id,
        )
        asset.rights_status = rights
        self.db.commit()
        return asset

    def make_run(self, codes, purpose="ADDITIONAL", cutout=None, **kw):
        cutout = cutout or self.upload()
        return self.service().run(self.company_id, 7, cutout.id, codes, purpose, **kw)


class SettingsTests(Base_):

    def test_status_never_shows_the_token_and_only_the_account_tail(self):
        self.assertFalse(self.service().status()["configured"])
        status = self.configure()
        self.assertTrue(status["configured"] and status["token_saved"] and status["account_id_saved"])
        self.assertEqual(status["account_id_tail"], ACCOUNT[-4:])
        self.assertNotIn(TOKEN, json.dumps(status))
        self.assertNotIn(ACCOUNT, json.dumps(status))
        self.assertFalse(status["sends_product_photo"])
        self.assertEqual(self.store.read(abs_.CREDENTIAL_TARGET), {"account_id": ACCOUNT, "api_token": TOKEN})

    def test_first_save_needs_both_values_and_formats_are_checked(self):
        for kwargs in ({"account_id": ACCOUNT, "api_token": None}, {"account_id": None, "api_token": TOKEN}, {"account_id": "XYZ", "api_token": TOKEN},
                       {"account_id": ACCOUNT, "api_token": "bad token"}):
            with self.assertRaises(BadRequestException):
                self.service().save_settings(**kwargs)
        self.assertFalse(self.service().status()["configured"])

    def test_empty_values_keep_what_is_saved(self):
        self.configure()
        new_token = "another-token_0123456789ABCDEFGH"
        self.service().save_settings(account_id="", api_token=new_token)
        self.assertEqual(self.store.read(abs_.CREDENTIAL_TARGET), {"account_id": ACCOUNT, "api_token": new_token})
        self.service().save_settings(account_id=ACCOUNT.replace("0", "f"), api_token=None)
        self.assertEqual(self.store.read(abs_.CREDENTIAL_TARGET)["api_token"], new_token)


class GuardTests(Base_):

    def test_refusals_happen_before_any_call(self):
        self.configure()
        self.grant()
        cutout = self.upload()
        for codes, purpose in ((["AI_BATHROOM_SHELF"], "MAIN"), ([], "ADDITIONAL"), (["AI_BATHROOM_SHELF", "AI_KITCHEN_COUNTER", "AI_LIVING_TABLE", "AI_BEDROOM_SOFT"], "ADDITIONAL"),
                               (["SOFT_BEIGE"], "ADDITIONAL"), (["NOPE_CODE"], "DETAIL"), ([""], "DETAIL")):
            with self.assertRaises(BadRequestException, msg=(codes, purpose)):
                self.make_run(codes, purpose, cutout=cutout)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.remaining(), 6)

    def test_without_settings_nothing_is_called(self):
        self.grant()
        with self.assertRaises(BadRequestException):
            self.make_run(["AI_BATHROOM_SHELF"])
        self.assertEqual(self.transport.calls, [])

    def test_other_company_or_denied_photos_are_refused(self):
        self.configure()
        self.grant()
        with self.assertRaises(NotFoundException):
            self.make_run(["AI_BATHROOM_SHELF"], cutout=self.upload(company_id=self.other_company_id))
        with self.assertRaises(BadRequestException):
            self.make_run(["AI_BATHROOM_SHELF"], cutout=self.upload(rights="RIGHTS_DENIED", data=cutout_png((90, 10, 10, 255))))
        self.assertEqual(self.transport.calls, [])

    def test_without_allowance_it_does_not_call_and_leaves_a_request(self):
        self.configure()
        outcomes = self.make_run(["AI_BATHROOM_SHELF", "AI_KITCHEN_COUNTER"])
        self.assertEqual([(o.status, o.reason) for o in outcomes], [("FAILED", "ALLOWANCE_REQUIRED"), ("SKIPPED", "ALLOWANCE_REQUIRED")])
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(system="CLOUDFLARE", purpose="AI_BACKGROUND", status="PENDING").all()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].requested_count, 6)
        self.assertEqual(self.db.query(MediaAsset).filter_by(asset_role="GENERATED").count(), 0)

    def test_emergency_stop_blocks_the_call(self):
        self.configure()
        self.grant()
        with mock.patch.object(ExternalCallPolicyService, "_emergency_stop_active", return_value=True):
            outcomes = self.make_run(["AI_BATHROOM_SHELF"])
        self.assertEqual((outcomes[0].status, outcomes[0].reason), ("FAILED", "ESTOP_ACTIVE"))
        self.assertEqual(self.transport.calls, [])


class RunTests(Base_):

    def setUp(self):
        super().setUp()
        self.configure()
        self.grant()

    def test_one_call_per_new_background_and_the_product_is_kept(self):
        # 배경 두 장이 서로 달라야 합성 결과도 다르다(같으면 기존 규칙대로 같은 사진으로 합쳐진다 — REUSED).
        self.transport = FakeTransport(FakeResponse(200, ok_body(jpeg((230, 230, 225)))), FakeResponse(200, ok_body(jpeg((180, 200, 230)))))
        outcomes = self.make_run(["AI_BATHROOM_SHELF", "AI_KITCHEN_COUNTER"], purpose="DETAIL")
        self.assertEqual([o.status for o in outcomes], ["CREATED", "CREATED"])
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.remaining(), 4)
        for call, code in zip(self.transport.calls, ("AI_BATHROOM_SHELF", "AI_KITCHEN_COUNTER")):
            self.assertEqual(call["body"], {"prompt": abc.AI_CONCEPTS[code].generation_prompt, "steps": 4})   # 상품 사진·이름은 보내지 않는다
        checks = {c["code"]: c for c in outcomes[0].outcome.checks}
        self.assertEqual(checks["PRODUCT_PRESERVATION"]["status"], "PASS")
        self.assertEqual(checks["NO_TEXT_OVERLAY"]["status"], "NOT_CHECKED")            # AI 배경은 글자 없음을 코드가 보장하지 못한다
        self.assertEqual(checks["POLICY_FLAGS"]["status"], "NOT_CHECKED")
        for o in outcomes:
            self.assertEqual((o.background_version, o.background_reused), (1, False))
            self.assertTrue((self.media_root / f"ai_backgrounds/{self.company_id}/{o.concept_code}/v1.png").exists())
            self.assertEqual(o.outcome.asset.asset_role, "GENERATED")
        jobs = self.db.query(ImageGenerationJob).filter_by(provider_code="CLOUDFLARE").all()
        self.assertEqual(sorted(j.model_name for j in jobs), ["AI_BATHROOM_SHELF-v1", "AI_KITCHEN_COUNTER-v1"])
        rows = self.db.execute(__import__("sqlalchemy").text("SELECT action, description FROM audit_logs WHERE action='AI_BACKGROUND_USAGE'")).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(TOKEN not in r[1] and ACCOUNT not in r[1] for r in rows))
        logs = self.db.query(ExternalCallLog).filter_by(system="CLOUDFLARE").all()
        self.assertEqual(sorted(l.outcome for l in logs), ["SUCCEEDED", "SUCCEEDED"])

    def test_saved_background_is_reused_for_another_product_without_a_call(self):
        self.make_run(["AI_LIVING_TABLE"])
        other = self.upload(cutout_png((200, 40, 40, 255)), owner_id=8)
        outcomes = self.make_run(["AI_LIVING_TABLE"], cutout=other)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.remaining(), 5)
        self.assertEqual((outcomes[0].status, outcomes[0].background_version, outcomes[0].background_reused), ("CREATED", 1, True))

    def test_reuse_needs_no_settings_or_allowance(self):
        self.make_run(["AI_LIVING_TABLE"])
        self.store = InMemoryCredentialStore()                                          # 설정을 지워도
        allowance = self.db.query(ExternalCallAllowance).filter_by(status="ACTIVE").one()         # 허용도 거둬도
        allowance.status = "REVOKED"
        self.db.commit()
        outcomes = self.make_run(["AI_LIVING_TABLE"], cutout=self.upload(cutout_png((10, 160, 40, 255)), owner_id=9))
        self.assertEqual(outcomes[0].status, "CREATED")
        self.assertEqual(len(self.transport.calls), 1)

    def test_new_background_draws_the_next_number(self):
        self.make_run(["AI_STUDIO_MARBLE"])
        outcomes = self.make_run(["AI_STUDIO_MARBLE"], new_background=True, cutout=self.upload(cutout_png((10, 10, 200, 255)), owner_id=10))
        self.assertEqual((outcomes[0].background_version, outcomes[0].background_reused), (2, False))
        self.assertEqual(len(self.transport.calls), 2)
        self.assertTrue((self.media_root / f"ai_backgrounds/{self.company_id}/AI_STUDIO_MARBLE/v2.png").exists())

    def test_daily_free_limit_stops_the_remaining_concepts(self):
        self.transport = FakeTransport(FakeResponse(429, {"success": False, "errors": [{"code": 3036, "message": "daily free allocation"}]}))
        outcomes = self.make_run(["AI_BATHROOM_SHELF", "AI_KITCHEN_COUNTER", "AI_OUTDOOR_TRACK"])
        self.assertEqual([(o.status, o.reason) for o in outcomes], [("FAILED", "DAILY_FREE_LIMIT"), ("SKIPPED", "DAILY_FREE_LIMIT"), ("SKIPPED", "DAILY_FREE_LIMIT")])
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.remaining(), 5)
        self.assertFalse((self.media_root / "ai_backgrounds").exists())

    def test_one_failure_does_not_stop_the_others(self):
        self.transport = FakeTransport(FakeResponse(500, {"success": False, "errors": [{"code": 1, "message": "boom"}]}), FakeResponse(200, ok_body()))
        outcomes = self.make_run(["AI_BATHROOM_SHELF", "AI_KITCHEN_COUNTER"])
        self.assertEqual([(o.status, o.reason) for o in outcomes], [("FAILED", "AI_CALL_FAILED"), ("CREATED", None)])
        self.assertIn("서버 오류", outcomes[0].detail)

    def test_bad_or_tiny_images_are_not_saved(self):
        for image in (b"not an image at all", png(size=(256, 256))):
            self.transport = FakeTransport(FakeResponse(200, ok_body(image)))
            outcomes = self.make_run(["AI_BEDROOM_SOFT"])
            self.assertEqual((outcomes[0].status, outcomes[0].reason), ("FAILED", "BAD_AI_IMAGE"))
        self.assertFalse((self.media_root / f"ai_backgrounds/{self.company_id}/AI_BEDROOM_SOFT/v1.png").exists())
        self.assertEqual(self.db.query(MediaAsset).filter_by(asset_role="GENERATED").count(), 0)

    def test_background_is_normalised_to_a_square_png(self):
        self.transport = FakeTransport(FakeResponse(200, ok_body(jpeg(size=(1024, 768)))))
        self.make_run(["AI_OUTDOOR_TRACK"])
        with Image.open(self.media_root / f"ai_backgrounds/{self.company_id}/AI_OUTDOOR_TRACK/v1.png") as saved:
            self.assertEqual((saved.format, saved.size), ("PNG", (1200, 1200)))

    def test_local_concept_run_still_skips_ai_codes(self):
        outcomes = ConceptRunService(self.db, queue=self.queue).run(self.company_id, 7, self.upload().id, ["AI_BATHROOM_SHELF", "SOFT_BEIGE"], "ADDITIONAL")
        self.assertEqual([(o.status, o.reason) for o in outcomes], [("SKIPPED", "UNKNOWN_CONCEPT"), ("CREATED", None)])
        self.assertEqual(self.transport.calls, [])


# ------------------------------------------------------------------
# 주소·요청 모양
# ------------------------------------------------------------------

class RouterTests(unittest.TestCase):

    def test_routes_are_registered(self):
        from app.main import app

        paths = {(route.path, tuple(sorted(route.methods))) for route in app.routes if hasattr(route, "methods")}
        for path, method in (("/ai-background/settings", "GET"), ("/ai-background/settings", "PUT"), ("/ai-background/concepts", "GET"), ("/ai-background/runs", "POST")):
            self.assertTrue(any(p == path and method in m for p, m in paths), (path, method))

    def test_saving_needs_a_recent_password_check(self):
        from app.domains.media_asset import ai_background_router as router

        user = SimpleNamespace(id=1, company_id=1)
        with mock.patch.object(router, "consume_recent_auth_token", return_value=False):
            with self.assertRaises(ForbiddenException):
                router.save_settings(AiBackgroundSettingsRequest(account_id=ACCOUNT, api_token=TOKEN), user, None, mock.Mock(), InMemoryCredentialStore())

    def test_request_shapes(self):
        with self.assertRaises(ValidationError):
            AiBackgroundRunRequest(cutout_asset_id=1, concept_codes=["AI_BATHROOM_SHELF"], purpose="MAIN")
        with self.assertRaises(ValidationError):
            AiBackgroundRunRequest(cutout_asset_id=1, concept_codes=["A_ONE", "A_TWO", "A_THREE", "A_FOUR"])
        with self.assertRaises(ValidationError):
            AiBackgroundRunRequest(cutout_asset_id=1, concept_codes=["<b>x</b>"])
        with self.assertRaises(ValidationError):
            AiBackgroundRunRequest(cutout_asset_id=1, concept_codes=["AI_BATHROOM_SHELF"], prompt="draw a logo")
        with self.assertRaises(ValidationError):
            AiBackgroundSettingsRequest(account_id=ACCOUNT, api_token=TOKEN, extra="x")

    def test_allowance_constants(self):
        self.assertIn(ExternalSystem.CLOUDFLARE, ExternalSystem.ALL)
        self.assertIn(CallPurpose.AI_BACKGROUND, CallPurpose.ALL)
        self.assertIn(CallPurpose.AI_BACKGROUND, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.AI_BACKGROUND], "AI 배경 그리기")


# ------------------------------------------------------------------
# 화면
# ------------------------------------------------------------------

JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
EASY = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
EASY_TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")


def lw_table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.(?:aibg|concept)_[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


def function_source(name, source=JS):
    match = re.search(r"(?:async )?function " + re.escape(name) + r"\(", source)
    assert match, name
    j, paren = match.end(), 1
    while paren:
        paren += {"(": 1, ")": -1}.get(source[j], 0)
        j += 1
    i = source.index("{", j)
    depth, k = 0, i
    while True:
        ch = source[k]
        depth += {"{": 1, "}": -1}.get(ch, 0)
        k += 1
        if depth == 0:
            return source[match.start():k]


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class UiBehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        consts = "\n".join(m.group(0) for m in re.finditer(r"^  const LW_(?:CONCEPT|AIBG)_[A-Z_]+ = [^\n]+$", JS, re.M))
        sources = "\n".join([consts] + [function_source(n) for n in ("lwConceptCheckText", "lwConceptSummaryHtml", "lwAiBgProblem", "lwAiBgBody", "lwAiBgSummaryHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(lw_table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_problem_rules(self):
        ready = '{configured: true}'
        self.assertEqual(self.run_js('lwAiBgProblem({codes: ["A"], settings: {configured: false}})'), "aibg_err_settings")
        self.assertEqual(self.run_js('lwAiBgProblem({codes: ["A"], settings: null})'), "aibg_err_settings")
        self.assertEqual(self.run_js(f'lwAiBgProblem({{codes: [], settings: {ready}}})'), "aibg_err_none")
        self.assertIsNone(self.run_js(f'lwAiBgProblem({{codes: ["A","B","C"], settings: {ready}}})'))
        self.assertEqual(self.run_js(f'lwAiBgProblem({{codes: ["A","B","C","D"], settings: {ready}}})'), "aibg_err_max")

    def test_body(self):
        self.assertEqual(self.run_js('lwAiBgBody({codes: ["AI_BATHROOM_SHELF"], newBg: true}, "12", "DETAIL")'),
                         {"cutout_asset_id": 12, "concept_codes": ["AI_BATHROOM_SHELF"], "purpose": "DETAIL", "new_background": True})
        self.assertFalse(self.run_js('lwAiBgBody({codes: ["X"], newBg: "yes"}, 1, "ADDITIONAL")')["new_background"])

    def test_summary_shows_the_generated_label_reuse_and_escapes_server_text(self):
        result = {"created": 1, "reused": 0, "skipped": 0, "check_failed": 0, "failed": 1, "drawn": 1, "results": [
            {"concept_code": "AI_BATHROOM_SHELF", "status": "CREATED", "reason": None, "checks": [], "asset": {"id": 9}, "background_version": 2, "background_reused": True},
            {"concept_code": "AI_KITCHEN_COUNTER", "status": "FAILED", "reason": "DAILY_FREE_LIMIT", "detail": "<img onerror=1>", "checks": [], "asset": None,
             "background_version": 1, "background_reused": False},
        ]}
        html = self.run_js(f"lwAiBgSummaryHtml({json.dumps(result)}, {json.dumps({'AI_BATHROOM_SHELF': '욕실 선반'})})")
        self.assertIn("생성 배경", html)
        self.assertIn("이번에 새로 그린 배경 1장", html)
        self.assertIn("욕실 선반 · 저장된 배경 2번을 다시 씀(호출 없음)", html)
        self.assertIn("무료 사용량을 다 써서", html)
        self.assertNotIn("<img", html)
        self.assertIn("쿠팡 이미지 정책에 맞는지는 자동으로 확인하지 못합니다", html)
        self.assertEqual(self.run_js("lwAiBgSummaryHtml(null, null)"), "")


class UiSourceTests(unittest.TestCase):

    def test_panel_mounts_the_ai_section_and_uses_the_right_routes(self):
        panel = function_source("lwOpenConceptPanel")
        self.assertIn('<div id="lw-aibg-box-${assetId}"></div>', panel)
        self.assertIn("lwAiBgMount(assetId, content, state.purpose, state.ai);", panel)
        mount = function_source("lwAiBgMount")
        self.assertIn('apiFetch("/ai-background/concepts")', mount)
        self.assertIn('apiFetch("/ai-background/settings")', mount)
        run = mount[mount.index("lw-aibg-run-btn-${assetId}`).addEventListener"):]
        self.assertLess(run.index("lwAiBgProblem(ai)"), run.index('"/ai-background/runs"'))
        self.assertLess(run.index("lwConceptNotice = { result, labels, ai: true }"), run.index("await lwRenderMediaStep(content)"))
        self.assertIn("withButtonGuard", run)
        self.assertIn('if (purpose === "MAIN")', mount)
        self.assertIn("escapeHtml(c.prompt", mount)
        self.assertIn("notice.ai ? lwAiBgSummaryHtml(", function_source("lwConceptNoticeHtml"))

    def test_every_aibg_and_concept_key_exists_in_both_languages(self):
        ko, en = lw_table(KO), lw_table(EN)
        used = set(re.findall(r'"(aibg_[a-z_0-9]+)"', JS))
        self.assertTrue(used)
        for key in sorted(used):
            self.assertIn(f"lw.{key}", ko, key)
            self.assertIn(f"lw.{key}", en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[f"lw.{key}"])), sorted(re.findall(r"\{(\w+)\}", en[f"lw.{key}"])), key)
        self.assertEqual({k for k in ko if k.startswith("lw.aibg_")} - {f"lw.{k}" for k in used}, set(), "쓰지 않는 AI 배경 문구가 있다")
        self.assertEqual(set(ko), set(en))

    def test_easy_view_settings_use_password_recheck_and_hide_the_token(self):
        edit = function_source("aiBgSettingsEdit", EASY)
        self.assertIn('api("/ai-background/settings", { method: "PUT", headers: { "X-Recent-Auth-Token": token }', edit)
        self.assertLess(edit.index("B.promptRecentAuthToken()"), edit.index('method: "PUT"'))
        self.assertIn('type="password" autocomplete="new-password"', edit)
        html = function_source("aiBgSettingsHtml", EASY)
        self.assertNotIn("api_token", html)
        self.assertIn("account_id_tail", html)
        self.assertIn('id="ez-aibg-box"', EASY)
        self.assertIn("aiBgSettingsLoad();", function_source("loadMore", EASY))
        used = set(re.findall(r'"(easy\.aibg\.[a-z_]+)"', EASY))
        self.assertTrue(used)
        for key in used:
            self.assertEqual(EASY_TEXTS.count(f'"{key}":'), 2, key)          # 한국어·영어 한 번씩


if __name__ == "__main__":
    unittest.main()
