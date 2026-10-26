"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_call_center_reply_provider.py

2026-10-26 — 쿠팡 쓰기 6번: **쿠팡 고객센터(콜센터) 이관 문의 답변**(외부 변경 — 고객 상담에 바로 쓰인다). `CoupangChannelControlProvider`에 섞어 쓴다.

공식 문서 확인(2026-10-26, developers.coupang.com):
  - 고객센터 문의 조회(목록)  GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/callCenterInquiries
      partnerCounselingStatus(NONE|ANSWER|NO_ANSWER|TRANSFER), inquiryStartAt/EndAt(최대 7일), pageNum, pageSize(최대 30).
  - 고객센터 문의 단건 조회  GET /v2/providers/openapi/apis/api/v5/vendors/callCenterInquiries/{inquiryId}  (경로에 vendorId 없음)
      data: inquiryId·inquiryStatus(progress|complete)·csPartnerCounselingStatus·vendorItemId·itemName·content·inquiryAt·orderId·
      replies[](answerId·parentAnswerId·partnerTransferStatus(none|requestAnswer|answered)·answerType(csAgent|vendor)·needAnswer·replyAt·content).
      "과도하게 조회하면 시스템이 자동으로 차단할 수 있다."
  - 고객센터 문의답변  POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/replies
      본문 {vendorId, inquiryId, content(2~1,000자, 줄바꿈 \n), replyBy(WING 아이디), parentAnswerId(답할 이관글의 answerId)}.
      inquiryStatus=progress 이고 이관글이 partnerTransferStatus=requestAnswer일 때만. 24시간 넘게 미답변이면 쿠팡이 자동 안내로 닫아 API로 답할 수 없다.
      성공 {"code":"200","message":"OK"}, 실패 400(답변 불가 상태·길이·parentAnswerId·userId 520/521·상담 종료 522).

  - 고객센터 문의확인  POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/confirms  (2026-10-27 확인)
      본문 {confirmBy: WING 아이디}. 쿠팡이 상담을 마친 업체 이관 문의(미확인 TRANSFER)를 판매자가 확인 처리한다. 상담사가 종료한 문의(522)·
      이미 확인한 문의·확인 요청 뒤 24시간이 지난 문의는 400. 확인 뒤 상태 값은 문서에 없다 — 미확인 목록에서 빠졌는지로만 본다.

규칙:
  - 구매자 전화번호(buyerPhone)·이메일, 상담사 이름·아이디(receptionistName·receptionist)는 읽지 않는다. 문의 글과 이관글 내용은 답을 쓰는 사람에게
    보여 주기 위해서만 읽고 이 모듈은 저장·로그하지 않는다.
  - 답할 이관글(parentAnswerId)은 '답변 요청' 상태인 상담사 글이 **정확히 하나**일 때만 고른다. 여럿이거나 없으면 추측하지 않는다.
  - 응답이 예상 모양이 아니면 추측하지 않고 실패(ok=False)로 돌려준다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.domains.purchase_task.coupang_after_sales_provider import CALL_CENTER_INQUIRIES_PATH
from app.domains.purchase_task.coupang_after_sales_provider import AfterSalesRead
from app.domains.purchase_task.coupang_inquiry_reply_provider import normalize_reply

CALL_CENTER_ONE_PATH = "/v2/providers/openapi/apis/api/v5/vendors/callCenterInquiries/{inquiry_id}"
CALL_CENTER_REPLY_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/callCenterInquiries/{inquiry_id}/replies"
CALL_CENTER_CONFIRM_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/callCenterInquiries/{inquiry_id}/confirms"
MIN_CC_REPLY_CHARS = 2       # 공식: 2~1,000자
MAX_CC_REPLY_CHARS = 1000
MAX_CC_TEXT_CHARS = 2000     # 화면에 보여 줄 문의 글·이관글 상한
MAX_CC_ID_DIGITS = 20


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None and not isinstance(value, (dict, list, bool)) else ""
    return text or None


def _id(value: Any) -> str | None:
    text = _text(value)
    return text if text and text.isdigit() and len(text) <= MAX_CC_ID_DIGITS else None


def valid_cc_id(value: Any) -> bool:
    return not isinstance(value, bool) and _id(value) is not None


def cc_reply_problem(content: Any) -> str | None:
    """공식 길이(2~1,000자) 검사 — 문제가 있으면 이유 코드."""

    if not isinstance(content, str):
        return "INVALID_CONTENT"
    text = normalize_reply(content)
    return None if MIN_CC_REPLY_CHARS <= len(text) <= MAX_CC_REPLY_CHARS else "INVALID_CONTENT"


def parse_call_center_inquiry(item: dict) -> dict | None:
    """목록·단건 공통 해석. 개인정보 필드는 읽지 않는다. 답할 이관글이 정확히 하나면 pending_answer_id에 담는다."""

    inquiry_id = _id(item.get("inquiryId"))
    if inquiry_id is None:
        return None
    raw = item.get("replies")
    replies = []
    for r in (raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            continue
        content = r.get("content") if isinstance(r.get("content"), str) else ""
        replies.append({
            "answer_id": _id(r.get("answerId")), "parent_answer_id": _id(r.get("parentAnswerId")),
            "transfer_status": _text(r.get("partnerTransferStatus")), "answer_type": _text(r.get("answerType")),
            "need_answer": r.get("needAnswer") is True, "reply_at": _text(r.get("replyAt")), "content": content[:MAX_CC_TEXT_CHARS],
        })
    pending = [r for r in replies if r["answer_type"] == "csAgent" and r["transfer_status"] == "requestAnswer" and r["answer_id"]]
    agents = [r for r in replies if r["answer_type"] == "csAgent" and r["content"]]
    question = item.get("content") if isinstance(item.get("content"), str) else ""
    vendor_item = item.get("vendorItemId")
    if isinstance(vendor_item, list):  # 문서 예시는 배열, 표는 숫자 — 둘 다 받는다
        vendor_item = vendor_item[0] if vendor_item else None
    return {
        "inquiry_id": inquiry_id, "inquiry_status": _text(item.get("inquiryStatus")),
        "counseling_status": _text(item.get("csPartnerCounselingStatus")), "inquiry_at": _text(item.get("inquiryAt")),
        "order_id": _text(item.get("orderId")), "vendor_item_id": _text(vendor_item), "item_name": _text(item.get("itemName")),
        "category": _text(item.get("receiptCategory")), "question": question[:MAX_CC_TEXT_CHARS],
        "replies": replies, "pending_count": len(pending),
        "pending_answer_id": pending[0]["answer_id"] if len(pending) == 1 else None,
        "pending_text": pending[0]["content"] if len(pending) == 1 else None,
        # 상담이 끝난 이관 문의를 확인할 때 보여 줄 쿠팡 상담사의 마지막 글(답변 목록의 마지막 상담사 글)
        "agent_text": agents[-1]["content"] if agents else None,
    }


@dataclass(frozen=True)
class CallCenterSnapshot:
    ok: bool
    row: dict | None = None
    reply_ready: bool = False  # 자격증명에 WING 아이디(replyBy)가 있는가
    detail: str = ""
    http_status: int | None = None


class _CallCenterReplyMixin:
    """`self._send`·`self._write`·`self._classify_status`·`self.vendor_id`·`self._wing_user_id`·`_read_inquiries`가 있는 제공자에 섞는다."""

    def read_call_center_for_reply(self, *, days: int = 7) -> AfterSalesRead:
        """답하지 않은 고객센터 이관 문의 목록(최근 7일, 쪽당 30). 개인정보는 읽지 않는다."""

        return self._read_inquiries(
            CALL_CENTER_INQUIRIES_PATH, {"partnerCounselingStatus": "NO_ANSWER"}, page_size=30, days=days,
            parse=parse_call_center_inquiry,
        )

    def read_call_center_transfers(self, *, days: int = 7) -> AfterSalesRead:
        """확인을 기다리는 업체 이관 문의(미확인 TRANSFER, 최근 7일, 쪽당 30). 개인정보는 읽지 않는다."""

        return self._read_inquiries(
            CALL_CENTER_INQUIRIES_PATH, {"partnerCounselingStatus": "TRANSFER"}, page_size=30, days=days,
            parse=parse_call_center_inquiry,
        )

    def validate_call_center_confirm(self, inquiry_id: Any):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult

        if not valid_cc_id(inquiry_id):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_TARGET", "상담번호가 숫자가 아니라 요청하지 않았다")
        if not getattr(self, "_wing_user_id", ""):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "MISSING_WING_USER_ID",
                "쿠팡 고객센터 문의확인 API가 요구하는 판매자 WING 아이디(confirmBy)가 자격증명에 없어 요청하지 않았다 — 추측해서 채우지 않는다",
            )
        return None

    def confirm_call_center(self, inquiry_id: Any):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult
        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        invalid = self.validate_call_center_confirm(inquiry_id)
        if invalid is not None:
            return invalid
        path = CALL_CENTER_CONFIRM_PATH.format(vendor_id=quote(self.vendor_id, safe=""), inquiry_id=_id(inquiry_id))
        sent = self._write("POST", path, {"confirmBy": self._wing_user_id})
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent) or {}
        code = str(body.get("code") or "").strip().upper()
        if code in ("200", "SUCCESS"):
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if code == "ERROR":
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE", "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_call_center_confirm_applied(self, inquiry_id: Any):
        """결과불명 대조 — 미확인 목록에 그대로 있으면 False(안 받음). 목록에서 빠졌으면 확인됐는지 24시간이 지났는지 알 수 없어
        판단하지 않는다(ok=False) — 확인 뒤 상태 값은 문서에 없다."""

        from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult

        read = self.read_call_center_transfers()
        if not read.ok:
            return ProviderReadResult(False, None, read.detail, read.http_status)
        target = _id(inquiry_id)
        if any(r["inquiry_id"] == target for r in read.rows):
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(False, None, "미확인 목록에서 빠졌다(확인됐거나 24시간이 지났을 수 있다) — 윙에서 확인 필요", 200)

    def read_call_center_inquiry(self, inquiry_id: Any) -> CallCenterSnapshot:
        import requests

        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        ready = bool(getattr(self, "_wing_user_id", ""))
        if not valid_cc_id(inquiry_id):
            return CallCenterSnapshot(False, reply_ready=ready, detail="상담번호 형식 오류")
        target = _id(inquiry_id)
        try:
            response = self._send("GET", CALL_CENTER_ONE_PATH.format(inquiry_id=target))
        except requests.RequestException:
            return CallCenterSnapshot(False, reply_ready=ready, detail="조회 통신 오류")
        if response.status_code != 200:
            return CallCenterSnapshot(False, reply_ready=ready, detail=_summary(_body(response)), http_status=response.status_code)
        data = (_body(response) or {}).get("data")
        row = parse_call_center_inquiry(data) if isinstance(data, dict) else None
        if row is None or row["inquiry_id"] != target:
            return CallCenterSnapshot(False, reply_ready=ready, detail="고객센터 문의 응답 해석 불가", http_status=200)
        return CallCenterSnapshot(True, row, ready, "", 200)

    def validate_call_center_reply(self, inquiry_id: Any, parent_answer_id: Any, content: Any):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult

        if not valid_cc_id(inquiry_id) or not valid_cc_id(parent_answer_id):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_TARGET", "상담번호나 이관글 번호가 숫자가 아니라 요청하지 않았다")
        if cc_reply_problem(content) is not None:
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_CONTENT",
                f"답변은 {MIN_CC_REPLY_CHARS}~{MAX_CC_REPLY_CHARS:,}자여야 해서 요청하지 않았다",
            )
        if not getattr(self, "_wing_user_id", ""):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "MISSING_WING_USER_ID",
                "쿠팡 고객센터 답변 API가 요구하는 판매자 WING 아이디(replyBy)가 자격증명에 없어 요청하지 않았다 — 추측해서 채우지 않는다",
            )
        return None

    def reply_call_center(self, inquiry_id: Any, parent_answer_id: Any, content: str):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult
        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        invalid = self.validate_call_center_reply(inquiry_id, parent_answer_id, content)
        if invalid is not None:
            return invalid
        target, parent = _id(inquiry_id), _id(parent_answer_id)
        path = CALL_CENTER_REPLY_PATH.format(vendor_id=quote(self.vendor_id, safe=""), inquiry_id=target)
        payload = {"vendorId": self.vendor_id, "inquiryId": target, "content": normalize_reply(content),
                   "replyBy": self._wing_user_id, "parentAnswerId": parent}
        sent = self._write("POST", path, payload)
        if isinstance(sent, ProviderCallResult):
            return sent
        failure = self._classify_status(sent)
        if failure is not None:
            return failure
        body = _body(sent) or {}
        code = str(body.get("code") or "").strip().upper()
        if code in ("200", "SUCCESS"):
            return ProviderCallResult(CallKind.SUCCEEDED, 200, None, _summary(body))
        if code == "ERROR":
            return ProviderCallResult(CallKind.ACTION_REQUIRED, 200, "REJECTED", _summary(body))
        return ProviderCallResult(
            CallKind.UNKNOWN, 200, "INVALID_RESPONSE", "200 응답이지만 성공·실패를 판정할 수 없는 본문이라 적용 여부를 알 수 없다",
        )

    def read_call_center_reply_applied(self, inquiry_id: Any, parent_answer_id: Any, content: str):
        """결과불명 대조 — 우리 답(판매자 글, 같은 내용)이 있으면 True. 이관글이 아직 '답변 요청'이고 판매자 글이 없으면 False(안 받음).
        그 밖(다른 판매자 글만 있음, 쿠팡이 자동으로 닫음 등)은 판단하지 않는다(ok=False) — 두 번째 답을 보내지 않게 한다."""

        from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult

        snap = self.read_call_center_inquiry(inquiry_id)
        if not snap.ok:
            return ProviderReadResult(False, None, snap.detail, snap.http_status)
        mine = normalize_reply(content)
        replies = snap.row["replies"]
        vendor = [r for r in replies if r["answer_type"] == "vendor"]
        if any(normalize_reply(r["content"]) == mine for r in vendor):
            return ProviderReadResult(True, True, "", 200)
        parent = next((r for r in replies if r["answer_id"] == _id(parent_answer_id)), None)
        if not vendor and parent is not None and parent["transfer_status"] == "requestAnswer":
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(False, None, "답변 상태를 판단할 수 없다 — 윙 쿠팡CS문의에서 확인 필요", 200)


__all__ = [
    "_CallCenterReplyMixin", "CallCenterSnapshot", "parse_call_center_inquiry", "cc_reply_problem", "valid_cc_id",
    "CALL_CENTER_ONE_PATH", "CALL_CENTER_REPLY_PATH", "CALL_CENTER_CONFIRM_PATH", "MIN_CC_REPLY_CHARS", "MAX_CC_REPLY_CHARS",
]
