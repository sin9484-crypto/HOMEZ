---
name: homez-test-gate
description: HOMEZ 전체 회귀 테스트 실행, 테스트 수 확인, 완료 판정 전 게이트를 수행할 때 사용한다.
---

# HOMEZ Test Gate

## 전체 회귀 실행 명령

```
venv/Scripts/python -m unittest \
  tests/test_settlement_hardening.py \
  tests/test_v23_schema_migration.py \
  tests/test_automation_safety.py \
  tests/test_new_product_discovery.py \
  tests/test_trend_discovery.py \
  tests/test_product_candidate.py \
  tests/test_v24_v3_schema_migration.py -v
```

## 기대 테스트 수 (2026-07-28 하드닝 이후 기준)

| 파일 | 개수 |
|---|---|
| test_settlement_hardening.py | 17 |
| test_v23_schema_migration.py | 17 |
| test_automation_safety.py | 15 |
| test_new_product_discovery.py | 10 |
| test_trend_discovery.py | 12 |
| test_product_candidate.py | 12 |
| test_v24_v3_schema_migration.py | 16 |
| **합계** | **99** |

숫자가 다르면 원인을 분석하고(신규 테스트 누락/중복 실행/import 실패)
보고한다 — 임의로 숫자만 맞추지 않는다.

## 게이트 전 확인

1. 위 전체 스위트 `OK`
2. `homez.db` 크기·mtime 변경 없음
3. `app.main` import 성공(신규 라우터 등록이 다른 모듈을 깨지 않는지)
4. Whitelist 밖 파일 변경 없음(`git status`로 재확인)
5. 새로 발견된 Critical/High가 없거나, 있다면 이번 라운드에서 해결됨

이 다섯 가지가 전부 확인되기 전에는 `docs/HOMEZ_PROJECT_STATE.md`에
"V3 완료"로 기록하지 않는다.
