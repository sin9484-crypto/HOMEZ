"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/service.py

2026-09-15 전면 감사 후속(Phase 9G, HOMEZ_USER_OPERATION_SETTINGS.md
10-4) — 상품 속성(이름/옵션/수량/사이즈/제조사/원산지) 정규화 비교와
자동 등록/자동 발주 차단 게이트.

호출부(자동 등록 파이프라인, 자동 발주 파이프라인)는 이미 각자
도메인에서 조회한 매입처·후보(candidate)·판매채널 값을 dict로
넘긴다 — 이 서비스 자체는 어떤 외부 조회도 하지 않는다(순수 비교·
저장·게이트).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.product_attribute_match.constants import AttributeMatchStatus
from app.domains.product_attribute_match.constants import ComparisonRunStatus
from app.domains.product_attribute_match.constants import ProductAttributeField
from app.domains.product_attribute_match.model import ProductAttributeComparisonItem
from app.domains.product_attribute_match.model import ProductAttributeComparisonRun
from app.domains.product_attribute_match.repository import (
    ProductAttributeMatchRepository,
)


def _normalize(value: str | None) -> str | None:
    """공백을 하나로 접고 앞뒤를 자른 뒤 대소문자를 없앤다 — 비교
    전용 정규화이며, 원본 값(대소문자·공백 포함)은 그대로 저장해
    화면에 보여준다. **문자열 유사도(편집거리 등)는 절대 쓰지
    않는다** — 정규화 후 완전히 같을 때만 MATCHED다."""

    if value is None:
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


def _compare_field(
    *, supplier_value: str | None, sales_channel_value: str | None,
    homez_current_value: str | None,
) -> str:

    values = {
        v for v in (
            _normalize(supplier_value),
            _normalize(sales_channel_value),
            _normalize(homez_current_value),
        ) if v is not None
    }

    if len(values) == 0:
        return AttributeMatchStatus.UNCONFIRMED

    non_none_count = sum(
        1 for v in (supplier_value, sales_channel_value, homez_current_value)
        if v is not None and v.strip() != ""
    )
    if non_none_count < 2:
        # 비교할 소스가 하나뿐이면(나머지는 아직 확인 안 됨) "같다"고
        # 판단할 근거가 없다 — 확인 불가로 본다.
        return AttributeMatchStatus.UNCONFIRMED

    return (
        AttributeMatchStatus.MATCHED if len(values) == 1
        else AttributeMatchStatus.MISMATCHED
    )


class ProductAttributeMatchService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = ProductAttributeMatchRepository(db)

    def run_comparison(
        self, *, company_id: int, product_identifier: str,
        connection_id: int | None = None,
        supplier_values: dict[str, tuple[str | None, str | None, datetime | None]],
        sales_channel_values: dict[str, tuple[str | None, str | None, datetime | None]],
        homez_current_values: dict[str, tuple[str | None, str | None, datetime | None]],
        triggered_by: int | None = None,
    ) -> ProductAttributeComparisonRun:
        """세 dict는 모두 `{field_name: (value, source_label,
        confirmed_at)}` 형태다(값이 없는 필드는 키 자체를 생략하거나
        (None, None, None)을 넣어도 된다). ProductAttributeField.ALL에
        속한 6개 필드 전부에 대해 항목을 만든다 — 호출부가 일부
        필드만 줘도 나머지는 UNCONFIRMED 항목으로 자동 채워진다(누락
        자체를 "확인 불가"로 정직하게 기록한다)."""

        if not product_identifier or not product_identifier.strip():
            raise BadRequestException("product_identifier가 비어 있습니다.")

        items: list[ProductAttributeComparisonItem] = []
        blocked = False

        for field in ProductAttributeField.ALL:
            s_value, s_source, s_at = supplier_values.get(field, (None, None, None))
            c_value, c_source, c_at = sales_channel_values.get(field, (None, None, None))
            h_value, h_source, h_at = homez_current_values.get(field, (None, None, None))

            status = _compare_field(
                supplier_value=s_value, sales_channel_value=c_value,
                homez_current_value=h_value,
            )
            if (
                status != AttributeMatchStatus.MATCHED
                and field in ProductAttributeField.REQUIRED_FOR_BLOCKING
            ):
                blocked = True

            items.append(ProductAttributeComparisonItem(
                field_name=field,
                supplier_value=s_value, supplier_source=s_source,
                supplier_confirmed_at=s_at,
                sales_channel_value=c_value, sales_channel_source=c_source,
                sales_channel_confirmed_at=c_at,
                homez_current_value=h_value, homez_current_source=h_source,
                homez_current_confirmed_at=h_at,
                match_status=status,
            ))

        run = ProductAttributeComparisonRun(
            company_id=company_id, product_identifier=product_identifier,
            connection_id=connection_id,
            overall_status=(
                ComparisonRunStatus.BLOCKED if blocked
                else ComparisonRunStatus.PASSED
            ),
            triggered_by=triggered_by,
            items=items,
        )

        return self.repository.add_run(run)

    def assert_attributes_confirmed_or_block(
        self, company_id: int, product_identifier: str,
    ) -> None:
        """자동 등록/자동 발주 직전에 호출한다. 이 상품에 대한 가장
        최근 비교 실행이 BLOCKED이고 아직 해소(resolve)되지 않았으면
        차단한다. 비교를 아예 한 번도 실행한 적이 없으면(레코드
        없음) 이 게이트 자체는 통과시킨다 — "비교를 실행하라"는
        별개의 정책이고, 이 메서드는 "이미 실행된 비교 결과"만
        본다."""

        if self.has_blocking_attribute_mismatch(company_id, product_identifier):
            latest = self.repository.get_latest_run_for_product(
                company_id, product_identifier,
            )
            raise ConflictException(
                f"상품({product_identifier}) 속성 비교에서 불일치/확인"
                "불가 항목이 있어 자동 등록·자동 발주를 진행하지 않습니다"
                f"(비교 실행 #{latest.id}) — 관리자가 값을 확인·선택한 "
                "뒤 처리해야 합니다.",
            )

    def has_blocking_attribute_mismatch(
        self, company_id: int, product_identifier: str,
    ) -> bool:
        """assert_attributes_confirmed_or_block()과 같은 판정을 예외
        대신 bool로 준다 — 이미 여러 블로커 코드를 문자열 목록으로
        모으는 호출부(listing_wizard_live_service.py::preflight())에서
        쓴다."""

        latest = self.repository.get_latest_run_for_product(
            company_id, product_identifier,
        )
        return (
            latest is not None
            and latest.overall_status == ComparisonRunStatus.BLOCKED
            and latest.resolved_at is None
        )

    def get_run(
        self, run_id: int, company_id: int,
    ) -> ProductAttributeComparisonRun:

        run = self.repository.get_run(run_id, company_id)
        if run is None:
            raise NotFoundException("상품 속성 비교 실행을 찾을 수 없습니다.")
        return run

    def list_runs(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ProductAttributeComparisonRun]:

        return self.repository.list_runs(company_id, status=status)

    def resolve_run(
        self, run_id: int, company_id: int, *, is_admin: bool,
        resolved_by: int, resolution_note: str,
        selected_values: dict[int, str],
    ) -> ProductAttributeComparisonRun:
        """불일치/확인불가로 판정된 모든 항목에 대해 사람이 직접 고른
        값(selected_values: {item_id: value})을 채워야 해소된다 —
        서버가 세 값 중 하나를 임의로 기본 선택하지 않는다."""

        if not is_admin:
            raise ForbiddenException(
                "상품 속성 비교 해소는 관리자만 가능합니다.",
            )

        run = self.get_run(run_id, company_id)
        if run.overall_status != ComparisonRunStatus.BLOCKED:
            raise BadRequestException(
                "차단 상태(BLOCKED)인 비교 실행만 해소할 수 있습니다.",
            )
        if run.resolved_at is not None:
            raise BadRequestException("이미 처리된 비교 실행입니다.")
        if not resolution_note or not resolution_note.strip():
            raise BadRequestException("처리 사유를 입력해야 합니다.")

        needs_selection = [
            item for item in run.items
            if item.match_status != AttributeMatchStatus.MATCHED
        ]
        missing = [
            item.id for item in needs_selection
            if not (selected_values.get(item.id) or "").strip()
        ]
        if missing:
            raise BadRequestException(
                "불일치/확인불가 항목 전부에 대해 선택값을 입력해야 "
                f"합니다(누락된 항목 id: {missing}).",
            )

        for item in needs_selection:
            item.selected_value = selected_values[item.id].strip()

        run.resolved_by = resolved_by
        run.resolution_note = resolution_note.strip()
        run.resolved_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(run)

        return run


def supplier_values_from_channel_lookup(
    result, *, source_label: str,
) -> dict[str, tuple[str | None, str | None, datetime | None]]:
    """2026-09-16 전면 감사 후속(10-4, Adapter 계약 확장) —
    `app.domains.purchase_task.channel_adapter.ProductLookupResult`를
    `run_comparison()`의 `supplier_values` 인자로 바로 쓸 수 있는
    dict로 바꾼다. 실제 매입처를 조회하는 호출부가 여럿(연결
    서비스의 `lookup_product()`, 발주 직전 게이트가 직접 쓰는
    adapter 호출)이라 이 변환 로직을 한 곳에만 둔다 — 각 호출부가
    각자 다시 구현하면 필드 하나를 빠뜨리는 식으로 어긋나기 쉽다.

    `title`(상품명)만 매입처가 실제로 채워주는 필드이므로 소스
    라벨을 붙여 확인된 값으로 취급하고, 나머지 6개(제조사/원산지/
    모델명/포장수량/규격/인증정보)는 `ChannelAttributeValue`가 이미
    갖고 있는 출처·확인시각을 그대로 옮긴다(값이 없으면 출처·
    확인시각도 전부 None — 추측으로 채우지 않는다)."""

    now = datetime.utcnow()

    def _attr(attr_value) -> tuple[str | None, str | None, datetime | None]:
        return (attr_value.value, attr_value.source, attr_value.confirmed_at)

    return {
        ProductAttributeField.NAME: (
            (result.title, source_label, now) if result.title else (None, None, None)
        ),
        ProductAttributeField.MANUFACTURER: _attr(result.manufacturer),
        ProductAttributeField.ORIGIN_COUNTRY: _attr(result.origin_country),
        ProductAttributeField.MODEL_NAME: _attr(result.model_name),
        ProductAttributeField.QUANTITY: _attr(result.package_quantity),
        ProductAttributeField.SIZE: _attr(result.size_specification),
        ProductAttributeField.CERTIFICATION_IDENTIFIERS: _attr(
            result.certification_identifiers,
        ),
    }


__all__ = ["ProductAttributeMatchService", "supplier_values_from_channel_lookup"]
