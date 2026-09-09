---
name: homez-operator-api
description: HOMEZ 운영자 승인 API(app/domains/product_candidate/router.py)와 관련 검증 방식에서 사용한다.
---

# 운영자 승인 API

`app/domains/product_candidate/router.py`, `app.main`에 등록됨(prefix
`/product-candidates`, `admin_guard` 필수).

## 엔드포인트

- `GET /product-candidates` — 목록(status/market 필터, skip/limit)
- `GET /product-candidates/{id}` — 상세
- `GET /product-candidates/{id}/evidence` — 근거 이력
- `GET /product-candidates/{id}/decisions` — 운영자 결정 이력
- `POST /product-candidates/{id}/approve|hold|reject` — 결정(memo 선택)

승인 API는 상품 등록·구매를 실행하지 않는다 — 상태 변경 + Event 생성만.
외부 마켓 등록 Adapter를 호출하지 않는다.

## 테스트 제약과 대안

이 환경에 `httpx`가 설치되어 있지 않아 FastAPI/Starlette `TestClient`를
쓸 수 없다(패키지 설치는 금지 범위). 대신:

1. `app.main.app` 전체 import 성공 여부(라우터 등록이 다른 모듈을 깨지
   않는지) 확인
2. 라우터 엔드포인트 함수를 ASGI 계층 없이 **직접 호출**(Depends 객체
   대신 실제 `db`/`current_user` 값을 인자로 직접 전달)해 서비스 위임과
   응답 형태를 검증

`tests/test_v24_v3_schema_migration.py`의
`ProductCandidateRouterOnMigratedSchemaTestCase`가 실제 Migration으로
만든 스키마 위에서 이 방식으로 목록/상세/근거/결정이력/승인/보류/거절
전체 경로를 검증한다.

`httpx`가 설치되면(향후, 사용자 승인 하에) 이 대체 검증을 실제
TestClient 기반으로 전환할 수 있다.
