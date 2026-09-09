"""
=========================================================
Homez OS

File : app/domains/purchase_task/csv_import.py

작업 F — CSV로 구매처 주문번호·송장 정보 가져오기. 미리보기 후
확정, 컬럼 매핑, 중복 행 차단, Formula Injection 방어, 인코딩 오류
처리, 행별 성공·실패 보고를 전부 이 모듈에서 처리한다. 전체 성공으로
가장하지 않는다 — 반환값은 항상 행별 결과 목록이다.
=========================================================
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

REQUIRED_COLUMNS = (
    "purchase_task_id", "shopping_mall_code", "external_order_number",
    "actual_amount",
)
OPTIONAL_COLUMNS = (
    "actual_shipping_fee", "purchased_at", "selected_option_note", "memo",
)

# Excel/Google Sheets Formula Injection 방어 — 이 문자로 시작하는 셀
# 값은 수식으로 해석될 수 있어(=, +, -, @, tab, CR) 앞에 작은따옴표를
# 붙여 무력화한다(엑셀 표준 완화책).
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_formula(value: str) -> str:

    if value and value[0] in _FORMULA_TRIGGER_CHARS:
        return "'" + value
    return value


@dataclass
class CsvRowResult:

    row_number: int
    success: bool
    error: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class CsvImportPreview:

    header: list[str]
    row_count: int
    sample_rows: list[dict]
    column_mapping_ok: bool
    missing_columns: list[str]


def decode_csv_bytes(raw: bytes) -> str:
    """인코딩 오류 처리 — UTF-8(BOM 포함) 우선, 실패하면 CP949(EUC-KR
    계열, 한국에서 흔한 Excel 저장 인코딩)로 재시도한다. 둘 다
    실패하면 명확한 예외를 던진다(깨진 채로 조용히 진행하지 않는다)."""

    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "CSV 파일 인코딩을 확인할 수 없습니다(UTF-8/CP949 모두 실패) "
        "— UTF-8로 다시 저장한 뒤 업로드하세요.",
    )


def preview_csv(raw: bytes, *, sample_size: int = 5) -> CsvImportPreview:

    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []

    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    rows = list(reader)

    return CsvImportPreview(
        header=header, row_count=len(rows),
        sample_rows=[
            {k: _neutralize_formula(str(v or "")) for k, v in r.items()}
            for r in rows[:sample_size]
        ],
        column_mapping_ok=not missing, missing_columns=missing,
    )


def parse_csv_rows(raw: bytes) -> list[CsvRowResult]:
    """행별로 파싱만 한다(DB 접근 없음) — 실제 등록은 서비스가
    담당한다. 여기서는 형식 오류(필수값 누락·숫자 파싱 실패)만
    행별로 잡아낸다. 같은 파일 안에서의 중복 행(같은 mall+order#)도
    여기서 미리 차단한다."""

    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))

    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {', '.join(missing)}")

    results: list[CsvRowResult] = []
    seen_keys: set[tuple[str, str]] = set()

    for idx, row in enumerate(reader, start=2):  # 1행은 헤더
        try:
            task_id_raw = (row.get("purchase_task_id") or "").strip()
            mall = _neutralize_formula((row.get("shopping_mall_code") or "").strip())
            order_number = _neutralize_formula(
                (row.get("external_order_number") or "").strip(),
            )
            amount_raw = (row.get("actual_amount") or "").strip()

            if not task_id_raw or not mall or not order_number or not amount_raw:
                results.append(CsvRowResult(
                    row_number=idx, success=False,
                    error="필수값 누락(purchase_task_id/shopping_mall_code/"
                          "external_order_number/actual_amount)",
                ))
                continue

            key = (mall, order_number)
            if key in seen_keys:
                results.append(CsvRowResult(
                    row_number=idx, success=False,
                    error="같은 파일 안에서 중복된 (구매처, 주문번호) 행입니다.",
                ))
                continue
            seen_keys.add(key)

            task_id = int(task_id_raw)
            actual_amount = float(amount_raw)

            shipping_raw = (row.get("actual_shipping_fee") or "").strip()
            actual_shipping_fee = float(shipping_raw) if shipping_raw else None

            purchased_at_raw = (row.get("purchased_at") or "").strip()
            purchased_at = (
                datetime.fromisoformat(purchased_at_raw)
                if purchased_at_raw else datetime.utcnow()
            )

            results.append(CsvRowResult(
                row_number=idx, success=True,
                data={
                    "purchase_task_id": task_id,
                    "shopping_mall_code": mall,
                    "external_order_number": order_number,
                    "actual_amount": actual_amount,
                    "actual_shipping_fee": actual_shipping_fee,
                    "purchased_at": purchased_at,
                    "selected_option_note": _neutralize_formula(
                        (row.get("selected_option_note") or "").strip(),
                    ) or None,
                    "memo": _neutralize_formula(
                        (row.get("memo") or "").strip(),
                    ) or None,
                },
            ))
        except (ValueError, TypeError) as e:
            results.append(CsvRowResult(
                row_number=idx, success=False, error=f"형식 오류: {e}",
            ))

    return results


__all__ = [
    "REQUIRED_COLUMNS", "OPTIONAL_COLUMNS", "CsvRowResult", "CsvImportPreview",
    "decode_csv_bytes", "preview_csv", "parse_csv_rows",
]
