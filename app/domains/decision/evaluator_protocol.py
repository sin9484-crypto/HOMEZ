"""
=========================================================
Homez OS

File : app/domains/decision/evaluator_protocol.py

HOMEZ V4 Decision AI — 실제 AI/LLM evaluator 연동 경계 (Phase I,
2026-07-30).

이번 단계에서 외부 AI API 사용은 필수가 아니다 — 실제 승인·요금·
네트워크 접근이 없다면 여기 정의된 계약(Protocol)만 준비하고, 현재
서비스는 계속 `app/domains/decision/service.py::evaluate_axes()`
(결정적 규칙 기반, `evaluator_kind="deterministic"`)를 사용한다.

이 파일은 **아직 DecisionService에 연결되지 않았다** — 향후 실제
LLM 기반 evaluator를 붙일 때 이 계약을 구현하는 어댑터를 하나
추가하고, `DecisionService`가 그 어댑터를 선택적으로 사용하도록
배선하는 별도 작업이 필요하다(이번 단계 범위 밖, 아래 "다음 단계"
참고). 지금 이 파일을 구현했다고 해서 "실제 AI 연동을 완료했다"고
보고하지 않는다 — 정의만 준비된 상태다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Protocol
from typing import runtime_checkable

# --------------------------------------------------
# 입력/출력 계약
# --------------------------------------------------


@dataclass(frozen=True)
class EvaluatorAxisInput:
    """평가기에 넘길 축 하나의 원시 입력 신호(있는 것만)."""

    axis: str
    raw_signal: str | None  # 원본 후보 필드 값(문자열로 정규화, Decimal 원본은 상위에서 보관)
    available: bool


@dataclass(frozen=True)
class EvaluatorInput:
    """
    evaluator에 전달되는 단일 평가 요청의 전체 입력. 후보/보강 입력의
    스냅샷이며, 이 값 자체가 `DecisionEvaluation.input_snapshot_json`
    /`input_fingerprint`의 재료가 된다(현재 deterministic 경로와 동일한
    소스).
    """

    candidate_id: int
    policy_set_id: str
    policy_version: str
    axis_inputs: tuple[EvaluatorAxisInput, ...]
    axis_weights: dict[str, str]  # Decimal 문자열 — float 금지
    requested_at: datetime


@dataclass(frozen=True)
class EvaluatorAxisResult:

    axis: str
    raw_score: str  # Decimal 문자열(0~100)
    confidence: str  # Decimal 문자열(0~1)
    data_sufficient: bool
    risk_flag: bool
    evidence_text: str


@dataclass(frozen=True)
class EvaluatorOutput:
    """
    evaluator가 반환해야 하는 전체 계약. `DecisionService`는 이 값을
    그대로 신뢰하지 않고 스키마/범위/enum을 재검증한 뒤에만 저장한다
    (아래 EvaluatorOutcome.status 참고 — PARSE_FAILED/TIMEOUT/ERROR는
    자동 승인으로 이어지지 않고 REVIEW_REQUIRED로만 이어진다).
    """

    axis_results: tuple[EvaluatorAxisResult, ...]
    model_name: str
    model_version: str
    prompt_version: str
    raw_response_fingerprint: str  # 원본 응답의 SHA-256 — 원본 자체는 저장하지 않는다(비밀정보 유출 방지)


class EvaluatorErrorKind:

    TIMEOUT = "TIMEOUT"
    PARSE_FAILED = "PARSE_FAILED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

    ALL = (TIMEOUT, PARSE_FAILED, SCHEMA_INVALID, RATE_LIMITED, UNKNOWN_ERROR)


@dataclass(frozen=True)
class EvaluatorOutcome:
    """
    evaluator 호출 1회의 최종 결과. 성공(`output`이 채워짐)이거나
    실패(`error_kind`가 채워짐) 중 하나만 유효하다 — 실패 시
    `DecisionService`는 절대 자동으로 REVIEW_REQUIRED 이외의
    추천으로 진행하지 않는다("파싱 실패 시 자동 승인 금지").
    """

    output: EvaluatorOutput | None
    error_kind: str | None
    error_detail: str | None = None


# --------------------------------------------------
# Evaluator Protocol
# --------------------------------------------------


@runtime_checkable
class DecisionEvaluatorProtocol(Protocol):
    """
    실제 LLM 기반 evaluator든 fixture든 이 인터페이스를 구현해야
    `DecisionService`가 사용할 수 있다(향후 배선 시점 기준 설계).

    필수 준수 사항(호출자가 강제):
      - timeout_seconds 안에 응답하지 않으면 EvaluatorErrorKind.TIMEOUT으로
        처리하고 REVIEW_REQUIRED로 귀결한다(자동 승인 금지).
      - JSON/스키마 검증 실패 시 EvaluatorErrorKind.PARSE_FAILED 또는
        SCHEMA_INVALID로 처리한다 — 부분 결과라도 신뢰하지 않는다.
      - raw_response에 포함된 비밀정보(API Key, 내부 프롬프트의 비밀
        지시 등)를 EvaluatorOutput에 그대로 저장하지 않는다 —
        `raw_response_fingerprint`만 남긴다.
      - retry_limit 회를 초과해 재시도하지 않는다(무한 재시도 금지,
        비용 폭주 방지).
      - 호출량 제한(`max_calls_per_minute` 등)은 이 Protocol을 구현하는
        어댑터 자신이 책임진다 — DecisionService는 단일 호출 계약만
        신경 쓴다.
    """

    def evaluate(
        self,
        evaluator_input: EvaluatorInput,
        *,
        timeout_seconds: float,
        retry_limit: int,
    ) -> EvaluatorOutcome:
        ...

    @property
    def model_name(self) -> str:
        ...

    @property
    def model_version(self) -> str:
        ...


# --------------------------------------------------
# 기본 정책 값(어댑터 구현 시 참고용 — 강제하지 않음)
# --------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_LIMIT = 1
DEFAULT_MAX_CALLS_PER_MINUTE = 30


__all__ = [
    "EvaluatorAxisInput",
    "EvaluatorInput",
    "EvaluatorAxisResult",
    "EvaluatorOutput",
    "EvaluatorErrorKind",
    "EvaluatorOutcome",
    "DecisionEvaluatorProtocol",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_RETRY_LIMIT",
    "DEFAULT_MAX_CALLS_PER_MINUTE",
]
