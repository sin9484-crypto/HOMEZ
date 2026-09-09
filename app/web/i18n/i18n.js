/* =========================================================
   Homez OS — app/web/i18n/i18n.js

   Gate F-1(2026-08-06) 다국어(i18n) 핵심 모듈. console.js보다 먼저
   로드되어야 한다(console.js가 window.HomezI18n.t()를 호출한다).

   설계 원칙(docs/V6_EXECUTION_LEDGER.md "Gate F 계획" 참고):
   - 번역 문자열을 HTML/JS에 직접 하드코딩하지 않고 이 모듈을 통해서만
     가져온다. 키는 의미 기반(auth.login.title 형태)이다.
   - 기본(fallback) 언어는 항상 한국어(ko-KR)다 — 다른 locale에 키가
     없으면 ko-KR로 떨어지고, ko-KR에도 없으면 화면에 키 문자열이나
     "undefined"를 노출하지 않고 빈 문자열을 반환하며 console.error로
     크게 알린다(개발 중 누락을 즉시 알아챌 수 있게).
   - 로그인 전에는 localStorage에만 저장한다(비민감 로컬 설정). 로그인
     후 서버 사용자 환경설정에 저장하는 것은 User 모델에 별도 컬럼이
     필요한 후속 작업(Gate F-2+)이라 이번 턴에는 하지 않는다 — 지금은
     로그인 전후 동일하게 localStorage만 사용한다(요구사항의 "로그인
     후에는 사용자 환경설정에 저장" 중 서버 영속 부분은 미구현으로
     정직하게 남긴다).
   - 사용자 입력값·상품명·브랜드명·채널 응답 원문·서버 예외 메시지는
     이 모듈이 다루는 대상이 아니다(임의 번역 금지 — 그런 값은 항상
     서버가 보낸 그대로 표시한다).
   ========================================================= */

(() => {
  "use strict";

  const STORAGE_KEY = "homez_console_locale";
  const DEFAULT_LOCALE = "ko-KR";
  const SUPPORTED_LOCALES = ["ko-KR", "en-US"];

  const CATALOGS = {
    "ko-KR": window.HOMEZ_I18N_CATALOG_KO_KR || {},
    "en-US": window.HOMEZ_I18N_CATALOG_EN_US || {},
  };

  let currentLocale = DEFAULT_LOCALE;

  function detectInitialLocale() {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved && SUPPORTED_LOCALES.includes(saved)) {
        return saved;
      }
    } catch (_) {
      /* localStorage 접근 실패(사생활 보호 모드 등) — OS 언어로 계속 시도 */
    }

    const navLang = (navigator.language || "").toLowerCase();
    if (navLang.startsWith("en")) return "en-US";
    if (navLang.startsWith("ko")) return "ko-KR";

    return DEFAULT_LOCALE; // 지원하지 않는 언어는 한국어로 fallback
  }

  function t(key, params) {
    let value = CATALOGS[currentLocale] && CATALOGS[currentLocale][key];

    if (value === undefined) {
      value = CATALOGS[DEFAULT_LOCALE] && CATALOGS[DEFAULT_LOCALE][key];
    }

    if (value === undefined) {
      // 두 카탈로그 모두에 없는 진짜 번역 키 누락 — 화면에는 절대
      // 키 문자열이나 "undefined"를 보여주지 않는다(요구사항).
      // eslint-disable-next-line no-console
      console.error(`[HomezI18n] 번역 키 누락(양쪽 locale 모두 없음): ${key}`);
      return "";
    }

    if (!params) return value;

    return Object.keys(params).reduce(
      (acc, paramKey) => acc.replace(`{${paramKey}}`, String(params[paramKey])),
      value,
    );
  }

  function applyToDom(root) {
    const scope = root || document;

    scope.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.getAttribute("data-i18n"));
    });

    // "attrName:key,attrName2:key2" 형태 — aria-label/placeholder/title 등
    // 여러 속성을 한 요소에서 동시에 번역할 때 사용한다.
    scope.querySelectorAll("[data-i18n-attr]").forEach((el) => {
      const spec = el.getAttribute("data-i18n-attr") || "";
      spec.split(",").forEach((pair) => {
        const [attrName, key] = pair.split(":").map((s) => s.trim());
        if (attrName && key) {
          el.setAttribute(attrName, t(key));
        }
      });
    });

    document.documentElement.lang = currentLocale === "en-US" ? "en" : "ko";
  }

  function getLocale() {
    return currentLocale;
  }

  function setLocale(locale) {
    if (!SUPPORTED_LOCALES.includes(locale)) return;

    currentLocale = locale;

    try {
      window.localStorage.setItem(STORAGE_KEY, locale);
    } catch (_) {
      /* 저장 실패해도 이번 세션 안에서는 언어 전환 자체는 계속 동작한다 */
    }

    applyToDom(document);
    document.dispatchEvent(new CustomEvent("homez:locale-changed", { detail: { locale } }));
  }

  // --------------------------------------------------
  // Locale formatter — Intl API 재사용(외부 라이브러리 없음)
  // --------------------------------------------------

  // Gate M-1(2026-08-21) — 날짜·시간 계약 보완. 백엔드(app/core/
  // audit_db.py 등 일부 예외를 빼면 대부분)는 naive UTC(타임존 표기
  // 없는) ISO 문자열을 돌려준다(예: "2026-08-21T13:20:00.554597") —
  // 그대로 `new Date(...)`에 넘기면 브라우저가 "로컬 시각"으로
  // 잘못 해석해(KST 환경에서는 9시간 어긋남) 이 값을 소비하는 모든
  // 절대시각 비교·카운트다운·표시가 깨진다. 이 문제는 이미 두 곳에서
  // 각자 따로 발견·수정된 적이 있다(Gate H의 lsParseUtc(), 그리고
  // sourcing 발주 화면의 spsParseUtcDateMs()) — 이번에 그 둘을
  // 포함해 앱 전체가 공유하는 단일 정규화 지점으로 통합한다.
  //
  // 규칙:
  //   - Date 인스턴스는 그대로 통과시킨다.
  //   - 이미 타임존 표기(Z 또는 ±HH:MM/±HHMM)가 있는 문자열은 건드리지
  //     않는다(중복으로 Z를 붙이지 않는다 — 예: audit_logs의 신규
  //     created_at은 이미 "+00:00"으로 끝난다).
  //   - 날짜만 있는 문자열("YYYY-MM-DD", 시각 없음 — 예: 공급가
  //     유효기한 입력값)은 건드리지 않는다. ISO 8601 스펙상 이미
  //     UTC 자정으로 해석되고, "시각"이 없는 값이라 로컬/UTC 혼동
  //     자체가 성립하지 않는다(작업지시 3 — 날짜만 표시하는 값과
  //     절대시각 값을 구분한다).
  //   - 그 외(시각까지 있는데 타임존 표기가 없는 문자열)만 "Z"를
  //     붙여 명시적으로 UTC로 해석시킨다 — 실제 수정 대상.
  function parseUtcDate(dateInput) {
    if (dateInput instanceof Date) return dateInput;
    if (dateInput === null || dateInput === undefined || dateInput === "") return null;
    if (typeof dateInput !== "string") {
      const d = new Date(dateInput);
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(dateInput);
    const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(dateInput);
    const normalized = (hasTimezone || isDateOnly) ? dateInput : `${dateInput}Z`;
    const d = new Date(normalized);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatDate(date, options) {
    const d = parseUtcDate(date);
    if (d === null) return "";
    return new Intl.DateTimeFormat(currentLocale, options || {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    }).format(d);
  }

  function formatNumber(n, options) {
    return new Intl.NumberFormat(currentLocale, options).format(n);
  }

  function formatCurrency(amount, currencyCode) {
    // 통화는 판매 데이터의 실제 통화를 유지한다(요구사항) — locale이
    // 바뀌어도 currencyCode는 호출자가 명시한 값을 그대로 쓴다(기본
    // KRW). 표기 스타일(천단위 구분자·기호 위치 등)만 locale을 따른다.
    const code = currencyCode || "KRW";
    return new Intl.NumberFormat(currentLocale, {
      style: "currency", currency: code,
      currencyDisplay: "symbol",
    }).format(amount);
  }

  function formatPercent(ratio, fractionDigits) {
    return new Intl.NumberFormat(currentLocale, {
      style: "percent",
      minimumFractionDigits: fractionDigits || 0,
      maximumFractionDigits: fractionDigits || 0,
    }).format(ratio);
  }

  currentLocale = detectInitialLocale();

  window.HomezI18n = {
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    t,
    getLocale,
    setLocale,
    applyToDom,
    formatDate,
    parseUtcDate,
    formatNumber,
    formatCurrency,
    formatPercent,
  };
})();
