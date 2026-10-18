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

  async function loadToday() {
    const root = $("ez-root-easy-today");
    root.innerHTML = loadingHtml();
    let wq, today, safety, ext, collect;
    try {
      [wq, today, safety, ext, collect] = await Promise.all([
        B.wq.fetchItems(),
        api("/easy-view/today").catch(() => null),
        api("/console/api/safety-status").catch(() => null),
        api("/external-calls/summary").catch(() => null),
        api("/orders/collection-ops/status").catch(() => null),
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
    $("ez-ap-stop").addEventListener("click", () => toggleEstop(estop));
    if (opts && opts.productId) { const anchor = $("ez-days-anchor"); if (anchor) anchor.scrollIntoView({ block: "start" }); }
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
    const token = await B.promptRecentAuthToken();
    if (token === null) return;
    const payload = standing ? { per_hour_limit: Number(result.get("per_hour")), hours: Number(result.get("hours")) } : { count: Number(result.get("count")) };
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

  async function grantDialog(preset) {
    const purposes = [["ORDER_DETECTION", "easy.purpose.order_detection"], ["ORDER_TRACKING", "easy.purpose.tracking"], ["AFTER_SALES_CHECK", "easy.purpose.after"], ["PRODUCT_STATUS_CHECK", "easy.purpose.pstatus"], ["INVOICE_UPLOAD", "easy.purpose.invoice"], ["PRODUCT_OUTBOUND_DAYS", "easy.purpose.days"], ["ANY", "easy.purpose.any"]];
    const body = `<label>${esc(T("easy.ap.d_purpose"))}</label><select name="purpose">${purposes.map(([v, k]) => `<option value="${v}" ${preset && preset.purpose === v ? "selected" : ""}>${esc(T(k))}</option>`).join("")}</select>
      <label>${esc(T("easy.ap.d_kind"))}</label><select name="kind"><option value="READ" ${!preset || preset.kind === "READ" ? "selected" : ""}>${esc(T("easy.kind.read"))}</option><option value="WRITE" ${preset && preset.kind === "WRITE" ? "selected" : ""}>${esc(T("easy.kind.write"))}</option></select>
      <label>${esc(T("easy.ap.d_system"))}</label><select name="system"><option value="COUPANG">${esc(T("easy.sys.coupang"))}</option><option value="ONCH">${esc(T("easy.sys.onch"))}</option></select>
      <label>${esc(T("easy.ap.d_count"))} <span class="ez-faint" style="font-weight:400">${esc(T("easy.ap.d_count_hint"))}</span></label><input name="count" type="number" min="1" max="50" value="${preset && preset.mode === "STANDING" ? "" : 1}">
      <label>${esc(T("easy.ap.d_per_hour"))} <span class="ez-faint" style="font-weight:400">${esc(T("easy.ap.d_standing_hint"))}</span></label><input name="per_hour" type="number" min="1" max="120" value="${preset && preset.per_hour_limit ? preset.per_hour_limit : ""}">
      <label>${esc(T("easy.ap.d_hours"))}</label><input name="hours" type="number" min="1" max="720" value="${preset && preset.hours ? preset.hours : ""}">`;
    const result = await ezDialog({ title: T("easy.ap.grant_title"), bodyHtml: body, okLabel: T("easy.ap.allow") });
    if (!result) return;
    const count = Number(result.get("count")) || null;
    const perHour = Number(result.get("per_hour")) || null;
    const hours = Number(result.get("hours")) || null;
    const standing = Boolean(perHour);
    const token = await B.promptRecentAuthToken();
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

  // 0~1 점수를 막대와 숫자로(높을수록 초록, 낮을수록 주황·빨강). 점수가 없으면 "점수 없음".
  function scoreCell(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return `<span class="ez-faint">${esc(T("easy.an.no_score"))}</span>`;
    const v = Math.max(0, Math.min(1, Number(value)));
    const tone = v >= 0.66 ? "" : (v >= 0.33 ? "mid" : "neg");
    return `<div class="ez-score"><div class="ez-num">${v.toFixed(2)}</div><div class="ez-bar ${tone}"><i style="width:${Math.round(v * 100)}%"></i></div></div>`;
  }

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

    const tabs = [["CAND", "easy.an.tab_cand", total], ["TREND", "easy.an.tab_trend", trendRows.length], ["NEW", "easy.an.tab_new", newRows.length],
      ["DEC", "easy.an.tab_dec", (decisions || []).length], ["AI", "easy.an.tab_ai", (models || []).length]];

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
