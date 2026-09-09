# V2.3 완료 체크리스트

- [x] Settlement MVP 구현 (create/get/list/cancel/confirm_deposit/reverse)
- [x] Funding Account/Ledger/Hold/SupplierPayment 연결
- [x] 단일 Transaction + 전체 rollback 보강
- [x] 조건부 상태 UPDATE + rowcount 검증
- [x] 부분 UNIQUE 인덱스 설계 (Model `__table_args__` 기준)
- [x] IntegrityError 경쟁 복구 검증(상태·타입·참조·계좌·금액 전부 일치 시에만 반환)
- [x] 기존 Ledger만으로 자동 완료하는 경로 제거
- [x] Hold/Supplier Payment 회귀 테스트
- [x] 전체 스키마 Migration 작성 (5개 테이블, Model 기준)
- [x] Migration 정적 검증 (BEGIN/COMMIT 1개, FK 없음, 컬럼 중복 없음 등)
- [x] Model ↔ Migration DDL 자동 드리프트 감지 테스트
- [x] Migration 중간 실패 시 rollback 검증 테스트
- [x] 임시 DB에서 Migration 적용/재적용 실패/rollback 스크립트 검증
- [x] 34개 테스트 통과 (17 기존 + 17 신규 Migration 관련)
- [x] 실제 `homez.db` 백업 생성 및 SHA-256/무결성 검증
- [x] 실제 `homez.db`에 전체 스키마 Migration 적용
- [x] 적용 후 테이블 5개·인덱스 24개·integrity_check 확인
- [x] 기존 데이터(11개 테이블) 보존 확인
- [ ] 실제 다중 프로세스 기반 동시성 검증 (스레드 기반 검증만 수행됨)
- [ ] Staging 환경 부하 테스트
- [ ] 동시성 스모크가 저장소 내 영속 회귀 테스트로 승격
- [ ] `app/core/dependency.py` / `app/core/dependency/` 이름 충돌 정리(WIP 정리 별도 승인 필요)
- [ ] `datetime.utcnow()` → timezone-aware 전환 (V2.4 기술부채)

체크되지 않은 항목은 Low~Medium 수준의 잔여 위험이며, Critical/High가 아니므로
V2.3을 "RC/운영 준비 완료(조건부)"로 판단하는 것을 막지 않는다.
