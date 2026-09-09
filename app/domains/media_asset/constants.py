"""
=========================================================
Homez OS

File : app/domains/media_asset/constants.py

Media Asset / Image Generation Job 상수 — 상태값·보안 제한값.

제한값은 현재 클래스 상수로 고정한다(운영자가 UI에서 바꿀 수 있는
설정 테이블이 아니다) — 이번 Phase 범위 밖의 "Admin이 한도를
조정하는 화면"까지 만들지 않는다는 명시적 선택. 실제 운영에서 값
조정이 필요해지면 이 상수를 DB 설정 테이블로 승격하는 별도 작업이
필요하다(이번 구현은 그 승격을 쉽게 하도록 전부 이 파일 한 곳에
모아둔다).
=========================================================
"""


class MediaAssetOwnerType:

    PRODUCT_CANDIDATE = "PRODUCT_CANDIDATE"
    LISTING_PACKAGE = "LISTING_PACKAGE"

    ALL = (PRODUCT_CANDIDATE, LISTING_PACKAGE)


class MediaAssetRole:

    ORIGINAL = "ORIGINAL"
    GENERATED = "GENERATED"
    CHANNEL_VARIANT = "CHANNEL_VARIANT"

    ALL = (ORIGINAL, GENERATED, CHANNEL_VARIANT)


class MediaAssetPurpose:

    MAIN = "MAIN"
    DETAIL = "DETAIL"
    THUMBNAIL = "THUMBNAIL"

    ALL = (MAIN, DETAIL, THUMBNAIL)


class MediaAssetStatus:

    ACTIVE = "ACTIVE"
    ORPHANED = "ORPHANED"
    DELETED = "DELETED"

    ALL = (ACTIVE, ORPHANED, DELETED)


class ImageJobStatus:

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    TERMINAL = (SUCCEEDED, PARTIAL, FAILED, CANCELLED)
    ALL = (PENDING, RUNNING, SUCCEEDED, PARTIAL, FAILED, CANCELLED)


class ImageResultStatus:

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"

    ALL = (SUCCEEDED, FAILED)


class ImageSafetyCheckStatus:
    """
    생성된 이미지 콘텐츠 안전성 판단 — fail-closed. 실제 외부 안전성
    검사 API를 호출하지 않는 이번 Phase에서는 Provider가 반환한
    self-report만 반영하고, Provider가 아무 판단도 주지 않으면
    UNKNOWN으로 남긴다(자동으로 PASSED로 승격하지 않는다).
    """

    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"

    USABLE = (PASSED,)
    ALL = (PASSED, BLOCKED, UNKNOWN)


# --------------------------------------------------
# 보안·비용 제한값
# --------------------------------------------------

MAX_IMAGE_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_IMAGE_WIDTH = 4096
MAX_IMAGE_HEIGHT = 4096
MIN_IMAGE_WIDTH = 16
MIN_IMAGE_HEIGHT = 16

MAX_IMAGES_PER_JOB = 10
MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE = 30
MAX_REGENERATIONS_PER_PACKAGE = 5

DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY = 200
DAILY_IMAGE_COST_LIMIT_PER_COMPANY = 50.0  # 회사 통화 단위(추정치, Decimal로 계산)

JOB_STALL_TIMEOUT_SECONDS = 300  # RUNNING 상태로 이 시간 넘게 멈춰 있으면 복구 대상

ALLOWED_MIME_TYPES = ("image/png", "image/jpeg", "image/webp")

# --------------------------------------------------
# 긴 상세 이미지 원본(2026-08-20 CTO 지시) — 일반 이미지 제한
# (MAX_IMAGE_HEIGHT=4096 등)은 절대 전역으로 올리지 않는다. 사용자가
# "상세페이지용 긴 이미지"임을 명시적으로 선택한 업로드 경로에서만
# 이 별도(더 넓지만 여전히 유한한) 한도를 적용한다 — 압축폭탄·메모리
# 고갈 방어를 위해 그래도 상한은 둔다.
# --------------------------------------------------

MAX_LONG_DETAIL_IMAGE_HEIGHT = 20000
MAX_LONG_DETAIL_IMAGE_PIXELS = 60_000_000  # width*height, 종횡비 무관 절대 상한
MAX_LONG_DETAIL_IMAGE_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB


class RightsVerificationBasis:
    """VERIFIED 전환 시 사용자가 반드시 밝혀야 하는 근거 유형
    (2026-08-20 3차 지시) — 감사로그에 이 값만 남기고 이미지 원문·
    Credential·개인정보는 절대 남기지 않는다."""

    SELF_CAPTURED = "SELF_CAPTURED"  # 직접 촬영 또는 직접 제작
    SUPPLIER_BRAND_PERMISSION = "SUPPLIER_BRAND_PERMISSION"  # 공급처·브랜드 상업적 사용 허가
    COMMERCIAL_LICENSE = "COMMERCIAL_LICENSE"  # 상업 이용 가능한 라이선스

    ALL = (SELF_CAPTURED, SUPPLIER_BRAND_PERMISSION, COMMERCIAL_LICENSE)


class MediaAssetSourceClassification:
    """MediaAsset.source_classification — 이미지를 어디서 가져왔는지
    구분만 한다(권리 유무 판단이 아니다, 2026-08-28 Section 1). URL로
    가져온 이미지는 호출자가 SUPPLIER/MANUFACTURER 중 아는 값을
    명시하고, 모르면 UNKNOWN을 그대로 둔다 — 추측으로 채우지 않는다."""

    SUPPLIER = "SUPPLIER"
    MANUFACTURER = "MANUFACTURER"
    USER_CAPTURED = "USER_CAPTURED"
    UNKNOWN = "UNKNOWN"

    ALL = (SUPPLIER, MANUFACTURER, USER_CAPTURED, UNKNOWN)


__all__ = [
    "MediaAssetOwnerType",
    "MediaAssetRole",
    "MediaAssetPurpose",
    "MediaAssetStatus",
    "ImageJobStatus",
    "ImageResultStatus",
    "ImageSafetyCheckStatus",
    "MAX_IMAGE_FILE_SIZE_BYTES",
    "MAX_IMAGE_WIDTH",
    "MAX_IMAGE_HEIGHT",
    "MIN_IMAGE_WIDTH",
    "MIN_IMAGE_HEIGHT",
    "MAX_IMAGES_PER_JOB",
    "MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE",
    "MAX_REGENERATIONS_PER_PACKAGE",
    "DAILY_IMAGE_COUNT_LIMIT_PER_COMPANY",
    "DAILY_IMAGE_COST_LIMIT_PER_COMPANY",
    "JOB_STALL_TIMEOUT_SECONDS",
    "ALLOWED_MIME_TYPES",
    "MediaAssetSourceClassification",
]
