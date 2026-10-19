"""
=========================================================
Homez OS

File : app/domains/media_asset/reference_generation.py

"참고 사진으로 만들기" R1(2026-10-19, 사용자 승인) — 참고 영상(17:51~21:27) 속 기능을 HOMEZ 규칙에 맞춰 옮긴 **요청 명세 단계**.

영상의 흐름: 공급처의 모델 착용 사진 한 장을 넣고 → 배경(카페·산책로·도로변·바다 …)과 포즈(팔 내림·몸 틀기 …)를 고르면 → 같은 옷을 입은 여러 장의
썸네일을 만든다. 이 모듈은 그 앞부분만 한다: **무엇을 몇 장 어떻게 만들지 검증하고, 같은 요청이면 같은 지문이 나오게 정리해 제공자 요청으로 바꾼다.**

  - 외부 호출 없음·DB 없음·파일 없음(순수 데이터·계산). 실제 이미지 생성 API는 연결하지 않았다 — 제공자는 FAKE만 허용한다(`ALLOWED_PROVIDER_CODES`).
    실제 제공자 연결은 가드 허용·비용 승인·정책 조사(I0)가 끝난 별도 단계다.
  - 이 기능은 I1 명세로 보면 **L2(생성 배경·생성 인물)** 다. 결과에는 "AI 생성" 표기가 항상 필요하며, 상품이 참고 사진과 다르게 나오지 않았는지 사람이 확인하는
    검수 항목이 따라붙는다.
  - **워터마크·글자를 이미지에 넣지 않는다**(2026-10-19 사용자 지시 "이미지 생성에 워터 마크 생성하지 마라"). 제공자 요청에 금지 목록(`FORBIDDEN_IN_IMAGE`)과
    `no_watermark=True`를 싣고, 검수 항목에 `NO_WATERMARK`를 요구한다. 이미지 안에 "AI 생성" 같은 표기를 **굽지 않는다** — AI 생성 표기는 이미지 밖(상품 설명·기록)에만
    두고(`LABEL_PLACEMENT`), 이미지 픽셀에는 어떤 글자·로고·서명도 넣지 않는다.
  - **대표(MAIN) 이미지도 만들 수 있다**(2026-10-19 사용자 결정 — "대표도 모든 배경·포즈 허용"). 이는 같은 날 앞서 권고대로 정한 "대표는 흰 배경만"을 사용자가
    뒤집은 것이다. 그래서 대표 요청에는 (1) 쿠팡 대표 이미지 규격·AI 생성 이미지 정책이 확인되지 않아 **반려될 수 있다는 경고**, (2) 대표 규격 검수 항목
    (`MAIN_IMAGE_SPEC`), (3) **사용자 최종 확인 필요 표시**(`requires_user_confirmation`)가 자동으로 붙는다. 이 표시를 읽고 실제로 막는 일은 호출하는 쪽(R3)의 몫이다.
    컨셉 명세(`image_concepts.py`)의 "L2 컨셉은 대표에 쓸 수 없다" 규칙은 그대로이며 이 모듈과 별개다(이 모듈은 컨셉을 쓰지 않는다).
  - 권리: 참고 사진이 RIGHTS_DENIED면 막는다. RIGHTS_UNVERIFIED(영상 속 "저작권 문제 없다"는 근거 없는 주장과 같은 상태)는 사용자 확인(경고 확인 기록)이
    있을 때만 통과한다 — 확인 기록 조회는 호출하는 쪽(서비스)이 하고, 이 모듈은 그 결과(`rights_acknowledged`)만 받는다.
  - 같은 요청 지문이면 같은 결과 계획이 나온다(멱등). 참고 사진이 바뀌면(바이트 해시) 지문이 달라진다.
=========================================================
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.core.exceptions import BadRequestException
from app.domains.media_asset.constants import ALLOWED_MIME_TYPES
from app.domains.media_asset.constants import MAX_IMAGE_FILE_SIZE_BYTES
from app.domains.media_asset.image_concepts import CHECK_MAIN_IMAGE_SPEC
from app.domains.media_asset.image_concepts import PURPOSE_ADDITIONAL
from app.domains.media_asset.image_concepts import PURPOSE_DETAIL
from app.domains.media_asset.image_concepts import PURPOSE_MAIN
from app.domains.media_asset.providers import ImageGenerationRequest
from app.domains.media_asset.providers import ImageGenerationRequestItem

REFERENCE_GENERATION_VERSION = 1

# 영상은 썸네일 3장을 예로 들었다. 비용·검수 부담을 생각해 한 요청의 상한을 낮게 고정한다.
MAX_VARIANTS_PER_REQUEST = 6
MAX_BACKGROUNDS = 3
MAX_POSES = 3

# 실제 제공자는 아직 없다. 늘리려면 별도 승인 + 이 값과 시험을 함께 바꾼다.
ALLOWED_PROVIDER_CODES = ("FAKE",)

ALLOWED_PURPOSES = (PURPOSE_MAIN, PURPOSE_ADDITIONAL, PURPOSE_DETAIL)

RIGHTS_VERIFIED = "VERIFIED"
RIGHTS_UNVERIFIED = "RIGHTS_UNVERIFIED"
RIGHTS_DENIED = "RIGHTS_DENIED"

# 이 기능으로 만든 이미지가 반드시 지나야 하는 검수 항목(자동 실행은 후속 단계 — 여기서는 "요구"만 선언한다).
REQUIRED_REVIEW_CHECKS = (
    "PRODUCT_MATCHES_REFERENCE",   # 옷의 색·모양·무늬·로고가 참고 사진과 같은가 (다르면 표시·광고 위반 소지)
    "NO_TEXT_OVERLAY",             # 이미지 안에 글자·공급처 표시가 없는가
    "NO_WATERMARK",                # 워터마크·로고·서명이 없는가 (참고 사진에 있던 것이 따라 나온 경우 포함)
    "NO_THIRD_PARTY_MARKS",        # 다른 브랜드 로고·인물 식별 정보가 없는가
    "GENERATED_LABEL_ATTACHED",    # "AI 생성 이미지" 표기가 **이미지 밖**(상품 설명·기록)에 붙었는가
)
GENERATED_LABEL_KO = "AI 생성 이미지"
LABEL_PLACEMENT = "OUTSIDE_IMAGE"  # 표기는 이미지 밖에만 둔다 — 이미지 픽셀에 굽지 않는다
FORBIDDEN_IN_IMAGE = ("watermark", "logo", "text overlay", "caption", "signature", "stamp", "label")

# 대표(MAIN)에만 더해지는 요구 — 사용자가 대표도 허용했지만(2026-10-19) 정책 미확인 위험은 그대로 남아 있다.
MAIN_EXTRA_CHECKS = (CHECK_MAIN_IMAGE_SPEC,)
MAIN_POLICY_WARNING_KO = (
    "대표 이미지에 AI 생성 사진을 쓰면 쿠팡 대표 이미지 규격(흰 배경 권장)이나 AI 생성 이미지 정책에 맞지 않아 반려될 수 있습니다. "
    "정책은 아직 확인하지 못했습니다. 등록 전에 규격과 상품 일치 여부를 직접 확인하세요."
)

_MAGIC = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
)


class ReferenceGenerationError(BadRequestException):
    """참고 사진 생성 요청이 규칙에 맞지 않을 때."""


@dataclass(frozen=True)
class ScenePreset:
    code: str
    label_ko: str
    hint: str  # 제공자에 넘길 장면 설명(고정 문구 — 사용자가 쓴 글을 그대로 넘기지 않는다)


BACKGROUND_PRESETS: tuple[ScenePreset, ...] = (
    ScenePreset("CAFE", "카페", "bright cafe interior, soft daylight"),
    ScenePreset("WALKING_PATH", "산책로", "park walking path, natural light"),
    ScenePreset("STREET", "도로변", "city street sidewalk, daytime"),
    ScenePreset("SEASIDE", "바닷가", "seaside promenade, clear sky"),
    ScenePreset("HOME_INDOOR", "실내", "tidy home interior, warm light"),
    ScenePreset("STUDIO_PLAIN", "단색 스튜디오", "plain studio backdrop, even lighting"),
)

POSE_PRESETS: tuple[ScenePreset, ...] = (
    ScenePreset("KEEP_ORIGINAL", "원본 포즈 유지", "keep the original pose"),
    ScenePreset("ARMS_DOWN", "팔 내리기", "arms relaxed at sides"),
    ScenePreset("HAND_ON_HIP", "손 허리", "one hand on hip"),
    ScenePreset("BODY_TURN_LEFT", "좌측으로 몸 틀기", "body turned slightly to the left"),
    ScenePreset("BODY_TURN_RIGHT", "우측으로 몸 틀기", "body turned slightly to the right"),
    ScenePreset("WALKING", "걷는 자세", "mid-step walking pose"),
)

_BACKGROUND_BY_CODE = {preset.code: preset for preset in BACKGROUND_PRESETS}
_POSE_BY_CODE = {preset.code: preset for preset in POSE_PRESETS}


@dataclass(frozen=True)
class ReferenceImage:
    """검증을 통과한 참고 사진(바이트는 지문 계산에만 쓰고, 요청에 그대로 실린다)."""

    image_bytes: bytes
    mime_type: str
    sha256_hex: str
    rights_status: str
    rights_acknowledged: bool


@dataclass(frozen=True)
class Variant:
    sequence_index: int
    background: str
    pose: str


@dataclass(frozen=True)
class ReferenceGenerationPlan:
    version: int
    purpose: str
    variants: tuple[Variant, ...]
    request_fingerprint: str
    generated_label: str
    required_checks: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    requires_user_confirmation: bool = False

    def to_summary(self) -> dict:
        """화면·기록용 요약(참고 사진 바이트는 담지 않는다)."""
        return {
            "version": self.version, "purpose": self.purpose, "count": len(self.variants),
            "variants": [
                {"index": v.sequence_index, "background": v.background, "pose": v.pose} for v in self.variants
            ],
            "request_fingerprint": self.request_fingerprint, "generated_label": self.generated_label,
            "label_placement": LABEL_PLACEMENT, "no_watermark": True,
            "required_checks": list(self.required_checks),
            "warnings": list(self.warnings), "requires_user_confirmation": self.requires_user_confirmation,
        }


def list_presets() -> dict:
    """화면이 고를 수 있게 보여 주는 배경·포즈 목록."""
    return {
        "backgrounds": [{"code": p.code, "label_ko": p.label_ko} for p in BACKGROUND_PRESETS],
        "poses": [{"code": p.code, "label_ko": p.label_ko} for p in POSE_PRESETS],
        "max_variants": MAX_VARIANTS_PER_REQUEST,
    }


def _sniff_mime(image_bytes: bytes) -> str | None:
    for mime, magic in _MAGIC:
        if image_bytes.startswith(magic):
            return mime
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_reference_image(
    image_bytes: bytes,
    mime_type: str,
    rights_status: str,
    rights_acknowledged: bool,
) -> ReferenceImage:
    """참고 사진이 쓸 수 있는지 검사한다 — 형식·크기·실제 내용(매직 바이트)·권리. 하나라도 어긋나면 막는다."""

    if not isinstance(image_bytes, (bytes, bytearray)) or len(image_bytes) == 0:
        raise ReferenceGenerationError("참고 사진이 비어 있습니다.")
    if len(image_bytes) > MAX_IMAGE_FILE_SIZE_BYTES:
        raise ReferenceGenerationError("참고 사진이 너무 큽니다(최대 10MB).")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ReferenceGenerationError("참고 사진은 PNG·JPEG·WEBP만 쓸 수 있습니다.")
    sniffed = _sniff_mime(bytes(image_bytes))
    if sniffed != mime_type:
        raise ReferenceGenerationError("참고 사진의 실제 형식이 선언한 형식과 다릅니다.")

    if rights_status == RIGHTS_DENIED:
        raise ReferenceGenerationError("사용이 금지된 사진(권리 거부)은 참고 사진으로 쓸 수 없습니다.")
    if rights_status == RIGHTS_VERIFIED:
        pass
    elif rights_status == RIGHTS_UNVERIFIED:
        if rights_acknowledged is not True:
            raise ReferenceGenerationError(
                "사용 권리를 확인하지 못한 사진입니다. 사진 사용 권리 경고를 확인하신 뒤에만 참고 사진으로 쓸 수 있습니다.",
            )
    else:
        # 모르는 상태는 통과시키지 않는다(fail-closed).
        raise ReferenceGenerationError("사진의 사용 권리 상태를 알 수 없어 쓸 수 없습니다.")

    return ReferenceImage(
        image_bytes=bytes(image_bytes), mime_type=mime_type, sha256_hex=hashlib.sha256(bytes(image_bytes)).hexdigest(),
        rights_status=rights_status, rights_acknowledged=bool(rights_acknowledged),
    )


def _unique(codes) -> list[str]:
    seen: list[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return seen


def build_plan(
    reference: ReferenceImage,
    purpose: str,
    backgrounds: list[str] | tuple[str, ...],
    poses: list[str] | tuple[str, ...],
    count: int,
) -> ReferenceGenerationPlan:
    """배경 × 포즈 조합에서 `count`장을 정해진 순서로 뽑는다(같은 입력 → 같은 계획·같은 지문)."""

    if purpose not in ALLOWED_PURPOSES:
        raise ReferenceGenerationError(f"알 수 없는 이미지 용도입니다: {purpose}")

    background_codes, pose_codes = _unique(backgrounds), _unique(poses)
    if not background_codes or not pose_codes:
        raise ReferenceGenerationError("배경과 포즈를 각각 하나 이상 골라야 합니다.")
    if len(background_codes) > MAX_BACKGROUNDS or len(pose_codes) > MAX_POSES:
        raise ReferenceGenerationError(f"배경은 {MAX_BACKGROUNDS}개, 포즈는 {MAX_POSES}개까지만 고를 수 있습니다.")
    for code in background_codes:
        if code not in _BACKGROUND_BY_CODE:
            raise ReferenceGenerationError(f"알 수 없는 배경입니다: {code}")
    for code in pose_codes:
        if code not in _POSE_BY_CODE:
            raise ReferenceGenerationError(f"알 수 없는 포즈입니다: {code}")

    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ReferenceGenerationError("만들 사진 수는 1 이상의 정수여야 합니다.")
    if count > MAX_VARIANTS_PER_REQUEST:
        raise ReferenceGenerationError(f"한 번에 최대 {MAX_VARIANTS_PER_REQUEST}장까지만 만들 수 있습니다.")
    combos = [(b, p) for b in background_codes for p in pose_codes]
    if count > len(combos):
        raise ReferenceGenerationError(f"고른 배경·포즈 조합이 {len(combos)}개라 {count}장은 만들 수 없습니다(조합 수 이하로 정하세요).")

    is_main = purpose == PURPOSE_MAIN
    variants = tuple(Variant(index, b, p) for index, (b, p) in enumerate(combos[:count]))
    fingerprint_payload = {
        "version": REFERENCE_GENERATION_VERSION, "reference_sha256": reference.sha256_hex, "purpose": purpose,
        "variants": [[v.background, v.pose] for v in variants],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()

    return ReferenceGenerationPlan(
        version=REFERENCE_GENERATION_VERSION, purpose=purpose, variants=variants, request_fingerprint=fingerprint,
        generated_label=GENERATED_LABEL_KO,
        required_checks=REQUIRED_REVIEW_CHECKS + (MAIN_EXTRA_CHECKS if is_main else ()),
        warnings=(MAIN_POLICY_WARNING_KO,) if is_main else (), requires_user_confirmation=is_main,
    )


def to_provider_request(
    plan: ReferenceGenerationPlan,
    reference: ReferenceImage,
    company_id: int,
    product_candidate_id: int,
    provider_code: str = "FAKE",
) -> ImageGenerationRequest:
    """계획을 제공자 요청으로 바꾼다. 실제 제공자(FAKE 외)는 이 단계에서 거절한다."""

    if provider_code not in ALLOWED_PROVIDER_CODES:
        raise ReferenceGenerationError(
            "실제 이미지 생성 제공자는 아직 연결되지 않았습니다 — 연결은 가드 허용·비용 승인 뒤 별도 단계에서 합니다.",
        )

    style_params = {
        "kind": "REFERENCE_GENERATION", "version": plan.version, "generated_label": plan.generated_label,
        "preserve_product": True, "no_watermark": True, "forbidden_in_image": list(FORBIDDEN_IN_IMAGE),
        "label_placement": LABEL_PLACEMENT, "purpose": plan.purpose, "required_checks": list(plan.required_checks),
        "requires_user_confirmation": plan.requires_user_confirmation, "warnings": list(plan.warnings),
        "variants": [
            {
                "index": v.sequence_index,
                "background": v.background, "background_hint": _BACKGROUND_BY_CODE[v.background].hint,
                "pose": v.pose, "pose_hint": _POSE_BY_CODE[v.pose].hint,
            }
            for v in plan.variants
        ],
    }
    return ImageGenerationRequest(
        company_id=company_id, product_candidate_id=product_candidate_id,
        prompt_fingerprint=plan.request_fingerprint, style_params=style_params,
        items=tuple(ImageGenerationRequestItem(purpose=plan.purpose, sequence_index=v.sequence_index) for v in plan.variants),
        reference_image_bytes=reference.image_bytes, reference_mime_type=reference.mime_type,
    )


__all__ = [
    "REFERENCE_GENERATION_VERSION", "MAX_VARIANTS_PER_REQUEST", "MAX_BACKGROUNDS", "MAX_POSES", "ALLOWED_PROVIDER_CODES",
    "ALLOWED_PURPOSES", "REQUIRED_REVIEW_CHECKS", "MAIN_EXTRA_CHECKS", "MAIN_POLICY_WARNING_KO", "GENERATED_LABEL_KO", "LABEL_PLACEMENT", "FORBIDDEN_IN_IMAGE", "ReferenceGenerationError", "ScenePreset",
    "BACKGROUND_PRESETS", "POSE_PRESETS", "ReferenceImage", "Variant", "ReferenceGenerationPlan", "list_presets",
    "check_reference_image", "build_plan", "to_provider_request",
]
