"""
=========================================================
Homez OS

File : app/core/config.py
Version : 5.0.0

Homez V5 Configuration System

=========================================================
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

# 2026-08-21 Live Gate 작업 5 — 배포 SemVer 단일 진실 공급원.
# app/core/version.py(app/domains/diagnostics/service.py가 이미
# 이 경로로 가져와 쓰고 있었다)를 그대로 재사용한다. 이 필드의 이전
# 하드코딩 기본값("5.0.0")은 app/core/version.py의 실제 배포 버전
# (2.1.0, installer/homez.iss의 AppVersion과 수동 동기화되는 값)과
# 완전히 무관한 숫자였다 — app/main.py의 루트 응답·OpenAPI 문서
# 버전이 실제 배포 버전과 다르게 보이던 원인이었다. "V7"은 제품
# 개발 단계명일 뿐 이 SemVer와 혼합하지 않는다.
from app.core.version import VERSION as _APP_VERSION

BASE_DIR = Path(__file__).resolve().parents[2]


def _default_database_url() -> str:
    """
    DATABASE_URL 기본값 계산(V7 Live Gate 4 재작업 — 결함 1).

    이전 기본값 "sqlite:///./homez.db"는 프로세스 현재 작업 디렉터리
    (CWD) 기준 상대경로였다 — 실제 설치된 앱을 시작메뉴/바탕화면
    바로가기로 실행하면(CWD=설치 폴더) app/desktop/paths.py가 정의하는
    공식 경로(%LOCALAPPDATA%\\HOMEZ\\data\\homez.db, 개발 모드는 저장소
    루트 homez.db)와 완전히 갈라져 설치 폴더 안에 Migration이 하나도
    적용 안 된 빈 DB가 새로 생겼다.

    이 함수는 app.desktop.paths.get_homez_db_path()가 계산하는 값과
    동일한 절대경로를 반환해, 실제 요청을 처리하는 SQLAlchemy 세션
    엔진(app/database/session.py)이 bootstrap/MigrationRunner와 항상
    같은 DB 파일을 가리키게 한다.

    get_homez_db_path()는 경로만 계산할 뿐 파일을 생성·이동·복사하지
    않으므로(자체 docstring 계약), 여기서 호출해도 실제 homez.db에는
    어떤 영향도 없다. confirm=True는 app/desktop/main.py의 공식 Desktop
    부팅 흐름과 동일한 명시적 의도 표현이다 — 이 기본값은 애초에 실제
    운영 DB(또는 개발 모드에서는 저장소 루트의 실제 homez.db)를
    가리키는 것이 의도이므로, 그 의도를 감추지 않고 명시적으로 드러낸다
    (app/desktop/paths.py의 ProductionDbAccessNotConfirmedError 가드
    자체는 약화시키지 않는다 — confirm=True를 넘기지 않으면 여전히
    예외가 발생한다).

    DATABASE_URL 환경변수(.env 포함, pydantic-settings가 필드 기본값보다
    우선 적용)가 설정되어 있으면 이 함수는 아예 호출되지 않는다 —
    테스트·개발 환경의 명시적 override는 그대로 유지된다.
    """

    from app.desktop.paths import get_homez_db_path

    db_path = get_homez_db_path(confirm=True)

    return f"sqlite:///{db_path}"


class Settings(BaseSettings):

    # ==================================================
    # Application
    # ==================================================

    APP_NAME: str = Field(
        default="Homez OS",
    )

    APP_VERSION: str = Field(
        default=_APP_VERSION,
    )

    APP_DESCRIPTION: str = Field(
        default="Homez V5 AI Commerce Platform",
    )

    APP_ENV: str = Field(
        default="development",
    )

    # 2026-08-03 V6 Gate 1: 운영 기본값은 반드시 False여야 한다 — True면
    # FastAPI가 처리되지 않은 예외의 전체 스택 트레이스를 HTTP 응답에
    # 노출한다(app/main.py의 debug=settings.DEBUG). 개발 중에는 .env로
    # DEBUG=true를 명시적으로 켠다.
    DEBUG: bool = Field(
        default=False,
    )

    TESTING: bool = Field(
        default=False,
    )

    TIMEZONE: str = Field(
        default="Asia/Seoul",
    )

    LANGUAGE: str = Field(
        default="ko",
    )

    INSTANCE_NAME: str = Field(
        default="homez-main",
    )

    NODE_NAME: str = Field(
        default="node-01",
    )

    DOMAIN: str = Field(
        default="localhost",
    )

    PORT: int = Field(
        default=8000,
    )

    API_PREFIX: str = Field(
        default="/api/v1",
    )

    ENABLE_DOCS: bool = Field(
        default=True,
    )

    ENABLE_REDOC: bool = Field(
        default=True,
    )

    ENABLE_OPENAPI: bool = Field(
        default=True,
    )

    # ==================================================
    # Environment
    # ==================================================

    DEVELOPMENT: bool = Field(
        default=True,
    )

    PRODUCTION: bool = Field(
        default=False,
    )

    STAGING: bool = Field(
        default=False,
    )

    LOG_LEVEL: str = Field(
        default="INFO",
    )

    # ==================================================
    # Database
    # ==================================================

    DATABASE_URL: str = Field(
        default_factory=_default_database_url,
    )

    DATABASE_ECHO: bool = Field(
        default=False,
    )

    DATABASE_POOL_SIZE: int = Field(
        default=20,
    )

    DATABASE_MAX_OVERFLOW: int = Field(
        default=30,
    )

    DATABASE_POOL_TIMEOUT: int = Field(
        default=30,
    )

    DATABASE_POOL_RECYCLE: int = Field(
        default=3600,
    )

    DATABASE_HEALTH_CHECK: bool = Field(
        default=True,
    )

    DATABASE_AUTO_CREATE: bool = Field(
        default=True,
    )

    DATABASE_AUTO_MIGRATION: bool = Field(
        default=False,
    )

    DATABASE_QUERY_LOG: bool = Field(
        default=False,
    )
        # ==================================================
    # Security
    # ==================================================

    SECRET_KEY: str = Field(
        default="CHANGE_ME",
    )

    JWT_SECRET_KEY: str = Field(
        default="CHANGE_ME",
    )

    REFRESH_SECRET_KEY: str = Field(
        default="CHANGE_ME",
    )

    API_KEY_SECRET: str = Field(
        default="CHANGE_ME",
    )

    ENCRYPTION_KEY: str = Field(
        default="CHANGE_ME",
    )

    PASSWORD_PEPPER: str = Field(
        default="CHANGE_ME",
    )

    SECURITY_VERSION: str = Field(
        default="v5",
    )

    TOKEN_ISSUER: str = Field(
        default="homez",
    )

    TOKEN_AUDIENCE: str = Field(
        default="homez-client",
    )

    TOKEN_TYPE: str = Field(
        default="Bearer",
    )

    TOKEN_BLACKLIST_ENABLED: bool = Field(
        default=True,
    )

    TOKEN_ROTATION_ENABLED: bool = Field(
        default=True,
    )

    API_KEY_ENABLED: bool = Field(
        default=True,
    )

    MFA_ENABLED: bool = Field(
        default=False,
    )

    SECRET_ROTATION_ENABLED: bool = Field(
        default=False,
    )

    # ==================================================
    # JWT
    # ==================================================

    JWT_ALGORITHM: str = Field(
        default="HS256",
    )

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
    )

    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=30,
    )

    LOGIN_MAX_ATTEMPT: int = Field(
        default=5,
    )

    LOGIN_LOCK_MINUTES: int = Field(
        default=30,
    )

    SESSION_TIMEOUT_MINUTES: int = Field(
        default=60,
    )

    DEVICE_LIMIT: int = Field(
        default=5,
    )

    REMEMBER_ME_DAYS: int = Field(
        default=30,
    )

    # ==================================================
    # Password Policy
    # ==================================================

    PASSWORD_MIN_LENGTH: int = Field(
        default=10,
    )

    PASSWORD_MAX_LENGTH: int = Field(
        default=128,
    )

    PASSWORD_REQUIRE_UPPER: bool = Field(
        default=True,
    )

    PASSWORD_REQUIRE_LOWER: bool = Field(
        default=True,
    )

    PASSWORD_REQUIRE_NUMBER: bool = Field(
        default=True,
    )

    PASSWORD_REQUIRE_SPECIAL: bool = Field(
        default=True,
    )

    PASSWORD_HISTORY_COUNT: int = Field(
        default=5,
    )

    PASSWORD_EXPIRE_DAYS: int = Field(
        default=90,
    )

    PASSWORD_BCRYPT_ROUNDS: int = Field(
        default=12,
    )

    # ==================================================
    # Encryption
    # ==================================================

    ENABLE_DATA_ENCRYPTION: bool = Field(
        default=True,
    )

    ENABLE_FIELD_ENCRYPTION: bool = Field(
        default=True,
    )

    ENABLE_FILE_ENCRYPTION: bool = Field(
        default=False,
    )

    ENABLE_AUDIT_SECURITY: bool = Field(
        default=True,
    )

    ENABLE_IP_WHITELIST: bool = Field(
        default=False,
    )

    ENABLE_RATE_LIMIT: bool = Field(
        default=True,
    )

    RATE_LIMIT_PER_MINUTE: int = Field(
        default=120,
    )

    RATE_LIMIT_BURST: int = Field(
        default=200,
    )
        # ==================================================
    # Redis
    # ==================================================

    REDIS_ENABLED: bool = Field(
        default=False,
    )

    REDIS_URL: str = Field(
        default="redis://localhost:6379",
    )

    REDIS_HOST: str = Field(
        default="localhost",
    )

    REDIS_PORT: int = Field(
        default=6379,
    )

    REDIS_DB: int = Field(
        default=0,
    )

    REDIS_USERNAME: str = Field(
        default="",
    )

    REDIS_PASSWORD: str = Field(
        default="",
    )

    REDIS_SSL: bool = Field(
        default=False,
    )

    REDIS_TIMEOUT: int = Field(
        default=10,
    )

    REDIS_MAX_CONNECTIONS: int = Field(
        default=100,
    )

    # ==================================================
    # Cache
    # ==================================================

    CACHE_ENABLED: bool = Field(
        default=True,
    )

    CACHE_BACKEND: str = Field(
        default="memory",
    )

    CACHE_DEFAULT_TTL: int = Field(
        default=3600,
    )

    CACHE_PRODUCT_TTL: int = Field(
        default=1800,
    )

    CACHE_USER_TTL: int = Field(
        default=600,
    )

    CACHE_PRICE_TTL: int = Field(
        default=300,
    )

    CACHE_MAX_ITEMS: int = Field(
        default=100000,
    )

    CACHE_CLEAN_INTERVAL: int = Field(
        default=600,
    )

    CACHE_COMPRESS: bool = Field(
        default=True,
    )

    CACHE_SERIALIZER: str = Field(
        default="json",
    )

    # ==================================================
    # Storage
    # ==================================================

    STORAGE_ROOT: str = Field(
        default="storage",
    )

    UPLOAD_PATH: str = Field(
        default="storage/uploads",
    )

    IMAGE_PATH: str = Field(
        default="storage/images",
    )

    PRODUCT_IMAGE_PATH: str = Field(
        default="storage/images/products",
    )

    PROFILE_IMAGE_PATH: str = Field(
        default="storage/images/profile",
    )

    EXPORT_PATH: str = Field(
        default="storage/export",
    )

    IMPORT_PATH: str = Field(
        default="storage/import",
    )

    BACKUP_PATH: str = Field(
        default="storage/backup",
    )

    TEMP_PATH: str = Field(
        default="storage/temp",
    )

    CACHE_PATH: str = Field(
        default="storage/cache",
    )

    LOG_PATH: str = Field(
        default="storage/logs",
    )

    REPORT_PATH: str = Field(
        default="storage/reports",
    )

    MODEL_PATH: str = Field(
        default="storage/models",
    )

    # ==================================================
    # Upload
    # ==================================================

    MAX_UPLOAD_SIZE: int = Field(
        default=50 * 1024 * 1024,
    )

    MAX_IMAGE_SIZE: int = Field(
        default=20 * 1024 * 1024,
    )

    MAX_VIDEO_SIZE: int = Field(
        default=500 * 1024 * 1024,
    )

    MAX_DOCUMENT_SIZE: int = Field(
        default=100 * 1024 * 1024,
    )

    ALLOWED_IMAGE_EXTENSIONS: str = Field(
        default="jpg,jpeg,png,webp,gif,bmp",
    )

    ALLOWED_DOCUMENT_EXTENSIONS: str = Field(
        default="pdf,xlsx,xls,csv,docx,pptx,txt",
    )

    ALLOWED_VIDEO_EXTENSIONS: str = Field(
        default="mp4,mov,avi,mkv",
    )

    AUTO_CREATE_DIRECTORY: bool = Field(
        default=True,
    )

    AUTO_OPTIMIZE_IMAGE: bool = Field(
        default=True,
    )

    IMAGE_QUALITY: int = Field(
        default=90,
    )

    THUMBNAIL_SIZE: int = Field(
        default=400,
    )

    ENABLE_WATERMARK: bool = Field(
        default=False,
    )

    # ==================================================
    # Logging
    # ==================================================

    LOG_ENABLED: bool = Field(
        default=True,
    )

    LOG_LEVEL: str = Field(
        default="INFO",
    )

    LOG_FORMAT: str = Field(
        default="text",
    )

    LOG_ROTATION: str = Field(
        default="1 day",
    )

    LOG_RETENTION: str = Field(
        default="30 days",
    )

    LOG_COMPRESSION: str = Field(
        default="zip",
    )

    LOG_CONSOLE: bool = Field(
        default=True,
    )

    LOG_FILE: bool = Field(
        default=True,
    )

    LOG_JSON: bool = Field(
        default=False,
    )

    LOG_SQL: bool = Field(
        default=False,
    )

    LOG_API: bool = Field(
        default=True,
    )

    LOG_AUTH: bool = Field(
        default=True,
    )

    LOG_MARKETPLACE: bool = Field(
        default=True,
    )

    LOG_AI: bool = Field(
        default=True,
    )

    LOG_AUDIT: bool = Field(
        default=True,
    )

    LOG_TRACE_ID: bool = Field(
        default=True,
    )
    # ==================================================
    # AI
    # ==================================================

    AI_ENABLED: bool = Field(
        default=True,
    )

    AI_PROVIDER: str = Field(
        default="openai",
    )

    AI_MODEL: str = Field(
        default="gpt-5",
    )

    AI_TIMEOUT: int = Field(
        default=60,
    )

    AI_MAX_TOKENS: int = Field(
        default=8192,
    )

    AI_TEMPERATURE: float = Field(
        default=0.2,
    )

    AI_CACHE_ENABLED: bool = Field(
        default=True,
    )

    AI_CACHE_TTL: int = Field(
        default=3600,
    )

    AI_RETRY_COUNT: int = Field(
        default=3,
    )

    AI_RETRY_DELAY: int = Field(
        default=5,
    )

    AI_LOG_ENABLED: bool = Field(
        default=True,
    )

    AI_AUTO_SUMMARY: bool = Field(
        default=True,
    )

    AI_AUTO_TRANSLATE: bool = Field(
        default=True,
    )

    AI_AUTO_CLASSIFICATION: bool = Field(
        default=True,
    )

    # ==================================================
    # Marketplace
    # ==================================================

    MARKETPLACE_ENABLED: bool = Field(
        default=True,
    )

    COUPANG_ENABLED: bool = Field(
        default=True,
    )

    NAVER_ENABLED: bool = Field(
        default=True,
    )

    ELEVENSTREET_ENABLED: bool = Field(
        default=True,
    )

    GMARKET_ENABLED: bool = Field(
        default=True,
    )

    AUCTION_ENABLED: bool = Field(
        default=True,
    )

    ALIEXPRESS_ENABLED: bool = Field(
        default=True,
    )

    TEMU_ENABLED: bool = Field(
        default=False,
    )

    MARKET_SYNC_INTERVAL: int = Field(
        default=300,
    )

    MARKET_PRICE_UPDATE_INTERVAL: int = Field(
        default=600,
    )

    MARKET_STOCK_UPDATE_INTERVAL: int = Field(
        default=300,
    )

    MARKET_AUTO_PRICE: bool = Field(
        default=True,
    )

    MARKET_AUTO_STOCK: bool = Field(
        default=True,
    )

    MARKET_AUTO_ORDER: bool = Field(
        default=False,
    )

    MARKET_AUTO_CANCEL: bool = Field(
        default=False,
    )

    # ==================================================
    # Crawler
    # ==================================================

    CRAWLER_ENABLED: bool = Field(
        default=True,
    )

    CRAWLER_HEADLESS: bool = Field(
        default=True,
    )

    CRAWLER_TIMEOUT: int = Field(
        default=60,
    )

    CRAWLER_WORKERS: int = Field(
        default=5,
    )

    CRAWLER_RETRY_COUNT: int = Field(
        default=3,
    )

    CRAWLER_DELAY: float = Field(
        default=1.0,
    )

    CRAWLER_USER_AGENT: str = Field(
        default="HomezCrawler/5.0",
    )

    CRAWLER_SAVE_HTML: bool = Field(
        default=False,
    )

    CRAWLER_SAVE_SCREENSHOT: bool = Field(
        default=False,
    )

    # ==================================================
    # Scheduler
    # ==================================================

    SCHEDULER_ENABLED: bool = Field(
        default=True,
    )

    SCHEDULER_TIMEZONE: str = Field(
        default="Asia/Seoul",
    )

    SCHEDULER_MAX_JOBS: int = Field(
        default=100,
    )

    SCHEDULER_COALESCE: bool = Field(
        default=True,
    )

    SCHEDULER_MISFIRE_GRACE_TIME: int = Field(
        default=300,
    )

    SCHEDULER_JOB_TIMEOUT: int = Field(
        default=3600,
    )

    # ==================================================
    # Worker
    # ==================================================

    WORKER_ENABLED: bool = Field(
        default=True,
    )

    WORKER_COUNT: int = Field(
        default=4,
    )

    WORKER_QUEUE_SIZE: int = Field(
        default=1000,
    )

    WORKER_SHUTDOWN_TIMEOUT: int = Field(
        default=30,
    )

    WORKER_RESTART_ON_FAILURE: bool = Field(
        default=True,
    )

    # ==================================================
    # Queue
    # ==================================================

    QUEUE_ENABLED: bool = Field(
        default=True,
    )

    QUEUE_BACKEND: str = Field(
        default="memory",
    )

    QUEUE_MAX_SIZE: int = Field(
        default=50000,
    )

    QUEUE_RETRY_COUNT: int = Field(
        default=5,
    )

    QUEUE_RETRY_DELAY: int = Field(
        default=30,
    )

    QUEUE_PRIORITY_LEVELS: int = Field(
        default=5,
    )

    QUEUE_DEAD_LETTER: bool = Field(
        default=True,
    )

    # ==================================================
    # Event Bus
    # ==================================================

    EVENT_BUS_ENABLED: bool = Field(
        default=True,
    )

    EVENT_ASYNC: bool = Field(
        default=True,
    )

    EVENT_MAX_HANDLERS: int = Field(
        default=100,
    )

    EVENT_RETRY_COUNT: int = Field(
        default=3,
    )

    EVENT_RETRY_DELAY: int = Field(
        default=10,
    )

    EVENT_LOGGING: bool = Field(
        default=True,
    )

    EVENT_DEAD_LETTER: bool = Field(
        default=True,
    )

    # ==================================================
    # Automation
    # ==================================================

    AUTOMATION_ENABLED: bool = Field(
        default=True,
    )

    AUTO_PRODUCT_DISCOVERY: bool = Field(
        default=True,
    )

    AUTO_PRICE_ANALYSIS: bool = Field(
        default=True,
    )

    AUTO_BRAND_ANALYSIS: bool = Field(
        default=True,
    )

    AUTO_IMAGE_PROCESSING: bool = Field(
        default=True,
    )

    AUTO_DESCRIPTION_GENERATION: bool = Field(
        default=True,
    )

    AUTO_CATEGORY_MATCHING: bool = Field(
        default=True,
    )

    AUTO_DUPLICATE_CHECK: bool = Field(
        default=True,
    )
        # ==================================================
    # Monitoring
    # ==================================================

    MONITORING_ENABLED: bool = Field(
        default=True,
    )

    HEALTH_CHECK_ENABLED: bool = Field(
        default=True,
    )

    HEALTH_CHECK_INTERVAL: int = Field(
        default=60,
    )

    METRICS_ENABLED: bool = Field(
        default=True,
    )

    METRICS_PATH: str = Field(
        default="/metrics",
    )

    PROMETHEUS_ENABLED: bool = Field(
        default=False,
    )

    TRACE_ENABLED: bool = Field(
        default=True,
    )

    TRACE_SAMPLE_RATE: float = Field(
        default=1.0,
    )

    PERFORMANCE_MONITORING: bool = Field(
        default=True,
    )

    SLOW_QUERY_THRESHOLD_MS: int = Field(
        default=1000,
    )

    MEMORY_MONITORING: bool = Field(
        default=True,
    )

    CPU_MONITORING: bool = Field(
        default=True,
    )

    DISK_MONITORING: bool = Field(
        default=True,
    )

    NETWORK_MONITORING: bool = Field(
        default=True,
    )

    # ==================================================
    # Audit
    # ==================================================

    AUDIT_ENABLED: bool = Field(
        default=True,
    )

    AUDIT_LOGIN: bool = Field(
        default=True,
    )

    AUDIT_PERMISSION: bool = Field(
        default=True,
    )

    AUDIT_PRODUCT: bool = Field(
        default=True,
    )

    AUDIT_ORDER: bool = Field(
        default=True,
    )

    AUDIT_PAYMENT: bool = Field(
        default=True,
    )

    AUDIT_AI: bool = Field(
        default=True,
    )

    AUDIT_ADMIN: bool = Field(
        default=True,
    )

    AUDIT_RETENTION_DAYS: int = Field(
        default=365,
    )

    # ==================================================
    # Feature Flags
    # ==================================================

    FEATURE_AI: bool = Field(
        default=True,
    )

    FEATURE_MARKETPLACE: bool = Field(
        default=True,
    )

    FEATURE_CRAWLER: bool = Field(
        default=True,
    )

    FEATURE_AUTOMATION: bool = Field(
        default=True,
    )

    FEATURE_ANALYTICS: bool = Field(
        default=True,
    )

    FEATURE_RECOMMENDATION: bool = Field(
        default=True,
    )

    FEATURE_BRAND_ENGINE: bool = Field(
        default=True,
    )

    FEATURE_PRODUCT_ENGINE: bool = Field(
        default=True,
    )

    FEATURE_TREND_ENGINE: bool = Field(
        default=True,
    )

    FEATURE_MEMORY_ENGINE: bool = Field(
        default=True,
    )

    FEATURE_RULE_ENGINE: bool = Field(
        default=True,
    )

    # ==================================================
    # Homez AI Engine
    # ==================================================

    AI_ENGINE_ENABLED: bool = Field(
        default=True,
    )

    AI_ENGINE_BATCH_SIZE: int = Field(
        default=100,
    )

    AI_ENGINE_MAX_WORKERS: int = Field(
        default=4,
    )

    AI_ENGINE_AUTO_LEARNING: bool = Field(
        default=True,
    )

    AI_ENGINE_CONFIDENCE_THRESHOLD: float = Field(
        default=0.80,
    )

    # ==================================================
    # Brand Engine
    # ==================================================

    BRAND_ENGINE_ENABLED: bool = Field(
        default=True,
    )

    BRAND_ANALYSIS_ENABLED: bool = Field(
        default=True,
    )

    BRAND_SCORE_ENABLED: bool = Field(
        default=True,
    )

    BRAND_DUPLICATE_CHECK: bool = Field(
        default=True,
    )

    # ==================================================
    # Product Engine
    # ==================================================

    PRODUCT_ENGINE_ENABLED: bool = Field(
        default=True,
    )

    PRODUCT_AUTO_MATCH: bool = Field(
        default=True,
    )

    PRODUCT_PRICE_COMPARE: bool = Field(
        default=True,
    )

    PRODUCT_DUPLICATE_FILTER: bool = Field(
        default=True,
    )

    PRODUCT_IMAGE_ANALYSIS: bool = Field(
        default=True,
    )

    # ==================================================
    # Recommendation Engine
    # ==================================================

    RECOMMENDATION_ENABLED: bool = Field(
        default=True,
    )

    RECOMMENDATION_TOP_K: int = Field(
        default=20,
    )

    RECOMMENDATION_CACHE: bool = Field(
        default=True,
    )

    RECOMMENDATION_REFRESH_MINUTES: int = Field(
        default=30,
    )

    # ==================================================
    # Trend Engine
    # ==================================================

    TREND_ENGINE_ENABLED: bool = Field(
        default=True,
    )

    TREND_ANALYSIS_INTERVAL: int = Field(
        default=3600,
    )

    TREND_SAVE_HISTORY: bool = Field(
        default=True,
    )

    TREND_RETENTION_DAYS: int = Field(
        default=365,
    )

    # ==================================================
    # Rule Engine
    # ==================================================

    RULE_ENGINE_ENABLED: bool = Field(
        default=True,
    )

    RULE_AUTO_APPLY: bool = Field(
        default=True,
    )

    RULE_CACHE_ENABLED: bool = Field(
        default=True,
    )

    RULE_MAX_RULES: int = Field(
        default=10000,
    )

    # ==================================================
    # Memory Engine
    # ==================================================

    MEMORY_ENGINE_ENABLED: bool = Field(
        default=True,
    )

    MEMORY_VECTOR_ENABLED: bool = Field(
        default=False,
    )

    MEMORY_MAX_HISTORY: int = Field(
        default=100000,
    )

    MEMORY_AUTO_CLEAN: bool = Field(
        default=True,
    )

    # ==================================================
    # Analytics
    # ==================================================

    ANALYTICS_ENABLED: bool = Field(
        default=True,
    )

    ANALYTICS_REALTIME: bool = Field(
        default=True,
    )

    ANALYTICS_SAVE_DAILY: bool = Field(
        default=True,
    )

    ANALYTICS_SAVE_MONTHLY: bool = Field(
        default=True,
    )

    ANALYTICS_RETENTION_DAYS: int = Field(
        default=1095,
    )

    # ==================================================
    # Backup
    # ==================================================

    BACKUP_ENABLED: bool = Field(
        default=True,
    )

    BACKUP_INTERVAL_HOURS: int = Field(
        default=24,
    )

    BACKUP_RETENTION_DAYS: int = Field(
        default=30,
    )

    BACKUP_COMPRESS: bool = Field(
        default=True,
    )

    BACKUP_VERIFY: bool = Field(
        default=True,
    )

    AUTO_BACKUP_BEFORE_UPDATE: bool = Field(
        default=True,
    )
    # ==================================================
    # Email
    # ==================================================

    EMAIL_ENABLED: bool = Field(
        default=False,
    )

    SMTP_HOST: str = Field(
        default="localhost",
    )

    SMTP_PORT: int = Field(
        default=587,
    )

    SMTP_USERNAME: str = Field(
        default="",
    )

    SMTP_PASSWORD: str = Field(
        default="",
    )

    SMTP_USE_TLS: bool = Field(
        default=True,
    )

    SMTP_USE_SSL: bool = Field(
        default=False,
    )

    EMAIL_SENDER: str = Field(
        default="noreply@homez.local",
    )

    # ==================================================
    # SMS
    # ==================================================

    SMS_ENABLED: bool = Field(
        default=False,
    )

    SMS_PROVIDER: str = Field(
        default="",
    )

    SMS_API_KEY: str = Field(
        default="",
    )

    SMS_API_SECRET: str = Field(
        default="",
    )

    SMS_SENDER: str = Field(
        default="",
    )

    # ==================================================
    # Notification
    # ==================================================

    NOTIFICATION_ENABLED: bool = Field(
        default=True,
    )

    PUSH_NOTIFICATION: bool = Field(
        default=True,
    )

    EMAIL_NOTIFICATION: bool = Field(
        default=True,
    )

    SMS_NOTIFICATION: bool = Field(
        default=False,
    )

    WEBHOOK_NOTIFICATION: bool = Field(
        default=False,
    )

    # ==================================================
    # External API
    # ==================================================

    EXTERNAL_API_TIMEOUT: int = Field(
        default=30,
    )

    EXTERNAL_API_RETRY: int = Field(
        default=3,
    )

    EXTERNAL_API_RETRY_DELAY: int = Field(
        default=5,
    )

    HTTP_CONNECTION_POOL: int = Field(
        default=100,
    )

    HTTP_KEEP_ALIVE: bool = Field(
        default=True,
    )

    HTTP_VERIFY_SSL: bool = Field(
        default=True,
    )

    # ==================================================
    # Homez Core
    # ==================================================

    CORE_VERSION: str = Field(
        default="5.0.0",
    )

    BUILD_NUMBER: int = Field(
        default=31,
    )

    ENABLE_EXPERIMENTAL: bool = Field(
        default=False,
    )

    ENABLE_DEBUG_TOOLBAR: bool = Field(
        default=False,
    )

    ENABLE_DEVELOPER_MODE: bool = Field(
        default=False,
    )

    ENABLE_BETA_FEATURE: bool = Field(
        default=False,
    )

    # ==================================================
    # model config
    # ==================================================

    model_config = SettingsConfigDict(

        env_file=".env",

        env_file_encoding="utf-8",

        case_sensitive=True,

        extra="ignore",

        validate_assignment=True,

    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Singleton Settings Loader
    """

    return Settings()


settings = get_settings()    