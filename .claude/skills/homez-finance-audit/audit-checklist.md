# Finance Audit Checklist (재사용용)

- [ ] Customer Payment 코드가 Funding/Settlement 테이블을 직접 건드리지 않는가
- [ ] 금액 검증이 생성 시점에 강제되고 자동 보정 경로가 없는가
- [ ] 상태를 바꾸는 모든 함수가 하나의 DB Transaction 안에서 commit 1회로 끝나는가
- [ ] 예외 발생 시 이미 수행된 flush/insert까지 전부 rollback되는가
- [ ] 동일 요청을 2번 호출해도 Ledger가 1건만 남는가
- [ ] DB UNIQUE(부분 인덱스 포함)가 애플리케이션 레벨 중복 방지의 최종 안전망으로 존재하는가
- [ ] 상태 전이가 `WHERE 현재상태=X` 조건부 UPDATE + rowcount 검증으로 이루어지는가
- [ ] 모든 자금 변경에 대응하는 Ledger 행이 생성되는가
- [ ] 관리자 권한 검증이 라우터/서비스 레벨에 존재하는가
- [ ] 종단 상태(DEPOSITED/REVERSED/COMMITTED 등) 재호출이 부작용 없이 기존 결과만 반환하는가
- [ ] 실패 시 "일부만 반영된" 중간 상태가 관측 가능한 경로가 있는가
- [ ] 테스트가 mock 없이 실제 DB 쿼리로 최종 상태를 검증하는가
- [ ] Migration의 컬럼·인덱스·제약이 Model과 정확히 일치하는가
