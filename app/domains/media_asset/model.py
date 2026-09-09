"""
=========================================================
Homez OS

File : app/domains/media_asset/model.py

Media Asset Domain — 상품 이미지(원본/AI 생성본/채널 변환본)와 AI
이미지 생성 Job/결과를 관리한다.

이 코드베이스 전체 컨벤션 그대로: ForeignKey를 쓰지 않는다(전부 논리
참조 컬럼). company_id는 전역 설정이 아닌 모든 테이블에 필수로
포함되며(이 도메인에 전역 테이블은 없다), 모든 단건 조회·조건부
UPDATE는 반드시 company_id를 WHERE에 포함한다(2026-08-01
MarketplaceListing tenant 격리 재감사와 동일한 원칙을 처음부터
적용한다 — 나중에 보완하지 않는다).

MediaAsset은 원본 파일을 절대 덮어쓰지 않는다 — 재생성은 항상 새
행을 만들고 source_asset_id로 계보만 연결한다. storage_path는
사용자가 올린 파일명을 절대 그대로 쓰지 않는다(경로 조작 방지 —
app/domains/media_asset/image_validation.py::build_storage_path
참고).

ImageGenerationResult는 append-only다 — Job 하나가 여러 결과 항목을
만들 수 있고(요청한 이미지 개수만큼), 그중 일부만 실패해도 성공한
항목은 그대로 유지한다(부분 성공, Job.status=PARTIAL).

ImageGenerationDailyUsage는 app/domains/automation_safety의 원자적
카운터 패턴(조건부 UPDATE ... WHERE consumed + amount <= limit)을
그대로 복제한다 — 그 도메인을 직접 확장하지 않는다(상거래
안전장치와 이미지 생성 비용 한도는 서로 다른 관심사이며, 이
코드베이스는 도메인 간 Model import를 하지 않는 것이 컨벤션이다).
=========================================================
"""

from datetime import datetime

from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Numeric
from sqlalchemy import DateTime
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

MONEY = Numeric(12, 4)


class MediaAsset(Base):
    """원본/생성본/채널 변환본 이미지 — 실제 바이트는 파일시스템에,
    이 행은 메타데이터만 보관한다."""

    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "storage_path",
            name="uq_media_assets_company_storage_path",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # PRODUCT_CANDIDATE / LISTING_PACKAGE — 논리 참조 대상의 종류.
    owner_type: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )

    # 논리 참조(owner_type이 가리키는 테이블의 id).
    owner_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # ORIGINAL / GENERATED / CHANNEL_VARIANT
    asset_role: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )

    # 이 자산이 어떤 자산으로부터 파생됐는지(재생성/채널 변환 계보).
    # 논리 참조(media_assets.id), 원본(ORIGINAL)은 None.
    source_asset_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    # CHANNEL_VARIANT일 때만 채움(예: "COUPANG").
    channel_code: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )

    # MAIN / DETAIL / THUMBNAIL
    purpose: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )

    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # 서버가 계산한 경로 — 사용자가 올린 파일명을 절대 그대로 쓰지
    # 않는다(image_validation.py::build_storage_path).
    storage_path: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )

    # 참고용 메타데이터로만 저장 — 파일시스템 경로 계산에 절대
    # 사용하지 않는다.
    original_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )

    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)

    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ACTIVE / ORPHANED / DELETED(soft) — 실제 파일 삭제는 이 Phase의
    # 범위 밖(별도 정리 배치가 ORPHANED 행을 대상으로 실행하는 구조만
    # 마련한다).
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE", index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    # VERIFIED / RIGHTS_UNVERIFIED(2026-08-20) / RIGHTS_DENIED(2026-08-28) —
    # 모든 외부 사용자 업로드·검색 이미지는 예외 없이 RIGHTS_UNVERIFIED로
    # 시작한다. VERIFIED 전환은 confirm_rights_verified()의 명시적 확인
    # (근거 유형 필수)으로만 가능하다. 파생 자산(누끼/분할/배경합성)은
    # 원본의 rights_status를 그대로 상속할 뿐 자동으로 격상되지 않는다.
    #
    # 2026-08-28 정책 반전(사용자 결정) — RIGHTS_UNVERIFIED는 더 이상
    # 공개 업로드·이미지 선택·8단계 승인을 하드 차단하지 않는다(경고 +
    # ImageRightsAcknowledgement로 대체, rights_evidence_service.py).
    # 다만 RIGHTS_DENIED(명시적 사용금지·권리철회·삭제요청·신고/분쟁,
    # job_queue_service.py::deny_rights())는 이번 반전과 무관하게 계속
    # 하드 차단된다 — "증빙 미제출"과 "명시적으로 금지됨"은 반대 방향의
    # 서로 다른 상태이므로 같은 정책으로 다루지 않는다.
    rights_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="RIGHTS_UNVERIFIED", index=True,
    )

    # 2026-08-27 추가(additive) — 쿠팡 Live 제출은 vendorPath로 공개
    # HTTPS URL만 받는다(로컬 경로 불가). 이 3개 컬럼은 "이미 외부
    # 호스팅에 업로드했는지, 어디로 업로드했는지"만 캐싱한다 — 원본
    # storage_path/파일은 그대로 로컬에 남는다. public_url이 None이면
    # 아직 업로드되지 않은 것이다(app/domains/media_asset/
    # public_hosting.py 참고).
    public_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )
    public_url_provider: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )
    public_url_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 2026-08-28 추가(additive, Section 1 이미지 가져오기) — URL로
    # 가져온 이미지의 출처. 로컬 파일 업로드면 둘 다 None(출처
    # URL이 없다는 사실 자체가 정보다 — 추정해 채우지 않는다).
    source_url: Mapped[str | None] = mapped_column(
        String(2000), nullable=True,
    )
    source_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True,
    )

    # SUPPLIER / MANUFACTURER / USER_CAPTURED / UNKNOWN — 공급처
    # 제공, 제조사 공식 자료, 사용자 직접 촬영, 출처 미확인 구분.
    # 이 값 자체가 권리를 보증하지 않는다(rights_status와 별개 축 —
    # "누가 준 사진인지"와 "쓸 권리가 있는지"는 다른 질문이다).
    source_classification: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNKNOWN",
    )


class ImageGenerationJob(Base):
    """AI 이미지 생성 요청 1건 — Provider 호출 단위."""

    __tablename__ = "image_generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_image_generation_jobs_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조(listing_packages.id) — Package 생성 이전에도 Job을
    # 만들 수 있어 nullable(예: 미리보기 생성).
    listing_package_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    # 논리 참조(product_candidates.id) — 이 Job이 어떤 상품을 위한
    # 이미지를 만드는지는 항상 필수.
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # FAKE / DISABLED / (향후 실제 Provider 코드)
    provider_code: Mapped[str] = mapped_column(String(30), nullable=False)

    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 요청 내용(purposes, 개수, style 파라미터 등)의 canonical JSON
    # SHA-256 — 같은 입력으로 재시도해도 같은 지문이 나온다.
    prompt_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    request_payload_json: Mapped[str] = mapped_column(
        String(4000), nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True,
    )

    progress_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    estimated_cost: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(MONEY, nullable=True)

    error_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    requested_by: Mapped[int] = mapped_column(Integer, nullable=False)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class ImageGenerationResult(Base):
    """Job 하나가 만든 결과 항목들 — append-only, 부분 실패를 항목
    단위로 기록한다."""

    __tablename__ = "image_generation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조(image_generation_jobs.id)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 논리 참조(media_assets.id) — 이 항목이 실패했으면 None(자산이
    # 아예 만들어지지 않음).
    media_asset_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # MAIN / DETAIL / THUMBNAIL
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)

    # SUCCEEDED / FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # PASSED / BLOCKED / UNKNOWN — fail-closed 기본값 UNKNOWN.
    safety_check_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNKNOWN",
    )

    error_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class ImageGenerationDailyUsage(Base):
    """회사별 일일 이미지 생성 개수·비용 원자적 카운터.

    app/domains/automation_safety::ExecutionPeriodUsage와 동일한
    패턴(조건부 UPDATE ... WHERE consumed + amount <= limit)을
    복제한다 — 그 테이블을 직접 재사용하지 않는다(도메인 분리
    유지).
    """

    __tablename__ = "image_generation_daily_usage"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "usage_date",
            name="uq_image_generation_daily_usage_company_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # "YYYY-MM-DD" 문자열(KST 기준) — 날짜 경계 계산은 service에서.
    usage_date: Mapped[str] = mapped_column(String(10), nullable=False)

    consumed_image_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    consumed_cost: Mapped[float] = mapped_column(
        MONEY, nullable=False, default=0,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class ImageRightsEvidence(Base):
    """이미지 권리 증빙(2026-08-28) — 공급처·출처 도메인 단위로
    등록해 여러 상품·이미지에서 재사용한다. 이 행이 있다고 실제
    권리자의 허가를 HOMEZ가 보증하지 않는다 — 사용자가 입력한
    참고자료일 뿐이다(app/domains/marketplace_listing/
    listing_wizard_service.py 등에서 이 존재만으로 자동 VERIFIED
    승격을 하지 않는다). 증빙 미제출은 어떤 기능도 차단하지
    않는다 — 이 테이블은 경고 화면에 표시할 근거를 모으는
    용도다."""

    __tablename__ = "image_rights_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 공급처 ID(logical reference) 또는 출처 도메인 문자열 중
    # 하나만 채운다 — 둘 다 없으면 재사용 대상을 특정할 수 없다.
    supplier_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    source_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True,
    )

    # SUPPLIER_PERMISSION_LETTER / EMAIL_OR_SMS_CAPTURE / CONTRACT /
    # LICENSE_FILE / USER_CAPTURED_CONFIRMATION 등 — 자유 코드,
    # 화면 표시용 라벨은 프론트엔드가 매핑한다.
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # JSON 배열 문자열 — 이 증빙이 허용하는 판매채널 코드 목록.
    allowed_channels_json: Mapped[str] = mapped_column(
        String(500), nullable=False, default="[]",
    )

    # ALLOWED / PROHIBITED / UNKNOWN — 상업적 이용 허용 여부.
    commercial_use_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNKNOWN",
    )

    editing_allowed: Mapped[bool | None] = mapped_column(
        nullable=True,
    )

    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 증빙파일 자체는 media_assets에 별도 업로드하고 여기서는 그
    # 논리 참조 또는 사용자 메모만 남긴다(원문 파일 중복 저장 방지).
    evidence_reference: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    verified_by_user_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class ImageRightsAcknowledgement(Base):
    """이미지 권리 경고에 대한 사용자의 진행 선택(append-only,
    2026-08-28). 증빙 미제출을 이유로 기능을 막지 않는다는 정책의
    핵심 근거 — 대신 "경고를 봤고, 이 시점에 계속 진행을
    선택했다"는 사실만 감사 가능하게 남긴다. 이미지가 바뀌면
    새 asset_id로 새 행이 쌓일 뿐, 기존 행을 수정하지 않는다(다른
    append-only 테이블과 동일 원칙)."""

    __tablename__ = "image_rights_acknowledgements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # 논리 참조(media_assets.id) — 이 경고가 어떤 이미지에 대한 것인지.
    asset_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # GENERATE_OR_EDIT_OR_EXPORT / CHANNEL_SUBMISSION_FINAL — 어느
    # 시점의 경고였는지(제출 직전 재경고를 구분하기 위함).
    workflow_stage: Mapped[str] = mapped_column(String(40), nullable=False)

    # RIGHTS_EVIDENCE_MISSING 등 — 이 경고가 어떤 조건으로 표시됐는지.
    warning_code: Mapped[str] = mapped_column(String(50), nullable=False)

    # CONTINUE / REGISTER_EVIDENCE_LATER / CANCELLED
    user_action: Mapped[str] = mapped_column(String(30), nullable=False)

    # 경고 표시 시점의 asset 파생 상태 지문 — 이미지가 바뀌면 값이
    # 달라져 "예전 확인이 지금도 유효하다"고 착각하지 않게 한다.
    image_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


__all__ = [
    "MediaAsset",
    "ImageGenerationJob",
    "ImageGenerationResult",
    "ImageGenerationDailyUsage",
    "ImageRightsEvidence",
    "ImageRightsAcknowledgement",
]
