"""Coupang category metadata contract used by listing step 5.

The live provider is deliberately injected at the API boundary.  This module
contains no credential access and performs no network calls, which keeps the
validation and fingerprint rules deterministic and testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NoticeFieldDefinition:
    key: str
    label: str
    required: bool = True
    allow_not_applicable: bool = False


@dataclass(frozen=True)
class PurchaseOptionAttribute:
    """
    2026-08-29 V7 종합 감사 Phase 4(V7-COUPANG-META-001 수정) — 쿠팡
    Category Metadata Query 공식 응답의 `attributes[]` 전체 계약.
    이전에는 `NoticeFieldDefinition`을 재사용해 `attributeTypeName`/
    `required`만 저장하고 `dataType`/`basicUnit`/`usableUnits`/
    `inputType`/`inputValues`/`groupNumber`/`exposed`는 전혀 저장하지
    않았다(2026-08-28 감사에서 코드로 확인된 결함) — 특히 `exposed`가
    없으면 "이 속성이 실제 구매옵션(EXPOSED)인지 검색전용옵션(NONE)
    인지"를 구분할 방법이 아예 없었다. 이 클래스는 공식 응답 필드를
    빠짐없이 그대로 보존한다(변환·추측 없음).
    """

    attribute_type_name: str
    data_type: str = ""  # STRING / NUMBER / DATE
    basic_unit: str = ""
    usable_units: tuple[str, ...] = ()
    required: bool = False
    input_type: str = ""  # INPUT / SELECT
    input_values: tuple[str, ...] = ()
    group_number: str = "NONE"
    # 공식 문서: EXPOSED(실제 구매옵션) / NONE(검색전용옵션). 이 값이
    # 없으면(과거처럼) "구매옵션 없음"과 "검색옵션만 있음"을 구분하지
    # 못해, 실제로는 필수 구매옵션이 있는데도 빈 것으로 오판할 수
    # 있다.
    exposed: bool = False


@dataclass(frozen=True)
class RequiredDocumentDefinition:
    """2026-08-29 Phase 4(V7-COUPANG-META-002 수정 일부) — Category
    Metadata 응답의 `requiredDocumentNames[]`. 이전에는 이 응답 자체를
    저장하지 않았다(코드로 확인된 결함)."""

    template_name: str
    # MANDATORY / OPTIONAL / MANDATORY_PARALLEL_IMPORTED /
    # MANDATORY_OVERSEAS_PURCHASED (공식 문서 그대로, 임의로 불리언화
    #하지 않는다 — 뒤 두 값은 조건부 필수라 단순 bool로 못 담는다)
    required: str = "OPTIONAL"


@dataclass(frozen=True)
class CertificationDefinition:
    """2026-08-29 Phase 4(V7-COUPANG-META-002 수정 일부) — Category
    Metadata 응답의 `certifications[]`. 이전에는 이 응답을 저장하지
    않고 제출 시 `NOT_REQUIRED` 하드코딩 기본값만 보냈다(코드로 확인된
    결함)."""

    certification_type: str
    name: str = ""
    data_type: str = "NONE"  # CODE / NONE
    required: str = "OPTIONAL"  # MANDATORY / RECOMMEND / OPTIONAL


@dataclass(frozen=True)
class CategoryMetadata:
    display_category_code: str
    display_category_name: str
    version: str
    notice_fields: tuple[NoticeFieldDefinition, ...]
    # 2026-08-29 Phase 4 — 타입을 NoticeFieldDefinition에서
    # PurchaseOptionAttribute로 교체(V7-COUPANG-META-001). 호출부가
    # dataclass.__dict__ 그대로 직렬화하는 기존 관례(router.py)는
    # 그대로 동작한다 — 키가 늘어날 뿐 기존 키(key/label/required)는
    # 유지하지 않으므로, 이 필드를 그대로 구독하던 코드는 새 필드명
    # (attribute_type_name 등)에 맞춰 갱신해야 한다(아래 provider와
    # 함께 이번 Phase에서 갱신함 — 하위 호환 깨짐을 의도적으로
    # 감수한다, 조용히 필드를 잃는 것보다 명확히 깨지는 편이 안전).
    purchase_option_fields: tuple[PurchaseOptionAttribute, ...] = ()
    required_documents: tuple[RequiredDocumentDefinition, ...] = ()
    certifications: tuple[CertificationDefinition, ...] = ()


class CategoryMetadataProvider(Protocol):
    def search(self, query: str) -> list[CategoryMetadata]: ...
    def get(self, display_category_code: str) -> CategoryMetadata: ...


def metadata_fingerprint(metadata: CategoryMetadata) -> str:
    payload = {
        "display_category_code": metadata.display_category_code,
        "display_category_name": metadata.display_category_name,
        "version": metadata.version,
        "notice_fields": [field.__dict__ for field in metadata.notice_fields],
        "purchase_option_fields": [
            field.__dict__ for field in metadata.purchase_option_fields
        ],
        # 2026-08-29 Phase 4 — required_documents/certifications을
        # 새로 저장하기 시작했으므로 fingerprint에도 포함한다. 이
        # 값들이 바뀌면(예: 카테고리가 요구하는 인증이 바뀜) 기존
        # 승인은 마땅히 무효화돼야 한다 — fingerprint에서 빠지면
        # 조용히 재검증을 건너뛰게 된다.
        "required_documents": [
            doc.__dict__ for doc in metadata.required_documents
        ],
        "certifications": [
            cert.__dict__ for cert in metadata.certifications
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def notice_input_fingerprint(
    category_code: str, metadata_version: str, metadata_hash: str,
    notice_information: dict,
) -> str:
    encoded = json.dumps(
        {
            "category_code": category_code,
            "metadata_version": metadata_version,
            "metadata_fingerprint": metadata_hash,
            "notice_information": notice_information,
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_notice_information(
    metadata: CategoryMetadata, notice_information: dict,
) -> list[str]:
    missing: list[str] = []
    definitions = {field.key: field for field in metadata.notice_fields}
    for key, field in definitions.items():
        if not field.required:
            continue
        value = notice_information.get(key)
        if value is None or not str(value).strip():
            missing.append(key)
        elif str(value).strip() == "NOT_APPLICABLE" and not field.allow_not_applicable:
            missing.append(key)
    return missing


def validate_purchase_options(
    field_definitions: list[dict], purchase_options: dict,
) -> list[str]:
    """
    2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 실제 백업 DB 증거
    (Wizard #6, 2026-08-26)에서 `purchase_options`가 Category
    Metadata의 실제 `attributeTypeName`/`inputType`/`inputValues`와
    전혀 연결되지 않은 채(원시 JSON 텍스트박스에 자유 입력) 저장된
    것을 확인했다. `field_definitions`는 `official_category_code`
    조회 시점의 `purchase_option_fields` 스냅샷(각 항목은
    attribute_type_name/input_type/input_values/required/exposed
    키를 가진 dict)이다 — exposed(EXPOSED)이면서 required(MANDATORY)인
    속성만 검사한다(exposed=NONE은 검색전용옵션이라 구매옵션이
    아니다). SELECT는 반드시 공식 inputValues 안의 값이어야 한다 —
    자유 텍스트를 그대로 허용하지 않는다.

    2026-09-24 실사용 중 발견 — 쿠팡 공식 문서(Category Metadata
    Query) 확인 결과 group_number가 "NONE"이 아닌 속성들은 "번들
    속성 그룹"으로, 그 그룹 중 하나만 채우면 된다(예: 이 상품의
    "개당 용량"·"개당 중량"은 같은 그룹 — 온채널 정보고시의
    "용량(중량) 또는 중량"과 동일한 양자택일 패턴). 이전 코드는 이
    관계를 무시하고 각 속성을 독립적으로 required 검사해, 그룹 내
    다른 속성을 이미 채웠는데도 나머지를 누락으로 잘못 표시했다.
    """

    filled_groups: set[str] = set()
    for field in field_definitions:
        if not field.get("exposed"):
            continue
        group = field.get("group_number")
        if not group or group == "NONE":
            continue
        name = field.get("attribute_type_name")
        value = purchase_options.get(name)
        if value is not None and str(value).strip():
            filled_groups.add(group)

    missing: list[str] = []
    for field in field_definitions:
        if not field.get("exposed"):
            continue
        name = field.get("attribute_type_name")
        value = purchase_options.get(name)
        has_value = value is not None and str(value).strip()
        if not has_value:
            group = field.get("group_number")
            group_satisfied = bool(group) and group != "NONE" and group in filled_groups
            if field.get("required") and not group_satisfied:
                missing.append(str(name))
            continue
        input_values = field.get("input_values") or []
        if field.get("input_type") == "SELECT" and input_values and str(value) not in input_values:
            missing.append(str(name))
            continue
        # 2026-08-29 — Section 2A("INPUT은 dataType·단위 계약에 맞게
        # 검증한다"). 공식 문서(Product Creation, 실제 예시 원문
        # 확인)의 attributes[] 계약은 단위가 있는 NUMBER 속성이면
        # attributeValueName 자체에 단위를 포함한다 — 공식 예시:
        # {"attributeTypeName": "수량", "attributeValueName": "1개"}.
        # 순수 숫자만 요구하면 실제 쿠팡 라이브 제출에서 거부된다(실제
        # 재현: "40"은 거부, "40개"는 통과 — 2026-08-29 Live 검증에서
        # 확인). usable_units가 있으면 값 끝에 그 단위 중 하나가 붙어
        # 있는지 먼저 확인하고, 없으면 그 접미사를 뗀 나머지가 숫자인지
        # 검사한다. usable_units가 비어 있으면(단위 없는 순수 NUMBER
        # 속성) 기존과 동일하게 전체 값이 숫자인지만 검사한다.
        if field.get("input_type") == "INPUT" and field.get("data_type") == "NUMBER":
            raw_value = str(value).strip()
            usable_units = field.get("usable_units") or []
            numeric_part = raw_value
            if usable_units:
                matched_unit = next(
                    (u for u in usable_units if raw_value.endswith(u)), None,
                )
                if matched_unit:
                    numeric_part = raw_value[: -len(matched_unit)]
            try:
                float(numeric_part.strip())
            except ValueError:
                missing.append(str(name))
    return missing


def validate_saved_notice_contract(attributes: dict) -> list[str]:
    """Validate a persisted contract without trusting a confirmation checkbox."""
    required = (
        "official_category_code", "category_metadata_version",
        "category_metadata_fingerprint", "notice_information",
        "notice_confirmed_at", "notice_confirmed_by_user_id",
        "notice_input_fingerprint", "notice_required_field_keys",
    )
    missing = [key for key in required if not attributes.get(key)]
    if missing:
        return missing
    notice = attributes.get("notice_information")
    keys = attributes.get("notice_required_field_keys")
    if not isinstance(notice, dict) or not isinstance(keys, list):
        return ["notice_information"]
    empty = [key for key in keys if not str(notice.get(key, "")).strip()]
    expected = notice_input_fingerprint(
        str(attributes["official_category_code"]),
        str(attributes["category_metadata_version"]),
        str(attributes["category_metadata_fingerprint"]), notice,
    )
    if attributes.get("notice_input_fingerprint") != expected:
        empty.append("notice_input_fingerprint")
    return sorted(set(empty))


__all__ = [
    "CategoryMetadata", "CategoryMetadataProvider", "NoticeFieldDefinition",
    "PurchaseOptionAttribute", "RequiredDocumentDefinition",
    "CertificationDefinition",
    "metadata_fingerprint", "notice_input_fingerprint",
    "validate_notice_information", "validate_saved_notice_contract",
    "validate_purchase_options",
]
