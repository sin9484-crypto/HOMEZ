"""
=========================================================
Homez OS

File : tests/test_route_authentication_contract.py

Gate X-2A(2026-08-12) — 실제 마운트된 모든 route에 대한 인증 계약을
고정한다. 이 저장소는 `httpx`가 설치돼 있지 않아(기존 관례,
`tests/test_company_router_security.py` 주석 참고) `TestClient`로
실제 HTTP 왕복을 재현할 수 없다 — 대신 FastAPI의 의존성 해석 결과
(`route.dependant`)를 직접 읽어, allowlist에 없는 모든 route가
실제로 인증 Depends 체인을 갖고 있는지 정적으로 검증한다. 이 방식은
경로 파라미터·요청 바디 구성 실패로 인한 오탐(false negative) 위험이
없다는 장점도 있다.

이 사고를 촉발한 사고: `/role-permissions`(임의 역할 Permission 일괄
교체)와 `/users`(회사 격리·권한 상승 방지 전무)가 어떤 인증도 없이
마운트돼 있었고, 이어서 brand/category/supplier/marketplace/product/
order/purchase/shipment 8개 레거시 라우터도 동일한 결함으로
발견·제거됐다(marketplace는 평문 Credential 저장 가능성까지
플래그된 도메인이라 위험이 더 컸다). 자세한 경위는
docs/V6_EXECUTION_LEDGER.md의 "Gate X" 절 참고.

실제 homez.db는 사용하지 않는다(app.main import만, 쓰기 없음).
=========================================================
"""

import unittest

# 명시적으로 공개인 경로만 allowlist에 둔다 — 그 외 전부 인증
# Depends가 있어야 한다. 새 공개 API를 추가하려면 이 목록에
# "왜 공개여야 하는가"를 근거로 명시적으로 추가해야 한다(조용히
# 통과시키지 않는다).
PUBLIC_ALLOWLIST = {
    # 앱 자체 식별 — 민감정보 없음
    "/",
    "/health",
    # 로그인 자체(로그인하지 않은 사용자가 호출해야 하므로 공개는
    # 설계상 필수 — 비밀번호는 본문에만 있고 5회 실패 시 잠금 등
    # 자체 방어가 service 계층에 있음, tests/test_homez_auth_login.py)
    "/auth/login",
    "/auth/refresh",
    # 로그인 전 화면 자체(정적 HTML/CSS/JS/i18n 파일 서빙 — 데이터
    # 없음, 로그인 폼 자체가 로그인 전에 로드돼야 하므로 공개 필수)
    "/console",
    "/console/static/assets/{filename}",
    "/console/static/console.css",
    "/console/static/console.js",
    "/console/static/i18n/{filename}",
    # Section 5(2026-08-28) 이미지 수동 편집기 MVP — Fabric.js(vendor된
    # 정적 JS 라이브러리, app/web/vendor/fabric.min.js)도 console.js와
    # 동일한 이유로 공개: <script src="...">로 로드되므로 Authorization
    # 헤더를 붙일 수 없고, 사용자 데이터를 전혀 담지 않는 라이브러리
    # 코드 자체일 뿐이다.
    "/console/static/vendor/{filename}",
    "/assets/guides/marketplace/{filename}",
    # 최초 설치·복구 부트스트랩 상태 조회 — 로그인 화면 자체를
    # 그리기 전에 "최초 설정 필요/제한 모드/복구 필요" 여부를 알아야
    # 하므로 로그인 여부와 무관하게 호출 가능해야 한다(기존 설계,
    # app/core/desktop_setup.py 등 주석에 명시). 값 자체는 boolean/
    # 상태 코드 수준이며 사용자 데이터를 반환하지 않는다.
    "/desktop-setup/status",
    "/desktop-setup/migration-status",
    "/desktop-setup/company-recovery/status",
    "/desktop-auth/bootstrap",
    # 로그아웃 상태의 "아이디/비밀번호 찾기" 진입 화면이 이메일
    # 복구 기능이 구성됐는지(현재는 항상 미구성) 미리 알아야
    # 하므로 공개. 특정 이메일의 존재 여부는 반환하지 않는다(설계
    # 검토는 Gate X-1 인벤토리 참고).
    "/account-recovery/reset-password/email/status",
    # FastAPI 자체 문서 — API 스키마 노출(Low 위험, loopback 전용
    # Desktop 앱이라 외부 네트워크에서는 애초에 도달 불가). 데이터
    # 접근이 아니라 스키마 열람이므로 이번 Gate에서는 허용 목록에
    # 유지하고 Low 잔존 위험으로만 기록한다.
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
}

KNOWN_AUTH_DEPENDENCY_NAMES = {
    "admin_guard", "AdminGuard", "ManagerGuard", "StaffGuard",
    "SellerGuard", "CustomerGuard", "SuperAdminGuard",
    "AdminOrManagerGuard", "RoleGuard",
    "get_current_user", "get_current_active_user", "get_current_superuser",
    "require_authenticated", "require_superuser",
    "require_desktop_mode_and_token", "require_desktop_token",
    "get_request_user",
    "ListingWizardPermissionGuard", "listing_wizard_permission_guard",
    "consume_recent_auth_token", "verify_approval_nonce",
    # 2026-08-22 14차 지시(Gate RP-2) — retail_purchase Provider
    # Webhook 수신 엔드포인트는 로그인한 HOMEZ 사용자가 아니라 외부
    # Provider 서버가 호출하므로 세션 인증 Guard를 쓸 수 없다. 대신
    # HMAC 서명 검증을 인증 수단으로 쓰는 FastAPI Depends다(app/
    # domains/retail_purchase/router.py::verify_webhook_request_
    # signature — secret이 연결되지 않은 동안은 항상 거부, fail-closed).
    "verify_webhook_request_signature",
}


def _collect_dependency_names(dependant, seen=None):

    if seen is None:
        seen = set()
    names = set()
    call = getattr(dependant, "call", None)
    if call is not None:
        names.add(getattr(call, "__name__", str(call)))
    for sub in getattr(dependant, "dependencies", []):
        key = id(sub)
        if key in seen:
            continue
        seen.add(key)
        names |= _collect_dependency_names(sub, seen)
    return names


class RouteAuthenticationContractTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        import app.main

        cls.routes = [
            r for r in app.main.app.routes if hasattr(r, "path")
        ]

    def test_every_non_allowlisted_route_has_a_recognized_auth_dependency(self):

        unauthenticated_and_not_allowlisted = []

        for route in self.routes:
            path = route.path
            if path in PUBLIC_ALLOWLIST:
                continue

            dependant = getattr(route, "dependant", None)
            dep_names = (
                _collect_dependency_names(dependant) if dependant else set()
            )

            if not (dep_names & KNOWN_AUTH_DEPENDENCY_NAMES):
                methods = sorted(getattr(route, "methods", []) or [])
                unauthenticated_and_not_allowlisted.append(
                    f"{','.join(methods)} {path}",
                )

        self.assertEqual(
            unauthenticated_and_not_allowlisted, [],
            "allowlist에 없는데 인증 Depends도 없는 route가 발견됐다 "
            "— 무인증 API 노출 결함(Gate X-2A와 동일한 클래스). 새 "
            "route라면 인증 Depends를 추가하거나, 정말로 공개여야 "
            "한다면 이 파일의 PUBLIC_ALLOWLIST에 근거와 함께 명시적으로 "
            "추가해야 한다: "
            + "; ".join(unauthenticated_and_not_allowlisted),
        )

    def test_previously_removed_legacy_crud_routers_stay_unmounted(self):
        """
        2026-08-12 사고의 재발을 구체적으로 고정한다 — 이 8개 접두사에
        원래 마운트돼 있던 "무인증" 레거시 CRUD 라우터가 다시 나타나면
        즉시 실패해야 한다.

        2026-08-15 V7 Gate 4 — CTO 지시로 `/orders`/`/purchases`/
        `/shipments` 3개는 이 목록에서 제외했다. 2026-08-12에 제거된
        대상은 "인증 자체가 전혀 없던" pre-pivot 레거시 라우터였다
        (products/suppliers FK를 직접 참조하는 구식 CRUD, admin_guard
        등 어떤 인증 Depends도 없었음). Gate 4가 이 3개 도메인을
        company_id 스코프 + admin_guard 전체 적용으로 완전히 새로
        설계·재구현해 다시 마운트했다 — 이는 "같은 접두사가 우연히
        다시 나타난 사고"가 아니라 CTO가 명시적으로 지시한 재설계
        결과다. 이 새 라우터들도 여전히 인증 없이는 절대 통과할 수
        없다는 사실은 바로 위
        `test_every_non_allowlisted_route_has_a_recognized_auth_
        dependency`가 이 8개 접두사를 포함한 전체 마운트 route에 대해
        빠짐없이 강제한다(allowlist에 없으면 인증 Depends 필수) — 그
        테스트가 이 3개 접두사의 실제 보안 요구사항을 여전히 담당한다.
        나머지 5개(brands/categories/suppliers/marketplaces/products)는
        여전히 완전히 빈 스캐폴딩이거나 미마운트 상태이므로 그대로
        둔다.
        """

        removed_prefixes = (
            "/brands", "/categories", "/suppliers", "/marketplaces",
            "/products",
        )
        removed_but_present = [
            route.path for route in self.routes
            if route.path.startswith(removed_prefixes)
        ]
        self.assertEqual(
            removed_but_present, [],
            "제거됐던 무인증 레거시 라우터가 다시 마운트됐다: "
            + ", ".join(removed_but_present),
        )

    def test_allowlist_entries_all_actually_exist_as_mounted_routes(self):
        """
        allowlist에 더 이상 존재하지 않는 죽은 경로가 쌓이지 않게
        한다(허용 목록 자체의 위생 검증).
        """

        mounted_paths = {route.path for route in self.routes}
        stale_allowlist_entries = sorted(PUBLIC_ALLOWLIST - mounted_paths)

        self.assertEqual(
            stale_allowlist_entries, [],
            "allowlist에 있지만 더 이상 마운트되지 않은 경로: "
            + ", ".join(stale_allowlist_entries),
        )


if __name__ == "__main__":
    unittest.main()
