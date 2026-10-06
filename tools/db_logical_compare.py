"""
=========================================================
Homez OS

File : tools/db_logical_compare.py

SQLite 두 DB(원본과 백업 사본, 또는 백업 복원본)의 **논리적 동일성**을 읽기 전용으로 대조한다
(2026-10-06, 68차). 파일 바이트 SHA-256 일치는 데이터 정합성의 유일한 조건이 아니다 — 같은 데이터라도
백업 방식(온라인 백업 API, VACUUM INTO)에 따라 바이트가 달라질 수 있고, 반대로 바이트가 같아도 그
자체가 스키마·이력·관계의 정합성을 설명하지는 않는다. 그래서 다음을 함께 본다:

  1) 양쪽 `PRAGMA integrity_check` == ok, `PRAGMA foreign_key_check` 위반 수
  2) 스키마(`sqlite_master`의 type/name/tbl_name/sql) 동일
  3) `schema_migrations`의 (filename, checksum) 집합과 건수 동일(Migration 이력)
  4) 테이블별 행 수와 **행 내용 지문**(모든 열을 기본키/rowid 순서로 정렬해 SHA-256) 동일
  5) 논리 참조(FK 제약이 없는 이 저장소의 `*_id` 참조 관계)의 **고아 행 수** 동일

보고서에는 건수·지문·이름만 담고 **셀 값은 담지 않는다**(자격증명 참조 문자열·개인정보 보호). 두 DB는
`mode=ro` + `PRAGMA query_only=ON`으로만 연다 — 어떤 경우에도 쓰지 않는다.
=========================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# (자식 테이블, 자식 열, 부모 테이블, 부모 열) — 이 저장소는 대부분 FK 제약 없이 논리 참조를 쓴다.
DEFAULT_LOGICAL_REFERENCES = (
    ("users", "company_id", "companies", "id"),
    ("store_connections", "company_id", "companies", "id"),
    ("marketplace_accounts", "company_id", "companies", "id"),
    ("listing_wizards", "company_id", "companies", "id"),
    ("supplier_option_links", "store_connection_id", "store_connections", "id"),
    ("supplier_option_links", "purchase_connection_id", "purchase_channel_connections", "id"),
    ("order_items", "order_id", "orders", "id"),
    ("marketplace_submissions", "listing_id", "marketplace_listings", "id"),
    ("channel_action_requests", "store_connection_id", "store_connections", "id"),
)


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _table_digest(conn: sqlite3.Connection, table: str) -> tuple[int, str]:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    pk = [r[1] for r in sorted(conn.execute(f'PRAGMA table_info("{table}")'), key=lambda r: r[5]) if r[5]]
    order = ", ".join(f'"{c}"' for c in (pk or cols)) or "1"
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
        h.update(repr(tuple(row)).encode("utf-8", errors="surrogatepass"))
        h.update(b"\n")
        n += 1
    return n, h.hexdigest()


def _orphans(conn: sqlite3.Connection, child: str, ccol: str, parent: str, pcol: str) -> int | None:
    tables = set(_tables(conn))
    if child not in tables or parent not in tables:
        return None
    ccols = {r[1] for r in conn.execute(f'PRAGMA table_info("{child}")')}
    pcols = {r[1] for r in conn.execute(f'PRAGMA table_info("{parent}")')}
    if ccol not in ccols or pcol not in pcols:
        return None
    return conn.execute(
        f'SELECT COUNT(*) FROM "{child}" c WHERE c."{ccol}" IS NOT NULL AND NOT EXISTS '
        f'(SELECT 1 FROM "{parent}" p WHERE p."{pcol}" = c."{ccol}")').fetchone()[0]


def snapshot(path: Path, references=DEFAULT_LOGICAL_REFERENCES) -> dict:
    conn = open_readonly(path)
    try:
        tables = _tables(conn)
        digests = {}
        for t in tables:
            n, d = _table_digest(conn, t)
            digests[t] = {"rows": n, "digest": d}
        schema = [tuple(r) for r in conn.execute(
            "SELECT type, name, tbl_name, COALESCE(sql,'') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name")]
        migrations = None
        if "schema_migrations" in tables:
            migrations = sorted(tuple(r) for r in conn.execute(
                "SELECT filename, checksum FROM schema_migrations"))
        return {
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
            "schema_digest": hashlib.sha256(repr(schema).encode("utf-8")).hexdigest(),
            "schema_objects": len(schema),
            "migrations": migrations,
            "tables": digests,
            "orphans": {f"{c}.{cc}->{p}.{pc}": _orphans(conn, c, cc, p, pc)
                        for c, cc, p, pc in references},
        }
    finally:
        conn.close()


@dataclass
class Comparison:
    equal: bool
    differences: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)

    def report(self) -> dict:
        """셀 값을 담지 않는 요약(건수·지문·이름)."""

        def brief(s):
            return {
                "integrity": s["integrity"], "foreign_key_violations": s["foreign_key_violations"],
                "schema_objects": s["schema_objects"], "schema_digest": s["schema_digest"][:16],
                "migration_rows": None if s["migrations"] is None else len(s["migrations"]),
                "tables": len(s["tables"]), "total_rows": sum(v["rows"] for v in s["tables"].values()),
            }
        return {"equal": self.equal, "differences": self.differences,
                "source": brief(self.source), "target": brief(self.target)}


def compare(source: Path, target: Path, references=DEFAULT_LOGICAL_REFERENCES) -> Comparison:
    a, b = snapshot(source, references), snapshot(target, references)
    diffs: list[str] = []
    for side, s in (("원본", a), ("대상", b)):
        if s["integrity"] != "ok":
            diffs.append(f"{side} integrity_check != ok ({s['integrity'][:60]})")
    if a["foreign_key_violations"] != b["foreign_key_violations"]:
        diffs.append(f"foreign_key_check 위반 수 다름: {a['foreign_key_violations']} vs {b['foreign_key_violations']}")
    if a["schema_digest"] != b["schema_digest"]:
        diffs.append("스키마(sqlite_master) 다름")
    if a["migrations"] != b["migrations"]:
        ma = set(a["migrations"] or []); mb = set(b["migrations"] or [])
        diffs.append(f"Migration 이력 다름(원본만 {len(ma - mb)}건, 대상만 {len(mb - ma)}건)")
    for t in sorted(set(a["tables"]) | set(b["tables"])):
        ta, tb = a["tables"].get(t), b["tables"].get(t)
        if ta is None or tb is None:
            diffs.append(f"테이블 {t}: {'대상' if tb is None else '원본'}에 없음")
        elif ta["rows"] != tb["rows"]:
            diffs.append(f"테이블 {t}: 행 수 다름 {ta['rows']} vs {tb['rows']}")
        elif ta["digest"] != tb["digest"]:
            diffs.append(f"테이블 {t}: 행 내용 지문 다름(행 수 {ta['rows']} 동일)")
    for rel in sorted(set(a["orphans"]) | set(b["orphans"])):
        if a["orphans"].get(rel) != b["orphans"].get(rel):
            diffs.append(f"논리 참조 {rel}: 고아 행 수 다름 {a['orphans'].get(rel)} vs {b['orphans'].get(rel)}")
    return Comparison(not diffs, diffs, a, b)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SQLite DB 두 개의 논리적 동일성 읽기 전용 대조")
    ap.add_argument("source")
    ap.add_argument("target")
    args = ap.parse_args(argv)
    result = compare(Path(args.source), Path(args.target))
    print(json.dumps(result.report(), ensure_ascii=False, indent=2))
    return 0 if result.equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
