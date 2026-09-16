# HOMEZ 사용자 운영 설정 — 구현 상태 감사 보고서 (2026-09-09)

- **신규 파일 생성**(읽기 전용 감사 산출물 — 코드/DB/Migration/자격증명/외부 API/운영 데이터는 이 감사 중 전혀 변경하지 않았고, 실제 상품등록·주문·결제·발주·취소·환불도 실행하지 않았다).
- **대상 문서**: `docs/HOMEZ_USER_OPERATION_SETTINGS.md`
- **공식 기준 커밋**: `0cfe239824ee0fb8a92067ef46ccf4e8c84b1b72`
- **감사 방법**: 1~5번, 6~10번, 11~14번 섹션을 3개의 독립된 읽기 전용 세션으로 병렬 조사한 뒤 이 문서로 통합했다. 각 세션은 실제 소스코드(Router/Service/Repository/Model/UI), Migration 파일, `homez.db`(읽기 전용 연결로 스키마·행수만), 기존 테스트 파일과 최근 회귀 로그를 근거로 사용했다. Cursor는 사용하지 않았다.
- **Track A/B와의 관계**: 이 감사는 같은 날 병행 진행된 두 다른 트랙과 완전히 분리됐다.
  - **Track A**(DB/Migration 결함 조사·수정): Migration 순서 역전 결함(4건)과 실제 DB 해시 기준선 노후화 결함(1건)을 원인 확정 후 수정, 세 번째(최종) 전체 회귀에서 **`Ran 3835 tests in 5191.759s / OK`(실패 0건)**으로 확정 검증됨.
  - **Track B**(신규 매입처 조사): `docs/HOMEZ_V7_PROCUREMENT_CHANNEL_SURVEY_20260909.md`에 국내 5개·해외 4개 후보 조사 완료(이 감사와 무관, 별도 산출물).

---

## 1. 현재 기준선과 불일치

| 확인 항목 | 결과 |
|---|---|
| `docs/HOMEZ_USER_OPERATION_SETTINGS.md` 존재 여부 | 존재함 |
| 공식 기준 커밋 `0cfe239...`이 현재 HEAD와 일치하는지 | **일치함** — `git rev-parse HEAD` = `0cfe239824ee0fb8a92067ef46ccf4e8c84b1b72` |
| HEAD가 `origin/main`과 일치하는지 | **일치함** — 로컬이 원격보다 앞서거나 뒤처져 있지 않음 |
| 해당 문서 파일의 git status | 변경사항 없음(clean) |
| 저장소 전체 git status | `git status --porcelain` 326줄 — 다수의 기존 미커밋 변경/신규 파일 존재(이전 세션들의 진행 중 작업). **이 감사와 Track A/B 어느 쪽도 이 기존 WIP를 삭제·복원·정리하지 않았다.** |
| 실행 중 서버·테스트 프로세스 | 감사 시작 시점 python 프로세스 1개(Track A의 세 번째 전체 회귀, 현재는 완료됨). 별도 개발 서버(uvicorn)는 실행 중이 아니었음 |
| 개발 DB 경로 | `C:\Users\Daum pc\Homez-OS\homez.db` — Migration 이력 최신(20260908_00까지 적용, 20260908_01은 의도적으로 미적용 상태 유지, Track A에서 이미 확정) |
| 설치판 DB 경로 | `%LOCALAPPDATA%\HOMEZ\data\homez.db` — 이번 감사에서는 직접 재조회하지 않음(이전 세션에서 dev DB보다 11건 뒤처진 상태로 이미 확인됨, 최신 상태는 별도 재확인 필요) |
| 감사 세션들이 실행 도중 전체 회귀를 중복 실행했는지 | 아니오 — 세 감사 세션 모두 지시대로 Track A의 회귀를 재실행하지 않고 기존 로그만 열람했다 |

**특기사항**: 1~5번 담당 세션은 Track A의 세 번째 회귀가 실행 중이라 그 결과 대신 **2026-09-08에 완료된 이전 회귀**(`full_regression_20260908_rerun.log`, 3624 tests, 무관한 1건만 실패)를 인용했다. 6~10번·11~14번 담당 세션은 **2026-09-09에 완료된 두 번째 회귀**(`full_regression_20260909_2.log`, 3835 tests, 실패 1건 — Track A가 이후 원인 규명·수정한 그 건)를 인용했다. 세 세션 모두 이 감사 완료 시점 기준으로는 이미 낡은 스냅샷을 인용한 셈이지만, **Track A의 최종(세 번째) 회귀가 3835/3835 전부 통과로 확정**됐으므로 이 보고서의 결론(2번, 9번 항목)은 그 최종 결과를 기준으로 삼는다.

---

## 2. Critical / High 결함 (수정하지 않고 보고만 함, 세 세션 결과 통합·중복 제거)

### Critical

1. ~~**결제(`payment`) 도메인이 통째로 빈 스캐폴딩**~~ **[2026-09-10 Phase 7에서 부분 해결]** `app/domains/payment/{model,service,schema,router,gateway}.py`를 채웠다 — `PaymentMethod`(카드/PayPal/계좌이체/가상계좌, 카드번호·CVC 원문 컬럼 자체가 없음, Windows Credential Manager 참조만 저장), `PaymentAutoLimit`(건당/일일 한도, append-only), `FakePaymentProvider`(실제 네트워크 호출 없는 검증 전용 — 실제 Provider는 여전히 구현하지 않음), `SuperAdminGuard`+재인증(`X-Recent-Auth-Token`) 게이트, `FunctionCode.PAYMENT` 자동화 모드 연동까지 30개 테스트로 검증됨(`docs/HOMEZ_PROJECT_STATE.md` 2026-09-10 후속 18). **다만 실제 결제 실행 자체는 여전히 없다**(설계상 의도 — 실제 Provider 연동은 이 세션 절대 경계 밖) — 일일 누적 한도 집계도 실행 원장이 없어 아직 구현하지 않았다(건당 한도만 검사). "카드번호를 저장하지 않는다"(4-11, 11-10)는 이제 실제 안전 설계(토큰화+Credential Manager 참조)의 결과다.
2. ~~**환불(`refund`) 도메인이 통째로 빈 스캐폴딩**~~ **[2026-09-10 Phase 8에서 부분 해결]** `app/domains/refund/{model,repository,service,schema,router}.py`+`executor.py`를 채웠다 — `Refund`(AWAITING_APPROVAL→APPROVED/REJECTED→EXECUTED 상태기계, return_order와 동일한 append-only 이력 패턴), "승인 후에만 진행"을 예외 없이 강제(자동 승인 경로 자체가 코드에 없음, `test_no_automatic_path_reaches_approved_status`로 검증), `FakeRefundExecutor`(실제 네트워크 호출 없음)까지 27개 테스트로 검증됨(`docs/HOMEZ_PROJECT_STATE.md` 2026-09-10 후속 19). **다만 실제 환불 실행 자체는 여전히 없다**(설계상 의도 — 절대 경계 밖)이고, CANCELLATION(취소)의 별도 상태 모델링은 이번에 손대지 않았다(order 도메인의 기존 `OrderCancelRequest` 등과 중복 방지 목적, 정밀 조사 미실시). 반품(물류)은 여전히 `app/domains/return_order`가 담당(변경 없음).
3. **[2026-09-10 Phase 6에서 부분 해결] 시스템 전체에 스케줄러가 없음** — `app/domains/scheduler/*` 전부 0줄이던 것을 Phase 6에서 `app/domains/scheduler/jobs.py`로 채우고, `app/core/scheduler_service.py`(APScheduler 기반, 기존에도 있었으나 미연결)를 `app/main.py` lifespan에 실제로 연결했다(`settings.SCHEDULER_ENABLED` 토글 포함). **다만 실제로 등록한 Job은 백업 복구 리허설 1개뿐이다** — "5분마다 주문 수집"(2-8), "주 1회 가격 검토"(2-5) 등 나머지 주기 실행 대상은 여전히 스케줄러에 연결되지 않았다(주문 수집은 실행 코드는 있으나 Phase 3 자동화 모드 게이트가 어디서도 확인되지 않아 그대로 연결하면 위험 소지가 있고, 가격/재고 모니터링은 Adapter만 있고 Service/저장소 자체가 없다 — 상세 근거는 docs/HOMEZ_PROJECT_STATE.md Phase 6 절). 따라서 이 항목은 여전히 Critical로 유지하되, 스케줄러 인프라 자체의 부재는 해소됐다.
4. **로그인 세션이 문서 요구와 정반대로 설계됨** — `app/core/desktop_console_session_store.py`가 Refresh Token(30일)을 Windows Credential Manager에 저장해 **프로그램 재시작 후에도 세션이 자동 복원**되도록 만들어져 있다. 문서 11-11 "로그인은 프로그램 종료 시 끝난다"와 정면 충돌. "3시간 idle" 정책 자체도 코드 어디에도 없다.
5. **로그인 실패(무차별 대입) 방어가 완전히 죽어 있음** — `user/repository.py::increment_failed_login`이 명시적 no-op(관련 DB 컬럼 자체가 없음), `LOGIN_MAX_ATTEMPT`/`LOGIN_LOCK_MINUTES` 설정값이 코드 어디에서도 참조되지 않는 죽은 설정. 문서 11-18 완전 미구현.
6. ~~자동모드가 전역 단일 구조 — 기능별 게이팅이 구조적으로 불가능~~ **[2026-09-09 Phase 3에서 구조적으로 해결]** — 기존 `AutomationModeState.mode`(전역 단일)는 그대로 두고, 신규 `function_automation_states`(회사×기능별, 10개 기능 독립)를 추가해 기능별 게이팅이 코드 구조상 가능해졌다. 다만 "안전시험 통과 후에만 자동 개방"이라는 실행 게이트(14-22)와 "문제 감지 시 자동 강등"의 실제 감지 연결(14-23)은 아직 남아있다 — 구조는 갖춰졌지만 전부 배선되지는 않았다.
7. **자동결제 한도 체계가 이중화돼 있고 한쪽은 죽은 코드** — `automation_safety.ExecutionLimit`(일 단위 전용, `set_limit()` 호출부 grep 0건=UI로 변경 불가)와 `purchase_task.PurchaseTaskPolicySetting`(건당/일/월 3단계, 실제 UI 존재하나 **기본값이 문서 수치로 채워지지 않음** — `per_order_max_amount` 등이 전부 None)가 서로 다른 개념으로 병존한다. 어느 쪽이 실제 실행 경로에서 최종 참조되는지 이번 감사에서 완전히 특정하지 못했다.

### High

8. ~~환율(`currency`/`exchange`) 도메인이 완전히 빈 스캐폴딩~~ **[2026-09-10 Phase 9에서 해결]** `app/domains/currency`(`ExchangeRate`+`ExchangeRateToleranceSetting`, 수동 입력만·실외부 API 호출 없음)를 신설, 문서 원문 초기값 2%를 기본 허용률로 사용, 19개 테스트로 검증됨. **다만 허용률 판정(`check_rate_within_tolerance`)을 실제 후보평가 흐름에 연결하는 "기준 환율 기록" 지점은 아직 없다** — Phase 4의 가격 인상 감지가 겪은 것과 정확히 같은 한계(`docs/HOMEZ_PROJECT_STATE.md` 2026-09-10 후속 20 참고).
9. ~~마진율/원가상승률 입력 UI가 문서 지시(퍼센트 단위)와 반대로 구현됨~~ **[2026-09-10 Phase 10에서 해결, Phase 13에서 브라우저 실측 확인]** `console.html`의 4개 입력(소매구매/구매작업 정책 화면 각각 최소마진율·최대가격상승률)을 `(0~1)`에서 `(%)` 0~100으로 바꾸고, `console.js`의 표시/저장/위험경고비교/읽기전용요약 로직 전부에 ×100·÷100 변환을 넣었다 — 백엔드 스키마(`ge=0, le=1`)는 그대로 두고 프론트엔드에서만 변환한다. Phase 13에서 격리 스크래치 서버로 실제 로그인해 두 화면의 입력 필드를 DOM에서 직접 확인(`min=0, max=100, step=0.1`) — 실제 브라우저 렌더링 검증 완료.
10. 배송비를 확인하지 못해도 실제로는 발주가 막히지 않음 — 화면엔 "확인 안 됨"이라 정직하게 표시되지만 이 값이 실제 차단 목록(`blocked_reasons`)에 들어가지 않는다(코드로 직접 확인된 결함, 6-3/6-4/8-9/8-10 관련).
11. 배송비/가격 정보의 TTL(문서: 가격 30분, 재고 10분)이 시스템 상수로 존재하지 않음 — TTL 캐시 레이어 자체가 없다.
12. ~~백업 파일이 암호화되지 않음(평문 SQLite 그대로 로컬 보관) — 문서 11-16 위반.~~ **[2026-09-09 Phase 5에서 부분 해결]** `app/domains/backup/encryption.py`(HMAC-SHA256 기반 encrypt-then-MAC, 표준 stdlib만 사용 — `cryptography` 패키지 미설치 상태라 새 의존성 추가는 별도 승인 대상으로 남김) 신설, 16/16 단독 테스트로 왕복·변조감지·오탈키거부 검증됨. **다만 아직 `BackupService.create_backup()`/`RestoreService`에 실제로 연결되지 않았다** — 두 서비스는 약 15곳의 운영 호출부와 ~50곳의 기존 테스트에서 인자 없이(`BackupService(db)`) 생성되고 있어, 지금 강제 연결하면 그 전부를 건드리는 대규모 변경이 된다. 원시 모듈만 먼저 완성·검증하고 파이프라인 연결은 별도 단계로 분리했다(자세한 이유는 `docs/HOMEZ_PROJECT_STATE.md` Phase 5 절).
13. ~~주간 자동/안내 기반 복구 시험 기능이 없음 — 코드 주석이 스스로 "스케줄러가 없어 범위 밖"이라 인정.~~ **[2026-09-09 Phase 5에서 해결]** `RestoreService.run_weekly_rehearsal()` 신설 — 대상 DB를 새로 백업(trigger_source=scheduled_rehearsal)한 뒤 그 백업을 버릴 목적의 임시 경로에 실제로 복원해 보고, 성공/실패를 `RestoreAttempt`/알림으로 남긴다(11/11 테스트 통과). 스케줄러(Phase 6)가 아직 없어 "매주 자동" 실행은 여전히 관리자가 수동으로 호출해야 한다 — "안내 기반" 절반만 충족.
14. 긴급 중지(Emergency Stop) 발동 알림이 "서버 관리자/사용자 관리자" 역할 구분 없이 **활성화를 실행한 사람 1명에게만** 발송됨 — 문서 11-21/11-22 위반.
15. "가상재고"(무재고 중개 특유의, 의도적으로 낮게 표시하는 재고) 개념이 시스템 어디에도 없음 — 대신 실물 창고형 `InventorySku` 모델만 존재해 사업 모델과 개념적으로 어긋남(7-7, 7-8, 8-17, 8-19).
16. 매입처 반복 실패 시 자동 일시정지 로직 없음(3-11, 7-11, 7-15, 7-16) — 개념 자체 미구현.

### Medium

17. `settlement_reconciliations.refund_amount` 컬럼이 DB에는 있으나 ORM/서비스 어디에도 매핑되지 않는 고아 컬럼(schema drift).
18. ~~감사로그(`write_audit_log`)의 자유 텍스트 `description` 필드가 중앙 강제 마스킹을 거치지 않음~~ **[2026-09-10 Phase 11에서 해결]** `write_audit_log()`가 저장 직전 `description`을 항상 `redact_free_text()`(전화번호·JWT 형태 패턴 치환)로 통과시킨다 — 일반 설명 문구는 영향 없음(패턴 매칭이라 오탐 없음), 4개 테스트로 검증. 감사로그를 쓰는 11개 파일 전체 회귀(168개) 영향 없음 확인.
19. AI 판단 화면의 "근거 부족" 문구가 문서 요구 문구 "확인 필요"가 아니라 "데이터 부족"으로 다르게 표시됨(12-20, 경미하지만 명시적 요구사항 불일치).
20. 한도 초과 시 `SafetyService.evaluate()`가 `DENY`만 반환하고 `REQUIRE_APPROVAL`(사용자 승인 요청)로 전환하지 않음(5-5) — "중지"는 되지만 "승인 요청" 워크플로로 이어지는지는 호출자 책임으로 남아있음.
21. ~~**[2026-09-09 Phase 4에서 발견]** "가격이 기존 확인값보다 인상됐으면 자동발주를 중지한다"(8-2)는 정책 판정 코드... 그 판정에 필요한 기준값을 실제로 채우는 호출부가 저장소 어디에도 없다~~ **[2026-09-10 Phase 10에서 해결]** `PurchaseTaskCandidate.expected_amount_at_creation`(신규 컬럼) 추가 + `evaluate_and_prepare()`가 후보를 처음 평가할 때 1회만 기록 + 재평가 시 비교하도록 연결했다. `PriceIncreaseBaselineTestCase`(4개, `tests/test_purchase_task_service.py`)가 end-to-end로 검증: 첫 평가는 오탐 없음, 재평가해도 기준선 불변, 허용률 초과 인상은 실제로 BLOCK+PRICE_CHANGE 강등까지 발생, 허용률 이내는 통과. `build_order_submission_review()`(발주 전 최종 검토)가 이 기준선을 함께 쓰는지는 별도 확인 필요(미검증으로 남김).

---

## 3. 1~14번 전체 상태표

> 판정 값: `IMPLEMENTED` / `PARTIALLY_IMPLEMENTED` / `NOT_IMPLEMENTED` / `VERIFICATION_REQUIRED` / `BLOCKED_BY_EXTERNAL_RESPONSE` / `BLOCKED_BY_USER_CONFIGURATION` / `BLOCKED_BY_USER_APPROVAL`

### 1. 기본 운영 설정

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 1-1 | 설치판 DB 실데이터 보존·갱신 | VERIFICATION_REQUIRED | `MigrationRunner.create_backup()` pre-migration 백업+integrity_check 패턴, `test_installer_data_safety.py` 존재 | `migration_runner.py` 전체 미검토, 실제 설치판 업그레이드 재현 사례 없음 | 전체 코드 검토 + 격리 DB 실행 결과 + 과거 실사례 확인 | GPT 구현 가능(검증절차), 재현은 사용자 승인 필요 |
| 1-2 | 사용자 본인만 사용하는 개인 베타 | PARTIALLY_IMPLEMENTED | `users` 테이블 실제 행수=1(현재 사실과 일치) | 이를 강제하는 코드(가입 차단 등) 없음, company/user 스코프는 이미 다중 사용자 전제 설계 | 다중 사용자 등록 시도 시 차단 여부 확인 | 사용자가 화면에서 직접 설정(현재는 운영정책으로 충족) |
| 1-3 | 기본 통화·시간대·백업 위치·주기·보관기간 설정 완료 | NOT_IMPLEMENTED | `app/domains/settings/seed.py`에 `DEFAULT_TIMEZONE` 존재하나 이 도메인은 `app/main.py`에 미mount(죽은 코드로 코드 주석 스스로 명시). 실제 mount된 `user_settings`의 `ALLOWED_SETTING_KEYS`는 4개(locale/guide_progress/ui_preferences/notification_preferences)뿐 | 통화 설정 코드 전무, 백업 주기(스케줄러 부재), 보관기간은 코드 상수뿐 설정화면 없음 | — | GPT가 코드로 구현 가능 |
| 1-4 | GitHub엔 코드·Migration·문서·테스트만 | IMPLEMENTED | `.gitignore`에 `*.db`/`.env`, `git ls-files`에 db/env 없음 | — | — | — |
| 1-5 | 실제 DB·로그·API키·JWT·카드·개인정보 GitHub 미업로드 | IMPLEMENTED | 1-4와 동일 근거 | `storage/logs` 디렉터리 자체의 git 추적 여부 별도 미확인 | `storage/logs`/`storage/backups` 추적 여부 재확인 | — |
| 1-6 | 실제 DB를 컴퓨터 내부에 별도 백업 | IMPLEMENTED | `BackupService.create_backup()` — SQLite 온라인 백업 API+integrity_check+이력. `backup_records` 실제 3건 존재. 2026-09-09 Phase 5: `RestoreService.run_weekly_rehearsal()`가 이 위에 백업+복구시험을 묶어 제공 | 자동/주기 백업 없음(스케줄러 부재로 여전히 수동 트리거만) | — | 수동 완료, 자동화는 Phase 6(스케줄러) 대상 |

### 2. 판매채널 운영 방식

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 2-1 | 쿠팡부터 시작, 안정화 후 네이버 추가 | PARTIALLY_IMPLEMENTED | 쿠팡 코드량 압도적, 실 HTTP+HMAC 서명 Provider 존재 | 네이버 어댑터도 이미 병행 개발 정황(`test_store_connection_naver_production_adapter.py` 존재) — 순서 원칙과 다름 | 쿠팡 실제 Live 성공사례, 네이버 어댑터 활성화 여부 확인 | 외부 업체(쿠팡/네이버) 공식 정책 확인 필요 |
| 2-2 | 초기 상품등록은 사용자 확인 후 실행 | IMPLEMENTED | `ApprovalService` — 요청/승인/거절/취소 항상 별도 API, company 스코프 강제 | — | — | — |
| 2-3 | 최종 목표: HOMEZ 자동등록+결과 통지 | NOT_IMPLEMENTED | 문서 자체가 "최종 목표"로 명시(의도된 미완료) | 자동화 모드 있어도 리스팅 제출은 여전히 승인 게이트 통과 필요 | — | 실제 주문·결제 데이터가 쌓여야 가능 |
| 2-4 | 초기 가격변경은 변경안 표시 후 확인 | IMPLEMENTED | `PriceChangeRequest`+fingerprint(요청/승인 시점 지문 불일치 시 재요청 요구) | — | — | — |
| 2-5 | 가격 주 1회 정기 검토 | PARTIALLY_IMPLEMENTED | `price_advisory_service.suggest()`는 수동 호출만. 2026-09-10 Phase 10: `PriceReviewCycleSetting`(회사별 검토주기, 기본 7일) 신설 — 설정값 관리는 완료 | 스케줄러(Phase 6에서 인프라는 생김)에 실제로 연결해 매주 자동 실행하는 Job은 아직 없음 | `price_advisory_service.suggest()`를 스케줄러 Job으로 연결 | GPT가 코드로 구현 가능(스케줄러 연결) |
| 2-6 | 매입원가 위험 시 정기검토 전 즉시 일시중지 | NOT_IMPLEMENTED | `MarketplaceListing.pause()` 존재하나 수동 admin API뿐 | 마진위험 감지→자동pause 연결 코드 없음 | — | GPT가 코드로 구현 가능 |
| 2-7 | 품절 확인 시 즉시 자동 판매중지 | NOT_IMPLEMENTED | 품절 상태 모델링(OUT_OF_STOCK)은 존재, 읽기전용 집계만 | 자동 pause 실행 코드 없음(수동 admin API만) | — | GPT가 코드로 구현 가능 |
| 2-8 | 신규 주문 5분마다 수집 | IMPLEMENTED [2026-09-16 Phase 6, 2026-09-16 Phase 7 운영 화면·브라우저 검증 완료] | 신규 모듈 `app/domains/order/auto_collection_scheduler.py`가 기존 검증된 수집 로직(`OrderMultiChannelCollectionService.run_all()`, 중복방지·정규화·커서 전진 규칙 전부 재사용, 새로 구현하지 않음)을 감싸 5개 미실행조건(EmergencyStop/Migration제한모드/`FunctionCode.ORDER_COLLECTION`이 AUTOMATIC 아님/이전실행진행중/자격증명만료)을 게이트로 적용한다. 회사 단위 주기 설정(`OrderAutoCollectionState.interval_minutes`, 기본 5분, 운영 화면에서 직접 변경 가능)과 3회 연속 전체실패 시 해당 회사의 `ORDER_COLLECTION` 기능만 자동으로 ERROR로 낮추고 회사 관리자+서버 관리자(`platform_alert`) 양쪽에 알린다. 실제 스케줄러 Job(`ORDER_COLLECTION_TICK_JOB_ID`, 매분 트리거·내부에서 회사별 주기 판단)을 `app/domains/scheduler/jobs.py`에 등록, `app/main.py` lifespan에서 자동 실행. `tests/test_order_auto_collection_scheduler.py`(13개 — 지시문이 요구한 12개 시나리오 전부 + 수동트리거 1개, Fake Provider+Fake Clock)로 검증. **2026-09-16 Phase 7 추가**: 신규 운영 화면(콘솔 nav "주문 자동 감지", `app/web/console.js::loadOrderCollectionOps()` + `app/domains/order/router.py`의 `/orders/collection-ops/{status,trigger,interval,pause,resume}` 5개 엔드포인트)에서 회사별 자동감지 사용여부·수집주기·마지막실행/성공·다음예정시각·신규/중복/미연결/실패 주문수·현재상태·최근오류를 표시하고, 수동 "지금 확인"·주기 변경·일시중지·재개를 제공한다. "중지·재개는 재인증 필요" 요구사항은 재개(PAUSED/ERROR→AUTOMATIC)에만 적용했다(일시중지는 더 안전한 방향이라 재인증 없음) — 기존 배송정보 열람과 동일한 `consume_recent_auth_token` 메커니즘을 재사용했다. 격리 스크래치 DB(`odo_ui_verify.db`)+실제 브라우저(데스크톱 1280×900, 모바일 390×844)로 전체 흐름(상태조회→지금확인(자격증명 미등록으로 SKIPPED_VALIDATION_BLOCKED 실제 재현·연속실패 미카운트 확인)→주기변경→일시중지→재개(비밀번호 재확인 다이얼로그 실제 노출·통과 확인))을 검증했고, 매 단계 SQLite 직접 재조회로 화면 표시값과 DB 실제 값이 일치함을 확인했다(`order_auto_collection_states`/`function_automation_states` 두 테이블 모두 대조). 두 뷰포트 모두 콘솔 오류 0건, 가로 스크롤 없음, DOM 요소 겹침 없음(`getClientRects()` 기반 점검) | 회사 단위 실행 단위다(연결/판매계정별 완전 독립 주기는 아님 — `run_all()`이 회사의 모든 연결을 한 번에 순회하는 기존 설계를 그대로 재사용했기 때문) — 한 회사 안에서 앞선 연결이 예외를 던지면 뒤의 연결은 이번 tick에서 시도되지 않고 다음 tick(최대 1분 뒤)으로 넘어간다. 범용 기능별 자동화 모드 화면(`POST /console/api/function-modes/{function_code}`)으로도 같은 회사의 `ORDER_COLLECTION`을 AUTOMATIC으로 바꿀 수 있고, 그 경로는 재인증을 요구하지 않는다(이 Phase 이전부터 있던 10개 기능 공용 화면 — 이번 범위에서 바꾸지 않음, 정직 공개) | 없음(2-8 관련 코드·테스트·UI·브라우저 검증 전부 완료) | 없음 |
| 2-9 | 가격/재고/상품정보 확인불가 시 중지+통지 | VERIFICATION_REQUIRED | Fail-closed 설계 패턴 전반 확인(UNKNOWN 상태 보존, 추론 안 함) | 케이스별 알림 발송 전수 연결 확인 못함 | 각 케이스의 실제 알림 발송 경로 전수 확인 | GPT가 코드로 구현 가능(연결 보강) |
| 2-10 | 개인베타 중 등록·가격변경마다 사용자 확인 | IMPLEMENTED | 2-2, 2-4와 동일 근거 | — | — | — |

### 3. 매입처 운영 방식

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 3-1 | 온채널부터 시작, 안정화 후 확대 | IMPLEMENTED | 온채널이 유일 구체구현 매입처, 모델은 다중매입처 지원하도록 일반화 | 없음(현 단계 부합) | — | — |
| 3-2 | 상품값·배송비·배송기간·재고 종합 비교해 매입처 선택 | NOT_IMPLEMENTED | `supplier/policy.py`는 trust/delivery score만, 다중 매입처 종합 비교 알고리즘 없음 | 매입처가 1곳뿐이라 비교 대상 자체 없음 | — | GPT가 코드로 구현 가능 |
| 3-3 | 첫 매입처 실패 시 다른 매입처 자동구매 안 하고 통지 | VERIFICATION_REQUIRED | RESULT_UNKNOWN 자동재시도 금지는 확인됨, 매입처 1곳뿐이라 원칙에 부합하는 상태 | 2곳 이상 연결 시 자동전환 차단 로직 존재 여부 미확인 | 2개 이상 매입처 시나리오 확인 | GPT가 코드로 구현 가능 |
| 3-4 | 매입처가 고객에게 직접 배송 | IMPLEMENTED | 고객 배송지 필드가 발주까지 전달되는 구조 확인 | — | — | — |
| 3-5 | 최종 운영은 HOMEZ 자동발주 목표 | PARTIALLY_IMPLEMENTED | `order_submission_service.py` 실 발주 API+idempotency 인프라 갖춤 | 실사용 자동발주 성공사례(Live) 미확인 | 실제 자동발주 성공 이력 확인 | 실제 주문·결제 데이터가 쌓여야 가능 |
| 3-6 | 첫 실발주는 사용자 자택 배송지로 검증 | NOT_IMPLEMENTED [BLOCKED_BY_USER_APPROVAL] | 임의 receiver 주소 입력 기능은 존재 | 전용 "검증 발주" 모드 없음, 실금전 지출 수반 | — | 사용자의 명시적 실행 승인 필요 |
| 3-7 | 가격/재고 변경 시 신규판매·발주 중지+통지 | VERIFICATION_REQUIRED | `PRICE_INCREASE_RATE_EXCEEDED` 정책 판단 존재 | 재고변경까지 포함한 신규판매 중지 로직 확인 못함 | policy_service 전체 흐름 추적 | GPT가 코드로 구현 가능 |
| 3-8 | 변경발견 이후 접수 주문은 자동취소 안 하고 확인대상 분리 | VERIFICATION_REQUIRED | `exception_analysis_service.py`가 제안만 생성(자동취소 없음) | 가격/재고 변경 시각 기준 분리 로직 구체 확인 못함 | 관련 서비스·테스트 상세 확인 | GPT가 코드로 구현 가능 |
| 3-9 | 주문별 손실액·재고상태 표시, 사용자가 발주/취소 결정 | VERIFICATION_REQUIRED | 관련 테스트 존재(`test_purchase_task_order_submission_review.py` 등) | 실제 화면·손실액 표시 로직 상세 미확인 | 리뷰 라우터/화면 상세 확인 | GPT가 코드로 구현 가능 |
| 3-10 | 반품은 HOMEZ 준비, 사용자 확인 후 신청 | IMPLEMENTED | `return_order/service.py` REQUESTED→APPROVED→RECEIVED 흐름, approve()는 REQUESTED만 승인 가능 | — | — | — |
| 3-11 | 매입처 문제 반복 시 자동발주 일시중지+재사용 여부 질의 | NOT_IMPLEMENTED | `PurchaseChannelConnection` 상태전이는 사람의 명시적 확인뿐(모델 docstring 명시) | 반복실패 카운트/자동pause 필드·트리거 없음 | — | GPT가 코드로 구현 가능 |

### 4. 결제수단 운영 방식

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 4-1 | 국내 매입처는 카드 또는 예치금 | PARTIALLY_IMPLEMENTED | 예치금 실제 구현(`funding` 도메인 model/service/test 완비) | 카드결제 전무 — `payment/*.py` 전부 0줄 | — | GPT가 코드로 구현 가능 |
| 4-2 | 해외 매입처는 카드 또는 PayPal | NOT_IMPLEMENTED | — | 해외 매입처 자체 미연결, PayPal/해외카드 코드 0건 | — | 외부 업체의 공식 답변 필요 |
| 4-3 | 매입처 공식지원 결제수단 1개를 기본값으로 | VERIFICATION_REQUIRED | `connection_method`는 로그인방식 개념으로 보임 | 결제수단 기본값 지정 필드 못 찾음 | `ConnectionMethod` 상수 전체 확인 | GPT가 코드로 구현 가능 |
| 4-4 | 결제 실패 시 다른 수단 자동재결제 안 함, 중지+통지 | NOT_IMPLEMENTED | — | payment 도메인 부재로 결제 자체 미구현 | — | GPT가 코드로 구현 가능 |
| 4-5 | 예치금 자동충전 미사용 | IMPLEMENTED | `add_funding`/`remove_funding` 관리자 수동 전용, 자동충전 코드 0건 | — | — | — |
| 4-6 | 해외결제는 해외매입처 실제 추가 시 별도 허용 | NOT_IMPLEMENTED | — | 해외매입처 없어 게이트 자체 대상 없음 | — | 외부 업체의 공식 답변 필요(해외매입처 확정 후) |
| 4-7 | 수동/반자동/자동 모드 각각 제공 | PARTIALLY_IMPLEMENTED | `AutomationMode`(RECOMMEND_ONLY/OPERATOR_APPROVAL/LIMITED_AUTOMATION/DISABLED) 개념 대응, UI 전환 가능 | 결제 흐름 자체가 없어 이 모드가 결제단계에 실제 연결됐는지 미확인 | 모드별 실제 결제/발주 실행 경로 연결 확인 | GPT가 코드로 구현 가능 |
| 4-8 | 수동모드: HOMEZ 준비, 사용자 결제완료 | VERIFICATION_REQUIRED | 발주 준비 흐름 존재 | 결제를 사용자가 완료하는 화면까지 명시 확인 못함 | 수동모드 화면 전체 흐름 확인 | GPT가 코드로 구현 가능 |
| 4-9 | 반자동모드: 결제 직전까지 준비, 사용자 승인 | PARTIALLY_IMPLEMENTED | REQUIRE_REVIEW/OPERATOR_APPROVAL 개념 존재 | 결제 직전 준비단계가 payment 도메인과 미연결 | — | GPT가 코드로 구현 가능 |
| 4-10 | 자동모드: 검증된 결제토큰 또는 예치금, 한도 준수 | PARTIALLY_IMPLEMENTED | 예치금 기반 한도체크는 UI까지 구현 확인(5-4) | "검증된 결제 토큰" 경로 전무 | — | GPT가 코드로 구현 가능 |
| 4-11 | 카드번호·보안코드 직접 저장 안 함 | IMPLEMENTED | 2026-09-10 Phase 7: `PaymentMethod` Model에 카드번호·CVC가 들어갈 컬럼 자체가 없음(구조적 강제) — `raw_details`는 `FakePaymentProvider.tokenize()`에만 잠깐 전달되고 버려지며, DB에는 `credential_target_name`(Windows Credential Manager 참조)만 남는다. `test_register_method_never_stores_raw_card_number_in_db`로 실제 DB 행을 읽어 확인 | 실제 Provider가 아직 없어 이 보장이 "진짜 카드결제"에서도 유지되는지는 실제 Provider 연동 시 재검증 필요(Fake로만 검증됨) | 실제 Provider 연동 시 동일 테스트 패턴 재적용 | GPT 구현 완료, 실제 Provider 연동은 별도 |
| 4-12 | 카드 자동결제엔 PG토큰/저장카드/가상카드 사용 | NOT_IMPLEMENTED | — | 토큰/가상카드/저장카드 코드 전무 | — | 외부 업체의 공식 답변 필요(PG 계약·문서 선행) |
| 4-13 | 토큰결제 미지원 매입처는 수동/반자동 운영 | NOT_IMPLEMENTED | — | 토큰지원 판단해 모드 강제하는 로직 없음(토큰결제 자체 없음) | — | 외부 업체의 공식 답변 필요 |
| 4-14 | 자택 테스트발주 성공 후 소액한도로 자동결제 시작 | NOT_IMPLEMENTED [BLOCKED_BY_USER_APPROVAL] | — | "테스트발주 성공→자동모드 개방" 전용 게이트 없음(수동전환만 가능) | — | 사용자의 명시적 실행 승인 필요 |

### 5. 자동결제 한도

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 5-1 | 주문 1건 기본한도 50,000원 | NOT_IMPLEMENTED | `per_order_max_amount` 필드 존재(nullable) | 기본값이 50000으로 채워지지 않음(None), `execution_limits` 0행 | — | 사용자가 화면에서 직접 설정(화면은 존재) |
| 5-2 | 하루 기본한도 100,000원 | NOT_IMPLEMENTED | `daily_purchase_limit_amount` 필드 존재 | 기본값 미설정 | — | 사용자가 화면에서 직접 설정 |
| 5-3 | 한 달 기본한도 500,000원 | NOT_IMPLEMENTED | `monthly_purchase_budget_amount` 필드 존재 | 기본값 미설정. `automation_safety.ExecutionLimit`은 `period`가 `"DAILY"` 고정이라 월단위 개념 자체가 없는 별도 계층도 존재 | — | 사용자가 화면에서 직접 설정 |
| 5-4 | 설정화면에서 한도 변경 가능 | IMPLEMENTED(purchase_task 경로만) | `PUT /purchase-tasks/policy` — recent-auth+낙관적 동시성, console.js 입력 필드 확인 | **Critical**: `automation_safety.SafetyService.set_limit()`는 호출부가 앱 전체에 전혀 없음 — UI에서 변경 불가능한 죽은 코드(별도 계층) | — | — |
| 5-5 | 한도 초과 시 자동결제 중지+사용자 승인 요청 | PARTIALLY_IMPLEMENTED | `PER_ORDER_MAX_EXCEEDED` 등 판정 존재(REQUIRE_REVIEW/BLOCK) | `SafetyService.evaluate()`는 DENY만 반환, REQUIRE_APPROVAL로 전환 안 함 | REQUIRE_REVIEW가 실제 승인대기 화면까지 연결되는지 확인 | GPT가 코드로 구현 가능(보강) |
| 5-6 | 같은 주문 자동재결제 항상 차단 | IMPLEMENTED(발주 기준) | 기존 처리된 idempotency_key 재조회 시 DUPLICATE_REQUEST로 DENY, UNIQUE 제약 | 결제 자체가 없어 "결제" 차원 검증 불가 | — | — |
| 5-7 | 이전 결제 실패 확정 시에만 재시도 승인 가능 | PARTIALLY_IMPLEMENTED | REJECTED(확정실패)/RESULT_UNKNOWN(불명) 구분, REJECTED만 재시도 대상(발주 기준) | payment 도메인 부재로 결제 재시도 미구현 | — | GPT가 코드로 구현 가능 |
| 5-8 | 재시도엔 새 결제시도번호 부여, 원주문과 연결 기록 | PARTIALLY_IMPLEMENTED | `purchase_task_id`로 연결, idempotency_key가 시도식별자 역할(발주 기준) | 명시적 순번 필드 없음, 결제 자체 미구현 | — | GPT가 코드로 구현 가능 |
| 5-9 | 이전 결제결과 불명확하면 재시도 안 함 | IMPLEMENTED(발주 기준) | RESULT_UNKNOWN 상태에서 자동추정·재시도 안 함(docstring 명시), UNIQUE 제약으로 DB레벨 차단 | 결제 자체 미구현 | — | — |
| 5-10 | 결제 확인되면 재결제 차단 | PARTIALLY_IMPLEMENTED | SUCCEEDED 상태는 idempotency_key UNIQUE로 재사용 불가(발주 기준) | payment 도메인 부재 | — | GPT가 코드로 구현 가능 |
| 5-11 | 모든 자동결제를 즉시 멈추는 긴급중지 | IMPLEMENTED | `EmergencyStop` 모델+서비스, console.js 버튼(사유필수), evaluate()가 최우선 검사 | 실제 결제 자체가 없어 막는 대상이 현재는 발주/실행-허용 판정뿐 | — | payment 도메인 생성 시 재연결 필요 |

### 6. 수익성 기준

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 6-1 | 최소 예상 마진율 15% | PARTIALLY_IMPLEMENTED | `min_margin_rate` 필드+`MIN_MARGIN_RATE_NOT_MET` 하드블록 존재 | 기본값이 0(15%가 프리셋 아님) — 회사가 직접 입력해야 함 | 실 레코드 값 확인 | 사용자가 화면에서 직접 설정 |
| 6-2 | 원가 상승 허용률 3% | PARTIALLY_IMPLEMENTED | `max_price_increase_rate`(기본값 0.05)+블로킹 로직 존재 | 기본값이 3%가 아니라 5% | — | 사용자가 화면에서 직접 설정 |
| 6-3 | 배송비 확인불가 시 자동발주 중지 | NOT_IMPLEMENTED | 배송비 미확인 상태는 정직하게 반환됨 | 이 값이 blocked_reasons에 안 들어가 실제 차단이 안 됨(코드 결함) | — | GPT가 코드로 구현 가능 |
| 6-4 | 중지 시 배송비 입력화면+통지 | NOT_IMPLEMENTED | — | 입력 UI, 알림 dispatch 미발견 | — | GPT가 코드로 구현 가능 |
| 6-5 | 신규 판매채널 추가 시 공식수수료 조사 | PARTIALLY_IMPLEMENTED | `channel_policy_rules`가 쿠팡 한정 공식근거+검증일 필수 구조 보유 | 쿠팡 외 채널 규칙 카탈로그 없음, 강제 워크플로 없음 | 다른 채널 추가 시 패턴 준수 재확인 | GPT가 코드로 구현 가능 |
| 6-6 | 수수료 미확정 시 보수적으로 높게 적용 | NOT_IMPLEMENTED | — | 보수적 기본 수수료 로직 없음 | — | GPT가 코드로 구현 가능 |
| 6-7 | 광고비는 초기 이익계산 제외 | IMPLEMENTED | `calculate_margin()`에 광고비 파라미터 자체 없음 | — | — | — |
| 6-8 | 광고사용 상품엔 `광고비 미반영` 표시, 자동확대 안 함 | NOT_IMPLEMENTED | — | 상태 라벨/플래그, UI, 자동확대 방지 로직 모두 미발견 | — | GPT가 코드로 구현 가능 |
| 6-9 | 반품 예상비는 수익계산 포함 | IMPLEMENTED | `return_risk_reserve` 파라미터가 net_profit 계산에 직접 반영, 회사별 기본값 필드 존재 | 실제 호출부가 항상 채우는지 추가확인 필요 | 실제 호출 시 인자 전달 여부 | — |
| 6-10 | 초기 환율 변동 허용률 2% | IMPLEMENTED(판정 로직, 실행 미연결) | 2026-09-10 Phase 9: `app/domains/currency`(`ExchangeRate`+`ExchangeRateToleranceSetting`, 기본 2%) 신설, `check_rate_within_tolerance()`로 판정 가능. 19개 테스트 통과 | 판정을 실제 후보평가 흐름에 연결하는 "기준 환율 기록" 지점이 없음(Phase 4 가격 인상 감지와 동일한 한계) | 기준 환율 기록 시점 설계 | GPT 구현 완료(메커니즘), 실제 흐름 연결은 별도 조사 |
| 6-11 | 환율 자동 모니터링 추후 개발 | NOT_IMPLEMENTED | 수동 입력만 지원(의도적 — 절대 경계: 실제 외부 API 호출 금지) | 자동 모니터링(외부 환율 API 연동) 자체가 이 세션 범위 밖 | — | 외부 환율 API 선정·연동은 별도 승인 필요 |
| 6-12 | 기준 미달 시 신규판매·자동발주 즉시중지+통지 | PARTIALLY_IMPLEMENTED | 마진율 미달 시 발주는 MIN_MARGIN_RATE_NOT_MET으로 BLOCK | 신규판매(리스팅) 쪽 동일 게이트 여부 미확인, 알림 dispatch 연결 미확인 | marketplace_listing 승인 흐름 재확인 | GPT가 코드로 구현 가능 |
| 6-13 | 기존 주문은 자동취소 안 하고 확인대상 분리 | IMPLEMENTED(원칙만) | 반품/주문 자동취소 로직 전역 미발견(자동취소 금지 원칙 전역 일관) | 이 항목 전용 로직인지 특정 못함 | — | — |
| 6-14 | 마진율/원가상승률/환율허용률 설정화면에서 변경 | PARTIALLY_IMPLEMENTED | 마진율/원가상승률 입력필드 존재(console.html/js) | 환율 허용률은 화면 자체 없음(도메인 부재) | — | 마진율·원가상승률: 완료 / 환율: GPT 구현 가능 |
| 6-15 | 비율은 퍼센트(`15%`) 단위로 입력 | IMPLEMENTED | 2026-09-10 Phase 10: 마진율·가격상승률 4개 입력을 퍼센트로 수정(High 결함 #9 해결). Phase 13: 실제 브라우저 DOM에서 min=0/max=100/step=0.1 확인 완료 | — | — | — |

### 7. 상품·공급처 평가

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 7-1 | 판매가능성 점수+판단이유 표시, 사용자 선택 | IMPLEMENTED | `ProductCandidate`의 다축 점수+`ProductCandidateEvidence`+`DecisionReview`(승인/보류/거절) | 단일 스코어가 아니라 다축 분산(문서와 완전 동일 모양은 아님) | — | — |
| 7-2 | 상품값·배송비·배송기간 종합 비교해 공급처 선택 | IMPLEMENTED | `rank_candidates()` — 순이익·마진율·신뢰도로 순위화, 배송일수/반품가능/신뢰도 근거 노출 | 최저가 단독선정 아님(순위 보조지표) | — | — |
| 7-3 | 매입처 API 재고 제공 시 자동조회 | VERIFICATION_REQUIRED | `lookup_product()` 실 API 호출 코드 완비 | 코드 docstring 자체가 "실제 저장된 JWT로 단 한 번도 호출한 적 없다"고 명시 — 실행 미확인 | 실제 온채널 계정 1회 승인 호출 테스트 | 사용자의 명시적 실행 승인 필요 |
| 7-4 | 발주 직전 상품·옵션 판매가능 상태 자동확인 | IMPLEMENTED(사람 트리거 기준) | lookup_product() 호출 후 blocked_reasons 계산, send_blocked 연결 | "자동"이 스케줄러 아닌 사람이 화면을 여는 행위가 트리거(스케줄러 부재 공통제약) | — | — |
| 7-5 | 정확한 수량 없고 판매가능여부만 제공되면 그 상태 사용 | IMPLEMENTED | `ChannelProductOption.in_stock: bool\|None` 설계 | — | — | — |
| 7-6 | 재고/판매가능여부 확인불가 시 자동발주 안 함 | IMPLEMENTED | any_price_unknown 등이 조회실패 케이스도 포함해 차단 | — | — | — |
| 7-7 | 판매채널 가상재고 기준 이하 시 신규판매 중지 | PARTIALLY_IMPLEMENTED | 2026-09-10 Phase 10: `app/domains/price_stock_safety`(`VirtualStockThreshold`+게이트 판정 함수) 신설, `InventorySku`(실물창고)와 명확히 분리 | 판정 함수가 받는 "표시 재고 수치"를 실제 판매채널 리스팅에서 읽어오는 필드 자체가 없음(marketplace_listing 도메인에 새로 설계 필요) | 리스팅 표시재고 필드 설계 | GPT가 코드로 구현 가능(보강) |
| 7-8 | 매입처 API 호출제한 준수 조회간격·캐시TTL | IMPLEMENTED [2026-09-15 Phase 9A] | `PurchaseChannelConnection.rate_limited_until`(429 시 자동 백오프, 재시도시각 노출)+`PriceStockQuoteCache`(가격/재고 독립 TTL, 회사·연결·상품·옵션별 격리)+`lookup_product_cached()`(캐시 히트 시 네트워크 미호출, 실패·인증실패·UNKNOWN은 캐시 안 함) — 온채널 발주 직전 조회는 여전히 캐시를 쓰지 않음(가격 인상을 놓치지 않기 위한 의도적 예외, 변경 없음). `tests/test_purchase_channel_connection_service.py::CachedLookupTestCase`(10개)+`tests/test_price_stock_safety_domain.py` Fake-clock TTL 경계 테스트로 검증 | — | — | — |
| 7-9 | 모델명·옵션·수량·크기·제조사 일치 상품만 자동연결 | PARTIALLY_IMPLEMENTED | `min_match_confidence`(기본 0.98)+match_tier(BLOCKED/NEEDS_REVIEW) 존재 | 필드단위 개별비교인지 단일 스칼라 판단인지 매칭 알고리즘 소스 미확인 | 실제 매칭 서비스 파일 확인 | GPT가 코드로 구현 가능(부분) |
| 7-10 | 배송기록 부족 공급처는 사용자 확인대상 | NOT_IMPLEMENTED | `CompanySupplierRelation`에 배송이력/신뢰도 필드 전무, approval_status만 존재 | 배송이력 기반 자동판단 없음 | — | GPT가 코드로 구현 가능 |
| 7-11 | 품절·오배송·취소·지연 반복 시 자동발주 일시중지+재사용질의 | IMPLEMENTED [2026-09-15 Phase 9B] | `SupplierIncidentService`(품절/오배송/취소/배송지연/인증실패 5종 `PurchaseChannelConnectionIncident` append-only 기록, 회사별 설정 가능한 기간·건수 임계치 — `SupplierIncidentAutoPauseSetting`) — 임계치 도달 시 그 연결의 ORDER 기능만 `order_paused_at`으로 낮추고(다른 연결·다른 기능엔 영향 없음, 회사 전체 `FunctionAutomationState`와 별개 계층) SUPER_ADMIN에게 통지. **자동 재활성화 없음** — `reactivate_order_function()`은 관리자+확인사유(confirmation_note) 입력이 있어야만 해제됨. `tests/test_supplier_incident_service.py`(15개)+`tests/test_purchase_channel_connection_service.py::OrderFunctionPauseAndDormancyTestCase`로 검증 | — | — | — |
| 7-12 | 반품조건·비용 확인불가 상품은 자동발주 안 함 | IMPLEMENTED(조건만) | `require_return_allowed`(기본True)+RETURN_NOT_ALLOWED 하드블록 | "비용" 조건은 없고 "가능여부(bool)"만 검사 | — | GPT가 코드로 구현 가능(비용검사 보완) |
| 7-13 | 실제 반품은 통지+승인 후 진행 | IMPLEMENTED | RETURN_EXCHANGE_APPROVAL_NEEDED 알림, approve()가 REQUESTED 상태에서만 명시적 전이 | — | — | — |
| 7-14 | 새 공급처 첫 주문만 자택검증 | NOT_IMPLEMENTED | "첫 주문/자택/home_verification" 전역 grep 없음 | 개념 자체 미구현 | — | GPT가 코드로 구현 가능 |
| 7-15 | 이후 지연·품절·오배송·취소율 자동 기록 | NOT_IMPLEMENTED | 관련 자동집계 필드 전무(7-10, 7-11과 동일 근본원인) | — | — | GPT가 코드로 구현 가능 |
| 7-16 | 일정기간 무거래 공급처는 재사용 전 자동점검 | PARTIALLY_IMPLEMENTED [2026-09-15 Phase 9C] | `PurchaseChannelConnection.last_successful_order_at`(실제 발주 성공 시에만 갱신)+`verify_connection_ready_for_order_submission()`이 회사별 설정 가능한 휴면 판정 기간(`DEFAULT_DORMANT_CONNECTION_THRESHOLD_DAYS`)을 넘긴 연결을 감지해 `STATUS_CHANGED` 감사 이벤트로 기록 | 이 감지가 "재확인을 강제로 요구"하는 독립 차단 게이트는 아직 아니다 — 기존의 `verified_at` 최신성 검사(이미 있던 게이트)에 얹혀서만 동작한다("점검을 새로 강제"하는 코드가 아니라 "이미 있는 강제에 정보만 더한" 수준) | 휴면 감지 자체를 독립 차단 게이트로 승격할지 여부 | GPT가 코드로 구현 가능(독립 게이트 승격) |
| 7-17 | 가격·배송·재고·반품 평가비중 설정화면에서 조절 | PARTIALLY_IMPLEMENTED | `axis_weights_json` Model·평가로직 실재 | 편집 API/화면 전혀 없음(백엔드 전용) | — | GPT가 코드로 구현 가능(UI/Router 추가) |

### 8. 가격·재고·배송 관리

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 8-1 | 발주 직전 매입처 현재가격 항상 자동확인 | IMPLEMENTED(사람 트리거 기준) | lookup_product() 실호출 경로 | 스케줄러 부재로 "항상"이 아니라 화면 여는 시점만 | — | — |
| 8-2 | 가격 인상 시 자동발주 중지+통지 | IMPLEMENTED | 2026-09-10 Phase 10: `PurchaseTaskCandidate.expected_amount_at_creation`으로 기준선을 실제로 기록·비교(Critical 결함 #21 해결), PRICE_CHANGE 기능 강등+통지까지 end-to-end 검증(4개 테스트) | — | — | — |
| 8-3 | 발주 직전 옵션 구매가능 상태 항상 자동확인 | IMPLEMENTED(사람 트리거 기준) | 7-4와 동일 근거 | 동일 제약 | — | — |
| 8-4 | 판매가능·재고 확인불가 시 중지+통지 | PARTIALLY_IMPLEMENTED | 차단 로직 확인됨 | 실제 알림 dispatch까지 이어지는지 미확인 | notification_center 연결 확인 | GPT가 코드로 구현 가능(보완) |
| 8-5 | 가격정보 기본TTL 30분, 설정변경 가능 | IMPLEMENTED [2026-09-15 Phase 9D] | `PriceCacheTtlSetting`(append-only, 기본 30분, 회사별 변경 가능)+`PriceStockQuoteCache.price_confirmed_at/price_expires_at`(출처·회사·연결·상품·옵션별 저장) — 만료된 가격은 캐시에서 반환되지 않고(재조회 유도), 발주 직전 조회는 이 캐시를 절대 쓰지 않아(9G 설계 원칙과 동일) 만료가격으로 발주되는 경로 자체가 없다. 가격 인상은 기존 8-2 게이트가 별도로 잡는다 | — | — | — |
| 8-6 | 재고정보 기본TTL 10분, 설정변경 가능 | IMPLEMENTED [2026-09-15 Phase 9E] | `StockCacheTtlSetting`(기본 10분, 회사별 변경 가능)+`PriceStockQuoteCache.stock_confirmed_at/stock_expires_at`이 가격 TTL과 완전히 독립적으로 만료된다(Fake-clock 테스트로 "가격은 유효, 재고만 만료" 케이스 확인). 만료·조회실패·해석불가는 전부 "재고 있음"으로 취급하지 않는다(8-19 게이트가 이어받음) | — | — | — |
| 8-7 | 배송기간 변경으로 약속일 어려우면 발주중지+통지 | PARTIALLY_IMPLEMENTED | `max_delivery_days` 초과 시 DELIVERY_DEADLINE_EXCEEDED 하드블록 | 채널 약속일 필드 자체 연동인지 고정상한인지 불명확 | 채널 약속배송일 연동 확인 | GPT가 코드로 구현 가능(보완) |
| 8-8 | 배송지연만으로 기존주문 자동취소 안 함 | IMPLEMENTED(원칙확인) | 자동취소 로직 전역 미발견 | — | — | — |
| 8-9 | 배송비 변경 시 마진 재계산, 미달 시 발주중지 | NOT_IMPLEMENTED | 온채널 경로는 배송비를 애초에 추정하지 않음(shipping_fee_known:False 고정) | 변경감지 자체가 성립할 전제 없음 | — | GPT가 코드로 구현 가능 |
| 8-10 | 배송비 확인불가 시 직접입력화면+통지 | NOT_IMPLEMENTED | — | 입력화면/알림 미발견(6-4와 동일) | — | GPT가 코드로 구현 가능 |
| 8-11 | 공급사 공식제공 옵션·정보 내에서만 자동갱신 | VERIFICATION_REQUIRED | 실 API응답 필드만 매핑, 추측채움 없음 | 실행 미확인(7-3과 동일 근거) | 실제 API 1회 호출 검증 | 사용자의 명시적 실행 승인 필요 |
| 8-12 | 고객주문 옵션 자동대체 안 함 | IMPLEMENTED | mismatch는 경고표시만, 자동대체 코드 미발견 | — | — | — |
| 8-13 | 옵션변경·신규옵션 필요 시 사용자 확인요청 | PARTIALLY_IMPLEMENTED | mismatch 경고 화면표시는 존재 | 명시적 확인요청 알림 dispatch 미확인 | notification 연결 확인 | GPT가 코드로 구현 가능(보완) |
| 8-14 | 매입처 조회실패 시 오래된 캐시로 결제·발주 안 함 | IMPLEMENTED(설계원칙) | 실패 그대로 전파, 캐시 레이어 자체 없음(물리적으로 불가능) | 캐시TTL 기능 자체 없음(8-5/8-6과 표리관계) | — | — |
| 8-15 | 조회는 재시도 가능하나 결제·발주는 자동재시도 안 함 | IMPLEMENTED | 자동 재시도 루프 전역 미발견 | — | — | — |
| 8-16 | 매입처 조회실패 지속 시 사용자에게 통지 | IMPLEMENTED [2026-09-15 Phase 9, 재확인 Phase 9K] | `PurchaseChannelConnection.consecutive_failure_count`(실패 종류 무관 항상 증가, 성공 시 0으로 리셋)+임계치 "최초 도달" 순간에만 회사 SUPER_ADMIN 전원에게 `SUPPLIER_LOOKUP_REPEATED_FAILURE` 통지(idempotency_key에 스트릭별 시각 포함 — 서로 다른 스트릭끼리 중복判定 안 됨). **Phase 9K 재감사**로 연결별 독립 카운터, 회사간 교차알림 없음, 재시작 후에도 DB 기준 카운터 지속을 각각 전용 회귀로 추가 검증(`tests/test_purchase_channel_connection_service.py::RepeatedLookupFailureNotificationTestCase`, 6개) | — | — | — |
| 8-17 | 판매채널 가상재고 초기 낮게, 기본수량 변경가능 | NOT_IMPLEMENTED | 가상재고 개념 부재(7-7과 동일) | — | — | GPT가 코드로 구현 가능 |
| 8-18 | 주문접수 시 판매채널 표시수량 자동감소 | PARTIALLY_IMPLEMENTED | `InventoryService.reserve()` 원자적 감소, `sync_channel_stock()` 채널동기화 시도 | sync_channel_stock()는 Fake Provider 전용, 실채널 반영 미확인. 실물창고 개념이지 "가상재고" 개념 아님 | 실채널 재고 API 연동 확인 | GPT가 코드로 구현 가능(실채널 연동) |
| 8-19 | 판매가능여부 확인불가 시 가상재고 0+신규판매중지 | IMPLEMENTED [2026-09-15 Phase 9F] | `VirtualStockZeroProposal`(PENDING→APPROVED/REJECTED)이 `lookup_product_cached()`의 조회실패·support≠SUPPORTED·요청옵션 in_stock=None 3가지 경로에서 자동 생성되고 SUPER_ADMIN에게 통지 — **실제 판매채널에는 아무것도 자동 반영하지 않는다**(제안일 뿐). 같은 상품에 PENDING 제안이 있으면 그 상품의 신규 발주를 차단(`order_submission_service.py`). 재고 확인이 나중에 성공해도 자동 해소되지 않음 — 관리자가 비어있지 않은 resolution_note로 승인/거부해야만 해소. `tests/test_price_stock_safety_domain.py`(11개)+`CachedLookupTestCase`(5개 트리거 경로 테스트)로 검증 | — | — | — |

### 9. 반품·환불·정산

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 9-1 | 반품요청 시 요청내용·주문 정리+통지 | IMPLEMENTED | `create_return_order()`—주문/품목/송장 검증 후 알림 dispatch, DB 테이블 실존 | — | — | — |
| 9-2 | 반품요청 자동승인 안 함 | IMPLEMENTED | approve()는 사람이 명시 호출해야 하는 별도 메서드, 자동호출 안 됨 | — | — | — |
| 9-3 | 상품상태·사유 확인 후 승인, 반품절차 진행 | IMPLEMENTED | REQUESTED→APPROVED→RECEIVED→COMPLETED 조건부 전이 강제 | 구조화된 "상품상태" 전용필드는 못 찾음(reason 필드만) | — | — |
| 9-4 | 실제 고객환불은 사용자 승인 후 실행 | IMPLEMENTED(메커니즘, 실행은 Fake) | 2026-09-10 Phase 8: `RefundService.approve_refund()`가 유일한 승인 진입점이고 항상 사람의 명시적 호출을 요구 — 자동 승인 경로 자체가 코드에 없음(`test_no_automatic_path_reaches_approved_status`). 승인 후 실행도 `FakeRefundExecutor`로만 검증 | 실제 Executor(진짜 환불 실행)는 아직 없음 — 절대 경계 | 실제 Executor 연동 시 재검증 | GPT 구현 완료(메커니즘), 실제 실행 연동은 사용자 승인 필요 |
| 9-5 | 고객·판매채널·매입처 환불 별도 상태·금액 기록 | PARTIALLY_IMPLEMENTED | 2026-09-10 Phase 8: `RefundType`으로 CUSTOMER_REFUND/SUPPLIER_RECLAIM 2종 분리(각각 상태·금액 독립 기록) | "판매채널" 환불(마켓플레이스 정산 조정)은 별도 타입으로 분리하지 않음 — 3종 중 2종만 구현. `settlement_reconciliations.refund_amount` 컬럼은 여전히 미사용(고아 컬럼, 별개 결함) | 판매채널 환불 타입 추가 여부 확인 | GPT가 코드로 구현 가능(보강) |
| 9-6 | 반품배송비 부담주체는 사유+정책에 따라 계산 | NOT_IMPLEMENTED | 계산 로직/필드 미발견 | — | — | GPT가 코드로 구현 가능 |
| 9-7 | 부담주체·금액 확인불가 시 사용자 선택요청 | NOT_IMPLEMENTED | 상동 | — | — | GPT가 코드로 구현 가능 |
| 9-8 | 부분반품·부분환불 금액 HOMEZ 계산, 승인 후 실행 | NOT_IMPLEMENTED | 코드가 명시적으로 "부분반품 미지원(Gate4 범위 밖)"이라 차단 | 부분반품 자체가 시스템적으로 불가능 | — | GPT가 코드로 구현 가능 |
| 9-9 | 매입처 환불완료 확인까지 미정산 표시 | NOT_IMPLEMENTED | 2026-09-10 Phase 8: `RefundType.SUPPLIER_RECLAIM`로 매입처 환불 개념 자체는 생겼지만, `settlement`/`funding` 도메인과 연동해 "미정산으로 표시"하는 로직은 아직 없음 | settlement/funding 연동 없음 | — | GPT가 코드로 구현 가능(보강) |
| 9-10 | 매입처 환불상태·환불액 조회기능 | IMPLEMENTED | 2026-09-10 Phase 8: `GET /refunds?status=`+`RefundType.SUPPLIER_RECLAIM` 필터로 매입처 환불 상태·금액 조회 가능(`RefundService.list_for_company`) | — | — | — |
| 9-11 | 판매채널 실입금액 vs HOMEZ예상액 자동대조 | PARTIALLY_IMPLEMENTED | `analyze()`가 total_amount vs gross_amount 비교, 허용오차 적용 | 서비스 자체 docstring이 "채널 정산명세서 원본 연동 아직 없음"이라 명시 — 수기입력인지 자동유입인지 미특정 | `MarketplaceSettlement` 실제 유입경로 확인 | 실제 채널 데이터 유입경로 확인 필요(VERIFICATION_REQUIRED 재분류 여지) |
| 9-12 | 정산금액 차이 시 구성항목 표시+통지 | IMPLEMENTED(9-11 전제하) | `SettlementDifference`가 구성요소 반환(difference_type/evidence 등) | — | — | — |
| 9-13 | 정산상태 매일 자동확인, 월말 재대조 | NOT_IMPLEMENTED | 스케줄러 부재 — analyze()는 수동호출 전용, 월말 재대조 로직도 미발견 | — | — | GPT가 코드로 구현 가능 |
| 9-14 | 판매 직후엔 예상이익 표시 | IMPLEMENTED | `MarginType.EXPECTED`/`ACTUAL` 구분, `MarginSnapshot` 저장 | — | — | — |
| 9-15 | 입금+반품가능기간 확인 후 최종손익 확정 | PARTIALLY_IMPLEMENTED | `SETTLEMENT_RECONCILED` 스냅샷 개념 존재 | 반품가능기간 경과를 조건화하는 로직 못 찾음 | 실제 확정 트리거 로직 확인 | GPT가 코드로 구현 가능(보완) |
| 9-16 | 환불·정산결과 확인불가 시 자동재실행 안 함+통지 | IMPLEMENTED(원칙만) | `ReconciliationStatus.MISMATCH`는 자동보정 안 함 명시, 자동재실행 로직 미발견. 2026-09-10 Phase 8: 환불도 실패 시 재시도를 자동으로 하지 않음(REJECTED/거부된 건을 다시 승인하려면 새 요청을 만들어야 함 — 상태기계가 REJECTED→APPROVED 전이를 허용하지 않음) | "확인불가 시 통지"는 환불 쪽에 아직 없음(별도 알림 미배선) | — | GPT가 코드로 구현 가능(통지 보강) |
| 9-17 | 같은 반품·환불 중복실행 항상 차단 | IMPLEMENTED | `ReturnOrder`가 (company_id, idempotency_key) UNIQUE — 반품(물류) 확인. 2026-09-10 Phase 8: `Refund`도 동일하게 (company_id, idempotency_key) UNIQUE로 환불(금액) 중복 생성을 차단(`test_create_refund_enforces_idempotency_key_uniqueness`) | — | — | — |

### 10. 상품·이미지·규제 데이터

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 10-1 | 매입처 공식API를 우선자료로, API로만 제한 안 함 | PARTIALLY_IMPLEMENTED | `ProductCandidateEvidence.evidence_type`이 여러 출처 구분 설계, 실API 경로 존재 | API 외 자료 수집·병합 한 흐름 확인 못함 | — | — |
| 10-2 | 지정URL·검색결과의 공개정보·설명·이미지 함께 분석 | PARTIALLY_IMPLEMENTED | `url_import_service.py`/`product_page_extraction.py`/`image_search_providers.py` 파일 존재 | 실제 라이브 연동(네이버 검색 API 등) 여부, FAKE/실 비율 미조사 | provider별 FAKE/실 여부 개별 확인 | VERIFICATION_REQUIRED |
| 10-3 | 웹페이지 내용은 분석자료로만, 페이지 내 명령 실행 안 함 | VERIFICATION_REQUIRED | 코드베이스 전역 관례로 확인되나 해당 파일 라인단위 확인은 못함 | 프롬프트 인젝션 방지 코드 직접 검증 필요 | `product_page_extraction.py` 상세 리뷰 | — |
| 10-4 | 자료 간 상품명·옵션·수량·크기·제조사·원산지 불일치 시 자동등록 중지 | IMPLEMENTED [2026-09-15 Phase 9G, 2026-09-16 Adapter 계약 확장] | `app/domains/product_attribute_match`(신규 도메인) — 상품명/옵션/수량/사이즈/제조사/원산지 6개(+모델명/인증정보 2개, 표시·비교만 하고 차단 필수 항목은 원문 6개 그대로 유지) 필드를 매입처·판매채널·HOMEZ 현재 값 3소스로 정규화(공백 접기+대소문자 무시) 비교. **문자열 유사도는 절대 쓰지 않는다**(정규화 후 완전 일치만 MATCHED). 소스가 1개뿐이면 UNCONFIRMED, 2개 이상이 다르면 MISMATCHED. **2026-09-16**: `ProductLookupResult`에 `ChannelAttributeValue`(값+출처+확인시각+해석가능여부) 타입의 6개 필드(manufacturer/origin_country/model_name/package_quantity/size_specification/certification_identifiers)를 추가하고, `PurchaseChannelConnectionService.lookup_product()`(연결 서비스 경로)와 `order_submission_service.py`의 발주 직전 조회(어댑터 직접 호출 경로, 별도 배선) 양쪽에서 실제 조회 성공마다 자동으로 `run_comparison()`을 호출해 최신 비교를 기록하도록 연결했다 — 더 이상 "비교를 누가 실행하는지 없음" 상태가 아니다. `tests/test_product_attribute_match_service.py`(20개)+`ChannelAttributeComparisonTriggerTestCase`(5개, 일치/불일치/누락/형식오류/발주경로 전부 커버)로 검증 | 온채널 공식 스펙(`docs/HOMEZ_ONCHANNEL_OPENAPI_SPEC_20260908.json`)의 `GET seller/product/{code}` 응답에 `gosi_info`(정보고시) 필드가 있으나 내부 키를 전혀 문서화하지 않았고, 그 정보고시 전용 API(`GET common/gosi`)도 스펙에 응답 스키마가 없다 — 그래서 실제 온채널 Adapter는 이 6개 필드를 전부 정직하게 UNKNOWN으로 돌려준다(추측 매핑 금지). 이 상태에서는 필수 6개 항목 중 제조사/원산지/수량/사이즈 4개가 항상 UNCONFIRMED가 되어, 실제 조회가 성공할 때마다 그 상품의 다음 발주·등록 시도가 자동으로 BLOCKED되는 것이 현재의 실제 동작이다(사람이 콘솔에서 값을 확인·선택해야 해소됨 — 10-5 UI) | 온채널에 `gosi_info`/`common/gosi`의 실제 내부 필드 키를 문의하거나, 실제 인증 호출로 응답을 1회 확인 | 외부 답변 필요(공식 필드 키) 또는 사용자의 명시적 실행 승인 필요(실제 API 1회 호출) |
| 10-5 | 불일치값 나란히 표시, 사용자 선택 | IMPLEMENTED [2026-09-15 Phase 9H, 2026-09-16 브라우저 실사용 검증 완료] | 콘솔 "상품 속성 비교" 화면(목록→상세) — 상세 화면이 필드별로 매입처 값/판매채널 값/HOMEZ 현재 값을 각각 출처·확인시각과 함께 나란히 표시하고, 불일치·확인불가 항목마다 라디오(발견된 값들)+직접입력 중 하나를 사람이 고르게 한다(**서버가 기본값을 미리 선택해두지 않음**). **2026-09-16**: 격리 스크래치 서버(`pac_ui_verify.db`, 합성 데이터 8필드 MATCHED/MISMATCHED/UNCONFIRMED 혼합)에 실제 로그인해 데스크톱(1280×900)·모바일(390×844) 양쪽에서 실행 확인 — (1) 상세 화면에서 8개 필드 전부 3소스+출처+확인시각이 정확히 렌더링됨, (2) 처리 버튼이 처음엔 `disabled`이고 사유+모든 선택을 채워야만 실시간으로 활성화됨(신규 client-side 검증 추가), (3) 실제 라디오 선택+직접입력+제출까지 end-to-end로 실행해 실제 `resolve` API 호출과 읽기전용 처리완료 화면 전환을 확인, (4) 모바일 390px에서 `document.body.scrollWidth`가 뷰포트와 정확히 일치(가로 스크롤 없음), 콘솔 오류 0건, `getClientRects()`로 실제 줄바꿈 조각까지 확인해 라디오·직접입력 라벨이 시각적으로 겹치지 않음을 확인(바운딩박스 합집합만 봤을 때의 오탐 1건은 실제 겹침이 아니었음을 직접 재확인) | 매입처 실제 데이터(10-4의 어댑터 계약 한계)가 아직 상품명 외엔 항상 UNKNOWN이라, 실사용 화면에 뜨는 "매입처 값" 칸은 실제 운영에서는 당분간 대부분 "—"로 보일 것이다(코드·UI 결함이 아니라 10-4의 외부 데이터 한계가 그대로 반영된 것) | — | — |
| 10-6 | 네이버이미지API·매입처이미지·지정URL에서 이미지후보 수집 | VERIFICATION_REQUIRED | `image_search_providers.py` 존재, 네이버 자격증명 화면 존재(타 문서 기록) | 실제 라이브 호출 여부 미확인 | provider 상세 확인 | VERIFICATION_REQUIRED |
| 10-7 | 파일해시+이미지유사도로 중복이미지 반복노출 방지 | PARTIALLY_IMPLEMENTED | `MediaAsset.sha256_hex`로 정확중복(해시) 구현 | "이미지 유사도"(근사중복) 검사 미발견 | — | GPT가 코드로 구현 가능(유사도 검사 추가) |
| 10-8 | AI가 기본이미지로 배경제거·밝기보정·크기변경·배경제작·변형 여러 개 생성 | PARTIALLY_IMPLEMENTED | `ImageGenerationJob/Result` 모델 완비, `image_processing_providers.py` 존재 | `provider_code`가 FAKE/DISABLED뿐 — 실 AI 이미지생성 미가동(모델 주석 명시) | 실 Provider 연동상태 확인 | GPT가 코드로 구현 가능(실Provider 연동) |
| 10-9 | 생성결과는 사용자 확인 후 선택 | VERIFICATION_REQUIRED | 여러 후보 남는 append-only 설계 확인 | 선택 확정 API 존재여부 라우터 상세 미확인 | `media_asset/router.py` 상세 확인 | — |
| 10-10 | 원본·AI결과 분리보관, 원본 복원 가능 | IMPLEMENTED | `asset_role`(ORIGINAL/GENERATED/CHANNEL_VARIANT)+`source_asset_id` 계보, 원본 불변 명시 | — | — | — |
| 10-11 | 각 결과에 원본·처리방식·생성시각·모델정보 기록 | IMPLEMENTED | `model_name/provider_code/requested_at/completed_at/prompt_fingerprint` 등 | — | — | — |
| 10-12 | HOMEZ가 상품명·상세설명 작성, 사용자 확인 후 채널 반영 | VERIFICATION_REQUIRED | `coupang_contents_builder.py`/`draft_content_provider.py`/승인흐름 파일 존재 | 상세 로직까지 미확인(범위 밖) | listing_wizard_service.py 상세 확인 | — |
| 10-13 | 무게·성분·인증번호 등 확인안된 값 AI가 추측 안 함 | IMPLEMENTED(원칙확인) | fail-closed 원칙 전역 일관 확인(UNKNOWN, 추측 확정 안 함 등) | — | — | — |
| 10-14 | 확인안된 항목은 정확히 `확인 필요`로 표시 | VERIFICATION_REQUIRED | 원칙은 코드전역 확인되나 정확히 이 라벨문자열이 무게/성분/인증번호 필드에 쓰이는지 미매칭 | UI 라벨 표기 재확인 필요 | console.js/i18n에서 문자열 실사용 확인 | — |
| 10-15 | 자료제공처명·원문URL·확인화면 등 함께 표시 | PARTIALLY_IMPLEMENTED | `MediaAsset.source_url/source_domain`, 정책 근거(`official_source_url`) 존재 | 상품스펙 필드별 출처URL 노출은 별도확인 못함 | 상품스펙 근거표시 UI 확인 | — |
| 10-16 | 식품·어린이제품·전기제품 등 필수정보 확인불가 시 자동등록 안 함 | PARTIALLY_IMPLEMENTED | `RESTRICTED_CATEGORY_CERTIFICATION_REQUIRED` 규칙(KC인증 등 필수증빙, 공식출처 근거), severity ACTION_REQUIRED | severity가 BLOCKING이 아닌 ACTION_REQUIRED — 실제 등록차단 여부는 평가엔진 판정로직 추가확인 필요, 쿠팡 한정 | ChannelPolicyEvaluation 판정 로직 확인 | GPT가 코드로 구현 가능(엔진 판정 확인·보완) |
| 10-17 | 판매중지·회수대상 여부 매일 자동확인 | PARTIALLY_IMPLEMENTED [2026-09-15 Phase 9I, 2026-09-16 공식 출처 확인·실제 Provider 코드 준비 완료] | `app/domains/recall_notice`(신규 도메인) — `RecallNoticeProvider` Protocol 계약+`FakeRecallNoticeProvider`(부분실패 시뮬레이션 지원)로 매일확인 메커니즘 전체(수집→dedupe→`RecallCheckRun` 기록, 이미 처리한 항목은 실패 도중에도 보존)를 구현·검증. 스케줄러 Job(`RECALL_NOTICE_CHECK_JOB_ID`, 매일 05:00 Asia/Seoul)을 `app/main.py` lifespan에 실제로 등록. `tests/test_recall_notice_service.py`(23개)+`tests/test_scheduler_jobs.py::RecallNoticeCheckJobTestCase`(3개)로 검증. **2026-09-16 추가**: 식약처(MFDS) "식품 회수·판매중지 정보"(공공데이터포털 #15074318, 서비스ID I0490) 공식 API 요청/응답 스펙을 문서로 완전히 확인(`docs/HOMEZ_RECALL_DATA_SOURCE_RESEARCH_20260916.md`), 그 스펙대로 파싱하는 실제(미호출) `MfdsRecallNoticeProvider` 작성(`app/domains/recall_notice/mfds_provider.py`), 파싱 로직을 합성 JSON 20개 테스트로 검증(`tests/test_recall_notice_mfds_provider.py`, 실제 네트워크 없음) | **실제 인증키가 아직 발급·등록되지 않았다** — `get_real_provider()`는 여전히 `NotImplementedError`를 던지며 `MfdsRecallNoticeProvider`를 반환하지 않는다. 파싱 로직은 문서 스펙과 동일한 구조의 합성 데이터로만 검증했고, 실제 서버 응답을 한 번도 관측하지 않았으므로 최초 1회 실제 호출로 재검증이 필요하다. Job 자동화 모드도 기본 PAUSED로 남아 있다. 일반 공산품 대상 KATS 후보(#15116894)는 요청/응답 스펙이 아직 완전히 확인되지 않았다 | MFDS 인증키 발급 신청(식품안전나라 회원가입)과 Credential Manager 등록, `get_real_provider()` 연결 및 실제 최초 1회 호출 검증 — 모두 사용자 승인 필요 | 사용자의 명시적 실행 승인 필요(MFDS 인증키 발급·실제 최초 호출 검증) — 더 이상 "출처 자체를 모른다"는 의미의 외부 답변 대기가 아니다 |
| 10-18 | 문제 확인되면 신규판매·자동발주 즉시중지+통지 | PARTIALLY_IMPLEMENTED [2026-09-15 Phase 9J, 2026-09-16 서버 관리자 알림 채널 신설] | `RecallProductBlock`(회사별 완전 격리, PENDING 개념 없이 즉시 BLOCKED로 시작)이 실제 발주(`order_submission_service.py`)와 실제 쿠팡 전송(`listing_wizard_live_service.py::preflight()`) 양쪽을 차단. 기존 접수 주문은 자동취소하지 않고 사용자 확인대상으로만 남김. 해제는 관리자+비어있지 않은 justification 입력이 있어야만 가능. `tests/test_recall_notice_service.py`의 차단/해제 12개 테스트로 검증. **2026-09-16 추가**: 신규 도메인 `app/domains/platform_alert`(회사와 무관한 플랫폼 전체 스코프 — 사용자관리자 알림과 저장 공간이 완전히 분리됨)로 "서버 관리자" 전용 알림 채널을 만들었다. 수신자 모델(`PlatformAlertRecipient`)·전달 로그(`PlatformAlertDeliveryLog`, 생성→전달시도→성공/실패→재전송 전부 기록, 감사로그와 별개 테이블)·Fake 이메일·SMS Provider(`FakePlatformAlertProvider`, 실제 Provider는 `NullPlatformAlertProvider`가 기본값이라 아무것도 실제로 보내지 않음)까지 구현. 지시문이 명시한 8종 중 7종(Migration제한모드/DB무결성실패/백업복구실패/반복Provider오류/Credential저장소오류/리콜판매중지감지/중복발주결제차단)을 실제 기존 감지·차단 지점(각각 `migration_restricted_mode.py`/`backup/service.py`/`restore/service.py`/`channel_connection_service.py`/`store_connection/service.py`(3곳)/`recall_notice/service.py`/`order_submission_service.py`)에 알림 호출만 추가하는 방식으로 배선했다(새 감지 로직을 추측으로 만들지 않았다). `tests/test_platform_alert_service.py`(15개: 수신자 없음/멱등성/실패·재시도/재시도 소진/수신자 비활성화 시 무한재시도 방지/감사로그와의 테이블 분리 확인)로 검증. 사용자관리자 5종 중 4종은 기존 `notification_center` 카탈로그가 이미 커버(A 상품검토=`CANDIDATE_REVIEW_NEEDED`, C 반품환불승인=`RETURN_EXCHANGE_APPROVAL_NEEDED`, D 가격재고변화=`PRICE_CHANGE_APPROVAL_NEEDED`, B 주문발주승인은 `purchase_task` 자체 20개 이벤트 시스템의 `REVIEW_REQUIRED`가 담당 — 중복 도메인을 만들지 않았다) | 8종 중 "스케줄러 중지"는 이 저장소에 스케줄러 자체의 정지/누락 감지 로직(heartbeat 등)이 전혀 없어(신규 기능 구현 필요, 이번 Phase 범위 밖) `PLATFORM_ALERT_CATALOG`에 `wired=False`로만 정의돼 있다. "DB 무결성 검사 실패"는 `backup/service.py`(실제 백업 생성 흐름)만 배선했고, `app/database/migration_runner.py`의 동일 검사는 의도적으로 배선하지 않았다 — 그 클래스는 `self.db_path`로 지정된 DB와 전역 `SessionLocal`이 가리키는 DB가 리허설/격리 테스트 상황에서 서로 다를 수 있어, 잘못된 DB의 알림 테이블에 쓰거나 리허설용 임시 DB를 실제 운영 상태처럼 알리는 오귀속 위험이 있다고 판단해 보류했다(추측으로 억지 배선하지 않음). "공급처 재사용여부"(사용자관리자 E)는 기존 `SUPPLIER_CONNECTION_ORDER_AUTO_PAUSED`(일시중지 알림)가 "확인이 필요하다"는 신호는 주지만, 재사용(재개) 결정 자체를 위한 전용 승인 알림은 없다(`reactivate_connection()`은 알림 없는 수동 UI 동작). "가격 확대"/"가상재고 증가" 전용 자동 call-site는 여전히 찾아 배선하지 못했다(10-18의 기존 잔여 항목, 이번 세션 범위 밖) | 스케줄러 정지 감지 로직 신설, 서버 관리자 연락처 실제 등록(운영 화면 또는 직접 DB 입력), 실제 이메일/SMS Provider 연결(전부 사용자 승인 필요), 가격확대·가상재고증가 call-site 특정, 공급처 재사용 전용 승인 알림 추가 여부 결정 | 사용자의 명시적 실행 승인 필요(서버 관리자 연락처 등록·실제 Provider 연결) — 코드·테스트는 이번 세션에서 완료 |
| 10-19 | 일반정보 하루1회, 가격·재고는 8번 더 짧은 주기 자동갱신 | NOT_IMPLEMENTED | 스케줄러 완전부재 — 8번의 TTL도 미구현 | — | — | GPT가 코드로 구현 가능 |

### 11. 개인정보·보안

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 11-1 | 고객 이름·전화·주소는 일반화면에서 일부 마스킹 | IMPLEMENTED | `sensitive_data.py`(mask_name/mask_phone/mask_address/mask_zipcode), 다수 실호출처 | — | 브라우저 렌더링 확인 | — |
| 11-2 | 개인정보 조회권한 별도 설정 | IMPLEMENTED | 2026-09-10 Phase 11: 전용 `VIEW_SENSITIVE_DATA` 권한 코드 신설(`DEFAULT_PERMISSIONS`), `/orders/{id}/sensitive-detail`+`order-submission-review`(원문 요청 시) 양쪽에 게이트 삽입. SUPER_ADMIN은 자동 통과, 비-SUPER_ADMIN 역할은 명시적 부여 전까지 항상 거부(5개 테스트로 검증). 기존 role_permission 관리 화면이 이 코드를 자동으로 노출해 UI 작업 불필요 | Phase 13에서 콘솔 화면에 새 권한 코드가 나타나는지까지는 확인하지 못함(role_permission 관리 화면 자체를 이번 브라우저 검증 표적에 포함하지 않음) | role_permission 관리 화면에서 실제 클릭 부여 확인 | — |
| 11-3 | 조회권한 있어도 비번 재인증 통과해야 원문 확인 | IMPLEMENTED | `recent_auth.py`(5분 TTL, 1회용, 5회실패 15분잠금), 12개 파일에서 사용 | — | 각 호출처가 실제로 개인정보조회에 걸려있는지 전수확인 | — |
| 11-4 | 인증 후 원문 1회만 표시, 기본 5분 | PARTIALLY_IMPLEMENTED | `RECENT_AUTH_TTL_SECONDS=300`(토큰 유효시간) | 이는 "토큰 유효시간"이지 "화면표시시간" 아님 — 화면 자동종료(재마스킹) 로직 미확인 | console.js 원문표시 타이머 UI 확인 | GPT가 코드로 구현 가능 |
| 11-5 | 원문표시시간 설정화면에서 변경 가능 | NOT_IMPLEMENTED | TTL은 코드 상수(하드코딩), DB 설정값 아님 | 설정화면 연동 코드 없음 | — | GPT가 코드로 구현 가능 |
| 11-6 | 법적보관정보와 배송상세정보 분리관리 | NOT_IMPLEMENTED | 분리설계 근거 못 찾음 | 분리 스키마·정책 자체 없음 | — | GPT가 코드로 구현 가능(법적기준 조사 선행) |
| 11-7 | 불필요한 상세정보 삭제·강한마스킹 | NOT_IMPLEMENTED | 자동삭제·재마스킹 배치/스케줄러 없음 | 삭제정책 실행코드 없음 | — | GPT가 코드로 구현 가능(법적기준 확정 후) |
| 11-8 | 정확한 보관기간은 법적기준·정책조사로 별도확정 | NOT_IMPLEMENTED | 조사결과 문서·상수 없음 | — | — | 외부업체(판매채널) 정책+사용자 법적기준 조사 필요 |
| 11-9 | API키·JWT는 Windows Credential Manager 저장 | IMPLEMENTED | `WindowsCredentialStore` — advapi32.dll 직접호출, non-Windows fail-closed | 실제 항목 존재여부는 원문조회 금지로 미확인(구조만) | — | — |
| 11-10 | API키·JWT·카드정보 원문을 DB/문서/로그/GitHub 미저장 | IMPLEMENTED | `mask_secret()`/`redact_free_text()`, `.gitignore` 커버. 2026-09-10 Phase 7: `payment_methods` 테이블에 카드정보 원문이 들어갈 컬럼 자체가 없음(4-11과 동일 근거). 2026-09-10 Phase 11: `mask_card_number()` 신설(이전에는 카드 마스킹 함수 자체가 없었음) | 실제 Provider 연동 시 재검증 필요(현재는 Fake Provider로만 검증) | 실제 Provider 연동 시 재검증 | GPT 구현 완료 |
| 11-11 | 로그인은 프로그램 종료 시 종료, 최대 3시간 유지 | VERIFICATION_REQUIRED (2026-09-09 Phase 2로 코드·격리테스트 완료, 실사용 미확인) | Phase 2에서 구현 완료: `app/desktop/main.py`가 프로세스 시작마다(SingleInstanceGuard로 진짜 최초 실행일 때만) Credential Manager의 콘솔 세션을 무조건 삭제(종료 시 종료 충족). `AuthSession.last_seen_at`을 매 인증 요청마다 갱신(`app/core/auth.py`)하고 `SESSION_TIMEOUT_MINUTES`(기본 180분=3시간, 기존엔 죽은 설정값이었음) 초과 시 `SessionStatus.IDLE_TIMEOUT`으로 거부 — 일반 API 호출뿐 아니라 `/auth/refresh`(타이머 기반 자동갱신)도 동일하게 막음(`refresh_service.py::rotate()`). 부수적으로 발견·수정한 별개 Critical 결함: `AuthSession.expires_at`이 로그인 시 30분 TTL로 고정된 채 회전마다 갱신되지 않아, 실제로는 로그인 30분 후부터 모든 Refresh가 진짜 폐기와 구분 안 되는 오류로 거부되던 문제(`SessionRepository.extend_expiry` 신설로 수정) — 이게 없었다면 IDLE_TIMEOUT 여부와 무관하게 세션이 30분마다 끊겼을 것. 격리 테스트 `tests/test_session_lifecycle_phase2.py` 7개 전부 통과(결함 재현 포함), `tests/test_refresh_token_security.py` 기존 14개 회귀 통과 | 실제 Desktop 앱을 띄워 "종료 후 재실행 시 로그인 화면이 뜨는지", "3시간 방치 후 요청이 실제로 거부되는지"는 실행 검증 못함(코드 경로만 확인) | 실제 Desktop 실행 환경에서의 종료·idle 시나리오 수동 확인 | — |
| 11-12 | 자동결제 설정 변경 전 비번 재확인 | IMPLEMENTED | retail_purchase 쪽 recent_auth 사용+위험변경 경고 UI. 2026-09-10 Phase 7: `app/domains/payment/router.py`의 결제수단 등록·비활성화·자동결제한도 변경 3개 엔드포인트 모두 X-Recent-Auth-Token 필수 | 실제 Provider 연동 시 재검증 필요(현재는 Fake Provider) | — | — |
| 11-13 | 로그에 누가/언제/무엇, 전화·주소·API키·카드는 항상 가림 | IMPLEMENTED | `write_audit_log` 실행자/시각/action 기록, redact 함수 존재. 2026-09-10 Phase 11: `description`이 저장 직전 항상 `redact_free_text()`를 거치도록 중앙 게이트 추가(Medium 결함 #18 해결) | — | — | — |
| 11-14 | 로그에 비밀값 원문저장 후 재표시 기능 금지 | IMPLEMENTED | `mask_secret()` 완전마스킹, 재표시 API 없음 | — | — | — |
| 11-15 | 필요 원문은 별도 보안저장소+전용권한+재인증 조회 | VERIFICATION_REQUIRED | Credential은 별도저장소+recent_auth 구조 확인 | 개인정보(전화/주소) 원문에도 동일 분리 있는지 미확인(현재는 마스킹 함수로만 방어) | 개인정보 원문이 평문 DB테이블에 있는지 스키마 확인 | GPT가 코드로 구현 가능 |
| 11-16 | 실DB 백업파일 암호화, GitHub 미업로드 | PARTIALLY_IMPLEMENTED | GitHub 미업로드 확인. 2026-09-09 Phase 5: `app/domains/backup/encryption.py`(HMAC 기반 encrypt-then-MAC, stdlib 전용) 구현+16/16 단독 테스트 통과 | 원시 모듈만 존재 — `BackupService.create_backup()`/`RestoreService` 파이프라인에는 아직 연결 안 됨(실제 생성되는 백업 파일은 여전히 평문) | 파이프라인 연결(약 15개 운영 호출부+~50개 기존 테스트 영향 분석 필요) | GPT가 코드로 구현 가능(범위가 커 별도 단계로 분리 권장) |
| 11-17 | DB복구 가능여부 매주 자동/안내기반 시험+기록 | IMPLEMENTED(메커니즘, 실행 미검증) | 2026-09-09 Phase 5: `RestoreService.run_weekly_rehearsal()` 구현(11/11 테스트). 2026-09-10 Phase 6: `app/domains/scheduler/jobs.py::run_backup_rehearsal_job()`을 매주 일요일 04:00(Asia/Seoul) 크론으로 `app/main.py` lifespan에 실제 연결 — 이제 "매주 자동"이 코드상 실재함(19/19 테스트) | 실제 서버가 이 코드로 기동돼 실제로 한 번이라도 발화한 적은 없음(실 서버 기동 자체가 이번 세션 범위 밖 — Phase 1 Migration 미적용) | 실 서버 기동 후 최초 1회 실제 발화 확인 | GPT 코드 구현 완료, 실 서버 기동 검증은 사용자 승인 필요 |
| 11-18 | 비번 반복오류·이상접근 시 로그인 차단+통지 | VERIFICATION_REQUIRED (2026-09-09 P0~P9 Phase 1로 코드·격리테스트 완료, 실 DB 미적용) | Phase 1에서 구현 완료: `migrations/20260909_00_add_login_lockout_columns.sql`(failed_login_count/locked_until/last_failed_login_at), `user/repository.py::increment_failed_login`(원자적 증가+조건부 잠금전환, 동시성 방어), `auth/service.py::login()`(잠금 시 비밀번호 검증 자체를 생략, 계정 열거 방지 위해 동일 오류 메시지 유지), `_notify_account_locked`(계정 소유자+같은 회사 SUPER_ADMIN에게 통지), `account_admin.py::unlock_user`(관리자 수동 해제). 감사기록: ACCOUNT_LOCKED/ACCOUNT_LOCKOUT_RESET/UNLOCK_USER. 격리 테스트 `tests/test_login_lockout.py` 13개 전부 통과(동시성 테스트 포함), 인접 인증 도메인 회귀 84개 전부 통과 | **실제 homez.db에는 아직 미적용**(별도 승인 대상) — 이 Migration이 실 DB에 적용되기 전까지, 실제 로그인 경로(`/auth/login`은 migration-restricted-mode 쓰기 화이트리스트라 항상 열려있음)가 `User` Model이 기대하는 새 컬럼을 실 DB에서 찾지 못해 즉시 깨질 수 있다 — 다른 pending Migration보다 우선순위 높게 적용 권장 | 실 homez.db에 이 Migration 적용(사용자 승인 필요) 후 실제 로그인 1회로 재검증 | 사용자의 명시적 실행 승인 필요(Migration 실제 DB 적용) |
| 11-19 | 긴급잠금은 상품등록·가격변경·발주·결제 한번에 중지 | IMPLEMENTED(결제 제외) | `is_emergency_stop_active()`가 상품등록/가격변경/발주 실행부에 배선 | 결제 도메인 부재로 결제차단 검증불가 | 결제도메인 구현 후 재검증 | GPT가 코드로 구현 가능(결제) |
| 11-20 | 긴급잠금 중에도 읽기·백업·진단 유지 | IMPLEMENTED | 조회성 엔드포인트가 EStop 게이트 미통과, "항상 조회가능해야 한다" 원칙 명시 | 백업 실행(생성) 자체가 잠금 중 가능한지 명시적 테스트 필요 | — | — |
| 11-21 | 긴급잠금 시작 시 서버관리자·사용자관리자에게 통지 | NOT_IMPLEMENTED | 활성화 실행자 1인에게만 발송(`ESTOP_ACTIVATED`), 역할분리 개념 자체 없음 | 역할분리 알림대상 개념 전무 | — | GPT가 코드로 구현 가능(역할정의는 사용자 결정 필요) |
| 11-22 | 개인베타 중 동일인이어도 알림역할 분리기록 | NOT_IMPLEMENTED | 11-21과 동일 근거 | — | — | GPT가 코드로 구현 가능(11-21과 함께) |

### 12. AI 분석·학습 기반

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 12-1 | 초기 AI는 추천·분석·위험알림 담당 | IMPLEMENTED | `decision` 도메인(평가/점수/추천), event_catalog 위험알림 정의 | — | — | — |
| 12-2 | 초기 AI가 자동결제·발주 안전규칙 스스로 못 바꿈 | IMPLEMENTED | `set_mode/set_limit` 호출부는 admin_guard 콘솔API 단 한 곳뿐, AI측 호출 없음 | — | — | — |
| 12-3 | AI추천/사유/점수/승인거절/실판매량/실마진/품절/취소/반품/지연/예측차이 저장 | PARTIALLY_IMPLEMENTED | `DecisionEvaluation`/`DecisionScore`/`DecisionReview` 실재. 2026-09-10 Phase 12: `app/domains/ai_learning::DecisionOutcome`(실판매액·실마진액·품절·취소·반품·배송지연, evaluation_id로 연결) 신설+`record_outcome()` | "예측 차이"(예상값 대비 실제값 차이) 자체를 계산하는 코드는 없음(값만 나란히 저장, 차이 계산 로직 없음) | 예측차이 계산 로직 추가 | GPT가 코드로 구현 가능(보강) |
| 12-4 | 사용자 수정 시 변경내용·이유 저장 | IMPLEMENTED | `DecisionReview.action="OVERRIDE"`+override_reason/previous/new value | — | — | — |
| 12-5 | 변경이유는 짧은 선택항목+선택적메모 | PARTIALLY_IMPLEMENTED | override_reason이 자유텍스트(1000자) | enum형 "짧은 선택항목" 구조 미확인(자유텍스트만) | console.js 입력위젯 확인 | GPT가 코드로 구현 가능 |
| 12-6 | 초기학습검토는 최소 50건 완료주문부터 시작가능 | IMPLEMENTED | 2026-09-10 Phase 12: `app/domains/ai_learning`(`LearningDatasetRecord`+`check_learning_readiness()`, `INITIAL_MIN_SAMPLE_SIZE=50`) 신설, 21개 테스트 통과 | — | — | — |
| 12-7 | 50건 결과는 자동미적용, 후보생성·비교에만 사용 | PARTIALLY_IMPLEMENTED | 2026-09-10 Phase 12: `ModelCandidate` 상태기계 — 승인해도 실제 라이브 정책에 적용하는 코드가 어디에도 없음(자동미적용 원칙 구조적으로 충족) | "비교"는 사람이 쓰는 자유텍스트 요약(`regression_comparison_summary`)뿐 — 정량 비교 계산은 없음 | 정량 회귀비교 계산 로직 | GPT가 코드로 구현 가능(보강) |
| 12-8 | 누적1,000건 이상 시 최소학습건수 100건 상향 | IMPLEMENTED | 2026-09-10 Phase 12: `RAISE_THRESHOLD_TOTAL_ORDERS=1000`+`RAISED_MIN_SAMPLE_SIZE=100`, 경계값 포함 테스트 통과 | — | — | — |
| 12-9 | 1,000건 기준·최소학습건수 설정화면에서 변경 | NOT_IMPLEMENTED | 2026-09-10 Phase 12: 값 자체는 `app/domains/ai_learning/constants.py`에 코드 상수로 존재(12-8) | 설정화면에서 변경 가능한 DB 설정으로는 아직 안 옮김(하드코딩 상수) | — | GPT가 코드로 구현 가능(설정화면 연동) |
| 12-10 | 정상/취소/반품/손실 사례가 평가에 충분히 포함됐는지 확인 | NOT_IMPLEMENTED | 학습 파이프라인 부재 | — | — | GPT가 코드로 구현 가능 |
| 12-11 | 결과유형 부족하면 건수충족해도 학습적용 보류 | NOT_IMPLEMENTED | 동일 | — | — | GPT가 코드로 구현 가능 |
| 12-12 | 새 학습후보 생성가능하나 운영모델 자동교체 안 함 | IMPLEMENTED | 2026-09-10 Phase 12: `create_model_candidate()`로 후보 생성 가능. APPROVED까지 가도 실제 라이브 정책 교체 코드가 전혀 없어 자동교체가 구조적으로 불가능(`app/domains/ai_learning/service.py` 상단 주석에 명시) | — | — | — |
| 12-13 | 새 모델은 과거데이터평가+사용자승인 통과해야 적용 | PARTIALLY_IMPLEMENTED | 2026-09-10 Phase 12: DRAFT→OFFLINE_EVALUATED→REGRESSION_COMPARED→APPROVED 상태기계로 순서를 건너뛸 수 없게 강제(4개 테스트), 승인은 재인증(recent-auth) 필요 | "과거데이터평가"의 실제 정량 채점은 없음(요약 텍스트만) — 애초에 "적용" 자체가 이 세션 범위 밖이라 "적용 전 통과해야 하는 게이트"는 만들었지만 그 뒤의 "적용" 코드는 없음 | 정량 오프라인평가 계산+실제 적용 연결 | GPT가 코드로 구현 가능(보강, 적용 연결은 별도 승인 필요) |
| 12-14 | 적중률·마진차이·예측·차단률·오차단률로 평가 | NOT_IMPLEMENTED | 평가지표 정의·계산 코드 없음 | — | — | GPT가 코드로 구현 가능 |
| 12-15 | 새 모델은 기존보다 전체기준 좋아야 교체후보 | NOT_IMPLEMENTED | 동일 | — | — | GPT가 코드로 구현 가능 |
| 12-16 | 새 모델은 그림자모드로 최소2주 시험 | NOT_IMPLEMENTED | 동일 | — | — | GPT가 코드로 구현 가능 |
| 12-17 | 테스트상품/입력실수/API오류/결과불명 데이터는 학습제외 | NOT_IMPLEMENTED | 학습자체 없어 제외로직도 없음(RESULT_UNKNOWN 구분은 발주레벨에만 존재, 용도 다름) | — | — | GPT가 코드로 구현 가능 |
| 12-18 | 모든 모델버전·평가 보관, 문제시 이전모델로 즉시복구 | PARTIALLY_IMPLEMENTED | 2026-09-10 Phase 12: `ModelCandidate`(version 필드)+`ModelCandidateStatusEvent`(append-only 이력)로 후보 모델 버전·상태 변화가 전부 보관됨 | "이전 모델로 즉시 복구"하는 실행 코드는 없음(애초에 "적용" 자체가 없으니 "복구"할 라이브 상태도 없음) | 실제 적용·복구 메커니즘(적용 연결 이후 과제) | GPT가 코드로 구현 가능(적용 연결 이후) |
| 12-19 | AI화면에 점수·판단이유·데이터·자료시각·확신도 표시 | PARTIALLY_IMPLEMENTED | recommendation_reason/axis별 raw_score/weight/confidence/evidence_text 렌더링 확인 | "자료시각(데이터 기준시각)" 표시여부 미확인 | console.js decision 상세 timestamp 필드 확인 | GPT가 코드로 구현 가능(부분) |
| 12-20 | 근거부족 판단은 `확인 필요`로 표시 | PARTIALLY_IMPLEMENTED | `data_sufficient=False` 시 "데이터 부족" 렌더링 — 개념은 있으나 **문구가 다름**(Medium 결함 #19) | 정확한 문구 불일치 | — | GPT가 코드로 구현 가능(문구 통일) |

### 13. 운영 UI·관제

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 13-1 | 첫화면 위쪽에 오늘 처리할 일·위험항목 우선표시 | PARTIALLY_IMPLEMENTED | `dash-todo-list`, `orchestration/priority_service.py` 실재 | 화면 최상단 배치 여부는 렌더링 확인 필요 | 브라우저 대시보드 최상단 확인 | — |
| 13-2 | 매출통계·그래프보다 조치필요항목 우선 | VERIFICATION_REQUIRED | dash-todo-list와 kpiGrid 공존 확인 | 시각적 우선순위는 DOM만으론 단정 어려움 | 브라우저 렌더링 순서 확인 | — |
| 13-3 | 주문수집~정산 상태를 한화면에서 순서대로 표시 | PARTIALLY_IMPLEMENTED | `DashboardService.get_summary`가 여러도메인 실COUNT 집계(정직성 원칙 확인) | 결제·정산까지 같은 화면 순서배치 여부 미확인 | 브라우저 전체 파이프라인 뷰 확인 | — |
| 13-4 | 가격인상/재고미확인/배송지연/반품환불/한도초과/AI근거부족을 하나의 확인목록에 통합 | PARTIALLY_IMPLEMENTED | event_catalog에 각 이벤트 정의 존재 | 다수 이벤트가 `wired=False`(정의만, 실발생지점 없음) | 각 wired=False 이벤트의 실제 연결 확인 | GPT가 코드로 구현 가능 |
| 13-5 | 확인목록은 위험도·처리기한순 정렬 | IMPLEMENTED | priority_service가 예외분석 재사용해 우선순위 집계 | — | 정렬 알고리즘 상세 확인 | — |
| 13-6 | 알림을 긴급·주의·정보 3단계로 구분 | PARTIALLY_IMPLEMENTED | `NotificationSeverity`가 CRITICAL/HIGH/MEDIUM/LOW **4단계** — 문서요구(3단계)와 불일치 | 3단계가 아닌 4단계 체계 | — | GPT가 코드로 구현 가능(재매핑 또는 사용자 재확인) |
| 13-7 | 결제·발주·개인정보·시스템잠금은 긴급알림 | PARTIALLY_IMPLEMENTED | ESTOP_ACTIVATED=CRITICAL 확인(시스템잠금 충족) | 결제는 대상없음(도메인부재), 발주는 HIGH(최상위 아님) | — | GPT가 코드로 구현 가능 |
| 13-8 | 가격변경·재고부족·배송지연은 주의알림 | VERIFICATION_REQUIRED | 등급체계 스케일이 달라 1:1 매핑 애매 | — | 사용자와 등급매핑표 재확인 | GPT가 코드로 구현 가능 |
| 13-9 | 정상완료·정기보고는 정보알림 | VERIFICATION_REQUIRED | "정상완료 알림"에 해당하는 카탈로그 항목 자체를 못 찾음 | 정상완료 알림 이벤트 부재 가능성 | — | GPT가 코드로 구현 가능 |
| 13-10 | 기능별 수동/반자동/자동/일시중지/오류 상태 항상표시 | IMPLEMENTED | 신규 `function_automation_states`(company_id×function_code, append-only) + `SafetyService.get_all_function_modes/set_function_mode/demote_function_to_error` + `/console/api/function-modes` 라우터 + console.js UI(표+선택+변경 버튼, 한국어 상태명+사유+변경시각). 10개 기능 독립 확인, 회사 격리 확인, 미설정 시 항상 "수동" 기본값 확인(테스트 14개 통과). **2026-09-10 Phase 13**: 격리 서버에 실제 로그인해 반자동/자동/일시중지/오류 4가지 상태가 정확한 한국어 라벨+설명+사유+변경시각으로 렌더링됨을 브라우저에서 직접 확인(모바일 375px에서도 가로스크롤 없이 카드형으로 정상 표시) | "항상 표시"가 안전 화면 밖(예: 대시보드 첫 화면)에서도 보이는지는 여전히 미확인 — 안전 화면 안에서만 확인함 | 대시보드 등 다른 화면에서의 노출 여부 확인 | — |
| 13-11 | 오늘/이번달 결제액·잔여한도·API사용량·AI비용 구분표시 | PARTIALLY_IMPLEMENTED | 오늘 자금사용량/일한도 라벨 확인, `api_usage_tracker.py` 존재 | 월간 누적치, AI사용량·예상비용 확인 못함 | api_usage_tracker.py 상세 확인 | GPT가 코드로 구현 가능(부분) |
| 13-12 | 주문번호/상품명/채널/매입처/상태/날짜로 검색·필터 | VERIFICATION_REQUIRED | 필터 관련 코드 다수 존재 | 6개 필드 전부가 한 화면에 걸려있는지 미확인 | 브라우저 주문목록 필터 UI 확인 | — |
| 13-13 | 모바일에서 조회·알림확인·승인·긴급중지 지원 | VERIFICATION_REQUIRED | `mobile-nav-toggle` 반응형 네비 존재 | 실제 모바일 뷰포트 승인·긴급중지 동작 미확인 | 모바일 뷰포트 브라우저 테스트 | — |
| 13-14 | 복잡한 설정변경·대량편집은 PC화면에서 진행 | VERIFICATION_REQUIRED | 반응형 존재 확인만 | "PC 전용 제한" 강제코드 확인 못함 | 모바일에서 설정화면 접근제한 확인 | — |
| 13-15 | 주문별 작업기록+전체 작업기록 모두 제공 | PARTIALLY_IMPLEMENTED | `audit/router.py` 전체감사로그 존재 | "주문별" 필터뷰 별도존재 여부 미확인 | audit/router.py 쿼리파라미터 확인 | GPT가 코드로 구현 가능(부분) |
| 13-16 | 작업기록에 실행자·시각·종류·결과, 비밀정보 미표시 | IMPLEMENTED | write_audit_log 구조 확인. 2026-09-10 Phase 11에서 강제마스킹 게이트 추가(11-13과 동일 근거) | — | — | — |
| 13-17 | 기술오류코드보다 사용자 행동을 먼저 표시 | VERIFICATION_REQUIRED | 사람이 읽을 문자열 반환 함수들 확인(좋은 신호) | 전체화면 일관성은 UX 전수확인 필요 | 주요 오류화면 전수 확인(브라우저) | — |

### 14. 실데이터 검증·출시 판정

| 번호 | 운영 기준 | 판정 | 구현 증거 | 빠진 부분 | 필요한 검증 | 담당 |
|---:|---|---|---|---|---|---|
| 14-1 | 실데이터 시험 직전 실제업무DB 전체백업 | IMPLEMENTED(메커니즘)/VERIFICATION_REQUIRED(실행) | `create_backup` 메커니즘 확인, `backup_records` 실제 3건 존재 | 시험 직전 시점 실제 실행여부는 시험 시점 로그 필요 | 시험시점 백업 실행여부 확인 | 사용자의 명시적 실행 승인 필요(시험 자체 미실행) |
| 14-2 | 시험 전 백업 무결성·복구가능 확인 | IMPLEMENTED(메커니즘) | `PRAGMA integrity_check` 강제, `test_restore_engine.py` 존재. 2026-09-09 Phase 5: `run_weekly_rehearsal()`이 임시 DB 기준으로 백업+복구+무결성 검증 전체 흐름을 실제로 통과시킴(11/11 테스트) | 이건 임시 DB 대상 검증이고, 실제 homez.db 대상 실행결과(pass/fail)는 여전히 이 세션에서 미실행(승인 대상) | 실제 DB 대상 1회 실행결과 별도 확인 | GPT가 임시 DB로는 검증 완료, 실 DB는 사용자 승인 필요 |
| 14-3 | 등록된 상품 중 저가상품 찾아 위치·주문방법 안내 | NOT_IMPLEMENTED | 관련 산출물 없음(코드구현 대상 아니라 실행단계 성격) | — | — | 사용자의 명시적 실행 승인 필요 |
| 14-4 | 승인없이 신규등록·실주문·결제·발주·취소·환불 금지 | IMPLEMENTED(설계원칙) | 이번 세션 포함 실행 이력 전무(자체준수), `is_onchannel_order_contract_fully_confirmed()` 하드게이트 | — | — | — |
| 14-5 | 시험은 사전설명+승인 후 1회씩 실행 | NOT_IMPLEMENTED [BLOCKED_BY_USER_APPROVAL] | 실행이력 없음(당연) | — | — | 사용자의 명시적 실행 승인 필요 |
| 14-6 | 실데이터 전체과정은 실제상품 2건으로 검증 | NOT_IMPLEMENTED | 실행이력 없음(관련 테이블 0행으로 뒷받침) | — | — | 사용자의 명시적 실행 승인 필요 |
| 14-7 | 1번 상품: 정상주문~정산·손익까지 검증 | NOT_IMPLEMENTED | 미실행 | — | — | 사용자의 명시적 실행 승인 필요 |
| 14-8 | 2번 상품: 반품~환불대조·손익까지 검증 | NOT_IMPLEMENTED | 미실행 | — | — | 사용자의 명시적 실행 승인 필요 |
| 14-9 | 배송지 원문 사용 전 표시+별도승인 | VERIFICATION_REQUIRED | recent_auth 재인증 패턴 존재 | "배송지 원문표시+별도승인" 전용흐름 구현여부 미확인 | 관련 라우터 확인 | GPT가 코드로 구현 가능 |
| 14-10 | 12개 오류상황을 Fake Provider+격리DB에서 검증 | PARTIALLY_IMPLEMENTED | 2026-09-10 Phase 14: 12개 항목 통합 매트릭스를 실제로 작성함(docs/HOMEZ_PROJECT_STATE.md 2026-09-10 후속 25 절) — 8/12(정상성공·품절·옵션불일치·중복주문·인증만료·타임아웃·결과불명·배송지연) 확실히 커버 확인 | 3/12(가격인상의 retail_purchase 쪽, API지연의 시간재현, 반품·부분환불의 "부분" 개념) 부분 커버, 1/12(중복 결제)는 결제 실행 메서드 자체가 없어 사실상 미커버 | 부족한 3.5개 시나리오 테스트 보강(중복결제는 결제실행 메서드 설계 선행 필요) | GPT가 코드로 구현 가능(보강, 성급히 하지 않기로 판단) |
| 14-11 | 12개 전체통과를 베타시작 일괄조건에서 제외 | IMPLEMENTED(정책상) | 문서 정책 문장, 코드구현 대상 아님 | — | — | — |
| 14-12 | 중복주문·결제/타임아웃/결과불명 미통과 기능은 자동모드로 안 엶 | VERIFICATION_REQUIRED | RESULT_UNKNOWN LOCKED 상태 확인 | 이 상태가 자동모드 개방여부와 실제 연동됐는지 미확인(전역단일모드라 기능별 게이팅 구조적 불가 — 13-10과 동일) | — | GPT가 코드로 구현 가능 |
| 14-13 | 같은 주문 반복처리해도 결제·발주 각1회만 발생 | IMPLEMENTED(발주만) | idempotency UNIQUE+DUPLICATE_REQUEST DENY+LOCKED 상태 — 발주측 매우 견고 | 결제측은 도메인부재로 검증불가 | 결제도메인 구현 후 동일수준 검증 | GPT가 코드로 구현 가능(결제) |
| 14-14 | 결제·발주 결과불명 시 자동재시도 안 함 | IMPLEMENTED(발주만) | RESULT_UNKNOWN이 LOCKED에 포함(자동재시도 차단) | 결제측 미확인(도메인부재) | — | GPT가 코드로 구현 가능(결제) |
| 14-15 | 긴급중지는 실결제 전 Fake환경에서 먼저검증 | VERIFICATION_REQUIRED | EStop 관련 테스트 다수 존재로 추정 | "Fake전용 EStop검증테스트" 명시적 특정 못함 | test_automation_safety.py 등 전수확인 | GPT가 코드로 구현 가능(존재 시 매핑만) |
| 14-16 | 긴급중지 시 등록·가격변경·결제·발주 멈추고 조회·백업·진단 유지 | IMPLEMENTED(결제 제외) | 11-19/11-20과 동일 근거 | 결제 미확인 | — | GPT가 코드로 구현 가능(결제) |
| 14-17 | 결제·발주·개인정보노출·중복처리 결함 허용 안 함 | VERIFICATION_REQUIRED(→해소됨) | 정책선언, Track A 최종 전체회귀 3835/3835 통과로 알려진 결함 0건 확정 | 결제 도메인 자체가 없어 "결제 결함 0건"은 검증대상 부재 상태 | — | — |
| 14-18 | 화면문구·배치문제는 안전무관 시 베타중 수정가능 | IMPLEMENTED(정책상) | 문서 정책 문장 | — | — | — |
| 14-19 | 베타시작조건=전체회귀+Migration정합성+백업복구시험+실제상품2건+긴급중지검증 | VERIFICATION_REQUIRED [BLOCKED_BY_USER_APPROVAL] | **전체회귀는 Track A에서 3835/3835 통과로 충족.** Migration 정합성도 Track A에서 확인·수정 완료. 백업/복구는 메커니즘만 존재(시험실행·기록 미확인), 실제상품 2건은 미실행 | 백업복구 "시험실행·기록", 실제상품2건 검증 미실행 | — | 사용자의 명시적 실행 승인 필요 + 실제 주문·결제 데이터 축적 필요 |
| 14-20 | 12개 전체통과는 베타조건서 제외, 기능별 자동모드조건으로 관리 | PARTIALLY_IMPLEMENTED (Phase 3) | 기능별 관리구조는 이제 존재(FunctionCode×FunctionMode) | "12개 오류상황 통과"와 각 기능의 AUTOMATIC 전환을 실제로 연동하는 게이트는 아직 없음(그 12개 시나리오 자체가 아직 실행되지 않음, 14번 후반부 대상) | 12개 시나리오 실행 후 연동 | 사용자의 명시적 실행 승인 필요(12개 시나리오는 실데이터 인접) |
| 14-21 | 조회/분석/등록/가격변경/결제/발주 각각 별도 자동모드 관리 | IMPLEMENTED (Phase 3) | `function_automation_states`가 회사×기능별 독립 컬럼 보유, 기능 간 완전 독립 확인(테스트: 한 기능 변경이 나머지 9개에 영향 없음) | — | — | — |
| 14-22 | 각 기능은 안전시험 통과 후 소액한도로 자동모드 개방 | NOT_IMPLEMENTED | 모드 저장·조회 구조는 있으나 "안전시험 통과"를 전제조건으로 강제하는 게이트는 없음(현재는 관리자가 바로 AUTOMATIC으로 바꿀 수 있음 — 개인 베타 중에는 실질적 영향 없음, 2-10/6-12 등 다른 게이트가 이미 실행을 막고 있기 때문) | 안전시험 결과와 모드 전환을 연동하는 게이트 부재 | — | GPT가 코드로 구현 가능(14번 실데이터 시험 이후) |
| 14-23 | 문제발생 시 해당기능만 반자동/일시중지로 낮춤 | PARTIALLY_IMPLEMENTED (Phase 3) | `SafetyService.demote_function_to_error()` 신설 — 호출되면 해당 기능만 ERROR로 낮아지고 나머지는 영향 없음(테스트로 확인). `set_by=0`으로 시스템 강등과 사람 조작을 감사에서 구분 | 이 메서드를 실제로 호출하는 감지 코드(가격 인상·재고 부족·인증 만료·API 오류·스키마 불일치 감지)는 아직 없음 — Phase 4(긴급 중지와 실행 게이트) 범위 | Phase 4에서 실제 감지 지점에 연결 | — |
| 14-24 | 화면에 `수동/반자동/자동/일시중지` 한국어 상태명+설명 표시 | IMPLEMENTED (Phase 3) | `FunctionMode.LABELS_KO`/`DESCRIPTIONS_KO`가 정확히 이 4개 한국어 이름(+오류)과 설명을 갖고, `/console/api/function-modes` 응답과 console.js 렌더링에 그대로 노출됨 | 기존 전역 단일 모드 화면(`safety.mode_title`)은 여전히 영문 상수로 표시됨(그대로 둠 — 기능별 화면과 병존) | — | — |
| 14-25 | 자동화단계는 토글/단계선택 컨트롤로 쉽게 조절 | PARTIALLY_IMPLEMENTED | 기능별 select+버튼 컨트롤 존재(Phase 3) | 토글 형태(더 간단한 UI)는 아님, select+버튼 방식 — Phase 13에서 UX 개선 여지 | — | — |

### 섹션별 요약 개수 — 2026-09-15 전면 감사 후속 Phase 11 재계산

> **Phase 11 정정 내역**: 이 요약표는 2026-09-09 Phase 0 정합화 이후 **한 번도 재계산되지 않은 채 방치**돼 있었다 — 그 사이 2026-09-09~2026-09-15에 걸친 여러 Phase(결제/환불/스케줄러/환율/가상재고 등 도메인 신설, 그리고 이번 세션의 Phase 9A~9K)가 개별 행(1~14번 표)의 판정을 수십 건 갱신했는데도, 이 표의 숫자는 옛 2026-09-09 기준선(58/52/84/29) 그대로였다. **이전 보고가 "10개 항목이 모두 끝난 것처럼" 읽힐 수 있었던 프레이밍 오류를 여기서 바로잡는다** — 실제로 이번 세션이 완결 처리한 항목은 7-8, 7-11, 8-5, 8-6, 8-16, 8-19, 10-4의 7개(IMPLEMENTED)뿐이고, 7-16·10-5·10-17·10-18의 4개는 각각 이유가 다른 PARTIALLY_IMPLEMENTED로 남는다(아래 개별 표·§11-A 참고). 이 표는 **223개 전체 행을 프로그램으로 다시 파싱해** 재계산한 값이며(수기 집계 아님), 프로그램 파싱 특성상 세션 도중 갱신되지 않은 나머지 ~200여 개 행은 각 행이 마지막으로 갱신된 시점의 판정을 그대로 반영한다 — "이번 세션이 그 항목들을 재검증했다"는 뜻이 아니다.

| 섹션 | 전체 세부기준 | 구현완료 | 부분구현 | 미구현 | 검증필요 | [보조] 외부답변대기 | [보조] 사용자설정필요 | [보조] 사용자승인필요 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1. 기본 운영 설정 | 6 | 3 | 1 | 1 | 1 | 0 | 1 | 0 |
| 2. 판매채널 운영 방식 | 10 | 4 | 2 | 3 | 1 | 1 | 0 | 0 |
| 3. 매입처 운영 방식 | 11 | 3 | 1 | 3 | 4 | 0 | 0 | 1 |
| 4. 결제수단 운영 방식 | 14 | 2 | 4 | 6 | 2 | 4 | 0 | 1 |
| 5. 자동결제 한도 | 11 | 4 | 4 | 3 | 0 | 0 | 3 | 0 |
| 6. 수익성 기준 | 15 | 5 | 5 | 5 | 0 | 0 | 2 | 0 |
| 7. 상품·공급처 평가 | 17 | 9 | 4 | 3 | 1 | 0 | 0 | 1 |
| 8. 가격·재고·배송 관리 | 19 | 11 | 4 | 3 | 1 | 0 | 0 | 1 |
| 9. 반품·환불·정산 | 17 | 9 | 3 | 5 | 0 | 0 | 0 | 0 |
| 10. 상품·이미지·규제 데이터 | 19 | 5 | 8 | 1 | 5 | 1 | 0 | 0 |
| 11. 개인정보·보안 | 22 | 11 | 2 | 6 | 3 | 1 | 0 | 0 |
| 12. AI 분석·학습 기반 | 20 | 6 | 7 | 7 | 0 | 0 | 0 | 0 |
| 13. 운영 UI·관제 | 17 | 3 | 7 | 0 | 7 | 0 | 0 | 0 |
| 14. 실데이터 검증·출시 판정 | 25 | 10 | 4 | 6 | 5 | 0 | 0 | 7 |
| **합계(기본 판정 4종만 — 전체 기준 수와 정확히 일치)** | **223** | **85** | **56** | **52** | **30** | **7** | **6** | **11** |

**검산**: 85 + 56 + 52 + 30 = 223 = 전체 세부기준 수(재계산 스크립트로 `grep`/정규식 파싱해 재확인, 수기 집계 아님). 보조 태그(외부답변대기 7 + 사용자설정필요 6 + 사용자승인필요 11 = 24건)는 기본 판정 위에 얹힌 부가 정보이며 별도로 합산하지 않는다(중복 계상 금지). **2026-09-16 갱신**: 10-5가 브라우저 실사용 검증을 통과해 PARTIALLY_IMPLEMENTED→IMPLEMENTED로 바뀌면서 83→84/58→57로 재조정됐다. 이후 2-8이 5분 자동 감지 스케줄러 구현으로 PARTIALLY_IMPLEMENTED→IMPLEMENTED로 바뀌면서 84→85/57→56으로 다시 재조정됐다(그 밖의 재계산 로직·아직 손대지 않은 행은 전날과 동일).

**2026-09-09 → 2026-09-15 변화**: 구현완료 58→83(+25), 부분구현 52→58(+6), 미구현 84→52(−32), 검증필요 29→30(+1). 이 전체 변화분 중 이번 세션(Phase 9A~9K)이 직접 만든 변화는 7건의 미구현→구현완료(7-8/7-11/8-5/8-6/8-16/8-19/10-4)와 4건의 미구현→부분구현(7-16/10-5/10-17/10-18)뿐이다 — 나머지 변화는 2026-09-09~09-14 사이의 이전 세션들(결제/환불/스케줄러/환율/개인정보/로그인잠금 등)이 만든 것으로, 이번 세션은 그 항목들을 재검증하지 않았고 그 판정을 있는 그대로 가져왔을 뿐이다.

(개수는 각 조사 세션이 문서 불릿을 실제로 몇 개로 분리했는지에 따라 소폭 편차가 있을 수 있음 — 정확한 개별 판정은 위 개별 표를 원본으로 한다.)

---

## 4. 사용자만 할 수 있는 설정 (BLOCKED_BY_USER_CONFIGURATION)

이미 화면/필드는 존재하나 값이 비어있거나 사용자 결정이 필요한 항목:

- **6-1, 6-2**: 최소 마진율 15%, 원가상승 허용률 3% — 필드는 있으나 기본값이 0/5%로 문서와 다름. 회사 정책 설정화면에서 직접 입력 필요.
- **5-1, 5-2, 5-3**: 자동결제 한도(건당 5만/일 10만/월 50만원) — `PUT /purchase-tasks/policy` 화면은 이미 있으나 값이 비어있음(None). 다만 이 화면이 실제 실행경로에서 최종 참조되는지(Critical 결함 #7 참고)는 GPT가 먼저 확인·정리해야 함.
- **1-2**: 개인 베타 단일사용자 운영 — 현재 운영정책으로 충족(users=1), 다중사용자 허용 시점은 사용자 결정.
- **11-5, 12-9**: 원문표시시간, 1,000건/최소학습건수 — 설정화면 자체를 먼저 구현해야 사용자가 값을 정할 수 있음(선행조건은 GPT 작업).

---

## 5. GPT가 승인 없이 진행할 수 있는 작업 (BLOCKED_BY_USER_CONFIGURATION/APPROVAL/EXTERNAL이 아닌 대부분)

전체 223건 중 약 130건 이상이 "GPT가 코드로 구현 가능"으로 분류됐다(위 표 참고, 순수 코드/스키마/UI 작업으로 외부 계약이나 실행 승인이 필요 없는 항목). 우선순위가 높은 대표 항목만 추리면:

- **Critical/High 결함 수정**(2절 참고): 배송비 미확인 시 실제 차단(6-3), 마진율 UI 소수→퍼센트 통일(6-15), 로그인 실패 잠금 DB컬럼+로직 추가(11-18), 자동결제 한도 이중구조 정리(5-4), 감사로그 강제 마스킹 게이트(11-13/13-16)
- ~~**스케줄러 인프라 신설**: `app/domains/scheduler` 구현+`app/main.py` 연결 — 2-5, 2-8, 9-13, 10-17, 10-19, 11-17 등 수십 개 항목이 동시에 해소됨~~ **[2026-09-10 Phase 6에서 인프라만 해소, 원래 예상은 과했음]** 인프라(`SchedulerService`+`app/main.py` 연결)는 실제로 완료됐고 11-17(백업 복구 리허설)은 그 위에서 실제로 연결·자동화됐다. 하지만 2-5(가격 검토)/2-8(주문 수집)/9-13/10-17/10-19처럼 **실제 외부 API를 호출하는 항목들은 인프라만으로 저절로 해소되지 않는다** — 각각 (a) 자동화 모드 게이트 연결, (b) 시스템-주체 감사 표현 방식 결정, (c) 일부는 저장소 계층 자체(가격/재고 모니터링)를 새로 설계해야 하는 별도 작업이 남아있다(상세 조사 결과: `docs/HOMEZ_PROJECT_STATE.md` 2026-09-10 후속 17). "인프라만 만들면 수십 개가 한 번에 풀린다"는 원래 감사의 이 추정은 과했다 — 항목별로 개별 배선이 필요하다.
- **환불(refund) 도메인 신설**: 9-4~9-10 전체
- ~~**가상재고 개념 신설**: 7-7, 7-8, 8-17, 8-19~~ **[2026-09-15 Phase 9A/9F 완료]** 7-8(호출제한·캐시TTL)·8-19(판매가능 확인불가 시 가상재고 0 제안)는 IMPLEMENTED. 7-7(가상재고 임계값 게이트)은 이미 이전 Phase에서 PARTIALLY_IMPLEMENTED. 8-17(초기 가상재고를 낮게 설정+기본수량 변경)은 여전히 손대지 않음(별도 작업 필요) — `InventorySku`(실물창고)와 `VirtualStockThreshold`/`PriceStockQuoteCache`(가상재고·가격/재고 안전장치)는 이제 명확히 분리된 별도 계층이다.
- **환율(currency/exchange) 도메인 신설**: 6-10, 6-11
- **알림 이벤트 배선 보강**(`wired=False` 항목들): 13-4, 13-8, 13-9
- **자동모드 기능별 스키마 확장**: 13-10, 14-20~14-25 (모델·Migration 설계는 GPT가 가능하나, 실제 DB 적용은 기존 원칙대로 사용자 승인 필요)

---

## 6. 사용자 승인이 필요한 작업 (BLOCKED_BY_USER_APPROVAL)

- **3-6**: 첫 실발주를 사용자 자택 배송지로 검증(실금전 지출)
- **4-14**: 자택 테스트발주 성공 후 소액한도 자동결제 시작
- **7-3, 8-11**: 실제 온채널 계정으로 API 최초 1회 호출 검증(현재까지 이 세션들에서 단 한 번도 실행 안 됨)
- **10-17**(2026-09-16 이동, 이전에는 §7 외부 답변 필요 목록에 있었음 — 이제 공식 출처(MFDS I0490)가 확정되고 Provider 코드도 준비됐으므로 남은 건 실행 승인뿐): MFDS 인증키 발급 신청, Credential Manager 등록, `get_real_provider()` 연결 및 실제 최초 1회 호출 검증
- **14-1, 14-3~14-8, 14-19**: 실데이터 시험(백업 실행 확인, 저가상품 안내, 실제 상품 2건 전체과정 검증) — 문서 14번 전체가 요구하는 실행 승인 대상

---

## 7. 외부 답변이 필요한 작업 (BLOCKED_BY_EXTERNAL_RESPONSE)

- **4-2, 4-6**: 해외 매입처의 카드/PayPal 결제 지원 여부(해외 매입처 확정 후)
- **4-12, 4-13**: PG사(결제사업자) 토큰/가상카드 계약 및 공식 문서
- **10-4**(2026-09-16 추가, 판정 자체는 IMPLEMENTED — 비교기·차단 게이트는 실제로 동작하나 온채널 쪽 제조사/원산지/수량/사이즈 실데이터만 막혀 있음): 온채널 `gosi_info`(정보고시)/`GET common/gosi`의 실제 내부 필드 키 확인
- **11-8**: 법적 개인정보 보관기간 기준(개인정보보호법 등) 및 판매채널(쿠팡/네이버)의 공식 보관정책 조사
- **2-1**: 쿠팡 실제 Live 등록 안정화 여부, 네이버 스마트스토어 공식 API 정책 재확인

---

## 8. 권장 구현 순서

1. **즉시(안전 결함 우선)**: 로그인 실패 잠금 미구현(11-18), 세션 3시간/종료시 파기 정책(11-11) 재설계, 배송비 미확인 시 실제 차단(6-3), 마진율 UI 소수/퍼센트 불일치(6-15), 자동결제 한도 이중구조 정리(5-4) — 전부 Critical/High이며 코드만으로 해결 가능.
2. **구조 기반 마련**: 스케줄러 인프라 신설(다수 항목의 공통 선행조건), `AutomationModeState`를 기능별 스코프로 확장(13-10, 14-20~14-25의 선행조건).
3. **핵심 도메인 신설**: 결제(payment) 도메인, 환불(refund) 도메인 — 각각 4/5/9/11/13/14번의 상당 부분을 동시에 해소.
4. ~~**부가 도메인**: 환율(currency/exchange), 가상재고 개념, 매입처 반복실패 자동일시정지(3-11/7-11).~~ **[2026-09-15 Phase 9A~9J 완료]** 매입처 반복실패 자동일시정지(7-11)·호출제한/캐시(7-8)·가격·재고 TTL(8-5/8-6)·가상재고 0 제안(8-19)·상품 속성 비교 차단(10-4)·리콜 차단 메커니즘(10-17/10-18, Provider 미선정)까지 전부 코드·테스트로 구현됨. 남은 것: 7-16(휴면 공급처 독립 게이트 승격), 10-5 브라우저 검증, 10-17 실제 데이터 소스 선정, 10-18 서버 관리자 알림 채널.
5. **AI 학습 파이프라인**(12번 후반 13개 항목) — 실제 완료 주문 데이터가 쌓이기 전에는 코드만 준비하고 실적용은 보류.
6. **실데이터 검증 실행**(14번) — 위 1~4가 정리된 뒤, 사용자 승인 하에 백업 실행 확인 → 실제 상품 2건 전체과정 검증 → 긴급중지 Fake 검증.

---

## 9. 개인 베타 가능 여부

**판정: 조건부 불가 (NOT READY — 문서 14-19가 정의한 베타 시작 조건 미충족)**

- ✅ 전체 회귀: Track A 최종 확인 결과 **3835개 테스트 전부 통과(0 실패)** — 충족.
- ✅ Migration 정합성: Track A에서 순서 역전 결함 확정·수정·재검증 완료 — 충족.
- ❌ 백업·복구 시험: 메커니즘은 있으나 "시험을 실행하고 결과를 기록"한 이력이 이번 감사에서 확인되지 않음 — **미충족**.
- ❌ 실제 상품 2건의 지정된 전체 과정(정상 1건 + 반품 1건) 검증: 전혀 실행되지 않음(관련 테이블 0행으로 뒷받침) — **미충족**.
- ❌ 긴급 중지 검증: Fake 환경 검증이 존재한다고 추정되나 명시적으로 특정하지 못함 — **검증 필요**.

즉 "코드가 도는가"는 이제 확인됐지만(전체 회귀 통과), 문서 자신이 정의한 베타 시작 조건 중 **실행 계열 조건(백업 시험 기록, 실제 상품 2건 검증)이 아직 하나도 채워지지 않았다.** 여기에 더해 로그인 잠금 완전 부재(11-18)와 세션 정책 위반(11-11)은 "안전 결함은 허용하지 않는다"(14-17)는 원칙과 직접 충돌하므로, 최소한 이 두 가지는 실데이터 시험 이전에 코드로 먼저 막아두는 것을 권장한다.

---

## 10. 자동모드 개방 가능 여부

**판정: 현재 구조로는 개방 불가**

- 문서가 요구하는 "조회/분석/상품등록/가격변경/결제/발주를 각각 별도로 자동모드 개방 관리"(14-21)가 **코드 구조상 존재할 수 없다** — `AutomationModeState`가 전역 단일 컬럼이라, 관리자가 자동모드를 한 번 올리면 아직 안전시험을 통과하지 않은 기능까지 전부 함께 열린다.
- **결제(자동결제) 자동모드는 애초에 열 대상 자체가 없다** — `payment` 도메인이 존재하지 않으므로 코드로도 물리적으로 불가능하다(이는 역설적으로 "지금 당장 결제 자동모드가 잘못 열릴 위험은 없다"는 뜻이기도 하다).
- 발주(구매) 쪽 멱등성·중복방지·긴급중지 연결은 이번 감사에서 확인된 항목 중 가장 견고하게 구현돼 있다(Critical/High 결함 목록에 발주 멱등성 관련 항목이 없다는 점 참고) — 다만 이 견고함이 "기능별 개방"이 아니라 "전역 모드 아래에서의 개별 안전장치"라는 한계는 그대로 남는다.
- 권장: 8절의 "구조 기반 마련"(자동모드 기능별 스코프 확장)이 끝나기 전까지는 `LIMITED_AUTOMATION`으로 전환하지 말고 `RECOMMEND_ONLY`/`OPERATOR_APPROVAL` 단계에 머무를 것.

---

## 11. Phase 0~14 연속 구현 완료 후 갱신 (2026-09-10)

이 감사 이후 진행된 Phase 0~14 연속 구현으로 이 문서의 일부 항목이
실제로 바뀌었다(문서 자체는 감사 시점 기록을 보존하기 위해 위
내용을 수정하지 않고, 변경 사항만 아래에 추가한다). 상세 근거는
`docs/HOMEZ_PROJECT_STATE.md`의 Phase 0~14 및 "최종 회귀 검증"
절 참고.

- **9절 "로그인 잠금 완전 부재(11-18)"** — Phase 1에서 해소.
  `migrations/20260909_00_add_login_lockout_columns.sql` +
  `app/domains/user/model.py`(`failed_login_count`/`locked_until`/
  `last_failed_login_at`) + 관련 서비스 로직. 단, 실제 homez.db에는
  아직 미적용(승인 대기, Migration 자체는 임시 DB에서 검증 완료).
- **4절 가격 인상 감지 dead trigger(Critical #21)** — Phase 10에서
  해소. `expected_amount_at_creation` 기준선을 실제로 채워 넣어
  가격 인상 감지가 실제로 동작하게 됨.
- **결제(payment) 도메인 부재** — Phase 7에서 신규 생성(단, 실제
  결제 실행 메서드·실 Provider 연동은 의도적으로 이번 범위 밖 —
  `FakePaymentProvider`만 존재, 절대 실제 네트워크 호출 없음).
- **10절 자동모드 전역 단일 컬럼 한계** — Phase 3의
  `FunctionAutomationState`(기능별 자동화 상태)로 구조적 개선.
  다만 실제 자동모드 개방 여부에 대한 이 문서 10절의 판정 자체를
  뒤집을 근거는 이번 Phase 범위에서 마련되지 않았다(그대로 유지
  권장 — 실행 계열 검증 미충족 상태는 변하지 않음).

**전체 회귀(4070개 테스트) 최종 확인: 전부 통과(0 실패, 0 오류)**
— `docs/HOMEZ_PROJECT_STATE.md`의 "최종 클린 전체 회귀" 절 참고.

이 갱신은 위 9·10절의 "개인 베타 가능 여부"/"자동모드 개방 가능
여부" **최종 판정 자체를 바꾸지 않는다** — 실데이터 실행 계열
조건(백업 시험 기록, 실제 상품 2건 검증, 실제 발주·결제·반품·환불
실행)은 이번 Phase 0~14에서도 전혀 실행되지 않았고(안전 경계상
당연히 승인 없이는 할 수 없음), 그 조건들이 채워지기 전까지는
9·10절의 판정이 유효하다.
