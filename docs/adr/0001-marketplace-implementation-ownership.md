# ADR 0001 — Marketplace 구현체 소유권 정리

- 상태: 채택됨
- 날짜: 2026-07-31
- 작성 배경: 채널별 판매 방식 선택 기능(`app/domains/marketplace_listing`)
  독립 재감사 과정에서 요청됨

## Context

이 저장소에는 "marketplace"라는 이름을 공유하는 서로 다른 성숙도의
구현체가 4개 존재한다. 실제로 각 구현체가 무엇을 하고, 실제 앱에서
도달 가능한지 여부를 코드 확인으로 검증했다(2026-07-31, 아래 확인
방법 참고).

## 확인 방법

```
grep -rln "from app.marketplace\|import app.marketplace" app/
  → app/marketplace/{aliexpress,coupang,elevenst,factory,smartstore}.py
    (자기 자신들끼리만 참조)
  → app/service/marketplace_service.py, app/service/product_service.py

grep -rn "from app\.service\.\|import app\.service\." app/main.py app/api/ app/domains/
  → (결과 없음 — app/service/* 는 app.main·app.api·app.domains 어디서도
     import되지 않는다)

app/service/product_service.py 등 app/service/ 아래 여러 파일이
`from app.services.marketplace_service import MarketplaceService`를
import하는데, `app/services/`(복수형) 디렉터리 자체가 존재하지 않는다
— 이 경로는 실제로 실행하면 ModuleNotFoundError가 난다(경로 오탈자로
보이는 기존 드리프트). 실제 존재하는 파일은 `app/service/`(단수형)
아래의 marketplace_service.py이며, 이 파일만 `app.marketplace.factory`
를 정상적으로 import한다.
```

**결론**: `app/marketplace` → `app/service/marketplace_service.py` →
(그 외 `app/service/*.py` 다수, 일부는 존재하지 않는 모듈을 import해
자체적으로도 깨져 있음)로 이어지는 전체 체인이 `app/main.py`가
등록하는 어떤 라우터에서도 도달 불가능하다.

## Decision

| 구현체 | 역할 | 근거 |
|---|---|---|
| `app/domains/coupang` | **canonical-owner** — 쿠팡 상품 등록 파이프라인(초안·정책·수익성·Dry Run·최종 승인) | `coupang_router`/`coupang_policy_router`가 `app/main.py`에 등록되어 실제로 서빙됨 |
| `app/domains/marketplace` | **credential-owner (LEGACY, frozen)** | `marketplace_router`가 `app/main.py`에 등록되어 서빙되지만, 실제 homez.db에 0행 — 신규 필드·테이블·쓰기를 이 위에 추가하지 않는다. `api_key`/`api_secret` 평문 컬럼은 이번 재감사 범위 밖의 별도 보안 감사 대상으로만 플래그한다 |
| `app/marketplace` | **legacy-deprecated** | 위 확인 방법대로 어떤 라우터에서도 도달 불가능함을 재확인. 확장하지 않는다. 실제 삭제는 이 재감사의 범위가 아닌 별도 정리 작업으로 남긴다 |
| `app/service/*`(단수형, product_service.py 등) | **legacy-deprecated, 일부 깨짐** | `app/main.py`/`app/api`/`app/domains` 어디서도 import되지 않음. 일부 파일은 존재하지 않는 `app.services.marketplace_service`(복수형)를 import해 실행 시 ModuleNotFoundError가 난다 — 손대지 않는다 |
| `app/domains/marketplace_listing` | **canonical-owner** — 채널별 판매 방식 선택·자격·승인·제출 거버넌스. **external-adapter-owner** — `adapters/`(Coupang/Naver)가 `app/marketplace`의 미사용 스켈레톤을 대체한다 | `marketplace_listing_router`가 `app/main.py`에 등록되어 서빙됨, 52+ 신규 테스트로 검증됨 |

## Consequences

- 물리적 파일 이동·병합은 이 ADR로 수행하지 않는다 — 역할 배정만
  명시한다.
- 새로운 중복 Model이나 credential 저장소를 추가하지 않는다 — 이
  ADR 자체가 그 요구사항의 이행 수단이다(앞으로 "marketplace" 관련
  신규 코드를 어디에 둘지 이 표를 참고해 결정한다).
- `app/domains/marketplace`의 평문 credential 컬럼과 `app/marketplace`/
  `app/service/*`의 실제 삭제 여부는 별도 후속 작업으로 남는다(이
  재감사의 범위 밖).
