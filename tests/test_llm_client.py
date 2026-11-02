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
from app.domains.marketplace_listing.llm_client import DEFAULT_TIMEOUT_SECONDS
from app.domains.marketplace_listing.llm_client import MAX_OUTPUT_TOKENS_CAP
from app.domains.marketplace_listing.llm_client import THINKING_RESERVE_TOKENS
from app.domains.marketplace_listing.llm_client import THINKING_TIMEOUT_SECONDS
from app.domains.marketplace_listing.llm_client import AnthropicClient
from app.domains.marketplace_listing.llm_client import LlmError
from app.domains.marketplace_listing.llm_client import LlmIncompleteError
from app.domains.marketplace_listing.llm_client import output_token_ceiling
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


class ThinkingModelTests(unittest.TestCase):
    """5세대 모델은 생각이 켜져 있고 `max_tokens`가 생각 + 답을 함께 제한한다."""

    THINKING = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5", "claude-fable-5-1", "claude-fable-5")
    OTHERS = ("claude-opus-4-8", "claude-opus-4-5", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-test")

    def _sent(self, model, **kw):
        transport = Transport(response(200, ok_body()))
        AnthropicClient(KEY, model, transport=transport, **kw).complete("s", "u", max_output_tokens=500)
        _, _, body, timeout = transport.calls[0]
        return body, timeout

    def test_thinking_models_get_room_for_thinking_low_effort_and_a_longer_wait(self):
        for model in self.THINKING:
            body, timeout = self._sent(model)
            self.assertEqual(body["max_tokens"], 500 + THINKING_RESERVE_TOKENS, model)
            self.assertEqual(body["output_config"], {"effort": "low"}, model)
            self.assertEqual(timeout, THINKING_TIMEOUT_SECONDS, model)
            self.assertNotIn("thinking", body)                          # 끄는 값은 이 모델들에서 400이다

    def test_other_models_are_sent_exactly_as_before(self):
        for model in self.OTHERS:
            body, timeout = self._sent(model)
            self.assertEqual(body["max_tokens"], 500, model)
            self.assertNotIn("output_config", body, model)              # 노력 수준을 받지 않는 모델이 있다
            self.assertEqual(timeout, DEFAULT_TIMEOUT_SECONDS, model)

    def test_an_explicit_timeout_wins(self):
        self.assertEqual(self._sent("claude-opus-5-5", timeout=7)[1], 7)
        self.assertEqual(self._sent("claude-test", timeout=7)[1], 7)

    def test_the_ceiling_caps_the_answer_then_adds_the_reserve(self):
        self.assertEqual(output_token_ceiling("claude-opus-5-5", 10**9), MAX_OUTPUT_TOKENS_CAP + THINKING_RESERVE_TOKENS)
        self.assertEqual(output_token_ceiling("claude-opus-5-5", 0), 1 + THINKING_RESERVE_TOKENS)
        self.assertEqual(output_token_ceiling("claude-test", 10**9), MAX_OUTPUT_TOKENS_CAP)
        self.assertEqual(output_token_ceiling("claude-test", 0), 1)

    def test_a_leading_thinking_block_is_ignored(self):
        body = ok_body()
        body["content"] = [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "답"}]
        body["stop_reason"] = "end_turn"
        c, _ = client(response(200, body))
        self.assertEqual(c.complete("s", "u").text, "답")


class IncompleteResponseTests(unittest.TestCase):

    def _body(self, stop_reason, content=None):
        body = ok_body("잘린 글", 321, 45)
        body["stop_reason"] = stop_reason
        if content is not None:
            body["content"] = content
        return body

    def test_finished_answers_are_returned(self):
        for reason in ("end_turn", "stop_sequence"):
            c, _ = client(response(200, self._body(reason)))
            self.assertEqual(c.complete("s", "u").text, "잘린 글")

    def test_a_cut_off_or_refused_answer_is_not_a_value_and_keeps_its_usage(self):
        for reason in ("max_tokens", "refusal", "pause_turn", "model_context_window_exceeded", "something_new", 7):
            c, _ = client(response(200, self._body(reason)))
            with self.assertRaises(LlmIncompleteError) as ctx:
                c.complete("비밀 시스템 글", "비밀 사용자 글")
            exc = ctx.exception
            self.assertIsInstance(exc, LlmError)
            self.assertEqual((exc.input_tokens, exc.output_tokens, exc.model), (321, 45, "claude-test"))
            for secret in ("잘린 글", KEY, "비밀 시스템 글", "비밀 사용자 글"):
                self.assertNotIn(secret, str(exc))

    def test_each_reason_says_what_happened(self):
        def message(reason):
            c, _ = client(response(200, self._body(reason)))
            with self.assertRaises(LlmIncompleteError) as ctx:
                c.complete("s", "u")
            return str(ctx.exception)

        self.assertIn("잘려", message("max_tokens"))
        self.assertIn("답하지 않았어요", message("refusal"))
        self.assertIn("끝까지 받지 못해", message("pause_turn"))

    def test_thinking_with_no_text_keeps_its_usage(self):
        for reason in ("end_turn", "max_tokens", None):
            c, _ = client(response(200, self._body(reason, [{"type": "thinking", "thinking": ""}])))
            with self.assertRaises(LlmIncompleteError) as ctx:
                c.complete("s", "u")
            self.assertEqual(ctx.exception.output_tokens, 45)

    def test_an_unreadable_usage_stays_a_plain_failure(self):
        body = self._body("max_tokens")
        del body["usage"]
        c, _ = client(response(200, body))
        with self.assertRaises(LlmError) as ctx:
            c.complete("s", "u")
        self.assertNotIsInstance(ctx.exception, LlmIncompleteError)


if __name__ == "__main__":
    unittest.main()
