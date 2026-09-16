"""
=========================================================
Homez OS

File : tests/test_recall_notice_mfds_provider.py

2026-09-16 개인 베타 잔여 작업(Phase 4, HOMEZ_USER_OPERATION_SETTINGS.md
10-17) — `MfdsRecallNoticeProvider`의 응답 파싱 로직 검증.

이 파일은 **실제 네트워크를 전혀 사용하지 않는다.** 공식 문서에서
확인한 응답 스펙과 동일한 구조의 합성(가짜) JSON 텍스트만 사용해
파싱 로직을 검증한다(`http_get`을 Fake 콜러블로 주입) — 실제 서버가
정말 이 구조로 응답하는지는 실제 인증키로 최초 1회 호출해봐야만
확인 가능하며, 그 호출은 사용자의 별도 승인 전까지 하지 않는다.
=========================================================
"""

import unittest

from app.domains.recall_notice.mfds_provider import (
    MFDS_SERVICE_ID,
    MFDS_SOURCE_LABEL,
    MfdsRecallNoticeProvider,
    MfdsResponseFormatError,
    parse_mfds_response,
)


def _sample_row(**overrides) -> dict:
    row = {
        "PRDTNM": "어린이용 장난감 자동차",
        "RTRVLPRVNS": "KC 인증 표시 위반",
        "BSSHNM": "(주)테스트완구",
        "ADDR": "서울특별시 강남구",
        "TELNO": "02-000-0000",
        "BRCDNO": "8801234567890",
        "FRMLCUNIT": "1개입",
        "MNFDT": "20260101",
        "RTRVLPLANDOC_RTRVLMTHD": "판매중지 및 수거",
        "DISTBTMLMT": "20270101",
        "PRDLST_TYPE": "완구류",
        "IMG_FILE_PATH": "https://example.foodsafetykorea.go.kr/img/1.jpg",
        "PRDLST_CD": "TOY-000123",
        "CRET_DTM": "20260915",
        "RTRVLDSUSE_SEQ": "1",
        "PRDLST_REPORT_NO": "REPORT-000123",
        "RTRVL_GRDCD_NM": "1등급",
        "PRDLST_CD_NM": "장난감",
    }
    row.update(overrides)
    return row


class ParseMfdsResponseTestCase(unittest.TestCase):

    def test_confirmed_schema_parses_into_recall_notice_record(self):

        body = {MFDS_SERVICE_ID: {"row": [_sample_row()]}}
        records = parse_mfds_response(body)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.product_identifier, "TOY-000123")
        self.assertEqual(record.reason, "KC 인증 표시 위반")
        self.assertEqual(record.source, MFDS_SOURCE_LABEL)
        self.assertEqual(record.manufacturer, "(주)테스트완구")
        self.assertEqual(record.model, "장난감")
        self.assertEqual(record.announcement_date.strftime("%Y%m%d"), "20260915")

    def test_multiple_rows_all_parsed(self):

        body = {
            MFDS_SERVICE_ID: {
                "row": [
                    _sample_row(PRDLST_CD="TOY-1"),
                    _sample_row(PRDLST_CD="TOY-2", RTRVLPRVNS="유해물질 검출"),
                ],
            },
        }
        records = parse_mfds_response(body)

        self.assertEqual([r.product_identifier for r in records], ["TOY-1", "TOY-2"])
        self.assertEqual(records[1].reason, "유해물질 검출")

    def test_zero_results_is_not_an_error(self):

        body = {MFDS_SERVICE_ID: {"row": []}}
        self.assertEqual(parse_mfds_response(body), [])

    def test_missing_row_key_treated_as_zero_results(self):

        body = {MFDS_SERVICE_ID: {"RESULT": {"CODE": "INFO-200", "MSG": "해당하는 데이터가 없습니다."}}}
        self.assertEqual(parse_mfds_response(body), [])

    def test_row_missing_product_identifier_is_skipped(self):

        body = {
            MFDS_SERVICE_ID: {
                "row": [
                    _sample_row(PRDLST_CD=None, PRDTNM=None),
                    _sample_row(PRDLST_CD="TOY-OK"),
                ],
            },
        }
        records = parse_mfds_response(body)
        self.assertEqual([r.product_identifier for r in records], ["TOY-OK"])

    def test_row_missing_reason_is_skipped(self):

        body = {MFDS_SERVICE_ID: {"row": [_sample_row(RTRVLPRVNS=None)]}}
        self.assertEqual(parse_mfds_response(body), [])

    def test_falls_back_to_product_name_when_code_absent(self):

        body = {MFDS_SERVICE_ID: {"row": [_sample_row(PRDLST_CD=None)]}}
        records = parse_mfds_response(body)
        self.assertEqual(records[0].product_identifier, "어린이용 장난감 자동차")

    def test_unparseable_date_leaves_announcement_date_none_without_dropping_record(self):

        body = {MFDS_SERVICE_ID: {"row": [_sample_row(CRET_DTM="알수없음")]}}
        records = parse_mfds_response(body)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].announcement_date)

    def test_dash_separated_date_also_parses(self):

        body = {MFDS_SERVICE_ID: {"row": [_sample_row(CRET_DTM="2026-09-15")]}}
        records = parse_mfds_response(body)
        self.assertEqual(records[0].announcement_date.strftime("%Y%m%d"), "20260915")

    def test_response_alias_key_is_also_accepted(self):

        body = {"response": {"row": [_sample_row()]}}
        records = parse_mfds_response(body)
        self.assertEqual(len(records), 1)

    def test_non_dict_body_raises_format_error(self):

        with self.assertRaises(MfdsResponseFormatError):
            parse_mfds_response(["not", "a", "dict"])

    def test_missing_service_container_raises_format_error(self):

        with self.assertRaises(MfdsResponseFormatError):
            parse_mfds_response({"UNEXPECTED_KEY": {"row": []}})

    def test_row_not_a_list_raises_format_error(self):

        with self.assertRaises(MfdsResponseFormatError):
            parse_mfds_response({MFDS_SERVICE_ID: {"row": "not-a-list"}})

    def test_non_dict_row_entries_are_skipped_without_crashing(self):

        body = {MFDS_SERVICE_ID: {"row": ["not-a-dict", _sample_row(PRDLST_CD="TOY-OK")]}}
        records = parse_mfds_response(body)
        self.assertEqual([r.product_identifier for r in records], ["TOY-OK"])


class _FakeResponse:

    def __init__(self, payload, *, status_code=200, raise_on_json=False):
        self._payload = payload
        self.status_code = status_code
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._payload


class MfdsRecallNoticeProviderTestCase(unittest.TestCase):
    """실제 `requests`는 절대 사용하지 않는다 — `http_get`에 항상 Fake
    콜러블을 주입한다."""

    def test_requires_api_key(self):

        with self.assertRaises(ValueError):
            MfdsRecallNoticeProvider("", http_get=lambda *a, **k: _FakeResponse({}))

    def test_fetch_notices_yields_parsed_records_and_calls_real_requests_get_is_never_hit(self):

        calls = []

        def fake_get(url, timeout=None):
            calls.append((url, timeout))
            return _FakeResponse({MFDS_SERVICE_ID: {"row": [_sample_row(PRDLST_CD="TOY-1")]}})

        provider = MfdsRecallNoticeProvider(
            "FAKE_KEY", http_get=fake_get, page_size=100,
        )
        records = list(provider.fetch_notices())

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].product_identifier, "TOY-1")
        self.assertEqual(len(calls), 1)
        url, timeout = calls[0]
        self.assertIn("/FAKE_KEY/I0490/json/1/100", url)

    def test_pagination_stops_when_page_shorter_than_page_size(self):

        def fake_get(url, timeout=None):
            if "/1/2" in url:
                return _FakeResponse({
                    MFDS_SERVICE_ID: {"row": [
                        _sample_row(PRDLST_CD="TOY-1"),
                        _sample_row(PRDLST_CD="TOY-2"),
                    ]},
                })
            if "/3/4" in url:
                return _FakeResponse({MFDS_SERVICE_ID: {"row": [_sample_row(PRDLST_CD="TOY-3")]}})
            raise AssertionError(f"예상하지 못한 페이지 호출: {url}")

        provider = MfdsRecallNoticeProvider("FAKE_KEY", http_get=fake_get, page_size=2)
        records = list(provider.fetch_notices())

        self.assertEqual(
            [r.product_identifier for r in records], ["TOY-1", "TOY-2", "TOY-3"],
        )

    def test_pagination_stops_on_empty_page(self):

        def fake_get(url, timeout=None):
            if "/1/2" in url:
                return _FakeResponse({
                    MFDS_SERVICE_ID: {"row": [
                        _sample_row(PRDLST_CD="TOY-1"),
                        _sample_row(PRDLST_CD="TOY-2"),
                    ]},
                })
            return _FakeResponse({MFDS_SERVICE_ID: {"row": []}})

        provider = MfdsRecallNoticeProvider("FAKE_KEY", http_get=fake_get, page_size=2)
        records = list(provider.fetch_notices())

        self.assertEqual(len(records), 2)

    def test_non_json_response_raises_format_error(self):

        def fake_get(url, timeout=None):
            return _FakeResponse(None, status_code=500, raise_on_json=True)

        provider = MfdsRecallNoticeProvider("FAKE_KEY", http_get=fake_get)

        with self.assertRaises(MfdsResponseFormatError):
            list(provider.fetch_notices())

    def test_malformed_container_raises_and_stops_iteration(self):

        def fake_get(url, timeout=None):
            return _FakeResponse({"UNEXPECTED": {}})

        provider = MfdsRecallNoticeProvider("FAKE_KEY", http_get=fake_get)

        with self.assertRaises(MfdsResponseFormatError):
            list(provider.fetch_notices())


if __name__ == "__main__":
    unittest.main()
