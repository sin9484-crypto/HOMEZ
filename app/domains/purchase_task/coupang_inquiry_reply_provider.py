"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_inquiry_reply_provider.py

2026-10-25 — 쿠팡 쓰기 4번: **상품 문의 답변**(외부 변경 — 고객에게 바로 보인다). `CoupangChannelControlProvider`에 섞어 쓴다.

공식 문서 확인(2026-10-25, developers.coupang.com):
  - 상품별 고객문의 조회  GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/onlineInquiries
      answeredType(ALL|ANSWERED|NOANSWER), inquiryStartAt/EndAt(yyyy-MM-dd, 최대 7일), pageNum, pageSize(최대 50).
      data.content[]: inquiryId·sellerProductId·vendorItemId·content(문의 글)·inquiryAt·orderIds·commentDtoList[](판매자 답변: content·inquiryCommentAt).
  - 상품별 고객문의 답변  POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/onlineInquiries/{inquiryId}/replies
      본문 {content, vendorId, replyBy(WING 아이디)}, 줄바꿈은 \n. 성공 {"code":"200","message":"OK"}. 실패는 400
      (replyBy 오류·삭제된 문의·내용 없음·"동일한 답변이 존재합니다"). 답변 수정·삭제 API는 문서에 없다(🔴 고칠 수 있는지 모름).

규칙:
  - replyBy는 자격증명의 wing_user_id(주문 취소와 같은 값)만 쓴다. 없으면 추측하지 않고 보내지 않는다.
  - 문의 글은 답을 쓰는 사람에게 보여 주기 위해서만 읽는다 — 이 모듈은 저장·로그하지 않는다. 구매자 개인정보 필드는 읽지 않는다.
  - 응답이 예상 모양이 아니면 추측하지 않고 실패(ok=False)로 돌려준다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.domains.purchase_task.coupang_after_sales_provider import ONLINE_INQUIRIES_PATH
from app.domains.purchase_task.coupang_after_sales_provider import AfterSalesRead
from app.domains.purchase_task.coupang_after_sales_provider import _int
from app.domains.purchase_task.coupang_after_sales_provider import _text

INQUIRY_REPLY_PATH = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/onlineInquiries/{inquiry_id}/replies"
MAX_REPLY_CHARS = 1000          # HOMEZ 자체 상한(쿠팡 문서에 길이 제한 없음)
MAX_QUESTION_CHARS = 2000       # 화면에 보여 줄 문의 글 상한
MAX_INQUIRY_ID_DIGITS = 20


def normalize_reply(text: str) -> str:
    """줄바꿈만 \\n으로 맞추고 앞뒤 공백을 걷는다(내용은 고쳐 쓰지 않는다)."""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


@dataclass(frozen=True)
class InquiryLookup:
    """문의 1건 찾기 결과. ok=False면 found·row는 알 수 없다. 최근 7일 밖이거나 목록이 잘렸으면 found=False·truncated로 알린다."""

    ok: bool
    found: bool = False
    row: dict | None = None
    truncated: bool = False
    reply_ready: bool = False  # 자격증명에 WING 아이디(replyBy)가 있는가
    detail: str = ""
    http_status: int | None = None


class _InquiryReplyMixin:
    """`self._send`·`self._write`·`self._classify_status`·`self.vendor_id`·`self._wing_user_id`·`_read_inquiries`가 있는 제공자에 섞는다."""

    @staticmethod
    def _parse_inquiry_for_reply(item: dict) -> dict | None:
        inquiry_id = _int(item.get("inquiryId"))
        if inquiry_id is None:
            return None
        raw = item.get("commentDtoList")
        comments = [
            {"content": c.get("content") if isinstance(c.get("content"), str) else "", "at": _text(c.get("inquiryCommentAt"))}
            for c in (raw if isinstance(raw, list) else []) if isinstance(c, dict)
        ]
        question = item.get("content") if isinstance(item.get("content"), str) else ""
        return {
            "inquiry_id": inquiry_id, "seller_product_id": _text(item.get("sellerProductId")),
            "vendor_item_id": _text(item.get("vendorItemId")), "inquiry_at": _text(item.get("inquiryAt")),
            "question": question[:MAX_QUESTION_CHARS], "question_clipped": len(question) > MAX_QUESTION_CHARS,
            "order_count": len(item.get("orderIds")) if isinstance(item.get("orderIds"), list) else 0,
            "comments": comments, "answered": bool(comments),
        }

    def read_inquiries_for_reply(self, *, days: int = 7, answered_type: str = "NOANSWER") -> AfterSalesRead:
        """답을 쓰기 위한 상품 문의 목록(문의 글 포함). 기본은 답하지 않은 것만."""

        if answered_type not in ("ALL", "ANSWERED", "NOANSWER"):
            return AfterSalesRead(False, detail="조회 조건 오류")
        return self._read_inquiries(
            ONLINE_INQUIRIES_PATH, {"answeredType": answered_type}, page_size=50, days=days, parse=self._parse_inquiry_for_reply,
        )

    def find_online_inquiry(self, inquiry_id: Any, *, days: int = 7) -> InquiryLookup:
        """최근 `days`일(최대 7) 상품 문의 전체에서 1건을 찾는다(답한 것 포함 — 이미 답했는지 알아야 한다)."""

        target = str(inquiry_id or "").strip()
        ready = bool(getattr(self, "_wing_user_id", ""))
        if not target.isdigit() or len(target) > MAX_INQUIRY_ID_DIGITS:
            return InquiryLookup(False, detail="문의 번호 형식 오류", reply_ready=ready)
        read = self.read_inquiries_for_reply(days=days, answered_type="ALL")
        if not read.ok:
            return InquiryLookup(False, detail=read.detail, http_status=read.http_status, reply_ready=ready)
        row = next((r for r in read.rows if str(r["inquiry_id"]) == target), None)
        return InquiryLookup(True, row is not None, row, read.truncated, ready, "", 200)

    def validate_inquiry_reply(self, inquiry_id: Any, content: Any):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult

        target = str(inquiry_id or "").strip()
        if not target.isdigit() or len(target) > MAX_INQUIRY_ID_DIGITS or isinstance(inquiry_id, bool):
            return ProviderCallResult(CallKind.ACTION_REQUIRED, None, "INVALID_TARGET", "문의 번호(inquiryId)가 숫자가 아니라 요청하지 않았다")
        if not isinstance(content, str) or not normalize_reply(content) or len(normalize_reply(content)) > MAX_REPLY_CHARS:
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "INVALID_CONTENT", f"답변은 1~{MAX_REPLY_CHARS:,}자여야 해서 요청하지 않았다",
            )
        if not getattr(self, "_wing_user_id", ""):
            return ProviderCallResult(
                CallKind.ACTION_REQUIRED, None, "MISSING_WING_USER_ID",
                "쿠팡 문의 답변 API가 요구하는 판매자 WING 아이디(replyBy)가 자격증명·판매자 고정값에 없어 요청하지 않았다 — 추측해서 채우지 않는다",
            )
        return None

    def reply_online_inquiry(self, inquiry_id: Any, content: str):
        from app.domains.purchase_task.coupang_channel_control_provider import CallKind
        from app.domains.purchase_task.coupang_channel_control_provider import ProviderCallResult
        from app.domains.purchase_task.coupang_channel_control_provider import _body
        from app.domains.purchase_task.coupang_channel_control_provider import _summary

        invalid = self.validate_inquiry_reply(inquiry_id, content)
        if invalid is not None:
            return invalid
        path = INQUIRY_REPLY_PATH.format(vendor_id=quote(self.vendor_id, safe=""), inquiry_id=str(inquiry_id).strip())
        payload = {"content": normalize_reply(content), "vendorId": self.vendor_id, "replyBy": self._wing_user_id}
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

    def read_inquiry_reply_applied(self, inquiry_id: Any, content: str):
        """결과불명 대조 — 우리 답변이 붙어 있으면 True, 답이 하나도 없으면 False(안 받음).
        다른 답만 있으면(윙에서 누가 답했을 수 있다) 판단하지 않는다(ok=False) — 두 번째 답을 보내지 않게 한다."""

        from app.domains.purchase_task.coupang_channel_control_provider import ProviderReadResult

        found = self.find_online_inquiry(inquiry_id)
        if not found.ok:
            return ProviderReadResult(False, None, found.detail, found.http_status)
        if not found.found:
            return ProviderReadResult(False, None, "최근 7일 문의에서 찾지 못했다 — 윙에서 확인 필요", 200)
        mine = normalize_reply(content)
        comments = found.row["comments"]
        if any(normalize_reply(c["content"]) == mine for c in comments):
            return ProviderReadResult(True, True, "", 200)
        if not comments:
            return ProviderReadResult(True, False, "", 200)
        return ProviderReadResult(False, None, "다른 답변이 달려 있다 — 윙에서 확인 필요", 200)


__all__ = ["_InquiryReplyMixin", "InquiryLookup", "INQUIRY_REPLY_PATH", "MAX_REPLY_CHARS", "MAX_QUESTION_CHARS", "normalize_reply"]
