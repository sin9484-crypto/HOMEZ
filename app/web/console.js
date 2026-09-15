/* =========================================================
   Homez OS — app/web/console.js
   HOMEZ V3 운영자 Console

   외부 CDN/라이브러리 의존 없음(순수 fetch + DOM API). 존재하지 않는
   API를 호출하지 않으며, Mock 데이터로 완료된 것처럼 표시하지 않는다.
   ========================================================= */

(() => {
  "use strict";

  const TOKEN_KEY = "homez_console_token";
  const USER_KEY = "homez_console_user";

  // --------------------------------------------------
  // 2026-08-14 Gate F-2 — Desktop 모드 로그인 세션 저장소.
  //
  // Desktop 앱은 재시작마다 완전히 무작위 포트로 뜨고(정책상 고정하지
  // 않는다, app/desktop/server.py::find_free_port() 참고) 그 결과
  // 브라우저 origin이 매번 바뀐다. origin에 종속된 localStorage에
  // 로그인 토큰을 두면 재시작마다 사라진다 — 그래서 Desktop 모드에서는
  // 토큰을 localStorage에 전혀 쓰지 않는다. 대신:
  //   1) 페이지가 살아있는 동안은 이 모듈 스코프의 in-memory 변수에만
  //      둔다(TOKEN_KEY 자체가 아니라 별도 변수 — 어떤 storage API도
  //      거치지 않는다).
  //   2) 재시작을 넘어 살아남아야 하는 부분은 pywebview 브리지로
  //      Python 프로세스의 Windows Credential Manager에 위임한다
  //      (app/core/desktop_console_session_store.py). 이 창 밖의
  //      일반 브라우저에는 window.pywebview 자체가 없어 이 경로를
  //      쓸 방법이 없다(app/core/desktop_auth.py와 동일한 경계).
  //
  // 일반 브라우저 fallback 런처(scripts/start_homez.cmd, 고정 포트라
  // 이 문제 자체가 없음)에서는 기존과 동일하게 localStorage(TOKEN_KEY)
  // 를 그대로 쓴다 — 이 파일 전체에서 "desktop 모드일 때만 분기"하는
  // 이유다.
  // --------------------------------------------------

  let isDesktopSessionBridgeActive = false;
  let inMemoryDesktopAccessToken = null;

  // 2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token은 Desktop
  // 모드든 일반 브라우저 fallback이든 절대 localStorage/sessionStorage
  // 에 두지 않는다(요구사항 원문 그대로) — 항상 이 모듈 스코프
  // in-memory 변수에만 두고, Desktop 재시작을 넘어 살아남아야 하는
  // 부분만 pywebview 브리지로 Windows Credential Manager에 위임한다
  // (access token과 동일한 경계).
  let inMemoryRefreshToken = null;
  let autoRefreshTimer = null;

  function getAccessToken() {
    if (isDesktopSessionBridgeActive) return inMemoryDesktopAccessToken;
    return localStorage.getItem(TOKEN_KEY);
  }

  function getRefreshToken() {
    return inMemoryRefreshToken;
  }

  // JWT는 서명 검증 없이 payload만 읽는다(클라이언트는 exp를 "언제
  // Credential Manager 항목을 정리할지"에만 참고용으로 쓸 뿐, 신뢰
  // 경계로 쓰지 않는다 — 실제 만료 판정은 항상 서버가 401로 결정한다).
  function decodeJwtExpEpoch(token) {
    try {
      const payloadPart = token.split(".")[1];
      const normalized = payloadPart.replace(/-/g, "+").replace(/_/g, "/");
      const json = JSON.parse(atob(normalized));
      return typeof json.exp === "number" ? json.exp : null;
    } catch (_) {
      return null;
    }
  }

  const el = (id) => document.getElementById(id);

  // --------------------------------------------------
  // Toast
  // --------------------------------------------------

  function toast(message, type = "info") {
    const region = el("toast-region");
    const node = document.createElement("div");
    node.className = "toast" + (type === "error" ? " toast-error" : type === "success" ? " toast-success" : "");
    node.textContent = message;
    region.appendChild(node);
    setTimeout(() => {
      node.remove();
    }, 4200);
  }

  // 2026-08-12 Gate X-3 — lsDownloadCsv/lwDownloadCsv/lwHandleNext에서
  // 반복되던 "요청 진행 중 버튼 비활성화" 패턴을 공용 헬퍼로 추출한다
  // (중복 제출 방지를 pause/resume·승인/거절/취소·재시도 같은 다른
  // 주요 쓰기 작업에도 동일하게 적용하기 위함). 버튼 라벨은 바꾸지
  // 않는다 — 매 호출부가 진행 중 문구를 알 필요 없게 disabled 상태만
  // 토글한다. 실패해도(catch 없이 그대로 던짐) finally에서 항상
  // 복구되므로, 실패 후 버튼/폼이 멈춰있는 상태가 남지 않는다.
  async function withButtonGuard(btn, fn) {
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    try {
      await fn();
    } finally {
      btn.disabled = false;
    }
  }

  // --------------------------------------------------
  // API 클라이언트
  // --------------------------------------------------

  class ApiError extends Error {
    constructor(status, detail) {
      super(detail || HomezI18n.t("common.error_request_failed", { status }));
      this.status = status;
      this.detail = detail;
    }
  }

  // --------------------------------------------------
  // 중앙 인증 상태 관리
  //
  // 2026-08-03 결함 수정: 이전에는 apiFetch()가 "어떤 API 호출이든
  // 401을 받으면" 무조건 토큰을 지우고 로그인 화면으로 전환했다 — 상단바
  // 상태 폴링(refreshTopbar, 20초 주기)을 포함해 화면 전환과 무관한
  // 배경 요청까지 전부 이 경로를 탔고, 서버가 반환한 401의 실제 원인
  // (진짜 세션 만료/폐기 vs. 그 외)을 전혀 구분하지 않았다. 또한 오래된
  // 요청의 지연 응답이 방금 성공한 새 로그인 상태를 되돌려버릴 수 있는
  // 경쟁 조건 방어도 없었다 — "프로그램 사용 중 이유 없이 로그인
  // 화면으로 전환되는" 증상의 핵심 원인이다.
  //
  // authGeneration은 로그인 성공/로그아웃/강제 전환마다 증가하는
  // 세대 번호다. apiFetch는 요청을 보내기 "직전"의 세대를 기억해두고,
  // 응답이 돌아왔을 때 세대가 이미 바뀌었다면(그사이 새 로그인이
  // 일어났거나 이미 로그아웃됐다면) 그 응답의 인증 실패 처리를
  // 완전히 무시한다 — 낡은 요청이 새 세션을 덮어쓰지 못하게 막는다.
  // --------------------------------------------------

  let authGeneration = 0;
  let topbarPollTimer = null;

  // 2026-08-05 CTO 재검증 지시 Gate E — Migration 승인 UX "제한 모드".
  // 사용자가 대기 중인 Migration 적용을 "나중에"로 미루면 true가 된다.
  // 서버 쪽 강제가 아니라 이 콘솔 클라이언트(apiFetch, 모든 쓰기 요청의
  // 유일한 통로)에서만 막는다는 한계를 최종 보고서에 명시해야 한다 —
  // 그래도 이 앱의 유일한 클라이언트가 콘솔이므로 실질적 효과는 있다.
  let homezLimitedMode = false;

  const LIMITED_MODE_ALLOWED_PATHS = new Set([
    "/auth/login",
    "/auth/logout",
    "/desktop-auth/bootstrap",
    "/desktop-setup/migration-status",
    "/desktop-setup/migration-status/approve",
  ]);

  const ALLOWED_LOGOUT_CODES = new Set([
    "SESSION_EXPIRED",
    "SESSION_REVOKED",
    "ACCOUNT_DISABLED",
    // 2026-09-09 Phase 2 — 마지막 사용 후 SESSION_TIMEOUT_MINUTES(기본
    // 3시간) 경과. 이 코드를 화이트리스트에 추가하지 않으면
    // handleAuthFailure가 "허용되지 않은 코드"로 판단해 로그아웃
    // 화면 전환 자체를 건너뛴다 — 서버는 정확히 거부했는데 화면만
    // 계속 붙어있는 상태가 된다.
    "SESSION_IDLE_TIMEOUT",
  ]);

  function stopTopbarPolling() {
    if (topbarPollTimer) {
      clearInterval(topbarPollTimer);
      topbarPollTimer = null;
    }
  }

  function clearSession() {
    if (autoRefreshTimer) {
      clearTimeout(autoRefreshTimer);
      autoRefreshTimer = null;
    }
    inMemoryRefreshToken = null;

    if (isDesktopSessionBridgeActive) {
      inMemoryDesktopAccessToken = null;
      // 이 함수는 apiFetch 내부의 동기 오류 처리 경로(handleAuthFailure)
      // 에서도 호출되므로 async로 바꾸지 않는다 — Credential Manager
      // 정리는 fire-and-forget으로 보낸다(실패해도 다음 save_console_
      // session()이 어차피 이전 값을 덮어쓰므로 안전).
      if (window.pywebview && window.pywebview.api
        && typeof window.pywebview.api.clear_console_session === "function") {
        window.pywebview.api.clear_console_session().catch(() => {});
      }
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
    localStorage.removeItem(USER_KEY);
  }

  // 실제로 서버가 세션 무효를 확인해준 경우에만 호출된다(허용된
  // 로그아웃 조건 — app/core/auth.py의 X-Auth-Error-Code 참고). 연결
  // 오류/일시적 오류에서는 이 함수 자체를 호출하지 않는다.
  function transitionToLogin(reason) {
    authGeneration += 1;
    stopTopbarPolling();
    clearSession();
    showLoginGate(reason || "");
  }

  // resp.status===401(또는 403의 ACCOUNT_DISABLED)에서만 호출된다.
  // requestGeneration은 이 요청을 "보내기 직전"의 authGeneration —
  // 응답을 받은 지금 세대가 이미 바뀌었다면 완전히 무시한다.
  function handleAuthFailure(resp, requestGeneration) {
    if (requestGeneration !== authGeneration) {
      return; // 낡은 요청의 지연 응답 — 최신 세션을 절대 건드리지 않는다.
    }

    const code = resp.headers.get("X-Auth-Error-Code");

    if (code && !ALLOWED_LOGOUT_CODES.has(code)) {
      return; // 알 수 없는/허용되지 않은 코드 — 안전 쪽으로 로그아웃하지 않는다.
    }

    // code가 없는 401은 FastAPI OAuth2PasswordBearer가 Authorization
    // 헤더 자체가 없을 때 자동으로 반환하는 경우뿐이다(실질적으로
    // "애초에 인증되지 않음") — 로그인 화면 전환이 안전하다.
    const reason = code === "SESSION_IDLE_TIMEOUT"
      ? HomezI18n.t("auth.session_idle_timeout")
      : HomezI18n.t("auth.session_expired");
    transitionToLogin(reason);
  }

  async function apiFetch(path, options = {}, { skipAuthHandling = false } = {}) {
    const method = (options.method || "GET").toUpperCase();
    if (
      homezLimitedMode
      && method !== "GET" && method !== "HEAD"
      && !LIMITED_MODE_ALLOWED_PATHS.has(path)
    ) {
      // status 0은 이 앱 전역에서 "네트워크 연결 끊김"의 고정 의미로
      // 이미 쓰이고 있다(여러 화면이 err.status===0을 그 문구로
      // 분기) — 여기서도 0을 쓰면 실제로는 제한 모드 때문인데 사용자가
      // "네트워크를 확인하라"는 엉뚱한 안내를 받는다(Browser E2E로
      // 실제 재현·확인함). 423(Locked)은 이 앱의 다른 어떤 실제 서버
      // 응답 코드와도 겹치지 않는 값이라 안전하게 구분된다.
      throw new ApiError(423, HomezI18n.t("ma.write_blocked_error"));
    }

    const requestGeneration = authGeneration;
    const token = getAccessToken();
    const headers = Object.assign({}, options.headers || {});

    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (options.body && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    let resp;
    try {
      resp = await fetch(path, Object.assign({}, options, { headers }));
    } catch (networkErr) {
      // 네트워크 실패는 인증 실패가 아니다 — 절대 로그아웃시키지 않는다
      // (배지/배너 갱신은 refreshTopbar의 /health 폴링이 20초 주기로
      // 담당한다 — 여기서 같은 배지를 중복 갱신하면 두 갱신 로직이
      // 서로 다른 문구로 깜빡이는 문제가 생긴다).
      throw new ApiError(0, HomezI18n.t("common.error_network_failed"));
    }

    if (resp.status === 401) {
      if (!skipAuthHandling) {
        handleAuthFailure(resp, requestGeneration);
      }
      throw new ApiError(401, HomezI18n.t("common.error_auth_required"));
    }

    if (
      resp.status === 403
      && !skipAuthHandling
      && resp.headers.get("X-Auth-Error-Code") === "ACCOUNT_DISABLED"
    ) {
      handleAuthFailure(resp, requestGeneration);
    }

    if (!resp.ok) {
      // Gate G(2026-08-07): 서버가 제한 모드로 423을 직접 반환한
      // 경우(이 클라이언트 자체 차단을 우회한 요청, 또는 이 클라이언트
      // 갱신 전 다른 프로세스가 승인을 완료해 상태가 아직 최신이
      // 아닌 경우 등) — 서버 detail은 한국어 고정이므로, 영어 UI에서도
      // 올바른 문구가 보이도록 헤더의 안정적 코드로 이 클라이언트의
      // 카탈로그에서 다시 찾는다(서버 문자열을 그대로 신뢰하지 않는다).
      if (
        resp.status === 423
        && resp.headers.get("X-Migration-Restricted-Code") === "MIGRATION_RESTRICTED_MODE"
      ) {
        throw new ApiError(423, HomezI18n.t("ma.write_blocked_error"));
      }

      let detail = HomezI18n.t("common.error_request_failed", { status: resp.status });
      try {
        const body = await resp.json();
        if (body && body.detail) {
          if (
            typeof body.detail === "object"
            && body.detail !== null
            && typeof body.detail.error_code === "string"
          ) {
            // Gate H(2026-08-07)에서 RATE_LIMITED 전용으로 시작한
            // 계약을 Gate J(2026-08-08)에서 일반화했다 — error_code가
            // 있는 구조화된 detail은 전부(RATE_LIMITED/
            // WIZARD_VERSION_CONFLICT 등) JSON.stringify로 뭉개지 않고
            // 그대로 보존해 던진다. 호출부가 각자 상황에 맞는 UI(카운트
            // 다운, 충돌 재조회 등)를 직접 렌더링할 수 있게 한다.
            throw new ApiError(resp.status, body.detail);
          }
          detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        }
      } catch (parseOrRateLimitErr) {
        if (parseOrRateLimitErr instanceof ApiError) {
          throw parseOrRateLimitErr;
        }
        /* ignore JSON parse failure — 위 detail 기본값을 그대로 쓴다 */
      }
      throw new ApiError(resp.status, detail);
    }

    if (resp.status === 204) {
      return null;
    }

    const contentType = resp.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return resp.json();
    }
    return resp.text();
  }

  // --------------------------------------------------
  // 인증
  // --------------------------------------------------

  // --------------------------------------------------
  // Desktop session token bootstrap
  //
  // pywebview 창 안에서만 window.pywebview.api.get_desktop_token()을
  // 호출할 수 있다(일반 브라우저에는 window.pywebview 자체가 없다).
  // 그 토큰을 요청 본문으로만 서버에 1회 전달해 HttpOnly 쿠키를
  // 발급받는다 — URL이나 localStorage에는 절대 두지 않는다.
  //
  // 2026-07-30 결함 수정: pywebview는 window.pywebview.api를 페이지
  // 로드와 "비동기로" 주입한다 — DOMContentLoaded 시점에는 아직 없을
  // 수 있고, 준비되면 window에 `pywebviewready` 이벤트를 쏜다. 이전
  // 코드는 이 이벤트를 기다리지 않고 즉시 window.pywebview 유무만
  // 확인해, 실제 네이티브 창에서는 타이밍에 따라 부트스트랩이 조용히
  // 스킵되고("일반 브라우저로 오인") 이후 Desktop token 쿠키가 없는
  // 채로 /desktop-setup/initialize를 호출해 "Desktop 인증이
  // 필요합니다"로 거부되는 결함이 있었다(실제 재현·확인됨). nonce는
  // 사용자가 비밀번호를 입력하는 동안 시간이 더 지나 우연히 브리지가
  // 준비돼 있어 정상 조회됐지만, 부트스트랩은 그보다 훨씬 이른
  // init() 시점에 1회만 시도돼 실패가 가려지지 않았다.
  // --------------------------------------------------

  function waitForPywebviewBridge(timeoutMs = 4000) {
    return new Promise((resolve) => {
      if (window.pywebview && window.pywebview.api) {
        resolve(true);
        return;
      }

      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        window.removeEventListener("pywebviewready", onReady);
        resolve(ok);
      };
      const onReady = () => finish(!!(window.pywebview && window.pywebview.api));

      window.addEventListener("pywebviewready", onReady);
      setTimeout(() => finish(!!(window.pywebview && window.pywebview.api)), timeoutMs);
    });
  }

  async function bootstrapDesktopTokenIfNeeded() {
    await waitForPywebviewBridge();

    if (!window.pywebview || !window.pywebview.api || typeof window.pywebview.api.get_desktop_token !== "function") {
      return; // 일반 브라우저 — Desktop 전용 방어 계층 대상이 아님
    }
    try {
      const token = await window.pywebview.api.get_desktop_token();
      if (!token) return;
      await fetch("/desktop-auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
    } catch (_) {
      /* Desktop 토큰 부트스트랩 실패는 조용히 무시한다 — 이 계층은
         "추가" 방어이므로 실패해도 로그인 자체를 막지 않는다. */
    }
  }

  async function getSetupNonceFromBridge() {
    await waitForPywebviewBridge();

    if (!window.pywebview || !window.pywebview.api || typeof window.pywebview.api.get_setup_nonce !== "function") {
      return null; // 일반 브라우저 — 최초 설정 화면 자체를 완료할 수 없음
    }
    try {
      return await window.pywebview.api.get_setup_nonce();
    } catch (_) {
      return null;
    }
  }

  // 2026-08-14 Gate F-2 — Desktop 모드 감지 + 재시작 이전 로그인 세션
  // 복원. bootstrapDesktopTokenIfNeeded()와 마찬가지로 pywebview 브리지가
  // 준비될 때까지 기다린다. 이 함수가 isDesktopSessionBridgeActive를
  // true로 설정하면, 이후 getAccessToken()/login()/logout()/
  // clearSession()이 전부 localStorage 대신 in-memory 변수 + Windows
  // Credential Manager 경로로 분기한다.
  async function restoreDesktopConsoleSessionIfNeeded() {
    await waitForPywebviewBridge();

    if (
      !window.pywebview || !window.pywebview.api
      || typeof window.pywebview.api.load_console_session !== "function"
    ) {
      return; // 일반 브라우저(scripts/start_homez.cmd 등) — localStorage 경로 유지
    }

    isDesktopSessionBridgeActive = true;

    try {
      const restored = await window.pywebview.api.load_console_session();
      if (restored && restored.access_token) {
        inMemoryDesktopAccessToken = restored.access_token;
        inMemoryRefreshToken = restored.refresh_token || null;
        scheduleAutoRefresh(restored.access_token);
      }
    } catch (_) {
      /* 복원 실패 — 로그인 화면으로 안전하게 진행한다(토큰 없음과 동일). */
    }
  }

  function getCurrentUser() {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function showLoginGate(message, prefillUsername) {
    el("shell").hidden = true;
    el("setup-gate").hidden = true;
    el("company-recovery-gate").hidden = true;
    el("account-recovery-gate").hidden = true;
    el("register-gate").hidden = true;
    el("login-gate").hidden = false;
    if (message) {
      el("login-error").textContent = message;
    }
    if (prefillUsername) {
      el("login-username").value = prefillUsername;
    }
    el("login-password").value = "";
    (prefillUsername ? el("login-password") : el("login-username")).focus();
  }

  function showSetupGate(configuredEmail) {
    el("shell").hidden = true;
    el("login-gate").hidden = true;
    el("company-recovery-gate").hidden = true;
    el("account-recovery-gate").hidden = true;
    el("register-gate").hidden = true;
    el("setup-gate").hidden = false;
    el("setup-email").value = configuredEmail || "";
    el("setup-company-name").focus();
  }

  function showCompanyRecoveryGate(adminEmail) {
    el("shell").hidden = true;
    el("login-gate").hidden = true;
    el("setup-gate").hidden = true;
    el("account-recovery-gate").hidden = true;
    el("register-gate").hidden = true;
    el("company-recovery-gate").hidden = false;
    el("company-recovery-admin-email").value = adminEmail || "";
    companyRecoveryConfiguredEmail = adminEmail || "";
    el("company-recovery-name").focus();
  }

  function showRegisterGate() {
    el("shell").hidden = true;
    el("login-gate").hidden = true;
    el("setup-gate").hidden = true;
    el("company-recovery-gate").hidden = true;
    el("account-recovery-gate").hidden = true;
    el("register-gate").hidden = false;
    el("register-email").focus();
  }

  // ADMIN/SUPER_ADMIN은 서버측 ListingWizardPermissionGuard가 코드
  // 레벨에서 무조건 통과시킨다(Gate Q-2/R-2) — 이 두 역할은 세부
  // Permission 시딩 여부와 무관하게 항상 접근 가능하다. 그 외 역할은
  // 로그인 응답에 포함된 명시적 permissions 배열(있는 경우)로만
  // 판단한다 — 실제 Permission이 아직 시딩되지 않은 동안에는(Gate
  // R-5 계획만 수립, 미실행) 코드가 임의로 켜진 것처럼 보이지 않게
  // 기본적으로 숨긴다. 클라이언트에서 메뉴를 숨기는 것과 별개로,
  // 서버는 이 값과 무관하게 항상 동일한 Permission 검사를 강제한다
  // (직접 URL·API 요청도 동일하게 403).
  // 2026-08-12 Gate X-3 — 이 값을 가진 메뉴는 세분화된 permissions
  // 배열과 무관하게 admin_guard(ADMIN/SUPER_ADMIN)로만 서버가 막혀
  // 있다는 뜻이다. 실제 코드 감사 결과(app/web/router.py 등) 이
  // 저장소의 listing_wizard.* 이외 거의 모든 API가 여기 해당한다 —
  // 그동안 nav-item에 빈 문자열(항상 표시)이 붙어 있어 Manager/Staff/
  // Viewer가 눌러도 결국 403만 뜨는 죽은 메뉴가 대부분이었다. 개별
  // Permission 코드를 흉내 내지 않고(그런 코드는 permission_catalog.py
  // 에 존재하지 않는다) 서버가 실제로 요구하는 역할 조건을 그대로
  // 코드화한다.
  const NAV_PERMISSION_ADMIN_ONLY = "__admin_only__";

  function applyPermissionGatedNav() {
    const user = getCurrentUser();
    const role = (user && user.role) || "";
    const isSuperRole = role === "ADMIN" || role === "SUPER_ADMIN";
    const grantedPermissions = new Set((user && Array.isArray(user.permissions) ? user.permissions : []));

    document.querySelectorAll(".nav-item[data-permission]").forEach((btn) => {
      const required = btn.dataset.permission;
      if (!required) return; // 빈 문자열 = Permission 요구 없음(항상 표시)
      const allowed = required === NAV_PERMISSION_ADMIN_ONLY
        ? isSuperRole
        : (isSuperRole || grantedPermissions.has(required));
      btn.hidden = !allowed;
    });
  }

  // 2026-08-12 Gate X-3 — 로그인 직후 기본 진입 화면(overview)이 현재
  // 역할에서 숨겨져 있으면(= admin_guard가 어차피 403을 낼 화면) 그
  // 대시보드를 억지로 띄워 오류만 보여주지 않는다. 화면에 실제로
  // 보이는 첫 nav-item으로 대신 이동한다 — 서버 판정과 화면 진입점을
  // 일치시킨다. 아무것도 보이지 않으면(이론상 발생하면 안 됨) 기존
  // 동작인 "overview"로 안전하게 폴백한다.
  function firstVisibleNavView() {
    const overviewBtn = document.querySelector('.nav-item[data-view="overview"]');
    if (overviewBtn && !overviewBtn.hidden) return "overview";

    const visible = Array.from(document.querySelectorAll(".nav-item[data-view]"))
      .find((btn) => !btn.hidden);
    return visible ? visible.dataset.view : "overview";
  }

  function showShell() {
    el("login-gate").hidden = true;
    el("account-recovery-gate").hidden = true;
    el("register-gate").hidden = true;
    el("shell").hidden = false;
    const user = getCurrentUser();
    el("operator-username").textContent = (user && (user.username || user.name)) || HomezI18n.t("operator.default_name");
    const roleEl = el("operator-role");
    if (roleEl) roleEl.textContent = (user && user.role) || "";
    applyPermissionGatedNav();
    checkMigrationApprovalGate();
  }

  // 2026-08-30 V7 후속 안정화 Phase 2 — 로그인 성공/Refresh 성공 양쪽이
  // 공유하는 토큰 저장 지점. Refresh Token은 어느 경로든 절대
  // localStorage에 쓰지 않는다(inMemoryRefreshToken만).
  async function applyIssuedTokens(data) {
    inMemoryRefreshToken = data.refresh_token || inMemoryRefreshToken;

    if (isDesktopSessionBridgeActive) {
      inMemoryDesktopAccessToken = data.access_token;
      if (window.pywebview && window.pywebview.api
        && typeof window.pywebview.api.save_console_session === "function"
        && data.user && data.user.id != null) {
        try {
          const expEpoch = decodeJwtExpEpoch(data.access_token);
          const refreshExpEpoch = inMemoryRefreshToken
            ? decodeJwtExpEpoch(inMemoryRefreshToken) : null;
          await window.pywebview.api.save_console_session(
            data.user.id, data.access_token, expEpoch || 0,
            inMemoryRefreshToken, refreshExpEpoch || null,
          );
        } catch (_) {
          /* Credential Manager 저장 실패 — 이번 프로세스의 로그인
             자체는 이미 성공했으므로 계속 진행한다(다음 재시작에만
             영향, "Desktop 재시작 시 로그인 세션이 사라지는 문제"가
             이번 1회 한정으로 재발할 뿐 로그인 자체를 막지 않는다). */
        }
      }
    } else {
      localStorage.setItem(TOKEN_KEY, data.access_token);
    }

    scheduleAutoRefresh(data.access_token);
  }

  // Access Token 만료 전에 미리 `/auth/refresh`를 호출해 갱신한다 —
  // 이전에는 이 호출 자체가 아예 없어(콘솔 어디서도 /auth/refresh를
  // 부르지 않았다) 사용자가 Access Token 수명마다 강제로 재로그인해야
  // 했다. 만료 60초 전을 목표로 하되, 이미 지났거나 임박했으면 즉시
  // 1회 실행한다.
  function scheduleAutoRefresh(accessToken) {
    if (autoRefreshTimer) {
      clearTimeout(autoRefreshTimer);
      autoRefreshTimer = null;
    }
    const expEpoch = decodeJwtExpEpoch(accessToken);
    if (!expEpoch) return;

    const delayMs = Math.max(1000, (expEpoch - Date.now() / 1000 - 60) * 1000);
    const scheduledGeneration = authGeneration;
    autoRefreshTimer = setTimeout(
      () => performAutoRefresh(scheduledGeneration), delayMs,
    );
  }

  async function performAutoRefresh(scheduledGeneration) {
    if (scheduledGeneration !== authGeneration) return; // 이미 로그아웃/재로그인됨

    const refreshToken = getRefreshToken();
    if (!refreshToken) return; // 갱신할 Refresh Token 자체가 없다 — 다음 API 호출의 401 처리에 맡긴다

    let resp;
    try {
      resp = await fetch("/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    } catch (_) {
      // 네트워크 일시 장애 — 로그아웃시키지 않고 잠시 뒤 다시 시도한다.
      autoRefreshTimer = setTimeout(
        () => performAutoRefresh(scheduledGeneration), 30000,
      );
      return;
    }

    if (scheduledGeneration !== authGeneration) return;

    if (!resp.ok) {
      const code = resp.headers.get("X-Auth-Error-Code");
      if (code && ALLOWED_LOGOUT_CODES.has(code)) {
        const reason = code === "SESSION_IDLE_TIMEOUT"
          ? HomezI18n.t("auth.session_idle_timeout")
          : HomezI18n.t("auth.session_expired");
        transitionToLogin(reason);
      }
      // 그 외(일시적 서버 오류 등)는 조용히 둔다 — 다음 실제 API
      // 호출이 필요해지는 시점에 apiFetch의 401 처리가 최종 판단한다.
      return;
    }

    const data = await resp.json();
    await applyIssuedTokens(data);
  }

  async function login(username, password) {
    // 2026-07-30: 이전에는 username/password를 URL 쿼리스트링으로 보내
    // 비밀번호가 그대로 URL에 노출되었다(브라우저 히스토리/서버 접근
    // 로그/프록시에 남을 수 있음) — 절대 금지 항목 위반이었다. 요청
    // 본문(JSON)으로만 전달한다.
    // 로그인 자체는 아직 인증되지 않은 상태에서 호출되므로, 실패해도
    // "인증 세션이 무효화됨" 처리가 아니라 로그인 폼 자체의 오류
    // 메시지로만 다뤄야 한다 — 중앙 인증 실패 처리를 건너뛴다.
    const data = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }, { skipAuthHandling: true });

    authGeneration += 1; // 새 로그인 성공 — 이전 세대의 지연 응답을 전부 무효화한다.
    // applyIssuedTokens()가 scheduleAutoRefresh() 안에서 "지금
    // authGeneration"을 캡처해 이후 그 예약이 유효한지 판단하므로,
    // 반드시 이 증가 *뒤에* 호출해야 한다(먼저 부르면 예약이 곧바로
    // 낡은 세대로 취급돼 절대 실행되지 않는다).
    await applyIssuedTokens(data);

    // permissions는 LoginResponse 최상위 필드다(user 안에 없음) — 여기서
    // 합쳐 저장해야 applyPermissionGatedNav()가 읽을 수 있다. 이 값은
    // UX 편의(메뉴 표시 여부)일 뿐, 서버는 매 요청마다 실제 권한을
    // 다시 검사한다(클라이언트 값을 신뢰하지 않음).
    localStorage.setItem(
      USER_KEY,
      JSON.stringify({ ...(data.user || {}), permissions: data.permissions || [] }),
    );

    settingsSyncEnabled = true;
    await syncServerBackedSettingsAfterAuth();
  }

  async function logout() {
    try {
      await apiFetch("/auth/logout", { method: "POST" }, { skipAuthHandling: true });
    } catch (_) {
      /* 서버 로그아웃 호출이 실패해도(네트워크 단절 등) 클라이언트
         측 세션은 반드시 지운다 — 아래에서 항상 clearSession()한다. */
    }
    authGeneration += 1;
    stopTopbarPolling();
    settingsSyncEnabled = false;
    clearSession();
    toast(HomezI18n.t("auth.logged_out_toast"));
    showLoginGate("");
  }

  // --------------------------------------------------
  // 2026-08-14 Gate F-2 — 서버 사용자 설정(`/user-settings/{key}`) 동기화.
  //
  // locale/guide_progress처럼 이전에는 origin 종속 localStorage에만
  // 있던 비민감 설정을, 로그인된 사용자에게 귀속되는 서버 값으로
  // 옮긴다(app/domains/user_settings). Desktop 재시작으로 origin이
  // 바뀌어도 이 값들은 로그인 직후 서버에서 다시 채워진다.
  //
  // 낙관적 동시성(expected_version)은 이 모듈이 이번 로그인 세션
  // 동안 기억한 마지막 버전만 사용한다 — 409(버전 충돌)를 받으면
  // 서버 값을 다시 조회해 그대로 받아들인다(서버가 항상 이긴다,
  // 이 클라이언트가 최후 승자를 강제하지 않는다).
  // --------------------------------------------------

  let settingsSyncEnabled = false;
  const userSettingVersions = Object.create(null);

  async function userSettingFetch(key) {
    try {
      const resp = await apiFetch(`/user-settings/${encodeURIComponent(key)}`, {}, { skipAuthHandling: true });
      userSettingVersions[key] = resp.version;
      return resp;
    } catch (_) {
      return null;
    }
  }

  async function userSettingSave(key, value, schemaVersion) {
    const expectedVersion = userSettingVersions[key] || 0;
    try {
      const resp = await apiFetch(`/user-settings/${encodeURIComponent(key)}`, {
        method: "PUT",
        body: JSON.stringify({
          value, expected_version: expectedVersion, schema_version: schemaVersion || 1,
        }),
      }, { skipAuthHandling: true });
      userSettingVersions[key] = resp.version;
      return resp;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // 다른 곳(다른 기기/탭)에서 먼저 저장했다 — 최신 버전을 다시
        // 받아 우리 쪽 버전 캐시만 갱신한다. 이번 시도의 값은 버린다
        // (서버가 이긴다는 설계 원칙).
        return userSettingFetch(key);
      }
      return null; // 네트워크 오류 등 — 다음 저장 시도에서 다시 시도된다.
    }
  }

  // --------------------------------------------------
  // locale 서버 동기화
  // --------------------------------------------------

  async function syncLocaleAfterAuth() {
    const server = await userSettingFetch("locale");
    if (server && server.value) {
      if (server.value !== HomezI18n.getLocale()) {
        HomezI18n.setLocale(server.value);
      }
      return;
    }
    // 서버에 아직 값이 없음 — 로그인 전(또는 이전 세션)부터 이어져온
    // localStorage 기반 언어 선택을 최초 1회 서버로 이전한다.
    await userSettingSave("locale", HomezI18n.getLocale(), 1);
  }

  function wireLocaleServerSync() {
    document.addEventListener("homez:locale-changed", (evt) => {
      if (!settingsSyncEnabled) return;
      const locale = evt && evt.detail && evt.detail.locale;
      if (!locale) return;
      userSettingSave("locale", locale, 1);
    });
  }

  // --------------------------------------------------
  // guide_progress 서버 동기화 — 실제 읽기/쓰기 지점은
  // tourSaveAllProgress()/tourResetAllProgress() 호출부에서 이 함수를
  // 함께 부른다(아래 가이드 모드 섹션 참고).
  // --------------------------------------------------

  async function syncGuideProgressAfterAuth() {
    const userKey = tourCurrentUserKey();
    const server = await userSettingFetch("guide_progress");

    if (server && server.value && typeof server.value === "object") {
      const root = tourLoadAllProgress();
      root.scopes[userKey] = server.value;
      tourSaveAllProgress(root.scopes);
      return;
    }

    // 서버에 아직 값이 없음 — 이 사용자의 기존 localStorage 진행
    // 상태(있다면)를 최초 1회 서버로 이전한다. 빈 상태까지 굳이
    // 밀어 넣지는 않는다(불필요한 최초 행 생성 방지).
    const existing = tourLoadAllProgress().scopes[userKey];
    if (existing && Object.keys(existing).length > 0) {
      await userSettingSave("guide_progress", existing, 1);
    }
  }

  function pushGuideProgressToServer() {
    if (!settingsSyncEnabled) return;
    const userKey = tourCurrentUserKey();
    const scope = tourLoadAllProgress().scopes[userKey] || {};
    // fire-and-forget — 가이드 단계 클릭마다 UI를 막지 않는다.
    userSettingSave("guide_progress", scope, 1);
  }

  async function syncServerBackedSettingsAfterAuth() {
    await syncLocaleAfterAuth();
    await syncGuideProgressAfterAuth();
  }

  // --------------------------------------------------
  // 로그인 폼
  // --------------------------------------------------

  function wirePasswordToggle(toggleId, inputId) {
    const toggleBtn = el(toggleId);
    const passwordInput = el(inputId);
    if (!toggleBtn || !passwordInput) return;

    toggleBtn.addEventListener("click", () => {
      const isShown = passwordInput.type === "text";
      passwordInput.type = isShown ? "password" : "text";
      toggleBtn.setAttribute("aria-pressed", String(!isShown));
      toggleBtn.setAttribute(
        "aria-label",
        HomezI18n.t(isShown ? "common.password_show" : "common.password_hide"),
      );
    });
  }

  function initPasswordToggle() {
    wirePasswordToggle("login-password-toggle", "login-password");
    wirePasswordToggle("setup-password-toggle", "setup-password");
    wirePasswordToggle("setup-password-confirm-toggle", "setup-password-confirm");
  }

  // --------------------------------------------------
  // 다국어(i18n) — 언어 선택기(Gate F-1, 2026-08-06)
  //
  // 로그인 화면과 설정 화면 양쪽에 [data-lang] 버튼이 있다 — 하나의
  // 공용 핸들러로 전부 처리한다. 활성 언어 버튼에는 .is-active를
  // 붙인다(현재 선택 표시). HomezI18n.setLocale()이 DOM 재적용과
  // "homez:locale-changed" 이벤트 발행까지 전부 처리한다.
  // --------------------------------------------------

  function updateActiveLangButtons() {
    document.querySelectorAll("[data-lang]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.getAttribute("data-lang") === HomezI18n.getLocale());
    });
  }

  function updatePageTitle() {
    document.title = HomezI18n.t("app.title");
  }

  function initLanguageSwitchButtons() {
    document.querySelectorAll("[data-lang]").forEach((btn) => {
      btn.addEventListener("click", () => {
        HomezI18n.setLocale(btn.getAttribute("data-lang"));
      });
    });
    document.addEventListener("homez:locale-changed", updateActiveLangButtons);
    document.addEventListener("homez:locale-changed", updatePageTitle);
    updatePageTitle();
    // 상단 배지는 서버 응답을 다시 번역해 바로 표시해야 한다("이미
    // 렌더링된 동적 상태도 현재 언어로 다시 표시" 요구사항) — 셸이
    // 보이는 상태(로그인 후)에서만 의미가 있다. 아직 번역되지 않은
    // 나머지 화면(목록·toast 등)은 F-3 이후 해당 화면을 번역할 때
    // 같은 패턴으로 확장한다.
    document.addEventListener("homez:locale-changed", () => {
      if (!el("shell").hidden) refreshTopbar();
    });
    document.addEventListener("homez:locale-changed", retranslateAllPasswordPolicyChecklists);
    // 설정 화면의 동적 테이블(가입 승인 대기·초대 코드·사용자 관리)도
    // 같은 이유로 다시 그린다 — 폼 입력값(비밀번호 필드 등)은 건드리지
    // 않도록 loadAccountSecurity() 전체가 아니라 테이블 3개만 새로
    // 불러온다(입력 중인 값을 잃지 않는다 — 요구사항 10).
    document.addEventListener("homez:locale-changed", () => {
      if (currentView !== "account-security") return;
      if (!el("registration-requests-panel").hidden) loadRegistrationRequestsPanel();
      if (!el("invitations-panel").hidden) loadInvitationsPanel();
      if (!el("admin-users-panel").hidden) loadAdminUsersTable();
    });
    // Gate T(2026-08-10) — Permission 편집 카탈로그도 JS가 그룹명·
    // Permission 이름·영향 설명을 직접 조립한다(위 Dashboard/Wizard와
    // 동일한 이유로 data-i18n만으로는 부족) — 서버를 다시 부르지 않고
    // 캐시된 카탈로그를 현재 체크 상태(저장하지 않은 변경 포함) 그대로
    // 유지한 채 새 언어로만 다시 그린다.
    document.addEventListener("homez:locale-changed", () => {
      if (currentView !== "account-security") return;
      if (el("pe-editable-area").hidden) return;
      if (!permissionCatalogCache) return;
      const checked = getCheckedPermissionCodes();
      renderPermissionCatalog(permissionCatalogCache, checked);
    });
    // Gate R-1(2026-08-09) — 상품등록 통합 마법사가 열려 있는 동안
    // 언어를 바꾸면 현재 단계도 같은 패턴(입력값 보존, 서버 재호출
    // 없음)으로 즉시 다시 그린다.
    document.addEventListener("homez:locale-changed", lwHandleLocaleChange);
    // 목록 화면(마법사를 열지 않은 상태)도 같은 이유로 다시 그린다 —
    // lwHandleLocaleChange는 lwState.wizard가 있을 때(진행 중인 단계를
    // 보는 중)만 처리하므로, 목록만 보고 있을 때는 별도로 걸어야 한다.
    document.addEventListener("homez:locale-changed", () => {
      if (currentView === "listing-wizard" && !lwState.wizard) lwRenderList();
    });
    // 상품 후보 목록도 같은 이유로 다시 그린다 — candidatesCache를
    // 그대로 재사용하므로 서버 재호출 없이 라벨만 새 언어로 바뀐다.
    document.addEventListener("homez:locale-changed", () => {
      if (currentView === "candidates" && candidatesCache) {
        populateSourceFilter(candidatesCache);
        renderCandidatesTable();
      }
    });
    // 상품 후보 상세(2026-08-19 V7 워크플로우 완성 — 분석/추천 버튼
    // 추가 시 발견) — 조회 전용 화면이라 값 보존 없이 그대로 다시
    // 불러온다(입력 폼이 아니므로 재조회해도 잃을 상태가 없다).
    document.addEventListener("homez:locale-changed", () => {
      if (currentView === "candidate-detail" && candidateDetailCurrentId) {
        loadCandidateDetail({ id: candidateDetailCurrentId });
      }
    });
    // Gate UI(2026-08-09) — Dashboard의 KPI·처리할 작업·AI 추천 목록은
    // data-i18n 정적 치환이 아니라 JS가 문자열을 직접 조립해 그린다
    // (동적 리스트라 data-i18n만으로는 항목별 라벨을 표현할 수 없음)
    // — 언어 전환 시 자동으로 다시 그려지지 않으므로, 현재 대시보드가
    // 열려 있을 때만 명시적으로 재호출한다. 실제 서버 데이터는
    // 그대로이므로 값 자체는 바뀌지 않고 라벨·문구만 새 언어로
    // 바뀐다(재조회이지만 승인 fingerprint 등 비영속 상태가 없는
    // 읽기 전용 화면이라 Wizard와 달리 별도 보존 로직이 필요 없다).
    document.addEventListener("homez:locale-changed", () => {
      if (currentView === "overview") loadOverview();
    });
    // 2026-08-13 — 가이드 화면도 동일한 패턴: 서버를 다시 부르지 않고
    // (guidesListCache 재사용) 현재 목록/상세 어느 쪽이 열려 있는지에
    // 따라 새 언어로만 다시 그린다. 상세 화면에서는 문서/영상/자막이
    // locale별로 다른 실제 파일을 가리키므로 renderGuideDetail이
    // 내부적으로 다시 fetch한다(캐시된 텍스트를 재번역하는 게 아니라
    // 실제로 그 언어의 문서가 존재하는지부터 다시 확인해야 하기 때문).
    document.addEventListener("homez:locale-changed", () => {
      if (currentView !== "guides" || !guidesListCache) return;
      if (!el("guides-detail-panel").hidden && guidesCurrentDetailId) {
        const guide = guidesListCache.find((g) => g.id === guidesCurrentDetailId);
        if (guide) renderGuideDetail(guide);
      } else {
        renderGuideCards();
      }
    });
    updateActiveLangButtons();
  }

  // --------------------------------------------------
  // 비밀번호 정책 체크리스트(편의 기능 — 서버가 항상 다시 검증한다)
  // app/core/config.py의 실제 기본값을 그대로 미러링한다:
  // PASSWORD_MIN_LENGTH=10, UPPER/LOWER/NUMBER/SPECIAL 전부 요구.
  // --------------------------------------------------

  const PASSWORD_POLICY_RULES = [
    { labelKey: "common.password_policy.min_length", test: (p) => p.length >= 10 },
    { labelKey: "common.password_policy.uppercase", test: (p) => /[A-Z]/.test(p) },
    { labelKey: "common.password_policy.lowercase", test: (p) => /[a-z]/.test(p) },
    { labelKey: "common.password_policy.number", test: (p) => /[0-9]/.test(p) },
    { labelKey: "common.password_policy.special", test: (p) => /[^A-Za-z0-9]/.test(p) },
  ];

  function passwordMeetsPolicy(password) {
    return PASSWORD_POLICY_RULES.every((rule) => rule.test(password));
  }

  function renderPasswordPolicyChecklist(listEl, password) {
    if (!listEl) return;
    listEl.innerHTML = PASSWORD_POLICY_RULES.map((rule) => {
      const ok = rule.test(password);
      return `<li class="${ok ? "ok" : ""}">${escapeHtml(HomezI18n.t(rule.labelKey))}</li>`;
    }).join("");
  }

  // 언어 전환 시 이미 렌더링된 체크리스트 문구도 다시 번역한다 —
  // 통과/실패 상태(class="ok" 여부)는 순서를 그대로 유지하므로 다시
  // 계산할 필요 없이 라벨 텍스트만 갱신한다.
  function retranslateAllPasswordPolicyChecklists() {
    document.querySelectorAll(".policy-checklist").forEach((listEl) => {
      Array.from(listEl.children).forEach((li, index) => {
        const rule = PASSWORD_POLICY_RULES[index];
        if (rule) li.textContent = HomezI18n.t(rule.labelKey);
      });
    });
  }

  function initLoginForm() {
    const form = el("login-form");
    const submitBtn = el("login-submit");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const username = el("login-username").value.trim();
      const password = el("login-password").value;
      const errorEl = el("login-error");
      errorEl.textContent = "";

      if (!username || !password) {
        errorEl.textContent = HomezI18n.t("auth.login.error_missing_fields");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = HomezI18n.t("auth.login.submitting");

      try {
        await login(username, password);
        // 2026-09-03 후속 지시(Release-Gate 감사) — 로그인 폼은
        // showShell() 이후 화면에서 숨겨지기만 할 뿐 DOM에서 제거되지
        // 않는다. 이 값을 여기서 비우지 않으면 로그인 성공 이후에도
        // 비밀번호 평문이 계속 DOM에 남아, 화면을 살펴보는 넓은 범위의
        // 조회(devtools, 자동화 스크립트 등)에 실수로 딸려 나올 수
        // 있다 — 실제 발생했던 사고 유형.
        el("login-password").value = "";
        await bootstrapDesktopTokenIfNeeded();
        showShell();
        startTopbarPolling();
        navigateTo(firstVisibleNavView());
      } catch (err) {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          errorEl.textContent = HomezI18n.t("auth.login.error_invalid_credentials");
        } else if (err instanceof ApiError && err.status === 0) {
          errorEl.textContent = err.message;
        } else {
          errorEl.textContent = HomezI18n.t("auth.login.error_generic");
        }
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = HomezI18n.t("auth.login.submit");
      }
    });
  }

  let setupConfiguredEmail = "";

  function initSetupForm() {
    const form = el("setup-form");
    const submitBtn = el("setup-submit");
    const companyNameInput = el("setup-company-name");
    const passwordInput = el("setup-password");
    const confirmInput = el("setup-password-confirm");
    const checklist = el("setup-policy-checklist");

    const updateChecklist = () => renderPasswordPolicyChecklist(checklist, passwordInput.value);
    passwordInput.addEventListener("input", updateChecklist);
    updateChecklist();

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const companyName = companyNameInput.value.trim();
      const password = passwordInput.value;
      const confirmValue = confirmInput.value;
      const errorEl = el("setup-error");
      errorEl.textContent = "";

      if (!companyName) {
        errorEl.textContent = HomezI18n.t("auth.error_company_name_required");
        return;
      }
      if (companyName.length > 100) {
        errorEl.textContent = HomezI18n.t("auth.error_company_name_too_long");
        return;
      }
      if (!passwordMeetsPolicy(password)) {
        errorEl.textContent = HomezI18n.t("auth.error_password_policy");
        return;
      }
      if (password !== confirmValue) {
        errorEl.textContent = HomezI18n.t("auth.error_password_mismatch");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = HomezI18n.t("auth.setup.creating");

      try {
        // Desktop token 쿠키가 이 시점까지 아직 발급되지 않았을 수
        // 있으므로(초기 init() 때의 부트스트랩이 브리지 준비 전에
        // 시도됐을 가능성에 대한 방어) 실제 제출 직전에 한 번 더
        // 확인·재시도한다 — "Desktop 인증이 필요합니다" 오류의
        // 재발을 막기 위한 안전장치.
        await bootstrapDesktopTokenIfNeeded();

        const nonce = await getSetupNonceFromBridge();
        if (!nonce) {
          errorEl.textContent = HomezI18n.t("auth.error_desktop_only");
          return;
        }

        await apiFetch("/desktop-setup/initialize", {
          method: "POST",
          body: JSON.stringify({
            email: setupConfiguredEmail,
            company_name: companyName,
            password,
            password_confirmation: confirmValue,
            setup_nonce: nonce,
          }),
        });

        // 계정 생성과 로그인을 자동 결합하지 않는다 — 입력값을 지우고
        // 로그인 화면으로 전환해 사용자가 방금 정한 비밀번호로 다시
        // 직접 로그인하도록 한다.
        passwordInput.value = "";
        confirmInput.value = "";
        toast(HomezI18n.t("auth.setup.success"), "success");
        showLoginGate("", setupConfiguredEmail);
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("auth.setup.error_generic");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = HomezI18n.t("auth.setup.submit");
      }
    });
  }

  let companyRecoveryConfiguredEmail = "";

  function initCompanyRecoveryForm() {
    const form = el("company-recovery-form");
    const submitBtn = el("company-recovery-submit");
    const companyNameInput = el("company-recovery-name");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const companyName = companyNameInput.value.trim();
      const errorEl = el("company-recovery-error");
      errorEl.textContent = "";

      if (!companyName) {
        errorEl.textContent = HomezI18n.t("auth.error_company_name_required");
        return;
      }
      if (companyName.length > 100) {
        errorEl.textContent = HomezI18n.t("auth.error_company_name_too_long");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = HomezI18n.t("auth.company_recovery.setting");

      try {
        await bootstrapDesktopTokenIfNeeded();

        const nonce = await getSetupNonceFromBridge();
        if (!nonce) {
          errorEl.textContent = HomezI18n.t("auth.error_desktop_only");
          return;
        }

        await apiFetch("/desktop-setup/company-recovery/initialize", {
          method: "POST",
          body: JSON.stringify({
            company_name: companyName,
            setup_nonce: nonce,
          }),
        });

        // 회사 연결과 로그인을 자동 결합하지 않는다 — 최초 관리자
        // 설정과 동일한 원칙(입력값을 지우고 로그인 화면으로 전환해
        // 다시 직접 로그인하도록 한다). 비밀번호 재입력은 요구하지
        // 않는다(이미 있는 계정의 비밀번호는 그대로다).
        companyNameInput.value = "";
        toast(HomezI18n.t("auth.company_recovery.success"), "success");
        showLoginGate("", companyRecoveryConfiguredEmail);
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("auth.company_recovery.error_generic");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = HomezI18n.t("auth.company_recovery.submit");
      }
    });
  }

  // --------------------------------------------------
  // 계정 복구(아이디 찾기 / 비밀번호 재설정) — 로그인 화면 링크
  //
  // 이 화면의 모든 API 호출은 계정 존재 여부와 무관하게 항상 같은
  // 문구를 보여준다(서버 쪽 app/domains/account_recovery/service.py가
  // 실제 열거 방지를 담당하고, 이 JS는 그 응답을 그대로 표시만 한다 —
  // 클라이언트에서 별도로 분기하지 않는다).
  // --------------------------------------------------

  function showAccountRecoveryGate(initialTab) {
    el("shell").hidden = true;
    el("login-gate").hidden = true;
    el("setup-gate").hidden = true;
    el("company-recovery-gate").hidden = true;
    el("register-gate").hidden = true;
    el("account-recovery-gate").hidden = false;
    selectRecoveryTab(initialTab || "forgot-id");
  }

  function selectRecoveryTab(tab) {
    const tabs = {
      "forgot-id": { btn: "recovery-tab-forgot-id", panel: "recovery-forgot-id-form" },
      "code": { btn: "recovery-tab-code", panel: "recovery-code-form" },
      "email": { btn: "recovery-tab-email", panel: "recovery-email-request-form" },
    };

    Object.entries(tabs).forEach(([key, ids]) => {
      const isActive = key === tab;
      el(ids.btn).setAttribute("aria-selected", String(isActive));
      el(ids.panel).hidden = !isActive;
    });

    if (tab === "email") {
      refreshEmailResetStatusBanner();
    }
  }

  async function refreshEmailResetStatusBanner() {
    const banner = el("recovery-email-status-banner");
    banner.textContent = HomezI18n.t("auth.recovery.email_status_checking");
    try {
      const status = await apiFetch("/account-recovery/reset-password/email/status");
      banner.textContent = status.configured
        ? HomezI18n.t("auth.recovery.email_status_configured")
        : HomezI18n.t("auth.recovery.email_status_not_configured");
    } catch (_) {
      banner.textContent = "";
    }
  }

  function initAccountRecoveryLinks() {
    el("login-forgot-id-link").addEventListener("click", () => showAccountRecoveryGate("forgot-id"));
    el("login-reset-password-link").addEventListener("click", () => showAccountRecoveryGate("code"));
    el("recovery-tab-forgot-id").addEventListener("click", () => selectRecoveryTab("forgot-id"));
    el("recovery-tab-code").addEventListener("click", () => selectRecoveryTab("code"));
    el("recovery-tab-email").addEventListener("click", () => selectRecoveryTab("email"));
    el("recovery-back-to-login").addEventListener("click", () => showLoginGate(""));
  }

  // --------------------------------------------------
  // 회원가입 — 가입 즉시 앱 접근 불가, 관리자 승인 필요.
  // --------------------------------------------------

  function initRegisterForm() {

    el("login-register-link").addEventListener("click", () => showRegisterGate());
    el("register-back-to-login").addEventListener("click", () => showLoginGate(""));

    wirePasswordToggle("register-password-toggle", "register-password");
    wirePasswordToggle("register-password-confirm-toggle", "register-password-confirm");

    const form = el("register-form");
    const submitBtn = el("register-submit");
    const passwordInput = el("register-password");
    const confirmInput = el("register-password-confirm");
    const checklist = el("register-policy-checklist");

    passwordInput.addEventListener("input", () => renderPasswordPolicyChecklist(checklist, passwordInput.value));
    renderPasswordPolicyChecklist(checklist, "");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errorEl = el("register-error");
      const resultEl = el("register-result");
      errorEl.textContent = "";
      resultEl.textContent = "";

      const email = el("register-email").value.trim();
      const displayName = el("register-display-name").value.trim();
      const password = passwordInput.value;
      const confirmValue = confirmInput.value;
      // 초대 코드는 선택 입력 — 공백만 있으면 "제공되지 않음"으로
      // 취급해 서버에는 null로 보낸다(빈 문자열을 유효한 코드처럼
      // 검증 시도하지 않도록).
      const invitationCodeRaw = el("register-invitation-code").value.trim();
      const invitationCode = invitationCodeRaw || null;

      if (!email || !displayName) {
        errorEl.textContent = HomezI18n.t("auth.error_all_fields_required");
        return;
      }
      if (!passwordMeetsPolicy(password)) {
        errorEl.textContent = HomezI18n.t("auth.error_password_policy");
        return;
      }
      if (password !== confirmValue) {
        errorEl.textContent = HomezI18n.t("auth.error_password_mismatch");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = HomezI18n.t("auth.register.submitting");

      try {
        await bootstrapDesktopTokenIfNeeded();

        const data = await apiFetch("/account-registration/register", {
          method: "POST",
          body: JSON.stringify({
            email, display_name: displayName, password,
            password_confirmation: confirmValue,
            invitation_code: invitationCode,
          }),
        });

        // 가입 신청 즉시 로그인시키지 않는다 — 입력값을 지우고 결과
        // 메시지만 보여준다(자동 로그인 없음, auth_session 생성 없음).
        form.reset();
        renderPasswordPolicyChecklist(checklist, "");
        resultEl.textContent = data.message;
        toast(HomezI18n.t("auth.register.success_toast"), "success");
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("auth.register.error_generic");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = HomezI18n.t("auth.register.submit");
      }
    });
  }

  function initForgotIdForm() {
    const form = el("recovery-forgot-id-form");
    const submitBtn = el("recovery-forgot-id-submit");
    const emailInput = el("recovery-forgot-id-email");
    const resultEl = el("recovery-forgot-id-result");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      resultEl.textContent = "";

      submitBtn.disabled = true;
      submitBtn.textContent = HomezI18n.t("auth.recovery.requesting");

      try {
        await bootstrapDesktopTokenIfNeeded();
        const data = await apiFetch("/account-recovery/forgot-id", {
          method: "POST",
          body: JSON.stringify({ email: emailInput.value.trim() }),
        });
        resultEl.textContent = data.message;
        emailInput.value = "";
      } catch (err) {
        resultEl.textContent = (err && err.message) || HomezI18n.t("auth.recovery.error_request_generic");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = HomezI18n.t("auth.recovery.forgot_id_submit");
      }
    });
  }

  function initRecoveryCodeResetForm() {
    const form = el("recovery-code-form");
    const submitBtn = el("recovery-code-submit");
    const emailInput = el("recovery-code-email");
    const codeInput = el("recovery-code-value");
    const passwordInput = el("recovery-code-new-password");
    const confirmInput = el("recovery-code-new-password-confirm");
    const checklist = el("recovery-code-policy-checklist");
    const resultEl = el("recovery-code-result");

    passwordInput.addEventListener("input", () => renderPasswordPolicyChecklist(checklist, passwordInput.value));
    renderPasswordPolicyChecklist(checklist, "");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      resultEl.textContent = "";

      if (!passwordMeetsPolicy(passwordInput.value)) {
        resultEl.textContent = HomezI18n.t("auth.error_new_password_policy");
        return;
      }
      if (passwordInput.value !== confirmInput.value) {
        resultEl.textContent = HomezI18n.t("auth.error_new_password_mismatch");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = HomezI18n.t("auth.recovery.resetting");

      try {
        await bootstrapDesktopTokenIfNeeded();
        const data = await apiFetch("/account-recovery/reset-password/recovery-code", {
          method: "POST",
          body: JSON.stringify({
            email: emailInput.value.trim(),
            code: codeInput.value.trim(),
            new_password: passwordInput.value,
            new_password_confirmation: confirmInput.value,
          }),
        });
        resultEl.textContent = data.message;
        emailInput.value = "";
        codeInput.value = "";
        passwordInput.value = "";
        confirmInput.value = "";
        renderPasswordPolicyChecklist(checklist, "");
        toast(HomezI18n.t("auth.recovery.reset_success_toast"), "success");
      } catch (err) {
        resultEl.textContent = (err && err.message) || HomezI18n.t("auth.recovery.reset_error_generic");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = HomezI18n.t("auth.recovery.code_submit");
      }
    });
  }

  function initEmailResetForms() {
    const requestForm = el("recovery-email-request-form");
    const requestSubmitBtn = el("recovery-email-request-submit");
    const requestEmailInput = el("recovery-email-request-email");
    const requestResultEl = el("recovery-email-request-result");

    requestForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      requestResultEl.textContent = "";

      requestSubmitBtn.disabled = true;
      requestSubmitBtn.textContent = HomezI18n.t("auth.recovery.requesting");

      try {
        await bootstrapDesktopTokenIfNeeded();
        const data = await apiFetch("/account-recovery/reset-password/email/request", {
          method: "POST",
          body: JSON.stringify({ email: requestEmailInput.value.trim() }),
        });
        requestResultEl.textContent = data.message;
        requestEmailInput.value = "";
      } catch (err) {
        requestResultEl.textContent = (err && err.message) || HomezI18n.t("auth.recovery.error_request_generic");
      } finally {
        requestSubmitBtn.disabled = false;
        requestSubmitBtn.textContent = HomezI18n.t("auth.recovery.email_request_submit");
      }
    });

    const confirmSubmitBtn = el("recovery-email-confirm-submit");
    const tokenInput = el("recovery-email-confirm-token");
    const confirmPasswordInput = el("recovery-email-confirm-new-password");
    const confirmPasswordConfirmInput = el("recovery-email-confirm-new-password-confirm");
    const confirmChecklist = el("recovery-email-confirm-policy-checklist");
    const confirmResultEl = el("recovery-email-confirm-result");

    confirmPasswordInput.addEventListener("input", () => renderPasswordPolicyChecklist(confirmChecklist, confirmPasswordInput.value));
    renderPasswordPolicyChecklist(confirmChecklist, "");

    confirmSubmitBtn.addEventListener("click", async () => {
      confirmResultEl.textContent = "";

      if (!tokenInput.value.trim()) {
        confirmResultEl.textContent = HomezI18n.t("auth.error_reset_token_required");
        return;
      }
      if (!passwordMeetsPolicy(confirmPasswordInput.value)) {
        confirmResultEl.textContent = HomezI18n.t("auth.error_new_password_policy");
        return;
      }
      if (confirmPasswordInput.value !== confirmPasswordConfirmInput.value) {
        confirmResultEl.textContent = HomezI18n.t("auth.error_new_password_mismatch");
        return;
      }

      confirmSubmitBtn.disabled = true;
      confirmSubmitBtn.textContent = HomezI18n.t("auth.recovery.resetting");

      try {
        await bootstrapDesktopTokenIfNeeded();
        const data = await apiFetch("/account-recovery/reset-password/email/confirm", {
          method: "POST",
          body: JSON.stringify({
            token: tokenInput.value.trim(),
            new_password: confirmPasswordInput.value,
            new_password_confirmation: confirmPasswordConfirmInput.value,
          }),
        });
        confirmResultEl.textContent = data.message;
        tokenInput.value = "";
        confirmPasswordInput.value = "";
        confirmPasswordConfirmInput.value = "";
        renderPasswordPolicyChecklist(confirmChecklist, "");
        toast(HomezI18n.t("auth.recovery.reset_success_toast"), "success");
      } catch (err) {
        confirmResultEl.textContent = (err && err.message) || HomezI18n.t("auth.recovery.reset_error_generic");
      } finally {
        confirmSubmitBtn.disabled = false;
        confirmSubmitBtn.textContent = HomezI18n.t("auth.recovery.token_submit");
      }
    });
  }

  function initAccountRecoveryForms() {
    initAccountRecoveryLinks();
    initForgotIdForm();
    initRecoveryCodeResetForm();
    initEmailResetForms();
    wirePasswordToggle("recovery-code-password-toggle", "recovery-code-new-password");
    wirePasswordToggle("recovery-code-password-confirm-toggle", "recovery-code-new-password-confirm");
    wirePasswordToggle("recovery-email-confirm-password-toggle", "recovery-email-confirm-new-password");
    wirePasswordToggle("recovery-email-confirm-password-confirm-toggle", "recovery-email-confirm-new-password-confirm");
  }

  // --------------------------------------------------
  // 확인 Dialog
  // --------------------------------------------------

  function confirmDialog({ title, body, requireReason = false, okLabel = HomezI18n.t("common.confirm") }) {
    return new Promise((resolve) => {
      const dialog = el("confirm-dialog");
      el("confirm-title").textContent = title;
      el("confirm-body").textContent = body;
      el("confirm-memo").value = "";
      el("confirm-reason").value = "";
      el("confirm-memo-field").hidden = requireReason;
      el("confirm-reason-field").hidden = !requireReason;
      el("confirm-ok").textContent = okLabel;

      const okBtn = el("confirm-ok");
      const cancelBtn = el("confirm-cancel");

      function cleanup() {
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
      }

      function onOk() {
        if (requireReason && !el("confirm-reason").value.trim()) {
          el("confirm-reason").focus();
          return;
        }
        const value = requireReason ? el("confirm-reason").value.trim() : el("confirm-memo").value.trim();
        cleanup();
        resolve({ confirmed: true, value });
      }

      function onCancel() {
        cleanup();
        resolve({ confirmed: false, value: null });
      }

      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      dialog.addEventListener("cancel", onCancel);

      dialog.showModal();
      (requireReason ? el("confirm-reason") : cancelBtn).focus();
    });
  }

  // --------------------------------------------------
  // Migration 승인 UX(2026-08-05 CTO 재검증 지시 Gate E)
  //
  // GET /desktop-setup/migration-status는 로그인 여부와 무관하게 항상
  // 조용히 호출 가능하지만, 실제 화면은 로그인 이후(showShell 직후)에만
  // 띄운다 — 로그인 폼 자체를 가리지 않기 위함이다. 세션당 1회만
  // 자동으로 뜬다(migrationGateChecked) — "나중에"를 눌러도 로그인을
  // 다시 하기 전까지 반복해서 튀어나오지 않는다(제한 모드 배너의
  // "지금 적용" 버튼으로 언제든 다시 열 수 있다).
  // --------------------------------------------------

  let migrationGateChecked = false;

  function applyLimitedModeUI(active) {
    homezLimitedMode = active;
    const banner = el("limited-mode-banner");
    if (banner) banner.hidden = !active;
  }

  function renderMigrationPlanList(status) {
    const listEl = el("ma-plan-list");
    if (!listEl) return;

    if (!status.pending_files.length) {
      listEl.innerHTML = "";
      return;
    }

    listEl.innerHTML = status.pending_files.map((filename) => {
      const targets = status.targets_by_file[filename] || { tables: [], indexes: [] };
      const tableStr = targets.tables.length ? targets.tables.join(", ") : "-";
      const indexStr = targets.indexes.length ? targets.indexes.join(", ") : "-";
      return (
        `<dt>${escapeHtml(filename)}</dt>`
        + `<dd>${HomezI18n.t("ma.plan.tables_label")}${escapeHtml(tableStr)}<br>${HomezI18n.t("ma.plan.indexes_label")}${escapeHtml(indexStr)}</dd>`
      );
    }).join("");
  }

  function showMigrationApprovalDialog(status) {
    const dialog = el("migration-approval-dialog");
    if (!dialog) return;

    renderMigrationPlanList(status);
    el("ma-backup-preview").textContent = status.backup_path_preview || HomezI18n.t("ma.backup_unknown");
    el("ma-progress").hidden = true;
    el("ma-result").hidden = true;
    el("ma-error").textContent = "";
    el("ma-current-password").value = "";
    const approveBtn = el("ma-approve-btn");
    const laterBtn = el("ma-later-btn");
    const closeBtn = el("ma-close-btn");
    approveBtn.hidden = false;
    approveBtn.disabled = false;
    laterBtn.hidden = false;
    laterBtn.disabled = false;
    closeBtn.hidden = true;

    function cleanup() {
      approveBtn.removeEventListener("click", onApprove);
      laterBtn.removeEventListener("click", onLater);
      closeBtn.removeEventListener("click", onClose);
      dialog.removeEventListener("cancel", onLater);
    }

    async function onApprove() {
      const currentPassword = el("ma-current-password").value;

      if (!currentPassword) {
        el("ma-error").textContent = HomezI18n.t("ma.error_current_password_required");
        return;
      }

      approveBtn.disabled = true;
      laterBtn.disabled = true;
      el("ma-progress").hidden = false;
      el("ma-error").textContent = "";

      try {
        // Gate G(2026-08-07): 실제 적용 전 SUPER_ADMIN 본인이 방금
        // 비밀번호를 다시 입력했다는 증거(recent-auth, 5분·1회용)를
        // 먼저 받아야 한다 — 실패는 세션 무효화가 아니라 이 Dialog
        // 안에서만 처리한다(중앙 인증 실패 처리를 거치지 않는다).
        let recentAuth;
        try {
          recentAuth = await apiFetch("/auth/recent-auth", {
            method: "POST",
            body: JSON.stringify({ current_password: currentPassword }),
          }, { skipAuthHandling: true });
        } catch (err) {
          el("ma-progress").hidden = true;
          approveBtn.disabled = false;
          laterBtn.disabled = false;
          if (err instanceof ApiError && err.status === 401) {
            el("ma-error").textContent = HomezI18n.t("ma.error_invalid_password");
          } else if (err instanceof ApiError && err.status === 429) {
            el("ma-error").textContent = HomezI18n.t("ma.error_too_many_attempts");
          } else {
            el("ma-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
          }
          return;
        }

        const result = await apiFetch("/desktop-setup/migration-status/approve", {
          method: "POST",
          headers: { "X-Recent-Auth-Token": recentAuth.recent_auth_token },
          body: JSON.stringify({
            approved_files: status.pending_files,
            approval_nonce: status.approval_nonce,
          }),
        });

        el("ma-current-password").value = "";
        el("ma-progress").hidden = true;

        if (result.approval_required) {
          // 그 사이 실제 pending 목록이 바뀌어 적용되지 않았다 — 최신
          // 상태로 다시 확인해 같은 Dialog를 새로 띄운다(부분/오래된
          // 승인을 그대로 밀어붙이지 않는다).
          const fresh = await apiFetch("/desktop-setup/migration-status");
          cleanup();
          if (fresh.approval_required) {
            showMigrationApprovalDialog(fresh);
          } else {
            dialog.close();
            applyLimitedModeUI(false);
          }
          return;
        }

        el("ma-result").hidden = false;
        el("ma-result").textContent = (
          HomezI18n.t("ma.result_prefix") + (result.integrity_check_result || HomezI18n.t("ma.result_unknown"))
        );
        approveBtn.hidden = true;
        laterBtn.hidden = true;
        closeBtn.hidden = false;
        applyLimitedModeUI(false);
        toast(HomezI18n.t("ma.applied_toast"), "success");
      } catch (err) {
        el("ma-progress").hidden = true;
        approveBtn.disabled = false;
        laterBtn.disabled = false;
        el("ma-error").textContent = (
          err instanceof ApiError ? (err.detail || err.message) : HomezI18n.t("ma.error_generic")
        );
      }
    }

    function onLater() {
      cleanup();
      dialog.close();
      applyLimitedModeUI(true);
      toast(HomezI18n.t("ma.later_toast"));
    }

    function onClose() {
      cleanup();
      dialog.close();
    }

    approveBtn.addEventListener("click", onApprove);
    laterBtn.addEventListener("click", onLater);
    closeBtn.addEventListener("click", onClose);
    dialog.addEventListener("cancel", onLater);

    dialog.showModal();
  }

  function initLimitedModeBanner() {
    const btn = el("limited-mode-apply-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      try {
        const status = await apiFetch("/desktop-setup/migration-status");
        if (status.approval_required) {
          showMigrationApprovalDialog(status);
        } else {
          applyLimitedModeUI(false);
        }
      } catch (_) {
        toast(HomezI18n.t("ma.status_check_error"), "error");
      }
    });
  }

  async function checkMigrationApprovalGate() {
    if (migrationGateChecked) return;
    migrationGateChecked = true;

    try {
      const status = await apiFetch("/desktop-setup/migration-status");
      if (status.approval_required) {
        showMigrationApprovalDialog(status);
      }
    } catch (_) {
      // 조회 실패(네트워크/콜드스타트 등)는 조용히 무시한다 — 다음
      // 로그인/새로고침에서 다시 시도된다. 승인 화면을 억지로 띄우지
      // 않는다(fail-open — 조회 실패 자체가 승인 거부 사유는 아니다).
      migrationGateChecked = false;
    }
  }

  // --------------------------------------------------
  // 라우팅 (뷰 전환)
  // --------------------------------------------------

  // --------------------------------------------------
  // Gate AI-F3(2026-08-22) — AI 업무 제안 통합 화면, 운영 우선순위,
  // 주문 예외 검토, 정산 차이 검토.
  //
  // 이 4개 화면은 전부 이미 구현·테스트된 백엔드(app/domains/
  // ai_governance, app/domains/order/exception_analysis_service.py,
  // app/domains/settlement/difference_analysis_service.py, app/
  // domains/orchestration/priority_service.py)를 그대로 호출할 뿐,
  // 새 판단 로직을 프런트엔드에 만들지 않는다. "capability"/
  // "ProposedAction"/"AIResultEnvelope" 같은 내부 용어는 여기 UI
  // 문구에 노출하지 않는다(전부 i18n 카탈로그의 사용자 문구로만
  // 표시).
  // --------------------------------------------------

  // HIGH_RISK_ACTION_TYPES — app/domains/ai_governance/constants.py와
  // 동일한 값을 그대로 미러링한다(서버가 최종 판단하므로 이 목록은
  // UI가 재인증 프롬프트를 미리 띄울지 결정하는 용도일 뿐이다 —
  // 클라이언트가 이 판단을 틀리게 해도 서버가 다시 강제한다).
  const AIP_HIGH_RISK_ACTION_TYPES = new Set([
    "PRICE_CHANGE_PROPOSAL",
    "INVENTORY_REPLENISHMENT_PROPOSAL",
    "PURCHASE_ORDER_PROPOSAL",
    "REFUND_REVIEW",
    "SETTLEMENT_DIFFERENCE_REVIEW",
  ]);

  const AIP_STATUS_LABEL_KEYS = {
    DRAFT: "ai_proposals.status_draft",
    REVIEW_REQUIRED: "ai_proposals.status_review_required",
    APPROVED: "ai_proposals.status_approved",
    REJECTED: "ai_proposals.status_rejected",
    EXPIRED: "ai_proposals.status_expired",
    EXECUTED: "ai_proposals.status_executed",
    INVALIDATED: "ai_proposals.status_invalidated",
  };

  const AIP_RISK_LABEL_KEYS = {
    LOW: "ai_proposals.risk_low",
    MEDIUM: "ai_proposals.risk_medium",
    HIGH: "ai_proposals.risk_high",
  };

  const AIP_OPEN_STATUSES = new Set(["DRAFT", "REVIEW_REQUIRED", "APPROVED"]);

  function aipStatusLabel(status) {
    const key = AIP_STATUS_LABEL_KEYS[status];
    return key ? HomezI18n.t(key) : status;
  }

  function aipRiskLabel(risk) {
    const key = AIP_RISK_LABEL_KEYS[risk];
    return key ? HomezI18n.t(key) : risk;
  }

  function aipRiskClass(risk) {
    if (risk === "HIGH") return "risk-high";
    if (risk === "MEDIUM") return "risk-mid";
    if (risk === "LOW") return "risk-low";
    return "";
  }

  function aipIsExpired(action) {
    if (!action.expires_at) return false;
    return new Date(action.expires_at).getTime() < Date.now();
  }

  // 여러 화면이 공유하는 EStop 배너 — app/web/console.js의 loadSafety()
  // 가 쓰는 것과 동일한 /console/api/safety-status를 재사용한다(새
  // 엔드포인트를 만들지 않는다).
  async function renderSharedEstopBanner(slotEl) {
    if (!slotEl) return false;
    let data;
    try {
      data = await apiFetch("/console/api/safety-status");
    } catch (_err) {
      slotEl.innerHTML = "";
      return false;
    }
    const stop = data && data.emergency_stop;
    const isActive = !!(stop && stop.is_active);
    if (!isActive) {
      slotEl.innerHTML = "";
      return false;
    }
    slotEl.innerHTML = `
      <div class="safety-banner is-stopped">
        <div>
          <div class="banner-title">${HomezI18n.t("ai_proposals.estop_banner_title")}</div>
          <div class="stat-sub">${escapeHtml(stop.reason || "")}</div>
        </div>
      </div>
    `;
    return true;
  }

  // --------------------------------------------------
  // 재인증(recent-auth) 프롬프트 — 고위험 action_type 승인 전용 공용
  // 헬퍼. 성공 시 토큰 문자열을, 사용자가 취소하면 null을 반환한다.
  // 비밀번호 자체는 이 함수 밖으로 절대 나가지 않는다(/auth/recent-auth
  // 호출에만 쓰고 즉시 버린다).
  // --------------------------------------------------

  function promptRecentAuthToken() {
    return new Promise((resolve) => {
      const dialog = el("recent-auth-dialog");
      const pwInput = el("ra-password");
      const errEl = el("ra-error");
      const progressEl = el("ra-progress");
      const okBtn = el("ra-ok");
      const cancelBtn = el("ra-cancel");

      pwInput.value = "";
      errEl.textContent = "";
      progressEl.hidden = true;
      okBtn.disabled = false;

      function cleanup() {
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
      }

      function onCancel() {
        cleanup();
        resolve(null);
      }

      async function onOk() {
        const currentPassword = pwInput.value;
        if (!currentPassword) {
          errEl.textContent = HomezI18n.t("ma.error_current_password_required");
          return;
        }
        okBtn.disabled = true;
        progressEl.hidden = false;
        errEl.textContent = "";
        try {
          const recentAuth = await apiFetch("/auth/recent-auth", {
            method: "POST",
            body: JSON.stringify({ current_password: currentPassword }),
          }, { skipAuthHandling: true });
          cleanup();
          resolve(recentAuth.recent_auth_token);
        } catch (err) {
          progressEl.hidden = true;
          okBtn.disabled = false;
          if (err instanceof ApiError && err.status === 401) {
            errEl.textContent = HomezI18n.t("ma.error_invalid_password");
          } else if (err instanceof ApiError && err.status === 429) {
            errEl.textContent = HomezI18n.t("ma.error_too_many_attempts");
          } else {
            errEl.textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
          }
        }
      }

      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      dialog.addEventListener("cancel", onCancel);
      dialog.showModal();
      pwInput.focus();
    });
  }

  // --------------------------------------------------
  // AI 업무 제안 — 목록
  // --------------------------------------------------

  let aipCurrentStatus = "";

  async function loadAiProposals() {
    wireAiProposalsToolbarOnce();
    renderSharedEstopBanner(el("aip-estop-banner-slot"));

    document.querySelectorAll(".aip-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.status === aipCurrentStatus);
    });

    const wrap = el("aip-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = aipCurrentStatus ? `?status=${encodeURIComponent(aipCurrentStatus)}` : "";
      rows = await apiFetch(`/ai-governance/proposed-actions${qs}`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (!rows || rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("ai_proposals.empty_title"), HomezI18n.t("ai_proposals.empty_sub"));
      return;
    }

    const colType = HomezI18n.t("ai_proposals.col_type");
    const colTarget = HomezI18n.t("ai_proposals.col_target");
    const colRisk = HomezI18n.t("ai_proposals.col_risk");
    const colStatus = HomezI18n.t("ai_proposals.col_status");
    const colCreated = HomezI18n.t("ai_proposals.col_created_at");
    const colExpires = HomezI18n.t("ai_proposals.col_expires_at");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colType}</th><th>${colTarget}</th><th>${colRisk}</th><th>${colStatus}</th><th>${colCreated}</th><th>${colExpires}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" data-id="${r.id}" tabindex="0">
            <td data-label="${colType}">${escapeHtml(r.action_type)}</td>
            <td data-label="${colTarget}">${escapeHtml(r.target_entity)}</td>
            <td data-label="${colRisk}"><span class="${aipRiskClass(r.risk_level)}">${escapeHtml(aipRiskLabel(r.risk_level))}</span></td>
            <td data-label="${colStatus}">${escapeHtml(aipStatusLabel(r.status))}${aipIsExpired(r) && AIP_OPEN_STATUSES.has(r.status) ? ` (${escapeHtml(HomezI18n.t("ai_proposals.expiring_soon"))})` : ""}</td>
            <td data-label="${colCreated}">${fmtDate(r.created_at)}</td>
            <td data-label="${colExpires}">${fmtDate(r.expires_at)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      const openIt = () => navigateTo("ai-proposal-detail", { id: Number(tr.dataset.id) });
      tr.addEventListener("click", openIt);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openIt();
        }
      });
    });
  }

  function wireAiProposalsToolbarOnce() {
    if (wireAiProposalsToolbarOnce._wired) return;
    wireAiProposalsToolbarOnce._wired = true;

    document.querySelectorAll(".aip-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        aipCurrentStatus = btn.dataset.status;
        loadAiProposals();
      });
    });
  }

  // --------------------------------------------------
  // AI 업무 제안 — 상세·승인·반려
  // --------------------------------------------------

  let aipDetailCurrentId = null;

  async function loadAiProposalDetail(opts = {}) {
    const id = opts.id || aipDetailCurrentId;
    aipDetailCurrentId = id;
    wireAiProposalDetailBackOnce();

    const body = el("aip-detail-body");
    body.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(body, HomezI18n.t("ai_proposals.empty_title"), "");
      return;
    }

    let action;
    try {
      action = await apiFetch(`/ai-governance/proposed-actions/${id}`);
    } catch (err) {
      renderErrorState(body, err);
      return;
    }

    renderAiProposalDetail(body, action);
  }

  function wireAiProposalDetailBackOnce() {
    if (wireAiProposalDetailBackOnce._wired) return;
    wireAiProposalDetailBackOnce._wired = true;
    el("aip-detail-back-btn").addEventListener("click", () => navigateTo("ai-proposals"));
  }

  function renderAiProposalDetail(body, action) {
    const isOpen = AIP_OPEN_STATUSES.has(action.status) && !aipIsExpired(action);
    const isHighRisk = AIP_HIGH_RISK_ACTION_TYPES.has(action.action_type);
    const canDecide = action.status === "DRAFT" || action.status === "REVIEW_REQUIRED";

    const evidenceHtml = (action.evidence || []).length
      ? `<ul>${action.evidence.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>`
      : `<p class="field-hint">${escapeHtml(HomezI18n.t("ai_proposals.detail_no_missing_evidence"))}</p>`;

    body.innerHTML = `
      <div class="view-header">
        <h1>${escapeHtml(action.action_type)}</h1>
        <span class="${aipRiskClass(action.risk_level)}">${escapeHtml(aipRiskLabel(action.risk_level))}</span>
      </div>
      <div class="dash-card">
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.col_target"))}</dt><dd>${escapeHtml(action.target_entity)}</dd>
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.detail_reason"))}</dt><dd>${escapeHtml(action.reason)}</dd>
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.detail_payload"))}</dt><dd><pre class="detail-payload">${escapeHtml(JSON.stringify(action.proposed_payload, null, 2))}</pre></dd>
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.detail_missing_evidence"))}</dt><dd>${evidenceHtml}</dd>
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.col_status"))}</dt><dd>${escapeHtml(aipStatusLabel(action.status))}</dd>
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.col_created_at"))}</dt><dd>${fmtDate(action.created_at)}</dd>
          <dt>${escapeHtml(HomezI18n.t("ai_proposals.col_expires_at"))}</dt><dd>${fmtDate(action.expires_at)}</dd>
          ${action.decision_reason ? `<dt>${escapeHtml(HomezI18n.t("ai_proposals.detail_decision_reason"))}</dt><dd>${escapeHtml(action.decision_reason)}</dd>` : ""}
          ${action.executed_at ? `<dt>${escapeHtml(HomezI18n.t("ai_proposals.detail_executed_reference"))}</dt><dd>${escapeHtml(action.executed_reference || "—")}</dd>` : ""}
        </dl>
      </div>
      ${!canDecide ? "" : `
        <div class="dialog-actions">
          <button type="button" class="btn btn-danger" id="aip-reject-btn">${escapeHtml(HomezI18n.t("ai_proposals.reject_btn"))}</button>
          <button type="button" class="btn btn-primary" id="aip-approve-btn" ${isOpen ? "" : "disabled"}>${escapeHtml(HomezI18n.t("ai_proposals.approve_btn"))}</button>
        </div>
        ${!isOpen ? `<p class="field-error">${escapeHtml(HomezI18n.t("ai_proposals.execution_blocked_expired"))}</p>` : ""}
      `}
    `;

    if (!canDecide) return;

    const approveBtn = el("aip-approve-btn");
    const rejectBtn = el("aip-reject-btn");

    if (approveBtn) {
      approveBtn.addEventListener("click", () => withButtonGuard(approveBtn, async () => {
        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("ai_proposals.approve_confirm_title"),
          body: HomezI18n.t("ai_proposals.approve_confirm_body"),
          okLabel: HomezI18n.t("ai_proposals.approve_btn"),
        });
        if (!confirmed) return;

        let recentAuthToken = null;
        if (isHighRisk) {
          recentAuthToken = await promptRecentAuthToken();
          if (recentAuthToken === null) return;
        }

        try {
          const headers = recentAuthToken ? { "X-Recent-Auth-Token": recentAuthToken } : {};
          await apiFetch(`/ai-governance/proposed-actions/${action.id}/approve`, {
            method: "POST",
            headers,
            body: JSON.stringify({}),
          });
          toast(HomezI18n.t("ai_proposals.approve_success"), "success");
          loadAiProposalDetail({ id: action.id });
        } catch (err) {
          toast((err && err.message) || HomezI18n.t("ai_proposals.approve_error"), "error");
        }
      }));
    }

    if (rejectBtn) {
      rejectBtn.addEventListener("click", () => withButtonGuard(rejectBtn, async () => {
        const { confirmed, reason } = await confirmDialog({
          title: HomezI18n.t("ai_proposals.reject_confirm_title"),
          body: HomezI18n.t("ai_proposals.reject_confirm_body"),
          requireReason: true,
          okLabel: HomezI18n.t("ai_proposals.reject_btn"),
        });
        if (!confirmed) return;

        try {
          await apiFetch(`/ai-governance/proposed-actions/${action.id}/reject`, {
            method: "POST",
            body: JSON.stringify({ reason }),
          });
          toast(HomezI18n.t("ai_proposals.reject_success"), "success");
          loadAiProposalDetail({ id: action.id });
        } catch (err) {
          toast((err && err.message) || HomezI18n.t("ai_proposals.reject_error"), "error");
        }
      }));
    }
  }

  // --------------------------------------------------
  // 2026-09-15 전면 감사 후속(Phase 9H, HOMEZ_USER_OPERATION_
  // SETTINGS.md 10-5) — 상품 속성 비교(매입처/판매채널/HOMEZ 현재
  // 값) 목록·상세·해소. ai-proposals와 동일한 탭 목록 → 상세 패턴을
  // 그대로 따른다.
  // --------------------------------------------------

  let pacCurrentStatus = "BLOCKED";

  async function loadProductAttrComparison() {
    wirePacToolbarOnce();

    document.querySelectorAll(".pac-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.status === pacCurrentStatus);
    });

    const wrap = el("pac-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = pacCurrentStatus ? `?status_filter=${encodeURIComponent(pacCurrentStatus)}` : "";
      rows = await apiFetch(`/product-attribute-comparisons${qs}`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (!rows || rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("pac.empty_title"), HomezI18n.t("pac.empty_sub"));
      return;
    }

    const colProduct = HomezI18n.t("pac.col_product");
    const colStatus = HomezI18n.t("pac.col_status");
    const colCreated = HomezI18n.t("pac.col_created_at");
    const colResolved = HomezI18n.t("pac.col_resolved_at");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colProduct}</th><th>${colStatus}</th><th>${colCreated}</th><th>${colResolved}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" data-id="${r.id}" tabindex="0">
            <td data-label="${colProduct}">${escapeHtml(r.product_identifier)}</td>
            <td data-label="${colStatus}">${statusPillHtml(r.overall_status)}</td>
            <td data-label="${colCreated}">${fmtDate(r.created_at)}</td>
            <td data-label="${colResolved}">${r.resolved_at ? fmtDate(r.resolved_at) : "—"}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      const openIt = () => navigateTo("product-attr-comparison-detail", { id: Number(tr.dataset.id) });
      tr.addEventListener("click", openIt);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openIt();
        }
      });
    });
  }

  function wirePacToolbarOnce() {
    if (wirePacToolbarOnce._wired) return;
    wirePacToolbarOnce._wired = true;

    document.querySelectorAll(".pac-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        pacCurrentStatus = btn.dataset.status;
        loadProductAttrComparison();
      });
    });
  }

  let pacDetailCurrentId = null;

  async function loadProductAttrComparisonDetail(opts = {}) {
    const id = opts.id || pacDetailCurrentId;
    pacDetailCurrentId = id;
    wirePacDetailBackOnce();

    const body = el("pac-detail-body");
    body.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(body, HomezI18n.t("pac.empty_title"), "");
      return;
    }

    let run;
    try {
      run = await apiFetch(`/product-attribute-comparisons/${id}`);
    } catch (err) {
      renderErrorState(body, err);
      return;
    }

    renderPacDetail(body, run);
  }

  function wirePacDetailBackOnce() {
    if (wirePacDetailBackOnce._wired) return;
    wirePacDetailBackOnce._wired = true;
    el("pac-detail-back-btn").addEventListener("click", () => navigateTo("product-attr-comparison"));
  }

  function pacFieldLabel(fieldName) {
    const key = `pac.field_${String(fieldName || "").toLowerCase()}`;
    const translated = HomezI18n.t(key);
    return translated && translated !== key ? translated : fieldName;
  }

  function pacNeedsSelection(item) {
    return item.match_status !== "MATCHED";
  }

  // 매입처/판매채널/HOMEZ 현재 값 중 실제로 존재하는(중복 제거한)
  // 값만 선택지로 보여준다 — 아무 라디오도 기본 선택하지 않는다
  // (임의 기본값 금지, HOMEZ_USER_OPERATION_SETTINGS.md 10-5).
  function pacSelectionInputHtml(item) {
    const options = [item.supplier_value, item.sales_channel_value, item.homez_current_value]
      .filter((v, idx, arr) => v != null && v !== "" && arr.indexOf(v) === idx);

    const radios = options.map((v) => `
      <label><input type="radio" name="pac-radio-${item.id}" value="${escapeHtml(v)}"> ${escapeHtml(v)}</label>
    `).join("");

    return `
      <div class="pac-selection">
        ${radios || `<p class="field-hint">${escapeHtml(HomezI18n.t("pac.no_candidate_values"))}</p>`}
        <label>${escapeHtml(HomezI18n.t("pac.custom_value_label"))}
          <input type="text" class="pt-cc-form-input" id="pac-custom-${item.id}"></label>
      </div>
    `;
  }

  function renderPacDetail(body, run) {
    const resolved = !!run.resolved_at;

    const rowsHtml = run.items.map((item) => `
      <tr>
        <td data-label="${escapeHtml(HomezI18n.t("pac.col_field"))}">${escapeHtml(pacFieldLabel(item.field_name))}</td>
        <td data-label="${escapeHtml(HomezI18n.t("pac.col_supplier_value"))}">
          ${escapeHtml(item.supplier_value ?? "—")}
          ${item.supplier_source ? `<div class="field-hint">${escapeHtml(item.supplier_source)}${item.supplier_confirmed_at ? " · " + fmtDate(item.supplier_confirmed_at) : ""}</div>` : ""}
        </td>
        <td data-label="${escapeHtml(HomezI18n.t("pac.col_sales_channel_value"))}">
          ${escapeHtml(item.sales_channel_value ?? "—")}
          ${item.sales_channel_source ? `<div class="field-hint">${escapeHtml(item.sales_channel_source)}${item.sales_channel_confirmed_at ? " · " + fmtDate(item.sales_channel_confirmed_at) : ""}</div>` : ""}
        </td>
        <td data-label="${escapeHtml(HomezI18n.t("pac.col_homez_current_value"))}">
          ${escapeHtml(item.homez_current_value ?? "—")}
          ${item.homez_current_source ? `<div class="field-hint">${escapeHtml(item.homez_current_source)}${item.homez_current_confirmed_at ? " · " + fmtDate(item.homez_current_confirmed_at) : ""}</div>` : ""}
        </td>
        <td data-label="${escapeHtml(HomezI18n.t("pac.col_match_status"))}">${statusPillHtml(item.match_status)}</td>
        <td data-label="${escapeHtml(HomezI18n.t("pac.col_selection"))}">
          ${resolved
            ? escapeHtml(item.selected_value ?? "—")
            : (pacNeedsSelection(item) ? pacSelectionInputHtml(item) : "—")}
        </td>
      </tr>
    `).join("");

    body.innerHTML = `
      <div class="view-header">
        <h1>${escapeHtml(run.product_identifier)}</h1>
        ${statusPillHtml(run.overall_status)}
      </div>
      <div class="dash-card">
        <dl class="kv-list">
          <dt>${escapeHtml(HomezI18n.t("pac.col_created_at"))}</dt><dd>${fmtDate(run.created_at)}</dd>
          ${run.resolved_at ? `<dt>${escapeHtml(HomezI18n.t("pac.detail_resolved_at"))}</dt><dd>${fmtDate(run.resolved_at)}</dd>` : ""}
          ${run.resolution_note ? `<dt>${escapeHtml(HomezI18n.t("pac.detail_resolution_note"))}</dt><dd>${escapeHtml(run.resolution_note)}</dd>` : ""}
        </dl>
      </div>
      <table class="responsive-cards pac-compare-table">
        <thead><tr>
          <th>${escapeHtml(HomezI18n.t("pac.col_field"))}</th>
          <th>${escapeHtml(HomezI18n.t("pac.col_supplier_value"))}</th>
          <th>${escapeHtml(HomezI18n.t("pac.col_sales_channel_value"))}</th>
          <th>${escapeHtml(HomezI18n.t("pac.col_homez_current_value"))}</th>
          <th>${escapeHtml(HomezI18n.t("pac.col_match_status"))}</th>
          <th>${escapeHtml(HomezI18n.t("pac.col_selection"))}</th>
        </tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
      ${resolved ? "" : `
        <div class="detail-panel">
          <h2>${escapeHtml(HomezI18n.t("pac.resolve_heading"))}</h2>
          <p class="field-hint">${escapeHtml(HomezI18n.t("pac.resolve_hint"))}</p>
          <label class="field-label" for="pac-resolution-note">${escapeHtml(HomezI18n.t("pac.resolve_note_label"))}</label>
          <textarea id="pac-resolution-note" class="pt-cc-form-input" maxlength="500"></textarea>
          <p class="field-error" id="pac-resolve-error"></p>
          <div class="dialog-actions">
            <button type="button" class="btn btn-primary" id="pac-resolve-submit-btn">${escapeHtml(HomezI18n.t("pac.resolve_submit_btn"))}</button>
          </div>
        </div>
      `}
    `;

    if (resolved) return;

    const submitBtn = el("pac-resolve-submit-btn");
    submitBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const errEl = el("pac-resolve-error");
      errEl.textContent = "";

      const note = el("pac-resolution-note").value.trim();
      if (!note) {
        errEl.textContent = HomezI18n.t("pac.resolve_missing_note");
        return;
      }

      const selectedValues = {};
      let missingCount = 0;
      run.items.filter(pacNeedsSelection).forEach((item) => {
        const checkedRadio = document.querySelector(`input[name="pac-radio-${item.id}"]:checked`);
        const customInput = document.getElementById(`pac-custom-${item.id}`);
        const customValue = customInput ? customInput.value.trim() : "";
        const value = customValue || (checkedRadio ? checkedRadio.value : "");
        if (!value) {
          missingCount += 1;
        } else {
          selectedValues[item.id] = value;
        }
      });
      if (missingCount > 0) {
        errEl.textContent = HomezI18n.t("pac.resolve_missing_selection");
        return;
      }

      try {
        await apiFetch(`/product-attribute-comparisons/${run.id}/resolve`, {
          method: "POST",
          body: JSON.stringify({ resolution_note: note, selected_values: selectedValues }),
        });
        toast(HomezI18n.t("pac.resolve_success"), "success");
        loadProductAttrComparisonDetail({ id: run.id });
      } catch (err) {
        errEl.textContent = (err && err.message) || HomezI18n.t("pac.action_error");
      }
    }));
  }

  // --------------------------------------------------
  // 2026-09-15 전면 감사 후속(Phase 9J, HOMEZ_USER_OPERATION_
  // SETTINGS.md 10-18) — 리콜/판매중지 확인 차단 목록·상세·해제.
  // --------------------------------------------------

  let rcbCurrentStatus = "BLOCKED";

  async function loadRecallBlocks() {
    wireRcbToolbarOnce();

    document.querySelectorAll(".rcb-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.status === rcbCurrentStatus);
    });

    const wrap = el("rcb-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = rcbCurrentStatus ? `?status_filter=${encodeURIComponent(rcbCurrentStatus)}` : "";
      rows = await apiFetch(`/recall-notices/product-blocks${qs}`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (!rows || rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("rcb.empty_title"), HomezI18n.t("rcb.empty_sub"));
      return;
    }

    const colProduct = HomezI18n.t("rcb.col_product");
    const colReason = HomezI18n.t("rcb.col_reason");
    const colStatus = HomezI18n.t("rcb.col_status");
    const colBlockedAt = HomezI18n.t("rcb.col_blocked_at");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colProduct}</th><th>${colReason}</th><th>${colStatus}</th><th>${colBlockedAt}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" data-id="${r.id}" tabindex="0">
            <td data-label="${colProduct}">${escapeHtml(r.product_identifier)}</td>
            <td data-label="${colReason}">${escapeHtml(r.reason)}</td>
            <td data-label="${colStatus}">${statusPillHtml(r.status)}</td>
            <td data-label="${colBlockedAt}">${fmtDate(r.blocked_at)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      const openIt = () => navigateTo("recall-blocks-detail", { id: Number(tr.dataset.id) });
      tr.addEventListener("click", openIt);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openIt();
        }
      });
    });
  }

  function wireRcbToolbarOnce() {
    if (wireRcbToolbarOnce._wired) return;
    wireRcbToolbarOnce._wired = true;

    document.querySelectorAll(".rcb-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        rcbCurrentStatus = btn.dataset.status;
        loadRecallBlocks();
      });
    });
  }

  let rcbDetailCurrentId = null;

  async function loadRecallBlocksDetail(opts = {}) {
    const id = opts.id || rcbDetailCurrentId;
    rcbDetailCurrentId = id;
    wireRcbDetailBackOnce();

    const body = el("rcb-detail-body");
    body.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(body, HomezI18n.t("rcb.empty_title"), "");
      return;
    }

    let block;
    try {
      block = await apiFetch(`/recall-notices/product-blocks/${id}`);
    } catch (err) {
      renderErrorState(body, err);
      return;
    }

    renderRcbDetail(body, block);
  }

  function wireRcbDetailBackOnce() {
    if (wireRcbDetailBackOnce._wired) return;
    wireRcbDetailBackOnce._wired = true;
    el("rcb-detail-back-btn").addEventListener("click", () => navigateTo("recall-blocks"));
  }

  function renderRcbDetail(body, block) {
    const isBlocked = block.status === "BLOCKED";

    body.innerHTML = `
      <div class="view-header">
        <h1>${escapeHtml(block.product_identifier)}</h1>
        ${statusPillHtml(block.status)}
      </div>
      <div class="dash-card">
        <dl class="kv-list">
          <dt>${escapeHtml(HomezI18n.t("rcb.col_reason"))}</dt><dd>${escapeHtml(block.reason)}</dd>
          <dt>${escapeHtml(HomezI18n.t("rcb.col_blocked_at"))}</dt><dd>${fmtDate(block.blocked_at)}</dd>
          ${block.unblock_approved_at ? `<dt>${escapeHtml(HomezI18n.t("rcb.detail_unblocked_at"))}</dt><dd>${fmtDate(block.unblock_approved_at)}</dd>` : ""}
          ${block.unblock_justification ? `<dt>${escapeHtml(HomezI18n.t("rcb.detail_justification"))}</dt><dd>${escapeHtml(block.unblock_justification)}</dd>` : ""}
        </dl>
      </div>
      ${!isBlocked ? "" : `
        <div class="detail-panel">
          <h2>${escapeHtml(HomezI18n.t("rcb.unblock_heading"))}</h2>
          <p class="field-hint">${escapeHtml(HomezI18n.t("rcb.unblock_hint"))}</p>
          <label class="field-label" for="rcb-justification">${escapeHtml(HomezI18n.t("rcb.unblock_justification_label"))}</label>
          <textarea id="rcb-justification" class="pt-cc-form-input" maxlength="500"></textarea>
          <p class="field-error" id="rcb-unblock-error"></p>
          <div class="dialog-actions">
            <button type="button" class="btn btn-danger" id="rcb-unblock-submit-btn">${escapeHtml(HomezI18n.t("rcb.unblock_submit_btn"))}</button>
          </div>
        </div>
      `}
    `;

    if (!isBlocked) return;

    const submitBtn = el("rcb-unblock-submit-btn");
    submitBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const errEl = el("rcb-unblock-error");
      errEl.textContent = "";

      const justification = el("rcb-justification").value.trim();
      if (!justification) {
        errEl.textContent = HomezI18n.t("rcb.unblock_missing_justification");
        return;
      }

      const { confirmed } = await confirmDialog({
        title: HomezI18n.t("rcb.unblock_confirm_title"),
        body: HomezI18n.t("rcb.unblock_confirm_body"),
        okLabel: HomezI18n.t("rcb.unblock_submit_btn"),
      });
      if (!confirmed) return;

      try {
        await apiFetch(`/recall-notices/product-blocks/${block.id}/unblock`, {
          method: "POST",
          body: JSON.stringify({ justification }),
        });
        toast(HomezI18n.t("rcb.unblock_success"), "success");
        loadRecallBlocksDetail({ id: block.id });
      } catch (err) {
        errEl.textContent = (err && err.message) || HomezI18n.t("rcb.action_error");
      }
    }));
  }

  // --------------------------------------------------
  // 운영 우선순위
  // --------------------------------------------------

  const AIP_URGENCY_LABEL_KEYS = {
    HIGH: "ai_proposals.risk_high",
    MEDIUM: "ai_proposals.risk_medium",
  };

  async function loadOperationsPriorities() {
    const wrap = el("aip-priorities-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let data;
    try {
      data = await apiFetch("/orchestration/priorities");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    const items = data.items || [];
    if (items.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("operations_priorities.empty_title"), HomezI18n.t("operations_priorities.empty_sub"));
      return;
    }

    wrap.innerHTML = `
      <ul class="dash-todo-list">
        ${items.map((i) => `
          <li>
            <span class="${aipRiskClass(i.urgency)}">${escapeHtml(AIP_URGENCY_LABEL_KEYS[i.urgency] ? HomezI18n.t(AIP_URGENCY_LABEL_KEYS[i.urgency]) : i.urgency)}</span>
            ${escapeHtml(i.label)}
          </li>
        `).join("")}
      </ul>
    `;
  }

  // --------------------------------------------------
  // 주문 예외 검토
  // --------------------------------------------------

  async function loadOrderExceptions() {
    renderSharedEstopBanner(el("oex-estop-banner-slot"));

    const wrap = el("oex-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let data;
    try {
      data = await apiFetch("/orders/exception-analysis");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    renderReviewableExceptionTable({
      wrap,
      items: data.exceptions || [],
      typeField: "exception_type",
      emptyTitleKey: "order_exceptions.empty_title",
      emptySubKey: "order_exceptions.empty_sub",
      createUrl: "/orders/exception-analysis/review-actions",
      createBodyKey: "exception_type",
      idempotencyPrefix: "oex",
      onCreated: loadOrderExceptions,
    });
  }

  // --------------------------------------------------
  // 정산 차이 검토
  // --------------------------------------------------

  async function loadSettlementDifferences() {
    renderSharedEstopBanner(el("sdf-estop-banner-slot"));

    const wrap = el("sdf-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let data;
    try {
      data = await apiFetch("/settlements/difference-analysis");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    renderReviewableExceptionTable({
      wrap,
      items: data.differences || [],
      typeField: "difference_type",
      emptyTitleKey: "settlement_differences.empty_title",
      emptySubKey: "settlement_differences.empty_sub",
      createUrl: "/settlements/difference-analysis/review-actions",
      createBodyKey: "difference_type",
      idempotencyPrefix: "sdf",
      onCreated: loadSettlementDifferences,
    });
  }

  // 주문 예외·정산 차이 둘 다 같은 모양(urgency/target_entity/evidence/
  // elapsed_hours/recommended_action/execution_allowed)이라 렌더링·
  // "제안 생성" 버튼 로직을 공유한다 — 두 화면에서 각자 다시 만들지
  // 않는다.
  function renderReviewableExceptionTable({
    wrap, items, typeField, emptyTitleKey, emptySubKey,
    createUrl, createBodyKey, idempotencyPrefix, onCreated,
  }) {
    if (!items || items.length === 0) {
      renderEmptyState(wrap, HomezI18n.t(emptyTitleKey), HomezI18n.t(emptySubKey));
      return;
    }

    const colType = HomezI18n.t("order_exceptions.col_type");
    const colUrgency = HomezI18n.t("order_exceptions.col_urgency");
    const colEvidence = HomezI18n.t("order_exceptions.col_evidence");
    const colAction = "";

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colType}</th><th>${colUrgency}</th><th>${colEvidence}</th><th>${colAction}</th></tr></thead>
        <tbody>${items.map((it, idx) => `
          <tr>
            <td data-label="${colType}">${escapeHtml(it[typeField])}</td>
            <td data-label="${colUrgency}"><span class="${aipRiskClass(it.urgency)}">${escapeHtml(it.urgency === "HIGH" ? HomezI18n.t("ai_proposals.risk_high") : HomezI18n.t("ai_proposals.risk_medium"))}</span></td>
            <td data-label="${colEvidence}">${escapeHtml(it.evidence)}</td>
            <td>
              ${it.execution_allowed
                ? `<button class="btn btn-primary btn-sm" data-create-idx="${idx}">${escapeHtml(HomezI18n.t("order_exceptions.create_proposal_btn"))}</button>`
                : `<span class="field-error">${escapeHtml(HomezI18n.t("order_exceptions.execution_blocked"))}</span>`}
            </td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("[data-create-idx]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const it = items[Number(btn.dataset.createIdx)];
        try {
          await apiFetch(createUrl, {
            method: "POST",
            body: JSON.stringify({
              [createBodyKey]: it[typeField],
              target_entity: it.target_entity,
              idempotency_key: `${idempotencyPrefix}:${it.target_entity}:${Date.now()}`,
            }),
          });
          toast(HomezI18n.t("order_exceptions.create_proposal_success"), "success");
          if (onCreated) onCreated();
        } catch (err) {
          toast((err && err.message) || HomezI18n.t("order_exceptions.create_proposal_error"), "error");
        }
      }));
    });
  }

  // --------------------------------------------------
  // Gate RP-1(2026-08-22) — 구매 실행 연결. 소비자 쇼핑몰 로그인
  // 자동화는 이 화면에 없다 — "네이버 계정 비밀번호 입력" 같은 UI를
  // 만들지 않는다(지시문 H). 실제 구매 실행(place_order)은 공식
  // API/서면 계약 Provider로만 연결되며, 실 계약 전에는 항상
  // 차단된다(LIVE_INPUT_REQUIRED) — 이 화면은 그 차단을 있는
  // 그대로 보여준다.
  // --------------------------------------------------

  const RP_PROVIDER_LABEL_KEYS = {
    FAKE: "retail_purchase.provider_fake",
    PROCUREMENT_GATEWAY: "retail_purchase.provider_procurement_gateway",
    OFFICIAL_MARKETPLACE: "retail_purchase.provider_official_marketplace",
    CORPORATE_PROCUREMENT: "retail_purchase.provider_corporate_procurement",
    VIRTUAL_CARD: "retail_purchase.provider_virtual_card",
    DIRECT_SUPPLIER: "retail_purchase.provider_direct_supplier",
  };

  const RP_CONNECTION_STATUS_LABEL_KEYS = {
    TEST_ONLY: "retail_purchase.status_test_only",
    CONTRACT_REQUIRED: "retail_purchase.status_contract_required",
    CREDENTIAL_REQUIRED: "retail_purchase.status_credential_required",
    CONNECTED: "retail_purchase.status_connected",
    REAUTH_REQUIRED: "retail_purchase.status_reauth_required",
    AUTO_PURCHASE_ENABLED: "retail_purchase.status_auto_purchase_enabled",
    AUTO_PURCHASE_DISABLED: "retail_purchase.status_auto_purchase_disabled",
    OUTAGE: "retail_purchase.status_outage",
    INACTIVE: "retail_purchase.status_inactive",
  };

  const RP_ORDER_STATUS_LABEL_KEYS = {
    PROPOSED: "retail_purchase.order_status_proposed",
    POLICY_CHECKED: "retail_purchase.order_status_policy_checked",
    BUDGET_RESERVED: "retail_purchase.order_status_budget_reserved",
    QUOTED: "retail_purchase.order_status_quoted",
    PLACING: "retail_purchase.order_status_placing",
    ORDERED: "retail_purchase.order_status_ordered",
    TRACKING_PENDING: "retail_purchase.order_status_tracking_pending",
    SHIPPED: "retail_purchase.order_status_shipped",
    DELIVERED: "retail_purchase.order_status_delivered",
    CANCEL_PENDING: "retail_purchase.order_status_cancel_pending",
    CANCELLED: "retail_purchase.order_status_cancelled",
    REFUND_PENDING: "retail_purchase.order_status_refund_pending",
    REFUNDED: "retail_purchase.order_status_refunded",
    BLOCKED: "retail_purchase.order_status_blocked",
    FAILED: "retail_purchase.order_status_failed",
    UNCERTAIN: "retail_purchase.order_status_uncertain",
  };

  function rpOrderStatusLabel(s) {
    const key = RP_ORDER_STATUS_LABEL_KEYS[s];
    return key ? HomezI18n.t(key) : s;
  }

  let rpCurrentStatus = "";
  let rpCurrentProviders = [];
  let rpCurrentEstopActive = false;

  async function loadRetailPurchase() {
    wireRetailPurchaseToolbarOnce();
    rpCurrentEstopActive = await renderSharedEstopBanner(el("rp-estop-banner-slot"));

    document.querySelectorAll(".rp-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.status === rpCurrentStatus);
    });

    await Promise.all([
      rpLoadProviders(),
      rpLoadPolicy(),
      rpLoadOrders(),
    ]);
    rpRenderNotifications();
  }

  // --------------------------------------------------
  // 알림 패널(작업 4) — 별도 알림 이벤트 저장소를 새로 만들지 않고,
  // 이미 존재하는 Provider 연결상태·주문 목록에서 알림성 항목을
  // 읽기 시점에 파생시킨다(automation_safety.EligibilityService와
  // 동일한 "읽기 시점 판단" 철학). 비밀번호·Credential·결제정보·
  // 전체 배송주소는 절대 포함하지 않는다(주문 ID·상품 식별자·상태
  // 문구만 사용).
  // --------------------------------------------------

  function rpMatchTierFromConfidence(confidence) {
    if (confidence === null || confidence === undefined) return null;
    if (confidence >= 0.98) return "AUTO_CANDIDATE";
    if (confidence >= 0.90) return "NEEDS_REVIEW";
    return "BLOCKED";
  }

  async function rpRenderNotifications() {
    const wrap = el("rp-notifications-wrap");
    if (!wrap) return;
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let orders = [];
    try {
      orders = await apiFetch("/retail-purchase");
    } catch (_err) {
      // 알림 패널 실패가 화면 전체를 깨뜨리지 않는다 — 빈 목록으로 대체.
    }

    const items = [];

    if (rpCurrentEstopActive) {
      items.push({
        kind: "estop",
        label: HomezI18n.t("retail_purchase.notif_estop_active"),
        orderId: null,
      });
    }

    (rpCurrentProviders || []).forEach((p) => {
      const providerLabel = RP_PROVIDER_LABEL_KEYS[p.provider_code]
        ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[p.provider_code]) : p.provider_code;
      if (p.connection_status === "OUTAGE") {
        items.push({
          kind: "provider_outage",
          label: HomezI18n.t("retail_purchase.notif_provider_outage", { provider: providerLabel }),
          orderId: null,
        });
      }
      if (p.connection_status === "REAUTH_REQUIRED") {
        items.push({
          kind: "reauth_required",
          label: HomezI18n.t("retail_purchase.notif_reauth_required", { provider: providerLabel }),
          orderId: null,
        });
      }
      if (p.connection_status === "CREDENTIAL_REQUIRED") {
        items.push({
          kind: "credential_required",
          label: HomezI18n.t("retail_purchase.notif_credential_required", { provider: providerLabel }),
          orderId: null,
        });
      }
    });

    (orders || []).forEach((o) => {
      if (o.status === "BLOCKED") {
        items.push({
          kind: "policy_blocked",
          label: HomezI18n.t("retail_purchase.notif_policy_blocked", { product: o.external_product_id, reason: o.failure_code || "" }),
          orderId: o.id,
        });
      } else if (o.status === "FAILED") {
        const reason = o.failure_code || "";
        const kind = reason === "PRICE_CHANGED" ? "price_changed"
          : reason === "OUT_OF_STOCK" ? "out_of_stock"
          : reason === "BUDGET_INSUFFICIENT" ? "budget_insufficient"
          : "purchase_failed";
        const labelKey = kind === "price_changed" ? "retail_purchase.notif_price_changed"
          : kind === "out_of_stock" ? "retail_purchase.notif_out_of_stock"
          : kind === "budget_insufficient" ? "retail_purchase.notif_budget_insufficient"
          : "retail_purchase.notif_purchase_failed";
        items.push({
          kind, label: HomezI18n.t(labelKey, { product: o.external_product_id, reason }),
          orderId: o.id,
        });
      } else if (o.status === "UNCERTAIN") {
        items.push({
          kind: "result_uncertain",
          label: HomezI18n.t("retail_purchase.notif_result_uncertain", { product: o.external_product_id }),
          orderId: o.id,
        });
      } else if (o.status === "ORDERED") {
        items.push({
          kind: "purchase_success",
          label: HomezI18n.t("retail_purchase.notif_purchase_success", { product: o.external_product_id }),
          orderId: o.id,
        });
      } else if (o.status === "TRACKING_PENDING" || o.status === "SHIPPED" || o.status === "DELIVERED") {
        items.push({
          kind: "shipping_update",
          label: HomezI18n.t("retail_purchase.notif_shipping_update", { product: o.external_product_id, status: rpOrderStatusLabel(o.status) }),
          orderId: o.id,
        });
      } else if (o.status === "POLICY_CHECKED") {
        items.push({
          kind: "approval_needed",
          label: HomezI18n.t("retail_purchase.notif_approval_needed", { product: o.external_product_id }),
          orderId: o.id,
        });
      }

      if (rpMatchTierFromConfidence(o.match_confidence) === "NEEDS_REVIEW") {
        items.push({
          kind: "match_uncertain",
          label: HomezI18n.t("retail_purchase.notif_match_uncertain", { product: o.external_product_id }),
          orderId: o.id,
        });
      }
    });

    if (items.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("retail_purchase.notifications_empty"), "");
      return;
    }

    wrap.innerHTML = `
      <ul class="dash-todo-list">
        ${items.map((it, idx) => `
          <li class="${it.orderId ? "row-clickable" : ""}" data-idx="${idx}" ${it.orderId ? 'tabindex="0"' : ""}>${escapeHtml(it.label)}</li>
        `).join("")}
      </ul>
    `;

    wrap.querySelectorAll("li[data-idx]").forEach((li) => {
      const it = items[Number(li.dataset.idx)];
      if (!it.orderId) return;
      const go = () => navigateTo("retail-purchase-detail", { id: it.orderId });
      li.addEventListener("click", go);
      li.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          go();
        }
      });
    });
  }

  async function rpLoadProviders() {
    const wrap = el("rp-providers-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let providers;
    try {
      providers = await apiFetch("/retail-purchase/providers");
      rpCurrentProviders = providers;
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    wrap.innerHTML = `
      <ul class="dash-todo-list">
        ${providers.map((p) => `
          <li>
            <strong>${escapeHtml(RP_PROVIDER_LABEL_KEYS[p.provider_code] ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[p.provider_code]) : p.provider_code)}</strong>
            — ${escapeHtml(RP_CONNECTION_STATUS_LABEL_KEYS[p.connection_status] ? HomezI18n.t(RP_CONNECTION_STATUS_LABEL_KEYS[p.connection_status]) : p.connection_status)}
            <div class="field-hint">${escapeHtml(HomezI18n.t("retail_purchase.capabilities_label"))}: ${p.capabilities.map(escapeHtml).join(", ") || "—"}</div>
          </li>
        `).join("")}
      </ul>
    `;
  }

  function rpFormatPolicySummary(policy) {

    const fmtAmount = (v) => (v === null || v === undefined ? HomezI18n.t("retail_purchase.no_limit") : escapeHtml(String(v)));
    const fmtDays = (v) => (v === null || v === undefined ? HomezI18n.t("retail_purchase.no_limit") : `${escapeHtml(String(v))}${HomezI18n.t("retail_purchase.days_suffix")}`);
    const fmtPercent = (v) => (v === null || v === undefined ? HomezI18n.t("retail_purchase.no_limit") : `${escapeHtml(String(v * 100))}%`);
    const providerLabels = policy.allowed_provider_codes.length
      ? policy.allowed_provider_codes.map((c) => (RP_PROVIDER_LABEL_KEYS[c] ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[c]) : c)).join(", ")
      : HomezI18n.t("retail_purchase.no_provider_allowed");

    return `
      <dl class="detail-grid">
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_allowed_providers"))}</dt><dd>${escapeHtml(providerLabels)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_min_net_profit"))}</dt><dd>${fmtAmount(policy.min_net_profit)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_min_margin_rate"))}</dt><dd>${fmtPercent(policy.min_margin_rate)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_max_purchase_price"))}</dt><dd>${fmtAmount(policy.max_purchase_price)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_max_price_increase_rate"))}</dt><dd>${fmtPercent(policy.max_price_increase_rate)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_max_delivery_days"))}</dt><dd>${fmtDays(policy.max_delivery_days)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_require_return_allowed"))}</dt><dd>${policy.require_return_allowed ? HomezI18n.t("common.yes") : HomezI18n.t("common.no")}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_min_seller_trust"))}</dt><dd>${escapeHtml(String(policy.min_seller_trust_score))}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_per_order_max"))}</dt><dd>${fmtAmount(policy.per_order_max_amount)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_daily_limit"))}</dt><dd>${fmtAmount(policy.daily_purchase_limit_amount)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_monthly_budget"))}</dt><dd>${fmtAmount(policy.monthly_purchase_budget_amount)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_max_concurrent"))}</dt><dd>${fmtAmount(policy.max_concurrent_orders)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_min_match_confidence"))}</dt><dd>${escapeHtml(String(policy.min_match_confidence))}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_max_quantity_per_product"))}</dt><dd>${fmtAmount(policy.max_quantity_per_product)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_auto_execute_enabled"))}</dt><dd>${policy.auto_execute_enabled ? HomezI18n.t("common.yes") : HomezI18n.t("common.no")}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_approval_required_amount_threshold"))}</dt><dd>${fmtAmount(policy.approval_required_amount_threshold)}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_uncertain_handling_policy"))}</dt><dd>${escapeHtml(HomezI18n.t(policy.uncertain_handling_policy === "AUTO_RELEASE_AFTER_TIMEOUT" ? "retail_purchase.uncertain_policy_auto_release" : "retail_purchase.uncertain_policy_hold_indefinitely"))}</dd>
        <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_uncertain_auto_release_after_hours"))}</dt><dd>${fmtAmount(policy.uncertain_auto_release_after_hours)}</dd>
      </dl>
    `;
  }

  let rpCurrentPolicy = null;

  async function rpLoadPolicy() {
    const wrap = el("rp-policy-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    try {
      rpCurrentPolicy = await apiFetch("/retail-purchase/policy");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    wrap.innerHTML = rpFormatPolicySummary(rpCurrentPolicy);
  }

  async function rpLoadOrders() {
    const wrap = el("rp-orders-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = rpCurrentStatus ? `?status=${encodeURIComponent(rpCurrentStatus)}` : "";
      rows = await apiFetch(`/retail-purchase${qs}`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (!rows || rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("retail_purchase.empty_title"), HomezI18n.t("retail_purchase.empty_sub"));
      return;
    }

    const colProduct = HomezI18n.t("retail_purchase.col_product");
    const colProvider = HomezI18n.t("retail_purchase.col_provider");
    const colStatus = HomezI18n.t("ai_proposals.col_status");
    const colMatch = HomezI18n.t("retail_purchase.col_match_confidence");
    const colAmount = HomezI18n.t("retail_purchase.col_amount");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colProduct}</th><th>${colProvider}</th><th>${colMatch}</th><th>${colStatus}</th><th>${colAmount}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" data-id="${r.id}" tabindex="0">
            <td data-label="${colProduct}">${escapeHtml(r.external_product_id)}</td>
            <td data-label="${colProvider}">${escapeHtml(RP_PROVIDER_LABEL_KEYS[r.provider_code] ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[r.provider_code]) : r.provider_code)}</td>
            <td data-label="${colMatch}"><span class="${riskClass(r.match_confidence)}">${r.match_confidence === null || r.match_confidence === undefined ? "—" : Math.round(r.match_confidence * 100) + "%"}</span></td>
            <td data-label="${colStatus}">${escapeHtml(rpOrderStatusLabel(r.status))}</td>
            <td data-label="${colAmount}">${r.actual_amount === null || r.actual_amount === undefined ? (r.expected_amount === null || r.expected_amount === undefined ? "—" : escapeHtml(String(r.expected_amount))) : escapeHtml(String(r.actual_amount))}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      const openIt = () => navigateTo("retail-purchase-detail", { id: Number(tr.dataset.id) });
      tr.addEventListener("click", openIt);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openIt();
        }
      });
    });
  }

  function wireRetailPurchaseToolbarOnce() {
    if (wireRetailPurchaseToolbarOnce._wired) return;
    wireRetailPurchaseToolbarOnce._wired = true;

    document.querySelectorAll(".rp-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        rpCurrentStatus = btn.dataset.status;
        rpLoadOrders();
      });
    });

    el("rp-policy-edit-btn").addEventListener("click", rpOpenPolicyEditDialog);
    el("rp-new-request-btn").addEventListener("click", rpOpenNewRequestDialog);
  }

  function rpOpenPolicyEditDialog() {
    if (!rpCurrentPolicy) return;

    const dialog = el("rp-policy-edit-dialog");
    const checkboxWrap = el("rp-policy-provider-checkboxes");
    const knownCodes = Object.keys(RP_PROVIDER_LABEL_KEYS);

    checkboxWrap.innerHTML = knownCodes.map((code) => `
      <label class="field-inline">
        <input type="checkbox" class="rp-provider-checkbox" value="${code}" ${rpCurrentPolicy.allowed_provider_codes.includes(code) ? "checked" : ""}>
        ${escapeHtml(RP_PROVIDER_LABEL_KEYS[code] ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[code]) : code)}
      </label>
    `).join(" ");

    el("rp-policy-min-net-profit").value = rpCurrentPolicy.min_net_profit;
    el("rp-policy-min-margin-rate").value = rpCurrentPolicy.min_margin_rate == null ? "" : rpCurrentPolicy.min_margin_rate * 100;
    el("rp-policy-max-purchase-price").value = rpCurrentPolicy.max_purchase_price ?? "";
    el("rp-policy-max-price-increase-rate").value = rpCurrentPolicy.max_price_increase_rate == null ? "" : rpCurrentPolicy.max_price_increase_rate * 100;
    el("rp-policy-max-delivery-days").value = rpCurrentPolicy.max_delivery_days ?? "";
    el("rp-policy-require-return-allowed").checked = !!rpCurrentPolicy.require_return_allowed;
    el("rp-policy-min-seller-trust").value = rpCurrentPolicy.min_seller_trust_score;
    el("rp-policy-per-order-max").value = rpCurrentPolicy.per_order_max_amount ?? "";
    el("rp-policy-daily-limit").value = rpCurrentPolicy.daily_purchase_limit_amount ?? "";
    el("rp-policy-monthly-budget").value = rpCurrentPolicy.monthly_purchase_budget_amount ?? "";
    el("rp-policy-max-concurrent").value = rpCurrentPolicy.max_concurrent_orders ?? "";
    el("rp-policy-min-match-confidence").value = rpCurrentPolicy.min_match_confidence;
    el("rp-policy-max-quantity-per-product").value = rpCurrentPolicy.max_quantity_per_product ?? "";
    el("rp-policy-auto-execute-enabled").checked = !!rpCurrentPolicy.auto_execute_enabled;
    el("rp-policy-approval-required-amount-threshold").value = rpCurrentPolicy.approval_required_amount_threshold ?? "";
    el("rp-policy-uncertain-handling-policy").value = rpCurrentPolicy.uncertain_handling_policy;
    el("rp-policy-uncertain-auto-release-after-hours").value = rpCurrentPolicy.uncertain_auto_release_after_hours ?? "";
    el("rp-policy-current-password").value = "";
    el("rp-policy-edit-error").textContent = "";
    el("rp-policy-risk-warning").hidden = true;
    el("rp-policy-risk-warning").textContent = "";

    const saveBtn = el("rp-policy-edit-save");
    const cancelBtn = el("rp-policy-edit-cancel");

    // 위험한 정책 완화 감지(작업 2) — 별도 서버 비교 없이, 다이얼로그를
    // 연 시점의 rpCurrentPolicy(방금 서버에서 받은 최신값)를 기준으로
    // 지금 입력값이 어느 방향으로 바뀌었는지 클라이언트에서 비교한다.
    // 차단하지 않는다 — 운영자가 의도한 완화일 수 있으므로 경고만
    // 표시하고 저장 자체는 막지 않는다.
    function detectRiskyRelaxation() {
      const numOrNull = (elId) => {
        const raw = el(elId).value;
        return raw === "" ? null : Number(raw);
      };
      const risks = [];

      if (numOrNull("rp-policy-min-net-profit") !== null
        && numOrNull("rp-policy-min-net-profit") < rpCurrentPolicy.min_net_profit) {
        risks.push(HomezI18n.t("retail_purchase.risk_min_net_profit_lowered"));
      }
      if (numOrNull("rp-policy-min-margin-rate") !== null
        && numOrNull("rp-policy-min-margin-rate") < rpCurrentPolicy.min_margin_rate * 100) {
        risks.push(HomezI18n.t("retail_purchase.risk_min_margin_rate_lowered"));
      }
      if (numOrNull("rp-policy-max-purchase-price") === null
        && rpCurrentPolicy.max_purchase_price !== null) {
        risks.push(HomezI18n.t("retail_purchase.risk_max_purchase_price_removed"));
      }
      if (numOrNull("rp-policy-max-price-increase-rate") !== null
        && numOrNull("rp-policy-max-price-increase-rate") > rpCurrentPolicy.max_price_increase_rate * 100) {
        risks.push(HomezI18n.t("retail_purchase.risk_price_increase_rate_raised"));
      }
      if (numOrNull("rp-policy-min-seller-trust") !== null
        && numOrNull("rp-policy-min-seller-trust") < rpCurrentPolicy.min_seller_trust_score) {
        risks.push(HomezI18n.t("retail_purchase.risk_min_seller_trust_lowered"));
      }
      if (numOrNull("rp-policy-min-match-confidence") !== null
        && numOrNull("rp-policy-min-match-confidence") < rpCurrentPolicy.min_match_confidence) {
        risks.push(HomezI18n.t("retail_purchase.risk_min_match_confidence_lowered"));
      }
      if (!el("rp-policy-require-return-allowed").checked && rpCurrentPolicy.require_return_allowed) {
        risks.push(HomezI18n.t("retail_purchase.risk_return_required_disabled"));
      }
      if (el("rp-policy-auto-execute-enabled").checked && !rpCurrentPolicy.auto_execute_enabled) {
        risks.push(HomezI18n.t("retail_purchase.risk_auto_execute_enabled"));
      }
      if (numOrNull("rp-policy-approval-required-amount-threshold") === null
        && rpCurrentPolicy.approval_required_amount_threshold !== null) {
        risks.push(HomezI18n.t("retail_purchase.risk_approval_threshold_removed"));
      }
      const newProviderCodes = Array.from(document.querySelectorAll(".rp-provider-checkbox:checked")).map((cb) => cb.value);
      const addedProviders = newProviderCodes.filter((c) => !rpCurrentPolicy.allowed_provider_codes.includes(c));
      if (addedProviders.length > 0) {
        risks.push(HomezI18n.t("retail_purchase.risk_provider_added", { providers: addedProviders.join(", ") }));
      }

      const warnEl = el("rp-policy-risk-warning");
      if (risks.length > 0) {
        warnEl.hidden = false;
        warnEl.textContent = `${HomezI18n.t("retail_purchase.risk_warning_prefix")}: ${risks.join(" / ")}`;
      } else {
        warnEl.hidden = true;
        warnEl.textContent = "";
      }
    }

    checkboxWrap.querySelectorAll(".rp-provider-checkbox").forEach((cb) => {
      cb.addEventListener("change", detectRiskyRelaxation);
    });
    el("rp-policy-edit-dialog").querySelectorAll("input, select").forEach((input) => {
      input.addEventListener("input", detectRiskyRelaxation);
      input.addEventListener("change", detectRiskyRelaxation);
    });

    function cleanup() {
      saveBtn.removeEventListener("click", onSave);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSave() {
      const currentPassword = el("rp-policy-current-password").value;
      if (!currentPassword) {
        el("rp-policy-edit-error").textContent = HomezI18n.t("ma.error_current_password_required");
        return;
      }

      const numOrNull = (elId) => {
        const raw = el(elId).value;
        return raw === "" ? null : Number(raw);
      };

      saveBtn.disabled = true;
      try {
        const recentAuth = await apiFetch("/auth/recent-auth", {
          method: "POST",
          body: JSON.stringify({ current_password: currentPassword }),
        }, { skipAuthHandling: true });

        const allowedProviderCodes = Array.from(
          document.querySelectorAll(".rp-provider-checkbox:checked"),
        ).map((cb) => cb.value);

        await apiFetch("/retail-purchase/policy", {
          method: "PUT",
          headers: { "X-Recent-Auth-Token": recentAuth.recent_auth_token },
          body: JSON.stringify({
            allowed_provider_codes: allowedProviderCodes,
            min_net_profit: numOrNull("rp-policy-min-net-profit"),
            min_margin_rate: (() => { const v = numOrNull("rp-policy-min-margin-rate"); return v === null ? null : v / 100; })(),
            max_purchase_price: numOrNull("rp-policy-max-purchase-price"),
            max_price_increase_rate: (() => { const v = numOrNull("rp-policy-max-price-increase-rate"); return v === null ? null : v / 100; })(),
            max_delivery_days: numOrNull("rp-policy-max-delivery-days"),
            require_return_allowed: el("rp-policy-require-return-allowed").checked,
            min_seller_trust_score: numOrNull("rp-policy-min-seller-trust"),
            per_order_max_amount: numOrNull("rp-policy-per-order-max"),
            daily_purchase_limit_amount: numOrNull("rp-policy-daily-limit"),
            monthly_purchase_budget_amount: numOrNull("rp-policy-monthly-budget"),
            max_concurrent_orders: numOrNull("rp-policy-max-concurrent"),
            min_match_confidence: numOrNull("rp-policy-min-match-confidence"),
            max_quantity_per_product: numOrNull("rp-policy-max-quantity-per-product"),
            auto_execute_enabled: el("rp-policy-auto-execute-enabled").checked,
            approval_required_amount_threshold: numOrNull("rp-policy-approval-required-amount-threshold"),
            uncertain_handling_policy: el("rp-policy-uncertain-handling-policy").value,
            uncertain_auto_release_after_hours: numOrNull("rp-policy-uncertain-auto-release-after-hours"),
          }),
        });

        cleanup();
        toast(HomezI18n.t("retail_purchase.policy_save_success"), "success");
        rpLoadPolicy();
      } catch (err) {
        saveBtn.disabled = false;
        if (err instanceof ApiError && err.status === 401) {
          el("rp-policy-edit-error").textContent = HomezI18n.t("ma.error_invalid_password");
        } else if (err instanceof ApiError && err.status === 409) {
          el("rp-policy-edit-error").textContent = HomezI18n.t("retail_purchase.policy_save_conflict");
        } else if (err instanceof ApiError && err.status === 422) {
          el("rp-policy-edit-error").textContent = HomezI18n.t("retail_purchase.policy_save_invalid_input");
        } else {
          el("rp-policy-edit-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
        }
      }
    }

    saveBtn.disabled = false;
    saveBtn.addEventListener("click", onSave);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  // --------------------------------------------------
  // 새 구매 요청(장바구니형 검토, 2026-08-22 14차 지시 작업 3) —
  // 상품 후보 조회 → 동일상품 비교 → 매입비·마진 미리보기 →
  // 구매 요청 생성까지 실제 API로 연결한다. RetailPurchaseOrder는
  // 마지막 "구매 요청 생성" 클릭에서만 만들어진다(검색·비교·미리보기
  // 단계는 전부 DB 쓰기 없는 순수 조회).
  // --------------------------------------------------

  let rpNewSelectedCandidate = null; // { provider_code, external_product_id, product_url, title, detail }
  let rpNewMatchResult = null; // { confidence, evidence }
  let rpNewPreviewResult = null; // CheckoutPreviewResponse
  let rpNewIdempotencyKey = null; // 다이얼로그를 여는 시점 1회만 생성 —
  // "구매 요청 생성" 클릭이 중복 발생해도(네트워크 재시도·이중
  // 클릭이 버튼 비활성화를 뚫는 경우 포함) 항상 같은 키로 요청되어
  // 서버가 같은 행으로 취급한다(idempotency_key 중복 방지 원칙).

  function rpUpdateNewRequestCreateEnabled() {
    el("rp-new-request-create").disabled = !(rpNewSelectedCandidate && rpNewMatchResult && rpNewPreviewResult);
  }

  function rpUpdateNewRequestDemoBanner() {
    const code = el("rp-new-provider").value;
    const provider = (rpCurrentProviders || []).find((p) => p.provider_code === code);
    const banner = el("rp-new-demo-banner");
    if (!provider) {
      banner.textContent = "";
      return;
    }
    if (provider.connection_status === "TEST_ONLY") {
      banner.textContent = HomezI18n.t("retail_purchase.demo_banner_test_only");
    } else {
      banner.textContent = HomezI18n.t("retail_purchase.demo_banner_not_connected");
    }
  }

  function rpOpenNewRequestDialog() {
    const dialog = el("rp-new-request-dialog");

    rpNewSelectedCandidate = null;
    rpNewMatchResult = null;
    rpNewPreviewResult = null;
    rpNewIdempotencyKey = genIdemKey("rp-new");

    const providerSelect = el("rp-new-provider");
    providerSelect.innerHTML = (rpCurrentProviders || []).map((p) => `
      <option value="${escapeHtml(p.provider_code)}">${escapeHtml(RP_PROVIDER_LABEL_KEYS[p.provider_code] ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[p.provider_code]) : p.provider_code)}</option>
    `).join("");
    rpUpdateNewRequestDemoBanner();

    el("rp-new-source-order-id").value = "";
    el("rp-new-search-keyword").value = "";
    el("rp-new-search-results").innerHTML = "";
    el("rp-new-selected-wrap").hidden = true;
    el("rp-new-selected-summary").innerHTML = "";
    el("rp-new-quantity").value = "1";
    ["brand", "manufacturer", "model", "gtin", "capacity", "color"].forEach((f) => {
      el(`rp-new-src-${f}`).value = "";
    });
    el("rp-new-match-result").innerHTML = "";
    el("rp-new-expected-sale-amount").value = "";
    el("rp-new-expected-sale-fee").value = "";
    el("rp-new-preview-result").innerHTML = "";
    el("rp-new-match-btn").disabled = true;
    el("rp-new-preview-btn").disabled = true;
    el("rp-new-request-create").disabled = true;
    el("rp-new-request-error").textContent = "";

    const searchBtn = el("rp-new-search-btn");
    const matchBtn = el("rp-new-match-btn");
    const previewBtn = el("rp-new-preview-btn");
    const createBtn = el("rp-new-request-create");
    const cancelBtn = el("rp-new-request-cancel");

    async function onSearch() {
      const keyword = el("rp-new-search-keyword").value.trim();
      if (!keyword) return;
      const resultsWrap = el("rp-new-search-results");
      resultsWrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
      try {
        const result = await apiFetch("/retail-purchase/search", {
          method: "POST",
          body: JSON.stringify({ provider_code: providerSelect.value, keyword }),
        });
        if (!result.items || result.items.length === 0) {
          renderEmptyState(resultsWrap, HomezI18n.t("retail_purchase.search_empty"), "");
          return;
        }
        resultsWrap.innerHTML = `
          <ul class="dash-todo-list">
            ${result.items.map((it, idx) => `
              <li class="row-clickable" data-idx="${idx}" tabindex="0">
                <strong>${escapeHtml(it.title)}</strong>
                — ${it.list_price === null || it.list_price === undefined ? "—" : escapeHtml(String(it.list_price))}
                ${it.in_stock === false ? `<span class="field-error">${escapeHtml(HomezI18n.t("retail_purchase.out_of_stock_badge"))}</span>` : ""}
              </li>
            `).join("")}
          </ul>
        `;
        resultsWrap.querySelectorAll("li[data-idx]").forEach((li) => {
          li.addEventListener("click", () => onSelectCandidate(result.items[Number(li.dataset.idx)]));
        });
      } catch (err) {
        renderErrorState(resultsWrap, err);
      }
    }

    async function onSelectCandidate(item) {
      let detail = null;
      try {
        detail = await apiFetch(
          `/retail-purchase/products/${encodeURIComponent(providerSelect.value)}/${encodeURIComponent(item.external_product_id)}`,
        );
      } catch (_err) {
        // 상세 조회 실패해도 검색 결과 자체는 선택 가능하게 둔다 —
        // 동일상품 비교 단계에서 후보 속성이 비어 있으면 그만큼
        // coverage가 낮게 나와 자연히 BLOCKED/EVIDENCE_REQUIRED로
        // 이어진다(허위로 채우지 않는다).
      }

      rpNewSelectedCandidate = {
        provider_code: providerSelect.value,
        external_product_id: item.external_product_id,
        product_url: item.product_url,
        title: item.title,
        detail,
      };
      rpNewMatchResult = null;
      rpNewPreviewResult = null;
      el("rp-new-match-result").innerHTML = "";
      el("rp-new-preview-result").innerHTML = "";

      el("rp-new-selected-wrap").hidden = false;
      el("rp-new-selected-summary").innerHTML = `
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_product_url"))}</dt>
          <dd><a href="${escapeHtml(item.product_url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></dd>
        </dl>
      `;
      matchBtn.disabled = false;
      previewBtn.disabled = false;
      rpUpdateNewRequestCreateEnabled();
    }

    async function onRunMatch() {
      if (!rpNewSelectedCandidate) return;
      const source = {
        brand: el("rp-new-src-brand").value || null,
        manufacturer: el("rp-new-src-manufacturer").value || null,
        model_name: el("rp-new-src-model").value || null,
        gtin: el("rp-new-src-gtin").value || null,
        capacity: el("rp-new-src-capacity").value || null,
        color_or_scent: el("rp-new-src-color").value || null,
      };
      const d = rpNewSelectedCandidate.detail;
      const candidate = d ? {
        brand: d.brand, manufacturer: d.manufacturer, model_name: d.model_name,
        gtin: d.gtin, capacity: null, color_or_scent: null,
      } : {};

      try {
        const result = await apiFetch("/retail-purchase/match-check", {
          method: "POST",
          body: JSON.stringify({ source, candidate }),
        });
        rpNewMatchResult = result;
        const pct = Math.round(result.confidence * 100);
        el("rp-new-match-result").innerHTML = `
          <p><strong>${escapeHtml(HomezI18n.t("retail_purchase.match_tier_label"))}:</strong>
            <span class="${riskClass(result.confidence)}">${escapeHtml(result.tier)} (${pct}%)</span>
            ${result.blocked_reason ? ` — ${escapeHtml(result.blocked_reason)}` : ""}
          </p>
        `;
      } catch (err) {
        el("rp-new-match-result").innerHTML = "";
        toast((err && err.message) || HomezI18n.t("retail_purchase.action_error"), "error");
      }
      rpUpdateNewRequestCreateEnabled();
    }

    async function onPreview() {
      if (!rpNewSelectedCandidate) return;
      const quantity = Number(el("rp-new-quantity").value) || 1;
      const expectedSaleAmount = el("rp-new-expected-sale-amount").value;
      const expectedSaleFee = el("rp-new-expected-sale-fee").value;

      try {
        const result = await apiFetch("/retail-purchase/checkout-preview", {
          method: "POST",
          body: JSON.stringify({
            provider_code: rpNewSelectedCandidate.provider_code,
            external_product_id: rpNewSelectedCandidate.external_product_id,
            quantity,
            expected_sale_amount: expectedSaleAmount === "" ? null : Number(expectedSaleAmount),
            expected_sale_fee_amount: expectedSaleFee === "" ? null : Number(expectedSaleFee),
          }),
        });
        rpNewPreviewResult = result;
        el("rp-new-preview-result").innerHTML = `
          <dl class="detail-grid">
            <dt>${escapeHtml(HomezI18n.t("retail_purchase.preview_unit_price"))}</dt><dd>${result.unit_price_estimate ?? "—"}</dd>
            <dt>${escapeHtml(HomezI18n.t("retail_purchase.preview_item_total"))}</dt><dd>${result.item_total}</dd>
            <dt>${escapeHtml(HomezI18n.t("retail_purchase.preview_shipping_fee"))}</dt><dd>${result.shipping_fee}</dd>
            <dt>${escapeHtml(HomezI18n.t("retail_purchase.preview_total_purchase"))}</dt><dd>${result.total_purchase_amount}</dd>
            <dt>${escapeHtml(HomezI18n.t("retail_purchase.preview_expected_profit"))}</dt><dd>${result.expected_net_profit ?? "—"}</dd>
            <dt>${escapeHtml(HomezI18n.t("retail_purchase.preview_expected_margin"))}</dt><dd>${result.expected_margin_rate === null || result.expected_margin_rate === undefined ? "—" : `${Math.round(result.expected_margin_rate * 1000) / 10}%`}</dd>
          </dl>
        `;
      } catch (err) {
        el("rp-new-preview-result").innerHTML = "";
        toast((err && err.message) || HomezI18n.t("retail_purchase.action_error"), "error");
      }
      rpUpdateNewRequestCreateEnabled();
    }

    async function onCreate() {
      if (!rpNewSelectedCandidate || !rpNewMatchResult || !rpNewPreviewResult) return;
      const sourceOrderId = Number(el("rp-new-source-order-id").value);
      if (!sourceOrderId) {
        el("rp-new-request-error").textContent = HomezI18n.t("retail_purchase.new_request_source_order_required");
        return;
      }

      createBtn.disabled = true;
      try {
        const order = await apiFetch("/retail-purchase", {
          method: "POST",
          body: JSON.stringify({
            source_order_id: sourceOrderId,
            provider_code: rpNewSelectedCandidate.provider_code,
            product_url: rpNewSelectedCandidate.product_url,
            external_product_id: rpNewSelectedCandidate.external_product_id,
            selected_option: null,
            quantity: Number(el("rp-new-quantity").value) || 1,
            idempotency_key: rpNewIdempotencyKey,
            match_confidence: rpNewMatchResult.confidence,
            match_evidence: rpNewMatchResult.evidence || [],
            expected_amount: rpNewPreviewResult.total_purchase_amount,
          }),
        });
        cleanup();
        toast(HomezI18n.t("retail_purchase.new_request_created"), "success");
        navigateTo("retail-purchase-detail", { id: order.id });
      } catch (err) {
        createBtn.disabled = false;
        el("rp-new-request-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    function cleanup() {
      searchBtn.removeEventListener("click", onSearch);
      matchBtn.removeEventListener("click", onRunMatch);
      previewBtn.removeEventListener("click", onPreview);
      createBtn.removeEventListener("click", onCreate);
      cancelBtn.removeEventListener("click", onCancelClick);
      providerSelect.removeEventListener("change", rpUpdateNewRequestDemoBanner);
      dialog.removeEventListener("cancel", onCancelClick);
      dialog.close();
    }

    function onCancelClick() {
      cleanup();
    }

    searchBtn.addEventListener("click", onSearch);
    matchBtn.addEventListener("click", () => withButtonGuard(matchBtn, onRunMatch));
    previewBtn.addEventListener("click", () => withButtonGuard(previewBtn, onPreview));
    createBtn.addEventListener("click", () => withButtonGuard(createBtn, onCreate));
    cancelBtn.addEventListener("click", onCancelClick);
    providerSelect.addEventListener("change", rpUpdateNewRequestDemoBanner);
    dialog.addEventListener("cancel", onCancelClick);
    dialog.showModal();
  }

  // --------------------------------------------------
  // 구매 실행 상세
  // --------------------------------------------------

  let rpDetailCurrentId = null;
  let rpDetailCurrentQuoteId = null;

  async function loadRetailPurchaseDetail(opts = {}) {
    const id = opts.id || rpDetailCurrentId;
    rpDetailCurrentId = id;
    wireRetailPurchaseDetailBackOnce();
    renderSharedEstopBanner(el("rp-detail-estop-banner-slot"));

    const body = el("rp-detail-body");
    body.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(body, HomezI18n.t("retail_purchase.empty_title"), "");
      return;
    }

    let order;
    try {
      order = await apiFetch(`/retail-purchase/${id}`);
    } catch (err) {
      renderErrorState(body, err);
      return;
    }

    rpRenderDetail(body, order);
  }

  function wireRetailPurchaseDetailBackOnce() {
    if (wireRetailPurchaseDetailBackOnce._wired) return;
    wireRetailPurchaseDetailBackOnce._wired = true;
    el("rp-detail-back-btn").addEventListener("click", () => navigateTo("retail-purchase"));
  }

  function rpRenderDetail(body, order) {

    const actionHtml = (() => {
      if (order.status === "PROPOSED") {
        return `<button type="button" class="btn btn-primary" id="rp-action-btn" data-action="policy-check">${escapeHtml(HomezI18n.t("retail_purchase.action_policy_check"))}</button>`;
      }
      if (order.status === "POLICY_CHECKED") {
        return `<button type="button" class="btn btn-primary" id="rp-action-btn" data-action="reserve-budget">${escapeHtml(HomezI18n.t("retail_purchase.action_reserve_budget"))}</button>`;
      }
      if (order.status === "BUDGET_RESERVED") {
        return `<button type="button" class="btn btn-primary" id="rp-action-btn" data-action="quote">${escapeHtml(HomezI18n.t("retail_purchase.action_request_quote"))}</button>`;
      }
      if (order.status === "QUOTED") {
        return `<button type="button" class="btn btn-danger" id="rp-action-btn" data-action="place-order">${escapeHtml(HomezI18n.t("retail_purchase.action_place_order"))}</button>`;
      }
      if (["ORDERED", "TRACKING_PENDING", "SHIPPED"].includes(order.status)) {
        return `<button type="button" class="btn btn-ghost" id="rp-action-btn" data-action="refresh-tracking">${escapeHtml(HomezI18n.t("retail_purchase.action_refresh_tracking"))}</button>`;
      }
      return "";
    })();

    body.innerHTML = `
      <div class="view-header">
        <h1>${escapeHtml(order.external_product_id)}</h1>
        <span>${escapeHtml(rpOrderStatusLabel(order.status))}</span>
      </div>
      <div class="dash-card">
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.col_provider"))}</dt><dd>${escapeHtml(RP_PROVIDER_LABEL_KEYS[order.provider_code] ? HomezI18n.t(RP_PROVIDER_LABEL_KEYS[order.provider_code]) : order.provider_code)}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_product_url"))}</dt><dd><a href="${escapeHtml(order.product_url)}" target="_blank" rel="noopener">${escapeHtml(order.product_url)}</a></dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.col_match_confidence"))}</dt><dd>${order.match_confidence === null || order.match_confidence === undefined ? "—" : Math.round(order.match_confidence * 100) + "%"}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_expected_amount"))}</dt><dd>${order.expected_amount ?? "—"}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_actual_amount"))}</dt><dd>${order.actual_amount ?? "—"}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_expected_net_profit"))}</dt><dd>${order.expected_net_profit ?? "—"}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_external_order_number"))}</dt><dd>${escapeHtml(order.external_order_number || "—")}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_tracking"))}</dt><dd>${escapeHtml(order.tracking_company || "—")} ${escapeHtml(order.tracking_number || "")}</dd>
          ${order.failure_code ? `<dt>${escapeHtml(HomezI18n.t("retail_purchase.field_failure_code"))}</dt><dd>${escapeHtml(order.failure_code)}</dd>` : ""}
        </dl>
      </div>
      ${actionHtml ? `<div class="dialog-actions">${actionHtml}</div>` : ""}
      <p class="field-hint">${escapeHtml(HomezI18n.t("retail_purchase.action_hint"))}</p>
    `;

    const actionBtn = el("rp-action-btn");
    if (!actionBtn) return;

    actionBtn.addEventListener("click", () => withButtonGuard(actionBtn, async () => {
      const action = actionBtn.dataset.action;
      try {
        if (action === "policy-check") {
          await rpRunPolicyCheckFlow(order);
        } else if (action === "reserve-budget") {
          await apiFetch(`/retail-purchase/${order.id}/reserve-budget`, {
            method: "POST",
            body: JSON.stringify({ required_amount: order.expected_amount || 0 }),
          });
        } else if (action === "quote") {
          const quoteResult = await apiFetch(`/retail-purchase/${order.id}/quote`, { method: "POST" });
          rpDetailCurrentQuoteId = quoteResult.quote_id;
        } else if (action === "place-order") {
          await rpRunPlaceOrderFlow(order);
        } else if (action === "refresh-tracking") {
          await apiFetch(`/retail-purchase/${order.id}/refresh-tracking`, { method: "POST" });
        }
        loadRetailPurchaseDetail({ id: order.id });
      } catch (err) {
        toast((err && err.message) || HomezI18n.t("retail_purchase.action_error"), "error");
      }
    }));
  }

  function rpRunPolicyCheckFlow(order) {
    // 2026-08-22 14차 지시(작업 2) — 반고정 데모 값을 서버로 보내던
    // 결함을 제거한다. 순이익 계산에 쓰는 실제 금액은 운영자가 실제
    // 확인한 값만 직접 입력한다(서버가 임의로 추정하지 않는다는
    // 원칙과 동일하게, 화면도 값을 지어내지 않는다).
    return new Promise((resolve, reject) => {
      const dialog = el("rp-policy-check-dialog");
      const runBtn = el("rp-policy-check-run");
      const cancelBtn = el("rp-policy-check-cancel");

      el("rp-pc-in-stock").checked = true;
      el("rp-pc-delivery-days").value = "";
      el("rp-pc-return-allowed").checked = true;
      el("rp-pc-seller-trust").value = "";
      el("rp-pc-sale-amount").value = "";
      el("rp-pc-actual-amount").value = order.expected_amount ?? "";
      el("rp-pc-shipping-fee").value = "";
      el("rp-pc-fee-amount").value = "";
      el("rp-policy-check-error").textContent = "";

      const numOrNull = (elId) => {
        const raw = el(elId).value;
        return raw === "" ? null : Number(raw);
      };

      function cleanup() {
        runBtn.removeEventListener("click", onRun);
        cancelBtn.removeEventListener("click", onCancel);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
      }

      function onCancel() {
        cleanup();
        resolve();
      }

      async function onRun() {
        runBtn.disabled = true;
        try {
          await apiFetch(`/retail-purchase/${order.id}/policy-check`, {
            method: "POST",
            body: JSON.stringify({
              in_stock: el("rp-pc-in-stock").checked,
              estimated_delivery_days: numOrNull("rp-pc-delivery-days"),
              return_allowed: el("rp-pc-return-allowed").checked,
              seller_trust_score: numOrNull("rp-pc-seller-trust"),
              coupang_sale_amount: numOrNull("rp-pc-sale-amount"),
              retail_actual_amount: numOrNull("rp-pc-actual-amount"),
              shipping_fee: numOrNull("rp-pc-shipping-fee"),
              coupang_fee_amount: numOrNull("rp-pc-fee-amount"),
            }),
          });
          cleanup();
          resolve();
        } catch (err) {
          runBtn.disabled = false;
          el("rp-policy-check-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
        }
      }

      runBtn.disabled = false;
      runBtn.addEventListener("click", onRun);
      cancelBtn.addEventListener("click", onCancel);
      dialog.addEventListener("cancel", onCancel);
      dialog.showModal();
    });
  }

  async function rpRunPlaceOrderFlow(order) {
    const { confirmed } = await confirmDialog({
      title: HomezI18n.t("retail_purchase.place_order_confirm_title"),
      body: HomezI18n.t("retail_purchase.place_order_confirm_body"),
      okLabel: HomezI18n.t("retail_purchase.action_place_order"),
    });
    if (!confirmed) return;

    if (!rpDetailCurrentQuoteId) {
      throw new Error(HomezI18n.t("retail_purchase.quote_required_first"));
    }

    await apiFetch(`/retail-purchase/${order.id}/place-order`, {
      method: "POST",
      body: JSON.stringify({
        quote_id: rpDetailCurrentQuoteId,
        shipping_address_reference: "console-manual-trigger",
      }),
    });
  }

  // --------------------------------------------------
  // 매입·발주 관리(A+E+F 혼합형, Gate PT-1 2026-08-22) — 소비자 계정
  // 자동 로그인·DOM 자동화·CAPTCHA/OTP 우회는 여기 어디에도 없다.
  // 후보 URL은 항상 새 탭으로 열어 사람이 직접 로그인·결제한다.
  // --------------------------------------------------

  const PT_STATUS_LABEL_KEYS = {
    SEARCH_REQUIRED: "purchase_task.status_search_required",
    CANDIDATES_READY: "purchase_task.status_candidates_ready",
    REVIEW_REQUIRED: "purchase_task.status_review_required",
    PURCHASE_READY: "purchase_task.status_purchase_ready",
    USER_PAYMENT_PENDING: "purchase_task.status_user_payment_pending",
    TRACKING_REQUIRED: "purchase_task.status_tracking_required",
    SHIPPED: "purchase_task.status_shipped",
    DELIVERED: "purchase_task.status_delivered",
    CANCEL_REQUIRED: "purchase_task.status_cancel_required",
    RETURN_REQUIRED: "purchase_task.status_return_required",
    REFUND_PENDING: "purchase_task.status_refund_pending",
    COMPLETED: "purchase_task.status_completed",
    BLOCKED: "purchase_task.status_blocked",
    FAILED: "purchase_task.status_failed",
    UNCERTAIN: "purchase_task.status_uncertain",
    SKIPPED_INVENTORY_AVAILABLE: "purchase_task.status_skipped_inventory_available",
    SOURCE_ORDER_CANCELLED: "purchase_task.status_source_order_cancelled",
  };

  const PT_CREATION_SOURCE_LABEL_KEYS = {
    MANUAL: "purchase_task.creation_source_manual",
    ORDER_AUTO: "purchase_task.creation_source_order_auto",
  };

  function ptCreationSourceLabel(source) {
    const key = PT_CREATION_SOURCE_LABEL_KEYS[source];
    return key ? HomezI18n.t(key) : source;
  }

  const PT_MALL_LABEL_KEYS = {
    NAVER_SHOPPING: "purchase_task.mall_naver_shopping",
    ELEVENST: "purchase_task.mall_elevenst",
    GMARKET: "purchase_task.mall_gmarket",
    AUCTION: "purchase_task.mall_auction",
    OTHER: "purchase_task.mall_other",
  };

  function ptStatusLabel(s) {
    const key = PT_STATUS_LABEL_KEYS[s];
    return key ? HomezI18n.t(key) : s;
  }

  function ptMallLabel(code) {
    const key = PT_MALL_LABEL_KEYS[code];
    return key ? HomezI18n.t(key) : code;
  }

  let ptCurrentStatus = "";
  let ptCurrentSourceOrderId = null;
  let ptCurrentEstopActive = false;
  let ptCurrentPolicy = null;
  let ptDetailCurrentId = null;

  async function loadPurchaseTask(opts = {}) {
    wirePurchaseTaskToolbarOnce();
    ptCurrentEstopActive = await renderSharedEstopBanner(el("pt-estop-banner-slot"));

    if (Object.prototype.hasOwnProperty.call(opts, "source_order_id")) {
      ptCurrentSourceOrderId = opts.source_order_id;
    }

    document.querySelectorAll(".pt-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.status === ptCurrentStatus);
    });

    await ptLoadTasks();
    ptRenderNotifications();
  }

  async function ptRenderNotifications() {
    const wrap = el("pt-notifications-wrap");
    if (!wrap) return;
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let tasks = [];
    try {
      tasks = await apiFetch("/purchase-tasks");
    } catch (_err) {
      // 알림 패널 실패가 화면 전체를 깨뜨리지 않는다 — 빈 목록으로 대체.
    }

    const items = [];
    if (ptCurrentEstopActive) {
      items.push({ label: HomezI18n.t("purchase_task.notif_estop_active"), taskId: null });
    }

    (tasks || []).forEach((t) => {
      const product = t.product_title;
      if (t.status === "REVIEW_REQUIRED") {
        items.push({ label: HomezI18n.t("purchase_task.notif_review_required", { product }), taskId: t.id });
      } else if (t.status === "USER_PAYMENT_PENDING") {
        items.push({ label: HomezI18n.t("purchase_task.notif_payment_pending", { product }), taskId: t.id });
      } else if (t.status === "TRACKING_REQUIRED") {
        items.push({ label: HomezI18n.t("purchase_task.notif_tracking_required", { product }), taskId: t.id });
      } else if (t.status === "BLOCKED") {
        items.push({ label: HomezI18n.t("purchase_task.notif_blocked", { product, reason: t.block_reason || t.failure_code || "" }), taskId: t.id });
      } else if (t.status === "FAILED") {
        items.push({ label: HomezI18n.t("purchase_task.notif_failed", { product, reason: t.failure_code || "" }), taskId: t.id });
      } else if (t.status === "UNCERTAIN") {
        items.push({ label: HomezI18n.t("purchase_task.notif_uncertain", { product }), taskId: t.id });
      } else if (["CANCEL_REQUIRED", "RETURN_REQUIRED", "REFUND_PENDING"].includes(t.status)) {
        items.push({ label: HomezI18n.t("purchase_task.notif_cancel_return_refund", { product, status: ptStatusLabel(t.status) }), taskId: t.id });
      } else if (t.status === "SEARCH_REQUIRED") {
        items.push({ label: HomezI18n.t("purchase_task.notif_search_required", { product }), taskId: t.id });
      } else if (t.status === "CANDIDATES_READY") {
        items.push({ label: HomezI18n.t("purchase_task.notif_candidates_ready", { product }), taskId: t.id });
      }
    });

    if (items.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("purchase_task.notifications_empty"), "");
      return;
    }

    wrap.innerHTML = `
      <ul class="dash-todo-list">
        ${items.map((it, idx) => `
          <li class="${it.taskId ? "row-clickable" : ""}" data-idx="${idx}" ${it.taskId ? 'tabindex="0"' : ""}>${escapeHtml(it.label)}</li>
        `).join("")}
      </ul>
    `;

    wrap.querySelectorAll("li[data-idx]").forEach((li) => {
      const it = items[Number(li.dataset.idx)];
      if (!it.taskId) return;
      const go = () => navigateTo("purchase-task-detail", { id: it.taskId });
      li.addEventListener("click", go);
      li.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          go();
        }
      });
    });
  }

  async function ptLoadTasks() {
    const wrap = el("pt-task-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    const filterBanner = el("pt-source-order-filter-banner");
    if (filterBanner) {
      if (ptCurrentSourceOrderId) {
        filterBanner.hidden = false;
        filterBanner.textContent = "";
        const label = document.createElement("span");
        label.textContent = HomezI18n.t("purchase_task.filtered_by_order", { id: ptCurrentSourceOrderId });
        const clearBtn = document.createElement("button");
        clearBtn.type = "button";
        clearBtn.className = "btn btn-ghost btn-sm";
        clearBtn.textContent = HomezI18n.t("purchase_task.clear_filter");
        clearBtn.addEventListener("click", () => {
          ptCurrentSourceOrderId = null;
          ptLoadTasks();
        });
        filterBanner.appendChild(label);
        filterBanner.appendChild(clearBtn);
      } else {
        filterBanner.hidden = true;
        filterBanner.textContent = "";
      }
    }

    let rows;
    try {
      const params = new URLSearchParams();
      if (ptCurrentStatus) params.set("status", ptCurrentStatus);
      if (ptCurrentSourceOrderId) params.set("source_order_id", String(ptCurrentSourceOrderId));
      const qs = params.toString() ? `?${params.toString()}` : "";
      rows = await apiFetch(`/purchase-tasks${qs}`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (!rows || rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("purchase_task.empty_title"), HomezI18n.t("purchase_task.empty_sub"));
      return;
    }

    const colProduct = HomezI18n.t("purchase_task.col_product");
    const colStatus = HomezI18n.t("ai_proposals.col_status");
    const colQty = HomezI18n.t("purchase_task.col_quantity");
    const colProfit = HomezI18n.t("purchase_task.col_expected_profit");
    const colMargin = HomezI18n.t("purchase_task.col_expected_margin");
    const colSource = HomezI18n.t("purchase_task.col_creation_source");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colProduct}</th><th>${colQty}</th><th>${colStatus}</th><th>${colProfit}</th><th>${colMargin}</th><th>${colSource}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" data-id="${r.id}" tabindex="0">
            <td data-label="${colProduct}">${escapeHtml(r.product_title)}</td>
            <td data-label="${colQty}">${escapeHtml(String(r.quantity))}</td>
            <td data-label="${colStatus}">${escapeHtml(ptStatusLabel(r.status))}</td>
            <td data-label="${colProfit}">${r.expected_net_profit === null || r.expected_net_profit === undefined ? "—" : escapeHtml(String(r.expected_net_profit))}</td>
            <td data-label="${colMargin}">${r.expected_margin_rate === null || r.expected_margin_rate === undefined ? "—" : `${Math.round(r.expected_margin_rate * 10) / 10}%`}</td>
            <td data-label="${colSource}">${escapeHtml(ptCreationSourceLabel(r.creation_source))}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      const openIt = () => navigateTo("purchase-task-detail", { id: Number(tr.dataset.id) });
      tr.addEventListener("click", openIt);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openIt();
        }
      });
    });
  }

  function wirePurchaseTaskToolbarOnce() {
    if (wirePurchaseTaskToolbarOnce._wired) return;
    wirePurchaseTaskToolbarOnce._wired = true;

    document.querySelectorAll(".pt-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        ptCurrentStatus = btn.dataset.status;
        document.querySelectorAll(".pt-tab").forEach((b) => b.classList.toggle("active", b === btn));
        ptLoadTasks();
      });
    });

    el("pt-new-task-btn").addEventListener("click", ptOpenCreateDialog);
    el("pt-operating-settings-btn").addEventListener("click", ptOpenOperatingSettingsDialog);
    el("pt-policy-btn").addEventListener("click", ptOpenPolicyDialog);
    el("pt-email-pref-btn").addEventListener("click", ptOpenEmailPrefDialog);
    el("pt-csv-import-btn").addEventListener("click", ptOpenCsvDialog);
  }

  // dialog는 정적 DOM 요소라서 여러 번 열릴 수 있다 — 사용자가 대화상자를
  // 취소/완료하지 않고 화면을 벗어나면(예: 상세 페이지 뒤로가기) cleanup()이
  // 호출되지 않아 이전 addEventListener가 그대로 남는다. 다음에 같은
  // 대화상자를 다시 열면 낡은 리스너가 새 리스너와 함께 중복 실행되어
  // 엉뚱한 task/candidate id로 API를 호출할 수 있다(실 브라우저 검증 중
  // 발견). 버튼을 매번 clone으로 교체해 이전 리스너를 통째로 제거한다.
  function ptFreshButton(id) {
    const original = el(id);
    const clone = original.cloneNode(true);
    original.replaceWith(clone);
    return clone;
  }

  function ptOpenCreateDialog() {
    const dialog = el("pt-create-dialog");
    ["pt-c-source-order-id", "pt-c-product-title", "pt-c-brand", "pt-c-manufacturer",
      "pt-c-model", "pt-c-gtin", "pt-c-capacity", "pt-c-color", "pt-c-region",
      "pt-c-sale-amount", "pt-c-fee-amount", "pt-c-deadline"].forEach((id) => { el(id).value = ""; });
    el("pt-c-quantity").value = "1";
    el("pt-create-error").textContent = "";

    const submitBtn = ptFreshButton("pt-create-submit");
    const cancelBtn = ptFreshButton("pt-create-cancel");

    function cleanup() {
      submitBtn.removeEventListener("click", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSubmit() {
      const sourceOrderId = Number(el("pt-c-source-order-id").value);
      const productTitle = el("pt-c-product-title").value.trim();
      if (!sourceOrderId || !productTitle) {
        el("pt-create-error").textContent = HomezI18n.t("purchase_task.create_required_error");
        return;
      }
      const deadlineRaw = el("pt-c-deadline").value;

      submitBtn.disabled = true;
      try {
        const task = await apiFetch("/purchase-tasks", {
          method: "POST",
          body: JSON.stringify({
            source_order_id: sourceOrderId,
            product_title: productTitle,
            brand: el("pt-c-brand").value || null,
            manufacturer: el("pt-c-manufacturer").value || null,
            model_name: el("pt-c-model").value || null,
            gtin: el("pt-c-gtin").value || null,
            capacity: el("pt-c-capacity").value || null,
            color_or_scent: el("pt-c-color").value || null,
            quantity: Number(el("pt-c-quantity").value) || 1,
            shippable_region_note: el("pt-c-region").value || null,
            coupang_sale_amount: el("pt-c-sale-amount").value === "" ? null : Number(el("pt-c-sale-amount").value),
            coupang_fee_amount: el("pt-c-fee-amount").value === "" ? null : Number(el("pt-c-fee-amount").value),
            purchase_deadline: deadlineRaw ? new Date(deadlineRaw).toISOString() : null,
            idempotency_key: `pt:${Date.now()}:${Math.random().toString(36).slice(2)}`,
          }),
        });
        cleanup();
        toast(HomezI18n.t("purchase_task.task_created"), "success");
        navigateTo("purchase-task-detail", { id: task.id });
      } catch (err) {
        submitBtn.disabled = false;
        el("pt-create-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    submitBtn.disabled = false;
    submitBtn.addEventListener("click", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  async function loadPurchaseTaskDetail(opts = {}) {
    const id = opts.id || ptDetailCurrentId;
    ptDetailCurrentId = id;
    wirePurchaseTaskDetailBackOnce();
    renderSharedEstopBanner(el("pt-detail-estop-banner-slot"));

    const body = el("pt-detail-body");
    body.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(body, HomezI18n.t("purchase_task.empty_title"), "");
      return;
    }

    let task, candidates;
    try {
      task = await apiFetch(`/purchase-tasks/${id}`);
      candidates = await apiFetch(`/purchase-tasks/${id}/candidates`);
    } catch (err) {
      renderErrorState(body, err);
      return;
    }

    let tracking = null;
    const trackingStatuses = [
      "TRACKING_REQUIRED", "SHIPPED", "DELIVERED", "CANCEL_REQUIRED",
      "RETURN_REQUIRED", "REFUND_PENDING", "COMPLETED",
    ];
    if (trackingStatuses.includes(task.status)) {
      try {
        tracking = await apiFetch(`/purchase-tasks/${id}/tracking`);
      } catch (_err) {
        tracking = null;
      }
    }

    ptRenderDetail(body, task, candidates, tracking);
  }

  function wirePurchaseTaskDetailBackOnce() {
    if (wirePurchaseTaskDetailBackOnce._wired) return;
    wirePurchaseTaskDetailBackOnce._wired = true;
    el("pt-detail-back-btn").addEventListener("click", () => navigateTo("purchase-task"));
  }

  function ptRenderDetail(body, task, candidates, tracking) {
    const showCandidates = ["SEARCH_REQUIRED", "CANDIDATES_READY", "REVIEW_REQUIRED"].includes(task.status);

    // 2026-09-09 후속("발주 전 최종 검토 화면") — 매입처 계정이
    // 배정된 작업에서만 보여준다(연결이 없으면 조회할 대상 자체가
    // 없다). 이 카드는 읽기 전용 조립 결과만 보여준다 — "실제 전송"
    // 버튼은 항상 렌더링되지만(화면이 그 직전까지는 이어져야 하므로)
    // 클릭해도 아무 API도 호출하지 않는다. 실제 전송은 이 화면 밖의
    // 완전히 별도 승인·별도 배선이 필요하다(현재 어디에도 없음).
    const reviewCardHtml = task.channel_connection_id ? `
      <div class="dash-card">
        <div class="dash-card-head">
          <h2>${escapeHtml(HomezI18n.t("purchase_task.review_title"))}</h2>
        </div>
        <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.review_intro"))}</p>
        <div class="pt-cc-add-form">
          <label class="field"><span class="field-label">${escapeHtml(HomezI18n.t("purchase_task.review_product_code_label"))}</span>
            <input type="text" id="pt-review-product-code" placeholder="${escapeHtml(HomezI18n.t("purchase_task.cc_lookup_product_field_label"))}"></label>
          <label class="field"><span class="field-label">${escapeHtml(HomezI18n.t("purchase_task.review_option_id_label"))}</span>
            <input type="text" id="pt-review-option-id"></label>
          <label class="field"><span class="field-label">${escapeHtml(HomezI18n.t("purchase_task.review_qty_label"))}</span>
            <input type="number" min="1" value="${escapeHtml(String(task.quantity || 1))}" id="pt-review-qty"></label>
          <button type="button" class="btn btn-primary btn-sm" id="pt-review-run-btn">${escapeHtml(HomezI18n.t("purchase_task.review_run_btn"))}</button>
        </div>
        <div id="pt-review-result"></div>
      </div>
    ` : `
      <div class="dash-card">
        <div class="dash-card-head"><h2>${escapeHtml(HomezI18n.t("purchase_task.review_title"))}</h2></div>
        <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.review_needs_connection_hint"))}</p>
      </div>
    `;

    const candidatesHtml = showCandidates ? `
      <div class="dash-card">
        <div class="dash-card-head">
          <h2>${escapeHtml(HomezI18n.t("purchase_task.candidates_title"))}</h2>
          <div>
            <button type="button" class="btn btn-ghost btn-sm" id="pt-search-links-btn">${escapeHtml(HomezI18n.t("purchase_task.action_search_links"))}</button>
            <button type="button" class="btn btn-primary btn-sm" id="pt-add-candidate-btn">${escapeHtml(HomezI18n.t("purchase_task.action_add_candidate"))}</button>
          </div>
        </div>
        <div id="pt-search-links-wrap"></div>
        ${candidates.length === 0 ? `<p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.candidates_empty"))}</p>` : `
          <table class="responsive-cards">
            <thead><tr>
              <th>${escapeHtml(HomezI18n.t("purchase_task.col_mall"))}</th>
              <th>${escapeHtml(HomezI18n.t("purchase_task.col_price"))}</th>
              <th>${escapeHtml(HomezI18n.t("retail_purchase.col_match_confidence"))}</th>
              <th>${escapeHtml(HomezI18n.t("ai_proposals.col_status"))}</th>
              <th></th>
            </tr></thead>
            <tbody>${candidates.map((c) => `
              <tr>
                <td data-label="${escapeHtml(HomezI18n.t("purchase_task.col_mall"))}"><a href="${escapeHtml(c.product_url)}" target="_blank" rel="noopener">${escapeHtml(ptMallLabel(c.shopping_mall_code))}</a></td>
                <td data-label="${escapeHtml(HomezI18n.t("purchase_task.col_price"))}">${c.estimated_price ?? "—"}</td>
                <td data-label="${escapeHtml(HomezI18n.t("retail_purchase.col_match_confidence"))}"><span class="${riskClass(c.match_confidence)}">${c.match_confidence === null || c.match_confidence === undefined ? "—" : Math.round(c.match_confidence * 100) + "%"}</span></td>
                <td data-label="${escapeHtml(HomezI18n.t("ai_proposals.col_status"))}">${c.is_selected ? escapeHtml(HomezI18n.t("purchase_task.candidate_selected")) : "—"}</td>
                <td><button type="button" class="btn btn-ghost btn-sm pt-cand-evaluate-btn" data-cand-id="${c.id}">${escapeHtml(HomezI18n.t("purchase_task.action_evaluate_candidate"))}</button></td>
              </tr>
            `).join("")}</tbody>
          </table>
        `}
      </div>
    ` : "";

    const trackingHtml = tracking ? `
      <div class="dash-card">
        <div class="dash-card-head"><h2>${escapeHtml(HomezI18n.t("purchase_task.tracking_title"))}</h2></div>
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_courier"))}</dt><dd>${escapeHtml(tracking.courier || "—")}${tracking.courier_confirmed ? "" : ` (${escapeHtml(HomezI18n.t("purchase_task.courier_unconfirmed"))})`}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_tracking_number"))}</dt><dd>${escapeHtml(tracking.tracking_number || "—")}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_shipped_at"))}</dt><dd>${fmtDate(tracking.shipped_at)}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_expected_arrival"))}</dt><dd>${fmtDate(tracking.expected_arrival_at)}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_delivery_status"))}</dt><dd>${escapeHtml(tracking.delivery_status || "—")}</dd>
          ${tracking.cancel_status ? `<dt>${escapeHtml(HomezI18n.t("purchase_task.field_cancel_status"))}</dt><dd>${escapeHtml(tracking.cancel_status)}</dd>` : ""}
          ${tracking.return_status ? `<dt>${escapeHtml(HomezI18n.t("purchase_task.field_return_status"))}</dt><dd>${escapeHtml(tracking.return_status)}</dd>` : ""}
          ${tracking.refund_status ? `<dt>${escapeHtml(HomezI18n.t("purchase_task.field_refund_status"))}</dt><dd>${escapeHtml(tracking.refund_status)} (${tracking.refund_amount ?? "—"})</dd>` : ""}
          <dt>${escapeHtml(HomezI18n.t("purchase_task.tracking_refresh_last_checked"))}</dt>
          <dd>${tracking.last_live_refresh_at
            ? `${fmtDate(tracking.last_live_refresh_at)}${tracking.last_live_refresh_result ? ` — ${escapeHtml(HomezI18n.t(`purchase_task.tracking_refresh_result.${tracking.last_live_refresh_result.toLowerCase()}`))}` : ""}`
            : escapeHtml(HomezI18n.t("purchase_task.tracking_refresh_never"))}</dd>
        </dl>
        <div class="dialog-actions">
          <button type="button" class="btn btn-ghost btn-sm" id="pt-tracking-refresh-btn">${escapeHtml(HomezI18n.t("purchase_task.tracking_refresh_btn"))}</button>
        </div>
      </div>
    ` : "";

    const actionButtons = [];
    if (task.status === "PURCHASE_READY") {
      actionButtons.push(`<button type="button" class="btn btn-primary" id="pt-open-payment-btn">${escapeHtml(HomezI18n.t("purchase_task.action_open_payment_page"))}</button>`);
    }
    if (task.status === "USER_PAYMENT_PENDING") {
      actionButtons.push(`<button type="button" class="btn btn-primary" id="pt-record-purchase-btn">${escapeHtml(HomezI18n.t("purchase_task.action_record_purchase"))}</button>`);
      actionButtons.push(`<button type="button" class="btn btn-ghost" id="pt-extend-reservation-btn">${escapeHtml(HomezI18n.t("purchase_task.action_extend_reservation"))}</button>`);
    }
    // 아래 상태별 버튼 노출 조건은 서비스 계층의 실제 허용 상태와
    // 정확히 일치시킨다(실 브라우저 검증 중 발견 — 이전에는 취소·반품
    // 버튼을 같은 넓은 상태 집합에서 함께 보여줘, 백엔드가 실제로는
    // 거부하는 조합(예: DELIVERED에서 취소 요청)도 클릭 가능하게
    // 보였다). request_cancel=TRACKING_REQUIRED/SHIPPED만,
    // request_return=DELIVERED만, record_refund=CANCEL_REQUIRED/
    // RETURN_REQUIRED만, complete_task=DELIVERED/REFUND_PENDING만
    // 허용한다(service.py 참고).
    if (task.status === "TRACKING_REQUIRED") {
      actionButtons.push(`<button type="button" class="btn btn-primary" id="pt-record-tracking-btn">${escapeHtml(HomezI18n.t("purchase_task.action_record_tracking"))}</button>`);
    }
    if (task.status === "SHIPPED") {
      actionButtons.push(`<button type="button" class="btn btn-ghost" id="pt-mark-delivered-btn">${escapeHtml(HomezI18n.t("purchase_task.action_mark_delivered"))}</button>`);
    }
    if (task.status === "DELIVERED" || task.status === "REFUND_PENDING") {
      actionButtons.push(`<button type="button" class="btn btn-primary" id="pt-complete-task-btn">${escapeHtml(HomezI18n.t("purchase_task.action_complete"))}</button>`);
    }
    if (["CANCEL_REQUIRED", "RETURN_REQUIRED"].includes(task.status)) {
      actionButtons.push(`<button type="button" class="btn btn-primary" id="pt-record-refund-btn">${escapeHtml(HomezI18n.t("purchase_task.action_record_refund"))}</button>`);
    }
    if (["TRACKING_REQUIRED", "SHIPPED"].includes(task.status)) {
      actionButtons.push(`<button type="button" class="btn btn-ghost" id="pt-request-cancel-btn">${escapeHtml(HomezI18n.t("purchase_task.action_request_cancel"))}</button>`);
    }
    if (task.status === "DELIVERED") {
      actionButtons.push(`<button type="button" class="btn btn-ghost" id="pt-request-return-btn">${escapeHtml(HomezI18n.t("purchase_task.action_request_return"))}</button>`);
    }

    body.innerHTML = `
      <div class="view-header">
        <h1>${escapeHtml(task.product_title)}</h1>
        <span>${escapeHtml(ptStatusLabel(task.status))}</span>
      </div>
      <div class="dash-card">
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_source_order_id"))}</dt><dd>${escapeHtml(String(task.source_order_id))} <button type="button" class="btn btn-ghost btn-sm" id="pt-goto-source-order-btn">${escapeHtml(HomezI18n.t("purchase_task.action_goto_source_order"))}</button></dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_creation_source"))}</dt><dd>${escapeHtml(ptCreationSourceLabel(task.creation_source))}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.field_quantity"))}</dt><dd>${escapeHtml(String(task.quantity))}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.attr_brand"))}</dt><dd>${escapeHtml(task.brand || "—")}</dd>
          <dt>${escapeHtml(HomezI18n.t("retail_purchase.attr_model"))}</dt><dd>${escapeHtml(task.model_name || "—")}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_coupang_sale_amount"))}</dt><dd>${task.coupang_sale_amount ?? "—"}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_expected_net_profit"))}</dt><dd>${task.expected_net_profit ?? "—"}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_expected_margin_rate"))}</dt><dd>${task.expected_margin_rate === null || task.expected_margin_rate === undefined ? "—" : `${Math.round(task.expected_margin_rate * 10) / 10}%`}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.field_purchase_deadline"))}</dt><dd>${fmtDate(task.purchase_deadline)}</dd>
          ${task.block_reason ? `<dt>${escapeHtml(HomezI18n.t("purchase_task.field_block_reason"))}</dt><dd>${escapeHtml(task.block_reason)}</dd>` : ""}
          ${task.caution_reason ? `<dt>${escapeHtml(HomezI18n.t("purchase_task.field_caution_reason"))}</dt><dd>${escapeHtml(task.caution_reason)}</dd>` : ""}
          ${task.failure_code ? `<dt>${escapeHtml(HomezI18n.t("retail_purchase.field_failure_code"))}</dt><dd>${escapeHtml(task.failure_code)}</dd>` : ""}
        </dl>
      </div>
      ${candidatesHtml}
      ${reviewCardHtml}
      ${trackingHtml}
      ${task.channel_connection_id ? `
        <div class="dash-card">
          <div class="dash-card-head">
            <h2>${escapeHtml(HomezI18n.t("purchase_task.attempt_history_heading"))}</h2>
            <button type="button" class="btn btn-ghost btn-sm" id="pt-attempt-history-toggle-btn">${escapeHtml(HomezI18n.t("purchase_task.attempt_history_toggle"))}</button>
          </div>
          <div id="pt-attempt-history-body" hidden></div>
        </div>
      ` : ""}
      ${actionButtons.length ? `<div class="dialog-actions">${actionButtons.join("")}</div>` : ""}
      <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.action_hint"))}</p>
    `;

    el("pt-goto-source-order-btn").addEventListener("click", () => {
      navigateTo("order-fulfillment", { orderId: task.source_order_id });
    });

    if (showCandidates) {
      el("pt-search-links-btn").addEventListener("click", () => withButtonGuard(el("pt-search-links-btn"), () => ptShowSearchLinks(task.id)));
      el("pt-add-candidate-btn").addEventListener("click", () => ptOpenAddCandidateDialog(task.id));
      body.querySelectorAll(".pt-cand-evaluate-btn").forEach((btn) => {
        btn.addEventListener("click", () => ptOpenMatchEvaluateDialog(task, Number(btn.dataset.candId)));
      });
    }

    const wireIfExists = (id, handler) => {
      const btn = el(id);
      if (btn) btn.addEventListener("click", () => withButtonGuard(btn, handler));
    };

    wireIfExists("pt-open-payment-btn", () => ptOpenPaymentPage(task));
    wireIfExists("pt-record-purchase-btn", () => ptOpenRecordPurchaseDialog(task));
    wireIfExists("pt-extend-reservation-btn", () => ptExtendReservation(task));
    wireIfExists("pt-record-tracking-btn", () => ptOpenTrackingDialog(task));
    wireIfExists("pt-mark-delivered-btn", () => ptMarkDelivered(task));
    wireIfExists("pt-complete-task-btn", () => ptCompleteTask(task));
    wireIfExists("pt-record-refund-btn", () => ptOpenRefundDialog(task));
    wireIfExists("pt-request-cancel-btn", () => ptOpenCancelReturnDialog(task, "cancel"));
    wireIfExists("pt-request-return-btn", () => ptOpenCancelReturnDialog(task, "return"));

    wireIfExists("pt-review-run-btn", () => ptRunOrderSubmissionReview(task));
    wireIfExists("pt-tracking-refresh-btn", () => ptRefreshTracking(task));

    const historyToggleBtn = el("pt-attempt-history-toggle-btn");
    if (historyToggleBtn) {
      let loaded = false;
      historyToggleBtn.addEventListener("click", () => withButtonGuard(historyToggleBtn, async () => {
        const historyBody = el("pt-attempt-history-body");
        if (!loaded) {
          await ptLoadAttemptHistory(task, historyBody);
          loaded = true;
        }
        historyBody.hidden = !historyBody.hidden;
      }));
    }
  }

  // 2026-09-11 신규(운영 전 최종 검증 라운드, 지시문 6번) — 토글을
  // 처음 열 때만 조회한다(페이지 로드 시 자동 조회 없음). 원본
  // 응답·수취인 개인정보·JWT·API 키는 이 화면 어디에도 나타나지
  // 않는다(백엔드 응답 자체에 없음).
  async function ptLoadAttemptHistory(task, container) {

    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    let attempts;
    try {
      attempts = await apiFetch(`/purchase-tasks/${task.id}/order-approval/attempts`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    ptRenderAttemptHistory(task, container, attempts);
  }

  function ptRenderAttemptHistory(task, container, attempts) {

    if (!attempts.length) {
      container.innerHTML = `<p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.attempt_history_empty"))}</p>`;
      return;
    }

    const rows = attempts.map((a) => {
      const statusKey = a.status.toLowerCase();
      const needsResolution = a.status === "RESULT_UNKNOWN"
        && (a.unknown_resolution_status === "UNRESOLVED" || a.unknown_resolution_status === "STILL_UNCLEAR");
      return `
        <tr>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_started"))}">${fmtDate(a.started_at)}</td>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_status"))}"><span class="pill ${a.status === "SUCCEEDED" ? "ok" : (a.status === "RESULT_UNKNOWN" ? "warn" : "neutral")}">${escapeHtml(HomezI18n.t(`purchase_task.attempt_status.${statusKey}`))}</span></td>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_order_code"))}">${escapeHtml(a.external_order_code || a.unknown_resolved_order_code || "—")}</td>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_sales_application"))}">${escapeHtml(a.sales_application_status || "—")}</td>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_shipping"))}">${a.shipping_cost_confirmed ? "✓" : "—"}</td>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_approval"))}">${escapeHtml(a.order_approval_status || "—")}</td>
          <td data-label="${escapeHtml(HomezI18n.t("purchase_task.attempt_col_unknown_resolution"))}">
            ${a.status === "RESULT_UNKNOWN" ? escapeHtml(HomezI18n.t(`purchase_task.unknown_resolution.${a.unknown_resolution_status.toLowerCase()}`)) : "—"}
            ${needsResolution ? `<button type="button" class="btn btn-primary btn-sm" data-attempt-id="${a.id}" id="pt-resolve-unknown-btn-${a.id}">${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_btn"))}</button>` : ""}
          </td>
        </tr>
      `;
    }).join("");

    // 2026-09-11 후속 — 확정 폼은 <table class="responsive-cards">의
    // <tr> 안에 두지 않는다. 이 표의 모바일 카드 CSS(`table.
    // responsive-cards tr { display: ... }`)가 `[hidden]`의 기본
    // `display:none`보다 명시도가 높아 실제로 숨겨지지 않는 버그를
    // 브라우저로 직접 재현해 발견했다 — 표 바깥의 독립된 <div>로
    // 옮겨 이 충돌 자체를 피한다.
    const forms = attempts
      .filter((a) => a.status === "RESULT_UNKNOWN"
        && (a.unknown_resolution_status === "UNRESOLVED" || a.unknown_resolution_status === "STILL_UNCLEAR"))
      .map((a) => `<div id="pt-resolve-unknown-form-row-${a.id}" hidden>${ptUnknownResolveFormHtml(a.id)}</div>`)
      .join("");

    container.innerHTML = `
      <div class="table-wrap"><table class="responsive-cards">
        <thead><tr>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_started"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_status"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_order_code"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_sales_application"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_shipping"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_approval"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.attempt_col_unknown_resolution"))}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      ${forms}
    `;

    attempts
      .filter((a) => a.status === "RESULT_UNKNOWN"
        && (a.unknown_resolution_status === "UNRESOLVED" || a.unknown_resolution_status === "STILL_UNCLEAR"))
      .forEach((a) => {
        const toggleBtn = document.getElementById(`pt-resolve-unknown-btn-${a.id}`);
        toggleBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
          const row = document.getElementById(`pt-resolve-unknown-form-row-${a.id}`);
          row.hidden = !row.hidden;
        }));
        ptWireUnknownResolveForm(task, container, a.id);
      });
  }

  function ptUnknownResolveFormHtml(attemptId) {

    return `
      <div class="detail-panel">
        <h3>${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_heading"))}</h3>
        <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_hint"))}</p>
        <form class="pt-cc-inline-form" style="display:flex" id="pt-resolve-unknown-form-${attemptId}">
          <label><input type="radio" name="resolution-${attemptId}" value="ORDER_CONFIRMED" checked> ${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_option_confirmed"))}</label>
          <label><input type="radio" name="resolution-${attemptId}" value="ORDER_NOT_CONFIRMED"> ${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_option_not_confirmed"))}</label>
          <label><input type="radio" name="resolution-${attemptId}" value="STILL_UNCLEAR"> ${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_option_unclear"))}</label>
          <label>${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_order_code_label"))}
            <input type="text" class="pt-cc-form-input" id="pt-resolve-order-code-${attemptId}"></label>
          <label>${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_basis_label"))}
            <textarea class="pt-cc-form-input" id="pt-resolve-basis-${attemptId}" maxlength="500"></textarea></label>
          <p class="field-error" id="pt-resolve-error-${attemptId}"></p>
          <div class="pt-cc-inline-form-actions">
            <button type="button" class="btn btn-primary btn-sm" id="pt-resolve-submit-${attemptId}">${escapeHtml(HomezI18n.t("purchase_task.unknown_resolve_submit_btn"))}</button>
          </div>
        </form>
      </div>
    `;
  }

  function ptWireUnknownResolveForm(task, historyContainer, attemptId) {

    const submitBtn = document.getElementById(`pt-resolve-submit-${attemptId}`);
    submitBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const errEl = document.getElementById(`pt-resolve-error-${attemptId}`);
      errEl.textContent = "";
      const form = document.getElementById(`pt-resolve-unknown-form-${attemptId}`);
      const resolution = form.querySelector(`input[name="resolution-${attemptId}"]:checked`).value;
      const orderCode = document.getElementById(`pt-resolve-order-code-${attemptId}`).value.trim();
      const basis = document.getElementById(`pt-resolve-basis-${attemptId}`).value.trim();

      if (resolution === "ORDER_CONFIRMED" && !orderCode) {
        errEl.textContent = HomezI18n.t("purchase_task.unknown_resolve_missing_input");
        return;
      }
      if (resolution === "ORDER_NOT_CONFIRMED" && !basis) {
        errEl.textContent = HomezI18n.t("purchase_task.unknown_resolve_missing_input");
        return;
      }

      try {
        await apiFetch(`/purchase-tasks/${task.id}/order-approval/attempts/${attemptId}/resolve-unknown`, {
          method: "POST",
          body: JSON.stringify({
            resolution,
            order_code: resolution === "ORDER_CONFIRMED" ? orderCode : null,
            basis: basis || null,
          }),
        });
        toast(HomezI18n.t("purchase_task.unknown_resolve_success"), "success");
        await ptLoadAttemptHistory(task, historyContainer);
      } catch (err) {
        errEl.textContent = (err && err.message) || HomezI18n.t("purchase_task.action_error");
      }
    }));
  }

  // 2026-09-11 후속(운영 전 최종 검증 라운드, "송장 다시 조회") —
  // 실제 매입처 API 호출이 나간다. 성공하든 실패하든 화면을 다시
  // 그려 마지막 조회시각·결과를 반영한다.
  async function ptRefreshTracking(task) {

    try {
      await apiFetch(`/purchase-tasks/${task.id}/tracking/refresh`, {
        method: "POST",
      });
      toast(HomezI18n.t("purchase_task.tracking_refresh_btn"), "success");
    } catch (err) {
      toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
    }
    loadPurchaseTaskDetail({ id: task.id });
  }

  // 2026-09-09 후속("발주 전 최종 검토 화면") — 검토 결과는 이
  // 함수가 호출될 때만 조회한다(페이지 로드 시 자동 조회 없음 —
  // 사람이 상품코드·옵션을 직접 입력하고 눌러야 한다). 이 함수는
  // 오직 GET 조회 성격의 POST(부수효과 없음, 서버 쪽 주석 참고)만
  // 호출한다 — 실제 발주 API는 어디에도 연결돼 있지 않다.
  async function ptRunOrderSubmissionReview(task, recentAuthToken) {
    const resultEl = el("pt-review-result");
    const productCode = el("pt-review-product-code").value.trim();
    const optionId = el("pt-review-option-id").value.trim();
    const qty = Number(el("pt-review-qty").value) || 1;
    if (!productCode || !optionId) {
      resultEl.innerHTML = `<p class="field-error">${escapeHtml(HomezI18n.t("purchase_task.review_missing_input"))}</p>`;
      return;
    }

    resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    let review;
    try {
      const headers = recentAuthToken ? { "X-Recent-Auth-Token": recentAuthToken } : {};
      review = await apiFetch(`/purchase-tasks/${task.id}/order-submission-review`, {
        method: "POST",
        body: JSON.stringify({
          external_product_id: productCode,
          options: [{ id: optionId, qty }],
        }),
        headers,
      });
    } catch (err) {
      renderErrorState(resultEl, err);
      return;
    }

    ptRenderOrderSubmissionReview(resultEl, task, review);
  }

  function ptRenderOrderSubmissionReview(resultEl, task, review) {
    const p = review.product;
    const optionsRows = p.options.length ? p.options.map((o) => `
      <tr>
        <td data-label="${escapeHtml(HomezI18n.t("purchase_task.review_col_option"))}">${escapeHtml(o.label)} (${escapeHtml(o.option_id)})</td>
        <td data-label="${escapeHtml(HomezI18n.t("purchase_task.review_col_price"))}">${o.price === null || o.price === undefined ? escapeHtml(HomezI18n.t("purchase_task.review_price_unknown")) : fmtMoney(o.price)}</td>
        <td data-label="${escapeHtml(HomezI18n.t("purchase_task.review_col_stock"))}">${o.in_stock === false ? escapeHtml(HomezI18n.t("purchase_task.review_out_of_stock")) : (o.in_stock === true ? escapeHtml(HomezI18n.t("purchase_task.review_in_stock")) : "—")}</td>
      </tr>
    `).join("") : `<tr><td colspan="3">—</td></tr>`;

    const warnings = [];
    if (review.product_title_mismatch_warning) {
      warnings.push(HomezI18n.t("purchase_task.review_title_mismatch_warning"));
    }
    if (review.quantity_mismatch_warning) {
      warnings.push(HomezI18n.t("purchase_task.review_quantity_mismatch_warning"));
    }

    const recipient = review.recipient;

    resultEl.innerHTML = `
      <dl class="detail-grid">
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_source_order_label"))}</dt>
        <dd>${escapeHtml(review.source_product_title)} × ${escapeHtml(String(review.source_order_quantity ?? "—"))}</dd>
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_connection_label"))}</dt>
        <dd>${escapeHtml(review.connection_mall_code)} — ${escapeHtml(review.connection_account_label)}</dd>
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_product_title_label"))}</dt>
        <dd>${escapeHtml(p.title || "—")}</dd>
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_estimated_amount_label"))}</dt>
        <dd>${p.estimated_item_amount === null || p.estimated_item_amount === undefined ? escapeHtml(HomezI18n.t("purchase_task.review_price_unknown")) : fmtMoney(p.estimated_item_amount)}</dd>
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_shipping_fee_label"))}</dt>
        <dd>${escapeHtml(review.shipping_fee_detail)}</dd>
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_sales_application_label"))}</dt>
        <dd>${review.sales_application.confirmed
          ? escapeHtml(HomezI18n.t("purchase_task.review_sales_application_confirmed"))
          : `<span class="field-error">${escapeHtml(HomezI18n.t("purchase_task.review_sales_application_not_confirmed"))}</span>`}</dd>
        <dt>${escapeHtml(HomezI18n.t("purchase_task.review_point_balance_label"))}</dt>
        <dd>${review.point_balance.point_interpretable
          ? fmtMoney(review.point_balance.point)
          : `<span class="field-error">${escapeHtml(HomezI18n.t("purchase_task.review_point_balance_unknown"))}</span>`}</dd>
      </dl>
      <div class="table-wrap"><table class="responsive-cards">
        <thead><tr>
          <th>${escapeHtml(HomezI18n.t("purchase_task.review_col_option"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.review_col_price"))}</th>
          <th>${escapeHtml(HomezI18n.t("purchase_task.review_col_stock"))}</th>
        </tr></thead>
        <tbody>${optionsRows}</tbody>
      </table></div>
      ${warnings.length ? `<ul class="dash-todo-list">${warnings.map((w) => `<li class="field-error">${escapeHtml(w)}</li>`).join("")}</ul>` : ""}
      <div class="detail-panel">
        <h3>${escapeHtml(HomezI18n.t("purchase_task.review_recipient_heading"))}</h3>
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_recipient_name"))}</dt><dd>${escapeHtml(recipient.name || "—")}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_recipient_phone"))}</dt><dd>${escapeHtml(recipient.phone || "—")}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_recipient_address"))}</dt><dd>${escapeHtml(recipient.address || "—")} (${escapeHtml(recipient.zipcode || "—")})</dd>
        </dl>
        ${!recipient.unmasked ? `<button type="button" class="btn btn-ghost btn-sm" id="pt-review-unmask-btn">${escapeHtml(HomezI18n.t("purchase_task.review_unmask_btn"))}</button>` : `<p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.review_unmasked_hint"))}</p>`}
      </div>
      ${review.blocked_reasons.length ? `
        <div class="detail-panel">
          <h3>${escapeHtml(HomezI18n.t("purchase_task.review_blocked_heading"))}</h3>
          <ul class="dash-todo-list">${review.blocked_reasons.map((r) => `<li class="field-error">${escapeHtml(r)}</li>`).join("")}</ul>
        </div>
      ` : ""}
      ${ptOrderApprovalPanelHtml(review)}
      ${ptOrderSubmitSectionHtml(review)}
      <div id="pt-review-attempt-result"></div>
    `;

    if (!recipient.unmasked) {
      el("pt-review-unmask-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        const token = await promptRecentAuthToken();
        if (token === null) return;
        await ptRunOrderSubmissionReview(task, token);
      }));
    }

    ptWireOrderApprovalHandlers(task, review);
  }

  // 2026-09-11 후속(반자동 완료 라운드 Phase 5·7) — 유효한(ACTIVE,
  // 가격 일치) 승인이 있으면 그 스냅샷(확인된 배송비·최종 필요
  // 포인트·예상 잔액·마진·만료 시각)을 보여준다. 승인이 있지만
  // 가격이 어긋났다면(matches_current_price=false) 재승인이
  // 필요하다는 경고만 보여준다 — 낡은 승인 값을 신뢰 가능한 것처럼
  // 표시하지 않는다.
  function ptOrderApprovalPanelHtml(review) {
    const a = review.order_approval;
    if (!a) return "";

    const statusLabel = HomezI18n.t(`purchase_task.approval_status.${a.status.toLowerCase()}`);
    const shippingText = a.shipping_cost_amount === null || a.shipping_cost_amount === undefined
      ? "—"
      : (a.shipping_cost_amount === 0 && a.shipping_cost_is_free_confirmed
        ? HomezI18n.t("purchase_task.review_shipping_free_label")
        : fmtMoney(a.shipping_cost_amount));

    return `
      <div class="detail-panel">
        <h3>${escapeHtml(HomezI18n.t("purchase_task.review_approval_heading"))}</h3>
        ${!a.matches_current_price ? `<p class="field-error">${escapeHtml(HomezI18n.t("purchase_task.review_approval_stale_warning"))}</p>` : ""}
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_approval_status_label"))}</dt>
          <dd><span class="pill ${a.status === "ACTIVE" && a.matches_current_price ? "ok" : "neutral"}">${escapeHtml(statusLabel)}</span></dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_approval_shipping_label"))}</dt>
          <dd>${shippingText}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_approval_required_points_label"))}</dt>
          <dd>${a.required_points === null || a.required_points === undefined ? "—" : fmtMoney(a.required_points)}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_approval_residual_points_label"))}</dt>
          <dd>${a.projected_residual_points === null || a.projected_residual_points === undefined ? "—" : fmtMoney(a.projected_residual_points)}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_approval_margin_label"))}</dt>
          <dd>${a.margin_amount === null || a.margin_amount === undefined ? "—" : `${fmtMoney(a.margin_amount)} (${(a.margin_rate * 100).toFixed(1)}%)`}</dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_approval_expires_label"))}</dt>
          <dd>${a.expires_at ? new Date(a.expires_at).toLocaleString() : "—"}</dd>
        </dl>
      </div>
    `;
  }

  // 실제 전송 버튼을 강조(primary)로 보여줄지, 아니면 배송비 입력/
  // 최종 승인 폼을 보여줄지는 정확히 하나의 조건으로만 갈린다 —
  // "지금 눌러야 할 행동 하나만 강조" 원칙(이 화면의 다른 곳과
  // 동일). 세 상태를 섞어 한 화면에 전부 펼쳐두지 않는다.
  function ptOrderSubmitSectionHtml(review) {
    const a = review.order_approval;
    const ready = !review.send_blocked && a && a.status === "ACTIVE" && a.matches_current_price
      && review.recipient.unmasked;

    if (ready) {
      return `
        <div class="dialog-actions">
          <button type="button" class="btn btn-primary" id="pt-review-send-btn">${escapeHtml(HomezI18n.t("purchase_task.review_send_btn"))}</button>
        </div>
      `;
    }

    const needsShippingInput = !a || ["PENDING_SHIPPING_COST", "EXPIRED", "INVALIDATED_PRICE_CHANGE", "INVALIDATED_SHIPPING_CHANGE"].includes(a.status) || !a.matches_current_price;
    const priceAndPointsKnown = review.product.estimated_item_amount !== null && review.product.estimated_item_amount !== undefined
      && review.point_balance.point_interpretable;
    const canFinalize = a && a.shipping_cost_amount !== null && a.shipping_cost_amount !== undefined
      && a.matches_current_price && priceAndPointsKnown;

    return `
      <div class="detail-panel">
        <h3>${escapeHtml(HomezI18n.t("purchase_task.review_shipping_form_heading"))}</h3>
        <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.review_shipping_form_hint"))}</p>
        <form id="pt-shipping-cost-form" class="pt-cc-inline-form" style="display:flex">
          <label>${escapeHtml(HomezI18n.t("purchase_task.review_shipping_amount_label"))}
            <input type="number" class="pt-cc-form-input" id="pt-shipping-amount" min="0" step="1" required>
          </label>
          <label style="display:flex;align-items:center;gap:6px;">
            <input type="checkbox" id="pt-shipping-free"> ${escapeHtml(HomezI18n.t("purchase_task.review_shipping_free_label"))}
          </label>
          <label>${escapeHtml(HomezI18n.t("purchase_task.review_shipping_source_label"))}
            <select class="pt-cc-form-input" id="pt-shipping-source">
              <option value="ONCHANNEL_PRODUCT_PAGE">${escapeHtml(HomezI18n.t("purchase_task.review_shipping_source_onchannel_page"))}</option>
              <option value="SUPPLIER_NOTICE">${escapeHtml(HomezI18n.t("purchase_task.review_shipping_source_supplier_notice"))}</option>
              <option value="ONCHANNEL_SUPPORT_ANSWER">${escapeHtml(HomezI18n.t("purchase_task.review_shipping_source_onchannel_support"))}</option>
              <option value="OTHER_USER_CONFIRMED">${escapeHtml(HomezI18n.t("purchase_task.review_shipping_source_other"))}</option>
            </select>
          </label>
          <label>${escapeHtml(HomezI18n.t("purchase_task.review_shipping_memo_label"))}
            <input type="text" class="pt-cc-form-input" id="pt-shipping-memo" maxlength="500">
          </label>
          <p class="field-error" id="pt-shipping-form-error"></p>
          <div class="pt-cc-inline-form-actions">
            <button type="button" class="btn ${needsShippingInput ? "btn-primary" : "btn-secondary"} btn-sm" id="pt-shipping-submit-btn">${escapeHtml(HomezI18n.t("purchase_task.review_shipping_submit_btn"))}</button>
          </div>
        </form>
        <div class="dialog-actions">
          <button type="button" class="btn ${canFinalize && !needsShippingInput ? "btn-primary" : "btn-secondary"} btn-sm" id="pt-finalize-btn" ${canFinalize ? "" : "disabled"}>${escapeHtml(HomezI18n.t("purchase_task.review_finalize_btn"))}</button>
        </div>
      </div>
      <div class="dialog-actions">
        <button type="button" class="btn btn-secondary" id="pt-review-send-btn" disabled title="${escapeHtml(HomezI18n.t("purchase_task.review_send_disabled_hint"))}">${escapeHtml(HomezI18n.t("purchase_task.review_send_btn"))}</button>
      </div>
      <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.review_send_disabled_hint"))}</p>
    `;
  }

  function ptWireOrderApprovalHandlers(task, review) {
    const productCode = el("pt-review-product-code").value.trim();
    const optionId = el("pt-review-option-id").value.trim();
    const qty = Number(el("pt-review-qty").value) || 1;

    const shippingBtn = document.getElementById("pt-shipping-submit-btn");
    if (shippingBtn) {
      shippingBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        const errEl = el("pt-shipping-form-error");
        errEl.textContent = "";
        const amountRaw = el("pt-shipping-amount").value;
        const isFree = el("pt-shipping-free").checked;
        const source = el("pt-shipping-source").value;
        const memo = el("pt-shipping-memo").value.trim();
        if (amountRaw === "") {
          errEl.textContent = HomezI18n.t("purchase_task.review_missing_input");
          return;
        }
        try {
          await apiFetch(`/purchase-tasks/${task.id}/order-approval/shipping-cost`, {
            method: "POST",
            body: JSON.stringify({
              shipping_cost_amount: Number(amountRaw),
              is_free_shipping_confirmed: isFree,
              source, basis_memo: memo || null,
              external_product_id: productCode, connection_id: review.connection_id,
            }),
          });
          toast(HomezI18n.t("purchase_task.review_shipping_submit_success"), "success");
          await ptRunOrderSubmissionReview(task, null);
        } catch (err) {
          errEl.textContent = err.message || HomezI18n.t("purchase_task.cc_action_error");
        }
      }));
    }

    const finalizeBtn = document.getElementById("pt-finalize-btn");
    if (finalizeBtn && !finalizeBtn.disabled) {
      finalizeBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        try {
          await apiFetch(`/purchase-tasks/${task.id}/order-approval/finalize`, {
            method: "POST",
            body: JSON.stringify({
              connection_id: review.connection_id,
              item_amount: Math.round(review.product.estimated_item_amount),
              current_points: review.point_balance.point,
            }),
          });
          toast(HomezI18n.t("purchase_task.review_finalize_success"), "success");
          await ptRunOrderSubmissionReview(task, null);
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      }));
    }

    const sendBtn = document.getElementById("pt-review-send-btn");
    if (sendBtn && !sendBtn.disabled) {
      sendBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        const recipient = review.recipient;
        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("purchase_task.review_send_btn"),
          body: HomezI18n.t("purchase_task.review_submit_confirm_dialog", {
            product: review.product.title || productCode,
            name: recipient.name || "—",
            address: `${recipient.address || "—"} (${recipient.zipcode || "—"})`,
          }),
          okLabel: HomezI18n.t("purchase_task.review_send_btn"),
        });
        if (!confirmed) return;

        const token = await promptRecentAuthToken();
        if (token === null) return;

        const resultEl = el("pt-review-attempt-result");
        resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
        try {
          const attempt = await apiFetch(`/purchase-tasks/${task.id}/order-approval/submit`, {
            method: "POST",
            headers: { "X-Recent-Auth-Token": token },
            body: JSON.stringify({
              connection_id: review.connection_id,
              // 시각을 넣지 않는다 — 같은 작업·연결·상품·옵션에
              // 대한 반복 클릭·페이지 재로드·중복 탭이 항상 같은
              // idempotency_key를 만들어야 DB UNIQUE 제약이 실제
              // 중복 발주를 막을 수 있다(지시문 8번, "company+
              // connection+PurchaseTask+sale_code 기반 안정적 키").
              // 이미 한 번 시도된 조합은 재시도 시 서버가 명시적
              // 오류로 거부한다 — 새 시도가 필요하면 사람이 근거를
              // 확인한 뒤 옵션을 바꾸거나 별도 절차로 새 키를 받아야
              // 한다(자동으로 새 시각 기반 키를 발급하지 않는다).
              idempotency_key: `pt-${task.id}-${review.connection_id}-${productCode}-${optionId}-${qty}`,
              product_code: productCode,
              options: [{ id: optionId, qty }],
              recv_name: recipient.name, recv_tell: recipient.phone,
              recv_mobile: recipient.phone, zipcode: recipient.zipcode,
              address: recipient.address,
              confirm_real_submission: true,
            }),
          });
          ptRenderOrderSubmissionAttemptResult(resultEl, attempt);
          toast(HomezI18n.t("purchase_task.review_submit_success", { status: attempt.status }), "success");
        } catch (err) {
          renderErrorState(resultEl, err);
        }
      }));
    }
  }

  function ptRenderOrderSubmissionAttemptResult(resultEl, attempt) {
    const isUnknown = attempt.status === "RESULT_UNKNOWN";
    resultEl.innerHTML = `
      <div class="detail-panel">
        <dl class="detail-grid">
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_attempt_status_label"))}</dt>
          <dd><span class="pill ${attempt.status === "SUCCEEDED" ? "ok" : (isUnknown ? "warn" : "danger")}">${escapeHtml(attempt.status)}</span></dd>
          <dt>${escapeHtml(HomezI18n.t("purchase_task.review_order_code_label"))}</dt>
          <dd>${escapeHtml(attempt.external_order_code || "—")}</dd>
        </dl>
        ${isUnknown ? `<p class="field-error">${escapeHtml(HomezI18n.t("purchase_task.review_submit_unknown_warning"))}</p>` : ""}
        ${attempt.failure_detail ? `<p class="field-hint">${escapeHtml(attempt.failure_detail)}</p>` : ""}
      </div>
    `;
  }

  async function ptShowSearchLinks(taskId) {
    const wrap = el("pt-search-links-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    try {
      const result = await apiFetch(`/purchase-tasks/${taskId}/search-links`);
      wrap.innerHTML = `
        <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.search_keyword_label"))}: ${escapeHtml(result.keyword)}</p>
        <ul class="dash-todo-list">
          ${Object.entries(result.urls).map(([mall, url]) => `
            <li><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(ptMallLabel(mall))}</a></li>
          `).join("")}
        </ul>
      `;
    } catch (err) {
      renderErrorState(wrap, err);
    }
  }

  function ptOpenAddCandidateDialog(taskId) {
    const dialog = el("pt-candidate-dialog");
    ["pt-cand-url", "pt-cand-brand", "pt-cand-manufacturer", "pt-cand-model", "pt-cand-gtin",
      "pt-cand-capacity", "pt-cand-color", "pt-cand-price", "pt-cand-shipping",
      "pt-cand-days", "pt-cand-trust"].forEach((id) => { el(id).value = ""; });
    el("pt-cand-mall").value = "NAVER_SHOPPING";
    el("pt-cand-return-allowed").checked = false;
    el("pt-candidate-error").textContent = "";

    const submitBtn = ptFreshButton("pt-candidate-submit");
    const cancelBtn = ptFreshButton("pt-candidate-cancel");

    function cleanup() {
      submitBtn.removeEventListener("click", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSubmit() {
      const url = el("pt-cand-url").value.trim();
      if (!url) {
        el("pt-candidate-error").textContent = HomezI18n.t("purchase_task.candidate_url_required_error");
        return;
      }
      submitBtn.disabled = true;
      try {
        await apiFetch(`/purchase-tasks/${taskId}/candidates`, {
          method: "POST",
          body: JSON.stringify({
            shopping_mall_code: el("pt-cand-mall").value,
            product_url: url,
            brand: el("pt-cand-brand").value || null,
            manufacturer: el("pt-cand-manufacturer").value || null,
            model_name: el("pt-cand-model").value || null,
            gtin: el("pt-cand-gtin").value || null,
            capacity: el("pt-cand-capacity").value || null,
            color_or_scent: el("pt-cand-color").value || null,
            estimated_price: el("pt-cand-price").value === "" ? null : Number(el("pt-cand-price").value),
            estimated_shipping_fee: el("pt-cand-shipping").value === "" ? null : Number(el("pt-cand-shipping").value),
            estimated_delivery_days: el("pt-cand-days").value === "" ? null : Number(el("pt-cand-days").value),
            seller_trust_score: el("pt-cand-trust").value === "" ? null : Number(el("pt-cand-trust").value),
            return_allowed: el("pt-cand-return-allowed").checked,
          }),
        });
        cleanup();
        toast(HomezI18n.t("purchase_task.candidate_added"), "success");
        loadPurchaseTaskDetail({ id: taskId });
      } catch (err) {
        submitBtn.disabled = false;
        el("pt-candidate-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    submitBtn.disabled = false;
    submitBtn.addEventListener("click", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  function ptOpenMatchEvaluateDialog(task, candidateId) {
    const dialog = el("pt-match-evaluate-dialog");
    el("pt-me-brand").value = task.brand || "";
    el("pt-me-manufacturer").value = task.manufacturer || "";
    el("pt-me-model").value = task.model_name || "";
    el("pt-me-gtin").value = task.gtin || "";
    el("pt-me-capacity").value = task.capacity || "";
    el("pt-me-color").value = task.color_or_scent || "";
    el("pt-me-match-result").innerHTML = "";
    el("pt-me-evaluate-result").innerHTML = "";
    el("pt-match-evaluate-error").textContent = "";

    const runMatchBtn = ptFreshButton("pt-me-run-match");
    const runEvalBtn = ptFreshButton("pt-me-run-evaluate");
    const cancelBtn = ptFreshButton("pt-match-evaluate-cancel");
    runEvalBtn.disabled = true;

    function sourcePayload() {
      return {
        brand: el("pt-me-brand").value || null,
        manufacturer: el("pt-me-manufacturer").value || null,
        model_name: el("pt-me-model").value || null,
        gtin: el("pt-me-gtin").value || null,
        capacity: el("pt-me-capacity").value || null,
        color_or_scent: el("pt-me-color").value || null,
        options: [],
      };
    }

    function cleanup() {
      runMatchBtn.removeEventListener("click", onRunMatch);
      runEvalBtn.removeEventListener("click", onRunEvaluate);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onRunMatch() {
      try {
        const result = await apiFetch(`/purchase-tasks/${task.id}/candidates/${candidateId}/match-check`, {
          method: "POST",
          body: JSON.stringify({ source: sourcePayload() }),
        });
        const pct = result.match_confidence === null || result.match_confidence === undefined ? null : Math.round(result.match_confidence * 100);
        el("pt-me-match-result").innerHTML = `
          <p><strong>${escapeHtml(HomezI18n.t("retail_purchase.match_tier_label"))}:</strong>
            <span class="${riskClass(result.match_confidence)}">${escapeHtml(result.match_tier || "—")}${pct === null ? "" : ` (${pct}%)`}</span>
          </p>
        `;
        runEvalBtn.disabled = false;
      } catch (err) {
        el("pt-me-match-result").innerHTML = "";
        el("pt-match-evaluate-error").textContent = (err && err.message) || HomezI18n.t("purchase_task.action_error");
      }
    }

    async function onRunEvaluate() {
      try {
        const result = await apiFetch(`/purchase-tasks/${task.id}/candidates/${candidateId}/evaluate`, {
          method: "POST",
          body: JSON.stringify({ source: sourcePayload() }),
        });
        el("pt-me-evaluate-result").innerHTML = `
          <dl class="detail-grid">
            <dt>${escapeHtml(HomezI18n.t("purchase_task.evaluate_decision_label"))}</dt><dd>${escapeHtml(result.decision)}</dd>
            <dt>${escapeHtml(HomezI18n.t("purchase_task.evaluate_reasons_label"))}</dt><dd>${result.reasons.map(escapeHtml).join("; ") || "—"}</dd>
            <dt>${escapeHtml(HomezI18n.t("purchase_task.field_expected_net_profit"))}</dt><dd>${result.task.expected_net_profit ?? "—"}</dd>
          </dl>
        `;
        toast(HomezI18n.t("purchase_task.evaluate_done"), result.decision === "ALLOW" ? "success" : "info");
        cleanup();
        loadPurchaseTaskDetail({ id: task.id });
      } catch (err) {
        el("pt-match-evaluate-error").textContent = (err && err.message) || HomezI18n.t("purchase_task.action_error");
      }
    }

    runMatchBtn.addEventListener("click", () => withButtonGuard(runMatchBtn, onRunMatch));
    runEvalBtn.addEventListener("click", () => withButtonGuard(runEvalBtn, onRunEvaluate));
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  async function ptOpenPaymentPage(task) {
    try {
      const candidates = await apiFetch(`/purchase-tasks/${task.id}/candidates`);
      const selected = candidates.find((c) => c.is_selected);
      await apiFetch(`/purchase-tasks/${task.id}/open-payment-page`, { method: "POST" });
      if (selected) {
        window.open(selected.product_url, "_blank", "noopener");
      }
      toast(HomezI18n.t("purchase_task.payment_page_opened"), "success");
      loadPurchaseTaskDetail({ id: task.id });
    } catch (err) {
      toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
    }
  }

  function ptOpenRecordPurchaseDialog(task) {
    const dialog = el("pt-record-purchase-dialog");
    el("pt-rp-mall").value = "NAVER_SHOPPING";
    el("pt-rp-order-number").value = "";
    el("pt-rp-amount").value = "";
    el("pt-rp-shipping").value = "";
    el("pt-rp-purchased-at").value = "";
    el("pt-rp-option").value = "";
    el("pt-rp-memo").value = "";
    el("pt-record-purchase-error").textContent = "";

    const submitBtn = ptFreshButton("pt-record-purchase-submit");
    const cancelBtn = ptFreshButton("pt-record-purchase-cancel");

    function cleanup() {
      submitBtn.removeEventListener("click", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSubmit() {
      const orderNumber = el("pt-rp-order-number").value.trim();
      const amount = Number(el("pt-rp-amount").value);
      const purchasedAtRaw = el("pt-rp-purchased-at").value;
      if (!orderNumber || !amount || !purchasedAtRaw) {
        el("pt-record-purchase-error").textContent = HomezI18n.t("purchase_task.record_purchase_required_error");
        return;
      }
      submitBtn.disabled = true;
      try {
        await apiFetch(`/purchase-tasks/${task.id}/record-purchase`, {
          method: "POST",
          body: JSON.stringify({
            shopping_mall_code: el("pt-rp-mall").value,
            external_order_number: orderNumber,
            actual_amount: amount,
            actual_shipping_fee: el("pt-rp-shipping").value === "" ? null : Number(el("pt-rp-shipping").value),
            purchased_at: new Date(purchasedAtRaw).toISOString(),
            selected_option_note: el("pt-rp-option").value || null,
            memo: el("pt-rp-memo").value || null,
            idempotency_key: `pt-rec:${task.id}:${Date.now()}`,
          }),
        });
        cleanup();
        toast(HomezI18n.t("purchase_task.purchase_recorded"), "success");
        loadPurchaseTaskDetail({ id: task.id });
      } catch (err) {
        submitBtn.disabled = false;
        el("pt-record-purchase-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    submitBtn.disabled = false;
    submitBtn.addEventListener("click", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  async function ptExtendReservation(task) {
    const { confirmed } = await confirmDialog({
      title: HomezI18n.t("purchase_task.extend_reservation_confirm_title"),
      body: HomezI18n.t("purchase_task.extend_reservation_confirm_body"),
      okLabel: HomezI18n.t("purchase_task.action_extend_reservation"),
    });
    if (!confirmed) return;
    try {
      await apiFetch(`/purchase-tasks/${task.id}/extend-reservation`, {
        method: "POST",
        body: JSON.stringify({ extra_hours: 24 }),
      });
      toast(HomezI18n.t("purchase_task.reservation_extended"), "success");
      loadPurchaseTaskDetail({ id: task.id });
    } catch (err) {
      toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
    }
  }

  function ptOpenTrackingDialog(task) {
    const dialog = el("pt-tracking-dialog");
    el("pt-tr-courier").value = "";
    el("pt-tr-courier-confirmed").checked = false;
    el("pt-tr-number").value = "";
    el("pt-tr-shipped-at").value = "";
    el("pt-tr-arrival").value = "";
    el("pt-tr-partial").checked = false;
    el("pt-tracking-error").textContent = "";

    const submitBtn = ptFreshButton("pt-tracking-submit");
    const cancelBtn = ptFreshButton("pt-tracking-cancel");

    function cleanup() {
      submitBtn.removeEventListener("click", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSubmit() {
      submitBtn.disabled = true;
      try {
        await apiFetch(`/purchase-tasks/${task.id}/tracking`, {
          method: "POST",
          body: JSON.stringify({
            courier: el("pt-tr-courier").value || null,
            courier_confirmed: el("pt-tr-courier-confirmed").checked,
            tracking_number: el("pt-tr-number").value || null,
            shipped_at: el("pt-tr-shipped-at").value ? new Date(el("pt-tr-shipped-at").value).toISOString() : null,
            expected_arrival_at: el("pt-tr-arrival").value ? new Date(el("pt-tr-arrival").value).toISOString() : null,
            is_partial_shipment: el("pt-tr-partial").checked,
          }),
        });
        cleanup();
        toast(HomezI18n.t("purchase_task.tracking_recorded"), "success");
        loadPurchaseTaskDetail({ id: task.id });
      } catch (err) {
        submitBtn.disabled = false;
        el("pt-tracking-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    submitBtn.disabled = false;
    submitBtn.addEventListener("click", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  async function ptMarkDelivered(task) {
    const { confirmed } = await confirmDialog({
      title: HomezI18n.t("purchase_task.mark_delivered_confirm_title"),
      body: HomezI18n.t("purchase_task.mark_delivered_confirm_body"),
      okLabel: HomezI18n.t("purchase_task.action_mark_delivered"),
    });
    if (!confirmed) return;
    try {
      await apiFetch(`/purchase-tasks/${task.id}/delivered`, { method: "POST" });
      toast(HomezI18n.t("purchase_task.marked_delivered"), "success");
      loadPurchaseTaskDetail({ id: task.id });
    } catch (err) {
      toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
    }
  }

  async function ptCompleteTask(task) {
    const { confirmed } = await confirmDialog({
      title: HomezI18n.t("purchase_task.complete_confirm_title"),
      body: HomezI18n.t("purchase_task.complete_confirm_body"),
      okLabel: HomezI18n.t("purchase_task.action_complete"),
    });
    if (!confirmed) return;
    try {
      await apiFetch(`/purchase-tasks/${task.id}/complete`, { method: "POST" });
      toast(HomezI18n.t("purchase_task.task_completed"), "success");
      loadPurchaseTaskDetail({ id: task.id });
    } catch (err) {
      toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
    }
  }

  function ptOpenRefundDialog(task) {
    const dialog = el("pt-refund-dialog");
    el("pt-rf-amount").value = "";
    el("pt-rf-memo").value = "";
    el("pt-refund-error").textContent = "";

    const submitBtn = ptFreshButton("pt-refund-submit");
    const cancelBtn = ptFreshButton("pt-refund-cancel");

    function cleanup() {
      submitBtn.removeEventListener("click", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSubmit() {
      const amount = el("pt-rf-amount").value;
      submitBtn.disabled = true;
      try {
        await apiFetch(`/purchase-tasks/${task.id}/refund`, {
          method: "POST",
          body: JSON.stringify({
            refund_amount: amount === "" ? 0 : Number(amount),
            memo: el("pt-rf-memo").value || null,
          }),
        });
        cleanup();
        toast(HomezI18n.t("purchase_task.refund_recorded"), "success");
        loadPurchaseTaskDetail({ id: task.id });
      } catch (err) {
        submitBtn.disabled = false;
        el("pt-refund-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    submitBtn.disabled = false;
    submitBtn.addEventListener("click", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  function ptOpenCancelReturnDialog(task, mode) {
    const dialog = el("pt-cancel-return-dialog");
    el("pt-cancel-return-title").textContent = mode === "cancel"
      ? HomezI18n.t("purchase_task.cancel_title")
      : HomezI18n.t("purchase_task.return_title");
    el("pt-cr-reason").value = "";
    el("pt-cancel-return-error").textContent = "";

    const submitBtn = ptFreshButton("pt-cancel-return-submit");
    const cancelBtn = ptFreshButton("pt-cancel-return-cancel");

    function cleanup() {
      submitBtn.removeEventListener("click", onSubmit);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
    }

    async function onSubmit() {
      const reason = el("pt-cr-reason").value.trim();
      if (!reason) {
        el("pt-cancel-return-error").textContent = HomezI18n.t("purchase_task.reason_required_error");
        return;
      }
      submitBtn.disabled = true;
      try {
        await apiFetch(`/purchase-tasks/${task.id}/${mode === "cancel" ? "cancel" : "return"}`, {
          method: "POST",
          body: JSON.stringify({ reason }),
        });
        cleanup();
        toast(HomezI18n.t("purchase_task.cancel_return_requested"), "success");
        loadPurchaseTaskDetail({ id: task.id });
      } catch (err) {
        submitBtn.disabled = false;
        el("pt-cancel-return-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    submitBtn.disabled = false;
    submitBtn.addEventListener("click", onSubmit);
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  // 2026-09-08 V7 통합 매입(docs/HOMEZ_V7_INTEGRATED_PROCUREMENT_
  // GOALS_20260907.md §3 "매입 운영 설정") — 사용자가 확정한 7개
  // 항목을 하나의 요약 화면으로 정리한다. 4~7번은 기존 pt-policy-
  // dialog가 이미 실제로 저장·적용하는 값이므로 그 화면을 그대로
  // 재사용하고(중복 구현 없음), 1~3번(매입처 연결 계정/기본·대체
  // 구매 방식/결제수단 연결 상태)은 아직 이 저장소 어디에도 실제
  // 구현이 없다는 사실을 그대로 "준비 중"으로 표시한다 — 화면만
  // 있고 실제로 저장·동작하지 않는 가짜 설정 항목을 만들지 않는다
  // (과장 금지 원칙).
  async function ptOpenOperatingSettingsDialog() {
    const dialog = el("pt-operating-settings-dialog");
    const listEl = el("pt-operating-settings-list");
    listEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    dialog.showModal();

    let policy = null;
    try {
      policy = await apiFetch("/purchase-tasks/policy");
    } catch (_err) {
      // 정책 조회 실패해도 아래 1~3번(준비 중) 항목은 그대로 보여준다.
    }

    let channelConnections = [];
    try {
      channelConnections = await apiFetch("/purchase-tasks/channel-connections");
    } catch (_err) {
      // 조회 실패해도 "연결 관리 열기" 버튼은 그대로 보여준다(재시도 유도).
    }

    const implementedDetail = policy
      ? HomezI18n.t("purchase_task.os_configured_detail", {
        perOrder: policy.per_order_max_amount != null ? fmtMoney(policy.per_order_max_amount) : "—",
        daily: policy.daily_purchase_limit_amount != null ? fmtMoney(policy.daily_purchase_limit_amount) : "—",
        maxDelivery: policy.max_delivery_days != null ? policy.max_delivery_days : "—",
      })
      : "";

    const providerAccountDetail = channelConnections.length > 0
      ? HomezI18n.t("purchase_task.os_provider_accounts_connected_detail", { count: channelConnections.length })
      : HomezI18n.t("purchase_task.os_item_provider_account_detail");

    const rows = [
      {
        title: HomezI18n.t("purchase_task.os_item_provider_account"),
        detail: providerAccountDetail,
        status: channelConnections.length > 0 ? "configured" : "planned",
        action: "channel_connections",
      },
      {
        title: HomezI18n.t("purchase_task.os_item_purchase_method"),
        detail: HomezI18n.t("purchase_task.os_item_purchase_method_detail"),
        status: "planned",
      },
      {
        title: HomezI18n.t("purchase_task.os_item_payment_method"),
        detail: HomezI18n.t("purchase_task.os_item_payment_method_detail"),
        status: "planned",
      },
      {
        title: HomezI18n.t("purchase_task.os_item_limits_and_policy"),
        detail: implementedDetail || HomezI18n.t("purchase_task.os_item_limits_and_policy_detail"),
        status: "configured",
      },
      {
        title: HomezI18n.t("purchase_task.os_item_manual_execution"),
        detail: HomezI18n.t("purchase_task.os_item_manual_execution_detail"),
        status: "by_design",
      },
    ];

    const pillClassByStatus = {
      configured: "ok", credential_registered: "ok",
      by_design: "neutral", planned: "warn",
    };

    listEl.innerHTML = rows.map((row, idx) => {
      const pillClass = pillClassByStatus[row.status] || "warn";
      const pillLabel = HomezI18n.t(`purchase_task.os_status_${row.status}`);
      let actionHtml = "";
      if (row.action === "channel_connections") {
        actionHtml = `<button type="button" class="btn btn-ghost btn-sm" data-os-open-channel-connections="1">${HomezI18n.t("purchase_task.os_open_channel_connections_btn")}</button>`;
      } else if (row.status === "configured") {
        actionHtml = `<button type="button" class="btn btn-ghost btn-sm" data-os-open-policy="1">${HomezI18n.t("purchase_task.os_open_policy_btn")}</button>`;
      }
      return `
        <div class="pt-os-item" data-row-idx="${idx}">
          <div class="pt-os-item-body">
            <span class="pt-os-item-title">${escapeHtml(row.title)}</span>
            <span class="pt-os-item-detail">${escapeHtml(row.detail)}</span>
          </div>
          <div class="pt-os-item-actions">
            <span class="pill ${pillClass}">${escapeHtml(pillLabel)}</span>
            ${actionHtml}
          </div>
        </div>
      `;
    }).join("");

    listEl.querySelectorAll("[data-os-open-policy]").forEach((btn) => {
      btn.addEventListener("click", () => {
        dialog.close();
        ptOpenPolicyDialog();
      });
    });

    listEl.querySelectorAll("[data-os-open-channel-connections]").forEach((btn) => {
      btn.addEventListener("click", () => {
        dialog.close();
        ptOpenChannelConnectionsDialog();
      });
    });

    const closeBtn = el("pt-operating-settings-close");
    const onClose = () => { dialog.close(); closeBtn.removeEventListener("click", onClose); };
    closeBtn.addEventListener("click", onClose);
  }

  // 2026-09-08(item 7 "사용자 계정 기반 매입 연결") — 매입처 연결
  // 카탈로그 화면. 비밀번호·세션은 여기서도 절대 다루지 않는다 —
  // 계정 표시 이름(닉네임)만 등록하고, "연결 확인 완료"는 사람이
  // 직접 로그인해본 뒤 눌러야만 CONNECTED로 표시된다(자격증명
  // 등록만으로는 절대 자동 승격되지 않는다 — channel_adapter.py의
  // 서버 쪽 규칙과 동일).
  //
  // 2026-09-08 후속(사용자 보고) — 이전에는 이 화면 안에 완전히
  // 별개인 레거시 전역 온채널 자격증명 등록 버튼("온채널 API 키
  // 등록(선택 · B2B 발주 연동용)", /purchases/system/onchannel-
  // credential)이 연결별 "자격증명 등록" 버튼 바로 옆에 있었다 —
  // 이름이 비슷해 사용자가 실제로 이 레거시 버튼을 눌러 저장했고,
  // 그 결과가 이 화면 어디에도 반영되지 않아 "저장했는데 확인할
  // 데가 없다"는 혼동으로 이어졌다. 그 버튼과 핸들러(구
  // ptRegisterOnchannelCredential)를 이 화면에서 제거했다 — 레거시
  // 전역 슬롯(homez_onchannel_api)과 그 API 자체는 그대로 두고
  // 손대지 않았다(다른 도메인의 실제 데이터).
  const PT_CC_STATUS_PILL_CLASS = {
    CONNECTED: "ok", REGISTERED_UNVERIFIED: "warn", LOGIN_REQUIRED: "warn",
    EXPIRED: "warn", ADDITIONAL_AUTH_REQUIRED: "warn", ERROR: "warn",
    NOT_CONNECTED: "neutral",
  };
  // 상품 조회 결과는 목록 새로고침마다 DOM이 통째로 다시 그려지므로
  // (연결 확인 성공 시 상태 pill도 함께 갱신해야 해서 새로고침이
  // 필요하다), 연결 id별로 마지막 조회 결과 문구만 따로 기억해 뒀다가
  // 다시 그릴 때 그대로 복원한다.
  const ptCcLookupResults = {};
  // 2026-09-08 후속(7번 결함 감사, 3번 — "실패 상태의 의미 구분") —
  // 조회 결과 문구가 명확한 실패(오류)인지 정상 정보 표시인지
  // 구분해 표시하기 위한 플래그. 값이 없으면(=아직 조회 안 함)
  // 에러 취급하지 않는다.
  const ptCcLookupResultsError = {};
  // 2026-09-08 후속(발주·결제 계약 조사) — 포인트 조회 결과 문구.
  // lookup-result와 같은 이유로 새로고침 사이에 기억해 둔다.
  const ptCcPointCheckResults = {};
  const ptCcPointCheckResultsError = {};
  // 2026-09-08 후속(사용자 요청 — "불필요한것 선택 삭제") — 체크박스로
  // 고른 연결 id 집합. 새로고침마다 DOM은 다시 그려지지만 이 Set은
  // 모듈 스코프라 유지된다 — 다시 그릴 때 체크 상태를 그대로 복원한다.
  const ptCcSelectedForDelete = new Set();

  function ptUpdateCcBulkDeleteBar() {
    const bar = el("pt-cc-bulk-delete-bar");
    const count = ptCcSelectedForDelete.size;
    bar.hidden = count === 0;
    if (count > 0) {
      el("pt-cc-bulk-delete-count").textContent = HomezI18n.t(
        "purchase_task.cc_bulk_delete_count", { count },
      );
    }
  }

  async function ptRefreshChannelConnectionsList() {
    const listEl = el("pt-cc-list");
    listEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let connections = [];
    try {
      connections = await apiFetch("/purchase-tasks/channel-connections?include_inactive=true");
    } catch (err) {
      listEl.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("purchase_task.cc_list_error"))}</p>`;
      return;
    }

    if (connections.length === 0) {
      listEl.innerHTML = `<p class="field-hint">${HomezI18n.t("purchase_task.cc_empty")}</p>`;
      ptCcSelectedForDelete.clear();
      ptUpdateCcBulkDeleteBar();
      return;
    }

    const stillPresentIds = new Set(connections.map((c) => c.id));
    [...ptCcSelectedForDelete].forEach((id) => {
      if (!stillPresentIds.has(id)) ptCcSelectedForDelete.delete(id);
    });

    listEl.innerHTML = connections.map((c) => {
      const pillClass = PT_CC_STATUS_PILL_CLASS[c.status] || "neutral";
      const isCredentialMethod = c.connection_method === "CREDENTIAL";
      // 2026-09-08 재정정 — CREDENTIAL 방식은 NOT_CONNECTED/CONNECTED/
      // ERROR의 실제 의미가 BROWSER_LOGIN과 다르다("로그인 필요"가
      // 아니라 "자격증명 미등록", "연결됨"이 아니라 "실제 조회로
      // 인증 확인됨", "오류"가 아니라 "사용 불가"). 같은 status 값을
      // 재사용하되 표시 문구만 매입처 방식에 따라 갈라 보여준다.
      const CC_CREDENTIAL_STATUS_LABEL_KEY = {
        NOT_CONNECTED: "purchase_task.cc_status_not_connected_credential",
        CONNECTED: "purchase_task.cc_status_connected_credential",
        ERROR: "purchase_task.cc_status_error_credential",
        // 2026-09-09 후속("인증 최신성 결함 처리" UI 반영) — CREDENTIAL
        // 방식은 BROWSER_LOGIN의 "만료됨"과 문구를 공유하지 않는다.
        // 여기서는 온채널 세션이 실제로 끊긴 게 아니라(자격증명은
        // 여전히 저장돼 있다) HOMEZ가 24시간마다 재확인을 요청하는
        // 것뿐이라는 사실을 그대로 드러낸다.
        EXPIRED: "purchase_task.cc_status_expired_credential",
      };
      const pillLabelKey = (isCredentialMethod && CC_CREDENTIAL_STATUS_LABEL_KEY[c.status])
        || `purchase_task.cc_status_${c.status.toLowerCase()}`;
      const pillLabel = HomezI18n.t(pillLabelKey);
      const lastChecked = c.last_checked_at
        ? HomezI18n.t("purchase_task.cc_last_checked_at", { time: new Date(c.last_checked_at).toLocaleString() })
        : HomezI18n.t("purchase_task.cc_never_checked");
      const inactiveClass = c.is_active ? "" : " pt-cc-inactive";

      // 2026-09-08 후속(사용자 보고 — "조작하기 어렵다") — 버튼을
      // 전부 같은 굵기의 회색 버튼으로 나열하지 않는다. 지금 상태에서
      // 다음에 눌러야 할 행동 하나만 강조(primary)하고, 나머지는
      // "관리" 묶음으로 아래에 작게 둔다.
      const primaryActions = [];
      const manageActions = [];
      let hintHtml = "";
      if (c.is_active) {
        if (isCredentialMethod) {
          // 2026-09-08 재정정(id=3/4 사고) — CREDENTIAL 방식(현재
          // 온채널)은 사람이 "연결 확인 완료"로 성공을 직접 선언할 수
          // 없다(서버도 이 요청을 거부한다) — 자격증명이 없는데도
          // CONNECTED로 표시됐던 사고의 원인이 이 버튼이었다. 실제
          // 조회(상품 조회) 성공만이 연결 확인을 기록한다.
          const credentialCls = (c.status === "NOT_CONNECTED" || c.status === "ERROR")
            ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm";
          // 2026-09-09 후속 — EXPIRED는 자격증명이 없어진 게 아니라
          // 재확인 시점이 지났을 뿐이다(REGISTERED_UNVERIFIED와 같은
          // 이유로 "상품 조회"가 다음 행동이다 — 재확인이 곧 그
          // 조회 자체이므로 자격증명 재등록을 유도할 필요가 없다).
          const lookupCls = (c.status === "REGISTERED_UNVERIFIED" || c.status === "EXPIRED")
            ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm";
          primaryActions.push(`<button type="button" class="${credentialCls}" data-cc-save-credential="${c.id}">${HomezI18n.t("purchase_task.cc_save_credential_btn")}</button>`);
          primaryActions.push(`<button type="button" class="${lookupCls}" data-cc-lookup-product="${c.id}">${HomezI18n.t("purchase_task.cc_lookup_product_btn")}</button>`);
          if (c.status === "EXPIRED") {
            // 온채널 정책이 아니라 HOMEZ 내부 안전 기준(24시간마다
            // 재확인)이라는 사실을 화면에서 바로 알 수 있게 한다 —
            // constants.py의 CREDENTIAL_REVERIFICATION_WINDOW_HOURS.
            hintHtml = `<span class="pt-cc-item-hint">${HomezI18n.t("purchase_task.cc_credential_reverify_hint")}</span>`;
          } else if (c.status !== "CONNECTED") {
            hintHtml = `<span class="pt-cc-item-hint">${HomezI18n.t("purchase_task.cc_credential_verify_hint")}</span>`;
          }
          // 2026-09-08 후속(발주·결제 계약 조사) — 진단 전용 조회.
          // "관리" 묶음(부차 동작)에 둔다 — 등록 흐름의 핵심 단계가
          // 아니다. 이 버튼은 연결 확인 상태를 절대 바꾸지 않는다
          // (서버 계약, channel_connection_service.check_member_point
          // 참고) — 그래서 primaryActions가 아니라 여기 둔다.
          manageActions.push(`<button type="button" class="btn btn-ghost btn-sm" data-cc-check-point="${c.id}">${HomezI18n.t("purchase_task.cc_check_point_btn")}</button>`);
        // 2026-09-11 후속(Phase 11 — UI-6 재검토) — 이 매입처
        // Adapter가 실제로 무엇을 확인했는지(8개 항목: 지원/미지원/
        // 미확인)를 보여준다. 단일 조회 성공 하나로 "이 연결은
        // 발주 가능"이라고 과잉 일반화하지 않기 위해, "발주 가능"
        // 같은 요약 문구 대신 항목별 실제 값을 그대로 노출한다.
        manageActions.push(`<button type="button" class="btn btn-ghost btn-sm" data-cc-capabilities="${c.id}">${HomezI18n.t("purchase_task.cc_capabilities_btn")}</button>`);
          // BROWSER_LOGIN(사람이 직접 로그인하는 매입처)은 HOMEZ가
          // 세션을 기술적으로 확인할 방법이 전혀 없어 이 자기보고
          // 버튼이 여전히 유일한 확인 수단이다.
          const verifyCls = c.status === "CONNECTED" ? "btn btn-secondary btn-sm" : "btn btn-primary btn-sm";
          primaryActions.push(`<button type="button" class="${verifyCls}" data-cc-verify="${c.id}">${HomezI18n.t("purchase_task.cc_verify_btn")}</button>`);
        }
        manageActions.push(`<button type="button" class="btn btn-ghost btn-sm" data-cc-refresh="${c.id}">${HomezI18n.t("purchase_task.cc_refresh_status_btn")}</button>`);
        if (c.status !== "ADDITIONAL_AUTH_REQUIRED") {
          manageActions.push(`<button type="button" class="btn btn-ghost btn-sm" data-cc-additional-auth="${c.id}">${HomezI18n.t("purchase_task.cc_additional_auth_btn")}</button>`);
        }
        manageActions.push(`<button type="button" class="btn btn-ghost btn-sm" data-cc-rename="${c.id}">${HomezI18n.t("purchase_task.cc_rename_btn")}</button>`);
        manageActions.push(`<button type="button" class="btn btn-ghost btn-sm" data-cc-deactivate="${c.id}">${HomezI18n.t("purchase_task.cc_deactivate_btn")}</button>`);
      } else {
        primaryActions.push(`<button type="button" class="btn btn-primary btn-sm" data-cc-reactivate="${c.id}">${HomezI18n.t("purchase_task.cc_reactivate_btn")}</button>`);
      }
      const checkedAttr = ptCcSelectedForDelete.has(c.id) ? " checked" : "";
      // 2026-09-09 후속("UI 다시 작업" — 편의성 중심 재구성) — 상태
      // pill을 제목과 한 줄에 붙여 매입처 목록을 표(매입처 | 상태 |
      // 마지막 확인)처럼 한눈에 훑을 수 있게 했다(UI 시안 "매입처·
      // 결제 설정" 참고). 부차 동작(이름 변경·연결 해제 등)은 항상
      // 펼쳐 두지 않고 "관리" 하나로 묶어 접어 둔다 — 지금 눌러야
      // 할 행동(강조 버튼)과 가끔 쓰는 관리 동작을 시각적으로
      // 분리한다.
      const manageMenuHtml = manageActions.length ? `
        <div class="pt-cc-item-manage-wrap">
          <button type="button" class="btn btn-ghost btn-sm pt-cc-manage-toggle" data-cc-manage-toggle="${c.id}" aria-expanded="false">${HomezI18n.t("purchase_task.cc_manage_toggle_btn")}</button>
          <div class="pt-cc-item-actions-manage" data-manage-menu="${c.id}" hidden>${manageActions.join("")}</div>
        </div>
      ` : "";
      return `
        <div class="pt-cc-item${inactiveClass}" data-connection-id="${c.id}">
          <label class="pt-cc-item-select">
            <input type="checkbox" data-cc-select="${c.id}"${checkedAttr}>
          </label>
          <div class="pt-cc-item-body">
            <div class="pt-cc-item-titlerow">
              <span class="pt-cc-item-title">${escapeHtml(c.mall_code)} — ${escapeHtml(c.account_label)}</span>
              <span class="pill ${pillClass}">${escapeHtml(pillLabel)}</span>
            </div>
            <span class="pt-cc-item-detail">${escapeHtml(lastChecked)}${c.is_active ? "" : " · " + HomezI18n.t("purchase_task.cc_status_deactivated")}</span>
            ${hintHtml}
            <span class="pt-cc-item-lookup-result${ptCcLookupResultsError[c.id] ? " is-error" : ""}" data-lookup-result="${c.id}">${escapeHtml(ptCcLookupResults[c.id] || "")}</span>
            <span class="pt-cc-item-lookup-result${ptCcPointCheckResultsError[c.id] ? " is-error" : ""}" data-point-result="${c.id}">${escapeHtml(ptCcPointCheckResults[c.id] || "")}</span>
            <div class="pt-cc-inline-form" data-credential-form="${c.id}" hidden>
              <input type="password" class="pt-cc-form-input" data-credential-authkey="${c.id}" placeholder="${escapeHtml(HomezI18n.t("purchase_task.cc_credential_auth_key_label"))}" autocomplete="off">
              <input type="text" class="pt-cc-form-input" data-credential-allowedip="${c.id}" placeholder="${escapeHtml(HomezI18n.t("purchase_task.cc_credential_allowed_ip_label"))}" autocomplete="off">
              <p class="field-error" data-credential-form-error="${c.id}"></p>
              <div class="pt-cc-inline-form-actions">
                <button type="button" class="btn btn-primary btn-sm" data-credential-submit="${c.id}">${HomezI18n.t("purchase_task.cc_credential_save_btn")}</button>
                <button type="button" class="btn btn-ghost btn-sm" data-credential-form-cancel="${c.id}">${HomezI18n.t("purchase_task.cc_form_cancel_btn")}</button>
              </div>
            </div>
            <div class="pt-cc-inline-form" data-lookup-form="${c.id}" hidden>
              <input type="text" class="pt-cc-form-input" data-lookup-code="${c.id}" placeholder="${escapeHtml(HomezI18n.t("purchase_task.cc_lookup_product_field_label"))}" autocomplete="off">
              <div class="pt-cc-inline-form-actions">
                <button type="button" class="btn btn-primary btn-sm" data-lookup-submit="${c.id}">${HomezI18n.t("purchase_task.cc_lookup_product_submit_btn")}</button>
                <button type="button" class="btn btn-ghost btn-sm" data-lookup-form-cancel="${c.id}">${HomezI18n.t("purchase_task.cc_form_cancel_btn")}</button>
              </div>
            </div>
            <div class="pt-cc-capabilities-panel" data-capabilities-panel="${c.id}" hidden></div>
          </div>
          <div class="pt-cc-item-actions">
            <div class="pt-cc-item-actions-primary">${primaryActions.join("")}</div>
            ${manageMenuHtml}
          </div>
        </div>
      `;
    }).join("");

    ptUpdateCcBulkDeleteBar();

    // 2026-09-09 후속("UI 다시 작업") — "관리" 버튼 하나로 접어 둔
    // 부차 동작을 펼친다/접는다. 다른 행의 관리 메뉴가 열려 있으면
    // 같이 닫아 화면이 한 번에 여러 개 펼쳐져 지저분해지지 않게 한다.
    listEl.querySelectorAll("[data-cc-manage-toggle]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.ccManageToggle;
        const menu = listEl.querySelector(`[data-manage-menu="${id}"]`);
        const willOpen = menu.hidden;
        listEl.querySelectorAll("[data-manage-menu]").forEach((m) => { m.hidden = true; });
        listEl.querySelectorAll(".pt-cc-manage-toggle").forEach((b) => {
          b.setAttribute("aria-expanded", "false");
        });
        if (willOpen) {
          menu.hidden = false;
          btn.setAttribute("aria-expanded", "true");
        }
      };
    });

    listEl.querySelectorAll("[data-cc-select]").forEach((checkbox) => {
      checkbox.onchange = () => {
        const id = Number(checkbox.dataset.ccSelect);
        if (checkbox.checked) {
          ptCcSelectedForDelete.add(id);
        } else {
          ptCcSelectedForDelete.delete(id);
        }
        ptUpdateCcBulkDeleteBar();
      };
    });

    // 2026-09-08 후속 — window.prompt() 연쇄 대신 행 안에 직접 펼쳐지는
    // 입력 폼을 쓴다("조작하기 어렵다"는 사용자 보고 반영). 자격증명
    // 값은 여전히 이 폼을 거쳐 API 요청 바디로만 전달될 뿐, 로그·화면
    // 어디에도 원문으로 남지 않는다.
    function closeAllInlineForms() {
      listEl.querySelectorAll(".pt-cc-inline-form").forEach((f) => { f.hidden = true; });
    }

    listEl.querySelectorAll("[data-cc-save-credential]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.ccSaveCredential;
        const form = listEl.querySelector(`[data-credential-form="${id}"]`);
        const wasHidden = form.hidden;
        closeAllInlineForms();
        form.hidden = !wasHidden;
        if (!form.hidden) {
          listEl.querySelector(`[data-credential-authkey="${id}"]`).focus();
        }
      };
    });

    listEl.querySelectorAll("[data-credential-form-cancel]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.credentialFormCancel;
        listEl.querySelector(`[data-credential-form="${id}"]`).hidden = true;
      };
    });

    listEl.querySelectorAll("[data-credential-submit]").forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.credentialSubmit;
        const authKeyEl = listEl.querySelector(`[data-credential-authkey="${id}"]`);
        const allowedIpEl = listEl.querySelector(`[data-credential-allowedip="${id}"]`);
        const errEl = listEl.querySelector(`[data-credential-form-error="${id}"]`);
        errEl.textContent = "";
        const authKey = authKeyEl.value;
        if (!authKey.trim()) {
          errEl.textContent = HomezI18n.t("purchase_task.cc_credential_auth_key_required");
          return;
        }
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${id}/credential`, {
            method: "POST",
            body: JSON.stringify({ auth_key: authKey, allowed_ip: allowedIpEl.value || "" }),
          });
          authKeyEl.value = "";
          allowedIpEl.value = "";
          toast(HomezI18n.t("purchase_task.cc_save_credential_success"), "success");
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          errEl.textContent = err.message || HomezI18n.t("purchase_task.cc_action_error");
        }
      };
    });

    listEl.querySelectorAll("[data-cc-lookup-product]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.ccLookupProduct;
        const form = listEl.querySelector(`[data-lookup-form="${id}"]`);
        const wasHidden = form.hidden;
        closeAllInlineForms();
        form.hidden = !wasHidden;
        if (!form.hidden) {
          listEl.querySelector(`[data-lookup-code="${id}"]`).focus();
        }
      };
    });

    listEl.querySelectorAll("[data-lookup-form-cancel]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.lookupFormCancel;
        listEl.querySelector(`[data-lookup-form="${id}"]`).hidden = true;
      };
    });

    listEl.querySelectorAll("[data-lookup-submit]").forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.lookupSubmit;
        const codeEl = listEl.querySelector(`[data-lookup-code="${id}"]`);
        const resultEl = listEl.querySelector(`[data-lookup-result="${id}"]`);
        const code = codeEl.value.trim();
        if (!code) return;
        resultEl.classList.remove("is-error");
        resultEl.textContent = HomezI18n.t("common.loading");
        try {
          const product = await apiFetch(`/purchase-tasks/channel-connections/${id}/products/${encodeURIComponent(code)}`);
          ptCcLookupResults[id] = HomezI18n.t("purchase_task.cc_lookup_product_result", {
            title: product.title || "-", count: product.options.length,
          });
          ptCcLookupResultsError[id] = false;
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          // 2026-09-08 후속(3번 — "실패 상태의 의미 구분") — 이전에는
          // 실패 시 목록을 다시 불러오지 않았다. 그런데 명확한 인증
          // 실패(401/403)는 서버에서 status를 ERROR로 즉시 바꾸고
          // verified_at을 지운다(_record_real_check_failure) — 목록을
          // 다시 부르지 않으면 화면의 상태 pill이 실제로는 이미
          // ERROR로 바뀐 연결을 계속 "CONNECTED"로 잘못 보여줄 수
          // 있었다(사용 가능하다고 오인시키는 결함). 실패 시에도
          // pill을 반드시 다시 불러온다.
          ptCcLookupResults[id] = err.message || HomezI18n.t("purchase_task.cc_action_error");
          ptCcLookupResultsError[id] = true;
          await ptRefreshChannelConnectionsList();
        }
      };
    });

    listEl.querySelectorAll("[data-cc-refresh]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${btn.dataset.ccRefresh}/check`, { method: "POST" });
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      };
    });

    // 2026-09-08 후속(발주·결제 계약 조사) — 이 버튼은 연결 확인
    // 상태를 절대 바꾸지 않는다(서버 계약) — 그래서 성공해도
    // ptRefreshChannelConnectionsList()를 다시 부르지 않는다(상태
    // pill이 바뀔 이유가 없다, 조회 결과 문구만 갱신하면 된다).
    listEl.querySelectorAll("[data-cc-check-point]").forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.ccCheckPoint;
        const resultEl = listEl.querySelector(`[data-point-result="${id}"]`);
        resultEl.classList.remove("is-error");
        resultEl.textContent = HomezI18n.t("common.loading");
        try {
          const point = await apiFetch(`/purchase-tasks/channel-connections/${id}/point`);
          const pointText = point.point_interpretable
            ? String(point.point) : HomezI18n.t("purchase_task.cc_point_unreadable");
          ptCcPointCheckResults[id] = HomezI18n.t("purchase_task.cc_point_result", {
            account: point.member_id_masked || HomezI18n.t("purchase_task.cc_point_unreadable"),
            point: pointText,
          });
          ptCcPointCheckResultsError[id] = false;
        } catch (err) {
          // 2026-09-08 후속(3번 — "실패 상태의 의미 구분") — 명확한
          // 조회 실패를 시각적으로도 구분한다(is-error). 이 결과는
          // 여전히 연결 확인 상태(status pill)를 바꾸지 않는다(서버
          // 계약, channel_connection_service.check_member_point 참고)
          // — 그래서 실패해도 이 pill이 "CONNECTED"로 남아 있을 수
          // 있다는 사실 자체가 정확하다(포인트 조회는 상품 조회
          // 인증 기록을 절대 덮어쓰지 않는다). 다만 이 결과 문구
          // 자체는 오류로 명확히 표시해, "포인트 조회는 실패했지만
          // 옆의 상태는 CONNECTED"라는 상황이 "전체가 정상"으로
          // 오인되지 않게 한다.
          ptCcPointCheckResults[id] = err.message || HomezI18n.t("purchase_task.cc_action_error");
          ptCcPointCheckResultsError[id] = true;
        }
        resultEl.textContent = ptCcPointCheckResults[id];
        resultEl.classList.toggle("is-error", !!ptCcPointCheckResultsError[id]);
      };
    });

    // 2026-09-11 후속(Phase 11) — 실제 네트워크 호출 없는 정적 조회
    // (capability_matrix()는 저장된 값을 그대로 보여줄 뿐, 호출
    // 시점에 온채널을 다시 두드리지 않는다 — router.py 주석 참고).
    // "발주 가능" 같은 요약 대신 8개 항목 각각의 실제 값(지원/
    // 미지원/미확인)을 그대로 보여줘 단일 조회 성공을 과잉
    // 일반화하지 않는다. 판매신청은 이 8개 고정 목록에 포함되지
    // 않는다(연결 단위가 아니라 상품 단위 확인이라는 사실 자체를
    // 별도 안내문으로 구분해 보여준다).
    listEl.querySelectorAll("[data-cc-capabilities]").forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.ccCapabilities;
        const panel = listEl.querySelector(`[data-capabilities-panel="${id}"]`);
        const wasHidden = panel.hidden;
        closeAllInlineForms();
        if (!wasHidden) {
          panel.hidden = true;
          return;
        }
        panel.hidden = false;
        panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
        try {
          const result = await apiFetch(`/purchase-tasks/channel-connections/${id}/capabilities`);
          const rows = Object.entries(result.capabilities || {}).map(([code, support]) => `
            <li>
              <span>${escapeHtml(HomezI18n.t(`purchase_task.cc_capability.${code.toLowerCase()}`))}</span>
              ${statusPillHtmlLabeled(support, "purchase_task.cc_capability_support.")}
            </li>
          `).join("");
          panel.innerHTML = `
            <ul class="pt-cc-capabilities-list">${rows || `<li>—</li>`}</ul>
            <p class="field-hint">${HomezI18n.t("purchase_task.cc_capabilities_sales_application_hint")}</p>
          `;
        } catch (err) {
          panel.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("purchase_task.cc_action_error"))}</p>`;
        }
      };
    });

    listEl.querySelectorAll("[data-cc-verify]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${btn.dataset.ccVerify}/verify`, { method: "POST" });
          toast(HomezI18n.t("purchase_task.cc_verify_success"), "success");
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      };
    });

    listEl.querySelectorAll("[data-cc-additional-auth]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${btn.dataset.ccAdditionalAuth}/additional-auth-required`, { method: "POST" });
          toast(HomezI18n.t("purchase_task.cc_additional_auth_success"), "success");
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      };
    });

    listEl.querySelectorAll("[data-cc-rename]").forEach((btn) => {
      btn.onclick = async () => {
        let newLabel;
        try {
          newLabel = window.prompt(HomezI18n.t("purchase_task.cc_rename_prompt"));
        } catch (err) {
          toast(HomezI18n.t("purchase_task.os_onchannel_prompt_unsupported"), "error");
          return;
        }
        if (!newLabel || !newLabel.trim()) return;
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${btn.dataset.ccRename}`, {
            method: "PUT",
            body: JSON.stringify({ account_label: newLabel.trim() }),
          });
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      };
    });

    listEl.querySelectorAll("[data-cc-deactivate]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${btn.dataset.ccDeactivate}/deactivate`, { method: "POST" });
          toast(HomezI18n.t("purchase_task.cc_deactivate_success"), "success");
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      };
    });

    listEl.querySelectorAll("[data-cc-reactivate]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${btn.dataset.ccReactivate}/reactivate`, { method: "POST" });
          await ptRefreshChannelConnectionsList();
        } catch (err) {
          toast(err.message || HomezI18n.t("purchase_task.cc_action_error"), "error");
        }
      };
    });
  }

  async function ptOpenChannelConnectionsDialog() {
    const dialog = el("pt-channel-connections-dialog");
    el("pt-cc-add-error").textContent = "";
    el("pt-cc-label-input").value = "";
    Object.keys(ptCcLookupResults).forEach((k) => { delete ptCcLookupResults[k]; });
    Object.keys(ptCcLookupResultsError).forEach((k) => { delete ptCcLookupResultsError[k]; });
    Object.keys(ptCcPointCheckResults).forEach((k) => { delete ptCcPointCheckResults[k]; });
    Object.keys(ptCcPointCheckResultsError).forEach((k) => { delete ptCcPointCheckResultsError[k]; });
    ptCcSelectedForDelete.clear();
    dialog.showModal();
    await ptRefreshChannelConnectionsList();

    // 2026-09-08 후속(사용자 요청 — "불필요한것 선택 삭제") — "연결
    // 해제"(비활성화, 되돌릴 수 있음)와 다른 완전 삭제다. 확인 없이
    // 바로 지우지 않는다. 과거 구매 작업·매입 기록이 실제로 참조하는
    // 연결은 서버가 거부하므로(409), 선택 중 일부만 지워질 수 있어
    // 결과를 개수로 정확히 알려준다.
    el("pt-cc-bulk-delete-btn").onclick = async () => {
      const ids = [...ptCcSelectedForDelete];
      if (ids.length === 0) return;
      const confirmed = window.confirm(
        HomezI18n.t("purchase_task.cc_bulk_delete_confirm", { count: ids.length }),
      );
      if (!confirmed) return;

      let succeeded = 0;
      let blocked = 0;
      let failed = 0;
      for (const id of ids) {
        try {
          await apiFetch(`/purchase-tasks/channel-connections/${id}`, { method: "DELETE" });
          succeeded += 1;
        } catch (err) {
          if (err && err.status === 409) {
            blocked += 1;
          } else {
            failed += 1;
          }
        }
      }

      ptCcSelectedForDelete.clear();
      const resultText = HomezI18n.t("purchase_task.cc_bulk_delete_result", {
        succeeded, blocked, failed,
      });
      toast(resultText, blocked + failed > 0 ? "error" : "success");
      await ptRefreshChannelConnectionsList();
    };

    el("pt-cc-add-btn").onclick = async () => {
      const mallCode = el("pt-cc-mall-select").value;
      const label = el("pt-cc-label-input").value.trim();
      const errEl = el("pt-cc-add-error");
      errEl.textContent = "";
      if (!label) {
        errEl.textContent = HomezI18n.t("purchase_task.cc_label_required");
        return;
      }
      try {
        await apiFetch("/purchase-tasks/channel-connections", {
          method: "POST",
          body: JSON.stringify({ mall_code: mallCode, account_label: label }),
        });
        el("pt-cc-label-input").value = "";
        toast(HomezI18n.t("purchase_task.cc_add_success"), "success");
        await ptRefreshChannelConnectionsList();
      } catch (err) {
        errEl.textContent = err.message || HomezI18n.t("purchase_task.cc_add_error");
      }
    };

    const closeBtn = el("pt-channel-connections-close");
    const onClose = () => { dialog.close(); closeBtn.removeEventListener("click", onClose); };
    closeBtn.addEventListener("click", onClose);
  }

  function ptOpenPolicyDialog() {
    const dialog = el("pt-policy-dialog");
    el("pt-policy-error").textContent = "";

    (async () => {
      try {
        ptCurrentPolicy = await apiFetch("/purchase-tasks/policy");
      } catch (err) {
        toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
        return;
      }

      el("pt-pol-per-order-max").value = ptCurrentPolicy.per_order_max_amount ?? "";
      el("pt-pol-daily-limit").value = ptCurrentPolicy.daily_purchase_limit_amount ?? "";
      el("pt-pol-monthly-budget").value = ptCurrentPolicy.monthly_purchase_budget_amount ?? "";
      el("pt-pol-max-quantity").value = ptCurrentPolicy.max_quantity_per_product ?? "";
      el("pt-pol-min-profit").value = ptCurrentPolicy.min_net_profit;
      el("pt-pol-min-margin").value = ptCurrentPolicy.min_margin_rate == null ? "" : ptCurrentPolicy.min_margin_rate * 100;
      el("pt-pol-max-increase").value = ptCurrentPolicy.max_price_increase_rate == null ? "" : ptCurrentPolicy.max_price_increase_rate * 100;
      el("pt-pol-max-delivery").value = ptCurrentPolicy.max_delivery_days ?? "";
      el("pt-pol-min-match").value = ptCurrentPolicy.min_match_confidence;
      el("pt-pol-max-concurrent").value = ptCurrentPolicy.max_concurrent_tasks ?? "";
      el("pt-pol-reservation-hours").value = ptCurrentPolicy.budget_reservation_hours;
      el("pt-pol-require-return").checked = !!ptCurrentPolicy.require_return_allowed;
      el("pt-pol-password").value = "";

      const submitBtn = ptFreshButton("pt-policy-submit");
      const cancelBtn = ptFreshButton("pt-policy-cancel");

      const numOrNull = (elId) => {
        const raw = el(elId).value;
        return raw === "" ? null : Number(raw);
      };

      function cleanup() {
        submitBtn.removeEventListener("click", onSubmit);
        cancelBtn.removeEventListener("click", onCancel);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
      }

      function onCancel() {
        cleanup();
      }

      async function onSubmit() {
        const password = el("pt-pol-password").value;
        if (!password) {
          el("pt-policy-error").textContent = HomezI18n.t("ma.error_current_password_required");
          return;
        }
        submitBtn.disabled = true;
        try {
          const recentAuth = await apiFetch("/auth/recent-auth", {
            method: "POST",
            body: JSON.stringify({ current_password: password }),
          }, { skipAuthHandling: true });

          await apiFetch("/purchase-tasks/policy", {
            method: "PUT",
            headers: {
              "X-Recent-Auth-Token": recentAuth.recent_auth_token,
              "X-If-Unmodified-Since": ptCurrentPolicy.updated_at,
            },
            body: JSON.stringify({
              per_order_max_amount: numOrNull("pt-pol-per-order-max"),
              daily_purchase_limit_amount: numOrNull("pt-pol-daily-limit"),
              monthly_purchase_budget_amount: numOrNull("pt-pol-monthly-budget"),
              max_quantity_per_product: numOrNull("pt-pol-max-quantity"),
              min_net_profit: numOrNull("pt-pol-min-profit"),
              min_margin_rate: (() => { const v = numOrNull("pt-pol-min-margin"); return v === null ? null : v / 100; })(),
              max_price_increase_rate: (() => { const v = numOrNull("pt-pol-max-increase"); return v === null ? null : v / 100; })(),
              max_delivery_days: numOrNull("pt-pol-max-delivery"),
              require_return_allowed: el("pt-pol-require-return").checked,
              min_match_confidence: numOrNull("pt-pol-min-match"),
              max_concurrent_tasks: numOrNull("pt-pol-max-concurrent"),
              budget_reservation_hours: numOrNull("pt-pol-reservation-hours"),
            }),
          });

          cleanup();
          toast(HomezI18n.t("purchase_task.policy_save_success"), "success");
        } catch (err) {
          submitBtn.disabled = false;
          if (err instanceof ApiError && err.status === 401) {
            el("pt-policy-error").textContent = HomezI18n.t("ma.error_invalid_password");
          } else if (err instanceof ApiError && err.status === 409) {
            el("pt-policy-error").textContent = HomezI18n.t("purchase_task.policy_save_conflict");
          } else {
            el("pt-policy-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
          }
        }
      }

      submitBtn.disabled = false;
      submitBtn.addEventListener("click", onSubmit);
      cancelBtn.addEventListener("click", onCancel);
      dialog.addEventListener("cancel", onCancel);
      dialog.showModal();
    })();
  }

  function ptOpenEmailPrefDialog() {
    const dialog = el("pt-email-pref-dialog");
    el("pt-email-pref-error").textContent = "";
    el("pt-email-pref-status").textContent = "";
    el("pt-ep-test-email").value = "";

    (async () => {
      let pref;
      try {
        pref = await apiFetch("/purchase-tasks/email-preference");
      } catch (err) {
        toast((err && err.message) || HomezI18n.t("purchase_task.action_error"), "error");
        return;
      }
      el("pt-ep-enabled").checked = !!pref.enabled;

      const submitBtn = ptFreshButton("pt-email-pref-submit");
      const cancelBtn = ptFreshButton("pt-email-pref-cancel");
      const testBtn = ptFreshButton("pt-ep-send-test");

      function cleanup() {
        submitBtn.removeEventListener("click", onSubmit);
        cancelBtn.removeEventListener("click", onCancel);
        testBtn.removeEventListener("click", onTest);
        dialog.removeEventListener("cancel", onCancel);
        dialog.close();
      }

      function onCancel() {
        cleanup();
      }

      async function onSubmit() {
        submitBtn.disabled = true;
        try {
          await apiFetch("/purchase-tasks/email-preference", {
            method: "PUT",
            body: JSON.stringify({ enabled: el("pt-ep-enabled").checked, event_toggles: {} }),
          });
          cleanup();
          toast(HomezI18n.t("purchase_task.email_pref_saved"), "success");
        } catch (err) {
          submitBtn.disabled = false;
          el("pt-email-pref-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
        }
      }

      async function onTest() {
        const toEmail = el("pt-ep-test-email").value.trim();
        if (!toEmail) {
          el("pt-email-pref-error").textContent = HomezI18n.t("purchase_task.test_email_required_error");
          return;
        }
        try {
          const result = await apiFetch("/purchase-tasks/email-preference/test", {
            method: "POST",
            body: JSON.stringify({ to_email: toEmail }),
          });
          el("pt-email-pref-status").textContent = result.status === "PROVIDER_NOT_CONFIGURED"
            ? HomezI18n.t("purchase_task.email_provider_not_configured")
            : HomezI18n.t("purchase_task.test_email_sent", { status: result.status });
        } catch (err) {
          el("pt-email-pref-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
        }
      }

      submitBtn.disabled = false;
      submitBtn.addEventListener("click", onSubmit);
      cancelBtn.addEventListener("click", onCancel);
      testBtn.addEventListener("click", () => withButtonGuard(testBtn, onTest));
      dialog.addEventListener("cancel", onCancel);
      dialog.showModal();
    })();
  }

  function ptOpenCsvDialog() {
    const dialog = el("pt-csv-dialog");
    el("pt-csv-file").value = "";
    el("pt-csv-preview-result").innerHTML = "";
    el("pt-csv-import-result").innerHTML = "";
    el("pt-csv-error").textContent = "";

    const previewBtn = ptFreshButton("pt-csv-preview-btn");
    const importBtn = ptFreshButton("pt-csv-import-submit");
    const cancelBtn = ptFreshButton("pt-csv-cancel");

    function cleanup() {
      previewBtn.removeEventListener("click", onPreview);
      importBtn.removeEventListener("click", onImport);
      cancelBtn.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.close();
    }

    function onCancel() {
      cleanup();
      if (ptDetailCurrentId && currentView === "purchase-task-detail") {
        loadPurchaseTaskDetail({ id: ptDetailCurrentId });
      } else if (currentView === "purchase-task") {
        ptLoadTasks();
      }
    }

    async function onPreview() {
      const file = el("pt-csv-file").files[0];
      if (!file) {
        el("pt-csv-error").textContent = HomezI18n.t("purchase_task.csv_file_required_error");
        return;
      }
      const form = new FormData();
      form.append("file", file);
      try {
        const result = await apiFetch("/purchase-tasks/csv/preview", {
          method: "POST", body: form,
        });
        el("pt-csv-preview-result").innerHTML = `
          <p class="field-hint">${escapeHtml(HomezI18n.t("purchase_task.csv_row_count", { count: result.row_count }))}</p>
          ${result.column_mapping_ok ? "" : `<p class="field-error">${escapeHtml(HomezI18n.t("purchase_task.csv_missing_columns", { columns: result.missing_columns.join(", ") }))}</p>`}
        `;
      } catch (err) {
        el("pt-csv-preview-result").innerHTML = "";
        el("pt-csv-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    async function onImport() {
      const file = el("pt-csv-file").files[0];
      if (!file) {
        el("pt-csv-error").textContent = HomezI18n.t("purchase_task.csv_file_required_error");
        return;
      }
      const form = new FormData();
      form.append("file", file);
      try {
        const result = await apiFetch("/purchase-tasks/csv/import", {
          method: "POST", body: form,
        });
        el("pt-csv-import-result").innerHTML = `
          <p>${escapeHtml(HomezI18n.t("purchase_task.csv_import_summary", { success: result.success_rows, failure: result.failure_rows, total: result.total_rows }))}</p>
          ${result.failure_rows > 0 ? `
            <ul class="dash-todo-list">
              ${result.results.filter((r) => !r.success).map((r) => `<li>${escapeHtml(HomezI18n.t("purchase_task.csv_row_error", { row: r.row_number, error: r.error || "" }))}</li>`).join("")}
            </ul>
          ` : ""}
        `;
        toast(HomezI18n.t("purchase_task.csv_import_done"), result.failure_rows > 0 ? "info" : "success");
      } catch (err) {
        el("pt-csv-import-result").innerHTML = "";
        el("pt-csv-error").textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    }

    previewBtn.addEventListener("click", () => withButtonGuard(previewBtn, onPreview));
    importBtn.addEventListener("click", () => withButtonGuard(importBtn, onImport));
    cancelBtn.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  }

  const VIEW_LOADERS = {
    overview: loadOverview,
    "ai-proposals": loadAiProposals,
    "ai-proposal-detail": loadAiProposalDetail,
    "operations-priorities": loadOperationsPriorities,
    "order-exceptions": loadOrderExceptions,
    "settlement-differences": loadSettlementDifferences,
    "retail-purchase": loadRetailPurchase,
    "retail-purchase-detail": loadRetailPurchaseDetail,
    "purchase-task": loadPurchaseTask,
    "purchase-task-detail": loadPurchaseTaskDetail,
    "product-attr-comparison": loadProductAttrComparison,
    "product-attr-comparison-detail": loadProductAttrComparisonDetail,
    "recall-blocks": loadRecallBlocks,
    "recall-blocks-detail": loadRecallBlocksDetail,
    candidates: loadCandidates,
    trend: loadTrend,
    "new-product": loadNewProduct,
    safety: loadSafety,
    decision: loadDecisionList,
    "listing-package": loadListingPackageView,
    "marketplace-listing": loadMarketplaceListingWizard,
    "listing-wizard": loadListingWizardView,
    "listing-status-sync": loadListingStatusSyncView,
    "store-connection": loadStoreConnectionView,
    finance: loadFinance,
    system: loadSystem,
    "account-security": loadAccountSecurity,
    guides: loadGuides,
  };

  let currentView = "overview";

  function navigateTo(viewName, opts = {}, sourceBtn = null) {
    currentView = viewName;

    // 화면을 벗어날 때 열려있던 <dialog>를 남겨두지 않는다(실 브라우저
    // 검증 중 발견 — 뒤로가기 등으로 화면이 바뀌어도 모달이 그대로 떠
    // 있었다). close()는 "cancel"이 아니라 "close" 이벤트만 발생시키므로
    // 각 대화상자의 cleanup()은 실행되지 않지만, 다음에 그 대화상자를
    // 다시 열 때 버튼을 clone으로 교체해 이전 리스너를 정리한다(위
    // ptFreshButton 참고) — 여기서는 화면 표시만 정리하면 된다.
    document.querySelectorAll("dialog[open]").forEach((d) => d.close());

    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    document.querySelectorAll(".nav-item").forEach((b) => b.removeAttribute("aria-current"));

    const viewEl = el(`view-${viewName}`);
    if (viewEl) viewEl.classList.add("active");

    // 그룹 메뉴에서는 같은 data-view를 가리키는 항목이 둘 이상일 수
    // 있다(예: "사용자·권한"과 "설정 · 계정 및 보안"이 모두 account-
    // security로 이동) — 실제로 클릭된 버튼이 있으면 그 버튼을 강조
    // 표시한다. 클릭 없이 프로그램적으로 이동한 경우(예: 새로고침
    // 버튼)에는 기존처럼 첫 번째 일치 항목을 강조한다.
    const navBtn = sourceBtn ||
      document.querySelector(`.nav-item[data-view="${viewName}"]`);
    if (navBtn) navBtn.setAttribute("aria-current", "page");

    document.getElementById("shell").classList.remove("nav-open");

    const loader = VIEW_LOADERS[viewName];
    if (loader) loader(opts);
  }

  const COMING_SOON_TITLE_KEYS = {
    "product-list": "nav.product_list",
    "order-status": "nav.order_status",
    "shipment": "nav.shipment",
    "return-exchange": "nav.return_exchange",
    "keyword-analysis": "nav.keyword_analysis",
    "margin-analysis": "nav.margin_analysis",
    "channel-settlement": "nav.channel_settlement",
  };

  function showComingSoon(placeholderKey, sourceBtn) {
    const titleKey = COMING_SOON_TITLE_KEYS[placeholderKey] || "coming_soon.title";
    el("coming-soon-title").textContent = HomezI18n.t(titleKey);
    navigateTo("coming-soon", {}, sourceBtn);
  }

  function initNav() {
    // 2026-08-13 죽은 UI 전수 재검증(Phase B 재확인) — 대시보드 "AI 추천
    // 상품" 카드의 "더보기" 버튼(class="link-btn", data-view="decision")은
    // .nav-item이 아니므로 예전 셀렉터(".nav-item[data-view]")로는 클릭
    // 리스너가 걸리지 않았다. [data-refresh]가 이미 클래스에 의존하지
    // 않는 범용 셀렉터를 쓰는 것과 동일하게 [data-view] 전체로 넓혀서
    // nav-item이 아닌 data-view 버튼도 정상적으로 화면 전환되게 한다.
    document.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => navigateTo(btn.dataset.view, {}, btn));
    });

    // 아직 실제 API·데이터가 연결되지 않은 메뉴 — 동작하는 것처럼
    // 꾸미지 않고 "준비 중" 안내 화면으로만 이동한다(Gate UI 지시문).
    document.querySelectorAll(".nav-item-placeholder[data-placeholder]").forEach((btn) => {
      btn.addEventListener("click", () => showComingSoon(btn.dataset.placeholder, btn));
    });

    document.querySelectorAll("[data-refresh]").forEach((btn) => {
      btn.addEventListener("click", () => navigateTo(btn.dataset.refresh));
    });

    const searchInput = el("topbar-search-input");
    if (searchInput) {
      searchInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          toast(HomezI18n.t("topbar.search_not_ready"), "info");
        }
      });
    }
    wireTopbarNotifications();
    const msgBtn = el("topbar-messages-btn");
    if (msgBtn) {
      msgBtn.addEventListener("click", () => toast(HomezI18n.t("topbar.messages_not_ready"), "info"));
    }

    el("mobile-nav-toggle").addEventListener("click", () => {
      const shell = el("shell");
      const isOpen = shell.classList.toggle("nav-open");
      el("mobile-nav-toggle").setAttribute("aria-expanded", String(isOpen));
    });

    el("operator-menu-btn").addEventListener("click", () => {
      const dropdown = el("operator-menu-dropdown");
      const isHidden = dropdown.hidden;
      dropdown.hidden = !isHidden;
      el("operator-menu-btn").setAttribute("aria-expanded", String(isHidden));
    });

    document.addEventListener("click", (ev) => {
      const menu = el("operator-menu-btn");
      const dropdown = el("operator-menu-dropdown");
      if (!dropdown.hidden && !menu.contains(ev.target) && !dropdown.contains(ev.target)) {
        dropdown.hidden = true;
        menu.setAttribute("aria-expanded", "false");
      }
    });

    el("logout-btn").addEventListener("click", logout);

    el("candidate-detail-back").addEventListener("click", () => navigateTo("candidates"));
    el("decision-detail-back").addEventListener("click", () => navigateTo("decision"));

    const decisionFilter = el("decision-filter-status");
    if (decisionFilter) decisionFilter.addEventListener("change", loadDecisionList);

    el("ml-load-candidate-btn").addEventListener("click", mlLoadCandidateStep);
    el("ml-confirm-channels-btn").addEventListener("click", mlConfirmChannelsStep);
    el("ml-finalize-btn").addEventListener("click", mlFinalizeStep);

    el("lw-new-btn").addEventListener("click", lwCreateNew);
    el("lw-csv-btn").addEventListener("click", lwDownloadCsv);
    el("lw-prev-btn").addEventListener("click", lwPrevStep);
    el("lw-next-btn").addEventListener("click", lwHandleNext);
    el("lw-close-btn").addEventListener("click", lwCloseWizard);
    el("lw-show-archived-toggle").addEventListener("change", (e) => {
      lwListState.includeArchived = e.target.checked;
      lwListState.selectedIds.clear();
      lwRenderList();
    });
    el("lw-bulk-delete-selected-btn").addEventListener("click", lwBulkDeleteSelected);
    el("lw-bulk-margin-apply-btn").addEventListener("click", lwBulkApplyMargin);
    el("lw-bulk-submit-selected-btn").addEventListener("click", lwBulkSubmitSelected);

    el("lp-add-channel-row-btn").addEventListener("click", () => lpAddChannelRow());
    el("lp-create-btn").addEventListener("click", () => withButtonGuard(el("lp-create-btn"), lpCreatePackage));
    el("lp-regenerate-btn").addEventListener("click", () => withButtonGuard(el("lp-regenerate-btn"), lpRegenerateImages));
    el("lp-approve-btn").addEventListener("click", () => withButtonGuard(el("lp-approve-btn"), lpApprovePackage));
    el("lp-reject-btn").addEventListener("click", () => withButtonGuard(el("lp-reject-btn"), lpRejectPackage));
    el("lp-submit-btn").addEventListener("click", () => withButtonGuard(el("lp-submit-btn"), lpSubmitPackage));
  }

  // --------------------------------------------------
  // 공통 렌더 헬퍼
  // --------------------------------------------------

  function fmtMoney(value, currency = "KRW") {
    if (value === null || value === undefined) return "—";
    const n = Number(value);
    // 2026-08-12 Gate X-3 — "ko-KR" 하드코딩 수정: en-US로 전환해도
    // 숫자 서식(천단위 구분자 등)이 한국어 스타일로 고정돼 있던
    // 결함. HomezI18n.formatNumber()는 현재 활성 locale을 그대로
    // 따른다(통화 코드 접미사 표기 스타일 자체는 유지 — 기호형
    // formatCurrency로 바꾸지 않는다, 요구사항은 locale 반영이지
    // 표기 스타일 변경이 아니다).
    return `${HomezI18n.formatNumber(n, { maximumFractionDigits: 0 })} ${currency}`;
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      return HomezI18n.formatDate(iso, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      return iso;
    }
  }

  function riskClass(score) {
    if (score === null || score === undefined) return "";
    if (score >= 0.66) return "risk-high";
    if (score >= 0.33) return "risk-mid";
    return "risk-low";
  }

  // 2026-09-10 UI 개선(시안 01 상품 발굴·분석 참고) — 상품 후보의
  // AI 점수(trend/margin/demand/novelty/confidence/risk_score,
  // 전부 0~1 float — app/domains/product_candidate/schema.py 참고)가
  // 지금까지 맨 숫자로만 표시돼 위험·기회를 한눈에 훑기 어려웠다.
  // 원래 수치를 그대로 보여주면서(추측으로 %로 재해석하지 않음 —
  // "0~1 점수"가 실제로 비율/확률을 의미한다는 근거가 스키마에
  // 없어 %로 바꾸면 실제보다 더 정밀한 통계처럼 보일 위험이 있음)
  // 막대로 크기를 시각화만 추가한다.
  // invert=false(기본): 높을수록 좋음(trend/margin/demand/novelty/
  // confidence) — 초록이 높은 쪽. invert=true: 높을수록 나쁨
  // (risk_score) — 기존 riskClass()와 동일한 방향, risk-tag와 색상
  // 일관성 유지.
  function scoreBarHtml(score, { invert = false } = {}) {
    if (score === null || score === undefined) {
      return `<span class="stat-sub">—</span>`;
    }
    const clamped = Math.max(0, Math.min(1, score));
    const pct = Math.round(clamped * 100);
    const cls = invert
      ? riskClass(score)
      : (score >= 0.66 ? "risk-low" : score >= 0.33 ? "risk-mid" : "risk-high");
    return `
      <div class="score-bar">
        <div class="score-bar-track"><div class="score-bar-fill ${cls}" style="width:${pct}%"></div></div>
        <span class="score-bar-value">${escapeHtml(clamped.toFixed(2))}</span>
      </div>
    `;
  }

  function renderEmptyState(container, message, sub = "", actionsHtml = "") {
    container.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "empty-state";
    wrap.innerHTML = `
      <img src="/console/static/assets/homez-logo.png" alt="">
      <div class="empty-title">${escapeHtml(message)}</div>
      <div>${escapeHtml(sub)}</div>
      ${actionsHtml ? `<div class="empty-state-actions">${actionsHtml}</div>` : ""}
    `;
    container.appendChild(wrap);
  }

  function renderErrorState(container, err) {
    container.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "error-state";
    const isAuthError = err instanceof ApiError && err.status === 403;
    const isNetworkError = err instanceof ApiError && err.status === 0;
    let title = HomezI18n.t("common.error_generic");
    if (isAuthError) title = HomezI18n.t("common.error_forbidden");
    if (isNetworkError) title = HomezI18n.t("common.error_network_disconnected");
    wrap.innerHTML = `${escapeHtml(title)}<span class="error-detail">${escapeHtml((err && err.message) || "")}</span>`;
    container.appendChild(wrap);
  }

  function renderSchemaWarning(container, message) {
    const warn = document.createElement("div");
    warn.className = "schema-warning";
    warn.textContent = message;
    container.prepend(warn);
  }

  function escapeHtml(s) {
    return String(s === undefined || s === null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  // 한국어 우선 표기 원칙 — 내부 영문 상태값을 화면에 그대로 노출하지
  // 않는다(번역 키가 없으면 개발자가 놓친 값이라는 뜻이므로, 조용히
  // 숨기지 않고 원문을 그대로 보여줘 눈에 띄게 한다).
  function candidateStatusLabel(status) {
    const key = `candidates.status.${String(status).toLowerCase()}`;
    const label = HomezI18n.t(key);
    return label === key ? status : label;
  }

  function mlStatusLabel(status) {
    const key = `ml.status.${String(status).toLowerCase()}`;
    const label = HomezI18n.t(key);
    return label === key ? status : label;
  }

  function decisionStatusLabel(status) {
    const key = `decision.status.${String(status).toLowerCase()}`;
    const label = HomezI18n.t(key);
    return label === key ? status : label;
  }

  function mlSubmissionStatusLabel(status) {
    const key = `ml.submission_status.${String(status).toLowerCase()}`;
    const label = HomezI18n.t(key);
    return label === key ? status : label;
  }

  // --------------------------------------------------
  // 개요
  // --------------------------------------------------

  // Gate UI(2026-08-09) — "개요"를 실제 운영 Dashboard로 확장했다.
  // 매출·주문·마진처럼 이 저장소에 아직 집계 API가 없는 지표는
  // 0이나 임의 값을 만들지 않고 명시적 빈 상태로 표시한다(지시문
  // 원칙: "API가 없거나 데이터가 없으면 정확한 빈 상태를 표시한다").
  // 상품 후보 발견 수·Decision AI 추천 목록·자동화 안전 상태처럼
  // 이미 실제 API가 있는 값만 실제 수치로 채운다.

  function kpiCard(label, value, iconSvg, iconClass, deltaText) {
    const card = document.createElement("div");
    card.className = "kpi-card";
    const isEmpty = value === null || value === undefined;
    card.innerHTML = `
      <div class="kpi-card-main">
        <div class="kpi-label">${escapeHtml(label)}</div>
        <div class="kpi-value${isEmpty ? " is-empty" : ""}">${isEmpty ? escapeHtml(HomezI18n.t("dashboard.kpi_no_data")) : escapeHtml(String(value))}</div>
        ${deltaText ? `<div class="kpi-delta">${escapeHtml(deltaText)}</div>` : ""}
      </div>
      <div class="kpi-icon ${iconClass}">${iconSvg}</div>
    `;
    return card;
  }

  const KPI_ICONS = {
    revenue: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="2" y="7" width="20" height="12" rx="2" stroke="currentColor" stroke-width="2" fill="none"/><circle cx="12" cy="13" r="2.5" stroke="currentColor" stroke-width="2" fill="none"/></svg>',
    orders: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M6 2h9l3 3v17H6z" stroke="currentColor" stroke-width="2" fill="none" stroke-linejoin="round"/><path d="M9 9h6M9 13h6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    margin: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M3 12h4l3-8 4 16 3-8h4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    ai: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 2l2 5 5 2-5 2-2 5-2-5-5-2 5-2 2-5z" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linejoin="round"/></svg>',
  };

  async function loadOverview() {
    const kpiGrid = el("dash-kpi-grid");
    const todoList = el("dash-todo-list");
    const aiList = el("dash-ai-recommend-list");
    kpiGrid.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    todoList.innerHTML = `<li class="loading-text">${HomezI18n.t("common.loading")}</li>`;
    aiList.innerHTML = `<li class="loading-text">${HomezI18n.t("common.loading")}</li>`;

    let data;
    try {
      data = await apiFetch("/console/api/overview");
    } catch (err) {
      renderErrorState(kpiGrid, err);
      todoList.innerHTML = "";
      aiList.innerHTML = "";
      return;
    }

    const schemaSlot = el("dash-schema-warning-slot");
    schemaSlot.innerHTML = "";
    if (!data.v3_schema_ready) {
      renderSchemaWarning(schemaSlot, HomezI18n.t("overview.schema_warning"));
    }

    // ---- KPI 4개: 실제 집계 API가 없으므로 전부 명시적 빈 상태 ----
    kpiGrid.innerHTML = "";
    kpiGrid.appendChild(kpiCard(HomezI18n.t("dashboard.kpi_revenue"), null, KPI_ICONS.revenue, "blue"));
    kpiGrid.appendChild(kpiCard(HomezI18n.t("dashboard.kpi_orders"), null, KPI_ICONS.orders, "green"));
    kpiGrid.appendChild(kpiCard(HomezI18n.t("dashboard.kpi_margin"), null, KPI_ICONS.margin, "orange"));
    const aiCount = data.candidates ? data.candidates.approved : null;
    kpiGrid.appendChild(kpiCard(
      HomezI18n.t("dashboard.kpi_ai_recommend"),
      aiCount === null || aiCount === undefined ? null : aiCount,
      KPI_ICONS.ai, "purple",
    ));

    // ---- 처리할 작업: 실제로 조회 가능한 값만 채운다 ----
    const todoItems = [];
    if (data.candidates) {
      todoItems.push([
        HomezI18n.t("dashboard.todo_pending_approval"),
        data.candidates.pending_review, "pending",
      ]);
    }
    todoItems.push([HomezI18n.t("dashboard.todo_listing_failed"), null, "unknown"]);
    todoItems.push([HomezI18n.t("dashboard.todo_retryable"), null, "unknown"]);
    todoItems.push([HomezI18n.t("dashboard.todo_connection_expired"), null, "unknown"]);
    todoItems.push([HomezI18n.t("dashboard.todo_margin_below_min"), null, "unknown"]);

    todoList.innerHTML = "";
    for (const [label, count, kind] of todoItems) {
      const li = document.createElement("li");
      li.className = "dash-todo-item";
      const isUnknown = count === null || count === undefined;
      const countClass = isUnknown ? "zero" : (kind === "pending" && count > 0 ? "pending" : "zero");
      const countText = isUnknown ? HomezI18n.t("dashboard.todo_not_tracked") : String(count);
      li.innerHTML = `
        <span>${escapeHtml(label)}</span>
        <span class="dash-todo-item-count ${countClass}">${escapeHtml(countText)}</span>
      `;
      todoList.appendChild(li);
    }

    // ---- AI 추천 상품: Decision AI 실제 추천 데이터를 재사용 ----
    try {
      const decisions = await apiFetch("/decisions/pending-review?limit=5");
      const recommended = decisions.filter((d) => d.recommendation === "RECOMMEND").slice(0, 5);
      const rowsToShow = recommended.length > 0 ? recommended : decisions.slice(0, 5);
      aiList.innerHTML = "";
      if (rowsToShow.length === 0) {
        const li = document.createElement("li");
        li.textContent = HomezI18n.t("dashboard.ai_recommend_empty");
        aiList.appendChild(li);
      } else {
        for (const d of rowsToShow) {
          const li = document.createElement("li");
          li.className = "dash-list-item";
          li.innerHTML = `
            <div class="dash-list-item-main">
              <div class="dash-list-item-title">#${d.candidate_id}</div>
              <div class="dash-list-item-sub">${escapeHtml(decisionRecommendationLabel(d.recommendation))}</div>
            </div>
            <span>${d.total_score ?? "—"}</span>
          `;
          aiList.appendChild(li);
        }
      }
    } catch (err) {
      renderErrorState(aiList, err);
    }

    await loadV7OperationsSummary();
  }

  // ---- V7 운영 현황(Orchestration Dashboard, 2026-08-20) ----
  // GET /orchestration/dashboard/summary는 집계 방법이 없는 항목을
  // null로 반환한다 — 0으로 대체하지 않고 "미집계"로 표시한다(기존
  // dash-todo-item "unknown" 관례 재사용).

  const V7_OPS_ITEMS = [
    ["product_input_required", "dashboard.v7ops_product_input_required", "candidates", {}],
    ["out_of_stock_items", "dashboard.v7ops_out_of_stock", "supplier-sourcing", { filter: "out_of_stock" }],
    ["purchase_approval_pending", "dashboard.v7ops_purchase_approval_pending", "supplier-sourcing", { filter: "approval_pending" }],
    ["purchase_failed", "dashboard.v7ops_purchase_failed", "supplier-sourcing", { filter: "failed" }],
    ["return_refund_pending", "dashboard.v7ops_return_refund_pending", "return-exchange", {}],
  ];

  async function loadV7OperationsSummary() {
    const list = el("dash-v7ops-list");
    if (!list) return;
    list.innerHTML = `<li class="loading-text">${HomezI18n.t("common.loading")}</li>`;

    let data;
    try {
      data = await apiFetch("/orchestration/dashboard/summary");
    } catch (err) {
      renderErrorState(list, err);
      return;
    }

    list.innerHTML = "";

    if (data.estop_active) {
      const li = document.createElement("li");
      li.className = "dash-todo-item";
      li.innerHTML = `
        <span>${escapeHtml(HomezI18n.t("dashboard.v7ops_estop_active"))}</span>
        <span class="dash-todo-item-count pending">${escapeHtml(HomezI18n.t("dashboard.v7ops_estop_active_badge"))}</span>
      `;
      list.appendChild(li);
    }

    for (const [key, labelKey, viewName, opts] of V7_OPS_ITEMS) {
      const value = data[key];
      const isUnknown = value === null || value === undefined;
      const li = document.createElement("li");
      li.className = "dash-todo-item";
      li.style.cursor = "pointer";
      const countClass = isUnknown ? "zero" : (value > 0 ? "pending" : "zero");
      const countText = isUnknown ? HomezI18n.t("dashboard.todo_not_tracked") : String(value);
      li.innerHTML = `
        <span>${escapeHtml(HomezI18n.t(labelKey))}</span>
        <span class="dash-todo-item-count ${countClass}">${escapeHtml(countText)}</span>
      `;
      li.addEventListener("click", () => navigateTo(viewName, opts || {}));
      list.appendChild(li);
    }
  }

  function initV7OpsDashboard() {
    const btn = el("dash-v7ops-refresh");
    if (btn) btn.addEventListener("click", () => loadV7OperationsSummary());
  }

  // --------------------------------------------------
  // 상품 후보 목록
  // --------------------------------------------------

  let candidatesCache = [];

  async function fetchCandidates() {
    return apiFetch("/product-candidates?limit=200");
  }

  async function loadCandidates() {
    const wrap = el("candidates-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    try {
      candidatesCache = await fetchCandidates();
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    populateSourceFilter(candidatesCache);
    renderCandidatesTable();
  }

  function populateSourceFilter(rows) {
    const select = el("candidates-filter-source");
    const current = select.value;
    const sources = Array.from(new Set(rows.map((r) => r.source_type))).sort();
    select.innerHTML = `<option value="">${HomezI18n.t("common.all")}</option>` + sources.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    select.value = current;
  }

  function getFilteredSortedCandidates() {
    const search = el("candidates-search").value.trim().toLowerCase();
    const statusFilter = el("candidates-filter-status").value;
    const sourceFilter = el("candidates-filter-source").value;
    const sort = el("candidates-sort").value;

    let rows = candidatesCache.filter((r) => {
      if (statusFilter && r.status !== statusFilter) return false;
      if (sourceFilter && r.source_type !== sourceFilter) return false;
      if (search) {
        const hay = `${r.product_name} ${r.source_reference} ${r.market}`.toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    });

    const byNum = (a, b, key) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity);

    if (sort === "trend_desc") rows = rows.sort((a, b) => byNum(a, b, "trend_score"));
    else if (sort === "margin_desc") rows = rows.sort((a, b) => byNum(a, b, "margin_score"));
    else if (sort === "risk_desc") rows = rows.sort((a, b) => byNum(a, b, "risk_score"));
    else rows = rows.sort((a, b) => (HomezI18n.parseUtcDate(b.discovered_at)?.getTime() || 0) - (HomezI18n.parseUtcDate(a.discovered_at)?.getTime() || 0));

    return rows;
  }

  function renderCandidatesTable() {
    const wrap = el("candidates-table-wrap");
    const rows = getFilteredSortedCandidates();

    if (rows.length === 0) {
      renderEmptyState(
        wrap, HomezI18n.t("candidates.empty"), HomezI18n.t("candidates.empty_sub"),
        `<button type="button" class="btn btn-primary" id="candidates-empty-create-btn">${HomezI18n.t("candidates.create_btn")}</button>`,
      );
      const emptyBtn = el("candidates-empty-create-btn");
      if (emptyBtn) emptyBtn.addEventListener("click", openCandidateCreateDialog);
      return;
    }

    const colName = HomezI18n.t("candidates.col_name");
    const colSource = HomezI18n.t("candidates.col_source");
    const colTrend = HomezI18n.t("candidates.col_trend");
    const colNewProduct = HomezI18n.t("candidates.col_new_product");
    const colMargin = HomezI18n.t("candidates.col_margin");
    const colDemand = HomezI18n.t("candidates.col_demand");
    const colRisk = HomezI18n.t("candidates.col_risk");
    const colStatus = HomezI18n.t("candidates.col_status");

    const trs = rows.map((r) => `
      <tr class="row-clickable" tabindex="0" data-id="${r.id}">
        <td data-label="${colName}">${escapeHtml(r.product_name)}</td>
        <td data-label="${colSource}">${escapeHtml(r.source_type)}</td>
        <td data-label="${colTrend}">${scoreBarHtml(r.trend_score)}</td>
        <td data-label="${colNewProduct}">${r.is_new_product === true ? HomezI18n.t("common.yes") : r.is_new_product === false ? HomezI18n.t("common.no") : "—"}</td>
        <td data-label="${colMargin}">${scoreBarHtml(r.margin_score)}</td>
        <td data-label="${colDemand}">${scoreBarHtml(r.demand_score)}</td>
        <td data-label="${colRisk}">${scoreBarHtml(r.risk_score, { invert: true })}</td>
        <td data-label="${colStatus}"><span class="status-tag status-${r.status}">${escapeHtml(candidateStatusLabel(r.status))}</span></td>
      </tr>
    `).join("");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr>
          <th>${colName}</th><th>${colSource}</th><th>${colTrend}</th><th>${colNewProduct}</th>
          <th>${colMargin}</th><th>${colDemand}</th><th>${colRisk}</th><th>${colStatus}</th>
        </tr></thead>
        <tbody>${trs}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      const openDetail = () => openCandidateDetail(Number(tr.dataset.id));
      tr.addEventListener("click", openDetail);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openDetail();
        }
      });
    });
  }

  ["candidates-search", "candidates-filter-status", "candidates-filter-source", "candidates-sort"].forEach((id) => {
    document.addEventListener("DOMContentLoaded", () => {
      const node = el(id);
      if (node) node.addEventListener("input", renderCandidatesTable);
    });
  });

  // --------------------------------------------------
  // 직접 상품 추가 (Gate TD, 2026-08-19) — 기존 POST /product-candidates/
  // private과 ProductCandidatePrivateCreate Schema를 그대로 재사용한다.
  // 새 필드·검증 규칙을 추측해서 만들지 않는다.
  // --------------------------------------------------

  let candidateCreateLastId = null;

  function openCandidateCreateDialog() {
    const dialog = el("candidate-create-dialog");
    el("candidate-create-form").reset();
    el("candidate-create-error").textContent = "";
    dialog.showModal();
    el("cc-product-name").focus();
  }

  function closeCandidateCreateDialog() {
    el("candidate-create-dialog").close();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const openBtn = el("candidates-create-btn");
    if (openBtn) openBtn.addEventListener("click", openCandidateCreateDialog);

    const cancelBtn = el("candidate-create-cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", closeCandidateCreateDialog);

    const dialog = el("candidate-create-dialog");
    if (dialog) dialog.addEventListener("cancel", closeCandidateCreateDialog);

    const form = el("candidate-create-form");
    if (form) {
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const errEl = el("candidate-create-error");
        errEl.textContent = "";

        const productName = el("cc-product-name").value.trim();
        const market = el("cc-market").value.trim();
        const sourceReference = el("cc-source-reference").value.trim();
        if (!productName || !market || !sourceReference) {
          errEl.textContent = HomezI18n.t("candidates.create_required");
          return;
        }

        const categoryHint = el("cc-category-hint").value.trim();
        const brandHint = el("cc-brand-hint").value.trim();
        const releaseDate = el("cc-release-date").value;

        const submitBtn = el("candidate-create-submit");
        submitBtn.disabled = true;

        const requestedAt = Date.now();
        try {
          const candidate = await apiFetch("/product-candidates/private", {
            method: "POST",
            body: JSON.stringify({
              product_name: productName,
              market,
              source_reference: sourceReference,
              category_hint: categoryHint || null,
              brand_hint: brandHint || null,
              release_date: releaseDate || null,
            }),
          });

          const createdAtMs = HomezI18n.parseUtcDate(candidate.created_at)?.getTime();
          const isFreshlyCreated = createdAtMs != null && Math.abs(requestedAt - createdAtMs) < 15000;
          candidateCreateLastId = candidate.id;
          closeCandidateCreateDialog();
          toast(
            HomezI18n.t(isFreshlyCreated ? "candidates.create_success_new" : "candidates.create_success_existing"),
            "success",
          );
          await loadCandidates();
          const newRow = document.querySelector(`#candidates-table-wrap tr[data-id="${candidate.id}"]`);
          if (newRow) {
            newRow.classList.add("row-highlight");
            newRow.scrollIntoView({ block: "center", behavior: "smooth" });
          }
        } catch (err) {
          if (err instanceof ApiError && err.status === 403) {
            errEl.textContent = HomezI18n.t("candidates.create_error_forbidden");
          } else if (err instanceof ApiError && err.status === 0) {
            errEl.textContent = HomezI18n.t("candidates.create_error_network");
          } else {
            errEl.textContent = (err && err.message) || HomezI18n.t("candidates.create_error_generic");
          }
        } finally {
          submitBtn.disabled = false;
        }
      });
    }
  });

  // --------------------------------------------------
  // 상품 후보 상세
  // --------------------------------------------------

  let candidateDetailCurrentId = null;

  async function openCandidateDetail(id) {
    navigateTo("candidate-detail", { id });
  }

  async function loadCandidateDetail({ id }) {
    candidateDetailCurrentId = id || null;
    const container = el("candidate-detail-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(container, HomezI18n.t("candidate_detail.select_prompt"));
      return;
    }

    let candidate, evidence, decisions;
    try {
      [candidate, evidence, decisions] = await Promise.all([
        apiFetch(`/product-candidates/${id}`),
        apiFetch(`/product-candidates/${id}/evidence`),
        apiFetch(`/product-candidates/${id}/decisions`),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    const decidable = ["ANALYZED", "RECOMMENDED", "HELD"].includes(candidate.status);

    container.innerHTML = `
      <div class="detail-grid">
        <div>
          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.basic_info_title")}</h2>
            <dl class="kv-list">
              <dt>${HomezI18n.t("candidates.col_name")}</dt><dd>${escapeHtml(candidate.product_name)}</dd>
              <dt>${HomezI18n.t("candidates.col_source")}</dt><dd>${escapeHtml(candidate.source_type)} / ${escapeHtml(candidate.source_reference)}</dd>
              <dt>${HomezI18n.t("candidate_detail.market_label")}</dt><dd>${escapeHtml(candidate.market)}</dd>
              <dt>${HomezI18n.t("candidate_detail.discovered_at_label")}</dt><dd>${fmtDate(candidate.discovered_at)}</dd>
              <dt>${HomezI18n.t("candidate_detail.release_date_label")}</dt><dd>${candidate.release_date || HomezI18n.t("candidate_detail.unknown")}</dd>
              <dt>${HomezI18n.t("candidates.col_status")}</dt><dd><span class="status-tag status-${candidate.status}">${escapeHtml(candidateStatusLabel(candidate.status))}</span></dd>
            </dl>
          </div>

          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.ai_scores_title")}</h2>
            <dl class="kv-list">
              <dt>${HomezI18n.t("candidate_detail.trend_score_label")}</dt><dd>${scoreBarHtml(candidate.trend_score)}</dd>
              <dt>${HomezI18n.t("candidate_detail.is_new_product_label")}</dt><dd>${candidate.is_new_product === true ? HomezI18n.t("common.yes") : candidate.is_new_product === false ? HomezI18n.t("common.no") : HomezI18n.t("candidate_detail.not_analyzed")}</dd>
              <dt>${HomezI18n.t("candidate_detail.novelty_score_label")}</dt><dd>${scoreBarHtml(candidate.novelty_score)}</dd>
              <dt>${HomezI18n.t("candidates.col_demand")}</dt><dd>${scoreBarHtml(candidate.demand_score)}</dd>
              <dt>${HomezI18n.t("candidates.col_margin")}</dt><dd>${scoreBarHtml(candidate.margin_score)}</dd>
              <dt>${HomezI18n.t("candidates.col_risk")}</dt><dd>${scoreBarHtml(candidate.risk_score, { invert: true })}</dd>
              <dt>${HomezI18n.t("candidate_detail.confidence_label")}</dt><dd>${scoreBarHtml(candidate.confidence)}</dd>
            </dl>
          </div>

          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.evidence_title")}</h2>
            ${evidence.length ? evidence.map((e) => `
              <div class="evidence-item">
                <span class="evidence-type">${escapeHtml(e.evidence_type)}</span>
                <div>${escapeHtml(e.payload_summary)}</div>
                <div class="stat-sub">${fmtDate(e.recorded_at)}${e.score !== null && e.score !== undefined ? `${HomezI18n.t("candidate_detail.evidence_score_prefix")}${e.score}` : ""}</div>
              </div>
            `).join("") : `<p class="stat-sub">${HomezI18n.t("candidate_detail.evidence_empty")}</p>`}
          </div>
        </div>

        <div>
          ${candidate.status === "DISCOVERED" ? `
          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.info_check_step_title")}</h2>
            <p class="stat-sub">${HomezI18n.t("candidate_detail.discovered_next_step_hint")}</p>
            <button type="button" class="btn btn-primary" id="btn-analyze">${HomezI18n.t("candidate_detail.verify_info_btn")}</button>
          </div>
          ` : ""}

          ${candidate.status === "ANALYZED" && (candidate.trend_score != null || candidate.novelty_score != null) ? `
          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.recommend_step_title")}</h2>
            <p class="stat-sub">${HomezI18n.t("candidate_detail.analyzed_with_score_next_step_hint")}</p>
            <button type="button" class="btn" id="btn-recommend">${HomezI18n.t("candidate_detail.recommend_btn")}</button>
          </div>
          ` : ""}

          ${candidate.status === "ANALYZED" && candidate.trend_score == null && candidate.novelty_score == null ? `
          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.info_check_step_title")}</h2>
            <p class="stat-sub">${HomezI18n.t("candidate_detail.analyzed_next_step_hint")}</p>
            <button type="button" class="btn" id="btn-refresh-trend">${HomezI18n.t("candidate_detail.refresh_trend_btn")}</button>
            <button type="button" class="btn btn-ghost btn-sm" id="btn-naver-credential-setup">${HomezI18n.t("candidate_detail.naver_credential_setup_btn")}</button>
            <p class="stat-sub" id="naver-credential-status"></p>
            <p class="stat-sub" id="refresh-trend-result"></p>
          </div>
          ` : ""}

          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.operator_decision_title")}</h2>
            <div class="decision-actions">
              <button class="btn btn-primary" id="btn-approve" ${decidable ? "" : "disabled"}>${HomezI18n.t("common.approve")}</button>
              <button class="btn" id="btn-hold" ${decidable ? "" : "disabled"}>${HomezI18n.t("common.hold")}</button>
              <button class="btn btn-danger" id="btn-reject" ${decidable ? "" : "disabled"}>${HomezI18n.t("common.reject")}</button>
            </div>
            ${!decidable ? `<p class="stat-sub">${HomezI18n.t("candidate_detail.not_decidable", { status: candidate.status })}</p>` : ""}
          </div>

          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.decision_history_title")}</h2>
            ${decisions.length ? decisions.map((d) => `
              <div class="decision-item">
                <strong>${escapeHtml(d.action)}</strong> · ${fmtDate(d.decided_at)}
                ${d.memo ? `<div>${escapeHtml(d.memo)}</div>` : ""}
              </div>
            `).join("") : `<p class="stat-sub">${HomezI18n.t("candidate_detail.decision_history_empty")}</p>`}
          </div>

          <!-- V7 Gate 7(2026-08-15) — AI 자동 등록 파이프라인(candidate_
               pipeline_service, Gate 6) 연결. candidate_pipeline_service.
               start_pipeline()이 require_approved_for_company()로
               APPROVED 후보만 받으므로, 승인되지 않은 후보에서는 버튼을
               비활성화해 실제로 동작하지 않는 상태를 정직하게 보여준다. -->
          <div class="detail-panel">
            <h2>${HomezI18n.t("cpl.heading")}</h2>
            <p class="stat-sub">${HomezI18n.t("cpl.disclosure")}</p>
            <button type="button" class="btn btn-primary" id="cpl-run-btn" ${candidate.status === "APPROVED" ? "" : "disabled"}>${HomezI18n.t("cpl.run_btn")}</button>
            ${candidate.status !== "APPROVED" ? `<p class="stat-sub">${HomezI18n.t("cpl.not_approved_hint")}</p>` : ""}
            <div id="cpl-result"></div>
          </div>

          <div class="detail-panel">
            <h2>${HomezI18n.t("ps.overview_title")}</h2>
            <p class="stat-sub">${HomezI18n.t("ps.overview_hint")}</p>
            <div id="ps-overview-body"><p class="loading-text">${HomezI18n.t("common.loading")}</p></div>
          </div>
        </div>
      </div>
    `;

    const bindWorkflowStep = (btnId, action, successMsg) => {
      const btn = el(btnId);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await apiFetch(`/product-candidates/${id}/${action}`, { method: "POST" });
          toast(successMsg, "success");
          loadCandidateDetail({ id });
        } catch (err) {
          toast(err.message || HomezI18n.t("candidate_detail.workflow_step_error"), "error");
          btn.disabled = false;
        }
      });
    };

    bindWorkflowStep("btn-analyze", "analyze", HomezI18n.t("candidate_detail.verify_info_success"));
    bindWorkflowStep("btn-recommend", "recommend", HomezI18n.t("candidate_detail.recommend_success"));

    const naverCredentialBtn = el("btn-naver-credential-setup");
    if (naverCredentialBtn) {
      const statusEl = el("naver-credential-status");
      apiFetch("/product-candidates/system/naver-datalab-credential").then((status) => {
        if (statusEl) {
          statusEl.textContent = status.registered
            ? HomezI18n.t("candidate_detail.naver_credential_registered")
            : HomezI18n.t("candidate_detail.naver_credential_not_registered");
        }
      }).catch(() => {});

      naverCredentialBtn.addEventListener("click", () => withButtonGuard(naverCredentialBtn, async () => {
        const clientId = window.prompt(HomezI18n.t("candidate_detail.naver_credential_client_id_prompt"));
        if (!clientId) return;
        const clientSecret = window.prompt(HomezI18n.t("candidate_detail.naver_credential_client_secret_prompt"));
        if (!clientSecret) return;

        try {
          await apiFetch("/product-candidates/system/naver-datalab-credential", {
            method: "POST",
            body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
          });
          toast(HomezI18n.t("candidate_detail.naver_credential_save_success"), "success");
          if (statusEl) statusEl.textContent = HomezI18n.t("candidate_detail.naver_credential_registered");
        } catch (err) {
          toast(err.message || HomezI18n.t("candidate_detail.naver_credential_save_error"), "error");
        }
      }));
    }

    const refreshTrendBtn = el("btn-refresh-trend");
    if (refreshTrendBtn) {
      refreshTrendBtn.addEventListener("click", () => withButtonGuard(refreshTrendBtn, async () => {
        const resultEl = el("refresh-trend-result");
        try {
          const result = await apiFetch(`/product-candidates/${id}/refresh-trend-analysis`, { method: "POST" });
          if (result.status === "APPLIED") {
            toast(HomezI18n.t("candidate_detail.refresh_trend_applied", { score: result.trend_score }), "success");
            loadCandidateDetail({ id });
          } else {
            if (resultEl) resultEl.textContent = result.reason || HomezI18n.t("candidate_detail.refresh_trend_no_signal");
            toast(HomezI18n.t("candidate_detail.refresh_trend_no_signal"), "error");
          }
        } catch (err) {
          toast(err.message || HomezI18n.t("candidate_detail.refresh_trend_error"), "error");
        }
      }));
    }

    loadProductSelectionOverview(id);

    const bindAction = (btnId, action, confirmTitle, confirmBody, successMsg, errorMsg, requireReason = false) => {
      const btn = el(btnId);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: confirmTitle,
          body: confirmBody,
          requireReason,
        });
        if (!confirmed) return;

        document.querySelectorAll(".decision-actions button").forEach((b) => (b.disabled = true));

        try {
          await apiFetch(`/product-candidates/${id}/${action}`, {
            method: "POST",
            body: JSON.stringify({ memo: value || null }),
          });
          toast(successMsg, "success");
          loadCandidateDetail({ id });
        } catch (err) {
          toast(err.message || errorMsg, "error");
          document.querySelectorAll(".decision-actions button").forEach((b) => (b.disabled = false));
        }
      });
    };

    bindAction(
      "btn-approve", "approve",
      HomezI18n.t("candidate_detail.approve_confirm_title"),
      HomezI18n.t("candidate_detail.approve_confirm_body", { name: candidate.product_name }),
      HomezI18n.t("candidate_detail.approve_success"),
      HomezI18n.t("candidate_detail.approve_error"),
    );
    bindAction(
      "btn-hold", "hold",
      HomezI18n.t("candidate_detail.hold_confirm_title"),
      HomezI18n.t("candidate_detail.hold_confirm_body", { name: candidate.product_name }),
      HomezI18n.t("candidate_detail.hold_success"),
      HomezI18n.t("candidate_detail.hold_error"),
    );
    bindAction(
      "btn-reject", "reject",
      HomezI18n.t("candidate_detail.reject_confirm_title"),
      HomezI18n.t("candidate_detail.reject_confirm_body", { name: candidate.product_name }),
      HomezI18n.t("candidate_detail.reject_success"),
      HomezI18n.t("candidate_detail.reject_error"),
      true,
    );

    const cplBtn = el("cpl-run-btn");
    if (cplBtn && !cplBtn.disabled) {
      cplBtn.addEventListener("click", () => runCandidatePipeline(id));
    }
  }

  VIEW_LOADERS["candidate-detail"] = loadCandidateDetail;

  // --------------------------------------------------
  // CA-2(2026-08-21) — 상품 선별 통합 요약. 내부 코드(step_code/
  // status)는 사용자에게 절대 노출하지 않는다 — 전부 i18n 라벨로만
  // 표시한다.
  // --------------------------------------------------

  const PS_STATUS_CLASS = {
    PASS: "lw-channel-policy-badge-eligible",
    NEEDS_IMPROVEMENT: "lw-channel-policy-badge-actions",
    DATA_REQUIRED: "lw-channel-policy-badge-actions",
    BLOCKED: "lw-channel-policy-badge-blocked",
    STALE: "lw-channel-policy-badge-actions",
  };

  async function loadProductSelectionOverview(candidateId) {
    const body = el("ps-overview-body");
    if (!body) return;

    let overview;
    try {
      overview = await apiFetch(`/product-selection/${candidateId}/overview`);
    } catch (err) {
      body.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
      return;
    }

    body.innerHTML = `<ul class="lw-policy-issue-list">${overview.steps.map((s) => `
      <li class="lw-policy-issue">
        <span class="lw-policy-issue-code">${escapeHtml(HomezI18n.t(`ps.step.${s.step_code.toLowerCase()}`))}</span>
        <span class="lw-channel-policy-badge ${PS_STATUS_CLASS[s.status] || ""}">${escapeHtml(HomezI18n.t(`ps.status.${s.status.toLowerCase()}`))}</span>
        ${s.detail_codes.length ? `<span class="lw-policy-issue-fields">${s.detail_codes.map((c) => escapeHtml(HomezI18n.t(`ps.detail.${c.toLowerCase()}`) || HomezI18n.t("ps.detail.generic"))).join(" · ")}</span>` : ""}
      </li>`).join("")}</ul>`;
  }

  // --------------------------------------------------
  // 트렌드 탐색 / 신제품 탐색 (기존 후보 데이터에서 파생)
  // --------------------------------------------------

  async function loadTrend() {
    const wrap = el("trend-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      rows = await fetchCandidates();
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    rows = rows.filter((r) => r.trend_score !== null && r.trend_score !== undefined)
      .sort((a, b) => b.trend_score - a.trend_score);

    if (rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("trend.empty"), HomezI18n.t("trend.empty_sub"));
      return;
    }

    const colName = HomezI18n.t("candidates.col_name");
    const colMarket = HomezI18n.t("trend.col_market");
    const colTrendScore = HomezI18n.t("trend.col_trend_score");
    const colConfidence = HomezI18n.t("candidate_detail.confidence_label");
    const colStatus = HomezI18n.t("candidates.col_status");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colName}</th><th>${colMarket}</th><th>${colTrendScore}</th><th>${colConfidence}</th><th>${colStatus}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" tabindex="0" data-id="${r.id}">
            <td data-label="${colName}">${escapeHtml(r.product_name)}</td>
            <td data-label="${colMarket}">${escapeHtml(r.market)}</td>
            <td data-label="${colTrendScore}">${scoreBarHtml(r.trend_score)}</td>
            <td data-label="${colConfidence}">${scoreBarHtml(r.confidence)}</td>
            <td data-label="${colStatus}"><span class="status-tag status-${r.status}">${escapeHtml(candidateStatusLabel(r.status))}</span></td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => openCandidateDetail(Number(tr.dataset.id)));
    });
  }

  async function loadNewProduct() {
    const wrap = el("new-product-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      rows = await fetchCandidates();
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    rows = rows.filter((r) => r.is_new_product === true);

    if (rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("new_product.empty"), HomezI18n.t("new_product.empty_sub"));
      return;
    }

    const colName = HomezI18n.t("candidates.col_name");
    const colMarket = HomezI18n.t("trend.col_market");
    const colReleaseDate = HomezI18n.t("candidate_detail.release_date_label");
    const colNoveltyScore = HomezI18n.t("new_product.col_novelty_score");
    const colStatus = HomezI18n.t("candidates.col_status");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colName}</th><th>${colMarket}</th><th>${colReleaseDate}</th><th>${colNoveltyScore}</th><th>${colStatus}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" tabindex="0" data-id="${r.id}">
            <td data-label="${colName}">${escapeHtml(r.product_name)}</td>
            <td data-label="${colMarket}">${escapeHtml(r.market)}</td>
            <td data-label="${colReleaseDate}">${r.release_date || "—"}</td>
            <td data-label="${colNoveltyScore}">${scoreBarHtml(r.novelty_score)}</td>
            <td data-label="${colStatus}"><span class="status-tag status-${r.status}">${escapeHtml(candidateStatusLabel(r.status))}</span></td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => openCandidateDetail(Number(tr.dataset.id)));
    });
  }

  // --------------------------------------------------
  // 자동화 안전
  // --------------------------------------------------

  async function loadSafety() {
    const container = el("safety-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let data;
    try {
      data = await apiFetch("/console/api/safety-status");
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    if (!data.schema_ready) {
      renderEmptyState(container, HomezI18n.t("safety.schema_not_ready_title"), data.message || HomezI18n.t("safety.schema_not_ready_default"));
      return;
    }

    const stop = data.emergency_stop;
    const isActive = stop && stop.is_active;

    const scope = data.global_scope;

    container.innerHTML = `
      <div class="safety-banner ${isActive ? "is-stopped" : ""}">
        <div>
          <div class="banner-title">${isActive ? HomezI18n.t("safety.estop_active_title") : HomezI18n.t("safety.estop_inactive_title")}</div>
          <div class="stat-sub">
            ${stop ? `${HomezI18n.t("safety.estop_reason_prefix")}${escapeHtml(stop.reason || "—")}${HomezI18n.t("safety.estop_changed_prefix")}${fmtDate(isActive ? stop.set_at : stop.cleared_at)}` : HomezI18n.t("safety.estop_no_history")}
          </div>
        </div>
        <button class="btn ${isActive ? "btn-primary" : "btn-danger"}" id="btn-toggle-estop">
          ${isActive ? HomezI18n.t("safety.estop_deactivate_btn") : HomezI18n.t("safety.estop_activate_btn")}
        </button>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("safety.mode_title")}</h2>
        <dl class="kv-list">
          <dt>${HomezI18n.t("safety.current_mode_label")}</dt><dd>${escapeHtml(data.mode)}</dd>
        </dl>
        <div class="decision-actions">
          <select id="mode-select">
            ${data.mode_all.map((m) => `<option value="${m}" ${m === data.mode ? "selected" : ""}>${m}</option>`).join("")}
          </select>
          <button class="btn btn-primary" id="btn-set-mode">${HomezI18n.t("safety.change_mode_btn")}</button>
        </div>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("safety.execution_limit_title", { period: escapeHtml(data.period_start_kst) })}</h2>
        ${scope ? `
          <dl class="kv-list">
            <dt>${HomezI18n.t("safety.daily_funding_limit_label")}</dt><dd>${scope.daily_funding_limit !== null ? fmtMoney(scope.daily_funding_limit) : HomezI18n.t("safety.not_set")}</dd>
            <dt>${HomezI18n.t("safety.used_funding_today_label")}</dt><dd class="money">${fmtMoney(scope.used_funding_today)}</dd>
            <dt>${HomezI18n.t("safety.daily_quantity_limit_label")}</dt><dd>${scope.daily_quantity_limit ?? HomezI18n.t("safety.not_set")}</dd>
            <dt>${HomezI18n.t("safety.used_quantity_today_label")}</dt><dd>${scope.used_quantity_today}</dd>
          </dl>
        ` : `<p class="stat-sub">${HomezI18n.t("safety.no_global_limit")}</p>`}
      </div>

      <div class="detail-panel" id="function-modes-panel">
        <h2>${HomezI18n.t("safety.function_modes_title")}</h2>
        <p class="stat-sub">${HomezI18n.t("safety.function_modes_intro")}</p>
        <div id="function-modes-list">
          <p class="loading-text">${HomezI18n.t("common.loading")}</p>
        </div>
      </div>
    `;

    el("btn-toggle-estop").addEventListener("click", () => withButtonGuard(el("btn-toggle-estop"), async () => {
      if (isActive) {
        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("safety.deactivate_confirm_title"),
          body: HomezI18n.t("safety.deactivate_confirm_body"),
          okLabel: HomezI18n.t("safety.deactivate_confirm_ok"),
        });
        if (!confirmed) return;
        try {
          await apiFetch("/console/api/safety/emergency-stop/deactivate", { method: "POST" });
          toast(HomezI18n.t("safety.deactivate_success"), "success");
          loadSafety();
        } catch (err) {
          toast(err.message || HomezI18n.t("safety.deactivate_error"), "error");
        }
      } else {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("safety.activate_confirm_title"),
          body: HomezI18n.t("safety.activate_confirm_body"),
          requireReason: true,
          okLabel: HomezI18n.t("safety.activate_confirm_ok"),
        });
        if (!confirmed) return;
        try {
          await apiFetch("/console/api/safety/emergency-stop/activate", {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("safety.activate_success"), "success");
          loadSafety();
        } catch (err) {
          toast(err.message || HomezI18n.t("safety.activate_error"), "error");
        }
      }
    }));

    el("btn-set-mode").addEventListener("click", () => withButtonGuard(el("btn-set-mode"), async () => {
      const newMode = el("mode-select").value;
      if (newMode === data.mode) {
        toast(HomezI18n.t("safety.mode_unchanged"));
        return;
      }
      const { confirmed, value } = await confirmDialog({
        title: HomezI18n.t("safety.mode_change_confirm_title"),
        body: HomezI18n.t("safety.mode_change_confirm_body", { from: data.mode, to: newMode }),
      });
      if (!confirmed) return;
      try {
        await apiFetch("/console/api/safety/mode", {
          method: "POST",
          body: JSON.stringify({ mode: newMode, reason: value || null }),
        });
        toast(HomezI18n.t("safety.mode_change_success"), "success");
        loadSafety();
      } catch (err) {
        toast(err.message || HomezI18n.t("safety.mode_change_error"), "error");
      }
    }));

    loadFunctionModes();
  }

  // 2026-09-09 Phase 3 — 회사×기능별 자동화 상태(기존 전역 단일 모드와
  // 별개). 이 화면은 기능적 최소치만 제공한다 — 표시/필터/모바일
  // 최적화 같은 편의성 다듬기는 Phase 13(UI 편의성 통합)에서 이어서
  // 한다.
  async function loadFunctionModes() {
    const list = el("function-modes-list");
    if (!list) return;

    let data;
    try {
      data = await apiFetch("/console/api/function-modes");
    } catch (err) {
      renderErrorState(list, err);
      return;
    }

    if (!data.schema_ready) {
      renderEmptyState(
        list,
        HomezI18n.t("safety.schema_not_ready_title"),
        data.message || HomezI18n.t("safety.schema_not_ready_default"),
      );
      return;
    }

    list.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>${HomezI18n.t("safety.function_modes_col_function")}</th>
            <th>${HomezI18n.t("safety.function_modes_col_mode")}</th>
            <th>${HomezI18n.t("safety.function_modes_col_action")}</th>
          </tr>
        </thead>
        <tbody>
          ${data.functions.map((fn) => `
            <tr data-function-code="${fn.code}">
              <td>${escapeHtml(fn.label_ko)}</td>
              <td>
                <span class="badge badge-mode-${fn.mode.toLowerCase()}">${escapeHtml(fn.mode_label_ko)}</span>
                <div class="stat-sub">${escapeHtml(fn.mode_description_ko)}</div>
                ${fn.reason ? `<div class="stat-sub">${HomezI18n.t("safety.function_modes_reason_prefix")}${escapeHtml(fn.reason)}</div>` : ""}
                ${fn.set_at ? `<div class="stat-sub">${HomezI18n.t("safety.function_modes_set_at_prefix")}${fmtDate(fn.set_at)}</div>` : ""}
              </td>
              <td>
                <select class="function-mode-select">
                  ${data.mode_all.map((m) => `<option value="${m}" ${m === fn.mode ? "selected" : ""}>${escapeHtml(data.mode_labels_ko[m])}</option>`).join("")}
                </select>
                <button class="btn btn-secondary btn-set-function-mode">${HomezI18n.t("safety.change_mode_btn")}</button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;

    list.querySelectorAll("tr[data-function-code]").forEach((tr) => {
      const code = tr.dataset.functionCode;
      const select = tr.querySelector(".function-mode-select");
      const btn = tr.querySelector(".btn-set-function-mode");
      const current = data.functions.find((fn) => fn.code === code);

      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const newMode = select.value;
        if (newMode === current.mode) {
          toast(HomezI18n.t("safety.mode_unchanged"));
          return;
        }
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("safety.mode_change_confirm_title"),
          body: HomezI18n.t("safety.mode_change_confirm_body", {
            from: current.mode_label_ko, to: data.mode_labels_ko[newMode],
          }),
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/console/api/function-modes/${encodeURIComponent(code)}`, {
            method: "POST",
            body: JSON.stringify({ mode: newMode, reason: value || null }),
          });
          toast(HomezI18n.t("safety.mode_change_success"), "success");
          loadFunctionModes();
        } catch (err) {
          toast(err.message || HomezI18n.t("safety.mode_change_error"), "error");
        }
      }));
    });
  }

  // --------------------------------------------------
  // Decision AI
  // --------------------------------------------------

  const DECISION_RECOMMENDATION_KEY = {
    INSUFFICIENT_DATA: "decision.recommendation.insufficient_data",
    REVIEW_REQUIRED: "decision.recommendation.review_required",
    RECOMMEND_REJECT: "decision.recommendation.recommend_reject",
    RECOMMEND_HOLD: "decision.recommendation.recommend_hold",
    RECOMMEND_APPROVE: "decision.recommendation.recommend_approve",
  };

  function decisionRecommendationLabel(recommendation) {
    const key = DECISION_RECOMMENDATION_KEY[recommendation];
    return key ? HomezI18n.t(key) : recommendation;
  }

  async function loadDecisionList() {
    const wrap = el("decision-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    const showAll = el("decision-filter-status").value === "all";
    const path = showAll ? "/decisions?limit=200" : "/decisions/pending-review?limit=200";

    let rows;
    try {
      rows = await apiFetch(path);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("decision.empty"), HomezI18n.t("decision.empty_sub"));
      return;
    }

    const colCandidateId = HomezI18n.t("decision.col_candidate_id");
    const colTotalScore = HomezI18n.t("decision.col_total_score");
    const colConfidence = HomezI18n.t("decision.col_confidence");
    const colRecommendation = HomezI18n.t("decision.col_recommendation");
    const colPolicyVersion = HomezI18n.t("decision.col_policy_version");
    const colSafetyBlocked = HomezI18n.t("decision.col_safety_blocked");
    const colStatus = HomezI18n.t("decision.col_status");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr>
          <th>${colCandidateId}</th><th>${colTotalScore}</th><th>${colConfidence}</th><th>${colRecommendation}</th><th>${colPolicyVersion}</th><th>${colSafetyBlocked}</th><th>${colStatus}</th>
        </tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="row-clickable" tabindex="0" data-id="${r.id}">
            <td data-label="${colCandidateId}">#${r.candidate_id}</td>
            <td data-label="${colTotalScore}">${r.total_score ?? "—"}</td>
            <td data-label="${colConfidence}">${r.confidence ?? "—"}</td>
            <td data-label="${colRecommendation}"><span class="status-tag">${escapeHtml(decisionRecommendationLabel(r.recommendation))}</span></td>
            <td data-label="${colPolicyVersion}">${escapeHtml(r.policy_version)}</td>
            <td data-label="${colSafetyBlocked}">${r.blocked_by_safety ? `<span class="risk-tag risk-high">${HomezI18n.t("decision.blocked_tag")}</span>` : "—"}</td>
            <td data-label="${colStatus}">${escapeHtml(decisionStatusLabel(r.status))}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => openDecisionDetail(Number(tr.dataset.id)));
    });
  }

  function openDecisionDetail(id) {
    navigateTo("decision-detail", { id });
  }

  async function loadDecisionDetail({ id }) {
    const container = el("decision-detail-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!id) {
      renderEmptyState(container, HomezI18n.t("decision_detail.select_prompt"));
      return;
    }

    let evaluation, scores, reviews;
    try {
      [evaluation, scores, reviews] = await Promise.all([
        apiFetch(`/decisions/${id}`),
        apiFetch(`/decisions/${id}/scores`),
        apiFetch(`/decisions/${id}/reviews`),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    const canReview = evaluation.status === "PENDING_REVIEW" && !evaluation.blocked_by_safety;
    const isFakeAiNotice = `evaluator: ${escapeHtml(evaluation.evaluator_kind)} v${escapeHtml(evaluation.evaluator_version)}`
      + (evaluation.evaluator_kind === "deterministic" ? HomezI18n.t("decision_detail.evaluator_deterministic_note") : "");

    container.innerHTML = `
      <div class="detail-grid">
        <div>
          <div class="detail-panel">
            <h2>${HomezI18n.t("decision_detail.summary_title")}</h2>
            ${evaluation.blocked_by_safety ? `<div class="safety-banner is-stopped"><div class="banner-title">${HomezI18n.t("decision_detail.blocked_title")}</div><div class="stat-sub">${escapeHtml(evaluation.safety_block_reason || "")}</div></div>` : ""}
            <dl class="kv-list">
              <dt>${HomezI18n.t("decision.col_candidate_id")}</dt><dd>#${evaluation.candidate_id}</dd>
              <dt>${HomezI18n.t("decision.col_total_score")}</dt><dd>${evaluation.total_score}</dd>
              <dt>${HomezI18n.t("decision.col_confidence")}</dt><dd>${evaluation.confidence}</dd>
              <dt>${HomezI18n.t("decision.col_recommendation")}</dt><dd><span class="status-tag">${escapeHtml(decisionRecommendationLabel(evaluation.recommendation))}</span></dd>
              <dt>${HomezI18n.t("decision_detail.recommendation_reason_label")}</dt><dd>${escapeHtml(evaluation.recommendation_reason)}</dd>
              <dt>${HomezI18n.t("decision.col_policy_version")}</dt><dd>${escapeHtml(evaluation.policy_version)}</dd>
              <dt>${HomezI18n.t("decision.col_status")}</dt><dd>${escapeHtml(decisionStatusLabel(evaluation.status))}</dd>
              <dt>${HomezI18n.t("decision_detail.evaluator_label")}</dt><dd class="stat-sub">${isFakeAiNotice}</dd>
            </dl>
          </div>

          <div class="detail-panel">
            <h2>${HomezI18n.t("decision_detail.scores_title")}</h2>
            <table class="schema-table"><tbody>
              ${scores.map((s) => `
                <tr>
                  <td>${escapeHtml(s.axis)}</td>
                  <td>${s.data_sufficient ? s.raw_score : `<span class="stat-sub">${HomezI18n.t("decision_detail.insufficient_data")}</span>`}</td>
                  <td>${HomezI18n.t("decision_detail.weight_prefix")}${s.weight}</td>
                  <td>${s.risk_flag ? `<span class="risk-tag risk-high">${HomezI18n.t("decision_detail.risk_tag")}</span>` : ""}</td>
                </tr>
              `).join("")}
            </tbody></table>
          </div>

          <div class="detail-panel">
            <h2>${HomezI18n.t("decision_detail.audit_history_title")}</h2>
            ${reviews.length ? reviews.map((r) => `
              <div class="decision-item">
                <strong>${escapeHtml(r.action)}</strong> · ${fmtDate(r.decided_at)}
                ${r.memo ? `<div>${escapeHtml(r.memo)}</div>` : ""}
                ${r.override_reason ? `<div>${HomezI18n.t("decision_detail.override_reason_prefix")}${escapeHtml(r.override_reason)} (${escapeHtml(r.previous_value || "")} → ${escapeHtml(r.new_value || "")})</div>` : ""}
              </div>
            `).join("") : `<p class="stat-sub">${HomezI18n.t("decision_detail.audit_history_empty")}</p>`}
          </div>
        </div>

        <div>
          <div class="detail-panel">
            <h2>${HomezI18n.t("candidate_detail.operator_decision_title")}</h2>
            <div class="decision-actions">
              <button class="btn btn-primary" id="btn-decision-approve" ${canReview ? "" : "disabled"}>${HomezI18n.t("common.approve")}</button>
              <button class="btn" id="btn-decision-hold" ${canReview ? "" : "disabled"}>${HomezI18n.t("common.hold")}</button>
              <button class="btn btn-danger" id="btn-decision-reject" ${canReview ? "" : "disabled"}>${HomezI18n.t("common.reject")}</button>
            </div>
            <div class="decision-actions">
              <button class="btn btn-ghost btn-sm" id="btn-decision-override" ${canReview ? "" : "disabled"}>${HomezI18n.t("decision_detail.override_btn")}</button>
            </div>
            ${!canReview ? `<p class="stat-sub">${HomezI18n.t("decision_detail.not_reviewable", { status: escapeHtml(evaluation.status) })}${evaluation.blocked_by_safety ? HomezI18n.t("decision_detail.not_reviewable_safety_blocked") : ""}</p>` : ""}
          </div>
        </div>
      </div>
    `;

    const runReview = async (endpoint, requireReason, confirmTitle, confirmBody, okLabel, successMsg, errorMsg) => {
      const { confirmed, value } = await confirmDialog({
        title: confirmTitle,
        body: confirmBody,
        requireReason,
        okLabel,
      });
      if (!confirmed) return;
      if (requireReason && !value) {
        toast(HomezI18n.t("decision_detail.override_reason_required"), "error");
        return;
      }

      document.querySelectorAll(".decision-actions button").forEach((b) => (b.disabled = true));

      try {
        const body = { idempotency_key: `console-${id}-${endpoint}-${Date.now()}` };
        if (requireReason) {
          body.override_reason = value;
          body.new_value = "RECOMMEND_APPROVE";
        } else {
          body.memo = value || null;
        }
        await apiFetch(`/decisions/${id}/${endpoint}`, { method: "POST", body: JSON.stringify(body) });
        toast(successMsg, "success");
        loadDecisionDetail({ id });
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          toast(HomezI18n.t("decision_detail.conflict_error"), "error");
          loadDecisionDetail({ id });
          return;
        }
        toast(err.message || errorMsg, "error");
        document.querySelectorAll(".decision-actions button").forEach((b) => (b.disabled = false));
      }
    };

    const approveBtn = el("btn-decision-approve");
    const holdBtn = el("btn-decision-hold");
    const rejectBtn = el("btn-decision-reject");
    const overrideBtn = el("btn-decision-override");
    if (approveBtn) approveBtn.addEventListener("click", () => runReview(
      "approve", false,
      HomezI18n.t("decision_detail.approve_confirm_title"),
      HomezI18n.t("decision_detail.approve_confirm_body", { id }),
      HomezI18n.t("common.approve"),
      HomezI18n.t("decision_detail.approve_success"),
      HomezI18n.t("decision_detail.approve_error"),
    ));
    if (holdBtn) holdBtn.addEventListener("click", () => runReview(
      "hold", false,
      HomezI18n.t("decision_detail.hold_confirm_title"),
      HomezI18n.t("decision_detail.hold_confirm_body", { id }),
      HomezI18n.t("common.hold"),
      HomezI18n.t("decision_detail.hold_success"),
      HomezI18n.t("decision_detail.hold_error"),
    ));
    if (rejectBtn) rejectBtn.addEventListener("click", () => runReview(
      "reject", false,
      HomezI18n.t("decision_detail.reject_confirm_title"),
      HomezI18n.t("decision_detail.reject_confirm_body", { id }),
      HomezI18n.t("common.reject"),
      HomezI18n.t("decision_detail.reject_success"),
      HomezI18n.t("decision_detail.reject_error"),
    ));
    if (overrideBtn) overrideBtn.addEventListener("click", () => runReview(
      "override", true,
      HomezI18n.t("decision_detail.override_confirm_title"),
      HomezI18n.t("decision_detail.override_confirm_body", { id }),
      HomezI18n.t("decision_detail.override_confirm_ok"),
      HomezI18n.t("decision_detail.override_success"),
      HomezI18n.t("decision_detail.override_error"),
    ));
  }

  VIEW_LOADERS["decision-detail"] = loadDecisionDetail;

  // --------------------------------------------------
  // 마켓 등록 (채널별 판매 방식 선택 위저드)
  //
  // 판매 방식은 Product 전역이 아니라 채널(Marketplace 계정)별로
  // 독립적으로 선택된다. 기본값은 항상 미선택이며, 자격이 없거나
  // 아직 확인되지 않은 방식은 disabled와 함께 이유를 표시한다 — 서버
  // (app/domains/marketplace_listing/service.py)가 동일한 조건을 다시
  // 검증하므로 이 화면을 우회해도 거부된다.
  // --------------------------------------------------

  const mlState = {
    candidateId: null,
    draftId: null,
    accountsByChannel: {},
    selectedAccountIds: [],
    listingsByAccountId: {},
  };

  function mlRenderCandidatePicker() {
    const wrap = el("ml-candidate-picker-wrap");
    const loadBtn = el("ml-load-candidate-btn");
    mountApprovedCandidatePicker(wrap, "ml-candidate-picker", (chosen) => {
      el("ml-candidate-id").value = chosen.id;
      loadBtn.disabled = false;
      renderApprovedCandidateSelectedSummary(wrap, "ml-candidate-picker", chosen, () => {
        el("ml-candidate-id").value = "";
        loadBtn.disabled = true;
        mlRenderCandidatePicker();
      });
    });
  }

  async function loadMarketplaceListingWizard() {
    mlState.candidateId = null;
    mlState.draftId = null;
    mlState.accountsByChannel = {};
    mlState.selectedAccountIds = [];
    mlState.listingsByAccountId = {};

    el("ml-candidate-id").value = "";
    el("ml-load-candidate-btn").disabled = true;
    el("ml-step1-error").textContent = "";
    el("ml-step2-panel").hidden = true;
    el("ml-step3-panel").hidden = true;
    el("ml-step6-panel").hidden = true;

    mlRenderCandidatePicker();
  }

  async function mlLoadCandidateStep() {
    const errEl = el("ml-step1-error");
    errEl.textContent = "";

    const raw = el("ml-candidate-id").value.trim();
    const candidateId = Number(raw);
    if (!raw || !Number.isInteger(candidateId) || candidateId <= 0) {
      errEl.textContent = HomezI18n.t("ml.candidate_id_invalid");
      return;
    }

    mlState.candidateId = candidateId;

    try {
      const draft = await apiFetch("/marketplace-listings/drafts", {
        method: "POST",
        body: JSON.stringify({
          product_candidate_id: candidateId,
          idempotency_key: `console-draft-${candidateId}-${Date.now()}`,
          product_basics: {},
        }),
      });
      mlState.draftId = draft.id;
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("ml.draft_create_error");
      return;
    }

    await mlLoadChannelChecklist();
    el("ml-step2-panel").hidden = false;
  }

  async function mlLoadChannelChecklist() {
    const wrap = el("ml-channel-checklist");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let channels;
    try {
      channels = await apiFetch("/marketplace-listings/channels");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (channels.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("ml.channels_empty"), HomezI18n.t("ml.channels_empty_sub"));
      return;
    }

    const accountLists = await Promise.all(
      channels.map((ch) => apiFetch(`/marketplace-listings/channels/${ch.id}/accounts`).catch(() => [])),
    );

    let html = "";
    channels.forEach((ch, idx) => {
      const accounts = accountLists[idx] || [];
      mlState.accountsByChannel[ch.id] = accounts;

      const unverified = ch.doc_verification_status === "UNVERIFIED";

      html += `
        <div class="ml-channel-row">
          <div class="ml-channel-title">
            <strong>${escapeHtml(ch.name)}</strong>
            ${unverified ? `<span class="risk-tag risk-high" title="${HomezI18n.t("ml.unverified_channel_title")}">${HomezI18n.t("ml.unverified_channel_tag")}</span>` : ""}
          </div>
          ${accounts.length === 0
            ? `<p class="stat-sub">${HomezI18n.t("ml.no_accounts")}</p>`
            : accounts.map((acct) => `
              <label class="ml-checkbox-row">
                <input type="checkbox" class="ml-account-checkbox" value="${acct.id}" ${unverified ? "disabled" : ""}>
                <span>${escapeHtml(acct.account_name)} (${escapeHtml(acct.account_code)})</span>
              </label>
            `).join("")
          }
        </div>
      `;
    });

    wrap.innerHTML = html;
  }

  async function mlConfirmChannelsStep() {
    const errEl = el("ml-step2-error");
    errEl.textContent = "";

    const checked = Array.from(document.querySelectorAll(".ml-account-checkbox:checked"))
      .map((cb) => Number(cb.value));

    if (checked.length === 0) {
      errEl.textContent = HomezI18n.t("ml.channel_select_required");
      return;
    }

    mlState.selectedAccountIds = checked;

    try {
      await apiFetch(`/marketplace-listings/drafts/${mlState.draftId}/select-channels`, {
        method: "POST",
        body: JSON.stringify({ marketplace_account_ids: checked }),
      });
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("ml.channel_confirm_error");
      return;
    }

    await mlRenderFulfillmentSections();
    el("ml-step3-panel").hidden = false;
  }

  function mlFindAccount(accountId) {
    for (const list of Object.values(mlState.accountsByChannel)) {
      const found = list.find((a) => a.id === accountId);
      if (found) return found;
    }
    return null;
  }

  function mlFindChannelIdForAccount(accountId) {
    for (const [channelId, list] of Object.entries(mlState.accountsByChannel)) {
      if (list.some((a) => a.id === accountId)) return Number(channelId);
    }
    return null;
  }

  async function mlRenderFulfillmentSections() {
    const container = el("ml-fulfillment-sections");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let html = "";
    for (const accountId of mlState.selectedAccountIds) {
      const account = mlFindAccount(accountId);
      const channelId = mlFindChannelIdForAccount(accountId);

      let listing;
      try {
        listing = await apiFetch("/marketplace-listings", {
          method: "POST",
          body: JSON.stringify({
            draft_id: mlState.draftId,
            product_candidate_id: mlState.candidateId,
            marketplace_account_id: accountId,
          }),
        });
        mlState.listingsByAccountId[accountId] = listing;
      } catch (err) {
        html += `<div class="detail-panel"><h3>${escapeHtml(account ? account.account_name : String(accountId))}</h3><p class="field-error">${escapeHtml(err.message || HomezI18n.t("ml.listing_create_failed"))}</p></div>`;
        continue;
      }

      let capabilities = [];
      try {
        capabilities = await apiFetch(`/marketplace-listings/channels/${channelId}/capabilities`);
      } catch (_) {
        capabilities = [];
      }

      html += `
        <div class="detail-panel ml-fulfillment-card" data-listing-id="${listing.id}">
          <h3>${escapeHtml(account ? account.account_name : String(accountId))}</h3>
          <p class="field-label">${HomezI18n.t("ml.fulfillment_mode_label")}</p>
          ${capabilities.length === 0 ? `<p class="stat-sub">${HomezI18n.t("ml.no_verified_capabilities")}</p>` : ""}
          ${capabilities.map((cap) => {
            const disabled = !cap.is_supported || cap.status !== "VERIFIED";
            const reason = !cap.is_supported
              ? HomezI18n.t("ml.reason_not_supported")
              : cap.status !== "VERIFIED"
              ? HomezI18n.t("ml.reason_not_verified")
              : "";
            return `
              <label class="ml-radio-row ${disabled ? "is-disabled" : ""}" title="${escapeHtml(reason)}">
                <input type="radio" name="ml-mode-${listing.id}" value="${escapeHtml(cap.fulfillment_mode)}" data-capability-id="${cap.id}" ${disabled ? "disabled" : ""}>
                <span>${escapeHtml(cap.external_display_name)}</span>
                ${disabled ? `<span class="stat-sub"> — ${escapeHtml(reason)}</span>` : ""}
              </label>
            `;
          }).join("")}
          <label class="field">
            <span class="field-label">${HomezI18n.t("ml.required_fields_label")}</span>
            <textarea class="ml-required-fields-input" placeholder='${HomezI18n.t("ml.required_fields_placeholder")}'></textarea>
          </label>
          <button class="btn btn-primary ml-select-mode-btn" data-listing-id="${listing.id}">${HomezI18n.t("ml.save_mode_btn")}</button>
          <p class="field-error ml-mode-error" data-listing-id="${listing.id}"></p>
        </div>
      `;
    }

    container.innerHTML = html;

    document.querySelectorAll(".ml-select-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => mlSelectFulfillmentMode(Number(btn.dataset.listingId)));
    });
  }

  async function mlSelectFulfillmentMode(listingId) {
    const card = document.querySelector(`.ml-fulfillment-card[data-listing-id="${listingId}"]`);
    const errEl = card.querySelector(".ml-mode-error");
    errEl.textContent = "";

    const checkedRadio = card.querySelector(`input[name="ml-mode-${listingId}"]:checked`);
    if (!checkedRadio) {
      errEl.textContent = HomezI18n.t("ml.mode_select_required");
      return;
    }

    let requiredFields = {};
    const rawFields = card.querySelector(".ml-required-fields-input").value.trim();
    if (rawFields) {
      try {
        requiredFields = JSON.parse(rawFields);
      } catch (_) {
        errEl.textContent = HomezI18n.t("ml.required_fields_json_invalid");
        return;
      }
    }
    if (Object.keys(requiredFields).length === 0) {
      errEl.textContent = HomezI18n.t("ml.required_fields_required");
      return;
    }

    try {
      await apiFetch("/marketplace-listings/fulfillment-selections", {
        method: "POST",
        body: JSON.stringify({
          listing_id: listingId,
          fulfillment_mode: checkedRadio.value,
          required_fields: requiredFields,
          idempotency_key: `console-sel-${listingId}-${Date.now()}`,
        }),
      });
      toast(HomezI18n.t("ml.mode_saved"), "success");
      await mlRenderSummary();
      el("ml-step6-panel").hidden = false;
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("ml.mode_save_error");
    }
  }

  async function mlRenderSummary() {
    const wrap = el("ml-summary-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      rows = await apiFetch(`/marketplace-listings/by-candidate/${mlState.candidateId}/summary`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("ml.summary_empty"));
      return;
    }

    const colChannel = HomezI18n.t("ml.col_channel");
    const colAccount = HomezI18n.t("ml.col_account");
    const colFulfillmentMode = HomezI18n.t("ml.col_fulfillment_mode");
    const colListingStatus = HomezI18n.t("ml.col_listing_status");
    const colPause = HomezI18n.t("ml.col_pause");
    const colSubmissionHistory = HomezI18n.t("ml.col_submission_history");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colChannel}</th><th>${colAccount}</th><th>${colFulfillmentMode}</th><th>${colListingStatus}</th><th>${colPause}</th><th>${colSubmissionHistory}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr>
            <td data-label="${colChannel}">${escapeHtml(r.channel_name || "—")}</td>
            <td data-label="${colAccount}">${escapeHtml(r.account_name || "—")}</td>
            <td data-label="${colFulfillmentMode}">${r.fulfillment_mode ? `<span class="status-tag">${escapeHtml(r.fulfillment_mode)}</span>` : `<span class="risk-tag risk-high">${HomezI18n.t("ml.mode_not_selected")}</span>`}</td>
            <td data-label="${colListingStatus}">${escapeHtml(mlStatusLabel(r.listing_status))}</td>
            <td data-label="${colPause}">${
              r.listing_status === "PAUSED"
                ? `<button type="button" class="btn btn-ghost btn-sm" data-ml-resume="${r.listing_id}">${HomezI18n.t("ml.resume_btn")}</button>`
                : (r.listing_status === "READY" || r.listing_status === "APPROVED")
                  ? `<button type="button" class="btn btn-ghost btn-sm" data-ml-pause="${r.listing_id}">${HomezI18n.t("ml.pause_btn")}</button>`
                  : "—"
            }</td>
            <td data-label="${colSubmissionHistory}"><button type="button" class="btn btn-ghost btn-sm" data-ml-show-submissions="${r.listing_id}">${HomezI18n.t("ml.view_btn")}</button></td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("[data-ml-show-submissions]").forEach((btn) => {
      btn.addEventListener("click", () => mlShowSubmissions(Number(btn.dataset.mlShowSubmissions)));
    });
    wrap.querySelectorAll("[data-ml-pause]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, () => mlPauseOrResumeListing(Number(btn.dataset.mlPause), "pause")));
    });
    wrap.querySelectorAll("[data-ml-resume]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, () => mlPauseOrResumeListing(Number(btn.dataset.mlResume), "resume")));
    });
  }

  // Gate 5(2026-08-01) — 일시정지/재개는 순수 표시용 상태 전이다(서버
  // service.py의 ListingStatus.PAUSABLE_FROM 조건과 동일한 제약을
  // 서버가 다시 검증한다 — 이 버튼은 조건에 맞을 때만 그려질 뿐, 실제
  // 허용 여부는 항상 서버가 최종 판단한다).
  async function mlPauseOrResumeListing(listingId, action) {

    try {
      await apiFetch(`/marketplace-listings/${listingId}/${action}`, {
        method: "POST",
      });
      toast(action === "pause" ? HomezI18n.t("ml.pause_success") : HomezI18n.t("ml.resume_success"), "success");
      await mlRenderSummary();
    } catch (err) {
      toast(err.message || HomezI18n.t("ml.status_change_error"), "error");
    }
  }

  // Gate 5(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5) — "조회·dry-run
  // ... 를 검증한다" 요구사항: 채널별 제출 이력(성공/실패/Safety
  // 판단/승인자/오류 사유)을 조회만 하는 읽기 전용 화면. 이 화면은
  // 제출을 실행하지 않는다(POST /marketplace-listings/submissions는
  // 별도 승인 흐름을 거쳐야 하며 이 UI에서 자동 호출되지 않는다).
  async function mlShowSubmissions(listingId) {
    const panel = el("ml-submissions-panel");
    const wrap = el("ml-submissions-table-wrap");
    panel.hidden = false;
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let submissions;
    try {
      submissions = await apiFetch(`/marketplace-listings/${listingId}/submissions`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    const historyHtml = (!submissions || submissions.length === 0)
      ? `<p class="stat-sub">${HomezI18n.t("ml.no_submission_history")}</p>`
      : `
        <table class="responsive-cards">
          <thead><tr><th>${HomezI18n.t("ml.col_status")}</th><th>${HomezI18n.t("ml.col_safety_decision")}</th><th>${HomezI18n.t("ml.col_approver")}</th><th>${HomezI18n.t("ml.col_error_reason")}</th><th>${HomezI18n.t("ml.col_time")}</th></tr></thead>
          <tbody>${submissions.map((s) => `
            <tr>
              <td data-label="${HomezI18n.t("ml.col_status")}">${escapeHtml(mlSubmissionStatusLabel(s.status))}</td>
              <td data-label="${HomezI18n.t("ml.col_safety_decision")}">${escapeHtml(s.safety_decision)}</td>
              <td data-label="${HomezI18n.t("ml.col_approver")}">${s.operator_approved_by != null ? escapeHtml(String(s.operator_approved_by)) : "—"}</td>
              <td data-label="${HomezI18n.t("ml.col_error_reason")}">${s.error_reason ? escapeHtml(s.error_reason) : "—"}</td>
              <td data-label="${HomezI18n.t("ml.col_time")}">${fmtDate(s.attempted_at)}</td>
            </tr>
          `).join("")}</tbody>
        </table>
      `;

    wrap.innerHTML = historyHtml + `<div id="ml-approval-actions"><p class="loading-text">${HomezI18n.t("ml.approval_checking")}</p></div>`;

    await mlRenderApprovalActions(listingId);
  }

  // Gate 5(2026-08-01) — "승인·거절·취소·재시도"를 이 패널에서
  // 수행한다. 승인 요청 생성 자체(금액·수량 입력 폼)는 이 화면의
  // 범위 밖이다 — 이미 존재하는 PENDING/APPROVED 승인 건에 대한
  // 결정과, 실패한 제출의 재시도만 다룬다.
  async function mlRenderApprovalActions(listingId) {
    const area = el("ml-approval-actions");
    if (!area) return;

    let selection;
    try {
      selection = await apiFetch(`/marketplace-listings/${listingId}/current-selection`);
    } catch (err) {
      area.innerHTML = "";
      return;
    }
    if (!selection) {
      area.innerHTML = "";
      return;
    }

    let history;
    try {
      history = await apiFetch(`/marketplace-listings/approvals/${listingId}/${selection.id}/history`);
    } catch (err) {
      area.innerHTML = "";
      return;
    }

    const latest = history && history.length > 0 ? history[history.length - 1] : null;

    let actionsHtml = "";
    if (latest && latest.status === "PENDING") {
      actionsHtml = `
        <button type="button" class="btn btn-primary btn-sm" id="ml-approve-btn" data-approval-id="${latest.id}">${HomezI18n.t("ml.approve_btn")}</button>
        <button type="button" class="btn btn-ghost btn-sm" id="ml-reject-btn" data-approval-id="${latest.id}">${HomezI18n.t("ml.reject_btn")}</button>
      `;
    } else if (latest && latest.status === "APPROVED") {
      actionsHtml = `<button type="button" class="btn btn-ghost btn-sm" id="ml-revoke-btn" data-approval-id="${latest.id}">${HomezI18n.t("ml.revoke_btn")}</button>`;
    }
    actionsHtml += ` <button type="button" class="btn btn-ghost btn-sm" id="ml-retry-submit-btn" data-listing-id="${listingId}" data-selection-id="${selection.id}">${HomezI18n.t("ml.retry_submit_btn")}</button>`;

    area.innerHTML = `<div class="ml-approval-actions-row">${actionsHtml}</div>`;

    const approveBtn = el("ml-approve-btn");
    if (approveBtn) {
      approveBtn.addEventListener("click", () => withButtonGuard(approveBtn, () => mlDecideApproval(approveBtn.dataset.approvalId, "approve", listingId)));
    }
    const rejectBtn = el("ml-reject-btn");
    if (rejectBtn) {
      rejectBtn.addEventListener("click", () => withButtonGuard(rejectBtn, () => mlDecideApproval(rejectBtn.dataset.approvalId, "reject", listingId)));
    }
    const revokeBtn = el("ml-revoke-btn");
    if (revokeBtn) {
      revokeBtn.addEventListener("click", () => withButtonGuard(revokeBtn, () => mlDecideApproval(revokeBtn.dataset.approvalId, "revoke", listingId)));
    }
    const retryBtn = el("ml-retry-submit-btn");
    if (retryBtn) {
      retryBtn.addEventListener("click", () => withButtonGuard(retryBtn, () => mlRetrySubmit(
        Number(retryBtn.dataset.listingId), Number(retryBtn.dataset.selectionId),
      )));
    }
  }

  async function mlDecideApproval(approvalId, action, listingId) {

    const idempotencyKey = `ml-appr-${action}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

    try {
      if (action === "approve") {
        const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
        await apiFetch(`/marketplace-listings/approvals/${approvalId}/approve`, {
          method: "POST",
          body: JSON.stringify({ expires_at: expiresAt, idempotency_key: idempotencyKey }),
        });
      } else {
        const reason = window.prompt(action === "reject" ? HomezI18n.t("ml.reject_reason_prompt") : HomezI18n.t("ml.revoke_reason_prompt"));
        if (!reason || !reason.trim()) {
          toast(HomezI18n.t("ml.reason_required"), "error");
          return;
        }
        await apiFetch(`/marketplace-listings/approvals/${approvalId}/${action}`, {
          method: "POST",
          body: JSON.stringify({ reason, idempotency_key: idempotencyKey }),
        });
      }
      toast(HomezI18n.t("ml.processed_success"), "success");
      await mlShowSubmissions(listingId);
      await mlRenderSummary();
    } catch (err) {
      toast(err.message || HomezI18n.t("ml.process_error"), "error");
    }
  }

  async function mlRetrySubmit(listingId, selectionId) {

    const idempotencyKey = `ml-sub-retry-${Date.now()}-${Math.random().toString(36).slice(2)}`;

    try {
      await apiFetch("/marketplace-listings/submissions", {
        method: "POST",
        body: JSON.stringify({
          listing_id: listingId, selection_id: selectionId,
          idempotency_key: idempotencyKey,
        }),
      });
      toast(HomezI18n.t("ml.retry_success"), "success");
      await mlShowSubmissions(listingId);
      await mlRenderSummary();
    } catch (err) {
      toast(err.message || HomezI18n.t("ml.retry_error"), "error");
    }
  }

  async function mlFinalizeStep() {
    const errEl = el("ml-step6-error");
    errEl.textContent = "";

    try {
      await apiFetch(`/marketplace-listings/drafts/${mlState.draftId}/finalize-fulfillment-selection`, {
        method: "POST",
      });
      toast(HomezI18n.t("ml.finalize_success"), "success");
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("ml.finalize_error");
    }
  }

  // --------------------------------------------------
  // Gate I(2026-08-08) — 상품등록 통합 마법사(listing-wizard).
  // 기존 도메인(product_candidate/media_asset/marketplace_listing)을
  // 그대로 호출하는 얇은 오케스트레이션 API(/listing-wizards)만
  // 쓴다 — 이 화면 자신은 채널 목록·계정 목록·이미지 목록 등을 새로
  // 조회하는 로직을 만들지 않고 기존 엔드포인트를 그대로 재사용한다.
  // --------------------------------------------------

  const LW_STEPS = [
    "SOURCE", "DRAFT", "MEDIA", "CHANNELS", "FULFILLMENT",
    "ECONOMICS", "PRECHECK", "APPROVAL", "EXECUTION", "RESULTS",
  ];

  // 2026-08-29 — 쿠팡 공식 택배사 코드(deliveryCompanyCode). 실제
  // 화면에는 한국어 택배사명만 보이고, 실제 전송값(value)은 쿠팡
  // 공식 문서(developers.coupang.com courier-code 목록)의 영문 코드
  // 그대로 유지한다 — "프로그래밍상으로는 영어 유지, 표기는 한글로"
  // 사용자 지시에 따른 구조. 롯데택배는 공식 문서에 레거시 코드
  // HYUNDAI로 매핑되어 있다(과거 현대택배 인수 이력 — 표기만
  // "롯데택배"로 바꾸고 코드는 문서 그대로 사용).
  const LW_COUPANG_DELIVERY_COMPANIES = [
    { code: "HANJIN", label: "한진택배" },
    { code: "HYUNDAI", label: "롯데택배" },
    { code: "KGB", label: "로젠택배" },
    { code: "EPOST", label: "우체국" },
    { code: "CJGLS", label: "CJ대한통운" },
    { code: "KDEXP", label: "경동택배" },
    { code: "ILYANG", label: "일양택배" },
    { code: "CHUNIL", label: "천일택배" },
    { code: "AJOU", label: "아주택배" },
    { code: "CSLOGIS", label: "SC로지스" },
    { code: "DAESIN", label: "대신택배" },
    { code: "CVS", label: "CVS택배" },
    { code: "HDEXP", label: "합동택배" },
    { code: "DHL", label: "DHL" },
  ];

  const lwState = {
    wizard: null,
    viewingStep: "SOURCE",
  };

  // 2026-08-28 "대기 상품 정리" — 목록 화면 전용 상태(선택/필터). 필터가
  // 바뀌면(archived 토글) 선택을 초기화한다 — "화면에 보이지 않는
  // 항목이 임의로 선택되지 않게 한다"는 요구를 그대로 구현.
  const lwListState = {
    selectedIds: new Set(),
    includeArchived: false,
    rows: [],
    deletableTotal: 0,
    deletableCapped: false,
  };

  // Gate R-1(2026-08-09) — 현재 단계의 "서버 값이 아닌" 화면 전용
  // 결과(사전검사 issue 목록, 승인/취소 미리보기 nonce)를 담아 둔다.
  // 실제 단계 전환(이전/다음/점프)에서는 lwRenderStep()이 자동으로
  // 비운다 — 언어 전환으로 같은 단계를 다시 그릴 때만 유지된다.
  let lwStepEphemeralCache = null;

  // 2026-08-20 3차 지시 — 이미지 단계/최종 검토 단계 썸네일은 인증
  // 헤더가 필요해 <img src>로 직접 불러올 수 없다(guidesObjectUrlFor와
  // 동일한 이유). apiFetchBlob() + createObjectURL로 받은 blob URL을
  // 재렌더 직전에 항상 회수한다.
  let lwMediaObjectUrls = [];
  let lwLongImageUploadInFlight = false;
  function lwRevokeMediaObjectUrls() {
    lwMediaObjectUrls.forEach((u) => URL.revokeObjectURL(u));
    lwMediaObjectUrls = [];
  }
  async function lwLoadThumbUrl(assetId) {
    const blob = await apiFetchBlob(`/media-assets/${assetId}/file`);
    const url = URL.createObjectURL(blob);
    lwMediaObjectUrls.push(url);
    return url;
  }
  function lwFormatBytes(n) {
    if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
    return n + " B";
  }
  function lwRightsBadge(rightsStatus) {
    const verified = rightsStatus === "VERIFIED";
    const cls = "lw-rights-badge " + (verified ? "lw-rights-badge-verified" : "lw-rights-badge-unverified");
    const label = HomezI18n.t(verified ? "lw.rights_verified_badge" : "lw.rights_unverified_badge");
    return `<span class="${cls}">${label}</span>`;
  }

  // 2026-08-29 Phase 5 — Fabric.js 수동 편집기(자르기/회전/밝기·대비/
  // 도형·화살표/되돌리기) MVP. 편집 자체는 브라우저 캔버스에서만
  // 일어나고, 서버는 최종 결과 이미지 1장만 받는다(app/domains/
  // media_asset/router.py::save_manual_edit, POST /media-assets/
  // manual-edit). 화면 해상도(LW_EDIT_MAX_SIDE) 안에서 편집·저장하는
  // MVP 범위 — 원본이 이보다 크면 저장 결과도 그 해상도로 줄어든다
  // (사용자에게 명시적으로 안내한다, 조용히 품질을 낮추지 않는다).
  // 640 — 모달의 실제 표시 가능 폭(툴바 220px 등을 뺀 나머지)에
  // 안전하게 맞도록 보수적으로 잡은 값(2026-08-29 브라우저 실측
  // 확인: 1280은 모달 안에서 스크롤이 생겨 화면이 잘려 보였다).
  const LW_EDIT_MAX_SIDE = 640;
  const LW_EDIT_HISTORY_LIMIT = 20;
  const LW_EDIT_ACCENT = "#FF6BC1";
  let lwFabricLoadPromise = null;

  function lwEnsureFabricLoaded() {
    if (window.fabric) return Promise.resolve();
    if (lwFabricLoadPromise) return lwFabricLoadPromise;
    lwFabricLoadPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/console/static/vendor/fabric.min.js";
      s.onload = () => resolve();
      s.onerror = () => { lwFabricLoadPromise = null; reject(new Error(HomezI18n.t("lw.media_edit_fabric_load_error"))); };
      document.head.appendChild(s);
    });
    return lwFabricLoadPromise;
  }

  function lwBuildArrow(x1, y1, x2, y2) {
    const angleDeg = Math.atan2(y2 - y1, x2 - x1) * (180 / Math.PI);
    const line = new fabric.Line([x1, y1, x2, y2], {
      stroke: LW_EDIT_ACCENT, strokeWidth: 3, selectable: false, evented: false,
    });
    const head = new fabric.Triangle({
      left: x2, top: y2, width: 14, height: 16,
      fill: LW_EDIT_ACCENT, angle: angleDeg + 90,
      originX: "center", originY: "center", selectable: false, evented: false,
    });
    return new fabric.Group([line, head], { selectable: true, evented: true });
  }

  function lwRotateDataUrl(dataUrl, w, h, clockwise) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const off = document.createElement("canvas");
        off.width = h; off.height = w;
        const ctx = off.getContext("2d");
        ctx.translate(h / 2, w / 2);
        ctx.rotate((clockwise ? 90 : -90) * Math.PI / 180);
        ctx.drawImage(img, -w / 2, -h / 2, w, h);
        resolve({ dataUrl: off.toDataURL("image/png"), width: h, height: w });
      };
      img.onerror = () => reject(new Error(HomezI18n.t("common.load_error")));
      img.src = dataUrl;
    });
  }

  async function lwOpenManualEditor(assetId, onSaved) {
    const FabricImageCtor = () => window.fabric.FabricImage || window.fabric.Image;

    const overlay = document.createElement("div");
    overlay.className = "lw-edit-modal-overlay";
    overlay.innerHTML = `
      <div class="lw-edit-modal">
        <div class="lw-edit-modal-header">
          <h3>${HomezI18n.t("lw.media_manual_edit_title")}</h3>
          <button type="button" class="btn btn-ghost btn-sm" id="lw-edit-close-btn">${HomezI18n.t("common.close")}</button>
        </div>
        <div class="lw-edit-modal-body">
          <div class="lw-edit-canvas-wrap"><canvas id="lw-edit-canvas"></canvas></div>
          <div class="lw-edit-toolbar">
            <div class="lw-edit-toolbar-group">
              <p class="lw-edit-toolbar-group-title">${HomezI18n.t("lw.media_edit_crop_group")}</p>
              <div class="lw-edit-toolbar-row">
                <button type="button" class="btn btn-secondary btn-sm" id="lw-edit-crop-start-btn">${HomezI18n.t("lw.media_edit_crop_start")}</button>
                <button type="button" class="btn btn-secondary btn-sm" id="lw-edit-crop-apply-btn" hidden>${HomezI18n.t("lw.media_edit_crop_apply")}</button>
                <button type="button" class="btn btn-ghost btn-sm" id="lw-edit-crop-cancel-btn" hidden>${HomezI18n.t("lw.media_edit_cancel")}</button>
              </div>
            </div>
            <div class="lw-edit-toolbar-group">
              <p class="lw-edit-toolbar-group-title">${HomezI18n.t("lw.media_edit_rotate_group")}</p>
              <div class="lw-edit-toolbar-row">
                <button type="button" class="btn btn-secondary btn-sm" id="lw-edit-rotate-left-btn">↺ 90°</button>
                <button type="button" class="btn btn-secondary btn-sm" id="lw-edit-rotate-right-btn">↻ 90°</button>
              </div>
            </div>
            <div class="lw-edit-toolbar-group">
              <p class="lw-edit-toolbar-group-title">${HomezI18n.t("lw.media_edit_adjust_group")}</p>
              <label class="lw-edit-slider-row">${HomezI18n.t("lw.media_edit_brightness")}
                <input type="range" id="lw-edit-brightness" min="-100" max="100" value="0">
              </label>
              <label class="lw-edit-slider-row">${HomezI18n.t("lw.media_edit_contrast")}
                <input type="range" id="lw-edit-contrast" min="-100" max="100" value="0">
              </label>
            </div>
            <div class="lw-edit-toolbar-group">
              <p class="lw-edit-toolbar-group-title">${HomezI18n.t("lw.media_edit_shape_group")}</p>
              <div class="lw-edit-toolbar-row">
                <button type="button" class="btn btn-secondary btn-sm lw-edit-tool-btn" data-tool="rect">${HomezI18n.t("lw.media_edit_rect")}</button>
                <button type="button" class="btn btn-secondary btn-sm lw-edit-tool-btn" data-tool="circle">${HomezI18n.t("lw.media_edit_circle")}</button>
                <button type="button" class="btn btn-secondary btn-sm lw-edit-tool-btn" data-tool="arrow">${HomezI18n.t("lw.media_edit_arrow")}</button>
              </div>
              <div class="lw-edit-toolbar-row">
                <button type="button" class="btn btn-ghost btn-sm" id="lw-edit-tool-select-btn">${HomezI18n.t("lw.media_edit_select")}</button>
                <button type="button" class="btn btn-ghost btn-sm" id="lw-edit-delete-btn">${HomezI18n.t("lw.media_edit_delete_selected")}</button>
              </div>
            </div>
            <div class="lw-edit-toolbar-group">
              <button type="button" class="btn btn-ghost btn-sm" id="lw-edit-undo-btn">${HomezI18n.t("lw.media_edit_undo")}</button>
            </div>
          </div>
        </div>
        <div class="lw-edit-modal-footer">
          <span class="lw-edit-status" id="lw-edit-status"></span>
          <div class="lw-edit-toolbar-row">
            <button type="button" class="btn btn-ghost" id="lw-edit-cancel-btn">${HomezI18n.t("lw.media_edit_cancel")}</button>
            <button type="button" class="btn btn-primary" id="lw-edit-save-btn" disabled>${HomezI18n.t("lw.media_edit_save_btn")}</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const statusEl = overlay.querySelector("#lw-edit-status");
    const setStatus = (text, isError) => {
      statusEl.textContent = text || "";
      statusEl.classList.toggle("field-error", !!isError);
    };

    let canvas = null;
    const closeModal = () => {
      try { if (canvas) canvas.dispose(); } catch (err) { /* 편집기 정리 중 오류는 무시 */ }
      overlay.remove();
    };
    overlay.querySelector("#lw-edit-close-btn").addEventListener("click", closeModal);
    overlay.querySelector("#lw-edit-cancel-btn").addEventListener("click", closeModal);
    overlay.addEventListener("click", (ev) => { if (ev.target === overlay) closeModal(); });

    setStatus(HomezI18n.t("common.loading"));

    try {
      await lwEnsureFabricLoaded();
    } catch (err) {
      setStatus(err.message, true);
      return;
    }

    let imageUrl;
    try {
      imageUrl = await lwLoadThumbUrl(assetId);
    } catch (err) {
      setStatus(err.message || HomezI18n.t("common.load_error"), true);
      return;
    }

    const canvasEl = overlay.querySelector("#lw-edit-canvas");
    canvas = new fabric.Canvas(canvasEl, { selection: true, preserveObjectStacking: true });

    let baseImage;
    try {
      baseImage = await FabricImageCtor().fromURL(imageUrl, {});
    } catch (err) {
      setStatus(HomezI18n.t("common.load_error"), true);
      return;
    }

    const naturalW = baseImage.width;
    const naturalH = baseImage.height;
    const initialScale = Math.min(1, LW_EDIT_MAX_SIDE / Math.max(naturalW, naturalH));
    const dispW = Math.max(1, Math.round(naturalW * initialScale));
    const dispH = Math.max(1, Math.round(naturalH * initialScale));

    canvas.setDimensions({ width: dispW, height: dispH });
    baseImage.set({
      left: 0, top: 0, originX: "left", originY: "top",
      scaleX: initialScale, scaleY: initialScale,
      selectable: false, evented: false, hasControls: false,
    });
    canvas.add(baseImage);
    canvas.requestRenderAll();

    setStatus(
      initialScale < 1
        ? HomezI18n.t("lw.media_edit_downscaled_notice", { size: LW_EDIT_MAX_SIDE })
        : "",
    );

    let currentBaseImage = baseImage;
    const saveBtn = overlay.querySelector("#lw-edit-save-btn");
    saveBtn.disabled = false;

    // ---- 되돌리기(다단계 undo) ----
    let history = [];
    let restoring = false;
    function snapshot() {
      if (restoring) return;
      history.push(JSON.stringify(canvas.toJSON()));
      if (history.length > LW_EDIT_HISTORY_LIMIT) history.shift();
    }
    async function undo() {
      if (history.length < 2 || restoring) return;
      restoring = true;
      history.pop();
      const prevJson = JSON.parse(history[history.length - 1]);
      await canvas.loadFromJSON(prevJson);
      const objects = canvas.getObjects();
      if (objects.length) {
        currentBaseImage = objects[0];
        currentBaseImage.set({ selectable: false, evented: false, hasControls: false });
      }
      canvas.requestRenderAll();
      restoring = false;
    }
    snapshot();
    canvas.on("object:added", snapshot);
    canvas.on("object:modified", snapshot);
    canvas.on("object:removed", snapshot);
    overlay.querySelector("#lw-edit-undo-btn").addEventListener("click", undo);

    // ---- 도구 공통: 현재 캔버스를 그대로 굽고(flatten) 새 배경
    // 이미지로 교체한다 — 자르기·회전 둘 다 이 방식으로 구현해
    // 도형이 함께 반영된 결과를 그대로 유지한다.
    async function replaceCanvasWithFlatImage(dataUrl, w, h) {
      canvas.clear();
      canvas.setDimensions({ width: w, height: h });
      const img = await FabricImageCtor().fromURL(dataUrl, {});
      img.set({
        left: 0, top: 0, originX: "left", originY: "top",
        selectable: false, evented: false, hasControls: false,
      });
      canvas.add(img);
      currentBaseImage = img;
      overlay.querySelector("#lw-edit-brightness").value = "0";
      overlay.querySelector("#lw-edit-contrast").value = "0";
      canvas.requestRenderAll();
      snapshot();
    }

    // ---- 회전 ----
    async function applyRotate(clockwise) {
      const w = canvas.getWidth();
      const h = canvas.getHeight();
      canvas.discardActiveObject();
      canvas.requestRenderAll();
      const flatUrl = canvas.toDataURL({ format: "png" });
      const rotated = await lwRotateDataUrl(flatUrl, w, h, clockwise);
      await replaceCanvasWithFlatImage(rotated.dataUrl, rotated.width, rotated.height);
    }
    overlay.querySelector("#lw-edit-rotate-left-btn").addEventListener(
      "click", () => withButtonGuard(overlay.querySelector("#lw-edit-rotate-left-btn"), () => applyRotate(false)),
    );
    overlay.querySelector("#lw-edit-rotate-right-btn").addEventListener(
      "click", () => withButtonGuard(overlay.querySelector("#lw-edit-rotate-right-btn"), () => applyRotate(true)),
    );

    // ---- 자르기 ----
    let cropRect = null;
    const cropStartBtn = overlay.querySelector("#lw-edit-crop-start-btn");
    const cropApplyBtn = overlay.querySelector("#lw-edit-crop-apply-btn");
    const cropCancelBtn = overlay.querySelector("#lw-edit-crop-cancel-btn");
    function setCropUiOpen(open) {
      cropStartBtn.hidden = open;
      cropApplyBtn.hidden = !open;
      cropCancelBtn.hidden = !open;
    }
    cropStartBtn.addEventListener("click", () => {
      if (cropRect) return;
      const w = canvas.getWidth();
      const h = canvas.getHeight();
      cropRect = new fabric.Rect({
        left: Math.round(w * 0.1), top: Math.round(h * 0.1),
        originX: "left", originY: "top",
        width: Math.round(w * 0.8), height: Math.round(h * 0.8),
        fill: "rgba(255,107,193,0.08)", stroke: LW_EDIT_ACCENT, strokeWidth: 2,
        strokeDashArray: [6, 4], cornerColor: LW_EDIT_ACCENT, transparentCorners: false,
      });
      canvas.add(cropRect);
      canvas.setActiveObject(cropRect);
      canvas.requestRenderAll();
      setCropUiOpen(true);
    });
    cropCancelBtn.addEventListener("click", () => {
      if (cropRect) { canvas.remove(cropRect); cropRect = null; }
      setCropUiOpen(false);
      canvas.requestRenderAll();
    });
    cropApplyBtn.addEventListener("click", () => withButtonGuard(cropApplyBtn, async () => {
      if (!cropRect) return;
      const bounds = cropRect.getBoundingRect();
      const left = Math.max(0, Math.round(bounds.left));
      const top = Math.max(0, Math.round(bounds.top));
      const width = Math.max(1, Math.round(bounds.width));
      const height = Math.max(1, Math.round(bounds.height));
      canvas.remove(cropRect);
      cropRect = null;
      const dataUrl = canvas.toDataURL({ format: "png", left, top, width, height });
      await replaceCanvasWithFlatImage(dataUrl, width, height);
      setCropUiOpen(false);
    }));

    // ---- 밝기 · 대비(현재 배경 이미지에만 적용) ----
    const brightnessInput = overlay.querySelector("#lw-edit-brightness");
    const contrastInput = overlay.querySelector("#lw-edit-contrast");
    function applyAdjustments() {
      const b = Number(brightnessInput.value) / 100;
      const c = Number(contrastInput.value) / 100;
      const filters = [];
      if (b !== 0) filters.push(new fabric.filters.Brightness({ brightness: b }));
      if (c !== 0) filters.push(new fabric.filters.Contrast({ contrast: c }));
      currentBaseImage.filters = filters;
      currentBaseImage.applyFilters();
      canvas.requestRenderAll();
    }
    brightnessInput.addEventListener("input", applyAdjustments);
    contrastInput.addEventListener("input", applyAdjustments);
    brightnessInput.addEventListener("change", snapshot);
    contrastInput.addEventListener("change", snapshot);

    // ---- 도형 · 화살표(드래그로 그리기) ----
    let activeTool = null;
    let drawingObj = null;
    let drawStart = null;
    const toolButtons = Array.from(overlay.querySelectorAll(".lw-edit-tool-btn"));
    function setActiveTool(tool) {
      activeTool = activeTool === tool ? null : tool;
      toolButtons.forEach((b) => b.classList.toggle("lw-edit-tool-active", b.dataset.tool === activeTool));
      canvas.selection = !activeTool;
      canvas.defaultCursor = activeTool ? "crosshair" : "default";
      canvas.discardActiveObject();
      canvas.requestRenderAll();
    }
    toolButtons.forEach((b) => b.addEventListener("click", () => setActiveTool(b.dataset.tool)));
    overlay.querySelector("#lw-edit-tool-select-btn").addEventListener("click", () => setActiveTool(null));
    overlay.querySelector("#lw-edit-delete-btn").addEventListener("click", () => {
      canvas.getActiveObjects().forEach((o) => { if (o !== currentBaseImage) canvas.remove(o); });
      canvas.discardActiveObject();
      canvas.requestRenderAll();
    });

    canvas.on("mouse:down", (opt) => {
      // Fabric v6 — 이벤트 payload가 opt.pointer 대신 opt.scenePoint를
      // 쓴다(구 API와의 breaking change, 2026-08-29 브라우저 실측으로
      // 확인). opt.pointer는 이 빌드에 존재하지 않는다.
      if (!activeTool || !opt.scenePoint) return;
      drawStart = { x: opt.scenePoint.x, y: opt.scenePoint.y };
      if (activeTool === "rect") {
        drawingObj = new fabric.Rect({
          left: drawStart.x, top: drawStart.y, originX: "left", originY: "top",
          width: 1, height: 1,
          fill: "rgba(255,107,193,0.15)", stroke: LW_EDIT_ACCENT, strokeWidth: 3,
          selectable: false, evented: false,
        });
      } else if (activeTool === "circle") {
        drawingObj = new fabric.Circle({
          left: drawStart.x, top: drawStart.y, originX: "left", originY: "top",
          radius: 1,
          fill: "rgba(255,107,193,0.15)", stroke: LW_EDIT_ACCENT, strokeWidth: 3,
          selectable: false, evented: false,
        });
      } else if (activeTool === "arrow") {
        drawingObj = new fabric.Line(
          [drawStart.x, drawStart.y, drawStart.x, drawStart.y],
          { stroke: LW_EDIT_ACCENT, strokeWidth: 3, selectable: false, evented: false },
        );
      }
      if (drawingObj) canvas.add(drawingObj);
    });
    canvas.on("mouse:move", (opt) => {
      if (!activeTool || !drawingObj || !opt.scenePoint) return;
      const p = opt.scenePoint;
      if (activeTool === "rect") {
        drawingObj.set({
          left: Math.min(drawStart.x, p.x), top: Math.min(drawStart.y, p.y),
          width: Math.abs(p.x - drawStart.x), height: Math.abs(p.y - drawStart.y),
        });
      } else if (activeTool === "circle") {
        const r = Math.max(Math.abs(p.x - drawStart.x), Math.abs(p.y - drawStart.y)) / 2;
        drawingObj.set({ left: drawStart.x - r, top: drawStart.y - r, radius: r });
      } else if (activeTool === "arrow") {
        drawingObj.set({ x2: p.x, y2: p.y });
      }
      canvas.requestRenderAll();
    });
    canvas.on("mouse:up", () => {
      if (!activeTool || !drawingObj) return;
      const finished = drawingObj;
      drawingObj = null;
      const usedTool = activeTool;
      setActiveTool(null);
      if (usedTool === "arrow") {
        canvas.remove(finished);
        const group = lwBuildArrow(drawStart.x, drawStart.y, finished.x2, finished.y2);
        canvas.add(group);
        canvas.setActiveObject(group);
      } else {
        finished.set({ selectable: true, evented: true });
        canvas.setActiveObject(finished);
      }
      canvas.requestRenderAll();
    });

    // ---- 저장 ----
    saveBtn.addEventListener("click", () => withButtonGuard(saveBtn, async () => {
      setStatus(HomezI18n.t("lw.media_edit_saving"));
      try {
        canvas.discardActiveObject();
        canvas.requestRenderAll();
        const dataUrl = canvas.toDataURL({ format: "png" });
        const base64 = dataUrl.split(",").pop();
        await apiFetch("/media-assets/manual-edit", {
          method: "POST",
          body: JSON.stringify({ source_asset_id: assetId, image_base64: base64 }),
        });
        toast(HomezI18n.t("lw.media_manual_edit_saved"), "success");
        closeModal();
        if (onSaved) await onSaved();
      } catch (err) {
        setStatus(err.message || HomezI18n.t("common.load_error"), true);
      }
    }));
  }

  async function loadListingWizardView() {
    lwState.wizard = null;
    el("lw-wizard-panel").hidden = true;
    el("lw-list-panel").hidden = false;
    await lwRenderList();
  }

  function lwStatusLabel(status) {
    const key = `lw.status.${String(status).toLowerCase()}`;
    const label = HomezI18n.t(key);
    return label === key ? status : label;
  }

  async function lwRenderList() {
    const wrap = el("lw-list-table-wrap");
    const archivedToggle = el("lw-show-archived-toggle");
    archivedToggle.checked = lwListState.includeArchived;
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    try {
      const query = lwListState.includeArchived ? "?include_archived=true" : "";
      const [rows, preview] = await Promise.all([
        apiFetch(`/listing-wizards${query}`),
        apiFetch("/listing-wizards/bulk-archive-preview"),
      ]);
      lwListState.rows = rows;
      lwListState.deletableTotal = preview.deletable_count;
      lwListState.deletableCapped = preview.capped;

      // 필터가 바뀌어 화면에서 사라진 항목은 선택에서도 제거한다.
      Array.from(lwListState.selectedIds).forEach((id) => {
        if (!rows.some((r) => r.id === id)) lwListState.selectedIds.delete(id);
      });

      if (!rows.length) {
        wrap.innerHTML = `<p class="empty-text">${HomezI18n.t("lw.list_empty")}</p>`;
        lwRenderBulkActionBar();
        return;
      }

      const anyDeletable = rows.some((r) => r.deletable);
      wrap.innerHTML = `
        <table>
          <thead><tr>
            <th class="lw-header-checkbox-cell">${anyDeletable ? `<input type="checkbox" id="lw-select-all-checkbox" aria-label="${HomezI18n.t("lw.select_all_aria")}">` : ""}</th>
            <th>ID</th>
            <th>${HomezI18n.t("lw.list_col_status")}</th>
            <th>${HomezI18n.t("lw.list_col_step")}</th>
            <th>${HomezI18n.t("lw.list_col_updated")}</th>
            <th></th>
          </tr></thead>
          <tbody>${rows.map((r) => {
            const checked = lwListState.selectedIds.has(r.id) ? "checked" : "";
            const disabledAttr = r.deletable ? "" : "disabled";
            const titleAttr = r.deletable ? "" : ` title="${escapeHtml(r.block_reason || "")}"`;
            const isArchived = r.status === "ARCHIVED";
            return `
            <tr${isArchived ? ' class="lw-row-archived"' : ""}>
              <td class="lw-row-checkbox-cell"><span${titleAttr}><input type="checkbox" class="lw-row-checkbox" data-lw-row-id="${r.id}" aria-label="${HomezI18n.t("lw.select_row_aria")}" ${checked} ${disabledAttr}></span></td>
              <td>${r.id}</td>
              <td><span class="pill">${escapeHtml(lwStatusLabel(r.status))}</span></td>
              <td>${HomezI18n.t(`lw.step.${r.current_step.toLowerCase()}`)}</td>
              <td>${escapeHtml(HomezI18n.formatDate(lsParseUtc(r.updated_at)))}</td>
              <td>${isArchived
                ? `<button class="btn btn-secondary" data-lw-restore="${r.id}">${HomezI18n.t("lw.restore_btn")}</button>`
                : `<button class="btn btn-secondary" data-lw-open="${r.id}">${HomezI18n.t("lw.open_btn")}</button>
                   <span${titleAttr}><button type="button" class="icon-btn lw-delete-icon-btn" data-lw-delete="${r.id}" aria-label="${HomezI18n.t("lw.delete_icon_aria")}" ${disabledAttr}>🗑</button></span>`
              }</td>
            </tr>`;
          }).join("")}
          </tbody>
        </table>`;

      wrap.querySelectorAll("[data-lw-open]").forEach((btn) => {
        btn.addEventListener("click", () => lwOpenWizard(Number(btn.dataset.lwOpen)));
      });
      wrap.querySelectorAll("[data-lw-restore]").forEach((btn) => {
        btn.addEventListener("click", () => withButtonGuard(btn, () => lwRestoreWizard(Number(btn.dataset.lwRestore))));
      });
      wrap.querySelectorAll("[data-lw-delete]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = Number(btn.dataset.lwDelete);
          const row = lwListState.rows.find((r) => r.id === id);
          lwDeleteSingleWizard(id, row, btn);
        });
      });
      wrap.querySelectorAll(".lw-row-checkbox").forEach((cb) => {
        cb.addEventListener("change", () => {
          const id = Number(cb.dataset.lwRowId);
          if (cb.checked) lwListState.selectedIds.add(id);
          else lwListState.selectedIds.delete(id);
          lwSyncSelectAllCheckboxState();
          lwRenderBulkActionBar();
        });
      });
      const selectAllCb = el("lw-select-all-checkbox");
      if (selectAllCb) {
        selectAllCb.addEventListener("change", () => {
          wrap.querySelectorAll(".lw-row-checkbox:not([disabled])").forEach((cb) => {
            cb.checked = selectAllCb.checked;
            const id = Number(cb.dataset.lwRowId);
            if (selectAllCb.checked) lwListState.selectedIds.add(id);
            else lwListState.selectedIds.delete(id);
          });
          lwRenderBulkActionBar();
        });
        lwSyncSelectAllCheckboxState();
      }
    } catch (err) {
      wrap.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
    }
    lwRenderBulkActionBar();
  }

  function lwSyncSelectAllCheckboxState() {
    const selectAllCb = el("lw-select-all-checkbox");
    if (!selectAllCb) return;
    const deletableRows = lwListState.rows.filter((r) => r.deletable);
    const selectedCount = deletableRows.filter((r) => lwListState.selectedIds.has(r.id)).length;
    selectAllCb.checked = deletableRows.length > 0 && selectedCount === deletableRows.length;
    selectAllCb.indeterminate = selectedCount > 0 && selectedCount < deletableRows.length;
  }

  function lwRenderBulkActionBar() {
    const bar = el("lw-bulk-action-bar");
    const countEl = el("lw-bulk-selected-count");
    const selectedCount = lwListState.selectedIds.size;
    bar.hidden = selectedCount === 0;
    if (selectedCount > 0) {
      countEl.textContent = HomezI18n.t("lw.bulk_selected_count", { count: selectedCount });
    }

    const allBtn = el("lw-bulk-delete-all-btn");
    const total = lwListState.deletableTotal || 0;
    if (total === 0) {
      allBtn.hidden = true;
      return;
    }
    allBtn.hidden = false;
    allBtn.textContent = lwListState.deletableCapped
      ? HomezI18n.t("lw.bulk_delete_all_btn_capped", { count: total })
      : HomezI18n.t("lw.bulk_delete_all_btn", { count: total });
    allBtn.onclick = () => lwDeleteAllMatchingFilter();
  }

  function lwGenerateRequestId(prefix) {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function lwToastBulkResult(response, keyPrefix) {
    const prefix = keyPrefix || "lw.bulk_delete_result";
    const succeeded = response.succeeded.length;
    const skipped = response.skipped.length;
    const failed = response.failed.length;
    if (failed > 0) {
      toast(HomezI18n.t(`${prefix}_has_failure`), "error");
    } else if (skipped > 0) {
      toast(HomezI18n.t(`${prefix}_partial`, { succeeded, skipped }), "success");
    } else {
      toast(HomezI18n.t(`${prefix}_all_succeeded`, { succeeded }), "success");
    }
  }

  async function lwDeleteSingleWizard(id, row, btn) {
    if (!row) return;
    await withButtonGuard(btn, async () => {
      let productName = `#${id}`;
      try {
        const detail = await apiFetch(`/listing-wizards/${id}`);
        if (detail.draft && detail.draft.product_name) {
          productName = detail.draft.product_name;
        }
      } catch (e) {
        // 상세 조회 실패해도 삭제 확인 자체는 ID로 계속 진행한다.
      }
      const message = HomezI18n.t("lw.delete_confirm_message", {
        name: productName,
        step: HomezI18n.t(`lw.step.${row.current_step.toLowerCase()}`),
        status: lwStatusLabel(row.status),
        note: HomezI18n.t("lw.delete_confirm_note"),
      });
      if (!window.confirm(message)) return;

      try {
        await apiFetch(`/listing-wizards/${id}`, {
          method: "DELETE",
          body: JSON.stringify({
            expected_version: row.version,
            reason: HomezI18n.t("lw.delete_default_reason"),
            deletion_request_id: lwGenerateRequestId(`del-${id}`),
          }),
        });
        toast(HomezI18n.t("lw.delete_success"), "success");
        lwListState.selectedIds.delete(id);
        await lwRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("lw.delete_error"), "error");
      }
    });
  }

  async function lwRestoreWizard(id) {
    const row = lwListState.rows.find((r) => r.id === id);
    if (!row) return;
    try {
      await apiFetch(`/listing-wizards/${id}/restore`, {
        method: "POST",
        body: JSON.stringify({ expected_version: row.version }),
      });
      toast(HomezI18n.t("lw.restore_success"), "success");
      await lwRenderList();
    } catch (err) {
      toast(err.message || HomezI18n.t("lw.restore_error"), "error");
    }
  }

  async function lwBulkDeleteSelected() {
    const btn = el("lw-bulk-delete-selected-btn");
    await withButtonGuard(btn, async () => {
      const ids = Array.from(lwListState.selectedIds);
      if (!ids.length) return;
      const deletableCount = lwListState.rows.filter(
        (r) => ids.includes(r.id) && r.deletable,
      ).length;
      const excludedCount = ids.length - deletableCount;
      const message = HomezI18n.t("lw.bulk_delete_selected_confirm_message", {
        total: ids.length, deletable: deletableCount, excluded: excludedCount,
        note: HomezI18n.t("lw.delete_confirm_note"),
      });
      if (!window.confirm(message)) return;

      try {
        const response = await apiFetch("/listing-wizards/bulk-archive", {
          method: "POST",
          body: JSON.stringify({
            wizard_ids: ids,
            reason: HomezI18n.t("lw.delete_default_reason"),
            deletion_request_id: lwGenerateRequestId("bulk"),
          }),
        });
        lwToastBulkResult(response);
        lwListState.selectedIds.clear();
        await lwRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("lw.delete_error"), "error");
      }
    });
  }

  async function lwBulkApplyMargin() {
    const btn = el("lw-bulk-margin-apply-btn");
    await withButtonGuard(btn, async () => {
      const ids = Array.from(lwListState.selectedIds);
      if (!ids.length) return;
      const rateInput = el("lw-bulk-margin-rate-input");
      const ratePercent = Number(rateInput.value);
      if (!rateInput.value || Number.isNaN(ratePercent) || ratePercent < 0 || ratePercent > 100) {
        toast(HomezI18n.t("lw.bulk_margin_rate_required"), "error");
        return;
      }
      const message = HomezI18n.t("lw.bulk_margin_apply_confirm_message", {
        count: ids.length, rate: ratePercent,
      });
      if (!window.confirm(message)) return;

      try {
        const response = await apiFetch("/listing-wizards/bulk-margin-apply", {
          method: "POST",
          body: JSON.stringify({
            wizard_ids: ids,
            target_margin_rate: ratePercent / 100,
          }),
        });
        lwToastBulkResult(response, "lw.bulk_margin_apply_result");
        await lwRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("lw.bulk_margin_apply_error"), "error");
      }
    });
  }

  async function lwBulkSubmitSelected() {
    const btn = el("lw-bulk-submit-selected-btn");
    await withButtonGuard(btn, async () => {
      const ids = Array.from(lwListState.selectedIds);
      if (!ids.length) return;
      const message = HomezI18n.t("lw.bulk_submit_confirm_message", {
        count: ids.length,
      });
      if (!window.confirm(message)) return;

      try {
        const response = await apiFetch("/listing-wizards/bulk-submit", {
          method: "POST",
          body: JSON.stringify({ wizard_ids: ids }),
        });
        lwToastBulkResult(response, "lw.bulk_submit_result");
        lwListState.selectedIds.clear();
        await lwRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("lw.bulk_submit_error"), "error");
      }
    });
  }

  async function lwDeleteAllMatchingFilter() {
    const btn = el("lw-bulk-delete-all-btn");
    await withButtonGuard(btn, async () => {
      const total = lwListState.deletableTotal || 0;
      if (total === 0) return;

      const confirmMessage = HomezI18n.t("lw.bulk_delete_all_confirm_message", {
        count: total, note: HomezI18n.t("lw.delete_confirm_note"),
      });
      if (!window.confirm(confirmMessage)) {
        return;
      }
      const typed = window.prompt(HomezI18n.t("lw.bulk_delete_all_type_confirm_prompt"));
      if (typed === null) return;
      if (typed !== "삭제") {
        toast(HomezI18n.t("lw.bulk_delete_all_confirm_mismatch"), "error");
        return;
      }
      const recentAuthToken = await promptRecentAuthToken();
      if (recentAuthToken === null) return;

      try {
        const response = await apiFetch("/listing-wizards/bulk-archive", {
          method: "POST",
          headers: { "X-Recent-Auth-Token": recentAuthToken },
          body: JSON.stringify({
            select_all_matching_filter: true,
            confirm_text: "삭제",
            reason: HomezI18n.t("lw.delete_default_reason"),
            deletion_request_id: lwGenerateRequestId("bulk-all"),
          }),
        });
        lwToastBulkResult(response);
        lwListState.selectedIds.clear();
        await lwRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("lw.delete_error"), "error");
      }
    });
  }

  // Gate X-3(2026-08-12) — 백엔드 `/listing-wizards/export.csv`는 Gate
  // U-3에서 이미 구현·테스트됐으나 이 버튼이 없어 실사용자가 도달할
  // 수 없는 죽은 경로였다(Gate X-1에서 발견). status-sync CSV
  // 내보내기(lsDownloadCsv)와 동일한 패턴을 그대로 재사용한다 —
  // LISTING_WIZARD_EXPORT Permission이 없으면 서버가 403을 반환하고
  // 여기서는 그 오류를 그대로 toast로 보여준다(클라이언트가 임의로
  // 버튼을 가리지 않는다 — 서버 판정이 유일한 근거).
  async function lwDownloadCsv() {
    const btn = el("lw-csv-btn");
    if (btn.disabled) return;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = HomezI18n.t("lw.csv_downloading");

    try {
      const locale = HomezI18n.getLocale ? HomezI18n.getLocale() : "ko-KR";
      const csvText = await apiFetch(`/listing-wizards/export.csv?locale=${encodeURIComponent(locale)}`);
      const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "listing-wizards.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(err.message || HomezI18n.t("lw.csv_error"), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  async function lwCreateNew() {
    try {
      const wizard = await apiFetch("/listing-wizards", {
        method: "POST",
        body: JSON.stringify({
          source_type: "MANUAL",
          creation_idempotency_key: `console-lw-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        }),
      });
      await lwOpenWizard(wizard.id);
    } catch (err) {
      toast(err.message || HomezI18n.t("lw.create_error"), "error");
    }
  }

  async function lwOpenWizard(id) {
    try {
      const wizard = await apiFetch(`/listing-wizards/${id}`);
      const hadPriorAutosave = !!wizard.autosave_saved_at;
      lwState.wizard = wizard;
      lwState.viewingStep = wizard.current_step;
      el("lw-list-panel").hidden = true;
      el("lw-wizard-panel").hidden = false;
      await lwRenderStep();
      // Gate J — 이전에 자동 저장된 적이 있으면(새로고침·브라우저
      // 재시작·다른 탭에서 이어옴 등) 어디까지 복구됐는지 한 번만
      // 알려준다. 매 단계 렌더마다 반복 표시하지 않는다(open 시점에
      // 딱 한 번).
      if (hadPriorAutosave) {
        toast(HomezI18n.t("lw.recovery_banner", {
          time: HomezI18n.formatDate(lsParseUtc(wizard.autosave_saved_at)),
        }), "info");
      }
    } catch (err) {
      toast(err.message || HomezI18n.t("lw.load_error"), "error");
    }
  }

  function lwCloseWizard() {
    lwClearAutosaveTimer();
    lwState.wizard = null;
    el("lw-wizard-panel").hidden = true;
    el("lw-list-panel").hidden = false;
    lwRenderList();
  }

  function lwUpdateStepIndicator() {
    const idx = Math.max(0, LW_STEPS.indexOf(lwState.viewingStep));
    el("lw-step-indicator").textContent = HomezI18n.t("lw.step_indicator", {
      current: idx + 1, total: LW_STEPS.length,
    });
    el("lw-progress-fill").style.width = `${((idx + 1) / LW_STEPS.length) * 100}%`;
    el("lw-status-pill").textContent = lwStatusLabel(lwState.wizard.status);
    el("lw-prev-btn").disabled = idx === 0;
    el("lw-next-btn").hidden = idx === LW_STEPS.length - 1;
  }

  async function lwRenderStep(opts = {}) {
    // Gate J — 단계를 벗어날 때마다(이전/다음/점프 전부) 그 단계에
    // 걸려 있던 자동 저장 타이머를 취소한다. 타이머를 그대로 두면
    // setTimeout 콜백이 이미 사라진 DOM(옛 단계의 input)을 참조한 채
    // 나중에 실행돼, 엉뚱한 시점에 저장을 시도하거나 존재하지 않는
    // 요소를 읽으려다 실패할 수 있다.
    lwClearAutosaveTimer();
    // Gate R-1(2026-08-09) — 실제 단계 전환에서는 화면 전용 캐시(사전
    // 검사 결과·승인/취소 미리보기)를 비운다. 언어 전환으로 "같은
    // 단계"를 다시 그릴 때만(opts.preserveEphemeral) 유지한다.
    if (!opts.preserveEphemeral) lwStepEphemeralCache = null;
    lwUpdateStepIndicator();
    const content = el("lw-step-content");
    el("lw-save-status").textContent = "";

    switch (lwState.viewingStep) {
      case "SOURCE": return lwRenderSourceStep(content);
      case "DRAFT": return lwRenderDraftStep(content);
      case "MEDIA": return lwRenderMediaStep(content);
      case "CHANNELS": return lwRenderChannelsStep(content);
      case "FULFILLMENT": return lwRenderFulfillmentStep(content);
      case "ECONOMICS": return lwRenderEconomicsStep(content);
      case "PRECHECK": return lwRenderPrecheckStep(content);
      case "APPROVAL": return lwRenderApprovalStep(content);
      case "EXECUTION": return lwRenderExecutionStep(content);
      case "RESULTS": return lwRenderResultsStep(content);
      default: content.innerHTML = "";
    }
  }

  // Gate R-1(2026-08-09) — 언어 전환 시 현재 열린 단계를 즉시 다시
  // 그린다. `#lw-step-content` 안의 모든 input/textarea/select 값을
  // 다시 그리기 전에 스냅샷으로 저장해 두고, 다시 그린 뒤 같은
  // 위치(가능하면 id, 없으면 순번)에 그대로 되돌려 놓는다 — 서버
  // 값(lwState.wizard)은 건드리지 않으므로 아직 저장되지 않은 입력도
  // 잃지 않는다. 값은 `.value`/`.checked`를 직접 대입하고 input
  // 이벤트를 일부러 발생시키지 않는다 — 그래야 자동 저장 debounce가
  // 불필요하게 다시 걸리지 않는다(요구사항 5).
  function lwSnapshotStepInputValues() {
    const container = el("lw-step-content");
    if (!container) return [];
    return Array.from(container.querySelectorAll("input, textarea, select")).map((elm, idx) => ({
      key: elm.id || `__lw_idx_${idx}`,
      type: elm.type,
      value: elm.value,
      checked: elm.type === "checkbox" || elm.type === "radio" ? elm.checked : undefined,
    }));
  }

  function lwRestoreStepInputValues(snapshot) {
    const container = el("lw-step-content");
    if (!container || !snapshot.length) return;
    const byKey = new Map(snapshot.map((s) => [s.key, s]));
    Array.from(container.querySelectorAll("input, textarea, select")).forEach((elm, idx) => {
      const key = elm.id || `__lw_idx_${idx}`;
      const saved = byKey.get(key);
      if (!saved) return;
      if (elm.type === "checkbox" || elm.type === "radio") {
        elm.checked = saved.checked;
      } else {
        elm.value = saved.value;
      }
    });
  }

  async function lwHandleLocaleChange() {
    if (currentView !== "listing-wizard") return;
    if (!lwState.wizard) return;
    const snapshot = lwSnapshotStepInputValues();
    await lwRenderStep({ preserveEphemeral: true });
    lwRestoreStepInputValues(snapshot);
  }

  function lwPrevStep() {
    const idx = LW_STEPS.indexOf(lwState.viewingStep);
    if (idx > 0) {
      lwState.viewingStep = LW_STEPS[idx - 1];
      lwRenderStep();
    }
  }

  function lwJumpToStep(stepName) {
    lwState.viewingStep = stepName;
    lwRenderStep();
  }

  // "다음" 버튼은 현재 보고 있는 단계 하나만 저장한다 — 각 단계
  // 렌더 함수가 lwState.wizard.__saveCurrentStep에 그 단계 전용 저장
  // 함수를 등록해 두면, 공용 다음 버튼 핸들러가 그것만 호출한다.
  let lwSaveCurrentStep = null;

  // 2026-08-12 Gate X-3 — "다음" 버튼이 저장 요청 진행 중에도 눌려
  // 있어 연타하면 같은 단계에 대해 PATCH가 중복으로 나갈 수 있었다
  // (Gate J의 낙관적 동시성이 데이터 손상은 막아주지만, 사용자 본인의
  // 더블클릭이 마치 다른 탭의 충돌처럼 혼란스러운 409 토스트를
  // 띄우는 부작용이 있었다). lsDownloadCsv/lwDownloadCsv와 동일한
  // "요청 중 버튼 비활성화" 패턴을 그대로 적용한다.
  let lwNextInFlight = false;

  async function lwHandleNext() {
    if (lwNextInFlight) return;
    lwNextInFlight = true;
    const nextBtn = el("lw-next-btn");
    const prevBtn = el("lw-prev-btn");
    const nextWasDisabled = nextBtn.disabled;
    const prevWasDisabled = prevBtn.disabled;
    nextBtn.disabled = true;
    prevBtn.disabled = true;
    try {
      if (typeof lwSaveCurrentStep === "function") {
        const ok = await lwSaveCurrentStep();
        if (!ok) return;
      } else {
        const idx = LW_STEPS.indexOf(lwState.viewingStep);
        if (idx < LW_STEPS.length - 1) {
          lwState.viewingStep = LW_STEPS[idx + 1];
          await lwRenderStep();
        }
      }
    } finally {
      lwNextInFlight = false;
      // lw-next-btn/lw-prev-btn은 console.html에 고정된 요소다(단계
      // 전환은 lw-step-content 내부만 다시 그린다) — 성공 시
      // lwRenderStep()이 idx===0/마지막 단계 여부로 다시 계산해
      // 덮어쓰므로, 여기서는 실패 경로(조기 return)를 위해 이전
      // 값으로 복구한다.
      nextBtn.disabled = nextWasDisabled;
      prevBtn.disabled = prevWasDisabled;
    }
  }

  function lwAdvanceAfterSave(freshWizard) {
    lwState.wizard = freshWizard;
    const idx = LW_STEPS.indexOf(lwState.viewingStep);
    lwState.viewingStep = LW_STEPS[Math.min(idx + 1, LW_STEPS.length - 1)];
    el("lw-save-status").textContent = HomezI18n.t("lw.save_saved");
    lwRenderStep();
  }

  // --------------------------------------------------
  // Gate J(2026-08-08) — 자동 저장·복구·중복 편집 방지.
  //
  // autosave 토큰은 탭마다 sessionStorage에 한 번만 만든다(localStorage가
  // 아니다 — 같은 위저드를 다른 탭에서 열면 서로 다른 토큰이어야
  // "누가 마지막으로 저장했는지"를 구분할 수 있다. localStorage는 탭
  // 간에 공유되므로 이 목적에 맞지 않는다). 이 토큰을 모든 PATCH/POST
  // approve/submit 요청에 실어 보내면, 서버는 저장이 성공할 때마다
  // autosave_client_token/autosave_saved_at을 갱신한다 — 다른 탭이
  // 그 사이 먼저 저장했다면 내 expected_version이 낡아 409(구조화된
  // WIZARD_VERSION_CONFLICT)를 받는다. 이 흐름 자체가 "중복 편집
  // 방지"다 — 별도 잠금 없이 낙관적 동시성만으로 충돌을 감지한다.
  // --------------------------------------------------

  function lwGetAutosaveToken() {
    const KEY = "homez_lw_autosave_token";
    let token = sessionStorage.getItem(KEY);
    if (!token) {
      token = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : `tab-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      sessionStorage.setItem(KEY, token);
    }
    return token;
  }

  function lwIsVersionConflict(err) {
    return !!(
      err && err.detail && typeof err.detail === "object"
      && err.detail.error_code === "WIZARD_VERSION_CONFLICT"
    );
  }

  function lwApplyConflictOrError(err, errEl) {
    if (lwIsVersionConflict(err)) {
      lwHandleConflict(err);
    } else if (errEl) {
      errEl.textContent = err.message || HomezI18n.t("lw.save_error");
    }
  }

  async function lwHandleConflict(err) {
    // 자동/수동 저장 중 다른 클라이언트(다른 탭·기기)가 이미 새
    // 버전을 저장한 경우 — 여기서 임의로 병합하지 않는다(잘못 병합
    //하면 조용한 데이터 유실 위험이 더 크다). 서버의 최신 상태를
    // 다시 불러와 사용자가 직접 확인한 뒤 이어서 입력하게 한다.
    lwClearAutosaveTimer();
    toast(HomezI18n.t("lw.conflict_reloaded_toast"), "error");
    if (lwState.wizard) {
      await lwOpenWizard(lwState.wizard.id);
    }
  }

  let lwAutosaveTimer = null;

  function lwClearAutosaveTimer() {
    if (lwAutosaveTimer) {
      clearTimeout(lwAutosaveTimer);
      lwAutosaveTimer = null;
    }
  }

  // 입력 이벤트가 멎고 1.5초 뒤에만 저장을 시도한다(매 키 입력마다
  // 서버를 두드리지 않는다) — 저장은 "다음"과 동일한 lwSaveCurrentStep
  // 을 재사용하되, 성공해도 다음 단계로 넘어가지 않는다(자동 저장은
  // 사용자가 아직 이 단계에 머무르는 동안 조용히 진행돼야 한다).
  function lwScheduleAutosave() {
    lwClearAutosaveTimer();
    lwAutosaveTimer = setTimeout(async () => {
      if (typeof lwSaveCurrentStep !== "function") return;
      const statusEl = el("lw-save-status");
      if (statusEl) statusEl.textContent = HomezI18n.t("lw.autosave_saving");
      await lwSaveCurrentStep({ silent: true });
    }, 1500);
  }

  function lwWireAutosaveInputs(content) {
    content.querySelectorAll("input, textarea, select").forEach((el2) => {
      // JSON editors can contain hundreds of characters. Browser automation,
      // IME composition, and accessibility typing may keep producing input
      // events longer than the debounce window; saving mid-edit can then
      // invalidate the element the user is still editing. Save these editors
      // only after the edit is committed (blur/change).
      const eventName = el2.matches("[data-lw-autosave-on-change]")
        ? "change"
        : "input";
      el2.addEventListener(eventName, lwScheduleAutosave);
    });
  }

  function lwFormatAutosaveBanner(wizard) {
    if (!wizard.autosave_saved_at) return "";
    const when = escapeHtml(HomezI18n.formatDate(lsParseUtc(wizard.autosave_saved_at)));
    return `<p class="lw-save-status" id="lw-recovery-banner">${HomezI18n.t("lw.recovery_banner", { time: when })}</p>`;
  }

  // 자동 저장 성공 — "다음" 클릭과 달리 다음 단계로 넘어가지 않는다
  // (사용자가 여전히 이 단계에서 입력 중일 수 있다). 서버가 갱신한
  // version/autosave_saved_at만 조용히 반영한다.
  function lwApplySilentSave(freshWizard) {
    lwState.wizard = freshWizard;
    const statusEl = el("lw-save-status");
    if (statusEl) {
      statusEl.textContent = HomezI18n.t("lw.autosave_saved", {
        time: HomezI18n.formatDate(lsParseUtc(freshWizard.autosave_saved_at)),
      });
    }
  }

  // ---- 1단계: 상품 소스 ----
  //
  // Gate TD(2026-08-19) — 사용자가 내부 ProductCandidate ID를 직접
  // 숫자로 입력하던 구조를 제거하고, 이 회사 관점에서 이미 승인
  // (company_status === APPROVED)된 후보만 검색·선택하는 UI로
  // 교체한다. 내부 id는 API 요청 바디에만 실리고 화면에는 참고용
  // 구분 번호로만 노출한다(직접 입력 요구 없음). 기존 PATCH
  // /listing-wizards/{id}/source 계약과 WizardSourceUpdateRequest는
  // 그대로 재사용 — 백엔드는 승인 여부를 강제하지 않지만(기존 계약),
  // 이 화면은 의도적으로 승인된 후보만 골라 선택 실수를 막는다.

  let lwStep1SelectedCandidate = null;

  async function lwFetchApprovedCandidates() {
    const rows = await apiFetch("/product-candidates?limit=200");
    return rows.filter((r) => r.company_status === "APPROVED");
  }

  // --------------------------------------------------
  // 승인된 후보 검색/선택 위젯(2026-08-19 V7 워크플로우 완성) — 마켓
  // 등록(ml-*)·AI 상품 등록(lp-*) 화면의 원시 ProductCandidate ID 수기
  // 입력을 대체한다. lw-* 소스 스텝의 lwRenderSourcePicker와 동일한
  // 데이터(lwFetchApprovedCandidates — 이 회사가 승인한 후보만, 서버
  // 쿼리 자체가 회사 스코프라 다른 회사 후보는 애초에 응답에 없다)를
  // 쓰되, DOM id를 파라미터로 받아 여러 화면에서 각자의 컨테이너에
  // 독립적으로 마운트할 수 있게 일반화했다. lw-* 자체는 이미 통과하는
  // 기존 구현을 그대로 둔다(불필요한 재구현 금지).
  // --------------------------------------------------

  async function mountApprovedCandidatePicker(containerEl, idPrefix, onSelect) {
    containerEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let approved;
    try {
      approved = await lwFetchApprovedCandidates();
    } catch (err) {
      renderErrorState(containerEl, err);
      return;
    }

    if (!approved.length) {
      renderEmptyState(
        containerEl,
        HomezI18n.t("lw.step1.empty_title"),
        "",
        `<button type="button" class="btn btn-primary" id="${idPrefix}-goto-create">${HomezI18n.t("lw.step1.empty_cta_create")}</button>
         <button type="button" class="btn" id="${idPrefix}-goto-review">${HomezI18n.t("lw.step1.empty_cta_review")}</button>`,
      );
      el(`${idPrefix}-goto-create`).addEventListener("click", () => {
        navigateTo("candidates");
        openCandidateCreateDialog();
      });
      el(`${idPrefix}-goto-review`).addEventListener("click", () => navigateTo("candidates"));
      return;
    }

    containerEl.innerHTML = `
      <label class="field">
        <span class="field-label">${HomezI18n.t("lw.step1.search_label")}</span>
        <input type="search" id="${idPrefix}-search" placeholder="${escapeHtml(HomezI18n.t("lw.step1.search_placeholder"))}">
      </label>
      <div id="${idPrefix}-list"></div>`;

    const renderList = () => {
      const q = el(`${idPrefix}-search`).value.trim().toLowerCase();
      const filtered = q ? approved.filter((c) => c.product_name.toLowerCase().includes(q)) : approved;
      const listEl = el(`${idPrefix}-list`);
      const colName = HomezI18n.t("lw.step1.col_name");
      const colStatus = HomezI18n.t("lw.step1.col_status");
      const colSource = HomezI18n.t("lw.step1.col_source");
      const colTaskNo = HomezI18n.t("lw.step1.col_task_no");
      listEl.innerHTML = `
        <table class="responsive-cards">
          <thead><tr><th>${colName}</th><th>${colStatus}</th><th>${colSource}</th><th>${colTaskNo}</th><th></th></tr></thead>
          <tbody>${filtered.map((c) => `
            <tr>
              <td data-label="${colName}">${escapeHtml(c.product_name)}</td>
              <td data-label="${colStatus}"><span class="status-tag status-${escapeHtml(c.company_status)}">${escapeHtml(c.company_status)}</span></td>
              <td data-label="${colSource}">${escapeHtml(c.source_type)}</td>
              <td data-label="${colTaskNo}">#${c.id}</td>
              <td><button type="button" class="btn btn-secondary" data-select-candidate="${c.id}">${HomezI18n.t("lw.step1.select_btn")}</button></td>
            </tr>`).join("")}
          </tbody>
        </table>`;
      listEl.querySelectorAll("[data-select-candidate]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const chosen = approved.find((c) => c.id === Number(btn.dataset.selectCandidate));
          onSelect(chosen);
        });
      });
    };
    el(`${idPrefix}-search`).addEventListener("input", renderList);
    renderList();
  }

  function renderApprovedCandidateSelectedSummary(containerEl, idPrefix, candidate, onChange) {
    containerEl.innerHTML = `
      <div class="lw-selected-candidate">
        <span class="field-label">${HomezI18n.t("lw.step1.selected_label")}</span>
        <div class="lw-selected-candidate-row">
          <strong>${escapeHtml(candidate.product_name)}</strong>
          <span class="status-tag status-${escapeHtml(candidate.company_status || candidate.status)}">${escapeHtml(candidate.company_status || candidate.status)}</span>
          <span class="stat-sub">${HomezI18n.t("lw.step1.col_task_no")} #${candidate.id}</span>
          <button type="button" class="btn btn-secondary" id="${idPrefix}-change-btn">${HomezI18n.t("lw.step1.change_btn")}</button>
        </div>
      </div>`;
    el(`${idPrefix}-change-btn`).addEventListener("click", onChange);
  }

  function lwRenderSourceStep(content) {
    const w = lwState.wizard;
    content.innerHTML = `
      <h2 data-i18n="lw.step.source">${HomezI18n.t("lw.step.source")}</h2>
      <div id="lw-source-body"><p class="loading-text">${HomezI18n.t("common.loading")}</p></div>
      <p class="field-error" id="lw-source-error"></p>`;

    lwStep1SelectedCandidate = null;

    lwSaveCurrentStep = async (opts = {}) => {
      const errEl = el("lw-source-error");
      errEl.textContent = "";
      const candidateId = lwStep1SelectedCandidate ? lwStep1SelectedCandidate.id : w.product_candidate_id;
      if (!candidateId) {
        if (!opts.silent) errEl.textContent = HomezI18n.t("lw.step1.required");
        return false;
      }
      try {
        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/source`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_version: lwState.wizard.version,
            product_candidate_id: candidateId,
            autosave_client_token: lwGetAutosaveToken(),
          }),
        });
        if (opts.silent) lwApplySilentSave(fresh); else lwAdvanceAfterSave(fresh);
        return true;
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
        return false;
      }
    };

    lwLoadSourceStepBody(w);
  }

  function lwRenderSelectedCandidate(body, candidate, approvedList) {
    lwStep1SelectedCandidate = candidate;
    body.innerHTML = `
      <div class="lw-selected-candidate">
        <span class="field-label">${HomezI18n.t("lw.step1.selected_label")}</span>
        <div class="lw-selected-candidate-row">
          <strong>${escapeHtml(candidate.product_name)}</strong>
          <span class="status-tag status-${escapeHtml(candidate.company_status || candidate.status)}">${escapeHtml(candidate.company_status || candidate.status)}</span>
          <span class="stat-sub">${HomezI18n.t("lw.step1.col_task_no")} #${candidate.id}</span>
          <button type="button" class="btn btn-secondary" id="lw-source-change-btn">${HomezI18n.t("lw.step1.change_btn")}</button>
        </div>
      </div>`;
    el("lw-source-change-btn").addEventListener("click", () => lwRenderSourcePicker(body, approvedList));
  }

  function lwRenderSourcePicker(body, approved) {
    if (!approved.length) {
      body.innerHTML = `
        <div class="empty-state">
          <div class="empty-title">${escapeHtml(HomezI18n.t("lw.step1.empty_title"))}</div>
          <div class="empty-state-actions">
            <button type="button" class="btn btn-primary" id="lw-step1-goto-create">${HomezI18n.t("lw.step1.empty_cta_create")}</button>
            <button type="button" class="btn" id="lw-step1-goto-review">${HomezI18n.t("lw.step1.empty_cta_review")}</button>
          </div>
        </div>`;
      el("lw-step1-goto-create").addEventListener("click", () => {
        navigateTo("candidates");
        openCandidateCreateDialog();
      });
      el("lw-step1-goto-review").addEventListener("click", () => navigateTo("candidates"));
      return;
    }

    body.innerHTML = `
      <label class="field">
        <span class="field-label">${HomezI18n.t("lw.step1.search_label")}</span>
        <input type="search" id="lw-source-search" placeholder="${escapeHtml(HomezI18n.t("lw.step1.search_placeholder"))}">
      </label>
      <div id="lw-source-picker-list"></div>`;

    const renderList = () => {
      const q = el("lw-source-search").value.trim().toLowerCase();
      const filtered = q ? approved.filter((c) => c.product_name.toLowerCase().includes(q)) : approved;
      const listEl = el("lw-source-picker-list");
      const colName = HomezI18n.t("lw.step1.col_name");
      const colStatus = HomezI18n.t("lw.step1.col_status");
      const colSource = HomezI18n.t("lw.step1.col_source");
      const colTaskNo = HomezI18n.t("lw.step1.col_task_no");
      listEl.innerHTML = `
        <table class="responsive-cards">
          <thead><tr><th>${colName}</th><th>${colStatus}</th><th>${colSource}</th><th>${colTaskNo}</th><th></th></tr></thead>
          <tbody>${filtered.map((c) => `
            <tr>
              <td data-label="${colName}">${escapeHtml(c.product_name)}</td>
              <td data-label="${colStatus}"><span class="status-tag status-${escapeHtml(c.company_status)}">${escapeHtml(c.company_status)}</span></td>
              <td data-label="${colSource}">${escapeHtml(c.source_type)}</td>
              <td data-label="${colTaskNo}">#${c.id}</td>
              <td><button type="button" class="btn btn-secondary" data-select-candidate="${c.id}">${HomezI18n.t("lw.step1.select_btn")}</button></td>
            </tr>`).join("")}
          </tbody>
        </table>`;
      listEl.querySelectorAll("[data-select-candidate]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const chosen = approved.find((c) => c.id === Number(btn.dataset.selectCandidate));
          el("lw-source-error").textContent = "";
          lwRenderSelectedCandidate(body, chosen, approved);
        });
      });
    };
    el("lw-source-search").addEventListener("input", renderList);
    renderList();
  }

  async function lwLoadSourceStepBody(w) {
    const body = el("lw-source-body");
    let approved;
    try {
      approved = await lwFetchApprovedCandidates();
    } catch (err) {
      body.innerHTML = `<p class="field-error">${escapeHtml((err && err.message) || HomezI18n.t("lw.step1.load_error"))}</p>`;
      return;
    }

    let current = w.product_candidate_id ? approved.find((c) => c.id === w.product_candidate_id) : null;
    if (w.product_candidate_id && !current) {
      try {
        current = await apiFetch(`/product-candidates/${w.product_candidate_id}`);
      } catch (_err) {
        current = null;
      }
    }

    if (current) {
      lwRenderSelectedCandidate(body, current, approved);
      return;
    }

    lwRenderSourcePicker(body, approved);
  }

  // ---- 2단계: 상품 초안 ----

  function lwRenderDraftStep(content) {
    const draft = lwState.wizard.draft || {};
    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.draft")}</h2>
      ${lwFormatAutosaveBanner(lwState.wizard)}
      <div class="lw-field-grid">
        <label class="field">
          <span class="field-label">${HomezI18n.t("lw.draft_name_label")}</span>
          <input type="text" id="lw-draft-name" value="${escapeHtml(draft.product_name || "")}">
        </label>
        <label class="field">
          <span class="field-label">${HomezI18n.t("lw.draft_brand_label")}</span>
          <input type="text" id="lw-draft-brand" value="${escapeHtml(draft.brand || "")}">
        </label>
        <label class="field">
          <span class="field-label">${HomezI18n.t("lw.draft_category_label")}</span>
          <input type="text" id="lw-draft-category" value="${escapeHtml(draft.category || "")}">
        </label>
        <label class="field">
          <span class="field-label">${HomezI18n.t("lw.draft_keywords_label")}</span>
          <input type="text" id="lw-draft-keywords" value="${escapeHtml((draft.keywords || []).join(", "))}">
        </label>
        <label class="field">
          <span class="field-label">${HomezI18n.t("lw.draft_certifications_label")}</span>
          <input type="text" id="lw-draft-certifications" value="${escapeHtml((draft.certifications || []).join(", "))}">
        </label>
      </div>
      <label class="field">
        <span class="field-label">${HomezI18n.t("lw.draft_description_label")}</span>
        <textarea id="lw-draft-description" rows="3">${escapeHtml(draft.description || "")}</textarea>
      </label>
      <p class="field-error" id="lw-draft-error"></p>`;

    lwSaveCurrentStep = async (opts = {}) => {
      const errEl = el("lw-draft-error");
      errEl.textContent = "";
      const name = el("lw-draft-name").value.trim();
      if (!name) {
        if (!opts.silent) errEl.textContent = HomezI18n.t("lw.draft_name_required");
        return false;
      }
      const splitList = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
      try {
        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/draft`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_version: lwState.wizard.version,
            product_name: name,
            brand: el("lw-draft-brand").value.trim() || null,
            category: el("lw-draft-category").value.trim() || null,
            description: el("lw-draft-description").value.trim() || null,
            keywords: splitList(el("lw-draft-keywords").value),
            certifications: splitList(el("lw-draft-certifications").value),
            options: [],
            sku_list: [],
            autosave_client_token: lwGetAutosaveToken(),
          }),
        });
        if (opts.silent) lwApplySilentSave(fresh); else lwAdvanceAfterSave(fresh);
        return true;
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
        return false;
      }
    };

    lwWireAutosaveInputs(content);
  }

  // ---- 3단계: 이미지 ----

  async function lwRenderLongImageResultPanel(response) {
    const statusEl = el("lw-long-image-status");
    const resultEl = el("lw-long-image-result");
    if (!statusEl || !resultEl) return;

    statusEl.innerHTML = `<p class="banner banner-success">${HomezI18n.t("lw.media_long_image_success", { count: response.segments.length })}</p>`;

    const segmentThumbs = await Promise.all(response.segments.map(async (seg, idx) => {
      let thumbUrl = "";
      try {
        thumbUrl = await lwLoadThumbUrl(seg.id);
      } catch (_err) {
        thumbUrl = "";
      }
      return { seg, idx, thumbUrl };
    }));

    resultEl.innerHTML = `
      <p class="stat-sub">${HomezI18n.t("lw.media_long_image_result_title")}</p>
      <div class="lw-segment-grid">${segmentThumbs.map(({ seg, idx, thumbUrl }) => `
        <div class="lw-segment-card">
          <div class="lw-segment-thumb">${thumbUrl ? `<img src="${thumbUrl}" alt="">` : ""}</div>
          <div class="lw-segment-meta">
            <span>${escapeHtml(HomezI18n.t("lw.media_segment_order_label", { n: idx + 1 }))}</span>
            <span>${lwFormatBytes(seg.file_size_bytes)}</span>
            ${lwRightsBadge(seg.rights_status)}
          </div>
        </div>`).join("")}</div>`;
  }

  async function lwRenderMediaStep(content) {
    const w = lwState.wizard;
    lwRevokeMediaObjectUrls();
    content.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    if (!w.product_candidate_id) {
      content.innerHTML = `<p class="field-error">${HomezI18n.t("lw.media_needs_source")}</p>`;
      lwSaveCurrentStep = null;
      return;
    }

    let assets = [];
    try {
      assets = await apiFetch(`/media-assets/owners/PRODUCT_CANDIDATE/${w.product_candidate_id}/assets`);
    } catch (err) {
      content.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
      lwSaveCurrentStep = null;
      return;
    }

    const selected = new Set(w.selected_media_asset_ids || []);
    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.media")}</h2>
      <p class="stat-sub">${HomezI18n.t("lw.media_add_hint")}</p>

      <div class="lw-media-search-panel">
        <div class="banner banner-info">${HomezI18n.t("lw.media_search_not_connected")}</div>
        <div class="lw-media-toolbar">
          <input type="text" id="lw-media-search-input" placeholder="${HomezI18n.t("lw.media_search_placeholder")}">
          <button type="button" class="btn btn-secondary btn-sm" id="lw-media-search-btn">${HomezI18n.t("lw.media_search_btn")}</button>
        </div>
        <div id="lw-media-search-results"></div>
      </div>

      <div class="lw-media-toolbar">
        <label class="btn btn-secondary" for="lw-media-upload-input">${HomezI18n.t("lw.media_upload_btn")}</label>
        <input type="file" id="lw-media-upload-input" accept="image/png,image/jpeg,image/webp" multiple hidden>
        <button type="button" class="btn btn-secondary" id="lw-long-image-toggle-btn">${HomezI18n.t("lw.media_long_image_btn")}</button>
      </div>
      <p class="field-error" id="lw-media-upload-error"></p>

      <div class="lw-long-image-panel" id="lw-long-image-panel" hidden>
        <p class="stat-sub">${HomezI18n.t("lw.media_long_image_hint")}</p>
        <div class="lw-long-image-policy">
          <p class="lw-long-image-policy-title">${HomezI18n.t("lw.media_long_image_policy_title")}</p>
          <ul>
            <li>${HomezI18n.t("lw.media_long_image_policy_formats")}</li>
            <li>${HomezI18n.t("lw.media_long_image_policy_size")}</li>
            <li>${HomezI18n.t("lw.media_long_image_policy_height")}</li>
          </ul>
        </div>
        <label class="btn btn-secondary" for="lw-long-image-input" id="lw-long-image-select-label">${HomezI18n.t("lw.media_long_image_select_btn")}</label>
        <input type="file" id="lw-long-image-input" accept="image/png,image/jpeg,image/webp" hidden>
        <div id="lw-long-image-status"></div>
        <div id="lw-long-image-result"></div>
      </div>

      ${!assets.length ? `<p class="empty-text">${HomezI18n.t("lw.media_none_available")}</p>` : `
      <div class="lw-checklist lw-media-grid">${assets.map((a) => {
        const isVerified = a.rights_status === "VERIFIED";
        return `
        <div class="lw-media-card" data-asset-id="${a.id}">
          <div class="lw-media-thumb" id="lw-thumb-${a.id}"></div>
          <label class="lw-checklist-item">
            <input type="checkbox" value="${a.id}" ${selected.has(a.id) ? "checked" : ""} ${isVerified ? "" : "disabled"}>
            <span>#${a.id} · ${escapeHtml(a.purpose)} · ${escapeHtml(a.asset_role)}${a.asset_role === "GENERATED" && a.source_asset_id ? ` (from #${a.source_asset_id})` : ""}</span>
          </label>
          ${lwRightsBadge(a.rights_status)}
          ${isVerified ? "" : `<p class="lw-media-thumb-note">${HomezI18n.t("lw.rights_unverified_selection_blocked")}</p>`}
          <div class="lw-media-card-actions">
            ${a.asset_role === "ORIGINAL" ? `<button type="button" class="btn btn-ghost btn-sm lw-cutout-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.media_cutout_btn")}</button>` : ""}
            ${a.asset_role === "GENERATED" ? `<button type="button" class="btn btn-ghost btn-sm lw-compose-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.media_compose_btn")}</button>` : ""}
            <button type="button" class="btn btn-ghost btn-sm lw-manual-edit-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.media_manual_edit_btn")}</button>
            <button type="button" class="btn btn-ghost btn-sm lw-detail-page-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.media_detail_page_btn")}</button>
            ${isVerified
              ? `<button type="button" class="btn btn-ghost btn-sm lw-rights-revoke-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.rights_revoke_btn")}</button>`
              : `<button type="button" class="btn btn-ghost btn-sm lw-rights-confirm-btn" data-asset-id="${a.id}">${HomezI18n.t("lw.rights_confirm_btn")}</button>`}
          </div>
          <div class="lw-rights-action-panel" id="lw-rights-panel-${a.id}"></div>
          <div class="lw-rights-action-panel" id="lw-detail-page-panel-${a.id}"></div>
        </div>`;
      }).join("")}</div>`}
      <p class="field-error" id="lw-media-error"></p>`;

    assets.forEach((a) => {
      lwLoadThumbUrl(a.id).then((url) => {
        const thumbEl = el(`lw-thumb-${a.id}`);
        if (thumbEl) thumbEl.innerHTML = `<img src="${url}" alt="">`;
      }).catch(() => {});
    });

    const fileToBase64 = (file) => new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",").pop());
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });

    el("lw-media-upload-input").addEventListener("change", async (ev) => {
      const errEl = el("lw-media-upload-error");
      errEl.textContent = "";
      const files = Array.from(ev.target.files || []);
      for (const file of files) {
        try {
          const base64 = await fileToBase64(file);
          await apiFetch("/media-assets/upload", {
            method: "POST",
            body: JSON.stringify({
              owner_type: "PRODUCT_CANDIDATE",
              owner_id: w.product_candidate_id,
              purpose: "MAIN",
              display_order: 0,
              image_base64: base64,
              original_filename: file.name,
            }),
          });
        } catch (err) {
          errEl.textContent = escapeHtml(err.message || HomezI18n.t("common.load_error"));
        }
      }
      await lwRenderMediaStep(content);
    });

    // 2026-08-20 3차 지시 — "긴 상세이미지 추가"는 일반 업로드와
    // 완전히 별도의 버튼·입력·API 경로다(요구사항 1/2). 정책 안내
    // (요구사항 3)는 패널을 펼치면 파일을 고르기 전에 항상 먼저
    // 보인다. 진행 중에는 입력을 disabled로 잠그고 모듈 스코프
    // 플래그로 이중 제출을 막는다(요구사항 8) — 성공하면 전체
    // 단계가 다시 그려지며 방금 만든 원본+구간이 메인 이미지
    // 목록에도 즉시 반영된다.
    el("lw-long-image-toggle-btn").addEventListener("click", () => {
      const panel = el("lw-long-image-panel");
      panel.hidden = !panel.hidden;
    });

    el("lw-long-image-input").addEventListener("change", async (ev) => {
      if (lwLongImageUploadInFlight) return;
      const file = (ev.target.files || [])[0];
      if (!file) return;

      lwLongImageUploadInFlight = true;
      const input = ev.target;
      const selectLabel = el("lw-long-image-select-label");
      const statusEl = el("lw-long-image-status");
      input.disabled = true;
      if (selectLabel) selectLabel.classList.add("lw-btn-disabled");
      statusEl.innerHTML = `<p class="loading-text">${HomezI18n.t("lw.media_long_image_uploading")}</p>`;
      el("lw-long-image-result").innerHTML = "";

      let response = null;
      let errorMessage = "";
      try {
        const base64 = await fileToBase64(file);
        response = await apiFetch("/media-assets/upload-long-detail-image", {
          method: "POST",
          body: JSON.stringify({
            owner_type: "PRODUCT_CANDIDATE",
            owner_id: w.product_candidate_id,
            image_base64: base64,
            original_filename: file.name,
          }),
        });
      } catch (err) {
        errorMessage = err.message || HomezI18n.t("common.load_error");
      }
      lwLongImageUploadInFlight = false;

      if (response) {
        lwStepEphemeralCache = { step: "MEDIA", kind: "long_image_result", data: response };
        toast(HomezI18n.t("lw.media_long_image_success", { count: response.segments.length }), "success");
        await lwRenderMediaStep(content);
        return;
      }

      statusEl.innerHTML = `<p class="field-error">${escapeHtml(errorMessage)}</p>`;
      input.value = "";
      input.disabled = false;
      if (selectLabel) selectLabel.classList.remove("lw-btn-disabled");
    });

    if (lwStepEphemeralCache && lwStepEphemeralCache.step === "MEDIA"
      && lwStepEphemeralCache.kind === "long_image_result") {
      el("lw-long-image-panel").hidden = false;
      lwRenderLongImageResultPanel(lwStepEphemeralCache.data);
    }

    el("lw-media-search-btn").addEventListener("click", () => withButtonGuard(
      el("lw-media-search-btn"),
      async () => {
        const resultsEl = el("lw-media-search-results");
        const query = el("lw-media-search-input").value.trim();
        if (!query) return;
        resultsEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
        try {
          const results = await apiFetch("/media-assets/search", {
            method: "POST",
            body: JSON.stringify({ product_name: query, provider_code: "NAVER" }),
          });
          if (!results.length) {
            resultsEl.innerHTML = `<p class="empty-text">${HomezI18n.t("lw.media_search_empty")}</p>`;
            return;
          }
          resultsEl.innerHTML = `<div class="lw-media-grid">${results.map((r) => `
            <div class="lw-media-card">
              <div class="lw-media-thumb"><img src="${escapeHtml(r.preview_image_url)}" alt="" loading="lazy"></div>
              <div>${escapeHtml(r.title)}</div>
              <div class="stat-sub">${escapeHtml(r.source_site)} · ${escapeHtml(r.permission_status)}</div>
              <div class="field-error">${r.selectable ? "" : HomezI18n.t("lw.media_search_preview_only")}</div>
            </div>`).join("")}</div>`;
        } catch (err) {
          resultsEl.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
        }
      },
    ));

    content.querySelectorAll(".lw-cutout-btn").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const errEl = el("lw-media-error");
        errEl.textContent = "";
        try {
          await apiFetch("/media-assets/background-removal", {
            method: "POST",
            body: JSON.stringify({
              source_asset_id: Number(btn.dataset.assetId),
              provider_code: "REMBG",
            }),
          });
          await lwRenderMediaStep(content);
        } catch (err) {
          errEl.textContent = escapeHtml(err.message || HomezI18n.t("common.load_error"));
        }
      }));
    });

    content.querySelectorAll(".lw-compose-btn").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const errEl = el("lw-media-error");
        errEl.textContent = "";
        try {
          await apiFetch("/media-assets/background-composition", {
            method: "POST",
            body: JSON.stringify({
              cutout_asset_id: Number(btn.dataset.assetId),
              background_kind: "WHITE",
            }),
          });
          await lwRenderMediaStep(content);
        } catch (err) {
          errEl.textContent = escapeHtml(err.message || HomezI18n.t("common.load_error"));
        }
      }));
    });

    content.querySelectorAll(".lw-manual-edit-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        lwOpenManualEditor(Number(btn.dataset.assetId), () => lwRenderMediaStep(content));
      });
    });

    // 2026-08-27 추가 — 상세이미지 자동 생성. 이 사진(hero) 그대로에
    // 브랜드명·특징·스펙·사용법 텍스트만 배치한다(사진 자체를 AI로
    // 다시 만들지 않는다 — app/domains/media_asset/
    // detail_page_generator.py 원칙과 동일). rights-confirm 패널과
    // 동일한 토글 방식: 열려 있으면 닫고, 아니면 폼을 그린다.
    content.querySelectorAll(".lw-detail-page-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const assetId = btn.dataset.assetId;
        const panel = el(`lw-detail-page-panel-${assetId}`);
        if (panel.dataset.open === "1") {
          panel.innerHTML = "";
          panel.dataset.open = "";
          return;
        }
        panel.dataset.open = "1";
        panel.innerHTML = `
          <p class="stat-sub">${HomezI18n.t("lw.detail_page_panel_title")}</p>
          <p class="stat-sub">${HomezI18n.t("lw.detail_page_hint")}</p>
          <label class="field-label">${HomezI18n.t("lw.detail_page_brand_label")}
            <input type="text" class="lw-dp-brand" maxlength="60">
          </label>
          <label class="field-label">${HomezI18n.t("lw.detail_page_tagline_label")}
            <input type="text" class="lw-dp-tagline" maxlength="100">
          </label>
          <p class="stat-sub">${HomezI18n.t("lw.detail_page_feature_section_title")}</p>
          <div class="lw-dp-features">
            ${[0, 1, 2].map((i) => `
            <div class="lw-dp-feature-row">
              <input type="text" class="lw-dp-feature-title" data-i="${i}" maxlength="60" placeholder="${HomezI18n.t("lw.detail_page_feature_title_placeholder")}">
              <input type="text" class="lw-dp-feature-desc" data-i="${i}" maxlength="200" placeholder="${HomezI18n.t("lw.detail_page_feature_desc_placeholder")}">
            </div>`).join("")}
          </div>
          <p class="stat-sub">${HomezI18n.t("lw.detail_page_spec_section_title")}</p>
          <div class="lw-dp-specs">
            ${[0, 1].map(() => `
            <div class="lw-dp-spec-row">
              <input type="text" class="lw-dp-spec-label" maxlength="30" placeholder="${HomezI18n.t("lw.detail_page_spec_label_placeholder")}">
              <input type="text" class="lw-dp-spec-value" maxlength="200" placeholder="${HomezI18n.t("lw.detail_page_spec_value_placeholder")}">
            </div>`).join("")}
          </div>
          <button type="button" class="btn btn-ghost btn-sm lw-dp-add-spec-btn">${HomezI18n.t("lw.detail_page_add_spec_btn")}</button>
          <label class="field-label">${HomezI18n.t("lw.detail_page_usage_label")}
            <textarea class="lw-dp-usage" maxlength="1000" rows="3"></textarea>
          </label>
          <button type="button" class="btn btn-primary btn-sm lw-dp-submit-btn">${HomezI18n.t("lw.detail_page_submit_btn")}</button>
          <button type="button" class="btn btn-ghost btn-sm lw-dp-close-btn">${HomezI18n.t("lw.detail_page_close_btn")}</button>
          <p class="field-error" id="lw-detail-page-error-${assetId}"></p>`;

        panel.querySelector(".lw-dp-close-btn").addEventListener("click", () => {
          panel.innerHTML = "";
          panel.dataset.open = "";
        });

        panel.querySelector(".lw-dp-add-spec-btn").addEventListener("click", () => {
          const row = document.createElement("div");
          row.className = "lw-dp-spec-row";
          row.innerHTML = `
            <input type="text" class="lw-dp-spec-label" maxlength="30" placeholder="${HomezI18n.t("lw.detail_page_spec_label_placeholder")}">
            <input type="text" class="lw-dp-spec-value" maxlength="200" placeholder="${HomezI18n.t("lw.detail_page_spec_value_placeholder")}">`;
          panel.querySelector(".lw-dp-specs").appendChild(row);
        });

        panel.querySelector(".lw-dp-submit-btn").addEventListener("click", () => withButtonGuard(
          panel.querySelector(".lw-dp-submit-btn"),
          async () => {
            const errEl = el(`lw-detail-page-error-${assetId}`);
            errEl.textContent = "";

            const brandName = panel.querySelector(".lw-dp-brand").value.trim();
            if (!brandName) {
              errEl.textContent = HomezI18n.t("lw.detail_page_brand_required");
              return;
            }
            const tagline = panel.querySelector(".lw-dp-tagline").value.trim();

            const featureHighlights = [];
            panel.querySelectorAll(".lw-dp-feature-row").forEach((row) => {
              const title = row.querySelector(".lw-dp-feature-title").value.trim();
              const description = row.querySelector(".lw-dp-feature-desc").value.trim();
              if (title && description) featureHighlights.push({ title, description });
            });

            const specRows = [];
            panel.querySelectorAll(".lw-dp-spec-row").forEach((row) => {
              const label = row.querySelector(".lw-dp-spec-label").value.trim();
              const value = row.querySelector(".lw-dp-spec-value").value.trim();
              if (label && value) specRows.push({ label, value });
            });

            const usageText = panel.querySelector(".lw-dp-usage").value.trim();

            try {
              await apiFetch("/media-assets/generate-detail-page", {
                method: "POST",
                body: JSON.stringify({
                  hero_asset_id: Number(assetId),
                  brand_name: brandName,
                  tagline: tagline || null,
                  feature_highlights: featureHighlights,
                  spec_rows: specRows,
                  usage_text: usageText || null,
                }),
              });
              toast(HomezI18n.t("lw.detail_page_success"), "success");
              await lwRenderMediaStep(content);
            } catch (err) {
              errEl.textContent = escapeHtml(err.message || HomezI18n.t("common.load_error"));
            }
          },
        ));
      });
    });

    // 2026-08-20 3차 지시 — 권리정책 UI 실연결. 확인 근거(basis)는
    // 반드시 3가지 중 하나를 명시적으로 골라야 하고(요구사항 2),
    // 취소는 사유를 필수로 받는다. 두 경우 모두 성공 시 전체 단계를
    // 다시 그려 배지/체크박스 disabled 상태를 즉시 반영한다.
    content.querySelectorAll(".lw-rights-confirm-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const assetId = btn.dataset.assetId;
        const panel = el(`lw-rights-panel-${assetId}`);
        if (panel.dataset.open === "1") {
          panel.innerHTML = "";
          panel.dataset.open = "";
          return;
        }
        panel.dataset.open = "1";
        panel.innerHTML = `
          <p class="stat-sub">${HomezI18n.t("lw.rights_confirm_title")}</p>
          <label class="lw-checklist-item"><input type="radio" name="lw-rights-basis-${assetId}" value="SELF_CAPTURED"> <span>${HomezI18n.t("lw.rights_confirm_basis_self_captured")}</span></label>
          <label class="lw-checklist-item"><input type="radio" name="lw-rights-basis-${assetId}" value="SUPPLIER_BRAND_PERMISSION"> <span>${HomezI18n.t("lw.rights_confirm_basis_supplier_brand_permission")}</span></label>
          <label class="lw-checklist-item"><input type="radio" name="lw-rights-basis-${assetId}" value="COMMERCIAL_LICENSE"> <span>${HomezI18n.t("lw.rights_confirm_basis_commercial_license")}</span></label>
          <button type="button" class="btn btn-primary btn-sm lw-rights-confirm-submit-btn">${HomezI18n.t("lw.rights_confirm_submit_btn")}</button>
          <p class="field-error" id="lw-rights-confirm-error-${assetId}"></p>`;

        panel.querySelector(".lw-rights-confirm-submit-btn").addEventListener("click", () => withButtonGuard(
          panel.querySelector(".lw-rights-confirm-submit-btn"),
          async () => {
            const errEl = el(`lw-rights-confirm-error-${assetId}`);
            errEl.textContent = "";
            const checked = panel.querySelector(`input[name="lw-rights-basis-${assetId}"]:checked`);
            if (!checked) {
              errEl.textContent = HomezI18n.t("lw.rights_confirm_title");
              return;
            }
            try {
              await apiFetch(`/media-assets/${assetId}/confirm-rights-verified`, {
                method: "POST",
                body: JSON.stringify({ basis: checked.value }),
              });
              await lwRenderMediaStep(content);
            } catch (err) {
              errEl.textContent = escapeHtml(err.message || HomezI18n.t("common.load_error"));
            }
          },
        ));
      });
    });

    content.querySelectorAll(".lw-rights-revoke-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const assetId = btn.dataset.assetId;
        const panel = el(`lw-rights-panel-${assetId}`);
        if (panel.dataset.open === "1") {
          panel.innerHTML = "";
          panel.dataset.open = "";
          return;
        }
        panel.dataset.open = "1";
        panel.innerHTML = `
          <input type="text" id="lw-rights-revoke-reason-${assetId}" placeholder="${HomezI18n.t("lw.rights_revoke_reason_placeholder")}">
          <button type="button" class="btn btn-secondary btn-sm lw-rights-revoke-submit-btn">${HomezI18n.t("lw.rights_revoke_submit_btn")}</button>
          <p class="field-error" id="lw-rights-revoke-error-${assetId}"></p>`;

        panel.querySelector(".lw-rights-revoke-submit-btn").addEventListener("click", () => withButtonGuard(
          panel.querySelector(".lw-rights-revoke-submit-btn"),
          async () => {
            const errEl = el(`lw-rights-revoke-error-${assetId}`);
            errEl.textContent = "";
            const reason = el(`lw-rights-revoke-reason-${assetId}`).value.trim();
            if (!reason) {
              errEl.textContent = HomezI18n.t("lw.rights_revoke_reason_placeholder");
              return;
            }
            try {
              await apiFetch(`/media-assets/${assetId}/revoke-rights-verification`, {
                method: "POST",
                body: JSON.stringify({ reason }),
              });
              await lwRenderMediaStep(content);
            } catch (err) {
              errEl.textContent = escapeHtml(err.message || HomezI18n.t("common.load_error"));
            }
          },
        ));
      });
    });

    lwSaveCurrentStep = async (opts = {}) => {
      const errEl = el("lw-media-error");
      errEl.textContent = "";
      const ids = Array.from(content.querySelectorAll('input[type="checkbox"]:checked'))
        .map((c) => Number(c.value));
      if (!ids.length) {
        if (!opts.silent) errEl.textContent = HomezI18n.t("lw.media_selection_required");
        return false;
      }
      try {
        const fresh = await apiFetch(`/listing-wizards/${w.id}/media`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_version: lwState.wizard.version,
            selected_media_asset_ids: ids,
            autosave_client_token: lwGetAutosaveToken(),
          }),
        });
        if (opts.silent) lwApplySilentSave(fresh); else lwAdvanceAfterSave(fresh);
        return true;
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
        return false;
      }
    };
  }

  // ---- 4단계: 판매채널 ----

  async function lwRenderChannelsStep(content) {
    content.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let channels = [];
    try {
      channels = await apiFetch("/marketplace-listings/channels");
    } catch (err) {
      content.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
      lwSaveCurrentStep = null;
      return;
    }

    const accountLists = await Promise.all(
      channels.map((ch) => apiFetch(`/marketplace-listings/channels/${ch.id}/accounts`).catch(() => [])),
    );
    const allAccounts = [];
    channels.forEach((ch, i) => {
      (accountLists[i] || []).forEach((acc) => allAccounts.push({ ...acc, channelCode: ch.code, channelName: ch.name }));
    });

    const selectedIds = new Set(
      (lwState.wizard.channel_selections || []).map((s) => s.marketplace_account_id),
    );

    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.channels")}</h2>
      ${!allAccounts.length ? `<p class="empty-text">${HomezI18n.t("lw.channels_none_available")}</p>` : `
      <div class="lw-checklist">${allAccounts.map((acc) => `
        <label class="lw-checklist-item">
          <input type="checkbox" value="${acc.id}" ${selectedIds.has(acc.id) ? "checked" : ""} ${acc.is_active ? "" : "disabled"}>
          <span>${escapeHtml(acc.channelName)} (${escapeHtml(acc.channelCode)}) · ${escapeHtml(acc.account_name)}${acc.is_active ? "" : ` — ${HomezI18n.t("lw.account_inactive")}`}</span>
        </label>`).join("")}</div>`}
      <p class="field-error" id="lw-channels-error"></p>`;

    lwSaveCurrentStep = async (opts = {}) => {
      const errEl = el("lw-channels-error");
      errEl.textContent = "";
      const ids = Array.from(content.querySelectorAll('input[type="checkbox"]:checked'))
        .map((c) => Number(c.value));
      if (!ids.length) {
        if (!opts.silent) errEl.textContent = HomezI18n.t("lw.channels_selection_required");
        return false;
      }
      try {
        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/channels`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_version: lwState.wizard.version,
            marketplace_account_ids: ids,
            autosave_client_token: lwGetAutosaveToken(),
          }),
        });
        if (opts.silent) lwApplySilentSave(fresh); else lwAdvanceAfterSave(fresh);
        return true;
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
        return false;
      }
    };
  }

  // ---- 5단계: 판매 방식 ----

  function lwPublicRequiredFields(fields) {
    const publicFields = { ...(fields || {}) };
    [
      "outboundShippingPlaceCode", "returnCenterCode", "returnChargeName",
      "companyContactNumber", "returnZipCode", "returnAddress",
      "returnAddressDetail",
    ].forEach((key) => delete publicFields[key]);
    return publicFields;
  }

  // 2026-08-30 V7 후속 안정화 Phase 3 — 브랜드 3상태(NO_BRAND/
  // OFFICIAL_BRAND/UNRESOLVED) 입력 UI. 백엔드 계약(coupang_
  // submission_contract.py)은 이미 세션에서 완성돼 있다 — 이 UI는
  // 그 계약이 요구하는 필드(brandState/brand/brandId/
  // officialBrandName/brandEnrollmentStatus/brandLookupFingerprint)를
  // 화면에서 직접 채우게 한다. 현재 상태는 패널 자신의
  // data-lw-brand-state-json 속성에 보관하고, 저장 시점(아래
  // lwSaveCurrentStep 확장부)에 그대로 requiredFields에 병합한다 —
  // "무브랜드"/"[브랜드 없음]" 같은 문자열을 이 파일 어디에서도
  // 자동으로 만들어 넣지 않는다(사용자가 직접 입력·확인한 값만).
  function lwBrandStateFromFields(fields) {
    return {
      brandState: fields.brandState || "UNRESOLVED",
      brand: fields.brand || "",
      brandId: fields.brandId || "",
      officialBrandName: fields.officialBrandName || "",
      brandEnrollmentStatus: fields.brandEnrollmentStatus || "",
      brandLookupFingerprint: fields.brandLookupFingerprint || "",
    };
  }

  function lwBrandEnrollmentLabel(status) {
    if (status === "ENROLLED") return HomezI18n.t("lw.brand_enrollment_enrolled");
    if (status === "NOT_ENROLLED") return HomezI18n.t("lw.brand_enrollment_not_enrolled");
    return HomezI18n.t("lw.brand_enrollment_unknown");
  }

  function lwRenderBrandStatePanel(fields) {
    const state = lwBrandStateFromFields(fields);
    const stateJson = escapeHtml(JSON.stringify(state));

    return `
      <div class="lw-brand-state-panel" data-lw-brand-state-json="${stateJson}">
        <span class="field-label">${HomezI18n.t("lw.brand_state_label")}</span>
        <div class="lw-brand-state-tabs">
          <button type="button" class="btn btn-sm ${state.brandState === "NO_BRAND" ? "btn-primary" : "btn-secondary"}" data-lw-brand-tab="NO_BRAND">${HomezI18n.t("lw.brand_state_no_brand_tab")}</button>
          <button type="button" class="btn btn-sm ${state.brandState === "OFFICIAL_BRAND" ? "btn-primary" : "btn-secondary"}" data-lw-brand-tab="OFFICIAL_BRAND">${HomezI18n.t("lw.brand_state_official_tab")}</button>
        </div>
        <div class="lw-brand-unresolved-notice" ${state.brandState === "UNRESOLVED" ? "" : "hidden"}>
          <p class="field-error">${HomezI18n.t("lw.brand_state_unresolved_notice")}</p>
        </div>
        <div class="lw-brand-no-brand-panel" ${state.brandState === "NO_BRAND" ? "" : "hidden"}>
          <label class="field">
            <input type="text" class="lw-brand-no-brand-input" placeholder="${HomezI18n.t("lw.brand_no_brand_input_placeholder")}" value="${state.brandState === "NO_BRAND" ? escapeHtml(state.brand) : ""}">
          </label>
          <button type="button" class="btn btn-secondary btn-sm" data-lw-brand-no-brand-confirm>${HomezI18n.t("lw.brand_no_brand_confirm_btn")}</button>
          ${state.brandState === "NO_BRAND" && state.brand ? `<p class="stat-sub">${HomezI18n.t("lw.brand_no_brand_confirmed_badge", { value: state.brand })}</p>` : ""}
        </div>
        <div class="lw-brand-official-panel" ${state.brandState === "OFFICIAL_BRAND" ? "" : "hidden"}>
          <div class="field-inline">
            <input type="text" class="lw-brand-search-input" placeholder="${HomezI18n.t("lw.brand_search_placeholder")}">
            <button type="button" class="btn btn-secondary btn-sm" data-lw-brand-search-btn>${HomezI18n.t("lw.brand_search_btn")}</button>
          </div>
          <div class="lw-brand-search-results"></div>
          ${state.brandState === "OFFICIAL_BRAND" && state.brandId ? `
          <div class="lw-brand-selected-info">
            <p class="stat-sub">${HomezI18n.t("lw.brand_selected_id_label")}: ${escapeHtml(state.brandId)}</p>
            <p class="stat-sub">${HomezI18n.t("lw.brand_selected_name_label")}: ${escapeHtml(state.officialBrandName)}</p>
            <p class="stat-sub">${HomezI18n.t("lw.brand_selected_enrollment_label")}: ${lwBrandEnrollmentLabel(state.brandEnrollmentStatus)}</p>
            ${state.brandEnrollmentStatus !== "ENROLLED" ? `<p class="field-error">${HomezI18n.t("lw.brand_enrollment_warning")}</p>` : ""}
          </div>` : ""}
        </div>
      </div>`;
  }

  function lwSetBrandState(panel, nextState) {
    let current;
    try {
      current = JSON.parse(panel.dataset.lwBrandStateJson || "{}");
    } catch (_) {
      current = {};
    }
    const merged = { ...lwBrandStateFromFields(current), ...nextState };
    panel.dataset.lwBrandStateJson = JSON.stringify(merged);
    panel.outerHTML = lwRenderBrandStatePanel(merged);
  }

  function lwWireBrandStatePanel(content) {
    content.querySelectorAll(".lw-brand-state-panel").forEach((panel) => {
      panel.querySelectorAll("[data-lw-brand-tab]").forEach((btn) => {
        btn.addEventListener("click", () => {
          lwSetBrandState(panel, { brandState: btn.dataset.lwBrandTab });
          lwWireBrandStatePanel(content); // 새로 그려진 패널에 다시 바인딩
          lwScheduleAutosave();
        });
      });

      const confirmBtn = panel.querySelector("[data-lw-brand-no-brand-confirm]");
      if (confirmBtn) {
        confirmBtn.addEventListener("click", () => {
          const value = panel.querySelector(".lw-brand-no-brand-input")?.value.trim() || "";
          if (!value) return;
          lwSetBrandState(panel, {
            brandState: "NO_BRAND", brand: value,
            brandId: "", officialBrandName: "", brandEnrollmentStatus: "",
            brandLookupFingerprint: "",
          });
          lwWireBrandStatePanel(content);
          lwScheduleAutosave();
        });
      }

      const searchBtn = panel.querySelector("[data-lw-brand-search-btn]");
      if (searchBtn) {
        searchBtn.addEventListener("click", async () => {
          const query = panel.querySelector(".lw-brand-search-input")?.value.trim() || "";
          const resultsHost = panel.querySelector(".lw-brand-search-results");
          if (!query || !resultsHost) return;
          resultsHost.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
          try {
            const resp = await apiFetch(
              `/listing-wizards/${lwState.wizard.id}/coupang/brand-search?query=${encodeURIComponent(query)}`,
            );
            const notice = resp.connected === false
              ? `<p class="stat-sub">${HomezI18n.t("lw.brand_search_demo_note")}</p>` : "";
            if (!resp.results || !resp.results.length) {
              resultsHost.innerHTML = notice + `<p class="stat-sub">${HomezI18n.t("lw.brand_search_empty")}</p>`;
              return;
            }
            resultsHost.innerHTML = notice + resp.results.map((r, i) => `
              <div class="lw-brand-search-result-row">
                <span>${escapeHtml(r.official_brand_name)} (${escapeHtml(r.brand_id)}) — ${lwBrandEnrollmentLabel(r.enrollment_status)}</span>
                <button type="button" class="btn btn-secondary btn-sm" data-lw-brand-select-idx="${i}">${HomezI18n.t("lw.brand_search_select_btn")}</button>
              </div>
            `).join("");
            resultsHost.querySelectorAll("[data-lw-brand-select-idx]").forEach((btn) => {
              btn.addEventListener("click", () => {
                const picked = resp.results[Number(btn.dataset.lwBrandSelectIdx)];
                lwSetBrandState(panel, {
                  brandState: "OFFICIAL_BRAND", brand: "",
                  brandId: picked.brand_id, officialBrandName: picked.official_brand_name,
                  brandEnrollmentStatus: picked.enrollment_status,
                  brandLookupFingerprint: picked.lookup_fingerprint,
                });
                lwWireBrandStatePanel(content);
                lwScheduleAutosave();
              });
            });
          } catch (err) {
            resultsHost.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
          }
        });
      }
    });
  }

  async function lwRenderFulfillmentStep(content) {
    const w = lwState.wizard;
    const selections = w.channel_selections || [];
    if (!selections.length) {
      content.innerHTML = `<p class="field-error">${HomezI18n.t("lw.fulfillment_needs_channels")}</p>`;
      lwSaveCurrentStep = null;
      return;
    }

    content.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    // 계정 → 채널 매핑 + 채널별 지원 방식(list_capabilities) 조회 —
    // 문자열 하드코딩 대신 기존 capability_registry를 그대로 재사용.
    const channels = await apiFetch("/marketplace-listings/channels");
    const accountsByChannel = await Promise.all(
      channels.map((ch) => apiFetch(`/marketplace-listings/channels/${ch.id}/accounts`).catch(() => [])),
    );
    const accountToChannel = {};
    channels.forEach((ch, i) => {
      (accountsByChannel[i] || []).forEach((acc) => { accountToChannel[acc.id] = ch; });
    });
    const capsByChannelId = {};
    for (const ch of channels) {
      capsByChannelId[ch.id] = await apiFetch(`/marketplace-listings/channels/${ch.id}/capabilities`).catch(() => []);
    }
    // 2026-08-31 — 상세설명(contents) 입력 UI. 3단계에서 이미 만든
    // media_asset 중 DETAIL 자산만 골라 상세설명 후보로 보여준다 —
    // 새 이미지를 여기서 만들지 않는다(기존 자산 재사용).
    const candidateMediaAssets = await apiFetch(
      `/media-assets/owners/PRODUCT_CANDIDATE/${w.product_candidate_id}/assets`,
    ).catch(() => []);
    const detailMediaAssets = candidateMediaAssets.filter(
      (a) => a.purpose === "DETAIL" && a.status === "ACTIVE",
    );

    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.fulfillment")}</h2>
      ${selections.map((sel, idx) => {
        const ch = accountToChannel[sel.marketplace_account_id];
        const supportedCaps = ch ? (capsByChannelId[ch.id] || []).filter((c) => c.is_supported) : [];
        const caps = supportedCaps.filter((c) => c.status === "VERIFIED");
        const pendingCaps = supportedCaps.filter((c) => c.status !== "VERIFIED");
        const policyAttrs = sel.channel_policy_attributes || {};
        const confirmedRules = new Set(sel.channel_policy_confirmed_evidence_rule_codes || []);
        const liveImage = (sel.required_fields?.images || []).find(
          (image) => image.imageType === "REPRESENTATION",
        ) || {};
        return `
        <div class="lw-channel-block" data-lw-account="${sel.marketplace_account_id}" data-lw-channel="${ch ? escapeHtml(ch.code) : ""}">
          <strong>${ch ? escapeHtml(ch.name) : sel.marketplace_account_id}</strong>
          <label class="field">
            <span class="field-label">${HomezI18n.t("lw.fulfillment_mode_label")}</span>
            <select class="lw-fulfillment-mode">
              <option value="">${HomezI18n.t("common.select_placeholder")}</option>
              ${caps.map((c) => `<option value="${escapeHtml(c.fulfillment_mode)}" ${sel.fulfillment_mode === c.fulfillment_mode ? "selected" : ""}>${escapeHtml(c.external_display_name)}</option>`).join("")}
            </select>
          </label>
          ${pendingCaps.length ? `<div class="banner banner-info">
            <span>${HomezI18n.t("lw.capability_activation_required")}</span>
            ${pendingCaps.map((c) => `<button type="button" class="btn btn-secondary btn-sm" data-lw-capability-verify="${c.id}">${escapeHtml(c.external_display_name)} ${HomezI18n.t("lw.capability_activate")}</button>`).join(" ")}
          </div>` : ""}
          <details class="lw-advanced-fields">
            <summary>${HomezI18n.t("lw.required_fields_label")}</summary>
            <label class="field">
              <textarea class="lw-required-fields" rows="4" data-lw-autosave-on-change>${escapeHtml(JSON.stringify(lwPublicRequiredFields(sel.required_fields), null, 2))}</textarea>
            </label>
          </details>
          ${ch && ch.code === "COUPANG" ? `
          <div class="lw-live-image-panel">
            <label class="field"><span class="field-label">${HomezI18n.t("lw.live_image_url_label")}</span>
              <div class="field-inline">
                <input type="url" class="lw-live-image-url" value="${escapeHtml(liveImage.vendorPath || "")}" placeholder="https://...">
                <button type="button" class="btn btn-secondary btn-sm" data-lw-image-auto-upload>${HomezI18n.t("lw.live_image_auto_upload_btn")}</button>
              </div>
            </label>
            <p class="stat-sub lw-image-auto-upload-status" data-lw-image-auto-upload-status></p>
            <label class="lw-checklist-item">
              <input type="checkbox" class="lw-live-image-rights" ${sel.required_fields?.liveImageRightsConfirmed ? "checked" : ""}>
              <span>${HomezI18n.t("lw.live_image_rights_confirm")}</span>
            </label>
            <p class="stat-sub">${HomezI18n.t("lw.live_image_url_hint")}</p>
          </div>
          <div class="lw-contents-panel" data-lw-contents-host
            data-lw-contents-json='${escapeHtml(JSON.stringify(sel.required_fields?.contents || []))}'
            data-lw-contents-source-ids='${escapeHtml(JSON.stringify(sel.required_fields?.contentsSourceAssetIds || []))}'>
            <span class="field-label">${HomezI18n.t("lw.contents_label")}</span>
            <p class="stat-sub">${HomezI18n.t("lw.contents_help")}</p>
            ${detailMediaAssets.length ? `
            <div class="lw-contents-asset-list">
              ${detailMediaAssets.map((a) => `
                <label class="lw-checklist-item">
                  <input type="checkbox" class="lw-contents-asset-checkbox" value="${a.id}" ${(sel.required_fields?.contentsSourceAssetIds || []).includes(a.id) ? "checked" : ""}>
                  <span>#${a.id} · ${escapeHtml(a.purpose)}</span>
                </label>`).join("")}
            </div>
            <button type="button" class="btn btn-secondary btn-sm" data-lw-contents-build>${HomezI18n.t("lw.contents_build_btn")}</button>
            <p class="stat-sub">${HomezI18n.t("lw.contents_replace_hint")}</p>
            ` : `
            <div class="banner banner-info">
              <span>${HomezI18n.t("lw.contents_no_detail_images")}</span>
              <button type="button" class="btn btn-secondary btn-sm" data-lw-contents-goto-media>${HomezI18n.t("lw.contents_goto_media_btn")}</button>
            </div>
            `}
            <p class="stat-sub lw-contents-status" data-lw-contents-status>${(sel.required_fields?.contents || []).length ? HomezI18n.t("lw.contents_saved_status", { count: (sel.required_fields.contents || []).length }) : ""}</p>
            <p class="field-error" data-lw-contents-error></p>
          </div>
          <div class="lw-logistics-panel">
            <div class="lw-field-grid">
              <label class="field"><span class="field-label">${HomezI18n.t("lw.outbound_shipping_place")}</span>
                <div class="field-inline">
                  <select class="lw-outbound-place" data-saved-code="${escapeHtml(sel.outbound_shipping_place_code || "")}">
                    <option value="">${HomezI18n.t("lw.logistics_lookup_required")}</option>
                    ${sel.outbound_shipping_place_code ? `<option value="${escapeHtml(sel.outbound_shipping_place_code)}" selected>${HomezI18n.t("lw.logistics_saved_value_pending_refresh", { code: sel.outbound_shipping_place_code })}</option>` : ""}
                  </select>
                  <button type="button" class="btn btn-secondary btn-sm" data-lw-outbound-load>${HomezI18n.t("lw.logistics_lookup")}</button>
                </div>
              </label>
              <label class="field"><span class="field-label">${HomezI18n.t("lw.return_shipping_center")}</span>
                <div class="field-inline">
                  <select class="lw-return-center" data-saved-code="${escapeHtml(sel.return_center_code || "")}">
                    <option value="">${HomezI18n.t("lw.logistics_lookup_required")}</option>
                    ${sel.return_center_code ? `<option value="${escapeHtml(sel.return_center_code)}" selected>${HomezI18n.t("lw.logistics_saved_value_pending_refresh", { code: sel.return_center_code })}</option>` : ""}
                  </select>
                  <button type="button" class="btn btn-secondary btn-sm" data-lw-return-load>${HomezI18n.t("lw.logistics_lookup")}</button>
                </div>
              </label>
            </div>
            <p class="stat-sub">${HomezI18n.t("lw.logistics_privacy_note")}</p>
            <p class="field-error" data-lw-logistics-error></p>
          </div>
          <div class="lw-coupang-charges-panel">
            <p class="field-label">${HomezI18n.t("lw.coupang_charges_title")}</p>
            <p class="stat-sub">${HomezI18n.t("lw.coupang_charges_hint")}</p>
            <div class="lw-field-grid">
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.delivery_charge_type_label")}</span>
                <select class="lw-delivery-charge-type">
                  ${["FREE", "NOT_FREE", "CHARGE_RECEIVED", "CONDITIONAL_FREE"].map((v) => `
                    <option value="${v}" ${sel.required_fields?.deliveryChargeType === v ? "selected" : ""}>${HomezI18n.t(`lw.delivery_charge_type_${v.toLowerCase()}`)}</option>
                  `).join("")}
                </select>
              </label>
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.delivery_charge_label")}</span>
                <input type="number" min="0" class="lw-delivery-charge" value="${sel.required_fields?.deliveryCharge ?? ""}">
                <p class="field-error" data-lw-delivery-charge-error></p>
              </label>
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.delivery_charge_on_return_label")}</span>
                <input type="number" min="0" class="lw-delivery-charge-on-return" value="${sel.required_fields?.deliveryChargeOnReturn ?? ""}">
                <p class="field-error" data-lw-delivery-charge-on-return-error></p>
              </label>
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.return_charge_label")}</span>
                <input type="number" min="0" class="lw-return-charge" value="${sel.required_fields?.returnCharge ?? ""}">
                <p class="field-error" data-lw-return-charge-error></p>
              </label>
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.max_buy_count_label")}</span>
                <input type="number" min="1" class="lw-max-buy-count" value="${sel.required_fields?.maximumBuyCount ?? ""}">
                <p class="field-error" data-lw-max-buy-count-error></p>
              </label>
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.delivery_company_label")}</span>
                <select class="lw-delivery-company">
                  <option value="">${HomezI18n.t("common.select_placeholder")}</option>
                  ${LW_COUPANG_DELIVERY_COMPANIES.map((c) => `
                    <option value="${c.code}" ${sel.required_fields?.deliveryCompanyCode === c.code ? "selected" : ""}>${escapeHtml(c.label)}</option>
                  `).join("")}
                </select>
                <p class="field-error" data-lw-delivery-company-error></p>
              </label>
              <label class="field">
                <span class="field-label">${HomezI18n.t("lw.vendor_user_id_label")}</span>
                <input type="text" class="lw-vendor-user-id" value="${escapeHtml(sel.required_fields?.vendorUserId || "")}" placeholder="${HomezI18n.t("lw.vendor_user_id_placeholder")}">
              </label>
            </div>
            <p class="stat-sub">${HomezI18n.t("lw.delivery_method_auto_note")}</p>
            ${lwRenderBrandStatePanel(sel.required_fields || {})}
          </div>` : ""}
          ${ch ? `
          <div class="lw-channel-policy-panel" data-lw-policy-channel="${escapeHtml(ch.code)}">
            <div class="lw-field-grid">
              <label class="field"><span class="field-label">${HomezI18n.t("lw.policy_origin_country")}</span>
                <select class="lw-policy-origin-country">
                  <option value="">${HomezI18n.t("common.select_placeholder")}</option>
                  <option value="KOREA" ${policyAttrs.origin_country === "KOREA" ? "selected" : ""}>${HomezI18n.t("lw.policy_origin_korea")}</option>
                </select>
              </label>
              <label class="field"><span class="field-label">${HomezI18n.t("lw.policy_brand")}</span>
                <input type="text" class="lw-policy-brand" value="${escapeHtml(policyAttrs.brand || "")}">
              </label>
              <label class="field"><span class="field-label">${HomezI18n.t("lw.policy_product_identifier")}</span>
                <input type="text" class="lw-policy-product-identifier" value="${escapeHtml(policyAttrs.product_identifier || "")}">
              </label>
              <label class="field"><span class="field-label">${HomezI18n.t("lw.policy_official_category_code")}</span>
                <div class="field-inline">
                  <input type="text" class="lw-policy-official-category" value="${escapeHtml(policyAttrs.official_category_code || "")}" readonly>
                  <button type="button" class="btn btn-secondary btn-sm" data-lw-category-recommend>${HomezI18n.t("lw.category_recommend")}</button>
                </div>
              </label>
            </div>
            <div class="lw-purchase-options" data-lw-purchase-option-fields></div>
            <div class="lw-item-combo-builder" data-lw-item-combo-builder></div>
            <div class="lw-notice-information" data-lw-notice-fields></div>
            <input type="hidden" class="lw-category-metadata-version" value="${escapeHtml(policyAttrs.category_metadata_version || "")}">
            <input type="hidden" class="lw-category-metadata-fingerprint" value="${escapeHtml(policyAttrs.category_metadata_fingerprint || "")}">
            <label class="lw-checklist-item">
              <input type="checkbox" class="lw-policy-notice-confirmed" ${confirmedRules.has("CATEGORY_NOTICE_INFO_REQUIRED") ? "checked" : ""} disabled>
              <span>${HomezI18n.t("lw.policy_notice_confirmed")}</span>
            </label>
            <div class="lw-channel-policy-header">
              <span class="field-label">${HomezI18n.t("lw.channel_policy_label")}</span>
              <span class="lw-channel-policy-badge" data-lw-policy-badge>${HomezI18n.t("lw.channel_policy_not_checked")}</span>
              <button type="button" class="btn btn-secondary btn-sm" data-lw-policy-run>${HomezI18n.t("lw.channel_policy_run")}</button>
            </div>
            <div class="lw-channel-policy-detail" data-lw-policy-detail></div>
          </div>` : ""}
        </div>`;
      }).join("")}
      <p class="field-error" id="lw-fulfillment-error"></p>`;

    content.querySelectorAll(".lw-channel-block").forEach((block) => {
      lwLoadChannelPolicyStatus(block, w.product_candidate_id);
    });
    content.querySelectorAll("[data-lw-capability-verify]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        await apiFetch(`/marketplace-listings/capabilities/${btn.dataset.lwCapabilityVerify}/verify`, {
          method: "POST",
          body: JSON.stringify({ idempotency_key: `console-capability-${btn.dataset.lwCapabilityVerify}-${Date.now()}` }),
        });
        await lwRenderFulfillmentStep(content);
      }));
    });
    content.querySelectorAll("[data-lw-policy-run]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const block = btn.closest(".lw-channel-block");
        // 확인 시각·사용자·fingerprint는 fulfillment 저장 시 서버가 만든다.
        // 정책 검사가 화면의 미확정 원시값을 앞질러 실행되지 않게 한다.
        const saved = await lwSaveCurrentStep({ silent: true });
        if (!saved) return;
        const accountId = Number(block.dataset.lwAccount);
        const selection = (lwState.wizard.channel_selections || []).find(
          (item) => item.marketplace_account_id === accountId,
        );
        await lwRunChannelPolicyEvaluate(block, w.product_candidate_id, selection);
      }));
    });
    content.querySelectorAll("[data-lw-category-recommend]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const block = btn.closest(".lw-channel-block");
        const detail = block.querySelector("[data-lw-policy-detail]");
        if (detail) detail.textContent = HomezI18n.t("common.loading");
        try {
          const recommendation = await apiFetch(`/listing-wizards/${w.id}/category-recommendation`, { method: "POST" });
          const metadata = await apiFetch(`/listing-wizards/${w.id}/category-metadata/${encodeURIComponent(recommendation.display_category_code)}`);
          block.querySelector(".lw-policy-official-category").value = recommendation.display_category_code;
          block.querySelector(".lw-category-metadata-version").value = metadata.metadata_version;
          block.querySelector(".lw-category-metadata-fingerprint").value = metadata.metadata_fingerprint;
          lwRenderNoticeFields(block, metadata.notice_fields, {});
          // 카테고리·Metadata가 바뀌면 이전 구매옵션 값은 새 카테고리
          // 기준으로 무효다 — 빈 값으로 다시 그려 재확인을 강제한다
          // (2026-08-29 쿠팡 상품등록 핵심 차단 해결, Section 4 요구).
          lwRenderPurchaseOptionFields(block, metadata.purchase_option_fields || [], {});
          // 카테고리가 바뀌면 옵션 조합도 전부 무효다 — 새로 만들어야
          // 한다(기존 조합을 그대로 들고 있으면 새 카테고리의 잘못된
          // attributeTypeName이 섞여 들어갈 수 있다).
          lwRenderItemComboBuilder(block, metadata.purchase_option_fields || [], []);
          if (detail) {
            detail.textContent = "";
            detail.classList.remove("field-error");
          }
        } catch (err) {
          if (detail) {
            detail.textContent = err?.message || HomezI18n.t("lw.category_lookup_failed");
            detail.classList.add("field-error");
          }
        }
      }));
    });
    const populateLogistics = (select, items, codeKey) => {
      const saved = select.dataset.savedCode || select.value || "";
      const savedStillValid = items.some((item) => item[codeKey] === saved);
      select.innerHTML = `<option value="">${HomezI18n.t("common.select_placeholder")}</option>`
        + (saved && !savedStillValid
          ? `<option value="" disabled>${HomezI18n.t("lw.logistics_saved_value_stale_placeholder")}</option>`
          : "")
        + items.map((item) =>
          `<option value="${escapeHtml(item[codeKey])}" ${item[codeKey] === saved ? "selected" : ""} ${item.usable ? "" : "disabled"}>${escapeHtml(item.shipping_place_name)}${item.usable ? "" : ` (${HomezI18n.t("lw.logistics_unusable")})`}</option>`
        ).join("");
      // 재조회로 확인됐으니, 더 이상 유효하지 않은 저장값은 다음 저장
      // 때 그대로 다시 보내지 않는다 — 사용자가 새로 선택해야 한다.
      select.dataset.savedCode = savedStillValid ? saved : "";
      return { saved, savedStillValid };
    };
    content.querySelectorAll("[data-lw-outbound-load]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const block = btn.closest(".lw-channel-block");
        const error = block.querySelector("[data-lw-logistics-error]");
        error.textContent = "";
        try {
          const items = await apiFetch(`/listing-wizards/${w.id}/coupang/outbound-shipping-places`);
          const { saved, savedStillValid } = populateLogistics(block.querySelector(".lw-outbound-place"), items, "outbound_shipping_place_code");
          if (!items.length) error.textContent = HomezI18n.t("lw.logistics_empty");
          else if (saved && !savedStillValid) error.textContent = HomezI18n.t("lw.logistics_saved_value_stale_warning");
        } catch (err) { error.textContent = err?.message || HomezI18n.t("lw.logistics_lookup_failed"); }
      }));
    });
    content.querySelectorAll("[data-lw-return-load]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const block = btn.closest(".lw-channel-block");
        const error = block.querySelector("[data-lw-logistics-error]");
        error.textContent = "";
        try {
          const items = await apiFetch(`/listing-wizards/${w.id}/coupang/return-shipping-centers`);
          const { saved, savedStillValid } = populateLogistics(block.querySelector(".lw-return-center"), items, "return_center_code");
          if (!items.length) error.textContent = HomezI18n.t("lw.logistics_empty");
          else if (saved && !savedStillValid) error.textContent = HomezI18n.t("lw.logistics_saved_value_stale_warning");
        } catch (err) { error.textContent = err?.message || HomezI18n.t("lw.logistics_lookup_failed"); }
      }));
    });
    content.querySelectorAll(".lw-outbound-place,.lw-return-center").forEach((select) => {
      select.addEventListener("change", () => {
        select.dataset.savedCode = select.value;
        lwScheduleAutosave();
      });
    });
    content.querySelectorAll(".lw-delivery-charge-type").forEach((select) => {
      const block = select.closest(".lw-channel-block");
      lwApplyDeliveryChargeVisibility(block);
      select.addEventListener("change", () => {
        lwApplyDeliveryChargeVisibility(block);
        lwValidateCoupangCharges(block);
        lwScheduleAutosave();
      });
    });
    content.querySelectorAll(
      ".lw-delivery-charge,.lw-delivery-charge-on-return,.lw-return-charge,.lw-max-buy-count",
    ).forEach((input) => {
      input.addEventListener("input", () => {
        lwValidateCoupangCharges(input.closest(".lw-channel-block"));
        lwScheduleAutosave();
      });
    });
    content.querySelectorAll(".lw-delivery-company").forEach((select) => {
      select.addEventListener("change", () => {
        lwValidateCoupangCharges(select.closest(".lw-channel-block"));
        lwScheduleAutosave();
      });
    });
    content.querySelectorAll(".lw-vendor-user-id").forEach((input) => {
      input.addEventListener("input", () => lwScheduleAutosave());
    });
    lwWireBrandStatePanel(content);
    content.querySelectorAll("[data-lw-image-auto-upload]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const block = btn.closest(".lw-channel-block");
        const status = block.querySelector("[data-lw-image-auto-upload-status]");
        status.textContent = HomezI18n.t("lw.live_image_auto_upload_running");
        try {
          const result = await apiFetch(`/listing-wizards/${w.id}/coupang/auto-upload-images`, { method: "POST" });
          const rep = (result.images || []).find((img) => img.imageType === "REPRESENTATION") || result.images?.[0];
          if (!rep) {
            status.textContent = HomezI18n.t("lw.live_image_auto_upload_empty");
            return;
          }
          block.querySelector(".lw-live-image-url").value = rep.vendorPath;
          block.querySelector(".lw-live-image-rights").checked = true;
          status.textContent = HomezI18n.t("lw.live_image_auto_upload_success");
          lwScheduleAutosave();
        } catch (err) {
          status.textContent = err?.message || HomezI18n.t("lw.live_image_auto_upload_failed");
        }
      }));
    });
    content.querySelectorAll("[data-lw-contents-goto-media]").forEach((btn) => {
      btn.addEventListener("click", () => lwJumpToStep("MEDIA"));
    });
    content.querySelectorAll("[data-lw-contents-build]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const block = btn.closest(".lw-channel-block");
        const host = block.querySelector("[data-lw-contents-host]");
        const status = block.querySelector("[data-lw-contents-status]");
        const error = block.querySelector("[data-lw-contents-error]");
        error.textContent = "";
        const checkedIds = Array.from(
          block.querySelectorAll(".lw-contents-asset-checkbox:checked"),
        ).map((c) => Number(c.value));
        if (!checkedIds.length) {
          error.textContent = HomezI18n.t("lw.contents_selection_required");
          return;
        }
        status.textContent = HomezI18n.t("common.loading");
        try {
          const result = await apiFetch(`/listing-wizards/${w.id}/coupang/contents-from-media`, {
            method: "POST",
            body: JSON.stringify({ media_asset_ids: checkedIds }),
          });
          host.dataset.lwContentsJson = JSON.stringify(result.contents || []);
          host.dataset.lwContentsSourceIds = JSON.stringify(checkedIds);
          status.textContent = HomezI18n.t("lw.contents_saved_status", { count: (result.contents || []).length });
          lwScheduleAutosave();
        } catch (err) {
          status.textContent = "";
          error.textContent = err?.message || HomezI18n.t("lw.contents_build_failed");
        }
      }));
    });
    content.querySelectorAll(".lw-channel-block").forEach((block) => {
      const accountId = Number(block.dataset.lwAccount);
      const savedSelection = selections.find((item) => item.marketplace_account_id === accountId);
      const saved = savedSelection?.channel_policy_attributes || {};
      lwRenderNoticeFields(block, saved.notice_field_definitions || [], saved.notice_information || {});
      lwRenderPurchaseOptionFields(
        block, saved.purchase_option_field_definitions || null, saved.purchase_options || {},
      );
      lwRenderItemComboBuilder(
        block, saved.purchase_option_field_definitions || null,
        (savedSelection?.required_fields?.items || []),
      );
    });

    lwSaveCurrentStep = async (opts = {}) => {
      const errEl = el("lw-fulfillment-error");
      errEl.textContent = "";
      const blocks = Array.from(content.querySelectorAll(".lw-channel-block"));
      const payload = [];
      for (const block of blocks) {
        const accountId = Number(block.dataset.lwAccount);
        const mode = block.querySelector(".lw-fulfillment-mode").value;
        const rawFields = block.querySelector(".lw-required-fields").value.trim();
        if (!mode) {
          if (!opts.silent) errEl.textContent = HomezI18n.t("lw.fulfillment_mode_required");
          return false;
        }
        let requiredFields = {};
        try {
          requiredFields = rawFields ? JSON.parse(rawFields) : {};
        } catch (_) {
          if (!opts.silent) errEl.textContent = HomezI18n.t("lw.required_fields_invalid_json");
          return false;
        }
        if (block.dataset.lwChannel === "COUPANG") {
          const imageUrl = block.querySelector(".lw-live-image-url")?.value.trim() || "";
          const rightsConfirmed = Boolean(block.querySelector(".lw-live-image-rights")?.checked);
          if (imageUrl) {
            try {
              const parsed = new URL(imageUrl);
              if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("protocol");
            } catch (_err) {
              if (!opts.silent) errEl.textContent = HomezI18n.t("lw.live_image_url_invalid");
              return false;
            }
            requiredFields.images = [{
              imageOrder: 0, imageType: "REPRESENTATION", vendorPath: imageUrl,
            }];
          } else {
            delete requiredFields.images;
          }
          requiredFields.liveImageRightsConfirmed = rightsConfirmed;

          // 2026-08-31 — 상세설명(contents). "선택한 이미지로
          // 상세설명 구성" 버튼을 이번 화면 방문에서 실제로 눌렀을
          // 때만 덮어쓴다 — 누르지 않았으면 원본 JSON(raw JSON
          // textarea)에 이미 있던 기존 contents를 그대로 둔다(단계를
          // 오갔다가 돌아와도 값이 유지되는 이유).
          const contentsHost = block.querySelector("[data-lw-contents-host]");
          if (contentsHost?.dataset.lwContentsJson) {
            try {
              const builtContents = JSON.parse(contentsHost.dataset.lwContentsJson);
              if (builtContents.length) {
                requiredFields.contents = builtContents;
                requiredFields.contentsSourceAssetIds = JSON.parse(
                  contentsHost.dataset.lwContentsSourceIds || "[]",
                );
              }
            } catch (_err) { /* 무시 — 원본 JSON의 기존 contents 유지 */ }
          }

          // 2026-08-29 — 배송비·반품비 구조화 입력(Section 2B).
          // deliveryMethod는 이 화면이 판매자배송(일반배송) 전용이므로
          // 항상 SEQUENCIAL로 자동 결정한다(공식 문서 — 국내 일반배송
          // 상품 기준, 로켓그로스/해외구매대행 등은 이 화면에서 다루지
          // 않는다).
          if (!lwValidateCoupangCharges(block)) {
            if (!opts.silent) errEl.textContent = HomezI18n.t("lw.coupang_charges_invalid");
            return false;
          }
          const chargeType = block.querySelector(".lw-delivery-charge-type")?.value;
          if (chargeType) {
            requiredFields.deliveryMethod = "SEQUENCIAL";
            requiredFields.deliveryChargeType = chargeType;
            requiredFields.deliveryCharge = Number(
              block.querySelector(".lw-delivery-charge")?.value || "0",
            );
            requiredFields.deliveryChargeOnReturn = Number(
              block.querySelector(".lw-delivery-charge-on-return")?.value || "0",
            );
            requiredFields.returnCharge = Number(
              block.querySelector(".lw-return-charge")?.value || "0",
            );
            requiredFields.maximumBuyCount = Number(
              block.querySelector(".lw-max-buy-count")?.value || "0",
            );
            requiredFields.deliveryCompanyCode = block.querySelector(".lw-delivery-company")?.value || null;
          }
          const vendorUserId = block.querySelector(".lw-vendor-user-id")?.value.trim();
          if (vendorUserId) requiredFields.vendorUserId = vendorUserId;
          else delete requiredFields.vendorUserId;

          // 브랜드 3상태 — 패널 자신의 data 속성에 보관된 현재 상태를
          // 그대로 병합한다(coupang_submission_contract.py가 요구하는
          // 필드 이름과 정확히 일치).
          const brandPanel = block.querySelector(".lw-brand-state-panel");
          if (brandPanel) {
            try {
              const brandState = JSON.parse(brandPanel.dataset.lwBrandStateJson || "{}");
              Object.assign(requiredFields, brandState);
            } catch (_) { /* 파싱 실패 — 기존 값 유지 */ }
          }
        }
        // 2026-08-29 — 옵션 조합 UI(Section 2A). 조합 빌더가 행을
        // 하나라도 가지고 있으면 그 표가 items[]의 유일한 출처다(원시
        // JSON에 남아있을 수 있는 옛 items를 덮어쓴다) — 표가 완전히
        // 비어 있으면(단일 옵션 상품이거나 아직 카테고리를 조회하지
        // 않음) 원시 JSON의 기존 items를 그대로 둔다.
        const comboHost = block.querySelector("[data-lw-item-combo-builder]");
        if (comboHost?.dataset.lwComboItems) {
          try {
            const comboItems = JSON.parse(comboHost.dataset.lwComboItems);
            if (comboItems.length) requiredFields.items = comboItems;
          } catch (_err) { /* 조합 빌더가 비활성 상태 — 원시 JSON 유지 */ }
        }
        // 2026-08-29 — 상품 전체 originalPrice/salePrice는 옵션
        // 조합표의 대표(첫) 행 값을 그대로 연결한다(사용자 명시 요구
        // — 별도 입력칸을 만들지 않는다). 행이 값을 아직 채우지
        // 않았으면 여기서 임의로 채우지 않고 그대로 두어(fail-closed)
        // 서버 측 필수값 검증이 그 사실을 그대로 드러내게 한다.
        if (block.dataset.lwChannel === "COUPANG") {
          const firstItem = (requiredFields.items || [])[0];
          if (firstItem?.originalPrice != null) requiredFields.originalPrice = firstItem.originalPrice;
          if (firstItem?.salePrice != null) requiredFields.salePrice = firstItem.salePrice;
        }
        let policyInput;
        try {
          policyInput = lwCollectChannelPolicyInput(block);
        } catch (_err) {
          if (!opts.silent) errEl.textContent = HomezI18n.t("lw.policy_purchase_options_invalid");
          return false;
        }
        payload.push({
          marketplace_account_id: accountId,
          fulfillment_mode: mode,
          required_fields: requiredFields,
          outbound_shipping_place_code: block.querySelector(".lw-outbound-place")?.value || null,
          return_center_code: block.querySelector(".lw-return-center")?.value || null,
          channel_policy_attributes: policyInput.attributes,
          channel_policy_confirmed_evidence_rule_codes: policyInput.confirmedRules,
        });
      }
      try {
        const fresh = await apiFetch(`/listing-wizards/${w.id}/fulfillment`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_version: lwState.wizard.version,
            selections: payload,
            autosave_client_token: lwGetAutosaveToken(),
          }),
        });
        if (opts.silent) lwApplySilentSave(fresh); else lwAdvanceAfterSave(fresh);
        return true;
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
        return false;
      }
    };
  }

  function lwCollectChannelPolicyInput(block) {
    const attributes = {};
    const origin = block.querySelector(".lw-policy-origin-country")?.value || "";
    const brand = block.querySelector(".lw-policy-brand")?.value.trim() || "";
    const identifier = block.querySelector(".lw-policy-product-identifier")?.value.trim() || "";
    const category = block.querySelector(".lw-policy-official-category")?.value.trim() || "";
    if (origin) attributes.origin_country = origin;
    if (brand) attributes.brand = brand;
    if (identifier) attributes.product_identifier = identifier;
    if (category) attributes.official_category_code = category;
    const metadataVersion = block.querySelector(".lw-category-metadata-version")?.value || "";
    const metadataFingerprint = block.querySelector(".lw-category-metadata-fingerprint")?.value || "";
    const noticeInputs = Array.from(block.querySelectorAll("[data-lw-notice-key]"));
    if (metadataVersion) attributes.category_metadata_version = metadataVersion;
    if (metadataFingerprint) attributes.category_metadata_fingerprint = metadataFingerprint;
    if (noticeInputs.length) {
      attributes.notice_information = {};
      attributes.notice_information.__notice_category_name = block.querySelector("[data-lw-notice-category]")?.value || "";
      attributes.notice_required_field_keys = [];
      attributes.notice_field_definitions = [];
      noticeInputs.forEach((input) => {
        const key = input.dataset.lwNoticeKey;
        attributes.notice_information[key] = input.value.trim();
        const required = input.dataset.lwNoticeRequired === "true";
        if (required) attributes.notice_required_field_keys.push(key);
        attributes.notice_field_definitions.push({ key, label: input.dataset.lwNoticeLabel, required });
      });
    }
    // 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 원시 JSON 텍스트박스
    // 대신 Category Metadata의 실제 attributeTypeName/inputType/
    // inputValues 기준으로 그려진 구조화된 입력에서 값을 모은다.
    // 실제 백업 DB 증거(Wizard #6)에서 이 값이 카테고리 계약과 무관한
    // 자유 텍스트였던 것을 확인했다 — 이 연결이 그 결함의 직접 수정.
    const purchaseOptionHost = block.querySelector("[data-lw-purchase-option-fields]");
    const purchaseOptionInputs = Array.from(block.querySelectorAll("[data-lw-purchase-option-key]"));
    if (purchaseOptionHost?.dataset.lwPurchaseOptionDefinitions) {
      attributes.purchase_option_field_definitions = JSON.parse(
        purchaseOptionHost.dataset.lwPurchaseOptionDefinitions,
      );
    }
    if (purchaseOptionInputs.length) {
      attributes.purchase_options = {};
      purchaseOptionInputs.forEach((input) => {
        const value = input.value.trim();
        if (value) attributes.purchase_options[input.dataset.lwPurchaseOptionKey] = value;
      });
    }
    const confirmedRules = [];
    if (block.querySelector(".lw-policy-notice-confirmed")?.checked) {
      confirmedRules.push("CATEGORY_NOTICE_INFO_REQUIRED");
    }
    return { attributes, confirmedRules };
  }

  function lwRenderNoticeFields(block, definitions, values) {
    const host = block.querySelector("[data-lw-notice-fields]");
    const checkbox = block.querySelector(".lw-policy-notice-confirmed");
    if (!host || !checkbox) return;
    const groups = [...new Set(definitions.map((field) => String(field.key).split("::", 1)[0]))];
    const savedGroup = values.__notice_category_name || "";
    host.innerHTML = definitions.length ? `
      <h3>${HomezI18n.t("lw.notice_information_title")}</h3>
      <label class="field"><span class="field-label">${HomezI18n.t("lw.notice_category_label")}</span>
        <select data-lw-notice-category><option value="">${HomezI18n.t("common.select_placeholder")}</option>
          ${groups.map((group) => `<option value="${escapeHtml(group)}" ${savedGroup === group ? "selected" : ""}>${escapeHtml(group)}</option>`).join("")}
        </select>
      </label>
      <div class="lw-field-grid" data-lw-notice-grid></div>` : `<p class="stat-sub">${HomezI18n.t("lw.notice_information_select_category")}</p>`;
    const refresh = () => {
      const selected = host.querySelector("[data-lw-notice-category]")?.value || "";
      const required = Array.from(host.querySelectorAll('[data-lw-notice-required="true"]'));
      const valid = Boolean(selected) && required.length > 0 && required.every((input) => input.value.trim());
      checkbox.disabled = !valid;
      if (!valid) checkbox.checked = false;
      required.forEach((input) => {
        const error = input.parentElement.querySelector("[data-lw-notice-error]");
        if (error) error.textContent = input.value.trim() ? "" : HomezI18n.t("lw.notice_information_required");
      });
    };
    const renderGroup = () => {
      const grid = host.querySelector("[data-lw-notice-grid]");
      const selected = host.querySelector("[data-lw-notice-category]")?.value || "";
      if (!grid) return;
      grid.innerHTML = definitions.filter((field) => String(field.key).startsWith(`${selected}::`)).map((field) => `
        <label class="field"><span class="field-label">${escapeHtml(field.label)}${field.required ? " *" : ""}</span>
          <input type="text" data-lw-notice-key="${escapeHtml(field.key)}" data-lw-notice-label="${escapeHtml(field.label)}" data-lw-notice-required="${field.required ? "true" : "false"}" value="${escapeHtml(values[field.key] || "")}">
          <span class="field-error" data-lw-notice-error></span>
        </label>`).join("");
      grid.querySelectorAll("[data-lw-notice-key]").forEach((input) => input.addEventListener("input", () => {
        refresh();
        lwScheduleAutosave();
      }));
      refresh();
    };
    host.querySelector("[data-lw-notice-category]")?.addEventListener("change", () => {
      renderGroup();
      lwScheduleAutosave();
    });
    checkbox.addEventListener("input", lwScheduleAutosave);
    renderGroup();
  }

  // 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 구매옵션을 원시 JSON
  // 텍스트박스가 아니라 Category Metadata의 실제 attributeTypeName/
  // inputType/inputValues/exposed 기준으로 그린다. exposed=NONE인
  // 속성(검색전용옵션)은 구매옵션이 아니므로 이 화면에 표시하지
  // 않는다 — exposed=EXPOSED인 것만 실제 "구매 옵션"이다(공식 문서
  // 확인 사항). `definitions`가 null이면 아직 카테고리를 조회하지
  // 않은 상태다.
  function lwRenderPurchaseOptionFields(block, definitions, values) {
    const host = block.querySelector("[data-lw-purchase-option-fields]");
    if (!host) return;
    if (definitions === null) {
      delete host.dataset.lwPurchaseOptionDefinitions;
      host.innerHTML = `<p class="stat-sub">${HomezI18n.t("lw.purchase_option_select_category_first")}</p>`;
      return;
    }
    host.dataset.lwPurchaseOptionDefinitions = JSON.stringify(definitions);
    const exposedFields = definitions.filter((field) => field.exposed);
    if (!exposedFields.length) {
      host.innerHTML = `<p class="stat-sub">${HomezI18n.t("lw.purchase_option_no_exposed_fields")}</p>`;
      return;
    }
    host.innerHTML = `
      <h3>${HomezI18n.t("lw.policy_purchase_options")}</h3>
      <div class="lw-field-grid" data-lw-purchase-option-grid></div>`;
    const grid = host.querySelector("[data-lw-purchase-option-grid]");
    grid.innerHTML = exposedFields.map((field) => {
      const name = field.attribute_type_name;
      const savedValue = values[name] || "";
      const requiredMark = field.required ? " *" : "";
      if (field.input_type === "SELECT" && (field.input_values || []).length) {
        return `
        <label class="field"><span class="field-label">${escapeHtml(name)}${requiredMark}</span>
          <select data-lw-purchase-option-key="${escapeHtml(name)}" data-lw-purchase-option-required="${field.required ? "true" : "false"}">
            <option value="">${HomezI18n.t("common.select_placeholder")}</option>
            ${field.input_values.map((v) => `<option value="${escapeHtml(v)}" ${savedValue === v ? "selected" : ""}>${escapeHtml(v)}</option>`).join("")}
          </select>
          <span class="field-error" data-lw-purchase-option-error></span>
        </label>`;
      }
      const unit = field.basic_unit ? ` (${escapeHtml(field.basic_unit)})` : "";
      return `
        <label class="field"><span class="field-label">${escapeHtml(name)}${unit}${requiredMark}</span>
          <input type="text" data-lw-purchase-option-key="${escapeHtml(name)}" data-lw-purchase-option-required="${field.required ? "true" : "false"}" value="${escapeHtml(savedValue)}">
          <span class="field-error" data-lw-purchase-option-error></span>
          <span class="stat-sub">${HomezI18n.t("lw.purchase_option_input_hint")}</span>
        </label>`;
    }).join("");
    const refresh = () => {
      grid.querySelectorAll("[data-lw-purchase-option-key]").forEach((input) => {
        const required = input.dataset.lwPurchaseOptionRequired === "true";
        const errorEl = input.parentElement.querySelector("[data-lw-purchase-option-error]");
        if (!errorEl) return;
        errorEl.textContent = required && !input.value.trim()
          ? HomezI18n.t("lw.purchase_option_value_required") : "";
      });
    };
    grid.querySelectorAll("[data-lw-purchase-option-key]").forEach((input) => {
      input.addEventListener("input", () => { refresh(); lwScheduleAutosave(); });
      input.addEventListener("change", () => { refresh(); lwScheduleAutosave(); });
    });
    refresh();
  }

  // 2026-08-29 — 쿠팡 판매자배송 배송비/반품비 구조화 입력(Section
  // 2B). 사용자 명시 요구 — 5단계 "필수 입력값(JSON)" 원시 편집 대신
  // 한국어 구조화 UI로 채운다. deliveryMethod/items/originalPrice/
  // salePrice는 이미 다른 화면 값(판매자배송 방식·옵션 조합표)에서
  // 자동 연결되므로 여기서는 배송비 유형에 따라 값을 사용자가 직접
  // 입력해야 하는 4개 항목(deliveryCharge/deliveryChargeOnReturn/
  // returnCharge/maximumBuyCount)만 다룬다.
  function lwApplyDeliveryChargeVisibility(block) {
    const typeEl = block.querySelector(".lw-delivery-charge-type");
    const chargeEl = block.querySelector(".lw-delivery-charge");
    if (!typeEl || !chargeEl) return;
    const isFree = typeEl.value === "FREE";
    chargeEl.disabled = isFree;
    if (isFree) chargeEl.value = "0";
  }

  function lwValidateCoupangCharges(block) {
    const typeEl = block.querySelector(".lw-delivery-charge-type");
    if (!typeEl) return true;

    let ok = true;
    const setError = (key, message) => {
      const errEl = block.querySelector(`[data-lw-${key}-error]`);
      if (errEl) errEl.textContent = message || "";
      if (message) ok = false;
    };
    const nonNegative = (input) => {
      const raw = input.value.trim();
      if (raw === "") return HomezI18n.t("lw.coupang_charge_required_error");
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 0) return HomezI18n.t("lw.coupang_charge_invalid_error");
      return "";
    };

    const chargeEl = block.querySelector(".lw-delivery-charge");
    const chargeOnReturnEl = block.querySelector(".lw-delivery-charge-on-return");
    const returnChargeEl = block.querySelector(".lw-return-charge");
    const maxBuyEl = block.querySelector(".lw-max-buy-count");

    if (chargeEl) setError("delivery-charge", nonNegative(chargeEl));
    if (chargeOnReturnEl) setError("delivery-charge-on-return", nonNegative(chargeOnReturnEl));
    if (returnChargeEl) setError("return-charge", nonNegative(returnChargeEl));
    if (maxBuyEl) {
      const raw = maxBuyEl.value.trim();
      const n = Number(raw);
      setError(
        "max-buy-count",
        (raw === "" || !Number.isFinite(n) || n <= 0)
          ? HomezI18n.t("lw.coupang_max_buy_count_invalid_error") : "",
      );
    }
    const companyEl = block.querySelector(".lw-delivery-company");
    if (companyEl) {
      setError(
        "delivery-company",
        companyEl.value.trim() === "" ? HomezI18n.t("lw.delivery_company_required_error") : "",
      );
    }
    return ok;
  }

  // 2026-08-29 쿠팡 상품등록 핵심 차단 해결(Section 2A — 옵션 조합
  // UI). exposed 구매옵션을 축(axis)으로 삼아 조합(=items[] 각 행)을
  // 만든다. 단일 옵션 상품은 조합을 하나도 만들지 않고 위
  // lwRenderPurchaseOptionFields()의 단일 값 입력만 쓰면 된다(기존
  // 동작 그대로) — 이 빌더는 완전히 선택적(additive)이다. 원시 JSON
  // 입력을 다시 노출하지 않는다 — 모든 값은 이 구조화 표에서만 온다.
  function lwRenderItemComboBuilder(block, definitions, savedItems) {
    const host = block.querySelector("[data-lw-item-combo-builder]");
    if (!host) return;
    if (definitions === null) {
      host.innerHTML = "";
      host.dataset.lwComboItems = "[]";
      return;
    }
    const exposedFields = definitions.filter((field) => field.exposed);
    if (!exposedFields.length) {
      host.innerHTML = "";
      host.dataset.lwComboItems = JSON.stringify(savedItems || []);
      return;
    }

    // 저장된 items의 optionAttributes에서 각 축의 기존 값을 복원한다.
    const axisValues = {};
    exposedFields.forEach((f) => { axisValues[f.attribute_type_name] = new Set(); });
    (savedItems || []).forEach((item) => {
      Object.entries(item.optionAttributes || {}).forEach(([k, v]) => {
        if (axisValues[k]) axisValues[k].add(v);
      });
    });

    let items = (savedItems || []).map((item) => ({ ...item }));

    host.innerHTML = `
      <p class="field-label">${HomezI18n.t("lw.combo_builder_title")}</p>
      <p class="stat-sub">${HomezI18n.t("lw.combo_builder_hint")}</p>
      <div class="lw-combo-axes" data-lw-combo-axes>${exposedFields.map((field) => {
        const name = field.attribute_type_name;
        if (field.input_type === "SELECT" && (field.input_values || []).length) {
          return `
          <div class="lw-combo-axis" data-lw-axis-name="${escapeHtml(name)}">
            <span class="field-label">${escapeHtml(name)}</span>
            <div class="lw-combo-axis-options">${field.input_values.map((v) => `
              <label class="lw-checklist-item">
                <input type="checkbox" data-lw-axis-value="${escapeHtml(v)}" ${axisValues[name].has(v) ? "checked" : ""}>
                <span>${escapeHtml(v)}</span>
              </label>`).join("")}</div>
          </div>`;
        }
        return `
        <div class="lw-combo-axis" data-lw-axis-name="${escapeHtml(name)}">
          <span class="field-label">${escapeHtml(name)}</span>
          <input type="text" data-lw-axis-csv placeholder="${HomezI18n.t("lw.combo_axis_csv_placeholder")}" value="${escapeHtml(Array.from(axisValues[name]).join(", "))}">
        </div>`;
      }).join("")}</div>
      <button type="button" class="btn btn-secondary btn-sm" data-lw-combo-generate>${HomezI18n.t("lw.combo_generate_btn")}</button>
      <p class="field-error" data-lw-combo-error></p>
      <div class="lw-table-scroll"><table class="lw-table">
        <thead><tr>
          <th>${HomezI18n.t("lw.combo_col_options")}</th>
          <th>${HomezI18n.t("lw.combo_col_item_name")}</th>
          <th>${HomezI18n.t("lw.submission_review_item_sale_price")}</th>
          <th>${HomezI18n.t("lw.submission_review_item_original_price")}</th>
          <th>${HomezI18n.t("lw.submission_review_item_stock")}</th>
          <th>${HomezI18n.t("lw.submission_review_item_sku")}</th>
          <th>${HomezI18n.t("lw.combo_col_unit_count")}</th>
          <th></th>
        </tr></thead>
        <tbody data-lw-item-rows></tbody>
      </table></div>
      <p class="stat-sub">${HomezI18n.t("lw.combo_item_name_help")}</p>
      <p class="stat-sub">${HomezI18n.t("lw.combo_unit_count_help")}</p>
      <button type="button" class="btn btn-ghost btn-sm" data-lw-item-add-manual>${HomezI18n.t("lw.combo_add_manual_row_btn")}</button>`;

    const rowsEl = host.querySelector("[data-lw-item-rows]");
    const errorEl = host.querySelector("[data-lw-combo-error]");

    const persist = () => { host.dataset.lwComboItems = JSON.stringify(items); };

    const renderRows = () => {
      rowsEl.innerHTML = items.map((item, idx) => {
        const comboLabel = item.optionAttributes
          ? Object.values(item.optionAttributes).join(" · ")
          : HomezI18n.t("lw.combo_manual_row_label");
        return `
        <tr data-lw-item-idx="${idx}">
          <td>${escapeHtml(comboLabel)}</td>
          <td><input type="text" data-lw-item-field="itemName" value="${escapeHtml(item.itemName || "")}" placeholder="${HomezI18n.t("lw.combo_item_name_placeholder")}"></td>
          <td><input type="number" min="0" data-lw-item-field="salePrice" value="${item.salePrice ?? ""}"></td>
          <td><input type="number" min="0" data-lw-item-field="originalPrice" value="${item.originalPrice ?? ""}"></td>
          <td><input type="number" min="0" data-lw-item-field="maximumBuyCount" value="${item.maximumBuyCount ?? ""}"></td>
          <td><input type="text" data-lw-item-field="externalVendorSku" value="${escapeHtml(item.externalVendorSku || "")}"></td>
          <td><input type="number" min="1" data-lw-item-field="unitCount" value="${item.unitCount ?? ""}"></td>
          <td><button type="button" class="btn btn-ghost btn-sm" data-lw-item-remove>${HomezI18n.t("common.delete")}</button></td>
        </tr>`;
      }).join("");

      rowsEl.querySelectorAll("[data-lw-item-field]").forEach((input) => {
        input.addEventListener("input", () => {
          const row = input.closest("tr");
          const idx = Number(row.dataset.lwItemIdx);
          const field = input.dataset.lwItemField;
          const raw = input.value;
          const numericFields = ["salePrice", "originalPrice", "maximumBuyCount", "unitCount"];
          items[idx][field] = numericFields.includes(field)
            ? (raw.trim() === "" ? undefined : Number(raw))
            : raw;
          persist();
          lwScheduleAutosave();
        });
      });
      rowsEl.querySelectorAll("[data-lw-item-remove]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const row = btn.closest("tr");
          const idx = Number(row.dataset.lwItemIdx);
          items.splice(idx, 1);
          persist();
          renderRows();
          lwScheduleAutosave();
        });
      });
    };

    host.querySelector("[data-lw-combo-generate]").addEventListener("click", () => {
      errorEl.textContent = "";
      const axisSelections = {};
      exposedFields.forEach((field) => {
        const name = field.attribute_type_name;
        const axisEl = host.querySelector(`[data-lw-axis-name="${CSS.escape(name)}"]`);
        if (!axisEl) return;
        if (field.input_type === "SELECT" && (field.input_values || []).length) {
          axisSelections[name] = Array.from(
            axisEl.querySelectorAll("input[data-lw-axis-value]:checked"),
          ).map((el) => el.dataset.lwAxisValue);
        } else {
          const csv = axisEl.querySelector("[data-lw-axis-csv]")?.value || "";
          axisSelections[name] = csv.split(",").map((v) => v.trim()).filter(Boolean);
        }
      });
      const axisNames = Object.keys(axisSelections).filter((n) => axisSelections[n].length);
      if (!axisNames.length) {
        errorEl.textContent = HomezI18n.t("lw.combo_no_axis_values_error");
        return;
      }
      let combos = [{}];
      axisNames.forEach((name) => {
        const next = [];
        combos.forEach((combo) => {
          axisSelections[name].forEach((value) => {
            next.push({ ...combo, [name]: value });
          });
        });
        combos = next;
      });
      const existingKeys = new Set(
        items
          .filter((item) => item.optionAttributes)
          .map((item) => JSON.stringify(item.optionAttributes, Object.keys(item.optionAttributes).sort())),
      );
      let added = 0;
      combos.forEach((combo) => {
        const sortedKeys = Object.keys(combo).sort();
        const key = JSON.stringify(combo, sortedKeys);
        if (existingKeys.has(key)) return;
        existingKeys.add(key);
        items.push({
          itemName: Object.values(combo).join(" · "),
          externalVendorSku: "",
          optionAttributes: combo,
        });
        added += 1;
      });
      if (!added) {
        errorEl.textContent = HomezI18n.t("lw.combo_all_exist_notice");
      }
      persist();
      renderRows();
      lwScheduleAutosave();
    });

    host.querySelector("[data-lw-item-add-manual]").addEventListener("click", () => {
      items.push({ itemName: "", externalVendorSku: "" });
      persist();
      renderRows();
      lwScheduleAutosave();
    });

    persist();
    renderRows();
  }

  // ---- CP-2(2026-08-21) — 채널 정책 패널(5단계 채널별 블록에 부착) ----
  // 정책 적합성과 수익성(6단계)은 절대 같은 배지·패널에 섞지 않는다.

  const LW_POLICY_RESULT_CLASS = {
    CHANNEL_ELIGIBLE: "lw-channel-policy-badge-eligible",
    CHANNEL_ELIGIBLE_WITH_ACTIONS: "lw-channel-policy-badge-actions",
    CHANNEL_DATA_REQUIRED: "lw-channel-policy-badge-actions",
    CHANNEL_POLICY_BLOCKED: "lw-channel-policy-badge-blocked",
    CHANNEL_POLICY_STALE: "lw-channel-policy-badge-actions",
  };

  function lwChannelPolicyBadgeHtml(result) {
    const cls = LW_POLICY_RESULT_CLASS[result] || "";
    const label = HomezI18n.t(`lw.channel_policy_result.${result.toLowerCase()}`);
    return `<span class="lw-channel-policy-badge ${cls}">${escapeHtml(label)}</span>`;
  }

  function lwRenderChannelPolicyDetail(detailEl, status) {
    if (!status) {
      detailEl.innerHTML = "";
      return;
    }
    const blocking = (status.rule_results || []).filter(
      (r) => r.applies && !r.satisfied,
    );
    if (!blocking.length) {
      detailEl.innerHTML = `<p class="lw-policy-ok-text">${HomezI18n.t("lw.channel_policy_all_clear")}</p>`;
      return;
    }
    detailEl.innerHTML = `<ul class="lw-policy-issue-list">${blocking.map((r) => `
      <li class="lw-policy-issue" data-severity="${escapeHtml(r.severity)}">
        <span class="lw-policy-issue-code">${escapeHtml(r.rule_code)}</span>
        ${r.missing_fields && r.missing_fields.length ? `<span class="lw-policy-issue-fields">${HomezI18n.t("lw.channel_policy_missing_fields")}: ${r.missing_fields.map(escapeHtml).join(", ")}</span>` : ""}
        ${r.missing_evidence && r.missing_evidence.length ? `<span class="lw-policy-issue-evidence">${r.missing_evidence.map(escapeHtml).join(" · ")}</span>` : ""}
        ${r.official_source_url ? `<a href="${escapeHtml(r.official_source_url)}" target="_blank" rel="noopener noreferrer" class="lw-policy-issue-source">${escapeHtml(r.source_title || r.official_source_url)}</a>` : ""}
      </li>`).join("")}</ul>`;
  }

  async function lwLoadChannelPolicyStatus(block, productCandidateId) {
    const channel = block.dataset.lwChannel;
    const badge = block.querySelector("[data-lw-policy-badge]");
    const detail = block.querySelector("[data-lw-policy-detail]");
    if (!channel || !badge || !productCandidateId) return;

    try {
      const status = await apiFetch(
        `/channel-policy/status/${productCandidateId}/${encodeURIComponent(channel)}`,
      );
      if (!status) {
        badge.innerHTML = HomezI18n.t("lw.channel_policy_not_checked");
        detail.innerHTML = "";
        return;
      }
      badge.innerHTML = lwChannelPolicyBadgeHtml(status.result);
      lwRenderChannelPolicyDetail(detail, status);
    } catch (_err) {
      badge.innerHTML = HomezI18n.t("lw.channel_policy_not_checked");
    }
  }

  async function lwRunChannelPolicyEvaluate(block, productCandidateId, savedSelection = null) {
    const channel = block.dataset.lwChannel;
    const badge = block.querySelector("[data-lw-policy-badge]");
    const detail = block.querySelector("[data-lw-policy-detail]");
    if (!channel || !productCandidateId) return;

    badge.textContent = HomezI18n.t("common.loading");
    try {
      const policyInput = savedSelection ? {
        attributes: savedSelection.channel_policy_attributes || {},
        confirmedRules: savedSelection.channel_policy_confirmed_evidence_rule_codes || [],
      } : lwCollectChannelPolicyInput(block);
      const status = await apiFetch("/channel-policy/evaluate", {
        method: "POST",
        body: JSON.stringify({
          product_candidate_id: productCandidateId,
          channel,
          product_attributes: policyInput.attributes,
          confirmed_evidence_rule_codes: policyInput.confirmedRules,
        }),
      });
      badge.innerHTML = lwChannelPolicyBadgeHtml(status.result);
      lwRenderChannelPolicyDetail(detail, status);
    } catch (err) {
      badge.innerHTML = HomezI18n.t("lw.channel_policy_not_checked");
      detail.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
    }
  }

  // ---- 6단계: 가격·마진 ----

  function lwRenderEconomicsStep(content) {
    const w = lwState.wizard;
    const selections = w.channel_selections || [];
    const inputByAccount = {};
    (w.economics_input || []).forEach((i) => { inputByAccount[i.marketplace_account_id] = i; });
    const resultByAccount = {};
    (w.economics_result || []).forEach((r) => { resultByAccount[r.marketplace_account_id] = r; });

    const fieldDef = [
      ["cost_of_goods", "lw.econ_cost_of_goods"], ["sale_price", "lw.econ_sale_price"],
      ["channel_fee_rate", "lw.econ_channel_fee_rate"], ["payment_fee_rate", "lw.econ_payment_fee_rate"],
      ["shipping_cost", "lw.econ_shipping_cost"], ["packaging_cost", "lw.econ_packaging_cost"],
      ["ad_cost", "lw.econ_ad_cost"], ["return_reserve_rate", "lw.econ_return_reserve_rate"],
      ["tax_basis_rate", "lw.econ_tax_basis_rate"],
    ];

    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.economics")}</h2>
      ${selections.map((sel) => {
        const inp = inputByAccount[sel.marketplace_account_id] || {};
        const res = resultByAccount[sel.marketplace_account_id];
        return `
        <div class="lw-channel-block" data-lw-account="${sel.marketplace_account_id}">
          <strong>${HomezI18n.t("common.account_id_label")} #${sel.marketplace_account_id}</strong>
          <div class="lw-field-grid">
            ${fieldDef.map(([key, i18nKey]) => `
              <label class="field">
                <span class="field-label">${HomezI18n.t(i18nKey)}</span>
                <input type="text" class="lw-econ-input" data-key="${key}" value="${inp[key] ?? "0"}">
              </label>`).join("")}
          </div>
          ${res ? `
          <div class="lw-margin-result">
            ${HomezI18n.t("lw.econ_margin_amount")}: <strong>${escapeHtml(HomezI18n.formatCurrency(res.margin_amount))}</strong> ·
            ${HomezI18n.t("lw.econ_margin_rate")}: <strong>${escapeHtml(HomezI18n.formatPercent(Number(res.margin_rate), 2))}</strong> ·
            ${HomezI18n.t("lw.econ_break_even")}: <strong>${res.break_even_price === null ? HomezI18n.t("lw.econ_break_even_impossible") : escapeHtml(HomezI18n.formatCurrency(res.break_even_price))}</strong>
          </div>` : ""}
        </div>`;
      }).join("")}
      <p class="field-error" id="lw-economics-error"></p>
      <p class="stat-sub">${HomezI18n.t("lw.econ_calc_note")}</p>`;

    lwSaveCurrentStep = async (opts = {}) => {
      const errEl = el("lw-economics-error");
      errEl.textContent = "";
      const blocks = Array.from(content.querySelectorAll(".lw-channel-block"));
      const items = blocks.map((block) => {
        const item = { marketplace_account_id: Number(block.dataset.lwAccount) };
        block.querySelectorAll(".lw-econ-input").forEach((input) => {
          item[input.dataset.key] = input.value.trim() || "0";
        });
        return item;
      });
      try {
        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/economics`, {
          method: "PATCH",
          body: JSON.stringify({
            expected_version: lwState.wizard.version, items,
            autosave_client_token: lwGetAutosaveToken(),
          }),
        });
        if (opts.silent) {
          // 자동 저장은 다음 단계로 넘어가지 않는다 — 계산된 마진
          // 결과만 조용히 반영해 같은 단계에 다시 그린다(사용자가 계속
          // 입력 중일 수 있다).
          lwState.wizard = fresh;
          await lwRenderEconomicsStep(content);
          lwUpdateStepIndicator();
          el("lw-save-status").textContent = HomezI18n.t("lw.autosave_saved", {
            time: HomezI18n.formatDate(lsParseUtc(fresh.autosave_saved_at)),
          });
        } else {
          lwAdvanceAfterSave(fresh);
        }
        return true;
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
        return false;
      }
    };

    lwWireAutosaveInputs(content);
  }

  // ---- 7단계: 사전검사 ----

  // Gate R-1(2026-08-09) — 언어 전환 즉시 재렌더링. 사전검사 결과는
  // lwState.wizard(서버 확정 값)에 저장되지 않는 순수 화면 상태라,
  // 언어만 바뀌어 단계를 다시 그릴 때도 그대로 보여줘야 한다(요구사항
  // 9 "validation summary도 즉시 변경" — 사라지는 게 아니라 같은
  // 내용을 새 언어로). lwStepEphemeralCache에 마지막 결과를 담아
  // 재사용한다 — 서버를 다시 호출하지 않는다(불필요한 재계산 금지).
  function lwRenderPrecheckResult(resultEl, result) {
    const blocking = result.issues.filter((i) => i.blocking);
    if (!result.issues.length) {
      resultEl.innerHTML = `<p class="banner banner-info">${HomezI18n.t("lw.precheck_none_blocking")}</p>`;
    } else {
      resultEl.innerHTML = `<div class="lw-issue-list">${result.issues.map((issue) => {
        // 서버가 새 계약 코드를 추가했는데 두 locale 카탈로그에 아직
        // 문구가 없을 수 있다 — 그런 경우에도 빈 문장 대신 코드+기본
        // 안내가 항상 나오게 한다(2026-08-31, i18n 누락 재발 방지).
        const message = HomezI18n.t(issue.localized_message_key)
          || HomezI18n.t("lw.precheck_unknown_issue_fallback", { code: issue.code });
        return `
        <div class="lw-issue-item ${issue.blocking ? "lw-issue-blocking" : ""}">
          <span>${escapeHtml(message)}</span>
          <button class="lw-issue-jump" data-lw-jump="${issue.step}">${HomezI18n.t("lw.precheck_jump_btn")} →</button>
        </div>`;
      }).join("")}</div>`;
      resultEl.querySelectorAll("[data-lw-jump]").forEach((btn) => {
        btn.addEventListener("click", () => lwJumpToStep(btn.dataset.lwJump));
      });
    }
    return blocking;
  }

  async function lwRenderPrecheckStep(content) {
    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.precheck")}</h2>
      <button class="btn btn-primary" id="lw-run-precheck-btn">${HomezI18n.t("lw.precheck_run_btn")}</button>
      <div id="lw-precheck-result"></div>`;

    if (lwStepEphemeralCache && lwStepEphemeralCache.step === "PRECHECK") {
      lwRenderPrecheckResult(el("lw-precheck-result"), lwStepEphemeralCache.data);
    }

    el("lw-run-precheck-btn").addEventListener("click", () => withButtonGuard(el("lw-run-precheck-btn"), async () => {
      const resultEl = el("lw-precheck-result");
      resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
      try {
        const result = await apiFetch(`/listing-wizards/${lwState.wizard.id}/validate`, {
          method: "POST",
          body: JSON.stringify({ expected_version: lwState.wizard.version }),
        });
        lwState.wizard = await apiFetch(`/listing-wizards/${lwState.wizard.id}`);
        lwUpdateStepIndicator();

        lwStepEphemeralCache = { step: "PRECHECK", data: result };
        const blocking = lwRenderPrecheckResult(resultEl, result);
        if (!blocking.length) {
          toast(HomezI18n.t("lw.precheck_passed_toast"), "success");
        } else {
          toast(HomezI18n.t("lw.precheck_blocked_toast"), "error");
        }
      } catch (err) {
        resultEl.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("lw.save_error"))}</p>`;
      }
    }));

    lwSaveCurrentStep = async () => {
      // 사전검사를 이미 통과한 뒤에만 도달할 수 있는 상태(APPROVED
      // 이후 재방문 등)에서는 validate()가 _assert_editable에 막혀
      // 다시 실행할 수 없다 — 그런 경우까지 "READY_FOR_APPROVAL만
      // 허용"으로 막으면 승인 후 재방문 시 다음 단계로 영원히 진행할
      // 수 없는 막다른 길이 된다(Gate K 실제 Migration 적용 후 Browser
      // E2E에서 재현·발견).
      const PRECHECK_ALREADY_PASSED_STATUSES = [
        "READY_FOR_APPROVAL", "APPROVED", "SUBMITTING",
        "PARTIALLY_SUCCEEDED", "SUCCEEDED", "FAILED",
      ];
      if (!PRECHECK_ALREADY_PASSED_STATUSES.includes(lwState.wizard.status)) {
        el("lw-precheck-result").insertAdjacentHTML(
          "beforeend",
          `<p class="field-error">${HomezI18n.t("lw.precheck_must_pass_first")}</p>`,
        );
        return false;
      }
      const idx = LW_STEPS.indexOf("PRECHECK");
      lwState.viewingStep = LW_STEPS[idx + 1];
      await lwRenderStep();
      return true;
    };
  }

  // ---- 8단계: 승인 ----

  // Gate Q-1(2026-08-09) — "승인 취소 후 수정" 정책. 승인 전(READY_
  // FOR_APPROVAL)에는 기존 승인 흐름을, 승인 후(APPROVED)에는 취소
  // 흐름을, 제출이 이미 시작됐거나 끝난 상태(SUBMITTING 이후)에는
  // 둘 다 잠긴 안내만 보여준다 — 서버의 상태 전이 계약을 화면이
  // 그대로 반영한다(서버가 최종 게이트, 화면은 안내일 뿐).
  const LW_REVOKE_LOCKED_STATUSES = [
    "SUBMITTING", "PARTIALLY_SUCCEEDED", "SUCCEEDED", "FAILED",
  ];

  function lwRenderApprovalHistory(container) {
    const history = lwState.wizard.approval_history || [];
    if (!history.length) return;
    container.insertAdjacentHTML("beforeend", `
      <div class="lw-approval-history">
        <h3>${HomezI18n.t("lw.approval_history_title")}</h3>
        <ul>${history.map((entry) => {
          const label = entry.action === "APPROVAL_REVOKED"
            ? HomezI18n.t("lw.approval_history_revoked_entry", { reason: entry.reason || "" })
            : HomezI18n.t("lw.approval_history_approved_entry");
          return `<li>${escapeHtml(label)} — ${escapeHtml(HomezI18n.formatDate(lsParseUtc(entry.at)))}</li>`;
        }).join("")}</ul>
      </div>`);
  }

  // Gate R-1(2026-08-09) — 승인/취소 미리보기는 nonce를 담고 있다.
  // 언어만 바뀌어 다시 그릴 때 서버를 다시 부르면 nonce·fingerprint가
  // 새로 발급돼 요구사항 6("승인 fingerprint와 payload를 다시 만들지
  // 않는다")을 위반한다 — 그래서 렌더 로직을 재사용 가능한 함수로
  // 분리하고, lwStepEphemeralCache에 담긴 마지막 응답을 그대로
  // 재사용해 다시 그린다(서버 재호출 없음).

  function lwRenderRevokePreviewPanel(panel, preview) {
    panel.innerHTML = `
      <div class="lw-revoke-impact banner banner-warn">
        <strong>${HomezI18n.t("lw.revoke_impact_title")}</strong>
        <ul>
          <li>${HomezI18n.t("lw.revoke_impact_1")}</li>
          <li>${HomezI18n.t("lw.revoke_impact_2")}</li>
          <li>${HomezI18n.t("lw.revoke_impact_3")}</li>
        </ul>
      </div>
      <label class="field">
        <span class="field-label">${HomezI18n.t("lw.revoke_reason_label")}</span>
        <textarea id="lw-revoke-reason" placeholder="${HomezI18n.t("lw.revoke_reason_placeholder")}"></textarea>
      </label>
      <label class="field">
        <span class="field-label">${HomezI18n.t("ma.current_password_label")}</span>
        <input type="password" id="lw-revoke-password" autocomplete="current-password">
      </label>
      <button class="btn btn-danger" id="lw-revoke-confirm-btn" disabled>${HomezI18n.t("lw.revoke_confirm_btn")}</button>
      <p class="field-error" id="lw-revoke-error"></p>`;

    const confirmBtn = el("lw-revoke-confirm-btn");
    const reasonInput = el("lw-revoke-reason");
    reasonInput.addEventListener("input", () => {
      confirmBtn.disabled = !reasonInput.value.trim();
    });

    confirmBtn.addEventListener("click", () => withButtonGuard(confirmBtn, async () => {
      const errEl = el("lw-revoke-error");
      errEl.textContent = "";
      const reason = reasonInput.value.trim();
      const password = el("lw-revoke-password").value;
      if (!reason) {
        errEl.textContent = HomezI18n.t("lw.revoke_error_reason_required");
        return;
      }
      if (!password) {
        errEl.textContent = HomezI18n.t("ma.error_current_password_required");
        return;
      }
      try {
        const recentAuth = await apiFetch("/auth/recent-auth", {
          method: "POST",
          body: JSON.stringify({ current_password: password }),
        }, { skipAuthHandling: true });

        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/revoke-approval`, {
          method: "POST",
          headers: { "X-Recent-Auth-Token": recentAuth.recent_auth_token },
          body: JSON.stringify({
            expected_version: preview.version,
            reason,
            revoke_nonce: preview.revoke_nonce,
          }),
        });
        lwState.wizard = fresh;
        lwStepEphemeralCache = null;
        toast(HomezI18n.t("lw.revoke_success_toast"), "success");
        await lwRenderApprovalStep(el("lw-step-content"));
        lwUpdateStepIndicator();
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
      }
    }));
  }

  // 2026-08-29 쿠팡 상품등록 핵심 차단 해결(Section 5 — 전송 전 검사
  // 화면). 이 데이터는 이미 approval_package에 전부 들어 있었다
  // (build_approval_package()가 channel_selections·economics_result를
  // fingerprint 입력으로 이미 포함) — 이 함수는 새 백엔드 호출 없이
  // 화면에 표시만 추가한다. Credential·주소·전화번호 원문은 표시하지
  // 않는다(출고지·반품지는 코드만 노출).
  function lwBuildSubmissionReviewHtml(pkg) {
    const selections = pkg.channel_selections || [];
    const economics = pkg.economics_result || [];
    if (!selections.length) {
      return `<p class="stat-sub">${HomezI18n.t("lw.submission_review_empty")}</p>`;
    }
    const cards = selections.map((sel) => {
      const fields = sel.required_fields || {};
      const policy = sel.channel_policy_attributes || {};
      const items = Array.isArray(fields.items) ? fields.items : [];
      const purchaseOptions = policy.purchase_options || {};
      const noticeInfo = Object.entries(policy.notice_information || {})
        .filter(([key]) => key !== "__notice_category_name");
      const margin = economics.find(
        (e) => e.marketplace_account_id === sel.marketplace_account_id,
      );

      const itemRows = items.map((item) => `
        <tr>
          <td>${escapeHtml(item.itemName || "—")}</td>
          <td>${escapeHtml(item.externalVendorSku || "—")}</td>
          <td>${item.salePrice != null ? escapeHtml(String(item.salePrice)) : escapeHtml(String(fields.salePrice ?? "—"))}</td>
          <td>${item.originalPrice != null ? escapeHtml(String(item.originalPrice)) : escapeHtml(String(fields.originalPrice ?? "—"))}</td>
          <td>${item.maximumBuyCount != null ? escapeHtml(String(item.maximumBuyCount)) : escapeHtml(String(fields.maximumBuyCount ?? "—"))}</td>
        </tr>`).join("");

      return `
      <div class="lw-submission-review-card">
        <h4>${HomezI18n.t("lw.submission_review_title")} · ${escapeHtml(String(sel.marketplace_account_id))}</h4>
        <dl class="kv-list">
          <dt>${HomezI18n.t("lw.submission_review_category")}</dt>
          <dd>${escapeHtml(policy.official_category_code || fields.displayCategoryCode || "—")}</dd>
          <dt>${HomezI18n.t("lw.submission_review_manufacture")}</dt>
          <dd>${escapeHtml(fields.manufacture || "—")}</dd>
          <dt>${HomezI18n.t("lw.submission_review_origin")}</dt>
          <dd>${escapeHtml(policy.origin_country || "—")}</dd>
        </dl>

        ${Object.keys(purchaseOptions).length ? `
        <p class="field-label">${HomezI18n.t("lw.submission_review_purchase_options")}</p>
        <dl class="kv-list">${Object.entries(purchaseOptions).map(([k, v]) => `
          <dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd>`).join("")}</dl>` : ""}

        ${noticeInfo.length ? `
        <p class="field-label">${HomezI18n.t("lw.submission_review_notices")}</p>
        <dl class="kv-list">${noticeInfo.map(([k, v]) => `
          <dt>${escapeHtml(k.split("::").pop())}</dt><dd>${escapeHtml(String(v))}</dd>`).join("")}</dl>` : ""}

        ${items.length ? `
        <p class="field-label">${HomezI18n.t("lw.submission_review_items_title")}</p>
        <div class="lw-table-scroll"><table class="lw-table">
          <thead><tr>
            <th>${HomezI18n.t("lw.draft_name_label")}</th>
            <th>${HomezI18n.t("lw.submission_review_item_sku")}</th>
            <th>${HomezI18n.t("lw.submission_review_item_sale_price")}</th>
            <th>${HomezI18n.t("lw.submission_review_item_original_price")}</th>
            <th>${HomezI18n.t("lw.submission_review_item_stock")}</th>
          </tr></thead>
          <tbody>${itemRows}</tbody>
        </table></div>` : ""}

        <p class="field-label">${HomezI18n.t("lw.submission_review_shipping_title")}</p>
        <dl class="kv-list">
          <dt>${HomezI18n.t("lw.submission_review_outbound_code")}</dt><dd>${escapeHtml(sel.outbound_shipping_place_code || "—")}</dd>
          <dt>${HomezI18n.t("lw.submission_review_return_code")}</dt><dd>${escapeHtml(sel.return_center_code || "—")}</dd>
          <dt>${HomezI18n.t("lw.submission_review_delivery_charge")}</dt><dd>${escapeHtml(String(fields.deliveryCharge ?? "—"))}</dd>
          <dt>${HomezI18n.t("lw.submission_review_return_charge")}</dt><dd>${escapeHtml(String(fields.returnCharge ?? "—"))}</dd>
        </dl>

        ${margin ? `
        <p class="field-label">${HomezI18n.t("lw.submission_review_margin_title")}</p>
        <dl class="kv-list">
          <dt>${HomezI18n.t("lw.submission_review_expected_revenue")}</dt><dd>${escapeHtml(String(margin.expected_revenue))}</dd>
          <dt>${HomezI18n.t("lw.submission_review_margin_amount")}</dt><dd>${escapeHtml(String(margin.margin_amount))}</dd>
          <dt>${HomezI18n.t("lw.submission_review_margin_rate")}</dt><dd>${escapeHtml(String(margin.margin_rate))}</dd>
        </dl>` : ""}

        <details class="lw-advanced-fields">
          <summary>${HomezI18n.t("lw.submission_review_raw_json")}</summary>
          <pre class="lw-raw-json">${escapeHtml(JSON.stringify(sel, null, 2))}</pre>
        </details>
      </div>`;
    }).join("");

    return `
      <p class="lw-long-image-policy-title">${HomezI18n.t("lw.submission_review_title")}</p>
      <p class="stat-sub">${HomezI18n.t("lw.submission_review_precheck_status")}: ${escapeHtml(pkg.precheck_status || "—")}</p>
      ${cards}`;
  }

  async function lwRenderApprovalPreviewPanel(panel, preview) {
    // 2026-08-20 3차 지시(작업 3) — 시스템은 용량·향·구성의 정확성을
    // 자동으로 확인할 수 없다는 것을 명시하고, 등록 상품 정보와
    // 선택 이미지 썸네일을 나란히 보여준 뒤, 사용자가 "이미지가
    // 등록 상품과 일치함"을 직접 체크해야만 승인 버튼이 활성화된다
    // (서버도 동일 게이트를 다시 검증한다 — listing_wizard_service.py
    // ::approve()의 PRODUCT_IMAGE_MATCH_NOT_CONFIRMED). 미확인 정보를
    // AI가 대신 채우지 않는다 — draft에 실제로 저장된 값만 그대로
    // 보여준다.
    const draft = preview.approval_package.draft || {};
    const mediaEntries = preview.approval_package.media || [];

    panel.innerHTML = `
      <p><strong>${HomezI18n.t("lw.approval_fingerprint_label")}:</strong> <code>${escapeHtml(preview.fingerprint)}</code></p>

      <p class="banner banner-warn">${HomezI18n.t("lw.approval_match_banner")}</p>

      <p class="lw-long-image-policy-title">${HomezI18n.t("lw.approval_match_product_title")}</p>
      <dl class="kv-list">
        <dt>${HomezI18n.t("lw.draft_name_label")}</dt><dd>${escapeHtml(draft.product_name || "—")}</dd>
        <dt>${HomezI18n.t("lw.draft_brand_label")}</dt><dd>${escapeHtml(draft.brand || "—")}</dd>
        <dt>${HomezI18n.t("lw.draft_category_label")}</dt><dd>${escapeHtml(draft.category || "—")}</dd>
        <dt>${HomezI18n.t("lw.draft_description_label")}</dt><dd>${escapeHtml(draft.description || "—")}</dd>
      </dl>
      ${(draft.options || []).length ? `<div class="lw-issue-list">${(draft.options || []).map((o) => `
        <div class="lw-issue-item"><strong>${escapeHtml(o.name)}</strong>: ${(o.values || []).map(escapeHtml).join(", ")}</div>`).join("")}</div>` : ""}

      ${lwBuildSubmissionReviewHtml(preview.approval_package)}

      <p class="lw-long-image-policy-title">${HomezI18n.t("lw.approval_match_images_title")}</p>
      <div class="lw-approval-match-grid" id="lw-approval-match-thumbs">${mediaEntries.map((m) => `
        <div class="lw-approval-match-thumb" id="lw-approval-thumb-${m.media_asset_id}"></div>`).join("")}</div>

      <label class="lw-checklist-item">
        <input type="checkbox" id="lw-approval-match-confirm">
        <span>${HomezI18n.t("lw.approval_match_confirm_label")}</span>
      </label>

      <label class="field">
        <span class="field-label">${HomezI18n.t("ma.current_password_label")}</span>
        <input type="password" id="lw-approve-password" autocomplete="current-password">
      </label>
      <button class="btn btn-primary" id="lw-approve-btn" disabled>${HomezI18n.t("lw.approval_approve_btn")}</button>
      <p class="field-error" id="lw-approval-error"></p>`;

    mediaEntries.forEach((m) => {
      lwLoadThumbUrl(m.media_asset_id).then((url) => {
        const thumbEl = el(`lw-approval-thumb-${m.media_asset_id}`);
        if (thumbEl) thumbEl.innerHTML = `<img src="${url}" alt="">`;
      }).catch(() => {});
    });

    const approveBtn = el("lw-approve-btn");
    el("lw-approval-match-confirm").addEventListener("change", (ev) => {
      approveBtn.disabled = !ev.target.checked;
    });

    approveBtn.addEventListener("click", () => withButtonGuard(el("lw-approve-btn"), async () => {
      const errEl = el("lw-approval-error");
      errEl.textContent = "";
      const matchConfirmed = el("lw-approval-match-confirm").checked;
      if (!matchConfirmed) {
        errEl.textContent = HomezI18n.t("lw.approval_match_required_error");
        return;
      }
      const password = el("lw-approve-password").value;
      if (!password) {
        errEl.textContent = HomezI18n.t("ma.error_current_password_required");
        return;
      }
      try {
        const recentAuth = await apiFetch("/auth/recent-auth", {
          method: "POST",
          body: JSON.stringify({ current_password: password }),
        }, { skipAuthHandling: true });

        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/approve`, {
          method: "POST",
          headers: { "X-Recent-Auth-Token": recentAuth.recent_auth_token },
          body: JSON.stringify({
            expected_version: preview.version,
            approval_nonce: preview.approval_nonce,
            expected_fingerprint: preview.fingerprint,
            product_image_match_confirmed: matchConfirmed,
          }),
        });
        lwStepEphemeralCache = null;
        lwAdvanceAfterSave(fresh);
        toast(HomezI18n.t("lw.approval_success_toast"), "success");
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
      }
    }));
  }

  function lwRenderApprovalStep(content) {
    lwSaveCurrentStep = null;
    const status = lwState.wizard.status;

    if (status === "APPROVED") {
      content.innerHTML = `
        <h2>${HomezI18n.t("lw.step.approval")}</h2>
        <p class="banner banner-info">${HomezI18n.t("lw.approval_already_approved_banner")}</p>
        <p><strong>${HomezI18n.t("lw.approval_fingerprint_label")}:</strong> <code>${escapeHtml(lwState.wizard.approval_fingerprint || "")}</code></p>
        <p><strong>${HomezI18n.t("lw.approval_approved_at_label")}:</strong> ${lwState.wizard.approved_at ? escapeHtml(HomezI18n.formatDate(lsParseUtc(lwState.wizard.approved_at))) : "—"}</p>
        <button class="btn btn-secondary" id="lw-revoke-load-preview-btn">${HomezI18n.t("lw.revoke_load_preview_btn")}</button>
        <div id="lw-revoke-panel"></div>
        <div id="lw-approval-history-panel"></div>`;
      lwRenderApprovalHistory(el("lw-approval-history-panel"));

      if (lwStepEphemeralCache && lwStepEphemeralCache.step === "APPROVAL"
        && lwStepEphemeralCache.kind === "revoke_preview") {
        lwRenderRevokePreviewPanel(el("lw-revoke-panel"), lwStepEphemeralCache.data);
      }

      el("lw-revoke-load-preview-btn").addEventListener("click", () => withButtonGuard(el("lw-revoke-load-preview-btn"), async () => {
        const panel = el("lw-revoke-panel");
        panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
        try {
          const preview = await apiFetch(`/listing-wizards/${lwState.wizard.id}/revoke-approval-preview`);
          lwStepEphemeralCache = { step: "APPROVAL", kind: "revoke_preview", data: preview };
          lwRenderRevokePreviewPanel(panel, preview);
        } catch (err) {
          panel.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
        }
      }));
      return;
    }

    if (LW_REVOKE_LOCKED_STATUSES.includes(status)) {
      content.innerHTML = `
        <h2>${HomezI18n.t("lw.step.approval")}</h2>
        <p class="banner banner-info">${HomezI18n.t("lw.approval_locked_banner")}</p>
        <div id="lw-approval-history-panel"></div>`;
      lwRenderApprovalHistory(el("lw-approval-history-panel"));
      return;
    }

    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.approval")}</h2>
      <button class="btn btn-secondary" id="lw-load-preview-btn">${HomezI18n.t("lw.approval_preview_btn")}</button>
      <div id="lw-approval-preview-panel"></div>
      <div id="lw-approval-history-panel"></div>`;
    lwRenderApprovalHistory(el("lw-approval-history-panel"));

    if (lwStepEphemeralCache && lwStepEphemeralCache.step === "APPROVAL"
      && lwStepEphemeralCache.kind === "approval_preview") {
      lwRenderApprovalPreviewPanel(el("lw-approval-preview-panel"), lwStepEphemeralCache.data);
    }

    el("lw-load-preview-btn").addEventListener("click", () => withButtonGuard(el("lw-load-preview-btn"), async () => {
      const panel = el("lw-approval-preview-panel");
      panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
      try {
        const preview = await apiFetch(`/listing-wizards/${lwState.wizard.id}/approval-preview`);
        lwStepEphemeralCache = { step: "APPROVAL", kind: "approval_preview", data: preview };
        lwRenderApprovalPreviewPanel(panel, preview);
      } catch (err) {
        panel.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
      }
    }));
  }

  // ---- 9단계: 실행 선택 ----

  function lwRenderExecutionStep(content) {
    content.innerHTML = `
      <h2>${HomezI18n.t("lw.step.execution")}</h2>
      <button class="btn btn-secondary" id="lw-draft-save-btn">${HomezI18n.t("lw.execution_draft_btn")}</button>
      <button class="btn btn-primary" id="lw-submit-btn">${HomezI18n.t("lw.execution_submit_btn")}</button>
      <p class="field-error" id="lw-execution-error"></p>`;
    lwSaveCurrentStep = null;

    const run = async (mode) => {
      const errEl = el("lw-execution-error");
      errEl.textContent = "";
      try {
        const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/submit`, {
          method: "POST",
          body: JSON.stringify({
            expected_version: lwState.wizard.version, execution_mode: mode,
          }),
        });
        if (mode === "SUBMIT") {
          lwAdvanceAfterSave(fresh);
        } else {
          lwState.wizard = fresh;
          toast(HomezI18n.t("lw.execution_draft_saved_toast"), "success");
        }
      } catch (err) {
        lwApplyConflictOrError(err, errEl);
      }
    };

    el("lw-draft-save-btn").addEventListener("click", () => withButtonGuard(el("lw-draft-save-btn"), () => run("DRAFT_SAVE")));
    el("lw-submit-btn").addEventListener("click", () => withButtonGuard(el("lw-submit-btn"), () => run("SUBMIT")));
  }

  // ---- 10단계: 결과 ----

  const LW_LIVE_BLOCKER_KEYS = {
    SUBMISSION_NOT_PENDING: "lw.live_blocker_not_pending",
    ALREADY_SUBMITTED: "lw.live_blocker_already_submitted",
    SUBMISSION_ALREADY_CLAIMED: "lw.live_blocker_already_claimed",
    ESTOP_ACTIVE: "lw.live_blocker_estop",
    OPERATOR_APPROVAL_MODE_REQUIRED: "lw.live_blocker_mode",
    VALID_APPROVAL_REQUIRED: "lw.live_blocker_approval",
    APPROVED_PAYLOAD_CHANGED: "lw.live_blocker_payload_changed",
    CHANNEL_SELECTION_REQUIRED: "lw.live_blocker_selection",
    PUBLIC_IMAGE_URL_REQUIRED: "lw.live_blocker_public_image",
    REPRESENTATION_IMAGE_REQUIRED: "lw.live_blocker_representation_image",
    IMAGE_RIGHTS_CONFIRMATION_REQUIRED: "lw.live_blocker_image_rights",
    DISPLAY_CATEGORY_CODE_REQUIRED: "lw.live_blocker_category",
    NOTICE_INFORMATION_REQUIRED: "lw.live_blocker_notice",
    ITEM_REQUIRED: "lw.live_blocker_item",
    PRODUCT_NAME_REQUIRED: "lw.live_blocker_product_name",
  };

  function lwLiveStatusLabel(status) {
    const key = {
      PENDING: "lw.live_status_prepared",
      SUBMITTING: "lw.live_status_submitting",
      SUBMITTED: "lw.live_status_submitted",
      FAILED: "lw.live_status_failed",
      UNKNOWN: "lw.live_status_unknown",
    }[status];
    return key ? HomezI18n.t(key) : status;
  }

  function lwLiveBlockerText(code) {
    const key = LW_LIVE_BLOCKER_KEYS[code];
    return key ? HomezI18n.t(key) : code;
  }

  // 2026-08-31 V7 필수 작업 2번(제출 장부 정합화) — PENDING/SUBMITTING/
  // UNKNOWN 제출에 대해 운영자가 실제 쿠팡 WING 화면에서 확인한
  // sellerProductId를 입력해 미리보기(dry-run) 후에만 적용할 수 있는
  // 검토 패널. 기본은 접혀 있다(기존 화면을 어지럽히지 않는다).
  function lwRenderReconciliationBlock(submissionId) {
    return `
      <button class="btn btn-ghost btn-sm lw-recon-toggle-btn"
        data-submission-id="${submissionId}">${HomezI18n.t("lw.reconciliation_toggle_btn")}</button>
      <div class="lw-recon-panel" data-submission-id="${submissionId}" style="display:none">
        <input type="text" class="lw-recon-seller-product-id"
          data-submission-id="${submissionId}"
          placeholder="${HomezI18n.t("lw.reconciliation_seller_product_id_placeholder")}">
        <button class="btn btn-secondary btn-sm lw-recon-preview-btn"
          data-submission-id="${submissionId}">${HomezI18n.t("lw.reconciliation_preview_btn")}</button>
        <div class="field-hint lw-recon-preview-result" data-submission-id="${submissionId}"></div>
        <textarea class="lw-recon-reason" data-submission-id="${submissionId}"
          placeholder="${HomezI18n.t("lw.reconciliation_reason_placeholder")}"></textarea>
        <button class="btn btn-primary btn-sm lw-recon-apply-btn"
          data-submission-id="${submissionId}" disabled>${HomezI18n.t("lw.reconciliation_apply_btn")}</button>
        <div class="field-hint lw-recon-apply-result" data-submission-id="${submissionId}"></div>
      </div>`;
  }

  const LW_RECONCILIATION_READY_OUTCOMES = new Set([
    "READY_AUTO_ELIGIBLE", "READY_MANUAL_REVIEW",
  ]);

  function lwReconciliationOutcomeText(outcome) {
    const key = `lw.reconciliation_outcome.${String(outcome || "").toLowerCase()}`;
    const translated = HomezI18n.t(key);
    return translated === key
      ? `${HomezI18n.t("lw.reconciliation_outcome_fallback")}: ${outcome}`
      : translated;
  }

  function lwReconciliationFieldText(field) {
    const key = `lw.reconciliation_field.${field}`;
    const translated = HomezI18n.t(key);
    return translated === key ? field : translated;
  }

  function lwRenderReconciliationAssessment(assessment) {
    const parts = [lwReconciliationOutcomeText(assessment.outcome)];
    if (assessment.matched_fields && assessment.matched_fields.length) {
      parts.push(
        `${HomezI18n.t("lw.reconciliation_matched_label")}: `
        + assessment.matched_fields.map(lwReconciliationFieldText).join(", "),
      );
    }
    if (assessment.mismatched_fields && assessment.mismatched_fields.length) {
      parts.push(
        `${HomezI18n.t("lw.reconciliation_mismatched_label")}: `
        + assessment.mismatched_fields.map(lwReconciliationFieldText).join(", "),
      );
    }
    if (assessment.unavailable_fields && assessment.unavailable_fields.length) {
      parts.push(
        `${HomezI18n.t("lw.reconciliation_unavailable_label")}: `
        + assessment.unavailable_fields.map(lwReconciliationFieldText).join(", "),
      );
    }
    if (assessment.detail) parts.push(assessment.detail);
    return parts.join(" · ");
  }

  async function lwRenderResultsStep(content) {
    content.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    lwSaveCurrentStep = null;

    try {
      const results = await apiFetch(`/listing-wizards/${lwState.wizard.id}/results`);
      content.innerHTML = `
        <h2>${HomezI18n.t("lw.step.results")}</h2>
        <div class="table-wrap">
          <table><thead><tr>
            <th>${HomezI18n.t("common.account_id_label")}</th>
            <th>${HomezI18n.t("lw.list_col_status")}</th>
            <th>Listing ID</th>
            <th>${HomezI18n.t("lw.results_error_reason_col")}</th>
            <th>${HomezI18n.t("lw.live_action_col")}</th>
          </tr></thead>
          <tbody>${results.channels.map((c) => `
            <tr>
              <td>${c.marketplace_account_id}</td>
              <td><span class="pill">${escapeHtml(
                c.status === "SUBMITTED" && c.provider_warning_summary
                  ? HomezI18n.t("lw.live_status_submitted_with_warning")
                  : lwLiveStatusLabel(c.status)
              )}</span>${c.status === "SUBMITTED" && c.provider_warning_summary
                ? `<div class="field-hint">${escapeHtml(c.provider_warning_summary)}</div>`
                : ""}</td>
              <td>${c.listing_id ?? "—"}</td>
              <td>${escapeHtml(c.error_reason || "—")}</td>
              <td>${c.status === "PENDING" && c.submission_id
                ? `<button class="btn btn-secondary btn-sm lw-live-check-btn"
                     data-submission-id="${c.submission_id}">${HomezI18n.t("lw.live_preflight_btn")}</button>
                   <button class="btn btn-primary btn-sm lw-live-send-btn"
                     data-submission-id="${c.submission_id}" disabled>${HomezI18n.t("lw.live_send_btn")}</button>
                   <div class="field-hint lw-live-message" data-submission-id="${c.submission_id}"></div>`
                : ""}${c.status === "SUBMITTED" && c.submission_id
                ? `<button class="btn btn-secondary btn-sm lw-live-status-btn"
                     data-submission-id="${c.submission_id}">${HomezI18n.t("lw.live_status_check_btn")}</button>
                   <div class="field-hint lw-live-status-message" data-submission-id="${c.submission_id}"></div>`
                : ""}${
                (c.status === "PENDING" || c.status === "SUBMITTING" || c.status === "UNKNOWN") && c.submission_id
                ? lwRenderReconciliationBlock(c.submission_id)
                : ""
              }${!c.submission_id ? "—" : ""}</td>
            </tr>`).join("")}</tbody>
          </table>
        </div>
        ${results.status === "PARTIALLY_SUCCEEDED" || results.status === "FAILED"
          ? `<button class="btn btn-primary" id="lw-retry-failed-btn">${HomezI18n.t("lw.results_retry_btn")}</button>`
          : ""}
        <p class="field-error" id="lw-results-error"></p>`;

      const retryBtn = el("lw-retry-failed-btn");
      if (retryBtn) {
        retryBtn.addEventListener("click", () => withButtonGuard(retryBtn, async () => {
          try {
            const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/retry-failed`, {
              method: "POST",
            });
            lwState.wizard = fresh;
            await lwRenderResultsStep(content);
            lwUpdateStepIndicator();
          } catch (err) {
            el("lw-results-error").textContent = err.message || HomezI18n.t("lw.save_error");
          }
        }));
      }

      content.querySelectorAll(".lw-live-check-btn").forEach((button) => {
        button.addEventListener("click", () => withButtonGuard(button, async () => {
          const submissionId = button.dataset.submissionId;
          const message = content.querySelector(
            `.lw-live-message[data-submission-id="${submissionId}"]`,
          );
          const sendButton = content.querySelector(
            `.lw-live-send-btn[data-submission-id="${submissionId}"]`,
          );
          try {
            const preflight = await apiFetch(
              `/listing-wizards/${lwState.wizard.id}/submissions/${submissionId}/live-preflight`,
            );
            sendButton.disabled = !preflight.ready;
            message.textContent = preflight.ready
              ? HomezI18n.t("lw.live_preflight_ready")
              : preflight.blockers.map(lwLiveBlockerText).join(" · ");
            message.classList.toggle("field-error", !preflight.ready);
          } catch (err) {
            sendButton.disabled = true;
            message.textContent = err.message || HomezI18n.t("lw.live_preflight_failed");
            message.classList.add("field-error");
          }
        }));
      });

      content.querySelectorAll(".lw-live-send-btn").forEach((button) => {
        button.addEventListener("click", () => withButtonGuard(button, async () => {
          if (!window.confirm(HomezI18n.t("lw.live_send_confirm"))) return;
          const recentAuthToken = await promptRecentAuthToken();
          if (recentAuthToken === null) return;
          const submissionId = button.dataset.submissionId;
          try {
            await apiFetch(
              `/listing-wizards/${lwState.wizard.id}/submissions/${submissionId}/send-live`,
              {
                method: "POST",
                headers: { "X-Recent-Auth-Token": recentAuthToken },
              },
            );
            await lwRenderResultsStep(content);
          } catch (err) {
            const message = content.querySelector(
              `.lw-live-message[data-submission-id="${submissionId}"]`,
            );
            if (message) {
              message.textContent = err.message || HomezI18n.t("lw.live_send_failed");
              message.classList.add("field-error");
            }
          }
        }));
      });

      // 2026-08-29 쿠팡 상품등록 핵심 차단 해결(V7-COUPANG-STATUS-001
      // UI 연결) — Provider·Service·Router는 이미 있었지만 실제
      // 업무 흐름에 연결된 버튼이 없었다(감사에서 확인된 결함). 알 수
      // 없는 상태를 성공으로 표시하지 않는다(outcome !== "FOUND"는
      // 항상 경고 스타일로 보여준다).
      content.querySelectorAll(".lw-live-status-btn").forEach((button) => {
        button.addEventListener("click", () => withButtonGuard(button, async () => {
          const submissionId = button.dataset.submissionId;
          const message = content.querySelector(
            `.lw-live-status-message[data-submission-id="${submissionId}"]`,
          );
          message.textContent = HomezI18n.t("common.loading");
          message.classList.remove("field-error");
          try {
            const status = await apiFetch(
              `/listing-wizards/${lwState.wizard.id}/submissions/${submissionId}/live-status`,
            );
            if (status.outcome === "FOUND" && status.status_name) {
              message.textContent = HomezI18n.t(
                `lw.live_status_name.${status.status_name.toLowerCase()}`,
              );
              message.classList.toggle(
                "field-error",
                status.status_name === "DENIED",
              );
            } else {
              message.textContent = status.error_summary
                || HomezI18n.t("lw.live_product_status_unknown");
              message.classList.add("field-error");
            }
          } catch (err) {
            message.textContent = err.message || HomezI18n.t("lw.live_status_check_failed");
            message.classList.add("field-error");
          }
        }));
      });

      // 2026-08-31 V7 필수 작업 2번(제출 장부 정합화) — 아래 세 버튼은
      // 항상 이 순서로만 동작한다: 검토 패널 펼치기 → 미리보기(dry-
      // run, 아무것도 쓰지 않음) → (BLOCKED가 아닐 때만) 적용. 적용은
      // 서버가 SuperAdminGuard로 별도 통제하므로, 이 화면에서 버튼을
      // 누를 수 있다는 사실 자체가 승인을 의미하지 않는다 — 서버가
      // 권한 없는 계정의 적용 요청을 거부한다.
      content.querySelectorAll(".lw-recon-toggle-btn").forEach((button) => {
        button.addEventListener("click", () => {
          const submissionId = button.dataset.submissionId;
          const panel = content.querySelector(
            `.lw-recon-panel[data-submission-id="${submissionId}"]`,
          );
          panel.style.display = panel.style.display === "none" ? "" : "none";
        });
      });

      content.querySelectorAll(".lw-recon-preview-btn").forEach((button) => {
        button.addEventListener("click", () => withButtonGuard(button, async () => {
          const submissionId = button.dataset.submissionId;
          const input = content.querySelector(
            `.lw-recon-seller-product-id[data-submission-id="${submissionId}"]`,
          );
          const resultBox = content.querySelector(
            `.lw-recon-preview-result[data-submission-id="${submissionId}"]`,
          );
          const applyButton = content.querySelector(
            `.lw-recon-apply-btn[data-submission-id="${submissionId}"]`,
          );
          const sellerProductId = (input.value || "").trim();
          if (!sellerProductId) {
            resultBox.textContent = HomezI18n.t("lw.reconciliation_seller_product_id_required");
            resultBox.classList.add("field-error");
            return;
          }
          resultBox.textContent = HomezI18n.t("common.loading");
          resultBox.classList.remove("field-error");
          applyButton.disabled = true;
          try {
            const assessment = await apiFetch(
              `/listing-wizards/reconciliation/submissions/${submissionId}/preview`,
              {
                method: "POST",
                body: JSON.stringify({
                  operator_confirmed_seller_product_id: sellerProductId,
                }),
              },
            );
            resultBox.textContent = lwRenderReconciliationAssessment(assessment);
            const ready = LW_RECONCILIATION_READY_OUTCOMES.has(assessment.outcome);
            resultBox.classList.toggle("field-error", !ready);
            applyButton.disabled = !ready;
          } catch (err) {
            resultBox.textContent = err.message || HomezI18n.t("lw.reconciliation_preview_failed");
            resultBox.classList.add("field-error");
          }
        }));
      });

      content.querySelectorAll(".lw-recon-apply-btn").forEach((button) => {
        button.addEventListener("click", () => withButtonGuard(button, async () => {
          if (!window.confirm(HomezI18n.t("lw.reconciliation_apply_confirm"))) return;
          const submissionId = button.dataset.submissionId;
          const input = content.querySelector(
            `.lw-recon-seller-product-id[data-submission-id="${submissionId}"]`,
          );
          const reasonInput = content.querySelector(
            `.lw-recon-reason[data-submission-id="${submissionId}"]`,
          );
          const applyResult = content.querySelector(
            `.lw-recon-apply-result[data-submission-id="${submissionId}"]`,
          );
          const reason = (reasonInput.value || "").trim();
          if (!reason) {
            applyResult.textContent = HomezI18n.t("lw.reconciliation_reason_required");
            applyResult.classList.add("field-error");
            return;
          }
          try {
            const result = await apiFetch(
              `/listing-wizards/reconciliation/submissions/${submissionId}/apply`,
              {
                method: "POST",
                body: JSON.stringify({
                  operator_confirmed_seller_product_id: (input.value || "").trim(),
                  reason,
                }),
              },
            );
            applyResult.textContent = lwReconciliationOutcomeText(result.outcome);
            applyResult.classList.toggle("field-error", result.outcome !== "RECONCILED");
            if (result.outcome === "RECONCILED") {
              await lwRenderResultsStep(content);
            }
          } catch (err) {
            applyResult.textContent = err.message || HomezI18n.t("lw.reconciliation_apply_failed");
            applyResult.classList.add("field-error");
          }
        }));
      });
    } catch (err) {
      content.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
    }
  }

  // --------------------------------------------------
  // AI 상품 등록(listing-package) — 상품 초안·이미지 통합 생성.
  //
  // 이미지 생성 자체는 별도 승인이 없다(요청 원문: 이미지 생성만을
  // 위한 중복 승인 금지) — 최종 "선택 채널에 일괄 등록" 전 승인
  // 1회만 서버에 요청한다. 이 화면의 제출 버튼은 항상 dry-run이다
  // (서버가 400/403으로 실제 제출을 차단한다 — 이 화면은 그 차단이
  // 실제로 동작하는지 그대로 보여준다).
  // --------------------------------------------------

  let lpState = { packageId: null, fingerprint: null };

  function lpAddChannelRow(channelCode, fulfillmentMode) {
    const container = el("lp-channel-rows");
    const row = document.createElement("div");
    row.className = "lp-channel-row";
    row.innerHTML = `
      <input type="text" class="lp-channel-code" placeholder="${HomezI18n.t("listing_package.channel_code_placeholder")}" value="${channelCode || ""}">
      <input type="text" class="lp-fulfillment-mode" placeholder="${HomezI18n.t("listing_package.fulfillment_mode_placeholder")}" value="${fulfillmentMode || ""}">
      <button type="button" class="btn btn-ghost btn-sm lp-remove-row-btn">${HomezI18n.t("listing_package.remove_row_btn")}</button>
    `;
    row.querySelector(".lp-remove-row-btn").addEventListener("click", () => row.remove());
    container.appendChild(row);
  }

  function lpCollectChannelSelections() {
    return Array.from(document.querySelectorAll("#lp-channel-rows .lp-channel-row"))
      .map((row) => ({
        channel_code: row.querySelector(".lp-channel-code").value.trim(),
        fulfillment_mode: row.querySelector(".lp-fulfillment-mode").value.trim(),
      }))
      .filter((c) => c.channel_code && c.fulfillment_mode);
  }

  // --------------------------------------------------
  // 채널별 등록 현황(플랫폼 상태 동기화, 2026-08-05 Phase 4 CTO 반려
  // 반영 item 6) — 새로고침/재시도/필터/검색/CSV/이력.
  //
  // 네트워크 오류 처리: apiFetch가 이미 401(SESSION_REVOKED 등 허용된
  // 코드)에서만 로그인 화면으로 전환하고, 네트워크 단절(status 0)은
  // 절대 강제 전환하지 않는다 — 이 화면은 그 공용 계약을 그대로
  // 따르고, 실패해도 현재 검색/필터 상태(lsState)를 지운 적이 없으므로
  // 재시도 버튼만 눌러도 같은 조건으로 복구된다(별도 상태 저장 불필요
  // — 필터 입력 필드 자체가 항상 최신 상태를 들고 있다).
  // --------------------------------------------------

  const lsState = { rows: [], historyListingId: null, loading: false };

  // 2026-08-05 CTO 재검증 지시 Gate E — 세션(탭) 수준 필터 상태 보존.
  // sessionStorage를 쓴다(localStorage 아님) — 다른 탭/다음 실행까지
  // 넘어가지 않고 이번 세션 안에서만 새로고침·화면 이동 후에도 필터가
  // 유지된다. 민감정보(회사 데이터 자체)는 저장하지 않는다 — 필터
  // "조건"(채널 코드, 상태 문자열, 검색어, 날짜, 정렬 순서)만 저장한다.
  const LS_FILTER_STORAGE_KEY = "homez_ls_filters_v1";

  const LS_STATUS_PILL_CLASS = {
    ACTIVE: "ok", PROCESSING: "ok", SUBMITTED: "ok", APPROVED: "ok",
    QUEUED: "verifying", SUBMITTING: "verifying", PENDING_APPROVAL: "verifying",
    PAUSED: "warn", DRAFT: "neutral", UNKNOWN: "neutral",
    REJECTED: "danger", FAILED: "danger", VALIDATION_FAILED: "danger",
    ENDED: "danger",
  };

  function lsStatusPillClass(status) {
    return LS_STATUS_PILL_CLASS[status] || "neutral";
  }

  function lsStatusLabel(status) {
    const key = `ls.status.${String(status).toLowerCase()}`;
    const label = HomezI18n.t(key);
    return label === key ? status : label;
  }

  function lsSaveFilterState() {
    try {
      sessionStorage.setItem(LS_FILTER_STORAGE_KEY, JSON.stringify({
        channel: el("ls-filter-channel").value,
        status: el("ls-filter-status").value,
        search: el("ls-filter-search").value,
        errorType: el("ls-filter-error-type").value,
        updatedFrom: el("ls-filter-updated-from").value,
        updatedTo: el("ls-filter-updated-to").value,
        sort: el("ls-filter-sort").value,
      }));
    } catch (_) {
      /* sessionStorage 접근 실패(사생활 보호 모드 등) — 필터 복원만
         안 될 뿐 화면 동작 자체에는 영향이 없다. */
    }
  }

  function lsRestoreBasicFilterState() {
    // channel/errorType은 각각 채널 목록·오류 유형 목록이 채워진
    // 뒤에만 안전하게 복원할 수 있어 이 함수에서 다루지 않는다
    // (loadListingStatusSyncView/lsLoadTable에서 별도로 복원한다).
    let saved = null;
    try {
      const raw = sessionStorage.getItem(LS_FILTER_STORAGE_KEY);
      saved = raw ? JSON.parse(raw) : null;
    } catch (_) {
      saved = null;
    }
    el("ls-filter-status").value = (saved && saved.status) || "";
    el("ls-filter-search").value = (saved && saved.search) || "";
    el("ls-filter-updated-from").value = (saved && saved.updatedFrom) || "";
    el("ls-filter-updated-to").value = (saved && saved.updatedTo) || "";
    el("ls-filter-sort").value = (saved && saved.sort) || "updated_desc";
    return saved;
  }

  async function loadListingStatusSyncView() {
    el("ls-filter-error").textContent = "";
    el("ls-history-panel").hidden = true;
    lsState.historyListingId = null;

    const saved = lsRestoreBasicFilterState();

    const channelSelect = el("ls-filter-channel");
    channelSelect.innerHTML = `<option value="">${HomezI18n.t("common.all")}</option>`;
    try {
      const channels = await apiFetch("/marketplace-listings/channels");
      channels.forEach((ch) => {
        const opt = document.createElement("option");
        opt.value = ch.code;
        opt.textContent = ch.name || ch.code;
        channelSelect.appendChild(opt);
      });
    } catch (_) {
      /* 채널 목록 실패는 "전체"만으로도 화면이 계속 동작해야 한다. */
    }
    if (saved && saved.channel) {
      channelSelect.value = saved.channel;
    }

    await lsLoadTable();

    // 오류 유형 옵션은 방금 lsLoadTable()이 실제 데이터로 채웠으니,
    // 그 뒤에만 저장된 값이 유효한지 확인하고 복원한다.
    if (saved && saved.errorType) {
      const errSelect = el("ls-filter-error-type");
      if (Array.from(errSelect.options).some((o) => o.value === saved.errorType)) {
        errSelect.value = saved.errorType;
        lsRenderFilteredTable();
      }
    }
  }

  function lsBuildQuery() {
    const params = new URLSearchParams();
    const channel = el("ls-filter-channel").value.trim();
    const status = el("ls-filter-status").value.trim();
    const search = el("ls-filter-search").value.trim();
    const updatedFrom = el("ls-filter-updated-from").value;
    const updatedTo = el("ls-filter-updated-to").value;
    if (channel) params.set("channel_code", channel);
    if (status) params.set("platform_sync_status", status);
    if (search) params.set("q", search);
    if (updatedFrom) params.set("updated_from", new Date(updatedFrom).toISOString());
    if (updatedTo) params.set("updated_to", new Date(updatedTo).toISOString());
    return params.toString();
  }

  function lsPopulateErrorTypeOptions() {
    const select = el("ls-filter-error-type");
    const current = select.value;
    const codes = new Set();
    lsState.rows.forEach((row) => {
      const code = row.listing.status_last_refresh_error_code;
      if (code) codes.add(code);
    });
    select.innerHTML = `<option value="">${HomezI18n.t("common.all")}</option>` + Array.from(codes).sort().map(
      (code) => `<option value="${escapeHtml(code)}">${escapeHtml(code)}</option>`,
    ).join("");
    if (Array.from(select.options).some((o) => o.value === current)) {
      select.value = current;
    }
  }

  function lsRenderStatusCounts() {
    const countsEl = el("ls-status-counts");
    if (!lsState.rows.length) {
      countsEl.innerHTML = "";
      return;
    }
    const counts = {};
    lsState.rows.forEach((row) => {
      const status = row.listing.platform_sync_status || "UNKNOWN";
      counts[status] = (counts[status] || 0) + 1;
    });
    countsEl.innerHTML = (
      `<span class="ls-attempt-note">${HomezI18n.t("common.all")} ${lsState.rows.length}</span> `
      + Object.keys(counts).sort().map((status) => (
        `<span class="pill ${lsStatusPillClass(status)}">${escapeHtml(status)} ${counts[status]}</span>`
      )).join(" ")
    );
  }

  function lsGetFilteredSortedRows() {
    const errorType = el("ls-filter-error-type").value;
    let rows = lsState.rows.slice();

    if (errorType) {
      rows = rows.filter((row) => row.listing.status_last_refresh_error_code === errorType);
    }

    const sort = el("ls-filter-sort").value;
    const byUpdated = (row) => (
      row.listing.status_last_refreshed_at ? (HomezI18n.parseUtcDate(row.listing.status_last_refreshed_at)?.getTime() || 0) : 0
    );

    if (sort === "updated_asc") {
      rows.sort((a, b) => byUpdated(a) - byUpdated(b));
    } else if (sort === "name_asc") {
      rows.sort((a, b) => (a.product_name || "").localeCompare(b.product_name || "", "ko"));
    } else if (sort === "status_asc") {
      rows.sort((a, b) => (
        (a.listing.platform_sync_status || "").localeCompare(b.listing.platform_sync_status || "")
      ));
    } else {
      rows.sort((a, b) => byUpdated(b) - byUpdated(a)); // updated_desc(기본값)
    }

    return rows;
  }

  function lsRenderFilteredTable() {
    const wrap = el("ls-table-wrap");

    if (lsState.rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("ls.empty"), HomezI18n.t("ls.empty_sub"));
      return;
    }

    const rows = lsGetFilteredSortedRows();

    if (rows.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("ls.empty_error_type"), HomezI18n.t("ls.empty_error_type_sub"));
      return;
    }

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead>
          <tr>
            <th>${HomezI18n.t("ls.col_product_name")}</th><th>${HomezI18n.t("ls.col_channel")}</th><th>${HomezI18n.t("ls.col_external_id")}</th><th>${HomezI18n.t("ls.col_status")}</th>
            <th>${HomezI18n.t("ls.col_last_refresh")}</th><th>${HomezI18n.t("ls.col_action")}</th>
          </tr>
        </thead>
        <tbody>${rows.map((row) => lsRenderRow(row)).join("")}</tbody>
      </table>
    `;

    rows.forEach((row) => {
      const listingId = row.listing.id;
      const refreshBtn = el(`ls-refresh-${listingId}`);
      if (refreshBtn) refreshBtn.addEventListener("click", () => lsRunAction(listingId, "refresh", refreshBtn));
      const retryBtn = el(`ls-retry-${listingId}`);
      if (retryBtn) retryBtn.addEventListener("click", () => lsRunAction(listingId, "retry", retryBtn));
      const historyBtn = el(`ls-history-${listingId}`);
      if (historyBtn) historyBtn.addEventListener("click", () => lsShowHistory(listingId));
    });
  }

  async function lsLoadTable() {
    // 새로고침/검색이 겹쳐 눌려도 두 번째 요청은 조용히 무시한다 —
    // 먼저 도착하는 응답이 나중 요청의 최신 상태를 덮어쓰는 경쟁
    // 조건을 막는다(2026-08-05 CTO 재검증 지시 Gate E "중복 실행 방지").
    if (lsState.loading) return;
    lsState.loading = true;

    const wrap = el("ls-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    el("ls-filter-error").textContent = "";
    el("ls-status-counts").innerHTML = "";

    let rows;
    try {
      rows = await apiFetch(`/marketplace-listings/status-sync/listings?${lsBuildQuery()}`);
    } catch (err) {
      lsState.loading = false;
      renderErrorState(wrap, err);
      return;
    }

    lsState.rows = rows || [];
    lsPopulateErrorTypeOptions();
    lsRenderStatusCounts();
    lsRenderFilteredTable();
    lsState.loading = false;
  }

  // Gate H(2026-08-07)가 처음 발견한 naive-UTC 파싱 문제 — Gate
  // M-1(2026-08-21)에서 HomezI18n.parseUtcDate()로 앱 전체 공용
  // 정규화 지점을 만들면서 이 로컬 함수는 그 얇은 위임으로 남긴다
  // (동작은 그대로, 중복 로직만 제거).
  function lsParseUtc(iso) {
    return HomezI18n.parseUtcDate(iso);
  }

  function lsRateLimitRemainingSeconds(listing, nowMs) {
    const availableAt = lsParseUtc(listing.rate_limit_retry_available_at);
    if (!availableAt) return 0;
    return Math.max(0, Math.ceil((availableAt.getTime() - nowMs) / 1000));
  }

  function lsFormatCountdown(totalSeconds) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function lsRenderRow(row) {
    const listing = row.listing;
    const status = listing.platform_sync_status || "UNKNOWN";
    const pillClass = lsStatusPillClass(status);
    const lastRefreshed = listing.status_last_refreshed_at ? fmtDate(listing.status_last_refreshed_at) : HomezI18n.t("ls.never_refreshed");
    // 마지막 새로고침이 오류로 끝났을 때만(=error_code가 있을 때만)
    // "재시도" 버튼을 노출한다 — 성공 상태에서는 재시도 대상이 없다.
    const showRetry = !!listing.status_last_refresh_error_code;

    const remainingSeconds = lsRateLimitRemainingSeconds(listing, Date.now());
    const isWaiting = remainingSeconds > 0;
    const disabledAttr = isWaiting ? "disabled" : "";

    return `
      <tr>
        <td data-label="${HomezI18n.t("ls.col_product_name")}">${escapeHtml(row.product_name || "")}</td>
        <td data-label="${HomezI18n.t("ls.col_channel")}">${escapeHtml(row.channel_code || "")}</td>
        <td data-label="${HomezI18n.t("ls.col_external_id")}">${listing.external_listing_id ? escapeHtml(listing.external_listing_id) : "—"}</td>
        <td data-label="${HomezI18n.t("ls.col_status")}"><span class="pill ${pillClass}">${escapeHtml(lsStatusLabel(status))}</span>${listing.status_last_refresh_error_code ? ` <span class="ls-attempt-note">(${escapeHtml(listing.status_last_refresh_error_code)})</span>` : ""}</td>
        <td data-label="${HomezI18n.t("ls.col_last_refresh")}">${escapeHtml(lastRefreshed)}</td>
        <td data-label="${HomezI18n.t("ls.col_action")}" class="ls-action-cell">
          ${isWaiting ? `
            <p class="ls-rate-limit-note" role="status">
              ${escapeHtml(HomezI18n.t("ls.rate_limit_banner"))}
              <span class="ls-countdown" id="ls-countdown-${listing.id}" data-retry-available-at="${escapeHtml(listing.rate_limit_retry_available_at || "")}">${escapeHtml(HomezI18n.t("ls.rate_limit_countdown", { time: lsFormatCountdown(remainingSeconds) }))}</span>
            </p>
          ` : ""}
          <button type="button" class="btn btn-ghost btn-sm" id="ls-refresh-${listing.id}" ${disabledAttr}>${HomezI18n.t("ls.refresh_btn")}</button>
          ${showRetry ? `<button type="button" class="btn btn-ghost btn-sm" id="ls-retry-${listing.id}" ${disabledAttr}>${HomezI18n.t("ls.retry_btn")}</button>` : ""}
          <button type="button" class="btn btn-ghost btn-sm" id="ls-history-${listing.id}">${HomezI18n.t("ls.history_btn")}</button>
        </td>
      </tr>
    `;
  }

  async function lsRunAction(listingId, action, btn) {
    // 중복 클릭 방지 — 요청이 끝날 때까지 버튼을 비활성화한다.
    if (btn.disabled) return;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = action === "refresh" ? HomezI18n.t("ls.refreshing") : HomezI18n.t("ls.retrying");

    const path = action === "refresh"
      ? `/marketplace-listings/${listingId}/refresh-status`
      : `/marketplace-listings/${listingId}/retry-status-check`;

    try {
      await apiFetch(path, { method: "POST" });
      toast(action === "refresh" ? HomezI18n.t("ls.refresh_success") : HomezI18n.t("ls.retry_success"), "success");
      await lsLoadTable();
      // 이력 패널이 이 listing에 대해 열려 있었다면 갱신된 내용으로 다시 그린다.
      if (lsState.historyListingId === listingId) {
        await lsShowHistory(listingId);
      }
    } catch (err) {
      if (
        err instanceof ApiError && err.status === 429
        && err.detail && typeof err.detail === "object"
        && err.detail.error_code === "RATE_LIMITED"
      ) {
        // Gate H(2026-08-07) — Provider가 실제로 알려준 대기 상태.
        // 토스트로만 알리지 않고 테이블을 다시 불러와 카운트다운
        // 배지를 즉시 보여준다(서버가 이미 알고 있는 절대 시각을
        // 그대로 신뢰한다 — 클라이언트가 재계산하지 않는다).
        toast(HomezI18n.t("ls.rate_limit_banner"), "error");
        await lsLoadTable();
      } else if (err instanceof ApiError && err.status === 429) {
        toast(err.message || HomezI18n.t("ls.rate_limited_error"), "error");
      } else if (err instanceof ApiError && err.status === 503) {
        toast(HomezI18n.t("ls.storage_busy_error"), "error");
      } else if (err instanceof ApiError && err.status === 0) {
        toast(HomezI18n.t("ls.network_disconnected_error"), "error");
      } else {
        toast(err.message || HomezI18n.t("ls.process_error"), "error");
      }
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  async function lsShowHistory(listingId) {
    const panel = el("ls-history-panel");
    const wrap = el("ls-history-table-wrap");
    panel.hidden = false;
    lsState.historyListingId = listingId;
    el("ls-history-listing-label").textContent = HomezI18n.t("ls.history_listing_label", { id: listingId });
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let history;
    try {
      history = await apiFetch(`/marketplace-listings/${listingId}/status-history`);
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (!history || history.length === 0) {
      renderEmptyState(wrap, HomezI18n.t("ls.history_no_data"));
      return;
    }

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead>
          <tr><th>${HomezI18n.t("ls.col_number")}</th><th>${HomezI18n.t("ls.col_source")}</th><th>${HomezI18n.t("ls.col_previous_status")}</th><th>${HomezI18n.t("ls.col_observed_status")}</th><th>${HomezI18n.t("ls.col_applied")}</th><th>${HomezI18n.t("ls.col_error")}</th><th>${HomezI18n.t("ls.col_time")}</th></tr>
        </thead>
        <tbody>${history.slice().reverse().map((e) => `
          <tr>
            <td data-label="${HomezI18n.t("ls.col_number")}">${e.attempt_number}</td>
            <td data-label="${HomezI18n.t("ls.col_source")}">${escapeHtml(e.source)}</td>
            <td data-label="${HomezI18n.t("ls.col_previous_status")}">${e.previous_status ? escapeHtml(e.previous_status) : "—"}</td>
            <td data-label="${HomezI18n.t("ls.col_observed_status")}"><span class="pill ${lsStatusPillClass(e.normalized_status)}">${escapeHtml(lsStatusLabel(e.normalized_status))}</span></td>
            <td data-label="${HomezI18n.t("ls.col_applied")}">${e.applied ? HomezI18n.t("common.yes") : HomezI18n.t("common.no")}</td>
            <td data-label="${HomezI18n.t("ls.col_error")}">${e.error_code ? escapeHtml(e.error_code) : "—"}</td>
            <td data-label="${HomezI18n.t("ls.col_time")}">${fmtDate(e.created_at)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;
  }

  async function lsDownloadCsv() {
    const btn = el("ls-csv-btn");
    if (btn.disabled) return;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = HomezI18n.t("ls.downloading");

    try {
      const csvText = await apiFetch(`/marketplace-listings/status-sync/export.csv?${lsBuildQuery()}`);
      const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "listing-status.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast(err.message || HomezI18n.t("ls.csv_error"), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  async function lsBulkRetryFailed() {
    // 2026-08-05 CTO 재검증 지시 Gate E "부분 실패 복구" — 실패한
    // 채널만 골라 재시도하고, 실행 전 대상 건수를 먼저 보여준 뒤
    // 승인을 받는다(개별 재시도 엔드포인트를 순차 재사용 — 새 서버
    // API를 추가하지 않는다). 실행 후 성공/실패/대상아님 건수를 모두
    // 보여준다.
    //
    // Gate H(2026-08-07) — 실행 전 분해 내역에 "아직 대기 중"(Provider
    // rate limit)과 "EStop 차단"을 추가했다. 대기 중인 항목은 애초에
    // 재시도 호출 자체를 시도하지 않는다(서버가 어차피 429로 막겠지만,
    // 클라이언트가 먼저 걸러 불필요한 요청·오탐 "failed" 카운트를
    // 만들지 않는다). "권한 없음"은 이 화면 자체가 admin_guard로
    // 단일 권한이라(행별 권한 차등이 없음) 이 저장소 구조상 원천적으로
    // 0건이다 — 항목은 계약대로 항상 보여주되 값은 정직하게 0으로
    // 둔다(가짜로 채우지 않는다).
    const failedTargets = lsState.rows.filter((row) => row.listing.status_last_refresh_error_code);

    if (failedTargets.length === 0) {
      toast(HomezI18n.t("ls.no_failed_targets"), "info");
      return;
    }

    const nowMs = Date.now();
    const waitingTargets = failedTargets.filter(
      (row) => lsRateLimitRemainingSeconds(row.listing, nowMs) > 0,
    );
    let retryableTargets = failedTargets.filter(
      (row) => lsRateLimitRemainingSeconds(row.listing, nowMs) === 0,
    );

    let estopActive = false;
    try {
      const safety = await apiFetch("/console/api/safety-status");
      estopActive = !!(safety.schema_ready && safety.emergency_stop && safety.emergency_stop.is_active);
    } catch (_) {
      // 조회 실패는 조용히 무시한다 — EStop이 실제로 활성 상태라면
      // 개별 재시도 호출이 어차피 서버에서 차단되어 failed로 집계된다.
    }

    const estopBlockedCount = estopActive ? retryableTargets.length : 0;
    if (estopActive) {
      retryableTargets = [];
    }
    const permissionDeniedCount = 0;

    const { confirmed } = await confirmDialog({
      title: HomezI18n.t("ls.bulk_retry_confirm_title"),
      body: HomezI18n.t("ls.bulk_retry_breakdown", {
        retryable: retryableTargets.length,
        waiting: waitingTargets.length,
        estopBlocked: estopBlockedCount,
        permissionDenied: permissionDeniedCount,
      }),
      okLabel: HomezI18n.t("ls.bulk_retry_confirm_ok"),
    });
    if (!confirmed) return;

    if (retryableTargets.length === 0) {
      toast(HomezI18n.t("ls.bulk_retry_nothing_to_do"), "info");
      return;
    }

    const targets = retryableTargets;
    const btn = el("ls-bulk-retry-btn");
    btn.disabled = true;
    const originalText = btn.textContent;

    let succeeded = 0;
    let failed = 0;
    const skipped = lsState.rows.length - targets.length;

    for (const row of targets) {
      btn.textContent = HomezI18n.t("ls.bulk_retry_progress", { current: succeeded + failed + 1, total: targets.length });
      try {
        await apiFetch(`/marketplace-listings/${row.listing.id}/retry-status-check`, { method: "POST" });
        succeeded += 1;
      } catch (_) {
        failed += 1;
      }
    }

    btn.disabled = false;
    btn.textContent = originalText;

    toast(
      HomezI18n.t("ls.bulk_retry_done", { succeeded, failed, skipped }),
      failed > 0 ? "error" : "success",
    );
    await lsLoadTable();
  }

  // Gate H(2026-08-07) — 카운트다운은 서버에 매초 요청하지 않고
  // 클라이언트가 로컬로 1초마다 갱신한다(item 4). 0초에 도달하면
  // 그 시점에만 서버 상태를 한 번 다시 조회한다(item 5) — 여러 행이
  // 동시에 0초에 도달해도 한 번만 재조회한다. listing-status-sync
  // 화면이 아닐 때는 카운트다운 요소가 DOM에 없으므로 이 tick은
  // 사실상 아무 일도 하지 않는다(topbar 폴링과 동일한 "항상 실행,
  // 조건부로만 실제 작업" 패턴 — 뷰 전환 시 별도 해제가 필요 없다).
  function lsTickCountdowns() {
    const nodes = document.querySelectorAll("[id^='ls-countdown-']");
    if (nodes.length === 0) return;

    const nowMs = Date.now();
    let anyReachedZero = false;

    nodes.forEach((node) => {
      const availableAt = lsParseUtc(node.dataset.retryAvailableAt);
      if (!availableAt) return;

      const remaining = Math.max(0, Math.ceil((availableAt.getTime() - nowMs) / 1000));
      if (remaining <= 0) {
        anyReachedZero = true;
        return;
      }
      node.textContent = HomezI18n.t("ls.rate_limit_countdown", { time: lsFormatCountdown(remaining) });
    });

    if (anyReachedZero && !lsState.loading) {
      lsLoadTable();
    }
  }

  function initListingStatusSync() {
    setInterval(lsTickCountdowns, 1000);

    el("ls-search-btn").addEventListener("click", () => {
      lsSaveFilterState();
      lsLoadTable();
    });
    el("ls-filter-search").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        lsSaveFilterState();
        lsLoadTable();
      }
    });
    el("ls-filter-channel").addEventListener("change", () => {
      lsSaveFilterState();
      lsLoadTable();
    });
    el("ls-filter-status").addEventListener("change", () => {
      lsSaveFilterState();
      lsLoadTable();
    });
    el("ls-filter-updated-from").addEventListener("change", () => {
      lsSaveFilterState();
      lsLoadTable();
    });
    el("ls-filter-updated-to").addEventListener("change", () => {
      lsSaveFilterState();
      lsLoadTable();
    });
    el("ls-filter-error-type").addEventListener("change", () => {
      lsSaveFilterState();
      lsRenderFilteredTable();
    });
    el("ls-filter-sort").addEventListener("change", () => {
      lsSaveFilterState();
      lsRenderFilteredTable();
    });
    el("ls-csv-btn").addEventListener("click", () => lsDownloadCsv());
    el("ls-bulk-retry-btn").addEventListener("click", () => lsBulkRetryFailed());
    el("ls-history-close-btn").addEventListener("click", () => {
      el("ls-history-panel").hidden = true;
      lsState.historyListingId = null;
    });
  }

  function lpRenderCandidatePicker() {
    const wrap = el("lp-candidate-picker-wrap");
    mountApprovedCandidatePicker(wrap, "lp-candidate-picker", (chosen) => {
      el("lp-candidate-id").value = chosen.id;
      renderApprovedCandidateSelectedSummary(wrap, "lp-candidate-picker", chosen, () => {
        el("lp-candidate-id").value = "";
        lpRenderCandidatePicker();
      });
    });
  }

  function loadListingPackageView() {
    lpState = { packageId: null, fingerprint: null };
    el("lp-result-panel").hidden = true;
    el("lp-create-error").textContent = "";
    el("lp-candidate-id").value = "";

    lpRenderCandidatePicker();

    const rows = el("lp-channel-rows");
    if (rows && rows.children.length === 0) {
      lpAddChannelRow();
    }
  }

  async function lpCreatePackage() {
    const errEl = el("lp-create-error");
    errEl.textContent = "";

    const candidateId = Number(el("lp-candidate-id").value);
    if (!candidateId) {
      errEl.textContent = HomezI18n.t("listing_package.candidate_id_required");
      return;
    }

    const channelSelections = lpCollectChannelSelections();
    if (channelSelections.length === 0) {
      errEl.textContent = HomezI18n.t("listing_package.channel_required");
      return;
    }

    const payload = {
      product_candidate_id: candidateId,
      channel_selections: channelSelections,
      image_options: {
        main_count: Number(el("lp-main-count").value) || 0,
        detail_count: Number(el("lp-detail-count").value) || 0,
        provider_code: "FAKE",
        style_params: {},
      },
      mode: el("lp-mode").value,
      idempotency_key: `console-${candidateId}-${Date.now()}`,
    };

    try {
      const pkg = await apiFetch("/listing-packages", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      lpRenderPackage(pkg);
      toast(HomezI18n.t("listing_package.create_success"), "success");
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("listing_package.create_error");
    }
  }

  function lpRenderPackage(pkg) {
    lpState.packageId = pkg.id;
    lpState.fingerprint = pkg.package_fingerprint;

    el("lp-result-panel").hidden = false;
    el("lp-result-title").textContent = pkg.mode === "AI_AUTO_PROPOSAL"
      ? HomezI18n.t("listing_package.result_title_ai_auto", { id: pkg.id, status: pkg.status })
      : HomezI18n.t("listing_package.result_title_review", { id: pkg.id, status: pkg.status });

    const draft = pkg.draft_payload || {};
    el("lp-draft-view").innerHTML = `
      <p><strong>${HomezI18n.t("listing_package.draft_name_label")}</strong> ${draft.product_name || "-"}</p>
      <p><strong>${HomezI18n.t("listing_package.draft_description_label")}</strong> ${draft.description || "-"}</p>
      <p><strong>${HomezI18n.t("listing_package.draft_keywords_label")}</strong> ${(draft.keywords || []).join(", ") || "-"}</p>
      <p class="stat-sub">${HomezI18n.t("listing_package.draft_price_note")}</p>
    `;

    const gallery = el("lp-image-gallery");
    gallery.innerHTML = "";
    (pkg.channel_readiness || []); // no-op, readiness rendered separately below

    apiFetch(`/media-assets/owners/LISTING_PACKAGE/${pkg.id}/assets`)
      .then((assets) => {
        if (!assets.length) {
          gallery.innerHTML = `<p class="stat-sub">${HomezI18n.t("listing_package.images_empty")}</p>`;
          return;
        }
        assets.forEach((asset) => {
          const card = document.createElement("div");
          card.className = "lp-image-card";
          card.innerHTML = `
            <div class="lp-image-caption">${asset.purpose} · ${asset.width || "?"}×${asset.height || "?"}</div>
          `;
          gallery.appendChild(card);
        });
      })
      .catch(() => {
        gallery.innerHTML = `<p class="stat-sub">${HomezI18n.t("listing_package.images_load_error")}</p>`;
      });

    const readinessWrap = el("lp-readiness-table-wrap");
    const readiness = pkg.channel_readiness || [];
    if (!readiness.length) {
      readinessWrap.innerHTML = `<p class="stat-sub">${HomezI18n.t("listing_package.readiness_empty")}</p>`;
    } else {
      readinessWrap.innerHTML = `
        <table>
          <thead><tr><th>${HomezI18n.t("listing_package.col_channel")}</th><th>${HomezI18n.t("listing_package.col_readiness")}</th><th>${HomezI18n.t("listing_package.col_reason")}</th></tr></thead>
          <tbody>
            ${readiness.map((r) => `
              <tr>
                <td>${r.channel_code}</td>
                <td>${r.ready ? HomezI18n.t("listing_package.ready") : HomezI18n.t("listing_package.not_ready")}</td>
                <td>${(r.missing_reasons || []).join(", ") || "-"}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    el("lp-recommendation-view").innerHTML = `
      <p><strong>${HomezI18n.t("listing_package.estimated_revenue_label")}</strong> ${pkg.estimated_revenue != null ? pkg.estimated_revenue : "-"}</p>
      <p><strong>${HomezI18n.t("listing_package.risk_summary_label")}</strong> ${pkg.risk_summary || "-"}</p>
      <p><strong>${HomezI18n.t("listing_package.recommendation_reason_label")}</strong> ${pkg.recommendation_reason || "-"}</p>
    `;

    el("lp-submit-btn").disabled = !pkg.submit_ready;
    el("lp-submit-status").textContent = pkg.submit_ready
      ? HomezI18n.t("listing_package.submit_ready")
      : HomezI18n.t("listing_package.submit_not_ready");
  }

  async function lpRegenerateImages() {
    if (!lpState.packageId) return;

    try {
      await apiFetch("/media-assets/image-jobs", {
        method: "POST",
        body: JSON.stringify({
          product_candidate_id: Number(el("lp-candidate-id").value),
          listing_package_id: lpState.packageId,
          items: [{ purpose: "MAIN" }],
          provider_code: "FAKE",
          idempotency_key: `console-regen-${lpState.packageId}-${Date.now()}`,
        }),
      });
      const pkg = await apiFetch(`/listing-packages/${lpState.packageId}`);
      lpRenderPackage(pkg);
      toast(HomezI18n.t("listing_package.regenerate_success"), "success");
    } catch (err) {
      toast(err.message || HomezI18n.t("listing_package.regenerate_error"), "error");
    }
  }

  async function lpApprovePackage() {
    const errEl = el("lp-approval-error");
    errEl.textContent = "";
    if (!lpState.packageId) return;

    try {
      await apiFetch(`/listing-packages/${lpState.packageId}/approve`, {
        method: "POST",
        body: JSON.stringify({
          expected_package_fingerprint: lpState.fingerprint,
          idempotency_key: `console-approve-${lpState.packageId}-${Date.now()}`,
        }),
      });
      const pkg = await apiFetch(`/listing-packages/${lpState.packageId}`);
      lpRenderPackage(pkg);
      toast(HomezI18n.t("listing_package.approve_success"), "success");
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("listing_package.approve_error");
    }
  }

  async function lpRejectPackage() {
    if (!lpState.packageId) return;
    const reason = window.prompt(HomezI18n.t("listing_package.reject_reason_prompt"));
    if (!reason) return;

    try {
      await apiFetch(`/listing-packages/${lpState.packageId}/reject`, {
        method: "POST",
        body: JSON.stringify({
          reason,
          idempotency_key: `console-reject-${lpState.packageId}-${Date.now()}`,
        }),
      });
      toast(HomezI18n.t("listing_package.reject_success"), "info");
      el("lp-result-panel").hidden = true;
    } catch (err) {
      toast(err.message || HomezI18n.t("listing_package.reject_error"), "error");
    }
  }

  async function lpSubmitPackage() {
    if (!lpState.packageId) return;

    try {
      await apiFetch(`/listing-packages/${lpState.packageId}/submit`, { method: "POST" });
      toast(HomezI18n.t("listing_package.submit_success"), "success");
    } catch (err) {
      // 이번 Phase는 항상 여기서 막힌다(dry-run) — 서버가 실제로
      // 차단하고 있음을 그대로 보여준다.
      toast(err.message || HomezI18n.t("listing_package.submit_blocked"), "info");
    }
  }

  // --------------------------------------------------
  // 판매채널 연동 (store-connection)
  //
  // Secret은 scWizard 안에서만(메모리) 잠깐 보관되고 localStorage/
  // sessionStorage에는 절대 쓰지 않는다. 마법사를 닫으면 scWizard
  // 자체를 null로 만들고 렌더된 input DOM 값도 명시적으로 비운다.
  // --------------------------------------------------

  const SC_CREDENTIAL_FIELDS = {
    COUPANG: [
      { key: "vendor_id", labelKey: "sc.credential_field.vendor_id", secret: false },
      { key: "access_key", label: "Access Key", secret: true },
      { key: "secret_key", label: "Secret Key", secret: true },
    ],
    NAVER_SMARTSTORE: [
      { key: "client_id", label: "Client ID", secret: false },
      { key: "client_secret", label: "Client Secret", secret: true },
    ],
  };

  const SC_MARKETPLACE_LABEL_KEY = {
    COUPANG: "sc.coupang.title",
    NAVER_SMARTSTORE: "sc.naver.title",
  };

  let scWizard = null;

  async function loadStoreConnectionView() {
    loadR2HostingStatus();
    const wrap = el("sc-connections-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    try {
      const connections = await apiFetch("/store-connections");

      if (!connections || connections.length === 0) {
        renderEmptyState(wrap, HomezI18n.t("sc.connections.empty"), HomezI18n.t("sc.connections.empty_sub"));
        return;
      }

      const colChannel = HomezI18n.t("sc.connections.col_channel");
      const colName = HomezI18n.t("sc.connections.col_name");
      const colStatus = HomezI18n.t("sc.connections.col_status");
      const colLastVerified = HomezI18n.t("sc.connections.col_last_verified");
      const colExpires = HomezI18n.t("sc.connections.col_expires");
      const colError = HomezI18n.t("sc.connections.col_error");
      const colActions = HomezI18n.t("sc.connections.col_actions");

      const rows = connections.map((c) => `
        <tr>
          <td data-label="${colChannel}">${escapeHtml(HomezI18n.t(SC_MARKETPLACE_LABEL_KEY[c.marketplace_code]) || c.marketplace_code)}</td>
          <td data-label="${colName}">${escapeHtml(c.display_name)}</td>
          <td data-label="${colStatus}">${scStatusPill(c.connection_status)}</td>
          <td data-label="${colLastVerified}">${fmtDate(c.last_verified_at)}</td>
          <td data-label="${colExpires}">${fmtDate(c.expires_at)}</td>
          <td data-label="${colError}">${c.last_error_summary ? escapeHtml(c.last_error_summary) : "—"}</td>
          <td data-label="${colActions}">
            <div class="sc-conn-actions">
              <button class="btn btn-ghost btn-sm" data-sc-action="verify" data-id="${c.id}">${HomezI18n.t("sc.connections.action_verify")}</button>
              <button class="btn btn-ghost btn-sm" data-sc-action="rotate" data-id="${c.id}">${HomezI18n.t("sc.connections.action_rotate")}</button>
              ${c.connection_status !== "DISABLED" ? `<button class="btn btn-ghost btn-sm" data-sc-action="disable" data-id="${c.id}">${HomezI18n.t("sc.connections.action_disable")}</button>` : ""}
              ${c.connection_status !== "NOT_CONFIGURED" ? `<button class="btn btn-danger btn-sm" data-sc-action="delete-credential" data-id="${c.id}">${HomezI18n.t("sc.connections.action_delete_credential")}</button>` : ""}
            </div>
          </td>
        </tr>
      `).join("");

      wrap.innerHTML = `
        <table class="responsive-cards">
          <thead><tr><th>${colChannel}</th><th>${colName}</th><th>${colStatus}</th><th>${colLastVerified}</th><th>${colExpires}</th><th>${colError}</th><th>${colActions}</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;

      wrap.querySelectorAll("[data-sc-action]").forEach((btn) => {
        btn.addEventListener("click", () => withButtonGuard(btn, () => scHandleRowAction(btn.dataset.scAction, Number(btn.dataset.id), connections)));
      });
    } catch (err) {
      renderErrorState(wrap, err);
    }
  }

  function scStatusPill(status) {
    const map = {
      NOT_CONFIGURED: ["neutral", "sc.status.not_configured"],
      VERIFYING: ["verifying", "sc.status.verifying"],
      CONNECTED: ["ok", "sc.status.connected"],
      ERROR: ["danger", "sc.status.error"],
      DISABLED: ["neutral", "sc.status.disabled"],
    };
    const [cls, labelKey] = map[status] || ["neutral", null];
    const label = labelKey ? HomezI18n.t(labelKey) : status;
    return `<span class="pill ${cls}">${escapeHtml(label)}</span>`;
  }

  async function scHandleRowAction(action, id, connections) {
    const connection = connections.find((c) => c.id === id);
    if (!connection) return;

    if (action === "verify") {
      try {
        const result = await apiFetch(`/store-connections/${id}/verify`, { method: "POST" });
        toast(
          result.success ? HomezI18n.t("sc.verify.success") : (result.error_summary || HomezI18n.t("sc.verify.failure_generic")),
          result.success ? "success" : "error",
        );
      } catch (err) {
        toast(err.message || HomezI18n.t("sc.verify.error"), "error");
      }
      loadStoreConnectionView();
      return;
    }

    if (action === "rotate") {
      scOpenWizard(connection.marketplace_code, "rotate", connection);
      return;
    }

    if (action === "disable") {
      const { confirmed } = await confirmDialog({
        title: HomezI18n.t("sc.disable.confirm_title"),
        body: HomezI18n.t("sc.disable.confirm_body", { name: connection.display_name }),
        okLabel: HomezI18n.t("sc.disable.confirm_ok"),
      });
      if (!confirmed) return;
      try {
        await apiFetch(`/store-connections/${id}/disable`, {
          method: "POST",
          body: JSON.stringify({ idempotency_key: `sc-disable-${id}-${Date.now()}` }),
        });
        toast(HomezI18n.t("sc.disable.success"), "success");
      } catch (err) {
        toast(err.message || HomezI18n.t("sc.disable.error"), "error");
      }
      loadStoreConnectionView();
      return;
    }

    if (action === "delete-credential") {
      const { confirmed } = await confirmDialog({
        title: HomezI18n.t("sc.delete_credential.confirm_title"),
        body: HomezI18n.t("sc.delete_credential.confirm_body", { name: connection.display_name }),
        okLabel: HomezI18n.t("sc.delete_credential.confirm_ok"),
        requireReason: true,
      });
      if (!confirmed) return;
      try {
        await apiFetch(`/store-connections/${id}/credential`, {
          method: "DELETE",
          body: JSON.stringify({
            confirm: true,
            idempotency_key: `sc-delete-credential-${id}-${Date.now()}`,
          }),
        });
        toast(HomezI18n.t("sc.delete_credential.success"), "success");
      } catch (err) {
        toast(err.message || HomezI18n.t("sc.delete_credential.error"), "error");
      }
      loadStoreConnectionView();
    }
  }

  async function scOpenWizard(marketplaceCode, mode, connection) {
    let guideSteps = [];
    let officialDocUrl = "";

    if (mode === "create") {
      try {
        const guide = await apiFetch(`/store-connections/guides/${marketplaceCode}`);
        officialDocUrl = guide.official_doc_url;
        guideSteps = guide.steps.map((s) => ({ type: "guide", data: s }));
      } catch (_) {
        guideSteps = [];
      }
    }

    scWizard = {
      marketplaceCode,
      mode,
      connectionId: connection ? connection.id : null,
      steps: [...guideSteps, { type: "input" }, { type: "test" }, { type: "save" }],
      stepIndex: 0,
      formValues: {},
      displayName: connection ? connection.display_name : "",
      sellerIdentifier: connection ? connection.seller_identifier : "",
      verifyResult: null,
      verifiedSnapshot: null,
      officialDocUrl,
    };

    el("sc-wizard-title").textContent = HomezI18n.t(
      mode === "create" ? "sc.wizard.title_create" : "sc.wizard.title_rotate",
      { marketplace: HomezI18n.t(SC_MARKETPLACE_LABEL_KEY[marketplaceCode]) },
    );
    el("sc-wizard-dialog").showModal();
    scRenderWizardStep();
  }

  function scGuideStepCount() {
    return scWizard.steps.filter((s) => s.type === "guide").length;
  }

  function scRenderWizardStep() {
    const step = scWizard.steps[scWizard.stepIndex];
    const body = el("sc-wizard-body");
    const total = scWizard.steps.length;

    el("sc-wizard-step-indicator").textContent = HomezI18n.t("sc.wizard.step_indicator", { current: scWizard.stepIndex + 1, total });
    el("sc-wizard-progress-fill").style.width = `${((scWizard.stepIndex + 1) / total) * 100}%`;

    const officialLink = el("sc-wizard-official-link");
    officialLink.href = scWizard.officialDocUrl || "#";
    officialLink.style.display = scWizard.officialDocUrl ? "" : "none";

    const prevBtn = el("sc-wizard-prev");
    const nextBtn = el("sc-wizard-next");
    prevBtn.disabled = scWizard.stepIndex === 0;
    nextBtn.style.display = "";
    nextBtn.textContent = HomezI18n.t("sc.wizard.next");
    nextBtn.disabled = false;

    if (step.type === "guide") {
      const s = step.data;
      const isLastGuide = scWizard.stepIndex === scGuideStepCount() - 1;
      body.innerHTML = `
        <div class="sc-guide-banner">${s.is_official_capture ? HomezI18n.t("sc.wizard.guide_official_banner") : HomezI18n.t("sc.wizard.guide_example_banner")}</div>
        <div class="sc-guide-image-wrap" id="sc-guide-image-wrap" tabindex="0" role="button" aria-label="${HomezI18n.t("sc.wizard.guide_zoom_aria")}">
          <img src="/${s.image_path}" alt="${escapeHtml(s.image_alt_text)}">
          <span class="sc-guide-image-zoom-hint">${HomezI18n.t("sc.wizard.guide_zoom_hint")}</span>
        </div>
        <h3>${scWizard.stepIndex + 1}. ${escapeHtml(s.title)}</h3>
        <p class="sc-guide-desc">${escapeHtml(s.description)}</p>
        ${isLastGuide ? `
          <label class="field field-inline" style="margin-top:12px;">
            <input type="checkbox" id="sc-guide-done-checkbox"> ${HomezI18n.t("sc.wizard.guide_done_checkbox")}
          </label>
        ` : ""}
      `;
      const imgWrap = el("sc-guide-image-wrap");
      imgWrap.addEventListener("click", () => scOpenImageZoom(`/${s.image_path}`, s.image_alt_text));
      imgWrap.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") scOpenImageZoom(`/${s.image_path}`, s.image_alt_text);
      });
    } else if (step.type === "input") {
      const fields = SC_CREDENTIAL_FIELDS[scWizard.marketplaceCode];
      body.innerHTML = `
        <h3>${HomezI18n.t("sc.wizard.input_title")}</h3>
        <p class="sc-guide-desc">${HomezI18n.t("sc.wizard.input_desc", { marketplace: HomezI18n.t(SC_MARKETPLACE_LABEL_KEY[scWizard.marketplaceCode]) })}</p>
        <label class="field">
          <span class="field-label">${HomezI18n.t("sc.wizard.display_name_label")}</span>
          <input type="text" id="sc-input-display-name" autocomplete="off">
        </label>
        <label class="field">
          <span class="field-label">${HomezI18n.t("sc.wizard.seller_identifier_label")}</span>
          <input type="text" id="sc-input-seller-identifier" autocomplete="off" ${scWizard.mode === "rotate" ? "disabled" : ""}>
        </label>
        ${fields.map((f) => `
          <label class="field">
            <span class="field-label">${escapeHtml(f.labelKey ? HomezI18n.t(f.labelKey) : f.label)}</span>
            <div class="sc-secret-field-wrap">
              <input type="${f.secret ? "password" : "text"}" id="sc-input-${f.key}" autocomplete="off" data-field-key="${f.key}">
              ${f.secret ? `<button type="button" class="icon-btn sc-secret-toggle" data-toggle-for="sc-input-${f.key}" aria-label="${HomezI18n.t("sc.wizard.secret_toggle_aria")}">👁</button>` : ""}
            </div>
          </label>
        `).join("")}
      `;
      el("sc-input-display-name").value = scWizard.displayName;
      el("sc-input-seller-identifier").value = scWizard.sellerIdentifier;
      fields.forEach((f) => {
        const inp = el(`sc-input-${f.key}`);
        if (inp && scWizard.formValues[f.key] !== undefined) inp.value = scWizard.formValues[f.key];
      });
      body.querySelectorAll(".sc-secret-toggle").forEach((btn) => {
        btn.addEventListener("click", () => {
          const target = el(btn.dataset.toggleFor);
          target.type = target.type === "password" ? "text" : "password";
        });
      });
    } else if (step.type === "test") {
      body.innerHTML = `
        <h3>${HomezI18n.t("sc.wizard.test_title")}</h3>
        <p class="sc-guide-desc">${HomezI18n.t("sc.wizard.test_desc")}</p>
        <button type="button" class="btn btn-primary" id="sc-run-verify-btn">${HomezI18n.t("sc.wizard.test_run_btn")}</button>
        <div id="sc-verify-result-area"></div>
      `;
      el("sc-run-verify-btn").addEventListener("click", scRunVerify);
      scRenderVerifyResult();
      nextBtn.disabled = !(scWizard.verifyResult && scWizard.verifyResult.success);
    } else if (step.type === "save") {
      body.innerHTML = `
        <h3>${HomezI18n.t("sc.wizard.save_title")}</h3>
        <img src="/assets/guides/marketplace/credential-security-warning.svg"
             alt="${HomezI18n.t("sc.wizard.save_image_alt")}"
             class="sc-overview-image" style="max-width:360px;">
        <p class="sc-guide-desc">${HomezI18n.t("sc.wizard.save_name_prefix")}<strong>${escapeHtml(scWizard.displayName)}</strong><br>${HomezI18n.t("sc.wizard.save_verified_prefix")}<strong>${escapeHtml((scWizard.verifyResult && scWizard.verifyResult.masked_credential_hint) || "")}</strong></p>
        <p class="stat-sub">${HomezI18n.t("sc.wizard.save_secret_note")}</p>
        <button type="button" class="btn btn-primary" id="sc-run-save-btn">${HomezI18n.t(scWizard.mode === "create" ? "sc.wizard.save_btn_create" : "sc.wizard.save_btn_rotate")}</button>
        <p class="field-error" id="sc-save-error"></p>
      `;
      el("sc-run-save-btn").addEventListener("click", scRunSave);
      nextBtn.style.display = "none";
    }
  }

  function scCaptureInputStep() {
    scWizard.displayName = el("sc-input-display-name").value.trim();
    scWizard.sellerIdentifier = el("sc-input-seller-identifier").value.trim();

    const fields = SC_CREDENTIAL_FIELDS[scWizard.marketplaceCode];
    const newValues = {};
    fields.forEach((f) => {
      const inp = el(`sc-input-${f.key}`);
      if (inp) newValues[f.key] = inp.value;
    });

    const changed = JSON.stringify(newValues) !== JSON.stringify(scWizard.verifiedSnapshot);
    scWizard.formValues = newValues;
    if (changed) {
      // High: 검증 이후 입력값이 바뀌면 이전 verification_token은 서버가
      // fingerprint 불일치로 거부한다 — 클라이언트도 미리 무효화해 재검증을
      // 유도한다.
      scWizard.verifyResult = null;
    }
  }

  async function scRunVerify() {
    const btn = el("sc-run-verify-btn");
    btn.disabled = true;
    btn.textContent = HomezI18n.t("sc.wizard.test_running");

    try {
      const resp = await apiFetch("/store-connections/verify", {
        method: "POST",
        body: JSON.stringify({
          marketplace_code: scWizard.marketplaceCode,
          seller_identifier: scWizard.sellerIdentifier,
          credential_fields: scWizard.formValues,
        }),
      });
      scWizard.verifyResult = resp;
      scWizard.verifiedSnapshot = Object.assign({}, scWizard.formValues);
    } catch (err) {
      scWizard.verifyResult = {
        success: false, verification_token: null, token_expires_at: null,
        masked_credential_hint: null, error_code: null,
        error_summary: err.message || HomezI18n.t("sc.verify.error"),
        expiration_status: null,
      };
    }

    btn.disabled = false;
    btn.textContent = HomezI18n.t("sc.wizard.test_run_btn");
    scRenderVerifyResult();
    el("sc-wizard-next").disabled = !(scWizard.verifyResult && scWizard.verifyResult.success);
  }

  function scRenderVerifyResult() {
    const area = el("sc-verify-result-area");
    if (!area) return;

    const r = scWizard.verifyResult;
    if (!r) {
      area.innerHTML = "";
      return;
    }

    if (r.success) {
      area.innerHTML = `<div class="sc-verify-result success">${HomezI18n.t("sc.wizard.verify_success_prefix")}${escapeHtml(r.masked_credential_hint || "")}</div>`;
    } else {
      const category = scErrorCategory(r.error_code);
      area.innerHTML = `<div class="sc-verify-result failure ${category.cssClass}">✕ <strong>[${HomezI18n.t(category.labelKey)}]</strong> ${escapeHtml(r.error_summary || HomezI18n.t("sc.wizard.verify_failure_unknown"))}</div>`;
    }
  }

  // Gate 4(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5): "연결 실패, 권한
  // 부족, 만료, rate limit 상태를 UI에 구분 표시한다" 요구사항 —
  // error_summary 텍스트뿐 아니라 카테고리 배지·색상으로도 구분한다.
  function scErrorCategory(errorCode) {
    switch (errorCode) {
      case "RATE_LIMITED_429":
        return { cssClass: "rate-limited", labelKey: "sc.error_category.rate_limited" };
      case "CREDENTIAL_EXPIRED":
        return { cssClass: "expired", labelKey: "sc.error_category.expired" };
      case "UNAUTHORIZED_401":
      case "SECRET_MISMATCH":
        return { cssClass: "unauthorized", labelKey: "sc.error_category.unauthorized" };
      case "FORBIDDEN_403":
      case "IP_NOT_ALLOWED":
        return { cssClass: "forbidden", labelKey: "sc.error_category.forbidden" };
      case "PERMISSION_PENDING":
      case "APPLICATION_INACTIVE":
        return { cssClass: "pending", labelKey: "sc.error_category.pending" };
      case "PLATFORM_ERROR_5XX":
      case "TIMEOUT":
        return { cssClass: "platform", labelKey: "sc.error_category.platform" };
      default:
        return { cssClass: "generic", labelKey: "sc.error_category.generic" };
    }
  }

  async function scRunSave() {
    const btn = el("sc-run-save-btn");
    const errEl = el("sc-save-error");
    btn.disabled = true;
    errEl.textContent = "";

    const idempotencyKey = `sc-${scWizard.mode}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

    try {
      if (scWizard.mode === "create") {
        await apiFetch("/store-connections", {
          method: "POST",
          body: JSON.stringify({
            marketplace_code: scWizard.marketplaceCode,
            display_name: scWizard.displayName,
            seller_identifier: scWizard.sellerIdentifier,
            credential_fields: scWizard.formValues,
            verification_token: scWizard.verifyResult.verification_token,
            idempotency_key: idempotencyKey,
          }),
        });
      } else {
        await apiFetch(`/store-connections/${scWizard.connectionId}/rotate-credential`, {
          method: "POST",
          body: JSON.stringify({
            credential_fields: scWizard.formValues,
            verification_token: scWizard.verifyResult.verification_token,
            idempotency_key: idempotencyKey,
          }),
        });
      }

      toast(HomezI18n.t("sc.wizard.save_success"), "success");
      scCloseWizard();
      loadStoreConnectionView();
    } catch (err) {
      errEl.textContent = err.message || HomezI18n.t("sc.wizard.save_error");
      btn.disabled = false;
    }
  }

  function scCloseWizard() {
    const body = el("sc-wizard-body");
    if (body) {
      body.querySelectorAll('input[type="password"], input[type="text"]').forEach((i) => {
        i.value = "";
      });
      body.innerHTML = "";
    }
    scWizard = null;
    el("sc-wizard-dialog").close();
  }

  function scOpenImageZoom(src, alt) {
    const img = el("sc-image-zoom-img");
    img.src = src;
    img.alt = alt;
    el("sc-image-zoom-dialog").showModal();
  }

  // 2026-08-29 Pre-Live 검증 — R2 공개 이미지 호스팅 Credential 저장
  // 화면. 백엔드(app/domains/media_asset/public_hosting.py)와 신규
  // 엔드포인트(app/domains/media_asset/router.py)는 이미 있었으나
  // 이 화면이 없어 위저드 5단계 이미지 자동채움이 항상 조용히
  // 실패하던 실사용 결함을 고친다.
  async function loadR2HostingStatus() {
    const statusEl = el("r2-status");
    if (!statusEl) return;
    statusEl.textContent = HomezI18n.t("r2.status_checking");
    try {
      const resp = await apiFetch("/media-assets/r2-hosting/status");
      statusEl.textContent = HomezI18n.t(
        resp.configured ? "r2.status_configured" : "r2.status_not_configured",
      );
    } catch (err) {
      statusEl.textContent = err.message || HomezI18n.t("r2.save_error_generic");
    }
  }

  function initR2HostingPanel() {
    const btn = el("r2-save-btn");
    if (!btn) return;
    btn.addEventListener("click", () => withButtonGuard(btn, async () => {
      const errEl = el("r2-save-error");
      errEl.textContent = "";
      const fields = {
        account_id: el("r2-account-id").value.trim(),
        access_key_id: el("r2-access-key-id").value.trim(),
        secret_access_key: el("r2-secret-access-key").value,
        bucket_name: el("r2-bucket-name").value.trim(),
        public_base_url: el("r2-public-base-url").value.trim(),
      };
      if (Object.values(fields).some((v) => !v)) {
        errEl.textContent = HomezI18n.t("r2.field_required");
        return;
      }
      btn.disabled = true;
      btn.textContent = HomezI18n.t("r2.saving");
      try {
        await apiFetch("/media-assets/r2-hosting/credentials", {
          method: "POST",
          body: JSON.stringify(fields),
        });
        el("r2-secret-access-key").value = "";
        el("r2-status").textContent = HomezI18n.t("r2.save_success");
        await loadR2HostingStatus();
      } catch (err) {
        errEl.textContent = err.message || HomezI18n.t("r2.save_error_generic");
      } finally {
        btn.disabled = false;
        btn.textContent = HomezI18n.t("r2.save_btn");
      }
    }));
  }

  function initStoreConnectionWizard() {
    el("sc-start-coupang-btn").addEventListener("click", () => scOpenWizard("COUPANG", "create", null));
    el("sc-start-naver-btn").addEventListener("click", () => scOpenWizard("NAVER_SMARTSTORE", "create", null));

    el("sc-wizard-close").addEventListener("click", scCloseWizard);
    el("sc-wizard-dialog").addEventListener("cancel", scCloseWizard);

    el("sc-wizard-prev").addEventListener("click", () => {
      if (!scWizard || scWizard.stepIndex === 0) return;
      scWizard.stepIndex -= 1;
      scRenderWizardStep();
    });

    el("sc-wizard-next").addEventListener("click", () => {
      if (!scWizard) return;
      const step = scWizard.steps[scWizard.stepIndex];

      if (step.type === "guide") {
        const isLastGuide = scWizard.stepIndex === scGuideStepCount() - 1;
        if (isLastGuide) {
          const cb = el("sc-guide-done-checkbox");
          if (cb && !cb.checked) {
            toast(HomezI18n.t("sc.wizard.guide_done_required"), "error");
            return;
          }
        }
      } else if (step.type === "input") {
        scCaptureInputStep();
      }

      scWizard.stepIndex += 1;
      scRenderWizardStep();
    });

    el("sc-wizard-troubleshoot").addEventListener("click", () => {
      toast(HomezI18n.t("sc.wizard.troubleshoot_hint"), "info");
    });

    el("sc-image-zoom-close").addEventListener("click", () => el("sc-image-zoom-dialog").close());
    el("sc-image-zoom-dialog").addEventListener("click", (ev) => {
      if (ev.target === el("sc-image-zoom-dialog")) el("sc-image-zoom-dialog").close();
    });
  }

  // --------------------------------------------------
  // 자금·정산
  // --------------------------------------------------

  async function loadFinance() {
    const container = el("finance-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let accounts = null;
    let accountsErr = null;
    let settlements = null;
    let settlementsErr = null;

    try {
      accounts = await apiFetch("/funding/accounts");
    } catch (err) {
      accountsErr = err;
    }

    try {
      settlements = await apiFetch("/settlements?limit=100");
    } catch (err) {
      settlementsErr = err;
    }

    const account = accounts && accounts.length ? accounts[0] : null;

    let settlementSummaryHtml = `<p class="stat-sub">${HomezI18n.t("finance.settlement_no_data")}</p>`;
    if (settlementsErr) {
      settlementSummaryHtml = `<p class="stat-sub">${escapeHtml(settlementsErr.message)}</p>`;
    } else if (settlements && settlements.length) {
      const counts = {};
      settlements.forEach((s) => { counts[s.status] = (counts[s.status] || 0) + 1; });
      // 2026-09-10 UI 개선 — 채널 정산 화면(UI-9)에서 이미 고친 것과
      // 같은 결함: 정산 상태가 영문 코드 그대로 노출되고 있었다.
      // 같은 stl.status.* 키를 재사용한다(이미 검증된 8개 상태 전부
      // 포함).
      settlementSummaryHtml = Object.entries(counts).map(([k, v]) => {
        const label = HomezI18n.t(`stl.status.${String(k).toLowerCase()}`) || k;
        return `<div>${escapeHtml(label)}: <strong>${v}</strong></div>`;
      }).join("");
    }

    container.innerHTML = `
      <div class="finance-domains">
        <div class="finance-domain-card">
          <h2>${HomezI18n.t("finance.available_funding_title")}</h2>
          ${accountsErr ? `<p class="stat-sub">${escapeHtml(accountsErr.message)}</p>` : account ? `
            <div class="stat-value money">${fmtMoney(account.available_amount, account.currency)}</div>
            <div class="stat-sub">${HomezI18n.t("finance.total_funding_prefix")}${fmtMoney(account.total_funding, account.currency)}</div>
          ` : `<p class="stat-sub">${HomezI18n.t("finance.no_funding_account")}</p>`}
        </div>

        <div class="finance-domain-card">
          <h2>${HomezI18n.t("finance.hold_title")}</h2>
          ${accountsErr ? `<p class="stat-sub">${escapeHtml(accountsErr.message)}</p>` : account ? `
            <div class="stat-value money">${fmtMoney(account.held_amount, account.currency)}</div>
            <div class="stat-sub">${HomezI18n.t("finance.hold_note")}</div>
          ` : `<p class="stat-sub">${HomezI18n.t("finance.no_data")}</p>`}
        </div>

        <div class="finance-domain-card">
          <h2>${HomezI18n.t("finance.supplier_payment_title")}</h2>
          <p class="stat-sub">${HomezI18n.t("finance.supplier_payment_note")}</p>
        </div>

        <div class="finance-domain-card">
          <h2>${HomezI18n.t("finance.marketplace_settlement_title")}</h2>
          ${settlementSummaryHtml}
        </div>
      </div>
    `;
  }

  // ==================================================
  // V7 Gate 7(2026-08-15) — 재고/주문/발주/배송/반품/가격·마진/
  // 채널정산 통합 UI 연결(Gate 3~5 신규 도메인). 전 엔드포인트가
  // admin_guard + current_user.company_id만 쓰므로(요청 바디로
  // company_id를 받지 않음) 이 화면들은 별도 회사 선택 UI가 필요
  //없다 — 로그인한 관리자의 회사로 자동 스코프된다.
  // ==================================================

  function genIdemKey(prefix) {
    return `console-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function genericStatusPillClass(status) {
    const s = String(status || "").toUpperCase();
    if ([
      "CANCELLED", "REJECTED", "FAILED", "MISMATCH", "MISMATCHED",
      "OUT_OF_STOCK", "REVERSED", "BLOCKED",
    ].includes(s)) return "danger";
    if ([
      "HELD", "PENDING", "REQUESTED", "PENDING_SETTLEMENT", "PENDING_APPROVAL",
      "PARTIALLY_SHIPPED", "UNCONFIRMED",
    ].includes(s)) return "warn";
    if ([
      "DELIVERED", "COMPLETED", "RECEIVED", "CONFIRMED", "MATCHED", "APPROVED",
      "SYNCED", "DEPOSITED", "RESERVED", "SHIPPED", "IN_TRANSIT", "RESTOCKED",
      "EXCHANGED", "RETURNED", "READY", "PASSED", "UNBLOCKED", "SUCCESS",
    ].includes(s)) return "ok";
    return "neutral";
  }

  function statusPillHtml(status) {
    return `<span class="pill ${genericStatusPillClass(status)}">${escapeHtml(String(status || "—"))}</span>`;
  }

  // 2026-09-10 UI 개선(시안 06 배송 관리 참고) — statusPillHtml()은
  // 여러 화면이 공유하므로 그대로 두고(번역 키 없는 화면에서 빈
  // 라벨이 뜨는 회귀를 막기 위해), 라벨 번역이 준비된 화면만 옵트인
  // 하는 별도 함수를 추가한다. 키가 없거나 빈 문자열이면(HomezI18n.t
  // fallback) 원문 상태 코드를 그대로 보여준다 — 번역 누락이 빈
  // 라벨로 이어지지 않는다.
  function statusPillHtmlLabeled(status, labelKeyPrefix) {
    const raw = String(status || "—");
    const translated = status ? HomezI18n.t(`${labelKeyPrefix}${raw.toLowerCase()}`) : "";
    return `<span class="pill ${genericStatusPillClass(status)}">${escapeHtml(translated || raw)}</span>`;
  }

  // 진행 단계 표시(status timeline) — 시안 04/06/07(주문·매입/배송/
  // 취소·반품)이 공통으로 쓰는 단계 도식을 재사용 가능한 형태로
  // 만든다. steps: [{label, time, state}], state는 done/current/
  // pending/branch 중 하나.
  function statusTimelineHtml(steps) {
    return `
      <div class="status-timeline">
        ${steps.map((s) => `
          <div class="status-timeline-step is-${escapeHtml(s.state)}">
            <div class="status-timeline-dot">${s.state === "done" ? "✓" : ""}</div>
            <div class="status-timeline-label">${escapeHtml(s.label)}</div>
            <div class="status-timeline-time">${s.time ? escapeHtml(s.time) : "—"}</div>
          </div>
        `).join("")}
      </div>
    `;
  }

  const SHIPMENT_TIMELINE_HAPPY_PATH = ["PENDING", "READY", "SHIPPED", "IN_TRANSIT", "DELIVERED"];

  function buildShipmentTimelineSteps(shipment, events) {
    // 각 상태에 처음 도달한 시각 — status_events의 new_status를
    // 순서대로 훑어 처음 매칭된 시각만 쓴다. PENDING은 생성 시점의
    // 상태라 별도 이벤트가 없을 수 있어 shipment.created_at으로
    // 대체한다.
    const reachedAt = {};
    (events || []).forEach((e) => {
      if (e.new_status && reachedAt[e.new_status] === undefined) {
        reachedAt[e.new_status] = e.created_at;
      }
    });
    if (reachedAt.PENDING === undefined) reachedAt.PENDING = shipment.created_at;

    const currentIndex = SHIPMENT_TIMELINE_HAPPY_PATH.indexOf(shipment.status);

    const steps = SHIPMENT_TIMELINE_HAPPY_PATH.map((s, idx) => {
      let state;
      if (currentIndex === -1) {
        // 현재 상태가 정상 경로 밖(취소·반품·교환 등 분기)이다 —
        // 이미 지나온 단계까지만 done, 나머지는 pending으로 남긴다
        // (정상 경로로 왜곡해 표시하지 않는다).
        state = reachedAt[s] !== undefined ? "done" : "pending";
      } else if (idx < currentIndex) {
        state = "done";
      } else if (idx === currentIndex) {
        // 정상 경로의 마지막 단계(예: DELIVERED)에 도달했다면 "아직
        // 진행 중"이 아니라 완전히 끝난 것이므로 done(체크)으로
        // 표시한다 — current(진행 중 표시)는 다음 단계가 실제로
        // 남아있을 때만 쓴다.
        state = (idx === SHIPMENT_TIMELINE_HAPPY_PATH.length - 1) ? "done" : "current";
      } else {
        state = "pending";
      }
      return {
        label: HomezI18n.t(`ship.status.${s.toLowerCase()}`) || s,
        time: reachedAt[s] ? fmtDate(reachedAt[s]) : null,
        state,
      };
    });

    if (currentIndex === -1) {
      // 분기 상태를 마지막 단계로 별도 표시한다.
      const branchKey = String(shipment.status || "").toLowerCase();
      steps.push({
        label: HomezI18n.t(`ship.status.${branchKey}`) || shipment.status,
        time: reachedAt[shipment.status] ? fmtDate(reachedAt[shipment.status]) : null,
        state: "branch",
      });
    }

    return steps;
  }

  // 2026-09-10 UI 개선(시안 07 취소·반품 관리 참고) — ReturnOrder는
  // 각 단계 시각을 이벤트에서 되짚을 필요 없이 리소스 자신에
  // requested_at/approved_at/received_at/completed_at으로 이미
  // 갖고 있어 shipment보다 더 단순하게 만들 수 있다.
  const RETURN_ORDER_TIMELINE_HAPPY_PATH = ["REQUESTED", "APPROVED", "RECEIVED", "COMPLETED"];
  const RETURN_ORDER_TIMELINE_TIMESTAMP_FIELD = {
    REQUESTED: "requested_at", APPROVED: "approved_at",
    RECEIVED: "received_at", COMPLETED: "completed_at",
  };

  function buildReturnOrderTimelineSteps(ret) {
    const currentIndex = RETURN_ORDER_TIMELINE_HAPPY_PATH.indexOf(ret.status);

    const steps = RETURN_ORDER_TIMELINE_HAPPY_PATH.map((s, idx) => {
      const ts = ret[RETURN_ORDER_TIMELINE_TIMESTAMP_FIELD[s]];
      let state;
      if (currentIndex === -1) {
        // REJECTED 등 정상 경로 밖 — 실제 시각 데이터가 있는
        // 단계까지만 done으로 표시한다(왜곡 방지, ship과 동일 원칙).
        state = ts ? "done" : "pending";
      } else if (idx < currentIndex) {
        state = "done";
      } else if (idx === currentIndex) {
        // shipment와 동일한 이유(위 buildShipmentTimelineSteps 주석
        // 참고) — 마지막 단계(COMPLETED)는 done으로 표시한다.
        state = (idx === RETURN_ORDER_TIMELINE_HAPPY_PATH.length - 1) ? "done" : "current";
      } else {
        state = "pending";
      }
      return {
        label: HomezI18n.t(`ret.status.${s.toLowerCase()}`) || s,
        time: ts ? fmtDate(ts) : null,
        state,
      };
    });

    if (currentIndex === -1) {
      const branchKey = String(ret.status || "").toLowerCase();
      steps.push({
        label: HomezI18n.t(`ret.status.${branchKey}`) || ret.status,
        // 거절(REJECTED) 시각은 이 리소스 응답에 별도 필드가 없어
        // 추측하지 않는다 — 아래 "상태 이력" 테이블에서 정확한
        // 시각을 확인할 수 있다.
        time: null,
        state: "branch",
      });
    }

    return steps;
  }

  // 2026-09-10 UI 개선(시안 07 "환불 확인" 참고) — Refund도
  // ReturnOrder와 동일하게 자체 타임스탬프 필드(requested_at/
  // approved_at/executed_at)를 갖는다.
  const REFUND_TIMELINE_HAPPY_PATH = ["AWAITING_APPROVAL", "APPROVED", "EXECUTED"];
  const REFUND_TIMELINE_TIMESTAMP_FIELD = {
    AWAITING_APPROVAL: "requested_at", APPROVED: "approved_at", EXECUTED: "executed_at",
  };

  function buildRefundTimelineSteps(refund) {
    const currentIndex = REFUND_TIMELINE_HAPPY_PATH.indexOf(refund.status);

    const steps = REFUND_TIMELINE_HAPPY_PATH.map((s, idx) => {
      const ts = refund[REFUND_TIMELINE_TIMESTAMP_FIELD[s]];
      let state;
      if (currentIndex === -1) {
        state = ts ? "done" : "pending";
      } else if (idx < currentIndex) {
        state = "done";
      } else if (idx === currentIndex) {
        state = (idx === REFUND_TIMELINE_HAPPY_PATH.length - 1) ? "done" : "current";
      } else {
        state = "pending";
      }
      return {
        label: HomezI18n.t(`refund.status.${s.toLowerCase()}`) || s,
        time: ts ? fmtDate(ts) : null,
        state,
      };
    });

    if (currentIndex === -1) {
      const branchKey = String(refund.status || "").toLowerCase();
      steps.push({
        label: HomezI18n.t(`refund.status.${branchKey}`) || refund.status,
        time: null,
        state: "branch",
      });
    }

    return steps;
  }

  function simpleEmptyPanel(message, sub) {
    return `
      <div class="detail-panel">
        <div class="empty-state">
          <img src="/console/static/assets/homez-logo.png" alt="">
          <div class="empty-title">${escapeHtml(message)}</div>
          <div>${escapeHtml(sub || "")}</div>
        </div>
      </div>
    `;
  }

  // --------------------------------------------------
  // 재고 관리 (Inventory, Gate 3)
  // --------------------------------------------------

  const invState = { rows: [], detailSkuId: null };

  async function loadInventory() {
    invState.detailSkuId = null;
    await invRenderList();
  }

  async function invRenderList(candidateId) {
    const container = el("inv-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = candidateId ? `?product_candidate_id=${encodeURIComponent(candidateId)}` : "";
      rows = await apiFetch(`/inventory/skus${qs}`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    invState.rows = rows || [];

    const filterHtml = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("inv.filter.candidate_label")}</span>
            <input type="number" min="1" id="inv-filter-candidate" placeholder="${escapeHtml(HomezI18n.t("inv.filter.candidate_placeholder"))}" value="${candidateId ? escapeHtml(String(candidateId)) : ""}">
          </label>
          <div class="ls-filter-actions">
            <button type="button" class="btn btn-primary btn-sm" id="inv-search-btn">${HomezI18n.t("inv.search_btn")}</button>
          </div>
        </div>
      </div>
    `;

    const tableHtml = invState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("inv.empty"), HomezI18n.t("inv.empty_sub"))
      : `
        <div class="detail-panel">
          <div class="table-wrap">
            <table class="responsive-cards">
              <thead><tr>
                <th>${HomezI18n.t("inv.col_sku")}</th><th>${HomezI18n.t("inv.col_option")}</th>
                <th>${HomezI18n.t("inv.col_available")}</th><th>${HomezI18n.t("inv.col_reserved")}</th>
                <th>${HomezI18n.t("inv.col_safety")}</th><th>${HomezI18n.t("inv.col_stock_status")}</th>
                <th>${HomezI18n.t("inv.col_action")}</th>
              </tr></thead>
              <tbody>${invState.rows.map((r) => invRenderRow(r)).join("")}</tbody>
            </table>
          </div>
        </div>
      `;

    container.innerHTML = `${filterHtml}${tableHtml}<div id="inv-detail-panel"></div>`;

    el("inv-search-btn").addEventListener("click", () => {
      const val = el("inv-filter-candidate").value.trim();
      invRenderList(val ? Number(val) : undefined);
    });

    invState.rows.forEach((r) => {
      const btn = el(`inv-detail-btn-${r.id}`);
      if (btn) btn.addEventListener("click", () => invShowDetail(r.id));
    });

    if (invState.detailSkuId) {
      await invShowDetail(invState.detailSkuId);
    }
  }

  function invStockStatusPill(sku) {
    if (sku.available_qty <= 0) return `<span class="pill danger">${HomezI18n.t("inv.status_out")}</span>`;
    if (sku.available_qty <= sku.safety_stock) return `<span class="pill warn">${HomezI18n.t("inv.status_low")}</span>`;
    return `<span class="pill ok">${HomezI18n.t("inv.status_ok")}</span>`;
  }

  function invRenderRow(sku) {
    return `
      <tr>
        <td data-label="${HomezI18n.t("inv.col_sku")}">${escapeHtml(sku.sku_code)}</td>
        <td data-label="${HomezI18n.t("inv.col_option")}">${escapeHtml(sku.option_label || "")}</td>
        <td data-label="${HomezI18n.t("inv.col_available")}">${sku.available_qty}</td>
        <td data-label="${HomezI18n.t("inv.col_reserved")}">${sku.reserved_qty}</td>
        <td data-label="${HomezI18n.t("inv.col_safety")}">${sku.safety_stock}</td>
        <td data-label="${HomezI18n.t("inv.col_stock_status")}">${invStockStatusPill(sku)}</td>
        <td data-label="${HomezI18n.t("inv.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="inv-detail-btn-${sku.id}">${HomezI18n.t("inv.detail_btn")}</button></td>
      </tr>
    `;
  }

  async function invShowDetail(skuId) {
    invState.detailSkuId = skuId;
    const panel = el("inv-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let sku, ledger, reservations, channels;
    try {
      [sku, ledger, reservations, channels] = await Promise.all([
        apiFetch(`/inventory/skus/${skuId}`),
        apiFetch(`/inventory/skus/${skuId}/ledger`),
        apiFetch(`/inventory/skus/${skuId}/reservations`),
        apiFetch(`/inventory/skus/${skuId}/channel-mappings`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("inv.detail_heading")} — ${escapeHtml(sku.sku_code)}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="inv-detail-close-btn">${HomezI18n.t("inv.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("inv.col_available")}</dt><dd>${sku.available_qty}</dd>
          <dt>${HomezI18n.t("inv.col_reserved")}</dt><dd>${sku.reserved_qty}</dd>
          <dt>${HomezI18n.t("inv.col_safety")}</dt><dd>${sku.safety_stock}</dd>
        </dl>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("inv.restock_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("inv.quantity_label")}</span><input type="number" min="1" id="inv-restock-qty"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("inv.reason_label")}</span><input type="text" id="inv-restock-reason" placeholder="${escapeHtml(HomezI18n.t("inv.reason_placeholder"))}"></label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="inv-restock-submit-btn">${HomezI18n.t("inv.restock_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("inv.adjust_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("inv.delta_label")}</span><input type="number" id="inv-adjust-delta"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("inv.reason_label")}</span><input type="text" id="inv-adjust-reason" placeholder="${escapeHtml(HomezI18n.t("inv.reason_placeholder"))}"></label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="inv-adjust-submit-btn">${HomezI18n.t("inv.adjust_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("inv.ledger_heading")}</h3>
        ${ledger.length === 0 ? `<p class="stat-sub">${HomezI18n.t("inv.ledger_empty")}</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("inv.col_ledger_type")}</th><th>${HomezI18n.t("inv.col_ledger_delta")}</th><th>${HomezI18n.t("inv.col_ledger_available_after")}</th><th>${HomezI18n.t("inv.col_ledger_reserved_after")}</th><th>${HomezI18n.t("inv.col_ledger_reason")}</th><th>${HomezI18n.t("inv.col_ledger_date")}</th></tr></thead>
            <tbody>${ledger.map((e) => `
              <tr>
                <td data-label="${HomezI18n.t("inv.col_ledger_type")}">${statusPillHtml(e.event_type)}</td>
                <td data-label="${HomezI18n.t("inv.col_ledger_delta")}">${e.quantity_delta > 0 ? "+" : ""}${e.quantity_delta}</td>
                <td data-label="${HomezI18n.t("inv.col_ledger_available_after")}">${e.available_after}</td>
                <td data-label="${HomezI18n.t("inv.col_ledger_reserved_after")}">${e.reserved_after}</td>
                <td data-label="${HomezI18n.t("inv.col_ledger_reason")}">${e.reason ? escapeHtml(e.reason) : "—"}</td>
                <td data-label="${HomezI18n.t("inv.col_ledger_date")}">${fmtDate(e.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("inv.reservations_heading")}</h3>
        ${reservations.length === 0 ? `<p class="stat-sub">${HomezI18n.t("inv.reservations_empty")}</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("inv.col_res_qty")}</th><th>${HomezI18n.t("inv.col_res_status")}</th><th>${HomezI18n.t("inv.col_res_ref")}</th><th>${HomezI18n.t("inv.col_res_date")}</th></tr></thead>
            <tbody>${reservations.map((r) => `
              <tr>
                <td data-label="${HomezI18n.t("inv.col_res_qty")}">${r.quantity}</td>
                <td data-label="${HomezI18n.t("inv.col_res_status")}">${statusPillHtml(r.status)}</td>
                <td data-label="${HomezI18n.t("inv.col_res_ref")}">${r.reference_type ? `${escapeHtml(r.reference_type)} #${r.reference_id}` : "—"}</td>
                <td data-label="${HomezI18n.t("inv.col_res_date")}">${fmtDate(r.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("inv.channels_heading")}</h3>
        ${channels.length === 0 ? `<p class="stat-sub">${HomezI18n.t("inv.channels_empty")}</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("inv.col_ch_code")}</th><th>${HomezI18n.t("inv.col_ch_sku")}</th><th>${HomezI18n.t("inv.col_ch_status")}</th><th>${HomezI18n.t("inv.col_ch_last_sync")}</th><th>${HomezI18n.t("inv.col_ch_action")}</th></tr></thead>
            <tbody>${channels.map((c) => `
              <tr>
                <td data-label="${HomezI18n.t("inv.col_ch_code")}">${escapeHtml(c.channel_code)}</td>
                <td data-label="${HomezI18n.t("inv.col_ch_sku")}">${escapeHtml(c.channel_sku)}</td>
                <td data-label="${HomezI18n.t("inv.col_ch_status")}">${statusPillHtml(c.last_sync_status)}</td>
                <td data-label="${HomezI18n.t("inv.col_ch_last_sync")}">${c.last_synced_at ? fmtDate(c.last_synced_at) : "—"}</td>
                <td data-label="${HomezI18n.t("inv.col_ch_action")}"><button type="button" class="btn btn-ghost btn-sm" id="inv-ch-sync-${c.id}">${HomezI18n.t("inv.sync_btn")}</button></td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>
    `;

    el("inv-detail-close-btn").addEventListener("click", () => {
      invState.detailSkuId = null;
      panel.innerHTML = "";
    });

    el("inv-restock-submit-btn").addEventListener("click", async () => {
      const qty = Number(el("inv-restock-qty").value);
      const reason = el("inv-restock-reason").value.trim();
      if (!qty || qty <= 0) { toast(HomezI18n.t("inv.quantity_required_error"), "error"); return; }
      try {
        await apiFetch(`/inventory/skus/${skuId}/restock`, {
          method: "POST",
          body: JSON.stringify({ quantity: qty, reason: reason || null, idempotency_key: genIdemKey(`inv-restock-${skuId}`) }),
        });
        toast(HomezI18n.t("inv.restock_success"), "success");
        await invRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("inv.load_error"), "error");
      }
    });

    el("inv-adjust-submit-btn").addEventListener("click", async () => {
      const delta = Number(el("inv-adjust-delta").value);
      const reason = el("inv-adjust-reason").value.trim();
      if (!delta) { toast(HomezI18n.t("inv.quantity_required_error"), "error"); return; }
      if (!reason) { toast(HomezI18n.t("inv.reason_required_error"), "error"); return; }
      try {
        await apiFetch(`/inventory/skus/${skuId}/adjust`, {
          method: "POST",
          body: JSON.stringify({ quantity_delta: delta, reason, idempotency_key: genIdemKey(`inv-adjust-${skuId}`) }),
        });
        toast(HomezI18n.t("inv.adjust_success"), "success");
        await invRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("inv.load_error"), "error");
      }
    });

    channels.forEach((c) => {
      const btn = el(`inv-ch-sync-${c.id}`);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await apiFetch(`/inventory/channel-mappings/${c.id}/sync`, { method: "POST" });
          toast(HomezI18n.t("inv.sync_success"), "success");
          await invShowDetail(skuId);
        } catch (err) {
          toast(err.message || HomezI18n.t("inv.load_error"), "error");
          btn.disabled = false;
        }
      });
    });
  }

  VIEW_LOADERS["inventory"] = loadInventory;

  // --------------------------------------------------
  // 주문 · 발주 (Order / Purchase, Gate 4)
  // --------------------------------------------------

  let ofmTabsBound = false;
  const ordState = {
    rows: [], statusFilter: "", detailOrderId: null,
    connections: [], collectionPositions: [], unresolvedItems: [], inventorySkus: [],
  };
  const purState = { rows: [], statusFilter: "", detailPurchaseId: null };

  const ORDER_STATUS_OPTIONS = [
    "PENDING", "RESERVED", "PARTIALLY_SHIPPED", "SHIPPED", "DELIVERED",
    "CANCELLED", "RETURN_REQUESTED", "RETURNED", "EXCHANGE_REQUESTED", "EXCHANGED",
  ];
  const PURCHASE_STATUS_OPTIONS = ["REQUESTED", "CONFIRMED", "RECEIVED", "CANCELLED"];

  function loadOrderFulfillment(opts = {}) {
    if (!ofmTabsBound) {
      ofmTabsBound = true;
      el("ofm-tab-orders").addEventListener("click", () => ofmSwitchTab("orders"));
      el("ofm-tab-purchases").addEventListener("click", () => ofmSwitchTab("purchases"));
    }
    purState.detailPurchaseId = null;
    // Gate PT-2G — purchase_task 상세에서 "원 주문으로 이동"으로
    // 들어온 경우 해당 주문 상세를 바로 연다(ordRenderList()가 이미
    // detailOrderId를 보고 자동으로 열어주는 기존 패턴을 재사용).
    ordState.detailOrderId = opts.orderId || null;
    ofmSwitchTab("orders");
  }

  function ofmSwitchTab(tab) {
    const isOrders = tab === "orders";
    el("ofm-tab-orders").setAttribute("aria-selected", String(isOrders));
    el("ofm-tab-purchases").setAttribute("aria-selected", String(!isOrders));
    el("ofm-orders-panel").hidden = !isOrders;
    el("ofm-purchases-panel").hidden = isOrders;
    if (isOrders) {
      ordRenderList();
    } else {
      purRenderList();
    }
  }

  function statusFilterSelectHtml(id, options, selected) {
    return `
      <select id="${id}">
        <option value="">${HomezI18n.t("common.all")}</option>
        ${options.map((o) => `<option value="${o}" ${o === selected ? "selected" : ""}>${o}</option>`).join("")}
      </select>
    `;
  }

  async function ordRenderList() {
    const container = el("ord-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = ordState.statusFilter ? `?order_status=${encodeURIComponent(ordState.statusFilter)}&limit=200` : "?limit=200";
      [rows, ordState.connections, ordState.collectionPositions, ordState.unresolvedItems, ordState.inventorySkus] = await Promise.all([
        apiFetch(`/orders${qs}`),
        apiFetch("/store-connections"),
        apiFetch("/orders/collection-status"),
        apiFetch("/orders/unresolved-items"),
        apiFetch("/inventory/skus?limit=500"),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    ordState.rows = rows || [];

    const coupangConnections = (ordState.connections || []).filter(
      (c) => c.marketplace_code === "COUPANG" && c.connection_status === "CONNECTED",
    );
    const channelStatuses = ["ACCEPT", "INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY", "NONE_TRACKING"];
    const collectionHtml = `
      <div class="detail-panel">
        <h3>${HomezI18n.t("ord.collection_heading")}</h3>
        <div class="ls-filter-row">
          <div class="ls-filter-actions"><button type="button" class="btn btn-secondary btn-sm" id="ord-collection-run-all-btn">${HomezI18n.t("ord.collection_run_all")}</button></div>
        </div>
        <p class="stat-sub" id="ord-collection-run-all-result"></p>
        ${coupangConnections.length === 0 ? `<p class="stat-sub">${HomezI18n.t("ord.collection_no_account")}</p>` : `
          <div class="ls-filter-row">
            <label class="field"><span class="field-label">${HomezI18n.t("ord.collection_account")}</span>
              <select id="ord-collection-account">${coupangConnections.map((c) => `<option value="${c.id}">${escapeHtml(c.display_name)}</option>`).join("")}</select>
            </label>
            <label class="field"><span class="field-label">${HomezI18n.t("ord.collection_status")}</span>
              <select id="ord-collection-channel-status">${channelStatuses.map((s) => `<option value="${s}">${HomezI18n.t(`ord.coupang_status_${s.toLowerCase()}`)}</option>`).join("")}</select>
            </label>
            <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ord-collection-run-btn">${HomezI18n.t("ord.collection_run")}</button></div>
          </div>
          <p class="stat-sub" id="ord-collection-last-success"></p>
        `}
        <p class="stat-sub">${HomezI18n.t("ord.collection_read_only_note")}</p>
        <h4>${HomezI18n.t("ord.collection_unresolved")}: ${(ordState.unresolvedItems || []).length}</h4>
        ${(ordState.unresolvedItems || []).length === 0 ? "" : `<div class="table-wrap"><table class="responsive-cards"><tbody>${ordState.unresolvedItems.map((item) => `
          <tr><td>${escapeHtml(item.product_name_snapshot)}</td><td>${escapeHtml(item.channel_sku)}</td><td>${item.quantity}</td><td>
            ${item.quantity <= 0 ? escapeHtml(HomezI18n.t("ord.collection_zero_quantity")) : `<select id="ord-unresolved-sku-${item.id}"><option value="">${HomezI18n.t("ord.collection_select_sku")}</option>${(ordState.inventorySkus || []).map((sku) => `<option value="${sku.id}">${escapeHtml(sku.sku_code)} · ${escapeHtml(sku.option_label)}</option>`).join("")}</select> <button type="button" class="btn btn-ghost btn-sm" id="ord-unresolved-resolve-${item.id}">${HomezI18n.t("ord.collection_connect_sku")}</button>`}
          </td></tr>`).join("")}</tbody></table></div>`}
      </div>
    `;

    const filterHtml = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("ord.filter.status_label")}</span>
            ${statusFilterSelectHtml("ord-filter-status", ORDER_STATUS_OPTIONS, ordState.statusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="ord-refresh-btn">${HomezI18n.t("ord.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const tableHtml = ordState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("ord.empty"), HomezI18n.t("ord.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("ord.col_order_number")}</th><th>${HomezI18n.t("ord.col_channel")}</th>
            <th>${HomezI18n.t("ord.col_status")}</th><th>${HomezI18n.t("ord.col_buyer")}</th>
            <th>${HomezI18n.t("ord.col_total")}</th><th>${HomezI18n.t("ord.col_ordered_at")}</th>
            <th>${HomezI18n.t("ord.col_action")}</th>
          </tr></thead>
          <tbody>${ordState.rows.map((o) => `
            <tr>
              <td data-label="${HomezI18n.t("ord.col_order_number")}">${escapeHtml(o.order_number)}</td>
              <td data-label="${HomezI18n.t("ord.col_channel")}">${escapeHtml(o.channel_code)}</td>
              <td data-label="${HomezI18n.t("ord.col_status")}">${statusPillHtml(o.status)}</td>
              <td data-label="${HomezI18n.t("ord.col_buyer")}">${escapeHtml(o.buyer_name)}</td>
              <td data-label="${HomezI18n.t("ord.col_total")}">${fmtMoney(o.total_amount)}</td>
              <td data-label="${HomezI18n.t("ord.col_ordered_at")}">${fmtDate(o.ordered_at)}</td>
              <td data-label="${HomezI18n.t("ord.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="ord-detail-btn-${o.id}">${HomezI18n.t("ord.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${collectionHtml}${filterHtml}${tableHtml}<div id="ord-detail-panel"></div>`;

    async function updateCollectionPositionText() {
      const account = el("ord-collection-account");
      const status = el("ord-collection-channel-status");
      const target = el("ord-collection-last-success");
      if (!account || !status || !target) return;
      const found = (ordState.collectionPositions || []).find(
        (p) => p.store_connection_id === Number(account.value) && p.channel_status === status.value,
      );
      target.textContent = `${HomezI18n.t("ord.collection_last_success")}: ${found && found.last_successful_to ? fmtDate(found.last_successful_to) : HomezI18n.t("ord.collection_not_run")}${found && found.last_error_code ? ` · ${found.last_error_code}` : ""}`;
      try {
        const preview = await apiFetch("/orders/collect/coupang/preview", {
          method: "POST", body: JSON.stringify({
            store_connection_id: Number(account.value), channel_status: status.value,
          }),
        });
        target.textContent += ` · ${HomezI18n.t("ord.collection_period")}: ${fmtDate(preview.created_at_from)} ~ ${fmtDate(preview.created_at_to)}`;
      } catch (_) {
        // 실행 버튼이 실제 오류를 표시한다. 미리보기 실패로 목록 전체를 막지 않는다.
      }
    }
    const collectionAccount = el("ord-collection-account");
    const collectionStatus = el("ord-collection-channel-status");
    if (collectionAccount && collectionStatus) {
      collectionAccount.addEventListener("change", updateCollectionPositionText);
      collectionStatus.addEventListener("change", updateCollectionPositionText);
      updateCollectionPositionText();
      el("ord-collection-run-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        try {
          const result = await apiFetch("/orders/collect/coupang", {
            method: "POST",
            body: JSON.stringify({
              store_connection_id: Number(collectionAccount.value),
              channel_status: collectionStatus.value,
            }),
          });
          const message = HomezI18n.t("ord.collection_result", {
            received: result.received_order_count,
            created: result.new_fulfillment_count,
            updated: result.updated_fulfillment_count,
            duplicate: result.duplicate_fulfillment_count,
            unresolved: result.new_unresolved_item_count, failed: result.failed_order_count,
          });
          toast(`${result.status === "SUCCEEDED" ? HomezI18n.t("ord.collection_success") : result.status === "PARTIAL" ? HomezI18n.t("ord.collection_partial") : HomezI18n.t("ord.collection_failed")} ${message}`, result.status === "SUCCEEDED" ? "success" : "error");
          await ordRenderList();
        } catch (err) {
          toast(err.message || HomezI18n.t("ord.collection_failed"), "error");
        }
      }));
    }
    el("ord-collection-run-all-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const resultEl = el("ord-collection-run-all-result");
      if (resultEl) resultEl.textContent = HomezI18n.t("ord.collection_run_all_running");
      try {
        const result = await apiFetch("/orders/collect/all", { method: "POST" });
        if (result.total_connections === 0) {
          toast(HomezI18n.t("ord.collection_run_all_none"), "error");
          if (resultEl) resultEl.textContent = HomezI18n.t("ord.collection_run_all_none");
          return;
        }
        const message = HomezI18n.t("ord.collection_run_all_result", {
          connections: result.total_connections,
          succeeded: result.succeeded_runs,
          partial: result.partial_runs,
          failed: result.failed_runs,
        });
        const skippedMessage = result.skipped_marketplace_codes.length
          ? ` · ${HomezI18n.t("ord.collection_run_all_skipped", { codes: result.skipped_marketplace_codes.join(", ") })}`
          : "";
        if (resultEl) resultEl.textContent = message + skippedMessage;
        toast(`${result.failed_runs === 0 ? HomezI18n.t("ord.collection_success") : HomezI18n.t("ord.collection_partial")} ${message}`, result.failed_runs === 0 ? "success" : "error");
        await ordRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("ord.collection_failed"), "error");
        if (resultEl) resultEl.textContent = "";
      }
    }));

    (ordState.unresolvedItems || []).filter((item) => item.quantity > 0).forEach((item) => {
      const button = el(`ord-unresolved-resolve-${item.id}`);
      if (!button) return;
      button.addEventListener("click", () => withButtonGuard(button, async () => {
        const select = el(`ord-unresolved-sku-${item.id}`);
        const inventorySkuId = Number(select && select.value);
        if (!inventorySkuId) {
          toast(HomezI18n.t("ord.collection_select_sku"), "error");
          return;
        }
        try {
          await apiFetch(`/orders/unresolved-items/${item.id}/resolve`, {
            method: "POST", body: JSON.stringify({ inventory_sku_id: inventorySkuId }),
          });
          toast(HomezI18n.t("ord.collection_connect_success"), "success");
          await ordRenderList();
        } catch (err) {
          toast(err.message || HomezI18n.t("common.error_request_failed"), "error");
        }
      }));
    });

    el("ord-filter-status").addEventListener("change", (ev) => {
      ordState.statusFilter = ev.target.value;
      ordRenderList();
    });
    el("ord-refresh-btn").addEventListener("click", () => ordRenderList());

    ordState.rows.forEach((o) => {
      const btn = el(`ord-detail-btn-${o.id}`);
      if (btn) btn.addEventListener("click", () => ordShowDetail(o.id));
    });

    if (ordState.detailOrderId) {
      await ordShowDetail(ordState.detailOrderId);
    }
  }

  async function ordShowDetail(orderId) {
    ordState.detailOrderId = orderId;
    const panel = el("ord-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let order, items, statusEvents, ingestionEvents;
    try {
      [order, items, statusEvents, ingestionEvents] = await Promise.all([
        apiFetch(`/orders/${orderId}`),
        apiFetch(`/orders/${orderId}/items`),
        apiFetch(`/orders/${orderId}/status-events`),
        apiFetch(`/orders/${orderId}/ingestion-events`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    const NOT_CANCELLABLE = ["SHIPPED", "PARTIALLY_SHIPPED", "DELIVERED", "CANCELLED", "RETURNED", "EXCHANGED"];
    const cancellable = !NOT_CANCELLABLE.includes(order.status);
    const outOfStockItems = items.filter((it) => it.status === "OUT_OF_STOCK");
    const reservedItems = items.filter((it) => it.status === "RESERVED");

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("ord.detail_heading")} — ${escapeHtml(order.order_number)}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="ord-detail-close-btn">${HomezI18n.t("ord.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("ord.col_status")}</dt><dd>${statusPillHtml(order.status)}</dd>
          <dt>${HomezI18n.t("ord.col_channel")}</dt><dd>${escapeHtml(order.channel_code)} / ${escapeHtml(order.channel_order_id)}</dd>
          <dt>${HomezI18n.t("ord.col_buyer")}</dt><dd>${escapeHtml(order.buyer_name)}</dd>
          <dt>${HomezI18n.t("ord.col_total")}</dt><dd>${fmtMoney(order.total_amount)}</dd>
          <dt>${HomezI18n.t("ord.col_ordered_at")}</dt><dd>${fmtDate(order.ordered_at)}</dd>
        </dl>
        <div class="decision-actions">
          <button type="button" class="btn btn-ghost btn-sm" id="ord-sensitive-detail-btn">${HomezI18n.t("ord.sensitive_detail_btn")}</button>
          <button type="button" class="btn btn-ghost btn-sm" id="ord-sync-btn">${HomezI18n.t("ord.sync_channel_btn")}</button>
          <button type="button" class="btn btn-danger btn-sm" id="ord-cancel-btn" ${cancellable ? "" : "disabled"}>${HomezI18n.t("ord.cancel_btn")}</button>
          <button type="button" class="btn btn-ghost btn-sm" id="ord-goto-purchase-tasks-btn">${HomezI18n.t("ord.goto_purchase_tasks_btn")}</button>
        </div>
        ${!cancellable ? `<p class="stat-sub">${HomezI18n.t("ord.not_cancellable")}</p>` : ""}
        <div id="ord-sensitive-detail-panel"></div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ord.items_heading")}</h3>
        <div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("ord.col_item_name")}</th><th>${HomezI18n.t("ord.col_item_channel_sku")}</th>
            <th>${HomezI18n.t("ord.col_item_qty")}</th><th>${HomezI18n.t("ord.col_item_price")}</th>
            <th>${HomezI18n.t("ord.col_item_status")}</th>
          </tr></thead>
          <tbody>${items.map((it) => `
            <tr>
              <td data-label="${HomezI18n.t("ord.col_item_name")}">${escapeHtml(it.product_name_snapshot)}</td>
              <td data-label="${HomezI18n.t("ord.col_item_channel_sku")}">${escapeHtml(it.channel_sku)}</td>
              <td data-label="${HomezI18n.t("ord.col_item_qty")}">${it.quantity}</td>
              <td data-label="${HomezI18n.t("ord.col_item_price")}">${fmtMoney(it.unit_price)}</td>
              <td data-label="${HomezI18n.t("ord.col_item_status")}">${statusPillHtml(it.status)}</td>
            </tr>
          `).join("")}</tbody>
        </table></div>
      </div>

      ${outOfStockItems.length > 0 ? `
        <div class="detail-panel">
          <h3>${HomezI18n.t("ord.create_purchase_heading")}</h3>
          ${outOfStockItems.map((it) => `
            <div class="ls-filter-row">
              <span class="field-label">${escapeHtml(it.product_name_snapshot)} (${HomezI18n.t("ord.col_item_qty")}: ${it.quantity})</span>
              <label class="field"><span class="field-label">${HomezI18n.t("ord.supplier_id_label")}</span><input type="number" min="1" id="ord-po-supplier-${it.id}"></label>
              <label class="field"><span class="field-label">${HomezI18n.t("ord.unit_cost_label")}</span><input type="number" min="0" step="0.01" id="ord-po-cost-${it.id}"></label>
              <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ord-po-submit-${it.id}">${HomezI18n.t("ord.create_purchase_submit")}</button></div>
            </div>
          `).join("")}
        </div>
      ` : ""}

      ${reservedItems.length > 0 ? `
        <div class="detail-panel">
          <h3>${HomezI18n.t("ord.create_shipment_heading")}</h3>
          <div>${reservedItems.map((it) => `
            <label class="field-inline"><input type="checkbox" class="ord-ship-item-cb" value="${it.id}"> ${escapeHtml(it.product_name_snapshot)} (${it.quantity})</label>
          `).join(" ")}</div>
          <div class="ls-filter-row">
            <label class="field"><span class="field-label">${HomezI18n.t("ord.courier_label")}</span><input type="text" id="ord-ship-courier"></label>
            <label class="field"><span class="field-label">${HomezI18n.t("ord.invoice_number_label")}</span><input type="text" id="ord-ship-invoice"></label>
            <label class="field"><span class="field-label">${HomezI18n.t("ord.tracking_url_label")}</span><input type="text" id="ord-ship-tracking"></label>
            <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ord-ship-submit-btn">${HomezI18n.t("ord.create_shipment_submit")}</button></div>
          </div>
        </div>
      ` : ""}

      <div class="detail-panel">
        <h3>${HomezI18n.t("ord.status_events_heading")}</h3>
        ${statusEvents.length === 0 ? `<p class="stat-sub">—</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("ord.col_event_prev")}</th><th>${HomezI18n.t("ord.col_event_new")}</th><th>${HomezI18n.t("ord.col_event_source")}</th><th>${HomezI18n.t("ord.col_event_reason")}</th><th>${HomezI18n.t("ord.col_event_date")}</th></tr></thead>
            <tbody>${statusEvents.map((e) => `
              <tr>
                <td data-label="${HomezI18n.t("ord.col_event_prev")}">${e.previous_status ? escapeHtml(e.previous_status) : "—"}</td>
                <td data-label="${HomezI18n.t("ord.col_event_new")}">${statusPillHtml(e.new_status)}</td>
                <td data-label="${HomezI18n.t("ord.col_event_source")}">${escapeHtml(e.source)}</td>
                <td data-label="${HomezI18n.t("ord.col_event_reason")}">${e.reason ? escapeHtml(e.reason) : "—"}</td>
                <td data-label="${HomezI18n.t("ord.col_event_date")}">${fmtDate(e.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ord.ingestion_events_heading")}</h3>
        ${ingestionEvents.length === 0 ? `<p class="stat-sub">—</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("ord.col_status")}</th><th>${HomezI18n.t("ord.col_event_date")}</th></tr></thead>
            <tbody>${ingestionEvents.map((e) => `
              <tr>
                <td data-label="${HomezI18n.t("ord.col_status")}">${statusPillHtml(e.status)}</td>
                <td data-label="${HomezI18n.t("ord.col_event_date")}">${fmtDate(e.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>
    `;

    el("ord-detail-close-btn").addEventListener("click", () => {
      ordState.detailOrderId = null;
      panel.innerHTML = "";
    });

    el("ord-sensitive-detail-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const token = await promptRecentAuthToken();
      if (token === null) return;
      try {
        const detail = await apiFetch(`/orders/${orderId}/sensitive-detail`, {
          headers: { "X-Recent-Auth-Token": token },
        });
        el("ord-sensitive-detail-panel").innerHTML = `<h3>${HomezI18n.t("ord.sensitive_detail_heading")}</h3><dl class="kv-list">
          <dt>${HomezI18n.t("ord.receiver_name")}</dt><dd>${escapeHtml(detail.receiver_name)}</dd>
          <dt>${HomezI18n.t("ord.receiver_phone")}</dt><dd>${escapeHtml(detail.receiver_phone)}</dd>
          <dt>${HomezI18n.t("ord.receiver_address")}</dt><dd>${escapeHtml(detail.receiver_address)} (${escapeHtml(detail.receiver_zipcode)})</dd>
        </dl>`;
      } catch (err) {
        toast(err.message || HomezI18n.t("common.error_request_failed"), "error");
      }
    }));

    el("ord-goto-purchase-tasks-btn").addEventListener("click", () => {
      navigateTo("purchase-task", { source_order_id: orderId });
    });

    el("ord-sync-btn").addEventListener("click", async () => {
      try {
        await apiFetch(`/orders/${orderId}/sync-channel-status`, { method: "POST" });
        toast(HomezI18n.t("ord.sync_channel_success"), "success");
        await ordShowDetail(orderId);
      } catch (err) {
        toast(err.message || "", "error");
      }
    });

    const cancelBtn = el("ord-cancel-btn");
    if (cancelBtn && !cancelBtn.disabled) {
      cancelBtn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("ord.cancel_confirm_title"),
          body: HomezI18n.t("ord.cancel_confirm_body"),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/orders/${orderId}/cancel`, {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("ord.cancel_success"), "success");
          await ordRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    outOfStockItems.forEach((it) => {
      const btn = el(`ord-po-submit-${it.id}`);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        const supplierId = Number(el(`ord-po-supplier-${it.id}`).value);
        const unitCost = Number(el(`ord-po-cost-${it.id}`).value);
        if (!supplierId || unitCost < 0 || Number.isNaN(unitCost)) {
          toast(HomezI18n.t("inv.quantity_required_error"), "error");
          return;
        }
        try {
          await apiFetch("/purchases", {
            method: "POST",
            body: JSON.stringify({
              order_id: orderId,
              supplier_id: supplierId,
              items: [{ order_item_id: it.id, unit_cost: unitCost }],
              idempotency_key: genIdemKey(`purchase-${orderId}-${it.id}`),
            }),
          });
          toast(HomezI18n.t("ord.create_purchase_success"), "success");
          await ordShowDetail(orderId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    });

    const shipSubmitBtn = el("ord-ship-submit-btn");
    if (shipSubmitBtn) {
      shipSubmitBtn.addEventListener("click", async () => {
        const selected = Array.from(document.querySelectorAll(".ord-ship-item-cb:checked")).map((cb) => Number(cb.value));
        if (selected.length === 0) {
          toast(HomezI18n.t("ord.select_items_error"), "error");
          return;
        }
        const courier = el("ord-ship-courier").value.trim();
        const invoice = el("ord-ship-invoice").value.trim();
        const tracking = el("ord-ship-tracking").value.trim();
        if (!courier || !invoice) {
          toast(HomezI18n.t("inv.reason_required_error"), "error");
          return;
        }
        try {
          await apiFetch(`/shipments/orders/${orderId}`, {
            method: "POST",
            body: JSON.stringify({
              order_item_ids: selected,
              courier,
              invoice_number: invoice,
              tracking_url: tracking || null,
              idempotency_key: genIdemKey(`shipment-${orderId}`),
            }),
          });
          toast(HomezI18n.t("ord.create_shipment_success"), "success");
          await ordShowDetail(orderId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }
  }

  async function purRenderList() {
    const container = el("pur-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = purState.statusFilter ? `?purchase_status=${encodeURIComponent(purState.statusFilter)}&limit=200` : "?limit=200";
      rows = await apiFetch(`/purchases${qs}`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    purState.rows = rows || [];

    const filterHtml = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pur.filter.status_label")}</span>
            ${statusFilterSelectHtml("pur-filter-status", PURCHASE_STATUS_OPTIONS, purState.statusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="pur-refresh-btn">${HomezI18n.t("ord.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const tableHtml = purState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("pur.empty"), HomezI18n.t("pur.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("pur.col_id")}</th><th>${HomezI18n.t("pur.col_order_id")}</th>
            <th>${HomezI18n.t("pur.col_supplier_id")}</th><th>${HomezI18n.t("pur.col_status")}</th>
            <th>${HomezI18n.t("pur.col_total_cost")}</th><th>${HomezI18n.t("pur.col_requested_at")}</th>
            <th>${HomezI18n.t("pur.col_action")}</th>
          </tr></thead>
          <tbody>${purState.rows.map((p) => `
            <tr>
              <td data-label="${HomezI18n.t("pur.col_id")}">#${p.id}</td>
              <td data-label="${HomezI18n.t("pur.col_order_id")}">#${p.order_id}</td>
              <td data-label="${HomezI18n.t("pur.col_supplier_id")}">#${p.supplier_id}</td>
              <td data-label="${HomezI18n.t("pur.col_status")}">${statusPillHtml(p.status)}</td>
              <td data-label="${HomezI18n.t("pur.col_total_cost")}">${fmtMoney(p.total_cost)}</td>
              <td data-label="${HomezI18n.t("pur.col_requested_at")}">${fmtDate(p.requested_at)}</td>
              <td data-label="${HomezI18n.t("pur.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="pur-detail-btn-${p.id}">${HomezI18n.t("pur.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${filterHtml}${tableHtml}<div id="pur-detail-panel"></div>`;

    el("pur-filter-status").addEventListener("change", (ev) => {
      purState.statusFilter = ev.target.value;
      purRenderList();
    });
    el("pur-refresh-btn").addEventListener("click", () => purRenderList());

    purState.rows.forEach((p) => {
      const btn = el(`pur-detail-btn-${p.id}`);
      if (btn) btn.addEventListener("click", () => purShowDetail(p.id));
    });

    if (purState.detailPurchaseId) {
      await purShowDetail(purState.detailPurchaseId);
    }
  }

  async function purShowDetail(purchaseId) {
    purState.detailPurchaseId = purchaseId;
    const panel = el("pur-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let purchase, items;
    try {
      [purchase, items] = await Promise.all([
        apiFetch(`/purchases/${purchaseId}`),
        apiFetch(`/purchases/${purchaseId}/items`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("pur.detail_heading")} — #${purchase.id}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="pur-detail-close-btn">${HomezI18n.t("pur.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("pur.col_status")}</dt><dd>${statusPillHtml(purchase.status)}</dd>
          <dt>${HomezI18n.t("pur.col_order_id")}</dt><dd>#${purchase.order_id}</dd>
          <dt>${HomezI18n.t("pur.col_supplier_id")}</dt><dd>#${purchase.supplier_id}</dd>
          <dt>${HomezI18n.t("pur.col_total_cost")}</dt><dd>${fmtMoney(purchase.total_cost)}</dd>
          <dt>${HomezI18n.t("pur.memo_label")}</dt><dd>${purchase.memo ? escapeHtml(purchase.memo) : "—"}</dd>
        </dl>
        <div class="decision-actions">
          <button type="button" class="btn btn-primary btn-sm" id="pur-confirm-btn" ${purchase.status === "REQUESTED" ? "" : "disabled"}>${HomezI18n.t("pur.confirm_btn")}</button>
          <button type="button" class="btn btn-primary btn-sm" id="pur-receive-btn" ${purchase.status === "CONFIRMED" ? "" : "disabled"}>${HomezI18n.t("pur.receive_btn")}</button>
          <button type="button" class="btn btn-danger btn-sm" id="pur-cancel-btn" ${["REQUESTED", "CONFIRMED"].includes(purchase.status) ? "" : "disabled"}>${HomezI18n.t("pur.cancel_btn")}</button>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("pur.items_heading")}</h3>
        <div class="table-wrap"><table class="responsive-cards">
          <thead><tr><th>${HomezI18n.t("pur.col_item_qty")}</th><th>${HomezI18n.t("pur.col_item_unit_cost")}</th><th>${HomezI18n.t("pur.col_item_subtotal")}</th></tr></thead>
          <tbody>${items.map((it) => `
            <tr>
              <td data-label="${HomezI18n.t("pur.col_item_qty")}">${it.quantity}</td>
              <td data-label="${HomezI18n.t("pur.col_item_unit_cost")}">${fmtMoney(it.unit_cost)}</td>
              <td data-label="${HomezI18n.t("pur.col_item_subtotal")}">${fmtMoney(it.subtotal_cost)}</td>
            </tr>
          `).join("")}</tbody>
        </table></div>
      </div>
    `;

    el("pur-detail-close-btn").addEventListener("click", () => {
      purState.detailPurchaseId = null;
      panel.innerHTML = "";
    });

    const confirmBtn = el("pur-confirm-btn");
    if (confirmBtn && !confirmBtn.disabled) {
      confirmBtn.addEventListener("click", async () => {
        try {
          await apiFetch(`/purchases/${purchaseId}/confirm`, { method: "POST" });
          toast(HomezI18n.t("pur.confirm_success"), "success");
          await purShowDetail(purchaseId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const receiveBtn = el("pur-receive-btn");
    if (receiveBtn && !receiveBtn.disabled) {
      receiveBtn.addEventListener("click", async () => {
        try {
          await apiFetch(`/purchases/${purchaseId}/receive`, { method: "POST" });
          toast(HomezI18n.t("pur.receive_success"), "success");
          await purShowDetail(purchaseId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const cancelBtn = el("pur-cancel-btn");
    if (cancelBtn && !cancelBtn.disabled) {
      cancelBtn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("pur.cancel_confirm_title"),
          body: HomezI18n.t("pur.cancel_confirm_body"),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/purchases/${purchaseId}/cancel`, {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("pur.cancel_success"), "success");
          await purRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }
  }

  VIEW_LOADERS["order-fulfillment"] = loadOrderFulfillment;

  // --------------------------------------------------
  // 공급처·발주(supplier-sourcing, 2026-08-21 작업 1~4) — 공급처
  // 검색·연결(관계)·상품 연결·비교·발주안·승인·전송·재시도·입고까지
  // 한 화면에서 실행한다. 위 order-fulfillment(ord-*/pur-*)의 ID·
  // 함수와 절대 겹치지 않도록 sps-* 접두를 쓰는 병행 구현이다 —
  // 이미 검증된 order-fulfillment 코드는 건드리지 않는다(회귀 위험
  // 최소화 목적의 의도적 중복).
  // --------------------------------------------------

  const SPS_PROVIDER_OPTIONS = ["FAKE", "MANUAL", "CSV"];

  const spsState = {
    tab: "search",
    searchProvider: "FAKE",
    searchResults: [],
    searched: false,
    prefillSupplierId: null,
    relations: [],
    publicSuppliers: [],
    candidates: [],
    linkCandidateId: null,
    links: [],
    bestLink: null,
    purchases: [],
    purchaseFilter: null,
    detailPurchaseId: null,
    outOfStockItems: [],
    retryWaitTimer: null,
  };

  let spsTabsBound = false;

  function spsIsAdmin() {
    const user = getCurrentUser();
    const role = (user && user.role) || "";
    return role === "ADMIN" || role === "SUPER_ADMIN";
  }

  function spsPermissionDeniedNoteHtml() {
    return `<p class="stat-sub">${HomezI18n.t("sps.permission_denied_note")}</p>`;
  }

  function spsParseUtcDateMs(isoString) {
    // Gate M-1(2026-08-21) — HomezI18n.parseUtcDate()로 위임(앱 전체
    // 공용 정규화 지점, 동작은 그대로).
    const d = HomezI18n.parseUtcDate(isoString);
    return d ? d.getTime() : null;
  }

  function loadSupplierSourcing(opts = {}) {
    if (!spsTabsBound) {
      spsTabsBound = true;
      el("sps-tab-search").addEventListener("click", () => spsSwitchTab("search"));
      el("sps-tab-relations").addEventListener("click", () => spsSwitchTab("relations"));
      el("sps-tab-links").addEventListener("click", () => spsSwitchTab("links"));
      el("sps-tab-purchases").addEventListener("click", () => spsSwitchTab("purchases"));
    }
    spsState.detailPurchaseId = null;
    if (opts && opts.filter) {
      spsState.purchaseFilter = opts.filter;
      spsSwitchTab("purchases");
    } else {
      spsSwitchTab(spsState.tab || "search");
    }
  }

  function spsSwitchTab(tab) {
    spsState.tab = tab;
    const tabs = { search: "sps-tab-search", relations: "sps-tab-relations", links: "sps-tab-links", purchases: "sps-tab-purchases" };
    const panels = { search: "sps-search-panel", relations: "sps-relations-panel", links: "sps-links-panel", purchases: "sps-purchases-panel" };
    Object.keys(tabs).forEach((key) => {
      el(tabs[key]).setAttribute("aria-selected", String(key === tab));
      el(panels[key]).hidden = key !== tab;
    });
    if (tab === "search") spsRenderSearch();
    else if (tab === "relations") spsRenderRelations();
    else if (tab === "links") spsRenderLinks();
    else if (tab === "purchases") spsRenderPurchases();
  }

  async function spsRenderSearch() {
    const container = el("sps-search-content");
    container.innerHTML = `
      <div class="detail-panel">
        <p class="stat-sub">${HomezI18n.t("sps.search_safety_note")}</p>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.search_provider_label")}</span>
            <select id="sps-search-provider">
              ${SPS_PROVIDER_OPTIONS.map((p) => `<option value="${p}" ${p === spsState.searchProvider ? "selected" : ""}>${p}</option>`).join("")}
            </select>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.search_product_name_label")}</span>
            <input type="text" id="sps-search-product-name">
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.search_brand_label")}</span>
            <input type="text" id="sps-search-brand">
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.search_barcode_label")}</span>
            <input type="text" id="sps-search-barcode">
          </label>
        </div>
        <div class="ls-filter-row" ${spsState.searchProvider === "CSV" ? "" : "hidden"}>
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.search_csv_rows_label")}</span>
            <textarea id="sps-search-csv-rows" rows="4" placeholder='[{"supplier_id":1,"supplier_product_id":"SKU-1","product_name":"...","unit_cost":1000}]'></textarea>
          </label>
        </div>
        <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="sps-search-submit-btn">${HomezI18n.t("sps.search_submit_btn")}</button></div>
      </div>
      <div id="sps-search-results"></div>
    `;

    el("sps-search-provider").addEventListener("change", (ev) => {
      spsState.searchProvider = ev.target.value;
      spsRenderSearch();
    });
    el("sps-search-submit-btn").addEventListener("click", () => spsRunSearch());

    spsRenderSearchResults();
  }

  async function spsRunSearch() {
    const productName = el("sps-search-product-name").value.trim();
    if (!productName) {
      toast(HomezI18n.t("sps.search_product_name_required"), "error");
      return;
    }
    const body = {
      provider_code: spsState.searchProvider,
      product_name: productName,
      brand: el("sps-search-brand").value.trim() || null,
      barcode: el("sps-search-barcode").value.trim() || null,
      csv_rows: [],
    };
    if (spsState.searchProvider === "CSV") {
      const raw = el("sps-search-csv-rows").value.trim();
      if (raw) {
        try {
          body.csv_rows = JSON.parse(raw);
        } catch (err) {
          toast(HomezI18n.t("sps.search_csv_parse_error"), "error");
          return;
        }
      }
    }
    const resultsEl = el("sps-search-results");
    resultsEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    try {
      spsState.searchResults = await apiFetch("/sourcing/search", { method: "POST", body: JSON.stringify(body) });
      spsState.searched = true;
    } catch (err) {
      renderErrorState(resultsEl, err);
      return;
    }
    spsRenderSearchResults();
  }

  function spsRenderSearchResults() {
    const resultsEl = el("sps-search-results");
    if (!resultsEl) return;
    if (!spsState.searched) {
      resultsEl.innerHTML = "";
      return;
    }
    if (spsState.searchResults.length === 0) {
      resultsEl.innerHTML = simpleEmptyPanel(HomezI18n.t("sps.search_empty"), HomezI18n.t("sps.search_empty_sub"));
      return;
    }
    resultsEl.innerHTML = `
      <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
        <thead><tr>
          <th>${HomezI18n.t("sps.col_source")}</th><th>${HomezI18n.t("sps.col_product_name")}</th>
          <th>${HomezI18n.t("sps.col_unit_cost")}</th><th>${HomezI18n.t("sps.col_moq")}</th>
          <th>${HomezI18n.t("sps.col_lead_time")}</th><th>${HomezI18n.t("sps.col_shipping_cost")}</th>
          <th>${HomezI18n.t("sps.col_stock")}</th><th>${HomezI18n.t("sps.col_action")}</th>
        </tr></thead>
        <tbody>${spsState.searchResults.map((r, idx) => {
          const isDemo = r.source === "FAKE_PROVIDER" || !r.supplier_id;
          return `
            <tr>
              <td data-label="${HomezI18n.t("sps.col_source")}">${escapeHtml(r.source)}${isDemo ? ` <span class="pill warn">${HomezI18n.t("sps.demo_badge")}</span>` : ""}</td>
              <td data-label="${HomezI18n.t("sps.col_product_name")}">${escapeHtml(r.product_name)}</td>
              <td data-label="${HomezI18n.t("sps.col_unit_cost")}">${fmtMoney(r.unit_cost)}</td>
              <td data-label="${HomezI18n.t("sps.col_moq")}">${r.moq}</td>
              <td data-label="${HomezI18n.t("sps.col_lead_time")}">${r.lead_time_days ?? "—"}</td>
              <td data-label="${HomezI18n.t("sps.col_shipping_cost")}">${r.shipping_cost != null ? fmtMoney(r.shipping_cost) : "—"}</td>
              <td data-label="${HomezI18n.t("sps.col_stock")}">${r.stock ?? "—"}</td>
              <td data-label="${HomezI18n.t("sps.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="sps-search-use-${idx}" ${isDemo ? "disabled" : ""}>${HomezI18n.t("sps.use_result_btn")}</button></td>
            </tr>
          `;
        }).join("")}</tbody>
      </table></div></div>
      <p class="stat-sub">${HomezI18n.t("sps.demo_result_note")}</p>
    `;

    spsState.searchResults.forEach((r, idx) => {
      const btn = el(`sps-search-use-${idx}`);
      if (btn && !btn.disabled) {
        btn.addEventListener("click", () => {
          spsState.prefillSupplierId = r.supplier_id;
          spsSwitchTab("relations");
          toast(HomezI18n.t("sps.use_result_hint"), "info");
        });
      }
    });
  }

  async function spsRenderRelations() {
    const container = el("sps-relations-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let relations, suppliers;
    try {
      [relations, suppliers] = await Promise.all([
        apiFetch("/sourcing/relations"),
        apiFetch("/sourcing/suppliers/public"),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    spsState.relations = relations || [];
    spsState.publicSuppliers = suppliers || [];

    const isAdmin = spsIsAdmin();
    const supplierName = (id) => {
      const s = spsState.publicSuppliers.find((x) => x.id === id);
      return s ? s.name : `#${id}`;
    };

    const createFormHtml = isAdmin ? `
      <div class="detail-panel">
        <h3>${HomezI18n.t("sps.relation_create_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.relation_supplier_label")}</span>
            <select id="sps-rel-supplier">
              ${spsState.publicSuppliers.map((s) => `<option value="${s.id}" ${spsState.prefillSupplierId === s.id ? "selected" : ""}>${escapeHtml(s.name)} (#${s.id})</option>`).join("")}
            </select>
          </label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.relation_contact_name_label")}</span><input type="text" id="sps-rel-contact-name"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.relation_contact_phone_label")}</span><input type="text" id="sps-rel-contact-phone"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.relation_contact_email_label")}</span><input type="text" id="sps-rel-contact-email"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.relation_payment_terms_label")}</span><input type="text" id="sps-rel-payment-terms"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.relation_credential_ref_label")}</span><input type="text" id="sps-rel-credential-ref"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.relation_notes_label")}</span><input type="text" id="sps-rel-notes"></label>
        </div>
        <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="sps-rel-create-btn">${HomezI18n.t("sps.relation_create_submit")}</button></div>
      </div>
    ` : spsPermissionDeniedNoteHtml();

    const listHtml = spsState.relations.length === 0
      ? simpleEmptyPanel(HomezI18n.t("sps.relation_empty"), HomezI18n.t("sps.relation_empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("sps.col_supplier")}</th><th>${HomezI18n.t("sps.col_approval_status")}</th>
            <th>${HomezI18n.t("sps.relation_contact_name_label")}</th><th>${HomezI18n.t("sps.relation_payment_terms_label")}</th>
            ${isAdmin ? `<th>${HomezI18n.t("sps.col_action")}</th>` : ""}
          </tr></thead>
          <tbody>${spsState.relations.map((r) => `
            <tr>
              <td data-label="${HomezI18n.t("sps.col_supplier")}">${escapeHtml(supplierName(r.supplier_id))}</td>
              <td data-label="${HomezI18n.t("sps.col_approval_status")}">${statusPillHtml(r.approval_status)}</td>
              <td data-label="${HomezI18n.t("sps.relation_contact_name_label")}">${r.contact_name != null ? escapeHtml(r.contact_name) : (isAdmin ? "—" : HomezI18n.t("sps.field_hidden"))}</td>
              <td data-label="${HomezI18n.t("sps.relation_payment_terms_label")}">${r.payment_terms != null ? escapeHtml(r.payment_terms) : (isAdmin ? "—" : HomezI18n.t("sps.field_hidden"))}</td>
              ${isAdmin ? `<td data-label="${HomezI18n.t("sps.col_action")}">${r.approval_status === "PENDING" ? `
                  <button type="button" class="btn btn-primary btn-sm" id="sps-rel-approve-${r.id}">${HomezI18n.t("sps.relation_approve_btn")}</button>
                  <button type="button" class="btn btn-danger btn-sm" id="sps-rel-reject-${r.id}">${HomezI18n.t("sps.relation_reject_btn")}</button>
                ` : "—"}</td>` : ""}
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${createFormHtml}${listHtml}`;

    if (!isAdmin) return;

    const createBtn = el("sps-rel-create-btn");
    if (createBtn) {
      createBtn.addEventListener("click", async () => {
        const supplierId = Number(el("sps-rel-supplier").value);
        if (!supplierId) {
          toast(HomezI18n.t("sps.relation_supplier_required"), "error");
          return;
        }
        try {
          await apiFetch("/sourcing/relations", {
            method: "POST",
            body: JSON.stringify({
              supplier_id: supplierId,
              contact_name: el("sps-rel-contact-name").value.trim() || null,
              contact_phone: el("sps-rel-contact-phone").value.trim() || null,
              contact_email: el("sps-rel-contact-email").value.trim() || null,
              payment_terms: el("sps-rel-payment-terms").value.trim() || null,
              credential_reference: el("sps-rel-credential-ref").value.trim() || null,
              notes: el("sps-rel-notes").value.trim() || null,
            }),
          });
          toast(HomezI18n.t("sps.relation_create_success"), "success");
          spsState.prefillSupplierId = null;
          await spsRenderRelations();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    spsState.relations.forEach((r) => {
      if (r.approval_status !== "PENDING") return;
      const approveBtn = el(`sps-rel-approve-${r.id}`);
      if (approveBtn) {
        approveBtn.addEventListener("click", async () => {
          try {
            await apiFetch(`/sourcing/relations/${r.id}/approval`, { method: "PATCH", body: JSON.stringify({ approve: true }) });
            toast(HomezI18n.t("sps.relation_approve_success"), "success");
            await spsRenderRelations();
          } catch (err) {
            toast(err.message || "", "error");
          }
        });
      }
      const rejectBtn = el(`sps-rel-reject-${r.id}`);
      if (rejectBtn) {
        rejectBtn.addEventListener("click", async () => {
          try {
            await apiFetch(`/sourcing/relations/${r.id}/approval`, { method: "PATCH", body: JSON.stringify({ approve: false }) });
            toast(HomezI18n.t("sps.relation_reject_success"), "success");
            await spsRenderRelations();
          } catch (err) {
            toast(err.message || "", "error");
          }
        });
      }
    });
  }

  async function spsRenderLinks() {
    const container = el("sps-links-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let candidates, suppliers;
    try {
      [candidates, suppliers] = await Promise.all([
        apiFetch("/sourcing/product-candidates?approved_only=true"),
        apiFetch("/sourcing/suppliers/public"),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    spsState.candidates = candidates || [];
    spsState.publicSuppliers = suppliers || [];
    if (spsState.linkCandidateId == null && spsState.candidates.length > 0) {
      spsState.linkCandidateId = spsState.candidates[0].id;
    }

    if (spsState.candidates.length === 0) {
      container.innerHTML = simpleEmptyPanel(HomezI18n.t("sps.links_no_candidates"), HomezI18n.t("sps.links_no_candidates_sub"));
      return;
    }

    container.innerHTML = `
      <div class="detail-panel">
        <label class="field">
          <span class="field-label">${HomezI18n.t("sps.links_candidate_label")}</span>
          <select id="sps-links-candidate-select">
            ${spsState.candidates.map((c) => `<option value="${c.id}" ${c.id === spsState.linkCandidateId ? "selected" : ""}>${escapeHtml(c.product_name)} (#${c.id})</option>`).join("")}
          </select>
        </label>
      </div>
      <div id="sps-links-detail"></div>
    `;

    el("sps-links-candidate-select").addEventListener("change", (ev) => {
      spsState.linkCandidateId = Number(ev.target.value);
      spsRenderLinksDetail();
    });

    await spsRenderLinksDetail();
  }

  async function spsRenderLinksDetail() {
    const detailEl = el("sps-links-detail");
    if (!detailEl || spsState.linkCandidateId == null) return;
    detailEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    const candidateId = spsState.linkCandidateId;
    let links, best;
    try {
      [links, best] = await Promise.all([
        apiFetch(`/sourcing/candidates/${candidateId}/links`),
        apiFetch(`/sourcing/candidates/${candidateId}/best-link`),
      ]);
    } catch (err) {
      renderErrorState(detailEl, err);
      return;
    }
    spsState.links = links || [];
    spsState.bestLink = best || null;

    const isAdmin = spsIsAdmin();
    const supplierName = (id) => {
      const s = spsState.publicSuppliers.find((x) => x.id === id);
      return s ? s.name : `#${id}`;
    };

    const createFormHtml = isAdmin ? `
      <div class="detail-panel">
        <h3>${HomezI18n.t("sps.link_create_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("sps.link_supplier_label")}</span>
            <select id="sps-link-supplier">
              ${spsState.publicSuppliers.map((s) => `<option value="${s.id}">${escapeHtml(s.name)} (#${s.id})</option>`).join("")}
            </select>
          </label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_supplier_sku_label")}</span><input type="text" id="sps-link-supplier-sku"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_unit_cost_label")}</span><input type="number" min="0.01" step="0.01" id="sps-link-unit-cost"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_moq_label")}</span><input type="number" min="1" id="sps-link-moq" value="1"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_lead_time_label")}</span><input type="number" min="0" id="sps-link-lead-time"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_shipping_cost_label")}</span><input type="number" min="0" step="0.01" id="sps-link-shipping-cost"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_stock_label")}</span><input type="number" min="0" id="sps-link-stock"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_price_valid_until_label")}</span><input type="date" id="sps-link-price-valid-until"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("sps.link_return_policy_label")}</span><input type="text" id="sps-link-return-policy"></label>
        </div>
        <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="sps-link-create-btn">${HomezI18n.t("sps.link_create_submit")}</button></div>
      </div>
    ` : spsPermissionDeniedNoteHtml();

    const tableHtml = spsState.links.length === 0
      ? simpleEmptyPanel(HomezI18n.t("sps.links_empty"), HomezI18n.t("sps.links_empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("sps.col_supplier")}</th><th>${HomezI18n.t("sps.col_unit_cost")}</th>
            <th>${HomezI18n.t("sps.col_moq")}</th><th>${HomezI18n.t("sps.col_shipping_cost")}</th>
            <th>${HomezI18n.t("sps.col_lead_time")}</th><th>${HomezI18n.t("sps.link_col_return_policy")}</th>
            <th>${HomezI18n.t("sps.link_col_price_valid_until")}</th><th>${HomezI18n.t("sps.col_status")}</th>
            ${isAdmin ? `<th>${HomezI18n.t("sps.col_action")}</th>` : ""}
          </tr></thead>
          <tbody>${spsState.links.map((l) => `
            <tr>
              <td data-label="${HomezI18n.t("sps.col_supplier")}">${escapeHtml(supplierName(l.supplier_id))}${spsState.bestLink && spsState.bestLink.id === l.id ? ` <span class="pill ok">${HomezI18n.t("sps.default_supplier_badge")}</span>` : ""}</td>
              <td data-label="${HomezI18n.t("sps.col_unit_cost")}">${fmtMoney(l.unit_cost)}</td>
              <td data-label="${HomezI18n.t("sps.col_moq")}">${l.moq}</td>
              <td data-label="${HomezI18n.t("sps.col_shipping_cost")}">${l.shipping_cost != null ? fmtMoney(l.shipping_cost) : "—"}</td>
              <td data-label="${HomezI18n.t("sps.col_lead_time")}">${l.lead_time_days ?? "—"}</td>
              <td data-label="${HomezI18n.t("sps.link_col_return_policy")}">${l.return_policy ? escapeHtml(l.return_policy) : "—"}</td>
              <td data-label="${HomezI18n.t("sps.link_col_price_valid_until")}">${l.price_valid_until ? fmtDate(l.price_valid_until) : "—"}</td>
              <td data-label="${HomezI18n.t("sps.col_status")}">${statusPillHtml(l.status)}</td>
              ${isAdmin ? `<td data-label="${HomezI18n.t("sps.col_action")}">${l.status === "ACTIVE" ? `<button type="button" class="btn btn-danger btn-sm" id="sps-link-deactivate-${l.id}">${HomezI18n.t("sps.link_deactivate_btn")}</button>` : "—"}</td>` : ""}
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    detailEl.innerHTML = `${createFormHtml}${tableHtml}`;

    if (!isAdmin) return;

    const createBtn = el("sps-link-create-btn");
    if (createBtn) {
      createBtn.addEventListener("click", async () => {
        const supplierId = Number(el("sps-link-supplier").value);
        const supplierSku = el("sps-link-supplier-sku").value.trim();
        const unitCost = Number(el("sps-link-unit-cost").value);
        if (!supplierId || !supplierSku || !(unitCost > 0)) {
          toast(HomezI18n.t("sps.link_required_error"), "error");
          return;
        }
        const priceValidUntilRaw = el("sps-link-price-valid-until").value;
        try {
          await apiFetch("/sourcing/links", {
            method: "POST",
            body: JSON.stringify({
              product_candidate_id: candidateId,
              supplier_id: supplierId,
              supplier_sku: supplierSku,
              unit_cost: unitCost,
              moq: Number(el("sps-link-moq").value) || 1,
              lead_time_days: el("sps-link-lead-time").value ? Number(el("sps-link-lead-time").value) : null,
              shipping_cost: el("sps-link-shipping-cost").value ? Number(el("sps-link-shipping-cost").value) : null,
              stock_available: el("sps-link-stock").value ? Number(el("sps-link-stock").value) : null,
              price_valid_until: priceValidUntilRaw ? new Date(priceValidUntilRaw).toISOString() : null,
              return_policy: el("sps-link-return-policy").value.trim() || null,
            }),
          });
          toast(HomezI18n.t("sps.link_create_success"), "success");
          await spsRenderLinksDetail();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    spsState.links.forEach((l) => {
      const btn = el(`sps-link-deactivate-${l.id}`);
      if (btn) {
        btn.addEventListener("click", async () => {
          try {
            await apiFetch(`/sourcing/links/${l.id}/deactivate`, { method: "PATCH" });
            toast(HomezI18n.t("sps.link_deactivate_success"), "success");
            await spsRenderLinksDetail();
          } catch (err) {
            toast(err.message || "", "error");
          }
        });
      }
    });
  }

  async function spsRenderPurchases() {
    const container = el("sps-purchases-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let outOfStockItems, purchases;
    try {
      const qs = spsState.purchaseFilter === "approval_pending" ? "?purchase_status=REQUESTED&limit=200" : "?limit=200";
      [outOfStockItems, purchases] = await Promise.all([
        apiFetch("/sourcing/order-items/out-of-stock"),
        apiFetch(`/purchases${qs}`),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    spsState.outOfStockItems = outOfStockItems || [];
    let rows = purchases || [];
    if (spsState.purchaseFilter === "failed") {
      rows = rows.filter((p) => p.submission_status === "FAILED");
    }
    spsState.purchases = rows;

    const oosIsAdmin = spsIsAdmin();
    const outOfStockHtml = spsState.outOfStockItems.length === 0 ? "" : `
      <div class="detail-panel">
        <h3>${HomezI18n.t("sps.oos_heading")}</h3>
        <div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("ord.col_item_name")}</th><th>${HomezI18n.t("ord.col_item_qty")}</th>
            <th>${HomezI18n.t("sps.col_action")}</th>
          </tr></thead>
          <tbody>${spsState.outOfStockItems.map((it) => `
            <tr>
              <td data-label="${HomezI18n.t("ord.col_item_name")}">${escapeHtml(it.product_name_snapshot)}</td>
              <td data-label="${HomezI18n.t("ord.col_item_qty")}">${it.quantity}</td>
              <td data-label="${HomezI18n.t("sps.col_action")}"><button type="button" class="btn btn-primary btn-sm" id="sps-oos-propose-${it.id}" ${oosIsAdmin ? "" : "disabled"} ${oosIsAdmin ? "" : `title="${HomezI18n.t("sps.permission_denied_note")}"`}>${HomezI18n.t("sps.oos_propose_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div>
        ${oosIsAdmin ? "" : spsPermissionDeniedNoteHtml()}
      </div>
    `;

    const filterHtml = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pur.filter.status_label")}</span>
            ${statusFilterSelectHtml("sps-pur-filter-status", PURCHASE_STATUS_OPTIONS, spsState.purchaseFilter === "approval_pending" ? "REQUESTED" : "")}
          </label>
          <label class="field-inline"><input type="checkbox" id="sps-pur-filter-failed" ${spsState.purchaseFilter === "failed" ? "checked" : ""}> ${HomezI18n.t("sps.filter_failed_only")}</label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="sps-pur-refresh-btn">${HomezI18n.t("ord.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const listHtml = spsState.purchases.length === 0
      ? simpleEmptyPanel(HomezI18n.t("pur.empty"), HomezI18n.t("pur.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("pur.col_id")}</th><th>${HomezI18n.t("pur.col_supplier_id")}</th>
            <th>${HomezI18n.t("pur.col_status")}</th><th>${HomezI18n.t("sps.col_submission_status")}</th>
            <th>${HomezI18n.t("pur.col_total_cost")}</th><th>${HomezI18n.t("pur.col_action")}</th>
          </tr></thead>
          <tbody>${spsState.purchases.map((p) => `
            <tr>
              <td data-label="${HomezI18n.t("pur.col_id")}">#${p.id}</td>
              <td data-label="${HomezI18n.t("pur.col_supplier_id")}">#${p.supplier_id}</td>
              <td data-label="${HomezI18n.t("pur.col_status")}">${statusPillHtml(p.status)}</td>
              <td data-label="${HomezI18n.t("sps.col_submission_status")}">${p.submission_status ? statusPillHtml(p.submission_status) : "—"}</td>
              <td data-label="${HomezI18n.t("pur.col_total_cost")}">${fmtMoney(p.total_cost)}</td>
              <td data-label="${HomezI18n.t("pur.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="sps-pur-detail-btn-${p.id}">${HomezI18n.t("pur.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${outOfStockHtml}${filterHtml}${listHtml}<div id="sps-pur-detail-panel"></div>`;

    spsState.outOfStockItems.forEach((it) => {
      const btn = el(`sps-oos-propose-${it.id}`);
      if (btn && !btn.disabled) {
        btn.addEventListener("click", async () => {
          try {
            await apiFetch(`/sourcing/order-items/${it.id}/purchase-proposal`, {
              method: "POST",
              body: JSON.stringify({ idempotency_key: genIdemKey(`sps-proposal-${it.id}`) }),
            });
            toast(HomezI18n.t("sps.oos_propose_success"), "success");
            spsState.purchaseFilter = null;
            await spsRenderPurchases();
          } catch (err) {
            toast(err.message || "", "error");
          }
        });
      }
    });

    el("sps-pur-filter-status").addEventListener("change", (ev) => {
      spsState.purchaseFilter = ev.target.value === "REQUESTED" ? "approval_pending" : null;
      spsRenderPurchases();
    });
    el("sps-pur-filter-failed").addEventListener("change", (ev) => {
      spsState.purchaseFilter = ev.target.checked ? "failed" : null;
      spsRenderPurchases();
    });
    el("sps-pur-refresh-btn").addEventListener("click", () => spsRenderPurchases());

    spsState.purchases.forEach((p) => {
      const btn = el(`sps-pur-detail-btn-${p.id}`);
      if (btn) btn.addEventListener("click", () => spsShowPurchaseDetail(p.id));
    });

    if (spsState.detailPurchaseId) {
      await spsShowPurchaseDetail(spsState.detailPurchaseId);
    }
  }

  async function spsShowPurchaseDetail(purchaseId) {
    spsState.detailPurchaseId = purchaseId;
    if (spsState.retryWaitTimer) {
      clearTimeout(spsState.retryWaitTimer);
      spsState.retryWaitTimer = null;
    }
    const panel = el("sps-pur-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let purchase, items;
    try {
      [purchase, items] = await Promise.all([
        apiFetch(`/purchases/${purchaseId}`),
        apiFetch(`/purchases/${purchaseId}/items`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    const isAdmin = spsIsAdmin();
    let accepted = null;
    let rejected = null;
    try { accepted = purchase.accepted_quantities_json ? JSON.parse(purchase.accepted_quantities_json) : null; } catch (e) { accepted = null; }
    try { rejected = purchase.rejected_quantities_json ? JSON.parse(purchase.rejected_quantities_json) : null; } catch (e) { rejected = null; }

    const canSubmit = isAdmin && purchase.status === "CONFIRMED" && !purchase.submission_status;
    const canRetry = isAdmin && purchase.submission_status === "FAILED";

    // 2026-08-21 5차 지시(작업 3) — 재시도 불가능 오류·Retry-After
    // 대기 중 상태를 UI에서 실제로 반영한다(버튼 비활성 + 사유 표시,
    // 대기 종료 시 자동 재평가). 서버도 동일 조건을 별도로 강제하므로
    // (purchase/service.py::retry_submission_to_supplier), 이 UI
    // 게이트는 방어의 두 번째 층일 뿐이다.
    const retryBlocked = purchase.submission_retryable === false;
    const submittedAtMs = spsParseUtcDateMs(purchase.submitted_at);
    const retryAvailableAtMs = (
      purchase.submission_retry_after_seconds != null && submittedAtMs != null
    ) ? submittedAtMs + purchase.submission_retry_after_seconds * 1000
      : null;
    const retryWaitRemainingMs = retryAvailableAtMs != null
      ? retryAvailableAtMs - Date.now()
      : 0;
    const retryWaiting = canRetry && retryWaitRemainingMs > 0;
    const retryButtonUsable = canRetry && !retryBlocked && !retryWaiting;

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("pur.detail_heading")} — #${purchase.id}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="sps-pur-detail-close-btn">${HomezI18n.t("pur.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("pur.col_status")}</dt><dd>${statusPillHtml(purchase.status)}</dd>
          <dt>${HomezI18n.t("sps.col_submission_status")}</dt><dd>${purchase.submission_status ? statusPillHtml(purchase.submission_status) : "—"}</dd>
          <dt>${HomezI18n.t("pur.col_supplier_id")}</dt><dd>#${purchase.supplier_id}</dd>
          <dt>${HomezI18n.t("pur.col_total_cost")}</dt><dd>${fmtMoney(purchase.total_cost)}</dd>
          <dt>${HomezI18n.t("sps.detail_provider_label")}</dt><dd>${purchase.submission_provider_code ? escapeHtml(purchase.submission_provider_code) : "—"}</dd>
          <dt>${HomezI18n.t("sps.detail_supplier_order_id_label")}</dt><dd>${purchase.supplier_order_id ? escapeHtml(purchase.supplier_order_id) : "—"}</dd>
          <dt>${HomezI18n.t("sps.detail_confirmed_price_label")}</dt><dd>${purchase.confirmed_price != null ? fmtMoney(purchase.confirmed_price) : "—"}</dd>
          <dt>${HomezI18n.t("sps.detail_error_code_label")}</dt><dd>${purchase.submission_error_code ? escapeHtml(purchase.submission_error_code) : "—"}</dd>
          <dt>${HomezI18n.t("sps.detail_retry_after_label")}</dt><dd>${purchase.submission_retry_after_seconds != null ? HomezI18n.t("sps.retry_after_seconds_value", { seconds: purchase.submission_retry_after_seconds }) : "—"}</dd>
        </dl>
        ${accepted ? `<p class="stat-sub">${HomezI18n.t("sps.accepted_quantities_label")}: ${escapeHtml(JSON.stringify(accepted))}</p>` : ""}
        ${rejected ? `<p class="stat-sub">${HomezI18n.t("sps.rejected_quantities_label")}: ${escapeHtml(JSON.stringify(rejected))}</p>` : ""}
        ${purchase.submission_provider_code === "CSV" && purchase.submission_status === "SUBMITTED" ? `<p class="stat-sub">${HomezI18n.t("sps.csv_submitted_note")}</p>` : ""}

        <div class="decision-actions">
          <button type="button" class="btn btn-primary btn-sm" id="sps-pur-confirm-btn" ${purchase.status === "REQUESTED" && isAdmin ? "" : "disabled"} ${!isAdmin ? `title="${HomezI18n.t("sps.permission_denied_note")}"` : ""}>${HomezI18n.t("pur.confirm_btn")}</button>
          <button type="button" class="btn btn-primary btn-sm" id="sps-pur-receive-btn" ${purchase.status === "CONFIRMED" && isAdmin ? "" : "disabled"} ${!isAdmin ? `title="${HomezI18n.t("sps.permission_denied_note")}"` : ""}>${HomezI18n.t("pur.receive_btn")}</button>
          <button type="button" class="btn btn-danger btn-sm" id="sps-pur-cancel-btn" ${["REQUESTED", "CONFIRMED"].includes(purchase.status) && isAdmin ? "" : "disabled"} ${!isAdmin ? `title="${HomezI18n.t("sps.permission_denied_note")}"` : ""}>${HomezI18n.t("pur.cancel_btn")}</button>
        </div>
        ${!isAdmin ? spsPermissionDeniedNoteHtml() : ""}

        ${canSubmit || canRetry ? `
          <div class="ls-filter-row">
            <label class="field">
              <span class="field-label">${HomezI18n.t("sps.submit_provider_label")}</span>
              <select id="sps-pur-submit-provider">
                ${SPS_PROVIDER_OPTIONS.map((p) => `<option value="${p}">${p}</option>`).join("")}
              </select>
            </label>
            <label class="field" id="sps-pur-scenario-field" hidden>
              <span class="field-label">${HomezI18n.t("sps.test_scenario_label")}</span>
              <select id="sps-pur-test-scenario">
                <option value="">${HomezI18n.t("sps.test_scenario_full_accept")}</option>
                <option value="PARTIAL">${HomezI18n.t("sps.test_scenario_partial")}</option>
                <option value="RETRYABLE_FAILURE">${HomezI18n.t("sps.test_scenario_retryable_failure")}</option>
                <option value="RETRY_AFTER">${HomezI18n.t("sps.test_scenario_retry_after")}</option>
                <option value="NON_RETRYABLE_FAILURE">${HomezI18n.t("sps.test_scenario_non_retryable_failure")}</option>
              </select>
            </label>
            <div class="ls-filter-actions">
              ${canSubmit ? `<button type="button" class="btn btn-primary btn-sm" id="sps-pur-submit-btn">${HomezI18n.t("sps.submit_btn")}</button>` : ""}
              ${canRetry ? `<button type="button" class="btn btn-primary btn-sm" id="sps-pur-retry-btn" ${retryButtonUsable ? "" : "disabled"}>${HomezI18n.t("sps.retry_btn")}</button>` : ""}
            </div>
          </div>
          ${retryBlocked ? `<p class="stat-sub" id="sps-pur-retry-blocked-note">${HomezI18n.t("sps.retry_not_retryable_note")}</p>` : ""}
          ${retryWaiting ? `<p class="stat-sub" id="sps-pur-retry-wait-note">${HomezI18n.t("sps.retry_wait_note", { seconds: Math.ceil(retryWaitRemainingMs / 1000) })}</p>` : ""}
          <p class="stat-sub" id="sps-pur-manual-note" hidden>${HomezI18n.t("sps.manual_provider_note")}</p>
          <p class="stat-sub">${HomezI18n.t("sps.submit_safety_note")}</p>
          <p class="stat-sub">${HomezI18n.t("sps.test_scenario_safety_note")}</p>
        ` : ""}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("pur.items_heading")}</h3>
        <div class="table-wrap"><table class="responsive-cards">
          <thead><tr><th>${HomezI18n.t("pur.col_item_qty")}</th><th>${HomezI18n.t("pur.col_item_unit_cost")}</th><th>${HomezI18n.t("pur.col_item_subtotal")}</th></tr></thead>
          <tbody>${items.map((it) => `
            <tr>
              <td data-label="${HomezI18n.t("pur.col_item_qty")}">${it.quantity}</td>
              <td data-label="${HomezI18n.t("pur.col_item_unit_cost")}">${fmtMoney(it.unit_cost)}</td>
              <td data-label="${HomezI18n.t("pur.col_item_subtotal")}">${fmtMoney(it.subtotal_cost)}</td>
            </tr>
          `).join("")}</tbody>
        </table></div>
      </div>
    `;

    el("sps-pur-detail-close-btn").addEventListener("click", () => {
      spsState.detailPurchaseId = null;
      panel.innerHTML = "";
    });

    const confirmBtn = el("sps-pur-confirm-btn");
    if (confirmBtn && !confirmBtn.disabled) {
      confirmBtn.addEventListener("click", async () => {
        try {
          await apiFetch(`/purchases/${purchaseId}/confirm`, { method: "POST" });
          toast(HomezI18n.t("pur.confirm_success"), "success");
          await spsShowPurchaseDetail(purchaseId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const receiveBtn = el("sps-pur-receive-btn");
    if (receiveBtn && !receiveBtn.disabled) {
      receiveBtn.addEventListener("click", async () => {
        try {
          await apiFetch(`/purchases/${purchaseId}/receive`, { method: "POST" });
          toast(HomezI18n.t("pur.receive_success"), "success");
          await spsShowPurchaseDetail(purchaseId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const cancelBtn = el("sps-pur-cancel-btn");
    if (cancelBtn && !cancelBtn.disabled) {
      cancelBtn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("pur.cancel_confirm_title"),
          body: HomezI18n.t("pur.cancel_confirm_body"),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/purchases/${purchaseId}/cancel`, {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("pur.cancel_success"), "success");
          spsState.detailPurchaseId = null;
          await spsRenderPurchases();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const providerSelect = el("sps-pur-submit-provider");
    const manualNote = el("sps-pur-manual-note");
    const scenarioField = el("sps-pur-scenario-field");
    if (providerSelect) {
      const syncProviderDependentFields = () => {
        if (manualNote) manualNote.hidden = providerSelect.value !== "MANUAL";
        if (scenarioField) scenarioField.hidden = providerSelect.value !== "FAKE";
      };
      providerSelect.addEventListener("change", syncProviderDependentFields);
      syncProviderDependentFields();
    }

    const submitBtn = el("sps-pur-submit-btn");
    if (submitBtn) {
      submitBtn.addEventListener("click", async () => {
        const providerCode = el("sps-pur-submit-provider").value;
        if (providerCode === "MANUAL") {
          toast(HomezI18n.t("sps.manual_provider_note"), "info");
          return;
        }
        const scenarioSelect = el("sps-pur-test-scenario");
        const testScenario = (providerCode === "FAKE" && scenarioSelect && scenarioSelect.value)
          ? scenarioSelect.value
          : null;
        try {
          await apiFetch(`/purchases/${purchaseId}/submit`, {
            method: "POST",
            body: JSON.stringify({ provider_code: providerCode, test_scenario: testScenario }),
          });
          toast(providerCode === "CSV" ? HomezI18n.t("sps.csv_submitted_note") : HomezI18n.t("sps.submit_success"), "success");
          await spsShowPurchaseDetail(purchaseId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const retryBtn = el("sps-pur-retry-btn");
    if (retryBtn && !retryBtn.disabled) {
      retryBtn.addEventListener("click", async () => {
        const providerCode = el("sps-pur-submit-provider").value;
        if (providerCode === "MANUAL") {
          toast(HomezI18n.t("sps.manual_provider_note"), "info");
          return;
        }
        const scenarioSelect = el("sps-pur-test-scenario");
        const testScenario = (providerCode === "FAKE" && scenarioSelect && scenarioSelect.value)
          ? scenarioSelect.value
          : null;
        try {
          await apiFetch(`/purchases/${purchaseId}/submit/retry`, {
            method: "POST",
            body: JSON.stringify({ provider_code: providerCode, test_scenario: testScenario }),
          });
          toast(HomezI18n.t("sps.retry_success"), "success");
          await spsShowPurchaseDetail(purchaseId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    if (retryWaiting) {
      const waitMs = Math.max(retryWaitRemainingMs, 0);
      spsState.retryWaitTimer = setTimeout(() => {
        // 대기 종료 시점에 사용자가 여전히 같은 발주 상세를 보고
        // 있을 때만 다시 그린다 — 다른 화면으로 이동했으면 아무 것도
        // 하지 않는다(존재하지 않는 DOM에 렌더링 시도 방지).
        if (spsState.detailPurchaseId === purchaseId) {
          spsShowPurchaseDetail(purchaseId);
        }
      }, waitMs + 250);
    }
  }

  VIEW_LOADERS["supplier-sourcing"] = loadSupplierSourcing;

  // --------------------------------------------------
  // 배송 (Shipment, Gate 4)
  // --------------------------------------------------

  const shipState = { rows: [], statusFilter: "", detailShipmentId: null };

  const SHIPMENT_STATUS_OPTIONS = [
    "PENDING", "READY", "SHIPPED", "IN_TRANSIT", "DELIVERED", "CANCELLED",
    "RETURN_REQUESTED", "RETURNED", "EXCHANGE_REQUESTED", "EXCHANGED",
  ];
  const SHIPMENT_ALLOWED_TRANSITIONS = {
    PENDING: ["READY", "CANCELLED"],
    READY: ["SHIPPED", "CANCELLED"],
    SHIPPED: ["IN_TRANSIT", "DELIVERED", "RETURN_REQUESTED"],
    IN_TRANSIT: ["DELIVERED", "RETURN_REQUESTED"],
    DELIVERED: ["RETURN_REQUESTED", "EXCHANGE_REQUESTED"],
    CANCELLED: [],
    RETURN_REQUESTED: ["RETURNED"],
    RETURNED: [],
    EXCHANGE_REQUESTED: ["EXCHANGED"],
    EXCHANGED: [],
  };

  async function loadShipment() {
    shipState.detailShipmentId = null;
    await shipRenderList();
  }

  async function shipRenderList() {
    const container = el("ship-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = shipState.statusFilter ? `?shipment_status=${encodeURIComponent(shipState.statusFilter)}&limit=200` : "?limit=200";
      rows = await apiFetch(`/shipments${qs}`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    shipState.rows = rows || [];

    const filterHtml = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("ship.filter.status_label")}</span>
            ${statusFilterSelectHtml("ship-filter-status", SHIPMENT_STATUS_OPTIONS, shipState.statusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="ship-refresh-btn">${HomezI18n.t("ship.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const tableHtml = shipState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("ship.empty"), HomezI18n.t("ship.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("ship.col_shipment_number")}</th><th>${HomezI18n.t("ship.col_order_id")}</th>
            <th>${HomezI18n.t("ship.col_status")}</th><th>${HomezI18n.t("ship.col_courier")}</th>
            <th>${HomezI18n.t("ship.col_shipped_at")}</th><th>${HomezI18n.t("ship.col_delivered_at")}</th>
            <th>${HomezI18n.t("ship.col_action")}</th>
          </tr></thead>
          <tbody>${shipState.rows.map((s) => `
            <tr>
              <td data-label="${HomezI18n.t("ship.col_shipment_number")}">${escapeHtml(s.shipment_number)}</td>
              <td data-label="${HomezI18n.t("ship.col_order_id")}">#${s.order_id}</td>
              <td data-label="${HomezI18n.t("ship.col_status")}">${statusPillHtmlLabeled(s.status, "ship.status.")}</td>
              <td data-label="${HomezI18n.t("ship.col_courier")}">${s.courier ? escapeHtml(s.courier) : "—"}</td>
              <td data-label="${HomezI18n.t("ship.col_shipped_at")}">${s.shipped_at ? fmtDate(s.shipped_at) : "—"}</td>
              <td data-label="${HomezI18n.t("ship.col_delivered_at")}">${s.delivered_at ? fmtDate(s.delivered_at) : "—"}</td>
              <td data-label="${HomezI18n.t("ship.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="ship-detail-btn-${s.id}">${HomezI18n.t("ship.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${filterHtml}${tableHtml}<div id="ship-detail-panel"></div>`;

    el("ship-filter-status").addEventListener("change", (ev) => {
      shipState.statusFilter = ev.target.value;
      shipRenderList();
    });
    el("ship-refresh-btn").addEventListener("click", () => shipRenderList());

    shipState.rows.forEach((s) => {
      const btn = el(`ship-detail-btn-${s.id}`);
      if (btn) btn.addEventListener("click", () => shipShowDetail(s.id));
    });

    if (shipState.detailShipmentId) {
      await shipShowDetail(shipState.detailShipmentId);
    }
  }

  async function shipShowDetail(shipmentId) {
    shipState.detailShipmentId = shipmentId;
    const panel = el("ship-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let shipment, items, events;
    try {
      [shipment, items, events] = await Promise.all([
        apiFetch(`/shipments/${shipmentId}`),
        apiFetch(`/shipments/${shipmentId}/items`),
        apiFetch(`/shipments/${shipmentId}/status-events`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    const nextStatuses = SHIPMENT_ALLOWED_TRANSITIONS[shipment.status] || [];

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("ship.detail_heading")} — ${escapeHtml(shipment.shipment_number)}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="ship-detail-close-btn">${HomezI18n.t("ship.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("ship.col_status")}</dt><dd>${statusPillHtmlLabeled(shipment.status, "ship.status.")}</dd>
          <dt>${HomezI18n.t("ship.col_order_id")}</dt><dd>#${shipment.order_id}</dd>
          <dt>${HomezI18n.t("ship.col_courier")}</dt><dd>${shipment.courier ? escapeHtml(shipment.courier) : "—"}</dd>
          <dt>${HomezI18n.t("ord.invoice_number_label")}</dt><dd>${shipment.invoice_number ? escapeHtml(shipment.invoice_number) : "—"}</dd>
        </dl>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ship.timeline_heading")}</h3>
        ${statusTimelineHtml(buildShipmentTimelineSteps(shipment, events))}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ship.items_heading")}</h3>
        <div class="table-wrap"><table class="responsive-cards">
          <thead><tr><th>${HomezI18n.t("ship.col_item_order_item")}</th><th>${HomezI18n.t("ship.col_item_qty")}</th><th>${HomezI18n.t("ship.create_return_btn")}</th></tr></thead>
          <tbody>${items.map((it) => `
            <tr>
              <td data-label="${HomezI18n.t("ship.col_item_order_item")}">#${it.order_item_id}</td>
              <td data-label="${HomezI18n.t("ship.col_item_qty")}">${it.quantity}</td>
              <td data-label="${HomezI18n.t("ship.create_return_btn")}"><button type="button" class="btn btn-ghost btn-sm" id="ship-return-toggle-${it.id}">${HomezI18n.t("ship.create_return_btn")}</button></td>
            </tr>
            <tr id="ship-return-form-row-${it.id}" hidden>
              <td colspan="3">
                <div class="ls-filter-row">
                  <label class="field"><span class="field-label">${HomezI18n.t("ship.return_type_label")}</span>
                    <select id="ship-return-type-${it.id}"><option value="RETURN">RETURN</option><option value="EXCHANGE">EXCHANGE</option></select>
                  </label>
                  <label class="field"><span class="field-label">${HomezI18n.t("ship.return_qty_label")}</span><input type="number" min="1" value="${it.quantity}" id="ship-return-qty-${it.id}"></label>
                  <label class="field"><span class="field-label">${HomezI18n.t("ship.return_reason_label")}</span><input type="text" id="ship-return-reason-${it.id}"></label>
                  <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ship-return-submit-${it.id}">${HomezI18n.t("ship.create_return_submit")}</button></div>
                </div>
              </td>
            </tr>
          `).join("")}</tbody>
        </table></div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ship.update_status_heading")}</h3>
        ${nextStatuses.length === 0 ? `<p class="stat-sub">${HomezI18n.t("ship.no_transitions")}</p>` : `
          <div class="ls-filter-row">
            <label class="field">
              <span class="field-label">${HomezI18n.t("ship.new_status_label")}</span>
              <select id="ship-new-status">${nextStatuses.map((s) => `<option value="${s}">${s}</option>`).join("")}</select>
            </label>
            <label class="field"><span class="field-label">${HomezI18n.t("inv.reason_label")}</span><input type="text" id="ship-status-reason"></label>
            <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ship-status-submit-btn">${HomezI18n.t("ship.update_status_submit")}</button></div>
          </div>
        `}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ship.status_events_heading")}</h3>
        ${events.length === 0 ? `<p class="stat-sub">—</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("ord.col_event_prev")}</th><th>${HomezI18n.t("ord.col_event_new")}</th><th>${HomezI18n.t("ord.col_event_reason")}</th><th>${HomezI18n.t("ord.col_event_date")}</th></tr></thead>
            <tbody>${events.map((e) => `
              <tr>
                <td data-label="${HomezI18n.t("ord.col_event_prev")}">${e.previous_status ? escapeHtml(e.previous_status) : "—"}</td>
                <td data-label="${HomezI18n.t("ord.col_event_new")}">${statusPillHtml(e.new_status)}</td>
                <td data-label="${HomezI18n.t("ord.col_event_reason")}">${e.reason ? escapeHtml(e.reason) : "—"}</td>
                <td data-label="${HomezI18n.t("ord.col_event_date")}">${fmtDate(e.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>
    `;

    el("ship-detail-close-btn").addEventListener("click", () => {
      shipState.detailShipmentId = null;
      panel.innerHTML = "";
    });

    const statusSubmitBtn = el("ship-status-submit-btn");
    if (statusSubmitBtn) {
      statusSubmitBtn.addEventListener("click", async () => {
        const newStatus = el("ship-new-status").value;
        const reason = el("ship-status-reason").value.trim();
        try {
          await apiFetch(`/shipments/${shipmentId}/status`, {
            method: "POST",
            body: JSON.stringify({ new_status: newStatus, reason: reason || null }),
          });
          toast(HomezI18n.t("ship.update_status_success"), "success");
          await shipShowDetail(shipmentId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    items.forEach((it) => {
      const toggleBtn = el(`ship-return-toggle-${it.id}`);
      const formRow = el(`ship-return-form-row-${it.id}`);
      if (toggleBtn && formRow) {
        toggleBtn.addEventListener("click", () => {
          formRow.hidden = !formRow.hidden;
        });
      }
      const submitBtn = el(`ship-return-submit-${it.id}`);
      if (submitBtn) {
        submitBtn.addEventListener("click", async () => {
          const returnType = el(`ship-return-type-${it.id}`).value;
          const qty = Number(el(`ship-return-qty-${it.id}`).value);
          const reason = el(`ship-return-reason-${it.id}`).value.trim();
          if (!qty || qty <= 0) { toast(HomezI18n.t("inv.quantity_required_error"), "error"); return; }
          if (!reason) { toast(HomezI18n.t("inv.reason_required_error"), "error"); return; }
          try {
            await apiFetch("/return-orders", {
              method: "POST",
              body: JSON.stringify({
                order_item_id: it.order_item_id,
                shipment_id: shipmentId,
                return_type: returnType,
                quantity: qty,
                reason,
                idempotency_key: genIdemKey(`return-${shipmentId}-${it.id}`),
              }),
            });
            toast(HomezI18n.t("ship.create_return_success"), "success");
            await shipShowDetail(shipmentId);
          } catch (err) {
            toast(err.message || "", "error");
          }
        });
      }
    });
  }

  VIEW_LOADERS["shipment"] = loadShipment;

  // --------------------------------------------------
  // 반품 · 교환 (ReturnOrder, Gate 4)
  // --------------------------------------------------

  const retState = { rows: [], statusFilter: "", detailReturnId: null };
  const RETURN_STATUS_OPTIONS = ["REQUESTED", "APPROVED", "RECEIVED", "COMPLETED", "REJECTED"];

  async function loadReturnOrder() {
    retState.detailReturnId = null;
    await retRenderList();
  }

  async function retRenderList() {
    const container = el("ret-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = retState.statusFilter ? `?return_status=${encodeURIComponent(retState.statusFilter)}&limit=200` : "?limit=200";
      rows = await apiFetch(`/return-orders${qs}`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    retState.rows = rows || [];

    const filterHtml = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("ret.filter.status_label")}</span>
            ${statusFilterSelectHtml("ret-filter-status", RETURN_STATUS_OPTIONS, retState.statusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="ret-refresh-btn">${HomezI18n.t("ret.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const tableHtml = retState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("ret.empty"), HomezI18n.t("ret.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("ret.col_id")}</th><th>${HomezI18n.t("ret.col_order_id")}</th>
            <th>${HomezI18n.t("ret.col_type")}</th><th>${HomezI18n.t("ret.col_status")}</th>
            <th>${HomezI18n.t("ret.col_qty")}</th><th>${HomezI18n.t("ret.col_requested_at")}</th>
            <th>${HomezI18n.t("ret.col_action")}</th>
          </tr></thead>
          <tbody>${retState.rows.map((r) => `
            <tr>
              <td data-label="${HomezI18n.t("ret.col_id")}">#${r.id}</td>
              <td data-label="${HomezI18n.t("ret.col_order_id")}">#${r.order_id}</td>
              <td data-label="${HomezI18n.t("ret.col_type")}">${escapeHtml(r.return_type)}</td>
              <td data-label="${HomezI18n.t("ret.col_status")}">${statusPillHtmlLabeled(r.status, "ret.status.")}</td>
              <td data-label="${HomezI18n.t("ret.col_qty")}">${r.quantity}</td>
              <td data-label="${HomezI18n.t("ret.col_requested_at")}">${fmtDate(r.requested_at)}</td>
              <td data-label="${HomezI18n.t("ret.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="ret-detail-btn-${r.id}">${HomezI18n.t("ret.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${filterHtml}${tableHtml}<div id="ret-detail-panel"></div>`;

    el("ret-filter-status").addEventListener("change", (ev) => {
      retState.statusFilter = ev.target.value;
      retRenderList();
    });
    el("ret-refresh-btn").addEventListener("click", () => retRenderList());

    retState.rows.forEach((r) => {
      const btn = el(`ret-detail-btn-${r.id}`);
      if (btn) btn.addEventListener("click", () => retShowDetail(r.id));
    });

    if (retState.detailReturnId) {
      await retShowDetail(retState.detailReturnId);
    }
  }

  async function retShowDetail(returnId) {
    retState.detailReturnId = returnId;
    const panel = el("ret-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let ret, events;
    try {
      [ret, events] = await Promise.all([
        apiFetch(`/return-orders/${returnId}`),
        apiFetch(`/return-orders/${returnId}/status-events`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    const canApprove = ret.status === "REQUESTED";
    const canReceive = ret.status === "APPROVED";
    const canComplete = ret.status === "RECEIVED";
    const canReject = ["REQUESTED", "APPROVED"].includes(ret.status);

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("ret.detail_heading")} — #${ret.id}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="ret-detail-close-btn">${HomezI18n.t("ret.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("ret.col_status")}</dt><dd>${statusPillHtmlLabeled(ret.status, "ret.status.")}</dd>
          <dt>${HomezI18n.t("ret.col_order_id")}</dt><dd>#${ret.order_id}</dd>
          <dt>${HomezI18n.t("ret.col_type")}</dt><dd>${escapeHtml(ret.return_type)}</dd>
          <dt>${HomezI18n.t("ret.col_qty")}</dt><dd>${ret.quantity}</dd>
          <dt>${HomezI18n.t("ret.col_reason")}</dt><dd>${escapeHtml(ret.reason)}</dd>
        </dl>
        <div class="decision-actions">
          <button type="button" class="btn btn-primary btn-sm" id="ret-approve-btn" ${canApprove ? "" : "disabled"}>${HomezI18n.t("ret.approve_btn")}</button>
          <button type="button" class="btn btn-primary btn-sm" id="ret-receive-btn" ${canReceive ? "" : "disabled"}>${HomezI18n.t("ret.receive_btn")}</button>
          <button type="button" class="btn btn-primary btn-sm" id="ret-complete-btn" ${canComplete ? "" : "disabled"}>${HomezI18n.t("ret.complete_btn")}</button>
          <button type="button" class="btn btn-danger btn-sm" id="ret-reject-btn" ${canReject ? "" : "disabled"}>${HomezI18n.t("ret.reject_btn")}</button>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ret.timeline_heading")}</h3>
        ${statusTimelineHtml(buildReturnOrderTimelineSteps(ret))}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("ret.status_events_heading")}</h3>
        ${events.length === 0 ? `<p class="stat-sub">—</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("ord.col_event_prev")}</th><th>${HomezI18n.t("ord.col_event_new")}</th><th>${HomezI18n.t("ord.col_event_reason")}</th><th>${HomezI18n.t("ord.col_event_date")}</th></tr></thead>
            <tbody>${events.map((e) => `
              <tr>
                <td data-label="${HomezI18n.t("ord.col_event_prev")}">${e.previous_status ? escapeHtml(e.previous_status) : "—"}</td>
                <td data-label="${HomezI18n.t("ord.col_event_new")}">${statusPillHtml(e.new_status)}</td>
                <td data-label="${HomezI18n.t("ord.col_event_reason")}">${e.reason ? escapeHtml(e.reason) : "—"}</td>
                <td data-label="${HomezI18n.t("ord.col_event_date")}">${fmtDate(e.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>
    `;

    el("ret-detail-close-btn").addEventListener("click", () => {
      retState.detailReturnId = null;
      panel.innerHTML = "";
    });

    const bindSimpleAction = (btnId, path, successKey) => {
      const btn = el(btnId);
      if (!btn || btn.disabled) return;
      btn.addEventListener("click", async () => {
        try {
          await apiFetch(`/return-orders/${returnId}/${path}`, { method: "POST" });
          toast(HomezI18n.t(successKey), "success");
          await retShowDetail(returnId);
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    };
    bindSimpleAction("ret-approve-btn", "approve", "ret.approve_success");
    bindSimpleAction("ret-receive-btn", "receive", "ret.receive_success");
    bindSimpleAction("ret-complete-btn", "complete", "ret.complete_success");

    const rejectBtn = el("ret-reject-btn");
    if (rejectBtn && !rejectBtn.disabled) {
      rejectBtn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("ret.reject_confirm_title"),
          body: HomezI18n.t("ret.reject_confirm_body"),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/return-orders/${returnId}/reject`, {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("ret.reject_success"), "success");
          await retRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }
  }

  VIEW_LOADERS["return-order"] = loadReturnOrder;

  // --------------------------------------------------
  // 환불 (Refund, Phase 8) — 2026-09-10 UI 개선(UI-8 감사 중 발견,
  // docs/HOMEZ_V7_UI_IMPLEMENTATION_AUDIT.md "UI-8" 절 참고). 백엔드
  // (app/domains/refund)는 이미 완성·테스트됐지만 이 화면이 생기기
  // 전까지는 콘솔에서 환불을 승인할 방법이 전혀 없었다. 신규 환불
  // 등록(생성) 화면은 이번 범위에 포함하지 않는다 — 반품/취소 처리
  // 흐름에서 실제로 어떻게 Refund가 생성돼야 하는지는 별도 설계가
  // 필요해, 승인/거부/실행확인(이미 만들어진 요청을 처리하는 것)만
  // 먼저 다룬다.
  // --------------------------------------------------

  const rfState = { rows: [], statusFilter: "", detailRefundId: null };
  const REFUND_STATUS_OPTIONS = ["AWAITING_APPROVAL", "APPROVED", "REJECTED", "EXECUTED"];

  async function loadRefund() {
    rfState.detailRefundId = null;
    await rfRenderList();
  }

  async function rfRenderList() {
    const container = el("rf-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = rfState.statusFilter ? `?status=${encodeURIComponent(rfState.statusFilter)}` : "";
      rows = await apiFetch(`/refunds${qs}`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    rfState.rows = rows || [];

    const filterHtml = `
      <div class="detail-panel">
        <p class="field-hint">${HomezI18n.t("refund.fake_notice")}</p>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("refund.filter.status_label")}</span>
            ${statusFilterSelectHtml("rf-filter-status", REFUND_STATUS_OPTIONS, rfState.statusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="rf-refresh-btn">${HomezI18n.t("refund.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const tableHtml = rfState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("refund.empty"), HomezI18n.t("refund.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("refund.col_id")}</th><th>${HomezI18n.t("refund.col_order_id")}</th>
            <th>${HomezI18n.t("refund.col_type")}</th><th>${HomezI18n.t("refund.col_status")}</th>
            <th>${HomezI18n.t("refund.col_amount")}</th><th>${HomezI18n.t("refund.col_requested_at")}</th>
            <th>${HomezI18n.t("refund.col_action")}</th>
          </tr></thead>
          <tbody>${rfState.rows.map((r) => `
            <tr>
              <td data-label="${HomezI18n.t("refund.col_id")}">#${r.id}</td>
              <td data-label="${HomezI18n.t("refund.col_order_id")}">#${r.order_id}</td>
              <td data-label="${HomezI18n.t("refund.col_type")}">${escapeHtml(HomezI18n.t(`refund.type.${String(r.refund_type).toLowerCase()}`) || r.refund_type)}</td>
              <td data-label="${HomezI18n.t("refund.col_status")}">${statusPillHtmlLabeled(r.status, "refund.status.")}</td>
              <td data-label="${HomezI18n.t("refund.col_amount")}">${fmtMoney(r.amount, r.currency)}</td>
              <td data-label="${HomezI18n.t("refund.col_requested_at")}">${fmtDate(r.requested_at)}</td>
              <td data-label="${HomezI18n.t("refund.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="rf-detail-btn-${r.id}">${HomezI18n.t("ret.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${filterHtml}${tableHtml}<div id="rf-detail-panel"></div>`;

    el("rf-filter-status").addEventListener("change", (ev) => {
      rfState.statusFilter = ev.target.value;
      rfRenderList();
    });
    el("rf-refresh-btn").addEventListener("click", () => rfRenderList());

    rfState.rows.forEach((r) => {
      const btn = el(`rf-detail-btn-${r.id}`);
      if (btn) btn.addEventListener("click", () => rfShowDetail(r.id));
    });

    if (rfState.detailRefundId) {
      rfShowDetail(rfState.detailRefundId);
    }
  }

  // GET /refunds/{id} 단건 조회 엔드포인트가 없다(백엔드 설계 —
  // 목록만 제공) — 방금 불러온 목록 캐시에서 찾는다. 승인/거부/
  // 실행확인 후에는 항상 rfRenderList()로 목록을 다시 불러오므로
  // 캐시가 오래된 상태로 남지 않는다.
  function rfShowDetail(refundId) {
    rfState.detailRefundId = refundId;
    const panel = el("rf-detail-panel");
    if (!panel) return;

    const refund = rfState.rows.find((r) => r.id === refundId);
    if (!refund) {
      panel.innerHTML = "";
      return;
    }

    const canApprove = refund.status === "AWAITING_APPROVAL";
    const canReject = refund.status === "AWAITING_APPROVAL";
    const canMarkExecuted = refund.status === "APPROVED";

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("refund.detail_heading")} — #${refund.id}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="rf-detail-close-btn">${HomezI18n.t("refund.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("refund.col_status")}</dt><dd>${statusPillHtmlLabeled(refund.status, "refund.status.")}</dd>
          <dt>${HomezI18n.t("refund.col_order_id")}</dt><dd>#${refund.order_id}</dd>
          <dt>${HomezI18n.t("refund.col_return_order_id")}</dt><dd>${refund.return_order_id ? `#${refund.return_order_id}` : "—"}</dd>
          <dt>${HomezI18n.t("refund.col_type")}</dt><dd>${escapeHtml(HomezI18n.t(`refund.type.${String(refund.refund_type).toLowerCase()}`) || refund.refund_type)}</dd>
          <dt>${HomezI18n.t("refund.col_amount")}</dt><dd>${fmtMoney(refund.amount, refund.currency)}</dd>
          <dt>${HomezI18n.t("refund.col_reason")}</dt><dd>${escapeHtml(refund.reason)}</dd>
          <dt>${HomezI18n.t("refund.col_requested_by")}</dt><dd>#${refund.requested_by}</dd>
          <dt>${HomezI18n.t("refund.col_approved_by")}</dt><dd>${refund.approved_by ? `#${refund.approved_by}` : "—"}</dd>
        </dl>
        <div class="decision-actions">
          <button type="button" class="btn btn-primary btn-sm" id="rf-approve-btn" ${canApprove ? "" : "disabled"}>${HomezI18n.t("refund.approve_btn")}</button>
          <button type="button" class="btn btn-danger btn-sm" id="rf-reject-btn" ${canReject ? "" : "disabled"}>${HomezI18n.t("refund.reject_btn")}</button>
          <button type="button" class="btn btn-primary btn-sm" id="rf-mark-executed-btn" ${canMarkExecuted ? "" : "disabled"}>${HomezI18n.t("refund.mark_executed_btn")}</button>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("refund.timeline_heading")}</h3>
        ${statusTimelineHtml(buildRefundTimelineSteps(refund))}
      </div>
    `;

    el("rf-detail-close-btn").addEventListener("click", () => {
      rfState.detailRefundId = null;
      panel.innerHTML = "";
    });

    const approveBtn = el("rf-approve-btn");
    if (approveBtn && !approveBtn.disabled) {
      approveBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        // 환불 승인은 실제 돈이 나가는 결정이라(app/domains/refund/
        // router.py 주석 참고) 결제수단 등록과 동일한 민감도로
        // 취급한다 — 현재 비밀번호 재확인(recent-auth) 필수.
        const token = await promptRecentAuthToken();
        if (token === null) return;
        try {
          await apiFetch(`/refunds/${refundId}/approve`, {
            method: "POST",
            headers: { "X-Recent-Auth-Token": token },
          });
          toast(HomezI18n.t("refund.approve_success"), "success");
          await rfRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      }));
    }

    const rejectBtn = el("rf-reject-btn");
    if (rejectBtn && !rejectBtn.disabled) {
      rejectBtn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("refund.reject_confirm_title"),
          body: HomezI18n.t("refund.reject_confirm_body"),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/refunds/${refundId}/reject`, {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("refund.reject_success"), "success");
          await rfRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }

    const markExecutedBtn = el("rf-mark-executed-btn");
    if (markExecutedBtn && !markExecutedBtn.disabled) {
      markExecutedBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        try {
          await apiFetch(`/refunds/${refundId}/mark-executed`, { method: "POST" });
          toast(HomezI18n.t("refund.mark_executed_success"), "success");
          await rfRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      }));
    }
  }

  VIEW_LOADERS["refund"] = loadRefund;

  // --------------------------------------------------
  // 결제 수단 (Payment, Phase 7) — 2026-09-10 UI 개선(UI-6 감사 중
  // 발견, docs/HOMEZ_V7_UI_IMPLEMENTATION_AUDIT.md "UI-6" 절 참고).
  // Refund와 동일한 이유로 화면이 없었다. raw_details(카드·계좌
  // 원문)는 백엔드가 애초에 저장하지 않으므로(app/domains/payment/
  // schema.py 주석 참고) 이 화면은 실제 카드·계좌번호 입력 필드를
  // 두지 않는다 — 등록 폼은 종류·이름만 받는다.
  // --------------------------------------------------

  const PAY_METHOD_TYPES = ["CARD", "PAYPAL", "BANK_TRANSFER", "VIRTUAL_ACCOUNT"];

  async function loadPayment() {
    const container = el("pay-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let methods, limit;
    try {
      [methods, limit] = await Promise.all([
        apiFetch("/payments/methods?include_inactive=true"),
        apiFetch("/payments/auto-limit"),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    const methodTypeOptionsHtml = PAY_METHOD_TYPES.map((t) => `<option value="${t}">${escapeHtml(HomezI18n.t(`pay.type.${t.toLowerCase()}`))}</option>`).join("");

    const methodsListHtml = (!methods || methods.length === 0)
      ? simpleEmptyPanel(HomezI18n.t("pay.empty"), HomezI18n.t("pay.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("pay.col_type")}</th><th>${HomezI18n.t("pay.col_display_name")}</th>
            <th>${HomezI18n.t("pay.col_default")}</th><th>${HomezI18n.t("pay.col_active")}</th>
            <th>${HomezI18n.t("pay.col_created_at")}</th><th>${HomezI18n.t("pay.col_action")}</th>
          </tr></thead>
          <tbody>${methods.map((m) => `
            <tr>
              <td data-label="${HomezI18n.t("pay.col_type")}">${escapeHtml(HomezI18n.t(`pay.type.${String(m.method_type).toLowerCase()}`) || m.method_type)}</td>
              <td data-label="${HomezI18n.t("pay.col_display_name")}">${escapeHtml(m.display_name)}</td>
              <td data-label="${HomezI18n.t("pay.col_default")}">${m.is_default ? HomezI18n.t("common.yes") : HomezI18n.t("common.no")}</td>
              <td data-label="${HomezI18n.t("pay.col_active")}"><span class="pill ${m.active ? "ok" : "neutral"}">${HomezI18n.t(m.active ? "pay.status.active" : "pay.status.inactive")}</span></td>
              <td data-label="${HomezI18n.t("pay.col_created_at")}">${fmtDate(m.created_at)}</td>
              <td data-label="${HomezI18n.t("pay.col_action")}">
                ${m.active ? `
                  ${!m.is_default ? `<button type="button" class="btn btn-ghost btn-sm" id="pay-default-${m.id}">${HomezI18n.t("pay.set_default_btn")}</button>` : ""}
                  <button type="button" class="btn btn-ghost btn-sm" id="pay-deactivate-${m.id}">${HomezI18n.t("pay.deactivate_btn")}</button>
                ` : "—"}
              </td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `
      <div class="detail-panel">
        <p class="field-hint">${HomezI18n.t("pay.fake_notice")}</p>
        <p class="field-hint">${HomezI18n.t("pay.automation_mode_hint")}</p>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("pay.methods_heading")}</h2>
      </div>
      ${methodsListHtml}

      <div class="detail-panel">
        <h2>${HomezI18n.t("pay.register_heading")}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pay.type_label")}</span>
            <select id="pay-register-type">${methodTypeOptionsHtml}</select>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("pay.display_name_label")}</span>
            <input type="text" id="pay-register-display-name" placeholder="${escapeHtml(HomezI18n.t("pay.display_name_placeholder"))}" maxlength="100">
          </label>
          <label class="field">
            <span class="field-label">&nbsp;</span>
            <span><input type="checkbox" id="pay-register-make-default"> ${HomezI18n.t("pay.make_default_label")}</span>
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="pay-register-submit-btn">${HomezI18n.t("pay.register_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("pay.auto_limit_heading")}</h2>
        <p class="field-hint">${HomezI18n.t("pay.daily_limit_not_enforced_notice")}</p>
        <h3>${HomezI18n.t("pay.current_limit_heading")}</h3>
        ${limit ? `
          <dl class="kv-list">
            <dt>${HomezI18n.t("pay.per_transaction_limit_label")}</dt><dd>${fmtMoney(limit.per_transaction_limit_amount, limit.currency)}</dd>
            <dt>${HomezI18n.t("pay.daily_limit_label")}</dt><dd>${fmtMoney(limit.daily_limit_amount, limit.currency)}</dd>
          </dl>
        ` : `<p class="stat-sub">${HomezI18n.t("pay.no_limit_set")}</p>`}
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pay.per_transaction_limit_label")}</span>
            <input type="number" min="0" step="1" id="pay-limit-per-transaction" value="${limit ? limit.per_transaction_limit_amount : ""}">
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("pay.daily_limit_label")}</span>
            <input type="number" min="0" step="1" id="pay-limit-daily" value="${limit ? limit.daily_limit_amount : ""}">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="pay-limit-save-btn">${HomezI18n.t("pay.auto_limit_save_submit")}</button></div>
        </div>
      </div>
    `;

    methods.forEach((m) => {
      const defaultBtn = el(`pay-default-${m.id}`);
      if (defaultBtn) {
        defaultBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
          try {
            await apiFetch(`/payments/methods/${m.id}/set-default`, { method: "POST" });
            toast(HomezI18n.t("pay.set_default_success"), "success");
            await loadPayment();
          } catch (err) {
            toast(err.message || "", "error");
          }
        }));
      }
      const deactivateBtn = el(`pay-deactivate-${m.id}`);
      if (deactivateBtn) {
        deactivateBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
          const token = await promptRecentAuthToken();
          if (token === null) return;
          try {
            await apiFetch(`/payments/methods/${m.id}/deactivate`, {
              method: "POST",
              headers: { "X-Recent-Auth-Token": token },
            });
            toast(HomezI18n.t("pay.deactivate_success"), "success");
            await loadPayment();
          } catch (err) {
            toast(err.message || "", "error");
          }
        }));
      }
    });

    el("pay-register-submit-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const displayName = el("pay-register-display-name").value.trim();
      if (!displayName) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      const token = await promptRecentAuthToken();
      if (token === null) return;
      try {
        await apiFetch("/payments/methods", {
          method: "POST",
          headers: { "X-Recent-Auth-Token": token },
          body: JSON.stringify({
            method_type: el("pay-register-type").value,
            display_name: displayName,
            raw_details: {},
            make_default: el("pay-register-make-default").checked,
          }),
        });
        toast(HomezI18n.t("pay.register_success"), "success");
        await loadPayment();
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));

    el("pay-limit-save-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const perTx = Number(el("pay-limit-per-transaction").value);
      const daily = Number(el("pay-limit-daily").value);
      if (!perTx || !daily || perTx <= 0 || daily <= 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      const token = await promptRecentAuthToken();
      if (token === null) return;
      try {
        await apiFetch("/payments/auto-limit", {
          method: "PUT",
          headers: { "X-Recent-Auth-Token": token },
          body: JSON.stringify({
            per_transaction_limit_amount: perTx,
            daily_limit_amount: daily,
            currency: "KRW",
          }),
        });
        toast(HomezI18n.t("pay.auto_limit_save_success"), "success");
        await loadPayment();
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));
  }

  VIEW_LOADERS["payment"] = loadPayment;

  // --------------------------------------------------
  // 환율 관리 (Currency, Phase 9) — 2026-09-10 UI 개선(시스템 차원
  // 발견, docs/HOMEZ_V7_UI_IMPLEMENTATION_AUDIT.md "4. 시스템 차원
  // 발견" 절). 실제 외부 환율 API 연동이 없고(사용자가 직접 확인한
  // 값을 기록), 허용률 초과 판정 로직도 아직 실제 매입 발주 흐름에
  // 연결되지 않았다 — 둘 다 화면에 정직하게 고지한다.
  // --------------------------------------------------

  const CURR_KNOWN_CURRENCIES = ["KRW", "USD", "CNY", "JPY", "EUR"];

  async function loadCurrency() {
    const container = el("curr-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let tolerance;
    try {
      tolerance = await apiFetch("/currency/tolerance");
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    const currencyOptionsHtml = (selected) => CURR_KNOWN_CURRENCIES.map((c) => `<option value="${c}" ${c === selected ? "selected" : ""}>${c}</option>`).join("");

    container.innerHTML = `
      <div class="detail-panel">
        <p class="field-hint">${HomezI18n.t("curr.fake_notice")}</p>
        <p class="field-hint">${HomezI18n.t("curr.tolerance_not_wired_notice")}</p>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("curr.record_heading")}</h2>
        <p class="field-hint">${HomezI18n.t("curr.rate_hint")}</p>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("curr.base_currency_label")}</span>
            <select id="curr-record-base">${currencyOptionsHtml("USD")}</select>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("curr.quote_currency_label")}</span>
            <select id="curr-record-quote">${currencyOptionsHtml("KRW")}</select>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("curr.rate_label")}</span>
            <input type="number" min="0" step="0.0001" id="curr-record-rate">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="curr-record-submit-btn">${HomezI18n.t("curr.record_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("curr.lookup_heading")}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("curr.base_currency_label")}</span>
            <select id="curr-lookup-base">${currencyOptionsHtml("USD")}</select>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("curr.quote_currency_label")}</span>
            <select id="curr-lookup-quote">${currencyOptionsHtml("KRW")}</select>
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="curr-lookup-submit-btn">${HomezI18n.t("curr.lookup_submit")}</button></div>
        </div>
        <div id="curr-lookup-result"></div>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("curr.tolerance_heading")}</h2>
        <dl class="kv-list">
          <dt>${HomezI18n.t("curr.current_tolerance_label")}</dt><dd>${tolerance.tolerance_percent}%</dd>
        </dl>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("curr.new_tolerance_label")}</span>
            <input type="number" min="0.1" step="0.1" id="curr-tolerance-input" value="${tolerance.tolerance_percent}">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="curr-tolerance-save-btn">${HomezI18n.t("curr.tolerance_save_submit")}</button></div>
        </div>
      </div>
    `;

    el("curr-record-submit-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const rate = Number(el("curr-record-rate").value);
      if (!rate || rate <= 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      try {
        await apiFetch("/currency/rates", {
          method: "POST",
          body: JSON.stringify({
            base_currency: el("curr-record-base").value,
            quote_currency: el("curr-record-quote").value,
            rate,
          }),
        });
        toast(HomezI18n.t("curr.record_success"), "success");
        el("curr-record-rate").value = "";
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));

    el("curr-lookup-submit-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const base = el("curr-lookup-base").value;
      const quote = el("curr-lookup-quote").value;
      const resultEl = el("curr-lookup-result");
      resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
      try {
        const rate = await apiFetch(`/currency/rates/latest?base_currency=${encodeURIComponent(base)}&quote_currency=${encodeURIComponent(quote)}`);
        if (!rate) {
          resultEl.innerHTML = `<p class="stat-sub">${HomezI18n.t("curr.lookup_not_found")}</p>`;
          return;
        }
        resultEl.innerHTML = `
          <dl class="kv-list">
            <dt>${HomezI18n.t("curr.col_rate")}</dt><dd>1 ${escapeHtml(rate.base_currency)} = ${rate.rate} ${escapeHtml(rate.quote_currency)}</dd>
            <dt>${HomezI18n.t("curr.col_source")}</dt><dd>${escapeHtml(rate.source)}</dd>
            <dt>${HomezI18n.t("curr.col_recorded_at")}</dt><dd>${fmtDate(rate.recorded_at)}</dd>
          </dl>
        `;
      } catch (err) {
        renderErrorState(resultEl, err);
      }
    }));

    el("curr-tolerance-save-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const value = Number(el("curr-tolerance-input").value);
      if (!value || value <= 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      try {
        await apiFetch("/currency/tolerance", {
          method: "PUT",
          body: JSON.stringify({ tolerance_percent: value }),
        });
        toast(HomezI18n.t("curr.tolerance_save_success"), "success");
        await loadCurrency();
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));
  }

  VIEW_LOADERS["currency"] = loadCurrency;

  // --------------------------------------------------
  // 공급처 능력 (SupplierCapability, Phase 9) — 2026-09-10 UI
  // 개선(시스템 차원 발견). API가 공급처 1곳 단위(`/suppliers/
  // {id}/capability/*`)라 전체 목록 화면이 아니라 ID 조회 방식으로
  // 만든다(Currency 화면의 "조회" 패턴과 동일). "미확인"은 절대
  // 추측하지 않는다 — 기본값 UNKNOWN을 그대로 노출한다.
  // --------------------------------------------------

  const SPC_SUPPORT_OPTIONS = ["SUPPORTED", "NOT_SUPPORTED", "UNKNOWN"];
  const SPC_FLAGS = [
    "PRODUCT_INFO", "OPTION_INFO", "PRICE_INFO", "STOCK_INFO",
    "SHIPPING_FEE_INFO", "SHIPPING_DAYS_INFO", "ORDER_PLACEMENT",
    "CANCELABILITY",
  ];

  let spcCurrentSupplierId = null;

  async function loadSupplierCapability() {
    const container = el("spc-content");
    container.innerHTML = `
      <div class="detail-panel">
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("spc.lookup_label")}</span>
            <input type="number" min="1" id="spc-lookup-id" placeholder="${escapeHtml(HomezI18n.t("spc.lookup_placeholder"))}">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="spc-lookup-submit-btn">${HomezI18n.t("spc.lookup_submit")}</button></div>
        </div>
      </div>
      <div id="spc-detail-panel"></div>
    `;

    el("spc-lookup-submit-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const supplierId = Number(el("spc-lookup-id").value);
      if (!supplierId || supplierId <= 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      await spcShowSupplier(supplierId);
    }));

    if (spcCurrentSupplierId) {
      el("spc-lookup-id").value = spcCurrentSupplierId;
      await spcShowSupplier(spcCurrentSupplierId);
    }
  }

  async function spcShowSupplier(supplierId) {
    spcCurrentSupplierId = supplierId;
    const panel = el("spc-detail-panel");
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let profile, matrix;
    try {
      [profile, matrix] = await Promise.all([
        apiFetch(`/suppliers/${supplierId}/capability/profile`),
        apiFetch(`/suppliers/${supplierId}/capability/matrix`),
      ]);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    const currencyOptionsHtml = CURR_KNOWN_CURRENCIES.map((c) => `<option value="${c}" ${c === profile.default_currency ? "selected" : ""}>${c}</option>`).join("");

    panel.innerHTML = `
      <div class="detail-panel">
        <h2>${HomezI18n.t("spc.profile_heading")} — #${supplierId}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("spc.is_international_label")}</span>
            <span><input type="checkbox" id="spc-profile-international" ${profile.is_international ? "checked" : ""}></span>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("spc.country_code_label")}</span>
            <input type="text" id="spc-profile-country" maxlength="2" placeholder="${escapeHtml(HomezI18n.t("spc.country_code_placeholder"))}" value="${profile.country_code ? escapeHtml(profile.country_code) : ""}">
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("spc.default_currency_label")}</span>
            <select id="spc-profile-currency">${currencyOptionsHtml}</select>
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("spc.consignment_direct_label")}</span>
            <span><input type="checkbox" id="spc-profile-consignment" ${profile.consignment_direct_to_customer ? "checked" : ""}></span>
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="spc-profile-save-btn">${HomezI18n.t("spc.profile_save_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("spc.matrix_heading")}</h2>
        <div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("spc.col_flag")}</th><th>${HomezI18n.t("spc.col_support")}</th>
            <th>${HomezI18n.t("spc.col_note")}</th><th>${HomezI18n.t("spc.col_action")}</th>
          </tr></thead>
          <tbody>${SPC_FLAGS.map((flag) => {
            const current = matrix.capabilities[flag] || {};
            const support = current.support || "UNKNOWN";
            const note = current.note || "";
            return `
              <tr>
                <td data-label="${HomezI18n.t("spc.col_flag")}">${escapeHtml(HomezI18n.t(`spc.flag.${flag.toLowerCase()}`) || flag)}</td>
                <td data-label="${HomezI18n.t("spc.col_support")}">
                  <select id="spc-support-${flag}">${SPC_SUPPORT_OPTIONS.map((s) => `<option value="${s}" ${s === support ? "selected" : ""}>${escapeHtml(HomezI18n.t(`spc.support.${s.toLowerCase()}`))}</option>`).join("")}</select>
                </td>
                <td data-label="${HomezI18n.t("spc.col_note")}"><input type="text" id="spc-note-${flag}" maxlength="500" value="${escapeHtml(note)}"></td>
                <td data-label="${HomezI18n.t("spc.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="spc-save-${flag}">${HomezI18n.t("spc.save_row_btn")}</button></td>
              </tr>
            `;
          }).join("")}</tbody>
        </table></div>
      </div>
    `;

    el("spc-profile-save-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      try {
        await apiFetch(`/suppliers/${supplierId}/capability/profile`, {
          method: "PUT",
          body: JSON.stringify({
            is_international: el("spc-profile-international").checked,
            country_code: el("spc-profile-country").value.trim() || null,
            default_currency: el("spc-profile-currency").value,
            consignment_direct_to_customer: el("spc-profile-consignment").checked,
          }),
        });
        toast(HomezI18n.t("spc.profile_save_success"), "success");
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));

    SPC_FLAGS.forEach((flag) => {
      el(`spc-save-${flag}`).addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        try {
          await apiFetch(`/suppliers/${supplierId}/capability/matrix`, {
            method: "PUT",
            body: JSON.stringify({
              capability: flag,
              support: el(`spc-support-${flag}`).value,
              note: el(`spc-note-${flag}`).value.trim() || null,
            }),
          });
          toast(HomezI18n.t("spc.save_row_success"), "success");
        } catch (err) {
          toast(err.message || "", "error");
        }
      }));
    });
  }

  VIEW_LOADERS["supplier-capability"] = loadSupplierCapability;

  // --------------------------------------------------
  // 가격·재고 안전 설정 (PriceStockSafety, Phase 10) — 2026-09-10
  // UI 개선(시스템 차원 발견). 두 가지 미연결 사실을 화면에 고지한다
  // (app/domains/price_stock_safety/service.py 모듈 docstring 참고):
  // 가상재고 확인은 수동 입력값 검사 도구일 뿐 판매채널을 자동으로
  // 읽지 않고, 가격 검토주기는 저장만 되고 스케줄러에 연결되지
  // 않았다.
  // --------------------------------------------------

  async function loadPriceStockSafety() {
    const container = el("pss-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let threshold, reviewCycle;
    try {
      [threshold, reviewCycle] = await Promise.all([
        apiFetch("/price-stock-safety/virtual-stock-threshold"),
        apiFetch("/price-stock-safety/review-cycle"),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    container.innerHTML = `
      <div class="detail-panel">
        <h2>${HomezI18n.t("pss.threshold_heading")}</h2>
        <p class="field-hint">${HomezI18n.t("pss.threshold_not_wired_notice")}</p>
        <dl class="kv-list">
          <dt>${HomezI18n.t("pss.current_threshold_label")}</dt>
          <dd>${threshold.threshold_quantity !== null && threshold.threshold_quantity !== undefined ? threshold.threshold_quantity : HomezI18n.t("pss.no_threshold_set")}</dd>
        </dl>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pss.new_threshold_label")}</span>
            <input type="number" min="0" step="1" id="pss-threshold-input" value="${threshold.threshold_quantity ?? ""}">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="pss-threshold-save-btn">${HomezI18n.t("pss.threshold_save_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("pss.check_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pss.displayed_stock_label")}</span>
            <input type="number" min="0" step="1" id="pss-check-stock-input">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="pss-check-submit-btn">${HomezI18n.t("pss.check_submit")}</button></div>
        </div>
        <div id="pss-check-result"></div>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("pss.review_cycle_heading")}</h2>
        <p class="field-hint">${HomezI18n.t("pss.review_cycle_not_wired_notice")}</p>
        <dl class="kv-list">
          <dt>${HomezI18n.t("pss.current_review_cycle_label")}</dt>
          <dd>${reviewCycle.review_cycle_days}${HomezI18n.t("pss.review_cycle_days_suffix")}</dd>
        </dl>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("pss.new_review_cycle_label")}</span>
            <input type="number" min="1" step="1" id="pss-review-cycle-input" value="${reviewCycle.review_cycle_days}">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="pss-review-cycle-save-btn">${HomezI18n.t("pss.review_cycle_save_submit")}</button></div>
        </div>
      </div>
    `;

    el("pss-threshold-save-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const value = el("pss-threshold-input").value;
      if (value === "" || Number(value) < 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      try {
        await apiFetch("/price-stock-safety/virtual-stock-threshold", {
          method: "PUT",
          body: JSON.stringify({ threshold_quantity: Number(value) }),
        });
        toast(HomezI18n.t("pss.threshold_save_success"), "success");
        await loadPriceStockSafety();
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));

    el("pss-check-submit-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const stockValue = el("pss-check-stock-input").value;
      const resultEl = el("pss-check-result");
      if (stockValue === "" || Number(stockValue) < 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
      try {
        const result = await apiFetch("/price-stock-safety/virtual-stock-check", {
          method: "POST",
          body: JSON.stringify({ displayed_stock: Number(stockValue) }),
        });
        resultEl.innerHTML = `<p class="${result.allowed ? "risk-low" : "risk-high"}">${result.allowed ? HomezI18n.t("pss.check_allowed") : `${HomezI18n.t("pss.check_blocked")} — ${escapeHtml(result.reason || "")}`}</p>`;
      } catch (err) {
        renderErrorState(resultEl, err);
      }
    }));

    el("pss-review-cycle-save-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const value = el("pss-review-cycle-input").value;
      if (!value || Number(value) < 1) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      try {
        await apiFetch("/price-stock-safety/review-cycle", {
          method: "PUT",
          body: JSON.stringify({ review_cycle_days: Number(value) }),
        });
        toast(HomezI18n.t("pss.review_cycle_save_success"), "success");
        await loadPriceStockSafety();
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));
  }

  VIEW_LOADERS["price-stock-safety"] = loadPriceStockSafety;

  // --------------------------------------------------
  // AI 학습 기반 (AiLearning, Phase 12) — 2026-09-10 UI 개선(시스템
  // 차원 발견). ModelCandidateStatus 자신이 "APPROVED조차 실제
  // 라이브 적용을 의미하지 않는다"고 명시한다(app/domains/
  // ai_learning/constants.py 참고) — 라벨에 그대로 반영한다. 평가결과
  // 기록/데이터셋 export는 Decision AI 화면과의 연결 설계가 필요해
  // 이번 범위에 포함하지 않았다(docs/HOMEZ_V7_UI_IMPLEMENTATION_
  // AUDIT.md 참고) — 모델 후보 심사 + 학습 데이터 준비도 확인만.
  // --------------------------------------------------

  const ailState = { rows: [], statusFilter: "", detailCandidateId: null };
  const AIL_STATUS_OPTIONS = ["DRAFT", "OFFLINE_EVALUATED", "REGRESSION_COMPARED", "APPROVED", "REJECTED"];

  async function loadAiLearning() {
    ailState.detailCandidateId = null;
    await ailRenderList();
  }

  async function ailRenderList() {
    const container = el("ail-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      const qs = ailState.statusFilter ? `?status=${encodeURIComponent(ailState.statusFilter)}` : "";
      rows = await apiFetch(`/ai-learning/model-candidates${qs}`);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    ailState.rows = rows || [];

    const filterHtml = `
      <div class="detail-panel">
        <p class="field-hint">${HomezI18n.t("ail.fake_notice")}</p>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("ail.filter.status_label")}</span>
            ${statusFilterSelectHtml("ail-filter-status", AIL_STATUS_OPTIONS, ailState.statusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="ail-refresh-btn">${HomezI18n.t("common.refresh")}</button></div>
        </div>
      </div>
    `;

    const listHtml = ailState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("ail.empty"), HomezI18n.t("ail.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("ail.col_id")}</th><th>${HomezI18n.t("ail.col_name")}</th>
            <th>${HomezI18n.t("ail.col_version")}</th><th>${HomezI18n.t("ail.col_status")}</th>
            <th>${HomezI18n.t("ail.col_created_at")}</th><th>${HomezI18n.t("ail.col_action")}</th>
          </tr></thead>
          <tbody>${ailState.rows.map((c) => `
            <tr>
              <td data-label="${HomezI18n.t("ail.col_id")}">#${c.id}</td>
              <td data-label="${HomezI18n.t("ail.col_name")}">${escapeHtml(c.name)}</td>
              <td data-label="${HomezI18n.t("ail.col_version")}">${escapeHtml(c.version)}</td>
              <td data-label="${HomezI18n.t("ail.col_status")}">${statusPillHtmlLabeled(c.status, "ail.status.")}</td>
              <td data-label="${HomezI18n.t("ail.col_created_at")}">${fmtDate(c.created_at)}</td>
              <td data-label="${HomezI18n.t("ail.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="ail-detail-btn-${c.id}">${HomezI18n.t("ret.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    const createHtml = `
      <div class="detail-panel">
        <h2>${HomezI18n.t("ail.create_heading")}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("ail.name_label")}</span>
            <input type="text" id="ail-create-name" maxlength="200">
          </label>
          <label class="field">
            <span class="field-label">${HomezI18n.t("ail.version_label")}</span>
            <input type="text" id="ail-create-version" maxlength="50">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ail-create-submit-btn">${HomezI18n.t("ail.create_submit")}</button></div>
        </div>
      </div>
    `;

    const readinessHtml = `
      <div class="detail-panel">
        <h2>${HomezI18n.t("ail.readiness_heading")}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("ail.total_completed_orders_label")}</span>
            <input type="number" min="0" step="1" id="ail-readiness-orders">
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="ail-readiness-check-btn">${HomezI18n.t("ail.readiness_check_submit")}</button></div>
        </div>
        <div id="ail-readiness-result"></div>
      </div>
    `;

    container.innerHTML = `${filterHtml}${listHtml}${createHtml}${readinessHtml}<div id="ail-detail-panel"></div>`;

    el("ail-filter-status").addEventListener("change", (ev) => {
      ailState.statusFilter = ev.target.value;
      ailRenderList();
    });
    el("ail-refresh-btn").addEventListener("click", () => ailRenderList());

    ailState.rows.forEach((c) => {
      const btn = el(`ail-detail-btn-${c.id}`);
      if (btn) btn.addEventListener("click", () => ailShowDetail(c.id));
    });

    el("ail-create-submit-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const name = el("ail-create-name").value.trim();
      const version = el("ail-create-version").value.trim();
      if (!name || !version) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      try {
        await apiFetch("/ai-learning/model-candidates", {
          method: "POST",
          body: JSON.stringify({ name, version }),
        });
        toast(HomezI18n.t("ail.create_success"), "success");
        await ailRenderList();
      } catch (err) {
        toast(err.message || "", "error");
      }
    }));

    el("ail-readiness-check-btn").addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
      const total = el("ail-readiness-orders").value;
      const resultEl = el("ail-readiness-result");
      if (total === "" || Number(total) < 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
      try {
        const result = await apiFetch(`/ai-learning/dataset/readiness?total_completed_orders=${encodeURIComponent(total)}`);
        resultEl.innerHTML = `
          <dl class="kv-list">
            <dt>${HomezI18n.t("ail.filter.status_label")}</dt><dd class="${result.ready ? "risk-low" : "risk-high"}">${result.ready ? HomezI18n.t("ail.readiness_ready") : HomezI18n.t("ail.readiness_not_ready")}${result.reason ? ` — ${escapeHtml(result.reason)}` : ""}</dd>
            <dt>${HomezI18n.t("ail.readiness_available_label")}</dt><dd>${result.available}</dd>
            <dt>${HomezI18n.t("ail.readiness_required_label")}</dt><dd>${result.required}</dd>
          </dl>
        `;
      } catch (err) {
        renderErrorState(resultEl, err);
      }
    }));

    if (ailState.detailCandidateId) {
      await ailShowDetail(ailState.detailCandidateId);
    }
  }

  async function ailShowDetail(candidateId) {
    ailState.detailCandidateId = candidateId;
    const panel = el("ail-detail-panel");
    if (!panel) return;

    const c = ailState.rows.find((r) => r.id === candidateId);
    if (!c) {
      panel.innerHTML = "";
      return;
    }

    const canOfflineEval = c.status === "DRAFT";
    const canRegressionCompare = c.status === "OFFLINE_EVALUATED";
    const canApprove = c.status === "REGRESSION_COMPARED";
    const canReject = ["DRAFT", "OFFLINE_EVALUATED", "REGRESSION_COMPARED"].includes(c.status);

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("ail.detail_heading")} — #${c.id}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="ail-detail-close-btn">${HomezI18n.t("ail.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("ail.col_name")}</dt><dd>${escapeHtml(c.name)}</dd>
          <dt>${HomezI18n.t("ail.col_version")}</dt><dd>${escapeHtml(c.version)}</dd>
          <dt>${HomezI18n.t("ail.col_status")}</dt><dd>${statusPillHtmlLabeled(c.status, "ail.status.")}</dd>
          <dt>${HomezI18n.t("ail.col_sample_size")}</dt><dd>${c.sample_size_used ?? "—"}</dd>
          <dt>${HomezI18n.t("ail.col_offline_summary")}</dt><dd>${c.offline_eval_summary ? escapeHtml(c.offline_eval_summary) : "—"}</dd>
          <dt>${HomezI18n.t("ail.col_regression_summary")}</dt><dd>${c.regression_comparison_summary ? escapeHtml(c.regression_comparison_summary) : "—"}</dd>
          <dt>${HomezI18n.t("ail.col_approved_by")}</dt><dd>${c.approved_by ? `#${c.approved_by}` : "—"}</dd>
          <dt>${HomezI18n.t("ail.col_approved_at")}</dt><dd>${c.approved_at ? fmtDate(c.approved_at) : "—"}</dd>
        </dl>
      </div>

      ${canOfflineEval ? `
      <div class="detail-panel">
        <h3>${HomezI18n.t("ail.offline_eval_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("ail.offline_summary_label")}</span><input type="text" id="ail-offline-summary" maxlength="1000"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("ail.sample_size_label")}</span><input type="number" min="0" step="1" id="ail-offline-sample-size"></label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ail-offline-submit-btn">${HomezI18n.t("ail.offline_eval_submit")}</button></div>
        </div>
      </div>
      ` : ""}

      ${canRegressionCompare ? `
      <div class="detail-panel">
        <h3>${HomezI18n.t("ail.regression_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("ail.regression_summary_label")}</span><input type="text" id="ail-regression-summary" maxlength="1000"></label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="ail-regression-submit-btn">${HomezI18n.t("ail.regression_submit")}</button></div>
        </div>
      </div>
      ` : ""}

      <div class="detail-panel">
        <div class="decision-actions">
          <button type="button" class="btn btn-primary btn-sm" id="ail-approve-btn" ${canApprove ? "" : "disabled"}>${HomezI18n.t("ail.approve_btn")}</button>
          <button type="button" class="btn btn-danger btn-sm" id="ail-reject-btn" ${canReject ? "" : "disabled"}>${HomezI18n.t("ail.reject_btn")}</button>
        </div>
      </div>
    `;

    el("ail-detail-close-btn").addEventListener("click", () => {
      ailState.detailCandidateId = null;
      panel.innerHTML = "";
    });

    const offlineBtn = el("ail-offline-submit-btn");
    if (offlineBtn) {
      offlineBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        const summary = el("ail-offline-summary").value.trim();
        const sampleSize = el("ail-offline-sample-size").value;
        if (!summary || sampleSize === "" || Number(sampleSize) < 0) {
          toast(HomezI18n.t("inv.quantity_required_error"), "error");
          return;
        }
        try {
          await apiFetch(`/ai-learning/model-candidates/${candidateId}/offline-evaluated`, {
            method: "POST",
            body: JSON.stringify({ summary, sample_size_used: Number(sampleSize) }),
          });
          toast(HomezI18n.t("ail.offline_eval_success"), "success");
          await ailRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      }));
    }

    const regressionBtn = el("ail-regression-submit-btn");
    if (regressionBtn) {
      regressionBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        const summary = el("ail-regression-summary").value.trim();
        if (!summary) {
          toast(HomezI18n.t("inv.quantity_required_error"), "error");
          return;
        }
        try {
          await apiFetch(`/ai-learning/model-candidates/${candidateId}/regression-compared`, {
            method: "POST",
            body: JSON.stringify({ summary }),
          });
          toast(HomezI18n.t("ail.regression_success"), "success");
          await ailRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      }));
    }

    const approveBtn = el("ail-approve-btn");
    if (approveBtn && !approveBtn.disabled) {
      approveBtn.addEventListener("click", (event) => withButtonGuard(event.currentTarget, async () => {
        const token = await promptRecentAuthToken();
        if (token === null) return;
        try {
          await apiFetch(`/ai-learning/model-candidates/${candidateId}/approve`, {
            method: "POST",
            headers: { "X-Recent-Auth-Token": token },
          });
          toast(HomezI18n.t("ail.approve_success"), "success");
          await ailRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      }));
    }

    const rejectBtn = el("ail-reject-btn");
    if (rejectBtn && !rejectBtn.disabled) {
      rejectBtn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t("ail.reject_confirm_title"),
          body: HomezI18n.t("ail.reject_confirm_body"),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/ai-learning/model-candidates/${candidateId}/reject`, {
            method: "POST",
            body: JSON.stringify({ reason: value }),
          });
          toast(HomezI18n.t("ail.reject_success"), "success");
          await ailRenderList();
        } catch (err) {
          toast(err.message || "", "error");
        }
      });
    }
  }

  VIEW_LOADERS["ai-learning"] = loadAiLearning;

  // --------------------------------------------------
  // 마진 · 수익 분석 (Pricing / Margin, Gate 5)
  // --------------------------------------------------

  const prcState = { rows: [], detailPricingId: null };

  const PRC_ECON_FIELDS = [
    ["cost_of_goods", "prc.cost_of_goods_label"],
    ["shipping_cost", "prc.shipping_cost_label"],
    ["packaging_cost", "prc.packaging_cost_label"],
    ["ad_cost", "prc.ad_cost_label"],
    ["channel_fee_rate", "prc.channel_fee_rate_label"],
    ["payment_fee_rate", "prc.payment_fee_rate_label"],
    ["return_reserve_rate", "prc.return_reserve_rate_label"],
    ["tax_basis_rate", "prc.tax_basis_rate_label"],
  ];

  // 2026-09-10 UI 개선 중 발견·수정 — 이 4개는 서버에 0~1 소수점
  // 비율로 저장되지만(app/domains/pricing/model.py::RATE), 나머지
  // 4개(cost_of_goods 등)는 원화 금액이다. 입력·표시만 %로
  // 바꾸고(퍼센트 입력 → 저장 시 /100) 서버 계약은 그대로 유지한다
  // (Phase 10이 purchase_task/retail_purchase에 이미 적용한 것과
  // 동일한 원칙 — 이 화면엔 빠져 있었다).
  const PRC_RATE_FIELDS = new Set([
    "channel_fee_rate", "payment_fee_rate", "return_reserve_rate", "tax_basis_rate",
  ]);

  // MarginSnapshotResponse의 13개 항목 — 금액 항목과 비율 항목을
  // 구분해 표시 방식을 다르게 한다(금액은 fmtMoney, margin_rate만 %).
  const PRC_COMPARISON_ITEMS = [
    "revenue", "cost_of_goods", "channel_fee", "payment_fee",
    "shipping_cost", "packaging_cost", "ad_cost", "return_reserve",
    "tax", "refund_adjustment", "total_cost", "margin_amount",
  ];

  // 2026-09-10 UI 개선(시안 08 정산·손익 관리 참고) — /pricing/
  // {listing_id}/margin-variance가 이미 돌려주는 expected/
  // latest_actual 전체 스냅샷(13개 항목)을 지금까지 화면이 버리고
  // 차이값만 보여주고 있었다. 실제로 존재하는 데이터로 "예상/확정/
  // 차이" 표를 만든다 — latest_actual이 없으면(아직 정산 반영 전)
  // 확정 칸을 "—"로 정직하게 남기고 0으로 추측하지 않는다.
  function prcComparisonTableHtml(variance) {
    const expected = variance.expected;
    const actual = variance.latest_actual || null;

    // 2026-09-11 후속(Phase 12 — UI-9 추가 검증) — 백엔드는 확정
    // 스냅샷의 각 항목이 실측인지 추정 대체인지를 estimated_
    // components_json에 이미 기록해 두고 있었지만, 이 화면은 지금까지
    // 그 값을 읽지 않고 있었다(반품·광고비·배송비·세금 등의 "출처"를
    // 보여주라는 요구사항이 채워지지 않은 상태였다). 파싱 실패 시
    // 조용히 빈 집합으로 — "출처 표시 실패"가 화면 전체를 깨뜨리지
    // 않는다.
    let estimatedComponents = new Set();
    if (actual && actual.estimated_components_json) {
      try {
        estimatedComponents = new Set(JSON.parse(actual.estimated_components_json));
      } catch (err) {
        estimatedComponents = new Set();
      }
    }

    const rows = PRC_COMPARISON_ITEMS.map((item) => {
      const expVal = expected && expected[item] !== undefined ? Number(expected[item]) : null;
      const actVal = actual && actual[item] !== undefined ? Number(actual[item]) : null;
      const diff = (expVal !== null && actVal !== null) ? (actVal - expVal) : null;
      const diffCls = diff === null ? "" : (diff === 0 ? "risk-low" : "risk-high");
      const isEstimated = actVal !== null && estimatedComponents.has(item);
      const sourceBadge = actVal === null
        ? ""
        : ` <span class="pill neutral prc-source-badge">${HomezI18n.t(isEstimated ? "prc.source_estimated" : "prc.source_measured")}</span>`;
      return `
        <tr>
          <td data-label="${HomezI18n.t("prc.comparison_col_item")}">${escapeHtml(HomezI18n.t(`prc.item.${item}`))}</td>
          <td data-label="${HomezI18n.t("prc.comparison_col_expected")}">${expVal === null ? "—" : fmtMoney(expVal)}</td>
          <td data-label="${HomezI18n.t("prc.comparison_col_actual")}">${actVal === null ? "—" : fmtMoney(actVal)}${sourceBadge}</td>
          <td data-label="${HomezI18n.t("prc.comparison_col_diff")}">${diff === null ? "—" : `<span class="${diffCls}">${fmtMoney(diff)}</span>`}</td>
        </tr>
      `;
    }).join("");

    const expRate = expected && expected.margin_rate !== undefined ? Number(expected.margin_rate) * 100 : null;
    const actRate = actual && actual.margin_rate !== undefined ? Number(actual.margin_rate) * 100 : null;
    const rateDiff = (expRate !== null && actRate !== null) ? (actRate - expRate) : null;
    const rateDiffCls = rateDiff === null ? "" : (rateDiff === 0 ? "risk-low" : "risk-high");
    const rateRow = `
      <tr>
        <td data-label="${HomezI18n.t("prc.comparison_col_item")}">${escapeHtml(HomezI18n.t("prc.item.margin_rate"))}</td>
        <td data-label="${HomezI18n.t("prc.comparison_col_expected")}">${expRate === null ? "—" : `${expRate.toFixed(1)}%`}</td>
        <td data-label="${HomezI18n.t("prc.comparison_col_actual")}">${actRate === null ? "—" : `${actRate.toFixed(1)}%`}</td>
        <td data-label="${HomezI18n.t("prc.comparison_col_diff")}">${rateDiff === null ? "—" : `<span class="${rateDiffCls}">${rateDiff.toFixed(1)}%p</span>`}</td>
      </tr>
    `;

    return `
      ${!actual ? `<p class="field-hint">${HomezI18n.t("prc.comparison_no_actual")}</p>` : ""}
      <div class="table-wrap"><table class="responsive-cards">
        <thead><tr>
          <th>${HomezI18n.t("prc.comparison_col_item")}</th><th>${HomezI18n.t("prc.comparison_col_expected")}</th>
          <th>${HomezI18n.t("prc.comparison_col_actual")}</th><th>${HomezI18n.t("prc.comparison_col_diff")}</th>
        </tr></thead>
        <tbody>${rows}${rateRow}</tbody>
      </table></div>
    `;
  }

  async function loadMarginAnalysis() {
    prcState.detailPricingId = null;
    await prcRenderList();
  }

  async function prcRenderList() {
    const container = el("prc-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      rows = await apiFetch("/pricing?limit=200");
    } catch (err) {
      renderErrorState(container, err);
      return;
    }
    prcState.rows = rows || [];

    const initFieldsHtml = PRC_ECON_FIELDS.map(([field, labelKey]) => {
      const isRate = PRC_RATE_FIELDS.has(field);
      return `<label class="field"><span class="field-label">${HomezI18n.t(labelKey)}</span><input type="number" step="${isRate ? "0.01" : "1"}" min="0" ${isRate ? 'max="100"' : ""} id="prc-init-${field}" value="0"></label>`;
    }).join("");

    const initHtml = `
      <div class="detail-panel">
        <h3>${HomezI18n.t("prc.init_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("prc.listing_id_label")}</span><input type="number" min="1" id="prc-init-listing-id"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("prc.initial_price_label")}</span><input type="number" min="0" step="0.01" id="prc-init-price"></label>
          ${initFieldsHtml}
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="prc-init-submit-btn">${HomezI18n.t("prc.init_submit")}</button></div>
        </div>
      </div>
    `;

    const listHtml = prcState.rows.length === 0
      ? simpleEmptyPanel(HomezI18n.t("prc.empty"), HomezI18n.t("prc.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("prc.col_listing_id")}</th><th>${HomezI18n.t("prc.col_sale_price")}</th>
            <th>${HomezI18n.t("prc.col_margin_rate")}</th><th>${HomezI18n.t("prc.col_margin_amount")}</th>
            <th>${HomezI18n.t("prc.col_version")}</th><th>${HomezI18n.t("prc.col_action")}</th>
          </tr></thead>
          <tbody>${prcState.rows.map((p) => `
            <tr>
              <td data-label="${HomezI18n.t("prc.col_listing_id")}">#${p.listing_id}</td>
              <td data-label="${HomezI18n.t("prc.col_sale_price")}">${p.current_sale_price !== undefined ? fmtMoney(p.current_sale_price) : "—"}</td>
              <td data-label="${HomezI18n.t("prc.col_margin_rate")}">${p.expected_margin_rate !== undefined ? `${(Number(p.expected_margin_rate) * 100).toFixed(1)}%` : "—"}</td>
              <td data-label="${HomezI18n.t("prc.col_margin_amount")}">${p.expected_margin_amount !== undefined ? fmtMoney(p.expected_margin_amount) : "—"}</td>
              <td data-label="${HomezI18n.t("prc.col_version")}">${p.version}</td>
              <td data-label="${HomezI18n.t("prc.col_action")}"><button type="button" class="btn btn-ghost btn-sm" id="prc-detail-btn-${p.id}">${HomezI18n.t("prc.detail_btn")}</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    const csvHtml = `
      <div class="detail-panel">
        <button type="button" class="btn btn-ghost btn-sm" id="prc-csv-btn">${HomezI18n.t("prc.csv_btn")}</button>
      </div>
    `;

    container.innerHTML = `${initHtml}${listHtml}${csvHtml}<div id="prc-detail-panel"></div>`;

    el("prc-init-submit-btn").addEventListener("click", async () => {
      const listingId = Number(el("prc-init-listing-id").value);
      const price = Number(el("prc-init-price").value);
      if (!listingId || !price || price <= 0) {
        toast(HomezI18n.t("inv.quantity_required_error"), "error");
        return;
      }
      const body = { listing_id: listingId, initial_sale_price: price };
      PRC_ECON_FIELDS.forEach(([field]) => {
        const raw = Number(el(`prc-init-${field}`).value) || 0;
        body[field] = PRC_RATE_FIELDS.has(field) ? raw / 100 : raw;
      });
      try {
        await apiFetch("/pricing/init", { method: "POST", body: JSON.stringify(body) });
        toast(HomezI18n.t("prc.init_success"), "success");
        await prcRenderList();
      } catch (err) {
        toast(err.message || HomezI18n.t("prc.load_error"), "error");
      }
    });

    el("prc-csv-btn").addEventListener("click", async () => {
      const btn = el("prc-csv-btn");
      btn.disabled = true;
      try {
        const locale = (HomezI18n.getLocale && HomezI18n.getLocale()) || "ko-KR";
        const csvText = await apiFetch(`/pricing/export/csv?locale=${encodeURIComponent(locale)}`);
        const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "homez_pricing_accounting.csv";
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        toast(err.message || HomezI18n.t("prc.load_error"), "error");
      } finally {
        btn.disabled = false;
      }
    });

    prcState.rows.forEach((p) => {
      const btn = el(`prc-detail-btn-${p.id}`);
      if (btn) btn.addEventListener("click", () => prcShowDetail(p.id));
    });

    if (prcState.detailPricingId) {
      await prcShowDetail(prcState.detailPricingId);
    }
  }

  async function prcShowDetail(pricingId) {
    prcState.detailPricingId = pricingId;
    const panel = el("prc-detail-panel");
    if (!panel) return;
    panel.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let pricing;
    try {
      pricing = await apiFetch(`/pricing/${pricingId}`);
    } catch (err) {
      renderErrorState(panel, err);
      return;
    }

    const listingId = pricing.listing_id;
    let priceChanges = [];
    let marginSnapshots = [];
    let variance = null;
    try {
      [priceChanges, marginSnapshots, variance] = await Promise.all([
        apiFetch(`/pricing/${listingId}/price-changes?limit=100`),
        apiFetch(`/pricing/${listingId}/margin-snapshots?limit=100`),
        apiFetch(`/pricing/${listingId}/margin-variance`),
      ]);
    } catch (_) {
      // 서브 리소스 조회 실패는 상세 화면 자체를 막지 않는다 —
      // 각 섹션은 독립적으로 비어 있는 상태로 표시된다.
    }

    // 2026-09-10 UI 개선(시안 08 정산·손익 관리 참고) — "Listing
    // #47"만으로는 어떤 상품인지 알 수 없다. Listing→ProductCandidate
    // 2단계 조회로 상품명을 붙인다(둘 중 하나라도 실패하면 상품명 없이
    // Listing ID만 표시 — 상세 화면 자체를 막지 않는다, 위와 동일한
    // 원칙). 목록 화면에서는 N+1 호출을 피하기 위해 하지 않는다 —
    // 상세 화면 1회 조회에서만 수행한다.
    let productName = null;
    try {
      const listing = await apiFetch(`/marketplace-listings/${listingId}`);
      const candidate = await apiFetch(`/product-candidates/${listing.product_candidate_id}`);
      productName = candidate.product_name || null;
    } catch (_) {
      productName = null;
    }

    const econFieldsHtml = PRC_ECON_FIELDS.map(([field, labelKey]) => {
      const isRate = PRC_RATE_FIELDS.has(field);
      const raw = pricing[field] !== undefined ? Number(pricing[field]) : 0;
      const displayValue = isRate ? raw * 100 : raw;
      return `<label class="field"><span class="field-label">${HomezI18n.t(labelKey)}</span><input type="number" step="${isRate ? "0.01" : "1"}" min="0" ${isRate ? 'max="100"' : ""} id="prc-econ-${field}" value="${displayValue}"></label>`;
    }).join("");

    panel.innerHTML = `
      <div class="detail-panel">
        <div class="view-header">
          <h2>${HomezI18n.t("prc.detail_heading")} — Listing #${listingId}</h2>
          <button type="button" class="btn btn-ghost btn-sm" id="prc-detail-close-btn">${HomezI18n.t("prc.close_btn")}</button>
        </div>
        <dl class="kv-list">
          <dt>${HomezI18n.t("prc.product_name_label")}</dt><dd>${productName ? escapeHtml(productName) : HomezI18n.t("prc.product_name_unavailable")}</dd>
          <dt>${HomezI18n.t("prc.col_sale_price")}</dt><dd>${pricing.current_sale_price !== undefined ? fmtMoney(pricing.current_sale_price) : "—"}</dd>
          <dt>${HomezI18n.t("prc.col_margin_rate")}</dt><dd>${pricing.expected_margin_rate !== undefined ? `${(Number(pricing.expected_margin_rate) * 100).toFixed(1)}%` : "—"}</dd>
          <dt>${HomezI18n.t("prc.col_margin_amount")}</dt><dd>${pricing.expected_margin_amount !== undefined ? fmtMoney(pricing.expected_margin_amount) : "—"}</dd>
        </dl>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("prc.economics_heading")}</h3>
        <div class="ls-filter-row">
          ${econFieldsHtml}
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="prc-econ-submit-btn">${HomezI18n.t("prc.economics_submit")}</button></div>
        </div>
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("prc.price_change_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("prc.requested_price_label")}</span><input type="number" min="0" step="0.01" id="prc-pc-price"></label>
          <label class="field"><span class="field-label">${HomezI18n.t("prc.price_change_reason_label")}</span><input type="text" id="prc-pc-reason"></label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="prc-pc-submit-btn">${HomezI18n.t("prc.price_change_submit")}</button></div>
        </div>
        ${(!priceChanges || priceChanges.length === 0) ? `<p class="stat-sub">${HomezI18n.t("prc.price_change_empty")}</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("prc.col_pc_requested")}</th><th>${HomezI18n.t("prc.col_pc_status")}</th><th>${HomezI18n.t("prc.col_pc_requested_at")}</th><th>${HomezI18n.t("prc.col_pc_action")}</th></tr></thead>
            <tbody>${priceChanges.map((pc) => `
              <tr>
                <td data-label="${HomezI18n.t("prc.col_pc_requested")}">${fmtMoney(pc.requested_sale_price)}</td>
                <td data-label="${HomezI18n.t("prc.col_pc_status")}">${statusPillHtml(pc.status)}</td>
                <td data-label="${HomezI18n.t("prc.col_pc_requested_at")}">${fmtDate(pc.requested_at)}</td>
                <td data-label="${HomezI18n.t("prc.col_pc_action")}">
                  ${pc.status === "PENDING" ? `
                    <button type="button" class="btn btn-ghost btn-sm" id="prc-pc-approve-${pc.id}">${HomezI18n.t("prc.approve_btn")}</button>
                    <button type="button" class="btn btn-ghost btn-sm" id="prc-pc-reject-${pc.id}">${HomezI18n.t("prc.reject_btn")}</button>
                    <button type="button" class="btn btn-ghost btn-sm" id="prc-pc-cancel-${pc.id}">${HomezI18n.t("prc.cancel_btn")}</button>
                  ` : "—"}
                </td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("prc.margin_snapshots_heading")}</h3>
        ${(!marginSnapshots || marginSnapshots.length === 0) ? `<p class="stat-sub">${HomezI18n.t("prc.margin_snapshots_empty")}</p>` : `
          <div class="table-wrap"><table class="responsive-cards">
            <thead><tr><th>${HomezI18n.t("prc.col_ms_type")}</th><th>${HomezI18n.t("prc.col_ms_margin_amount")}</th><th>${HomezI18n.t("prc.col_ms_margin_rate")}</th><th>${HomezI18n.t("prc.col_ms_reason")}</th><th>${HomezI18n.t("prc.col_ms_date")}</th></tr></thead>
            <tbody>${marginSnapshots.map((ms) => `
              <tr>
                <td data-label="${HomezI18n.t("prc.col_ms_type")}">${statusPillHtmlLabeled(ms.margin_type, "prc.margin_type.")}</td>
                <td data-label="${HomezI18n.t("prc.col_ms_margin_amount")}">${fmtMoney(ms.margin_amount)}</td>
                <td data-label="${HomezI18n.t("prc.col_ms_margin_rate")}">${(Number(ms.margin_rate) * 100).toFixed(1)}%</td>
                <td data-label="${HomezI18n.t("prc.col_ms_reason")}">${escapeHtml(ms.reason)}</td>
                <td data-label="${HomezI18n.t("prc.col_ms_date")}">${fmtDate(ms.created_at)}</td>
              </tr>
            `).join("")}</tbody>
          </table></div>
        `}
      </div>

      <div class="detail-panel">
        <h3>${HomezI18n.t("prc.comparison_heading")}</h3>
        ${!variance || !variance.expected ? `<p class="stat-sub">${HomezI18n.t("prc.margin_variance_empty")}</p>` : prcComparisonTableHtml(variance)}
      </div>
    `;

    el("prc-detail-close-btn").addEventListener("click", () => {
      prcState.detailPricingId = null;
      panel.innerHTML = "";
    });

    el("prc-econ-submit-btn").addEventListener("click", async () => {
      const body = {};
      PRC_ECON_FIELDS.forEach(([field]) => {
        const raw = Number(el(`prc-econ-${field}`).value) || 0;
        body[field] = PRC_RATE_FIELDS.has(field) ? raw / 100 : raw;
      });
      try {
        await apiFetch(`/pricing/${pricingId}/economics`, { method: "PATCH", body: JSON.stringify(body) });
        toast(HomezI18n.t("prc.economics_success"), "success");
        await prcShowDetail(pricingId);
      } catch (err) {
        toast(err.message || HomezI18n.t("prc.load_error"), "error");
      }
    });

    el("prc-pc-submit-btn").addEventListener("click", async () => {
      const price = Number(el("prc-pc-price").value);
      const reason = el("prc-pc-reason").value.trim();
      if (!price || price <= 0) { toast(HomezI18n.t("inv.quantity_required_error"), "error"); return; }
      try {
        await apiFetch(`/pricing/${listingId}/price-changes`, {
          method: "POST",
          body: JSON.stringify({
            requested_sale_price: price,
            reason: reason || null,
            idempotency_key: genIdemKey(`price-change-${listingId}`),
          }),
        });
        toast(HomezI18n.t("prc.price_change_success"), "success");
        await prcShowDetail(pricingId);
      } catch (err) {
        toast(err.message || HomezI18n.t("prc.load_error"), "error");
      }
    });

    (priceChanges || []).forEach((pc) => {
      if (pc.status !== "PENDING") return;
      const bind = (btnId, path, successKey) => {
        const btn = el(btnId);
        if (!btn) return;
        btn.addEventListener("click", async () => {
          try {
            await apiFetch(`/pricing/price-changes/${pc.id}/${path}`, { method: "POST" });
            toast(HomezI18n.t(successKey), "success");
            await prcShowDetail(pricingId);
          } catch (err) {
            toast(err.message || HomezI18n.t("prc.load_error"), "error");
          }
        });
      };
      bind(`prc-pc-approve-${pc.id}`, "approve", "prc.approve_success");
      bind(`prc-pc-reject-${pc.id}`, "reject", "prc.reject_success");
      bind(`prc-pc-cancel-${pc.id}`, "cancel", "prc.cancel_success");
    });
  }

  VIEW_LOADERS["margin-analysis"] = loadMarginAnalysis;

  // --------------------------------------------------
  // 채널 정산 (Settlement Reconciliation, Gate 5)
  // --------------------------------------------------

  const stlState = { reconStatusFilter: "", settlementStatusFilter: "" };
  const RECONCILIATION_STATUS_OPTIONS = ["PENDING_SETTLEMENT", "MATCHED", "MISMATCH", "HELD"];
  const SETTLEMENT_STATUS_OPTIONS = ["PENDING", "HELD", "MISMATCH", "DEPOSITED", "CANCELLED", "REVERSED"];

  async function loadChannelSettlement() {
    const container = el("stl-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let recons, settlements;
    try {
      const reconQs = stlState.reconStatusFilter ? `?status=${encodeURIComponent(stlState.reconStatusFilter)}&limit=200` : "?limit=200";
      const settlementQs = stlState.settlementStatusFilter ? `?status=${encodeURIComponent(stlState.settlementStatusFilter)}&limit=200` : "?limit=200";
      [recons, settlements] = await Promise.all([
        apiFetch(`/pricing/reconciliations${reconQs}`),
        apiFetch(`/settlements${settlementQs}`),
      ]);
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    const recordMarginHtml = `
      <div class="detail-panel">
        <h3>${HomezI18n.t("stl.record_margin_heading")}</h3>
        <div class="ls-filter-row">
          <label class="field"><span class="field-label">${HomezI18n.t("stl.order_id_label")}</span><input type="number" min="1" id="stl-record-order-id"></label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-primary btn-sm" id="stl-record-margin-btn">${HomezI18n.t("stl.record_margin_submit")}</button></div>
        </div>
      </div>
    `;

    const reconFilterHtml = `
      <div class="detail-panel">
        <h2>${HomezI18n.t("stl.reconciliation_heading")}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("stl.filter.status_label")}</span>
            ${statusFilterSelectHtml("stl-recon-filter-status", RECONCILIATION_STATUS_OPTIONS, stlState.reconStatusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="stl-recon-refresh-btn">${HomezI18n.t("stl.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const reconListHtml = (!recons || recons.length === 0)
      ? simpleEmptyPanel(HomezI18n.t("stl.empty"), HomezI18n.t("stl.empty_sub"))
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("stl.col_order_id")}</th><th>${HomezI18n.t("stl.col_expected_net")}</th>
            <th>${HomezI18n.t("stl.col_actual_net")}</th><th>${HomezI18n.t("stl.col_variance")}</th>
            <th>${HomezI18n.t("stl.col_recon_status")}</th><th>${HomezI18n.t("stl.col_action")}</th>
          </tr></thead>
          <tbody>${recons.map((r) => `
            <tr>
              <td data-label="${HomezI18n.t("stl.col_order_id")}">#${r.order_id}</td>
              <td data-label="${HomezI18n.t("stl.col_expected_net")}">${fmtMoney(r.expected_net_amount)}</td>
              <td data-label="${HomezI18n.t("stl.col_actual_net")}">${r.actual_net_amount !== null && r.actual_net_amount !== undefined ? fmtMoney(r.actual_net_amount) : "—"}</td>
              <td data-label="${HomezI18n.t("stl.col_variance")}">${r.variance_amount !== null && r.variance_amount !== undefined ? `<span class="${r.variance_amount === 0 ? "risk-low" : "risk-high"}">${fmtMoney(r.variance_amount)}</span>` : "—"}</td>
              <td data-label="${HomezI18n.t("stl.col_recon_status")}">${statusPillHtmlLabeled(r.status, "stl.status.")}</td>
              <td data-label="${HomezI18n.t("stl.col_action")}">
                ${r.status === "HELD"
                  ? `<button type="button" class="btn btn-ghost btn-sm" id="stl-recon-release-${r.order_id}">${HomezI18n.t("stl.release_hold_btn")}</button>`
                  : `<button type="button" class="btn btn-ghost btn-sm" id="stl-recon-hold-${r.order_id}">${HomezI18n.t("stl.hold_btn")}</button>`}
              </td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    const settlementFilterHtml = `
      <div class="detail-panel">
        <h2>${HomezI18n.t("stl.settlement_heading")}</h2>
        <div class="ls-filter-row">
          <label class="field">
            <span class="field-label">${HomezI18n.t("stl.filter.status_label")}</span>
            ${statusFilterSelectHtml("stl-settlement-filter-status", SETTLEMENT_STATUS_OPTIONS, stlState.settlementStatusFilter)}
          </label>
          <div class="ls-filter-actions"><button type="button" class="btn btn-ghost btn-sm" id="stl-settlement-refresh-btn">${HomezI18n.t("stl.refresh_btn")}</button></div>
        </div>
      </div>
    `;

    const settlementListHtml = (!settlements || settlements.length === 0)
      ? simpleEmptyPanel(HomezI18n.t("stl.settlement_empty"), "")
      : `
        <div class="detail-panel"><div class="table-wrap"><table class="responsive-cards">
          <thead><tr>
            <th>${HomezI18n.t("stl.col_settlement_id")}</th><th>${HomezI18n.t("stl.col_market")}</th>
            <th>${HomezI18n.t("stl.col_settlement_status")}</th><th>${HomezI18n.t("stl.col_net_amount")}</th>
            <th>${HomezI18n.t("stl.col_settlement_action")}</th>
          </tr></thead>
          <tbody>${settlements.map((s) => `
            <tr>
              <td data-label="${HomezI18n.t("stl.col_settlement_id")}">#${s.id}</td>
              <td data-label="${HomezI18n.t("stl.col_market")}">${escapeHtml(s.market)}</td>
              <td data-label="${HomezI18n.t("stl.col_settlement_status")}">${statusPillHtmlLabeled(s.status, "stl.status.")}</td>
              <td data-label="${HomezI18n.t("stl.col_net_amount")}">${fmtMoney(s.net_amount)}</td>
              <td data-label="${HomezI18n.t("stl.col_settlement_action")}">${stlSettlementActionsHtml(s)}</td>
            </tr>
          `).join("")}</tbody>
        </table></div></div>
      `;

    container.innerHTML = `${recordMarginHtml}${reconFilterHtml}${reconListHtml}${settlementFilterHtml}${settlementListHtml}`;

    el("stl-record-margin-btn").addEventListener("click", async () => {
      const orderId = Number(el("stl-record-order-id").value);
      if (!orderId) { toast(HomezI18n.t("inv.quantity_required_error"), "error"); return; }
      try {
        await apiFetch(`/pricing/orders/${orderId}/record-actual-margin`, { method: "POST" });
        toast(HomezI18n.t("stl.record_margin_success"), "success");
        await loadChannelSettlement();
      } catch (err) {
        toast(err.message || HomezI18n.t("stl.load_error"), "error");
      }
    });

    el("stl-recon-filter-status").addEventListener("change", (ev) => {
      stlState.reconStatusFilter = ev.target.value;
      loadChannelSettlement();
    });
    el("stl-recon-refresh-btn").addEventListener("click", () => loadChannelSettlement());
    el("stl-settlement-filter-status").addEventListener("change", (ev) => {
      stlState.settlementStatusFilter = ev.target.value;
      loadChannelSettlement();
    });
    el("stl-settlement-refresh-btn").addEventListener("click", () => loadChannelSettlement());

    (recons || []).forEach((r) => {
      const holdBtn = el(`stl-recon-hold-${r.order_id}`);
      if (holdBtn) {
        holdBtn.addEventListener("click", async () => {
          const { confirmed, value } = await confirmDialog({
            title: HomezI18n.t("stl.hold_confirm_title"),
            body: HomezI18n.t("stl.hold_confirm_body"),
            requireReason: true,
          });
          if (!confirmed) return;
          try {
            await apiFetch(`/pricing/orders/${r.order_id}/reconciliation/hold`, {
              method: "POST",
              body: JSON.stringify({ notes: value }),
            });
            toast(HomezI18n.t("stl.hold_success"), "success");
            await loadChannelSettlement();
          } catch (err) {
            toast(err.message || HomezI18n.t("stl.load_error"), "error");
          }
        });
      }
      const releaseBtn = el(`stl-recon-release-${r.order_id}`);
      if (releaseBtn) {
        releaseBtn.addEventListener("click", async () => {
          try {
            await apiFetch(`/pricing/orders/${r.order_id}/reconciliation/release-hold`, { method: "POST" });
            toast(HomezI18n.t("stl.release_hold_success"), "success");
            await loadChannelSettlement();
          } catch (err) {
            toast(err.message || HomezI18n.t("stl.load_error"), "error");
          }
        });
      }
    });

    (settlements || []).forEach((s) => stlBindSettlementActions(s));
  }

  function stlSettlementActionsHtml(s) {
    if (s.status === "PENDING") {
      return `
        <button type="button" class="btn btn-ghost btn-sm" id="stl-confirm-deposit-${s.id}">${HomezI18n.t("stl.confirm_deposit_btn")}</button>
        <button type="button" class="btn btn-ghost btn-sm" id="stl-hold-${s.id}">${HomezI18n.t("stl.settlement_hold_btn")}</button>
        <button type="button" class="btn btn-ghost btn-sm" id="stl-cancel-${s.id}">${HomezI18n.t("stl.cancel_btn")}</button>
      `;
    }
    if (s.status === "HELD") {
      return `
        <button type="button" class="btn btn-ghost btn-sm" id="stl-release-hold-${s.id}">${HomezI18n.t("stl.settlement_release_hold_btn")}</button>
        <button type="button" class="btn btn-ghost btn-sm" id="stl-flag-mismatch-${s.id}">${HomezI18n.t("stl.flag_mismatch_btn")}</button>
      `;
    }
    if (s.status === "MISMATCH") {
      return `<button type="button" class="btn btn-ghost btn-sm" id="stl-resolve-mismatch-${s.id}">${HomezI18n.t("stl.resolve_mismatch_btn")}</button>`;
    }
    if (s.status === "DEPOSITED") {
      return `<button type="button" class="btn btn-ghost btn-sm" id="stl-reverse-${s.id}">${HomezI18n.t("stl.reverse_btn")}</button>`;
    }
    return "—";
  }

  function stlBindSettlementActions(s) {
    const simple = (btnId, path, successKey) => {
      const btn = el(btnId);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        try {
          await apiFetch(`/settlements/${s.id}/${path}`, { method: "POST" });
          toast(HomezI18n.t(successKey), "success");
          await loadChannelSettlement();
        } catch (err) {
          toast(err.message || HomezI18n.t("stl.load_error"), "error");
        }
      });
    };
    const withReason = (btnId, path, titleKey, bodyKey, successKey) => {
      const btn = el(btnId);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        const { confirmed, value } = await confirmDialog({
          title: HomezI18n.t(titleKey),
          body: HomezI18n.t(bodyKey),
          requireReason: true,
        });
        if (!confirmed) return;
        try {
          await apiFetch(`/settlements/${s.id}/${path}`, {
            method: "POST",
            body: JSON.stringify({ memo: value }),
          });
          toast(HomezI18n.t(successKey), "success");
          await loadChannelSettlement();
        } catch (err) {
          toast(err.message || HomezI18n.t("stl.load_error"), "error");
        }
      });
    };

    simple(`stl-confirm-deposit-${s.id}`, "confirm-deposit", "stl.confirm_deposit_success");
    simple(`stl-hold-${s.id}`, "hold", "stl.settlement_hold_success");
    simple(`stl-cancel-${s.id}`, "cancel", "stl.cancel_success");
    simple(`stl-release-hold-${s.id}`, "release-hold", "stl.settlement_release_hold_success");
    simple(`stl-reverse-${s.id}`, "reverse", "stl.reverse_success");
    withReason(`stl-flag-mismatch-${s.id}`, "flag-mismatch", "stl.flag_mismatch_confirm_title", "stl.flag_mismatch_confirm_body", "stl.flag_mismatch_success");
    withReason(`stl-resolve-mismatch-${s.id}`, "resolve-mismatch", "stl.resolve_mismatch_confirm_title", "stl.resolve_mismatch_confirm_body", "stl.resolve_mismatch_success");
  }

  VIEW_LOADERS["channel-settlement"] = loadChannelSettlement;

  // --------------------------------------------------
  // AI 자동 등록 파이프라인(Candidate Pipeline, Gate 6) —
  // 상품 후보 상세 화면(view-candidate-detail)에서 호출된다.
  // --------------------------------------------------

  async function runCandidatePipeline(candidateId) {
    const { confirmed } = await confirmDialog({
      title: HomezI18n.t("cpl.run_confirm_title"),
      body: HomezI18n.t("cpl.run_confirm_body"),
    });
    if (!confirmed) return;

    const resultEl = el("cpl-result");
    const btn = el("cpl-run-btn");
    if (btn) btn.disabled = true;
    if (resultEl) resultEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    try {
      const result = await apiFetch("/candidate-pipeline/run", {
        method: "POST",
        body: JSON.stringify({
          candidate_id: candidateId,
          wizard_creation_idempotency_key: genIdemKey(`cpl-wizard-${candidateId}`),
          image_job_idempotency_key: genIdemKey(`cpl-image-${candidateId}`),
          image_purposes: ["MAIN"],
          image_provider_code: "FAKE",
          content_provider_code: "FAKE",
        }),
      });
      toast(HomezI18n.t("cpl.run_success"), "success");
      cplRenderResult(result);
    } catch (err) {
      toast(err.message || "", "error");
      if (resultEl) resultEl.innerHTML = "";
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function cplRenderResult(result) {
    const resultEl = el("cpl-result");
    if (!resultEl) return;
    resultEl.innerHTML = `
      <dl class="kv-list">
        <dt>${HomezI18n.t("cpl.result_mode_label")}</dt><dd>${escapeHtml(result.automation_mode)}</dd>
        <dt>${HomezI18n.t("cpl.result_applied_label")}</dt><dd>${result.auto_applied ? HomezI18n.t("common.yes") : HomezI18n.t("common.no")}</dd>
        <dt>${HomezI18n.t("cpl.result_content_source_label")}</dt><dd>${escapeHtml(result.content_source)}</dd>
        <dt>${HomezI18n.t("cpl.result_policy_passed_label")}</dt><dd>${result.content_policy_passed ? HomezI18n.t("common.yes") : HomezI18n.t("common.no")}</dd>
        <dt>${HomezI18n.t("cpl.result_blocking_label")}</dt><dd>${(result.content_policy_blocking_flags || []).length ? result.content_policy_blocking_flags.map(escapeHtml).join(", ") : "—"}</dd>
        <dt>${HomezI18n.t("cpl.result_warning_label")}</dt><dd>${(result.content_policy_warning_flags || []).length ? result.content_policy_warning_flags.map(escapeHtml).join(", ") : "—"}</dd>
        <dt>${HomezI18n.t("cpl.result_wizard_label")}</dt><dd>${result.wizard_id ? `#${result.wizard_id} (${escapeHtml(result.wizard_status || "")})` : HomezI18n.t("cpl.result_none")}</dd>
        <dt>${HomezI18n.t("cpl.result_image_job_label")}</dt><dd>${result.image_job_id ? `#${result.image_job_id} (${escapeHtml(result.image_job_status || "")})` : HomezI18n.t("cpl.result_none")}</dd>
        <dt>${HomezI18n.t("cpl.result_suggested_description_label")}</dt><dd>${escapeHtml(result.suggested_description || "—")}</dd>
        <dt>${HomezI18n.t("cpl.result_suggested_keywords_label")}</dt><dd>${(result.suggested_keywords || []).map(escapeHtml).join(", ") || "—"}</dd>
      </dl>
    `;
  }

  // --------------------------------------------------
  // 시스템 상태
  // --------------------------------------------------

  async function loadSystem() {
    const container = el("system-content");
    container.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let data;
    try {
      data = await apiFetch("/console/api/system-status");
    } catch (err) {
      renderErrorState(container, err);
      return;
    }

    const tableRow = (name, ok) => `
      <tr><td>${escapeHtml(name)}</td><td>${ok ? `<span class="status-tag status-APPROVED">${HomezI18n.t("system.table_present")}</span>` : `<span class="status-tag status-REJECTED">${HomezI18n.t("system.table_absent")}</span>`}</td></tr>
    `;

    container.innerHTML = `
      <div class="stat-grid">
        <div class="stat-card accent-teal">
          <div class="stat-label">${HomezI18n.t("system.api_connection")}</div>
          <div class="stat-value">${HomezI18n.t("system.normal")}</div>
        </div>
        <div class="stat-card accent-teal">
          <div class="stat-label">${HomezI18n.t("system.app_version_label")}</div>
          <div class="stat-value">${escapeHtml(data.app_version || "—")}</div>
        </div>
        <div class="stat-card ${data.db_integrity === "ok" ? "accent-green" : "accent-pink"}">
          <div class="stat-label">${HomezI18n.t("system.db_integrity")}</div>
          <div class="stat-value">${escapeHtml(data.db_integrity)}</div>
        </div>
        <div class="stat-card ${data.v23_schema_applied ? "accent-green" : "accent-yellow"}">
          <div class="stat-label">${HomezI18n.t("system.v23_tables_label")}</div>
          <div class="stat-value">${HomezI18n.t(data.v23_schema_applied ? "system.applied" : "system.apply_required")}</div>
        </div>
        <div class="stat-card ${data.v24_v3_schema_applied ? "accent-green" : "accent-yellow"}">
          <div class="stat-label">${HomezI18n.t("system.v24_tables_label")}</div>
          <div class="stat-value">${HomezI18n.t(data.v24_v3_schema_applied ? "system.applied" : "system.apply_required")}</div>
        </div>
      </div>

      ${!data.v24_v3_schema_applied ? `
        <div class="schema-warning">
          ${HomezI18n.t("system.schema_warning")}
        </div>
      ` : ""}

      <div class="detail-panel">
        <h2>${HomezI18n.t("system.v23_tables_title")}</h2>
        <table class="schema-table"><tbody>
          ${Object.entries(data.v23_tables).map(([name, ok]) => tableRow(name, ok)).join("")}
        </tbody></table>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("system.v24_tables_title")}</h2>
        <table class="schema-table"><tbody>
          ${Object.entries(data.v24_v3_tables).map(([name, ok]) => tableRow(name, ok)).join("")}
        </tbody></table>
      </div>

      <div class="detail-panel">
        <h2>${HomezI18n.t("system.recorded_tests_title")}</h2>
        ${data.last_recorded_test_evidence
          ? `<div class="evidence-pre">${escapeHtml(data.last_recorded_test_evidence)}</div>`
          : `<p class="stat-sub">${HomezI18n.t("system.recorded_tests_missing")}</p>`}
      </div>

      <div class="detail-panel" id="db-identity-panel">
        <h2>${HomezI18n.t("system.db_identity_title")}</h2>
        <p class="stat-sub">${HomezI18n.t("system.db_identity_not_loaded_hint")}</p>
        <button type="button" class="btn btn-secondary btn-sm" id="db-identity-refresh-btn">${HomezI18n.t("system.db_identity_refresh_btn")}</button>
      </div>
    `;

    const refreshBtn = el("db-identity-refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => withButtonGuard(refreshBtn, loadDbIdentityPanel));
    }
  }

  // 2026-08-30 후속 지시(DB Identity 운영 안전 감사) — SHA-256은
  // 파일 전체를 읽어야 계산되므로, "시스템 상태" 화면을 열 때마다
  // 자동으로 실행하면 DB가 커질수록 이 화면 자체가 느려진다. 그래서
  // loadSystem()이 이 함수를 더 이상 자동 호출하지 않는다 — 사용자가
  // "진단 새로고침" 버튼을 명시적으로 눌렀을 때만 계산한다.
  // /diagnostics/db-identity를 그대로 보여준다(값을 재해석·재계산
  // 하지 않는다 — 서버가 계산한 값을 그대로 표시만 한다). admin이
  // 아닌 사용자가 어떤 경로로든 이 화면에 도달해도 서버가 403을
  // 돌려주므로 그대로 실패 안내만 보여주고 그 이상 진행하지 않는다.
  async function loadDbIdentityPanel() {
    const panel = el("db-identity-panel");
    if (!panel) return;

    panel.innerHTML = `
      <h2>${HomezI18n.t("system.db_identity_title")}</h2>
      <p class="loading-text">${HomezI18n.t("common.loading")}</p>
    `;

    let identity;
    try {
      identity = await apiFetch("/diagnostics/db-identity");
    } catch (err) {
      panel.innerHTML = `
        <h2>${HomezI18n.t("system.db_identity_title")}</h2>
        <p class="field-error">${escapeHtml(err.message || HomezI18n.t("system.db_identity_load_failed"))}</p>
        <button type="button" class="btn btn-secondary btn-sm" id="db-identity-refresh-btn">${HomezI18n.t("system.db_identity_refresh_btn")}</button>
      `;
      const retryBtn = el("db-identity-refresh-btn");
      if (retryBtn) retryBtn.addEventListener("click", () => withButtonGuard(retryBtn, loadDbIdentityPanel));
      return;
    }

    const env = identity.homez_data_root_env || {};
    const win = identity.windows_file_identity || {};

    const identityValueRows = identity.exists ? `
      <tr><td colspan="2"><strong>${HomezI18n.t("system.db_identity_value_section_label")}</strong></td></tr>
      ${!identity.identity_stable ? `
      <tr><td colspan="2" class="field-error">${HomezI18n.t("system.db_identity_do_not_compare_warning")}</td></tr>
      ` : ""}
      ${identity.hash_skipped_reason ? `
      <tr><td>SHA-256</td><td class="stat-sub">${HomezI18n.t("system.db_identity_hash_skipped")}</td></tr>
      ` : `
      <tr><td>SHA-256${identity.identity_stable ? "" : ` (${HomezI18n.t("system.db_identity_unstable_note")})`}</td>
        <td class="mono-cell${identity.identity_stable ? "" : " db-identity-unconfirmed"}">${escapeHtml(identity.sha256 || "—")}</td></tr>
      `}
      ${win.available ? `
      <tr><td>NTFS Volume Serial</td><td class="mono-cell">${escapeHtml(String(win.volume_serial ?? "—"))}</td></tr>
      <tr><td>NTFS File Index</td><td class="mono-cell">${escapeHtml(String(win.file_index ?? "—"))}</td></tr>
      <tr><td>${HomezI18n.t("system.db_identity_hardlink_count")}</td><td class="mono-cell">${escapeHtml(String(win.hardlink_count ?? "—"))}</td></tr>
      ` : `<tr><td colspan="2" class="stat-sub">${HomezI18n.t("system.db_identity_windows_unavailable")}</td></tr>`}
    ` : `<tr><td colspan="2" class="stat-sub">${HomezI18n.t("system.db_identity_file_missing")}</td></tr>`;

    panel.innerHTML = `
      <h2>${HomezI18n.t("system.db_identity_title")}</h2>

      <div class="stat-grid">
        <div class="stat-card ${identity.redirection_suspected ? "accent-pink" : "accent-teal"}">
          <div class="stat-label">${HomezI18n.t("system.db_identity_redirection_label")}</div>
          <div class="stat-value">${identity.redirection_suspected
            ? HomezI18n.t("system.db_identity_redirection_suspected")
            : HomezI18n.t("system.db_identity_redirection_none")}</div>
        </div>
        <div class="stat-card ${identity.path_mismatch_warning ? "accent-pink" : "accent-green"}">
          <div class="stat-label">${HomezI18n.t("system.db_identity_path_match_label")}</div>
          <div class="stat-value">${identity.path_mismatch_warning
            ? HomezI18n.t("system.db_identity_path_mismatch")
            : HomezI18n.t("system.db_identity_path_match")}</div>
        </div>
        <div class="stat-card ${identity.identity_stable ? "accent-green" : "accent-yellow"}">
          <div class="stat-label">${HomezI18n.t("system.db_identity_stable_label")}</div>
          <div class="stat-value">${identity.identity_stable
            ? HomezI18n.t("system.db_identity_stable_yes")
            : HomezI18n.t("system.db_identity_stable_no")}</div>
        </div>
      </div>

      <table class="schema-table"><tbody>
        <tr><td>${HomezI18n.t("system.db_identity_path_label")}</td>
          <td class="mono-cell">${escapeHtml(identity.resolved_db_path || "—")}</td></tr>
        <tr><td>${HomezI18n.t("system.db_identity_size_label")}</td>
          <td class="mono-cell">${identity.file_size_bytes != null ? escapeHtml(String(identity.file_size_bytes)) : "—"}</td></tr>
        <tr><td>${HomezI18n.t("system.db_identity_mtime_label")}</td>
          <td class="mono-cell">${escapeHtml(identity.mtime_utc || "—")}</td></tr>
        ${identityValueRows}
        <tr><td>HOMEZ_DATA_ROOT</td>
          <td class="mono-cell">${env.is_set
            ? escapeHtml(env.masked_value || "—")
            : HomezI18n.t("system.db_identity_env_not_set")}</td></tr>
        <tr><td>${HomezI18n.t("system.db_identity_sampled_at_label")}</td>
          <td class="mono-cell">${escapeHtml(identity.sampled_at || "—")}</td></tr>
      </tbody></table>
      <button type="button" class="btn btn-secondary btn-sm" id="db-identity-refresh-btn">${HomezI18n.t("system.db_identity_refresh_btn")}</button>
    `;

    const refreshBtn2 = el("db-identity-refresh-btn");
    if (refreshBtn2) {
      refreshBtn2.addEventListener("click", () => withButtonGuard(refreshBtn2, loadDbIdentityPanel));
    }
  }

  // --------------------------------------------------
  // 가이드 (2026-08-13)
  //
  // docs/guides/의 실제 문서·PDF·자막·영상을 앱 내부에서 읽기 전용
  // 으로 보여준다. `/guides` API가 매 요청 실제 파일 존재 여부로
  // 계산한 가용 상태만 신뢰한다 — 존재하지 않는 영상을 재생 가능한
  // 것처럼 보여주지 않는다(요구사항 핵심). 영상/이미지/PDF는 모두
  // apiFetchBlob()로 Authorization 헤더를 붙여 받은 뒤 blob: URL로
  // 바꿔 <video>/<img>/새 탭에 넣는다 — 이 앱은 쿠키가 아니라
  // localStorage 토큰만으로 인증하므로, <video src>/<img src>처럼
  // 브라우저가 직접 요청하는 태그는 애초에 인증 헤더를 실어 보낼 수
  // 없다(토큰을 URL에 넣지 않는다는 보안 요구사항과도 직결). 이미
  // 이 파일의 CSV/복구코드 다운로드가 쓰는 것과 동일한
  // Blob+createObjectURL 패턴을 재사용한다.
  // --------------------------------------------------

  let guidesListCache = null;
  let guidesCurrentFilter = "all";
  let guidesCurrentSearch = "";
  let guidesCurrentDetailId = null;
  let guidesActiveObjectUrls = [];

  function guidesRevokeObjectUrls() {
    guidesActiveObjectUrls.forEach((u) => URL.revokeObjectURL(u));
    guidesActiveObjectUrls = [];
  }

  function guidesFileUrl(relativePath) {
    return "/guides/files/" + relativePath.split("/").map(encodeURIComponent).join("/");
  }

  async function apiFetchBlob(path) {
    const token = getAccessToken();
    const headers = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const resp = await fetch(path, { headers });
    if (resp.status === 401) {
      throw new ApiError(401, HomezI18n.t("common.error_auth_required"));
    }
    if (!resp.ok) {
      throw new ApiError(resp.status, HomezI18n.t("common.error_request_failed", { status: resp.status }));
    }
    return resp.blob();
  }

  async function guidesObjectUrlFor(relativePath) {
    const blob = await apiFetchBlob(guidesFileUrl(relativePath));
    const url = URL.createObjectURL(blob);
    guidesActiveObjectUrls.push(url);
    return url;
  }

  async function guidesSubtitleVttUrl(relativeSrtPath) {
    // <track>은 SRT를 직접 지원하지 않으므로 WebVTT로 변환한다.
    // 저장된 원본 .srt 파일 자체는 전혀 건드리지 않는다(요구사항) —
    // 여기서 만드는 건 메모리상의 임시 blob 하나뿐이다. SRT와 VTT의
    // 유일한 실질적 차이는 헤더 한 줄과 타임코드 구분자(콤마→마침표)
    // 뿐이라 정규식 치환으로 충분하다.
    try {
      const srtText = await apiFetch(guidesFileUrl(relativeSrtPath));
      const vttText = "WEBVTT\n\n" + String(srtText)
        .replace(/\r/g, "")
        .replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2");
      const blob = new Blob([vttText], { type: "text/vtt" });
      const url = URL.createObjectURL(blob);
      guidesActiveObjectUrls.push(url);
      return url;
    } catch (_) {
      return null;
    }
  }

  // --------------------------------------------------
  // 가이드 — 안전한 최소 Markdown 렌더러
  //
  // 범용 Markdown 파서가 아니라, 실제 가이드 문서(docs/guides/*.md)에
  // 쓰인 패턴(#/##, 표, 이미지, 굵게, 인용, 목록, 코드펜스, 링크)만
  // 처리하는 전용 렌더러다. XSS 방지 원칙: 모든 텍스트를 먼저
  // escapeHtml()로 이스케이프한 뒤에만 안전한 태그로 치환한다 — 원문에
  // <script>나 onerror= 같은 것이 있어도 치환 전에 이미 무해한 HTML
  // 엔티티가 되어 있으므로 실제 태그로 되살아날 수 없다. 이미지 src는
  // 마크다운 원문의 문자열을 그대로 쓰지 않고, 반드시 미리 인증된
  // blob: URL로 교체된 것만(imageUrlMap에 있는 것만) 허용한다 — 없으면
  // 통째로 생략한다(임의 URL 삽입 불가).
  // --------------------------------------------------

  function guidesInlineMarkdown(text, imageUrlMap) {
    let out = escapeHtml(text);
    out = out.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    out = out.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_m, alt, src) => {
      const resolved = imageUrlMap && imageUrlMap.get(src);
      if (!resolved) return "";
      return `<img src="${resolved}" alt="${escapeHtml(alt)}" loading="lazy" class="guide-doc-img">`;
    });
    out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, label, href) => {
      // http(s) 외부 링크만 실제 <a>로 만든다(target=_blank +
      // rel=noopener). 그 외(다른 가이드 .md 상대 링크 등)는 임의
      // 스킴(javascript: 등) 삽입을 원천 차단하기 위해 일반 텍스트로만
      // 남긴다 — 현재 가이드 문서에는 실제로 그런 상대 링크가 거의
      // 없고, 있어도 안내 목적의 참조일 뿐이라 텍스트로도 충분하다.
      if (/^https?:\/\//i.test(href)) {
        return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
      }
      return `<span class="guide-doc-link-ref">${label}</span>`;
    });
    return out;
  }

  function renderGuideMarkdown(mdText, imageUrlMap) {
    const lines = String(mdText).split(/\r?\n/);
    const out = [];
    let inList = false;
    let inTable = false;
    let inCodeFence = false;
    let tableRows = [];

    const flushList = () => {
      if (inList) { out.push("</ul>"); inList = false; }
    };
    const flushTable = () => {
      if (!inTable) return;
      const [head, ...rest] = tableRows;
      out.push(
        "<table><thead><tr>"
        + head.map((c) => `<th>${guidesInlineMarkdown(c.trim(), imageUrlMap)}</th>`).join("")
        + "</tr></thead><tbody>",
      );
      rest.forEach((row) => {
        if (/^-+$/.test(row[0].trim().replace(/:/g, ""))) return; // 구분선 행(|---|---|) 스킵
        out.push("<tr>" + row.map((c) => `<td>${guidesInlineMarkdown(c.trim(), imageUrlMap)}</td>`).join("") + "</tr>");
      });
      out.push("</tbody></table>");
      tableRows = [];
      inTable = false;
    };

    for (const raw of lines) {
      const line = raw.trim();

      if (line.startsWith("```")) {
        if (!inCodeFence) {
          flushList();
          flushTable();
          out.push('<pre class="guide-code-block">');
          inCodeFence = true;
        } else {
          out.push("</pre>");
          inCodeFence = false;
        }
        continue;
      }
      if (inCodeFence) {
        out.push(escapeHtml(raw) + "\n");
        continue;
      }

      if (line.startsWith("|") && line.endsWith("|") && line.length > 1) {
        tableRows.push(line.replace(/^\|/, "").replace(/\|$/, "").split("|"));
        inTable = true;
        continue;
      }
      flushTable();

      if (/^#{1,6}\s/.test(line)) {
        flushList();
        const level = Math.min(line.match(/^#+/)[0].length + 1, 6);
        const text = line.replace(/^#+\s*/, "");
        out.push(`<h${level}>${guidesInlineMarkdown(text, imageUrlMap)}</h${level}>`);
      } else if (line.startsWith("> ")) {
        flushList();
        out.push(`<blockquote>${guidesInlineMarkdown(line.slice(2), imageUrlMap)}</blockquote>`);
      } else if (line.startsWith("- ")) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push(`<li>${guidesInlineMarkdown(line.slice(2), imageUrlMap)}</li>`);
      } else if (line === "") {
        flushList();
      } else {
        flushList();
        out.push(`<p>${guidesInlineMarkdown(line, imageUrlMap)}</p>`);
      }
    }
    flushList();
    flushTable();

    return out.join("\n");
  }

  async function guidesBuildImageUrlMap(mdText) {
    const map = new Map();
    const re = /!\[[^\]]*\]\(([^)]+)\)/g;
    const seen = new Set();
    let m;
    while ((m = re.exec(mdText))) {
      seen.add(m[1]);
    }
    await Promise.all(Array.from(seen).map(async (src) => {
      try {
        map.set(src, await guidesObjectUrlFor(src));
      } catch (_) {
        /* 이미지 하나가 실패해도 나머지 문서는 계속 렌더링한다 */
      }
    }));
    return map;
  }

  // --------------------------------------------------
  // 가이드 — 목록 화면
  // --------------------------------------------------

  async function loadGuides() {
    guidesRevokeObjectUrls();
    guidesCurrentDetailId = null;
    el("guides-detail-panel").hidden = true;
    el("guides-list-panel").hidden = false;

    const grid = el("guides-card-grid");
    grid.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    const topbarVersion = el("badge-version");
    const versionBadge = el("guides-app-version-badge");
    if (topbarVersion && versionBadge) versionBadge.textContent = topbarVersion.textContent;

    try {
      const data = await apiFetch("/guides");
      guidesListCache = (data.guides || []).slice().sort((a, b) => a.order - b.order);
    } catch (err) {
      grid.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
      return;
    }

    wireGuidesToolbarOnce();
    renderGuideCards();
  }

  function wireGuidesToolbarOnce() {
    if (wireGuidesToolbarOnce._wired) return;
    wireGuidesToolbarOnce._wired = true;

    el("guides-search-input").addEventListener("input", (ev) => {
      guidesCurrentSearch = ev.target.value.trim().toLowerCase();
      renderGuideCards();
    });

    document.querySelectorAll(".guide-filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        guidesCurrentFilter = btn.dataset.filter;
        document.querySelectorAll(".guide-filter-btn").forEach((b) => {
          b.classList.toggle("active", b === btn);
          b.setAttribute("aria-pressed", String(b === btn));
        });
        renderGuideCards();
      });
    });

    el("guide-detail-back-btn").addEventListener("click", () => {
      guidesRevokeObjectUrls();
      guidesCurrentDetailId = null;
      el("guides-detail-panel").hidden = true;
      el("guides-list-panel").hidden = false;
    });
  }

  function guideCardHtml(g) {
    const locale = HomezI18n.getLocale();
    const videoAvailable = !!(g.video && g.video[locale] && g.video[locale].available);
    const docAvailable = !!(g.document && g.document[locale] && g.document[locale].available);
    const langLabelKey = locale === "en-US" ? "lang.selector.en" : "lang.selector.ko";

    return `
      <button type="button" class="guide-card" data-guide-open="${escapeHtml(g.id)}">
        <div class="guide-card-order">${escapeHtml(HomezI18n.t("guide.step_badge", { order: g.order }))}</div>
        <h3 class="guide-card-title">${escapeHtml(HomezI18n.t(g.title_key))}</h3>
        <p class="guide-card-desc">${escapeHtml(HomezI18n.t(g.description_key))}</p>
        <div class="guide-card-meta">
          <span class="guide-card-minutes">${escapeHtml(HomezI18n.t("guide.minutes_format", { range: g.estimated_minutes }))}</span>
          <span class="guide-card-badge ${videoAvailable ? "ok" : "pending"}">${escapeHtml(HomezI18n.t(videoAvailable ? "guide.watch_video" : "guide.video_pending"))}</span>
        </div>
        <div class="guide-card-lang-status ${docAvailable ? "ok" : "pending"}">
          ${escapeHtml(HomezI18n.t(docAvailable ? "guide.language_available" : "guide.language_pending", { lang: HomezI18n.t(langLabelKey) }))}
        </div>
      </button>`;
  }

  function renderGuideCards() {
    const grid = el("guides-card-grid");
    if (!guidesListCache) return;

    const filtered = guidesListCache.filter((g) => {
      if (guidesCurrentFilter !== "all" && g.category !== guidesCurrentFilter) return false;
      if (!guidesCurrentSearch) return true;
      const title = HomezI18n.t(g.title_key).toLowerCase();
      const desc = HomezI18n.t(g.description_key).toLowerCase();
      return title.includes(guidesCurrentSearch) || desc.includes(guidesCurrentSearch);
    });

    if (filtered.length === 0) {
      grid.innerHTML = `
        <div class="guide-empty-state">
          <p>${HomezI18n.t("guide.no_results")}</p>
          <p class="stat-sub">${HomezI18n.t("guide.no_results_hint")}</p>
        </div>`;
      return;
    }

    grid.innerHTML = filtered.map(guideCardHtml).join("");
    grid.querySelectorAll("[data-guide-open]").forEach((card) => {
      card.addEventListener("click", () => openGuideDetail(card.dataset.guideOpen));
    });
  }

  // --------------------------------------------------
  // 가이드 — 상세 화면
  // --------------------------------------------------

  async function guidesRenderVideoArea(guide) {
    const locale = HomezI18n.getLocale();
    const videoStatus = guide.video && guide.video[locale];

    if (!videoStatus || !videoStatus.available) {
      return `
        <div class="detail-panel guide-video-wrap guide-video-pending">
          <p>${HomezI18n.t("guide.video_unavailable")}</p>
        </div>`;
    }

    try {
      const videoUrl = await guidesObjectUrlFor(videoStatus.path);

      let tracksHtml = "";
      const koSub = guide.subtitles && guide.subtitles["ko-KR"];
      const enSub = guide.subtitles && guide.subtitles["en-US"];
      if (koSub && koSub.available) {
        const vttUrl = await guidesSubtitleVttUrl(koSub.path);
        if (vttUrl) tracksHtml += `<track kind="subtitles" srclang="ko" label="${escapeHtml(HomezI18n.t("guide.subtitle_ko"))}" src="${vttUrl}">`;
      }
      if (enSub && enSub.available) {
        const vttUrl = await guidesSubtitleVttUrl(enSub.path);
        if (vttUrl) tracksHtml += `<track kind="subtitles" srclang="en" label="${escapeHtml(HomezI18n.t("guide.subtitle_en"))}" src="${vttUrl}">`;
      }

      // <track>에 default를 붙이지 않는다 — 시작 시 자막이 꺼진
      // 상태이며, 브라우저 기본 컨트롤의 자막 메뉴에서 켜거나(한국어/
      // 영어 중 선택) "끄기"를 선택할 수 있다(요구사항: 자막을 끌 수
      // 있어야 한다).
      return `
        <div class="detail-panel guide-video-wrap">
          <video controls preload="metadata">
            <source src="${videoUrl}" type="video/mp4">
            ${tracksHtml}
          </video>
        </div>`;
    } catch (_) {
      return `
        <div class="detail-panel guide-video-wrap guide-video-pending">
          <p>${HomezI18n.t("guide.video_unavailable")}</p>
        </div>`;
    }
  }

  async function guidesRenderDocument(guide) {
    const locale = HomezI18n.getLocale();
    const docStatus = guide.document && guide.document[locale];
    if (!docStatus || !docStatus.available) {
      return `<p class="stat-sub">${HomezI18n.t("guide.document_pending")}</p>`;
    }
    try {
      const mdText = await apiFetch(guidesFileUrl(docStatus.path));
      const imageMap = await guidesBuildImageUrlMap(String(mdText));
      return `<div class="guide-doc-viewer">${renderGuideMarkdown(mdText, imageMap)}</div>`;
    } catch (err) {
      return `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
    }
  }

  async function guidesRenderScreenshots(guide) {
    if (!guide.screenshots || guide.screenshots.length === 0) return "";

    const imgs = await Promise.all(guide.screenshots.map(async (path) => {
      try {
        const url = await guidesObjectUrlFor(path);
        return `<img src="${url}" alt="" loading="lazy" class="guide-screenshot-thumb">`;
      } catch (_) {
        return "";
      }
    }));
    const valid = imgs.filter(Boolean);
    if (valid.length === 0) return "";

    return `
      <div class="detail-panel">
        <h2>${HomezI18n.t("guide.screenshots_title")}</h2>
        <div class="guide-screenshot-grid">${valid.join("")}</div>
      </div>`;
  }

  function guidesRenderFaq(guide) {
    const bodyKey = guide.id === "troubleshooting"
      ? "guide.troubleshooting_description"
      : "guide.faq_see_troubleshooting";
    return `
      <div class="detail-panel">
        <h2>${HomezI18n.t("guide.faq_title")}</h2>
        <p class="stat-sub">${HomezI18n.t(bodyKey)}</p>
      </div>`;
  }

  function guidesRenderPrevNext(guide) {
    const idx = guidesListCache.findIndex((g) => g.id === guide.id);
    const prev = idx > 0 ? guidesListCache[idx - 1] : null;
    const next = idx >= 0 && idx < guidesListCache.length - 1 ? guidesListCache[idx + 1] : null;

    return `
      <div class="guide-prev-next">
        ${prev
          ? `<button type="button" class="btn btn-secondary" data-guide-open="${escapeHtml(prev.id)}">← ${escapeHtml(HomezI18n.t("guide.previous"))}: ${escapeHtml(HomezI18n.t(prev.title_key))}</button>`
          : "<span></span>"}
        ${next
          ? `<button type="button" class="btn btn-primary" data-guide-open="${escapeHtml(next.id)}">${escapeHtml(HomezI18n.t("guide.next"))}: ${escapeHtml(HomezI18n.t(next.title_key))} →</button>`
          : "<span></span>"}
      </div>`;
  }

  async function guidesOpenPdf(relativePath, btn) {
    const original = btn.textContent;
    btn.disabled = true;
    try {
      const blob = await apiFetchBlob(guidesFileUrl(relativePath));
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
      // 새 탭이 blob을 다 읽은 뒤에 회수하도록 넉넉히 지연한다(너무
      // 빨리 revoke하면 새 탭이 빈 화면으로 뜰 수 있다).
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      toast(err.message || HomezI18n.t("common.load_error"), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  async function renderGuideDetail(guide) {
    guidesCurrentDetailId = guide.id;
    guidesRevokeObjectUrls();

    const content = el("guide-detail-content");
    content.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    el("guides-detail-panel").scrollTop = 0;

    const locale = HomezI18n.getLocale();
    const pdfStatus = guide.pdf && guide.pdf[locale];

    const [videoHtml, docHtml, screenshotsHtml] = await Promise.all([
      guidesRenderVideoArea(guide),
      guidesRenderDocument(guide),
      guidesRenderScreenshots(guide),
    ]);
    const faqHtml = guidesRenderFaq(guide);
    const prevNextHtml = guidesRenderPrevNext(guide);
    const pdfAvailable = !!(pdfStatus && pdfStatus.available);

    content.innerHTML = `
      <div class="detail-panel guide-detail-header">
        <span class="guide-card-order">${escapeHtml(HomezI18n.t("guide.step_badge", { order: guide.order }))}</span>
        <h2>${escapeHtml(HomezI18n.t(guide.title_key))}</h2>
        <p class="stat-sub">${escapeHtml(HomezI18n.t(guide.description_key))}</p>
        <div class="guide-detail-actions">
          <button type="button" class="btn btn-secondary" id="guide-open-pdf-btn" ${pdfAvailable ? "" : "disabled"}>
            ${escapeHtml(HomezI18n.t(pdfAvailable ? "guide.open_pdf" : "guide.pdf_pending"))}
          </button>
        </div>
      </div>

      ${videoHtml}

      <div class="detail-panel">
        <h2>${HomezI18n.t("guide.related_documents_title")}</h2>
        ${docHtml}
      </div>

      ${screenshotsHtml}
      ${faqHtml}
      ${prevNextHtml}
    `;

    if (pdfAvailable) {
      el("guide-open-pdf-btn").addEventListener("click", (ev) => guidesOpenPdf(pdfStatus.path, ev.currentTarget));
    }
    content.querySelectorAll("[data-guide-open]").forEach((btn) => {
      btn.addEventListener("click", () => openGuideDetail(btn.dataset.guideOpen));
    });
  }

  async function openGuideDetail(guideId) {
    guidesCurrentDetailId = guideId;
    el("guides-list-panel").hidden = true;
    el("guides-detail-panel").hidden = false;

    const content = el("guide-detail-content");
    content.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let guide = guidesListCache && guidesListCache.find((g) => g.id === guideId);
    if (!guide) {
      try {
        guide = await apiFetch(`/guides/${encodeURIComponent(guideId)}`);
      } catch (err) {
        content.innerHTML = `<p class="field-error">${escapeHtml(err.message || HomezI18n.t("common.load_error"))}</p>`;
        return;
      }
    }

    await renderGuideDetail(guide);
  }

  // --------------------------------------------------
  // 인터랙티브 가이드 모드(Guided Tour, 2026-08-13)
  //
  // 설계 원칙(사용자 지시 그대로):
  //   1) 이 엔진은 어떤 요소도 대신 클릭하지 않는다 — "click" 단계는
  //      실제 사용자가 대상 요소에서 발생시킨 진짜 click 이벤트만
  //      리스닝한다(합성 이벤트 dispatch 없음). 그래서 로그아웃·
  //      비밀번호 변경·삭제·외부 전송 같은 위험 동작을 이 엔진이
  //      "실수로" 실행하는 경로 자체가 구조적으로 없다.
  //   2) 상품 등록 연습(Pilot B)은 실제 "AI 상품 등록"/"상품등록
  //      통합 마법사" 화면의 진짜 입력 필드를 쓰지 않는다 — 그
  //      화면들은 첫 클릭(초안 생성/마법사 시작)부터 실제 백엔드에
  //      쓰기 때문이다(사전 검토에서 확인). 대신 이 모듈이 그리는
  //      완전히 별도의 연습 전용 패널(#tour-practice-panel, 실제
  //      DOM이지만 API 호출이 전혀 연결되지 않음)에서 입력을
  //      연습한다 — "실제 상품 DB에 저장하지 않음"을 코드 구조로
  //      보장한다.
  //   3) 진행 상태는 로그인 토큰이 아니라 사용자 id로만 네임스페이싱
  //      된 비민감 정보(어떤 과정을, 몇 단계까지, 건너뛴 단계가
  //      무엇인지)만 localStorage에 저장한다.
  // --------------------------------------------------

  const TOUR_PROGRESS_KEY = "homez_tour_progress_v1";

  // section 16 순서 그대로 — 1차 구현은 screens-and-menus/product-
  // registration-practice 2개뿐이다. 나머지 4개는 comingSoon:true로
  // 화면에는 노출하되 "구현 완료"로 절대 표시하지 않는다.
  const HOMEZ_TOURS = [
    {
      id: "quick-start",
      order: 1,
      titleKey: "tour.course.quick_start.title",
      descriptionKey: "tour.course.quick_start.description",
      version: "1.0",
      // "화면과 메뉴 둘러보기"와 마찬가지로 대시보드/상품 관리 메뉴를
      // 대상으로 삼아 관리자 전용이다(요구사항 8 우선순위 1번 — 짧은
      // 압축판, 대상은 재사용해도 무방하다: 서로 다른 과정이 같은
      // data-guide-id를 target으로 삼는 것은 "한 화면에 같은 id가
      // 중복 부여됨"과는 다른 문제다).
      requiredPermission: "admin",
      dataChange: "view_only",
      steps: [
        {
          id: "qs-welcome", action: "describe", route: null,
          titleKey: "tour.step.qs_welcome.title", descriptionKey: "tour.step.qs_welcome.description",
        },
        {
          id: "qs-open-dashboard", action: "click", target: "nav-dashboard", route: "overview", placement: "right",
          titleKey: "tour.step.qs_open_dashboard.title", descriptionKey: "tour.step.qs_open_dashboard.description",
          actionHintKey: "tour.step.qs_open_dashboard.action",
        },
        {
          id: "qs-open-products", action: "click", target: "nav-products", route: "candidates", placement: "right",
          titleKey: "tour.step.qs_open_products.title", descriptionKey: "tour.step.qs_open_products.description",
          actionHintKey: "tour.step.qs_open_products.action",
        },
        {
          id: "qs-complete", action: "describe", target: "products-area", route: "candidates", placement: "bottom", isComplete: true,
          titleKey: "tour.step.qs_complete.title", descriptionKey: "tour.step.qs_complete.description",
        },
      ],
    },
    {
      id: "screens-and-menus",
      order: 2,
      titleKey: "tour.course.screens_and_menus.title",
      descriptionKey: "tour.course.screens_and_menus.description",
      version: "1.0",
      // 2026-08-13 결함 수정: 이 과정이 강조하는 모든 대상
      // (nav-dashboard/nav-products/nav-ai-listing/nav-settings와
      // 그 하위 뷰인 dashboard-kpi/products-area/settings-area)이
      // 실제로는 전부 console.html의 data-permission="__admin_only__"
      // 뒤에 숨겨져 있다(applyPermissionGatedNav()가 비관리자에게
      // btn.hidden=true 처리) — requiredPermission: null로 두면
      // 비관리자가 시작하자마자 첫 클릭 단계부터 대상을 영원히 찾지
      // 못한다. 이 과정은 겹치는 대상이 100%라 부분 건너뛰기를 적용해도
      // 결국 모든 단계가 비어 남는다 — 그래서 사용자가 제시한 대안(과정
      // 자체를 실제 권한 요구사항과 일치시키는 것)을 택했다. 비관리자를
      // 위한 화면 둘러보기 과정은 비관리자에게도 보이는 대상(가이드
      // 메뉴 등)만으로 별도로 새로 설계해야 하며, 이번 범위에는 없다.
      requiredPermission: "admin",
      dataChange: "view_only",
      steps: [
        {
          id: "sm-welcome", action: "describe", route: null,
          titleKey: "tour.step.sm_welcome.title", descriptionKey: "tour.step.sm_welcome.description",
        },
        {
          id: "sm-open-dashboard", action: "click", target: "nav-dashboard", route: "overview", placement: "right",
          titleKey: "tour.step.sm_open_dashboard.title", descriptionKey: "tour.step.sm_open_dashboard.description",
          actionHintKey: "tour.step.sm_open_dashboard.action",
        },
        {
          id: "sm-dashboard-overview", action: "describe", target: "dashboard-kpi", route: "overview", placement: "bottom",
          titleKey: "tour.step.sm_dashboard_overview.title", descriptionKey: "tour.step.sm_dashboard_overview.description",
        },
        {
          id: "sm-open-products", action: "click", target: "nav-products", route: "candidates", placement: "right",
          titleKey: "tour.step.sm_open_products.title", descriptionKey: "tour.step.sm_open_products.description",
          actionHintKey: "tour.step.sm_open_products.action",
        },
        {
          id: "sm-products-overview", action: "describe", target: "products-area", route: "candidates", placement: "bottom",
          titleKey: "tour.step.sm_products_overview.title", descriptionKey: "tour.step.sm_products_overview.description",
        },
        {
          id: "sm-status-area", action: "describe", target: "topbar-status", route: "candidates", placement: "bottom",
          titleKey: "tour.step.sm_status_area.title", descriptionKey: "tour.step.sm_status_area.description",
        },
        {
          id: "sm-open-settings", action: "click", target: "nav-settings", route: "account-security", placement: "right",
          titleKey: "tour.step.sm_open_settings.title", descriptionKey: "tour.step.sm_open_settings.description",
          actionHintKey: "tour.step.sm_open_settings.action",
        },
        {
          id: "sm-settings-overview", action: "describe", target: "settings-area", route: "account-security", placement: "bottom", isComplete: true,
          titleKey: "tour.step.sm_settings_overview.title", descriptionKey: "tour.step.sm_settings_overview.description",
        },
      ],
    },
    {
      id: "admin-setup",
      order: 3,
      titleKey: "tour.course.admin_setup.title",
      descriptionKey: "tour.course.admin_setup.description",
      version: "1.0",
      requiredPermission: "admin",
      dataChange: "view_only",
      comingSoon: true,
      steps: [],
    },
    {
      id: "product-registration-practice",
      order: 4,
      titleKey: "tour.course.product_registration.title",
      descriptionKey: "tour.course.product_registration.description",
      version: "1.0",
      requiredPermission: "admin",
      dataChange: "practice",
      steps: [
        {
          id: "pr-welcome", action: "describe", route: null,
          titleKey: "tour.step.pr_welcome.title", descriptionKey: "tour.step.pr_welcome.description",
        },
        {
          id: "pr-open-products", action: "click", target: "nav-products", route: "candidates", placement: "right",
          titleKey: "tour.step.pr_open_products.title", descriptionKey: "tour.step.pr_open_products.description",
          actionHintKey: "tour.step.pr_open_products.action",
        },
        {
          id: "pr-open-ai-listing", action: "click", target: "nav-ai-listing", route: "listing-package", placement: "right",
          titleKey: "tour.step.pr_open_ai_listing.title", descriptionKey: "tour.step.pr_open_ai_listing.description",
          actionHintKey: "tour.step.pr_open_ai_listing.action",
        },
        {
          id: "pr-practice-intro", action: "describe", route: "listing-package", practicePanel: true,
          titleKey: "tour.step.pr_practice_intro.title", descriptionKey: "tour.step.pr_practice_intro.description",
        },
        {
          id: "pr-practice-name", action: "input", target: "tour-practice-name-field", route: "listing-package", practicePanel: true, minLength: 2, placement: "top",
          titleKey: "tour.step.pr_practice_name.title", descriptionKey: "tour.step.pr_practice_name.description",
          actionHintKey: "tour.step.pr_practice_name.action",
        },
        {
          id: "pr-practice-image", action: "describe", target: "tour-practice-image-area", route: "listing-package", practicePanel: true, placement: "top",
          titleKey: "tour.step.pr_practice_image.title", descriptionKey: "tour.step.pr_practice_image.description",
        },
        {
          // 2026-08-13 결함 수정: 이전에는 가격 입력란 하나만
          // minLength로 검사해, 재고를 비워둔 채로도 다음 단계로 넘어갈
          // 수 있었다 — 가격·재고 두 입력을 함께 검증하는 "input-group"
          // 액션으로 교체했다(targets/validation은 tourWireInputGroupInteraction
          // 참고, 순서상 targets[i]가 validation의 i번째 키에 대응한다).
          id: "pr-practice-price-stock", action: "input-group",
          targets: ["tour-practice-price-field", "tour-practice-stock-field"],
          validation: {
            price: { required: true, type: "number", min: 0 },
            stock: { required: true, type: "integer", min: 0 },
          },
          route: "listing-package", practicePanel: true, placement: "top",
          titleKey: "tour.step.pr_practice_price_stock.title", descriptionKey: "tour.step.pr_practice_price_stock.description",
          actionHintKey: "tour.step.pr_practice_price_stock.action",
        },
        {
          id: "pr-practice-save", action: "click", target: "tour-practice-save-btn", route: "listing-package", practicePanel: true, placement: "top", isComplete: true,
          titleKey: "tour.step.pr_practice_save.title", descriptionKey: "tour.step.pr_practice_save.description",
          actionHintKey: "tour.step.pr_practice_save.action",
        },
      ],
    },
    {
      id: "mobile-usage",
      order: 5,
      titleKey: "tour.course.mobile_usage.title",
      descriptionKey: "tour.course.mobile_usage.description",
      version: "1.0",
      requiredPermission: null,
      dataChange: "view_only",
      comingSoon: true,
      steps: [],
    },
    {
      id: "troubleshooting",
      order: 6,
      titleKey: "tour.course.troubleshooting.title",
      descriptionKey: "tour.course.troubleshooting.description",
      version: "1.0",
      // 요구사항 8 우선순위 2번 — 대상이 전부 일반 사용자에게도 보이는
      // 요소(topbar-status/nav-guides는 data-permission=""로 항상
      // 표시됨)라 requiredPermission을 null로 유지해도 실제로 끝까지
      // 진행 가능하다(screens-and-menus와 달리 실제 겹침 검증됨).
      requiredPermission: null,
      dataChange: "view_only",
      steps: [
        {
          id: "ts-welcome", action: "describe", route: null,
          titleKey: "tour.step.ts_welcome.title", descriptionKey: "tour.step.ts_welcome.description",
        },
        {
          id: "ts-status-badges", action: "describe", target: "topbar-status", route: null, placement: "bottom",
          titleKey: "tour.step.ts_status_badges.title", descriptionKey: "tour.step.ts_status_badges.description",
        },
        {
          id: "ts-open-guides", action: "click", target: "nav-guides", route: "guides", placement: "right",
          titleKey: "tour.step.ts_open_guides.title", descriptionKey: "tour.step.ts_open_guides.description",
          actionHintKey: "tour.step.ts_open_guides.action",
        },
        {
          id: "ts-complete", action: "describe", route: "guides", isComplete: true,
          titleKey: "tour.step.ts_complete.title", descriptionKey: "tour.step.ts_complete.description",
        },
      ],
    },
  ];

  // 가이드 정의 자체가 잘못돼도(예: 잘못 고친 매니페스트) 앱 전체가
  // 죽지 않게 한다 — 문제 있는 과정은 조용히 목록에서 제외하고
  // console.error로만 남긴다.
  function validateTourDefinition(tour) {
    if (!tour || typeof tour.id !== "string" || !tour.id) return "missing id";
    if (typeof tour.titleKey !== "string") return "missing titleKey";
    if (!Array.isArray(tour.steps)) return "steps must be array";
    const seenStepIds = new Set();
    const seenTargets = new Set();
    for (const step of tour.steps) {
      if (!step || typeof step.id !== "string") return `invalid step in ${tour.id}`;
      if (seenStepIds.has(step.id)) return `duplicate step id ${step.id} in ${tour.id}`;
      seenStepIds.add(step.id);
      // "input-group"(여러 입력을 함께 검증)은 target 대신 targets
      // 배열을 쓴다 — 두 형태 모두 같은 중복 검사 대상에 합친다.
      const stepTargets = step.target ? [step.target] : (Array.isArray(step.targets) ? step.targets : []);
      for (const t of stepTargets) {
        if (seenTargets.has(t)) return `duplicate target ${t} in ${tour.id}`;
        seenTargets.add(t);
      }
      if (!["describe", "click", "input", "input-group", "select", "navigate", "open-modal", "verify", "complete"].includes(step.action)) {
        return `unknown action "${step.action}" in ${tour.id}/${step.id}`;
      }
      if (step.action === "input-group") {
        if (!Array.isArray(step.targets) || step.targets.length === 0) return `input-group step ${step.id} missing targets in ${tour.id}`;
        if (!step.validation || typeof step.validation !== "object") return `input-group step ${step.id} missing validation in ${tour.id}`;
        if (Object.keys(step.validation).length !== step.targets.length) return `input-group step ${step.id} targets/validation length mismatch in ${tour.id}`;
      }
    }
    return null;
  }

  function getValidTours() {
    const valid = [];
    for (const tour of HOMEZ_TOURS) {
      const problem = validateTourDefinition(tour);
      if (problem) {
        // eslint-disable-next-line no-console
        console.error(`[HomezTour] 잘못된 가이드 정의 — 목록에서 제외: ${problem}`);
        continue;
      }
      valid.push(tour);
    }
    return valid.sort((a, b) => a.order - b.order);
  }

  // --------------------------------------------------
  // 진행 상태 저장 — 사용자 id로만 네임스페이스 분리, 토큰/민감정보
  // 없음. 기존 localStorage 사용 관례(homez_console_locale 등)와
  // 동일한 저장 위치를 쓴다.
  //
  // 2026-08-13 v2로 갱신 — { schemaVersion, scopes: { [userId]: {
  // [tourId]: { tourVersion, lastStepId, lastStepIndex, completed,
  // skippedStepIds, lastRunAt } } } } 형태로 바뀌었다. v1(루트가 곧
  // { [userId]: {...} }였던 형태)은 손실 없이 그대로 scopes 아래로
  // 감싸 마이그레이션한다. lastStepIndex 대신 lastStepId를 1차
  // 재개 기준으로 쓴다 — 향후 과정 단계가 추가/삭제/재배열되면
  // 인덱스만으로는 엉뚱한 단계로 재개될 수 있기 때문이다(id가 현재
  // 매니페스트에 없으면 안전하게 처음부터 다시 시작한다).
  //
  // 회사/환경 스코프는 일부러 넣지 않았다 — localStorage는 브라우저
  // origin(scheme+host+port)별로 이미 완전히 분리되고, 이 앱의
  // user.id는 회사 간에도 유일한 전역 PK라 "같은 브라우저에서 다른
  // 회사가 같은 사용자 id로 뒤섞이는" 시나리오는 발생하지 않는다.
  // 다만 Desktop 실행 파일 자체가 매 실행마다 무작위 포트로
  // 뜨는 구조(app/desktop/server.py find_free_port(), 고정 포트
  // 재시도 없음)라 실제 앱을 완전히 종료했다가 재실행하면 origin이
  // 통째로 바뀌어 이 localStorage 자체가 초기화된 것처럼 보일 수
  // 있다 — 이는 진행 상태 저장 형식의 문제가 아니라 Desktop 런처의
  // 구조적 문제이며, 이번 범위 밖으로 별도 보고한다(최종 보고 참고).
  // --------------------------------------------------

  const TOUR_PROGRESS_SCHEMA_VERSION = 2;

  function tourEmptyProgressRoot() {
    return { schemaVersion: TOUR_PROGRESS_SCHEMA_VERSION, scopes: {} };
  }

  function tourLoadAllProgress() {
    let raw;
    try {
      raw = JSON.parse(localStorage.getItem(TOUR_PROGRESS_KEY) || "null");
    } catch (_) {
      return tourEmptyProgressRoot();
    }
    if (!raw || typeof raw !== "object") return tourEmptyProgressRoot();
    if (raw.schemaVersion === TOUR_PROGRESS_SCHEMA_VERSION && raw.scopes && typeof raw.scopes === "object") {
      return raw;
    }
    if (raw.schemaVersion == null) {
      // v1 — 루트 자체가 { [userId]: { [tourId]: {...} } }였다.
      return { schemaVersion: TOUR_PROGRESS_SCHEMA_VERSION, scopes: raw };
    }
    // 알 수 없는 미래 버전 — 추측해서 읽지 않고 안전하게 빈 상태로
    // 시작한다(크래시보다는 진행 상태 손실이 낫다).
    return tourEmptyProgressRoot();
  }

  function tourSaveAllProgress(scopes) {
    try {
      localStorage.setItem(
        TOUR_PROGRESS_KEY,
        JSON.stringify({ schemaVersion: TOUR_PROGRESS_SCHEMA_VERSION, scopes }),
      );
    } catch (_) {
      /* 저장 실패해도(용량 초과 등) 이번 세션 진행 자체는 계속된다 */
    }
  }

  function tourCurrentUserKey() {
    const user = getCurrentUser();
    return user && user.id != null ? String(user.id) : "anonymous";
  }

  function tourGetProgress(tourId) {
    const root = tourLoadAllProgress();
    const userProgress = root.scopes[tourCurrentUserKey()] || {};
    return userProgress[tourId] || null;
  }

  function tourSaveProgress(tourId, patch) {
    const root = tourLoadAllProgress();
    const userKey = tourCurrentUserKey();
    if (!root.scopes[userKey]) root.scopes[userKey] = {};
    const existing = root.scopes[userKey][tourId] || {
      tourVersion: null, lastStepId: null, lastStepIndex: 0, completed: false, skippedStepIds: [], lastRunAt: null,
    };
    root.scopes[userKey][tourId] = Object.assign({}, existing, patch, { lastRunAt: new Date().toISOString() });
    tourSaveAllProgress(root.scopes);
    // 2026-08-14 Gate F-2 — localStorage는 즉시(동기) 갱신하고, 서버
    // 반영은 별도로 비동기 fire-and-forget한다(가이드 단계 클릭마다
    // UI가 네트워크를 기다리지 않는다).
    pushGuideProgressToServer();
  }

  function tourResetProgress(tourId) {
    const root = tourLoadAllProgress();
    const userKey = tourCurrentUserKey();
    if (root.scopes[userKey]) {
      delete root.scopes[userKey][tourId];
      tourSaveAllProgress(root.scopes);
      pushGuideProgressToServer();
    }
  }

  function tourResetAllProgress() {
    const root = tourLoadAllProgress();
    delete root.scopes[tourCurrentUserKey()];
    tourSaveAllProgress(root.scopes);
    pushGuideProgressToServer();
  }

  function tourHasAnyProgress() {
    const root = tourLoadAllProgress();
    const userProgress = root.scopes[tourCurrentUserKey()] || {};
    return Object.values(userProgress).some((p) => p && (p.lastStepIndex > 0 || p.completed));
  }

  function tourMostRecentInProgress() {
    const root = tourLoadAllProgress();
    const userProgress = root.scopes[tourCurrentUserKey()] || {};
    let best = null;
    for (const [tourId, p] of Object.entries(userProgress)) {
      if (!p || p.completed) continue;
      if (!best || (p.lastRunAt || "") > (best.progress.lastRunAt || "")) {
        best = { tourId, progress: p };
      }
    }
    return best;
  }

  // 저장된 lastStepId를 현재 매니페스트의 실제 단계 목록에서 다시
  // 찾아 안전한 재개 인덱스를 계산한다 — 과정 버전이 바뀌었거나
  // 해당 id를 가진 단계가 더 이상 없으면(개편/삭제) 잘못된 인덱스로
  // 재개하는 대신 처음부터 다시 시작한다. lastStepId가 아직 없는
  // 구v1 데이터는 기존 lastStepIndex를 그대로 쓰되 범위를 벗어나지
  // 않게 클램프한다.
  function tourResolveResumeIndex(tour, progress) {
    if (!progress) return 0;
    if (progress.lastStepId) {
      const idx = tour.steps.findIndex((s) => s.id === progress.lastStepId);
      return idx >= 0 ? idx : 0;
    }
    const idx = Number.isInteger(progress.lastStepIndex) ? progress.lastStepIndex : 0;
    return Math.min(Math.max(idx, 0), Math.max(tour.steps.length - 1, 0));
  }

  function tourUserHasPermission(tour) {
    if (!tour.requiredPermission) return true;
    const user = getCurrentUser();
    const role = (user && user.role) || "";
    if (tour.requiredPermission === "admin") {
      return role === "ADMIN" || role === "SUPER_ADMIN";
    }
    return true;
  }

  // --------------------------------------------------
  // 실행 상태(현재 열려 있는 가이드) — 모듈 스코프 변수
  // --------------------------------------------------

  let tourActiveId = null;
  let tourActiveDef = null;
  let tourStepIndex = 0;
  let tourWatchdogTimer = null;
  let tourClickListenerCleanup = null;
  let tourTargetNotFoundSince = null;
  const TOUR_TARGET_RETRY_TIMEOUT_MS = 6000;

  function tourPrefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function tourIsMobileViewport() {
    return window.innerWidth <= 640;
  }

  // --------------------------------------------------
  // 과정 선택 화면
  // --------------------------------------------------

  function tourCourseCardHtml(tour) {
    const progress = tourGetProgress(tour.id);
    const hasPermission = tourUserHasPermission(tour);
    const totalSteps = tour.steps.length;

    let statusKey = "tour.course.status_not_started";
    if (progress && progress.completed) statusKey = "tour.course.status_completed";
    else if (progress && progress.lastStepIndex > 0) statusKey = "tour.course.status_in_progress";

    const dataChangeKey = {
      view_only: "tour.course.data_change_view_only",
      practice: "tour.course.data_change_practice",
      real: "tour.course.data_change_real",
    }[tour.dataChange] || "tour.course.data_change_view_only";

    const permissionKey = tour.requiredPermission ? "tour.course.permission_admin" : "tour.course.permission_none";

    let startLabel = HomezI18n.t("tour.start");
    if (progress && !progress.completed && progress.lastStepIndex > 0) {
      startLabel = HomezI18n.t("tour.course.resume_from_step", { step: progress.lastStepIndex + 1 });
    } else if (progress && progress.completed) {
      startLabel = HomezI18n.t("tour.restart");
    }

    return `
      <div class="tour-course-card ${tour.comingSoon ? "tour-course-card-disabled" : ""}">
        <div class="tour-course-card-head">
          <h3>${escapeHtml(HomezI18n.t(tour.titleKey))}</h3>
          ${tour.comingSoon ? `<span class="pill neutral">${escapeHtml(HomezI18n.t("tour.course.coming_soon"))}</span>` : `<span class="pill ${statusKey === "tour.course.status_completed" ? "ok" : "neutral"}">${escapeHtml(HomezI18n.t(statusKey))}</span>`}
        </div>
        <p class="stat-sub">${escapeHtml(HomezI18n.t(tour.descriptionKey))}</p>
        <ul class="tour-course-meta-list">
          ${totalSteps ? `<li>${escapeHtml(HomezI18n.t("tour.course.total_steps", { count: totalSteps }))}</li>` : ""}
          <li>${escapeHtml(HomezI18n.t(dataChangeKey))}</li>
          <li>${escapeHtml(HomezI18n.t(permissionKey))}</li>
        </ul>
        ${!hasPermission ? `<p class="field-error">${escapeHtml(HomezI18n.t("tour.course.permission_required_notice"))}</p>` : ""}
        <button type="button" class="btn btn-primary btn-sm" data-tour-start="${escapeHtml(tour.id)}" ${(tour.comingSoon || !hasPermission || totalSteps === 0) ? "disabled" : ""}>
          ${escapeHtml(startLabel)}
        </button>
      </div>`;
  }

  function renderTourSelectDialog() {
    const list = el("tour-select-list");
    const tours = getValidTours();
    list.innerHTML = tours.map(tourCourseCardHtml).join("");
    list.querySelectorAll("[data-tour-start]").forEach((btn) => {
      btn.addEventListener("click", () => {
        el("tour-select-dialog").close();
        startTour(btn.dataset.tourStart);
      });
    });
  }

  function openTourSelectDialog() {
    renderTourSelectDialog();
    el("tour-select-dialog").showModal();
  }

  // 설정 화면 4버튼(가이드 시작/이어서 하기/처음부터 다시 시작/진행
  // 기록 초기화) — 과정별이 아니라 화면 전체 버튼이므로, "이어서"와
  // "다시 시작"은 가장 최근에 진행 중이던 과정을 대상으로 한다.
  function renderGuideModeSettingsPanel() {
    const container = el("guide-mode-settings-actions");
    if (!container) return;

    const mostRecent = tourMostRecentInProgress();
    const hasProgress = tourHasAnyProgress();

    const buttons = [];
    buttons.push(`<button type="button" class="btn btn-primary" id="guide-mode-start-btn" data-i18n="tour.start">${escapeHtml(HomezI18n.t("tour.start"))}</button>`);
    if (mostRecent) {
      buttons.push(`<button type="button" class="btn btn-secondary" id="guide-mode-resume-btn" data-i18n="tour.resume">${escapeHtml(HomezI18n.t("tour.resume"))}</button>`);
      buttons.push(`<button type="button" class="btn btn-ghost" id="guide-mode-restart-btn" data-i18n="tour.restart">${escapeHtml(HomezI18n.t("tour.restart"))}</button>`);
    }
    if (hasProgress) {
      buttons.push(`<button type="button" class="btn btn-ghost" id="guide-mode-reset-btn" data-i18n="tour.reset_progress">${escapeHtml(HomezI18n.t("tour.reset_progress"))}</button>`);
    }
    container.innerHTML = buttons.join("");

    el("guide-mode-start-btn").addEventListener("click", openTourSelectDialog);

    if (mostRecent) {
      el("guide-mode-resume-btn").addEventListener("click", () => startTour(mostRecent.tourId, { resume: true }));
      el("guide-mode-restart-btn").addEventListener("click", () => {
        tourResetProgress(mostRecent.tourId);
        startTour(mostRecent.tourId);
      });
    }
    if (hasProgress) {
      el("guide-mode-reset-btn").addEventListener("click", async () => {
        const result = await confirmDialog({
          title: HomezI18n.t("tour.reset_progress"),
          body: HomezI18n.t("tour.exit_confirm_body"),
        });
        if (result.confirmed) {
          tourResetAllProgress();
          renderGuideModeSettingsPanel();
          toast(HomezI18n.t("tour.reset_progress"), "info");
        }
      });
    }
  }

  function initTourMode() {
    renderGuideModeSettingsPanel();
    document.addEventListener("homez:locale-changed", () => {
      if (currentView === "account-security") renderGuideModeSettingsPanel();
    });

    el("guide-mode-launch-from-guides-btn").addEventListener("click", openTourSelectDialog);
    el("tour-select-close").addEventListener("click", () => el("tour-select-dialog").close());
  }

  // --------------------------------------------------
  // 실행 엔진
  // --------------------------------------------------

  function tourStepCount() {
    return tourActiveDef ? tourActiveDef.steps.length : 0;
  }

  function tourCurrentStep() {
    return tourActiveDef ? tourActiveDef.steps[tourStepIndex] : null;
  }

  async function startTour(tourId, opts = {}) {
    const tour = getValidTours().find((t) => t.id === tourId);
    if (!tour || tour.comingSoon || tour.steps.length === 0) return;
    if (!tourUserHasPermission(tour)) {
      toast(HomezI18n.t("tour.course.permission_required_notice"), "error");
      return;
    }

    tourActiveId = tourId;
    tourActiveDef = tour;

    const progress = tourGetProgress(tourId);
    tourStepIndex = (opts.resume && progress && !progress.completed)
      ? tourResolveResumeIndex(tour, progress)
      : 0;

    tourSaveProgress(tourId, {
      lastStepIndex: tourStepIndex,
      lastStepId: tour.steps[tourStepIndex] ? tour.steps[tourStepIndex].id : null,
      tourVersion: tour.version,
      completed: false,
    });

    el("tour-overlay").hidden = false;
    el("tour-overlay").setAttribute("aria-hidden", "false");
    document.addEventListener("keydown", tourHandleKeydown);
    tourWatchdogTimer = window.setInterval(tourWatchdogTick, 350);

    await tourRenderCurrentStep();
  }

  function tourHandleKeydown(ev) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      tourOpenExitConfirm();
    }
    // 접근성 요구사항 — Tab 이동을 툴팁 내부로만 가둔다(포커스 트랩).
    if (ev.key === "Tab") {
      const tooltip = el("tour-tooltip");
      const focusable = Array.from(tooltip.querySelectorAll("button, input, [tabindex]:not([tabindex='-1'])"))
        .filter((elx) => !elx.disabled && elx.offsetParent !== null);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    }
  }

  function tourWatchdogTick() {
    if (!tourActiveId) return;

    // 세션 만료/로그아웃 — shell이 숨겨지면 로그인 화면으로 전환된
    // 것이다(요구사항: "세션이 만료됨"/"가이드 중 로그아웃함").
    const shellEl = el("shell");
    if (shellEl && shellEl.hidden) {
      tourPauseForRecovery();
      return;
    }

    tourRepositionForCurrentStep();
  }

  function tourPauseForRecovery() {
    // 진행 상태는 이미 각 단계 진입 시 저장되어 있으므로 그대로
    // 두고, 오버레이만 정리한다 — 다음 로그인 후 "이어서 하기"로
    // 정확히 이 지점부터 재개된다.
    tourTeardownOverlay();
  }

  function tourTeardownOverlay() {
    if (tourWatchdogTimer) {
      window.clearInterval(tourWatchdogTimer);
      tourWatchdogTimer = null;
    }
    document.removeEventListener("keydown", tourHandleKeydown);
    if (tourClickListenerCleanup) {
      tourClickListenerCleanup();
      tourClickListenerCleanup = null;
    }
    const overlay = el("tour-overlay");
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    el("tour-practice-panel").hidden = true;
    tourActiveId = null;
    tourActiveDef = null;
    tourStepIndex = 0;
    tourTargetNotFoundSince = null;
  }

  async function tourRenderCurrentStep() {
    const step = tourCurrentStep();
    if (!step) return;

    if (tourClickListenerCleanup) {
      tourClickListenerCleanup();
      tourClickListenerCleanup = null;
    }
    el("tour-target-not-found").hidden = true;
    tourTargetNotFoundSince = null;

    // "뒤로 이동하면 이전 화면과 강조 요소를 복원", 그리고 저장된
    // 진행 상태로 재개할 때도 같은 경로를 탄다 — 이 단계가 요구하는
    // 화면으로 필요하면 실제 navigateTo()를 호출한다(사용자를 대신해
    // 클릭하는 것이 아니라, 이미 진행 중인 가이드의 "현재 위치"를
    // 화면과 일치시키는 내비게이션 복원이다).
    if (step.route && currentView !== step.route) {
      navigateTo(step.route);
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }

    el("tour-course-name").textContent = HomezI18n.t(tourActiveDef.titleKey);
    el("tour-step-progress").textContent = HomezI18n.t("tour.step_progress", {
      current: tourStepIndex + 1, total: tourStepCount(),
    });
    el("tour-practice-badge").hidden = tourActiveDef.dataChange !== "practice";
    el("tour-step-title").textContent = HomezI18n.t(step.titleKey);
    el("tour-step-description").textContent = HomezI18n.t(step.descriptionKey);
    el("tour-step-action-hint").textContent = step.actionHintKey ? HomezI18n.t(step.actionHintKey) : "";
    el("tour-step-action-hint").hidden = !step.actionHintKey;

    // 접근성 요구사항 — 단계가 바뀔 때마다 툴팁 자체에 포커스를
    // 옮겨, 스크린 리더가 aria-labelledby(제목)/aria-describedby
    // (설명)를 자동으로 읽어주게 한다(이전에는 진행률 배지의
    // aria-live만 갱신되고 실제 제목·설명은 안내되지 않았다).
    // aria-modal="false"라 포커스를 옮겨도 배경의 실제 강조 대상은
    // 여전히 스크린 리더 트리에서 접근 가능하다.
    el("tour-tooltip").focus({ preventScroll: true });

    el("tour-prev-btn").disabled = tourStepIndex === 0;
    el("tour-next-btn").hidden = step.action !== "describe";
    el("tour-next-btn").disabled = false;
    el("tour-exit-btn").hidden = false;
    el("tour-skip-btn").hidden = !!step.isComplete;

    el("tour-practice-panel").hidden = !step.practicePanel;
    if (step.practicePanel) {
      // 각 실행은 항상 빈 연습 입력으로 시작한다 — 이전 실행/이전
      // 사용자의 값이 남아 보이지 않게 한다.
      if (tourStepIndex === tourActiveDef.steps.findIndex((s) => s.practicePanel)) {
        el("tour-practice-name-field").value = "";
        el("tour-practice-price-field").value = "";
        el("tour-practice-stock-field").value = "";
      }
    }

    if (tourMaybeShowSafetyWarning(step)) {
      await tourFindTargetAndPosition(step);
    }
  }

  // 실제 데이터 변경 단계 진입 전 확인 — 지금 구현된 2개 시범 과정은
  // 둘 다 view_only/practice라 이 경로를 타지 않지만(사전 검토에서
  // 확인), 이후 과정 확장 시 step.safetyLevel === "real"만 붙이면
  // 그대로 작동하도록 미리 구현해 둔다. true를 반환하면 계속 진행,
  // false면 tourRenderCurrentStep 호출부가 대기해야 한다(다이얼로그
  // 선택을 기다리는 중).
  function tourMaybeShowSafetyWarning(step) {
    if (step.safetyLevel !== "real") return true;

    const dialog = el("tour-safety-warning-dialog");
    dialog.showModal();

    const cleanup = () => {
      el("tour-safety-practice-btn").onclick = null;
      el("tour-safety-real-btn").onclick = null;
      el("tour-safety-skip-btn").onclick = null;
      el("tour-safety-exit-btn").onclick = null;
      dialog.close();
    };

    el("tour-safety-practice-btn").onclick = () => {
      cleanup();
      tourFindTargetAndPosition(step);
    };
    el("tour-safety-real-btn").onclick = () => {
      cleanup();
      // 실제 기능으로 계속하더라도 이 엔진은 여전히 어떤 버튼도
      // 대신 클릭하지 않는다 — 사용자의 진짜 클릭만 기다린다.
      tourFindTargetAndPosition(step);
    };
    el("tour-safety-skip-btn").onclick = () => {
      cleanup();
      tourSkipCurrentStep();
    };
    el("tour-safety-exit-btn").onclick = () => {
      cleanup();
      tourShowSummary({ completed: false });
    };

    return false;
  }

  function tourShowFullDim() {
    // 강조할 페이지 요소가 없을 때(설명 전용 단계) 쓰는 전체 화면
    // 배경 오버레이 — 마스크 4개 중 하나를 화면 전체 크기로 펼친다.
    el("tour-spotlight-ring").hidden = true;
    const top = document.querySelector(".tour-mask-top");
    const bottom = document.querySelector(".tour-mask-bottom");
    const left = document.querySelector(".tour-mask-left");
    const right = document.querySelector(".tour-mask-right");
    [bottom, left, right].forEach((m) => { if (m) m.hidden = true; });
    if (top) {
      top.hidden = false;
      Object.assign(top.style, { left: "0px", top: "0px", width: `${window.innerWidth}px`, height: `${window.innerHeight}px` });
    }
  }

  async function tourFindTargetAndPosition(step) {
    // "input-group"은 target 대신 targets 배열을 쓴다 — step.target이
    // 없다고 곧바로 "대상 없는 설명 단계"로 취급하면 이 액션 타입이
    // 항상 잘못된 분기(빈 배경만 뜨고 입력창에 리스너가 안 붙는 결함)
    // 를 타게 된다.
    if (!step.target && step.action !== "input-group") {
      // 대상이 없는 설명 단계(환영 인사 등).
      tourShowFullDim();
      tourPositionTooltipCentered();
      return;
    }

    if (step.practicePanel) {
      // 연습 패널 안의 입력창/버튼은 페이지 위 실제 요소가 아니라
      // 이 오버레이 자신의 UI(툴팁) 안에 있다 — 그 위에 또 다른
      // 스포트라이트를 그리면 시각적으로 의미가 없다(자기 자신을
      // 감싸는 빈 사각형만 떠 보이는 실제 버그를 캡처로 발견해
      // 고쳤다). 배경만 전체 어둡게 하고 툴팁을 중앙에 고정한 뒤,
      // 클릭/입력 리스너는 그대로 실제 연습 요소에 붙인다.
      tourShowFullDim();
      tourPositionTooltipCentered();

      if (step.action === "input-group") {
        const elements = step.targets.map((guideId) => document.querySelector(`[data-guide-id="${CSS.escape(guideId)}"]`));
        if (elements.some((elx) => !elx)) {
          tourShowTargetNotFound();
          return;
        }
        tourWireInputGroupInteraction(step, elements);
        window.setTimeout(() => elements[0].focus(), 50);
        return;
      }

      const targetEl = document.querySelector(`[data-guide-id="${CSS.escape(step.target)}"]`);
      if (!targetEl) {
        tourShowTargetNotFound();
        return;
      }
      tourWireStepInteraction(step, targetEl);
      if (step.action === "input") {
        window.setTimeout(() => targetEl.focus(), 50);
      }
      return;
    }

    const startedAt = Date.now();
    let targetEl = null;

    while (Date.now() - startedAt < TOUR_TARGET_RETRY_TIMEOUT_MS) {
      targetEl = tourResolveTarget(step.target);
      if (targetEl) break;
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => window.setTimeout(resolve, 200));
    }

    if (!targetEl) {
      tourShowTargetNotFound();
      return;
    }

    tourWireStepInteraction(step, targetEl);
    tourScrollAndPosition(targetEl, step.placement || "bottom");
  }

  function tourResolveTarget(guideId) {
    const elx = document.querySelector(`[data-guide-id="${CSS.escape(guideId)}"]`);
    if (!elx) return null;
    const rect = elx.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return null; // hidden(권한 등으로 숨김 포함)

    // 모바일에서 사이드바 대상이 접혀 있으면 자동으로 펼친다
    // (요구사항: "필요한 경우 자동으로 펼치거나").
    if (tourIsMobileViewport() && elx.closest(".sidenav")) {
      const shellEl = el("shell");
      if (shellEl && !shellEl.classList.contains("nav-open")) {
        shellEl.classList.add("nav-open");
      }
    }
    return elx;
  }

  function tourShowTargetNotFound() {
    if (!tourTargetNotFoundSince) tourTargetNotFoundSince = Date.now();
    el("tour-spotlight-ring").hidden = true;
    document.querySelectorAll("[data-tour-mask]").forEach((m) => { m.hidden = true; });
    el("tour-target-not-found").hidden = false;
    tourPositionTooltipCentered();
  }

  function tourWireStepInteraction(step, targetEl) {
    if (step.action === "click") {
      const onClick = () => tourHandleTargetActivated(step);
      targetEl.addEventListener("click", onClick, { once: true });
      tourClickListenerCleanup = () => targetEl.removeEventListener("click", onClick);
    } else if (step.action === "input") {
      const minLength = step.minLength || 1;
      const nextBtn = el("tour-next-btn");
      nextBtn.hidden = false;
      nextBtn.disabled = (targetEl.value || "").trim().length < minLength;
      const onInput = () => {
        nextBtn.disabled = (targetEl.value || "").trim().length < minLength;
      };
      targetEl.addEventListener("input", onInput);
      tourClickListenerCleanup = () => targetEl.removeEventListener("input", onInput);
    } else {
      el("tour-next-btn").hidden = false;
    }
  }

  // 2026-08-13 결함 수정: 가격·재고처럼 "여러 입력이 동시에 유효해야
  // 다음으로 진행" 요구를 표현하는 검증 규칙. 빈 문자열/음수/NaN/
  // 소수 재고를 각각 거부한다 — required 필드가 아니면 빈 값은 통과.
  function tourValidateInputGroupField(rule, rawValue) {
    const value = (rawValue == null ? "" : String(rawValue)).trim();
    if (value === "") return !rule.required;
    if (rule.type === "integer") {
      if (!/^-?\d+$/.test(value)) return false;
      const n = parseInt(value, 10);
      if (rule.min != null && n < rule.min) return false;
      if (rule.max != null && n > rule.max) return false;
      return true;
    }
    if (rule.type === "number") {
      if (!/^-?\d+(\.\d+)?$/.test(value)) return false;
      const n = parseFloat(value);
      if (rule.min != null && n < rule.min) return false;
      if (rule.max != null && n > rule.max) return false;
      return true;
    }
    return true;
  }

  // "input-group" 액션 — step.targets[i]가 Object.keys(step.validation)의
  // i번째 키에 위치상 대응한다(validateTourDefinition이 두 길이가
  // 같은지 미리 강제한다). 모든 필드가 유효해야만 다음 버튼이
  // 활성화되며, 값이 다시 무효해지면 즉시 재비활성화된다. 실제 API는
  // 어디에도 호출하지 않는다.
  function tourWireInputGroupInteraction(step, elements) {
    const nextBtn = el("tour-next-btn");
    nextBtn.hidden = false;

    const fieldNames = Object.keys(step.validation);
    const computeValid = () => elements.every((elx, idx) => tourValidateInputGroupField(step.validation[fieldNames[idx]], elx.value));
    const updateDisabled = () => { nextBtn.disabled = !computeValid(); };
    updateDisabled();

    const cleanupFns = elements.map((elx) => {
      elx.addEventListener("input", updateDisabled);
      return () => elx.removeEventListener("input", updateDisabled);
    });
    tourClickListenerCleanup = () => cleanupFns.forEach((fn) => fn());
  }

  async function tourHandleTargetActivated(step) {
    if (step.route) {
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        const viewEl = document.getElementById(`view-${step.route}`);
        if (viewEl && viewEl.classList.contains("active")) break;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((resolve) => window.setTimeout(resolve, 100));
      }
    }
    tourAdvance();
  }

  function tourAdvance() {
    const step = tourCurrentStep();
    if (step && step.isComplete) {
      tourSaveProgress(tourActiveId, { lastStepIndex: tourStepIndex, lastStepId: step.id, completed: true });
      tourShowSummary({ completed: true });
      return;
    }
    if (tourStepIndex < tourStepCount() - 1) {
      tourStepIndex += 1;
      const nextStep = tourCurrentStep();
      tourSaveProgress(tourActiveId, { lastStepIndex: tourStepIndex, lastStepId: nextStep ? nextStep.id : null });
      tourRenderCurrentStep();
    }
  }

  function tourGoBack() {
    if (tourStepIndex > 0) {
      tourStepIndex -= 1;
      const prevStep = tourCurrentStep();
      tourSaveProgress(tourActiveId, { lastStepIndex: tourStepIndex, lastStepId: prevStep ? prevStep.id : null });
      tourRenderCurrentStep();
    }
  }

  function tourSkipCurrentStep() {
    const root = tourLoadAllProgress();
    const userKey = tourCurrentUserKey();
    const existing = (root.scopes[userKey] && root.scopes[userKey][tourActiveId]) || { skippedStepIds: [] };
    const skipped = new Set(existing.skippedStepIds || []);
    const step = tourCurrentStep();
    if (step) skipped.add(step.id);
    tourSaveProgress(tourActiveId, { skippedStepIds: Array.from(skipped) });
    tourAdvance();
  }

  function tourRepositionForCurrentStep() {
    const step = tourCurrentStep();
    if (!step) return;
    if (!step.target || step.practicePanel) {
      const top = document.querySelector(".tour-mask-top");
      if (top && !top.hidden) {
        Object.assign(top.style, { width: `${window.innerWidth}px`, height: `${window.innerHeight}px` });
      }
      return;
    }
    if (el("tour-target-not-found").hidden === false) return;
    const targetEl = tourResolveTarget(step.target);
    if (!targetEl) {
      // 유예 시간을 두고서만 "찾을 수 없음"으로 전환한다(일시적
      // 재렌더링 중 깜빡임 방지).
      if (tourTargetNotFoundSince && Date.now() - tourTargetNotFoundSince > TOUR_TARGET_RETRY_TIMEOUT_MS) {
        tourShowTargetNotFound();
      } else if (!tourTargetNotFoundSince) {
        tourTargetNotFoundSince = Date.now();
      }
      return;
    }
    tourTargetNotFoundSince = null;
    tourPositionSpotlightAndMasks(targetEl.getBoundingClientRect());
    tourPositionTooltip(targetEl.getBoundingClientRect(), step.placement || "bottom");
  }

  function tourScrollAndPosition(targetEl, placement) {
    targetEl.scrollIntoView({
      behavior: tourPrefersReducedMotion() ? "auto" : "smooth",
      block: "center",
      inline: "nearest",
    });
    window.setTimeout(() => {
      const rect = targetEl.getBoundingClientRect();
      tourPositionSpotlightAndMasks(rect);
      tourPositionTooltip(rect, placement);
    }, tourPrefersReducedMotion() ? 0 : 260);
  }

  const TOUR_SPOTLIGHT_PADDING = 6;

  function tourPositionSpotlightAndMasks(rect) {
    const pad = TOUR_SPOTLIGHT_PADDING;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const ring = el("tour-spotlight-ring");
    ring.hidden = false;
    ring.style.left = `${Math.max(0, rect.left - pad)}px`;
    ring.style.top = `${Math.max(0, rect.top - pad)}px`;
    ring.style.width = `${rect.width + pad * 2}px`;
    ring.style.height = `${rect.height + pad * 2}px`;

    const top = document.querySelector(".tour-mask-top");
    const bottom = document.querySelector(".tour-mask-bottom");
    const left = document.querySelector(".tour-mask-left");
    const right = document.querySelector(".tour-mask-right");

    const spotTop = Math.max(0, rect.top - pad);
    const spotBottom = Math.min(vh, rect.bottom + pad);
    const spotLeft = Math.max(0, rect.left - pad);
    const spotRight = Math.min(vw, rect.right + pad);

    [top, bottom, left, right].forEach((m) => { if (m) m.hidden = false; });

    if (top) Object.assign(top.style, { left: "0px", top: "0px", width: `${vw}px`, height: `${spotTop}px` });
    if (bottom) Object.assign(bottom.style, { left: "0px", top: `${spotBottom}px`, width: `${vw}px`, height: `${Math.max(0, vh - spotBottom)}px` });
    if (left) Object.assign(left.style, { left: "0px", top: `${spotTop}px`, width: `${spotLeft}px`, height: `${Math.max(0, spotBottom - spotTop)}px` });
    if (right) Object.assign(right.style, { left: `${spotRight}px`, top: `${spotTop}px`, width: `${Math.max(0, vw - spotRight)}px`, height: `${Math.max(0, spotBottom - spotTop)}px` });
  }

  function tourPositionTooltipCentered() {
    const tooltip = el("tour-tooltip");
    tooltip.classList.add("tour-tooltip-centered");
    tooltip.classList.remove("tour-tooltip-sheet");
    tooltip.style.left = "";
    tooltip.style.top = "";
    tooltip.style.transform = "";
  }

  function tourPositionTooltip(rect, placement) {
    const tooltip = el("tour-tooltip");
    tooltip.classList.remove("tour-tooltip-centered");

    if (tourIsMobileViewport()) {
      // 모바일에서는 강조 요소와 겹치지 않도록 항상 하단 시트로
      // 고정한다(요구사항).
      tooltip.classList.add("tour-tooltip-sheet");
      tooltip.style.left = "";
      tooltip.style.top = "";
      tooltip.style.transform = "";
      return;
    }
    tooltip.classList.remove("tour-tooltip-sheet");

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const tw = tooltip.offsetWidth || 320;
    const th = tooltip.offsetHeight || 160;
    const gap = 14;

    const candidates = {
      right: { left: rect.right + gap, top: rect.top },
      left: { left: rect.left - tw - gap, top: rect.top },
      bottom: { left: rect.left, top: rect.bottom + gap },
      top: { left: rect.left, top: rect.top - th - gap },
    };

    let order = [placement, "bottom", "right", "left", "top"];
    let chosen = candidates[placement] || candidates.bottom;

    for (const key of order) {
      const c = candidates[key];
      if (!c) continue;
      if (c.left >= 8 && c.left + tw <= vw - 8 && c.top >= 8 && c.top + th <= vh - 8) {
        chosen = c;
        break;
      }
    }

    const clampedLeft = Math.min(Math.max(chosen.left, 8), Math.max(8, vw - tw - 8));
    const clampedTop = Math.min(Math.max(chosen.top, 8), Math.max(8, vh - th - 8));

    tooltip.style.left = `${clampedLeft}px`;
    tooltip.style.top = `${clampedTop}px`;
    tooltip.style.transform = "";
  }

  function tourOpenExitConfirm() {
    el("tour-exit-confirm-dialog").showModal();
  }

  function tourExitNow() {
    tourTeardownOverlay();
  }

  function tourShowSummary({ completed }) {
    const progress = tourGetProgress(tourActiveId) || { skippedStepIds: [] };
    const total = tourStepCount();
    const completedCount = completed ? total : tourStepIndex;
    const skippedCount = (progress.skippedStepIds || []).length;

    el("tour-summary-title").textContent = completed ? HomezI18n.t("tour.completed") : HomezI18n.t("tour.summary_title");
    el("tour-summary-body").innerHTML = `
      <p>${escapeHtml(HomezI18n.t("tour.summary.completed_steps", { count: completedCount }))}</p>
      <p>${escapeHtml(HomezI18n.t("tour.summary.skipped_steps", { count: skippedCount }))}</p>
      <p class="stat-sub">${escapeHtml(HomezI18n.t("tour.summary.review_hint"))}</p>
    `;

    const nextTour = getValidTours().find((t) => !t.comingSoon && t.id !== tourActiveId && !(tourGetProgress(t.id) || {}).completed);
    el("tour-summary-next-btn").hidden = !nextTour;
    if (nextTour) {
      el("tour-summary-next-btn").onclick = () => {
        el("tour-summary-dialog").close();
        tourTeardownOverlay();
        startTour(nextTour.id);
      };
    }

    const finishedTourId = tourActiveId;
    tourTeardownOverlay();
    el("tour-summary-dialog").showModal();

    el("tour-summary-restart-btn").onclick = () => {
      el("tour-summary-dialog").close();
      tourResetProgress(finishedTourId);
      startTour(finishedTourId);
    };
    el("tour-summary-open-docs-btn").onclick = () => {
      el("tour-summary-dialog").close();
      navigateTo("guides");
    };
    el("tour-summary-close-btn").onclick = () => {
      el("tour-summary-dialog").close();
    };

    if (currentView === "account-security") renderGuideModeSettingsPanel();
  }

  function initTourOverlayControls() {
    el("tour-prev-btn").addEventListener("click", tourGoBack);
    el("tour-next-btn").addEventListener("click", tourAdvance);
    el("tour-skip-btn").addEventListener("click", tourSkipCurrentStep);
    el("tour-skip-from-error-btn").addEventListener("click", tourSkipCurrentStep);
    el("tour-exit-btn").addEventListener("click", tourOpenExitConfirm);
    el("tour-retry-btn").addEventListener("click", () => tourRenderCurrentStep());
    el("tour-review-btn").addEventListener("click", () => tourRenderCurrentStep());

    el("tour-exit-confirm-ok").addEventListener("click", () => {
      el("tour-exit-confirm-dialog").close();
      tourShowSummary({ completed: false });
    });
    el("tour-exit-confirm-cancel").addEventListener("click", () => {
      el("tour-exit-confirm-dialog").close();
    });

    // "다른 곳(가려진 배경) 클릭 시 진행하지 않음" — 4개 마스크는
    // 클릭을 그냥 흡수만 하고 아무 것도 진행시키지 않는다. 살짝
    // 흔들어 "여기가 아니다"만 알려준다(모션 줄이기 설정 존중).
    document.querySelectorAll("[data-tour-mask]").forEach((mask) => {
      mask.addEventListener("click", () => {
        if (tourPrefersReducedMotion()) return;
        const tooltip = el("tour-tooltip");
        tooltip.classList.add("tour-shake");
        window.setTimeout(() => tooltip.classList.remove("tour-shake"), 260);
      });
    });

    let resizeDebounce = null;
    window.addEventListener("resize", () => {
      if (!tourActiveId) return;
      window.clearTimeout(resizeDebounce);
      resizeDebounce = window.setTimeout(tourRepositionForCurrentStep, 120);
    });

    // 실제 저장(연습 완료) 버튼 — 어떤 API도 호출하지 않는다. click
    // 리스너는 tourWireStepInteraction()이 매 단계 진입 시 동적으로
    // 붙인다(여기서는 정적 버튼 자체에 아무 핸들러도 미리 걸지
    // 않는다 — 실수로 다른 곳에서 재사용돼도 안전하다).
  }

  // --------------------------------------------------
  // 설정 · 계정 및 보안
  // --------------------------------------------------

  function isSuperAdmin() {
    const user = getCurrentUser();
    return !!(user && (user.role || "").toUpperCase() === "SUPER_ADMIN");
  }

  async function loadAccountSecurity() {
    el("cp-current").value = "";
    el("cp-new").value = "";
    el("cp-new-confirm").value = "";
    renderPasswordPolicyChecklist(el("cp-policy-checklist"), "");
    renderGuideModeSettingsPanel();

    const adminUsersPanel = el("admin-users-panel");
    const adminCreatePanel = el("admin-create-user-panel");
    const userDetailPanel = el("user-detail-panel");
    const permissionEditorPanel = el("permission-editor-panel");
    const auditLogPanel = el("audit-log-panel");

    await loadRegistrationRequestsPanel();
    await loadInvitationsPanel();
    await loadCompanyInfoPanel();
    await loadChannelPolicySettingsPanel();
    await loadNotificationPreferencePanel();

    if (!isSuperAdmin()) {
      adminUsersPanel.hidden = true;
      adminCreatePanel.hidden = true;
      userDetailPanel.hidden = true;
      permissionEditorPanel.hidden = true;
      auditLogPanel.hidden = true;
      return;
    }

    adminUsersPanel.hidden = false;
    adminCreatePanel.hidden = false;
    userDetailPanel.hidden = true; // 사용자가 "상세"를 누를 때만 표시
    permissionEditorPanel.hidden = false;
    auditLogPanel.hidden = false;

    await loadAdminUsersTable();
    await loadPermissionEditorPanel();
    await loadAuditLogPanel();
  }

  // --------------------------------------------------
  // 회사 정보 — SUPER_ADMIN 전용, PUT /companies/{id}로 회사명만 변경.
  // 서버가 실제로 200/403을 반환하는 결과에 따라 표시 여부를 정한다
  // (reactive hide — client-side 역할 표기만 믿지 않는다).
  //
  // 2026-08-04 V6 Gate 1A 보안 보완: 기존 회사명은 민감정보일 수 있으므로
  // 입력창에 절대 prefill하지 않는다(값을 아예 화면에 표시하지 않고,
  // 존재 여부만 알린다). 새 회사명은 사용자가 빈 입력창에 직접 입력해야
  // 하고, 제출 전 현재 비밀번호로 recent-auth 토큰을 먼저 받아야 한다.
  // --------------------------------------------------

  let currentCompanyId = null;
  let channelPolicySettingsVersion = 0;

  async function loadCompanyInfoPanel() {
    const panel = el("company-info-panel");

    let companies;
    try {
      companies = await apiFetch("/companies/");
    } catch (_) {
      panel.hidden = true;
      currentCompanyId = null;
      return;
    }

    if (!companies || companies.length === 0) {
      panel.hidden = true;
      currentCompanyId = null;
      return;
    }

    currentCompanyId = companies[0].id;
    panel.hidden = false;
  }

  // --------------------------------------------------
  // CP-2(2026-08-21) — 채널 정책·수익성 회사 설정. 정책 적합성
  // (channel_policy_rules)과 완전히 분리된 축이라는 것을 화면에서도
  // 그대로 유지한다 — 이 패널은 오직 마진 판정 기준값만 다룬다.
  // ADMIN 권한이면 서버가 200/PUT 성공을 반환한다 — reactive hide.
  // --------------------------------------------------

  async function loadChannelPolicySettingsPanel() {
    const panel = el("channel-policy-settings-panel");

    let settings;
    try {
      settings = await apiFetch("/channel-policy/settings");
    } catch (_) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;

    const setNum = (id, value) => { el(id).value = (value === null || value === undefined) ? "" : value; };

    if (settings) {
      channelPolicySettingsVersion = settings.version;
      setNum("cps-min-target-margin-rate", settings.min_target_margin_rate);
      setNum("cps-min-profit-per-order", settings.min_profit_per_order);
      setNum("cps-max-initial-purchase-amount", settings.max_initial_purchase_amount);
      setNum("cps-max-moq", settings.max_moq);
      setNum("cps-max-lead-time-days", settings.max_lead_time_days);
      setNum("cps-max-return-shipping-cost", settings.max_return_shipping_cost);
      setNum("cps-safety-stock-buffer", settings.safety_stock_buffer);
      el("cps-allowed-categories").value = (settings.allowed_categories || []).join("\n");
      el("cps-forbidden-categories").value = (settings.forbidden_categories || []).join("\n");
    } else {
      // 아직 설정이 없다 — expected_version=0으로 최초 생성해야 한다.
      channelPolicySettingsVersion = 0;
    }
  }

  // --------------------------------------------------
  // Gate PT-3(2026-08-23 17차 지시) — 중앙 운영 알림 설정
  // --------------------------------------------------

  async function loadNotificationPreferencePanel() {
    let pref;
    try {
      pref = await apiFetch("/notifications/preference");
    } catch (_) {
      return; // 인증 전이면 조용히 넘어간다(다른 설정 패널과 동일한 방어)
    }
    el("np-email-enabled").checked = !!pref.email_enabled;
    el("np-quiet-start").value = pref.quiet_hours_start === null || pref.quiet_hours_start === undefined ? "" : pref.quiet_hours_start;
    el("np-quiet-end").value = pref.quiet_hours_end === null || pref.quiet_hours_end === undefined ? "" : pref.quiet_hours_end;
    el("np-status").textContent = "";
    el("np-error").textContent = "";
  }

  const NOTIF_SEVERITY_LABEL_KEY = {
    CRITICAL: "settings.notification.severity_critical",
    HIGH: "settings.notification.severity_high",
    MEDIUM: "settings.notification.severity_medium",
    LOW: "settings.notification.severity_low",
  };

  async function openNotificationCatalogDialog() {
    const dialog = el("notif-catalog-dialog");
    const listEl = el("notif-catalog-list");
    const errorEl = el("notif-catalog-error");
    errorEl.textContent = "";
    listEl.innerHTML = `<p class="loading-text" data-i18n="common.loading">불러오는 중…</p>`;
    dialog.showModal();

    try {
      const [rows, preferences] = await Promise.all([
        apiFetch("/notifications/catalog"),
        apiFetch("/notifications/event-preferences"),
      ]);
      const preferenceByCode = new Map(preferences.map((p) => [p.event_code, p]));
      if (rows.length === 0) {
        listEl.innerHTML = `<p class="loading-text">-</p>`;
      } else {
        listEl.innerHTML = rows.map((r) => {
          const severityKey = NOTIF_SEVERITY_LABEL_KEY[r.severity] || r.severity;
          const wiredKey = r.wired ? "settings.notification.wired_yes" : "settings.notification.wired_no";
          const pref = preferenceByCode.get(r.event_code);
          const enabled = !pref || pref.enabled;
          return `
            <label class="notif-catalog-row" style="display:flex; gap:8px; align-items:center; padding:8px 0; min-height:44px; border-bottom:1px solid var(--border-color, #333); cursor:${r.critical_cannot_disable ? "default" : "pointer"};">
              <input type="checkbox" data-notif-event-toggle="${escapeHtml(r.event_code)}"
                ${enabled ? "checked" : ""} ${r.critical_cannot_disable ? "disabled" : ""}
                aria-label="${escapeHtml(r.description_ko)}">
              <span class="pill neutral">${escapeHtml(HomezI18n.t(severityKey))}</span>
              <span style="flex:1;">${escapeHtml(HomezI18n.getLocale() === "en-US" ? r.description_en : r.description_ko)}</span>
              <span class="stat-sub">${escapeHtml(HomezI18n.t(wiredKey))}</span>
            </label>`;
        }).join("");
        listEl.querySelectorAll("[data-notif-event-toggle]").forEach((input) => {
          input.addEventListener("change", async () => {
            input.disabled = true;
            try {
              await apiFetch(`/notifications/event-preferences/${encodeURIComponent(input.dataset.notifEventToggle)}`, {
                method: "PUT",
                body: JSON.stringify({ enabled: input.checked }),
              });
              toast(HomezI18n.t("settings.notification.saved"), "success");
            } catch (err) {
              input.checked = !input.checked;
              errorEl.textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
            } finally {
              input.disabled = false;
            }
          });
        });
      }
    } catch (err) {
      errorEl.textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
    }

    const closeBtn = el("notif-catalog-close");
    const onClose = () => { dialog.close(); closeBtn.removeEventListener("click", onClose); };
    closeBtn.addEventListener("click", onClose);
  }

  function initNotificationPreferenceForm() {
    el("notif-pref-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitBtn = el("np-submit");
      const errorEl = el("np-error");
      const statusEl = el("np-status");
      errorEl.textContent = "";
      statusEl.textContent = "";
      submitBtn.disabled = true;

      const startRaw = el("np-quiet-start").value;
      const endRaw = el("np-quiet-end").value;

      try {
        await apiFetch("/notifications/preference", {
          method: "PUT",
          body: JSON.stringify({
            email_enabled: el("np-email-enabled").checked,
            quiet_hours_start: startRaw === "" ? null : Number(startRaw),
            quiet_hours_end: endRaw === "" ? null : Number(endRaw),
            clear_quiet_hours: startRaw === "" && endRaw === "",
          }),
        });
        statusEl.textContent = HomezI18n.t("settings.notification.saved");
        toast(HomezI18n.t("settings.notification.saved"), "success");
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      } finally {
        submitBtn.disabled = false;
      }
    });

    el("np-send-test").addEventListener("click", async () => {
      const toEmail = el("np-test-email").value.trim();
      const statusEl = el("np-test-status");
      if (!toEmail) {
        statusEl.textContent = HomezI18n.t("purchase_task.test_email_required_error");
        return;
      }
      try {
        const result = await apiFetch("/notifications/preference/test-email", {
          method: "POST",
          body: JSON.stringify({ to_email: toEmail }),
        });
        statusEl.textContent = result.status === "PROVIDER_NOT_CONFIGURED"
          ? HomezI18n.t("purchase_task.email_provider_not_configured")
          : HomezI18n.t("purchase_task.test_email_sent", { status: result.status });
      } catch (err) {
        statusEl.textContent = (err && err.message) || HomezI18n.t("ma.error_generic");
      }
    });

    el("np-open-catalog").addEventListener("click", () => openNotificationCatalogDialog());
  }

  // --------------------------------------------------
  // 가입 승인 대기 — SUPER_ADMIN 또는 USER_REGISTRATION_VIEW 권한
  // 보유자에게만 서버가 200을 반환한다(그 외에는 403) — 클라이언트는
  // 그 결과에 따라 패널을 보이거나 숨긴다(reactive hide).
  // --------------------------------------------------

  const REJECTION_REASON_KEYS = {
    INVALID_INVITATION: "settings.rejection_reason.invalid_invitation",
    DUPLICATE_ACCOUNT: "settings.rejection_reason.duplicate_account",
    POLICY_VIOLATION: "settings.rejection_reason.policy_violation",
    UNVERIFIED_IDENTITY: "settings.rejection_reason.unverified_identity",
    OTHER: "settings.rejection_reason.other",
  };

  async function loadRegistrationRequestsPanel() {
    const panel = el("registration-requests-panel");

    let rows;
    try {
      rows = await apiFetch("/account-registration/requests");
    } catch (_) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;
    renderRegistrationRequestsTable(rows);
  }

  function renderRegistrationRequestsTable(rows) {
    const wrap = el("registration-requests-table-wrap");

    if (rows.length === 0) {
      wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("settings.registration_requests.empty")}</p>`;
      return;
    }

    const roleOptions = () => `
      <option value="VIEWER">VIEWER</option>
      <option value="STAFF">STAFF</option>
      <option value="MANAGER">MANAGER</option>
      <option value="ADMIN">ADMIN</option>
      ${isSuperAdmin() ? '<option value="SUPER_ADMIN">SUPER_ADMIN</option>' : ""}
    `;

    const reasonOptions = () => Object.entries(REJECTION_REASON_KEYS)
      .map(([code, key]) => `<option value="${code}">${escapeHtml(HomezI18n.t(key))}</option>`).join("");

    const colDisplayName = HomezI18n.t("settings.registration_requests.col_display_name");
    const colEmail = HomezI18n.t("settings.registration_requests.col_email");
    const colRequestedAt = HomezI18n.t("settings.registration_requests.col_requested_at");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colDisplayName}</th><th>${colEmail}</th><th>${colRequestedAt}</th><th></th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr>
            <td data-label="${colDisplayName}">${escapeHtml(r.display_name)}</td>
            <td data-label="${colEmail}">${escapeHtml(r.masked_email)}</td>
            <td data-label="${colRequestedAt}">${escapeHtml(r.requested_at)}</td>
            <td>
              <select class="role-select-inline" data-approve-role-for="${r.id}">${roleOptions()}</select>
              <button class="btn btn-primary btn-sm" data-approve="${r.id}">${HomezI18n.t("settings.registration_requests.approve")}</button>
              <select class="role-select-inline" data-reject-reason-for="${r.id}">${reasonOptions()}</select>
              <button class="btn btn-danger btn-sm" data-reject="${r.id}">${HomezI18n.t("settings.registration_requests.reject")}</button>
            </td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("[data-approve]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const id = Number(btn.dataset.approve);
        const roleCode = wrap.querySelector(`[data-approve-role-for="${id}"]`).value;
        const isDangerous = roleCode === "SUPER_ADMIN" || roleCode === "ADMIN";

        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("settings.registration_requests.approve_confirm_title"),
          body: HomezI18n.t(
            isDangerous
              ? "settings.registration_requests.approve_confirm_body_dangerous"
              : "settings.registration_requests.approve_confirm_body",
            { role: roleCode },
          ),
          okLabel: HomezI18n.t("settings.registration_requests.approve_confirm_ok"),
        });
        if (!confirmed) return;

        try {
          await apiFetch(`/account-registration/${id}/approve`, {
            method: "POST",
            body: JSON.stringify({ role_code: roleCode }),
          });
          toast(HomezI18n.t("settings.registration_requests.approve_success"), "success");
          loadRegistrationRequestsPanel();
        } catch (err) {
          toast(err.message || HomezI18n.t("settings.registration_requests.approve_error"), "error");
        }
      }));
    });

    wrap.querySelectorAll("[data-reject]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const id = Number(btn.dataset.reject);
        const reasonCode = wrap.querySelector(`[data-reject-reason-for="${id}"]`).value;

        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("settings.registration_requests.reject_confirm_title"),
          body: HomezI18n.t("settings.registration_requests.reject_confirm_body"),
          okLabel: HomezI18n.t("settings.registration_requests.reject_confirm_ok"),
        });
        if (!confirmed) return;

        try {
          await apiFetch(`/account-registration/${id}/reject`, {
            method: "POST",
            body: JSON.stringify({ reason_code: reasonCode }),
          });
          toast(HomezI18n.t("settings.registration_requests.reject_success"), "success");
          loadRegistrationRequestsPanel();
        } catch (err) {
          toast(err.message || HomezI18n.t("settings.registration_requests.reject_error"), "error");
        }
      }));
    });
  }

  // --------------------------------------------------
  // 초대 코드 관리
  // --------------------------------------------------

  async function loadInvitationsPanel() {
    const panel = el("invitations-panel");

    let rows;
    try {
      rows = await apiFetch("/account-registration/invitations");
    } catch (_) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;
    el("invitation-reveal").hidden = true;
    renderInvitationsTable(rows);
  }

  function renderInvitationsTable(rows) {
    const wrap = el("invitations-table-wrap");

    if (rows.length === 0) {
      wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("settings.invitations.empty")}</p>`;
      return;
    }

    const colMaxRole = HomezI18n.t("settings.invitations.col_max_role");
    const colExpires = HomezI18n.t("settings.invitations.col_expires");
    const colUsed = HomezI18n.t("settings.invitations.col_used");
    const colStatus = HomezI18n.t("settings.invitations.col_status");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colMaxRole}</th><th>${colExpires}</th><th>${colUsed}</th><th>${colStatus}</th><th></th></tr></thead>
        <tbody>${rows.map((inv) => `
          <tr>
            <td data-label="${colMaxRole}">${escapeHtml(inv.max_role_code)}</td>
            <td data-label="${colExpires}">${escapeHtml(inv.expires_at)}</td>
            <td data-label="${colUsed}">${inv.used_count}/${inv.max_uses}</td>
            <td data-label="${colStatus}">${HomezI18n.t(inv.revoked ? "settings.invitations.status_revoked" : "settings.invitations.status_active")}</td>
            <td>
              ${!inv.revoked ? `<button class="btn btn-ghost btn-sm" data-revoke-invitation="${inv.id}">${HomezI18n.t("settings.invitations.revoke")}</button>` : ""}
            </td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("[data-revoke-invitation]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const id = Number(btn.dataset.revokeInvitation);

        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("settings.invitations.revoke_confirm_title"),
          body: HomezI18n.t("settings.invitations.revoke_confirm_body"),
          okLabel: HomezI18n.t("settings.invitations.revoke_confirm_ok"),
        });
        if (!confirmed) return;

        try {
          await apiFetch(`/account-registration/invitations/${id}`, { method: "DELETE" });
          toast(HomezI18n.t("settings.invitations.revoke_success"), "success");
          loadInvitationsPanel();
        } catch (err) {
          toast(err.message || HomezI18n.t("settings.invitations.revoke_error"), "error");
        }
      }));
    });
  }

  function initInvitationForm() {
    const form = el("create-invitation-form");
    const submitBtn = el("create-invitation-submit");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errorEl = el("create-invitation-error");
      errorEl.textContent = "";

      const maxRoleCode = el("inv-max-role").value;
      const isDangerous = maxRoleCode === "SUPER_ADMIN" || maxRoleCode === "ADMIN";

      const { confirmed } = await confirmDialog({
        title: HomezI18n.t("settings.invitations.create_confirm_title"),
        body: HomezI18n.t(
          isDangerous
            ? "settings.invitations.create_confirm_body_dangerous"
            : "settings.invitations.create_confirm_body",
          { role: maxRoleCode },
        ),
        okLabel: HomezI18n.t("settings.invitations.create_confirm_ok"),
      });
      if (!confirmed) return;

      submitBtn.disabled = true;

      try {
        const data = await apiFetch("/account-registration/invitations", {
          method: "POST",
          body: JSON.stringify({ max_role_code: maxRoleCode }),
        });
        el("invitation-code-value").textContent = data.invitation_code;
        el("invitation-reveal").hidden = false;
        toast(HomezI18n.t("settings.invitations.create_success"), "success");
        loadInvitationsPanel();
      } catch (err) {
        errorEl.textContent = err.message || HomezI18n.t("settings.invitations.create_error");
      } finally {
        submitBtn.disabled = false;
      }
    });

    el("btn-copy-invitation-code").addEventListener("click", async () => {
      const ok = await copyTextWithAutoClear(el("invitation-code-value").textContent);
      toast(HomezI18n.t(ok ? "common.copy_success" : "common.copy_failure"), ok ? "success" : "error");
    });
  }

  async function loadAdminUsersTable() {
    const wrap = el("admin-users-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      rows = await apiFetch("/admin/users");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    const me = getCurrentUser();
    const colStatus = HomezI18n.t("settings.admin_users.col_status");
    const colLastLogin = HomezI18n.t("settings.admin_users.col_last_login");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>username</th><th>email</th><th>role</th><th>${colStatus}</th><th>${colLastLogin}</th><th></th></tr></thead>
        <tbody>${rows.map((u) => `
          <tr>
            <td data-label="username">${escapeHtml(u.username)}</td>
            <td data-label="email">${escapeHtml(u.email)}</td>
            <td data-label="role">${escapeHtml(u.role || "—")}</td>
            <td data-label="${colStatus}"><span class="status-tag status-${u.is_active ? "APPROVED" : "REJECTED"}">${HomezI18n.t(u.is_active ? "settings.admin_users.status_active" : "settings.admin_users.status_inactive")}</span></td>
            <td data-label="${colLastLogin}">${u.last_login_at ? escapeHtml(u.last_login_at) : HomezI18n.t("settings.admin_users.last_login_never")}</td>
            <td>
              <button class="btn btn-ghost btn-sm" data-view-detail="${u.id}">${HomezI18n.t("settings.admin_users.view_detail")}</button>
              <button class="btn btn-ghost btn-sm" data-toggle-active="${u.id}" data-next="${!u.is_active}" ${u.id === me.id && u.is_active ? "disabled" : ""}>
                ${HomezI18n.t(u.is_active ? "settings.admin_users.deactivate" : "settings.admin_users.activate")}
              </button>
              ${u.id !== me.id ? `<button class="btn btn-ghost btn-sm" data-init-reset="${u.id}">${HomezI18n.t("settings.admin_users.init_reset")}</button>` : ""}
              ${u.id !== me.id ? `
                <select class="role-select-inline" data-role-select-for="${u.id}">
                  <option value="VIEWER" ${u.role === "VIEWER" ? "selected" : ""}>VIEWER</option>
                  <option value="STAFF" ${u.role === "STAFF" ? "selected" : ""}>STAFF</option>
                  <option value="MANAGER" ${u.role === "MANAGER" ? "selected" : ""}>MANAGER</option>
                  <option value="ADMIN" ${u.role === "ADMIN" ? "selected" : ""}>ADMIN</option>
                  ${isSuperAdmin() ? `<option value="SUPER_ADMIN" ${u.role === "SUPER_ADMIN" ? "selected" : ""}>SUPER_ADMIN</option>` : ""}
                </select>
                <button class="btn btn-ghost btn-sm" data-assign-role="${u.id}">${HomezI18n.t("settings.admin_users.assign_role")}</button>
              ` : ""}
            </td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    wrap.querySelectorAll("[data-view-detail]").forEach((btn) => {
      btn.addEventListener("click", () => showUserDetail(Number(btn.dataset.viewDetail)));
    });

    wrap.querySelectorAll("[data-assign-role]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const userId = Number(btn.dataset.assignRole);
        const select = wrap.querySelector(`[data-role-select-for="${userId}"]`);
        const roleCode = select.value;
        const isDangerous = roleCode === "SUPER_ADMIN" || roleCode === "ADMIN";

        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("settings.admin_users.assign_role_confirm_title"),
          body: HomezI18n.t(
            isDangerous
              ? "settings.admin_users.assign_role_confirm_body_dangerous"
              : "settings.admin_users.assign_role_confirm_body",
            { role: roleCode },
          ),
          okLabel: HomezI18n.t("settings.admin_users.assign_role_confirm_ok"),
        });
        if (!confirmed) return;

        try {
          await apiFetch(`/account-registration/${userId}/assign-role`, {
            method: "POST",
            body: JSON.stringify({ role_code: roleCode }),
          });
          toast(HomezI18n.t("settings.admin_users.assign_role_success"), "success");
          loadAdminUsersTable();
        } catch (err) {
          toast(err.message || HomezI18n.t("settings.admin_users.assign_role_error"), "error");
        }
      }));
    });

    wrap.querySelectorAll("[data-init-reset]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const userId = Number(btn.dataset.initReset);

        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("settings.admin_users.init_reset_confirm_title"),
          body: HomezI18n.t("settings.admin_users.init_reset_confirm_body"),
          okLabel: HomezI18n.t("settings.admin_users.init_reset_confirm_ok"),
        });
        if (!confirmed) return;

        try {
          const data = await apiFetch("/account-recovery/admin/initiate-reset", {
            method: "POST",
            body: JSON.stringify({ user_id: userId }),
          });
          el("admin-reset-token-value").textContent = data.reset_token;
          el("admin-reset-token-reveal").hidden = false;
          toast(HomezI18n.t("settings.admin_users.init_reset_success"), "success");
        } catch (err) {
          toast(err.message || HomezI18n.t("settings.admin_users.init_reset_error"), "error");
        }
      }));
    });

    el("btn-copy-admin-reset-token").addEventListener("click", async () => {
      const ok = await copyTextWithAutoClear(el("admin-reset-token-value").textContent);
      toast(HomezI18n.t(ok ? "common.copy_success" : "common.copy_failure"), ok ? "success" : "error");
    });

    wrap.querySelectorAll("[data-toggle-active]").forEach((btn) => {
      btn.addEventListener("click", () => withButtonGuard(btn, async () => {
        const userId = Number(btn.dataset.toggleActive);
        const nextActive = btn.dataset.next === "true";

        const { confirmed } = await confirmDialog({
          title: HomezI18n.t(nextActive ? "settings.admin_users.toggle_active_confirm_title_activate" : "settings.admin_users.toggle_active_confirm_title_deactivate"),
          body: HomezI18n.t(nextActive ? "settings.admin_users.toggle_active_confirm_body_activate" : "settings.admin_users.toggle_active_confirm_body_deactivate"),
          okLabel: HomezI18n.t(nextActive ? "settings.admin_users.toggle_active_confirm_ok_activate" : "settings.admin_users.toggle_active_confirm_ok_deactivate"),
        });
        if (!confirmed) return;

        try {
          await apiFetch(`/admin/users/${userId}/active`, {
            method: "PATCH",
            body: JSON.stringify({ active: nextActive }),
          });
          toast(HomezI18n.t("settings.admin_users.toggle_active_success"), "success");
          loadAdminUsersTable();
        } catch (err) {
          toast(err.message || HomezI18n.t("settings.admin_users.toggle_active_error"), "error");
        }
      }));
    });
  }

  // --------------------------------------------------
  // 사용자 상세 — 기본정보/회사/역할/Permission/세션수/최근 보안 이벤트.
  // Credential·비밀번호 해시는 서버 응답 자체에 없다(app/core/
  // account_admin.py::AdminUserDetail 계약).
  // --------------------------------------------------

  async function showUserDetail(userId) {
    const panel = el("user-detail-panel");
    const body = el("user-detail-body");
    panel.hidden = false;
    body.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    let detail;
    try {
      detail = await apiFetch(`/admin/users/${userId}`);
    } catch (err) {
      renderErrorState(body, err);
      return;
    }

    const events = detail.recent_security_events.length
      ? `<ul class="audit-event-list">${detail.recent_security_events.map((e) => `<li><strong>${escapeHtml(e.action)}</strong> — ${escapeHtml(e.description)}</li>`).join("")}</ul>`
      : `<p class="stat-sub">${HomezI18n.t("settings.user_detail.no_events")}</p>`;

    body.innerHTML = `
      <dl class="kv-list">
        <dt>username</dt><dd>${escapeHtml(detail.username)}</dd>
        <dt>email</dt><dd>${escapeHtml(detail.email)}</dd>
        <dt>${HomezI18n.t("settings.user_detail.role")}</dt><dd>${escapeHtml(detail.role || "—")}</dd>
        <dt>${HomezI18n.t("settings.user_detail.company_id")}</dt><dd>${detail.company_id ?? "—"}</dd>
        <dt>${HomezI18n.t("settings.admin_users.col_status")}</dt><dd>${HomezI18n.t(detail.is_active ? "settings.admin_users.status_active" : "settings.admin_users.status_inactive")}</dd>
        <dt>${HomezI18n.t("settings.admin_users.col_last_login")}</dt><dd>${detail.last_login_at ? escapeHtml(detail.last_login_at) : HomezI18n.t("settings.admin_users.last_login_never")}</dd>
        <dt>${HomezI18n.t("settings.user_detail.active_sessions")}</dt><dd>${detail.active_session_count ?? HomezI18n.t("settings.user_detail.unavailable")}</dd>
        <dt>${HomezI18n.t("settings.user_detail.permissions")}</dt><dd>${detail.permission_codes.length ? detail.permission_codes.map(escapeHtml).join(", ") : "—"}</dd>
      </dl>
      <h3>${HomezI18n.t("settings.user_detail.recent_events")}</h3>
      ${events}
    `;
  }

  // --------------------------------------------------
  // 역할별 Permission 편집 — 그룹화된 카탈로그 + Preset(편의) + 개별
  // 체크박스 + recent-auth + 단발성 nonce + 낙관적 동시성(expected_codes).
  // Preset은 여기서 체크박스만 미리 켜고 끌 뿐, 서버에는 항상 실제
  // 체크된 개별 코드 목록만 보낸다 — 서버는 Preset 이름을 모른다.
  // --------------------------------------------------

  let permissionCatalogCache = null;
  let currentRolePermissionState = null; // { roleId, roleCode, expectedCodes: Set }

  const RISKY_BADGE = () => `<span class="status-tag status-REJECTED" data-i18n="settings.permission_editor.risky_badge">${HomezI18n.t("settings.permission_editor.risky_badge")}</span>`;

  // Preset은 개별 Permission 코드 문자열을 전혀 몰라야 한다(알면
  // 서버 403과 별개인 "그림자 권한 로직"이 생길 위험 — 이 화면이
  // 실제로 저장하는 값은 항상 체크된 개별 코드일 뿐, Preset 이름조차
  // 서버에 전달하지 않는다). 카탈로그가 이미 내려주는 group/action/
  // risky 분류만으로 필터링한다.
  const permissionsFlat = (catalog) => catalog.flatMap((g) => g.permissions.map((p) => ({ ...p, group: g.group })));

  function presetCodesFor(presetKey, catalog) {
    const flat = permissionsFlat(catalog);

    if (presetKey === "view_only") {
      return new Set(flat.filter((p) => p.action === "view").map((p) => p.code));
    }
    if (presetKey === "product_staff") {
      return new Set(flat.filter((p) =>
        ["product", "category", "brand", "inventory"].includes(p.group) ||
        (p.group === "listing_wizard" && ["view", "create", "edit"].includes(p.action))
      ).map((p) => p.code));
    }
    if (presetKey === "approval_manager") {
      return new Set(flat.filter((p) =>
        p.group === "listing_wizard" && ["view", "approve", "submit", "retry"].includes(p.action)
      ).map((p) => p.code));
    }
    if (presetKey === "full_operator") {
      return new Set(flat.filter((p) => p.action !== "economics").map((p) => p.code));
    }
    return new Set();
  }

  function renderPermissionCatalog(catalog, checkedCodes) {
    const wrap = el("pe-catalog-wrap");
    const locale = HomezI18n.getLocale ? HomezI18n.getLocale() : "ko-KR";
    const isKo = locale.startsWith("ko");

    wrap.innerHTML = catalog.map((group) => `
      <fieldset class="pe-group">
        <legend>${escapeHtml(isKo ? group.label_ko : group.label_en)}</legend>
        ${group.permissions.map((p) => `
          <label class="pe-checkbox-row">
            <input type="checkbox" data-pe-code="${escapeHtml(p.code)}" ${checkedCodes.has(p.code) ? "checked" : ""}>
            <span>${escapeHtml(isKo ? p.name_ko : p.name_en)}</span>
            ${p.risky ? RISKY_BADGE() : ""}
            <span class="field-hint">${escapeHtml(isKo ? p.impact_ko : p.impact_en)}</span>
          </label>
        `).join("")}
      </fieldset>
    `).join("");
  }

  function getCheckedPermissionCodes() {
    return new Set(
      Array.from(el("pe-catalog-wrap").querySelectorAll("[data-pe-code]:checked"))
        .map((input) => input.dataset.peCode),
    );
  }

  async function loadRolePermissionsIntoEditor(roleId) {
    const noteEl = el("pe-super-admin-note");
    const areaEl = el("pe-editable-area");
    const errorEl = el("pe-error");
    errorEl.textContent = "";

    let data;
    try {
      data = await apiFetch(`/admin/roles/${roleId}/permissions`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        noteEl.hidden = false;
        areaEl.hidden = true;
        return;
      }
      errorEl.textContent = err.message || HomezI18n.t("settings.permission_editor.error_load");
      return;
    }

    noteEl.hidden = true;
    areaEl.hidden = false;

    if (!permissionCatalogCache) {
      permissionCatalogCache = await apiFetch("/admin/permissions/catalog");
    }

    currentRolePermissionState = {
      roleId,
      roleCode: data.role_code,
      expectedCodes: new Set(data.codes),
      nonce: data.edit_nonce,
    };

    el("pe-preset-select").value = "";
    renderPermissionCatalog(permissionCatalogCache, currentRolePermissionState.expectedCodes);
  }

  async function loadPermissionEditorPanel() {
    const roleSelect = el("pe-role-select");

    let roles;
    try {
      roles = await apiFetch("/admin/roles");
    } catch (err) {
      el("permission-editor-panel").hidden = true;
      return;
    }

    roleSelect.innerHTML = roles.map((r) => `<option value="${r.id}">${escapeHtml(r.code)} — ${escapeHtml(r.name)}</option>`).join("");

    if (roles.length > 0) {
      await loadRolePermissionsIntoEditor(roles[0].id);
    }
  }

  // --------------------------------------------------
  // 감사 이력
  // --------------------------------------------------

  async function loadAuditLogPanel() {
    const wrap = el("audit-log-table-wrap");
    wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let rows;
    try {
      rows = await apiFetch("/admin/audit-logs?limit=50");
    } catch (err) {
      renderErrorState(wrap, err);
      return;
    }

    if (rows.length === 0) {
      wrap.innerHTML = `<p class="loading-text">${HomezI18n.t("settings.audit_log.empty")}</p>`;
      return;
    }

    const colAction = HomezI18n.t("settings.audit_log.col_action");
    const colEntity = HomezI18n.t("settings.audit_log.col_entity");
    const colDescription = HomezI18n.t("settings.audit_log.col_description");

    wrap.innerHTML = `
      <table class="responsive-cards">
        <thead><tr><th>${colAction}</th><th>${colEntity}</th><th>${colDescription}</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr>
            <td data-label="${colAction}">${escapeHtml(r.action)}</td>
            <td data-label="${colEntity}">${escapeHtml(r.entity)}:${escapeHtml(r.entity_id)}</td>
            <td data-label="${colDescription}">${escapeHtml(r.description)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;
  }

  function initAccountSecurityForms() {
    const cpForm = el("change-password-form");
    const cpNew = el("cp-new");
    cpNew.addEventListener("input", () => renderPasswordPolicyChecklist(el("cp-policy-checklist"), cpNew.value));

    cpForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errorEl = el("cp-error");
      errorEl.textContent = "";

      const currentPassword = el("cp-current").value;
      const newPassword = cpNew.value;
      const newConfirm = el("cp-new-confirm").value;

      if (!passwordMeetsPolicy(newPassword)) {
        errorEl.textContent = HomezI18n.t("settings.password.error_policy");
        return;
      }
      if (newPassword !== newConfirm) {
        errorEl.textContent = HomezI18n.t("settings.password.error_mismatch");
        return;
      }

      const submitBtn = el("cp-submit");
      submitBtn.disabled = true;

      try {
        await apiFetch("/auth/change-password", {
          method: "POST",
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
            new_password_confirmation: newConfirm,
          }),
        });

        // 서버가 이 요청을 포함한 모든 세션을 이미 폐기했다 — 다음 API
        // 호출을 기다리지 않고 바로 로그인 화면으로 전환한다.
        authGeneration += 1;
        stopTopbarPolling();
        clearSession();
        toast(HomezI18n.t("settings.password.success"), "success");
        showLoginGate("");
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("settings.password.error_generic");
        submitBtn.disabled = false;
      }
    });

    // 2026-08-04 V6 Gate 1A 보안 보완: 회사명 변경 전 현재 비밀번호로
    // recent-auth 토큰을 먼저 받아야 한다(5분·1회용) — 기존 PUT
    // 엔드포인트를 그냥 연결하기만 한 상태로 두지 않는다. 성공/실패
    // 관계없이 비밀번호·새 회사명 입력값은 즉시 초기화한다.
    const companyNameForm = el("company-name-form");
    companyNameForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errorEl = el("company-name-error");
      const currentPasswordEl = el("company-name-current-password");
      const newNameEl = el("company-name-input");
      errorEl.textContent = "";

      const currentPassword = currentPasswordEl.value;
      const newName = newNameEl.value.trim();

      if (!currentPassword) {
        errorEl.textContent = HomezI18n.t("settings.company.error_current_password_required");
        return;
      }
      if (!newName) {
        errorEl.textContent = HomezI18n.t("settings.company.error_new_name_required");
        return;
      }
      if (currentCompanyId === null) {
        errorEl.textContent = HomezI18n.t("settings.company.error_company_not_loaded");
        return;
      }

      const submitBtn = el("company-name-submit");
      submitBtn.disabled = true;

      try {
        // recent-auth 자체의 실패(비밀번호 오류·잠금)는 "세션이
        // 무효하다"는 뜻이 아니다 — 이미 로그인된 본인이 재확인
        // 절차에서 한 번 틀린 것뿐이므로, 중앙 인증 실패 처리(강제
        // 로그아웃)를 절대 거치면 안 된다.
        let recentAuth;
        try {
          recentAuth = await apiFetch("/auth/recent-auth", {
            method: "POST",
            body: JSON.stringify({ current_password: currentPassword }),
          }, { skipAuthHandling: true });
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            errorEl.textContent = HomezI18n.t("settings.company.error_invalid_password");
          } else if (err instanceof ApiError && err.status === 429) {
            errorEl.textContent = HomezI18n.t("settings.company.error_too_many_attempts");
          } else {
            errorEl.textContent = (err && err.message) || HomezI18n.t("settings.company.error_verify_generic");
          }
          return;
        }

        await apiFetch(`/companies/${currentCompanyId}`, {
          method: "PUT",
          headers: { "X-Recent-Auth-Token": recentAuth.recent_auth_token },
          body: JSON.stringify({ name: newName }),
        });
        toast(HomezI18n.t("settings.company.success"), "success");
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("settings.company.error_generic");
      } finally {
        currentPasswordEl.value = "";
        newNameEl.value = "";
        submitBtn.disabled = false;
      }
    });

    const channelPolicySettingsForm = el("channel-policy-settings-form");
    channelPolicySettingsForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errorEl = el("channel-policy-settings-error");
      errorEl.textContent = "";

      const numOrNull = (id) => {
        const raw = el(id).value.trim();
        return raw === "" ? null : Number(raw);
      };
      const linesOrEmpty = (id) => el(id).value.split("\n").map((s) => s.trim()).filter(Boolean);

      const submitBtn = el("channel-policy-settings-submit");
      submitBtn.disabled = true;
      try {
        const fresh = await apiFetch("/channel-policy/settings", {
          method: "PUT",
          body: JSON.stringify({
            expected_version: channelPolicySettingsVersion,
            min_target_margin_rate: numOrNull("cps-min-target-margin-rate"),
            min_profit_per_order: numOrNull("cps-min-profit-per-order"),
            max_initial_purchase_amount: numOrNull("cps-max-initial-purchase-amount"),
            max_moq: numOrNull("cps-max-moq"),
            max_lead_time_days: numOrNull("cps-max-lead-time-days"),
            max_return_shipping_cost: numOrNull("cps-max-return-shipping-cost"),
            safety_stock_buffer: numOrNull("cps-safety-stock-buffer"),
            allowed_categories: linesOrEmpty("cps-allowed-categories"),
            forbidden_categories: linesOrEmpty("cps-forbidden-categories"),
          }),
        });
        channelPolicySettingsVersion = fresh.version;
        toast(HomezI18n.t("settings.channel_policy.save_success"), "success");
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          errorEl.textContent = HomezI18n.t("settings.channel_policy.error_conflict");
          await loadChannelPolicySettingsPanel();
        } else {
          errorEl.textContent = err.message || HomezI18n.t("common.load_error");
        }
      } finally {
        submitBtn.disabled = false;
      }
    });

    el("btn-revoke-all-sessions").addEventListener("click", () => withButtonGuard(el("btn-revoke-all-sessions"), async () => {
      const { confirmed } = await confirmDialog({
        title: HomezI18n.t("settings.sessions.revoke_confirm_title"),
        body: HomezI18n.t("settings.sessions.revoke_confirm_body"),
        okLabel: HomezI18n.t("settings.sessions.revoke_confirm_ok"),
      });
      if (!confirmed) return;

      try {
        await apiFetch("/auth/sessions/revoke-all", { method: "POST" });
        authGeneration += 1;
        stopTopbarPolling();
        clearSession();
        toast(HomezI18n.t("settings.sessions.revoke_success"), "success");
        showLoginGate("");
      } catch (err) {
        toast(err.message || HomezI18n.t("settings.sessions.revoke_error"), "error");
      }
    }));

    const cuForm = el("create-user-form");
    cuForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const errorEl = el("cu-error");
      errorEl.textContent = "";

      const password = el("cu-password").value;
      const confirmValue = el("cu-password-confirm").value;

      if (password !== confirmValue) {
        errorEl.textContent = HomezI18n.t("settings.create_user.error_password_mismatch");
        return;
      }

      const submitBtn = el("cu-submit");
      submitBtn.disabled = true;

      try {
        await apiFetch("/admin/users", {
          method: "POST",
          body: JSON.stringify({
            username: el("cu-username").value.trim(),
            email: el("cu-email").value.trim(),
            password,
            password_confirmation: confirmValue,
            role_code: el("cu-role").value,
          }),
        });

        cuForm.reset();
        toast(HomezI18n.t("settings.create_user.success"), "success");
        loadAdminUsersTable();
      } catch (err) {
        errorEl.textContent = (err && err.message) || HomezI18n.t("settings.create_user.error_generic");
      } finally {
        submitBtn.disabled = false;
      }
    });

    el("pe-role-select").addEventListener("change", (ev) => {
      loadRolePermissionsIntoEditor(Number(ev.target.value));
    });

    el("pe-preset-select").addEventListener("change", (ev) => {
      if (!permissionCatalogCache || !ev.target.value) return;
      const codes = presetCodesFor(ev.target.value, permissionCatalogCache);
      el("pe-catalog-wrap").querySelectorAll("[data-pe-code]").forEach((input) => {
        input.checked = codes.has(input.dataset.peCode);
      });
    });

    el("pe-submit").addEventListener("click", async () => {
      const errorEl = el("pe-error");
      errorEl.textContent = "";

      if (!currentRolePermissionState) return;

      const newCodes = getCheckedPermissionCodes();
      const addedRisky = permissionCatalogCache
        .flatMap((g) => g.permissions)
        .filter((p) => p.risky && newCodes.has(p.code) && !currentRolePermissionState.expectedCodes.has(p.code));

      if (addedRisky.length > 0) {
        const locale = HomezI18n.getLocale ? HomezI18n.getLocale() : "ko-KR";
        const isKo = locale.startsWith("ko");
        const { confirmed } = await confirmDialog({
          title: HomezI18n.t("settings.permission_editor.risky_confirm_title"),
          body: HomezI18n.t("settings.permission_editor.risky_confirm_body", {
            list: addedRisky.map((p) => (isKo ? p.name_ko : p.name_en)).join(", "),
          }),
          okLabel: HomezI18n.t("settings.permission_editor.risky_confirm_ok"),
        });
        if (!confirmed) return;
      }

      const currentPasswordEl = el("pe-current-password");
      const currentPassword = currentPasswordEl.value;

      if (!currentPassword) {
        errorEl.textContent = HomezI18n.t("settings.company.error_current_password_required");
        return;
      }

      const submitBtn = el("pe-submit");
      submitBtn.disabled = true;

      try {
        let recentAuth;
        try {
          recentAuth = await apiFetch("/auth/recent-auth", {
            method: "POST",
            body: JSON.stringify({ current_password: currentPassword }),
          }, { skipAuthHandling: true });
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            errorEl.textContent = HomezI18n.t("settings.company.error_invalid_password");
          } else if (err instanceof ApiError && err.status === 429) {
            errorEl.textContent = HomezI18n.t("settings.company.error_too_many_attempts");
          } else {
            errorEl.textContent = (err && err.message) || HomezI18n.t("settings.company.error_verify_generic");
          }
          return;
        }

        await apiFetch(`/admin/roles/${currentRolePermissionState.roleId}/permissions`, {
          method: "PUT",
          headers: { "X-Recent-Auth-Token": recentAuth.recent_auth_token },
          body: JSON.stringify({
            expected_codes: Array.from(currentRolePermissionState.expectedCodes),
            new_codes: Array.from(newCodes),
            nonce: currentRolePermissionState.nonce,
          }),
        });

        toast(HomezI18n.t("settings.permission_editor.success"), "success");
        await loadRolePermissionsIntoEditor(currentRolePermissionState.roleId);
        await loadAuditLogPanel();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          errorEl.textContent = HomezI18n.t("settings.permission_editor.error_conflict");
          await loadRolePermissionsIntoEditor(currentRolePermissionState.roleId);
        } else {
          errorEl.textContent = (err && err.message) || HomezI18n.t("settings.permission_editor.error_generic");
        }
      } finally {
        currentPasswordEl.value = "";
        submitBtn.disabled = false;
      }
    });

    el("audit-log-refresh").addEventListener("click", () => loadAuditLogPanel());

    initRecoveryCodesGeneration();
    initNotificationPreferenceForm();
  }

  // --------------------------------------------------
  // 복구 코드 생성(로그인 상태 본인 발급) — 설정 화면
  // --------------------------------------------------

  async function copyTextWithAutoClear(text, clearAfterMs = 30000) {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      return false;
    }
    try {
      await navigator.clipboard.writeText(text);
      setTimeout(async () => {
        try {
          const current = await navigator.clipboard.readText();
          if (current === text) {
            await navigator.clipboard.writeText("");
          }
        } catch (_) {
          /* 클립보드 읽기 권한이 없으면 자동 제거는 포기한다 — 값 자체는
             이미 사용자 화면에 노출된 것과 별개로, 이 시도 실패가
             기능을 막지는 않는다. */
        }
      }, clearAfterMs);
      return true;
    } catch (_) {
      return false;
    }
  }

  function initRecoveryCodesGeneration() {
    const generateBtn = el("btn-generate-recovery-codes");
    const reveal = el("recovery-codes-reveal");
    const listEl = el("recovery-codes-list");

    generateBtn.addEventListener("click", async () => {
      const { confirmed } = await confirmDialog({
        title: HomezI18n.t("settings.recovery_codes.confirm_title"),
        body: HomezI18n.t("settings.recovery_codes.confirm_body"),
        okLabel: HomezI18n.t("settings.recovery_codes.confirm_ok"),
      });
      if (!confirmed) return;

      generateBtn.disabled = true;
      try {
        const data = await apiFetch("/account-recovery/recovery-codes", { method: "POST" });
        listEl.textContent = data.codes.join("\n");
        reveal.hidden = false;
        toast(HomezI18n.t("settings.recovery_codes.success"), "success");
      } catch (err) {
        toast(err.message || HomezI18n.t("settings.recovery_codes.error"), "error");
      } finally {
        generateBtn.disabled = false;
      }
    });

    el("btn-copy-recovery-codes").addEventListener("click", async () => {
      const ok = await copyTextWithAutoClear(listEl.textContent);
      toast(HomezI18n.t(ok ? "common.copy_success" : "common.copy_failure"), ok ? "success" : "error");
    });

    el("btn-download-recovery-codes").addEventListener("click", () => {
      const blob = new Blob([listEl.textContent], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "homez-recovery-codes.txt";
      a.click();
      URL.revokeObjectURL(url);
    });

    el("btn-print-recovery-codes").addEventListener("click", () => {
      const printWindow = window.open("", "_blank");
      if (!printWindow) return;
      printWindow.document.write(`<pre>${escapeHtml(listEl.textContent)}</pre>`);
      printWindow.document.close();
      printWindow.print();
    });
  }

  // --------------------------------------------------
  // 상단 알림 벨(Gate PT-2F) — notification_center의 통합 조회
  // 계약(/notifications/unified) 하나만 쓴다. 새 알림 저장소를
  // 만들지 않는다.
  // --------------------------------------------------

  function wireTopbarNotifications() {
    const btn = el("topbar-notifications-btn");
    const dropdown = el("topbar-notifications-dropdown");
    if (!btn || !dropdown) return;

    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = dropdown.hidden;
      dropdown.hidden = !willOpen;
      btn.setAttribute("aria-expanded", String(willOpen));
      if (willOpen) loadTopbarNotifications();
    });

    document.addEventListener("click", (ev) => {
      if (dropdown.hidden) return;
      if (dropdown.contains(ev.target) || btn.contains(ev.target)) return;
      dropdown.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    });

    el("topbar-notif-mark-all-read").addEventListener("click", () => withButtonGuard(
      el("topbar-notif-mark-all-read"),
      async () => {
        try {
          await apiFetch("/notifications/read-all", { method: "POST" });
          await loadTopbarNotifications();
          await refreshTopbarNotifBadge();
        } catch (err) {
          toast((err && err.message) || HomezI18n.t("common.error_generic"), "error");
        }
      },
    ));
  }

  async function refreshTopbarNotifBadge() {
    const badge = el("topbar-notif-badge");
    if (!badge) return;
    try {
      const result = await apiFetch("/notifications/unread-count");
      const count = result.unread_count || 0;
      if (count > 0) {
        badge.textContent = count > 99 ? "99+" : String(count);
        badge.hidden = false;
      } else {
        badge.hidden = true;
      }
    } catch (_err) {
      // 배지 갱신 실패는 조용히 무시한다(다음 폴링에서 재시도).
    }
  }

  function topbarNotifTargetView(linkPath) {
    if (!linkPath) return null;
    const [view, query] = linkPath.split("?");
    const opts = {};
    if (query) {
      new URLSearchParams(query).forEach((value, key) => {
        opts[key] = /^\d+$/.test(value) ? Number(value) : value;
      });
    }
    return { view, opts };
  }

  async function loadTopbarNotifications() {
    const listEl = el("topbar-notif-list");
    listEl.innerHTML = `<p class="loading-text">${HomezI18n.t("common.loading")}</p>`;

    let result;
    try {
      result = await apiFetch("/notifications/unified");
    } catch (err) {
      renderErrorState(listEl, err);
      return;
    }

    const items = result.items || [];
    if (items.length === 0) {
      renderEmptyState(listEl, HomezI18n.t("topbar.notifications_empty"), "");
      return;
    }

    listEl.innerHTML = items.map((it, idx) => `
      <button type="button" class="topbar-notif-item ${it.is_read ? "" : "unread"}" data-idx="${idx}">
        <div class="topbar-notif-title">
          ${it.level === "error" ? `<span class="risk-high">●</span>` : it.level === "warning" ? `<span class="risk-mid">●</span>` : ""}
          ${escapeHtml(it.title)}
        </div>
        <div class="topbar-notif-message">${escapeHtml(it.message)}</div>
        <div class="topbar-notif-meta">${it.created_at ? fmtDate(it.created_at) : ""}${it.action_status === "ACTION_REQUIRED" ? ` · ${escapeHtml(HomezI18n.t("topbar.action_required"))}` : ""}</div>
      </button>
    `).join("");

    listEl.querySelectorAll(".topbar-notif-item").forEach((btn) => {
      const it = items[Number(btn.dataset.idx)];
      btn.addEventListener("click", async () => {
        if (it.id > 0 && !it.is_read) {
          try {
            await apiFetch(`/notifications/${it.id}/read`, { method: "POST" });
            refreshTopbarNotifBadge();
          } catch (_err) {
            // 읽음 처리 실패해도 이동은 계속 진행한다.
          }
        }
        const target = topbarNotifTargetView(it.link_path);
        el("topbar-notifications-dropdown").hidden = true;
        el("topbar-notifications-btn").setAttribute("aria-expanded", "false");
        if (target) navigateTo(target.view, target.opts);
      });
    });
  }

  // --------------------------------------------------
  // 상단바 상태 폴링
  // --------------------------------------------------

  async function refreshTopbar() {
    refreshTopbarNotifBadge();
    try {
      const root = await fetch("/").then((r) => r.json());
      const badgeVersion = el("badge-version");
      badgeVersion.textContent = `V${root.version || "?"}`;
      badgeVersion.classList.add("ok");
    } catch (_) {
      /* 무시: 버전 표시는 부가 정보 */
    }

    const connBadge = el("badge-conn");
    try {
      const health = await fetch("/health").then((r) => r.json());
      const isHealthy = health && health.success === true && health.status === "healthy";
      connBadge.textContent = HomezI18n.t(isHealthy ? "topbar.conn_ok" : "topbar.conn_warn");
      connBadge.className = "pill " + (isHealthy ? "ok" : "warn");
    } catch (_) {
      connBadge.textContent = HomezI18n.t("topbar.conn_down");
      connBadge.className = "pill danger";
    }

    try {
      const sys = await apiFetch("/console/api/system-status");
      const schemaBadge = el("badge-schema");
      schemaBadge.textContent = HomezI18n.t(sys.v24_v3_schema_applied ? "topbar.schema_applied" : "topbar.schema_required");
      schemaBadge.className = "pill " + (sys.v24_v3_schema_applied ? "ok" : "warn");
    } catch (err) {
      /* 401은 apiFetch가 이미 로그인 화면으로 전환 처리함 — 그 외 오류만 배지에 반영 */
      if (!(err instanceof ApiError && err.status === 401)) {
        const schemaBadge = el("badge-schema");
        schemaBadge.textContent = HomezI18n.t("topbar.schema_check_failed");
        schemaBadge.className = "pill danger";
      }
    }

    try {
      const safety = await apiFetch("/console/api/safety-status");
      const modeBadge = el("badge-mode");
      const estopBadge = el("badge-estop");
      if (safety.schema_ready) {
        modeBadge.textContent = HomezI18n.t("topbar.mode_prefix", { mode: safety.mode });
        modeBadge.className = "pill ok";
        const active = safety.emergency_stop && safety.emergency_stop.is_active;
        estopBadge.textContent = HomezI18n.t(active ? "topbar.estop_active" : "topbar.estop_inactive");
        estopBadge.className = "pill " + (active ? "danger" : "ok");
      } else {
        modeBadge.textContent = HomezI18n.t("topbar.mode_schema_required");
        modeBadge.className = "pill warn";
        estopBadge.textContent = HomezI18n.t("topbar.estop_schema_required");
        estopBadge.className = "pill warn";
      }
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) {
        const modeBadge = el("badge-mode");
        const estopBadge = el("badge-estop");
        modeBadge.textContent = HomezI18n.t("topbar.mode_check_failed");
        modeBadge.className = "pill danger";
        estopBadge.textContent = HomezI18n.t("topbar.estop_check_failed");
        estopBadge.className = "pill danger";
      }
    }
  }

  function startTopbarPolling() {
    refreshTopbar();
    if (topbarPollTimer) clearInterval(topbarPollTimer);
    topbarPollTimer = setInterval(refreshTopbar, 20000);
  }

  // --------------------------------------------------
  // 초기화
  // --------------------------------------------------

  let appInitialized = false;

  // 시작 시 저장된 토큰이 실제로 아직 유효한지 서버에 확인한다.
  // pywebview/서버가 막 기동 중이라 일시적으로 응답이 없을 수 있으므로
  // (콜드 스타트 경쟁 조건, 2026-07-30 결함 수정과 동일한 종류의 타이밍
  // 문제) 네트워크 오류에는 즉시 포기하지 않고 짧게 재시도한다 — 그
  // 사이에는 토큰을 절대 지우지 않는다. 반환값: 응답 객체(유효, 2026-08-14
  // Gate F-2부터 — user/permissions를 담고 있어 Desktop 세션 복원 시
  // USER_KEY를 다시 채우는 데 쓴다)/false=서버가 명시적으로 무효 확인/
  // null=계속 연결할 수 없어 판단 불가.
  async function verifyExistingSessionWithRetry(maxAttempts = 3) {
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        return await apiFetch("/auth/verify", {}, { skipAuthHandling: true });
      } catch (err) {
        const isNetworkError = err instanceof ApiError && err.status === 0;
        if (!isNetworkError) {
          return false;
        }
        if (attempt < maxAttempts) {
          await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
        }
      }
    }
    return null;
  }

  async function init() {
    if (appInitialized) return; // console.js가 실수로 두 번 로드/실행돼도 재초기화하지 않는다.
    appInitialized = true;

    HomezI18n.applyToDom(document);
    initLanguageSwitchButtons();
    initLoginForm();
    initSetupForm();
    initCompanyRecoveryForm();
    initAccountRecoveryForms();
    initRegisterForm();
    initInvitationForm();
    initPasswordToggle();
    initNav();
    initAccountSecurityForms();
    initStoreConnectionWizard();
    initR2HostingPanel();
    initListingStatusSync();
    initV7OpsDashboard();
    initLimitedModeBanner();
    initTourMode();
    initTourOverlayControls();
    wireLocaleServerSync();

    await bootstrapDesktopTokenIfNeeded();
    await restoreDesktopConsoleSessionIfNeeded();

    // 최초 관리자 설정이 필요한지부터 확인한다 — users==0이면 로그인
    // 화면이 아니라 최초 설정 화면만 보여준다(로그인 폼 자체를 렌더링
    // 하지 않는 것은 아니고 DOM에는 있지만 hidden 상태를 유지한다).
    try {
      const setupStatus = await apiFetch("/desktop-setup/status");
      if (setupStatus.setup_required) {
        setupConfiguredEmail = setupStatus.configured_email || "";
        showSetupGate(setupConfiguredEmail);
        return;
      }
    } catch (_) {
      /* 상태 조회 실패(네트워크 등) — 안전하게 일반 로그인 화면으로 진행 */
    }

    // 2026-08-02 Gate R6-B: 관리자는 있지만(users>0) companies=0이고
    // 그 관리자가 아직 어느 회사에도 연결되지 않은 상태 — 일반 로그인
    // 화면이 아니라 회사 초기 설정 화면만 보여준다. 이 상태와 정상
    // 최초 설치(setup_required)는 서로 배타적이므로(위에서 setup_
    // required면 이미 return했음) 순서상 이 지점에는 항상 최대 하나의
    // 화면만 해당된다.
    try {
      const recoveryStatus = await apiFetch("/desktop-setup/company-recovery/status");
      if (recoveryStatus.recovery_required) {
        showCompanyRecoveryGate(recoveryStatus.admin_email || "");
        return;
      }
    } catch (_) {
      /* 상태 조회 실패(네트워크 등) — 안전하게 일반 로그인 화면으로 진행 */
    }

    const token = getAccessToken();

    if (!token) {
      showLoginGate("");
      return;
    }

    // 2026-07-30: 이전에는 localStorage에 토큰이 "있다"는 사실만으로
    // 곧바로 메인 화면(shell)을 표시했다 — 그 토큰이 이미 만료/로그아웃
    // 되었어도 실제 데이터 API가 401을 반환하기 전까지는 메뉴/레이아웃이
    // 잠깐 그대로 보이는 문제가 있었다. 화면을 전환하기 전에 서버에
    // 먼저 물어 확인한다("로그인 화면부터 시작"이라는 요구를 새로고침/
    // 재실행 시에도 안전하게 지킨다).
    //
    // 2026-08-03 결함 수정: 이전에는 이 확인이 "네트워크 오류든 진짜
    // 인증 실패든" 구분 없이 전부 토큰 삭제로 이어졌다 — 서버가 아직
    // 완전히 기동하지 않은 순간(콜드 스타트) 하나만으로 멀쩡한 로그인
    // 세션이 지워졌다. 이제 실제로 서버가 세션 무효를 확인해준 경우
    // (false)에만 토큰을 지운다. 계속 연결이 안 되는 경우(null)는
    // 토큰을 그대로 둔 채 로그인 화면을 보여준다 — 서버가 살아나면
    // 다음 실행/새로고침에서 같은 토큰으로 곧바로 재인증된다.
    const verifyResult = await verifyExistingSessionWithRetry();

    if (verifyResult === false) {
      clearSession();
      showLoginGate("");
      return;
    }

    if (verifyResult === null) {
      showLoginGate(HomezI18n.t("auth.server_unreachable"));
      return;
    }

    // 2026-08-14 Gate F-2 — verifyResult는 이제 /auth/verify 응답
    // 객체다(user/permissions 포함). Desktop 모드에서 토큰만 Credential
    // Manager에서 복원했을 때는 USER_KEY(사용자 프로필 echo, origin
    // 종속 localStorage라 재시작 시 사라짐)가 비어 있을 수 있으므로,
    // 매번 서버 응답으로 다시 채운다(로컬 캐시를 신뢰하지 않고 서버
    // 값으로 덮어쓴다 — 일반 브라우저 모드에서도 안전하게 최신화됨).
    if (verifyResult && verifyResult.user) {
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ ...(verifyResult.user || {}), permissions: verifyResult.permissions || [] }),
      );
    }

    settingsSyncEnabled = true;
    await syncServerBackedSettingsAfterAuth();

    showShell();
    startTopbarPolling();
    navigateTo(firstVisibleNavView());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
