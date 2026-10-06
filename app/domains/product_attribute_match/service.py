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

import hashlib
import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.product_attribute_match.constants import AttributeMatchStatus
from app.domains.product_attribute_match.constants import AttributeResolutionContract
from app.domains.product_attribute_match.constants import ComparisonRunStatus
from app.domains.product_attribute_match.constants import ProductAttributeField
from app.domains.product_attribute_match.constants import RegistrationBinding
from app.domains.product_attribute_match.constants import SupplierSourceCheck
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


def _is_unconfirmed_placeholder(value: str | None) -> bool:
    """"미확인"·"미제공"처럼 확인하지 못했다는 뜻의 자리표시자인지. 빈 문자열,
    "인증대상 아님"·"없음" 같은 실제 내용을 말하는 값과는 서로 다른 값이다."""

    if value is None:
        return False
    normalized = _normalize(value)
    compact = "".join(normalized.split()) if normalized else ""
    return compact in AttributeResolutionContract.UNCONFIRMED_PLACEHOLDERS


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
        return self._is_effectively_blocking(latest)

    # ------------------------------
    # 해소의 유효 범위: 같은 비교 내용일 때만 과거 해소를 이어 쓴다
    # ------------------------------

    def comparison_fingerprint(self, run: ProductAttributeComparisonRun) -> str:
        """해소 전제를 이루는 비교 내용의 지문. 회사·매입처 연결·상품 식별자와
        **차단 필드 6개**의 매입처/판매채널/HOMEZ 값(정규화 후), 그리고 이 계약의
        버전·차단 필드 목록이 들어간다(지문은 저장하지 않고 항목에서 그때그때
        계산하므로, 규칙 변경에 대한 보호는 `_resolution_covers_current_rules`가
        맡는다). 출처·확인시각·run id는 넣지 않는다(조회할
        때마다 달라지기 때문). 공급처 값 하나가 같다는 것만으로는 지문이 같지
        않다 — 세 소스의 차단 필드 값이 모두 같아야 한다. 비차단 필드(모델명·인증정보)는
        해소 판단에 쓰이지 않으므로 넣지 않는다."""

        return self._fingerprint(run, include_registration_side=True)

    def supplier_fingerprint(self, run: ProductAttributeComparisonRun) -> str:
        """`comparison_fingerprint`에서 공급처 값만 본 지문(판매채널·HOMEZ 값 제외)."""

        return self._fingerprint(run, include_registration_side=False)

    def _fingerprint(
        self, run: ProductAttributeComparisonRun, *, include_registration_side: bool,
    ) -> str:

        by_field = {item.field_name: item for item in run.items}
        values = []
        for field in ProductAttributeField.REQUIRED_FOR_BLOCKING:
            item = by_field.get(field)
            row = [field, _normalize(item.supplier_value) if item else None]
            if include_registration_side:
                row.append(_normalize(item.sales_channel_value) if item else None)
                row.append(_normalize(item.homez_current_value) if item else None)
            values.append(row)
        payload = {
            "contract": AttributeResolutionContract.VERSION,
            "company_id": run.company_id,
            "connection_id": run.connection_id,
            "product_identifier": run.product_identifier,
            "blocking_fields": list(ProductAttributeField.REQUIRED_FOR_BLOCKING),
            "side": "full" if include_registration_side else "supplier",
            "values": values,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()

    def _has_no_registration_side(self, run: ProductAttributeComparisonRun) -> bool:
        """공급처 조회만으로 만들어진 run(판매채널·HOMEZ 값이 차단 필드에 하나도
        없음) — 등록 내용에 대해서는 아무것도 주장하지 않는다."""

        return all(
            item.sales_channel_value is None and item.homez_current_value is None
            for item in run.items
            if item.field_name in ProductAttributeField.REQUIRED_FOR_BLOCKING
        )

    def _compatible_with_resolution(
        self, run: ProductAttributeComparisonRun,
        resolved: ProductAttributeComparisonRun,
    ) -> bool:
        """`run`의 비교 내용이 `resolved`가 해소한 내용과 같은가. 공급처 값이 같아야
        하고, 등록 쪽(판매채널·HOMEZ) 값은 ① `run`이 공급처 조회만 한 기록이면
        (등록 내용에 대해 아무 주장도 하지 않으므로) 따지지 않고, ② 값이 있으면
        해소된 run과 같아야 한다. 등록 내용이 해소 근거와 일치하는지는 최종 점검이
        현재 payload로 따로 확인한다(`check_registration_binding`)."""

        if self.supplier_fingerprint(run) != self.supplier_fingerprint(resolved):
            return False
        return (
            self._has_no_registration_side(run)
            or self.comparison_fingerprint(run) == self.comparison_fingerprint(resolved)
        )

    def find_covering_resolution(
        self, run: ProductAttributeComparisonRun | None,
    ) -> ProductAttributeComparisonRun | None:
        """미해소 BLOCKED run이 **같은 비교 내용의 이전 해소**로 덮이는지 찾는다.
        같은 범위(회사·상품·매입처 연결)의 이전 기록을 최신 순으로 보며, 지문이
        다른 기록을 만나면 그 사이에 내용이 바뀐 것이므로 덮이지 않는다(새 검토
        대상). 지문이 같은 미해소 기록은 건너뛰고, 지문이 같은 해소 기록을 만나면
        그 기록을 돌려준다. 어떤 행도 수정하지 않는다 — 해소자·근거·시각은 원래
        해소 run에만 남아 있다."""

        if (
            run is None
            or run.overall_status != ComparisonRunStatus.BLOCKED
            or run.resolved_at is not None
        ):
            return None

        # 가장 최근의 해소 run을 만날 때까지 거슬러 올라가며, 그 사이의 모든 run(과
        # 이 run)이 그 해소와 같은 내용이어야 한다. 가장 최근 해소가 이 내용과 다르면
        # 더 오래된 해소로 넘어가지 않는다(마지막 사람의 판단이 우선).
        between = [run]
        for earlier in self.repository.list_earlier_runs_in_scope(run):
            if (
                earlier.overall_status == ComparisonRunStatus.BLOCKED
                and earlier.resolved_at is not None
            ):
                # 해소 전제 확인: 지금 규칙상 선택값이 필요한 모든 항목에 그 해소가
                # 실제로 선택값을 남겼어야 한다(차단 필드 목록이 넓어졌는데 그 항목을
                # 확인한 적이 없는 해소는 이어 쓰지 않는다).
                if self._resolution_covers_current_rules(earlier) and all(
                    self._compatible_with_resolution(candidate, earlier)
                    for candidate in between
                ):
                    return earlier
                return None
            between.append(earlier)
        return None

    @staticmethod
    def _resolution_covers_current_rules(
        resolved_run: ProductAttributeComparisonRun,
    ) -> bool:

        return all(
            (item.selected_value or "").strip()
            for item in resolved_run.items
            if item.match_status != AttributeMatchStatus.MATCHED
            and item.field_name in ProductAttributeField.REQUIRED_FOR_BLOCKING
        )

    def _is_effectively_blocking(
        self, run: ProductAttributeComparisonRun | None,
    ) -> bool:

        return (
            run is not None
            and run.overall_status == ComparisonRunStatus.BLOCKED
            and run.resolved_at is None
            and self.find_covering_resolution(run) is None
        )

    # ------------------------------
    # 해소와 실제 등록 내용의 연결
    # ------------------------------

    def _basis_of(
        self, run: ProductAttributeComparisonRun,
    ) -> ProductAttributeComparisonRun | None:
        """이 run이 지금 '문제없음'으로 서 있는 근거가 되는 run — 통과했거나 사람이
        해소한 run 자신, 또는 같은 내용으로 덮어 주는 이전 해소 run. 근거가 없으면
        (실제로 차단 중이면) None."""

        if run.overall_status == ComparisonRunStatus.PASSED or run.resolved_at is not None:
            return run
        return self.find_covering_resolution(run)

    def check_registration_binding(
        self, company_id: int, source_reference: str | None,
        current_values: dict[str, str | None],
    ) -> tuple[str, list[str]]:
        """공급처 상품의 비교가 통과·해소로 서 있을 때, 그 근거 run에 기록된 **HOMEZ 값**이
        지금 등록하려는 값(`current_values`: 최종 payload에서 뽑은 차단 필드 6개)과
        같은지 본다. 같지 않으면(공급처 조회만 해소한 기록처럼 HOMEZ 값이 비어 있는
        경우 포함) 그 해소는 현재 등록 내용을 검토한 것이 아니므로 UNBOUND다.

        이 메서드는 아무것도 저장하지 않는다(새 run도, 과거 기록 수정도 없다).
        근거가 되는 통과·해소 기록이 없으면 NOT_APPLICABLE — 비교 기록 자체가 없을 때
        통과시키는 기존 계약은 이 메서드의 범위가 아니다."""

        parsed = self.parse_supplier_source_reference(source_reference)
        if parsed is None:
            return RegistrationBinding.NOT_APPLICABLE, []
        mall_code, product_code = parsed

        from app.domains.purchase_task.model import PurchaseChannelConnection

        connection_ids = [
            row[0] for row in self.repository.db.query(
                PurchaseChannelConnection.id,
            ).filter(
                PurchaseChannelConnection.company_id == company_id,
                PurchaseChannelConnection.mall_code == mall_code,
            ).all()
        ]
        mismatched: list[str] = []
        had_basis = False
        for latest in self.repository.get_latest_runs_per_connection(
            company_id, product_code, connection_ids,
        ):
            basis = self._basis_of(latest)
            if basis is None:
                continue
            had_basis = True
            recorded = {item.field_name: item for item in basis.items}
            for field in ProductAttributeField.REQUIRED_FOR_BLOCKING:
                item = recorded.get(field)
                recorded_value = _normalize(item.homez_current_value) if item else None
                if recorded_value != _normalize(current_values.get(field)):
                    if field not in mismatched:
                        mismatched.append(field)
        if not had_basis:
            return RegistrationBinding.NOT_APPLICABLE, []
        if mismatched:
            return RegistrationBinding.UNBOUND, mismatched
        return RegistrationBinding.BOUND, []

    def record_registration_comparison(
        self, company_id: int, source_reference: str | None,
        registration_values: dict[str, str | None],
        *, triggered_by: int | None = None,
    ) -> ProductAttributeComparisonRun:
        """지금 등록하려는 내용(HOMEZ 값)과 **이미 저장된 공급처 조회 값**을 한 run에
        담아 새로 기록한다 — 사람이 이 run을 해소하면 그 해소가 등록 내용과 이어진다.
        새 외부 조회는 하지 않고, 과거 run은 수정하지 않는다(항상 새 run 추가).
        공급처 조회 기록이 없거나(먼저 조회 필요) 같은 상품의 최신 기록이 둘 이상의
        매입처 연결에 걸쳐 있어 어느 것인지 불명확하면 만들지 않는다."""

        parsed = self.parse_supplier_source_reference(source_reference)
        if parsed is None:
            raise BadRequestException(
                "후보의 공급처 상품 식별(매입처:상품코드)을 읽을 수 없어 비교를 기록할 수 없습니다.",
            )
        mall_code, product_code = parsed

        from app.domains.purchase_task.model import PurchaseChannelConnection

        connection_ids = [
            row[0] for row in self.repository.db.query(
                PurchaseChannelConnection.id,
            ).filter(
                PurchaseChannelConnection.company_id == company_id,
                PurchaseChannelConnection.mall_code == mall_code,
            ).all()
        ]
        latest_runs = self.repository.get_latest_runs_per_connection(
            company_id, product_code, connection_ids,
        )
        if not latest_runs:
            raise BadRequestException(
                "이 공급처 상품의 조회 기록이 없습니다 — 공급처 조회를 먼저 실행해야 "
                "비교를 기록할 수 있습니다.",
            )
        if len(latest_runs) > 1:
            raise BadRequestException(
                "같은 공급처 상품의 비교 기록이 여러 매입처 연결에 걸쳐 있어 어느 "
                "연결 기준으로 기록할지 불명확합니다.",
            )
        source_run = latest_runs[0]

        supplier_values = {
            item.field_name: (
                item.supplier_value, item.supplier_source, item.supplier_confirmed_at,
            )
            for item in source_run.items if item.supplier_value is not None
        }
        homez_values = {
            field: (value, "위저드 최종 등록 내용", None)
            for field, value in registration_values.items() if value is not None
        }
        return self.run_comparison(
            company_id=company_id, product_identifier=source_run.product_identifier,
            connection_id=source_run.connection_id,
            supplier_values=supplier_values, sales_channel_values={},
            homez_current_values=homez_values, triggered_by=triggered_by,
        )

    def describe_run(self, run: ProductAttributeComparisonRun) -> dict:
        """화면·API용 파생 정보(저장하지 않음): 이 run이 이전 해소로 덮이는지,
        지금 실제로 차단 중인지, 해소와 별개로 미확인인 비차단 필드."""

        covering = self.find_covering_resolution(run)
        return {
            "covered_by_run_id": covering.id if covering is not None else None,
            "blocking_active": (
                run.overall_status == ComparisonRunStatus.BLOCKED
                and run.resolved_at is None and covering is None
            ),
            "non_blocking_unconfirmed_fields": [
                item.field_name for item in run.items
                if item.field_name not in ProductAttributeField.REQUIRED_FOR_BLOCKING
                and item.match_status != AttributeMatchStatus.MATCHED
            ],
        }

    @staticmethod
    def _known_supplier_mall_codes() -> frozenset[str]:
        """후보의 공급처 표기로 인정하는 매입처 코드. `OTHER`는 특정 공급처를
        가리키지 않는 범용 값이라 제외한다(일반 문장이 공급처로 오인되지 않게)."""

        from app.domains.purchase_task.constants import PurchaseChannelMallCode

        return frozenset(
            code.upper() for code in PurchaseChannelMallCode.ALL
            if code != PurchaseChannelMallCode.OTHER
        )

    @staticmethod
    def parse_supplier_source_reference(
        source_reference: str | None,
    ) -> tuple[str, str] | None:
        """후보의 `source_reference`("ONCHANNEL:CH1147184" 형태 =
        매입처 코드:상품 코드)를 (매입처, 상품 코드)로 나눈다. 형태가
        아니면 None — 공급처 정체성을 알 수 없다는 뜻이다.

        허용하는 정규화는 **공백 제거**와 **매입처 코드의 대소문자**(매입처 코드는
        서버가 정한 대문자 코드 집합이라 같은 코드로 본다)뿐이다. 상품 코드는
        대소문자를 구분하며 그대로 둔다."""

        if not source_reference or ":" not in source_reference:
            return None
        mall_code, _, product_code = source_reference.partition(":")
        mall_code, product_code = mall_code.strip(), product_code.strip()
        if not mall_code or not product_code:
            return None
        if mall_code.upper() in ProductAttributeMatchService._known_supplier_mall_codes():
            mall_code = mall_code.upper()
        return mall_code, product_code

    def check_supplier_source(
        self, company_id: int, source_reference: str | None,
    ) -> str:
        """후보가 공급처 상품에서 왔을 때(`source_reference`) 그 공급처 상품의 속성
        비교 상태를 `SupplierSourceCheck` 값으로 돌려준다.

        * NOT_APPLICABLE: 공급처를 가리키는 표기가 없다(빈 값, 알려진 매입처 코드로
          시작하지 않는 일반 문자열) — 이 점검 대상이 아니다.
        * IDENTIFIER_UNREADABLE: 알려진 매입처 코드로 시작하는데 `매입처:상품코드`로
          읽을 수 없다("ONCHANNEL", "ONCHANNEL:", "ONCHANNEL CH1147184" 등). 공급처
          상품임은 분명하므로 "차단 기록 없음"으로 보지 않고 fail-closed로 알린다.
        * BLOCKED / CLEAR: 식별 가능 — **같은 회사의 해당 매입처 연결에 묶인 기록만**
          연결별 최신 1건씩 본다(연결이 없는 기록, 다른 회사, 다른 매입처의 같은 코드는
          섞이지 않는다). 하나라도 실제로 차단 중이면 BLOCKED.
        * IDENTIFIER_AMBIGUOUS: 정확히 같은 코드에는 차단이 없지만 대소문자만 다른
          코드(같은 회사·같은 매입처 연결)에 실제 차단이 있다. 상품 코드의 대소문자를
          임의로 합치지 않으므로 같은 상품으로 단정하지 않고, 모호함 자체를 알린다."""

        reference = (source_reference or "").strip()
        if not reference:
            return SupplierSourceCheck.NOT_APPLICABLE

        leading = re.match(r"[A-Za-z0-9_]+", reference)
        if (
            leading is None
            or leading.group(0).upper() not in self._known_supplier_mall_codes()
        ):
            return SupplierSourceCheck.NOT_APPLICABLE

        parsed = self.parse_supplier_source_reference(reference)
        if parsed is None or parsed[0] != leading.group(0).upper():
            return SupplierSourceCheck.IDENTIFIER_UNREADABLE
        mall_code, product_code = parsed

        from app.domains.purchase_task.model import PurchaseChannelConnection

        connection_ids = [
            row[0] for row in self.repository.db.query(
                PurchaseChannelConnection.id,
            ).filter(
                PurchaseChannelConnection.company_id == company_id,
                PurchaseChannelConnection.mall_code == mall_code,
            ).all()
        ]
        runs = self.repository.get_latest_runs_per_connection(
            company_id, product_code, connection_ids,
        )
        if any(self._is_effectively_blocking(run) for run in runs):
            return SupplierSourceCheck.BLOCKED

        variants = self.repository.get_latest_runs_for_case_variants(
            company_id, product_code, connection_ids,
        )
        if any(self._is_effectively_blocking(run) for run in variants):
            return SupplierSourceCheck.IDENTIFIER_AMBIGUOUS
        return SupplierSourceCheck.CLEAR

    def has_blocking_mismatch_for_supplier_source(
        self, company_id: int, source_reference: str | None,
    ) -> bool:
        """`check_supplier_source()`가 BLOCKED인지만 bool로 준다. 식별자를 읽지
        못한 경우(UNREADABLE/AMBIGUOUS)는 별도 결과이므로 호출부는
        `check_supplier_source()`를 직접 써서 그 둘을 놓치지 않아야 한다."""

        return (
            self.check_supplier_source(company_id, source_reference)
            == SupplierSourceCheck.BLOCKED
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

        # 선택값이 반드시 필요한 것은 차단 필드(REQUIRED_FOR_BLOCKING) 6개의
        # 미일치·미확인 항목뿐이다. 모델명·인증정보 같은 비차단 항목은 미확인이어도
        # 해소를 막지 않으며 미확인 상태 그대로 남는다(값을 요구하거나 만들지 않음).
        needs_selection = [
            item for item in run.items
            if item.match_status != AttributeMatchStatus.MATCHED
            and item.field_name in ProductAttributeField.REQUIRED_FOR_BLOCKING
        ]
        missing = [
            item.id for item in needs_selection
            if not (selected_values.get(item.id) or "").strip()
        ]
        if missing:
            raise BadRequestException(
                "차단 항목 중 불일치/확인불가 항목 전부에 대해 선택값을 입력해야 "
                f"합니다(누락된 항목 id: {missing}).",
            )

        # "미확인"·"미제공" 같은 자리표시자는 확인한 값이 아니므로 선택값이 될 수
        # 없다. 사람이 입력한 값은 어느 항목이든(비차단 포함) 같은 규칙을 따른다.
        items_by_id = {item.id: item for item in run.items}
        placeholder = [
            item_id for item_id, value in selected_values.items()
            if item_id in items_by_id and _is_unconfirmed_placeholder(value)
        ]
        if placeholder:
            raise BadRequestException(
                "'미확인'·'미제공' 같은 확인하지 못했다는 뜻의 값은 선택값으로 쓸 수 "
                f"없습니다(항목 id: {placeholder}) — 확인한 값을 입력하세요.",
            )

        for item in needs_selection:
            item.selected_value = selected_values[item.id].strip()

        # 비차단 항목에 사람이 직접 값을 준 경우에만 기록한다(판정 상태는 그대로).
        needs_ids = {item.id for item in needs_selection}
        for item_id, value in selected_values.items():
            item = items_by_id.get(item_id)
            if (
                item is not None and item_id not in needs_ids
                and item.field_name not in ProductAttributeField.REQUIRED_FOR_BLOCKING
                and (value or "").strip()
            ):
                item.selected_value = value.strip()

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
