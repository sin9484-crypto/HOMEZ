"""
=========================================================
Homez OS

File : app/domains/channel_policy/terms_checklist.py

2026-10-23 — 약관·정책 AI T4: **약관·방침 초안 보조** — 순수 함수(DB·네트워크·AI 없음, 저장 없음).
  ① 필수 조항 **체크리스트**(`CLAUSES`) ② 사용자가 가진 약관 초안이 각 조항을 **언급하는지** 낱말로 확인(`coverage`) ③ 조항 제목만 있는 **뼈대**(`skeleton`).

기준:
  - **법률 자문이 아니다.** 모든 결과에 고정 문구(`DISCLAIMER`)가 붙고, 뼈대는 `NOT_FOR_USE`로 표시된다. 약관은 법적 효력이 달린 문서이므로 **법무 검토 없이 적용하지 않는다**
    (결정 기록: 약관 앱 적용은 V10 시점).
  - **조항 문장을 만들어 내지 않는다.** 뼈대는 제목과 "여기에 무엇이 들어가야 하는지"만 적고 본문 칸은 비워 둔다 — 문장 초안은 법무·사업자가 쓴다(AI 문안 생성은 하지 않는다).
  - `coverage`의 MENTIONED는 **낱말이 있다**는 뜻일 뿐 **내용이 적절하다**는 뜻이 아니다. NOT_FOUND는 "초안에서 찾지 못함"이며 비어 있는 초안은 판단하지 않고 UNKNOWN으로 알린다.
  - 조항 목록은 일반적인 온라인 판매 약관 항목에서 이끌어 낸 **점검 후보**이며 법령 조문 번호는 적지 않았다(확인하지 못했다) — 근거 조문은 법무가 정한다.
  - `applies_to`가 있는 조항은 사업 모델(구매대행·위탁판매)에 해당할 때만 점검한다. 해당하는지 모르면(`None`) 점검 대상에 넣고 이유를 밝힌다.
=========================================================
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.core.exceptions import BadRequestException

DISCLAIMER = "이 내용은 법률 자문이 아닙니다. 약관은 법적 효력이 있는 문서이므로 적용 전에 반드시 법무 검토를 받아야 합니다."
MAX_TEXT_CHARS = 100_000
MODELS = ("PROXY_PURCHASE", "DROPSHIP")     # 구매대행, 위탁판매(무재고 중개)

# (code, title, must_cover, words, applies_to)
CLAUSES: tuple[dict[str, Any], ...] = (
    {"code": "PURPOSE_DEFINITIONS", "title": "목적과 용어의 정의", "must_cover": "약관의 목적, 서비스·회원·상품 등 용어의 뜻", "words": ("목적", "정의"), "applies_to": None},
    {"code": "OPERATOR_INFO", "title": "사업자 정보 표시", "must_cover": "상호·대표자·주소·연락처·사업자등록번호·통신판매업 신고 정보", "words": ("상호", "대표자", "사업자등록", "통신판매업"), "applies_to": None},
    {"code": "TERMS_NOTICE_AMENDMENT", "title": "약관의 게시와 개정", "must_cover": "약관을 알리는 방법, 개정 절차와 공지 시기, 개정 약관에 동의하지 않을 때의 처리", "words": ("개정", "공지"), "applies_to": None},
    {"code": "SERVICE_SCOPE", "title": "서비스의 내용과 사업자의 역할", "must_cover": "판매자 본인이 파는 것인지, 대행·중개인지, 어디까지 책임지는지", "words": ("서비스의 내용", "서비스 내용", "역할", "대행", "중개"), "applies_to": None},
    {"code": "ACCOUNT", "title": "회원 가입과 계정 관리", "must_cover": "가입 요건, 계정 관리 책임, 이용 제한", "words": ("회원가입", "회원 가입", "계정"), "applies_to": None},
    {"code": "ORDER_CONTRACT", "title": "주문과 계약의 성립", "must_cover": "주문 방법, 계약이 성립하는 시점, 주문을 거절·취소할 수 있는 경우", "words": ("계약의 성립", "계약 성립", "주문"), "applies_to": None},
    {"code": "PRICE_PAYMENT", "title": "가격과 결제", "must_cover": "표시 가격, 결제 수단, 가격이 달라질 수 있는 경우와 그때의 처리", "words": ("결제", "가격"), "applies_to": None},
    {"code": "DELIVERY", "title": "배송", "must_cover": "배송 기간·비용·지연 시 안내, 도서산간 추가 비용", "words": ("배송",), "applies_to": None},
    {"code": "WITHDRAWAL_RETURN_REFUND", "title": "청약철회·반품·환불", "must_cover": "철회·반품 가능 기간과 조건, 반품 배송비 부담, 환불 시기와 방법, 철회가 제한되는 경우", "words": ("청약철회", "반품", "환불"), "applies_to": None},
    {"code": "EXCHANGE_WARRANTY", "title": "교환·품질보증·A/S", "must_cover": "교환 조건, 하자 시 처리, A/S 창구", "words": ("교환", "품질보증", "a/s", "하자"), "applies_to": None},
    {"code": "PRIVACY", "title": "개인정보 보호", "must_cover": "수집 항목·이용 목적·보관 기간·제3자 제공·처리 위탁(공급처·택배사 등)은 별도 개인정보 처리방침 문서와 연결", "words": ("개인정보",), "applies_to": None},
    {"code": "LIABILITY", "title": "책임의 제한과 면책", "must_cover": "사업자가 책임지지 않는 경우의 범위(고의·중과실은 제외되는 점 포함)", "words": ("면책", "책임의 제한", "책임 제한", "책임을 지지"), "applies_to": None},
    {"code": "DISPUTE", "title": "분쟁 해결과 관할", "must_cover": "고객 불만 접수 창구, 분쟁 조정 안내, 준거법·관할 법원", "words": ("분쟁", "관할"), "applies_to": None},
    {"code": "PROXY_CUSTOMS_SHIPPING", "title": "(구매대행) 관세·통관·해외 배송 안내", "must_cover": "관세·부가세 부담, 통관 지연, 해외 배송 기간, 개인통관고유부호 사용 여부", "words": ("관세", "통관"), "applies_to": "PROXY_PURCHASE"},
    {"code": "PROXY_NO_RETURN_CASES", "title": "(구매대행) 반품·교환이 제한되는 경우", "must_cover": "해외 반품 비용과 기간, 반품 불가 사유를 주문 전에 알리는 방법", "words": ("해외 반품", "반품 불가", "반품이 불가", "반품 제한"), "applies_to": "PROXY_PURCHASE"},
    {"code": "DROPSHIP_STOCKOUT", "title": "(위탁판매) 공급처 재고 부족·품절 시 처리", "must_cover": "재고가 없을 때 주문 취소·환불 방법과 시기, 고객 안내 방식", "words": ("재고", "품절"), "applies_to": "DROPSHIP"},
    {"code": "DROPSHIP_FULFILLMENT_NOTICE", "title": "(위탁판매) 배송 주체와 상품 출처 표시", "must_cover": "실제 발송 주체가 공급처일 수 있다는 점과 문의·반품 접수 방법", "words": ("공급처", "발송", "출고"), "applies_to": "DROPSHIP"},
)

_NO_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _NO_SPACE.sub("", unicodedata.normalize("NFKC", text)).lower()


def _models(models: Any) -> list[str] | None:
    if models is None:
        return None
    if not isinstance(models, (list, tuple)) or any(m not in MODELS for m in models):
        raise BadRequestException("알 수 없는 사업 모델입니다.")
    return list(dict.fromkeys(models))


def applicable_clauses(models: Any) -> list[dict[str, Any]]:
    """사업 모델에 해당하는 조항. 모델을 모르면(None) 모델별 조항도 모두 넣는다 — 이유(`why`)를 함께 준다. 빈 목록은 '공통 조항만'이다."""

    chosen = _models(models)
    rows = []
    for clause in CLAUSES:
        need = clause["applies_to"]
        if need is None:
            why = "공통"
        elif chosen is None:
            why = "사업 모델을 몰라 포함(해당하지 않으면 무시)"
        elif need in chosen:
            why = "선택한 사업 모델에 해당"
        else:
            continue
        rows.append({**{k: clause[k] for k in ("code", "title", "must_cover")}, "applies_to": need, "why": why})
    return rows


def checklist(models: Any = None) -> dict[str, Any]:
    return {"disclaimer": DISCLAIMER, "models": _models(models), "clauses": applicable_clauses(models), "legal_references_included": False}


def coverage(text: Any, models: Any = None) -> dict[str, Any]:
    if text is not None and not isinstance(text, str):
        raise BadRequestException("약관 글은 글이어야 합니다.")
    if text is not None and len(text) > MAX_TEXT_CHARS:
        raise BadRequestException(f"약관 글이 너무 깁니다({MAX_TEXT_CHARS:,}자 이하).")
    clauses = applicable_clauses(models)
    if not text or not text.strip():
        return {"disclaimer": DISCLAIMER, "status": "UNKNOWN", "reason": "TEXT_EMPTY", "results": [], "mentioned": 0, "not_found": 0, "total": len(clauses),
                "note": "점검할 글이 없어 판단하지 않았습니다."}
    body = _norm(text)
    by_code = {c["code"]: c for c in CLAUSES}
    results = []
    for clause in clauses:
        words = by_code[clause["code"]]["words"]
        hits = [w for w in words if _norm(w) in body]
        results.append({"code": clause["code"], "title": clause["title"], "status": "MENTIONED" if hits else "NOT_FOUND", "matched_words": hits, "must_cover": clause["must_cover"]})
    mentioned = sum(1 for r in results if r["status"] == "MENTIONED")
    return {"disclaimer": DISCLAIMER, "status": "CHECKED", "reason": None, "results": results, "mentioned": mentioned, "not_found": len(results) - mentioned, "total": len(results),
            "note": "MENTIONED는 관련 낱말이 있다는 뜻이지 내용이 적절하다는 뜻이 아닙니다. 낱말이 없다고 해서 반드시 빠진 것은 아니지만 확인이 필요합니다."}


def skeleton(models: Any = None) -> dict[str, Any]:
    """조항 제목과 들어가야 할 내용만 있는 뼈대. 본문은 비워 둔다(문장을 만들어 내지 않는다) — 적용 불가(NOT_FOR_USE)."""

    sections = [{"number": i, "title": c["title"], "must_cover": c["must_cover"], "body": ""} for i, c in enumerate(applicable_clauses(models), start=1)]
    return {"disclaimer": DISCLAIMER, "draft_status": "NOT_FOR_USE", "models": _models(models), "sections": sections,
            "note": "본문은 비어 있습니다. 법무 검토를 받아 사업자가 직접 작성해야 하며, 이 뼈대는 그대로 쓸 수 없습니다."}


__all__ = ["checklist", "coverage", "skeleton", "applicable_clauses", "CLAUSES", "DISCLAIMER", "MODELS", "MAX_TEXT_CHARS"]
