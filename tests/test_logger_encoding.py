"""
=========================================================
Homez OS

File : tests/test_logger_encoding.py

app/core/logger.py의 콘솔 핸들러 인코딩 검증(2026-07-30).

실제 재현된 버그: pythonw.exe로 실행되는 Desktop Shell에서 콘솔
스트림 인코딩이 시스템 로케일(cp949)로 고정되어, em dash(—)처럼
그 코드페이지에 없는 문자가 로그 메시지에 포함되면
UnicodeEncodeError가 발생하고 이것이 pywebview 창의 FormClosing
이벤트 핸들러까지 전파되어 처리되지 않은 예외 대화상자로 앱이
죽는 문제로 실제 확인되었다. 이 테스트는 그 회귀를 방지한다.
=========================================================
"""

import io
import logging
import unittest

from app.core.logger import create_logger


class ConsoleHandlerEncodingTestCase(unittest.TestCase):

    def _get_console_handler(self, logger: logging.Logger) -> logging.StreamHandler:

        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler,
            ):
                return handler

        self.fail("콘솔 StreamHandler를 찾지 못했습니다")

    def test_console_handler_stream_is_utf8_or_safely_reconfigured(self):

        logger = create_logger("test_logger_encoding_case1")
        console = self._get_console_handler(logger)

        stream_encoding = getattr(console.stream, "encoding", None)

        if stream_encoding is not None:
            self.assertEqual(stream_encoding.lower().replace("-", ""), "utf8")

    def test_logging_em_dash_does_not_raise(self):
        """
        실제 버그 재현 문자(em dash, U+2014)를 포함한 메시지를 로깅해도
        예외가 전파되지 않아야 한다.
        """

        logger = create_logger("test_logger_encoding_case2")

        try:
            logger.info("창 종료 이벤트 수신 — 서버 정리를 준비합니다.")
        except UnicodeEncodeError as exc:
            self.fail(f"로깅 중 UnicodeEncodeError가 발생했습니다: {exc}")

    def test_logging_various_non_ascii_symbols_does_not_raise(self):

        logger = create_logger("test_logger_encoding_case3")

        symbols = "— ✓ → ★ café 日本語"

        try:
            logger.info(f"기타 유니코드 문자 테스트: {symbols}")
        except UnicodeEncodeError as exc:
            self.fail(f"로깅 중 UnicodeEncodeError가 발생했습니다: {exc}")


if __name__ == "__main__":
    unittest.main()
