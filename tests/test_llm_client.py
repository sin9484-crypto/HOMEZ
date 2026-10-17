"""
=========================================================
Homez OS

File : tests/test_llm_client.py

2026-10-18 — 등록 도우미 AI 초안(Phase B)의 Claude 클라이언트(`llm_client.py`) 규칙을 고정한다(가짜 전송, 바깥 호출 0).

  - 요청은 Anthropic Messages API 모양이고 키는 헤더로만 간다(본문·오류 문구에 들어가지 않는다).
  - 응답에서 글과 사용량(입력·출력 토큰)만 꺼낸다. 모양이 다르거나 오류면 `LlmError`다(값을 지어내지 않는다).
  - 출력 토큰 상한은 클라이언트 쪽에서도 한 번 더 막는다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.llm_client import API_URL
from app.domains.marketplace_listing.llm_client import MAX_OUTPUT_TOKENS_CAP
from app.domains.marketplace_listing.llm_client import AnthropicClient
from app.domains.marketplace_listing.llm_client import LlmError
from tests.test_coupang_invoice_upload import response

KEY = "sk-ant-TEST-0123456789abcdefghij"


class Transport:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def post(self, url, *, headers, json_body, timeout):
        self.calls.append((url, headers, json_body, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def ok_body(text='{"a":1}', input_tokens=120, output_tokens=60):
    return {"id": "m", "type": "message", "model": "claude-test", "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


def client(outcome):
    transport = Transport(outcome)
    return AnthropicClient(KEY, "claude-test", transport=transport), transport


class RequestShapeTests(unittest.TestCase):

    def test_the_request_follows_the_messages_api_and_keeps_the_key_in_the_header_only(self):
        c, t = client(response(200, ok_body()))
        c.complete("시스템", "사용자 글", max_output_tokens=500)
        url, headers, body, timeout = t.calls[0]
        self.assertEqual(url, API_URL)
        self.assertTrue(url.startswith("https://api.anthropic.com/"))
        self.assertEqual(headers["x-api-key"], KEY)
        self.assertIn("anthropic-version", headers)
        self.assertEqual((body["model"], body["max_tokens"], body["system"]), ("claude-test", 500, "시스템"))
        self.assertEqual(body["messages"], [{"role": "user", "content": "사용자 글"}])
        self.assertNotIn(KEY, repr(body))
        self.assertGreater(timeout, 0)

    def test_the_output_limit_is_clamped(self):
        c, t = client(response(200, ok_body()))
        c.complete("s", "u", max_output_tokens=10**9)
        self.assertEqual(t.calls[0][2]["max_tokens"], MAX_OUTPUT_TOKENS_CAP)
        c.complete("s", "u", max_output_tokens=0)
        self.assertEqual(t.calls[1][2]["max_tokens"], 1)

    def test_a_missing_key_or_model_never_reaches_the_network(self):
        for key, model in (("", "m"), ("k", "")):
            transport = Transport(response(200, ok_body()))
            with self.assertRaises(LlmError):
                AnthropicClient(key, model, transport=transport).complete("s", "u")
            self.assertEqual(transport.calls, [])


class ResponseTests(unittest.TestCase):

    def test_text_and_usage_are_returned(self):
        c, _ = client(response(200, ok_body('{"x":"y"}', 321, 45)))
        result = c.complete("s", "u")
        self.assertEqual((result.text, result.input_tokens, result.output_tokens, result.model), ('{"x":"y"}', 321, 45, "claude-test"))

    def test_several_text_blocks_are_joined_and_other_blocks_ignored(self):
        body = ok_body()
        body["content"] = [{"type": "text", "text": "가"}, {"type": "tool_use", "name": "x"}, {"type": "text", "text": "나"}]
        c, _ = client(response(200, body))
        self.assertEqual(c.complete("s", "u").text, "가나")

    def test_errors_and_odd_shapes_are_failures_not_values(self):
        no_usage = ok_body()
        del no_usage["usage"]
        bool_usage = ok_body(input_tokens=True)
        no_text = ok_body()
        no_text["content"] = []
        for outcome in (
            response(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}),
            response(429, {"error": {"type": "rate_limit_error"}}), response(500, None), response(200, None),
            response(200, [1]), response(200, no_usage), response(200, bool_usage), response(200, no_text),
        ):
            c, _ = client(outcome)
            with self.assertRaises(LlmError):
                c.complete("s", "u")

    def test_an_error_message_never_contains_the_key_or_the_request(self):
        class Boom(Exception):
            pass

        for outcome in (Boom(f"connect failed for key {KEY}"), response(401, {"error": {"type": "authentication_error", "message": KEY}})):
            c, _ = client(outcome)
            with self.assertRaises(LlmError) as ctx:
                c.complete("비밀 시스템 글", "비밀 사용자 글")
            for secret in (KEY, "비밀 시스템 글", "비밀 사용자 글"):
                self.assertNotIn(secret, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
