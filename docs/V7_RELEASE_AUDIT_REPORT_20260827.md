# HOMEZ V7 전체 기능 감사 중간 종료 보고서

작성일: 2026-08-27  
범위: 운영 변경 전 코드 감사, 결함 수정, Migration 리허설, 격리 테스트,
최종 전체 회귀

## 1. 이번 작업의 원래 목표

HOMEZ V7 전체 기능을 사용자 Workflow 기준으로 감사하고 결함을 수정한
뒤, 운영 Migration 적용, 최종 패키징/설치, 쿠팡 실제 상품등록 1회까지
검증하여 출시 가능 여부를 판정하는 것이었다.

이번 요청에 따라 현재 실행 중이던 최종 전체 회귀까지 마무리하고,
운영 변경·패키징·Live 외부 실행은 시작하지 않은 상태에서 중단했다.

## 2. 시작 기준선

- Branch: `main`
- Git WIP: 269개 항목. reset/revert/clean하지 않음.
- 공식 EXE:
  `C:\Users\Daum pc\AppData\Local\Programs\EVERY HOMEZ\Homez.exe`
- 개발 DB SHA-256:
  `5C22D204D7D290608DD4585CF95353823487B8B21549D82F9427B1756A7C5179`
- 운영 DB SHA-256:
  `6F0C155BEE152755742AED10F2ADB937458BE05A5813FA56E724D9FAC98D0617`
- 운영 DB: integrity_check=ok, Migration 35건, 기존 FK 위반 6건
- 운영 모드: `RECOMMEND_ONLY`
- EStop: 비활성
- 쿠팡 연결: id=2, `CONNECTED`, Credential reference 존재
- Wizard 6: `SUCCEEDED`, Listing 2: `SUBMITTED`
- Submission 3: 내부 `PENDING`, 외부 접수번호 없음
- 최신 Approval 6: 작업 시작 시 이미 만료

## 3. 이번에 완료한 작업

### 3.1 Coupang Live 제출 안전성

- 외부 호출 전 `PENDING -> SUBMITTING` 원자적 선점
- request fingerprint/correlation ID/HTTP 상태 추적
- 버튼 연타·동시 요청 중복 차단
- 4xx=`FAILED`, timeout/network/5xx/응답 유실=`UNKNOWN`
- 불확실 결과 자동 재시도 차단
- 쿠팡 외부 ID가 있을 때만 `SUBMITTED` 인정
- RECOMMEND_ONLY, EStop, 승인 만료, 회사 불일치, 공개 이미지 누락,
  이미지 권리 미확인 차단

### 3.2 승인 후 Payload 변경 차단

Live 제출 직전 저장된 승인 패키지를 현재 Wizard 데이터로 다시 계산한다.
상품명·이미지·카테고리·고시정보·판매방식·가격 등 승인 대상이 변경되면
`APPROVED_PAYLOAD_CHANGED`로 차단하고 재검토·재승인을 요구한다.

### 3.3 Migration #36 리허설

`20260826_00_add_marketplace_submission_live_tracking.sql`

- request_fingerprint
- correlation_id
- external_http_status
- 관련 인덱스
- 기존 Submission 3행 보존

운영 DB 복사본에서 35→36 적용, pending=0, integrity_check=ok.

### 3.4 기존 운영 DB FK 위반 보정

원인: `NOTIFICATION_EMAIL_SKIPPED` 시스템 감사로그 6행이 존재하지 않는
`users.id=0`을 참조함.

신규 Migration:
`20260827_00_repair_notification_audit_user_fk.sql`

다음 조건에 정확히 해당하는 행만 `user_id=NULL`로 보정한다.

- user_id=0
- action=`NOTIFICATION_EMAIL_SKIPPED`
- 실제 users.id=0이 존재하지 않음

운영 DB 복사본에서 Migration #36·#37을 공식 순서로 적용한 결과:

- Migration 37건
- pending=0
- integrity_check=ok
- foreign_key_check=0
- Submission 3→3행
- audit_logs 47→47행
- 보정 대상 6행

운영 원본에는 적용하지 않았다.

## 4. 테스트 결과

- FK 보정·Migration·알림 집중 테스트: 52/52
- 상품등록 Live·Wizard 집중 테스트: 130/130
- V7 핵심 Workflow 통합 테스트: 348/348
- 최종 전체 회귀: **2943/2943, 실패 0, 오류 0**
- 전체 회귀 로그:
  `scratchpad/v7_release_audit_20260827/full_regression.log`

## 5. 기능별 현재 수준

| 기능 | 현재 상태 | 근거/제한 |
|---|---|---|
| 관리자·로그인·권한 | ISOLATED_VERIFIED | 역할·회사 격리 테스트 존재 |
| Dashboard·운영 현황 | ISOLATED_VERIFIED | 실제 집계와 null 미집계 구분 |
| AI 상품 후보 | ISOLATED_VERIFIED | 생성→분석→추천→승인 E2E |
| 이미지 처리 | ISOLATED_VERIFIED | 업로드·분할·권리 차단. 실제 검색 Provider는 미연결 |
| 쿠팡 카테고리·고시정보 | LIVE_INPUT_REQUIRED | UI/Provider 코드 존재, 실제 조회는 승인 대상 |
| 가격·마진 | ISOLATED_VERIFIED | 개별 예상이익·마진 계산 검증 |
| 채널 정책·최종 승인 | ISOLATED_VERIFIED | 승인 만료·변경 무효화 포함 |
| 쿠팡 실제 상품 제출 | CODE_ONLY | 실제 Provider 구현, 아직 외부 POST 미실행 |
| 주문·재고 | ISOLATED_VERIFIED | 정상·재고부족 E2E |
| 공급처 검색 | PROVIDER_PENDING | Fake/Manual/CSV만 존재 |
| 공급처 실제 발주 | PROVIDER_PENDING | Fake/Manual/CSV, 실제 공급처 API 미연결 |
| 대형 쇼핑몰 구매 실행 | CODE_ONLY | 작업 큐·정책은 존재, 실제 결제 Provider 미연결 |
| 배송·반품·정산 | ISOLATED_VERIFIED | 내부 E2E, 실제 채널 Live 미검증 |
| 운영 알림 | ISOLATED_VERIFIED | In-app 연결, 실제 SMTP 미연결 |
| 백업·복원 | LIVE_VERIFIED | 이전 Live Gate에서 packaged restore 검증 |
| 업데이트 | CODE_ONLY | 배포·서명 인프라 미연결 |
| 설치·제거 | LIVE_RETEST_REQUIRED | 과거 DataDir 사고 이후 최종 패키징 재검증 필요 |

## 6. 냉정한 현재 판정

코드 수준의 핵심 Workflow와 회귀는 정상이다. 그러나 아래 이유로 아직
`V7_RELEASE_READY` 또는 완전자동 운영 완료라고 선언할 수 없다.

1. 운영 DB Migration #36·#37 미적용
2. 운영 DB FK 위반 6건이 원본에 남아 있음
3. 최신 코드 미패키징·미설치
4. 실제 쿠팡 상품등록 POST 미실행
5. external_submission_ref 없음
6. 최신 최종 승인 만료
7. 공급처 검색·실발주·대형 쇼핑몰 결제 Live Provider 미연결
8. 실제 SMTP Provider 미연결

현재 판정:
`V7_RELEASE_CODE_COMPLETE_LIVE_APPROVAL_REQUIRED`

## 7. 원래 이어서 해야 했던 작업

### 승인 묶음 A — 운영 반영

1. 운영 DB 최종 백업
2. Migration #36·#37 운영 적용
3. Migration=37, pending=0, integrity_check=ok, foreign_key_check=0 확인

### 승인 묶음 B — 최종 패키징

1. clean PyInstaller build
2. Inno Setup 패키징
3. 설치 파일 SHA-256 기록
4. HOMEZ 정상 종료
5. 최종 설치 1회
6. 로그인·기존 데이터 유지·Restricted Mode 해제 확인

### 승인 묶음 C — 쿠팡 Live 최소 검증

1. 권리 확인된 공개 대표 이미지 URL 준비
2. 쿠팡 카테고리·고시·가격·수량 최종 확인
3. 새로운 8단계 승인 생성
4. `OPERATOR_APPROVAL` 임시 전환
5. 상품명·판매가·수량·계정·API·fingerprint·예상 DB 변경 표시
6. 사용자 최종 승인
7. 쿠팡 상품등록 POST 정확히 1회
8. external_submission_ref 또는 공식 오류 응답 확인
9. 중복 등록 0건 확인
10. 즉시 `RECOMMEND_ONLY` 복원

### Live 이후 추가 검증

1. 실제 테스트 주문 1건
2. 주문 수집·재고 예약·배송·반품·정산 확인
3. 공급처 검색·발주는 Live Provider가 없으므로 별도 개발 범위 확정
4. 최종 사용자 가이드·알려진 제한사항 갱신

## 8. 종료 안전 확인

- 테스트 프로세스: 0
- 개발 DB 종료 SHA-256: 시작값과 동일
- 운영 DB 종료 SHA-256: 시작값과 동일
- 운영 DB 쓰기: 없음
- 운영 Migration 적용: 없음
- Credential 원문 접근: 없음
- 외부 API 호출: 없음
- 패키징·설치·제거: 없음
- Git commit·push: 없음

## 9. 중단 후 격리 패키징 재개 결과

- 실행 시각: 2026-08-27
- 운영 DB 최종 사전 백업:
  `C:\Users\Daum pc\Homez-Backups\v7_release_20260827\homez_pre_migration_36_37.db`
- 백업 SHA-256:
  `6F0C155BEE152755742AED10F2ADB937458BE05A5813FA56E724D9FAC98D0617`
- 운영 DB와 백업의 SHA-256·크기가 일치함을 확인했다.
- PyInstaller clean build: 성공
- 격리 EXE:
  `scratchpad\v7_release_package_20260827\dist\Homez\Homez.exe`
- EXE 크기: 24,415,247 bytes
- EXE SHA-256:
  `8BF468AEDD78E63ECC6A075C68580070E439C298646025947055FD3492BB5B30`
- 번들에 `app.main`, Migration #36, Migration #37 포함 확인
- Inno Setup compile: 성공
- 격리 installer:
  `scratchpad\v7_release_package_20260827\installer-output\HOMEZ-Setup-2.1.0.exe`
- Installer 크기: 97,574,537 bytes
- Installer SHA-256:
  `873B9ADA69614480448123E57C01E85C346123FD7AE23C316394AACA6CF2AB4B`
- 이 단계에서 installer는 실행하지 않았고, 설치·제거·운영 Migration·외부 API 호출을 수행하지 않았다.
- 패키징 종료 후 운영 DB SHA-256은
  `6F0C155BEE152755742AED10F2ADB937458BE05A5813FA56E724D9FAC98D0617`로 불변이다.

현재 남은 작업은 승인 필요 작업이다. 먼저 Migration #36·#37을
운영 DB에 적용하고 무결성과 대기 Migration 0건을 확인한 뒤,
최신 installer를 1회 설치하여 실제 실행을 검증해야 한다.
실제 쿠팡 상품등록 POST는 이 두 조치가 통과한 후에도
정확한 제출 내용을 보여주고 사용자의 최종 승인을 다시 받아야 한다.

## 10. 운영 Migration·최종 설치·기동 검증

- 사용자 최종 승인 후 Migration #36·#37을 공식
  `MigrationRunner` 순서로 운영 DB에 적용했다.
- 적용 전 pending: #36, #37 정확히 2건
- 적용 후 `schema_migrations=37`, pending=0, diagnose errors=0
- `integrity_check=ok`, `foreign_key_check=0`
- 이전 `NOTIFICATION_EMAIL_SKIPPED` 감사로그의 `user_id=0`
  6건은 #37의 한정된 수정 규칙으로 복구됐다.
- 최신 installer 설치 성공 로그:
  `C:\Users\Public\homez-final-install.log`
- 공식 설치 EXE SHA-256은 격리 빌드 EXE와 일치:
  `8BF468AEDD78E63ECC6A075C68580070E439C298646025947055FD3492BB5B30`
- 설치 후 자동 실행 해제 선택에도 Inno Setup `[Run]`이 실행됨.
  설치 자체는 성공했지만 자동 실행 제어 결함은 잔존한다.
- 사용자 정상 종료 후 프로세스 0건, DB 파일 핸들 해제 확인
- 공식 최신 설치본 기동·로그인 통과
- `/health` 응답: healthy (port 53082)
- 부트스트랩: apply=0, Migration 승인 대기 없음
- 역할 5건, 권한 38건, 채널 정책 10건 유지
- 쿠팡 연결 id=2 `CONNECTED` 유지, Credential 원문 미조회,
  이번 기동에서 외부 연결 테스트 미실행
- 운영 모드: `RECOMMEND_ONLY`
- EStop: 비활성
- Wizard #6: `SUCCEEDED/RESULTS`, Listing #2 보존
- Submission #3: `PENDING`, 외부 참조·HTTP 상태·correlation id 없음
  (실제 POST 미실행 유지)
- 기존 marketplace approval #6은 2026-08-26에 만료되어 재사용 불가
- 대표 media asset #3은 `ACTIVE`, `VERIFIED`이지만 로컬 storage
  path만 있고 쿠팡이 접근할 공개 HTTPS URL은 없다.

따라서 실제 쿠팡 POST 직전 남은 필수 조건은
(1) 권리 확인된 이미지의 공개 HTTPS 전달 경로,
(2) 현재 payload로 새 8단계 승인,
(3) `OPERATOR_APPROVAL` 임시 전환,
(4) 제출 직전 사용자 최종 승인이다.
