"""
=========================================================
Homez OS

File : main.py
Version : 2.2.0

Application Entry Point
=========================================================
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.migration_restricted_mode import get_restricted_mode_state
from app.core.migration_restricted_mode import is_restricted_mode
from app.core.migration_restricted_mode import refresh_restricted_mode_state
from app.core.startup import startup

logger = logging.getLogger("homez.main")

from app.core.account_admin import router as account_admin_router
from app.core.company_recovery_setup import router as company_recovery_setup_router
from app.core.desktop_auth import router as desktop_auth_router
from app.core.desktop_setup import router as desktop_setup_router
from app.core.migration_approval import router as migration_approval_router
from app.domains.account_recovery.router import router as account_recovery_router
from app.domains.account_registration.router import router as account_registration_router
from app.domains.ai_learning.router import router as ai_learning_router
from app.domains.backup.router import router as backup_router
from app.domains.currency.router import router as currency_router
from app.domains.payment.router import router as payment_router
from app.domains.refund.router import router as refund_router
from app.domains.price_stock_safety.router import router as price_stock_safety_router
from app.domains.product_attribute_match.router import (
    router as product_attribute_match_router,
)
from app.domains.recall_notice.router import router as recall_notice_router
from app.domains.supplier_capability.router import router as supplier_capability_router
from app.domains.diagnostics.router import router as diagnostics_router
from app.domains.guides.router import router as guides_router
from app.domains.notification_center.router import (
    router as notification_center_router,
)
from app.domains.restore.router import router as restore_router
from app.domains.update.router import router as update_router
from app.domains.role_permission.admin_router import router as role_permission_admin_router
from app.domains.auth import router as auth_router
from app.domains.brand import router as brand_router
from app.domains.category import router as category_router
from app.domains.company.router import router as company_router
from app.domains.coupang.router import policy_router as coupang_policy_router
from app.domains.coupang.router import router as coupang_router
from app.domains.decision.router import (
    company_policy_router as decision_company_policy_router,
)
from app.domains.decision.router import policy_router as decision_policy_router
from app.domains.decision.router import router as decision_router
from app.domains.funding.router import router as funding_router
from app.domains.inventory.router import router as inventory_router
from app.domains.marketplace.router import router as marketplace_router
from app.domains.listing_package.router import (
    router as listing_package_router,
)
from app.domains.media_asset.router import (
    router as media_asset_router,
)
from app.domains.marketplace_listing.router import (
    router as marketplace_listing_router,
)
from app.domains.marketplace_listing.listing_wizard_router import (
    router as listing_wizard_router,
)
from app.domains.marketplace_listing.candidate_pipeline_router import (
    router as candidate_pipeline_router,
)
from app.domains.user_settings.router import (
    router as user_settings_router,
)
from app.domains.order.router import router as order_router
from app.domains.pricing.router import router as pricing_router
from app.domains.product import router as product_router
from app.domains.product_candidate.router import (
    router as product_candidate_router,
)
from app.domains.purchase.router import router as purchase_router
from app.domains.orchestration.router import router as orchestration_router
from app.domains.source.router import router as source_router
from app.domains.channel_policy.router import router as channel_policy_router
from app.domains.ai_governance.router import router as ai_governance_router
from app.domains.product_selection.router import router as product_selection_router
from app.domains.role_permission.router import (
    router as role_permission_router,
)
from app.domains.retail_purchase.router import (
    router as retail_purchase_router,
)
from app.domains.purchase_task.router import (
    router as purchase_task_router,
)
from app.domains.return_order.router import (
    router as return_order_router,
)
from app.domains.settlement.router import (
    router as settlement_router,
)
from app.domains.shipment.router import router as shipment_router
from app.domains.store_connection.router import (
    router as store_connection_router,
)
from app.domains.supplier import router as supplier_router
from app.domains.user import router as user_router
from app.web.router import router as web_console_router


# --------------------------------------------------
# Lifespan
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Homez Startup

    실행 순서

    Database
        ↓
    Seed
        ↓
    Permission
        ↓
    Ready
    """

    startup()

    # Gate G(2026-08-07): 서버가 실제로 요청을 받기 전에 Migration
    # 제한 모드 상태를 1회 계산해 캐시한다 — 재시작마다 다시 계산
    # 되므로, 재시작 사이에 관리자가 직접 파일을 적용/되돌린 경우도
    # 반영된다. 진단 자체가 실패해도(파일 손상 의심 등) 예외를 밖으로
    # 던지지 않는다 — fail-closed로 제한 모드를 켠 채 서버는 계속
    # 뜨게 한다(health/최소 인증까지 막히면 아무도 문제를 조사할 수
    # 없다).
    refresh_restricted_mode_state()

    # 2026-09-10 Phase 6(HOMEZ_USER_OPERATION_SETTINGS.md 11번) —
    # 주간 백업 복구 리허설 Job 등록. 실패해도(예: 예상 밖 경로
    # 오류) 서버 기동 자체를 막지 않는다 — refresh_restricted_mode_
    # state()와 동일한 fail-open 원칙(백업 리허설 하나가 안 되는
    # 것보다 서버 전체가 안 뜨는 게 더 심각한 장애다).
    if settings.SCHEDULER_ENABLED:
        try:
            from app.core.scheduler_service import SchedulerService
            from app.domains.scheduler.jobs import register_all_jobs

            SchedulerService.start()
            register_all_jobs()
        except Exception:  # noqa: BLE001
            logger.exception("스케줄러 Job 등록 실패 — 서버는 계속 기동합니다.")

    yield

    from app.core.scheduler_service import SchedulerService
    SchedulerService.shutdown()
    # redis close
    # etc.


# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# --------------------------------------------------
# CORS
#
# 2026-08-04 V6 Gate 1D 보완: 이전에는 `allow_origins=["*"]`(cross-
# Origin Resource Sharing 허용 대상 없음 → getattr 기본값 그대로)와
# `allow_credentials=True`를 함께 켜둔 상태였다 — 스펙상 이례적인
# 조합이고, HOMEZ Desktop은 애초에 외부 Origin과 통신할 이유가 없다
# (loopback 단일 프로세스, 매 실행마다 동적 포트). 와일드카드를 완전히
# 제거하고 loopback(127.0.0.1/localhost, 임의 포트)만 정규식으로
# 허용한다 — 이 이상의 외부 Origin·`null` Origin은 전부 매치되지
# 않아 거부된다. DNS 리바인딩 등 Host 헤더 위조 자체는 이 미들웨어의
# 책임이 아니라 `app/core/desktop_setup.py::require_matching_origin`
# (Host 헤더 자체를 검사)이 별도로 이미 방어한다 — 이 CORS 설정은
# 브라우저 JS의 cross-Origin 응답 접근만 제한하는 추가 방어선이다.
# --------------------------------------------------

_LOOPBACK_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=_LOOPBACK_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 인증 실패 원인을 구분하는 X-Auth-Error-Code(app/core/auth.py 등)와
    # Migration 제한 모드 차단 원인을 구분하는
    # X-Migration-Restricted-Code(아래 미들웨어)를 브라우저 JS가 필요한
    # 경우에만 읽을 수 있도록 이것만 노출한다.
    expose_headers=["X-Auth-Error-Code", "X-Migration-Restricted-Code"],
)

# --------------------------------------------------
# Cache-Control (민감 화면/응답 캐시 방지)
#
# 2026-07-30: 로그아웃 후 브라우저/webview의 뒤로가기·캐시가 이전에
# 렌더링된 보호 화면(Console 등)을 그대로 다시 보여줄 수 있다는 지적에
# 대응한다. 이 앱은 내부 운영자 도구이고 정적 자산(CSS/JS/이미지)을
# 제외한 모든 응답이 개인정보·운영 데이터를 담을 수 있으므로, 개별
# 엔드포인트마다 헤더를 다는 대신 전역으로 no-store를 적용한다(정적
# 자산만 캐시를 허용해 반복 로드 성능을 지킨다).
# --------------------------------------------------

_CACHEABLE_SUFFIXES = (".css", ".js", ".png", ".ico", ".jpg", ".jpeg", ".svg")


@app.middleware("http")
async def _no_store_for_sensitive_responses(request, call_next):

    response = await call_next(request)

    if not request.url.path.endswith(_CACHEABLE_SUFFIXES):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    return response


# --------------------------------------------------
# Migration 제한 모드 서버 강제 (Gate G, 2026-08-07)
#
# 배경: 미적용 Migration이 있을 때의 "제한 모드"는 그동안
# app/web/console.js(homezLimitedMode)에서만 강제됐다 — 콘솔 클라이언트를
# 거치지 않는 요청(curl, 변조된 JS 등)은 서버가 실제로 아무것도 막지
# 않았으므로 그대로 쓰기에 성공할 수 있었다(app/web/console.js:66-70에
# 이미 이 한계가 명시돼 있었다). 이 미들웨어가 그 서버측 강제다.
#
# GET/HEAD/OPTIONS(읽기 전용 진단 포함)는 제한 모드에서도 항상
# 허용한다 — 데이터를 바꾸지 않기 때문이다. 그 외 메서드는 아래
# 화이트리스트에 정확히 일치하는 경로만 통과한다:
#   - /health                              : 헬스체크
#   - /auth/login, /auth/logout, /auth/refresh, /auth/recent-auth
#                                           : 최소 인증(로그인 유지 +
#                                             승인에 필요한 recent-auth
#                                             토큰 발급까지 포함해야,
#                                             제한 모드 중에도 SUPER_ADMIN이
#                                             실제로 승인 절차를 밟을 수
#                                             있다)
#   - /desktop-auth/bootstrap              : Desktop 세션 토큰 부트스트랩
#   - /desktop-setup/migration-status/approve
#                                           : Migration 승인·적용 그 자체
#                                             (내부적으로 SUPER_ADMIN·
#                                             recent-auth·단발성 nonce·
#                                             Desktop 토큰·loopback·Origin
#                                             일치를 전부 별도로 요구한다
#                                             — app/core/migration_approval.py)
# 화이트리스트에 없는 나머지 모든 쓰기는 423 Locked로 차단한다.
# --------------------------------------------------

_MIGRATION_RESTRICTED_MODE_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_MIGRATION_RESTRICTED_MODE_WRITE_WHITELIST = frozenset({
    "/auth/login",
    "/auth/logout",
    "/auth/refresh",
    "/auth/recent-auth",
    "/desktop-auth/bootstrap",
    "/desktop-setup/migration-status/approve",
})

MIGRATION_RESTRICTED_MODE_ERROR_CODE = "MIGRATION_RESTRICTED_MODE"


def _notify_server_admin_restricted_mode_blocked_write() -> None:
    """2026-09-16 개인 베타 잔여 작업(Phase 5, 10-18) — Migration
    제한 모드가 실제로 쓰기 요청 하나를 막은 순간에만 서버 관리자
    에게 알린다(진단 시점이 아니라 실제 영향이 생긴 시점). 같은
    pending 파일 집합으로는 반복 요청마다 중복 알림을 보내지 않도록
    (idempotency_key가 이미 platform_alert 레이어에서 막아 준다),
    매 요청마다 여기 도달해도 실제 DB 쓰기는 최초 1회만 일어난다.
    알림 실패가 423 응답 자체를 막지 않는다 — best-effort."""

    try:
        from app.core.migration_restricted_mode import get_restricted_mode_state
        from app.database.session import SessionLocal
        from app.domains.platform_alert.constants import PlatformAlertEventCode
        from app.domains.platform_alert.service import PlatformAlertService

        state = get_restricted_mode_state()
        key_material = ",".join(sorted(state.pending_files)) or (state.error or "unknown")
        db = SessionLocal()
        try:
            PlatformAlertService(db).dispatch_alert(
                PlatformAlertEventCode.MIGRATION_RESTRICTED_MODE_ENTERED,
                title="Migration 제한 모드가 쓰기 요청을 차단했습니다",
                message=(
                    f"미적용 Migration {len(state.pending_files)}건 또는 진단 오류로 "
                    f"제한 모드가 활성화돼 쓰기 요청이 차단됐습니다: "
                    f"{state.error or ', '.join(state.pending_files)}"
                ),
                entity_ref="migration_restricted_mode",
                idempotency_key=f"migration_restricted_mode:{key_material}",
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - 알림 실패가 423 응답 자체를 막지 않는다
        pass


@app.middleware("http")
async def _enforce_migration_restricted_mode(request, call_next):

    if (
        request.method not in _MIGRATION_RESTRICTED_MODE_SAFE_METHODS
        and request.url.path not in _MIGRATION_RESTRICTED_MODE_WRITE_WHITELIST
        and is_restricted_mode()
    ):
        _notify_server_admin_restricted_mode_blocked_write()
        return JSONResponse(
            status_code=423,
            content={
                "detail": (
                    "제한 모드입니다 — 대기 중인 데이터 구조 업데이트를 "
                    "먼저 적용해야 저장·전송이 가능합니다."
                ),
                "error_code": MIGRATION_RESTRICTED_MODE_ERROR_CODE,
            },
            headers={"X-Migration-Restricted-Code": MIGRATION_RESTRICTED_MODE_ERROR_CODE},
        )

    return await call_next(request)


# --------------------------------------------------
# 2026-09-08 사고 재발 방지 — 위 미들웨어는 쓰기만 막는다("읽기는 항상
# 안전하다"는 전제). 실제로는 대기 중인 Migration이 추가한 nullable
# 컬럼/테이블을 이미 참조하는 Model로 GET 요청이 SELECT를 실행하면
# `OperationalError: no such column/table`로 그대로 500이 난다(item 7
# 작업 중 실제로 겪은 장애 — docs/HOMEZ_PROJECT_STATE.md 2026-09-08
# "사고" 절 참고). 모든 GET을 미리 차단하거나(다른 회사·다른 테이블의
# 정상 조회까지 막게 됨) 런타임에 스키마를 임의로 맞추는 대신, 제한
# 모드일 때 "이 에러가 실제로 발생하면" 그 자리에서 명확한 갱신 필요
# 응답으로 바꿔치기한다 — 로그인·상태진단·복구처럼 이 컬럼/테이블을
# 건드리지 않는 경로는 애초에 이 예외 자체가 나지 않으므로 그대로
# 정상 동작한다. 제한 모드가 아니거나 스키마 불일치로 보이지 않는
# OperationalError(진짜 DB 결함 등)는 그대로 다시 던져 기존 처리를
# 그대로 따른다 — 조용히 숨기지 않는다.
# --------------------------------------------------

SCHEMA_UPDATE_REQUIRED_ERROR_CODE = "SCHEMA_UPDATE_REQUIRED"

_SCHEMA_MISMATCH_MARKERS = ("no such column", "no such table")


def _looks_like_pending_schema_mismatch(exc: OperationalError) -> bool:
    """SQLAlchemy가 감싼 원본 드라이버 메시지를 우선 본다(더 구체적).
    exc.orig가 없으면 SQLAlchemy 메시지 전체(SQL 문 포함)로 대체한다."""

    message = str(exc.orig) if getattr(exc, "orig", None) is not None else str(exc)
    lowered = message.lower()
    return any(marker in lowered for marker in _SCHEMA_MISMATCH_MARKERS)


@app.exception_handler(OperationalError)
async def _handle_operational_error(request: Request, exc: OperationalError):

    if is_restricted_mode() and _looks_like_pending_schema_mismatch(exc):
        state = get_restricted_mode_state()
        logger.warning(
            "스키마 갱신 필요 — pending=%s, path=%s",
            state.pending_files, request.url.path,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "이 기능을 사용하려면 아직 적용되지 않은 데이터 구조 "
                    "업데이트가 먼저 필요합니다 — 관리자에게 문의하세요."
                ),
                "error_code": SCHEMA_UPDATE_REQUIRED_ERROR_CODE,
                "pending_migration_count": len(state.pending_files),
            },
            headers={
                "X-Migration-Restricted-Code": SCHEMA_UPDATE_REQUIRED_ERROR_CODE,
            },
        )

    # 제한 모드가 아니거나 스키마 불일치로 보이지 않으면 이 핸들러가
    # 개입하지 않은 것처럼 원래 예외를 그대로 다시 던진다 — 기존 기본
    # 500 처리 경로를 그대로 유지한다(다른 진짜 DB 오류를 이 메시지로
    # 가리지 않기 위함).
    raise exc


# --------------------------------------------------
# System
# --------------------------------------------------

@app.get(
    "/",
    tags=["System"],
)
def root():

    return {
        "success": True,
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
    }


@app.get(
    "/health",
    tags=["System"],
)
def health():
    """
    "service": "HOMEZ"는 이 API 응답이 실제 HOMEZ 서버에서 온 것인지
    확인하기 위한 고정 문자열 식별자다(설정값이 아니라 리터럴 —
    APP_NAME처럼 환경설정으로 바뀌지 않는다). Desktop Shell 런처
    (app/desktop/server.py)가 포트가 열려 있어도 이 필드가 없으면 다른
    프로그램으로 간주하고 중단한다. 기존 success/status 필드는 그대로
    유지해 기존 계약을 깨지 않는다(추가 필드만 도입).
    """

    return {
        "success": True,
        "status": "healthy",
        "service": "HOMEZ",
    }


# --------------------------------------------------
# Routers
# --------------------------------------------------

# permission / role:
# router 심볼 부재로 기동에서 일시 제외.

# 2026-08-12 Gate X-2A 감사 — brand/category/supplier/marketplace/
# product/order/purchase/shipment 8개 라우터 전부 어떤 라우트에도
# 인증 Depends가 없었다(Critical/High — product는 가격 변경까지,
# marketplace는 이미 별도 ADR로 "레거시·평문 api_key/secret 저장
# 가능성" 플래그가 붙어 있던 도메인이라 무인증 노출 시 Credential
# 유출 위험까지 있었다). HOMEZ의 실제 판매 파이프라인은 이미
# product_candidate/decision/marketplace_listing/store_connection
# 도메인으로 대체되었고, 이 8개는 pivot 이전의 레거시 쇼핑몰형
# 스캐폴딩이다 — console.js·다른 라이브 도메인·테스트 어디에서도
# 이 8개 라우터의 경로를 호출하지 않음을 재확인했다(grep 0건).
# 즉시 안전하게 인증을 붙일 정식 대체 경로가 없으므로(카탈로그성
# 기능 자체가 현재 제품 방향에 없음) 마운트를 제거한다 — 향후 이
# 계열 기능이 실제로 필요해지면 company_id 격리와 Permission Guard를
# 갖춘 새 domain slice로 다시 설계해야 한다(정식 대체 경로 계획,
# 이번 Gate에서는 실행하지 않음).
# app.include_router(
#     brand_router,
#     prefix="/brands",
#     tags=["Brand"],
# )

# app.include_router(
#     category_router,
#     prefix="/categories",
#     tags=["Category"],
# )

app.include_router(
    company_router,
    prefix="/companies",
    tags=["Company"],
)

# app.include_router(
#     supplier_router,
#     prefix="/suppliers",
#     tags=["Supplier"],
# )

# app.include_router(
#     marketplace_router,
#     prefix="/marketplaces",
#     tags=["Marketplace"],
# )

# app.include_router(
#     product_router,
#     prefix="/products",
#     tags=["Product"],
# )

# 2026-08-15 V7 Gate 4 — Order/Purchase/Shipment/ReturnOrder 전면
# 재설계(company_id 스코프 완비, Inventory Gate 3/Funding Gate 2와
# 실제로 배선됨). 실제 homez.db에는 이 테이블들이 아직 없다(Migration은
# 임시 SQLite에서만 리허설 — Gate 2/3가 이미 쓴 선례와 동일하게 마운트는
# 먼저 하되 실제 DB 적용은 별도 승인).
app.include_router(
    order_router,
)

app.include_router(
    purchase_router,
)

app.include_router(
    shipment_router,
)

app.include_router(
    return_order_router,
)

# 2026-08-20 V7 Section F — 공급처 검색·상품 연결(SupplierProductLink,
# company_id 스코프). 전역 Supplier 카탈로그(app.domains.supplier)와
# purchase.supplier_id를 그대로 참조 — 새 Supplier 테이블 없음.
# Migration은 임시 SQLite에서만 리허설(위 order/purchase/shipment/
# return_order와 동일한 선례) — 실제 homez.db 미적용,
# LIVE_MIGRATION_PENDING.
app.include_router(
    source_router,
)

# CP-2(2026-08-21) — 채널 정책 엔진. 신규 테이블 3개는 임시 SQLite
# 에서만 검증(migrations/20260821_02_create_channel_policy_schema.sql)
# — 실제 homez.db 미적용, LIVE_MIGRATION_PENDING.
app.include_router(
    channel_policy_router,
)

# CA-5(2026-08-21) — AI Capability Registry. 신규 테이블 없음(정적
# Python 카탈로그) — Migration 불필요.
app.include_router(
    ai_governance_router,
)

# CA-2(2026-08-21) — 상품 선별 통합 화면. 새 테이블 없음(기존 도메인
# 읽기 전용 조회만) — Migration 불필요.
app.include_router(
    product_selection_router,
)

app.include_router(
    orchestration_router,
)

app.include_router(
    funding_router,
)

# 2026-08-15 V7 Gate 3 — 신규 재고 도메인(SKU/예약/원장/채널매핑,
# company_id 스코프 완비). 실제 homez.db에는 이 테이블들이 아직
# 없다(Migration은 임시 SQLite에서만 리허설, 이번 Gate는 Live Gate가
# 아니다) — Gate 2가 company_decision_policies에 대해 이미 쓴 것과
# 동일한 선례(마운트는 먼저 하되 실제 DB 적용은 별도 승인)를 따른다.
app.include_router(
    inventory_router,
)

app.include_router(
    retail_purchase_router,
)

app.include_router(
    purchase_task_router,
)

app.include_router(
    settlement_router,
)

# 2026-08-15 V7 Gate 5 — Pricing & Margin Reconciliation(가격/원가/
# 마진 계산, 가격 변경 승인, 정산 대사). 실제 homez.db에는 이
# 테이블들이 아직 없다(Migration은 임시 SQLite에서만 리허설, 이번
# Gate도 Live Gate가 아니다) — Gate 2/3/4가 이미 쓴 선례와 동일(마운트는
# 먼저 하되 실제 DB 적용은 별도 승인).
app.include_router(
    pricing_router,
)

app.include_router(
    product_candidate_router,
)

app.include_router(
    coupang_router,
)

app.include_router(
    coupang_policy_router,
)

app.include_router(
    decision_router,
)

app.include_router(
    decision_policy_router,
)

app.include_router(
    decision_company_policy_router,
)

app.include_router(
    marketplace_listing_router,
)

app.include_router(
    listing_wizard_router,
)

# 2026-08-15 V7 Gate 6 — 후보 선택→초안 생성→이미지 생성 통합
# 파이프라인. 새 테이블 없음(기존 ListingWizard/ImageGenerationJob만
# 재사용) — 실제 homez.db 영향 없음.
app.include_router(
    candidate_pipeline_router,
)

app.include_router(
    user_settings_router,
)

app.include_router(
    media_asset_router,
)

app.include_router(
    listing_package_router,
)

app.include_router(
    store_connection_router,
)

app.include_router(
    backup_router,
)

app.include_router(
    restore_router,
)

# 2026-09-10 Phase 7 — 결제수단·자동결제 한도(app/domains/payment).
# 실제 homez.db에는 이 테이블들이 아직 없다(Migration은 임시
# SQLite에서만 리허설) — order/purchase/inventory/pricing 등이 이미
# 쓴 선례와 동일하게 마운트는 먼저 하되 실제 DB 적용은 별도 승인.
# 이 도메인은 실제 결제 Provider를 구현하지 않는다(FakePaymentProvider
# 뿐 — app/domains/payment/gateway.py 참고), 실제 결제 실행
# 엔드포인트 자체가 없다.
app.include_router(
    payment_router,
)

# 2026-09-10 Phase 8 — 환불(app/domains/refund). return_order(물류
# — 회수/검수)와 완전히 별개인 "돈이 돌아가는 과정" Domain. 실제
# homez.db에는 이 테이블들이 아직 없다(Migration은 임시 SQLite에서만
# 리허설) — payment와 동일한 선례. 실제 환불 실행 Provider는
# 구현하지 않는다(FakeRefundExecutor뿐).
app.include_router(
    refund_router,
)

# 2026-09-10 Phase 9 — 환율(app/domains/currency). 실제 homez.db에는
# 이 테이블들이 아직 없다(Migration은 임시 SQLite에서만 리허설) —
# payment/refund와 동일한 선례. 실제 외부 환율 API 호출은 없다
# (수동 입력만).
app.include_router(
    currency_router,
)

# 2026-09-10 Phase 9 — 공급처 능력 플래그
# (app/domains/supplier_capability). 기존 4개 Adapter 계약을
# 병합하지 않고 별도로 추가한 "능력 요약표"다(사유는
# app/domains/supplier_capability/constants.py 상단 주석). 실제
# homez.db에는 이 테이블들이 아직 없다(Migration은 임시 SQLite
# 에서만 리허설).
app.include_router(
    supplier_capability_router,
)

# 2026-09-10 Phase 10 — 가격·재고 안전장치
# (app/domains/price_stock_safety). 실제 homez.db에는 이
# 테이블들이 아직 없다(Migration은 임시 SQLite에서만 리허설).
app.include_router(
    price_stock_safety_router,
)

# 2026-09-15 전면 감사 후속(Phase 9G) — 상품 속성(이름/옵션/수량/
# 사이즈/제조사/원산지) 정규화 비교(app/domains/product_attribute_
# match). 실제 homez.db에는 이 테이블들이 아직 없다(Migration은
# 임시 SQLite에서만 리허설).
app.include_router(
    product_attribute_match_router,
)

# 2026-09-15 전면 감사 후속(Phase 9I/9J) — 리콜/판매중지 매일 확인·
# 확인된 문제 상품 차단(app/domains/recall_notice). 실제 homez.db에는
# 이 테이블들이 아직 없다(Migration은 임시 SQLite에서만 리허설). 실제
# Provider도 아직 선정되지 않았다(app/domains/recall_notice/
# provider.py::get_real_provider() 참고).
app.include_router(
    recall_notice_router,
)

# 2026-09-10 Phase 12 — AI 데이터·학습 기반(app/domains/ai_learning).
# app/domains/decision(DecisionEvaluation/DecisionReview)은 전혀
# 수정하지 않는다 — 결과 기록·학습셋 export·후보 모델 검증
# 상태기계만 추가한다. 실제 모델 학습·적용 코드는 없다. 실제
# homez.db에는 이 테이블들이 아직 없다(Migration은 임시 SQLite
# 에서만 리허설).
app.include_router(
    ai_learning_router,
)

app.include_router(
    notification_center_router,
)

app.include_router(
    update_router,
)

app.include_router(
    diagnostics_router,
)

app.include_router(
    guides_router,
)

app.include_router(
    web_console_router,
)

# permission / role:
# router 심볼 부재(Phase 1-Fix High)로 기동에서 제외.
# whitelist 외 파일은 수정하지 않는다.

# 2026-08-12 Gate X-1 감사 — role_permission_router(/role-permissions)와
# user_router(/users)는 어떤 라우트에도 인증/Permission Depends가 없어
# 무인증 상태로 역할별 Permission 일괄 교체(PUT)와 사용자 생성·조회·
# 수정(role_id/company_id/active 포함)·삭제·검색이 전부 가능했다
# (Critical, 위 role_router/permission_router와 동일한 사유로 기동에서
# 제외). 이 기능들의 실제 인증된 대체 경로는 이미 존재한다
# (`/admin/roles/...` — role_permission_admin_router, `/admin/users/...`
# — app/core/account_admin.py) — console.js도 항상 이 두 대체 경로만
# 사용하며 아래 두 라우터를 호출하지 않는다(grep 재확인 완료).
# app.include_router(
#     role_permission_router,
#     prefix="/role-permissions",
#     tags=["RolePermission"],
# )

# app.include_router(
#     user_router,
#     prefix="/users",
#     tags=["User"],
# )

app.include_router(
    auth_router,
)

app.include_router(
    desktop_auth_router,
)

app.include_router(
    desktop_setup_router,
)

app.include_router(
    migration_approval_router,
)

app.include_router(
    company_recovery_setup_router,
)

app.include_router(
    account_recovery_router,
)

app.include_router(
    account_registration_router,
)

app.include_router(
    account_admin_router,
)

app.include_router(
    role_permission_admin_router,
)