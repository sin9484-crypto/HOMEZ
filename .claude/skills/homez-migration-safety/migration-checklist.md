# Migration 작업 체크리스트

## 작성 시

- [ ] Model 파일을 다시 읽고 컬럼·타입·nullable·unique·index를 전수 확인했는가
- [ ] CREATE TABLE에 IF NOT EXISTS를 쓰지 않았는가(부분 적용을 숨기지 않기 위해)
- [ ] 논리 참조 컬럼에 임의로 FK를 추가하지 않았는가
- [ ] Model에 없는 DEFAULT/CHECK/CASCADE/trigger를 추가하지 않았는가
- [ ] BEGIN/COMMIT이 각각 정확히 1개만 실행되는가
- [ ] rollback DROP 문은 주석 안에만 있고 실행되지 않는가
- [ ] rollback DROP 순서가 테이블 생성 역순인가

## 검증 시

- [ ] SQLAlchemy canonical DDL과 Migration 실행 DDL을 diff했는가 (missing/extra 0)
- [ ] 임시 SQLite DB에 적용 → 재적용 시 명시적으로 실패하는가
- [ ] 중간 실패(일부 테이블만 생성된 후 실패) 시 rollback으로 전부 되돌아가는가
- [ ] 부분 UNIQUE 인덱스가 있다면 대상 reference_type만 차단하고 다른 타입은
      차단하지 않는가

## 실제 DB 적용 시 (반드시 사용자 승인 후)

- [ ] 적용 전 백업 생성 및 SHA-256/`integrity_check` 검증
- [ ] 적용 직전 read-only 연결로 대상 테이블 전부 부재 재확인
- [ ] 적용은 read-write 연결로 실행, 실패 시 즉시 `rollback()`
- [ ] 적용 후 테이블·인덱스·제약 실물 확인 + `integrity_check`
- [ ] 기존 테이블 행 수 보존 확인(내용 미출력, 개수만)
- [ ] 원본 백업 파일이 이번 작업으로 변경되지 않았는지 재확인
- [ ] 관련 unittest 재실행
