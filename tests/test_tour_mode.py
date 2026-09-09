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
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(REPO_ROOT, "app", "web")
I18N_DIR = os.path.join(WEB_DIR, "i18n")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


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
            result = subprocess.run(
                [node_path, "-e", script, tmp_path],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            os.unlink(tmp_path)

        if result.returncode != 0:
            raise AssertionError(f"HOMEZ_TOURS 배열을 node로 평가하지 못했습니다: {result.stderr}")

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
            result = subprocess.run(
                [self.node_path, tmp_path], capture_output=True, text=True, timeout=30,
            )
        finally:
            os.unlink(tmp_path)
        if result.returncode != 0:
            raise AssertionError(f"Node 시나리오 실행 실패: {result.stderr}")
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


if __name__ == "__main__":
    unittest.main()
