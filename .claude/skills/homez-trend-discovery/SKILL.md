---
name: homez-trend-discovery
description: HOMEZ Trend Discovery AI(app/domains/trend_discovery)의 점수 로직, Adapter 계약, Fixture 검증에서 사용한다.
---

# Trend Discovery AI

- `TrendSignal`(adapter.py) — Adapter 출력 계약. `FixtureTrendAdapter`만
  존재(외부 네트워크 미사용). 향후 실 API Adapter도 동일 계약을 만족하면
  된다.
- `TrendDiscoveryService.evaluate()` — 규칙·가중치를 클래스 상수로 명시
  분리한 결정론적 점수 계산. LLM 등 비결정론적 요소로 핵심 점수를 만들지
  않는다.
- 처리 케이스: 데이터 없음 / 금지상품(`is_banned_category`) / 데이터
  부족(`MIN_DATA_POINTS`=3 미만) / 오래된 데이터(`STALE_DATA_MAX_AGE_DAYS`
  =7일) / 중복(변화없는) 데이터 / 상승·하락 추세 / 점수 클리핑([0,1]).
- 실제 구매·상품 등록을 실행하지 않는다 — 점수와 근거만 반환.

테스트: `tests/test_trend_discovery.py` (12개, 결정론적 재실행 포함).
