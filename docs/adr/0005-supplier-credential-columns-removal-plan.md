# ADR 0005 — suppliers.api_key/api_secret 제거 계획

- 작성일: 2026-08-21 (CA-4, CTO 지시)
- 상태: 계획만 확정, **미실행**(운영 DB 적용은 별도 승인 대기)

## 배경

`app/domains/supplier/model.py::Supplier`는 전역 공개 공급처
디렉터리다(2026-08-20 CTO 정정 — `app/domains/source/model.py` 헤더
참고: "공개 식별정보만 전역 공유"). 그런데 이 모델에는 `api_key`/
`api_secret` 컬럼이 남아 있다 — 공개 디렉터리 원칙과 정면으로
충돌하는 설계 잔재다.

## 조사 결과 (CA-4)

- **쓰기 경로**: 이 두 컬럼을 받는 유일한 엔드포인트는
  `app/domains/supplier/router.py`다. 이 router는 `app/main.py`에
  마운트되지 않는다(`# supplier_router,` — 주석 처리). 유일하게
  이 router를 import하는 다른 파일(`app/api/router.py`)도 앱 어디
  에서도 import되지 않는 죽은 코드다. 즉 **이 컬럼을 채우는 도달
  가능한 HTTP 경로가 현재 전혀 없다**(코드로 검증됨 —
  `tests/test_supplier_legacy_schema_upgrade.py::
  SupplierCredentialFieldUnreachableTestCase`).
- **응답 노출**: `SupplierPublicDirectoryResponse`가 이 두 필드를
  선언하지 않는다 — 어떤 응답에도 절대 포함되지 않는다(테스트로
  검증됨).
- **기존 데이터**: 실제 운영 DB·개발 DB 둘 다 `suppliers` 행
  0건(2026-08-21 read-only 실측) — 마이그레이션 시점에 보존해야 할
  실제 Credential 데이터가 없다.

## 왜 지금 당장 컬럼을 제거하지 않는가

SQLite는 `ALTER TABLE ... DROP COLUMN`을 안전하게 지원하려면 버전
3.35+가 필요하고, 이 프로젝트는 최소 지원 SQLite 버전을 아직 확정
검증하지 않았다. 컬럼 제거의 표준 안전 기법은 "새 테이블 생성 →
데이터 복사 → 기존 테이블 삭제 → 이름 변경"인데, 이는 단순 ADD
COLUMN보다 훨씬 큰 작업 범위이자 운영 위험이다. 지시 원칙("SQLite
테이블 재구성이 필요하면 운영 적용은 승인 대기로 남긴다")에 따라
이번 라운드에는 실행하지 않는다.

## 제거 계획 (실행 대기)

향후 별도 승인 후 진행할 순서:

1. `Supplier` ORM 모델에서 `api_key`/`api_secret` 컬럼 선언 제거.
2. 신규 Migration(예: `2026MMDD_NN_rebuild_suppliers_drop_credential_
   columns.sql`) — 재구성 기법으로 두 컬럼만 제외한 새 `suppliers`
   테이블을 만들고 기존 행을 복사한 뒤 교체. 기존 인덱스(`ix_
   suppliers_code` UNIQUE, `ix_suppliers_is_active`, `ix_suppliers_
   name`, `ix_suppliers_id`)와 레거시 컬럼(company_id/business_
   number/ceo/phone/email/address/active)은 전부 그대로 유지한다.
3. 임시 SQLite 파일에서 재적용·재현성·integrity_check·기존 행 보존을
   먼저 검증(이 프로젝트의 기존 Migration 관례와 동일).
4. 이 시점 실제 운영 DB의 `suppliers` 행 수를 다시 실측 확인 —
   여전히 0건이면 데이터 손실 위험 자체가 없음을 재확인.
5. 사용자 명시 승인 후에만 운영 DB에 적용.
6. `app/domains/supplier/router.py`/`policy.py`의 마운트되지 않은
   레거시 CRUD 코드 자체를 완전히 삭제할지(계속 미마운트 상태로
   남길지)는 별도 CTO 판단 사항으로 남긴다 — 이 ADR은 컬럼 제거
   계획만 다룬다.

## 현재 완화 조치 (이미 적용됨, 2026-08-21)

- `Supplier.api_key`/`api_secret` 필드에 DEPRECATED 주석 명시.
- 구조적 회귀 테스트 3건 추가(라우터 미마운트, 죽은 파일 미import,
  공개 응답 스키마에 필드 미선언) — 누군가 실수로 재연결하면 즉시
  테스트가 깨진다.

## 추가 완화 조치 (CA-8, 2026-08-21 — "도달 불가능은 방어가 아니다"
지적 반영)

"도달 가능한 HTTP 경로가 없다"는 사실은 현재 상태를 설명할 뿐,
그 자체로는 방어 기제가 아니다(향후 실수로 라우터가 재마운트되거나
새 쓰기 경로가 추가되면 바로 뚫린다). 그래서 ORM 레벨에 마지막
방어선을 추가했다:

- `Supplier` 모델에 `@validates("api_key", "api_secret")` 신규 —
  `None`(미설정)은 계속 허용하되, 실제 값을 대입하려는 모든 시도를
  `SUPPLIER_CREDENTIAL_WRITE_BLOCKED` 구조화 오류(`BadRequestException`)
  로 즉시 차단한다. 이 검증은 SQLAlchemy 세션/커밋 여부와 무관하게
  Python 객체에 값을 대입하는 순간 실행된다 — 라우터나 서비스 계층이
  실수로 다시 연결되더라도 실제 값이 DB에 저장되기 전에 막힌다.
- `tests/test_supplier_legacy_schema_upgrade.py::
  SupplierCredentialFieldUnreachableTestCase`에 검증 테스트 3건 추가
  (api_key 대입 시 예외, api_secret 대입 시 예외, None 대입은 여전히
  허용).
- 이 조치는 위 "제거 계획"을 대체하지 않는다 — 컬럼 자체는 여전히
  DB에 남아 있고, 물리적 제거는 여전히 별도 승인·SQLite 재구성
  Migration이 필요하다(계획 불변). ORM validator는 그 승인이 나기
  전까지의 중간 방어선일 뿐이다.
