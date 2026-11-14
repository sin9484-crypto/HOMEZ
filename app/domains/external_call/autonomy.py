"""
=========================================================
Homez OS

File : app/domains/external_call/autonomy.py

2026-11-14 — **기능별 자율 단계 표**(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-5, 사용자 지시 "다음작업 진행해라").
바깥 서비스를 부르는 기능마다 지금 HOMEZ가 어디까지 스스로 하는지와, 단계를 올리거나 내릴지 판단할 근거(최근 기록)를 한 표로 보여 준다.

단계(이 표만의 이름 — `ai_governance.AutomationLevel`의 L0~L3과 뜻이 다르다. 그쪽 L3은 '승인 요청'이다):
  - VIEW     보기       : 바깥을 부르지 않는다
  - SUGGEST  제안       : AI 결과를 초안·제안으로만 보여 준다(사람이 고쳐 쓰거나 버린다)
  - PREPARE  준비       : HOMEZ가 미리보기를 만들고, 사람이 누르고 횟수 허용을 줄 때만 바깥을 바꾼다
  - AUTO     정해진 범위 자동 : 사람이 준 허용(기간·시간당 상한 또는 횟수) 안에서 일정에 따라 스스로 한다

단계는 실제 코드 동작을 그대로 적은 것이다(스케줄 작업 = AUTO, 버튼으로만 = PREPARE, AI = SUGGEST). 이 표는 **아무것도 바꾸지 않는다**:
  - 올리기 후보: '준비' 중 돈·고객 글과 무관한 쓰기만, 최근 연속 성공 30회·30일 실패 0이면 '자동 후보'로 **제안**한다. 올리는 장치는 없다.
  - 돈(판매가·반품 환불)·고객에게 바로 보이는 글(문의 답변)·쿠팡 재심사(상품명)는 언제나 매번 확인 — 후보로도 올리지 않는다.
  - AI는 제안 단계에 머문다(직접 실행하지 않는다).
  - 내리기 검토: 쓰기는 최근 7일 실패 1회부터, 그 밖(5분마다 도는 조회 등)은 최근 7일 실패율 5% 이상(실패 3회 이상일 때)이면 '검토'.
    지금 실패가 3회 이상 이어져도 '검토'. 자주 도는 조회는 일시 오류가 조금씩 있어 횟수만으로 보면 늘 '검토'가 떠 경고가 무뎌진다.
'연속 성공'은 보낸 요청의 성공 기록이다 — 사람이 미리보기를 고쳤는지는 기록에 없어 세지 않는다(그렇게 보이지 않게 이름을 붙인다).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from sqlalchemy.orm import Session

from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.constants import LogOutcome
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog

VIEW, SUGGEST, PREPARE, AUTO = "VIEW", "SUGGEST", "PREPARE", "AUTO"
STAGES = (VIEW, SUGGEST, PREPARE, AUTO)

# 어떻게 움직이나
SCHEDULED = "SCHEDULED"          # 일정에 따라(5분·10분·30분마다, 하루 한 번)
ON_PRESS = "ON_PRESS"            # 사람이 누를 때만

# 올리기 정책
ELIGIBLE = "ELIGIBLE"            # 기록이 쌓이면 '자동 후보'로 제안할 수 있다
ALWAYS_CONFIRM = "ALWAYS_CONFIRM"  # 돈·고객 글·재심사 — 매번 확인, 후보로도 올리지 않는다
AI_SUGGEST_ONLY = "AI_SUGGEST_ONLY"  # AI는 제안에 머문다
TOP = "TOP"                      # 이미 정해진 범위 자동(더 올릴 단계 없음)

RAISE_STREAK = 30
RAISE_WINDOW_DAYS = 30
LOWER_WINDOW_DAYS = 7
LOWER_FAILS_WRITE = 1
LOWER_FAILS_OTHER = 3
LOWER_FAIL_RATE = 0.05
LOWER_FAIL_STREAK = 3
STREAK_SCAN = 60


@dataclass(frozen=True)
class Feature:
    purpose: str
    kind: str             # 기록을 세는 종류(이 기능의 핵심 호출)
    stage: str
    how: str
    raise_policy: str


FEATURES: tuple[Feature, ...] = (
    Feature(CallPurpose.ORDER_DETECTION, CallKind.READ, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.ORDER_TRACKING, CallKind.READ, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.AFTER_SALES_CHECK, CallKind.READ, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.PRODUCT_STATUS_CHECK, CallKind.READ, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.TREND_LOOKUP, CallKind.READ, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.SUPPLIER_RELEASE_DATE, CallKind.READ, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.BRIEF_EMAIL, CallKind.WRITE, AUTO, SCHEDULED, TOP),
    Feature(CallPurpose.STOCK_UPDATE, CallKind.WRITE, PREPARE, ON_PRESS, ELIGIBLE),
    Feature(CallPurpose.PRODUCT_OUTBOUND_DAYS, CallKind.WRITE, PREPARE, ON_PRESS, ELIGIBLE),
    Feature(CallPurpose.INVOICE_UPLOAD, CallKind.WRITE, PREPARE, ON_PRESS, ELIGIBLE),
    Feature(CallPurpose.PRICE_UPDATE, CallKind.WRITE, PREPARE, ON_PRESS, ALWAYS_CONFIRM),
    Feature(CallPurpose.RETURN_PROCESS, CallKind.WRITE, PREPARE, ON_PRESS, ALWAYS_CONFIRM),
    Feature(CallPurpose.INQUIRY_REPLY, CallKind.WRITE, PREPARE, ON_PRESS, ALWAYS_CONFIRM),
    Feature(CallPurpose.PRODUCT_NAME_UPDATE, CallKind.WRITE, PREPARE, ON_PRESS, ALWAYS_CONFIRM),
    Feature(CallPurpose.AI_DRAFT, CallKind.WRITE, SUGGEST, ON_PRESS, AI_SUGGEST_ONLY),
    Feature(CallPurpose.AI_ANALYSIS, CallKind.WRITE, SUGGEST, ON_PRESS, AI_SUGGEST_ONLY),
    Feature(CallPurpose.AI_ADVICE, CallKind.WRITE, SUGGEST, ON_PRESS, AI_SUGGEST_ONLY),
    Feature(CallPurpose.IMAGE_GENERATION, CallKind.WRITE, SUGGEST, ON_PRESS, AI_SUGGEST_ONLY),
    Feature(CallPurpose.AI_BACKGROUND, CallKind.WRITE, SUGGEST, ON_PRESS, AI_SUGGEST_ONLY),
)

# 내리기·올리기 판정 결과
OK, REVIEW = "OK", "REVIEW"
CANDIDATE, NOT_YET, NEVER, AI_ONLY, AT_TOP = "CANDIDATE", "NOT_YET", "NEVER", "AI_ONLY", "AT_TOP"


def judge_raise(feature: Feature, streak: int, failed_window: int) -> str:
    if feature.raise_policy == TOP:
        return AT_TOP
    if feature.raise_policy == ALWAYS_CONFIRM:
        return NEVER
    if feature.raise_policy == AI_SUGGEST_ONLY:
        return AI_ONLY
    return CANDIDATE if streak >= RAISE_STREAK and failed_window == 0 else NOT_YET


def judge_lower(feature: Feature, failed_recent: int, succeeded_recent: int, fail_streak: int) -> str:
    if fail_streak >= LOWER_FAIL_STREAK:
        return REVIEW
    if feature.stage == PREPARE:
        return REVIEW if failed_recent >= LOWER_FAILS_WRITE else OK
    total = failed_recent + succeeded_recent
    return REVIEW if failed_recent >= LOWER_FAILS_OTHER and failed_recent / total >= LOWER_FAIL_RATE else OK


class AutonomyTableService:

    def __init__(self, db: Session, *, now_factory=None):
        self.db = db
        self._now = now_factory or datetime.utcnow

    def _base(self, company_id: int, feature: Feature):
        return self.db.query(ExternalCallLog).filter(
            ExternalCallLog.company_id == company_id, ExternalCallLog.purpose == feature.purpose, ExternalCallLog.kind == feature.kind,
            ExternalCallLog.outcome.in_((LogOutcome.SUCCEEDED, LogOutcome.FAILED)),
        )

    def _count(self, company_id: int, feature: Feature, outcome: str, since: datetime) -> int:
        return self._base(company_id, feature).filter(ExternalCallLog.outcome == outcome, ExternalCallLog.created_at >= since).count()

    def _latest(self, company_id: int, feature: Feature) -> list[str]:
        rows = self._base(company_id, feature).with_entities(ExternalCallLog.outcome).order_by(ExternalCallLog.id.desc()).limit(STREAK_SCAN).all()
        return [r[0] for r in rows]

    def _allowances(self, company_id: int, purpose: str, now: datetime) -> list[dict]:
        rows = (
            self.db.query(ExternalCallAllowance)
            .filter(
                ExternalCallAllowance.company_id == company_id, ExternalCallAllowance.purpose == purpose,
                ExternalCallAllowance.status == AllowanceStatus.ACTIVE,
            )
            .order_by(ExternalCallAllowance.id.desc()).all()
        )
        # 같은 대상·종류·방식은 한 줄로 묶는다(횟수형은 남은 횟수 합, 상시형은 가장 늦은 만료) — 허용이 여러 건 쌓여도 칸이 길어지지 않게.
        groups: dict[tuple[str, str, str], dict] = {}
        for a in rows:
            if a.expires_at is not None and a.expires_at <= now:
                continue  # 요약 화면이 열릴 때 만료로 바뀌는 것 — 여기서는 상태를 바꾸지 않고 빼기만 한다
            if a.mode != AllowanceMode.STANDING and not (a.remaining_count or 0) > 0:
                continue  # 다 쓴 횟수 허용
            key = (a.system, a.kind, a.mode)
            g = groups.get(key)
            if g is None:
                groups[key] = g = {
                    "system": a.system, "system_label": ExternalSystem.LABELS.get(a.system, a.system), "kind": a.kind,
                    "kind_label": CallKind.LABELS.get(a.kind, a.kind), "mode": a.mode, "per_hour_limit": a.per_hour_limit,
                    "remaining_count": 0, "expires_at": None, "count": 0,
                }
            g["count"] += 1
            if a.mode == AllowanceMode.STANDING:
                if a.expires_at is not None and (g["expires_at"] is None or a.expires_at.isoformat() > g["expires_at"]):
                    g["expires_at"], g["per_hour_limit"] = a.expires_at.isoformat(), a.per_hour_limit
            else:
                g["remaining_count"] += int(a.remaining_count or 0)
        return sorted(groups.values(), key=lambda g: (g["system"], g["kind"], g["mode"]))

    def row(self, company_id: int, feature: Feature) -> dict:
        now = self._now()
        since_window = now - timedelta(days=RAISE_WINDOW_DAYS)
        latest = self._latest(company_id, feature)
        streak = 0
        for o in latest:
            if o != LogOutcome.SUCCEEDED:
                break
            streak += 1
        fail_streak = 0
        for o in latest:
            if o != LogOutcome.FAILED:
                break
            fail_streak += 1
        failed_window = self._count(company_id, feature, LogOutcome.FAILED, since_window)
        recent_failed = self._count(company_id, feature, LogOutcome.FAILED, now - timedelta(days=LOWER_WINDOW_DAYS))
        recent_ok = self._count(company_id, feature, LogOutcome.SUCCEEDED, now - timedelta(days=LOWER_WINDOW_DAYS))
        return {
            "purpose": feature.purpose, "purpose_label": CallPurpose.LABELS.get(feature.purpose, feature.purpose), "kind": feature.kind,
            "stage": feature.stage, "how": feature.how, "raise_policy": feature.raise_policy,
            "succeeded_30d": self._count(company_id, feature, LogOutcome.SUCCEEDED, since_window), "failed_30d": failed_window, "failed_7d": recent_failed, "succeeded_7d": recent_ok,
            "success_streak": streak, "fail_streak": fail_streak, "raise_needed": RAISE_STREAK,
            "raise": judge_raise(feature, streak, failed_window), "lower": judge_lower(feature, recent_failed, recent_ok, fail_streak),
            "allowances": self._allowances(company_id, feature.purpose, now),
        }

    def table(self, company_id: int) -> dict:
        return {
            "installed": True, "rows": [self.row(company_id, f) for f in FEATURES],
            "rules": {"raise_streak": RAISE_STREAK, "raise_window_days": RAISE_WINDOW_DAYS, "lower_window_days": LOWER_WINDOW_DAYS,
                      "lower_fails_write": LOWER_FAILS_WRITE, "lower_fails_other": LOWER_FAILS_OTHER, "lower_fail_rate": LOWER_FAIL_RATE,
                      "lower_fail_streak": LOWER_FAIL_STREAK},
        }


__all__ = ["AutonomyTableService", "FEATURES", "Feature", "STAGES", "judge_lower", "judge_raise"]
