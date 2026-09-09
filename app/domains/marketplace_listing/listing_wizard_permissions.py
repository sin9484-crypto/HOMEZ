"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_permissions.py

Gate Q-2(2026-08-09) — 상품등록 마법사 세부 Permission 코드 정의.
새 테이블/Migration을 만들지 않는다 — 기존 `app/domains/permission`
(Permission 테이블) + `app/domains/role_permission`(RolePermission
테이블) + `app/core/permission_check.py`(has_permission) 인프라를
그대로 재사용한다.

실제 시딩 범위에 대한 명시적 경계 — 이 파일은 코드 상수와 "향후 실제
시딩 시 사용할 정의 목록"만 담는다. `LISTING_WIZARD_PERMISSION_DEFS`를
실제 homez.db에 반영(= `app/database/seed.py::seed_permissions()`에
등록해 다음 서버 부팅 때 자동 삽입)하는 것은 Gate Q-2 범위 밖이다
(사용자 지시의 Stop 조건 — "실제 Permission 시딩이 필요한 경우 중단
하고 보고"). 아래 상수는 오직 (1) Guard 로직의 코드 비교, (2) 임시 DB
테스트에서만 쓰인다. VIEWER 등 비-관리자 역할이 실제로 이 권한을
갖게 하려면, 향후 별도 승인 후 (a) 이 정의를 seed_permissions()에
반영해 실제 Permission 행을 만들고 (b) 기존 역할-권한 관리 화면에서
원하는 역할에 개별 권한을 부여해야 한다 — 둘 다 지금은 수행하지
않는다. 그 전까지는 ADMIN/SUPER_ADMIN만 이 Domain에 접근할 수 있다
(오늘과 동일한 접근성 — 실제 시딩 없이도 기존 배포가 절대 깨지지
않는다. `listing_wizard_permission_guard.py`의 Guard 설계 참고).
=========================================================
"""

LISTING_WIZARD_VIEW = "listing_wizard.view"
LISTING_WIZARD_CREATE = "listing_wizard.create"
LISTING_WIZARD_EDIT = "listing_wizard.edit"
LISTING_WIZARD_APPROVE = "listing_wizard.approve"
LISTING_WIZARD_SUBMIT = "listing_wizard.submit"
LISTING_WIZARD_RETRY = "listing_wizard.retry"
LISTING_WIZARD_EXPORT = "listing_wizard.export"
LISTING_ECONOMICS_VIEW = "listing_wizard.economics_view"
# 2026-08-28 "대기 상품 정리" — 개별/선택/전체(필터) 삭제(archive)와
# 복원(restore) 공통 권한. "전체 필터 삭제"(select_all_matching_filter)
# 모드는 이 권한에 더해 recent-auth 재확인도 추가로 요구한다
# (listing_wizard_router.py::bulk_archive_wizards 참고) — 사용자 결정
# "대량 삭제는 관리자 권한 또는 recent-auth를 검토"를 둘 다 반영.
LISTING_WIZARD_DELETE = "listing_wizard.delete"
# 2026-08-31 V7 필수 작업 2번 — 제출 장부 정합화 미리보기/조회 열람.
# 실제 적용(apply)은 이 권한이 아니라 SuperAdminGuard로 별도 통제한다
# (listing_wizard_router.py의 정합화 apply 엔드포인트 참고 — "별도 운영
# 승인 없이는 실행되지 않게" 요구사항을 이 두 단계로 나눠 반영한다).
LISTING_WIZARD_RECONCILE = "listing_wizard.reconcile"

# (code, name, description) — 향후 실제 시딩 시 seed_permissions()가
# 소비할 형태를 그대로 미리 맞춰 둔다(지금은 어디에도 연결되지 않음).
LISTING_WIZARD_PERMISSION_DEFS = [
    (
        LISTING_WIZARD_VIEW, "상품등록 마법사 조회",
        "위저드 목록·상세를 조회할 수 있다(금액 정보는 별도 권한).",
    ),
    (
        LISTING_WIZARD_CREATE, "상품등록 마법사 생성",
        "새 위저드를 만들거나 기존 위저드를 복제할 수 있다.",
    ),
    (
        LISTING_WIZARD_EDIT, "상품등록 마법사 편집",
        "위저드의 각 단계(출처/초안/미디어/채널/이행/마진/사전검사)를 "
        "수정할 수 있다.",
    ),
    (
        LISTING_WIZARD_APPROVE, "상품등록 마법사 승인 열람",
        "승인 미리보기·승인 취소 미리보기를 열람할 수 있다(실제 승인/"
        "취소 자체는 SUPER_ADMIN 3중 게이트가 별도로 통제한다).",
    ),
    (
        LISTING_WIZARD_SUBMIT, "상품등록 마법사 제출",
        "승인된 위저드를 실제 판매채널에 제출할 수 있다.",
    ),
    (
        LISTING_WIZARD_RETRY, "상품등록 마법사 재시도",
        "부분 실패한 채널만 골라 재시도할 수 있다.",
    ),
    (
        LISTING_WIZARD_EXPORT, "상품등록 마법사 내보내기",
        "위저드 관련 데이터를 CSV 등으로 내보낼 수 있다(현재 이 "
        "Domain에는 바인딩된 Export 엔드포인트가 없음 — 향후 대비 정의).",
    ),
    (
        LISTING_ECONOMICS_VIEW, "상품등록 마법사 금액 정보 열람",
        "원가·판매가·마진 등 금액 필드를 열람할 수 있다 — "
        "LISTING_WIZARD_VIEW와 독립적으로 통제된다.",
    ),
    (
        LISTING_WIZARD_DELETE, "상품등록 마법사 삭제/복원",
        "삭제 가능한 상태의 위저드를 개별/선택/전체(필터) 삭제하거나 "
        "복원할 수 있다. 실제 판매채널 제출·승인된 항목은 이 권한으로도 "
        "삭제할 수 없다(서비스 레벨 fail-closed).",
    ),
    (
        LISTING_WIZARD_RECONCILE, "제출 장부 정합화 미리보기",
        "PENDING/SUBMITTING/UNKNOWN 제출 건에 대해 정합화 미리보기(dry-"
        "run)를 열람할 수 있다. 실제 적용은 이 권한과 무관하게 "
        "SUPER_ADMIN만 수행할 수 있다.",
    ),
]

__all__ = [
    "LISTING_WIZARD_VIEW",
    "LISTING_WIZARD_CREATE",
    "LISTING_WIZARD_EDIT",
    "LISTING_WIZARD_APPROVE",
    "LISTING_WIZARD_SUBMIT",
    "LISTING_WIZARD_RETRY",
    "LISTING_WIZARD_EXPORT",
    "LISTING_ECONOMICS_VIEW",
    "LISTING_WIZARD_DELETE",
    "LISTING_WIZARD_RECONCILE",
    "LISTING_WIZARD_PERMISSION_DEFS",
]
