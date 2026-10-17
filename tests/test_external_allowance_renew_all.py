"""
=========================================================
Homez OS

File : tests/test_external_allowance_renew_all.py

2026-10-17 — 연동 안정화 A2: 만료 임박·만료 상시 조회 허용을 한 번의 비밀번호 확인으로 같은 한도로 일괄 연장한다.

고정하는 규칙:
- 대상은 상시(STANDING) 조회(READ) 허용뿐이다. 목적별 가장 최근 항목만 보고, 만료까지 12시간 이하이거나 이미 만료(7일 안)된 것이다.
- 사용자가 취소·거절한 것, 아직 대기 중인 것, 변경(쓰기)·횟수형 허용은 연장하지 않는다(결정을 존중하고 권한을 넓히지 않는다).
- 연장은 같은 시간당 한도·같은 목적으로 새 허용을 만든다. 이미 새로 연장했으면 다시 대상이 아니다.
- API는 비밀번호 재확인이 필요하고 감사 기록을 남긴다. 요약 응답에 `renewable`이 실려 화면이 알린다.
=========================================================
"""

import unittest
from datetime import timedelta
from pathlib import Path

from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from tests.test_external_call_allowance import ServiceBase

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "app/domains/external_call/router.py").read_text(encoding="utf-8")
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")


class RenewTests(ServiceBase):

    def standing(self, purpose=CallPurpose.ORDER_DETECTION, per_hour=14, hours=24):
        return self.grant(
            system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=purpose, mode=AllowanceMode.STANDING,
            count=None, per_hour_limit=per_hour, hours=hours,
        )

    def test_nothing_is_offered_while_the_allowance_has_plenty_of_time(self):
        self.standing(hours=24)
        self.assertEqual(self.svc().renewable(1), [])

    def test_an_allowance_close_to_expiry_is_offered_with_its_hours_left(self):
        self.standing(hours=24)
        self.clock[0] += timedelta(hours=14)
        items = self.svc().renewable(1)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["purpose"], items[0]["state"], items[0]["per_hour_limit"]), ("ORDER_DETECTION", "EXPIRING", 14))
        self.assertEqual(items[0]["hours_left"], 10.0)

    def test_an_expired_allowance_is_offered_for_a_week_then_dropped(self):
        self.standing(hours=24)
        self.clock[0] += timedelta(days=2)
        self.assertEqual(self.svc().renewable(1)[0]["state"], "EXPIRED")
        self.clock[0] += timedelta(days=9)
        self.assertEqual(self.svc().renewable(1), [])

    def test_renewing_creates_a_new_allowance_with_the_same_limit_and_removes_it_from_the_list(self):
        self.standing(per_hour=14)
        self.standing(purpose=CallPurpose.AFTER_SALES_CHECK, per_hour=30)
        self.clock[0] += timedelta(hours=20)
        created = self.svc().renew_expiring(1, user_id=1)
        self.assertEqual(sorted((a.purpose, a.per_hour_limit, a.mode, a.status) for a in created), [
            ("AFTER_SALES_CHECK", 30, "STANDING", "ACTIVE"), ("ORDER_DETECTION", 14, "STANDING", "ACTIVE"),
        ])
        for allowance in created:
            self.assertEqual(allowance.expires_at, self.clock[0] + timedelta(hours=24))
        self.assertEqual(self.svc().renewable(1), [])
        self.assertEqual(self.svc().renew_expiring(1, user_id=1), [])

    def test_user_decisions_and_other_kinds_are_not_touched(self):
        revoked = self.standing(purpose=CallPurpose.ORDER_DETECTION)
        self.svc().revoke(1, revoked.id, user_id=1)
        self.grant(purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS, mode=AllowanceMode.COUNTED, count=3)
        self.svc().ensure_pending_request(1, ExternalSystem.COUPANG, CallKind.READ, CallPurpose.ORDER_TRACKING)
        self.clock[0] += timedelta(days=2)
        self.assertEqual(self.svc().renewable(1), [])
        before = self.db.query(ExternalCallAllowance).count()
        self.assertEqual(self.svc().renew_expiring(1, user_id=1), [])
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), before)

    def test_only_the_latest_allowance_per_purpose_counts(self):
        self.standing(hours=24)
        self.clock[0] += timedelta(hours=20)
        self.svc().renew_expiring(1, user_id=1)
        self.clock[0] += timedelta(hours=6)  # 옛 허용은 만료됐지만 새 허용이 유효하다.
        self.assertEqual(self.svc().renewable(1), [])

    def test_the_summary_carries_the_renewable_list(self):
        self.standing(hours=24)
        self.clock[0] += timedelta(hours=14)
        summary = self.svc().summary(1)
        self.assertEqual([i["purpose"] for i in summary["renewable"]], ["ORDER_DETECTION"])

    def test_other_companies_are_not_renewed(self):
        self.standing(hours=24)
        self.clock[0] += timedelta(hours=20)
        self.assertEqual(self.svc().renewable(2), [])
        self.assertEqual(self.svc().renew_expiring(2, user_id=9), [])


class WiringTests(unittest.TestCase):

    def test_the_endpoint_needs_a_recent_password_check_and_writes_an_audit_log(self):
        body = ROUTER.split('@router.post("/renew-expiring")')[1].split("@router.post(")[0]
        self.assertLess(body.index("_require_recent_auth("), body.index("ExternalCallPolicyService(db).renew_expiring("))
        self.assertIn('action="EXTERNAL_CALL_RENEWED"', body)
        self.assertIn("class RenewRequest", ROUTER)
        self.assertIn('extra="forbid"', ROUTER.split("class RenewRequest")[1].split("@router")[0])

    def test_the_screen_confirms_then_asks_for_the_password_before_the_request(self):
        body = JS.split("async function renewAllDialog(")[1].split("async function grantDialog(")[0]
        self.assertLess(body.index("await ezDialog("), body.index("promptRecentAuthToken"))
        self.assertLess(body.index("promptRecentAuthToken"), body.index("/external-calls/renew-expiring"))
        self.assertIn("X-Recent-Auth-Token", body)

    def test_the_today_list_warns_before_the_allowance_runs_out(self):
        self.assertIn('key: "renew", group: "APPROVAL"', JS)
        self.assertIn("ext.renewable", JS)

    def test_the_texts_exist_in_both_languages(self):
        import re

        texts = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
        for lang in ("ko-KR", "en-US"):
            block = re.search(r'"%s": \{(.*?)\n  \}' % lang, texts, re.S).group(1)
            for key in ("renew_title", "renew_sub", "renew_btn", "renew_hours", "renew_expired", "renew_soon",
                        "renew_confirm_title", "renew_confirm_body", "renew_done"):
                self.assertIn(f'"easy.ap.{key}"', block, (lang, key))
            for key in ("renew_title", "renew_sub", "renew_name", "renew_action", "renew_detail"):
                self.assertIn(f'"easy.today.{key}"', block, (lang, key))


if __name__ == "__main__":
    unittest.main()
