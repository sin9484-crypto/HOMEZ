"""
=========================================================
Homez OS

File : tests/test_tour_mode.py

2026-08-13 — 인터랙티브 가이드 모드(Guided Tour) 정적 검증.

이 저장소 관례(tests/test_i18n.py, tests/test_guides.py)를 그대로
따른다 — httpx가 없어 실제 서버를 띄우지 않고, 순수 정적 파일
분석(console.html/console.js/i18n 카탈로그)만으로 요구사항을
검증한다. 특히 아래 항목은 "화면을 실제로 클릭해봐야" 확인 가능한
동작이 아니라, 코드 구조 자체가 안전 요구사항을 만족하는지
확인하는 성격이라 정적 검사가 오히려 더 강력하다:

  - 가이드 엔진이 실제 target에 .click()/dispatchEvent를 호출하지
    않는지(=사용자 대신 클릭하지 않는지)
  - 가이드 모듈 코드 범위 안에 apiFetch(실제 백엔드 호출)가 전혀
    없는지(=연습 모드가 실제로 아무것도 저장하지 않는지)
  - data-guide-id가 문서 전체에서 중복되지 않는지
  - 각 tour가 참조하는 target이 실제 DOM에 존재하는지
  - ko/en 번역 키가 tour.* 전체에서 대칭인지

실제 브라우저 클릭 시퀀스 검증(대상 강조, 다음 버튼 동작, 모바일
레이아웃 등)은 별도 Browser E2E(scratchpad/capture_tour_mode.py,
capture_tour_mobile.py)에서 수행하며 이 파일의 책임이 아니다.

실제 homez.db는 전혀 사용하지 않는다.
=========================================================
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(REPO_ROOT, "app", "web")
I18N_DIR = os.path.join(WEB_DIR, "i18n")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Node 자식 프로세스 실행 + 실패 시 원인 보존 (2026-10-05 66차 전체 회귀 진단 보강)
#
# 전체 회귀(5,111건)에서 이 파일의 Node 실행 9건이 "returncode != 0 + 빈 stderr"로 실패했는데
# 기존 코드는 stderr만 남겨 종료코드·stdout·실행 환경이 사라졌고 원인을 확정할 수 없었다.
# 아래 헬퍼는 **판정 조건을 바꾸지 않는다**(returncode != 0이면 그대로 실패, 시간초과도 실패) —
# 실패 메시지에 종료코드(십진·16진), stdout/stderr, 시간초과 여부, 실행 경로·존재·크기, 작업
# 디렉터리, 소요 시간, 환경 존재 여부(값은 기록하지 않음), 프로세스 자원(메모리·핸들·스레드),
# 그리고 같은 시점의 Node 단순 실행 탐침 결과를 담는다. 환경변수 `HOMEZ_NODE_DIAG_LOG`에
# 파일 경로를 주면 성공 호출을 포함한 모든 Node 호출을 한 줄씩(JSON) 추가로 기록한다 —
# 시간에 따른 자원 변화(핸들 누수 등)를 관측하기 위한 선택 기능이다. 비밀값은 기록하지 않는다.
# 이 파일의 종료코드(전체 테스트 실행의 종료코드)와 Node 자식의 종료코드는 서로 다른 값이다.
# ---------------------------------------------------------------------------

_NODE_DIAG_TEXT_LIMIT = 600


def _clip(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value if len(value) <= _NODE_DIAG_TEXT_LIMIT else value[:_NODE_DIAG_TEXT_LIMIT] + "…(잘림)"


def _process_resource_snapshot():
    snapshot = {"python_threads": threading.active_count()}
    if sys.platform != "win32":
        return snapshot
    try:
        import ctypes
        from ctypes import wintypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        kernel32 = ctypes.windll.kernel32
        # 인자 타입을 지정하지 않으면 의사 핸들(-1)이 변환 오류를 낸다(66차 진단 헬퍼의 초기 결함).
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetProcessHandleCount.restype = wintypes.BOOL
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            snapshot["memory_load_percent"] = int(status.dwMemoryLoad)
            snapshot["avail_phys_mb"] = int(status.ullAvailPhys // (1024 * 1024))
            snapshot["avail_pagefile_mb"] = int(status.ullAvailPageFile // (1024 * 1024))
        handles = wintypes.DWORD(0)
        if kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(handles)):
            snapshot["process_handle_count"] = int(handles.value)
    except Exception as exc:  # noqa: BLE001 — 진단 수집 실패가 원래 실패를 가리지 않게 한다
        snapshot["snapshot_error"] = repr(exc)
    return snapshot


def _environment_presence():
    """값은 기록하지 않고 존재·형태만 기록한다."""

    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    path_entries = [e for e in os.environ.get("PATH", "").split(os.pathsep) if e]
    return {
        "SystemRoot_set": bool(os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")),
        "windir_set": bool(os.environ.get("windir") or os.environ.get("WINDIR")),
        "TEMP_set": bool(tmp), "TEMP_dir_exists": bool(tmp) and os.path.isdir(tmp),
        "tempfile_gettempdir_exists": os.path.isdir(tempfile.gettempdir()),
        "PATH_entry_count": len(path_entries),
        "NODE_OPTIONS_set": "NODE_OPTIONS" in os.environ,
        "NODE_PATH_set": "NODE_PATH" in os.environ,
        "env_var_count": len(os.environ),
    }


def _probe_node(node_path, args, timeout=15):
    try:
        probe = subprocess.run(
            [node_path, *args], capture_output=True, text=True, timeout=timeout)
        return {"returncode": probe.returncode, "stdout": _clip(probe.stdout),
                "stderr": _clip(probe.stderr)}
    except subprocess.TimeoutExpired:
        return {"timed_out": True, "timeout": timeout}
    except OSError as exc:
        return {"spawn_error": repr(exc)}


def _node_failure_report(label, node_path, args, *, timeout, elapsed, timed_out,
                         returncode, stdout, stderr, spawn_error=None):
    script_path = args[-1] if args else None
    report = {
        "label": label, "returncode": returncode,
        "returncode_hex": (f"0x{returncode & 0xFFFFFFFF:08X}" if isinstance(returncode, int) else None),
        "timed_out": timed_out, "timeout_s": timeout, "elapsed_s": round(elapsed, 3),
        "spawn_error": spawn_error, "stdout": _clip(stdout), "stderr": _clip(stderr),
        "node_path": node_path, "node_exists": bool(node_path) and os.path.isfile(node_path),
        "node_size": (os.path.getsize(node_path) if node_path and os.path.isfile(node_path) else None),
        "cwd": os.getcwd(),
        "script_path": script_path,
        "script_exists": bool(script_path) and os.path.isfile(script_path),
        "script_size": (os.path.getsize(script_path)
                        if script_path and os.path.isfile(script_path) else None),
        "python": sys.version.split()[0], "platform": sys.platform,
        "environment": _environment_presence(),
        "resources": _process_resource_snapshot(),
        # 같은 시점에 Node 자체가 일반적으로 실행되는가 — 전역 문제(환경·자원)와 스크립트별 문제를 가른다
        "probe_node_version": _probe_node(node_path, ["--version"]) if node_path else None,
        "probe_node_trivial": _probe_node(node_path, ["-e", "process.stdout.write('ok')"]) if node_path else None,
    }
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


def _append_node_diag_log(entry):
    path = os.environ.get("HOMEZ_NODE_DIAG_LOG", "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _run_node(node_path, args, *, label, timeout=30):
    """Node를 실행하고 returncode != 0·시간초과·실행 실패는 **그대로 AssertionError**로 올린다
    (판정 완화 없음). 실패 메시지에 원인 진단을 담는다."""

    started = time.monotonic()
    try:
        result = subprocess.run(
            [node_path, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _append_node_diag_log({"label": label, "timed_out": True,
                               "elapsed_s": round(time.monotonic() - started, 3)})
        raise AssertionError(
            "Node 실행 시간초과\n" + _node_failure_report(
                label, node_path, args, timeout=timeout, elapsed=time.monotonic() - started,
                timed_out=True, returncode=None, stdout=exc.stdout, stderr=exc.stderr),
        ) from exc
    except OSError as exc:
        raise AssertionError(
            "Node 실행 시작 실패\n" + _node_failure_report(
                label, node_path, args, timeout=timeout, elapsed=time.monotonic() - started,
                timed_out=False, returncode=None, stdout=None, stderr=None,
                spawn_error=repr(exc)),
        ) from exc
    elapsed = time.monotonic() - started
    _append_node_diag_log({
        "label": label, "returncode": result.returncode, "elapsed_s": round(elapsed, 3),
        "resources": _process_resource_snapshot(), "time": time.strftime("%H:%M:%S"),
    })
    if result.returncode != 0:
        raise AssertionError(
            "Node 실행 실패(종료코드가 0이 아님)\n" + _node_failure_report(
                label, node_path, args, timeout=timeout, elapsed=elapsed, timed_out=False,
                returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
    return result


def _load_js_object_literal(path, var_name):
    """tests/test_i18n.py의 동일 헬퍼와 같은 방식 — 문자열 키:값 쌍만
    정규식으로 추출한다(순수 객체 리터럴이라 완전한 파서 불필요)."""

    content = _read(path)
    start = content.index(f"window.{var_name}")
    body = content[start:]
    pairs = re.findall(r'"([a-zA-Z0-9_.]+)":\s*"((?:[^"\\]|\\.)*)"', body)
    return dict(pairs)


def _extract_bracket_block(text, start_marker, open_ch="[", close_ch="]"):
    """`start_marker` 바로 뒤 첫 open_ch부터 짝이 맞는 close_ch까지의
    원문(괄호 포함)을 잘라낸다. 문자열 리터럴 안의 괄호는 무시하도록
    간단한 따옴표 상태 추적을 포함한다."""

    idx = text.index(start_marker)
    open_idx = text.index(open_ch, idx)
    depth = 0
    in_string = None
    i = open_idx
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == in_string:
                in_string = None
        elif c in ('"', "'", "`"):
            in_string = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
        i += 1
    raise ValueError(f"'{start_marker}'에서 짝이 맞는 '{close_ch}'를 찾지 못했습니다.")


class TourManifestNodeParsedTestCase(unittest.TestCase):
    """Node로 HOMEZ_TOURS 배열 리터럴을 실제로 평가해 JSON으로 덤프한
    뒤, 파이썬에서 구조적으로 검증한다(따옴표 없는 키 등 JS 객체
    리터럴이라 json.loads로는 직접 파싱 불가 — node eval만 신뢰
    가능)."""

    @classmethod
    def setUpClass(cls):

        node_path = shutil.which("node")
        if node_path is None:
            raise unittest.SkipTest("Node.js를 찾을 수 없어 매니페스트 구조 검사를 건너뜁니다.")

        js = _read(os.path.join(WEB_DIR, "console.js"))
        array_literal = _extract_bracket_block(js, "const HOMEZ_TOURS = ")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(array_literal)
            tmp_path = tmp.name

        try:
            script = (
                "const fs = require('fs');"
                "const src = fs.readFileSync(process.argv[1], 'utf8');"
                "const arr = eval(src);"
                "process.stdout.write(JSON.stringify(arr));"
            )
            result = _run_node(
                node_path, ["-e", script, tmp_path], label="HOMEZ_TOURS 매니페스트 평가",
            )
        finally:
            os.unlink(tmp_path)

        cls.tours = json.loads(result.stdout)
        cls.tours_by_id = {t["id"]: t for t in cls.tours}

    def test_exactly_six_courses_defined(self):
        self.assertEqual(len(self.tours), 6)

    def test_course_ids_and_order_match_required_sequence(self):
        expected_order = [
            "quick-start", "screens-and-menus", "admin-setup",
            "product-registration-practice", "mobile-usage", "troubleshooting",
        ]
        ordered = [t["id"] for t in sorted(self.tours, key=lambda t: t["order"])]
        self.assertEqual(ordered, expected_order)

    def test_each_course_has_required_card_fields(self):
        for tour in self.tours:
            for field in ("id", "titleKey", "descriptionKey", "version", "dataChange"):
                self.assertIn(field, tour, f"{tour.get('id')}에 '{field}' 필드가 없습니다.")
            self.assertIn("requiredPermission", tour)
            self.assertIsInstance(tour["steps"], list)

    def test_pilot_courses_have_real_steps(self):
        self.assertGreater(len(self.tours_by_id["screens-and-menus"]["steps"]), 0)
        self.assertGreater(len(self.tours_by_id["product-registration-practice"]["steps"]), 0)

    def test_expansion_courses_have_real_steps(self):
        """2026-08-13 확장 — 빠른 시작/문제 해결 과정을 comingSoon에서
        실제 구현으로 전환했다."""

        self.assertGreater(len(self.tours_by_id["quick-start"]["steps"]), 0)
        self.assertNotIn("comingSoon", self.tours_by_id["quick-start"])
        self.assertGreater(len(self.tours_by_id["troubleshooting"]["steps"]), 0)
        self.assertNotIn("comingSoon", self.tours_by_id["troubleshooting"])

    def test_quick_start_requires_admin_like_screens_and_menus(self):
        """빠른 시작이 재사용하는 대상(nav-dashboard/nav-products)도
        전부 관리자 전용 nav 뒤에 있다 — screens-and-menus와 동일한
        이유로 admin 권한이 필요하다."""

        self.assertEqual(self.tours_by_id["quick-start"]["requiredPermission"], "admin")

    def test_troubleshooting_has_no_permission_requirement(self):
        """topbar-status/nav-guides는 모든 로그인 사용자에게 보이므로
        (data-permission="") 이 과정은 일반 사용자도 끝까지 진행할 수
        있어야 한다."""

        self.assertIsNone(self.tours_by_id["troubleshooting"]["requiredPermission"])

    def test_still_coming_soon_courses_are_unchanged(self):
        for tour_id in ("admin-setup", "mobile-usage"):
            tour = self.tours_by_id[tour_id]
            self.assertTrue(tour.get("comingSoon"), f"{tour_id}는 아직 comingSoon이어야 합니다.")
            self.assertEqual(tour["steps"], [], f"{tour_id}는 아직 실제 단계가 없어야 합니다.")

    def test_screens_and_menus_course_is_view_only(self):
        tour = self.tours_by_id["screens-and-menus"]
        self.assertEqual(tour["dataChange"], "view_only")

    def test_screens_and_menus_requires_admin_permission(self):
        """2026-08-13 결함 수정 고정 — 이 과정이 강조하는 모든 nav
        대상(nav-dashboard/nav-products/nav-ai-listing/nav-settings)이
        console.html에서 실제로 data-permission="__admin_only__" 뒤에
        있어(applyPermissionGatedNav) 일반 사용자에게는 전부 숨겨진다.
        requiredPermission이 다시 None으로 되돌아가면 일반 사용자가
        시작하자마자 첫 클릭 단계에서 영원히 대상을 찾지 못하는
        회귀가 재발한다."""

        tour = self.tours_by_id["screens-and-menus"]
        self.assertEqual(tour["requiredPermission"], "admin")

    def test_product_registration_course_is_practice_and_admin_only(self):
        tour = self.tours_by_id["product-registration-practice"]
        self.assertEqual(tour["dataChange"], "practice")
        self.assertEqual(tour["requiredPermission"], "admin")

    def test_step_ids_are_unique_within_each_course(self):
        for tour in self.tours:
            ids = [s["id"] for s in tour["steps"]]
            self.assertEqual(len(ids), len(set(ids)), f"{tour['id']}에 중복 step id가 있습니다: {ids}")

    def test_step_targets_are_unique_within_each_course(self):
        for tour in self.tours:
            targets = []
            for step in tour["steps"]:
                if step.get("target"):
                    targets.append(step["target"])
                targets.extend(step.get("targets") or [])
            self.assertEqual(
                len(targets), len(set(targets)),
                f"{tour['id']}에서 동일 data-guide-id가 여러 step에 재사용되었습니다: {targets}",
            )

    def test_every_step_has_a_known_action_type(self):
        allowed = {"describe", "click", "input", "input-group", "select", "navigate", "open-modal", "verify", "complete"}
        for tour in self.tours:
            for step in tour["steps"]:
                self.assertIn(step["action"], allowed, f"{tour['id']}/{step['id']}의 action이 알 수 없습니다.")

    def test_click_and_input_steps_have_a_target(self):
        for tour in self.tours:
            for step in tour["steps"]:
                if step["action"] in ("click", "input"):
                    self.assertTrue(step.get("target"), f"{tour['id']}/{step['id']}는 target이 있어야 합니다.")

    def test_input_group_steps_have_matching_targets_and_validation(self):
        """2026-08-13 결함 수정 고정 — 가격만 검사하고 재고를 놓치던
        버그의 재발을 막는다: input-group 단계는 반드시 targets 배열과
        길이가 같은 validation 규칙 맵을 가져야 하고, 각 규칙은
        required=true·min=0을 가져야 한다(음수/빈값 통과 방지)."""

        found_any = False
        for tour in self.tours:
            for step in tour["steps"]:
                if step["action"] != "input-group":
                    continue
                found_any = True
                self.assertGreaterEqual(len(step.get("targets") or []), 2)
                validation = step.get("validation") or {}
                self.assertEqual(len(validation), len(step["targets"]))
                for rule in validation.values():
                    self.assertTrue(rule.get("required"), f"{tour['id']}/{step['id']}의 검증 규칙은 required여야 합니다.")
                    self.assertEqual(rule.get("min"), 0, f"{tour['id']}/{step['id']}의 검증 규칙은 min=0이어야 합니다(음수 차단).")
        self.assertTrue(found_any, "input-group 액션을 쓰는 step이 하나도 없습니다 — 가격·재고 동시 검증 수정이 반영되지 않았습니다.")

    def test_price_stock_step_validates_both_fields(self):
        tour = self.tours_by_id["product-registration-practice"]
        step = next(s for s in tour["steps"] if s["id"] == "pr-practice-price-stock")
        self.assertEqual(step["action"], "input-group")
        self.assertEqual(set(step["targets"]), {"tour-practice-price-field", "tour-practice-stock-field"})
        self.assertEqual(step["validation"]["stock"]["type"], "integer")

    def test_each_course_ends_with_exactly_one_complete_step(self):
        for tour in self.tours:
            if not tour["steps"]:
                continue
            complete_steps = [s for s in tour["steps"] if s.get("isComplete")]
            self.assertEqual(len(complete_steps), 1, f"{tour['id']}는 정확히 하나의 완료 단계를 가져야 합니다.")
            self.assertTrue(
                tour["steps"][-1].get("isComplete"),
                f"{tour['id']}의 마지막 단계가 완료 단계가 아닙니다.",
            )

    def test_practice_course_never_targets_real_save_buttons(self):
        """실제 백엔드에 저장하는 버튼(#lp-create-btn 등 AI 상품 등록
        화면의 실제 제출 버튼)을 연습 코스가 절대 target으로 삼지
        않는지 확인한다 — target으로 삼으면 사용자가 실제로 그
        버튼을 클릭해야 진행되므로 실제 저장이 벌어질 수 있다."""

        real_write_targets = {"lp-create-btn", "lw-create-new-btn", "lp-submit-btn"}
        tour = self.tours_by_id["product-registration-practice"]
        for step in tour["steps"]:
            if step.get("target"):
                self.assertNotIn(
                    step["target"], real_write_targets,
                    f"{step['id']}가 실제 저장 버튼을 target으로 삼고 있습니다.",
                )

    def test_practice_course_save_step_uses_practice_panel_target(self):
        tour = self.tours_by_id["product-registration-practice"]
        save_step = next(s for s in tour["steps"] if s.get("isComplete"))
        self.assertTrue(save_step.get("practicePanel"), "완료 단계는 연습 패널(practicePanel) 안에 있어야 합니다.")
        self.assertEqual(save_step["target"], "tour-practice-save-btn")


def _extract_function_block(text, fn_name):
    """`_extract_bracket_block`은 여는 괄호부터만 반환하므로(HOMEZ_TOURS
    배열 추출에는 맞지만) 여기서는 `function name(...) { ... }` 전체
    선언문이 필요하다 — marker 시작 위치부터 짝이 맞는 닫는 중괄호
    직후까지 직접 다시 잘라낸다."""

    marker = f"function {fn_name}("
    start = text.index(marker)
    body_only = _extract_bracket_block(text, marker, open_ch="{", close_ch="}")
    open_idx = text.index("{", start)
    end_idx = open_idx + len(body_only)
    return text[start:end_idx]


class TourProgressStorageBehaviorTestCase(unittest.TestCase):
    """2026-08-13 진행 상태 저장 v2(schemaVersion/scopes/lastStepId)
    실제 동작 검증 — 구조 검사가 아니라 Node에서 실제 함수를 그대로
    실행해 v1→v2 마이그레이션, 손상된 JSON, 알 수 없는 미래 버전,
    삭제된 step id로부터의 안전한 재개까지 검증한다."""

    @classmethod
    def setUpClass(cls):
        node_path = shutil.which("node")
        if node_path is None:
            raise unittest.SkipTest("Node.js를 찾을 수 없어 진행 상태 저장 동작 검증을 건너뜁니다.")
        cls.node_path = node_path
        cls.js = _read(os.path.join(WEB_DIR, "console.js"))

    def _run_scenarios(self, scenarios_js):
        blocks = "\n".join(
            _extract_function_block(self.js, fn)
            for fn in ("tourEmptyProgressRoot", "tourLoadAllProgress", "tourSaveAllProgress", "tourResolveResumeIndex")
        )
        script = f"""
        const TOUR_PROGRESS_KEY = "homez_tour_progress_v1";
        const TOUR_PROGRESS_SCHEMA_VERSION = 2;
        let __store = {{}};
        const localStorage = {{
          getItem: (k) => (Object.prototype.hasOwnProperty.call(__store, k) ? __store[k] : null),
          setItem: (k, v) => {{ __store[k] = String(v); }},
          removeItem: (k) => {{ delete __store[k]; }},
        }};
        {blocks}
        const results = {{}};
        {scenarios_js}
        process.stdout.write(JSON.stringify(results));
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as tmp:
            tmp.write(script)
            tmp_path = tmp.name
        try:
            result = _run_node(
                self.node_path, [tmp_path], label=f"진행 상태 시나리오 {self._testMethodName}",
            )
        finally:
            os.unlink(tmp_path)
        return json.loads(result.stdout)

    def test_v1_raw_object_migrates_to_v2_scopes_without_loss(self):
        out = self._run_scenarios("""
          __store[TOUR_PROGRESS_KEY] = JSON.stringify({ "7": { "screens-and-menus": { lastStepIndex: 3, completed: false, skippedStepIds: [], lastRunAt: "2026-01-01" } } });
          const root = tourLoadAllProgress();
          results.schemaVersion = root.schemaVersion;
          results.migratedIndex = root.scopes["7"]["screens-and-menus"].lastStepIndex;
        """)
        self.assertEqual(out["schemaVersion"], 2)
        self.assertEqual(out["migratedIndex"], 3)

    def test_corrupted_json_returns_empty_root_without_crashing(self):
        out = self._run_scenarios("""
          __store[TOUR_PROGRESS_KEY] = "{not valid json!!";
          const root = tourLoadAllProgress();
          results.schemaVersion = root.schemaVersion;
          results.scopesIsEmptyObject = JSON.stringify(root.scopes) === "{}";
        """)
        self.assertEqual(out["schemaVersion"], 2)
        self.assertTrue(out["scopesIsEmptyObject"])

    def test_unknown_future_schema_version_falls_back_to_empty_root(self):
        out = self._run_scenarios("""
          __store[TOUR_PROGRESS_KEY] = JSON.stringify({ schemaVersion: 99, scopes: { should: "not be trusted" } });
          const root = tourLoadAllProgress();
          results.scopesIsEmptyObject = JSON.stringify(root.scopes) === "{}";
        """)
        self.assertTrue(out["scopesIsEmptyObject"])

    def test_v2_data_round_trips_unchanged(self):
        out = self._run_scenarios("""
          tourSaveAllProgress({ "9": { "product-registration-practice": { lastStepId: "pr-practice-image", completed: false } } });
          const root = tourLoadAllProgress();
          results.lastStepId = root.scopes["9"]["product-registration-practice"].lastStepId;
        """)
        self.assertEqual(out["lastStepId"], "pr-practice-image")

    def test_resume_index_prefers_step_id_over_stale_index(self):
        out = self._run_scenarios("""
          const tour = { steps: [{ id: "a" }, { id: "b" }, { id: "c" }] };
          results.byId = tourResolveResumeIndex(tour, { lastStepId: "c", lastStepIndex: 0 });
        """)
        self.assertEqual(out["byId"], 2)

    def test_resume_index_falls_back_to_zero_when_step_id_removed(self):
        """과정 단계가 개편되어 저장된 lastStepId가 더 이상 존재하지
        않으면(예: 매니페스트 버전업으로 단계 삭제) 잘못된 인덱스로
        재개하지 않고 안전하게 처음부터 다시 시작해야 한다."""

        out = self._run_scenarios("""
          const tour = { steps: [{ id: "a" }, { id: "b" }] };
          results.idx = tourResolveResumeIndex(tour, { lastStepId: "removed-step", lastStepIndex: 5 });
        """)
        self.assertEqual(out["idx"], 0)

    def test_resume_index_clamps_stale_v1_index_without_step_id(self):
        out = self._run_scenarios("""
          const tour = { steps: [{ id: "a" }, { id: "b" }] };
          results.idx = tourResolveResumeIndex(tour, { lastStepIndex: 99 });
        """)
        self.assertEqual(out["idx"], 1)

    def test_no_progress_resumes_at_zero(self):
        out = self._run_scenarios("""
          const tour = { steps: [{ id: "a" }] };
          results.idx = tourResolveResumeIndex(tour, null);
        """)
        self.assertEqual(out["idx"], 0)


class TourDataGuideIdTargetingTestCase(unittest.TestCase):
    """data-guide-id 기반 안정적 대상 식별 요구사항."""

    @classmethod
    def setUpClass(cls):
        cls.html = _read(os.path.join(WEB_DIR, "console.html"))
        cls.js = _read(os.path.join(WEB_DIR, "console.js"))
        cls.guide_ids_in_html = re.findall(r'data-guide-id="([^"]+)"', cls.html)

    def test_data_guide_id_attributes_exist(self):
        self.assertGreater(len(self.guide_ids_in_html), 0)

    def test_no_data_guide_id_is_reused_on_multiple_elements(self):
        counts = {}
        for gid in self.guide_ids_in_html:
            counts[gid] = counts.get(gid, 0) + 1
        duplicates = {gid: n for gid, n in counts.items() if n > 1}
        self.assertEqual(duplicates, {}, f"중복 사용된 data-guide-id: {duplicates}")

    def test_target_resolution_uses_data_guide_id_attribute_selector(self):
        """CSS class나 번역된 화면 문구가 아니라 반드시
        [data-guide-id="..."] 속성 선택자로 대상을 찾는지 확인한다."""

        self.assertIn('querySelector(`[data-guide-id="${CSS.escape(guideId)}"]`)', self.js)

    def test_all_step_targets_referenced_in_manifest_exist_in_html(self):
        step_targets = set(re.findall(r'target:\s*"([^"]+)"', self.js))
        # "input-group" 액션은 target 대신 targets: [...] 배열을 쓴다.
        for group in re.findall(r'targets:\s*\[([^\]]*)\]', self.js):
            step_targets.update(re.findall(r'"([^"]+)"', group))
        missing = step_targets - set(self.guide_ids_in_html)
        self.assertEqual(missing, set(), f"HOMEZ_TOURS가 참조하지만 console.html에 없는 data-guide-id: {missing}")


class TourEngineSafetyInvariantsTestCase(unittest.TestCase):
    """가이드 엔진이 사용자를 대신해 클릭/저장/전송하지 않는다는
    핵심 안전 요구사항을 코드 구조로 강제하는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        js = _read(os.path.join(WEB_DIR, "console.js"))
        start = js.index("const TOUR_PROGRESS_KEY")
        # 주의: "설정 · 계정 및 보안"이라는 문구는 이 모듈 시작보다
        # 앞쪽(다른 주석)에도 한 번 더 나온다 — 반드시 start 이후
        # 위치에서만 찾아야 한다(안 그러면 end < start로 빈 문자열).
        end = js.index("설정 · 계정 및 보안", start)
        cls.tour_module = js[start:end]
        assert len(cls.tour_module) > 5000, "tour 모듈 경계 추출이 잘못되었습니다."

    def test_tour_module_never_calls_api_fetch(self):
        """가이드 엔진 코드 범위 안에서 apiFetch(실제 백엔드 호출)가
        단 한 번도 호출되지 않아야 한다 — 연습 모드가 구조적으로
        아무것도 저장하지 않음을 보장하는 핵심 불변조건."""

        self.assertNotIn("apiFetch(", self.tour_module)

    def test_tour_module_never_synthetically_clicks_the_target(self):
        """targetEl.click()이나 dispatchEvent로 사용자 클릭을 흉내내지
        않는다 — 실제 사용자 클릭 이벤트만 기다린다(addEventListener).
        """

        self.assertNotIn("targetEl.click(", self.tour_module)
        self.assertNotIn("target.click(", self.tour_module)
        self.assertNotIn(".dispatchEvent(new MouseEvent", self.tour_module)

    def test_click_step_wiring_uses_real_once_listener(self):
        self.assertIn('addEventListener("click", onClick, { once: true })', self.tour_module.replace("'", '"'))

    def test_no_dangerous_action_ids_appear_as_step_targets(self):
        """요구사항 9번 — 삭제/계정삭제/권한변경/비밀번호변경/결제/정산/
        초기화 관련 버튼 id를 어떤 course도 target으로 삼지 않는다."""

        dangerous_substrings = (
            "delete", "logout", "reset-progress", "danger", "revoke-all",
            "change-password", "deactivate",
        )
        step_targets = re.findall(r'target:\s*"([^"]+)"', self.tour_module)
        for target in step_targets:
            lowered = target.lower()
            for bad in dangerous_substrings:
                self.assertNotIn(
                    bad, lowered,
                    f"위험한 대상으로 보이는 target '{target}'이 tour 정의에 있습니다.",
                )

    def test_safety_warning_dialog_never_auto_confirms(self):
        """tourMaybeShowSafetyWarning은 항상 사용자의 실제 버튼 클릭을
        기다려야 하며, 자동으로 '실제 기능으로 계속'을 선택해서는
        안 된다."""

        start = self.tour_module.index("function tourMaybeShowSafetyWarning")
        end = self.tour_module.index("\n  }\n", start)
        body = self.tour_module[start:end]
        self.assertIn("tour-safety-practice-btn", body)
        self.assertIn("tour-safety-real-btn", body)
        self.assertIn("tour-safety-skip-btn", body)
        self.assertIn("tour-safety-exit-btn", body)

    def test_progress_storage_never_contains_token_or_password_fields(self):
        """진행 상태 저장 함수(tourSaveProgress)가 다루는 patch
        객체에는 토큰/비밀번호 관련 필드가 존재해서는 안 된다."""

        start = self.tour_module.index("function tourSaveProgress")
        end = self.tour_module.index("\n  }\n", start)
        body = self.tour_module[start:end]
        for forbidden in ("token", "password", "secret", "credential"):
            self.assertNotIn(forbidden, body.lower())

    def test_progress_is_namespaced_by_user_id_not_username(self):
        self.assertIn("function tourCurrentUserKey", self.tour_module)
        start = self.tour_module.index("function tourCurrentUserKey")
        end = self.tour_module.index("\n  }\n", start)
        body = self.tour_module[start:end]
        self.assertIn("user.id", body)
        self.assertNotIn("user.username", body)
        self.assertNotIn("user.token", body)

    def test_mask_click_never_advances_the_tour(self):
        """가려진 배경(마스크) 클릭은 tourAdvance를 호출하지 않아야
        한다 — 강조된 요소 밖 클릭은 진행되지 않아야 한다는 요구사항."""

        start = self.tour_module.index("[data-tour-mask]")
        end = self.tour_module.index("});", start) + 3
        body = self.tour_module[start:end]
        self.assertNotIn("tourAdvance(", body)

    def test_resize_repositions_without_advancing(self):
        self.assertIn("tourRepositionForCurrentStep", self.tour_module)
        start = self.tour_module.index('window.addEventListener("resize"')
        end = self.tour_module.index("});", start) + 3
        body = self.tour_module[start:end]
        self.assertNotIn("tourAdvance(", body)


class TourProgressAndControlFunctionsExistTestCase(unittest.TestCase):
    """뒤로/다음/건너뛰기/종료/재개/초기화 등 필수 제어 함수가 실제로
    정의돼 있는지 확인한다(요구사항 4/5/8/15)."""

    @classmethod
    def setUpClass(cls):
        cls.js = _read(os.path.join(WEB_DIR, "console.js"))

    def test_required_functions_are_defined(self):
        for fn in (
            "function startTour(", "function tourAdvance(", "function tourGoBack(",
            "function tourSkipCurrentStep(", "function tourOpenExitConfirm(",
            "function tourExitNow(", "function tourShowSummary(",
            "function tourGetProgress(", "function tourSaveProgress(",
            "function tourResetProgress(", "function tourResetAllProgress(",
            "function tourHasAnyProgress(", "function tourMostRecentInProgress(",
            "function tourFindTargetAndPosition(", "function tourResolveTarget(",
            "function tourShowTargetNotFound(", "function tourHandleKeydown(",
            "function tourRepositionForCurrentStep(",
        ):
            self.assertIn(fn, self.js, f"{fn}이 console.js에 없습니다.")

    def test_target_not_found_has_a_bounded_retry_timeout(self):
        self.assertIn("TOUR_TARGET_RETRY_TIMEOUT_MS", self.js)
        m = re.search(r"TOUR_TARGET_RETRY_TIMEOUT_MS\s*=\s*(\d+)", self.js)
        self.assertIsNotNone(m)
        timeout_ms = int(m.group(1))
        self.assertGreater(timeout_ms, 0)
        self.assertLess(timeout_ms, 60000, "재시도 타임아웃이 비정상적으로 길면 사실상 무한대기와 같습니다.")

    def test_escape_key_opens_exit_confirm_not_immediate_exit(self):
        start = self.js.index("function tourHandleKeydown")
        end = self.js.index("\n  }\n", start)
        body = self.js[start:end]
        self.assertIn("Escape", body)
        self.assertIn("tourOpenExitConfirm", body)

    def test_reduced_motion_preference_is_checked(self):
        self.assertIn("function tourPrefersReducedMotion", self.js)
        self.assertIn("prefers-reduced-motion", self.js)

    def test_mobile_viewport_uses_bottom_sheet_class(self):
        start = self.js.index("function tourPositionTooltip(")
        end = self.js.index("\n  }\n", start)
        # tourPositionTooltip 함수 시작부에서 모바일 분기 로직까지
        # 포함하도록 충분히 넓게 자른다(중첩 return 때문에 첫 "\n  }\n"
        # 는 모바일 분기 안에서 끝날 수 있음 — 그래서 별도로 넉넉히
        # 슬라이스 후 검사).
        body = self.js[start:start + 1200]
        self.assertIn("tourIsMobileViewport()", body)
        self.assertIn("tour-tooltip-sheet", body)


class TourCssBottomSheetTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.css = _read(os.path.join(WEB_DIR, "console.css"))

    def test_mobile_media_query_defines_bottom_sheet_layout(self):
        """console.css에는 640px 이하 @media 블록이 두 개 있다(공통
        레이아웃용 1개 + 가이드 모드 전용 1개) — 반드시
        .tour-tooltip-sheet를 담고 있는 두 번째 블록을 기준으로
        검사해야 한다."""

        start = self.css.index(".tour-tooltip-sheet")
        block_start = self.css.rindex("@media (max-width: 640px)", 0, start)
        block = self.css[block_start:start + 400]
        self.assertIn(".tour-tooltip-sheet", block)
        self.assertIn("bottom: 0", block)

    def test_hidden_attribute_specificity_bugfix_present(self):
        """이전 버그(연습 배지가 hidden이어도 계속 보이던 CSS
        specificity 문제)가 재발하지 않도록 [hidden] 규칙이 존재하는지
        고정한다."""

        self.assertIn(".tour-practice-badge[hidden]", self.css)


class TourI18nKeySymmetryTestCase(unittest.TestCase):
    """tour.*/settings.guide_mode* 키가 ko-KR/en-US 양쪽에 모두
    존재하고, console.js가 참조하는 모든 tour 관련 키가 실제
    카탈로그에 있는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.ko = _load_js_object_literal(
            os.path.join(I18N_DIR, "ko-KR.js"), "HOMEZ_I18N_CATALOG_KO_KR",
        )
        cls.en = _load_js_object_literal(
            os.path.join(I18N_DIR, "en-US.js"), "HOMEZ_I18N_CATALOG_EN_US",
        )
        cls.js = _read(os.path.join(WEB_DIR, "console.js"))
        cls.html = _read(os.path.join(WEB_DIR, "console.html"))

    def test_tour_and_guide_mode_keys_exist(self):
        tour_keys = {k for k in self.ko if k.startswith("tour.")}
        self.assertGreater(len(tour_keys), 20, "tour.* 키가 예상보다 너무 적습니다.")
        self.assertIn("settings.guide_mode", self.ko)
        self.assertIn("settings.guide_mode_description", self.ko)

    def test_tour_keys_are_symmetric_between_locales(self):
        ko_tour_keys = {k for k in self.ko if k.startswith(("tour.", "settings.guide_mode"))}
        en_tour_keys = {k for k in self.en if k.startswith(("tour.", "settings.guide_mode"))}
        self.assertEqual(ko_tour_keys - en_tour_keys, set())
        self.assertEqual(en_tour_keys - ko_tour_keys, set())

    def test_all_referenced_tour_keys_resolve_in_catalog(self):
        """console.js/html이 실제로 참조하는 tour.*/settings.guide_mode*
        키가 전부 카탈로그에 존재하는지(누락 키 노출 방지)."""

        key_literal = re.compile(r'"(tour\.[a-zA-Z0-9_.]+|settings\.guide_mode[a-zA-Z0-9_.]*)"')
        referenced = set(key_literal.findall(self.js)) | set(key_literal.findall(self.html))
        missing = referenced - set(self.ko)
        self.assertEqual(missing, set(), f"카탈로그에 없는 참조 키: {missing}")

    def test_no_hardcoded_korean_inside_tour_manifest_titles(self):
        """요구사항 12 — 가이드 정의 파일(HOMEZ_TOURS) 자체에는 표시
        문구를 하드코딩하지 않고 titleKey/descriptionKey만 사용해야
        한다."""

        start = self.js.index("const HOMEZ_TOURS = ")
        array_literal = _extract_bracket_block(self.js, "const HOMEZ_TOURS = ")
        # titleKey/descriptionKey/actionHintKey 값은 전부 "tour."로
        # 시작하는 키여야 하며, 한글 표시 문구 자체가 아니어야 한다.
        for m in re.finditer(r'(titleKey|descriptionKey|actionHintKey):\s*"([^"]+)"', array_literal):
            field, value = m.groups()
            self.assertTrue(
                value.startswith("tour."),
                f"{field}='{value}'가 실제 번역 키가 아닌 것으로 보입니다.",
            )
            self.assertFalse(
                re.search(r"[가-힣]", value),
                f"{field}='{value}'에 한글이 하드코딩된 것으로 보입니다.",
            )


class TourEntryPointsTestCase(unittest.TestCase):
    """설정 화면과 가이드 메뉴 양쪽에 진입점이 있는지 확인한다
    (요구사항 2 — 설정은 관리자 전용이므로 이미 항상 보이는 '가이드'
    메뉴에도 별도 진입점이 필요하다는 요구사항)."""

    @classmethod
    def setUpClass(cls):
        cls.html = _read(os.path.join(WEB_DIR, "console.html"))

    def test_settings_view_has_guide_mode_panel(self):
        start = self.html.index('id="view-account-security"')
        end = self.html.index("</section>", start)
        section = self.html[start:end]
        self.assertIn('id="guide-mode-settings-actions"', section)
        self.assertIn('data-i18n="settings.guide_mode"', section)

    def test_guides_view_has_secondary_entry_button(self):
        start = self.html.index('id="view-guides"')
        end = self.html.index("</section>", start)
        section = self.html[start:end]
        self.assertIn('id="guide-mode-launch-from-guides-btn"', section)

    def test_course_select_dialog_exists(self):
        self.assertIn('id="tour-select-dialog"', self.html)
        self.assertIn('id="tour-select-list"', self.html)

    def test_overlay_and_tooltip_control_buttons_exist(self):
        for control_id in (
            "tour-prev-btn", "tour-next-btn", "tour-skip-btn",
            "tour-exit-btn", "tour-review-btn",
        ):
            self.assertIn(f'id="{control_id}"', self.html)


class TourAccessibilityTestCase(unittest.TestCase):
    """요구사항 13 — 스크린 리더가 단계 제목/설명을 실제로 전달받는지,
    포커스 트랩·Escape·모션 감소 설정이 코드에 존재하는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.html = _read(os.path.join(WEB_DIR, "console.html"))
        cls.js = _read(os.path.join(WEB_DIR, "console.js"))

    def test_tooltip_has_dialog_role_and_labelling(self):
        start = self.html.index('id="tour-tooltip"')
        end = self.html.index(">", start)
        tag = self.html[start:end]
        self.assertIn('role="dialog"', tag)
        self.assertIn('aria-labelledby="tour-step-title"', tag)
        self.assertIn('aria-describedby="tour-step-description"', tag)

    def test_tooltip_is_programmatically_focusable(self):
        """2026-08-13 결함 수정 — 이전에는 단계가 바뀌어도 포커스가
        전혀 이동하지 않아 스크린 리더가 새 제목/설명을 자동으로
        읽어주지 않았다(진행률 배지의 aria-live만 갱신됨)."""

        start = self.html.index('id="tour-tooltip"')
        end = self.html.index(">", start)
        tag = self.html[start:end]
        self.assertIn('tabindex="-1"', tag)

        start_fn = self.js.index("async function tourRenderCurrentStep")
        end_fn = self.js.index("\n  }\n", start_fn)
        body = self.js[start_fn:end_fn]
        self.assertIn('el("tour-tooltip").focus(', body)

    def test_step_progress_badge_has_aria_live(self):
        start = self.html.index('id="tour-step-progress"')
        end = self.html.index(">", start)
        self.assertIn("aria-live=", self.html[start:end])

    def test_focus_trap_implemented_for_tab_key(self):
        start = self.js.index("function tourHandleKeydown")
        end = self.js.index("\n  }\n", start)
        body = self.js[start:end]
        self.assertIn('"Tab"', body)
        self.assertIn("focusable", body)


class NodeDiagnosticsTestCase(unittest.TestCase):
    """66차 — Node 실행 실패 시 원인이 보존되는지(판정은 완화되지 않는지) 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls.node_path = shutil.which("node")
        if cls.node_path is None:
            raise unittest.SkipTest("Node.js를 찾을 수 없어 진단 검증을 건너뜁니다.")

    def _failure_report(self, args, **kwargs):
        with self.assertRaises(AssertionError) as caught:
            _run_node(self.node_path, args, label="진단 시험", **kwargs)
        return str(caught.exception)

    def test_nonzero_exit_is_still_a_failure_and_keeps_code_stdout_stderr_and_context(self):
        message = self._failure_report([
            "-e", "process.stdout.write('OUT-MARK'); process.stderr.write('ERR-MARK'); process.exit(7)",
        ])
        report = json.loads(message[message.index("{"):])
        self.assertEqual(report["returncode"], 7)
        self.assertEqual(report["returncode_hex"], "0x00000007")
        self.assertEqual(report["stdout"], "OUT-MARK")
        self.assertEqual(report["stderr"], "ERR-MARK")
        self.assertFalse(report["timed_out"])
        self.assertEqual(report["label"], "진단 시험")
        self.assertTrue(report["node_exists"])
        self.assertIn("cwd", report)
        self.assertIn("python_threads", report["resources"])
        if sys.platform == "win32":
            # 자원 수집 실패가 조용히 빈 값으로 남지 않아야 한다(핸들 수·가용 메모리가 실제 값)
            self.assertNotIn("snapshot_error", report["resources"])
            self.assertGreater(report["resources"]["process_handle_count"], 0)
            self.assertGreater(report["resources"]["avail_phys_mb"], 0)
        self.assertIn("SystemRoot_set", report["environment"])
        # 같은 시점의 Node 단순 실행 탐침(전역 문제와 스크립트별 문제를 가른다)
        self.assertEqual(report["probe_node_trivial"]["returncode"], 0)
        self.assertEqual(report["probe_node_trivial"]["stdout"], "ok")
        self.assertEqual(report["probe_node_version"]["returncode"], 0)

    def test_empty_stderr_failure_is_distinguishable_from_a_timeout(self):
        silent = json.loads(
            (lambda m: m[m.index("{"):])(self._failure_report(["-e", "process.exit(3)"])))
        self.assertEqual((silent["returncode"], silent["stderr"], silent["timed_out"]), (3, "", False))
        slow = self._failure_report(["-e", "setTimeout(() => {}, 20000)"], timeout=1)
        self.assertIn("시간초과", slow)
        report = json.loads(slow[slow.index("{"):])
        self.assertTrue(report["timed_out"])
        self.assertIsNone(report["returncode"])

    def test_missing_executable_is_reported_as_a_spawn_failure(self):
        with self.assertRaises(AssertionError) as caught:
            _run_node(os.path.join(tempfile.gettempdir(), "no-such-node.exe"), ["-e", "1"],
                      label="없는 실행 파일")
        self.assertIn("실행 시작 실패", str(caught.exception))
        self.assertIn("spawn_error", str(caught.exception))

    def test_success_is_returned_and_optionally_logged_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "node_diag.jsonl")
            previous = os.environ.get("HOMEZ_NODE_DIAG_LOG")
            os.environ["HOMEZ_NODE_DIAG_LOG"] = log_path
            try:
                result = _run_node(self.node_path, ["-e", "process.stdout.write('{}')"], label="성공 호출")
            finally:
                if previous is None:
                    os.environ.pop("HOMEZ_NODE_DIAG_LOG", None)
                else:
                    os.environ["HOMEZ_NODE_DIAG_LOG"] = previous
            self.assertEqual((result.returncode, result.stdout), (0, "{}"))
            entry = json.loads(open(log_path, encoding="utf-8").read().splitlines()[-1])
            self.assertEqual((entry["label"], entry["returncode"]), ("성공 호출", 0))
            self.assertIn("python_threads", entry["resources"])
            for forbidden in ("access_key", "secret", "password", "token"):
                self.assertNotIn(forbidden, json.dumps(entry).lower())


if __name__ == "__main__":
    unittest.main()
