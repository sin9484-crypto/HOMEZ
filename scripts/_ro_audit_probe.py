# 임시 읽기 전용 감사 프로브 — Secret 미출력
from __future__ import annotations

import sqlite3

import app.main as m
from app.core.version import VERSION


def main() -> None:
    c = sqlite3.connect("file:homez.db?mode=ro", uri=True)
    tables = [
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
        )
    ]
    idxs = [
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    ]
    print("table_count", len(tables))
    for t in tables:
        print("T", t)
    print(
        "has_settlement_idx",
        "uq_funding_ledger_settlement_type" in idxs,
    )
    for n in (
        "auth_sessions",
        "product_candidates",
        "automation_mode_states",
        "coupang_policy_sets",
        "decision_policies",
        "marketplace_listings",
        "marketplace_channels",
    ):
        print(n, "YES" if n in tables else "NO")
    print("users_count", c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    c.close()

    routes = [r for r in m.app.routes if hasattr(r, "methods")]
    print("import_ok", True)
    print("routes", len(routes))
    print("version", VERSION)


if __name__ == "__main__":
    main()
