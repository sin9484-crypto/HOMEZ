"""
=========================================================
Homez OS

File : app/domains/notification_center/email_templates.py

Gate PT-3(2026-08-23 17차 지시) — 중앙 알림 이메일 본문. 이벤트마다
다른 템플릿을 따로 만들지 않고, 지시문이 요구한 고정 구조(서비스명·
심각도·작업명·사유·대상 식별정보·금액 요약·마감시간·확인 링크·
"이 이메일에서는 승인·결제할 수 없습니다" 문구)를 모든 이벤트에
동일하게 적용하는 단일 렌더러를 쓴다 — 구조 자체가 보안 요구사항
(이메일만으로 승인 불가)이므로 이벤트별로 문구가 흔들리면 안 된다.
=========================================================
"""

from __future__ import annotations

from app.domains.notification_center.event_catalog import get_event_definition

_SEVERITY_LABEL = {
    "ko-KR": {
        "CRITICAL": "긴급", "HIGH": "높음", "MEDIUM": "보통", "LOW": "낮음",
    },
    "en-US": {
        "CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low",
    },
}

_DISCLAIMER = {
    "ko-KR": "이 이메일에서는 승인·결제할 수 없습니다 — HOMEZ에 로그인해 직접 확인·처리하세요.",
    "en-US": "You cannot approve or pay from this email — sign in to HOMEZ to review and act.",
}


def render_subject(event_code: str, locale: str = "ko-KR") -> str:

    definition = get_event_definition(event_code)
    label = definition.description_ko if locale == "ko-KR" else definition.description_en
    prefix = "[HOMEZ]"
    return f"{prefix} {label}"


def render_body(
    event_code: str, locale: str = "ko-KR", *,
    entity_summary: str = "", reason: str = "", amount_summary: str = "",
    deadline_text: str = "", console_url: str = "",
) -> str:
    """지시문 필수 이메일 내용을 고정된 순서로 담는다. 어떤 값도
    비민감 식별정보만 담아야 한다는 책임은 호출자에게 있다(전체
    배송지·전화번호·카드정보·비밀번호·Credential은 여기 어디에도
    올려서는 안 된다는 계약)."""

    definition = get_event_definition(event_code)
    locale = locale if locale in ("ko-KR", "en-US") else "ko-KR"
    severity_label = _SEVERITY_LABEL[locale][definition.severity]
    task_name = (
        definition.description_ko if locale == "ko-KR" else definition.description_en
    )

    if locale == "ko-KR":
        lines = [
            "HOMEZ 운영 알림",
            f"심각도: {severity_label}",
            f"필요한 작업: {task_name}",
        ]
        if reason:
            lines.append(f"사유: {reason}")
        if entity_summary:
            lines.append(f"대상: {entity_summary}")
        if amount_summary:
            lines.append(f"금액/이익/위험 요약: {amount_summary}")
        if deadline_text:
            lines.append(f"처리 마감: {deadline_text}")
        if console_url:
            lines.append(f"HOMEZ에서 확인하기: {console_url}")
        lines.append("")
        lines.append(_DISCLAIMER["ko-KR"])
    else:
        lines = [
            "HOMEZ Operations Notification",
            f"Severity: {severity_label}",
            f"Action needed: {task_name}",
        ]
        if reason:
            lines.append(f"Reason: {reason}")
        if entity_summary:
            lines.append(f"Target: {entity_summary}")
        if amount_summary:
            lines.append(f"Amount/profit/risk summary: {amount_summary}")
        if deadline_text:
            lines.append(f"Deadline: {deadline_text}")
        if console_url:
            lines.append(f"Open in HOMEZ: {console_url}")
        lines.append("")
        lines.append(_DISCLAIMER["en-US"])

    return "\n".join(lines)


__all__ = ["render_subject", "render_body"]
