"""
=========================================================
Homez OS

File : tests/test_claude_gemini_link.py

2026-11-23 — 클로드 ↔ 제미나이 연결 장치(사용자 "구글 AI 스튜디오 클로드에 플러그인해라", 목적: 사진 만들기·글 질문·사용량 모두).
임시 SQLite·메모리 자격증명·가짜 전송기 — 실제 제미나이·네트워크 0.

  - 통신 규약: initialize·tools/list·tools/call·ping, 알림은 답하지 않음, 모르는 요청·도구·깨진 메시지는 오류. 도구 실패는 도구 결과(isError)로.
  - 글 질문: 같은 키·같은 월 크레딧(이미지와 합산)·허락(GEMINI_QUESTION) 1회. 허락이 없으면 부르지 않고 요청이 남는다. 크레딧을 넘으면 부르지 않는다.
    비용은 응답 토큰으로, 토큰이 없으면 최악 비용으로, 402·401 같은 보내기 전 거절은 0. 키는 결과·기록에 없다.
  - 연결 장치: 사진 목록·사진 만들기(앱과 같은 저장 규칙)·사용량. 실패는 사람에게 보일 사유로 돌려준다.
  - 다른 프로세스와의 한 줄 서기: 잠금 파일을 다른 쪽이 잡고 있으면 기다리다 부르지 않는다.
=========================================================
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.domains.ai_governance import gemini_question_service as gqs
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.media_asset import gemini_image_client as gic
from app.domains.media_asset import image_ai_service as ias
from app.domains.media_asset.model import MediaAsset
from tests.test_gemini_image import KEY
from tests.test_gemini_image import Base_
from tests.test_gemini_image import FakeResponse
from tests.test_gemini_image import FakeTransport
from tools.claude_gemini_link import server as srv
from tools.claude_gemini_link.link import GeminiLink

ROOT = Path(__file__).resolve().parents[1]


def answer(text_="답입니다", usage=None):
    body = {"id": "i1", "status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": text_}]}]}
    if usage is not False:
        body["usage"] = usage or {"total_input_tokens": 1000, "total_output_tokens": 500, "total_thought_tokens": 300}
    return body


class ProtocolTests(unittest.TestCase):

    class FakeLink:
        def __init__(self):
            self.calls = []

        def list_photos(self, candidate_id=None):
            self.calls.append(("list", candidate_id))
            return {"사진": []}

        def usage(self):
            return {"준비됨": True}

        def ask(self, question, instruction=None, model=None):
            raise RuntimeError("일부러 실패")

        def make_photo(self, *a, **k):
            self.calls.append(("make", a, k))
            return {"만듦": 1}

    def server(self):
        link = self.FakeLink()
        return srv.Server(link_factory=lambda: link), link

    def test_initialize_list_and_ping(self):
        server, _ = self.server()
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(init["result"]["serverInfo"]["name"], "homez-gemini")
        self.assertIn("tools", init["result"]["capabilities"])
        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["gemini_list_photos", "gemini_make_photo", "gemini_ask", "gemini_usage"])
        for tool in tools:
            self.assertRegex(tool["name"], r"^[a-zA-Z0-9_-]{1,64}$")
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("사용료", tool["description"]) if tool["name"] in ("gemini_make_photo", "gemini_ask") else None
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"], {})

    def test_notifications_get_no_reply_and_unknown_things_are_errors(self):
        server, _ = self.server()
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})["error"]["code"], -32601)
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "delete_everything"}})["error"]["code"], -32602)
        self.assertEqual(server.handle({"id": 6, "method": "ping"})["error"]["code"], -32600)
        self.assertEqual(server.handle([1, 2])["error"]["code"], -32600)

    def test_tool_calls_return_json_text_and_failures_as_tool_errors(self):
        server, link = self.server()
        ok = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "gemini_list_photos", "arguments": {"candidate_id": 5}}})
        self.assertFalse(ok["result"]["isError"])
        self.assertEqual(json.loads(ok["result"]["content"][0]["text"]), {"사진": []})
        self.assertEqual(link.calls, [("list", 5)])
        bad = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "gemini_ask", "arguments": {"question": "q"}}})
        self.assertTrue(bad["result"]["isError"])
        self.assertIn("일부러 실패", bad["result"]["content"][0]["text"])
        missing = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "gemini_make_photo", "arguments": {"reference_asset_id": True}}})
        self.assertTrue(missing["result"]["isError"])
        self.assertEqual(len(link.calls), 1)                                          # 잘못된 번호로는 만들지 않는다
        server.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "gemini_make_photo", "arguments": {"reference_asset_id": 4}}})
        self.assertEqual(link.calls[-1], ("make", (4,), {"purpose": "ADDITIONAL", "backgrounds": None, "poses": None, "count": 1, "instruction": None, "confirmed_main": False}))

    def test_the_loop_writes_one_reply_per_request_line(self):
        server, _ = self.server()
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        out = io.StringIO()
        srv.serve(stdin=stdin, stdout=out, server=server)
        lines = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([l.get("id") for l in lines], [1, None])
        self.assertEqual(lines[1]["error"]["code"], -32700)

    def test_the_real_entry_point_speaks_the_protocol_without_touching_the_app(self):
        stdin = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        proc = subprocess.run([sys.executable, str(ROOT / "tools/claude_gemini_link/server.py")], input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60)
        lines = [json.loads(l) for l in proc.stdout.splitlines()]
        self.assertEqual([l["id"] for l in lines], [1, 2])
        self.assertEqual(len(lines[1]["result"]["tools"]), 4)

    def test_the_project_registers_the_link(self):
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        entry = config["mcpServers"]["homez-gemini"]
        self.assertTrue(entry["command"].replace("\\", "/").endswith("venv/Scripts/python.exe"))
        self.assertTrue(entry["args"][0].replace("\\", "/").endswith("tools/claude_gemini_link/server.py"))
        self.assertNotIn("AIza", json.dumps(config))                                   # 키는 설정 파일에 없다
        self.assertNotIn("env", entry)


class QuestionTests(Base_):

    def setUp(self):
        super().setUp()
        self.qt = FakeTransport(FakeResponse(200, answer()))

    def service(self):
        return gqs.GeminiQuestionService(self.db, self.store, transport=self.qt, now_factory=lambda: self.NOW)

    def grant_question(self, count=10):
        ExternalCallPolicyService(self.db).grant(self.company_id, system=ExternalSystem.GEMINI, kind=CallKind.WRITE, purpose=CallPurpose.GEMINI_QUESTION,
                                                 mode=AllowanceMode.COUNTED, count=count, user_id=7)

    def test_the_new_purpose_is_registered(self):
        from app.domains.external_call import constants as ec
        from app.domains.external_call.service import _DEFAULT_REQUEST as DEFAULT_PROPOSALS

        self.assertIn(CallPurpose.GEMINI_QUESTION, CallPurpose.ALL)
        self.assertIn(CallPurpose.GEMINI_QUESTION, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.GEMINI_QUESTION], "제미나이 글 질문")
        self.assertEqual(ec.PURPOSE_TARGETS[CallPurpose.GEMINI_QUESTION], ((ExternalSystem.GEMINI,), (CallKind.WRITE,)))
        self.assertEqual(DEFAULT_PROPOSALS[(CallPurpose.GEMINI_QUESTION, CallKind.WRITE)], (AllowanceMode.COUNTED, 10, None))
        self.assertLessEqual(len(CallPurpose.GEMINI_QUESTION), 40)

    def test_an_answer_is_returned_with_the_measured_cost_and_shared_credit(self):
        self.configure()
        self.grant_question()
        result = self.service().ask(self.company_id, 7, "  바디워시   상품명 추천해 줘 ", instruction="한국어로 짧게")
        self.assertEqual((result["ok"], result["answer"]), (True, "답입니다"))
        self.assertEqual(result["cost_usd"], round((1000 * 0.30 + 800 * 2.50) / 1_000_000, 6))   # 생각 토큰도 출력 값
        call = self.qt.calls[0]
        self.assertEqual(call["url"], "https://generativelanguage.googleapis.com/v1beta/interactions")
        self.assertEqual(call["headers"]["x-goog-api-key"], KEY)
        self.assertEqual(call["body"], {"model": "gemini-3.5-flash-lite", "input": "바디워시 상품명 추천해 줘", "system_instruction": "한국어로 짧게",
                                        "generation_config": {"max_output_tokens": 2048}, "store": False})
        rows = self.db.execute(text("SELECT description FROM audit_logs WHERE action='GEMINI_TEXT_USAGE'")).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(KEY, rows[0][0])
        self.assertNotIn("바디워시", rows[0][0])                                       # 질문 글은 기록하지 않는다
        status = self.ai().status(7, self.company_id)
        self.assertEqual((status["window_calls"], status["window_used_usd"]), (1, round(result["cost_usd"], 4)))   # 이미지와 같은 31일 합계

    def test_without_an_allowance_nothing_is_sent_and_a_request_is_left(self):
        self.configure()
        with self.assertRaises(ExternalCallNotAllowed):
            self.service().ask(self.company_id, 7, "질문")
        self.assertEqual(self.qt.calls, [])
        self.assertEqual(self.db.execute(text("SELECT count(*) FROM audit_logs WHERE action='GEMINI_TEXT_USAGE'")).scalar(), 0)
        pending = self.db.query(ExternalCallAllowance).filter_by(system="GEMINI", purpose="GEMINI_QUESTION", status="PENDING").count()
        self.assertEqual(pending, 1)

    def test_the_monthly_credit_is_shared_with_images_and_blocks_before_sending(self):
        self.configure(credit=0.06)
        self.add_usage(self.NOW, 0.05)                                                # 이미지로 이미 $0.05
        self.grant_question()
        with self.assertRaises(BadRequestException) as caught:
            self.service().ask(self.company_id, 7, "질문")                            # 최악 비용 약 $0.0104 → 넘는다
        self.assertIn("월 크레딧", str(caught.exception.detail))
        self.assertEqual(self.qt.calls, [])
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(purpose="GEMINI_QUESTION", status="ACTIVE").one().remaining_count, 10)

    def test_refusals_cost_nothing_and_missing_usage_costs_the_worst_case(self):
        self.configure()
        self.grant_question()
        self.qt.outcomes = [FakeResponse(402, {"error": {"message": "prepay"}}), FakeResponse(200, answer(usage=False))]
        refused = self.service().ask(self.company_id, 7, "질문")
        self.assertFalse(refused["ok"])
        self.assertIn("결제 필요", refused["detail"])
        self.assertEqual(refused["cost_usd"], 0.0)
        unknown = self.service().ask(self.company_id, 7, "질문")
        self.assertEqual(unknown["cost_usd"], gqs.worst_cost_usd("gemini-3.5-flash-lite", "질문", ""))
        self.assertNotIn(KEY, json.dumps([refused, unknown], ensure_ascii=False))

    def test_bad_input_and_unknown_models_are_refused_before_anything(self):
        self.configure()
        self.grant_question()
        for kwargs in (dict(question="   "), dict(question="가" * 8001), dict(question="q", model="gemini-9-ultra"), dict(question="q", instruction="가" * 2001)):
            question = kwargs.pop("question")
            with self.assertRaises(BadRequestException, msg=str(kwargs)[:40]):
                self.service().ask(self.company_id, 7, question, **kwargs)
        self.assertEqual(self.qt.calls, [])

    def test_without_a_key_or_credit_nothing_is_sent(self):
        self.grant_question()
        with self.assertRaises(BadRequestException):
            self.service().ask(self.company_id, 7, "질문")
        self.assertEqual(self.qt.calls, [])

    def test_parsing(self):
        self.assertEqual(gqs.parse_text(answer("a")), (True, "a", ""))
        self.assertFalse(gqs.parse_text({"status": "failed", "errors": [{"message": "blocked"}]})[0])
        self.assertFalse(gqs.parse_text({"status": "completed", "steps": []})[0])
        self.assertFalse(gqs.parse_text(None)[0])
        self.assertIsNone(gqs.actual_cost_usd("gemini-3.5-flash-lite", {"total_input_tokens": "1"}))
        self.assertEqual(gqs.actual_cost_usd("gemini-3.5-flash-lite", {"total_input_tokens": 1_000_000, "total_output_tokens": 0}), 0.30)


class LinkTests(Base_):

    def setUp(self):
        super().setUp()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, company_id INTEGER)")
            conn.exec_driver_sql(f"INSERT INTO users (id, company_id) VALUES (7, {self.company_id})")
        self.qt = FakeTransport(FakeResponse(200, answer()))
        self.link = GeminiLink(sessionmaker(bind=self.engine, expire_on_commit=False), lambda: self.store, user_id=7, media_root=self.media_root,
                               question_transport=self.qt,
                               image_client_factory=lambda api_key, model: gic.GeminiImageClient(api_key, model, transport=self.transport))

    def test_photos_are_listed_with_their_files(self):
        asset = self.upload()
        listed = self.link.list_photos(5)["사진"]
        self.assertEqual([p["사진_번호"] for p in listed], [asset.id])
        self.assertTrue(Path(listed[0]["파일"]).exists())
        self.assertEqual(self.link.list_photos(999)["사진"], [])

    def test_a_photo_is_made_and_saved_like_the_app_does(self):
        self.configure()
        self.grant(6)
        asset = self.upload()
        made = self.link.make_photo(asset.id)
        self.assertEqual(made["만듦"], 1)
        self.assertTrue(Path(made["사진"][0]["파일"]).exists())
        self.assertEqual(made["지금_더_만들_수_있는_장수"], 179)
        generated = self.db.query(MediaAsset).filter_by(id=made["사진"][0]["사진_번호"]).one()
        self.assertEqual((generated.asset_role, generated.source_asset_id), ("GENERATED", asset.id))
        body = self.transport.calls[0]["body"]
        self.assertIn("Background / scene", body["input"][0]["text"])

    def test_failures_come_back_as_readable_reasons(self):
        asset = self.upload()
        made = self.link.make_photo(asset.id)                                         # 설정 없음
        self.assertEqual(made["만듦"], 0)
        self.assertIn("API 키", made["실패_사유"])
        self.configure()
        made = self.link.make_photo(asset.id)                                         # 허락 없음
        self.assertIn("허용", made["실패_사유"])
        self.assertEqual(self.transport.calls, [])
        asked = self.link.ask("질문")                                                 # 글 질문 허락 없음
        self.assertIsNone(asked["답"])
        self.assertIn("허용", asked["실패_사유"])

    def test_ask_and_usage(self):
        self.configure()
        self.grant_question = lambda: ExternalCallPolicyService(self.db).grant(
            self.company_id, system=ExternalSystem.GEMINI, kind=CallKind.WRITE, purpose=CallPurpose.GEMINI_QUESTION, mode=AllowanceMode.COUNTED, count=3, user_id=7)
        self.grant_question()
        asked = self.link.ask("질문")
        self.assertEqual(asked["답"], "답입니다")
        usage = self.link.usage()
        self.assertEqual((usage["준비됨"], usage["키_저장됨"], usage["최근_31일_호출_수"], usage["월_크레딧_달러"]), (True, True, 1, 10.0))
        self.assertEqual(usage["최근_기록"][0]["무엇"], "ASK")
        self.assertIn("구글 AI 스튜디오 결제 화면", usage["안내"])
        self.assertNotIn(KEY, json.dumps(usage, ensure_ascii=False))


class CrossProcessLockTests(unittest.TestCase):

    def test_a_lock_held_elsewhere_stops_the_call(self):
        try:
            import msvcrt
        except ImportError:
            self.skipTest("윈도우 전용 잠금")
        path = Path(tempfile.mkdtemp()) / "gemini.lock"
        other = open(path, "a+b")
        other.seek(0)
        msvcrt.locking(other.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            with self.assertRaises(ias.CreditLimitReached) as caught:
                with ias._file_lock(path, wait_seconds=0.5):
                    self.fail("잠금을 잡으면 안 된다")
            self.assertIn("다른 제미나이 호출", caught.exception.detail)
        finally:
            other.seek(0)
            msvcrt.locking(other.fileno(), msvcrt.LK_UNLCK, 1)
            other.close()
        with ias._file_lock(path, wait_seconds=0.5):                                  # 풀리면 잡힌다
            pass


if __name__ == "__main__":
    unittest.main()
