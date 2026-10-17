"""
=========================================================
Homez OS

File : app/domains/product_import/channel_fee_source.py

2026-10-18 — 판매 플랫폼 수수료율 출처(URL 상품 분석 Phase E7). 쿠팡만 가정하지 않도록 플랫폼마다 같은 방식으로 수수료율을 구한다.

출처 등급(결과에 그대로 표시한다):
  - PLATFORM_API    — 플랫폼 공식 API가 등록 전에 수수료율을 줄 때. **지금은 어떤 플랫폼도 해당하지 않는다**:
                      쿠팡 카테고리 메타정보 API에는 수수료 필드가 없고(2026-10-18 공식 문서 확인), 상품 조회의 `saleAgentCommission`은
                      응답 예시에만 있는 비문서화 필드이며 등록된 상품에서만 읽힌다. 다른 플랫폼은 아직 연동돼 있지 않다.
  - USER_CONFIRMED  — 사용자가 플랫폼(예: 쿠팡 WING)에서 직접 확인해 저장한 값. 저장 시각·입력자를 함께 남긴다.
                      카테고리 코드별 값이 우선이고, 없으면 플랫폼 공통 값(`*`)을 쓰되 "카테고리별 값이 아님"을 표시한다.
  - UNKNOWN         — 둘 다 없다. 호출하는 쪽은 이익 계산과 판정을 **보류**해야 한다(가정값으로 채우지 않는다).

저장 위치: `user_settings.listing_defaults.channel_fees`(새 테이블 없음, 기존 고정값은 보존). 설정 값 크기 상한(8000자)과 다른 고정값을 함께 담아야 해서 항목은 40개, 메모는 40자로 제한한다.
=========================================================
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.service import UserSettingService

SETTINGS_KEY = "listing_defaults"
SETTINGS_SUBKEY = "channel_fees"
CHANNEL_DEFAULT = "*"
MAX_ENTRIES = 40
MAX_RATE = Decimal("0.5")

# 등록 전에 수수료율을 공식 API로 줄 수 있는 플랫폼. E0 확인 결과 현재 없음 — 문서화된 API를 확인한 플랫폼만 여기에 더한다.
PLATFORM_API_CHANNELS: frozenset[str] = frozenset()

_CHANNEL = re.compile(r"^[A-Z0-9_]{2,20}$")
_CATEGORY = re.compile(r"^(\*|\d{1,20})$")


class FeeSourceKind:
    PLATFORM_API = "PLATFORM_API"
    USER_CONFIRMED = "USER_CONFIRMED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FeeLookup:
    source: str
    rate: Decimal | None = None
    scope: str | None = None          # "category" | "channel_default"
    confirmed_at: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source, "rate": str(self.rate) if self.rate is not None else None, "scope": self.scope,
            "confirmed_at": self.confirmed_at, "note": self.note,
        }


def _parse_rate(value) -> Decimal:
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BadRequestException("수수료율은 숫자여야 합니다(예: 0.108).") from None
    if isinstance(value, bool) or not rate.is_finite() or not Decimal("0") < rate <= MAX_RATE:
        raise BadRequestException("수수료율은 0보다 크고 0.5(50%) 이하여야 합니다.")
    return rate.quantize(Decimal("0.000001")).normalize()


class UserConfirmedFeeStore:
    """사용자가 확인한 플랫폼 수수료율 저장소."""

    def __init__(self, db: Session):
        self.db = db

    def _blob(self, user_id: int, company_id: int) -> tuple[dict, int]:
        row = UserSettingRepository(self.db).get_for_user(user_id, company_id, SETTINGS_KEY)
        if row is None:
            return {}, 0
        try:
            value = json.loads(row.setting_value)
        except ValueError:
            return {}, row.version
        return (value if isinstance(value, dict) else {}), row.version

    def entries(self, user_id: int, company_id: int) -> dict:
        fees = self._blob(user_id, company_id)[0].get(SETTINGS_SUBKEY)
        return fees if isinstance(fees, dict) else {}

    def save(
        self, user_id: int, company_id: int, *, channel: str, category_code: str, rate, note: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        channel = (channel or "").strip().upper()
        category_code = (category_code or "").strip()
        if not _CHANNEL.match(channel):
            raise BadRequestException("플랫폼 코드 형식이 올바르지 않습니다(영문 대문자·숫자·_ 2~20자).")
        if not _CATEGORY.match(category_code):
            raise BadRequestException("카테고리 코드는 숫자이거나, 플랫폼 공통 값은 '*'여야 합니다.")
        if channel in PLATFORM_API_CHANNELS:
            raise BadRequestException("이 플랫폼은 공식 API로 수수료율을 확인합니다 — 직접 입력할 수 없습니다.")
        decimal_rate = _parse_rate(rate)
        blob, version = self._blob(user_id, company_id)
        fees = blob.get(SETTINGS_SUBKEY) if isinstance(blob.get(SETTINGS_SUBKEY), dict) else {}
        by_channel = fees.get(channel) if isinstance(fees.get(channel), dict) else {}
        by_channel[category_code] = {
            "rate": str(decimal_rate), "confirmed_at": (now or datetime.utcnow()).isoformat(timespec="seconds"),
            "by": user_id, "note": (note or "").strip()[:40],
        }
        fees[channel] = by_channel
        if sum(len(v) for v in fees.values() if isinstance(v, dict)) > MAX_ENTRIES:
            raise BadRequestException(f"저장할 수 있는 수수료율 항목은 {MAX_ENTRIES}개까지입니다.")
        blob[SETTINGS_SUBKEY] = fees
        UserSettingService(UserSettingRepository(self.db)).put(user_id, company_id, SETTINGS_KEY, blob, version, 1)
        return by_channel[category_code]

    def lookup(self, user_id: int, company_id: int, channel: str, category_code: str | None) -> FeeLookup:
        channel = (channel or "").strip().upper()
        if channel in PLATFORM_API_CHANNELS:
            # 공식 API 경로는 아직 구현된 플랫폼이 없다. 생기면 여기서 조회하고, 실패하면 UNKNOWN으로 둔다(가정 금지).
            return FeeLookup(FeeSourceKind.UNKNOWN, note="플랫폼 API 조회가 구현되지 않았다")
        by_channel = self.entries(user_id, company_id).get(channel)
        if not isinstance(by_channel, dict):
            return FeeLookup(FeeSourceKind.UNKNOWN)
        code = (category_code or "").strip()
        for key, scope in ((code, "category"), (CHANNEL_DEFAULT, "channel_default")):
            if not key:
                continue
            entry = by_channel.get(key)
            if isinstance(entry, dict):
                try:
                    rate = _parse_rate(entry.get("rate"))
                except BadRequestException:
                    continue  # 깨진 값은 없는 것으로 본다 — 가정으로 대체하지 않는다
                return FeeLookup(FeeSourceKind.USER_CONFIRMED, rate, scope, entry.get("confirmed_at"), entry.get("note") or None)
        return FeeLookup(FeeSourceKind.UNKNOWN)
