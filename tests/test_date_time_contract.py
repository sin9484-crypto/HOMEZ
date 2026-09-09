"""
=========================================================
Homez OS

File : tests/test_date_time_contract.py

Gate M-1(2026-08-21) — HomezI18n.parseUtcDate()/formatDate()의 실제
동작을 검증한다. 이 저장소에는 JS 테스트 러너가 없다(TestClient도
httpx 미설치로 못 씀 — 기존 관례) — 그래서 실제 app/web/i18n/i18n.js
소스를 Node.js(`node`, 이 개발 환경에 이미 존재 — console.js/i18n.js
문법 검사에 이미 사용 중)로 그대로 실행해 진짜 브라우저 JS 엔진의
Date 파싱 동작을 검증한다. DOM 전체를 흉내내지 않고 i18n.js가 로드
시점에 실제로 건드리는 최소 전역(window/navigator/localStorage)만
스텁한다.
=========================================================
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_I18N_JS_PATH = _REPO_ROOT / "app" / "web" / "i18n" / "i18n.js"

_HARNESS_TEMPLATE = """
"use strict";
const store = {};
global.window = global;
// Node 20+/24는 전역 navigator를 getter-only로 이미 제공한다 —
// 단순 대입은 TypeError가 난다. defineProperty로 덮어쓴다.
Object.defineProperty(global, "navigator", {
  value: { language: "ko-KR" }, configurable: true, writable: true,
});
global.localStorage = {
  getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
  setItem: (k, v) => { store[k] = v; },
};
global.document = {
  querySelectorAll: () => [],
  documentElement: {},
  dispatchEvent: () => {},
};
global.CustomEvent = function CustomEvent() {};

{i18n_source}

const HomezI18n = window.HomezI18n;
const results = {};

function msOrNull(d) {
  return d === null ? null : d.getTime();
}

// 1) naive UTC(타임존 표기 없음) — Z를 붙여 UTC로 해석해야 한다.
results.naive_utc_ms = msOrNull(HomezI18n.parseUtcDate("2026-08-21T13:20:00.554597"));
results.naive_utc_expected_ms = Date.UTC(2026, 7, 21, 13, 20, 0, 554);

// 2) 이미 Z가 있음 — 중복으로 또 붙이면 안 된다(붙이면 파싱 자체가
//    실패하거나 값이 달라진다 — 여기서는 정상적으로 같은 값이 나와야
//    함을 확인한다).
results.already_z_ms = msOrNull(HomezI18n.parseUtcDate("2026-08-21T13:20:00Z"));
results.already_z_expected_ms = Date.UTC(2026, 7, 21, 13, 20, 0, 0);

// 3) 이미 +00:00 오프셋(신규 audit_logs.created_at 포맷) — 그대로.
results.plus_zero_offset_ms = msOrNull(HomezI18n.parseUtcDate("2026-08-21T13:20:00.123456+00:00"));
results.plus_zero_offset_expected_ms = Date.UTC(2026, 7, 21, 13, 20, 0, 123);

// 4) 이미 +09:00 오프셋 — naive로 오인해 다시 Z를 붙이면 실제
//    UTC 04:20이어야 할 값이 13:20으로 잘못 해석된다(9시간 차이) —
//    반드시 원래 오프셋을 그대로 존중해야 한다.
results.plus_nine_offset_ms = msOrNull(HomezI18n.parseUtcDate("2026-08-21T13:20:00+09:00"));
results.plus_nine_offset_expected_ms = Date.UTC(2026, 7, 21, 4, 20, 0, 0);

// 5) 날짜만 있는 값(YYYY-MM-DD) — ISO 8601 스펙상 이미 UTC 자정으로
//    해석된다 — 손대지 않아야 한다(Z를 붙이면 "YYYY-MM-DDZ"가 되어
//    스펙 밖의 값이 된다).
results.date_only_ms = msOrNull(HomezI18n.parseUtcDate("2026-08-21"));
results.date_only_expected_ms = Date.UTC(2026, 7, 21, 0, 0, 0, 0);

// 6) 빈 값/null/undefined — 안전하게 null(빈 상태)이어야 한다.
results.empty_string_is_null = HomezI18n.parseUtcDate("") === null;
results.null_is_null = HomezI18n.parseUtcDate(null) === null;
results.undefined_is_null = HomezI18n.parseUtcDate(undefined) === null;

// 7) 잘못된 문자열 — 안전하게 null이어야 한다(NaN Date를 그대로
//    흘려보내지 않는다).
results.garbage_is_null = HomezI18n.parseUtcDate("not-a-real-date") === null;

// 8) Date 인스턴스는 그대로 통과.
const d = new Date(Date.UTC(2026, 0, 1, 0, 0, 0));
results.date_instance_passthrough = HomezI18n.parseUtcDate(d) === d;

// 9) formatDate()가 null/빈 값에서 "" (안전한 빈 상태)를 돌려주는지.
results.format_date_empty_result = HomezI18n.formatDate("");
results.format_date_garbage_result = HomezI18n.formatDate("garbage");

// 10) formatDate()가 naive 문자열과 이미-Z문자열에 대해 동일한 렌더
//     결과를 내는지(같은 순간을 가리키므로 표시도 같아야 한다).
const optsFixed = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "UTC" };
results.format_date_naive_matches_explicit_z = (
  HomezI18n.formatDate("2026-08-21T13:20:00", optsFixed)
  === HomezI18n.formatDate("2026-08-21T13:20:00Z", optsFixed)
);

console.log(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "이 환경에 node 실행파일이 없어 건너뜀")
class ParseUtcDateNodeBehaviorTestCase(unittest.TestCase):
    """실제 Node.js로 app/web/i18n/i18n.js를 그대로 실행해 브라우저와
    동일한 Date 파싱 엔진으로 검증한다(정규식/문자열 흉내가 아니라
    진짜 동작)."""

    @classmethod
    def setUpClass(cls):

        i18n_source = _I18N_JS_PATH.read_text(encoding="utf-8")
        harness = _HARNESS_TEMPLATE.replace("{i18n_source}", i18n_source)

        fd, path = tempfile.mkstemp(suffix=".js")
        import os
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(harness)
        cls._harness_path = Path(path)

        proc = subprocess.run(
            ["node", str(cls._harness_path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"node harness 실행 실패(exit={proc.returncode}):\n"
                f"stdout={proc.stdout}\nstderr={proc.stderr}",
            )
        cls.results = json.loads(proc.stdout)

    @classmethod
    def tearDownClass(cls):

        if cls._harness_path.exists():
            cls._harness_path.unlink()

    def test_naive_utc_string_is_interpreted_as_utc_not_local(self):

        self.assertEqual(
            self.results["naive_utc_ms"], self.results["naive_utc_expected_ms"],
        )

    def test_already_zulu_suffixed_string_is_not_double_suffixed(self):

        self.assertEqual(
            self.results["already_z_ms"], self.results["already_z_expected_ms"],
        )

    def test_already_plus_zero_offset_string_is_respected(self):

        self.assertEqual(
            self.results["plus_zero_offset_ms"],
            self.results["plus_zero_offset_expected_ms"],
        )

    def test_existing_non_utc_offset_is_not_reinterpreted_as_naive(self):
        """+09:00처럼 UTC가 아닌 기존 오프셋이 있는 문자열에 실수로
        Z를 또 붙이면 9시간이 완전히 잘못된 방향으로 어긋난다 — 가장
        위험한 회귀 시나리오라 별도로 강하게 확인한다."""

        self.assertEqual(
            self.results["plus_nine_offset_ms"],
            self.results["plus_nine_offset_expected_ms"],
        )

    def test_date_only_string_is_untouched_and_treated_as_utc_midnight(self):

        self.assertEqual(
            self.results["date_only_ms"], self.results["date_only_expected_ms"],
        )

    def test_empty_null_undefined_all_return_null(self):

        self.assertTrue(self.results["empty_string_is_null"])
        self.assertTrue(self.results["null_is_null"])
        self.assertTrue(self.results["undefined_is_null"])

    def test_garbage_string_returns_null_not_invalid_date(self):

        self.assertTrue(self.results["garbage_is_null"])

    def test_date_instance_passes_through_unchanged(self):

        self.assertTrue(self.results["date_instance_passthrough"])

    def test_format_date_returns_blank_for_empty_or_invalid(self):

        self.assertEqual(self.results["format_date_empty_result"], "")
        self.assertEqual(self.results["format_date_garbage_result"], "")

    def test_format_date_renders_naive_and_explicit_zulu_identically(self):
        """같은 순간을 가리키는 naive 문자열과 명시적 Z 문자열은
        화면에 표시되는 결과도 완전히 같아야 한다 — 이것이 이번
        수정의 실제 사용자 체감 효과다."""

        self.assertTrue(self.results["format_date_naive_matches_explicit_z"])


if __name__ == "__main__":
    unittest.main()
