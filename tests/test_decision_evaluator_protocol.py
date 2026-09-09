"""
=========================================================
Homez OS

File : tests/test_decision_evaluator_protocol.py

HOMEZ V4 Decision AI — evaluator protocol 계약 구조 검증 (Phase I,
2026-07-30). 이 Protocol은 아직 DecisionService에 연결되지 않았다 —
여기서는 계약 자체(필드/기본값/타임아웃-실패 시 REVIEW_REQUIRED 귀결
설계)만 구조적으로 검증한다. 실제 LLM 호출 통합 테스트는 아니다.
=========================================================
"""

import unittest
from datetime import datetime

from app.domains.decision.evaluator_protocol import DEFAULT_RETRY_LIMIT
from app.domains.decision.evaluator_protocol import DEFAULT_TIMEOUT_SECONDS
from app.domains.decision.evaluator_protocol import DecisionEvaluatorProtocol
from app.domains.decision.evaluator_protocol import EvaluatorAxisInput
from app.domains.decision.evaluator_protocol import EvaluatorAxisResult
from app.domains.decision.evaluator_protocol import EvaluatorErrorKind
from app.domains.decision.evaluator_protocol import EvaluatorInput
from app.domains.decision.evaluator_protocol import EvaluatorOutcome
from app.domains.decision.evaluator_protocol import EvaluatorOutput


class _FixtureEvaluator:
    """이 Protocol을 실제로 만족하는 최소 구현 — fixture 전용, 실제 AI 아님."""

    model_name = "fixture"
    model_version = "0.0.1"

    def evaluate(self, evaluator_input, *, timeout_seconds, retry_limit):

        results = tuple(
            EvaluatorAxisResult(
                axis=axis_input.axis,
                raw_score="0",
                confidence="0",
                data_sufficient=False,
                risk_flag=False,
                evidence_text="fixture",
            )
            for axis_input in evaluator_input.axis_inputs
        )

        output = EvaluatorOutput(
            axis_results=results,
            model_name=self.model_name,
            model_version=self.model_version,
            prompt_version="n/a",
            raw_response_fingerprint="0" * 64,
        )

        return EvaluatorOutcome(output=output, error_kind=None)


class DecisionEvaluatorProtocolTestCase(unittest.TestCase):

    def test_fixture_evaluator_satisfies_protocol(self):

        evaluator = _FixtureEvaluator()

        self.assertIsInstance(evaluator, DecisionEvaluatorProtocol)

    def test_evaluator_input_holds_decimal_strings_not_float(self):

        evaluator_input = EvaluatorInput(
            candidate_id=1,
            policy_set_id="test",
            policy_version="1.0.0",
            axis_inputs=(
                EvaluatorAxisInput(axis="REVENUE_POTENTIAL", raw_signal=None, available=False),
            ),
            axis_weights={"REVENUE_POTENTIAL": "0.1000"},
            requested_at=datetime.utcnow(),
        )

        for value in evaluator_input.axis_weights.values():
            self.assertIsInstance(value, str)
            self.assertNotIsInstance(value, float)

    def test_timeout_error_kind_does_not_carry_output(self):
        """
        타임아웃/파싱 실패 시 output이 없어야 하고, 호출자가 이를
        REVIEW_REQUIRED로만 귀결시켜야 한다(자동 승인 금지) — 이
        테스트는 EvaluatorOutcome 자체가 그 두 값을 상호 배타적으로
        표현할 수 있음을 확인한다.
        """

        outcome = EvaluatorOutcome(
            output=None, error_kind=EvaluatorErrorKind.TIMEOUT, error_detail="10s exceeded",
        )

        self.assertIsNone(outcome.output)
        self.assertEqual(outcome.error_kind, EvaluatorErrorKind.TIMEOUT)

    def test_error_kind_is_one_of_allowed_enum(self):

        self.assertIn(EvaluatorErrorKind.TIMEOUT, EvaluatorErrorKind.ALL)
        self.assertIn(EvaluatorErrorKind.PARSE_FAILED, EvaluatorErrorKind.ALL)
        self.assertIn(EvaluatorErrorKind.SCHEMA_INVALID, EvaluatorErrorKind.ALL)

    def test_default_limits_are_finite_and_positive(self):

        self.assertGreater(DEFAULT_TIMEOUT_SECONDS, 0)
        self.assertGreaterEqual(DEFAULT_RETRY_LIMIT, 0)


if __name__ == "__main__":
    unittest.main()
