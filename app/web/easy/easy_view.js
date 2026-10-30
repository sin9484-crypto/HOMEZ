/* =========================================================
   Homez OS — app/web/easy/easy_view.js

   2026-10-16 "쉬운 보기". 기존 화면 위에 얹는 단순한 보기(오늘 할 일 · 상품 · 주문·배송 · 정산·수익 · 승인 센터 · 더보기).
   - console.js의 `window.HomezEasyBridge`만 통해 기존 기능을 쓴다(내부 변수에 직접 닿지 않는다).
   - 새 판단을 만들지 않는다: 값은 서버 집계(`/easy-view/*`)와 기존 API가 이미 계산한 것을 읽어 보여 준다.
   - 화면을 여는 것만으로 쿠팡·온채널을 부르지 않는다. 바깥을 부르는 일은 버튼을 눌렀을 때만, 허용(승인 센터)을 거쳐 나간다.
   - 관리자만 켜진다(서버 집계가 관리자 전용). 끄면 기존 화면 그대로다(더보기 또는 계정 메뉴에서 켜고 끈다).
   ========================================================= */

(() => {
  "use strict";

  const B = window.HomezEasyBridge;
  if (!B) return; // console.js가 연결부를 만들지 못했으면(예: 오래된 캐시) 아무것도 바꾸지 않는다.

  // ---- 문구: 쉬운 보기 전용 문구를 기존 카탈로그에 합친다(같은 객체를 i18n.js가 읽는다). ----
  const TEXTS = window.HOMEZ_EASY_TEXTS || {};
  if (window.HOMEZ_I18N_CATALOG_KO_KR) Object.assign(window.HOMEZ_I18N_CATALOG_KO_KR, TEXTS["ko-KR"] || {});
  if (window.HOMEZ_I18N_CATALOG_EN_US) Object.assign(window.HOMEZ_I18N_CATALOG_EN_US, TEXTS["en-US"] || {});

  const T = (key, params) => HomezI18n.t(key, params);
  const esc = B.escapeHtml;
  const api = (path, options) => B.apiFetch(path, options);
  const $ = (id) => document.getElementById(id);

  const ICONS = {
    today: '<path d="M8 4h8M9 2h6v4H9zM6 4H5a1 1 0 00-1 1v15a1 1 0 001 1h14a1 1 0 001-1V5a1 1 0 00-1-1h-1M8 12h8M8 16h6"/>',
    box: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    truck: '<path d="M3 7h11v9H3zM14 10h4l3 3v3h-7M7 19a1.6 1.6 0 100-3 1.6 1.6 0 000 3zM17 19a1.6 1.6 0 100-3 1.6 1.6 0 000 3z"/>',
    money: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/>',
    shield: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3zM9 12l2 2 4-4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 00-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 00-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 00-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 005 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 002 1.2l.4 2.6h4l.4-2.6a7 7 0 002-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 114 2c-.9.6-1.5 1-1.5 2M12 17v.01"/>',
    chart: '<path d="M5 20V11M12 20V4M19 20v-6"/>',
    bag: '<path d="M6 7h12l1 13H5zM9 7a3 3 0 016 0"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-8 9"/>',
  };
  const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  const EASY_VIEWS = new Set(["easy-today", "easy-products", "easy-product-detail", "easy-orders", "easy-money", "easy-analysis", "easy-approvals", "easy-more"]);
  const NAV = [
    { id: "today", view: "easy-today", icon: "today", key: "easy.nav.today" },
    { id: "products", view: "easy-products", icon: "box", key: "easy.nav.products" },
    { id: "orders", view: "easy-orders", icon: "truck", key: "easy.nav.orders" },
    { id: "money", view: "easy-money", icon: "money", key: "easy.nav.money" },
    { id: "analysis", view: "easy-analysis", icon: "chart", key: "easy.nav.analysis" },
    { sep: true },
    { id: "approvals", view: "easy-approvals", icon: "shield", key: "easy.nav.approvals", badge: true },
    { spacer: true },
    { id: "more", view: "easy-more", icon: "gear", key: "easy.nav.more", sub: true },
    { id: "help", view: "guides", icon: "help", key: "easy.nav.help", sub: true },
  ];
  // 기존 화면이 속한 업무 → 쉬운 보기 메뉴 강조
  const SECTION_TO_NAV = { today: "today", products: "products", orders: "orders", money: "money", analysis: "analysis", settings: "more" };

  const S = { enabled: false, badge: 0, wizards: [], ordersCache: null, selected: {}, delIds: new Set() };

  // =========================================================
  // 공통 도우미
  // =========================================================

  function isAdmin() {
    const user = B.getCurrentUser();
    const role = (user && user.role) || "";
    return role === "ADMIN" || role === "SUPER_ADMIN";
  }

  const chip = (kind, text) => `<span class="ez-chip ${kind}">${esc(text)}</span>`;
  const dot = (kind, mark) => `<span class="ez-dot ${kind}" aria-hidden="true">${esc(mark)}</span>`;
  const loadingHtml = () => `<p class="ez-loading">${esc(T("common.loading"))}</p>`;
  const emptyHtml = (titleKey, subKey) => `<div class="ez-empty"><b>${esc(T(titleKey))}</b>${subKey ? esc(T(subKey)) : ""}</div>`;

  function thumbHtml(assetId, fallbackIconName) {
    if (assetId) return `<span class="ez-thumb">${B.lwThumbHtml({ thumbnail_asset_id: assetId })}</span>`;
    return `<span class="ez-thumb">${icon(fallbackIconName || "box")}</span>`;
  }

  function errorBox(container, err) {
    container.innerHTML = "";
    B.renderErrorState(container, err);
  }

  function won(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : `${HomezI18n.formatNumber(Math.round(Number(value)), { maximumFractionDigits: 0 })}${T("easy.won")}`;
  }

  function maskedTime(iso) {
    return iso ? B.fmtDate(iso) : "—";
  }

  // 공용 대화상자(확인·입력). 앱의 기본 대화상자와 달리 입력칸을 직접 꾸밀 수 있다.
  function ezDialog({ title, bodyHtml, okLabel, cancelLabel, danger }) {
    return new Promise((resolve) => {
      const dialog = document.createElement("dialog");
      dialog.className = "ez-dialog";
      dialog.innerHTML = `<form method="dialog"><h3>${esc(title)}</h3><div class="ez-dialog-body">${bodyHtml || ""}</div>
        <div class="actions"><button type="button" class="ez-btn line" data-ez-cancel>${esc(cancelLabel || T("dialog.confirm.cancel"))}</button>
        <button type="submit" class="ez-btn ${danger ? "danger" : ""}" data-ez-ok>${esc(okLabel || T("common.confirm"))}</button></div></form>`;
      document.body.appendChild(dialog);
      const close = (value) => { try { dialog.close(); } catch (_) { /* 이미 닫힘 */ } dialog.remove(); resolve(value); };
      dialog.querySelector("[data-ez-cancel]").addEventListener("click", () => close(null));
      dialog.addEventListener("cancel", () => close(null));
      dialog.querySelector("form").addEventListener("submit", (event) => {
        event.preventDefault();
        close({ root: dialog, get: (name) => { const input = dialog.querySelector(`[name="${name}"]`); return input ? input.value : ""; } });
      });
      dialog.showModal();
    });
  }

  // 같은 화면 안에서 항목 선택·표시를 다시 그릴 때 쓰는 작은 도우미
  // 상세 패널이 목록 아래로 내려가 있는 폭(좁은 창·모바일)에서는, 행을 눌렀을 때 패널이 보이게 내려 준다.
  function revealPanel(panel) {
    if (!panel || !panel.parentElement || typeof panel.scrollIntoView !== "function") return;
    if (panel.offsetWidth >= panel.parentElement.offsetWidth - 2) panel.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function bindRows(root, selector, handler) {
    root.querySelectorAll(selector).forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-ez-stop]")) return;
        handler(row.dataset.key, row);
      });
      row.addEventListener("keydown", (event) => {
        if ((event.key === "Enter" || event.key === " ") && event.target === row) { event.preventDefault(); handler(row.dataset.key, row); }
      });
    });
  }

  // =========================================================
  // 껍데기: 사이드바 · 아래 탭 · 켜기/끄기
  // =========================================================

  function ensureShell() {
    if ($("ez-side")) return;
    const sidenav = document.querySelector(".sidenav");
    const brand = sidenav && sidenav.querySelector(".sidenav-brand");
    const viewRoot = $("view-root");
    if (!sidenav || !brand || !viewRoot) return;

    const side = document.createElement("div");
    side.id = "ez-side";
    side.setAttribute("role", "navigation");
    side.setAttribute("aria-label", T("easy.nav.aria"));
    side.innerHTML = NAV.map((item) => {
      if (item.sep) return '<hr class="ez-nav-sep">';
      if (item.spacer) return '<div class="ez-nav-spacer"></div>';
      return `<button type="button" class="ez-nav ${item.sub ? "ez-sub" : ""}" data-ez-nav="${item.id}" data-ez-view="${item.view}">${icon(item.icon)}<span>${esc(T(item.key))}</span>${item.badge ? '<span class="ez-badge" hidden>0</span>' : ""}</button>`;
    }).join("");
    brand.after(side);
    side.querySelectorAll("[data-ez-view]").forEach((btn) => btn.addEventListener("click", () => B.navigateTo(btn.dataset.ezView)));

    // 좁은 화면 아래 탭(오늘·상품·주문·정산·승인)
    const bottom = document.createElement("nav");
    bottom.id = "ez-bottom";
    bottom.setAttribute("aria-label", T("easy.nav.aria"));
    bottom.innerHTML = ["today", "products", "orders", "money", "approvals"].map((id) => {
      const item = NAV.find((n) => n.id === id);
      return `<button type="button" data-ez-nav="${id}" data-ez-view="${item.view}">${icon(item.icon)}<span>${esc(T(item.key))}</span>${item.badge ? '<span class="ez-badge" hidden>0</span>' : ""}</button>`;
    }).join("");
    document.body.appendChild(bottom);
    bottom.querySelectorAll("[data-ez-view]").forEach((btn) => btn.addEventListener("click", () => B.navigateTo(btn.dataset.ezView)));

    // 기존(고급) 화면 위에 돌아가기 줄
    const bar = document.createElement("div");
    bar.id = "ez-legacy-bar";
    bar.innerHTML = `<span>${esc(T("easy.legacy_bar"))}</span><button type="button" data-ez-back>${esc(T("easy.legacy_back"))}</button>`;
    viewRoot.before(bar);
    bar.querySelector("[data-ez-back]").addEventListener("click", () => B.navigateTo("easy-today"));

    // 화면 컨테이너
    EASY_VIEWS.forEach((name) => {
      if ($(`view-${name}`)) return;
      const section = document.createElement("section");
      section.id = `view-${name}`;
      section.className = "view";
      section.innerHTML = `<div id="ez-root-${name}" class="ez-page"></div>`;
      viewRoot.appendChild(section);
    });

    // 계정 메뉴에 "쉬운 보기" 켜기/끄기(끄고 나서 다시 켤 수 있는 길)
    const dropdown = $("operator-menu-dropdown");
    if (dropdown && !$("ez-toggle-menu")) {
      const toggle = document.createElement("button");
      toggle.id = "ez-toggle-menu";
      toggle.className = "dropdown-item";
      toggle.setAttribute("role", "menuitem");
      toggle.addEventListener("click", () => { dropdown.hidden = true; setEasyEnabled(!S.enabled, true); });
      dropdown.prepend(toggle);
    }
    refreshToggleText();
  }

  function refreshToggleText() {
    const toggle = $("ez-toggle-menu");
    if (toggle) toggle.textContent = T(S.enabled ? "easy.menu_turn_off" : "easy.menu_turn_on");
  }

  function setEnabled(on) {
    S.enabled = on;
    document.body.classList.toggle("easy-view", on);
    document.body.classList.toggle("ez-mobile-tabs", on);
    B.state.homeView = on ? "easy-today" : null;
    refreshToggleText();
  }

  async function savePreference(on) {
    const current = await B.userSettingFetch("ui_preferences");
    const base = current && current.value && typeof current.value === "object" ? { ...current.value } : {};
    base.easy_view = on;
    return B.userSettingSave("ui_preferences", base, 1);
  }

  // 사용자가 직접 켜거나 끌 때: 설정을 저장하고 알맞은 첫 화면으로 간다.
  async function setEasyEnabled(on, persist) {
    ensureShell();
    setEnabled(on);
    if (persist) await savePreference(on);
    B.toast(T(on ? "easy.toast_on" : "easy.toast_off"), "success");
    B.navigateTo(on ? "easy-today" : "overview");
  }

  function activeNavId(viewName) {
    if (viewName === "easy-product-detail") return "products";
    if (EASY_VIEWS.has(viewName)) return NAV.find((n) => n.view === viewName).id;
    if (viewName === "guides") return "help";
    const section = B.sectionIdFor(viewName);
    return SECTION_TO_NAV[section] || null;
  }

  function onNavigated(viewName) {
    if (!S.enabled) return;
    const active = activeNavId(viewName);
    document.querySelectorAll("[data-ez-nav]").forEach((btn) => {
      if (btn.dataset.ezNav === active) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
    });
    document.body.classList.toggle("ez-on-legacy", !EASY_VIEWS.has(viewName) && viewName !== "guides");
  }

  function setBadge(count) {
    S.badge = count;
    document.querySelectorAll("[data-ez-nav='approvals'] .ez-badge").forEach((node) => { node.textContent = String(count); node.hidden = count <= 0; });
  }

  // 승인 센터 배지 = 허용 요청 + 송장 등록·출고일 확인처럼 내 결정이 필요한 일. 실패하면 조용히 둔다.
  async function refreshBadge() {
    try {
      const [ext, today] = await Promise.all([api("/external-calls/summary").catch(() => null), api("/easy-view/today").catch(() => null)]);
      const pending = ext && Array.isArray(ext.pending) ? ext.pending.length : 0;
      const days = today && Array.isArray(today.outbound_days_todo) ? today.outbound_days_todo.length : 0;
      setBadge(pending + days);
    } catch (_) { /* 배지는 부가 정보 */ }
  }

  function onShellShown() {
    ensureShell();
    if (!isAdmin()) { setEnabled(false); return; }
    setEnabled(true);
    refreshBadge();
    // 저장된 선택이 "끄기"면 되돌린다(기본은 켜짐).
    B.userSettingFetch("ui_preferences").then((resp) => {
      const value = resp && resp.value;
      if (value && value.easy_view === false && S.enabled) {
        setEnabled(false);
        if (EASY_VIEWS.has(B.currentView)) B.navigateTo("overview");
      }
    });
  }

  // =========================================================
  // 오늘 할 일
  // =========================================================

  const GROUP_ORDER = { CHECK: 0, APPROVAL: 1, PROGRESS: 2 };

  function wqRowFromItem(item, wizardThumbs) {
    const wizardId = item.next && item.next.wizardId;
    return {
      key: item.key, group: item.state, title: B.wq.title(item), sub: item.detail || "",
      product: item.product ? { name: item.product.name, sub: item.product.supplier || "", thumb: item.product.thumbAssetId } : (item.target ? { name: item.target, sub: "", thumb: null } : null),
      chip: stateChip(item.state),
      action: { label: T(item.next.labelKey), run: () => (wizardId ? B.openWizard(wizardId) : B.navigateTo(item.next.view, item.next.opts || {})) },
      facts: [[T("easy.fact.kind"), T(`wq.kind.${item.kind}`)]],
    };
  }

  function stateChip(state) {
    if (state === "CHECK") return chip("red", T("easy.state.check"));
    if (state === "APPROVAL") return chip("wait", T("easy.state.approval"));
    return chip("blue", T("easy.state.progress"));
  }

  // 2026-10-18 — 상품 관리 AI P4: 오늘 브리핑(규칙 기반, AI 없음·새 호출 없음). 이미 불러온 값으로 "가장 먼저 볼 것 / 24시간 안에 있을 일 /
  // 자동 확인이 멈춘 건 아닌지"를 한 장에 모은다. 서버 시각 값은 UTC라 시간대 표기가 없으면 UTC로 읽는다.
  const BRIEF_DETECT_MIN_MINUTES = 30;       // 주문 자동 감지(5분 주기) — 이보다 오래 성공 기록이 없으면 점검 대상(주기의 3배와 비교해 큰 쪽)
  const BRIEF_PSTATUS_STALE_MINUTES = 90;    // 쿠팡 상품 상태 확인(30분 주기) — 아직 승인되지 않은 상품이 있을 때만
  const BRIEF_SYS_KEYS = { SCHEDULER_NOT_RUNNING: "easy.brief.sys_scheduler", JOBS_MISSING: "easy.brief.sys_jobs_missing", JOBS_PAUSED: "easy.brief.sys_jobs_paused", JOBS_OVERDUE: "easy.brief.sys_jobs_overdue", BACKUP_NONE: "easy.brief.sys_backup_none", BACKUP_STALE: "easy.brief.sys_backup_stale", BACKUP_OLD: "easy.brief.sys_backup_old", REHEARSAL_FAILED: "easy.brief.sys_rehearsal_failed", REHEARSAL_STALE: "easy.brief.sys_rehearsal_stale", DISK_LOW: "easy.brief.sys_disk_low", DISK_CRITICAL: "easy.brief.sys_disk_critical", ESTOP_ACTIVE: "easy.brief.sys_estop", DETECTION_NEEDS_ALLOWANCE: "easy.brief.sys_det_allowance", DETECTION_ERROR: "easy.brief.sys_det_error", DETECTION_PAUSED: "easy.brief.sys_det_paused", DETECTION_FAILING: "easy.brief.sys_det_failing", DETECTION_BLOCKED: "easy.brief.sys_det_blocked", DETECTION_ENV_BLOCKED: "easy.brief.sys_det_env", DETECTION_STALE: "easy.brief.sys_det_stale", READS_ALLOWANCE_LAPSED: "easy.brief.sys_reads_lapsed" };
  const BRIEF_STALL_KEYS = { detect: "easy.brief.st_detect", pstatus: "easy.brief.st_pstatus", settle: "easy.brief.st_settle" };  // 문구 키를 글자 그대로 둔다(쓰지 않는 문구 점검이 찾을 수 있게)
  const BRIEF_SETTLEMENT_STALE_HOURS = 36;   // 쿠팡 정산 읽기(하루 몇 번)

  function parseUtcMs(iso) {
    if (!iso) return null;
    const text = String(iso);
    const ms = Date.parse(/(?:[zZ]|[+-]\d\d:?\d\d)$/.test(text) ? text : `${text}Z`);
    return Number.isFinite(ms) ? ms : null;
  }

  // 같은 "내가 할 일"이어도 급한 정도가 다르다: 승인반려·송장·반품/문의처럼 판매와 고객에 바로 닿는 일을 먼저, 출고일 확인 같은 정리성 일을 뒤로.
  function briefWeight(row) {
    const key = String(row.key || "");
    const kind = key.startsWith("pstatus:") ? 0 : key.startsWith("invoice:") ? 1 : (key.startsWith("return:") || key.startsWith("inq:") || key.startsWith("supret:")) ? 2
      : key.startsWith("unres:") ? 3 : key === "detect" ? 4 : key.startsWith("days:") ? 9 : 5;
    return (row.group === "CHECK" ? 0 : 10) + kind;
  }

  function briefingFrom({ rows, ext, collect, today, health, nowMs }) {
    const mine = (rows || []).filter((r) => r.group !== "PROGRESS")
      .map((r, index) => ({ r, index, w: briefWeight(r) })).sort((a, b) => a.w - b.w || a.index - b.index).map((x) => x.r);
    const urgent = mine.slice(0, 3).map((r) => ({ key: r.key, title: r.title, name: (r.product && r.product.name) || "" }));
    const upcoming = [];
    ((ext && Array.isArray(ext.renewable)) ? ext.renewable : []).forEach((r) => {
      if (r.state === "EXPIRED") upcoming.push({ kind: "expired", what: r.purpose_label || "" });
      else if (r.hours_left !== null && r.hours_left !== undefined && r.hours_left !== "" && Number.isFinite(Number(r.hours_left)) && Number(r.hours_left) <= 24) upcoming.push({ kind: "soon", what: r.purpose_label || "", hours: Number(r.hours_left) });
    });
    const stalled = [];
    if (collect && ["OK", "WAITING", "FAILING"].includes(collect.health)) {
      const last = parseUtcMs(collect.last_succeeded_at);
      const limit = Math.max(BRIEF_DETECT_MIN_MINUTES, 3 * (Number(collect.interval_minutes) || 5));
      if (last !== null && (nowMs - last) / 60000 > limit) stalled.push({ kind: "detect", minutes: Math.round((nowMs - last) / 60000) });
    }
    const pending = ((today && today.product_status) || []).filter((p) => p.status && p.status !== "APPROVED" && p.status !== "DELETED");
    const checked = pending.map((p) => parseUtcMs(p.checked_at)).filter((ms) => ms !== null);
    if (pending.length && checked.length) {
      const minutes = (nowMs - Math.max(...checked)) / 60000;
      if (minutes > BRIEF_PSTATUS_STALE_MINUTES) stalled.push({ kind: "pstatus", minutes: Math.round(minutes) });
    }
    const settled = parseUtcMs(today && today.settlement_checked_at);
    if (settled !== null && (nowMs - settled) / 3600000 > BRIEF_SETTLEMENT_STALE_HOURS) stalled.push({ kind: "settle", hours: Math.round((nowMs - settled) / 3600000) });
    // 앱 상태 점검(자동 작업·백업·디스크·긴급 중지): 서버가 규칙으로 낸 이상 후보 중 문구가 있는 것만(알 수 없는 코드는 건너뛴다).
    const system = ((health && Array.isArray(health.checks)) ? health.checks : [])
      .filter((c) => c && BRIEF_SYS_KEYS[c.code] && (c.level === "FAIL" || c.level === "WARN"))
      .map((c) => ({ code: c.code, level: c.level, params: c.params || {} }));
    return { mine: mine.length, urgent, upcoming, stalled, system };
  }

  function briefSysText(c) {
    const p = (c && c.params) || {};
    const val = (v) => (v === undefined || v === null ? "…" : v);  // 값이 없으면 "undefined" 대신 "…"
    return T(BRIEF_SYS_KEYS[c.code], { jobs: Array.isArray(p.jobs) ? p.jobs.length : "…", days: val(p.days), mb: val(p.free_mb), m: val(p.minutes), what: Array.isArray(p.purposes) ? p.purposes.map((x) => T(`easy.brief.reads_${String(x).toLowerCase()}`)).join(", ") : "…" });
  }

  function briefingHtml(b) {
    const li = (cls, text) => `<li class="${cls}">${esc(text)}</li>`;
    const urgent = b.urgent.length
      ? b.urgent.map((u) => li("", u.name ? `${u.title} · ${u.name}` : u.title)).join("")
      : li("ez-muted", T("easy.brief.urgent_none"));
    const upcoming = b.upcoming.length
      ? b.upcoming.map((u) => li(u.kind === "expired" ? "ez-c-red" : "ez-c-orange", T(u.kind === "expired" ? "easy.brief.exp_done" : "easy.brief.exp_soon", { what: u.what, h: u.hours }))).join("")
      : li("ez-muted", T("easy.brief.upcoming_none"));
    const lines = b.stalled.map((s) => li("ez-c-red", T(BRIEF_STALL_KEYS[s.kind], { m: s.minutes, h: s.hours })))
      .concat((b.system || []).map((c) => li(c.level === "FAIL" ? "ez-c-red" : "ez-c-orange", briefSysText(c))));
    const stalled = lines.length ? lines.join("") : li("ez-muted", T("easy.brief.stalled_none"));
    return `<section class="ez-brief" aria-label="${esc(T("easy.brief.title"))}">
      <div class="ez-sec">${esc(T("easy.brief.title"))}<small>${esc(T(b.mine ? "easy.brief.headline" : "easy.brief.headline_none", { n: b.mine }))}</small></div>
      <div class="ez-cards" style="grid-template-columns:repeat(3,1fr)">
        <div class="ez-card"><div class="k">${esc(T("easy.brief.urgent"))}</div><ul class="ez-brief-list" style="margin:6px 0 0 16px;padding:0;font-size:14px;line-height:1.5">${urgent}</ul></div>
        <div class="ez-card"><div class="k">${esc(T("easy.brief.upcoming"))}</div><ul class="ez-brief-list" style="margin:6px 0 0 16px;padding:0;font-size:14px;line-height:1.5">${upcoming}</ul></div>
        <div class="ez-card"><div class="k">${esc(T("easy.brief.stalled"))}</div><ul class="ez-brief-list" style="margin:6px 0 0 16px;padding:0;font-size:14px;line-height:1.5">${stalled}</ul>${(b.system || []).length ? `<div style="margin-top:8px"><button type="button" class="ez-btn line sm" data-ez-sysadv>${esc(T("easy.sysadv.btn"))}</button></div>` : ""}</div>
      </div></section>`;
  }

  async function loadToday() {
    const root = $("ez-root-easy-today");
    root.innerHTML = loadingHtml();
    let wq, today, safety, ext, collect, sysHealth;
    try {
      [wq, today, safety, ext, collect, sysHealth] = await Promise.all([
        B.wq.fetchItems(),
        api("/easy-view/today").catch(() => null),
        api("/console/api/safety-status").catch(() => null),
        api("/external-calls/summary").catch(() => null),
        api("/orders/collection-ops/status").catch(() => null),
        api("/diagnostics/health-summary").catch(() => null),
      ]);
    } catch (err) { errorBox(root, err); return; }
    if (wq.allFailed && !today) { errorBox(root, wq.error); return; }

    const rows = [];
    const wizardThumb = new Map((Array.isArray(wq.data && wq.data.wizards) ? wq.data.wizards : []).map((w) => [w.id, w.thumbnail_asset_id || null]));

    // 1) 출고일 확인 — 쿠팡에 등록한 상품 중 출고일이 1일(당일출고)이거나 저장된 값이 없는 것
    ((today && today.outbound_days_todo) || []).forEach((p) => rows.push({
      key: `days:${p.seller_product_id}`, group: "CHECK", title: T("easy.today.days_title"),
      sub: T(p.saved_days ? "easy.today.days_sub_saved" : "easy.today.days_sub_unset", { days: p.saved_days || 1 }),
      product: { name: p.product_name, sub: T("easy.today.coupang_no", { id: p.seller_product_id }), thumb: wizardThumb.get(p.wizard_id) || null },
      chip: chip("wait", T("easy.state.approval")),
      action: { label: T("easy.today.days_action"), run: () => B.navigateTo("easy-approvals", { productId: p.seller_product_id }) },
      facts: [[T("easy.fact.why"), T("easy.today.days_why")], [T("easy.fact.calls"), T("easy.today.days_calls")]],
      detail: T("easy.today.days_why"),
    }));

    // 2) 송장 등록 대기 주문
    ((today && today.invoice_todo) || []).forEach((o) => rows.push({
      key: `invoice:${o.task_id}`, group: "CHECK", title: T("easy.today.invoice_title"), sub: T("easy.today.invoice_sub"),
      product: { name: o.product_name, sub: o.channel_order_id, thumb: null },
      chip: chip("red", T("easy.state.check")),
      action: { label: T("easy.today.invoice_action"), run: () => B.navigateTo("easy-orders", { orderId: o.order_id }) },
      facts: [[T("easy.fact.order"), o.channel_order_id]], detail: T("easy.today.invoice_sub"),
    }));

    // 2-b) 상품이 재고 상품에 연결되지 않아 구매 작업이 되지 못한 새 주문 — 조용히 빠지지 않게 "내가 할 일"로 올린다
    ((today && today.unresolved_orders) || []).forEach((u) => rows.push({
      key: `unres:${u.id}`, group: "CHECK", title: T("easy.today.unres_title"), sub: T("easy.today.unres_sub"),
      product: { name: u.product_name, sub: u.channel_order_id, thumb: null, icon: "bag" },
      chip: chip("red", T("easy.state.check")),
      action: { label: T("easy.today.unres_action"), run: () => B.navigateTo("order-fulfillment") },
      facts: [[T("easy.fact.order"), u.channel_order_id], [T("easy.col.qty"), String(u.quantity)]], detail: T("easy.today.unres_why"),
    }));

    // 2-c) 배송 이후 — 쿠팡에 들어온 반품 요청(끝나기 전)과 답변이 필요한 문의. HOMEZ는 읽어 알려 줄 뿐 처리는 쿠팡 WING에서 한다.
    const afterSales = (today && today.after_sales) || {};
    (afterSales.returns_open || []).forEach((r) => rows.push({
      key: `return:${r.id}`, group: "CHECK", title: T("easy.today.return_title"), sub: T("easy.today.return_sub"),
      product: { name: r.o || "—", sub: T("easy.today.return_receipt", { id: r.id }), thumb: null, icon: "bag" },
      chip: chip("red", tryT(`easy.rs.${String(r.s).toLowerCase()}`, String(r.s || ""))),
      action: { label: T("easy.today.return_action"), run: () => B.navigateTo("easy-orders") },
      facts: [[T("easy.fact.order"), r.o || "—"], [T("easy.today.return_state"), tryT(`easy.rs.${String(r.s).toLowerCase()}`, String(r.s || ""))],
        [T("easy.today.return_reason"), r.rc || "—"], [T("easy.today.return_fault"), tryT(`easy.fault.${String(r.f).toLowerCase()}`, String(r.f || "—"))],
        [T("easy.today.return_pickup"), returnPickupText(r)]],
      detail: T("easy.today.return_detail"),
    }));
    (afterSales.inquiries_open || []).forEach((q) => rows.push({
      key: `inq:${q.entity_id}`, group: "CHECK", title: T("easy.today.inq_title"), sub: T("easy.today.inq_sub"),
      product: { name: q.o || "—", sub: T(q.k === "INQ_CALL" ? "easy.today.inq_call" : "easy.today.inq_online", { id: q.id }), thumb: null, icon: "bag" },
      chip: chip("wait", T("easy.today.inq_unanswered")),
      action: { label: T("easy.today.inq_action"), run: () => B.navigateTo("easy-orders") },
      facts: [[T("easy.fact.order"), q.o || "—"]], detail: T("easy.today.inq_detail"),
    }));

    // 2-d) 공급처(온채널) 반품 요청 — 쿠팡에 반품이 접수되면 공급처에도 반품을 요청해야 한다(접수는 온채널 화면, HOMEZ는 준비·기록·확인).
    ((today && today.supplier_returns) || []).forEach((c) => {
      const state = c.supplier_state;
      const done = state === "CONFIRMED" || state === "REFUNDED";
      rows.push({
        key: `supret:${c.receipt_id}`, group: state === "NOT_REQUESTED" ? "CHECK" : "PROGRESS", title: T("easy.sr.title"),
        sub: T(state === "NOT_REQUESTED" ? "easy.sr.sub_todo" : (done ? "easy.sr.sub_done" : "easy.sr.sub_wait")),
        product: { name: c.product_title || c.channel_order_id, sub: T("easy.sr.order_code", { code: c.supplier_order_code || "—" }), thumb: null, icon: "bag" },
        chip: state === "NOT_REQUESTED" ? chip("red", T("easy.sr.chip_todo")) : (done ? chip("ok", T("easy.sr.chip_done")) : chip("wait", T("easy.sr.chip_wait"))),
        action: state === "NOT_REQUESTED"
          ? { label: T("easy.sr.action_todo"), run: () => supplierReturnDialog(c) }
          : (done ? { label: T("easy.today.return_action"), run: () => B.navigateTo("easy-orders") } : { label: T("easy.sr.action_check"), run: () => supplierReturnCheck(c) }),
        facts: [[T("easy.fact.order"), c.channel_order_id], [T("easy.sr.f_receipt"), c.receipt_id], [T("easy.sr.f_code"), c.supplier_order_code || "—"],
          [T("easy.sr.f_state"), T(done ? "easy.sr.chip_done" : (state === "NOT_REQUESTED" ? "easy.sr.chip_todo" : "easy.sr.chip_wait"))]],
        detail: T("easy.sr.detail"),
      });
    });

    // 2-d2) 쿠팡에 전송한 상품의 심사 상태 — 승인반려는 바로 확인해야 하고(사유 보기), 심사 중이면 진행 중으로 보여 준다(승인완료는 표시하지 않는다).
    ((today && today.product_status) || []).forEach((p) => {
      if (!p.status || p.status === "APPROVED" || p.status === "DELETED") return;
      const denied = p.status === "DENIED";
      rows.push({
        key: `pstatus:${p.seller_product_id}`, group: denied ? "CHECK" : "PROGRESS",
        title: T(denied ? "easy.pstatus.denied_title" : "easy.pstatus.review_title"),
        sub: T(denied ? "easy.pstatus.denied_sub" : "easy.pstatus.review_sub"),
        product: { name: p.product_name, sub: T("easy.today.coupang_no", { id: p.seller_product_id }), thumb: null, icon: "bag" },
        chip: denied ? chip("red", T("easy.pstatus.chip_denied")) : chip("wait", T("easy.pstatus.chip_review")),
        action: denied
          ? { label: T("easy.pstatus.reason_btn"), run: () => productReasonDialog(p) }
          : { label: T("easy.today.return_action"), run: () => B.navigateTo("easy-products") },
        facts: [[T("easy.pstatus.f_status"), T(denied ? "easy.pstatus.chip_denied" : "easy.pstatus.chip_review")], [T("easy.pstatus.f_checked"), p.checked_at || "—"]],
        detail: T(denied ? "easy.pstatus.denied_detail" : "easy.pstatus.review_detail"),
      });
    });

    // 2-e) 상시 허용 만료 임박·만료 — 만료되면 자동 확인이 멈추므로 미리 알린다(승인 센터에서 비밀번호 한 번으로 일괄 연장).
    const renewable = (ext && Array.isArray(ext.renewable)) ? ext.renewable : [];
    if (renewable.length) {
      const expired = renewable.some((r) => r.state === "EXPIRED");
      rows.push({
        key: "renew", group: "APPROVAL", title: T("easy.today.renew_title"),
        sub: T("easy.today.renew_sub", { n: renewable.length }),
        product: { name: T("easy.today.renew_name"), sub: renewable.map((r) => r.purpose_label).join(" · "), thumb: null, icon: "bag" },
        chip: expired ? chip("red", T("easy.ap.renew_expired")) : chip("wait", T("easy.ap.renew_soon")),
        action: { label: T("easy.today.renew_action"), run: () => B.navigateTo("easy-approvals") },
        facts: renewable.map((r) => [r.purpose_label, r.state === "EXPIRED" ? T("easy.ap.renew_expired") : T("easy.ap.renew_hours", { h: r.hours_left })]),
        detail: T("easy.today.renew_detail"),
      });
    }

    // 3) 주문 자동 감지 상태 — 켜져 있지 않거나 실패하면 알린다(정상이면 아래 "지켜보는 일"에만 보인다)
    const health = collect && collect.health;
    if (health && health !== "OK" && health !== "WAITING") {
      const bad = ["FAILING", "ERROR", "BLOCKED", "ENV_BLOCKED"].includes(health);
      rows.push({
        key: "detect", group: bad ? "CHECK" : (health === "NEEDS_ALLOWANCE" ? "APPROVAL" : "PROGRESS"),
        title: T("easy.today.detect_title"), sub: collect.health_message || "",
        product: { name: T("easy.today.detect_name"), sub: T("easy.today.detect_sub"), thumb: null, icon: "bag" },
        chip: health === "OFF" || health === "PAUSED" ? chip("gray", T(`odo.health.${health.toLowerCase()}`)) : chip(bad ? "red" : "wait", T(`odo.health.${health.toLowerCase()}`)),
        action: health === "NEEDS_ALLOWANCE"
          ? { label: T("easy.today.detect_allow"), run: () => B.navigateTo("easy-approvals") }
          : { label: T("easy.today.detect_open"), run: () => B.navigateTo("order-collection-ops") },
        facts: [[T("easy.fact.status"), T(`odo.health.${health.toLowerCase()}`)]], detail: collect.health_message || "",
      });
    }

    // 4) 허용 요청
    const pendingAllowances = (ext && Array.isArray(ext.pending)) ? ext.pending : [];
    if (pendingAllowances.length) {
      rows.push({
        key: "allowances", group: "APPROVAL", title: T("easy.today.allow_title", { count: pendingAllowances.length }),
        sub: pendingAllowances.map((a) => `${a.system_label} ${a.kind_label}`).join(" · "),
        product: { name: T("easy.today.allow_name"), sub: "", thumb: null, icon: "shield" },
        chip: chip("wait", T("easy.state.approval")),
        action: { label: T("easy.today.allow_action"), run: () => B.navigateTo("easy-approvals") },
        facts: [[T("easy.fact.count"), String(pendingAllowances.length)]], detail: T("easy.today.allow_detail"),
      });
    }

    // 5) 기존 "전체 업무"(주문 예외·정산 차이·AI 제안·운영 우선순위·등록 작업)
    wq.items.forEach((item) => rows.push(wqRowFromItem(item, wizardThumb)));
    rows.sort((a, b) => GROUP_ORDER[a.group] - GROUP_ORDER[b.group]);

    const counts = { CHECK: 0, APPROVAL: 0, PROGRESS: 0 };
    rows.forEach((r) => { counts[r.group] += 1; });
    const brief = briefingFrom({ rows, ext, collect, today, health: sysHealth, nowMs: Date.now() });
    const mine = counts.CHECK + counts.APPROVAL;
    const filter = S.selected.todoFilter || "ALL";
    const visible = rows.filter((r) => filter === "ALL" || (filter === "MINE" ? r.group !== "PROGRESS" : r.group === "PROGRESS"));

    const p = (today && today.pipeline) || {};
    const hotSelling = ((today && today.outbound_days_todo) || []).length > 0;
    const hotShipping = ((today && today.invoice_todo) || []).length > 0;
    const hotPrepare = rows.some((r) => r.key.startsWith("wizard:") && r.group === "CHECK");
    const flow = [
      ["prepare", p.prepare, hotPrepare], ["selling", p.selling, hotSelling], ["new_orders", p.new_orders, (p.new_orders || 0) > 0],
      ["shipping", p.shipping, hotShipping], ["after_sales", p.after_sales, (p.after_sales || 0) > 0], ["settlement", p.settlement, false],
    ].map(([key, n, hot]) => `<div class="${hot ? "hot" : ""}"><div class="n ez-num">${n === undefined ? "—" : n}</div><div class="l">${esc(T(`easy.flow.${key}`))}</div><div class="d">${esc(T(`easy.flow.${key}_d`))}</div></div>`).join("");

    const mode = safety && safety.schema_ready ? B.safetyModeLabel(safety.mode) : "—";
    const estop = safety && safety.emergency_stop && safety.emergency_stop.is_active;
    const todayCalls = ext && ext.today ? Object.values(ext.today).reduce((sum, kinds) => sum + Object.values(kinds).reduce((s, n) => s + n, 0), 0) : null;
    const watch = `
      <div class="ez-sec">${esc(T("easy.today.watch_title"))}<small>${esc(T("easy.today.watch_sub"))}</small></div>
      <div class="ez-cards" style="grid-template-columns:repeat(3,1fr)">
        <div class="ez-card"><div class="k">${esc(T("easy.today.watch_detect"))}</div><div class="v" style="font-size:19px">${esc(health ? T(`odo.health.${health.toLowerCase()}`) : "—")}</div><div class="s">${esc((collect && collect.health_message) || "")}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.today.watch_mode"))}</div><div class="v" style="font-size:19px">${esc(mode)}</div><div class="s">${esc(T(estop ? "easy.today.estop_on" : "easy.today.estop_off"))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.today.watch_calls"))}</div><div class="v" style="font-size:19px">${todayCalls === null ? "—" : esc(T("easy.times", { n: todayCalls }))}</div><div class="s">${esc(T("easy.today.watch_calls_sub"))}</div></div>
      </div>`;

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.nav.today"))}</h1><div class="ez-lead">${esc(T("easy.today.lead", { mine, watch: counts.PROGRESS }))}</div></div></div>
      ${wq.failed.length ? `<div class="ez-note info"><div>${esc(T("wq.partial_notice", { sources: wq.failed.map((k) => T(`wq.src.${k}`)).join(", ") }))}</div></div>` : ""}
      ${briefingHtml(brief)}
      <div class="ez-flow" role="group" aria-label="${esc(T("easy.flow.aria"))}">${flow}</div>
      <div class="ez-tabs" role="tablist">
        ${[["ALL", "easy.tab.all", rows.length], ["MINE", "easy.tab.mine", mine], ["PROGRESS", "easy.tab.progress", counts.PROGRESS]].map(([id, key, n]) =>
          `<button type="button" class="ez-tab" role="tab" data-ez-filter="${id}" aria-selected="${filter === id}">${esc(T(key))} <span class="ez-num">${n}</span></button>`).join("")}
      </div>
      <div class="ez-summary"><div>${esc(T("easy.state.check"))}<b class="ez-c-red ez-num">${counts.CHECK}</b></div><div>${esc(T("easy.state.approval"))}<b class="ez-c-orange ez-num">${counts.APPROVAL}</b></div><div>${esc(T("easy.state.progress"))}<b class="ez-c-blue ez-num">${counts.PROGRESS}</b></div></div>
      <div class="ez-layout">
        <div class="ez-main" id="ez-today-main"></div>
        <aside class="ez-panel" id="ez-today-panel" aria-live="polite"></aside>
      </div>
      ${watch}`;

    const sysAdv = root.querySelector("[data-ez-sysadv]");
    if (sysAdv) sysAdv.addEventListener("click", () => systemAdviceAsk(brief.system));
    root.querySelectorAll("[data-ez-filter]").forEach((btn) => btn.addEventListener("click", () => { S.selected.todoFilter = btn.dataset.ezFilter; loadToday(); }));

    const main = $("ez-today-main");
    if (!visible.length) {
      main.innerHTML = emptyHtml("easy.today.empty_title", "easy.today.empty_sub");
      $("ez-today-panel").innerHTML = "";
    } else {
      main.innerHTML = `<table class="ez-table"><thead><tr><th>${esc(T("easy.col.task"))}</th><th>${esc(T("easy.col.target"))}</th><th>${esc(T("easy.col.state"))}</th><th>${esc(T("easy.col.next"))}</th></tr></thead>
        <tbody>${visible.map((r) => `<tr data-key="${esc(r.key)}" tabindex="0">
          <td><b>${esc(r.title)}</b><small class="ez-muted" style="display:block;font-size:13px">${esc(r.sub || "")}</small></td>
          <td>${r.product ? `<span class="ez-cell">${r.product.icon ? `<span class="ez-thumb">${icon(r.product.icon)}</span>` : thumbHtml(r.product.thumb)}<span><b>${esc(r.product.name)}</b><small>${esc(r.product.sub || "")}</small></span></span>` : "—"}</td>
          <td>${r.chip}</td>
          <td><button type="button" class="ez-btn ${r.group === "PROGRESS" ? "line" : ""} sm" data-ez-stop data-ez-run="${esc(r.key)}">${esc(r.action.label)}</button></td></tr>`).join("")}</tbody></table>`;
      B.lwHydrateThumbs(main);
      const select = (key) => {
        S.selected.todoKey = key;
        main.querySelectorAll("tbody tr").forEach((tr) => tr.classList.toggle("hl", tr.dataset.key === key));
        const row = visible.find((r) => r.key === key) || visible[0];
        $("ez-today-panel").innerHTML = `
          <h2>${esc(row.title)}</h2>
          ${row.product ? `<div class="ez-cell" style="margin:14px 0 4px">${row.product.icon ? `<span class="ez-thumb">${icon(row.product.icon)}</span>` : thumbHtml(row.product.thumb)}<span><b>${esc(row.product.name)}</b><small>${esc(row.product.sub || "")}</small></span></div>` : ""}
          <div style="margin:10px 0">${row.chip}</div>
          ${row.detail ? `<div class="ez-note ${row.group === "CHECK" ? "warn" : "info"}"><div>${esc(row.detail)}</div></div>` : ""}
          <div style="margin-top:12px">${(row.facts || []).map(([k, v]) => `<div class="ez-kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>
          <div style="margin-top:18px"><button type="button" class="ez-btn big" data-ez-panel-run>${esc(row.action.label)}</button></div>`;
        B.lwHydrateThumbs($("ez-today-panel"));
        $("ez-today-panel").querySelector("[data-ez-panel-run]").addEventListener("click", () => row.action.run());
      };
      bindRows(main, "tbody tr", (key) => { select(key); revealPanel($("ez-today-panel")); });
      main.querySelectorAll("[data-ez-run]").forEach((btn) => btn.addEventListener("click", () => {
        const row = visible.find((r) => r.key === btn.dataset.ezRun);
        if (row) row.action.run();
      }));
      select(visible.some((r) => r.key === S.selected.todoKey) ? S.selected.todoKey : visible[0].key);
    }
    setBadge((pendingAllowances.length) + (((today && today.outbound_days_todo) || []).length));
  }

  // =========================================================
  // 상품
  // =========================================================

  const WIZARD_CHIP = {
    DRAFT: ["gray", "easy.wstatus.draft"], NEEDS_CORRECTION: ["red", "easy.wstatus.needs_correction"], READY_FOR_APPROVAL: ["blue", "easy.wstatus.ready"],
    APPROVED: ["blue", "easy.wstatus.approved"], SUBMITTING: ["blue", "easy.wstatus.submitting"], VALIDATING: ["blue", "easy.wstatus.submitting"],
    FAILED: ["red", "easy.wstatus.failed"], PARTIALLY_SUCCEEDED: ["wait", "easy.wstatus.partial"], SUCCEEDED: ["ok", "easy.wstatus.done"],
  };
  const OPEN_WIZARD = new Set(["DRAFT", "NEEDS_CORRECTION", "READY_FOR_APPROVAL", "APPROVED", "SUBMITTING", "VALIDATING", "FAILED", "PARTIALLY_SUCCEEDED"]);

  function wizardChip(status) {
    const [kind, key] = WIZARD_CHIP[status] || ["gray", "easy.wstatus.draft"];
    return chip(kind, T(key));
  }

  // 쿠팡에 전송한 상품이면 마지막으로 읽은 쿠팡 심사 상태를 칩으로 덧붙인다(읽은 적이 없으면 아무것도 붙이지 않는다).
  function productStatusChip(w, today) {
    const entry = ((today && today.product_status) || []).find((p) => p.wizard_id === w.id && p.status);
    if (!entry) return "";
    const map = { APPROVED: ["ok", "easy.pstatus.chip_approved"], DENIED: ["red", "easy.pstatus.chip_denied"], DELETED: ["gray", "easy.pstatus.chip_deleted"] };
    const [kind, key] = map[entry.status] || ["wait", "easy.pstatus.chip_review"];
    return `<br>${chip(kind, T(key))}`;
  }

  function startNewProduct() {
    B.navigateTo("listing-wizard");
    setTimeout(() => { const btn = $("lw-url-import-btn"); if (btn) btn.click(); }, 450);
  }

  async function loadProducts() {
    const root = $("ez-root-easy-products");
    root.innerHTML = loadingHtml();
    let wizards, today;
    try {
      [wizards, today] = await Promise.all([api("/listing-wizards"), api("/easy-view/today").catch(() => null)]);
    } catch (err) { errorBox(root, err); return; }
    const allWizards = (Array.isArray(wizards) ? wizards : (wizards && wizards.items) || []).filter((w) => !w.deleted_at);
    // 같은 상품을 여러 번 올려 본 이력은 숨기고 "지금 판매 중인" 위저드만 한 건씩 보여 준다(이력은 지우지 않는다).
    const currentIds = today && Array.isArray(today.selling_wizard_ids) ? new Set(today.selling_wizard_ids) : null;
    const list = allWizards.filter((w) => !currentIds || w.status !== "SUCCEEDED" || currentIds.has(w.id));
    S.wizards = list;
    const todoByWizard = new Set(((today && today.outbound_days_todo) || []).map((p) => p.wizard_id));
    const tab = S.selected.productTab || "OPEN";
    const open = list.filter((w) => OPEN_WIZARD.has(w.status));
    const done = list.filter((w) => w.status === "SUCCEEDED");
    const attention = open.filter((w) => (w.attention || []).length || ["NEEDS_CORRECTION", "FAILED", "PARTIALLY_SUCCEEDED"].includes(w.status));
    const visible = tab === "OPEN" ? open : (tab === "DONE" ? done : list);

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.nav.products"))}</h1><div class="ez-lead">${esc(T("easy.products.lead"))}</div></div>
        <button type="button" class="ez-btn" id="ez-new-product">＋ ${esc(T("easy.products.new"))}</button></div>
      <div class="ez-tabs" role="tablist">
        ${[["OPEN", "easy.products.tab_open", open.length], ["DONE", "easy.products.tab_done", done.length], ["ALL", "easy.tab.all", list.length]].map(([id, key, n]) =>
          `<button type="button" class="ez-tab" role="tab" data-ez-ptab="${id}" aria-selected="${tab === id}">${esc(T(key))} <span class="ez-num">${n}</span></button>`).join("")}
      </div>
      <div class="ez-summary"><div>${esc(T("easy.products.sum_open"))}<b class="ez-c-blue ez-num">${open.length}</b></div><div>${esc(T("easy.products.sum_attention"))}<b class="ez-c-orange ez-num">${attention.length}</b></div><div>${esc(T("easy.products.sum_done"))}<b class="ez-c-green ez-num">${done.length}</b></div><div>${esc(T("easy.products.sum_days"))}<b class="ez-c-red ez-num">${todoByWizard.size}</b></div></div>
      <div class="ez-selbar" id="ez-sel-bar" hidden><span id="ez-sel-count"></span><button type="button" class="ez-btn danger sm" id="ez-sel-delete">${esc(T("easy.products.sel_delete"))}</button></div>
      <div id="ez-products-list"></div>`;
    $("ez-new-product").addEventListener("click", startNewProduct);
    root.querySelectorAll("[data-ez-ptab]").forEach((btn) => btn.addEventListener("click", () => { S.selected.productTab = btn.dataset.ezPtab; loadProducts(); }));

    const host = $("ez-products-list");
    if (!visible.length) { host.innerHTML = emptyHtml("easy.products.empty_title", "easy.products.empty_sub"); return; }
    // 지울 수 있는 위저드만 고를 수 있다(서버가 계산한 deletable). 이미 올라간 상품·진행 중인 건은 막힌다.
    const deletableIds = new Set(visible.filter((w) => w.deletable).map((w) => w.id));
    S.delIds = new Set([...S.delIds].filter((id) => deletableIds.has(id)));
    host.innerHTML = `<table class="ez-table"><thead><tr><th class="ez-selcol" data-ez-stop><input type="checkbox" id="ez-sel-all" aria-label="${esc(T("easy.products.sel_all"))}"></th><th>${esc(T("easy.col.product"))}</th><th>${esc(T("easy.col.channel"))}</th><th>${esc(T("easy.col.progress"))}</th><th>${esc(T("easy.col.state"))}</th><th>${esc(T("easy.col.check"))}</th><th>${esc(T("easy.col.next"))}</th></tr></thead>
      <tbody>${visible.map((w) => {
        const n = w.step_number || 0;
        const total = w.step_total || 10;
        const pct = w.status === "SUCCEEDED" ? 100 : Math.round((n / total) * 100);
        const channels = (w.channels || []).map((c) => c && c.code).filter(Boolean).join(", ") || "—";
        const attn = (w.attention || []).slice(0, 2).map((code) => `<span class="ez-dot warn" aria-hidden="true">!</span> ${esc(T(`lw.attention.${String(code).toLowerCase()}`))}`).join("<br>");
        const needDays = todoByWizard.has(w.id) ? `<br><span class="ez-chip wait">${esc(T("easy.products.days_chip"))}</span>` : "";
        return `<tr data-key="${w.id}" tabindex="0">
          <td class="ez-selcol" data-ez-stop><input type="checkbox" data-ez-sel="${w.id}" ${w.deletable ? "" : "disabled"} ${S.delIds.has(w.id) ? "checked" : ""} title="${esc(w.deletable ? "" : (w.block_reason || T("easy.products.sel_blocked")))}" aria-label="${esc(T("easy.products.sel_one"))}"></td>
          <td><span class="ez-cell">${thumbHtml(w.thumbnail_asset_id)}<span><b>${esc(w.product_name || `#${w.id}`)}</b><small>${esc(w.supplier_label || "")}</small></span></span></td>
          <td>${esc(channels)}</td>
          <td style="min-width:130px"><div class="ez-num" style="font-size:13px;margin-bottom:4px">${w.status === "SUCCEEDED" ? esc(T("easy.products.finished")) : `${n} / ${total}`}</div><div class="ez-bar"><i style="width:${pct}%"></i></div></td>
          <td>${wizardChip(w.status)}${productStatusChip(w, today)}</td>
          <td style="font-size:13.5px">${attn || '<span class="ez-faint">—</span>'}${needDays}</td>
          <td><button type="button" class="ez-btn ${w.status === "SUCCEEDED" ? "line" : ""} sm" data-ez-stop data-ez-open="${w.id}">${esc(T(w.status === "SUCCEEDED" ? "easy.products.open_result" : "easy.products.open_prep"))}</button></td></tr>`;
      }).join("")}</tbody></table>`;
    B.lwHydrateThumbs(host);
    bindRows(host, "tbody tr", (key) => B.navigateTo("easy-product-detail", { wizardId: Number(key) }));
    host.querySelectorAll("[data-ez-open]").forEach((btn) => btn.addEventListener("click", () => B.navigateTo("easy-product-detail", { wizardId: Number(btn.dataset.ezOpen) })));
    bindProductSelection(root, host, deletableIds);
  }

  // 선택 삭제: 기존 "대기 상품 정리"(POST /listing-wizards/bulk-archive)를 그대로 쓴다. 보관 처리라 지우는 것이 아니라 숨기는 것이고, 서버가 지울 수 없는 건을 건너뛴다.
  function syncSelectionBar(root, host, deletableIds) {
    const n = S.delIds.size;
    const bar = $("ez-sel-bar");
    bar.hidden = n === 0;
    $("ez-sel-count").textContent = T("easy.products.sel_count", { count: n });
    const all = $("ez-sel-all");
    all.disabled = deletableIds.size === 0;
    all.checked = deletableIds.size > 0 && n === deletableIds.size;
    all.indeterminate = n > 0 && n < deletableIds.size;
    host.querySelectorAll("[data-ez-sel]").forEach((cb) => { cb.checked = S.delIds.has(Number(cb.dataset.ezSel)); });
  }

  function bindProductSelection(root, host, deletableIds) {
    host.querySelectorAll("[data-ez-sel]").forEach((cb) => cb.addEventListener("change", () => {
      const id = Number(cb.dataset.ezSel);
      if (cb.checked) S.delIds.add(id); else S.delIds.delete(id);
      syncSelectionBar(root, host, deletableIds);
    }));
    $("ez-sel-all").addEventListener("change", (event) => {
      S.delIds = event.target.checked ? new Set(deletableIds) : new Set();
      syncSelectionBar(root, host, deletableIds);
    });
    $("ez-sel-delete").addEventListener("click", async () => {
      const ids = [...S.delIds];
      if (!ids.length) return;
      const names = ids.map((id) => { const w = S.wizards.find((x) => x.id === id); return `<li>${esc((w && w.product_name) || `#${id}`)}</li>`; }).join("");
      const ok = await ezDialog({ title: T("easy.products.sel_confirm_title"), danger: true, okLabel: T("easy.products.sel_delete"),
        bodyHtml: `<p>${esc(T("easy.products.sel_confirm_body", { count: ids.length }))}</p><ul style="margin:8px 0 0 18px">${names}</ul>` });
      if (!ok) return;
      try {
        const r = await api("/listing-wizards/bulk-archive", { method: "POST", body: JSON.stringify({
          wizard_ids: ids, reason: T("easy.products.sel_reason"), deletion_request_id: `ez-bulk-${Date.now()}-${Math.random().toString(36).slice(2, 10)}` }) });
        const done = (r.succeeded || []).length, skipped = (r.skipped || []).length, failed = (r.failed || []).length;
        B.toast(failed ? T("easy.products.sel_has_failure") : (skipped ? T("easy.products.sel_partial", { done, skipped }) : T("easy.products.sel_done", { done })), failed ? "error" : "success");
        S.delIds = new Set();
        loadProducts();
      } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
    });
    syncSelectionBar(root, host, deletableIds);
  }

  // ---- 올리기 준비 화면(위저드 한 건) ----
  const COURIERS = { CJGLS: "CJ대한통운", HYUNDAI: "롯데택배", HANJIN: "한진택배", EPOST: "우체국택배", LOGEN: "로젠택배", KDEXP: "경동택배" };

  async function loadProductDetail(opts) {
    const root = $("ez-root-easy-product-detail");
    const id = Number((opts && opts.wizardId) || S.selected.wizardId);
    S.selected.wizardId = id;
    if (!id) { B.navigateTo("easy-products"); return; }
    root.innerHTML = loadingHtml();
    let wizard;
    try { wizard = await api(`/listing-wizards/${id}`); } catch (err) { errorBox(root, err); return; }
    const draft = wizard.draft || {};
    const selection = (wizard.channel_selections || [])[0] || {};
    const rf = selection.required_fields || {};
    const eco = (wizard.economics_result || [])[0] || null;
    const ecoIn = (wizard.economics_input || [])[0] || null;
    const issues = ((wizard.validation_result && wizard.validation_result.issues) || []);
    const status = wizard.status;
    const done = status === "SUCCEEDED";
    const optionCount = Array.isArray(rf.items) ? rf.items.length : 0;
    const photos = (wizard.selected_media_asset_ids || []).length;
    const rightsOk = rf.liveImageRightsConfirmed === true;
    const marginRate = eco && eco.margin_rate !== undefined ? Number(eco.margin_rate) : null;
    const marginOk = marginRate !== null && marginRate >= 0.18;
    const days = Number.isInteger(rf.outboundShippingTimeDay) ? rf.outboundShippingTimeDay : null;
    const ready = status === "READY_FOR_APPROVAL" || status === "APPROVED" || done;

    const need = [];
    if (!rightsOk) need.push({ k: T("easy.prep.rights"), v: T("easy.prep.rights_v"), small: T("easy.prep.rights_s") });
    if (!photos) need.push({ k: T("easy.prep.photos"), v: T("easy.prep.photos_none"), small: T("easy.prep.photos_s") });
    issues.forEach((issue) => need.push({ k: T("easy.prep.issue"), v: String(issue.message || issue.code || issue), small: issue.step ? T("easy.prep.issue_step", { step: issue.step }) : "" }));
    if (status === "READY_FOR_APPROVAL") need.push({ k: T("easy.prep.approve"), v: T("easy.prep.approve_v"), small: T("easy.prep.approve_s") });
    if (status === "FAILED" || status === "PARTIALLY_SUCCEEDED") need.push({ k: T("easy.prep.result"), v: T("easy.prep.result_v"), small: T("easy.prep.result_s") });

    const checks = [
      [photos > 0 && rightsOk, T("easy.prep.chk_media")],
      [optionCount > 0, T("easy.prep.chk_options")],
      [marginRate === null ? null : marginOk, T("easy.prep.chk_margin")],
      [issues.length === 0 && ready, T("easy.prep.chk_valid")],
    ];
    const passed = checks.filter(([ok]) => ok === true).length;

    const stepOn = done ? 3 : (status === "APPROVED" || status === "SUBMITTING" ? 3 : 2);
    const fieldRow = (k, v, small, extra) => `<div class="ez-field ${extra || ""}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}${small ? `<small>${esc(small)}</small>` : ""}</div><span></span></div>`;

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(draft.product_name || `#${id}`)}</h1><div class="ez-lead">${esc(T("easy.prep.lead"))}</div></div>
        <div style="display:flex;gap:10px"><button type="button" class="ez-btn line" id="ez-prep-back">← ${esc(T("easy.prep.back"))}</button></div></div>
      <div class="ez-steps3"><div class="done"><span class="n">✓</span>${esc(T("easy.prep.step1"))}</div>
        <div class="${stepOn === 2 ? "on" : "done"}"><span class="n">${stepOn === 2 ? "2" : "✓"}</span>${esc(T("easy.prep.step2"))}</div>
        <div class="${done ? "done" : (stepOn === 3 ? "on" : "")}"><span class="n">${done ? "✓" : "3"}</span>${esc(T("easy.prep.step3"))}</div></div>
      <div class="ez-layout">
        <div class="ez-main">
          <div class="ez-block"><h3>${esc(T("easy.prep.need_title"))} ${need.length ? chip("wait", T("easy.count", { n: need.length })) : chip("ok", T("easy.prep.none"))}</h3>
            ${need.length ? need.map((n) => fieldRow(n.k, n.v, n.small, "need")).join("") : `<div class="ez-field"><div class="k"></div><div class="v">${esc(T("easy.prep.need_none"))}</div><span></span></div>`}</div>
          <div class="ez-block"><h3>${esc(T("easy.prep.ai_title"))} ${chip("ok", T("easy.prep.auto"))}</h3>
            ${fieldRow(T("easy.prep.name"), draft.product_name || "—")}
            ${fieldRow(T("easy.prep.options"), optionCount ? T("easy.prep.options_v", { n: optionCount }) : "—", T("easy.prep.options_s"))}
            ${fieldRow(T("easy.prep.photos"), photos ? T("easy.prep.photos_v", { n: photos }) : "—")}
            ${fieldRow(T("easy.prep.notice"), Array.isArray(rf.notices) && rf.notices.length ? T("easy.prep.notice_v") : "—")}
            ${fieldRow(T("easy.prep.price"), rf.salePrice ? won(rf.salePrice) : "—")}</div>
          <div class="ez-block"><h3>${esc(T("easy.prep.default_title"))} ${chip("gray", T("easy.prep.default"))}</h3>
            ${fieldRow(T("easy.prep.days"), days ? T("easy.prep.days_v", { n: days }) : T("easy.prep.days_unset"), days ? "" : T("easy.prep.days_unset_s"), days ? "" : "need")}
            ${fieldRow(T("easy.prep.courier"), COURIERS[rf.deliveryCompanyCode] || rf.deliveryCompanyCode || "—")}
            ${fieldRow(T("easy.prep.place"), rf.outboundShippingPlaceCode ? T("easy.prep.place_v", { code: rf.outboundShippingPlaceCode }) : "—")}</div>
        </div>
        <aside class="ez-panel">
          <div class="ez-thumb lg">${photos ? B.lwThumbHtml({ thumbnail_asset_id: wizard.selected_media_asset_ids[0] }) : icon("image")}</div>
          <h2 style="margin-top:12px">${esc(draft.product_name || "")}</h2>
          <div style="margin:8px 0">${wizardChip(status)}</div>
          <div style="font-size:28px;font-weight:800" class="ez-num">${rf.salePrice ? won(rf.salePrice) : "—"}</div>
          <div class="ez-sec" style="margin-top:20px">${esc(T("easy.prep.money_title"))}</div>
          ${eco ? `<div class="ez-money ez-num">
            <span class="ez-muted">${esc(T("easy.prep.m_price"))}</span><b>${won(eco.expected_revenue)}</b>
            ${ecoIn ? `<span class="ez-muted">${esc(T("easy.prep.m_cost"))}</span><b>− ${won(Number(ecoIn.cost_of_goods || 0) + Number(ecoIn.shipping_cost || 0))}</b>` : ""}
            <span class="ez-muted">${esc(T("easy.prep.m_other"))}</span><b>− ${won(Number(eco.total_cost) - (ecoIn ? Number(ecoIn.cost_of_goods || 0) + Number(ecoIn.shipping_cost || 0) : 0))}</b>
            <span class="tot"><b style="text-align:left">${esc(T("easy.prep.m_net"))}</b></span><b class="tot ${marginOk ? "ez-c-green" : "ez-c-red"}">${won(eco.margin_amount)} (${marginRate === null ? "—" : Math.round(marginRate * 100)}%)</b></div>` : `<div class="ez-muted">${esc(T("easy.prep.m_none"))}</div>`}
          <div class="ez-sec" style="margin-top:20px">${esc(T("easy.prep.chk_title"))} <small class="ez-num">${passed} / ${checks.length}</small></div>
          <ul class="ez-checks">${checks.map(([ok, text]) => `<li class="${ok === true ? "" : "w"}">${dot(ok === true ? "ok" : (ok === null ? "gray" : "warn"), ok === true ? "✓" : (ok === null ? "·" : "!"))}${esc(text)}</li>`).join("")}</ul>
          <div style="margin-top:16px"><button type="button" class="ez-btn big ${done ? "line" : ""}" id="ez-prep-open">${esc(T(done ? "easy.prep.open_result" : (ready ? "easy.prep.open_submit" : "easy.prep.open_edit")))}</button>
            <div class="ez-faint" style="font-size:13px;margin-top:8px;text-align:center">${esc(T("easy.prep.open_hint"))}</div></div>
        </aside>
      </div>`;
    B.lwHydrateThumbs(root);
    $("ez-prep-back").addEventListener("click", () => B.navigateTo("easy-products"));
    $("ez-prep-open").addEventListener("click", () => B.openWizard(id));
  }

  // =========================================================
  // 주문·배송
  // =========================================================

  const STEP_NAMES = ["ordered", "supplier_order", "supplier_shipped", "invoice", "delivered"];

  function stepBadgeChip(order) {
    const now = order.steps.find((s) => s.state === "now");
    if (!now) return chip("ok", T("easy.orders.done"));
    const code = order.next_action.code;
    if (code === "REGISTER_INVOICE") return chip("red", T("easy.orders.chip_invoice"));
    return chip(now.key === "supplier_order" ? "wait" : "blue", T(`easy.step.${now.key}`));
  }

  function progressHtml(order) {
    const doneCount = order.steps.filter((s) => s.state === "done").length;
    const pct = Math.min(80, Math.max(0, (Math.max(0, doneCount - 1) / 4) * 80));
    return `<div class="ez-prog"><span class="ez-prog-bar" style="width:${pct}%"></span>${order.steps.map((s, i) => `<div class="${s.state}"><div class="c">${s.state === "done" ? "✓" : i + 1}</div><b>${esc(T(`easy.step.${s.key}`))}</b><span>${esc(stepDetailText(s))}</span></div>`).join("")}</div>`;
  }

  function stepDetailText(step) {
    if (step.state === "now") return T("easy.orders.now");
    if (!step.detail) return "";
    if (step.key === "ordered") {
      const full = B.fmtDate(step.detail);
      const m = String(full).match(/\d{4}\.\s*(\d{1,2}\.\s*\d{1,2}\.)/);
      return m ? m[1] : String(full);
    }
    return String(step.detail);
  }

  async function loadOrders(opts) {
    const root = $("ez-root-easy-orders");
    if (opts && opts.orderId) S.selected.orderId = opts.orderId;
    root.innerHTML = loadingHtml();
    let data;
    try { data = await api("/easy-view/orders"); } catch (err) { errorBox(root, err); return; }
    const items = data.items || [];
    S.ordersCache = items;
    const waiting = items.filter((o) => o.next_action.code === "REGISTER_INVOICE" || o.next_action.code === "ORDER_FROM_SUPPLIER").length;
    const shipping = items.filter((o) => o.now_step >= 3 && o.next_action.code !== "DONE").length;
    const finished = items.filter((o) => o.next_action.code === "DONE").length;
    const tab = S.selected.orderTab || "ALL";
    const visible = items.filter((o) => tab === "ALL" || (tab === "TODO" ? ["REGISTER_INVOICE", "ORDER_FROM_SUPPLIER", "CHECK_COUPANG_ORDER", "RETURN_IN_PROGRESS"].includes(o.next_action.code) : o.next_action.code === "DONE"));

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.nav.orders"))}</h1><div class="ez-lead">${esc(T("easy.orders.lead"))}</div></div>
        <button type="button" class="ez-btn line" id="ez-orders-refresh">${esc(T("common.refresh"))}</button></div>
      <div class="ez-tabs" role="tablist">
        ${[["ALL", "easy.tab.all", items.length], ["TODO", "easy.orders.tab_todo", waiting], ["DONE", "easy.orders.tab_done", finished]].map(([id, key, n]) =>
          `<button type="button" class="ez-tab" role="tab" data-ez-otab="${id}" aria-selected="${tab === id}">${esc(T(key))} <span class="ez-num">${n}</span></button>`).join("")}
      </div>
      <div class="ez-summary"><div>${esc(T("easy.orders.sum_todo"))}<b class="ez-c-orange ez-num">${waiting}</b></div><div>${esc(T("easy.orders.sum_shipping"))}<b class="ez-c-blue ez-num">${shipping}</b></div><div>${esc(T("easy.orders.sum_done"))}<b class="ez-c-green ez-num">${finished}</b></div></div>
      <div class="ez-layout"><div class="ez-main" id="ez-orders-main"></div><aside class="ez-panel" id="ez-orders-panel" aria-live="polite"></aside></div>`;
    $("ez-orders-refresh").addEventListener("click", () => loadOrders());
    root.querySelectorAll("[data-ez-otab]").forEach((btn) => btn.addEventListener("click", () => { S.selected.orderTab = btn.dataset.ezOtab; loadOrders(); }));

    const main = $("ez-orders-main");
    if (!visible.length) { main.innerHTML = emptyHtml("easy.orders.empty_title", "easy.orders.empty_sub"); $("ez-orders-panel").innerHTML = ""; return; }
    main.innerHTML = `<table class="ez-table"><thead><tr><th>${esc(T("easy.col.order"))}</th><th>${esc(T("easy.col.qty"))}</th><th>${esc(T("easy.col.step"))}</th><th>${esc(T("easy.col.next"))}</th></tr></thead>
      <tbody>${visible.map((o) => `<tr data-key="${o.order_id}" tabindex="0">
        <td><span class="ez-cell"><span class="ez-thumb">${icon("bag")}</span><span><b>${esc(o.channel_order_id)}</b><small>${esc(o.product_name)} · ${esc(o.buyer_masked)}</small></span></span></td>
        <td class="ez-num">${esc(T("easy.orders.qty_n", { n: o.quantity || 1 }))}</td>
        <td>${stepBadgeChip(o)}<div class="ez-faint ez-num" style="font-size:12px;margin-top:4px">${o.now_step} / 5</div></td>
        <td><button type="button" class="ez-btn ${o.next_action.code === "REGISTER_INVOICE" ? "" : "line"} sm" data-ez-stop data-ez-ord="${o.order_id}">${esc(T(`easy.next.${o.next_action.code.toLowerCase()}`))}</button></td></tr>`).join("")}</tbody></table>`;
    const select = (key) => {
      S.selected.orderId = Number(key);
      main.querySelectorAll("tbody tr").forEach((tr) => tr.classList.toggle("hl", tr.dataset.key === String(key)));
      renderOrderPanel(visible.find((o) => String(o.order_id) === String(key)) || visible[0]);
    };
    bindRows(main, "tbody tr", (key) => { select(key); revealPanel($("ez-orders-panel")); });
    main.querySelectorAll("[data-ez-ord]").forEach((btn) => btn.addEventListener("click", () => { select(btn.dataset.ezOrd); revealPanel($("ez-orders-panel")); }));
    select(visible.some((o) => o.order_id === S.selected.orderId) ? S.selected.orderId : visible[0].order_id);
  }

  function renderOrderPanel(order) {
    const panel = $("ez-orders-panel");
    const code = order.next_action.code;
    const tr = order.tracking;
    let action = "";
    if (code === "REGISTER_INVOICE") {
      action = `
        <h3 style="margin:18px 0 6px;font-size:17px">${esc(T("easy.orders.inv_title"))}</h3>
        <div>${[[T("easy.orders.courier"), (tr && tr.courier) || "—"], [T("easy.orders.number"), (tr && tr.number) || "—"], [T("easy.orders.coupang_state"), order.coupang_status ? T("easy.orders.coupang_stale", { s: order.coupang_status }) : "—"]]
          .map(([k, v]) => `<div class="ez-kv"><span>${esc(k)}</span><b class="ez-num">${esc(v)}</b></div>`).join("")}</div>
        <div id="ez-inv-result"></div>
        <div class="ez-note warn"><span class="ez-dot warn">!</span><div>${esc(T("easy.orders.inv_warn"))}</div></div>
        <div style="margin-top:14px;display:grid;gap:10px"><button type="button" class="ez-btn big" id="ez-inv-preview">${esc(T("easy.orders.inv_preview"))}</button>
          <button type="button" class="ez-btn line big" id="ez-inv-done">${esc(T("easy.orders.inv_done"))}</button></div>
        <div class="ez-faint" style="font-size:13px;margin-top:10px;text-align:center">${esc(T("easy.orders.inv_calls"))}</div>`;
    } else if (code === "ORDER_FROM_SUPPLIER") {
      action = `<div class="ez-note info"><span class="ez-dot info">i</span><div>${esc(T("easy.orders.supplier_note"))}</div></div>
        <div style="margin-top:14px"><button type="button" class="ez-btn big" id="ez-open-task">${esc(T("easy.next.order_from_supplier"))}</button></div>`;
    } else if (code === "WAIT_SUPPLIER_SHIPMENT") {
      action = `<div class="ez-note info"><span class="ez-dot info">i</span><div>${esc(T("easy.orders.wait_ship"))}</div></div>
        ${order.task ? `<div style="margin-top:14px"><button type="button" class="ez-btn line big" id="ez-open-task">${esc(T("easy.orders.open_task"))}</button></div>` : ""}`;
    } else if (code === "CHECK_COUPANG_ORDER") {
      action = `<div class="ez-note warn"><span class="ez-dot warn">!</span><div>${esc(T("easy.orders.need_coupang"))}</div></div>
        <div style="margin-top:14px"><button type="button" class="ez-btn big" id="ez-open-detect">${esc(T("easy.orders.open_detect"))}</button></div>`;
    } else if (code === "RETURN_IN_PROGRESS") {
      action = `<div class="ez-note warn"><span class="ez-dot warn">!</span><div>${esc(T("easy.orders.return_note"))}</div></div>`;
    } else if (code === "WAIT_DELIVERY") {
      action = `<div class="ez-note ok"><div>${esc(T("easy.orders.wait_delivery"))}</div></div>`;
    } else {
      action = `<div class="ez-note ok"><div>${esc(T("easy.orders.all_done"))}</div></div>`;
    }
    panel.innerHTML = `
      <h2>${esc(order.product_name || order.channel_order_id)} × ${order.quantity || 1}</h2>
      <div class="ez-muted" style="font-size:14px;margin-top:2px">${esc(T("easy.orders.head_sub", { id: order.channel_order_id, time: maskedTime(order.ordered_at) }))}</div>
      ${progressHtml(order)}
      ${trackHtml(order)}
      ${afterHtml(order)}
      ${action}`;
    const trackBtn = $("ez-track-now");
    if (trackBtn) trackBtn.addEventListener("click", () => trackNow(order));

    const previewBtn = $("ez-inv-preview");
    if (previewBtn) previewBtn.addEventListener("click", () => invoicePreview(order));
    const doneBtn = $("ez-inv-done");
    if (doneBtn) doneBtn.addEventListener("click", () => invoiceMarkDone(order));
    const openTask = $("ez-open-task");
    if (openTask) openTask.addEventListener("click", () => B.navigateTo(order.next_action.view || "purchase-task", order.task ? { id: order.task.id } : {}));
    const openDetect = $("ez-open-detect");
    if (openDetect) openDetect.addEventListener("click", () => B.navigateTo("order-collection-ops"));
  }

  // 쿠팡 주문·배송 현황: 쿠팡이 알려 준 상태와 마지막 확인 시각. 10분마다 자동으로 읽고, 여기서 지금 한 번 읽을 수도 있다.
  function trackHtml(order) {
    if (!order.task) return "";
    const s = String(order.coupang_status || "");
    const label = s ? tryT(`easy.cs.${s.toLowerCase()}`, s) : "—";
    const done = s === "FINAL_DELIVERY";
    const checked = order.coupang_checked_at ? B.fmtDate(order.coupang_checked_at) : T("easy.orders.track_never");
    return `<div class="ez-block" style="margin-top:16px"><h3 style="margin:0 0 6px;font-size:17px">${esc(T("easy.orders.track_title"))}</h3>
      ${[[T("easy.orders.coupang_state"), label], [T("easy.orders.track_checked"), checked]].map(([k, v]) => `<div class="ez-kv"><span>${esc(k)}</span><b class="ez-num">${esc(v)}</b></div>`).join("")}
      <div id="ez-track-result"></div>
      ${done ? "" : `<div style="margin-top:10px"><button type="button" class="ez-btn line" id="ez-track-now">${esc(T("easy.orders.track_now"))}</button>
        <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.orders.track_calls"))}</div></div>`}</div>`;
  }

  // 쿠팡 승인반려 사유 보기: 쿠팡 상태 이력에 적힌 문구를 그대로 보여 준다(사유가 없으면 WING 확인을 안내). 재제출·수정은 하지 않는다.
  async function productReasonDialog(p) {
    const kv = (k, v) => `<div class="ez-kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`;
    const reason = p.reason ? `<div class="ez-note warn" style="margin-top:12px"><span class="ez-dot warn">!</span><div>${esc(p.reason)}</div></div>`
      : `<div class="ez-note info" style="margin-top:12px"><span class="ez-dot info">i</span><div>${esc(T(p.reason_state === "NO_REASON" ? "easy.pstatus.no_reason" : "easy.pstatus.reason_pending"))}</div></div>`;
    // 사유 문구를 규칙으로 분류한 고치는 방법(AI 없음). 관측한 문구는 "이 사유는", 미관측 문구는 "예상되는 원인"으로만 표시한다.
    const advice = (Array.isArray(p.advice) ? p.advice : []).map((a) => {
      const steps = [1, 2, 3, 4].map((n) => tryT(`easy.advice.${a.code}.s${n}`, "")).filter(Boolean);
      const head = a.code === "UNKNOWN" ? "" : `<span class="ez-muted" style="font-size:12px">${esc(T(a.observed ? "easy.advice.observed" : "easy.advice.guess"))}</span> `;
      return `<div class="ez-note info" style="margin-top:10px"><span class="ez-dot info">i</span><div>${head}<b>${esc(tryT(`easy.advice.${a.code}.t`, ""))}</b>`
        + `<ol style="margin:6px 0 0 18px;padding:0">${steps.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></div></div>`;
    }).join("");
    const aiBlock = p.ai_advice ? productAdviceHtml(p.ai_advice) : "";
    const canAsk = !!p.reason && !p.ai_advice;
    const asked = await ezDialog({
      title: T("easy.pstatus.dialog_title"), okLabel: canAsk ? T("easy.advice.ask_btn") : T("easy.advice.close"), cancelLabel: canAsk ? T("easy.advice.close") : T("dialog.confirm.cancel"),
      bodyHtml: `<div>${kv(T("easy.pstatus.f_product"), p.product_name)}${kv(T("easy.today.coupang_no", { id: p.seller_product_id }), "")}${kv(T("easy.pstatus.f_checked"), p.checked_at || "—")}</div>${reason}${advice}${aiBlock}
        <p class="ez-muted" style="font-size:13px;margin-top:10px">${esc(T("easy.pstatus.howto"))}</p>`,
    });
    if (canAsk && asked) await productAdviceAsk(p);
  }

  // 공급처(온채널) 반품 요청 내용: 온채널 화면에서 이 주문의 "반품/교환 요청"을 접수한 뒤 "요청했어요"로 기록한다. 온채널로 보내는 것은 없다.
  async function supplierReturnDialog(c) {
    const kv = (k, v) => `<div class="ez-kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`;
    const body = `<div>${kv(T("easy.sr.f_code"), c.supplier_order_code || "—")}${kv(T("easy.sr.f_product"), `${c.product_title || "—"} × ${c.task_quantity || "—"}`)}
      ${kv(T("easy.sr.f_receipt"), c.receipt_id)}${kv(T("easy.today.return_reason"), c.reason_category || "—")}
      ${kv(T("easy.today.return_fault"), tryT(`easy.fault.${String(c.fault_by).toLowerCase()}`, String(c.fault_by || "—")))}</div>
      <div class="ez-note info" style="margin-top:12px"><span class="ez-dot info">i</span><div>${esc(T("easy.sr.howto"))}</div></div>`;
    const result = await ezDialog({ title: T("easy.sr.dialog_title"), bodyHtml: body, okLabel: T("easy.sr.mark") });
    if (!result) return;
    try {
      await api(`/purchase-tasks/supplier-returns/${encodeURIComponent(c.receipt_id)}/requested`, { method: "POST" });
      B.toast(T("easy.sr.marked"), "success");
      loadToday();
    } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
  }

  // 온채널 주문 상태를 읽어(온채널 조회 1회, 버튼을 눌렀을 때만) 반품이 접수됐는지 확인한다.
  async function supplierReturnCheck(c) {
    const ok = await B.askConfirm(T("easy.sr.check_body", { code: c.supplier_order_code || "—" }), { title: T("easy.sr.check_title"), okLabel: T("easy.sr.action_check") });
    if (!ok) return;
    try {
      const r = await api(`/purchase-tasks/supplier-returns/${encodeURIComponent(c.receipt_id)}/check`, { method: "POST" });
      B.toast(T(r.supplier_state === "REQUESTED" ? "easy.sr.check_not_yet" : "easy.sr.check_ok", { status: r.supplier_status_text || "—" }), r.supplier_state === "REQUESTED" ? "info" : "success");
      loadToday();
    } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
  }

  // 반품 회수 택배: 고객이 보낸 송장이 아직 없으면 그렇게 알려 준다.
  function returnPickupText(r) {
    if (!r.di) return T("easy.today.return_pickup_none");
    const name = COURIERS[r.dc] || r.dc || "";
    return `${name} ${r.di}`.trim();
  }

  // 배송 이후(반품·문의): 이 주문에 대해 쿠팡이 알려 준 것. 10분마다 읽고, 처리는 쿠팡 WING에서 한다.
  function afterHtml(order) {
    const list = order.after_sales || [];
    if (!order.task) return "";
    const line = (e) => {
      if (e.k === "RETURN") return [T("easy.after.return"), tryT(`easy.rs.${String(e.s).toLowerCase()}`, String(e.s || "")), `${e.rc ? ` · ${e.rc}` : ""} · ${T("easy.today.return_pickup")}: ${returnPickupText(e)}`];
      return [T(e.k === "INQ_CALL" ? "easy.after.inq_call" : "easy.after.inq_online"), T(e.s === "ANSWERED" ? "easy.after.answered" : "easy.today.inq_unanswered"), ""];
    };
    const rows = list.length ? list.map((e) => { const [k, v, extra] = line(e); return `<div class="ez-kv"><span>${esc(k)}</span><b>${esc(v + extra)}</b></div>`; }).join("")
      : `<div class="ez-muted" style="font-size:14px">${esc(T("easy.after.none"))}</div>`;
    return `<div class="ez-block" style="margin-top:16px"><h3 style="margin:0 0 6px;font-size:17px">${esc(T("easy.after.title"))}</h3>${rows}
      <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.after.note"))}</div></div>`;
  }

  async function trackNow(order) {
    const box = $("ez-track-result");
    const btn = $("ez-track-now");
    if (btn) btn.disabled = true;
    box.innerHTML = loadingHtml();
    try {
      const r = await api(`/purchase-tasks/${order.task.id}/delivery/refresh`, { method: "POST" });
      if (r.ok) {
        B.toast(T(r.delivered_marked ? "easy.orders.track_delivered" : (r.changed ? "easy.orders.track_changed" : "easy.orders.track_same")), "success");
        loadOrders({ orderId: order.order_id });
        return;
      }
      const blocked = r.blocked;
      box.innerHTML = `<div class="ez-note ${blocked ? "warn" : "bad"}" style="margin-top:10px"><div>${esc(blocked ? T("easy.orders.track_blocked") : (r.detail || T("easy.error")))}</div>${blocked ? `<button type="button" class="ez-btn sm" id="ez-track-allow" style="margin-top:8px">${esc(T("easy.orders.track_allow"))}</button>` : ""}</div>`;
      const allow = $("ez-track-allow");
      if (allow) allow.addEventListener("click", () => B.navigateTo("easy-approvals"));
    } catch (err) {
      box.innerHTML = `<div class="ez-note bad" style="margin-top:10px"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
    }
    if (btn) btn.disabled = false;
  }

  async function invoiceMarkDone(order) {
    const ok = await B.askConfirm(T("easy.orders.mark_body"), { title: T("easy.orders.mark_title"), okLabel: T("easy.orders.inv_done") });
    if (!ok) return;
    try {
      await api(`/purchase-tasks/${order.task.id}/coupang-invoice/mark-registered`, { method: "POST" });
      B.toast(T("easy.orders.mark_ok"), "success");
      loadOrders({ orderId: order.order_id });
    } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
  }

  // 쿠팡 발주서를 읽어(조회 1회, 버튼을 눌렀을 때만) 보낼 값과 막는 이유를 보여 준다.
  async function invoicePreview(order) {
    const box = $("ez-inv-result");
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/${order.task.id}/coupang-invoice/preview?live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const v = plan.values || {};
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const notAllowed = reasons.some((b) => b.code === "EXTERNAL_CALL_NOT_ALLOWED");
    let state;
    if (plan.already_registered) state = `<div class="ez-note ok"><div>${esc(T("easy.orders.inv_already"))}</div></div>`;
    else if (plan.sendable) state = `<div class="ez-note ok"><div>${esc(T("easy.orders.inv_ready"))}</div></div>`;
    else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}${notAllowed ? `<br><button type="button" class="ez-btn sm" id="ez-go-allow" style="margin-top:8px">${esc(T("easy.orders.go_allow"))}</button>` : ""}</div></div>`;
    box.innerHTML = `<div style="margin-top:10px">${[[T("easy.orders.coupang_state"), plan.coupang && plan.coupang.status ? plan.coupang.status : "—"], [T("easy.orders.send_values"), `${v.courier_name || "—"} · ${v.invoice_number || "—"}`]]
      .map(([k, val]) => `<div class="ez-kv"><span>${esc(k)}</span><b>${esc(val)}</b></div>`).join("")}</div>${state}
      ${plan.sendable ? `<button type="button" class="ez-btn big" id="ez-inv-send" style="margin-top:10px">${esc(T("easy.orders.inv_send"))}</button>` : ""}
      ${plan.already_registered ? `<button type="button" class="ez-btn line big" id="ez-inv-record" style="margin-top:10px">${esc(T("easy.orders.inv_record"))}</button>` : ""}`;
    const go = $("ez-go-allow");
    if (go) go.addEventListener("click", () => B.navigateTo("easy-approvals"));
    const rec = $("ez-inv-record");
    if (rec) rec.addEventListener("click", () => invoiceMarkDone(order));
    const send = $("ez-inv-send");
    if (send) send.addEventListener("click", async () => {
      const ok = await B.askConfirm(T("easy.orders.send_confirm", { order: order.channel_order_id, box: v.shipment_box_id, courier: v.courier_name, number: v.invoice_number }), { title: T("easy.orders.inv_title"), okLabel: T("easy.orders.inv_send") });
      if (!ok) return;
      const token = await B.promptRecentAuthToken();
      if (token === null) return;
      try {
        const result = await api(`/purchase-tasks/${order.task.id}/coupang-invoice/send`, {
          method: "POST", headers: { "X-Recent-Auth-Token": token },
          body: JSON.stringify({ delivery_company_code: v.delivery_company_code, invoice_number: v.invoice_number, shipment_box_id: v.shipment_box_id, vendor_item_ids: v.vendor_item_ids, retry_action_required: false }),
        });
        B.toast(result.outcome === "SUCCEEDED" ? T("easy.orders.send_ok") : `${result.outcome}: ${result.detail || ""}`, result.outcome === "SUCCEEDED" ? "success" : "error");
        loadOrders({ orderId: order.order_id });
      } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
    });
  }

  // =========================================================
  // 정산·수익
  // =========================================================

  // 쿠팡이 알려 준 정산 현황(읽기 전용): 지급 예정·지급 완료와 금액. 주문 당시 예상값과 다를 수 있다.
  function settlementBlock(today) {
    const list = (today && today.after_sales && today.after_sales.settlements) || [];
    const rows = list.length ? `<table class="ez-table"><thead><tr><th>${esc(T("easy.settle.month"))}</th><th>${esc(T("easy.settle.type"))}</th><th>${esc(T("easy.settle.date"))}</th><th>${esc(T("easy.settle.sale"))}</th><th>${esc(T("easy.settle.final"))}</th><th>${esc(T("easy.col.state"))}</th></tr></thead>
      <tbody>${list.map((s) => `<tr style="cursor:default"><td class="ez-num">${esc(s.id || "—")}</td><td>${esc(tryT(`easy.settle.type_${String(s.ty).toLowerCase()}`, String(s.ty || "—")))}</td><td class="ez-num">${esc(s.d || "—")}</td>
        <td class="ez-num">${won(s.sale)}</td><td class="ez-num"><b>${won(s.fin)}</b></td><td><span class="ez-chip ${s.st === "DONE" ? "ok" : "wait"}">${esc(tryT(`easy.settle.st_${String(s.st).toLowerCase()}`, String(s.st || "—")))}</span></td></tr>`).join("")}</tbody></table>`
      : `<div class="ez-muted" style="font-size:14px;margin:6px 0">${esc(T("easy.settle.none"))}</div>`;
    const checked = today && today.settlement_checked_at;
    const checkedLine = `<div class="ez-faint" style="font-size:13px;margin:4px 0 8px">${esc(checked ? T("easy.settle.checked", { at: B.fmtDate(checked) }) : T("easy.settle.not_checked"))}</div>`;
    return `<div class="ez-sec">${esc(T("easy.settle.title"))}<small>${esc(T("easy.settle.sub"))}</small></div>${checkedLine}${rows}`;
  }

  async function loadMoney() {
    const root = $("ez-root-easy-money");
    root.innerHTML = loadingHtml();
    let tasks, wq, today;
    try {
      [tasks, wq, today] = await Promise.all([api("/purchase-tasks"), B.wq.fetchItems().catch(() => null), api("/easy-view/today").catch(() => null)]);
    } catch (err) { errorBox(root, err); return; }
    const list = (Array.isArray(tasks) ? tasks : (tasks && tasks.items) || []).filter((t) => (t.source_order_id || 0) > 0);
    const num = (v) => (v === null || v === undefined ? 0 : Number(v));
    const revenue = list.reduce((s, x) => s + num(x.coupang_sale_amount), 0);
    const fee = list.reduce((s, x) => s + num(x.coupang_fee_amount), 0);
    const profit = list.reduce((s, x) => s + num(x.expected_net_profit), 0);
    const known = list.filter((x) => x.expected_margin_rate !== null && x.expected_margin_rate !== undefined);
    const avg = known.length ? known.reduce((s, x) => s + num(x.expected_margin_rate), 0) / known.length : null;
    const diffs = wq ? wq.items.filter((i) => i.kind === "difference").length : null;
    const maxProfit = Math.max(1, ...list.map((x) => Math.abs(num(x.expected_net_profit))));

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.nav.money"))}</h1><div class="ez-lead">${esc(T("easy.money.lead"))}</div></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" class="ez-btn line" data-ez-go="channel-settlement">${esc(T("easy.money.go_settlement"))}</button><button type="button" class="ez-btn line" data-ez-go="finance">${esc(T("easy.money.go_finance"))}</button></div></div>
      <div class="ez-cards">
        <div class="ez-card"><div class="k">${esc(T("easy.money.revenue"))}</div><div class="v">${won(revenue)}</div><div class="s">${esc(T("easy.money.revenue_s", { n: list.length }))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.money.fee"))}</div><div class="v">${won(fee)}</div><div class="s">${esc(T("easy.money.fee_s"))}</div></div>
        <div class="ez-card accent"><div class="k">${esc(T("easy.money.profit"))}</div><div class="v ${profit < 0 ? "ez-c-red" : "ez-c-green"}">${won(profit)}</div><div class="s">${esc(T("easy.money.profit_s"))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.money.margin"))}</div><div class="v">${avg === null ? "—" : `${Math.round(avg * 10) / 10}%`}</div><div class="s">${esc(diffs === null ? T("easy.money.diff_unknown") : T("easy.money.diff_s", { n: diffs }))}</div></div>
      </div>
      <div class="ez-note info" style="margin-top:14px"><span class="ez-dot info">i</span><div>${esc(T("easy.money.note"))}</div></div>
      ${settlementBlock(today)}
      <div class="ez-sec">${esc(T("easy.money.by_order"))}</div>
      ${list.length ? `<table class="ez-table"><thead><tr><th>${esc(T("easy.col.product"))}</th><th>${esc(T("easy.money.revenue"))}</th><th>${esc(T("easy.money.fee"))}</th><th>${esc(T("easy.money.profit"))}</th><th>${esc(T("easy.money.margin"))}</th></tr></thead>
        <tbody>${list.map((x) => `<tr style="cursor:default"><td><span class="ez-cell"><span class="ez-thumb">${icon("bag")}</span><span><b>${esc(x.product_title)}</b><small>#${x.source_order_id} · ${esc(B.taskStatusLabel(x.status))}</small></span></span></td>
          <td class="ez-num">${won(x.coupang_sale_amount)}</td><td class="ez-num">${won(x.coupang_fee_amount)}</td>
          <td style="min-width:150px"><div class="ez-num ${num(x.expected_net_profit) < 0 ? "ez-c-red" : ""}" style="font-weight:700">${won(x.expected_net_profit)}</div><div class="ez-bar ${num(x.expected_net_profit) < 0 ? "neg" : ""}"><i style="width:${Math.round((Math.abs(num(x.expected_net_profit)) / maxProfit) * 100)}%"></i></div></td>
          <td class="ez-num">${x.expected_margin_rate === null || x.expected_margin_rate === undefined ? "—" : `${Math.round(num(x.expected_margin_rate) * 10) / 10}%`}</td></tr>`).join("")}</tbody></table>` : emptyHtml("easy.money.empty_title", "easy.money.empty_sub")}
      <div class="ez-sec">${esc(T("easy.money.more_title"))}<small>${esc(T("easy.money.more_sub"))}</small></div>
      <div class="ez-groups">${[["settlement-differences", "easy.money.l_diff"], ["finance", "easy.money.l_finance"], ["channel-settlement", "easy.money.l_channel"]].map(([v, k]) =>
        `<div class="ez-group"><h3>${esc(T(k))}</h3><button type="button" data-ez-go="${v}">${esc(T("easy.open"))} ›</button></div>`).join("")}</div>`;
    root.querySelectorAll("[data-ez-go]").forEach((btn) => btn.addEventListener("click", () => B.navigateTo(btn.dataset.ezGo)));
  }

  // =========================================================
  // 승인 센터
  // =========================================================

  async function loadApprovals(opts) {
    const root = $("ez-root-easy-approvals");
    root.innerHTML = loadingHtml();
    let ext, safety, today;
    try {
      [ext, safety, today] = await Promise.all([
        api("/external-calls/summary"), api("/console/api/safety-status").catch(() => null), api("/easy-view/today").catch(() => null),
      ]);
    } catch (err) { errorBox(root, err); return; }
    const installed = ext.installed !== false;
    const modeKey = safety && safety.schema_ready ? safety.mode : null;
    const estop = safety && safety.emergency_stop && safety.emergency_stop.is_active;
    const todo = (today && today.outbound_days_todo) || [];
    setBadge((ext.pending || []).length + todo.length);

    const modeDefs = [["RECOMMEND_ONLY", "easy.ap.mode_rec"], ["OPERATOR_APPROVAL", "easy.ap.mode_op"], ["LIMITED_AUTOMATION", "easy.ap.mode_auto"], ["DISABLED", "easy.ap.mode_off"]];
    const modes = modeDefs.map(([code, key]) => `<div class="${modeKey === code ? "on" : ""}"><b>${esc(T(key))}${modeKey === code ? ` · ${esc(T("easy.ap.now"))}` : ""}</b><span>${esc(T(`${key}_s`))}</span></div>`).join("");

    const limits = (a) => {
      if (a.mode === "STANDING") return `<span>${esc(T("easy.ap.l_hour"))} <b>${a.per_hour_limit}${esc(T("easy.times_short"))}</b></span><span>${esc(T("easy.ap.l_until"))} <b>${a.expires_at ? esc(B.fmtDate(a.expires_at)) : "—"}</b></span>`;
      return `<span>${esc(T("easy.ap.l_count"))} <b>${a.remaining_count !== null && a.remaining_count !== undefined ? a.remaining_count : a.requested_count}${esc(T("easy.times_short"))}</b></span>${a.expires_at ? `<span>${esc(T("easy.ap.l_until"))} <b>${esc(B.fmtDate(a.expires_at))}</b></span>` : ""}`;
    };
    const rowName = (a) => `<span class="ez-cell"><span class="ez-thumb">${icon("shield")}</span><span><b>${esc(a.purpose_label)}</b><small>${esc(a.system_label)} · ${esc(a.kind_label)}${a.reason ? ` · ${esc(a.reason)}` : ""}</small></span></span>`;

    const pendingRows = (ext.pending || []).map((a) => `<tr data-key="${a.id}" style="cursor:default"><td>${rowName(a)}</td><td><b>${esc(a.system_label)}</b></td><td><div class="ez-lim">${limits(a)}</div></td>
      <td><div style="display:flex;gap:8px"><button type="button" class="ez-btn sm" data-ez-approve="${a.id}">${esc(T("easy.ap.allow"))}</button><button type="button" class="ez-btn red sm" data-ez-reject="${a.id}">${esc(T("easy.ap.reject"))}</button></div></td></tr>`).join("");
    const activeRows = (ext.active || []).map((a) => `<tr style="cursor:default"><td>${rowName(a)}</td><td><b>${esc(a.system_label)}</b></td><td><div class="ez-lim">${limits(a)}</div></td>
      <td><div style="display:flex;gap:8px;align-items:center">${chip("ok", T("easy.ap.allowed"))}<button type="button" class="ez-btn line sm" data-ez-revoke="${a.id}">${esc(T("easy.ap.revoke"))}</button></div></td></tr>`).join("");
    const table = (rows, empty) => (rows ? `<table class="ez-table"><thead><tr><th>${esc(T("easy.ap.col_req"))}</th><th>${esc(T("easy.ap.col_to"))}</th><th>${esc(T("easy.ap.col_limit"))}</th><th>${esc(T("easy.col.next"))}</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="ez-empty">${esc(T(empty))}</div>`);

    const todayCounts = Object.entries(ext.today || {}).map(([system, kinds]) => Object.entries(kinds).map(([kind, n]) => `<li><span>${esc(T(`easy.sys.${system.toLowerCase()}`))} ${esc(T(`easy.kind.${kind.toLowerCase()}`))}</span><b class="ez-num">${esc(T("easy.times", { n }))}</b></li>`).join("")).join("");
    const recent = (ext.recent || []).slice(0, 6).map((r) => `<li><span>${esc(r.system_label)} ${esc(r.kind_label)} · ${esc(r.purpose_label)}</span><b>${esc(T(`easy.outcome.${String(r.outcome).toLowerCase()}`))}</b></li>`).join("");

    const renewable = Array.isArray(ext.renewable) ? ext.renewable : [];
    const renewBlock = renewable.length ? `<div class="ez-note warn" style="margin:0 0 14px"><span class="ez-dot warn">!</span><div>
        <b>${esc(T("easy.ap.renew_title", { n: renewable.length }))}</b> ${esc(T("easy.ap.renew_sub"))}
        <ul style="margin:8px 0 10px 18px">${renewable.map((r) => `<li>${esc(r.purpose_label)} · ${esc(T("easy.ap.l_hour"))} ${r.per_hour_limit}${esc(T("easy.times_short"))} — ${esc(r.state === "EXPIRED" ? T("easy.ap.renew_expired") : T("easy.ap.renew_hours", { h: r.hours_left }))}</li>`).join("")}</ul>
        <button type="button" class="ez-btn sm" id="ez-ap-renew">${esc(T("easy.ap.renew_btn"))}</button></div></div>` : "";

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.nav.approvals"))}</h1><div class="ez-lead">${esc(T("easy.ap.lead"))}</div></div>
        <button type="button" class="ez-btn line" id="ez-ap-mode">${esc(T("easy.ap.mode_link"))}</button></div>
      <div class="ez-modes">${modes}</div>
      ${installed ? "" : `<div class="ez-note warn"><span class="ez-dot warn">!</span><div>${esc(T("easy.ap.not_installed"))}</div></div>`}
      <div class="ez-layout">
        <div class="ez-main">
          ${renewBlock}
          <div class="ez-sec">${esc(T("easy.ap.pending_title"))} <small>${esc(T("easy.count", { n: (ext.pending || []).length }))}</small></div>
          ${table(pendingRows, "easy.ap.pending_none")}
          <div class="ez-sec">${esc(T("easy.ap.active_title"))} <small>${esc(T("easy.count", { n: (ext.active || []).length }))}</small></div>
          ${table(activeRows, "easy.ap.active_none")}
          ${installed ? `<div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap"><button type="button" class="ez-btn ghost" id="ez-ap-grant-detect">${esc(T("easy.ap.grant_detect"))}</button><button type="button" class="ez-btn ghost" id="ez-ap-grant-track">${esc(T("easy.ap.grant_track"))}</button><button type="button" class="ez-btn ghost" id="ez-ap-grant-after">${esc(T("easy.ap.grant_after"))}</button><button type="button" class="ez-btn ghost" id="ez-ap-grant-pstatus">${esc(T("easy.ap.grant_pstatus"))}</button><button type="button" class="ez-btn ghost" id="ez-ap-grant-custom">${esc(T("easy.ap.grant_custom"))}</button></div>` : ""}
          <div class="ez-sec" id="ez-days-anchor">${esc(T("easy.ap.days_title"))}<small>${esc(T("easy.ap.days_sub"))}</small></div>
          <div id="ez-days-box">${todo.length ? todo.map((p) => `<div class="ez-block" style="margin-top:10px"><div class="ez-field" style="grid-template-columns:1fr auto"><div class="v">${esc(p.product_name)}<small>${esc(T("easy.today.coupang_no", { id: p.seller_product_id }))} · ${esc(p.saved_days ? T("easy.today.days_sub_saved", { days: p.saved_days }) : T("easy.today.days_sub_unset", { days: 1 }))}</small></div>
            <button type="button" class="ez-btn sm" data-ez-days="${esc(p.seller_product_id)}">${esc(T("easy.ap.days_check"))}</button></div><div id="ez-days-${esc(p.seller_product_id)}" style="padding:0 18px 12px"></div></div>`).join("") : `<div class="ez-empty">${esc(T("easy.ap.days_none"))}</div>`}</div>
          <div class="ez-sec" id="ez-stock-anchor">${esc(T("easy.stock.title"))}<small>${esc(T("easy.stock.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
              <label>${esc(T("easy.stock.item_label"))}<br><input type="text" inputmode="numeric" id="ez-stock-item" maxlength="20" placeholder="${esc(T("easy.stock.item_placeholder"))}"></label>
              <label>${esc(T("easy.stock.qty_label"))}<br><input type="number" id="ez-stock-qty" min="0" max="${STOCK_MAX}" step="1"></label>
              <button type="button" class="ez-btn sm" id="ez-stock-check">${esc(T("easy.stock.check"))}</button>
            </div>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.stock.where_id"))}</div>
            <div id="ez-stock-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-sec" id="ez-price-anchor">${esc(T("easy.price.title"))}<small>${esc(T("easy.price.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
              <label>${esc(T("easy.price.item_label"))}<br><input type="text" inputmode="numeric" id="ez-price-item" maxlength="20" placeholder="${esc(T("easy.stock.item_placeholder"))}"></label>
              <label>${esc(T("easy.price.price_label"))}<br><input type="number" id="ez-price-new" min="10" max="${PRICE_MAX}" step="10"></label>
              <label>${esc(T("easy.price.cost_label"))}<br><input type="number" id="ez-price-cost" min="1" max="${PRICE_MAX}" step="1"></label>
              <label>${esc(T("easy.price.ship_label"))}<br><input type="number" id="ez-price-ship" min="0" max="${PRICE_MAX}" step="1"></label>
              <button type="button" class="ez-btn sm" id="ez-price-check">${esc(T("easy.price.check"))}</button>
            </div>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.price.where"))}</div>
            <div id="ez-price-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-sec" id="ez-pname-anchor">${esc(T("easy.pname.title"))}<small>${esc(T("easy.pname.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
              <label>${esc(T("easy.pname.id_label"))}<br><input type="text" inputmode="numeric" id="ez-pname-id" maxlength="20" placeholder="${esc(T("easy.pname.id_placeholder"))}"></label>
              <label style="flex:1;min-width:240px">${esc(T("easy.pname.display_label"))}<br><input type="text" id="ez-pname-display" maxlength="${PNAME_MAX}" style="width:100%"></label>
              <label style="flex:1;min-width:200px">${esc(T("easy.pname.seller_label"))}<br><input type="text" id="ez-pname-seller" maxlength="${PNAME_MAX}" style="width:100%"></label>
              <button type="button" class="ez-btn sm" id="ez-pname-check">${esc(T("easy.pname.check"))}</button>
            </div>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.pname.where"))}</div>
            <div id="ez-pname-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-sec" id="ez-inq-anchor">${esc(T("easy.inq.title"))}<small>${esc(T("easy.inq.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <button type="button" class="ez-btn sm" id="ez-inq-load">${esc(T("easy.inq.load"))}</button>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.inq.where"))}</div>
            <div id="ez-inq-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-sec" id="ez-cc-anchor">${esc(T("easy.cc.title"))}<small>${esc(T("easy.cc.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <button type="button" class="ez-btn sm" id="ez-cc-load">${esc(T("easy.cc.load"))}</button>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.cc.where"))}</div>
            <div id="ez-cc-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px" id="ez-ccc-anchor">
            <b>${esc(T("easy.ccc.title"))}</b> <span class="ez-faint" style="font-size:13px">${esc(T("easy.ccc.sub"))}</span>
            <div style="margin-top:8px"><button type="button" class="ez-btn sm" id="ez-ccc-load">${esc(T("easy.ccc.load"))}</button></div>
            <div id="ez-ccc-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-sec" id="ez-ret-anchor">${esc(T("easy.ret.title"))}<small>${esc(T("easy.ret.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
              <label>${esc(T("easy.ret.id_label"))}<br><input type="text" inputmode="numeric" id="ez-ret-id" maxlength="20" placeholder="${esc(T("easy.ret.id_placeholder"))}"></label>
              <label>${esc(T("easy.ret.action_label"))}<br><select id="ez-ret-action"><option value="RECEIVE">${esc(T("easy.ret.action_receive"))}</option><option value="APPROVE">${esc(T("easy.ret.action_approve"))}</option></select></label>
              <button type="button" class="ez-btn sm" id="ez-ret-check">${esc(T("easy.ret.check"))}</button>
            </div>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.ret.where"))}</div>
            <div id="ez-ret-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px" id="ez-pick-anchor">
            <b>${esc(T("easy.pick.title"))}</b> <span class="ez-faint" style="font-size:13px">${esc(T("easy.pick.sub"))}</span>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:8px">
              <label>${esc(T("easy.ret.id_label"))}<br><input type="text" inputmode="numeric" id="ez-pick-id" maxlength="20" placeholder="${esc(T("easy.ret.id_placeholder"))}"></label>
              <label>${esc(T("easy.pick.courier_label"))}<br><input type="text" id="ez-pick-courier" maxlength="30" placeholder="${esc(T("easy.pick.courier_placeholder"))}"></label>
              <label>${esc(T("easy.pick.invoice_label"))}<br><input type="text" id="ez-pick-invoice" maxlength="30"></label>
              <label>${esc(T("easy.pick.reg_label"))}<br><input type="text" id="ez-pick-reg" maxlength="30"></label>
              <button type="button" class="ez-btn sm" id="ez-pick-check">${esc(T("easy.ret.check"))}</button>
            </div>
            <div id="ez-pick-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-sec" id="ez-exch-anchor">${esc(T("easy.exch.title"))}<small>${esc(T("easy.exch.sub"))}</small></div>
          <div class="ez-block" style="margin-top:10px;padding:12px 18px">
            <button type="button" class="ez-btn sm" id="ez-exch-load">${esc(T("easy.exch.load"))}</button>
            <div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.exch.where"))}</div>
            <div id="ez-exch-box" style="margin-top:10px"></div>
          </div>
          <div class="ez-note info"><span class="ez-dot info">i</span><div><b>${esc(T("easy.ap.block_title"))}</b> ${esc(T("easy.ap.block_body"))}</div></div>
        </div>
        <aside class="ez-panel">
          <h2>${esc(T("easy.ap.today_title"))}</h2>
          <ul class="ez-list">${todayCounts || `<li><span>${esc(T("easy.ap.today_none"))}</span><b>—</b></li>`}</ul>
          ${recent ? `<div class="ez-sec" style="margin-top:18px;font-size:16px">${esc(T("easy.ap.recent_title"))}</div><ul class="ez-list">${recent}</ul>` : ""}
          <div class="ez-faint" style="font-size:12.5px;margin-top:8px">${esc(T("easy.ap.log_note"))}</div>
          <div class="ez-stop"><h3>${esc(T("easy.ap.stop_title"))}</h3><p class="ez-muted" style="font-size:14px;margin:4px 0 14px">${esc(T(estop ? "easy.ap.stop_on" : "easy.ap.stop_body"))}</p>
            <button type="button" class="ez-btn ${estop ? "line" : "danger"} big" id="ez-ap-stop">${esc(T(estop ? "easy.ap.stop_release" : "easy.ap.stop_now"))}</button></div>
        </aside>
      </div>`;

    $("ez-ap-mode").addEventListener("click", () => B.navigateTo("safety"));
    const renewBtn = $("ez-ap-renew");
    if (renewBtn) renewBtn.addEventListener("click", () => renewAllDialog(renewable));
    root.querySelectorAll("[data-ez-approve]").forEach((btn) => btn.addEventListener("click", () => approveDialog((ext.pending || []).find((a) => String(a.id) === btn.dataset.ezApprove))));
    root.querySelectorAll("[data-ez-reject]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/external-calls/requests/${btn.dataset.ezReject}/reject`, { method: "POST" }); B.toast(T("easy.ap.rejected"), "success"); loadApprovals(); }
      catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
    }));
    root.querySelectorAll("[data-ez-revoke]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/external-calls/allowances/${btn.dataset.ezRevoke}/revoke`, { method: "POST" }); B.toast(T("easy.ap.revoked"), "success"); loadApprovals(); }
      catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
    }));
    const detect = $("ez-ap-grant-detect");
    if (detect) detect.addEventListener("click", () => grantDialog({ system: "COUPANG", kind: "READ", purpose: "ORDER_DETECTION", mode: "STANDING", per_hour_limit: 14, hours: 24 }));
    const track = $("ez-ap-grant-track");
    if (track) track.addEventListener("click", () => grantDialog({ system: "COUPANG", kind: "READ", purpose: "ORDER_TRACKING", mode: "STANDING", per_hour_limit: 30, hours: 24 }));
    const after = $("ez-ap-grant-after");
    if (after) after.addEventListener("click", () => grantDialog({ system: "COUPANG", kind: "READ", purpose: "AFTER_SALES_CHECK", mode: "STANDING", per_hour_limit: 30, hours: 24 }));
    const pstatus = $("ez-ap-grant-pstatus");
    if (pstatus) pstatus.addEventListener("click", () => grantDialog({ system: "COUPANG", kind: "READ", purpose: "PRODUCT_STATUS_CHECK", mode: "STANDING", per_hour_limit: 12, hours: 24 }));
    const custom = $("ez-ap-grant-custom");
    if (custom) custom.addEventListener("click", () => grantDialog(null));
    root.querySelectorAll("[data-ez-days]").forEach((btn) => btn.addEventListener("click", () => daysPreview(btn.dataset.ezDays)));
    $("ez-stock-check").addEventListener("click", () => stockPreview($("ez-stock-item").value, $("ez-stock-qty").value));
    $("ez-price-check").addEventListener("click", () => pricePreview($("ez-price-item").value, $("ez-price-new").value, $("ez-price-cost").value, $("ez-price-ship").value));
    $("ez-pname-check").addEventListener("click", () => pnamePreview($("ez-pname-id").value, $("ez-pname-display").value, $("ez-pname-seller").value));
    $("ez-inq-load").addEventListener("click", () => inquiryList());
    $("ez-cc-load").addEventListener("click", () => callCenterList());
    $("ez-ccc-load").addEventListener("click", () => callCenterConfirmList());
    $("ez-ret-check").addEventListener("click", () => returnPreview($("ez-ret-id").value, $("ez-ret-action").value));
    $("ez-pick-check").addEventListener("click", () => pickupPreview($("ez-pick-id").value, $("ez-pick-courier").value, $("ez-pick-invoice").value, $("ez-pick-reg").value));
    $("ez-exch-load").addEventListener("click", () => exchangeList());
    $("ez-ap-stop").addEventListener("click", () => toggleEstop(estop));
    if (opts && opts.productId) { const anchor = $("ez-days-anchor"); if (anchor) anchor.scrollIntoView({ block: "start" }); }
  }

  const GRANT_PURPOSES = [["ORDER_DETECTION", "easy.purpose.order_detection"], ["ORDER_TRACKING", "easy.purpose.tracking"], ["AFTER_SALES_CHECK", "easy.purpose.after"], ["PRODUCT_STATUS_CHECK", "easy.purpose.pstatus"], ["INVOICE_UPLOAD", "easy.purpose.invoice"], ["PRODUCT_OUTBOUND_DAYS", "easy.purpose.days"], ["STOCK_UPDATE", "easy.purpose.stock"], ["PRICE_UPDATE", "easy.purpose.price"], ["PRODUCT_NAME_UPDATE", "easy.purpose.pname"], ["INQUIRY_REPLY", "easy.purpose.inq"], ["RETURN_PROCESS", "easy.purpose.ret"], ["ANY", "easy.purpose.any"]];

  // 비밀번호 재확인 창에 다시 보여 줄 "무엇을 허용하는지" 요약(2026-10-29). 목적·종류·보내는 곳·범위를 사람이 읽는 말로 만든다.
  // 변경(쓰기)과 '모든 목적'은 눈에 띄게 표시한다. 상시형은 시간당 상한이 있을 때, 아니면 횟수형이다.
  function allowanceSummaryRows(a) {
    const purposeKey = (GRANT_PURPOSES.find(([v]) => v === a.purpose) || [])[1];
    const purpose = a.purpose_label || (purposeKey ? T(purposeKey) : String(a.purpose || ""));
    const write = a.kind === "WRITE";
    const kind = write ? T("easy.ap.sum_write") : T("easy.kind.read");
    const system = a.system_label || T(a.system === "ONCH" ? "easy.sys.onch" : "easy.sys.coupang");
    const perHour = Number(a.per_hour_limit) || 0;
    const amount = perHour
      ? T("easy.ap.sum_standing", { n: perHour, h: Number(a.hours) || 24 })
      : (Number(a.count) ? T("easy.ap.sum_count", { n: Number(a.count) }) : T("easy.ap.sum_count_unset"));
    return [
      { label: T("easy.ap.sum_purpose"), value: purpose, strong: a.purpose === "ANY" },
      { label: T("easy.ap.sum_kind"), value: kind, strong: write },
      { label: T("easy.ap.sum_system"), value: system },
      { label: T("easy.ap.sum_amount"), value: amount },
    ];
  }

  // 허용 조건을 정하는 대화상자(횟수형은 횟수, 상시형은 시간당 상한과 기간)
  async function approveDialog(allowance) {
    if (!allowance) return;
    const standing = allowance.mode === "STANDING";
    const body = standing
      ? `<p>${esc(T("easy.ap.d_standing_p"))}</p><label>${esc(T("easy.ap.d_per_hour"))}</label><input name="per_hour" type="number" min="1" max="120" value="${allowance.per_hour_limit || 14}">
         <label>${esc(T("easy.ap.d_hours"))}</label><input name="hours" type="number" min="1" max="720" value="24">`
      : `<p>${esc(T("easy.ap.d_counted_p"))}</p><label>${esc(T("easy.ap.d_count"))}</label><input name="count" type="number" min="1" max="50" value="${allowance.requested_count || 1}">`;
    const result = await ezDialog({ title: `${allowance.purpose_label} · ${allowance.system_label} ${allowance.kind_label}`, bodyHtml: body, okLabel: T("easy.ap.allow") });
    if (!result) return;
    const payload = standing ? { per_hour_limit: Number(result.get("per_hour")), hours: Number(result.get("hours")) } : { count: Number(result.get("count")) };
    const summary = allowanceSummaryRows({ ...allowance, ...(standing ? { per_hour_limit: payload.per_hour_limit, hours: payload.hours } : { per_hour_limit: null, count: payload.count }) });
    const token = await B.promptRecentAuthToken({ summary });
    if (token === null) return;
    try {
      await api(`/external-calls/requests/${allowance.id}/approve`, { method: "POST", headers: { "X-Recent-Auth-Token": token }, body: JSON.stringify(payload) });
      B.toast(T("easy.ap.approved"), "success");
      loadApprovals();
    } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
  }

  // 만료 임박·만료 상시 조회 허용을 같은 한도로 24시간 일괄 연장한다. 확인창 → 비밀번호 재확인 순서이고, 변경(쓰기) 허용은 연장하지 않는다.
  async function renewAllDialog(items) {
    const list = `<ul style="margin:8px 0 0 18px">${items.map((r) => `<li>${esc(r.purpose_label)} · ${esc(T("easy.ap.l_hour"))} ${r.per_hour_limit}${esc(T("easy.times_short"))}</li>`).join("")}</ul>`;
    const result = await ezDialog({ title: T("easy.ap.renew_confirm_title"), bodyHtml: `<p>${esc(T("easy.ap.renew_confirm_body"))}</p>${list}`, okLabel: T("easy.ap.renew_btn") });
    if (!result) return;
    const token = await B.promptRecentAuthToken();
    if (token === null) return;
    try {
      const r = await api("/external-calls/renew-expiring", { method: "POST", headers: { "X-Recent-Auth-Token": token }, body: JSON.stringify({}) });
      B.toast(T("easy.ap.renew_done", { n: (r.renewed || []).length }), "success");
      loadApprovals();
    } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
  }

  // 목적은 미리 골라 두지 않는다(빈 칸 → 고르지 않으면 허용하지 않음). 비밀번호 확인 창에 허용할 내용을 다시 보여 준다(2026-10-29).
  async function grantDialog(preset) {
    const picked = preset && preset.purpose;
    const body = `<label>${esc(T("easy.ap.d_purpose"))}</label><select name="purpose" required><option value="" ${picked ? "" : "selected"}>${esc(T("easy.ap.d_pick"))}</option>${GRANT_PURPOSES.map(([v, k]) => `<option value="${v}" ${picked === v ? "selected" : ""}>${esc(T(k))}</option>`).join("")}</select>
      <label>${esc(T("easy.ap.d_kind"))}</label><select name="kind"><option value="READ" ${!preset || preset.kind === "READ" ? "selected" : ""}>${esc(T("easy.kind.read"))}</option><option value="WRITE" ${preset && preset.kind === "WRITE" ? "selected" : ""}>${esc(T("easy.kind.write"))}</option></select>
      <label>${esc(T("easy.ap.d_system"))}</label><select name="system"><option value="COUPANG">${esc(T("easy.sys.coupang"))}</option><option value="ONCH">${esc(T("easy.sys.onch"))}</option></select>
      <label>${esc(T("easy.ap.d_count"))} <span class="ez-faint" style="font-weight:400">${esc(T("easy.ap.d_count_hint"))}</span></label><input name="count" type="number" min="1" max="50" value="${preset && preset.mode === "STANDING" ? "" : (preset && Number(preset.count)) || 1}">
      <label>${esc(T("easy.ap.d_per_hour"))} <span class="ez-faint" style="font-weight:400">${esc(T("easy.ap.d_standing_hint"))}</span></label><input name="per_hour" type="number" min="1" max="120" value="${preset && preset.per_hour_limit ? preset.per_hour_limit : ""}">
      <label>${esc(T("easy.ap.d_hours"))}</label><input name="hours" type="number" min="1" max="720" value="${preset && preset.hours ? preset.hours : ""}">`;
    const result = await ezDialog({ title: T("easy.ap.grant_title"), bodyHtml: body, okLabel: T("easy.ap.allow") });
    if (!result) return;
    const count = Number(result.get("count")) || null;
    const perHour = Number(result.get("per_hour")) || null;
    const hours = Number(result.get("hours")) || null;
    const standing = Boolean(perHour);
    if (!result.get("purpose")) {
      B.toast(T("easy.ap.d_pick_needed"), "error");
      return grantDialog({ purpose: "", kind: result.get("kind"), mode: standing ? "STANDING" : "COUNTED", count, per_hour_limit: perHour, hours });
    }
    const summary = allowanceSummaryRows({ purpose: result.get("purpose"), kind: result.get("kind"), system: result.get("system"), count, per_hour_limit: perHour, hours });
    const token = await B.promptRecentAuthToken({ summary });
    if (token === null) return;
    try {
      await api("/external-calls/allowances", {
        method: "POST", headers: { "X-Recent-Auth-Token": token },
        body: JSON.stringify({ system: result.get("system"), kind: result.get("kind"), purpose: result.get("purpose"), mode: standing ? "STANDING" : "COUNTED", count: standing ? null : count, per_hour_limit: standing ? perHour : null, hours: standing ? hours : (hours || null) }),
      });
      B.toast(T("easy.ap.approved"), "success");
      loadApprovals();
    } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
  }

  async function toggleEstop(active) {
    if (active) {
      const { confirmed } = await B.confirmDialog({ title: T("safety.deactivate_confirm_title"), body: T("safety.deactivate_confirm_body"), okLabel: T("safety.deactivate_confirm_ok") });
      if (!confirmed) return;
      try { await api("/console/api/safety/emergency-stop/deactivate", { method: "POST" }); B.toast(T("safety.deactivate_success"), "success"); loadApprovals(); }
      catch (err) { B.toast((err && err.message) || T("safety.deactivate_error"), "error"); }
      return;
    }
    const { confirmed, value } = await B.confirmDialog({ title: T("safety.activate_confirm_title"), body: T("safety.activate_confirm_body"), requireReason: true, okLabel: T("safety.activate_confirm_ok") });
    if (!confirmed) return;
    try { await api("/console/api/safety/emergency-stop/activate", { method: "POST", body: JSON.stringify({ reason: value }) }); B.toast(T("safety.activate_success"), "success"); loadApprovals(); }
    catch (err) { B.toast((err && err.message) || T("safety.activate_error"), "error"); }
  }

  // 등록 상품 출고일 변경: 미리보기(쿠팡 조회, 허용 필요) → 확인 → 변경 → 서버가 다시 읽어 확인
  async function daysPreview(productId) {
    const box = $(`ez-days-${productId}`);
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/coupang-products/${encodeURIComponent(productId)}/outbound-days/preview?days=2&live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const cur = (plan.coupang && plan.coupang.current_days) || [];
    const distinct = Array.from(new Set(cur));
    let state;
    if (plan.already_applied) state = `<div class="ez-note ok"><div>${esc(T("easy.ap.days_already"))}</div></div>`;
    else if (plan.sendable) state = `<div class="ez-note ok"><div>${esc(T("easy.ap.days_ready", { cur: distinct.join(", "), n: cur.length }))}</div></div>`;
    else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}</div></div>`;
    box.innerHTML = `${state}${plan.sendable ? `<button type="button" class="ez-btn sm" id="ez-days-send-${esc(productId)}" style="margin-top:10px">${esc(T("easy.ap.days_send"))}</button>` : ""}`;
    const send = $(`ez-days-send-${productId}`);
    if (send) send.addEventListener("click", async () => {
      const ok = await B.askConfirm(T("easy.ap.days_confirm", { id: productId, cur: distinct.join(", "), n: cur.length }), { title: T("easy.ap.days_title"), okLabel: T("easy.ap.days_send") });
      if (!ok) return;
      try {
        const result = await api(`/purchase-tasks/coupang-products/${encodeURIComponent(productId)}/outbound-days/send`, {
          method: "POST", body: JSON.stringify({ days: 2, expected_current_days: cur, retry_action_required: false }),
        });
        B.toast(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE"
          ? T(result.verified === false ? "easy.ap.days_unverified" : "easy.ap.days_ok") : `${result.outcome}: ${result.detail || ""}`,
        result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
        loadApprovals();
      } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
    });
  }

  // 2026-10-24 쿠팡 옵션 재고 수량 변경: 현재 재고 확인(쿠팡 조회, 허용 필요) → 확인 → 변경 → 서버가 다시 읽어 확인.
  // 0은 따로 확인해야 보낸다. 요청마다 참조값을 새로 만들어 같은 요청이 두 번 나가지 않게 하고, 결과불명이면 다시 보내지 않고 조회로만 확인한다.
  const STOCK_MAX = 99999;

  function stockInputProblem(itemId, qtyText) {
    const item = String(itemId || "").trim();
    if (!/^\d{1,20}$/.test(item)) return "easy.stock.err_item";
    const q = String(qtyText === undefined || qtyText === null ? "" : qtyText).trim();
    if (!/^\d+$/.test(q) || Number(q) > STOCK_MAX) return "easy.stock.err_qty";
    return null;
  }

  function stockRef() {
    const raw = (typeof crypto !== "undefined" && crypto.randomUUID) ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    return String(raw).replace(/[^A-Za-z0-9-]/g, "").slice(0, 40);
  }

  function stockResultHtml(result, quantity) {
    // 끝난 뒤 화면에 남기는 기록(토스트만으로 끝내지 않는다).
    let tone = "bad";
    let text = `${result.outcome}: ${result.detail || ""}`;
    if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T("easy.stock.ok", { q: quantity }); }
    else if (result.outcome === "SUCCEEDED" && result.verified === false) { tone = "warn"; text = T("easy.stock.after_diff", { q: quantity, after: result.after_stock }); }
    else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.stock.unverified"); }
    else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.stock.already_done"); }
    else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.stock.unknown"); }
    else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
    return `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
  }

  async function stockPreview(itemId, qtyText) {
    const box = $("ez-stock-box");
    const problem = stockInputProblem(itemId, qtyText);
    if (problem) { box.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem, { max: STOCK_MAX.toLocaleString("ko-KR") }))}</div></div>`; return; }
    const item = String(itemId).trim();
    const quantity = Number(String(qtyText).trim());
    const ref = stockRef();
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/coupang-vendor-items/${encodeURIComponent(item)}/stock/preview?quantity=${quantity}&request_ref=${encodeURIComponent(ref)}&live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const cp = plan.coupang || {};
    let state;
    if (plan.already_applied) state = `<div class="ez-note ok"><div>${esc(T("easy.stock.already", { q: quantity }))}</div></div>`;
    else if (plan.sendable) {
      const sale = cp.on_sale === true ? T("easy.stock.on_sale") : (cp.on_sale === false ? T("easy.stock.off_sale") : T("easy.stock.sale_unknown"));
      const price = Number.isInteger(cp.sale_price) ? cp.sale_price.toLocaleString("ko-KR") : "—";
      state = `<div class="ez-note ok"><div>${esc(T("easy.stock.ready", { cur: cp.current_stock, q: quantity, price, sale }))}</div></div>`;
    } else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}</div></div>`;
    const zero = plan.sendable && plan.zero_warning
      ? `<div class="ez-note bad" style="margin-top:8px"><div>${esc(T("easy.stock.zero_warn"))}<br><label><input type="checkbox" id="ez-stock-zero"> ${esc(T("easy.stock.zero_confirm"))}</label></div></div>` : "";
    box.innerHTML = `${state}${zero}${plan.sendable ? `<button type="button" class="ez-btn sm" id="ez-stock-send" style="margin-top:10px">${esc(T("easy.stock.send", { q: quantity }))}</button>` : ""}<div id="ez-stock-result" style="margin-top:10px"></div>`;
    const send = $("ez-stock-send");
    if (!send) return;
    send.addEventListener("click", async () => {
      const zeroBox = $("ez-stock-zero");
      if (plan.zero_warning && !(zeroBox && zeroBox.checked)) { B.toast(T("easy.stock.zero_needed"), "error"); return; }
      const ok = await B.askConfirm(T("easy.stock.confirm", { id: item, cur: cp.current_stock, q: quantity }), { title: T("easy.stock.title"), okLabel: T("easy.stock.send", { q: quantity }) });
      if (!ok) return;
      send.disabled = true;
      try {
        const result = await api(`/purchase-tasks/coupang-vendor-items/${encodeURIComponent(item)}/stock/send`, {
          method: "POST", body: JSON.stringify({ quantity, expected_current_stock: cp.current_stock, request_ref: ref, confirm_zero: plan.zero_warning === true && !!(zeroBox && zeroBox.checked), retry_action_required: false }),
        });
        $("ez-stock-result").innerHTML = stockResultHtml(result, quantity)
          + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" id="ez-stock-reconcile" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
        B.toast(T(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "easy.stock.toast_done" : "easy.stock.toast_check"), result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
        const rec = $("ez-stock-reconcile");
        if (rec) rec.addEventListener("click", async () => {
          try {
            const r = await api(`/purchase-tasks/coupang-vendor-items/${encodeURIComponent(item)}/stock/reconcile?quantity=${quantity}&request_ref=${encodeURIComponent(ref)}`, { method: "POST" });
            $("ez-stock-result").innerHTML = stockResultHtml({ ...r, verified: r.outcome === "SUCCEEDED" ? true : null }, quantity);
          } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
        });
      } catch (err) {
        send.disabled = false;
        $("ez-stock-result").innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
      }
    });
  }

  // 2026-10-25 쿠팡 옵션 판매가 변경: 매입가·배송비는 사용자가 넣는다(마진 확인용 — HOMEZ가 추측하지 않는다).
  // 지금 판매가 확인(쿠팡 조회, 허용 필요) → 최저 판매가·마진 확인 → 확인 → 변경 → 서버가 다시 읽어 확인. 20% 이상은 확인 칸을 체크해야 보낸다.
  const PRICE_MAX = 10000000;

  function priceInputProblem(itemId, priceText, costText, shipText) {
    const item = String(itemId || "").trim();
    if (!/^\d{1,20}$/.test(item)) return "easy.stock.err_item";
    const txt = (v) => String(v === undefined || v === null ? "" : v).trim();
    const p = txt(priceText);
    if (!/^\d+$/.test(p) || Number(p) < 10 || Number(p) > PRICE_MAX || Number(p) % 10 !== 0) return "easy.price.err_price";
    const c = txt(costText);
    if (!/^\d+$/.test(c) || Number(c) < 1 || Number(c) > PRICE_MAX) return "easy.price.err_cost";
    const s = txt(shipText);
    if (!/^\d+$/.test(s) || Number(s) > PRICE_MAX) return "easy.price.err_ship";
    return null;
  }

  function priceNum(n) {
    return Number.isInteger(n) ? n.toLocaleString("ko-KR") : "—";
  }

  function priceResultHtml(result, price) {
    // 끝난 뒤 화면에 남기는 기록(토스트만으로 끝내지 않는다).
    let tone = "bad";
    let text = `${result.outcome}: ${result.detail || ""}`;
    if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T("easy.price.ok", { p: priceNum(price) }); }
    else if (result.outcome === "SUCCEEDED" && result.verified === false) { tone = "warn"; text = T("easy.price.after_diff", { p: priceNum(price), after: priceNum(result.after_price) }); }
    else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.price.unverified"); }
    else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.stock.already_done"); }
    else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.price.unknown"); }
    else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
    return `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
  }

  async function pricePreview(itemId, priceText, costText, shipText) {
    const box = $("ez-price-box");
    const problem = priceInputProblem(itemId, priceText, costText, shipText);
    if (problem) { box.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem, { max: PRICE_MAX.toLocaleString("ko-KR") }))}</div></div>`; return; }
    const item = String(itemId).trim();
    const price = Number(String(priceText).trim());
    const unitCost = Number(String(costText).trim());
    const shippingCost = Number(String(shipText).trim());
    const ref = stockRef();
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/coupang-vendor-items/${encodeURIComponent(item)}/price/preview?price=${price}&unit_cost=${unitCost}&shipping_cost=${shippingCost}&request_ref=${encodeURIComponent(ref)}&live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const cp = plan.coupang || {};
    const m = plan.margin || {};
    const pct = (r) => (typeof r === "number" ? `${(r * 100).toFixed(1)}%` : "—");
    // 마진은 내림으로 보인다 — 17.97%를 18.0%로 올려 보이면 기준을 지킨 것처럼 읽힌다.
    const pctDown = (r) => (typeof r === "number" ? `${(Math.floor(r * 1000) / 10).toFixed(1)}%` : "—");
    const marginLine = Number.isInteger(m.floor_price)
      ? `<div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.price.margin_line", { floor: priceNum(m.floor_price), margin: pctDown(m.margin_rate), fee: pct(m.fee_rate), reserve: pct(m.return_reserve_rate), min: pct(m.min_margin) }))}</div>` : "";
    let state;
    if (plan.already_applied) state = `<div class="ez-note ok"><div>${esc(T("easy.price.already", { p: priceNum(price) }))}</div></div>`;
    else if (plan.sendable) state = `<div class="ez-note ok"><div>${esc(T("easy.price.ready", { cur: priceNum(cp.current_price), p: priceNum(price), change: pct(plan.change_rate) }))}</div></div>`;
    else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}</div></div>`;
    const big = plan.sendable && plan.big_change
      ? `<div class="ez-note bad" style="margin-top:8px"><div>${esc(T("easy.price.big_warn", { change: pct(plan.change_rate) }))}<br><label><input type="checkbox" id="ez-price-big"> ${esc(T("easy.price.big_confirm"))}</label></div></div>` : "";
    box.innerHTML = `${state}${marginLine}${big}${plan.sendable ? `<button type="button" class="ez-btn sm" id="ez-price-send" style="margin-top:10px">${esc(T("easy.price.send", { p: priceNum(price) }))}</button>` : ""}<div id="ez-price-result" style="margin-top:10px"></div>`;
    const send = $("ez-price-send");
    if (!send) return;
    send.addEventListener("click", async () => {
      const bigBox = $("ez-price-big");
      if (plan.big_change && !(bigBox && bigBox.checked)) { B.toast(T("easy.price.big_needed"), "error"); return; }
      const ok = await B.askConfirm(T("easy.price.confirm", { id: item, cur: priceNum(cp.current_price), p: priceNum(price) }), { title: T("easy.price.title"), okLabel: T("easy.price.send", { p: priceNum(price) }) });
      if (!ok) return;
      send.disabled = true;
      try {
        const result = await api(`/purchase-tasks/coupang-vendor-items/${encodeURIComponent(item)}/price/send`, {
          method: "POST", body: JSON.stringify({ price, unit_cost: unitCost, shipping_cost: shippingCost, expected_current_price: cp.current_price, request_ref: ref, confirm_big_change: plan.big_change === true && !!(bigBox && bigBox.checked), retry_action_required: false }),
        });
        $("ez-price-result").innerHTML = priceResultHtml(result, price)
          + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" id="ez-price-reconcile" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
        B.toast(T(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "easy.price.toast_done" : "easy.price.toast_check"), result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
        const rec = $("ez-price-reconcile");
        if (rec) rec.addEventListener("click", async () => {
          try {
            const r = await api(`/purchase-tasks/coupang-vendor-items/${encodeURIComponent(item)}/price/reconcile?price=${price}&request_ref=${encodeURIComponent(ref)}`, { method: "POST" });
            $("ez-price-result").innerHTML = priceResultHtml({ ...r, verified: r.outcome === "SUCCEEDED" ? true : null }, price);
          } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
        });
      } catch (err) {
        send.disabled = false;
        $("ez-price-result").innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
      }
    });
  }

  // 2026-10-25 쿠팡 등록 상품의 상품명 수정: 지금 이름·상태 확인(쿠팡 조회, 허용 필요) → 승인 뒤 반영 확인 칸 → 확인 → 요청 → 서버가 다시 읽어 확인.
  // 쿠팡은 상품 전체를 다시 받는다 — 서버가 방금 읽은 상품에서 이름만 바꿔 보낸다(화면은 본문을 만들지 않는다).
  const PNAME_MAX = 100;

  function pnameInputProblem(productId, displayText, sellerText) {
    if (!/^\d{1,20}$/.test(String(productId || "").trim())) return "easy.pname.err_id";
    const bad = (v) => v.length > PNAME_MAX || /[\u0000-\u001f\u007f]/.test(v);
    const display = String(displayText === undefined || displayText === null ? "" : displayText).trim();
    if (!display || bad(display)) return "easy.pname.err_display";
    const seller = String(sellerText === undefined || sellerText === null ? "" : sellerText).trim();
    if (seller && bad(seller)) return "easy.pname.err_seller";
    return null;
  }

  function pnameResultHtml(result, name) {
    // 끝난 뒤 화면에 남기는 기록(토스트만으로 끝내지 않는다).
    let tone = "bad";
    let text = `${result.outcome}: ${result.detail || ""}`;
    const after = result.after || {};
    if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T("easy.pname.ok", { name }); }
    else if (result.outcome === "SUCCEEDED" && after.status_name) { tone = "warn"; text = T("easy.pname.waiting", { status: after.status_name }); }
    else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.pname.unverified"); }
    else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.stock.already_done"); }
    else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.pname.unknown"); }
    else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
    return `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
  }

  async function pnamePreview(productId, displayText, sellerText) {
    const box = $("ez-pname-box");
    const problem = pnameInputProblem(productId, displayText, sellerText);
    if (problem) { box.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem, { max: PNAME_MAX }))}</div></div>`; return; }
    const id = String(productId).trim();
    const displayName = String(displayText).trim();
    const sellerName = String(sellerText || "").trim();
    const ref = stockRef();
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/coupang-products/${encodeURIComponent(id)}/name/preview?display_name=${encodeURIComponent(displayName)}${sellerName ? `&seller_name=${encodeURIComponent(sellerName)}` : ""}&request_ref=${encodeURIComponent(ref)}&live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const cp = plan.coupang || {};
    let state;
    if (plan.already_applied) state = `<div class="ez-note ok"><div>${esc(T("easy.pname.already"))}</div></div>`;
    else if (plan.sendable) {
      state = `<div class="ez-note ok"><div>${esc(T(plan.mode === "RESUBMIT" ? "easy.pname.ready_resubmit" : "easy.pname.ready", { cur: cp.display_name || "—", name: displayName, status: cp.status_name || "—", options: cp.option_count }))}</div></div>`;
    } else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}</div></div>`;
    const review = plan.sendable
      ? `<div class="ez-note bad" style="margin-top:8px"><div>${esc(T("easy.pname.review_warn"))}<br><label><input type="checkbox" id="ez-pname-review"> ${esc(T("easy.pname.review_confirm"))}</label></div></div>` : "";
    box.innerHTML = `${state}${review}${plan.sendable ? `<button type="button" class="ez-btn sm" id="ez-pname-send" style="margin-top:10px">${esc(T("easy.pname.send"))}</button>` : ""}<div id="ez-pname-result" style="margin-top:10px"></div>`;
    const send = $("ez-pname-send");
    if (!send) return;
    send.addEventListener("click", async () => {
      const reviewBox = $("ez-pname-review");
      if (!(reviewBox && reviewBox.checked)) { B.toast(T("easy.pname.review_needed"), "error"); return; }
      const ok = await B.askConfirm(T("easy.pname.confirm", { id, cur: cp.display_name || "—", name: displayName }), { title: T("easy.pname.title"), okLabel: T("easy.pname.send") });
      if (!ok) return;
      send.disabled = true;
      try {
        const result = await api(`/purchase-tasks/coupang-products/${encodeURIComponent(id)}/name/send`, {
          method: "POST", body: JSON.stringify({ display_name: displayName, seller_name: sellerName || null, expected_display_name: cp.display_name || "", request_ref: ref, confirm_review: !!(reviewBox && reviewBox.checked), retry_action_required: false }),
        });
        $("ez-pname-result").innerHTML = pnameResultHtml(result, displayName)
          + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" id="ez-pname-reconcile" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
        B.toast(T(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "easy.pname.toast_done" : "easy.pname.toast_check"), result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
        const rec = $("ez-pname-reconcile");
        if (rec) rec.addEventListener("click", async () => {
          try {
            const r = await api(`/purchase-tasks/coupang-products/${encodeURIComponent(id)}/name/reconcile?display_name=${encodeURIComponent(displayName)}&request_ref=${encodeURIComponent(ref)}`, { method: "POST" });
            $("ez-pname-result").innerHTML = pnameResultHtml({ ...r, verified: r.outcome === "SUCCEEDED" ? true : null }, displayName);
          } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
        });
      } catch (err) {
        send.disabled = false;
        $("ez-pname-result").innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
      }
    });
  }

  // 2026-10-25 쿠팡 상품 문의 답변: 답하지 않은 문의 불러오기(쿠팡 조회 1회) → 답변 쓰기 → 고객에게 바로 보인다는 확인 칸 → 확인 → 등록
  // (서버가 보내기 직전에 문의를 다시 읽어 아직 답이 없을 때만 보내고, 보낸 뒤 다시 읽어 확인한다). 한 문의에는 한 번만 보낸다.
  const INQ_MAX = 1000;

  function inquiryReplyProblem(text) {
    const value = String(text === undefined || text === null ? "" : text).replace(/\r\n?/g, "\n").trim();
    if (!value || value.length > INQ_MAX) return "easy.inq.err_content";
    if (/\[(전화번호|이메일|주소창|주소|번호|이름)\]/.test(value) || value.includes("※ 초안입니다")) return "easy.inq.err_draft";
    return null;
  }

  function inquiryTime(value) {
    // 쿠팡 문의 시각은 시간대 없이 온다(예: 2026-10-25T09:00:00) — 브라우저가 다른 시간대로 바꾸지 않게 받은 그대로 보인다.
    const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(String(value || ""));
    return m ? `${m[1]} ${m[2]}` : (value ? String(value) : "—");
  }

  function inquiryResultHtml(result) {
    // 끝난 뒤 화면에 남기는 기록(토스트만으로 끝내지 않는다).
    let tone = "bad";
    let text = `${result.outcome}: ${result.detail || ""}`;
    if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T("easy.inq.ok"); }
    else if (result.outcome === "SUCCEEDED" && result.verified === false) { tone = "warn"; text = T("easy.inq.not_seen"); }
    else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.inq.unverified"); }
    else if (result.outcome === "ALREADY_ANSWERED") { tone = "warn"; text = T("easy.inq.already"); }
    else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.stock.already_done"); }
    else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.inq.unknown"); }
    else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
    return `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
  }

  async function inquiryList() {
    const box = $("ez-inq-box");
    box.innerHTML = loadingHtml();
    let listed;
    try { listed = await api("/purchase-tasks/coupang-inquiries/unanswered"); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    if (!listed.ok) { box.innerHTML = `<div class="ez-note warn"><div>${(listed.blockers || []).map((b) => esc(b.message)).join("<br>")}</div></div>`; return; }
    const rows = listed.rows || [];
    if (!rows.length) { box.innerHTML = `<div class="ez-empty">${esc(T("easy.inq.none"))}</div>`; return; }
    box.innerHTML = (listed.truncated ? `<div class="ez-note warn"><div>${esc(T("easy.inq.truncated"))}</div></div>` : "")
      + rows.map((r) => `<div class="ez-block" style="margin-top:10px;padding:10px 14px" data-ez-inq-row="${esc(r.inquiry_id)}">
        <div class="ez-faint" style="font-size:13px">${esc(T("easy.inq.meta", { id: r.inquiry_id, at: inquiryTime(r.inquiry_at), product: r.seller_product_id || "—" }))}</div>
        <div style="white-space:pre-wrap;margin:6px 0">${esc(r.question || "")}${r.question_clipped ? " …" : ""}</div>
        <textarea data-ez-inq-text="${esc(r.inquiry_id)}" maxlength="${INQ_MAX}" rows="4" style="width:100%" placeholder="${esc(T("easy.inq.placeholder"))}"></textarea>
        <label style="display:block;margin-top:6px"><input type="checkbox" data-ez-inq-public="${esc(r.inquiry_id)}"> ${esc(T("easy.inq.public_confirm"))}</label>
        <button type="button" class="ez-btn sm" data-ez-inq-send="${esc(r.inquiry_id)}" style="margin-top:8px">${esc(T("easy.inq.send"))}</button>
        <div data-ez-inq-result="${esc(r.inquiry_id)}" style="margin-top:8px"></div></div>`).join("");
    box.querySelectorAll("[data-ez-inq-send]").forEach((btn) => btn.addEventListener("click", () => inquirySend(btn, btn.dataset.ezInqSend)));
  }

  async function inquirySend(btn, id) {
    const box = $("ez-inq-box");
    const textEl = box.querySelector(`[data-ez-inq-text="${id}"]`);
    const publicEl = box.querySelector(`[data-ez-inq-public="${id}"]`);
    const out = box.querySelector(`[data-ez-inq-result="${id}"]`);
    const content = String(textEl.value || "").replace(/\r\n?/g, "\n").trim();
    const problem = inquiryReplyProblem(content);
    if (problem) { out.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem, { max: INQ_MAX.toLocaleString("ko-KR") }))}</div></div>`; return; }
    if (!(publicEl && publicEl.checked)) { B.toast(T("easy.inq.public_needed"), "error"); return; }
    const ok = await B.askConfirm(T("easy.inq.confirm", { id, chars: content.length }), { title: T("easy.inq.title"), okLabel: T("easy.inq.send") });
    if (!ok) return;
    btn.disabled = true;
    try {
      const result = await api(`/purchase-tasks/coupang-inquiries/${encodeURIComponent(id)}/reply/send`, {
        method: "POST", body: JSON.stringify({ content, confirm_public: !!(publicEl && publicEl.checked), retry_action_required: false }),
      });
      out.innerHTML = inquiryResultHtml(result)
        + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" data-ez-inq-reconcile="${esc(id)}" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
      B.toast(T(result.outcome === "SUCCEEDED" ? "easy.inq.toast_done" : "easy.inq.toast_check"), result.outcome === "SUCCEEDED" ? "success" : "error");
      if (result.outcome !== "ACTION_REQUIRED") textEl.readOnly = true;
      const rec = out.querySelector("[data-ez-inq-reconcile]");
      if (rec) rec.addEventListener("click", async () => {
        try {
          const r = await api(`/purchase-tasks/coupang-inquiries/${encodeURIComponent(id)}/reply/reconcile`, { method: "POST", body: JSON.stringify({ content, confirm_public: true }) });
          out.innerHTML = inquiryResultHtml({ ...r, verified: r.outcome === "SUCCEEDED" ? true : null });
        } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
      });
    } catch (err) {
      btn.disabled = false;
      out.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
    }
  }

  // 2026-10-26 쿠팡 고객센터(콜센터) 이관 문의 답변: 답변을 기다리는 이관 문의 불러오기(쿠팡 조회 1회) → 상담사 글을 보고 답변 쓰기(2~1,000자)
  // → 확인 칸 → 확인 → 등록(서버가 그 상담사 글이 아직 답변을 기다릴 때만 보내고 다시 읽어 확인). 허용은 상품 문의 답변과 같다.
  const CC_MIN = 2;

  function callCenterReplyProblem(text) {
    const value = String(text === undefined || text === null ? "" : text).replace(/\r\n?/g, "\n").trim();
    if (value.length < CC_MIN || value.length > INQ_MAX) return "easy.cc.err_content";
    return inquiryReplyProblem(value) === "easy.inq.err_draft" ? "easy.inq.err_draft" : null;
  }

  async function callCenterList() {
    const box = $("ez-cc-box");
    box.innerHTML = loadingHtml();
    let listed;
    try { listed = await api("/purchase-tasks/coupang-call-center-inquiries/unanswered"); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    if (!listed.ok) { box.innerHTML = `<div class="ez-note warn"><div>${(listed.blockers || []).map((b) => esc(b.message)).join("<br>")}</div></div>`; return; }
    const rows = listed.rows || [];
    if (!rows.length) { box.innerHTML = `<div class="ez-empty">${esc(T("easy.cc.none"))}</div>`; return; }
    box.innerHTML = (listed.truncated ? `<div class="ez-note warn"><div>${esc(T("easy.inq.truncated"))}</div></div>` : "")
      + rows.map((r) => {
        const answerable = r.pending_count === 1 && r.pending_answer_id;
        return `<div class="ez-block" style="margin-top:10px;padding:10px 14px">
        <div class="ez-faint" style="font-size:13px">${esc(T("easy.cc.meta", { id: r.inquiry_id, at: inquiryTime(r.inquiry_at), order: r.order_id || "—", category: r.category || "—" }))}</div>
        <div style="white-space:pre-wrap;margin:6px 0">${esc(r.question || "")}</div>
        ${answerable ? `<div class="ez-note info" style="margin:6px 0"><div><b>${esc(T("easy.cc.agent_label"))}</b> <span style="white-space:pre-wrap">${esc(r.pending_text || "")}</span></div></div>
        <textarea data-ez-cc-text="${esc(r.inquiry_id)}" maxlength="${INQ_MAX}" rows="4" style="width:100%" placeholder="${esc(T("easy.inq.placeholder"))}"></textarea>
        <label style="display:block;margin-top:6px"><input type="checkbox" data-ez-cc-public="${esc(r.inquiry_id)}"> ${esc(T("easy.cc.public_confirm"))}</label>
        <button type="button" class="ez-btn sm" data-ez-cc-send="${esc(r.inquiry_id)}" data-ez-cc-parent="${esc(r.pending_answer_id)}" style="margin-top:8px">${esc(T("easy.inq.send"))}</button>`
          : `<div class="ez-note warn"><div>${esc(T("easy.cc.multiple"))}</div></div>`}
        <div data-ez-cc-result="${esc(r.inquiry_id)}" style="margin-top:8px"></div></div>`;
      }).join("");
    box.querySelectorAll("[data-ez-cc-send]").forEach((btn) => btn.addEventListener("click", () => callCenterSend(btn, btn.dataset.ezCcSend, btn.dataset.ezCcParent)));
  }

  async function callCenterSend(btn, id, parent) {
    const box = $("ez-cc-box");
    const textEl = box.querySelector(`[data-ez-cc-text="${id}"]`);
    const publicEl = box.querySelector(`[data-ez-cc-public="${id}"]`);
    const out = box.querySelector(`[data-ez-cc-result="${id}"]`);
    const content = String(textEl.value || "").replace(/\r\n?/g, "\n").trim();
    const problem = callCenterReplyProblem(content);
    if (problem) { out.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem, { min: CC_MIN, max: INQ_MAX.toLocaleString("ko-KR") }))}</div></div>`; return; }
    if (!(publicEl && publicEl.checked)) { B.toast(T("easy.inq.public_needed"), "error"); return; }
    const ok = await B.askConfirm(T("easy.cc.confirm", { id, chars: content.length }), { title: T("easy.cc.title"), okLabel: T("easy.inq.send") });
    if (!ok) return;
    btn.disabled = true;
    try {
      const result = await api(`/purchase-tasks/coupang-call-center-inquiries/${encodeURIComponent(id)}/reply/send`, {
        method: "POST", body: JSON.stringify({ content, expected_parent_answer_id: parent, confirm_public: !!(publicEl && publicEl.checked), retry_action_required: false }),
      });
      out.innerHTML = inquiryResultHtml(result)
        + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" data-ez-cc-reconcile="${esc(id)}" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
      B.toast(T(result.outcome === "SUCCEEDED" ? "easy.inq.toast_done" : "easy.inq.toast_check"), result.outcome === "SUCCEEDED" ? "success" : "error");
      if (result.outcome !== "ACTION_REQUIRED") textEl.readOnly = true;
      const rec = out.querySelector("[data-ez-cc-reconcile]");
      if (rec) rec.addEventListener("click", async () => {
        try {
          const r = await api(`/purchase-tasks/coupang-call-center-inquiries/${encodeURIComponent(id)}/reply/reconcile`, { method: "POST", body: JSON.stringify({ content, expected_parent_answer_id: parent }) });
          out.innerHTML = inquiryResultHtml({ ...r, verified: r.outcome === "SUCCEEDED" ? true : null });
        } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
      });
    } catch (err) {
      btn.disabled = false;
      out.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
    }
  }

  // 2026-10-27 쿠팡 고객센터 업체 이관 문의 확인: 확인을 기다리는 이관 문의 불러오기(쿠팡 조회 1회) → 내용 확인 칸 → 확인 → 확인 처리
  // → 서버가 그 문의가 아직 미확인 목록에 있을 때만 보내고, 보낸 뒤 목록에서 빠졌는지로 확인한다(확인 뒤 상태 값은 공식 문서에 없다).
  async function callCenterConfirmList() {
    const box = $("ez-ccc-box");
    box.innerHTML = loadingHtml();
    let listed;
    try { listed = await api("/purchase-tasks/coupang-call-center-inquiries/unconfirmed"); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    if (!listed.ok) { box.innerHTML = `<div class="ez-note warn"><div>${(listed.blockers || []).map((b) => esc(b.message)).join("<br>")}</div></div>`; return; }
    const rows = listed.rows || [];
    const gates = (listed.gate_blockers || []).length
      ? `<div class="ez-note warn"><div>${listed.gate_blockers.map((b) => esc(b.message)).join("<br>")}</div></div>` : "";
    if (!rows.length) { box.innerHTML = `${gates}<div class="ez-empty">${esc(T("easy.ccc.none"))}</div>`; return; }
    box.innerHTML = gates + (listed.truncated ? `<div class="ez-note warn"><div>${esc(T("easy.inq.truncated"))}</div></div>` : "")
      + rows.map((r) => `<div class="ez-block" style="margin-top:10px;padding:10px 14px">
        <div class="ez-faint" style="font-size:13px">${esc(T("easy.cc.meta", { id: r.inquiry_id, at: inquiryTime(r.inquiry_at), order: r.order_id || "—", category: r.category || "—" }))}</div>
        <div style="white-space:pre-wrap;margin:6px 0">${esc(r.question || "")}</div>
        <div class="ez-note info" style="margin:6px 0"><div><b>${esc(T("easy.ccc.agent_label"))}</b> <span style="white-space:pre-wrap">${esc(r.agent_text || T("easy.ccc.no_agent_text"))}</span></div></div>
        ${(listed.gate_blockers || []).length ? "" : `<label style="display:block"><input type="checkbox" data-ez-ccc-seen="${esc(r.inquiry_id)}"> ${esc(T("easy.ccc.seen"))}</label>
        <button type="button" class="ez-btn sm" data-ez-ccc-send="${esc(r.inquiry_id)}" style="margin-top:6px">${esc(T("easy.ccc.send"))}</button>`}
        <div data-ez-ccc-result="${esc(r.inquiry_id)}" style="margin-top:8px"></div></div>`).join("");
    box.querySelectorAll("[data-ez-ccc-send]").forEach((btn) => btn.addEventListener("click", () => callCenterConfirmSend(btn, btn.dataset.ezCccSend)));
  }

  async function callCenterConfirmSend(btn, id) {
    const box = $("ez-ccc-box");
    const seen = box.querySelector(`[data-ez-ccc-seen="${id}"]`);
    const out = box.querySelector(`[data-ez-ccc-result="${id}"]`);
    if (!(seen && seen.checked)) { B.toast(T("easy.ret.confirm_needed"), "error"); return; }
    if (!(await B.askConfirm(T("easy.ccc.ask", { id }), { title: T("easy.ccc.title"), okLabel: T("easy.ccc.send") }))) return;
    btn.disabled = true;
    try {
      const result = await api(`/purchase-tasks/coupang-call-center-inquiries/${encodeURIComponent(id)}/confirm`, {
        method: "POST", body: JSON.stringify({ confirm_seen: !!(seen && seen.checked), retry_action_required: false }),
      });
      let tone = "bad";
      let text = `${result.outcome}: ${result.detail || ""}`;
      if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T("easy.ccc.ok"); }
      else if (result.outcome === "SUCCEEDED" && result.verified === false) { tone = "warn"; text = T("easy.ccc.still"); }
      else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.ret.unverified"); }
      else if (result.outcome === "NOT_PENDING") { tone = "warn"; text = T("easy.ccc.not_pending"); }
      else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.ret.unknown"); }
      else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
      out.innerHTML = `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
      B.toast(T(result.outcome === "SUCCEEDED" ? "easy.ccc.toast_done" : "easy.ccc.toast_check"), result.outcome === "SUCCEEDED" ? "success" : "error");
    } catch (err) {
      btn.disabled = false;
      out.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
    }
  }

  // 2026-10-26 쿠팡 반품 입고 확인·승인: 반품 상태 확인(쿠팡 조회, 허용 필요) → 상품 수령 확인 칸(승인은 환불 확인 칸도) → 확인 → 처리
  // → 서버가 다시 읽어 확인. 승인 수량은 쿠팡이 준 접수 수량을 그대로 쓴다. 고객 환불로 이어져 확인 문구에 그 점을 적는다.
  // 상태·귀책 이름은 반품 알림과 같은 문구(easy.rs.*·easy.fault.*)를 쓴다. 모르는 값은 코드 그대로.
  function returnStatusLabel(status) {
    return status ? tryT(`easy.rs.${String(status).toLowerCase()}`, String(status)) : "—";
  }

  function returnResultHtml(result, action) {
    // 끝난 뒤 화면에 남기는 기록(토스트만으로 끝내지 않는다).
    let tone = "bad";
    let text = `${result.outcome}: ${result.detail || ""}`;
    const after = returnStatusLabel(result.after_status);
    if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T(action === "APPROVE" ? "easy.ret.ok_approve" : "easy.ret.ok_receive", { status: after }); }
    else if (result.outcome === "SUCCEEDED" && result.verified === false) { tone = "warn"; text = T("easy.ret.not_changed", { status: after }); }
    else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.ret.unverified"); }
    else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.ret.already", { status: returnStatusLabel((result.plan || {}).coupang ? result.plan.coupang.status : null) }); }
    else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.ret.unknown"); }
    else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
    return `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
  }

  async function returnPreview(receiptId, action) {
    const box = $("ez-ret-box");
    const id = String(receiptId || "").trim();
    if (!/^\d{1,20}$/.test(id)) { box.innerHTML = `<div class="ez-note warn"><div>${esc(T("easy.ret.err_id"))}</div></div>`; return; }
    if (action !== "RECEIVE" && action !== "APPROVE") return;
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/coupang-returns/${encodeURIComponent(id)}/preview?action=${action}&live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const cp = plan.coupang || {};
    const facts = plan.live_checked
      ? `<div class="ez-faint" style="font-size:13px;margin-top:6px">${esc(T("easy.ret.facts", { status: returnStatusLabel(cp.status), order: cp.order_id || "—", count: cp.cancel_count === null || cp.cancel_count === undefined ? "—" : cp.cancel_count, fault: cp.fault_by ? tryT(`easy.fault.${String(cp.fault_by).toLowerCase()}`, String(cp.fault_by)) : "—", charge: Number.isInteger(cp.return_shipping_charge) ? cp.return_shipping_charge.toLocaleString("ko-KR") : "—" }))}</div>` : "";
    let state;
    if (plan.already_applied) state = `<div class="ez-note ok"><div>${esc(T("easy.ret.already", { status: returnStatusLabel(cp.status) }))}</div></div>`;
    else if (plan.sendable) state = `<div class="ez-note ok"><div>${esc(T(action === "APPROVE" ? "easy.ret.ready_approve" : "easy.ret.ready_receive", { count: cp.cancel_count }))}</div></div>`;
    else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}</div></div>`;
    const prerefund = plan.sendable && action === "RECEIVE" && cp.pre_refund === true
      ? `<div class="ez-note bad" style="margin-top:8px"><div>${esc(T("easy.ret.prerefund_warn"))}</div></div>` : "";
    const checks = plan.sendable
      ? `<div class="ez-note bad" style="margin-top:8px"><div><label><input type="checkbox" id="ez-ret-received"> ${esc(T("easy.ret.received_confirm"))}</label>${action === "APPROVE" ? `<br><label><input type="checkbox" id="ez-ret-refund"> ${esc(T("easy.ret.refund_confirm", { count: cp.cancel_count }))}</label>` : ""}</div></div>` : "";
    box.innerHTML = `${state}${facts}${prerefund}${checks}${plan.sendable ? `<button type="button" class="ez-btn sm" id="ez-ret-send" style="margin-top:10px">${esc(T(action === "APPROVE" ? "easy.ret.send_approve" : "easy.ret.send_receive"))}</button>` : ""}<div id="ez-ret-result" style="margin-top:10px"></div>`;
    const send = $("ez-ret-send");
    if (!send) return;
    send.addEventListener("click", async () => {
      const received = $("ez-ret-received");
      const refund = $("ez-ret-refund");
      if (!(received && received.checked) || (action === "APPROVE" && !(refund && refund.checked))) { B.toast(T("easy.ret.confirm_needed"), "error"); return; }
      const ok = await B.askConfirm(T(action === "APPROVE" ? "easy.ret.confirm_approve" : "easy.ret.confirm_receive", { id, count: cp.cancel_count }), { title: T("easy.ret.title"), okLabel: T(action === "APPROVE" ? "easy.ret.send_approve" : "easy.ret.send_receive") });
      if (!ok) return;
      send.disabled = true;
      try {
        const result = await api(`/purchase-tasks/coupang-returns/${encodeURIComponent(id)}/send`, {
          method: "POST", body: JSON.stringify({ action, expected_status: cp.status, expected_cancel_count: action === "APPROVE" ? cp.cancel_count : null, confirm_received: !!(received && received.checked), confirm_refund: action === "APPROVE" && !!(refund && refund.checked), retry_action_required: false }),
        });
        $("ez-ret-result").innerHTML = returnResultHtml(result, action)
          + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" id="ez-ret-reconcile" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
        B.toast(T(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "easy.ret.toast_done" : "easy.ret.toast_check"), result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
        const rec = $("ez-ret-reconcile");
        if (rec) rec.addEventListener("click", async () => {
          try {
            const r = await api(`/purchase-tasks/coupang-returns/${encodeURIComponent(id)}/reconcile?action=${action}`, { method: "POST" });
            $("ez-ret-result").innerHTML = returnResultHtml({ ...r, verified: r.outcome === "SUCCEEDED" ? true : null }, action);
          } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
        });
      } catch (err) {
        send.disabled = false;
        $("ez-ret-result").innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
      }
    });
  }

  // 2026-10-27 쿠팡 반품 회수 송장 등록(직접 회수): 반품 상태 확인(쿠팡 조회) → 직접 회수 확인 칸 → 확인 → 등록 → 서버가 다시 읽어 확인.
  // 택배사는 이름(예: CJ대한통운)이나 공식 코드로 넣고, 서버가 공식 목록으로만 코드로 바꾼다(없는 이름은 막는다).
  function pickupInputProblem(receiptId, courier, invoice, reg) {
    if (!/^\d{1,20}$/.test(String(receiptId || "").trim())) return "easy.ret.err_id";
    if (!String(courier || "").trim()) return "easy.pick.err_courier";
    if (!/^[A-Za-z0-9-]{6,30}$/.test(String(invoice || "").trim())) return "easy.pick.err_invoice";
    const r = String(reg || "").trim();
    if (r && !/^[A-Za-z0-9]{1,30}$/.test(r)) return "easy.pick.err_reg";
    return null;
  }

  async function pickupPreview(receiptId, courier, invoice, reg) {
    const box = $("ez-pick-box");
    const problem = pickupInputProblem(receiptId, courier, invoice, reg);
    if (problem) { box.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem))}</div></div>`; return; }
    const id = String(receiptId).trim();
    const c = String(courier).trim();
    const n = String(invoice).trim();
    const r = String(reg || "").trim();
    box.innerHTML = loadingHtml();
    let plan;
    try { plan = await api(`/purchase-tasks/coupang-returns/${encodeURIComponent(id)}/pickup-invoice/preview?courier=${encodeURIComponent(c)}&invoice_number=${encodeURIComponent(n)}${r ? `&reg_number=${encodeURIComponent(r)}` : ""}&live=true`); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    const reasons = (plan.blockers || []).concat(plan.gate_blockers || []);
    const cp = plan.coupang || {};
    let state;
    if (plan.already_applied) state = `<div class="ez-note ok"><div>${esc(T("easy.pick.already", { invoice: n }))}</div></div>`;
    else if (plan.sendable) state = `<div class="ez-note ok"><div>${esc(T("easy.pick.ready", { status: returnStatusLabel(cp.status), courier: plan.courier_code, invoice: n }))}</div></div>`;
    else state = `<div class="ez-note warn"><div>${reasons.map((b) => esc(b.message)).join("<br>")}</div></div>`;
    const check = plan.sendable
      ? `<div class="ez-note bad" style="margin-top:8px"><div><label><input type="checkbox" id="ez-pick-confirm"> ${esc(T("easy.pick.confirm_box"))}</label></div></div>` : "";
    box.innerHTML = `${state}${check}${plan.sendable ? `<button type="button" class="ez-btn sm" id="ez-pick-send" style="margin-top:10px">${esc(T("easy.pick.send"))}</button>` : ""}<div id="ez-pick-result" style="margin-top:10px"></div>`;
    const send = $("ez-pick-send");
    if (!send) return;
    send.addEventListener("click", async () => {
      const box2 = $("ez-pick-confirm");
      if (!(box2 && box2.checked)) { B.toast(T("easy.ret.confirm_needed"), "error"); return; }
      const ok = await B.askConfirm(T("easy.pick.confirm", { id, courier: plan.courier_code, invoice: n }), { title: T("easy.pick.title"), okLabel: T("easy.pick.send") });
      if (!ok) return;
      send.disabled = true;
      try {
        const result = await api(`/purchase-tasks/coupang-returns/${encodeURIComponent(id)}/pickup-invoice/send`, {
          method: "POST", body: JSON.stringify({ courier: c, invoice_number: n, reg_number: r || null, expected_status: cp.status, confirm_pickup: !!(box2 && box2.checked), retry_action_required: false }),
        });
        let tone = "bad";
        let text = `${result.outcome}: ${result.detail || ""}`;
        if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T("easy.pick.ok", { invoice: n }); }
        else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.ret.unverified"); }
        else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.pick.already", { invoice: n }); }
        else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.ret.unknown"); }
        else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
        $("ez-pick-result").innerHTML = `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`
          + (result.outcome === "UNKNOWN" ? `<button type="button" class="ez-btn line sm" id="ez-pick-reconcile" style="margin-top:8px">${esc(T("easy.stock.reconcile"))}</button>` : "");
        B.toast(T(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "easy.ret.toast_done" : "easy.ret.toast_check"), result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
        const rec = $("ez-pick-reconcile");
        if (rec) rec.addEventListener("click", async () => {
          try {
            const rr = await api(`/purchase-tasks/coupang-returns/${encodeURIComponent(id)}/pickup-invoice/reconcile?invoice_number=${encodeURIComponent(n)}`, { method: "POST" });
            const done = rr.outcome === "SUCCEEDED";
            $("ez-pick-result").innerHTML = `<div class="ez-note ${done ? "ok" : "warn"}"><div>${esc(done ? T("easy.pick.ok", { invoice: n }) : `${rr.outcome}: ${rr.detail || ""}`)}</div></div>`;
          } catch (err) { B.toast((err && err.message) || T("easy.error"), "error"); }
        });
      } catch (err) {
        send.disabled = false;
        $("ez-pick-result").innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
      }
    });
  }

  // 2026-10-27 쿠팡 교환 처리: 열린 교환 불러오기(쿠팡 조회 1회) → 교환마다 할 수 있는 처리만 보인다 —
  // 거부(쿠팡이 거부 가능으로 알린 교환만, 사유는 매진·고객 철회)·회수 송장 등록(회수연동전이고 송장이 없을 때만). 확인 칸 → 확인 → 처리.
  // 서버가 보내기 직전에 다시 찾아 상태가 그대로일 때만 보내고 다시 읽어 확인한다. 입고 확인·교환품 송장은 공식 문서가 모자라 윙에서 한다.
  function exchangeStatusLabel(status) {
    return status ? tryT(`easy.exch.st.${String(status).toLowerCase()}`, String(status)) : "—";
  }

  async function exchangeList() {
    const box = $("ez-exch-box");
    box.innerHTML = loadingHtml();
    let listed;
    try { listed = await api("/purchase-tasks/coupang-exchanges/open"); }
    catch (err) { box.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`; return; }
    if (!listed.ok) { box.innerHTML = `<div class="ez-note warn"><div>${(listed.blockers || []).map((b) => esc(b.message)).join("<br>")}</div></div>`; return; }
    const rows = listed.rows || [];
    if (!rows.length) { box.innerHTML = `<div class="ez-empty">${esc(T("easy.exch.none"))}</div>`; return; }
    box.innerHTML = (listed.truncated ? `<div class="ez-note warn"><div>${esc(T("easy.inq.truncated"))}</div></div>` : "")
      + rows.map((r) => {
        const id = esc(r.exchange_id);
        const items = (r.items || []).map((i) => `${esc(i.order_item || "—")} → ${esc(i.target_item || "—")}${Number.isInteger(i.quantity) ? ` × ${i.quantity}` : ""}`).join("<br>");
        const canPickup = r.collect_status === "BeforeDirection" && !(r.return_invoices || []).length;
        const reject = r.rejectable ? `<div style="margin-top:8px"><b>${esc(T("easy.exch.reject_title"))}</b>
          <select data-ez-exch-code="${id}"><option value="SOLDOUT">${esc(T("easy.exch.code_soldout"))}</option><option value="WITHDRAW">${esc(T("easy.exch.code_withdraw"))}</option></select>
          <label style="display:block"><input type="checkbox" data-ez-exch-confirm-reject="${id}"> ${esc(T("easy.exch.reject_confirm"))}</label>
          <button type="button" class="ez-btn sm" data-ez-exch-reject="${id}" data-ez-exch-status="${esc(r.status)}">${esc(T("easy.exch.reject_send"))}</button></div>` : "";
        const pickup = canPickup ? `<div style="margin-top:8px"><b>${esc(T("easy.pick.title"))}</b>
          <input type="text" data-ez-exch-courier="${id}" maxlength="30" placeholder="${esc(T("easy.pick.courier_placeholder"))}">
          <input type="text" data-ez-exch-invoice="${id}" maxlength="30" placeholder="${esc(T("easy.pick.invoice_label"))}">
          <label style="display:block"><input type="checkbox" data-ez-exch-confirm-pickup="${id}"> ${esc(T("easy.pick.confirm_box"))}</label>
          <button type="button" class="ez-btn sm" data-ez-exch-pickup="${id}" data-ez-exch-collect="${esc(r.collect_status)}">${esc(T("easy.pick.send"))}</button></div>` : "";
        return `<div class="ez-block" style="margin-top:10px;padding:10px 14px">
          <div class="ez-faint" style="font-size:13px">${esc(T("easy.exch.meta", { id: r.exchange_id, at: inquiryTime(r.created_at), order: r.order_id || "—", status: exchangeStatusLabel(r.status), reason: r.reason || "—" }))}</div>
          <div style="margin:6px 0">${items}</div>
          ${reject}${pickup}${reject || pickup ? "" : `<div class="ez-note info"><div>${esc(T("easy.exch.nothing"))}</div></div>`}
          <div data-ez-exch-result="${id}" style="margin-top:8px"></div></div>`;
      }).join("");
    box.querySelectorAll("[data-ez-exch-reject]").forEach((btn) => btn.addEventListener("click", () => exchangeSend(btn, btn.dataset.ezExchReject, "REJECT")));
    box.querySelectorAll("[data-ez-exch-pickup]").forEach((btn) => btn.addEventListener("click", () => exchangeSend(btn, btn.dataset.ezExchPickup, "PICKUP")));
  }

  async function exchangeSend(btn, id, action) {
    const box = $("ez-exch-box");
    const out = box.querySelector(`[data-ez-exch-result="${id}"]`);
    let body;
    if (action === "REJECT") {
      const confirmEl = box.querySelector(`[data-ez-exch-confirm-reject="${id}"]`);
      if (!(confirmEl && confirmEl.checked)) { B.toast(T("easy.ret.confirm_needed"), "error"); return; }
      const code = box.querySelector(`[data-ez-exch-code="${id}"]`).value;
      body = { action, reject_code: code, expected_status: btn.dataset.ezExchStatus, confirm: true, retry_action_required: false };
      if (!(await B.askConfirm(T("easy.exch.reject_ask", { id }), { title: T("easy.exch.title"), okLabel: T("easy.exch.reject_send") }))) return;
    } else {
      const confirmEl = box.querySelector(`[data-ez-exch-confirm-pickup="${id}"]`);
      const courier = String(box.querySelector(`[data-ez-exch-courier="${id}"]`).value || "").trim();
      const invoice = String(box.querySelector(`[data-ez-exch-invoice="${id}"]`).value || "").trim();
      const problem = pickupInputProblem(id, courier, invoice, "");
      if (problem) { out.innerHTML = `<div class="ez-note warn"><div>${esc(T(problem))}</div></div>`; return; }
      if (!(confirmEl && confirmEl.checked)) { B.toast(T("easy.ret.confirm_needed"), "error"); return; }
      body = { action, courier, invoice_number: invoice, expected_status: btn.dataset.ezExchCollect, confirm: true, retry_action_required: false };
      if (!(await B.askConfirm(T("easy.exch.pickup_ask", { id, invoice }), { title: T("easy.exch.title"), okLabel: T("easy.pick.send") }))) return;
    }
    btn.disabled = true;
    try {
      const result = await api(`/purchase-tasks/coupang-exchanges/${encodeURIComponent(id)}/send`, { method: "POST", body: JSON.stringify(body) });
      let tone = "bad";
      let text = `${result.outcome}: ${result.detail || ""}`;
      if (result.outcome === "SUCCEEDED" && result.verified === true) { tone = "ok"; text = T(action === "REJECT" ? "easy.exch.reject_ok" : "easy.exch.pickup_ok"); }
      else if (result.outcome === "SUCCEEDED") { tone = "warn"; text = T("easy.ret.unverified"); }
      else if (result.outcome === "ALREADY_DONE") { tone = "ok"; text = T("easy.stock.already_done"); }
      else if (result.outcome === "UNKNOWN") { tone = "warn"; text = T("easy.ret.unknown"); }
      else if (result.outcome === "ACTION_REQUIRED") { text = T("easy.stock.rejected", { detail: result.detail || "" }); }
      out.innerHTML = `<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`;
      B.toast(T(result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "easy.ret.toast_done" : "easy.ret.toast_check"), result.outcome === "SUCCEEDED" || result.outcome === "ALREADY_DONE" ? "success" : "error");
    } catch (err) {
      btn.disabled = false;
      out.innerHTML = `<div class="ez-note bad"><div>${esc((err && err.message) || T("easy.error"))}</div></div>`;
    }
  }

  // =========================================================
  // 더보기
  // =========================================================

  async function loadMore() {
    const root = $("ez-root-easy-more");
    const sections = B.sections().map((section) => ({ ...section, tabs: section.tabs.filter((tab) => B.isViewVisible(tab.view)) })).filter((s) => s.tabs.length);
    let version = "";
    try { const info = await fetch("/").then((r) => r.json()); version = info && info.version ? `V${info.version}` : ""; } catch (_) { /* 부가 정보 */ }
    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.more.title"))}</h1><div class="ez-lead">${esc(T("easy.more.lead"))}</div></div></div>
      <div class="ez-switch"><div style="flex:1"><b>${esc(T("easy.more.switch_title"))}</b><div class="ez-muted" style="font-size:14px">${esc(T("easy.more.switch_sub"))}</div></div>
        <button type="button" class="ez-btn line" id="ez-more-off">${esc(T("easy.more.switch_off"))}</button></div>
      <div class="ez-groups">${sections.map((s) => `<div class="ez-group"><h3>${esc(T(s.labelKey))}</h3>${s.tabs.map((tab) => `<button type="button" data-ez-go="${esc(tab.view)}">${esc(T(tab.labelKey))}</button>`).join("")}</div>`).join("")}</div>
      <div class="ez-faint" style="margin-top:22px;font-size:13px">${esc(version)}</div>`;
    root.querySelectorAll("[data-ez-go]").forEach((btn) => btn.addEventListener("click", () => B.navigateTo(btn.dataset.ezGo)));
    $("ez-more-off").addEventListener("click", () => setEasyEnabled(false, true));
  }

  // =========================================================
  // 분석(트렌드·신상품·판단·AI 학습을 한 화면에서) — 읽기 전용, 기존 화면의 데이터를 그대로 쓴다
  // =========================================================

  const REC_KEY = {
    INSUFFICIENT_DATA: "decision.recommendation.insufficient_data", REVIEW_REQUIRED: "decision.recommendation.review_required",
    RECOMMEND_REJECT: "decision.recommendation.recommend_reject", RECOMMEND_HOLD: "decision.recommendation.recommend_hold",
    RECOMMEND_APPROVE: "decision.recommendation.recommend_approve",
  };
  const REC_CHIP = { RECOMMEND_APPROVE: "ok", RECOMMEND_REJECT: "red", RECOMMEND_HOLD: "wait", REVIEW_REQUIRED: "blue", INSUFFICIENT_DATA: "gray" };
  const CAND_SEG = { DISCOVERED: 1, ANALYZED: 2, RECOMMENDED: 3, APPROVED: 2, HELD: 4, REJECTED: 5, EXPIRED: 4 };
  const AI_CHIP = { DRAFT: "gray", OFFLINE_EVALUATED: "blue", REGRESSION_COMPARED: "blue", APPROVED: "ok", REJECTED: "red" };

  function tryT(key, fallback) {
    const text = T(key);
    return text === key ? fallback : text;
  }

  // 반려 사유 AI 설명(참고용): 이미 받은 설명을 그리거나, 버튼을 눌렀을 때만 서버에 요청한다(월 한도·승인 센터 허용 필요). 글은 전부 이스케이프한다.
  function productAdviceHtml(ai) {
    const list = (items) => (Array.isArray(items) && items.length ? `<ul style="margin:4px 0 0 18px;padding:0">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : "");
    return `<div class="ez-note info" style="margin-top:10px"><span class="ez-dot info">AI</span><div><b>${esc(T("easy.advice.ai_title"))}</b>`
      + `<div style="margin-top:4px">${esc(ai.summary || "")}</div>${list(ai.causes)}`
      + `<div class="ez-muted" style="font-size:12px;margin-top:6px">${esc(T("easy.advice.ai_steps"))}</div>${list(ai.steps)}`
      + `<div class="ez-muted" style="font-size:12px;margin-top:6px">${esc(T("easy.advice.ai_note"))}</div></div></div>`;
  }

  async function productAdviceAsk(p) {
    const ok = await ezDialog({ title: T("easy.advice.ask_title"), bodyHtml: `<p>${esc(T("easy.advice.ask_body"))}</p>`, okLabel: T("easy.advice.ask_btn") });
    if (!ok) return;
    try {
      const res = await api("/orders/product-rejection-advice", { method: "POST", body: JSON.stringify({ seller_product_id: p.seller_product_id }) });
      const body = res.explanation ? productAdviceHtml(res.explanation)
        : `<div class="ez-note warn" style="margin-top:10px"><span class="ez-dot warn">!</span><div>${esc(T("easy.advice.ai_none"))}${Object.values(res.rejected || {}).map((w) => `<div>${esc(w)}</div>`).join("")}</div></div>`;
      const usage = res.usage ? `<p class="ez-muted" style="font-size:12px;margin-top:8px">${esc(T("easy.advice.ai_usage", { cost: res.usage.cost_krw, used: Math.round(res.usage.month_used_krw || 0), limit: res.usage.monthly_limit_krw }))}</p>` : "";
      await ezDialog({ title: T("easy.advice.ai_title"), bodyHtml: body + usage, okLabel: T("easy.advice.close") });
      if (res.explanation) loadToday();
    } catch (err) {
      B.toast((err && err.message) || T("easy.advice.ai_failed"), "error");
    }
  }

  // 운영 상태 이상 AI 설명(참고용): 버튼을 눌렀을 때만 서버에 요청한다(월 한도·승인 센터 허용 필요). 서버가 점검 결과를 직접 모으고 요청에는 글을 싣지 않는다. 글은 전부 이스케이프한다.
  function systemAdviceHtml(advice, system) {
    const by = new Map((system || []).map((c) => [c.code, c]));
    const list = (items) => (Array.isArray(items) && items.length ? `<ul style="margin:4px 0 0 18px;padding:0">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : "");
    const items = (advice.items || []).filter((it) => it && BRIEF_SYS_KEYS[it.code]).map((it) =>
      `<div style="margin-top:10px"><b>${esc(briefSysText(by.get(it.code) || { code: it.code, params: {} }))}</b>`
      + `<div style="margin-top:2px">${esc(it.cause || "")}</div>`
      + `<div class="ez-muted" style="font-size:12px;margin-top:4px">${esc(T("easy.sysadv.steps"))}</div>${list(it.steps)}</div>`).join("");
    return `<div class="ez-note info" style="margin-top:10px"><span class="ez-dot info">AI</span><div>`
      + `${advice.summary ? `<div>${esc(advice.summary)}</div>` : ""}${items}`
      + `<div class="ez-muted" style="font-size:12px;margin-top:8px">${esc(T("easy.sysadv.note"))}</div></div></div>`;
  }

  async function systemAdviceAsk(system) {
    const ok = await ezDialog({ title: T("easy.sysadv.ask_title"), bodyHtml: `<p>${esc(T("easy.sysadv.ask_body"))}</p>`, okLabel: T("easy.sysadv.btn") });
    if (!ok) return;
    try {
      const res = await api("/diagnostics/health-advice", { method: "POST" });
      const body = res.advice ? systemAdviceHtml(res.advice, system)
        : `<div class="ez-note warn" style="margin-top:10px"><span class="ez-dot warn">!</span><div>${esc(T("easy.sysadv.none"))}${Object.values(res.rejected || {}).map((w) => `<div>${esc(w)}</div>`).join("")}</div></div>`;
      const at = res.cached && res.at ? parseUtcMs(res.at) : null;
      const cached = at === null ? "" : `<p class="ez-muted" style="font-size:12px;margin-top:8px">${esc(T("easy.sysadv.cached", { at: new Date(at).toLocaleString() }))}</p>`;
      const usage = res.usage ? `<p class="ez-muted" style="font-size:12px;margin-top:8px">${esc(T("easy.sysadv.usage", { cost: res.usage.cost_krw, used: Math.round(res.usage.month_used_krw || 0), limit: res.usage.monthly_limit_krw }))}</p>` : "";
      await ezDialog({ title: T("easy.sysadv.title"), bodyHtml: body + cached + usage, okLabel: T("easy.sysadv.close") });
    } catch (err) {
      B.toast((err && err.message) || T("easy.sysadv.failed"), "error");
    }
  }

  // 0~1 점수를 막대와 숫자로(높을수록 초록, 낮을수록 주황·빨강). 점수가 없으면 "점수 없음".
  function scoreCell(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return `<span class="ez-faint">${esc(T("easy.an.no_score"))}</span>`;
    const v = Math.max(0, Math.min(1, Number(value)));
    const tone = v >= 0.66 ? "" : (v >= 0.33 ? "mid" : "neg");
    return `<div class="ez-score"><div class="ez-num">${v.toFixed(2)}</div><div class="ez-bar ${tone}"><i style="width:${Math.round(v * 100)}%"></i></div></div>`;
  }

  // ---- 판매 성과 (2026-10-18, 마케팅 AI M4 lite) ----
  // 쿠팡 매출내역을 사용자가 "쿠팡에서 읽기"를 눌렀을 때만 읽는다(읽기 전용, 승인 센터 '반품·민원·정산 확인' 허용 필요). 화면을 열거나 새로고침할 때는 읽지 않는다.
  // 옵션별 판매·반품·수수료와 표본 크기만 보여 주고, 어떤 제안이 맞았는지는 판정하지 않는다. 서버가 준 모든 글은 이스케이프한다.

  const PERF_SAMPLE = { INSUFFICIENT: ["gray", "easy.perf.sample_insufficient"], REFERENCE: ["blue", "easy.perf.sample_reference"], ENOUGH: ["ok", "easy.perf.sample_enough"] };
  const PERF_DAYS = [7, 14, 30];
  const perfState = { days: 30, data: null, error: "", busy: false };

  function perfPercent(rate) {
    return rate === null || rate === undefined || rate === "" ? "—" : `${(Number(rate) * 100).toFixed(1)}%`;
  }

  function perfWon(value) {
    return value === null || value === undefined || value === "" ? "—" : `${Number(value).toLocaleString()}${T("easy.perf.won")}`;
  }

  function perfHtml(state) {
    const note = (tone, text) => `<div class="ez-note ${tone}" style="margin-top:12px"><span class="ez-dot ${tone}" aria-hidden="true">${tone === "info" ? "i" : "!"}</span><div>${esc(text)}</div></div>`;
    const num = (value) => (value === null || value === undefined || value === "" ? "—" : value);  // 없는 값은 "undefined" 대신 "—"
    const data = state && state.data;
    if (state && state.error) return note("warn", state.error);
    if (!data) return note("info", T("easy.perf.empty"));
    if (!data.ok) {
      if (data.no_connection) return note("warn", T("easy.perf.no_connection"));
      if (data.blocked) return note("warn", T("easy.perf.blocked"));
      return note("warn", T("easy.perf.failed", { why: data.detail || "" }));
    }
    const s = data.summary || { totals: {}, items: [], notes: [], period: {} };
    const t = s.totals || {};
    const sampleChip = (level) => { const pair = PERF_SAMPLE[level] || PERF_SAMPLE.INSUFFICIENT; return `<span class="ez-chip ${pair[0]}">${esc(T(pair[1]))}</span>`; };
    const cards = `<div class="ez-cards" style="margin-top:12px">
      <div class="ez-card accent"><div class="k">${esc(T("easy.perf.k_orders"))}</div><div class="v">${esc(t.orders || 0)}</div><div class="s">${sampleChip(t.sample)}</div></div>
      <div class="ez-card"><div class="k">${esc(T("easy.perf.k_units"))}</div><div class="v">${esc(t.sale_units || 0)}</div><div class="s">${esc(T("easy.perf.k_items", { n: t.items || 0 }))}</div></div>
      <div class="ez-card"><div class="k">${esc(T("easy.perf.k_amount"))}</div><div class="v">${esc(perfWon(t.sale_amount))}</div><div class="s">${esc(T("easy.perf.k_fee", { rate: perfPercent(t.fee_ratio) }))}</div></div>
      <div class="ez-card"><div class="k">${esc(T("easy.perf.k_return"))}</div><div class="v">${esc(perfPercent(t.return_rate))}</div><div class="s">${esc(T("easy.perf.k_return_units", { n: t.refund_units || 0 }))}</div></div></div>`;
    const period = s.period && s.period.from ? `<p class="ez-muted" style="margin:10px 0 0">${esc(T("easy.perf.period", { from: s.period.from, to: s.period.to }))}</p>` : "";
    const rows = (s.items || []).map((item) => `<tr>
      <td><b>${esc(item.name || T("easy.perf.no_name"))}</b><small class="ez-muted" style="display:block;font-size:13px">${esc(num(item.vendor_item_id))}${item.refund_without_sale ? ` · ${esc(T("easy.perf.refund_only"))}` : ""}</small></td>
      <td class="ez-num">${esc(num(item.sale_units))}</td><td class="ez-num">${esc(perfWon(item.sale_amount))}</td>
      <td class="ez-num">${esc(num(item.refund_units))} · ${esc(perfPercent(item.return_rate))}${item.high_return ? ` <span class="ez-chip red">${esc(T("easy.perf.high_return"))}</span>` : ""}</td>
      <td class="ez-num">${esc(perfPercent(item.fee_ratio))}</td><td>${sampleChip(item.sample)}</td></tr>`).join("");
    const table = rows
      ? `<table class="ez-table" style="margin-top:12px"><thead><tr>${["easy.perf.col_option", "easy.perf.col_units", "easy.perf.col_amount", "easy.perf.col_return", "easy.perf.col_fee", "easy.perf.col_sample"].map((k) => `<th>${esc(T(k))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`
      : note("info", T("easy.perf.no_sales"));
    const extras = [];
    if (data.truncated) extras.push(note("warn", T("easy.perf.truncated")));
    if (s.omitted) extras.push(`<p class="ez-muted">${esc(T("easy.perf.omitted", { n: s.omitted }))}</p>`);
    if (s.duplicates || s.skipped) extras.push(`<p class="ez-muted">${esc(T("easy.perf.skipped", { d: s.duplicates || 0, s: s.skipped || 0 }))}</p>`);
    const notes = (s.notes || []).map((text) => `<p class="ez-muted" style="font-size:13px;margin:4px 0">${esc(text)}</p>`).join("");
    return `${cards}${period}${table}${extras.join("")}<div style="margin-top:12px">${notes}</div>
      <p class="ez-faint" style="font-size:12px">${esc(T("easy.perf.checked", { at: data.checked_at ? new Date(`${data.checked_at}Z`).toLocaleString() : "" }))}</p>`;
  }

  function renderPerf(host) {
    host.innerHTML = `<div class="ez-note info" style="margin-top:12px"><span class="ez-dot info" aria-hidden="true">i</span><div>${esc(T("easy.perf.lead"))}</div></div>
      <div style="display:flex;gap:12px;align-items:center;margin:14px 0;flex-wrap:wrap">
        <label>${esc(T("easy.perf.days_label"))} <select id="ez-perf-days">${PERF_DAYS.map((d) => `<option value="${d}" ${d === perfState.days ? "selected" : ""}>${esc(T("easy.perf.days_opt", { d }))}</option>`).join("")}</select></label>
        <button type="button" class="ez-btn" id="ez-perf-read">${esc(T("easy.perf.read_btn"))}</button></div>
      <div id="ez-perf-out" aria-live="polite">${perfHtml(perfState)}</div>`;
    $("ez-perf-days").addEventListener("change", (event) => { perfState.days = Number(event.target.value) || 30; });
    $("ez-perf-read").addEventListener("click", async () => {
      if (perfState.busy) return;   // 확인 창이 떠 있거나 읽는 중이면 다시 누르지 못한다(창이 겹쳐 열리거나 두 번 읽는 것을 막는다)
      perfState.busy = true;
      try {
        const ok = await ezDialog({ title: T("easy.perf.ask_title"), bodyHtml: `<p>${esc(T("easy.perf.ask_body", { d: perfState.days }))}</p>`, okLabel: T("easy.perf.read_btn") });
        if (!ok) return;
        const out = $("ez-perf-out");
        out.innerHTML = loadingHtml();
        try {
          perfState.data = await api(`/orders/sales-performance?days=${encodeURIComponent(perfState.days)}`, { method: "POST" });
          perfState.error = "";
        } catch (err) {
          perfState.error = (err && err.message) || T("easy.perf.failed", { why: "" });
        }
        const target = $("ez-perf-out");
        if (target) target.innerHTML = perfHtml(perfState);
      } finally {
        perfState.busy = false;
      }
    });
  }

  // ---- /판매 성과 ----

  // ---- 평가 기준 (2026-10-30, AI 통합 계획 1단계) ----
  // 판매 판단 AI가 쓸 기준(12개 항목 비중·승인 추천 점수·신뢰도)을 보고, 고치고, 확정한다. 화면을 여는 것은 읽기만 한다.
  // 확정은 확인 창 → 비밀번호 재확인(요약 표시) → 서버가 회사 정책을 만든다. 차단 규칙·근거 비중 기준은 평가 프로그램에 있어 여기서 바꾸지 않는다.

  const CRIT_AXES = [
    ["MARGIN_AND_FEES", "easy.crit.ax_margin"], ["POLICY_PROHIBITED_RISK", "easy.crit.ax_policy"], ["BRAND_IP_RISK", "easy.crit.ax_brand"],
    ["SUPPLY_STABILITY", "easy.crit.ax_supply"], ["DEMAND_TREND_STRENGTH", "easy.crit.ax_demand"], ["RETURN_CLAIM_RISK", "easy.crit.ax_return"],
    ["PRICE_COMPETITIVENESS", "easy.crit.ax_price"], ["COMPETITION_INTENSITY", "easy.crit.ax_competition"], ["INVENTORY_SHIPPING_RISK", "easy.crit.ax_shipping"],
    ["REVENUE_POTENTIAL", "easy.crit.ax_revenue"], ["FUNDING_LIMIT_IMPACT", "easy.crit.ax_funding"], ["DATA_RELIABILITY", "easy.crit.ax_reliability"],
  ];
  const critState = { data: null, error: "", busy: false };

  function critNum(value, digits) {
    return value === null || value === undefined || value === "" || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
  }

  function critPct(rate) {
    return rate === null || rate === undefined || rate === "" ? "—" : `${Math.round(Number(rate) * 100)}%`;
  }

  function critDate(iso) {
    return iso ? new Date(`${iso}Z`).toLocaleDateString() : "—";
  }

  // 입력칸의 지금 값. 비중 합은 만분율 정수로 더해 소수 오차 없이 1.00인지 본다(서버도 소수 4자리로 확인한다).
  function critReadForm(root) {
    const weights = {};
    let bad = false;
    CRIT_AXES.forEach(([axis]) => {
      const el = root.querySelector(`[data-ez-crit-w="${axis}"]`);
      const text = el ? String(el.value).trim() : "";
      const v = Number(text);
      if (text === "" || !Number.isFinite(v) || v < 0 || v > 1) bad = true;
      weights[axis] = text;
    });
    const sumBp = bad ? NaN : CRIT_AXES.reduce((acc, [axis]) => acc + Math.round(Number(weights[axis]) * 10000), 0);
    const scoreEl = root.querySelector("#ez-crit-score"), confEl = root.querySelector("#ez-crit-conf");
    const score = scoreEl && scoreEl.value !== "" ? Number(scoreEl.value) : NaN;
    const conf = confEl && confEl.value !== "" ? Number(confEl.value) : NaN;
    return { weights, score, conf, sumBp, bad };
  }

  function critProblem(f) {
    if (f.bad) return T("easy.crit.err_weight");
    if (f.sumBp !== 10000) return T("easy.crit.err_sum", { sum: (f.sumBp / 10000).toFixed(2) });
    if (!Number.isFinite(f.score) || f.score <= 0 || f.score > 100) return T("easy.crit.err_score");
    if (!Number.isFinite(f.conf) || f.conf <= 0 || f.conf > 1) return T("easy.crit.err_conf");
    return "";
  }

  function critVersionText(version) {
    if (version === "D1-20261029") return T("easy.crit.ver_draft");
    if (version === "D1-20261029-EDITED") return T("easy.crit.ver_edited");
    return String(version || "");
  }

  function critHtml(d) {
    const note = (tone, text) => `<div class="ez-note ${tone}" style="margin-top:12px"><span class="ez-dot ${tone}" aria-hidden="true">${tone === "warn" ? "!" : "i"}</span><div>${esc(text)}</div></div>`;
    const use = d.in_use;
    const fr = d.fixed_rules || {};
    const state = !use ? note("warn", T("easy.crit.none"))
      : (use.source === "COMPANY"
        ? note("ok", T("easy.crit.company", { ver: critVersionText(use.policy_version), at: critDate(use.effective_at), until: critDate(use.expires_at) }))
        : note("info", T("easy.crit.global", { ver: use.policy_version })));
    // 입력칸의 처음 값: 우리 회사 기준이 있으면 그 값, 없으면 초안.
    const start = use && use.source === "COMPANY" ? use : d.draft;
    const required = new Set(fr.required_for_approve || []), blocks = new Set(fr.hard_block_axes || []);
    const rows = CRIT_AXES.map(([axis, key]) => `<tr>
      <td><b>${esc(T(key))}</b>${required.has(axis) ? ` <span class="ez-chip blue">${esc(T("easy.crit.required"))}</span>` : ""}${blocks.has(axis) ? ` <span class="ez-chip red">${esc(T("easy.crit.block_chip", { n: critNum(fr.hard_block_threshold, 0) }))}</span>` : ""}</td>
      <td class="ez-num">${esc(critNum((d.draft.axis_weights || {})[axis], 2))}</td>
      <td class="ez-num">${use ? esc(critNum((use.axis_weights || {})[axis], 2)) : "—"}</td>
      <td><input class="ez-crit-in" type="number" step="0.01" min="0" max="1" inputmode="decimal" data-ez-crit-w="${axis}" aria-label="${esc(T(key))}" value="${esc(critNum((start.axis_weights || {})[axis], 2))}"></td></tr>`).join("");
    const history = (d.history || []).map((p) => {
      const expired = p.expires_at && new Date(`${p.expires_at}Z`).getTime() <= Date.now();
      const chip = p.in_use ? `<span class="ez-chip ok">${esc(T("easy.crit.st_in_use"))}</span>`
        : `<span class="ez-chip gray">${esc(T(expired ? "easy.crit.st_expired" : "easy.crit.st_old"))}</span>`;
      return `<tr><td class="ez-num">${esc(critDate(p.effective_at))}</td><td>${esc(critVersionText(p.policy_version))}<small class="ez-muted" style="display:block;font-size:13px">${esc(T("easy.crit.hist_values", { score: critNum(p.min_approve_total_score, 0), conf: critNum(p.min_confidence_for_recommendation, 2) }))}</small></td>
        <td class="ez-num">${esc(critDate(p.expires_at))}</td><td>${chip}</td></tr>`;
    }).join("");
    return `${note("info", T("easy.crit.lead"))}${state}
      <table class="ez-table ez-crit"><thead><tr>${["easy.crit.col_axis", "easy.crit.col_draft", "easy.crit.col_now", "easy.crit.col_new"].map((k) => `<th>${esc(T(k))}</th>`).join("")}</tr></thead>
        <tbody>${rows}<tr><td><b>${esc(T("easy.crit.total"))}</b></td><td class="ez-num">1.00</td><td class="ez-num">${use ? "1.00" : "—"}</td><td class="ez-num" id="ez-crit-sum"></td></tr></tbody></table>
      <div class="ez-crit-th">
        <label>${esc(T("easy.crit.score_label"))} <input class="ez-crit-in" id="ez-crit-score" type="number" step="1" min="1" max="100" inputmode="numeric" value="${esc(critNum(start.min_approve_total_score, 0))}"> <small class="ez-faint">${esc(T("easy.crit.draft_is", { v: critNum(d.draft.min_approve_total_score, 0) }))}</small></label>
        <label>${esc(T("easy.crit.conf_label"))} <input class="ez-crit-in" id="ez-crit-conf" type="number" step="0.05" min="0.05" max="1" inputmode="decimal" value="${esc(critNum(start.min_confidence_for_recommendation, 2))}"> <small class="ez-faint">${esc(T("easy.crit.draft_is", { v: critNum(d.draft.min_confidence_for_recommendation, 2) }))}</small></label>
      </div>
      <div id="ez-crit-msg" aria-live="polite"></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px">
        <button type="button" class="ez-btn" id="ez-crit-confirm">${esc(T("easy.crit.confirm_btn"))}</button>
        <button type="button" class="ez-btn line" id="ez-crit-reset">${esc(T("easy.crit.reset_btn"))}</button></div>
      <div class="ez-sec">${esc(T("easy.crit.fixed_title"))}<small>${esc(T("easy.crit.fixed_sub"))}</small></div>
      <ul class="ez-crit-rules">
        <li>${esc(T("easy.crit.rule_axes", { n: fr.min_sufficient_axes === undefined ? "—" : fr.min_sufficient_axes }))}</li>
        <li>${esc(T("easy.crit.rule_coverage", { p: critPct(fr.min_weight_coverage) }))}</li>
        <li>${esc(T("easy.crit.rule_required"))}</li>
        <li>${esc(T("easy.crit.rule_block", { n: critNum(fr.hard_block_threshold, 0) }))}</li>
        <li>${esc(T("easy.crit.rule_margin", { p: critPct(fr.min_margin_rate) }))}</li>
        <li>${esc(T("easy.crit.rule_funding"))}</li>
        <li>${esc(T("easy.crit.rule_human"))}</li></ul>
      <div class="ez-sec">${esc(T("easy.crit.hist_title"))}</div>
      ${history
    ? `<table class="ez-table"><thead><tr>${["easy.crit.col_at", "easy.crit.col_ver", "easy.crit.col_until", "easy.col.state"].map((k) => `<th>${esc(T(k))}</th>`).join("")}</tr></thead><tbody>${history}</tbody></table>`
    : `<p class="ez-muted">${esc(T("easy.crit.hist_none"))}</p>`}`;
  }

  async function renderCriteria(host) {
    host.innerHTML = loadingHtml();
    try {
      critState.data = await api("/decision-company-policies/criteria");
      critState.error = "";
    } catch (err) {
      critState.data = null;
      critState.error = (err && err.message) || T("easy.error");
    }
    if (!critState.data) {
      host.innerHTML = `<div class="ez-note warn" style="margin-top:12px"><span class="ez-dot warn" aria-hidden="true">!</span><div>${esc(critState.error)}</div></div>`;
      return;
    }
    const d = critState.data;
    host.innerHTML = critHtml(d);
    const sync = () => {
      const f = critReadForm(host);
      const problem = critProblem(f);
      $("ez-crit-sum").textContent = Number.isFinite(f.sumBp) ? (f.sumBp / 10000).toFixed(2) : "—";
      $("ez-crit-msg").innerHTML = problem
        ? `<div class="ez-note warn" style="margin-top:12px"><span class="ez-dot warn" aria-hidden="true">!</span><div>${esc(problem)}</div></div>`
        : `<p class="ez-muted" style="margin:10px 0 0">${esc(T("easy.crit.sum_ok"))}</p>`;
    };
    host.querySelectorAll(".ez-crit-in").forEach((el) => el.addEventListener("input", sync));
    sync();
    $("ez-crit-reset").addEventListener("click", () => {
      CRIT_AXES.forEach(([axis]) => { host.querySelector(`[data-ez-crit-w="${axis}"]`).value = critNum(d.draft.axis_weights[axis], 2); });
      $("ez-crit-score").value = critNum(d.draft.min_approve_total_score, 0);
      $("ez-crit-conf").value = critNum(d.draft.min_confidence_for_recommendation, 2);
      sync();
    });
    $("ez-crit-confirm").addEventListener("click", () => confirmCriteria(host));
  }

  // 확인 창 → 비밀번호 재확인(무엇을 확정하는지 요약) → 확정. 화면을 연 뒤 다른 곳에서 기준이 바뀌었으면 서버가 거절한다(expected_in_use_id).
  async function confirmCriteria(host) {
    if (critState.busy || !critState.data) return;
    const f = critReadForm(host);
    const problem = critProblem(f);
    if (problem) { B.toast(problem, "error"); return; }
    critState.busy = true;
    try {
      const d = critState.data;
      const bp = (v) => Math.round(Number(v) * 10000);
      const changed = CRIT_AXES.filter(([axis]) => bp(f.weights[axis]) !== bp(d.draft.axis_weights[axis])).map(([, key]) => T(key));
      const sameAsDraft = !changed.length && f.score === Number(d.draft.min_approve_total_score) && bp(f.conf) === bp(d.draft.min_confidence_for_recommendation);
      const ok = await ezDialog({ title: T("easy.crit.ask_title"), bodyHtml: `<p>${esc(T("easy.crit.ask_body", { days: d.valid_days }))}</p>`, okLabel: T("easy.crit.confirm_btn") });
      if (!ok) return;
      const summary = [
        { label: T("easy.crit.sum_what"), value: T("easy.crit.sum_what_v") },
        { label: T("easy.crit.sum_score"), value: String(f.score) },
        { label: T("easy.crit.sum_conf"), value: f.conf.toFixed(2) },
        { label: T("easy.crit.sum_weights"), value: changed.length ? T("easy.crit.sum_changed", { list: changed.join(", ") }) : T("easy.crit.sum_draft"), strong: !sameAsDraft },
        { label: T("easy.crit.sum_valid"), value: T("easy.crit.sum_days", { n: d.valid_days }) },
      ];
      const token = await B.promptRecentAuthToken({ summary });
      if (token === null) return;
      await api("/decision-company-policies/criteria/confirm", {
        method: "POST", headers: { "X-Recent-Auth-Token": token },
        body: JSON.stringify({
          axis_weights: f.weights, min_approve_total_score: String(f.score), min_confidence_for_recommendation: String(f.conf),
          expected_in_use_id: d.in_use && d.in_use.source === "COMPANY" ? d.in_use.id : null,
        }),
      });
      B.toast(T("easy.crit.done"), "success");
      await renderCriteria(host);
    } catch (err) {
      B.toast((err && err.message) || T("easy.error"), "error");
    } finally {
      critState.busy = false;
    }
  }

  // ---- /평가 기준 ----

  // ---- 평가하기 (2026-10-31, AI 통합 계획 1단계 마무리) ----
  // 후보 하나를 골라 자동 제안(위저드 마진·사전 평가)을 보고, 사람이 아는 점수를 더 넣은 뒤 '미리 보기'로 결과를 본다(저장 없음).
  // '평가 기록하기'를 눌러야 평가가 남고 판매 판단 대기 목록에 오른다. 승인·보류·거절은 기존 판단 상세 화면에서 사람이 한다.
  // 상품 상태·가격·쿠팡에는 아무것도 하지 않는다. 미리 본 값과 지금 입력이 다르면 기록하지 않는다.

  const EVAL_FIELDS = [
    ["expected_margin_rate", "easy.eval.f_margin", "pct", true, "easy.eval.h_margin"],
    ["policy_prohibited_score", "easy.eval.f_policy", "score", true, "easy.eval.h_policy"],
    ["brand_ip_risk_score", "easy.eval.f_brand", "score", true, "easy.eval.h_brand"],
    ["supply_stability_score", "easy.eval.f_supply", "score", true, "easy.eval.h_supply"],
    ["return_claim_risk_score", "easy.eval.f_return", "score", false, "easy.eval.h_return"],
    ["price_competitiveness_score", "easy.eval.f_price", "score", false, "easy.eval.h_price"],
    ["inventory_shipping_risk_score", "easy.eval.f_shipping", "score", false, "easy.eval.h_shipping"],
  ];
  const evalState = { candidateId: null, suggestion: null, preview: null, previewKey: "", recordKey: "", busy: false };

  // 입력칸 → 서버로 보낼 보강 입력. 빈칸은 '모름'이라 보내지 않는다. 마진은 %로 받아 비율(0.1824)로 바꾼다.
  function evalReadForm(root) {
    const inputs = {};
    let problem = "";
    EVAL_FIELDS.forEach(([field, labelKey, kind]) => {
      const el = root.querySelector(`[data-ez-eval="${field}"]`);
      const text = el ? String(el.value).trim() : "";
      if (text === "") return;
      const v = Number(text);
      const ok = Number.isFinite(v) && v >= 0 && (kind === "pct" ? v < 100 : v <= 100);
      if (!ok) { if (!problem) problem = T(kind === "pct" ? "easy.eval.err_pct" : "easy.eval.err_score", { name: T(labelKey) }); return; }
      inputs[field] = kind === "pct" ? (Math.round(v * 100) / 10000).toFixed(4) : String(v);
    });
    return { inputs, problem };
  }

  function evalFormHtml(sug) {
    const basis = new Map((sug.basis || []).map((b) => [b.field, b]));
    const start = sug.inputs || {};
    const rows = EVAL_FIELDS.map(([field, labelKey, kind, required, hintKey]) => {
      const raw = start[field];
      const value = raw === undefined ? "" : (kind === "pct" ? (Number(raw) * 100).toFixed(2) : String(Number(raw)));
      const b = basis.get(field);
      const why = b ? `<span class="ez-chip blue">${esc(T("easy.eval.suggested"))}</span> ${esc(b.note || "")}` : `<span class="ez-muted">${esc(T(hintKey))}</span>`;
      return `<tr><td><b>${esc(T(labelKey))}</b>${required ? ` <span class="ez-chip blue">${esc(T("easy.crit.required"))}</span>` : ""}</td>
        <td><input class="ez-crit-in ez-eval-in" type="number" step="${kind === "pct" ? "0.01" : "1"}" min="0" max="100" inputmode="decimal" data-ez-eval="${field}" aria-label="${esc(T(labelKey))}" value="${esc(value)}">${kind === "pct" ? " %" : ""}</td>
        <td class="ez-eval-why">${why}</td></tr>`;
    }).join("");
    const notes = (sug.notes || []).map((text) => `<li>${esc(text)}</li>`).join("");
    return `<table class="ez-table ez-eval"><thead><tr>${["easy.eval.col_item", "easy.eval.col_value", "easy.eval.col_why"].map((k) => `<th>${esc(T(k))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
      ${notes ? `<ul class="ez-crit-rules">${notes}</ul>` : ""}
      <p class="ez-muted" style="margin:10px 0 0">${esc(T("easy.eval.blank_unknown"))}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px"><button type="button" class="ez-btn" id="ez-eval-preview">${esc(T("easy.eval.preview_btn"))}</button></div>
      <div id="ez-eval-out" aria-live="polite"></div>`;
  }

  function evalPreviewHtml(p) {
    const labels = new Map(CRIT_AXES);
    const rec = `<span class="ez-chip ${REC_CHIP[p.recommendation] || "gray"}">${esc(REC_KEY[p.recommendation] ? T(REC_KEY[p.recommendation]) : String(p.recommendation))}</span>`;
    const rows = (p.axes || []).map((a) => {
      const state = !a.data_sufficient ? `<span class="ez-chip gray">${esc(T("easy.eval.st_unknown"))}</span>`
        : (a.risk_flag ? `<span class="ez-chip red">${esc(T("easy.eval.st_risk"))}</span>` : `<span class="ez-chip ok">${esc(T("easy.eval.st_ok"))}</span>`);
      return `<tr><td>${esc(labels.has(a.axis) ? T(labels.get(a.axis)) : a.axis)}</td><td class="ez-num">${esc(critNum(a.weight, 2))}</td>
        <td class="ez-num">${a.data_sufficient ? esc(critNum(a.raw_score, 0)) : "—"}</td><td>${state}</td></tr>`;
    }).join("");
    const fr = p.fixed_rules || {};
    return `<div class="ez-sec">${esc(T("easy.eval.result_title"))}<small>${esc(T("easy.eval.result_sub"))}</small></div>
      <div class="ez-cards">
        <div class="ez-card accent"><div class="k">${esc(T("easy.eval.k_rec"))}</div><div class="v" style="font-size:18px">${rec}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.eval.k_total"))}</div><div class="v">${esc(critNum(p.total_score, 1))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.eval.k_cover"))}</div><div class="v">${esc(critPct(p.coverage))}</div><div class="s">${esc(T("easy.eval.k_cover_s", { p: critPct(fr.min_weight_coverage) }))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.eval.k_axes"))}</div><div class="v">${esc(p.sufficient_count)}/12</div><div class="s">${esc(T("easy.eval.k_axes_s", { n: fr.min_sufficient_axes === undefined ? "—" : fr.min_sufficient_axes }))}</div></div></div>
      <div class="ez-note info" style="margin-top:12px"><span class="ez-dot info" aria-hidden="true">i</span><div>${esc(p.reason || "")}</div></div>
      <table class="ez-table"><thead><tr>${["easy.crit.col_axis", "easy.eval.col_weight", "easy.eval.col_score", "easy.col.state"].map((k) => `<th>${esc(T(k))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
      <p class="ez-muted" style="margin:12px 0 0">${esc(T("easy.eval.record_note"))}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px"><button type="button" class="ez-btn" id="ez-eval-record">${esc(T("easy.eval.record_btn"))}</button></div>`;
  }

  function evalErrorHtml(message) {
    return `<div class="ez-note warn" style="margin-top:12px"><span class="ez-dot warn" aria-hidden="true">!</span><div>${esc(message)}
      <div style="margin-top:8px"><button type="button" class="ez-btn line" data-ez-eval-crit>${esc(T("easy.eval.open_crit"))}</button></div></div></div>`;
  }

  function evalBindCrit(root) {
    root.querySelectorAll("[data-ez-eval-crit]").forEach((btn) => btn.addEventListener("click", () => { S.selected.analysisTab = "CRIT"; loadAnalysis(); }));
  }

  function renderEval(host, cands) {
    const list = (cands || []).slice().sort((a, b) => b.id - a.id);
    if (!list.length) { host.innerHTML = emptyHtml("easy.an.empty_cand", "easy.an.empty_cand_s"); return; }
    host.innerHTML = `<div class="ez-note info" style="margin-top:12px"><span class="ez-dot info" aria-hidden="true">i</span><div>${esc(T("easy.eval.lead"))}</div></div>
      <label class="ez-eval-pick">${esc(T("easy.eval.pick"))} <select id="ez-eval-cand"><option value="">${esc(T("easy.eval.pick_none"))}</option>${list.map((c) =>
        `<option value="${c.id}" ${c.id === evalState.candidateId ? "selected" : ""}>#${c.id} ${esc(c.product_name)}</option>`).join("")}</select></label>
      <div id="ez-eval-body"></div>`;
    $("ez-eval-cand").addEventListener("change", (event) => {
      evalState.candidateId = Number(event.target.value) || null;
      evalLoad();
    });
    if (evalState.candidateId && list.some((c) => c.id === evalState.candidateId)) evalLoad();
  }

  async function evalLoad() {
    const body = $("ez-eval-body");
    const id = evalState.candidateId;
    evalState.suggestion = null;
    evalState.preview = null;
    if (!body) return;
    if (!id) { body.innerHTML = ""; return; }
    body.innerHTML = loadingHtml();
    let sug;
    try {
      sug = await api(`/decisions/candidates/${encodeURIComponent(id)}/suggested-inputs`);
    } catch (err) {
      body.innerHTML = evalErrorHtml((err && err.message) || T("easy.error"));
      evalBindCrit(body);
      return;
    }
    if (evalState.candidateId !== id || !$("ez-eval-body")) return;  // 그 사이 다른 후보를 골랐다
    evalState.suggestion = sug;
    body.innerHTML = evalFormHtml(sug);
    body.querySelectorAll(".ez-eval-in").forEach((el) => el.addEventListener("input", () => {
      // 값을 바꾸면 미리 본 결과는 더 이상 이 입력의 결과가 아니다 — 다시 미리 봐야 기록할 수 있다.
      if (!evalState.preview) return;
      evalState.preview = null;
      $("ez-eval-out").innerHTML = `<p class="ez-muted" style="margin:10px 0 0">${esc(T("easy.eval.changed"))}</p>`;
    }));
    $("ez-eval-preview").addEventListener("click", () => evalPreview(body));
  }

  async function evalPreview(body) {
    if (evalState.busy || !evalState.candidateId) return;
    const f = evalReadForm(body);
    if (f.problem) { B.toast(f.problem, "error"); return; }
    evalState.busy = true;
    const out = $("ez-eval-out");
    out.innerHTML = loadingHtml();
    try {
      const id = evalState.candidateId;
      const p = await api("/decisions/preview", { method: "POST", body: JSON.stringify({ candidate_id: id, supplementary_inputs: f.inputs }) });
      if (evalState.candidateId !== id) return;
      evalState.preview = p;
      evalState.previewKey = JSON.stringify([id, f.inputs]);
      // 같은 입력으로 다시 기록을 눌러도(응답을 못 받은 경우 등) 평가가 하나만 생기도록 미리 본 입력마다 멱등 키를 하나 만든다.
      evalState.recordKey = `easy-eval-${id}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      out.innerHTML = evalPreviewHtml(p);
      $("ez-eval-record").addEventListener("click", () => evalRecord(body));
    } catch (err) {
      out.innerHTML = evalErrorHtml((err && err.message) || T("easy.error"));
      evalBindCrit(out);
    } finally {
      evalState.busy = false;
    }
  }

  async function evalRecord(body) {
    if (evalState.busy || !evalState.preview) return;
    const f = evalReadForm(body);
    const id = evalState.candidateId;
    if (f.problem || JSON.stringify([id, f.inputs]) !== evalState.previewKey) { B.toast(T("easy.eval.changed"), "error"); return; }
    evalState.busy = true;
    try {
      const ok = await ezDialog({ title: T("easy.eval.ask_title"), bodyHtml: `<p>${esc(T("easy.eval.ask_body"))}</p>`, okLabel: T("easy.eval.record_btn") });
      if (!ok) return;
      const res = await api("/decisions/evaluate", {
        method: "POST", body: JSON.stringify({ candidate_id: id, idempotency_key: evalState.recordKey, supplementary_inputs: f.inputs }),
      });
      B.toast(T("easy.eval.done"), "success");
      B.navigateTo("decision-detail", { id: Number(res.id) });
    } catch (err) {
      B.toast((err && err.message) || T("easy.error"), "error");
    } finally {
      evalState.busy = false;
    }
  }

  // ---- /평가하기 ----

  async function loadAnalysis() {
    const root = $("ez-root-easy-analysis");
    root.innerHTML = loadingHtml();
    const [cRes, dRes, aRes] = await Promise.allSettled([
      api("/product-candidates?limit=200"), api("/decisions/pending-review?limit=200"), api("/ai-learning/model-candidates"),
    ]);
    if (cRes.status === "rejected" && dRes.status === "rejected" && aRes.status === "rejected") { errorBox(root, cRes.reason); return; }
    const asList = (res) => (res.status === "fulfilled" ? (Array.isArray(res.value) ? res.value : (res.value && res.value.items) || []) : null);
    const cands = asList(cRes), decisions = asList(dRes), models = asList(aRes);
    const partial = cands === null || decisions === null || models === null;
    const byId = new Map((cands || []).map((c) => [c.id, c]));
    const nameOf = (id) => (byId.get(id) && byId.get(id).product_name) || T("easy.an.unknown_product", { id });
    const count = (list, pred) => (list ? list.filter(pred).length : null);
    const dash = (n) => (n === null ? "—" : n);

    const discovered = count(cands, (c) => c.status === "DISCOVERED"), analyzed = count(cands, (c) => c.status === "ANALYZED");
    const trendRows = (cands || []).filter((c) => c.trend_score !== null && c.trend_score !== undefined).sort((a, b) => b.trend_score - a.trend_score);
    const newRows = (cands || []).filter((c) => c.is_new_product === true);
    const tab = S.selected.analysisTab || "CAND";

    // 상태별 분포(가로 막대): 같은 색 번호의 상태는 한 덩어리로 합친다.
    const dist = {};
    (cands || []).forEach((c) => {
      const k = CAND_SEG[c.status] || 4;
      dist[k] = dist[k] || { n: 0, names: new Set() };
      dist[k].n += 1;
      dist[k].names.add(tryT(`candidates.status.${String(c.status).toLowerCase()}`, c.status));
    });
    const segs = Object.keys(dist).sort();
    const total = (cands || []).length;

    const perfBadge = perfState.data && perfState.data.ok && perfState.data.summary ? perfState.data.summary.totals.items : "—";
    const tabs = [["CAND", "easy.an.tab_cand", total], ["TREND", "easy.an.tab_trend", trendRows.length], ["NEW", "easy.an.tab_new", newRows.length],
      ["DEC", "easy.an.tab_dec", (decisions || []).length], ["CRIT", "easy.an.tab_crit", critState.data ? (critState.data.in_use ? "✓" : "!") : ""],
      ["EVAL", "easy.an.tab_eval", ""],
      ["AI", "easy.an.tab_ai", (models || []).length], ["PERF", "easy.an.tab_perf", perfBadge]];

    root.innerHTML = `
      <div class="ez-head"><div><h1>${esc(T("easy.nav.analysis"))}</h1><div class="ez-lead">${esc(T("easy.an.lead"))}</div></div></div>
      ${partial ? `<div class="ez-note warn"><span class="ez-dot warn" aria-hidden="true">!</span><div>${esc(T("easy.an.partial"))}</div></div>` : ""}
      <div class="ez-cards">
        <div class="ez-card accent"><div class="k">${esc(T("easy.an.k_cand"))}</div><div class="v">${dash(cands ? total : null)}</div><div class="s">${esc(T("easy.an.k_cand_s", { d: dash(discovered), a: dash(analyzed) }))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.an.k_new"))}</div><div class="v">${dash(cands ? newRows.length : null)}</div><div class="s">${esc(T("easy.an.k_new_s"))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.an.k_dec"))}</div><div class="v ${decisions && decisions.length ? "ez-c-orange" : ""}">${dash(decisions ? decisions.length : null)}</div><div class="s">${esc(T("easy.an.k_dec_s"))}</div></div>
        <div class="ez-card"><div class="k">${esc(T("easy.an.k_ai"))}</div><div class="v">${dash(models ? models.length : null)}</div><div class="s">${esc(T("easy.an.k_ai_s"))}</div></div>
      </div>
      ${total ? `<div class="ez-sec">${esc(T("easy.an.dist"))}<small>${esc(T("easy.an.dist_sub"))}</small></div>
        <div class="ez-stack" role="img" aria-label="${esc(T("easy.an.dist"))}">${segs.map((k) => `<i class="ez-seg-${k}" style="width:${(dist[k].n / total) * 100}%"></i>`).join("")}</div>
        <div class="ez-legend">${segs.map((k) => `<span><u class="ez-seg-${k}" aria-hidden="true"></u>${esc([...dist[k].names].join(" · "))}<b class="ez-num">${dist[k].n}</b></span>`).join("")}</div>` : ""}
      <div class="ez-tabs" role="tablist" style="margin-top:22px">${tabs.map(([id, key, n]) =>
        `<button type="button" class="ez-tab" role="tab" data-ez-atab="${id}" aria-selected="${tab === id}">${esc(T(key))} <span class="ez-num">${n}</span></button>`).join("")}</div>
      <div id="ez-an-list"></div>
      <div class="ez-sec">${esc(T("easy.an.more_title"))}<small>${esc(T("easy.an.more_sub"))}</small></div>
      <div class="ez-groups">${[["trend", "easy.an.tab_trend"], ["new-product", "easy.an.tab_new"], ["decision", "easy.an.tab_dec"], ["ai-learning", "easy.an.tab_ai"]].map(([v, k]) =>
        `<div class="ez-group"><h3>${esc(T(k))}</h3><button type="button" data-ez-go="${v}">${esc(T("easy.an.open_old"))} ›</button></div>`).join("")}</div>`;

    root.querySelectorAll("[data-ez-atab]").forEach((btn) => btn.addEventListener("click", () => { S.selected.analysisTab = btn.dataset.ezAtab; loadAnalysis(); }));
    root.querySelectorAll("[data-ez-go]").forEach((btn) => btn.addEventListener("click", () => B.navigateTo(btn.dataset.ezGo)));

    const host = $("ez-an-list");
    const candName = (c) => `<span class="ez-cell"><span class="ez-thumb">${icon("bag")}</span><span><b>${esc(c.product_name)}</b><small>${esc(c.market || "")}</small></span></span>`;
    const candChip = (c) => `<span class="ez-chip ${c.status === "APPROVED" || c.status === "ANALYZED" ? "ok" : (c.status === "REJECTED" ? "red" : "gray")}">${esc(tryT(`candidates.status.${String(c.status).toLowerCase()}`, c.status))}</span>`;
    const th = (...keys) => `<thead><tr>${keys.map((k) => `<th>${esc(T(k))}</th>`).join("")}</tr></thead>`;
    const candTable = (rows, headKeys, cells, emptyKeys) => {
      if (!rows.length) { host.innerHTML = emptyHtml(emptyKeys[0], emptyKeys[1]); return; }
      host.innerHTML = `<table class="ez-table">${th(...headKeys)}<tbody>${rows.map((r) => `<tr data-key="${r.id}" tabindex="0">${cells(r)}</tr>`).join("")}</tbody></table>`;
      bindRows(host, "tbody tr", (key) => B.navigateTo("candidate-detail", { id: Number(key) }));
    };

    if (tab === "CAND") {
      candTable((cands || []).slice().sort((a, b) => (b.trend_score || 0) - (a.trend_score || 0) || b.id - a.id),
        ["easy.col.product", "easy.an.col_trend", "easy.col.state"], (c) => `<td>${candName(c)}</td><td>${scoreCell(c.trend_score)}</td><td>${candChip(c)}</td>`, ["easy.an.empty_cand", "easy.an.empty_cand_s"]);
    } else if (tab === "TREND") {
      candTable(trendRows, ["easy.col.product", "easy.an.col_trend", "easy.an.col_conf", "easy.col.state"],
        (c) => `<td>${candName(c)}</td><td>${scoreCell(c.trend_score)}</td><td>${scoreCell(c.confidence)}</td><td>${candChip(c)}</td>`, ["easy.an.empty_trend", "easy.an.empty_trend_s"]);
    } else if (tab === "NEW") {
      candTable(newRows, ["easy.col.product", "easy.an.col_release", "easy.an.col_novel", "easy.col.state"],
        (c) => `<td>${candName(c)}</td><td class="ez-num">${esc(c.release_date || "—")}</td><td>${scoreCell(c.novelty_score)}</td><td>${candChip(c)}</td>`, ["easy.an.empty_new", "easy.an.empty_new_s"]);
    } else if (tab === "DEC") {
      const rows = decisions || [];
      if (!rows.length) { host.innerHTML = emptyHtml("easy.an.empty_dec", "easy.an.empty_dec_s"); return; }
      host.innerHTML = `<table class="ez-table">${th("easy.col.product", "easy.an.col_score", "easy.an.col_conf", "easy.an.col_rec", "easy.an.col_block")}<tbody>${rows.map((d) => `<tr data-key="${d.id}" tabindex="0">
        <td><span class="ez-cell"><span class="ez-thumb">${icon("bag")}</span><span><b>${esc(nameOf(d.candidate_id))}</b><small>#${d.candidate_id}</small></span></span></td>
        <td>${scoreCell(d.total_score)}</td><td>${scoreCell(d.confidence)}</td>
        <td><span class="ez-chip ${REC_CHIP[d.recommendation] || "gray"}">${esc(REC_KEY[d.recommendation] ? T(REC_KEY[d.recommendation]) : String(d.recommendation))}</span></td>
        <td>${d.blocked_by_safety ? `<span class="ez-chip red">${esc(T("easy.an.blocked"))}</span>` : '<span class="ez-faint">—</span>'}</td></tr>`).join("")}</tbody></table>`;
      bindRows(host, "tbody tr", (key) => B.navigateTo("decision-detail", { id: Number(key) }));
    } else if (tab === "PERF") {
      renderPerf(host);
    } else if (tab === "CRIT") {
      renderCriteria(host);
    } else if (tab === "EVAL") {
      renderEval(host, cands);
    } else {
      const rows = models || [];
      if (!rows.length) { host.innerHTML = emptyHtml("easy.an.empty_ai", "easy.an.empty_ai_s"); return; }
      host.innerHTML = `<div class="ez-note info" style="margin-top:12px"><span class="ez-dot info" aria-hidden="true">i</span><div>${esc(T("easy.an.ai_note"))}</div></div>
        <table class="ez-table">${th("easy.col.product", "easy.an.col_ver", "easy.an.col_sample", "easy.col.state")}<tbody>${rows.map((m) => `<tr data-key="${m.id}" tabindex="0">
        <td><b>${esc(m.name)}</b></td><td class="ez-num">${esc(m.version)}</td><td class="ez-num">${m.sample_size_used === null || m.sample_size_used === undefined ? "—" : esc(m.sample_size_used)}</td>
        <td><span class="ez-chip ${AI_CHIP[m.status] || "gray"}">${esc(tryT(`ail.status.${String(m.status).toLowerCase()}`, m.status))}</span></td></tr>`).join("")}</tbody></table>`;
      bindRows(host, "tbody tr", () => B.navigateTo("ai-learning"));
    }
  }

  // =========================================================
  // 설치
  // =========================================================

  B.registerView("easy-today", loadToday);
  B.registerView("easy-products", loadProducts);
  B.registerView("easy-product-detail", loadProductDetail);
  B.registerView("easy-orders", loadOrders);
  B.registerView("easy-money", loadMoney);
  B.registerView("easy-analysis", loadAnalysis);
  B.registerView("easy-approvals", loadApprovals);
  B.registerView("easy-more", loadMore);
  B.hooks.shellShown.push(onShellShown);
  B.hooks.navigated.push(onNavigated);

  window.HomezEasyView = Object.freeze({ isEnabled: () => S.enabled, setEnabled: (on) => setEasyEnabled(Boolean(on), true) });
})();
