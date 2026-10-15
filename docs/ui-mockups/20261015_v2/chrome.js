/* 시안 공통: 사이드바·상단바를 붙이고, 상품 사진 자리를 그림으로 채운다. 시안 전용. */
(function () {
  const ICON = {
    today: '<path d="M8 4h8M9 2h6v4H9zM6 4H5a1 1 0 00-1 1v15a1 1 0 001 1h14a1 1 0 001-1V5a1 1 0 00-1-1h-1M8 12h8M8 16h6"/>',
    box: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    truck: '<path d="M3 7h11v9H3zM14 10h4l3 3v3h-7M7 19a1.6 1.6 0 100-3 1.6 1.6 0 000 3zM17 19a1.6 1.6 0 100-3 1.6 1.6 0 000 3z"/>',
    money: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M6 9v.01M18 15v.01"/>',
    shield: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3zM9 12l2 2 4-4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 00-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 00-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 00-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 005 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 002 1.2l.4 2.6h4l.4-2.6a7 7 0 002-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 114 2c-.9.6-1.5 1-1.5 2M12 17v.01"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    bell: '<path d="M6 16V11a6 6 0 1112 0v5l2 2H4zM10 20a2 2 0 004 0"/>',
    down: '<path d="M6 9l6 6 6-6"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
  };
  const svg = (k, extra) => `<svg viewBox="0 0 24 24" ${extra || ""}>${ICON[k]}</svg>`;
  window.HZ_ICON = svg;

  const hasChrome = !!document.querySelector(".page");
  const active = document.body.dataset.active || "today";
  const badge = document.body.dataset.badge || "2";
  const nav = (key, icon, label, extra) => `<div class="nav ${active === key ? "on" : ""}">${svg(icon)}${label}${extra || ""}</div>`;
  if (hasChrome) {
  const side = document.createElement("aside");
  side.className = "side";
  side.innerHTML = `<div class="logo">HOMEZ</div>
    ${nav("today", "today", "오늘 할 일")}
    ${nav("products", "box", "상품")}
    ${nav("orders", "truck", "주문·배송")}
    ${nav("money", "money", "정산·수익")}
    <hr>
    ${nav("approvals", "shield", "승인 센터", badge !== "0" ? `<span class="badge">${badge}</span>` : "")}
    <div class="sp"></div>
    <div class="nav sub">${svg("gear")}더보기 · 설정</div>
    <div class="nav sub">${svg("help")}도움말</div>`;

  const top = document.createElement("header");
  top.className = "top";
  top.innerHTML = `<div class="search">${svg("search")}상품명, 주문번호, 고객명으로 검색하세요.</div>
    <div class="sel">전체 채널 ${svg("down")}</div>
    <span class="mode"><i></i>운영자 승인 모드</span>
    <div class="bell">${svg("bell")}<i></i></div>
    <div class="stop">긴급 중지</div>
    <div class="note">기능별 UI 시안 · 예시 데이터<br>2026. 10. 15 (목) 18:30 기준</div>`;

  const page = document.querySelector(".page");
  const panel = document.querySelector(".panel");
  const main = document.createElement("div");
  main.className = "main";
  const wrap = document.createElement("div");
  wrap.className = "wrap";
  if (!panel) wrap.style.gridTemplateColumns = "1fr";
  page.replaceWith(main);
  main.appendChild(top);
  main.appendChild(wrap);
  wrap.appendChild(page);
  if (panel) wrap.appendChild(panel);
  document.body.prepend(side);

  }

  // 상품 사진 자리 — 종류별 그림(실제 상품 사진이 아니다)
  const ART = {
    water: `<svg viewBox="0 0 64 64"><rect width="64" height="64" fill="#EAF3FB"/><g><rect x="34" y="14" width="8" height="5" rx="1.5" fill="#2B6CB0"/><path d="M33 19h10v5l4 6v22a4 4 0 01-4 4H33a4 4 0 01-4-4V30l4-6z" fill="#fff" stroke="#9CC3E6" stroke-width="1.5"/><rect x="29" y="34" width="18" height="12" fill="#2F80ED"/><rect x="12" y="20" width="8" height="5" rx="1.5" fill="#2B6CB0"/><path d="M11 25h10v5l4 6v18a4 4 0 01-4 4H11a4 4 0 01-4-4V36l4-6z" fill="#fff" stroke="#9CC3E6" stroke-width="1.5"/><rect x="7" y="40" width="18" height="10" fill="#5AA0F2"/></g></svg>`,
    panty: `<svg viewBox="0 0 64 64"><rect width="64" height="64" fill="#FBEFF3"/><path d="M12 22h40l-1 6c-1 10-8 18-19 18S13 38 13 28z" fill="#F2C2D1" stroke="#E08AA6" stroke-width="1.5"/><rect x="11" y="19" width="42" height="5" rx="2.5" fill="#E8A3BA"/><path d="M26 46l6 8 6-8" fill="#F8DCE5" stroke="#E08AA6" stroke-width="1.5"/></svg>`,
    stack: `<svg viewBox="0 0 64 64"><rect width="64" height="64" fill="#EEF4EF"/><rect x="10" y="12" width="26" height="26" rx="4" fill="#fff" stroke="#B9D4C0" stroke-width="1.5"/><rect x="24" y="24" width="26" height="26" rx="4" fill="#D8EBDD" stroke="#9CC4A8" stroke-width="1.5"/><path d="M31 37l5 5 8-9" stroke="#138049" stroke-width="3" fill="none"/></svg>`,
    eye: `<svg viewBox="0 0 64 64"><rect width="64" height="64" fill="#F2F0FB"/><circle cx="32" cy="32" r="16" fill="#fff" stroke="#B8AEE6" stroke-width="1.5"/><path d="M32 22v11l7 4" stroke="#6C5CE7" stroke-width="3" fill="none" stroke-linecap="round"/></svg>`,
    check: `<svg viewBox="0 0 64 64"><rect width="64" height="64" fill="#E9F6EE"/><circle cx="32" cy="32" r="17" fill="#fff" stroke="#9CC4A8" stroke-width="1.5"/><path d="M23 33l7 7 12-14" stroke="#138049" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  };
  document.querySelectorAll("[data-art]").forEach((el) => { el.innerHTML = ART[el.dataset.art] || ""; });
  document.querySelectorAll("[data-ic]").forEach((el) => { el.innerHTML = svg(el.dataset.ic); });
  const foot = document.createElement("div");
  foot.className = "foot";
  foot.textContent = "예시 화면 · 사진은 그림이며 실제 화면과 다를 수 있어요";
  document.body.appendChild(foot);
})();
