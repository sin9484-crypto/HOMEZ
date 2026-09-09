"""
=========================================================
Homez OS

File : tests/test_listing_wizard_ui.py

Gate I/J(2026-08-08) — 상품등록 통합 마법사 Desktop UI 정적 검증.
다른 *_ui.py 테스트 파일과 동일하게 console.html/console.js 소스
텍스트만 확인한다 — 실제 서버/브라우저를 띄우지 않는다.
=========================================================
"""

import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ListingWizardHtmlTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_nav_item_and_view_container_exist(self):

        self.assertIn('data-view="listing-wizard"', self.html)
        self.assertIn('id="view-listing-wizard"', self.html)

    def test_wizard_panel_has_progress_step_indicator_and_nav_buttons(self):

        for element_id in (
            "lw-new-btn", "lw-list-table-wrap", "lw-wizard-panel",
            "lw-progress-fill", "lw-step-indicator", "lw-status-pill",
            "lw-save-status", "lw-step-content", "lw-prev-btn",
            "lw-next-btn", "lw-close-btn",
        ):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_csv_export_button_exists(self):
        """
        Gate X-3(2026-08-12) — 백엔드 `/listing-wizards/export.csv`는
        Gate U-3에서 이미 구현됐으나 버튼이 없어 도달 불가능한 죽은
        경로였다(Gate X-1 발견). 목록 화면에 실제 버튼이 있어야 한다.
        """

        self.assertIn('id="lw-csv-btn"', self.html)


class ListingWizardJsTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_syntax_is_valid_when_node_available(self):

        node_path = shutil.which("node")
        if node_path is None:
            self.skipTest("Node.js를 찾을 수 없어 문법 검사를 건너뜁니다.")

        js_path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        result = subprocess.run(
            [node_path, "--check", js_path],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_view_loader_registered(self):

        self.assertIn('"listing-wizard": loadListingWizardView', self.js)

    def test_all_ten_step_render_functions_exist(self):

        for name in (
            "lwRenderSourceStep", "lwRenderDraftStep", "lwRenderMediaStep",
            "lwRenderChannelsStep", "lwRenderFulfillmentStep",
            "lwRenderEconomicsStep", "lwRenderPrecheckStep",
            "lwRenderApprovalStep", "lwRenderExecutionStep",
            "lwRenderResultsStep",
        ):
            self.assertIn(f"function {name}", self.js, f"{name} 함수가 없습니다.")

    def test_fulfillment_step_collects_and_persists_channel_policy_inputs(self):
        """The policy check must not keep sending hard-coded empty inputs."""

        self.assertIn("function lwCollectChannelPolicyInput", self.js)
        self.assertIn("data-lw-category-recommend", self.js)
        self.assertIn("function lwRenderNoticeFields", self.js)
        self.assertIn("notice_required_field_keys", self.js)
        self.assertIn("channel_policy_attributes: policyInput.attributes", self.js)
        self.assertIn(
            "channel_policy_confirmed_evidence_rule_codes: policyInput.confirmedRules",
            self.js,
        )
        self.assertNotIn("product_attributes: {},", self.js)
        self.assertNotIn("confirmed_evidence_rule_codes: [],", self.js)

    def test_policy_check_saves_then_uses_server_confirmed_evidence(self):
        """Policy evaluation must not race ahead of confirmation persistence."""

        self.assertIn("const saved = await lwSaveCurrentStep({ silent: true });", self.js)
        self.assertIn("if (!saved) return;", self.js)
        self.assertIn("savedSelection.channel_policy_attributes || {}", self.js)
        self.assertIn(
            "savedSelection.channel_policy_confirmed_evidence_rule_codes || []",
            self.js,
        )

    def test_dynamic_notice_fields_participate_in_autosave(self):
        """Metadata-driven fields are added after the generic autosave wiring."""

        self.assertIn('checkbox.addEventListener("input", lwScheduleAutosave)', self.js)
        self.assertIn("refresh();\n        lwScheduleAutosave();", self.js)

    def test_unverified_fulfillment_mode_requires_explicit_admin_activation(self):
        self.assertIn('c.status === "VERIFIED"', self.js)
        self.assertIn("data-lw-capability-verify", self.js)
        self.assertIn("/marketplace-listings/capabilities/", self.js)

    def test_wizard_functions_never_reimplement_channel_or_account_listing(self):
        """
        Gate I 설계 원칙 — 이 화면 자신은 채널·계정·이미지 목록을 새로
        조회하는 로직을 만들지 않고 기존 엔드포인트를 그대로 재사용
        한다.
        """

        self.assertIn('"/marketplace-listings/channels"', self.js)
        self.assertIn("/media-assets/owners/PRODUCT_CANDIDATE/", self.js)

    def test_csv_export_wired_to_backend_endpoint(self):

        self.assertIn("function lwDownloadCsv()", self.js)
        self.assertIn("/listing-wizards/export.csv?locale=", self.js)
        self.assertIn('el("lw-csv-btn").addEventListener("click", lwDownloadCsv)', self.js)

    def test_handle_next_guards_against_duplicate_clicks(self):
        """
        Gate X-3(2026-08-12) — "다음" 버튼 연타로 같은 단계에 대해
        PATCH 요청이 중복으로 나가지 않도록, 요청 진행 중에는 재진입을
        막고 두 네비게이션 버튼을 비활성화한다.
        """

        start = self.js.index("async function lwHandleNext()")
        end = self.js.index("\n  }\n", start)
        body = self.js[start:end]

        self.assertIn("if (lwNextInFlight) return;", body)
        self.assertIn('el("lw-next-btn")', body)
        self.assertIn('el("lw-prev-btn")', body)
        self.assertIn(".disabled = true", body)


class ListingWizardGateJAutosaveTestCase(unittest.TestCase):
    """Gate J(2026-08-08) — 자동 저장·복구·중복 편집 방지."""

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_autosave_token_uses_session_storage_not_local_storage(self):
        """
        탭마다 다른 토큰이어야 "다른 탭에서 편집 중"을 구분할 수 있다
        — localStorage(탭 간 공유)가 아니라 sessionStorage(탭별 격리)를
        써야 한다.
        """

        start = self.js.index("function lwGetAutosaveToken()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("sessionStorage.getItem", body)
        self.assertIn("sessionStorage.setItem", body)
        self.assertNotIn("localStorage.getItem(KEY)", body)

    def test_debounce_does_not_save_on_every_keystroke(self):
        """
        입력마다 서버를 두드리지 않는다 — setTimeout으로 마지막 입력
        후 일정 시간 뒤에만 저장을 시도한다(Gate H의 카운트다운
        1초 tick과 동일한 "매 이벤트마다 서버를 부르지 않는다"는
        원칙을 자동 저장에도 그대로 적용).
        """

        start = self.js.index("function lwScheduleAutosave()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("setTimeout(", body)
        self.assertIn("lwClearAutosaveTimer()", body)

    def test_autosave_timer_is_cleared_on_every_step_transition(self):
        """
        단계를 벗어나면(이전/다음/점프) 그 단계에 걸려 있던 자동 저장
        타이머를 반드시 취소한다 — 그러지 않으면 이미 사라진 DOM을
        참조하는 콜백이 나중에 실행돼 엉뚱한 저장을 시도할 수 있다.
        """

        start = self.js.index("async function lwRenderStep(opts = {})")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("lwClearAutosaveTimer()", body)

    def test_version_conflict_is_detected_by_structured_error_code(self):

        start = self.js.index("function lwIsVersionConflict(err)")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("WIZARD_VERSION_CONFLICT", body)

    def test_conflict_handler_reloads_instead_of_silently_merging(self):
        """
        충돌 시 임의로 병합하지 않는다 — 서버 최신 상태를 다시 불러와
        사용자가 직접 확인하게 한다(조용한 데이터 유실 방지).
        """

        start = self.js.index("async function lwHandleConflict(err)")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("lwOpenWizard(lwState.wizard.id)", body)
        self.assertIn("lw.conflict_reloaded_toast", body)

    def test_all_six_patch_steps_send_autosave_client_token(self):

        for endpoint in ("source", "draft", "media", "channels", "fulfillment", "economics"):
            self.assertIn(
                "autosave_client_token: lwGetAutosaveToken()", self.js,
                f"{endpoint} 단계 저장 요청에 autosave_client_token이 없습니다.",
            )

    def test_all_six_patch_steps_route_conflicts_through_shared_handler(self):

        count = self.js.count("lwApplyConflictOrError(err, errEl)")
        # 6개 PATCH 단계 + approve + submit = 8곳.
        self.assertGreaterEqual(count, 8)

    def test_recovery_banner_shown_once_on_open_not_every_render(self):

        start = self.js.index("async function lwOpenWizard(id)")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("hadPriorAutosave", body)
        self.assertIn("lw.recovery_banner", body)

    def test_economics_step_advances_on_manual_next_not_stuck(self):
        """
        회귀 방지: 자동 저장(silent)일 때만 같은 단계에 머무르고, 수동
        "다음" 클릭 시에는 반드시 다음 단계로 넘어가야 한다(이전 구현은
        경제성 저장 후 항상 같은 단계를 다시 그려 "다음"이 영원히
        진행되지 않는 결함이 있었다).
        """

        start = self.js.index("function lwRenderEconomicsStep(content)")
        end = self.js.index("\n  }\n\n  // ---- 7단계", start)
        body = self.js[start:end]
        self.assertIn("if (opts.silent) {", body)
        self.assertIn("lwAdvanceAfterSave(fresh);", body)

    def test_precheck_step_advances_past_already_approved_wizard(self):
        """
        회귀 방지(Gate K, 2026-08-08 실제 Migration 리허설 중 실제
        브라우저에서 발견) — 사전검사 단계의 "다음"이 오직
        status === "READY_FOR_APPROVAL"일 때만 진행을 허용했다.
        승인(APPROVED)까지 마친 위저드를 다시 열면 서버의 current_step이
        여전히 PRECHECK로 남아 있는데(승인은 current_step을 갱신하지
        않음), validate()는 APPROVED 상태에서 재실행이 막혀 있어(서버측
        _assert_editable, 의도된 동작) "다음"을 눌러도 영원히 다음
        단계로 진행할 수 없는 막다른 길이었다. 사전검사를 이미 통과한
        뒤에만 도달 가능한 상태들(APPROVED 이후)도 함께 허용하도록
        수정했다.
        """

        start = self.js.index("async function lwRenderPrecheckStep(content)")
        end = self.js.index("\n  }\n\n  // ---- 8단계", start)
        body = self.js[start:end]
        self.assertIn("PRECHECK_ALREADY_PASSED_STATUSES", body)
        self.assertIn('"APPROVED"', body)
        self.assertIn('"SUBMITTING"', body)
        self.assertIn('"PARTIALLY_SUCCEEDED"', body)
        self.assertIn('"SUCCEEDED"', body)
        self.assertIn('"FAILED"', body)
        self.assertNotIn(
            'lwState.wizard.status !== "READY_FOR_APPROVAL"', body,
        )


class ListingWizardI18nWordingTestCase(unittest.TestCase):

    def test_gate_j_keys_present_in_both_locales(self):

        i18n_dir = os.path.join(REPO_ROOT, "app", "web", "i18n")
        with open(os.path.join(i18n_dir, "ko-KR.js"), encoding="utf-8") as f:
            ko = f.read()
        with open(os.path.join(i18n_dir, "en-US.js"), encoding="utf-8") as f:
            en = f.read()

        for key in (
            '"lw.autosave_saving"', '"lw.autosave_saved"',
            '"lw.recovery_banner"', '"lw.conflict_reloaded_toast"',
        ):
            self.assertIn(key, ko)
            self.assertIn(key, en)

    def test_gate_q1_revoke_keys_present_in_both_locales(self):

        i18n_dir = os.path.join(REPO_ROOT, "app", "web", "i18n")
        with open(os.path.join(i18n_dir, "ko-KR.js"), encoding="utf-8") as f:
            ko = f.read()
        with open(os.path.join(i18n_dir, "en-US.js"), encoding="utf-8") as f:
            en = f.read()

        for key in (
            '"lw.revoke_load_preview_btn"', '"lw.revoke_impact_title"',
            '"lw.revoke_impact_1"', '"lw.revoke_impact_2"',
            '"lw.revoke_impact_3"', '"lw.revoke_reason_label"',
            '"lw.revoke_confirm_btn"', '"lw.revoke_success_toast"',
            '"lw.revoke_error_reason_required"',
            '"lw.approval_locked_banner"', '"lw.approval_history_title"',
        ):
            self.assertIn(key, ko)
            self.assertIn(key, en)


class ListingWizardGateQ1RevokeApprovalTestCase(unittest.TestCase):
    """
    Gate Q-1(2026-08-09) — "승인 취소 후 수정" 화면 배선의 정적 검증.
    실제 상태 전이·서버 응답 로직은 tests/test_listing_wizard_service.py
    가 전담하므로, 여기서는 화면이 그 계약(APPROVED에서만 취소 노출,
    SUBMITTING 이후 잠금, recent-auth+nonce+사유 필수)을 그대로
    반영하는지만 확인한다.
    """

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()
        # Gate R-1(2026-08-09) — 승인/취소 미리보기 렌더링이
        # lwRenderRevokePreviewPanel/lwRenderApprovalPreviewPanel로
        # 분리됐다(언어 전환 시 nonce를 다시 발급하지 않고 캐시된
        # 데이터로 재사용하기 위함) — 그 두 함수부터 lwRenderApprovalStep
        # 끝까지를 함께 검증 범위로 잡는다.
        start = cls.js.index("function lwRenderRevokePreviewPanel(panel, preview)")
        end = cls.js.index("\n  // ---- 9단계", start)
        cls.body = cls.js[start:end]

    def test_revoke_flow_only_reachable_when_status_approved(self):

        self.assertIn('status === "APPROVED"', self.body)
        self.assertIn("/revoke-approval-preview", self.body)
        self.assertIn("/revoke-approval`", self.body)

    def test_locked_statuses_block_both_approve_and_revoke(self):

        self.assertIn("LW_REVOKE_LOCKED_STATUSES", self.js)
        for status in (
            '"SUBMITTING"', '"PARTIALLY_SUCCEEDED"',
            '"SUCCEEDED"', '"FAILED"',
        ):
            self.assertIn(status, self.js[
                self.js.index("LW_REVOKE_LOCKED_STATUSES"):
                self.js.index("LW_REVOKE_LOCKED_STATUSES") + 300
            ])

    def test_revoke_requires_reason_and_password_before_confirm(self):

        self.assertIn("lw-revoke-reason", self.body)
        self.assertIn("lw-revoke-password", self.body)
        self.assertIn("confirmBtn.disabled", self.body)
        self.assertIn("X-Recent-Auth-Token", self.body)
        self.assertIn("revoke_nonce: preview.revoke_nonce", self.body)

    def test_approval_history_rendered_on_approval_step(self):

        self.assertIn("lwRenderApprovalHistory", self.body)
        self.assertIn("function lwRenderApprovalHistory", self.js)


class ListingWizardGateR1LocaleRerenderTestCase(unittest.TestCase):
    """
    Gate R-1(2026-08-09) — 언어 전환 시 열려 있는 Wizard 단계를 즉시
    다시 그리는 계약의 정적 검증. 실제 DOM 조작 결과(입력값 보존 등)는
    Browser E2E가 전담하므로, 여기서는 소스 코드가 그 계약을 실제로
    구현하고 있는지만 확인한다.
    """

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_locale_changed_listener_registered_exactly_once(self):
        """요구사항 10 — 중복 등록 방지."""

        self.assertEqual(
            self.js.count(
                'document.addEventListener("homez:locale-changed", lwHandleLocaleChange)',
            ),
            1,
        )

    def test_locale_handler_scoped_to_listing_wizard_view_with_open_wizard(self):

        start = self.js.index("async function lwHandleLocaleChange()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn('currentView !== "listing-wizard"', body)
        self.assertIn("!lwState.wizard", body)

    def test_locale_handler_preserves_ephemeral_state_and_input_values(self):
        """요구사항 1·2·3·4 — current_step 유지(lwRenderStep을 그대로
        재사용해 lwState.viewingStep을 건드리지 않음), 입력값 스냅샷/
        복원, 서버 값으로 덮어쓰지 않음(preserveEphemeral)."""

        start = self.js.index("async function lwHandleLocaleChange()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("lwSnapshotStepInputValues()", body)
        self.assertIn("lwRenderStep({ preserveEphemeral: true })", body)
        self.assertIn("lwRestoreStepInputValues(snapshot)", body)

    def test_input_restore_sets_value_without_dispatching_input_event(self):
        """요구사항 5 — 자동 저장 debounce를 강제로 재실행하지 않는다
        (input 이벤트를 직접 dispatch하지 않고 .value/.checked만
        대입)."""

        start = self.js.index("function lwRestoreStepInputValues(snapshot)")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("elm.checked = saved.checked", body)
        self.assertIn("elm.value = saved.value", body)
        self.assertNotIn("dispatchEvent", body)


    def test_approval_preview_reuses_cache_instead_of_refetching(self):
        """요구사항 6 — 승인 fingerprint·nonce를 다시 만들지 않는다.
        캐시가 있으면 lwRenderApprovalPreviewPanel/
        lwRenderRevokePreviewPanel을 캐시 데이터로 즉시 호출하고,
        서버 재호출(apiFetch)은 오직 버튼 클릭 핸들러 안에서만
        일어난다."""

        start = self.js.index("function lwRenderApprovalStep(content)")
        end = self.js.index("\n  // ---- 9단계", start)
        body = self.js[start:end]
        self.assertIn(
            'lwStepEphemeralCache.kind === "approval_preview"', body,
        )
        self.assertIn(
            'lwStepEphemeralCache.kind === "revoke_preview"', body,
        )
        self.assertIn("lwRenderApprovalPreviewPanel(el(\"lw-approval-preview-panel\"), lwStepEphemeralCache.data)", body)
        self.assertIn("lwRenderRevokePreviewPanel(el(\"lw-revoke-panel\"), lwStepEphemeralCache.data)", body)

    def test_precheck_result_cached_and_replayed_without_refetch(self):
        """요구사항 9 — validation summary도 즉시 변경(사라지지 않고
        같은 결과를 새 언어로 다시 보여준다). 서버 재호출 없이 캐시된
        결과로 lwRenderPrecheckResult를 즉시 호출한다."""

        start = self.js.index("async function lwRenderPrecheckStep(content)")
        end = self.js.index("\n  }\n\n  // ---- 8단계", start)
        body = self.js[start:end]
        self.assertIn('lwStepEphemeralCache.step === "PRECHECK"', body)
        self.assertIn(
            "lwRenderPrecheckResult(el(\"lw-precheck-result\"), lwStepEphemeralCache.data)",
            body,
        )

    def test_ephemeral_cache_cleared_on_real_step_transitions_only(self):
        """실제 단계 전환(이전/다음/점프)에서는 캐시를 비우고, 언어
        전환 재렌더에서만 유지한다."""

        start = self.js.index("async function lwRenderStep(opts = {})")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("if (!opts.preserveEphemeral) lwStepEphemeralCache = null;", body)

    def test_mobile_overflow_guard_present_for_wizard_step_content(self):
        """요구사항 13 — 360px에서 긴 en-US 문구가 가로로 넘치지
        않아야 한다."""

        path = os.path.join(REPO_ROOT, "app", "web", "console.css")
        with open(path, encoding="utf-8") as f:
            css = f.read()
        self.assertIn("#lw-step-content", css)
        self.assertIn("overflow-wrap: break-word", css)


class ListingWizardJsonEditorAutosaveTestCase(unittest.TestCase):
    """5단계 긴 JSON 편집 중 autosave가 DOM을 교체하지 않는 계약."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_json_editors_autosave_only_after_change(self):
        self.assertIn(
            'class="lw-required-fields" rows="4" data-lw-autosave-on-change',
            self.js,
        )
        wire_start = self.js.index("function lwWireAutosaveInputs(content)")
        wire_end = self.js.index("\n  }", wire_start)
        wire_body = self.js[wire_start:wire_end]
        self.assertIn('[data-lw-autosave-on-change]', wire_body)
        self.assertIn('? "change"', wire_body)

    def test_structured_purchase_option_fields_wire_their_own_autosave(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결(Section 2A) — 구매옵션은
        # 더 이상 원시 JSON textarea(`lw-policy-purchase-options`)가
        # 아니다. Category Metadata 기준 구조화된 SELECT/INPUT으로
        # 대체됐으므로(`lwRenderPurchaseOptionFields`), "긴 JSON
        # 편집 중간에 autosave가 끼어들지 않는다"는 이 클래스의 계약이
        # 더 이상 그 필드에는 적용되지 않는다(짧은 개별 값이라 mid-edit
        # 손상 위험이 없다) — 그 필드가 저장 자체는 여전히 트리거하는지
        # (다른 방식으로) 확인해 커버리지를 대체한다.
        fn_start = self.js.index("function lwRenderPurchaseOptionFields(block")
        fn_end = self.js.index("\n  }", fn_start)
        fn_body = self.js[fn_start:fn_end]
        self.assertIn("data-lw-purchase-option-key", fn_body)
        self.assertIn("lwScheduleAutosave()", fn_body)


if __name__ == "__main__":
    unittest.main()
