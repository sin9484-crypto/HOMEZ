"""
=========================================================
Homez OS

File : tests/test_store_connection_verification_token_boot_id.py

2026-08-04 V6 Gate 2A: StoreConnection verification token의 boot_id
기반 프로세스 재시작 재사용 차단 + jti 단발성 소비의 나머지 필수
시나리오(만료, 동시 소비, 저장소 thread-safety, 정리). 실제 homez.db는
사용하지 않는다.
=========================================================
"""

import threading
import unittest
from datetime import datetime, timedelta

from app.domains.store_connection.verification_token import (
    InvalidVerificationTokenError,
    current_boot_id,
    issue_token,
    reset_boot_id_for_tests,
    reset_verification_token_jti_state_for_tests,
    verify_token,
)
import app.domains.store_connection.verification_token as vt_module

COMPANY_ID = 1
MARKETPLACE_CODE = "COUPANG"
SELLER_ID = "boot-id-seller"
CREDENTIAL_FIELDS = {"vendor_id": "A00123456", "access_key": "fake-access", "secret_key": "fake-secret"}


class VerificationTokenBootIdTestCase(unittest.TestCase):

    def setUp(self):
        self._original_boot_id = current_boot_id()

    def tearDown(self):
        reset_verification_token_jti_state_for_tests()
        reset_boot_id_for_tests(self._original_boot_id)

    # --------------------------------------------------
    # 기본 단발성
    # --------------------------------------------------

    def test_first_use_succeeds(self):

        token, _ = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

    def test_second_use_of_same_token_rejected(self):

        token, _ = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

    # --------------------------------------------------
    # 재시작(boot_id 교체) 이후 이전 토큰 거부
    # --------------------------------------------------

    def test_token_rejected_after_simulated_restart_even_if_unused(self):
        """
        발급만 되고 아직 한 번도 쓰이지 않은(=jti 저장소에는 안 걸리는)
        토큰이라도, "재시작" 이후에는 거부돼야 한다 — jti 단발성만으로는
        막지 못했던 정확한 그 구멍.
        """

        token, _ = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        reset_boot_id_for_tests()  # 프로세스 재시작 흉내 — jti 저장소도 실제로는 함께 비워진다.

        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

    def test_token_from_different_boot_id_rejected_even_with_valid_signature(self):
        """
        boot_id만 다른(그 외 payload는 전부 유효한) 토큰을 직접 만들어
        constant-time 비교 자체가 실제로 실패를 만들어내는지 확인한다.
        """

        token, expires_at = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        # 이 프로세스의 boot_id를 바꿔, 방금 발급한 토큰이 "다른 boot_id"
        # 토큰이 되도록 만든다.
        reset_boot_id_for_tests("a-completely-different-boot-id")

        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

    def test_new_token_issued_after_restart_uses_new_boot_id_and_works(self):

        reset_boot_id_for_tests()
        new_boot_id = current_boot_id()

        token, _ = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        # "재시작 후 새로 발급된" 토큰은 새 boot_id로 정상 검증된다.
        verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)
        self.assertEqual(current_boot_id(), new_boot_id)

    # --------------------------------------------------
    # 만료
    # --------------------------------------------------

    def test_expired_token_rejected(self):

        issued_at = datetime.utcnow() - timedelta(minutes=10)
        token, expires_at = issue_token(
            COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS, now=issued_at,
        )
        self.assertLess(expires_at, datetime.utcnow())

        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

    def test_token_still_valid_just_before_expiry(self):

        token, expires_at = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        just_before_expiry = expires_at - timedelta(seconds=1)
        verify_token(
            token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS,
            now=just_before_expiry,
        )

    # --------------------------------------------------
    # 동시 소비 — 정확히 1건만 성공
    # --------------------------------------------------

    def test_concurrent_verify_of_same_token_exactly_one_succeeds(self):

        token, _ = issue_token(COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            try:
                verify_token(token, COMPANY_ID, MARKETPLACE_CODE, SELLER_ID, CREDENTIAL_FIELDS)
                ok = True
            except InvalidVerificationTokenError:
                ok = False
            with results_lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 9)

    def test_ten_distinct_tokens_concurrently_all_succeed_exactly_once(self):
        """저장소 thread-safety를 서로 다른 키에 대해서도 확인한다(교차 오염 없음)."""

        tokens = [
            issue_token(COMPANY_ID, MARKETPLACE_CODE, f"seller-{i}", CREDENTIAL_FIELDS)[0]
            for i in range(10)
        ]

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker(tok, seller):
            barrier.wait()
            try:
                verify_token(tok, COMPANY_ID, MARKETPLACE_CODE, seller, CREDENTIAL_FIELDS)
                ok = True
            except InvalidVerificationTokenError:
                ok = False
            with results_lock:
                results.append(ok)

        threads = [
            threading.Thread(target=worker, args=(tokens[i], f"seller-{i}"))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 10, results)

    # --------------------------------------------------
    # 만료된 jti 정리(메모리 무한 증가 방지)
    # --------------------------------------------------

    def test_expired_jti_entries_are_cleaned_up_on_next_consume(self):
        """
        verify_token()은 만료 검사를 jti 소비보다 먼저 하므로, 청소
        로직 자체는 내부 함수(_consume_jti)를 직접 호출해 독립적으로
        검증한다 — 메모리 무한 증가를 막는지가 관심사다.
        """

        past = datetime.utcnow() - timedelta(minutes=1)
        vt_module._consume_jti("already-expired-jti", past, datetime.utcnow() - timedelta(minutes=2))
        self.assertIn("already-expired-jti", vt_module._consumed_jtis)

        # 새로운 소비 시도가 청소를 트리거한다.
        vt_module._consume_jti("trigger-cleanup", datetime.utcnow() + timedelta(minutes=5), datetime.utcnow())

        self.assertNotIn("already-expired-jti", vt_module._consumed_jtis)


if __name__ == "__main__":
    unittest.main()
