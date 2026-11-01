"""
=========================================================
Homez OS

File : app/domains/trend_discovery/keyword_watch_service.py

2026-11-01 — 사용자 결정 D3: **트렌드 키워드는 사람이 입력한 목록에서 가져온다.** 공식 API에는 인기 검색어 목록이 없어
(`docs/research/20261101_NAVER_SHOPPING_INSIGHT_SCOPE.md`) 어떤 키워드를 볼지는 사람이 정하고, 앱은 그 추이만 읽는다.

  - 목록은 사용자별 설정(`trend_keywords`)에 둔다. 새 표를 만들지 않는다. 최대 30개.
  - 조회: 기존 네이버 검색어 트렌드 Adapter와 트렌드 점수 규칙(`TrendDiscoveryService`)을 그대로 쓴다. 호출마다 승인 센터
    '검색어 트렌드 조회' 허용 1회. 허용이 없거나 비상 정지면 호출하지 않고 멈춘 이유를 돌려준다.
  - 값의 뜻: 네이버 값은 키워드마다 그 기간 최대값을 100으로 둔 상대 지수다. **키워드끼리 검색량을 비교할 수 없고**, 점수는 그 키워드의
    첫 주와 마지막 주 변화만 나타낸다. 0.5가 변화 없음이다.
  - 받지 못한 경우(자격증명 없음·검색량 없음·네트워크 오류는 Adapter가 구분하지 않는다)는 0점으로 꾸미지 않고 NO_DATA로 둔다.
    이전에 받은 점수는 지우지 않는다.
  - 판매 판단·후보 등록·상품 등록을 하지 않는다. 사람이 보고 상품을 찾는다.
=========================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.domains.trend_discovery.keyword_rule import normalize_user_keyword
from app.domains.trend_discovery.service import TrendDiscoveryService
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.schema import SETTING_KEY_TREND_KEYWORDS
from app.domains.user_settings.service import UserSettingService

logger = logging.getLogger("homez")

MAX_KEYWORDS = 30
MAX_LOOKUPS_PER_PRESS = 10   # 허용 요청 기본 10회와 같다
STATE_NEVER = "NEVER"
STATE_OK = "OK"
STATE_NO_DATA = "NO_DATA"
STATE_FEW_DATA = "FEW_DATA"
BLOCKED_ALLOWANCE = "ALLOWANCE_REQUIRED"
BLOCKED_ESTOP = "ESTOP"
BLOCKED_NO_KEY = "NO_KEY"

_KST = timedelta(hours=9)


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def clean_keyword(value) -> str:
    """사람이 적은 키워드 정리. 비었거나 40자 초과, 개인정보로 보이는 글은 거절한다(네이버로 나가는 글이다)."""

    from app.domains.order.cs_reply_draft import mask_personal_info

    try:
        keyword = normalize_user_keyword(value)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from None
    if mask_personal_info(keyword).masked_count:
        raise BadRequestException("전화번호·이메일·주소·긴 번호처럼 보이는 글은 키워드로 쓸 수 없어요.")
    return keyword


class TrendKeywordService:

    def __init__(self, db: Session, *, now_factory: Callable[[], datetime] | None = None):
        self.db = db
        self._now = now_factory or datetime.utcnow

    # ---------------- 저장 ----------------
    def _read(self, user_id: int, company_id: int) -> tuple[list[dict], int]:
        row = UserSettingRepository(self.db).get_for_user(user_id, company_id, SETTING_KEY_TREND_KEYWORDS)
        if row is None:
            return [], 0
        try:
            value = json.loads(row.setting_value)
        except ValueError:
            return [], row.version
        items = value.get("items") if isinstance(value, dict) else None
        return ([i for i in items if isinstance(i, dict) and isinstance(i.get("k"), str)] if isinstance(items, list) else []), row.version

    def _update(self, user_id: int, company_id: int, change: Callable[[list[dict]], list[dict]]) -> list[dict]:
        """최신 값을 읽어 바꾼다. 다른 쪽이 먼저 저장했으면 한 번 다시 읽어 합친다(조회 중 지운 키워드가 되살아나지 않게)."""

        for attempt in (1, 2):
            items, version = self._read(user_id, company_id)
            updated = change(items)
            try:
                UserSettingService(UserSettingRepository(self.db)).put(user_id, company_id, SETTING_KEY_TREND_KEYWORDS, {"items": updated}, version, 1)
                return updated
            except ConflictException:
                self.db.rollback()
                if attempt == 2:
                    raise
        return []

    # ---------------- 보기·고치기 ----------------
    def _today(self) -> str:
        return (self._now() + _KST).date().isoformat()

    def list_rows(self, user_id: int, company_id: int) -> dict:
        items, _version = self._read(user_id, company_id)
        today = self._today()
        rows = [{
            "keyword": i["k"], "state": i.get("state") or STATE_NEVER, "at": i.get("at"), "score": i.get("score"), "confidence": i.get("conf"),
            "first": i.get("first"), "last": i.get("last"), "points": i.get("n"), "score_at": i.get("ok_at"),
            "due": str(i.get("at") or "")[:10] != today,
        } for i in items]
        # 점수 높은 순, 점수 없는 것은 뒤로(같으면 적은 순서)
        rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
        return {"rows": rows, "max": MAX_KEYWORDS, "due": sum(1 for r in rows if r["due"]), "per_press": MAX_LOOKUPS_PER_PRESS}

    def add(self, user_id: int, company_id: int, keyword) -> dict:
        cleaned = clean_keyword(keyword)

        def change(items: list[dict]) -> list[dict]:
            if any(_same(i["k"], cleaned) for i in items):
                raise BadRequestException("이미 목록에 있는 키워드예요.")
            if len(items) >= MAX_KEYWORDS:
                raise BadRequestException(f"키워드는 {MAX_KEYWORDS}개까지 넣을 수 있어요.")
            return [*items, {"k": cleaned}]

        self._update(user_id, company_id, change)
        return self.list_rows(user_id, company_id)

    def remove(self, user_id: int, company_id: int, keyword) -> dict:
        target = str(keyword or "")

        def change(items: list[dict]) -> list[dict]:
            kept = [i for i in items if not _same(i["k"], target)]
            if len(kept) == len(items):
                raise BadRequestException("목록에 없는 키워드예요.")
            return kept

        self._update(user_id, company_id, change)
        return self.list_rows(user_id, company_id)

    # ---------------- 조회 ----------------
    def due_keywords(self, user_id: int, company_id: int) -> list[str]:
        """오늘(한국 시각) 아직 안 본 키워드. 한 번도 안 본 것 → 오래된 것 순."""

        items, _version = self._read(user_id, company_id)
        today = self._today()
        due = [i for i in items if str(i.get("at") or "")[:10] != today]
        due.sort(key=lambda i: str(i.get("at") or ""))
        return [i["k"] for i in due]

    def _store_result(self, user_id: int, company_id: int, keyword: str, fields: dict) -> None:
        def change(items: list[dict]) -> list[dict]:
            return [{**i, **fields} if _same(i["k"], keyword) else i for i in items]

        self._update(user_id, company_id, change)

    def lookup_one(self, user_id: int, company_id: int, keyword: str, adapter) -> str:
        """한 키워드 조회·저장. 돌려주는 값은 저장한 상태. 허용이 없으면 `ExternalCallNotAllowed`가 그대로 나간다(아무것도 저장하지 않는다)."""

        at = (self._now() + _KST).strftime("%Y-%m-%dT%H:%M")
        signal = adapter.fetch(keyword)
        if signal is None:
            self._store_result(user_id, company_id, keyword, {"state": STATE_NO_DATA, "at": at})
            return STATE_NO_DATA
        series = signal.search_volume_series
        result = TrendDiscoveryService().evaluate(signal)
        if len(series) < TrendDiscoveryService.MIN_DATA_POINTS:
            self._store_result(user_id, company_id, keyword, {"state": STATE_FEW_DATA, "at": at, "n": len(series)})
            return STATE_FEW_DATA
        self._store_result(user_id, company_id, keyword, {
            "state": STATE_OK, "at": at, "ok_at": at, "score": result.trend_score, "conf": result.confidence,
            "first": round(float(series[0]), 2), "last": round(float(series[-1]), 2), "n": len(series),
        })
        return STATE_OK

    def lookup(
        self, user_id: int, company_id: int, adapter, *, keyword: str | None = None, limit: int = MAX_LOOKUPS_PER_PRESS,
        key_check: Callable[[], bool | None] | None = None,
    ) -> dict:
        """`keyword`가 있으면 그것만(오늘 봤어도), 없으면 오늘 안 본 것을 `limit`개까지."""

        from app.domains.external_call.service import ExternalCallNotAllowed

        if keyword is not None:
            items, _version = self._read(user_id, company_id)
            found = next((i["k"] for i in items if _same(i["k"], str(keyword))), None)
            if found is None:
                raise BadRequestException("목록에 없는 키워드예요.")
            targets = [found]
        else:
            targets = self.due_keywords(user_id, company_id)
        out = {"saved": 0, "no_data": 0, "few_data": 0, "failed": 0, "blocked": None, "left": 0}
        if not targets:
            return out
        if key_check is not None and key_check() is False:
            out["blocked"] = BLOCKED_NO_KEY
            out["left"] = len(targets)
            return out
        for index, target in enumerate(targets):
            if index >= limit:
                out["left"] = len(targets) - index
                break
            try:
                state = self.lookup_one(user_id, company_id, target, adapter)
            except ExternalCallNotAllowed as exc:
                out["blocked"] = BLOCKED_ESTOP if exc.reason == "ESTOP_ACTIVE" else BLOCKED_ALLOWANCE
                out["left"] = len(targets) - index
                break
            except Exception:  # noqa: BLE001 — 한 키워드의 실패가 나머지를 막지 않는다
                self.db.rollback()
                out["failed"] += 1
                logger.exception("관심 키워드 조회 실패")
                continue
            out[{STATE_OK: "saved", STATE_NO_DATA: "no_data", STATE_FEW_DATA: "few_data"}[state]] += 1
        return out

    def company_users(self, company_id: int) -> list[int]:
        """키워드 목록이 있는 이 회사의 활성 사용자(자동 실행용)."""

        from app.domains.user.model import User
        from app.domains.user_settings.model import UserSetting

        rows = (
            self.db.query(UserSetting.user_id)
            .join(User, User.id == UserSetting.user_id)
            .filter(UserSetting.company_id == company_id, UserSetting.setting_key == SETTING_KEY_TREND_KEYWORDS, User.is_active.is_(True), User.company_id == company_id)
            .order_by(UserSetting.user_id)
            .all()
        )
        return [user_id for (user_id,) in rows]


__all__ = ["MAX_KEYWORDS", "MAX_LOOKUPS_PER_PRESS", "TrendKeywordService", "clean_keyword"]
