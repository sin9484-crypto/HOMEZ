"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_approval.py

Gate I(2026-08-08) — 8단계(승인) 불변 승인 패키지 + fingerprint.
`fingerprint.py`의 canonical_json()/sha256_hex() 두 순수 함수를 그대로
재사용한다(새 해시 로직을 만들지 않는다 — docs/V6_EXECUTION_LEDGER.md
"Gate I 설계" 5번 항목).

포함(fingerprint 입력): 정규화된 초안, 선택 media_asset_id 목록 +
각 sha256_hex, 채널 계정 id 목록, 채널별 fulfillment_mode + 검증된
required_fields, 가격·비용·마진 입력값(Decimal → 문자열)과 계산 결과,
사전검사 결과 요약 + 정책 버전.

제외: locale, 번역 문구, 화면 표시 순서, 비밀번호, Credential 실제
값, 일회용 인증 토큰, Provider 원문 응답 — 애초에 이 패키지를 만드는
데 쓰는 어떤 조회 함수도 그런 값을 반환하지 않는다.

**설계 문서 대비 한 가지 명시적 조정**: "생성시각(UTC ISO)"은 fingerprint
입력에서 제외했다 — 매 GET .../approval-preview 호출마다 시각이
달라지면, 제출 직전 "승인 이후 내용이 실제로 바뀌었는가" 재검증
(submission 단계에서 이 모듈을 다시 호출해 비교)이 순수 시간 경과만
으로 항상 불일치로 판정돼 매번 불필요한 재승인을 강제하는 결함이
생긴다. 생성시각은 감사 목적으로 패키지 자체에는 그대로 담되(응답에
포함), fingerprint 해시 입력에서는 뺀다.
=========================================================
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.marketplace_listing.fingerprint import canonical_json
from app.domains.marketplace_listing.fingerprint import sha256_hex
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.media_asset.model import MediaAsset

PRECHECK_POLICY_VERSION = "listing_wizard_precheck.v1"


def build_approval_package(
    wizard: ListingWizard, db: Session, company_id: int,
    precheck_status: str,
) -> tuple[dict, str]:
    """(승인 패키지 dict, fingerprint) 튜플을 반환한다."""

    draft = json.loads(wizard.draft_json) if wizard.draft_json else {}
    media_ids = json.loads(wizard.selected_media_asset_ids_json or "[]")
    channel_selections = json.loads(wizard.channel_selections_json or "[]")
    economics_input = json.loads(wizard.economics_input_json or "[]")
    economics_result = json.loads(wizard.economics_result_json or "[]")

    media_assets = (
        db.query(MediaAsset)
        .filter(MediaAsset.company_id == company_id)
        .filter(MediaAsset.id.in_(media_ids))
        .order_by(MediaAsset.id)
        .all()
    )
    media_entries = [
        {"media_asset_id": asset.id, "sha256_hex": asset.sha256_hex}
        for asset in media_assets
    ]

    fingerprinted_content = {
        "wizard_id": wizard.id,
        "product_candidate_id": wizard.product_candidate_id,
        "draft": draft,
        "media": media_entries,
        "channel_selections": channel_selections,
        "economics_input": economics_input,
        "economics_result": economics_result,
        "precheck_status": precheck_status,
        "precheck_policy_version": PRECHECK_POLICY_VERSION,
    }
    fingerprint = sha256_hex(canonical_json(fingerprinted_content))

    package = dict(fingerprinted_content)
    package["generated_at"] = datetime.utcnow().isoformat()
    package["fingerprint"] = fingerprint

    return package, fingerprint


def strip_economics_from_package(package: dict) -> dict:
    """
    Gate U-1(2026-08-10) — `LISTING_ECONOMICS_VIEW` 권한이 없는 요청에게
    승인 Package(승인 미리보기·승인 취소 미리보기·승인 이력 스냅샷)를
    보여줄 때 쓴다. fingerprint 자체는 이 함수 호출 이전에 이미
    `build_approval_package()`가 전체 내용으로 계산을 끝낸 뒤이므로
    영향받지 않는다(내부 계약 유지) — 이 함수는 오직 "클라이언트에게
    보여줄 사본"에서만 두 키를 제거한다.
    """

    if not package:
        return package

    redacted = dict(package)
    redacted.pop("economics_input", None)
    redacted.pop("economics_result", None)

    return redacted


def redact_approval_history(history: list[dict]) -> list[dict]:
    """
    `revoke_approval()`이 남기는 `APPROVAL_REVOKED` 이력 항목은
    `preserved_approval_package`에 그 시점의 전체 Package(경제성 정보
    포함)를 스냅샷으로 담는다 — Economics 권한이 없는 열람자에게는
    이 중첩된 스냅샷에서도 두 키를 제외해야 한다.
    """

    redacted_history = []

    for entry in history:
        entry = dict(entry)
        nested = entry.get("preserved_approval_package")
        if nested:
            entry["preserved_approval_package"] = strip_economics_from_package(nested)
        redacted_history.append(entry)

    return redacted_history


__all__ = [
    "build_approval_package",
    "PRECHECK_POLICY_VERSION",
    "strip_economics_from_package",
    "redact_approval_history",
]
