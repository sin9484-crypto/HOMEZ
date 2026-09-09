---
name: homez-new-product
description: HOMEZ New Product Discovery AI(app/domains/new_product_discovery)의 15일 신제품 기준, timezone 처리에서 사용한다.
---

# New Product Discovery AI

## 신제품 기준 (HOMEZ 확정 결정)

- 출시일(release_date) 기준 **0일 이상 15일 이하**(경계 포함)를 신제품으로
  본다.
- 기준 timezone은 **Asia/Seoul(KST)**. Windows 환경에 `tzdata` 패키지가
  없어 `zoneinfo.ZoneInfo("Asia/Seoul")`가 `ZoneInfoNotFoundError`를
  던질 수 있다(패키지 설치 금지 범위) — 한국은 서머타임이 없는 연중 고정
  UTC+9이므로 `timezone(timedelta(hours=9))`로 대체한다
  (`app/domains/new_product_discovery/service.py`의 `KST` 상수,
  `app/domains/automation_safety/service.py`도 동일 패턴 사용).
- 미래 날짜(`release_date > 오늘`)는 `NewProductEvaluationError`를 던진다
  (자동 보정 금지, 검토 필요로 취급).
- 날짜 불명(`release_date=None`)은 신제품으로 자동 판정하지 않는다.

## 테스트 포인트

당일(0일)/14일/15일(경계, True)/16일(경계, False)/미래 날짜(예외)/날짜
불명/timezone 경계(UTC 기준 어제가 KST로는 오늘인 케이스)/naive datetime
처리/결정론적 재실행/KST가 실제로 고정 UTC+9인지 자체 검증.

테스트: `tests/test_new_product_discovery.py` (10개).
