"""
=========================================================
Homez OS

File : tests/test_gate8_restore_app_closed_confirmation.py

V7 Gate 8(2026-08-15) — `app/domains/restore/service.py::
require_app_closed_confirmation()` 검증.

배경: 실제 운영 DB 복원 실행 엔드포인트는 여전히 노출되지 않는다
(Gate Y-2와 동일한 V7 Live Gate 경계 유지) — 하지만 CTO 지시에 따라
"복원 전 재백업 + 전체 앱 종료 확인" 안전장치 중 재백업만 있고
"앱 종료 확인"이 없다는 것을 Gate 8 감사에서 확인해, 향후 실행
엔드포인트가 노출될 때 즉시 쓸 수 있는 fail-closed 헬퍼를
추가했다. 이 테스트는 그 헬퍼 자체의 계약만 검증한다(라우터에
아직 연결되지 않았으므로 라우터 테스트는 없다).
=========================================================
"""

import unittest

from app.domains.restore.service import RestoreError
from app.domains.restore.service import require_app_closed_confirmation


class RequireAppClosedConfirmationTestCase(unittest.TestCase):

    def test_true_passes_silently(self):

        require_app_closed_confirmation(True)  # 예외 없이 통과해야 함

    def test_false_is_rejected(self):

        with self.assertRaises(RestoreError):
            require_app_closed_confirmation(False)

    def test_none_is_rejected(self):

        with self.assertRaises(RestoreError):
            require_app_closed_confirmation(None)

    def test_truthy_non_bool_values_are_rejected(self):
        """
        fail-closed 계약 확인 — "true" 문자열이나 1 같은 truthy 값도
        엄격히 True(bool)가 아니면 통과시키지 않는다(문자열 파싱
        실수로 우회되는 것을 막는다).
        """

        for value in ["true", "True", 1, 1.0, [True]]:
            with self.subTest(value=value):
                with self.assertRaises(RestoreError):
                    require_app_closed_confirmation(value)


if __name__ == "__main__":
    unittest.main()
