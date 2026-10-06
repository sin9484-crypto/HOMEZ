# V7 출시 후보 준비 — 입력 재사용·업무 DB 적용 실행안·차단 구분·실환경 시험안 (2026-10-05, 65차)

이 문서는 **준비·정리**다. 원본 DB 변경, 실제 판매중지·취소·환불, Migration 적용, 커밋·push는 하지 않았고 이 문서가 그 승인도 아니다.
출처 표기: **직접** = 이번 세션에서 코드·DB(읽기 전용)·공식 문서로 확인 / **기록** = 이전 문서 재사용 / **추정** / **미확인**.

---

## 1. WING 로그인 ID 재사용 (기존 저장값 확인)

| 질문 | 답 | 근거 |
|---|---|---|
| 상품등록 `vendorUserId`와 주문취소 `userId`는 같은 의미인가 | **같은 종류의 값(쿠팡 WING 로그인 ID)** | 직접(공식): 상품 생성 `vendorUserId` = "Actual user ID (Coupang Wing ID)… User ID affiliated to the vendor", 주문 취소 `userId` = "Vendor's ID to log in Coupang Wing"(둘 다 필수 문자열, 예시도 `wing_login…`) |
| 자격증명 저장소에 있나 | **없다 — 설계상 3개 필드만 받는다** | 직접: `CoupangConnectionAdapter.validate_credential_shape`가 `vendor_id/access_key/secret_key`만 요구(`store_connection/adapters/coupang.py:62-71`). 비밀값은 읽지 않았다 |
| HOMEZ 전체에 없나 | **있다 — Wizard#1에 저장돼 있다** | 직접(읽기 전용, 값 미출력): 저장소 DB `listing_wizards#1.channel_selections_json[0].required_fields.vendorUserId` 존재, 6자, 이메일 형태 아님. 설치판 위저드 5·6·7(SUCCEEDED)에도 같은 길이의 값이 있다 |
| 대상 계정이 일치하나 | **일치** | 직접: Wizard#1의 판매계정 #1(`홈즈쿠팡`, 활성) ↔ 판매 연결 #1(`seller_identifier='홈즈쿠팡'`, CONNECTED) |
| 그러면 자동 재사용해도 되나 | **아니오 — 계정 단위 보관 칸이 없는 값을 모든 주문에 전역 적용하지 않는다** | 이 값은 "그 상품등록 입력"에 묶여 있다. 계정 단위 정상 저장 경로(자격증명 필드·판매 연결 컬럼)는 없고 만드는 것은 새 기능이다 |

**결론(쉬운 말):** 같은 값을 쓸 수 있는 *의미*는 확인됐고 값도 이미 있다. 다만 어디에도 "이 판매계정의 WING ID"로 저장돼 있지 않아, 코드가 알아서 가져다 쓰면 "위저드 한 건의 값을 모든 주문에 전역 적용"이 된다. 그래서 **승인된 시험이 그 값을 명시 지정한 1건의 요청에만** 쓰도록 했다(저장·복사 없음). 새로 입력받을 값은 없다.

### 같이 발견·수정한 결함 (문제 기록)
- **충돌:** 64차는 "자격증명 업체코드 == 판매 연결 `seller_identifier`"가 아니면 요청하지 않았다. 그런데 `seller_identifier`는 사용자가 입력한 **이름**이다(저장소 DB `홈즈쿠팡`, 설치판 `에브리홈즈`) — 업체코드(`A…`)가 아니다.
- **영향:** 실제 DB에서는 항상 거부 → 안전하게 막히지만 영원히 쓸 수 없는 상태였다. 테스트 픽스처가 두 값을 같게 만들어 이 문제를 가렸다.
- **수정:** 이름-코드 비교를 제거하고, 승인된 시험이 지정한 `expected_vendor_id`가 있을 때만 비교한다(`channel_action_service.py` `CoupangChannelActions.__init__/_provider`). `wing_user_id`는 승인된 시험의 명시 지정 값 1건만 메모리에서 합친다(자격증명에 값이 있으면 그 값이 우선, 저장 안 함).
- **재발 방지:** 테스트 픽스처를 실제와 같게(이름 ≠ 업체코드) 바꿨고 `test_connection_name_is_not_compared_with_the_vendor_code_but_an_approved_code_is`, `test_explicit_wing_user_id_is_used_for_that_request_only_and_never_stored`, `test_credential_wing_user_id_takes_precedence…` 추가. 옛 방어를 되살리면 11건 중 10건이 실패함을 변조로 확인.
- **실환경 반영:** 없음(코드·격리 검증까지).
- **조회 오류 재발 방지(필드 위치·적용 범위):** 위저드 필수값은 `draft_json`이 아니라 `channel_selections_json[].required_fields`에 있다(63차 브랜드 오판 원인, 메모리 `reference-wizard-required-fields-location`). `vendorUserId`는 위저드(상품등록 입력) 범위이고 판매 연결·자격증명 범위가 아니다.

---

## 2. 64차 완료 주장과 실제 보장 범위

| 주장 | 실제로 보장되는 것 | 보장되지 않는 것 | 근거 |
|---|---|---|---|
| "장부 선점으로 중복 요청 없음" | 같은 (회사, 동작, 대상)에 대한 외부 요청은 **이 시스템에서 최대 1회**. 유일 제약 + 요청 전 commit. 8스레드 동시 실행에서 요청 1건 | 쿠팡 쪽에서 결과가 어땠는지(성공·실패)를 아는 것과는 별개다 | 테스트 `test_concurrent_executions_send_exactly_one_request` |
| "외부 성공 후 내부 저장 실패에도 중복 없음" | 장부 확정(`finish`)이 실패해도 `REQUESTING`이 남아 재실행은 `UNKNOWN`으로 읽고 **다시 요청하지 않는다**. 5분 지난 `REQUESTING`/`UNKNOWN`은 읽기 조회(`inventories`/`ordersheets`)로 대조해 적용이면 성공 확정, 미적용이면 한도 안에서 재요청 가능 | **결과를 "내 요청이 성공시켰다"고 확정하는 것은 아니다** — 대조는 "원하는 상태(판매중지됨/취소됨)가 지금 그렇다"만 확인한다(다른 사유로 이미 그 상태일 수 있음). 방금 난 불명은 같은 처리 안에서 판정하지 않는다 | `test_external_success_followed_by_internal_save_failure_is_not_resent`, `test_fresh_requesting_row_is_not_reconciled…`, `test_unknown_stop_is_not_resent_and_is_confirmed_by_reading_the_sale_status` — **기존 테스트로 충분, 새로 만들지 않음** |
| "429 재시도" | Retry-After와 기본 간격(15분→60분) 중 큰 값으로 `next_retry_at` 저장, 시도 횟수 `attempt_count` 저장, 최대 3회 후 `ACTION_REQUIRED(RETRY_EXHAUSTED)` | **자동 반복은 보장되지 않는다(의도적).** 재평가는 처리 서비스가 다시 호출될 때만 일어난다. 65차 시점의 호출처는 `submit_order` 하나뿐이었고 **66차에 운영자 재처리 API 2개를 추가했다**(§9-3). 스케줄러는 만들지 않았다 | 직접: 호출처 grep(65차) → 66차 §9-3 |
| "사람이 원인 해결 후 재실행" | 서비스 인자 `retry_action_required=True`로 `ACTION_REQUIRED` 요청을 다시 보낼 수 있음 | 65차 시점에는 호출할 화면·API가 없었다 → 66차에 `POST /purchase-tasks/supplier-stop-sale/re-execute`로 해소(관리자+최근 인증) | 직접 |
| "환불은 숨기지 않는다" | 알림 문구가 "쿠팡 주문 취소 반영됨"과 같은 문장에 "환불은 요청하지도 확인하지도 않았습니다"를 남김. 이번에 수정(전에는 건수·요약 코드로만 묻혀 있었다) | 쿠팡 환불 처리 자체는 확인하지 않는다 — 공식 문서에 취소와 환불 관계가 없다 | `build_notification_message`, `test_notification_message_never_hides_a_missing_refund…` |

**65차에서 입증된 공백(66차에 최소 수정으로 해소 — §9-3):** 재시도·결과불명 대조·`retry_action_required`를 사용자가 직접 돌릴 진입점이 없었다. 새 주문이 들어와 같은 상품의 발주를 다시 시도하는 경우에만 재평가되는 구조를 정상 운영 방식으로 두지 않기로 했고, 기존 장부 서비스를 호출하는 운영자 API 2개(조회·대조 / 재실행)를 추가했다.

---

## 3. 업무 DB 선택·적용 실행안 (한 장)

**사전 정리 — 이미 있는 결정(재질문하지 않는다).** 2026-09-21(6차 §7)에 "이번 V7 Migration 적용 작업의 업무 DB = 저장소 루트 `homez.db`"가 명시 확정됐고 09-23(7·9차)에 재확인됐다(`HOMEZ_V7_DB_DECISION_REVIEW_20260923.md` §2). 설치판 DB는 "별도 대상·별도 승인"이다. 권고안 B(저장소 DB를 업무 DB로 + 설치판 이력은 보존·참조)는 이 결정과 같은 방향이다. 따라서 이번에 사용자에게 필요한 것은 "DB 선택"이 아니라 **"B로 진행하고 이번 Migration 1건을 적용해도 되는가"** 두 가지다(아래 승인 ①·②).

### 3-1. 현재 지문 (읽기 전용, 두 DB 모두 실행 중인 프로세스 없음·`-wal/-shm/-journal` 없음)

| 항목 | 저장소 DB (업무 DB 후보) | 설치판 DB |
|---|---|---|
| 경로 | `C:\Users\Daum pc\Homez-OS\homez.db` | `%LOCALAPPDATA%\HOMEZ\data\homez.db` |
| 수정 시각 / 크기 | 2026-10-04 21:35 / 3,899,392 B | 2026-08-29 21:02 / 2,953,216 B |
| SHA-256(이 문서 작성 시점) | `34c5002a…5913d` (전체 `34c5002a054537cb45f6ac82bb5a21a37a1f7fd15321b6b1b2364219e8b5913d`) | `c28eaf57…22913` (전체 `c28eaf57616876fa034bf1bffb4fa87ac2dd630bf4057686b8dcfcd09d322913`) |
| Migration | 적용 76 / **대기 1**(`20261005_00`) | 적용 41 / 대기 36 |
| 업무 데이터 | 위저드 2(#1 NEEDS_CORRECTION, 레이펄스 KR-120395), 판매계정 1·판매연결 1, 매입처 연결 2, 옵션 연결 0, 속성 비교·판매신청 기록 1(NEEDS_REVIEW), 후보 3, 미디어 2, 정책 설정 1(18%), 제출 이력 **0** | 위저드 7(전부 SUCCEEDED 이력), 판매계정 2·판매연결 2, 제출 10(FAILED 8·PENDING 1·**UNKNOWN 1**), 후보 1, 정책 설정 0, 매입처 연결·옵션 연결·판매신청 테이블 **없음** |

적용 직전에 다시 계산해 위 값과 **정확히 일치**해야 한다(하나라도 다르면 이 실행안은 무효, 처음부터 다시 조사).

### 3-2. B를 적용할 때 보존·구분할 것
- **설치판 이력(보존, 수정 금지):** 설치판 DB는 그대로 둔다. 거기 있는 중복등록 방지 근거(UNKNOWN 제출 #10 — 상품명·판매가·카테고리·감사 `COUPANG_PRODUCT_SUBMISSION_UNKNOWN`, `auto_retry_allowed=false`)는 **문서 기록으로 참조**한다(기록: 56·57차).
- **저장소 업무 상태(보존):** Wizard#1(브랜드 `OFFICIAL_BRAND/KR-120395` 포함), 매입처 연결, 판매신청 NEEDS_REVIEW 증빙, 속성 비교 run, 정책 18%.
- **계정·자격증명 참조:** 판매 연결 `HOMEZ:store_connection:<uuid>`(충돌 없음), 매입처 연결 `homez_channel_connection_{id}`(숫자 id라 DB 간 충돌 가능 — **DB 병합·id 재사용 금지**). 두 DB의 참조 문자열 겹침 0. Windows 자격증명 항목은 이번에도 읽지 않는다.
- **중복등록 방지의 한계(사실):** 방지는 DB 하나 안의 `(company_id, listing_id)`와 부분 유니크 인덱스 기준이다. 저장소 DB에는 제출 이력이 0건이라 **설치판의 UNKNOWN 제출(부직포 행주)** 을 알지 못한다 — B에서는 그 상품을 저장소 DB에서 다시 등록하지 않는다는 운영 규칙으로 막고(코드 보호 없음), 필요하면 별도 승인으로 해당 이력만 정식 경로로 옮기는 것을 결정한다(이관 도구는 만들지 않는다).
- **설치판 앱이 어느 DB를 여는가(별도 결정):** 경로 계약상 패키징 실행은 `%LOCALAPPDATA%` DB, 개발 실행은 저장소 DB를 연다(`config.py:34-70`). 출시 기준 "최신 설치판 + 업무 DB 연결·업그레이드·재실행"을 B로 충족하려면 (a) 설치판 검증을 **업무 DB의 복원 사본**으로 수행하거나 (b) 설치판이 업무 DB를 열도록 경로를 바꾸는 별도 결정이 필요하다. 이번 Migration 적용 승인과 섞지 않는다.

### 3-3. 이번에 적용할 Migration (1건)
| 항목 | 값 |
|---|---|
| 파일 | `migrations/20261005_00_create_channel_action_request_schema.sql` |
| SHA-256 | `18ae61f41df72af90d20d2e95c4110b8baf0e3aefd6ca67b689e76f87b1b8b55` (1,904 B, LF) |
| 예상 변경 | 신규 테이블 `channel_action_requests` 1개 + 인덱스 4개. 기존 테이블·행 변경 0(추가형). 모델과 동일 DDL임을 `test_channel_action_migration`으로 검증 |
| 되돌리기 | 파일 하단 주석의 `DROP TABLE IF EXISTS channel_action_requests;`(자동 실행 안 됨) |
| 구버전 호환 | 신규 테이블을 참조하지 않는 코드는 영향 없음. 이 테이블이 없으면 새 코드는 외부 요청을 보내지 않는다(`ACTION_REQUIRED`) |
| 설치판 DB | 이번 대상 아님(대기 36개 단위로 별도 승인) |

### 3-4. 승인을 두 개로 나눈다
- **승인 ① DB 선택:** "업무 DB = 저장소 DB(B)로 진행." 이것만으로는 복사·병합·이관·Migration을 하지 않는다.
- **승인 ② Migration 적용:** 위 3-3의 파일 1건을 3-1 지문과 일치하는 저장소 DB에 적용.

### 3-5. 선택 후 진행 순서 (기존 절차 재사용 — `HOMEZ_V7_DB_UPGRADE_PREP_20260923.md` §4·5·8, `HOMEZ_V7_DB_DECISION_REVIEW_20260923.md` §6)
1. 서버·스케줄러 완전 정지 확인(`tasklist`에 python/HOMEZ 0개, 포트 LISTENING 없음). 서버를 띄운 채 적용하지 않는다.
2. `sqlite3.Connection.backup()`으로 `C:\Users\Daum pc\Homez-Backups\homez_root_pre_channelaction_<타임스탬프>\`에 백업 → 원본·사본 SHA-256 일치 + 사본 `integrity_check=ok`.
3. **사본 리허설**(백업 사본이 아닌 별도 복사본): 적용 → `integrity_check=ok`·FK 위반 0·기존 테이블 행 수 불변 → 재적용 자동 차단 → 수동 DROP+`schema_migrations` 행 삭제로 원상 복구.
4. 원본 지문 재확인 → 순수 `MigrationRunner` 호출로 적용(서버 없이).
5. 적용 후: `integrity_check`, 적용 목록이 계획과 일치, 신규 테이블 존재, 서버 재기동 후 로그인·목록·옵션 연결 패널 1회 확인.
6. 실패 시: 서버 재기동 금지 → 백업으로 원자적 교체(임시 파일명→검증→rename) → 원인 규명 전 재시도 금지.

---

## 4. 상품등록의 현재 차단 — 단계별 구분

**주의:** 저장된 `validation_result_json`은 **과거(2026-10-04) 사전검사 결과**이고, 등록 직전 점검(`listing_wizard_live_service.preflight`)은 더 많은 조건을 본다. 아래 "현재"는 **최신 코드의 읽기 조건을 저장소 DB 원본에 읽기 전용 연결로 적용**해 계산했다(쓰기 시도는 오류가 나게 연결, 원본 변경 없음). 최종 payload 조립(`_build`)은 외부 이미지 처리 가능성이 있어 실행하지 않았다(미계산).

| 항목 | 막는 단계 | 현재 Wizard#1 상태 | 종류 | 근거 |
|---|---|---|---|---|
| 경제성(포장비·세금 기준율) | 등록 사전검사 + 등록 직전 | **차단 중**(`MARGIN_PROVISIONAL`, 최소마진 18%) | **내부 경제성 정책** — 외부 등록 의무가 아니다. 미확인 포장비를 0으로 바꾸지 않음 | 직접(읽기 전용 계산), `margin_gate.py` |
| 속성 비교(공급처 식별) | 등록 직전(`PRODUCT_ATTRIBUTE_MISMATCH_BLOCKED`) | **차단 중**(`ONCHANNEL:CH1147184` → `BLOCKED`, 미해소 run) | 내부 안전 게이트(6개 필수 항목 중 하나라도 미확인) | 직접 계산. 과거 사전검사에는 이 항목이 없어 "경제성뿐"으로 보였다 |
| 속성 비교의 구성수량(QUANTITY) | 위 속성 비교에 포함 — 등록 직전 | 위와 같음 | 구성수량이 모호하면 해소가 안 된다 | 기록(59차) |
| 등록 내용과 해소의 결합 | 등록 직전(`ATTRIBUTE_RESOLUTION_NOT_BOUND_TO_REGISTRATION`) | 해소가 선행돼야 평가됨 → 현재는 위 차단 뒤 | 내부 | `listing_wizard_live_service.py:340-353` |
| 승인(8단계)·운영 모드 | 등록 직전(`VALID_APPROVAL_REQUIRED`, `OPERATOR_APPROVAL_MODE_REQUIRED`) | **차단 중**(승인 없음, 모드 `RECOMMEND_ONLY`) | 사용자 결정·함수별 모드 개방 | 직접 |
| 판매 연결 해석 | 등록 직전(`SELLER_CONNECTION_*`) | **통과**(연결 #1 CONNECTED, 계정 정확히 1개) | 내부 | 직접 계산 |
| 리콜·판매중지 차단 | 등록 직전 | 통과 | 내부 | 직접 계산 |
| 브랜드 | 최종 payload 점검 | **통과**(OFFICIAL_BRAND/KR-120395 보존) | 쿠팡 필수 필드 | 직접 |
| **옵션 매핑 / 구성수량 확인(옵션 연결 `units_per_sale`)** | **등록이 아니라 등록 후·발주 단계** — 등록 직전 점검에는 없다. 연결 저장은 외부 확인 조회를 동반하고, 발주 검토 때 연결이 `ACTIVE`여야 자동 경로로 진행 | 연결 0건 | 발주 단계 조건 | 직접: 등록 점검 코드에 `units_per_sale` 참조 없음 |
| **판매신청 NEEDS_REVIEW** | **발주 단계**(`submit_order`의 판매신청 게이트, `SUBMITTED`만 통과) — 상품 **등록**을 막지 않는다 | 기록 1건(2026-09-14 apply HTTP 200 정황, 내부 추적상 NEEDS_REVIEW) | 거부가 아님. 일반 판매신청 정상 절차의 내부 추적 상태. **해소·재신청은 하지 않았다** | 직접 |
| 포장비·세금 | 위 "경제성" | — | **내부 경제성 정책 결정**이며 외부 등록 의무와 구분 | — |
| 공급처 판매 허락 메일 | **요구하지 않는다** | — | 온채널형 공급처는 공식 정책·상품 정보·정상 판매신청 절차를 따른다(2026-10-05 사용자 확정) | `HOMEZ_USER_OPERATION_SETTINGS.md` |

**구성수량의 두 얼굴 — 65차 표의 모순 정정(66차).** 65차는 구성수량을 한 곳에서는 "등록 직전 속성 비교를 막음", 다른 곳에서는 "발주 단계 조건"이라고 적어 서로 모순돼 보였다. 코드로 확인한 사실은 이렇다.

| | 등록 내용의 수량(`QUANTITY`) | 옵션 연결의 `units_per_sale` |
|---|---|---|
| 무엇인가 | 최종 payload의 구매옵션 "수량" 속성(`extract_registration_attribute_values`, 주문 수량·`unitCount`와 별개)을 공급처 포장수량(`package_quantity`)과 비교 | 판매 1개가 공급 옵션 몇 개인지(발주 수량 계산에 곱함) |
| 막는 단계 | **등록 직전**: 공급처 API가 포장수량을 주지 않아 미확인 → 속성 비교 BLOCKED → `PRODUCT_ATTRIBUTE_MISMATCH_BLOCKED` → 사람이 해소해야 통과, 해소 뒤에도 등록 내용과 결합돼야 함(`ATTRIBUTE_RESOLUTION_NOT_BOUND_TO_REGISTRATION`) | **저장 자체**는 위저드 옵션 SKU와 판매 연결만 요구(등록 성공 여부와 무관, 외부 확인 조회 동반). 쓰임새는 **발주 검토** — 연결이 ACTIVE여야 자동 경로 |
| 서로 대조하나 | **아니오.** 두 값은 코드에서 서로 검증하지 않는다(`save_link`는 위저드 수량을 보지 않고, 속성 비교는 `units_per_sale`을 보지 않는다) | |

따라서 "구성수량은 발주 단계 조건"이라는 말은 **`units_per_sale`에 한해서만 맞고**, 구성수량이라는 *사실*은 등록 단계(속성 비교)도 **간접적으로 막는다.** 둘이 어긋나게 입력될 수 있다는 점(등록 수량 1, `units_per_sale` 2 등)이 입증된 공백이며, 같은 근거(상품 자료·공급처 확인)로 두 곳을 채우는 것이 현재의 유일한 보호다. 새 검증은 이번 범위 밖이라 추가하지 않았다. 문제 기록: 원인=한 줄 표에 두 개념을 합쳐 씀 / 영향=사용자가 "구성수량은 등록과 무관"으로 오해할 수 있음 / 수정=이 표와 상태 문서 정정 / 재발 방지=두 개념을 용어로 분리(`QUANTITY` vs `units_per_sale`) / 실환경 반영=없음.

기존 스레드 읽기는 별도 승인 범위에서만 하며 이번에 읽지 않았다(재문의 없음).
"시험 주문 문의"는 코드 게이트가 아니지만 쿠팡이 판매자 본인 시험 주문·취소를 허용하는지는 **확인된 바 없어** 불필요하다고 단정하지 않는다(결정 사항으로 남긴다).

---

## 5. 출시 후보 보관과 검증 준비

### 5-1. 변경 대조 (git, HEAD `36f970f` = `origin/main`)
- 수정 40개(앱 25·프런트 3·문서 2·테스트 10) + 신규 21개 항목(신규 파일 20 + 스킬 디렉터리 1; 이 중 1개는 제외 대상인 타 작업물). 신규에는 이번 64·65차의 Provider·장부·서비스·Migration과 그 테스트, 51~63차의 신규 모듈이 포함된다.
- **기준선 테스트 시작(16:44) 이후 바뀐 파일은 5개뿐**: `app/domains/purchase_task/channel_action_service.py`, `app/domains/purchase_task/supplier_stop_sale_service.py`, `tests/test_channel_action_service.py`, `tests/test_supplier_stop_sale.py`, `docs/HOMEZ_PROJECT_STATE.md`. 이 변경분이 닿는 모듈은 다시 실행했다(§6 표). 나머지 파일의 기준선(통합 121·발주 86·알림 57)은 그대로 유효하다.
- **기존 테스트 하나는 이번 작업 이전부터 실패 상태였다**: Migration 파일 목록(`test_permissions_timestamps_migration`)이 13차 `20260924_00` 파일을 몰랐다 — 같은 갱신 규칙으로 두 파일을 반영.

### 5-2. 커밋 묶음 제안 (승인 요청용 — 한 번에 정확히)
**포함:** 위 수정 40개 + 신규 파일 전부 중 아래 제외 항목을 뺀 것.
**제외(커밋하지 않음):** `docs/research/20261005_BIGBUY_API_FEASIBILITY.md`(다른 정기 조사 작업물, 이 작업 소유 아님).
**선택(별도 묶음 권장):** `.claude/skills/homez-action-briefing/`(사용자 요청으로 만든 스킬 — 코드와 분리해 의미가 섞이지 않게).
권장 분할: ① 51~63차 누적(마진 18%·속성 비교·판매 연결·정책 문서) ② 64~65차(판매중단 Provider·작업 장부·Migration 파일·테스트). 두 묶음은 같은 파일(`purchase_task/model.py`, `constants.py`, `order_submission_service.py`, 상태 문서)을 공유해 파일 단위로 깔끔히 나누기 어렵다 — 하나로 묶는 것이 안전하다. 사용자가 결정. **push는 별도 승인.**

### 5-3. 전체 회귀 1회 계획 (출시 후보 고정 뒤)
- 실행 전 확인: 커밋(또는 동결) 후 변경 없음, 두 DB 지문이 §3-1과 같음.
- **실제 DB·자격증명·네트워크 가드(기존):** `tests/support/regression_guard/sitecustomize.py` — `PYTHONPATH=<저장소>\tests\support\regression_guard;<저장소>`, `HOMEZ_GUARD_LOG=<저장소 밖 로그>`. 저장소 DB·설치판 `%LOCALAPPDATA%\HOMEZ`·`Homez-Backups`·Windows Credential Manager·비루프백 네트워크·가드를 잃는 자식 프로세스를 차단·로그. 한계(문서화됨): ctypes 직접 Win32 호출, 비파이썬 자식 내부, SQL `ATTACH`.
- 새 코드의 안전성: 새 테스트는 `InMemoryCredentialStore`와 Fake HTTP만 쓰고, `CoupangChannelActions` 기본값(Windows 저장소)은 장부 테이블이 없는 격리 DB에서는 자격증명을 읽기 **전에** 거부한다.
- 가드가 막은 "실제 DB 필요 테스트"는 통과로 세지 않고 이름 분류해 따로 보고한다(메모리 `project-regression-tests-read-real-db`).
- 직전 전체 회귀 기록: 4,411건·6,479초(`HEAD 2e422b0`, 2026-09-16) — 이후 코드가 크게 바뀌었으므로 새 기준선이 필요하다.
- **서버 재시작:** 새 기능의 실제 사용 시점에만, 미저장 입력 보존을 확인한 뒤.

---

## 6. 이번 라운드의 검증 (변경분 한정)
기준선(64차 종료 시점, 변경 없는 범위에 재사용): 통합·장부·Provider·Migration·옵션 연결·Migration 목록 **121건**, 발주 서비스 모듈 **86건**, 알림 합본 **57건** 모두 통과.

변경분 재실행(이번 라운드 수정 파일이 닿는 범위): `test_channel_action_service`(21) + `test_supplier_stop_sale`(31) + `test_coupang_channel_control_provider`(15) + `test_channel_action_migration`(5) + `PointBalanceGateTestCase`(14) = **86건 통과**(160초, 실패·오류 0).

변조 확인: 이름=업체코드 옛 방어를 되살리면 11건 중 10건 실패(원본 복구 확인).
미실행: 전체 회귀(출시 후보 고정 전), 실제 쿠팡·온채널 호출(0회), 원본 DB 변경(0회).

---

## 7. 다음 실환경 시험안 — 판매중지와 주문취소를 **묶지 않는다**

두 시험은 서로 독립이며 각각 별도 승인 없이는 실행하지 않는다. 판매자 본인 시험 주문이 허용된다고 가정하지 않는다(쿠팡 공식 허용 조건 미확인).

### 시험 S — 옵션 판매중지 1건
| 항목 | 값 |
|---|---|
| 지정 계정 | 판매계정 #1 `홈즈쿠팡` / 판매 연결 #1. 업체코드는 승인 시 `expected_vendor_id`로 지정(WING 화면의 `A01767141`은 **기록상 값** — 자격증명의 실제 업체코드는 읽지 않았다) |
| 지정 vendorItemId | **미확인.** HOMEZ에 저장된 곳이 없다(옵션 연결 0건·저장소 제출 이력 0건). 상품 `16364645696`(WING 판매중, 에브리홈즈 `A01767141`)의 옵션번호는 WING 화면 또는 읽기 조회로 먼저 확인해야 한다 — **별도 읽기 승인** |
| 현재 상태 | 시험 전 `GET …/vendor-items/{id}/inventories`로 `onSale` 1회 확인(읽기) |
| 영향 | **실제 판매 중인 상품이면 실제 판매가 멈춘다.** 이 계정에서 확인된 판매 상품은 `16364645696` 하나(WING 표시상 최근 30일 판매 2개, 기록) |
| 호출 상한 | 쓰기 `PUT …/sales/stop` **1회**(재시도 없음), 읽기 최대 2회(전·후) |
| 실행 경로 | `CoupangChannelActions.stop_sale` 직접 호출(주문 취소·공급처 판정과 분리). 함수별 모드를 우회하지 않으려면 `INVENTORY_RESPONSE`를 `SEMI_AUTOMATIC`으로 두고 승인자 명시 — **모드 전환은 별도 승인** |
| 복원 조건 | `PUT …/sales/resume`(본문 없음, 성공 `판매가 재개되었습니다.`) 또는 WING 화면. **불가:** 옵션이 삭제된 경우, 쿠팡이 모니터링해 정지한 경우. 이 저장소에는 재개 호출 코드가 없다(구현하지 않음 — 복원은 WING 화면 또는 승인된 1회 호출) |
| 선행 | 장부 Migration 적용(승인 ②). 없으면 코드가 요청을 보내지 않는다 |
| 성공 기준 | 응답 `code=SUCCESS` + 읽기 조회 `onSale=false`. 불일치·읽기 실패는 `UNKNOWN`으로 보고(성공으로 쓰지 않음) |

### 시험 C — 고객 주문 취소 1건
| 항목 | 값 |
|---|---|
| 지정 주문·박스·옵션·수량 | **현재 지정할 수 있는 대상이 없다.** HOMEZ 주문 0건(두 DB 모두), 쿠팡 쪽 시험 대상 없음. 시험하려면 실제 주문(또는 허용 확인된 시험 주문)이 먼저 있어야 한다 |
| 주문 상태 조건 | 박스 1개, 쿠팡 상태 `ACCEPT`(결제완료)만 자동 대상. `INSTRUCT` 이후는 출고 중지라 사람이 확인 |
| 공급처 발주 여부 | **발주·매입 기록이 없는 주문이어야 한다**(있으면 취소하지 않고 상태 확인 대상) |
| `userId` | 승인된 시험이 지정: **Wizard#1에 저장된 vendorUserId(계정 #1 일치 확인됨)** — 그 요청에만 사용, 저장·복사 없음 |
| 환불 영향 | **공식 문서에 취소와 환불의 관계가 없다.** 취소 후 쿠팡 화면에서 환불·취소 상태를 직접 확인해야 하며, HOMEZ는 환불을 요청하지도 완료로 표시하지도 않는다 |
| 호출 상한 | 쓰기 `POST …/cancel` **1회**(재시도 없음), 읽기 `GET …/ordersheets/{박스}` 최대 2회 |
| 복원 조건 | **복원 불가**(주문 취소는 되돌릴 수 없다) |
| 성공 기준 | 응답 `code="200"`·`failedItemIds` 비어 있음 + 발주서 조회에서 요청 품목 `cancelCount`/`canceled` 확인 → 그 뒤에만 내부 주문 취소 |

---

## 8. 문제 기록 연결표
| 문제 | 원인 | 수정 | 재발 방지 | 실환경 반영 |
|---|---|---|---|---|
| 63차 브랜드 "비어 있음" 보고 | 잘못된 JSON 컬럼(`draft_json`) 조회 | 보고 정정(코드·데이터 변경 없음) | 메모리 `reference-wizard-required-fields-location`, 이 문서 §1 | 해당 없음 |
| 판매중단 감지가 앞선 게이트에 막힘 | 감지가 포인트 점검 안쪽에 위치 | `detect_and_handle`을 어댑터 생성 직후로 이동 | `test_stop_sale_is_detected_even_when_other_order_gates_would_block_first` + 변조 확인 | 없음(격리) |
| 같은 SKU 다른 계정 링크 혼동 | 링크를 SKU만으로 키 | 키를 (판매 연결, SKU)로 | `ScopeIsolationTestCase` 단언 강화 + 변조 확인 | 없음 |
| 이름=업체코드 가정으로 영구 거부 | 픽스처가 값을 같게 만들어 가려짐 | 선택적 `expected_vendor_id`로 교체 | 실제와 같은 픽스처 + 3개 테스트 + 변조 확인 | 없음 |
| 환불 미확인이 취소 완료에 묻힘 | 알림 문구가 코드 요약에만 의존 | `build_notification_message` | 문구 테스트 | 없음 |
| 재시도·재처리 진입점 없음 | 호출처가 `submit_order` 하나 | 66차: 운영자 API 2개(`reconcile`·`re-execute`) | `tests/test_supplier_stop_sale_operator.py` 18건 + 변조 3건 확인 | 없음(격리) |

---

## 9. 66차 — 모순 정리와 출시 후보 고정 (2026-10-05)

### 9-1. 구성수량 모순 → §4 "구성수량의 두 얼굴" 참조.

### 9-2. 판매계정 검증이 약해졌는가 — 점검 결과
- **유지:** 이름 = 업체코드 비교 제거(64차 결함 수정)는 그대로다.
- **`expected_vendor_id`가 선택값이 된 뒤 계정 검증이 생략되는 경로:**

| 경로 | 업체코드 검증 | 나머지 보장 | 평가 |
|---|---|---|---|
| 운영자 재실행 API(66차 신설) | **필수**(없으면 400, 코드로 거부 — `test_requires_an_approved_vendor_code…`, 변조로 확인) | 관리자+최근 인증, 현재 공급처 상태 재확인, 모드·재시도 한도 | 닫혔다 |
| 운영자 조회·대조 API | 불필요(읽기 전용, 외부 변경 요청을 구조적으로 보내지 않음) | 비상정지 중에도 읽기·진단 유지(확정 정책), 변경(외부 요청·내부 주문 취소)은 차단 — 67차 정정, 66차는 읽기까지 차단해 정책과 충돌했다 | 해당 없음 |
| 공급처 이벤트가 자동으로 부르는 처리(`submit_order` 경유) | **없음**(자동 경로에는 승인된 업체코드를 줄 사람이 없다) | 회사 일치, 옵션 연결이 명시한 판매 연결(`store_connection_id`는 사용자가 확인해 저장한 연결), 연결 CONNECTED·자격증명 참조 존재, 기능 모드(수동/반자동/자동) | **열려 있음 — 자동 모드 개방 전 확인 항목.** 저장소 DB에 기능 모드 행이 없어(직접: `function_automation_states` 0행) `INVENTORY_RESPONSE`·`CANCELLATION`은 코드 기본값 `MANUAL`이라 외부 변경을 보내지 않는다(준비만) |

- **자격증명·계정 관계의 기존 계약(재사용, 재실행 안 함):** 승인한 판매계정 → 명시적 판매 연결(`resolve_seller_connection`: 같은 회사·`account_code == seller_identifier`·정확히 1개·CONNECTED·자격증명 참조) → 그 연결의 자격증명. 승인 후 연결이나 자격증명 교체는 등록 직전 점검이 `SELLER_CONNECTION_CHANGED_AFTER_APPROVAL`로 막는다 — 근거 테스트 `tests/test_coupang_live_submission.py:1736, 1750`, `tests/test_coupang_seller_connection_routing.py::test_descriptor_changes_when_the_connection_or_credential_version_changes`(변경 파일 아님, 기준선 그대로 유효).
- **한계(사실):** 자격증명의 **실제 업체코드**와 판매 연결 행을 서로 대조할 DB 근거는 없다(`seller_identifier`는 이름). 공식 문서상 주문 취소는 "Product is not the requesting seller's" 오류로 타 판매자 상품을 거부하지만, 판매중지 API의 같은 보호는 문서에 명시돼 있지 않다(추정). 그래서 "계정 동일성 검증 완료"라고 확대하지 않는다 — 자동 경로는 위 표대로 업체코드 검증이 없고, 운영자 경로는 승인된 업체코드가 필수다.
- **WING ID:** 운영자 API는 값을 직접 받지 않고 `source_wizard_id`로 위저드를 지정하며, 그 위저드가 선택한 판매계정이 `resolve_seller_connection`으로 **요청 대상 판매 연결과 같은 연결로 해석될 때만** 그 요청에 쓴다(`wing_user_ids_by_store`; 해석 실패·값 없음·다른 연결은 쓰지 않음, 저장 없음, 자격증명 값이 있으면 그 값 우선). Wizard#1 값을 모든 주문에 적용하지 않는다.

### 9-3. 운영자 재처리 경로 (최소 — 새 스케줄러·범용 재시도 플랫폼 아님)
기존 관리자 화면·명령·API 중 판매중단 처리를 부를 수 있는 것은 없었다(`submit_order` 하나). 기존 `AdminGuard`+최근 인증(`X-Recent-Auth-Token`) 패턴 그대로 API 2개를 `purchase_task/router.py`에 추가했다(동적 `{task_id}` 경로보다 앞에 등록).

| 명령 | `POST /purchase-tasks/supplier-stop-sale/…` | 하는 일 | 하지 않는 일 |
|---|---|---|---|
| **조회·대조** | `reconcile` (관리자) | 이전 판매중지·고객 주문 취소 요청의 결과를 **읽기 조회(`inventories`·`ordersheets`)로만** 대조해 확인된 사실을 장부·연결·(취소 기능 모드·승인 범위가 허용할 때) 내부 주문에 반영 | **외부 변경 요청 전송**(코드 경로상 쓰기 메서드를 부르지 않음 — 미적용이 확인돼도 재시도 가능으로만 되돌림), 비상정지 중 내부 주문 취소 반영(쿠팡 취소가 확인돼도 보류). 읽기 조회는 비상정지 중에도 한다 — 확정 정책 "긴급 중지 시 조회·백업·진단은 유지"(OPERATION_SETTINGS 232·296행) |
| **재실행** | `re-execute` (관리자 + 최근 인증) | 다음을 **다시** 검사한 뒤 기존 처리에 맡김: 승인된 업체코드 필수, **현재 공급처 상태가 여전히 판매중단(3)** (조회 실패·그 외 상태는 거부), 기능 모드·비상정지, 장부의 재시도 시각·횟수, 조치 필요는 `retry_action_required=true`일 때만 | UNKNOWN을 곧바로 재요청(조회로 미적용이 확인된 뒤에만, 한도 안에서) |

회사 격리는 로그인 사용자의 회사만 쓰고(요청 본문의 회사 무시), 두 명령 모두 감사 로그(`SUPPLIER_STOP_SALE_OPERATOR_RECONCILE/REEXECUTE`)를 남기며, 중복 방지는 기존 작업 장부가 맡는다. **격리 검증만**(Fake HTTP) — 실제 요청은 없다.

### 9-4. 설치판 검증은 사본으로 (준비만 — 실행·복사 안 함)
**기존 지원 기능(확인):** `HOMEZ_DATA_ROOT`(패키징 데이터 루트 격리 — data/logs/backups/config/media 전부, `app/desktop/paths.py`), 이 값이 있으면 자격증명 이름공간이 `HOMEZ_TEST`로 바뀜(`desktop_console_session_store.py`, 판매 연결 서비스도 같은 원칙) → 운영 자격증명을 읽지도 복제하지도 않는다. 빌드 절차: `docs/HOMEZ_INSTALLER_OPERATIONS.md`(`pyinstaller homez.spec` → Inno Setup). 마지막 설치 파일은 2.1.4(2026-09-02)라 현재 코드를 담지 않는다. PyInstaller 6.16.0은 개발 환경에 설치돼 있다.

**준비된 순서(각 단계가 별도 승인 대상임을 표시):**
1. 새 빌드 생성(`pyinstaller homez.spec --noconfirm`, 필요 시 Inno Setup) — 저장소 변경 없음, 산출물만 생성. *(빌드 승인)*
2. **원본 DB 사본 생성** — `sqlite3.Connection.backup()`으로 업무 DB(저장소 루트)를 스크래치 경로로 복원 사본 생성, 원본·사본 SHA-256 일치와 `integrity_check=ok`. *(원본 복사 승인 — 이번에는 하지 않았다)*
3. 사본을 `<검증 루트>\data\homez.db`에 두고 **`HOMEZ_DATA_ROOT=<검증 루트>`** 로 새 빌드를 실행 → 데이터 경로·자격증명 이름공간이 모두 격리됨. 사본에 대기 Migration 1건(`20261005_00`)이 자동 적용되는지, 로그인·목록·옵션 연결 패널·재기동 후 재실행(멱등)을 확인.
4. 실제 쿠팡·온채널 호출은 자격증명이 없으므로 불가능한 상태에서 수행(Fake 격리). 가드 환경(`tests/support/regression_guard`)을 건 채 실행하면 실제 DB·자격증명·네트워크 접근이 차단·기록된다.
5. **설치판의 실제 데이터 경로 전환·과거 이력 이관은 사본 검증 이후의 별도 승인 대상**이며 미승인이다.

**명시(임시 제한 ≠ 영구 구현):** 설치판의 UNKNOWN 제출(부직포 행주)을 저장소 DB에서 재등록하지 않는 규칙은 **임시 운영 제한**이다. 저장소 DB에는 제출 이력이 0건이라 코드가 그 사건을 모르며, 영구적인 중복 방지가 구현된 것이 아니다. 과거 이력은 설치판 DB에 그대로 보존하고 덮어쓰지 않는다. 보존·대조(정식 이관 여부)는 별도 결정이다.

### 9-5. 포장비·세금 입력 — 66차 정정(철회 포함, 67차)
**철회:** 66차 최종 보고와 이 절의 이전 판은 `세금 2.5% = 공제 최대`, `5% = 공제 일부`, `10% = 공제 없음`이라는 라벨과 부가세 순부담 계산(매출세액·매입세액 공제)을 제시했다. 이 해석은 **뒷받침할 근거가 없다** — 사업자 과세유형, 세금계산서 수취·공제 가능 여부는 HOMEZ에 기록이 없고, 코드의 `tax_basis_rate`는 그런 의미를 갖지 않는다. 그 표와 라벨, 그리고 "세금 기준율 2.5/5/10% 중 선택"을 사용자 결정으로 요구한 것을 철회한다. 사용자에게 세금 선택지를 다시 요구하지 않는다.

**사실만 남긴다(직접 확인):**
- `tax_basis_rate`는 위저드 화면 라벨 "세금 기준율(0~1)"이며 마진 계산에서 `판매가 × tax_basis_rate`를 비용에 더하는 **내부 비용 근사 입력**이다(`margin_calculator.py`). 실제 세액·세무 처리와 동일하지 않다.
- Wizard#1 저장값(`economics_input_json`, 판매가 12,900원): 상품 5,050 / 채널 수수료 9.6% / 배송 3,000 / 반품 준비 3%가 확정이고 `packaging_cost`·`tax_basis_rate`는 미입력(`null`) → 확정 비용만의 마진 3,224.6원(25.0%), `is_provisional=true`.
- 18% 기준(2,322원)을 지키려면 **미입력 두 항목이 합쳐서 902.6원(판매가의 약 7.0%) 이하**여야 한다. 이는 위 저장값에서 나온 산술이며 어떤 값을 골라야 한다는 뜻이 아니다. 미확인을 0으로 두지 않는 기존 정책은 그대로다.
- 포장비는 온채널의 별도 청구 여부를 공급처 회신으로 확인하지 못했다(회신 미조회).

**회사 공통 적용(표현 정정):** 66차 서술의 "두 값은 회사 공통 정책으로 쓰되 상품마다 재결정하지 않는다"는 **구현 사실이 아니다.** `company_channel_policy_settings`에는 최소마진·한도만 있고 포장비·세금 열이 없으며(`channel_policy/model.py`), 현재 두 값은 위저드(상품등록)별 입력으로만 확인된다. 회사 공통 저장·적용 경로는 확인되지 않았고 만들지 않았다(G2, 출시 후 개선 후보). 이 정정은 새 결정을 요구하지 않는다.

### 9-6. 출시 후보 정리
- 아래 §9-7 참조(Git 대조·제외·묶음·전체 회귀 실행 기록).
### 9-7. 출시 후보 고정과 전체 회귀 실행 기록 (2026-10-05)

**Git 대조(실제 `git status`, HEAD `36f970f` = `origin/main`):** 수정 41개(앱 26·프런트 3·문서 2·테스트 10) + 신규 23개 항목(신규 파일 22 + 스킬 디렉터리 1). 제외 대상: `docs/research/20261005_BIGBUY_API_FEASIBILITY.md`(다른 작업자의 조사 문서). DB·자격증명·`wiz1_state.json` 등은 저장소 추적 대상이 아니거나 포함하지 않는다. 한 묶음이 안전하다: 51~66차가 `purchase_task/model.py`·`constants.py`·`order_submission_service.py`·상태 문서를 공유해 파일을 나눠 커밋하려면 같은 파일을 쪼개 다시 써야 한다(기술적으로 분리 가능한 범위는 신규 독립 파일 — 스킬 디렉터리, `docs/…NORMAL_REGISTRATION…`, `…RELEASE_CANDIDATE_PREP…` — 정도이며 별도 커밋으로 나누는 실익이 작다).

**출시 후보 지문(전체 회귀 시작 직전 17:36):** HEAD `36f970f5af98ee55552b185d8b7c904a3d2666db`, 대상 파일 63개(BIGBUY 제외) 집계 SHA-256 `66c96f41fbdfa8bce61e2b833cde2029b42f6458a591edcfdeae3d6f8c13bbe1`. 저장소 DB `34c5002a…5913d`, 설치판 DB `c28eaf57…22913`.

**전체 회귀 1회 실행 기록**
| 항목 | 값 |
|---|---|
| 명령 | `python -m unittest discover -s tests -p "test_*.py"` (가드 차단 모드: `PYTHONPATH=tests/support/regression_guard;저장소`, `HOMEZ_GUARD_LOG` 저장소 밖) |
| 기준선 | 위 지문. 이전 전체 회귀 4,411건·6,479초(HEAD `2e422b0`, 09-16) |
| 시작·종료 | 2026-10-05 17:36:32 ~ 19:48:50 (프로세스 PID 13544). 도구 쪽 백그라운드 감시 쉘이 2시간 제한으로 종료됐으나 **회귀 프로세스는 계속 실행**돼 정상 종료했다(종료코드는 래퍼가 기록하지 못해 로그의 요약으로 판정) |
| 결과 | **`Ran 5111 tests in 7908.908s — FAILED (failures=8, errors=1, skipped=25)`** — 통과로 선언하지 않는다 |
| 실패 9건 전부 | `test_tour_mode` 한 파일: `TourManifestNodeParsedTestCase.setUpClass` 오류 1 + `TourProgressStorageBehaviorTestCase` 8건. 전부 Node 자식 프로세스가 **빈 stderr로 비정상 종료**(`HOMEZ_TOURS 배열을 node로 평가하지 못했습니다` / `Node 시나리오 실행 실패`) |
| 재현 시험 | `test_tour_mode` 단독 60건 통과(가드 끔·켬 모두) / `test_t*` 77건 통과 / `test_[st]*` 구간 **629건 통과**(같은 프로세스에서 `test_tour_mode` 포함, 811초). **전체(단일 프로세스) 실행에서만 발생, 원인 미확정**: 단독·`test_t*`·`test_[st]*` 구간 묶음에서는 **이번 실행에서 재현되지 않았을 뿐**이며, 선행 테스트 오염이나 제품 코드 결함을 배제한 것이 아니다(`test_s` 이전 구간은 확인하지 않았다). Node를 쓰는 다른 6개 파일(`test_date_time_contract`·`test_i18n`·`test_listing_status_sync_ui`·`test_listing_wizard_ui`·`test_migration_approval_ui`·`test_store_connection_ui`)은 전부 이 실행에서 통과했고 `test_tour_mode`는 Node를 쓰는 마지막 파일이다 — 장시간 실행 중 자원 고갈(추정, 미검증) 또는 그 앞(`test_s` 이전) 구간의 오염 가능성이 남는다 |
| 변경과의 관계 | `console.js`는 이번(64~66차) 라운드에서 바뀌지 않았고 현재 내용으로 단독·구간 실행이 통과한다. 이번 변경이 닿는 모듈은 이 실행의 다른 구간에서 실패가 없다. **다만 원인을 확정하지 못했으므로 "무관"으로 단정하지 않는다** |
| 가드 | 활성(`GUARD_ACTIVE mode=block`). 차단 시도 8건 — 전부 앱 기동(`main.py:155 lifespan` → `migration_restricted_mode.refresh_restricted_mode_state`)이 저장소 DB를 읽으려 한 시도이며 접근은 일어나지 않았고 테스트 실패로 이어지지 않았다(실제 DB 필요 테스트 아님 — 기동 경로의 방어적 조회). 기록된 자식 프로세스는 `node`·`powershell`(가드가 볼 수 없는 비파이썬, 로그만) |
| 실제 DB·자격증명·네트워크 | 회귀 전후 두 DB SHA-256 **동일**(변경 0). Credential Manager·외부 네트워크 접근 시도 차단 기록 0건(차단이 필요한 시도가 없었다) |
| 건너뜀 25 | 이번에는 항목별로 확인하지 않았다(`-v` 미사용). 코드상 조건은 PowerShell 부재·Windows 전용·Node 부재·`HOMEZ_RUN_REAL*` 옵트인 실제 DB 테스트다. 이전 기록(7건)보다 늘었으며 전부 의도된 건너뜀이라고 단정하지 않는다 |

**판정:** 전체 회귀는 **통과가 아니다**(9건 실패, 모두 한 파일의 Node 실행 문제, 단독·구간 재실행은 통과, 원인 미확정). 출시 후보를 "전체 회귀 통과"로 선언하지 않는다. 계획: 커밋 승인 후 **그 커밋 해시를 기준선으로 전체 회귀를 한 번 더** 실행하고(약 2.2시간, 완료 전 통과 선언·push 없음), 그때도 `test_tour_mode`가 실패하면 Node 자식 프로세스의 종료코드·stdout을 캡처하는 진단을 먼저 추가한다(이번 실행은 종료코드를 남기지 않아 원인 확정이 불가능했다 — 테스트 수정은 하지 않았다).

### 9-8. 67차 — 전체 회귀 실패 진단 보강·건너뜀/차단 분류·관측 실행 (2026-10-05~06)

**이전 지시별 수행 증거 구분(요약)**
- 증거 있음(66차까지): 구성수량 모순 정리(§4), 판매계정 검증 점검(§9-2), 운영자 재처리 API 2개와 격리 테스트 18건·변조 3건(§9-3), 설치판 사본 검증 *계획*(§9-4, 실행 아님), Git 대조·지문, 전체 회귀 1회 실행 기록(§9-7, 결과 FAILED).
- 66차에서 **수행하지 않았던 것**: 실패 원인 진단 보강, 건너뜀 25건·가드 차단 8건의 항목별 분류, 세금 서술 철회, 비상정지 읽기 정책 대조, 파일별 해시 기준선, 원인 관측용 실행. "보고 이후 파일이 바뀌지 않았다"는 확인은 이 항목들의 수행 증거가 아니다. 이 절이 그 수행 기록이다.

**1) 진단 보강(`tests/test_tour_mode.py`, 판정 조건은 그대로)** — 기존 코드는 `returncode != 0`일 때 `stderr`만 남겨 이번 실패의 종료코드·stdout이 사라졌다. 두 Node 실행 지점(`setUpClass` 1회, `_run_scenarios` 8개 테스트가 공유)을 `_run_node`로 묶어 실패·시간초과·실행 시작 실패를 **그대로 AssertionError**로 올리되 메시지에 다음을 담는다: 종료코드(십진·16진), 시간초과 여부, stdout/stderr(600자), Node 실행 파일 경로·존재·크기, 작업 디렉터리, 소요 시간, 임시 스크립트 존재·크기, 환경 존재 여부(값 미기록: SystemRoot·TEMP·PATH 항목 수·`NODE_OPTIONS` 등), 프로세스 자원(가용 물리 메모리·페이지파일·핸들 수·스레드 수), 같은 시점의 Node 단순 실행 탐침(`--version`, `-e`). 환경변수 `HOMEZ_NODE_DIAG_LOG`를 주면 **성공 호출을 포함한 모든 Node 호출**을 JSONL로 기록한다(시점별 자원 추이 관측용). 전체 테스트 종료코드와 Node 자식 종료코드는 다른 값이며 전자는 래퍼가, 후자는 실패 메시지가 기록한다. 자체 검증 4건 추가(종료코드 7·빈 stderr 3·시간초과·실행 파일 없음·성공 기록). **진단 헬퍼의 초기 결함 1건을 관측 실행 준비 중 발견·수정**: `GetProcessHandleCount` 인자 타입을 지정하지 않아 의사 핸들 변환 오류가 나 자원 값이 비어 기록되던 문제 → `argtypes` 지정 + "Windows에서는 `snapshot_error` 없이 핸들 수·가용 메모리가 있어야 한다"는 단언 추가.

**2) 가설별 좁은 검증(결과는 "미재현"이며 배제가 아님)**
| 가설 | 검증 | 결과 |
|---|---|---|
| Node 자체가 간헐적으로 빈 stderr로 종료 | 테스트와 같은 방식(임시 `.js` 생성→실행→삭제) 1,000회 순차 | 전부 종료코드 0, 이상 0 |
| CPU 부하 | CPU 코어 수(4)만큼 점유하며 400회 | 전부 0(중앙값 0.66초, 최대 1.64초) |
| `test_s*`~`test_t*` 선행 테스트의 환경 오염 | 같은 프로세스에서 629건(`test_tour_mode` 포함) | 통과(`test_s` 이전은 미확인) |
| 콘솔 없는 분리 실행이 원인 | `test_ai_governance`를 분리 실행 | 16건 통과 — 분리 실행은 원인이 아님 |
| **메모리 압박** | PC 사양·시점 관측 | 물리 메모리 **4,011MB**, 스냅샷 시 사용률 92%·가용 290MB(Claude·Chrome·ChatGPT 등과 공유). 샤드 실행 내내 사용률 83~91%, 최저 가용 320MB. 단일 프로세스 2차 시도는 초반(131번째 테스트 부근, `test_ai_governance`)에서 **33분간 CPU 약 0.25초·스레드 1개로 정지**했고 같은 모듈은 단독·분리 실행에서 통과. **관측 사실과 부합하나 실패 당시의 값은 기록하지 못해 원인으로 확정하지 못했다** |
또한 부하가 겹친 시간대에 `test_homez_desktop.test_log_file_never_contains_session_token`(서버 15초 준비 시간제한)이 한 번 실패했고 단독 재실행은 통과(57건) — 부하에 민감한 시간제한 테스트다.

**3) 관측 실행**
- 단일 프로세스 2차 시도(20:57 시작): 위 정지로 **내가 종료**했다. 결과 없음(통과·실패 아님).
- 같은 전체 테스트를 **12개 샤드(짧은 수명 프로세스)** 로 나눠 가드 차단 모드로 실행(래퍼가 샤드별 종료코드·시간·메모리를 기록, 테스트별 정지 감시·스택 덤프, 20분 무진행 시 종료). **12개 샤드 모두 종료코드 0, 5,136건 실행, 건너뜀 25, 실패 0, 정지 0, 스택 덤프 0**, `test_tour_mode`(64건) 통과. 시간: 샤드당 198~1,723초(테스트 시간 합계 8,174초=2시간 16분, 경과 22:05~00:27=2시간 22분). Node 호출 기록 12건(비정상 종료코드 2건·시간초과 1건은 위 자체 검증이 일부러 만든 실패 — 실제 테스트의 Node 실패 기록은 0건): 소요 중앙 0.21초·최대 1.04초, 호출 시점 핸들 179~182개·가용 물리 메모리 638~695MB. 프로세스 핸들 수는 샤드 안에서 107→최대 235(샤드 4)로 누수 신호 없음.
- **판정 한계(중요):** 샤드 실행은 단일 프로세스 `discover` 전체 회귀와 **같지 않다**(프로세스 간 상호작용·누적 상태는 검증하지 않는다). **관측한 두 번의 단일 프로세스 실행에서 각각 실패(Node 9건)와 정지가 발생했다**(관측 사실이며 "이 PC에서 단일 프로세스는 신뢰할 수 없다"는 일반화로 쓰지 않는다 — 그렇게 말할 만큼의 반복 관측이 없다). 따라서 "9건 실패의 원인을 찾았다"가 아니라 "원인은 미확정이며, 같은 테스트 전부가 샤드 실행에서는 통과했다. 메모리 압박은 관측 사실과 부합하는 **가설**일 뿐 제품 결함을 배제하지 않는다. 분리·단독·구간 실행의 통과는 그 조건에서 미재현이라는 뜻이지 원인 배제의 증거가 아니다"가 현재 확정 수준이다. 5,136건과 이전 5,111건의 차이 25의 항목별 대조는 §9-9에 있다.

**4) 건너뜀 25건 — 전부 이름·사유 확인(`-v`)**
| 사유 | 건수 | 분류 | 출시 검증에 미치는 영향 |
|---|---|---|---|
| 실제 설치환경 진단 테스트(`HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1` 옵트인) | 18 | **의도된 제외** — 실제 `homez.db`·백업을 열지 않기 위해 기본 회귀에서 제외 | 실제 DB 대상 불변·적용 여부 확인은 이 회귀가 **검증하지 않는다**(별도 승인된 실환경 검증 몫) |
| 실제 Windows Credential Manager 통합 테스트(`HOMEZ_RUN_REAL_CREDENTIAL_TESTS=1` 옵트인) | 7 | **의도된 제외**(IA-012) | 실제 자격증명 저장소 연동은 이 회귀가 검증하지 않는다 |
| 환경 문제(Node·PowerShell 부재 등) | 0 | — | — |
| 설명 없는 건너뜀 | 0 | — | — |
통과를 만들기 위해 가드를 해제하거나 skip을 추가하지 않았다. 건너뜀은 통과로 세지 않는다.

**5) 가드 차단 8건 — 출처 확정**: 전부 `sqlite3.connect`로 저장소 `homez.db`를 읽으려는 시도(차단, 접근 없음). 6건 `test_homez_desktop`(서버 기동 → `lifespan`의 `refresh_restricted_mode_state`), 2건 `test_migration_approval`(Migration 적용 직후 같은 재계산). 해당 코드는 진단 실패를 **fail-closed(제한 모드)** 로 처리하도록 설계돼 있고(`migration_restricted_mode.py`), 두 테스트 파일은 제한 모드 상태를 단언하지 않는다(주석뿐) → 이 차단으로 **삼켜진 검증은 확인되지 않았다.** 실제 DB 진단 경로의 정상 동작은 임시 DB를 쓰는 `test_migration_restricted_mode`가 검증한다. 참고: 실제 저장소 DB는 대기 Migration 1건(`20261005_00`)이 있어, 가드 없이 앱을 기동하면 제한 모드(쓰기 423)가 되는 것이 설계 동작이다 — 이 Migration 적용 전에는 그렇다. Credential Manager·외부 네트워크 접근 시도 차단은 0건. 두 DB 해시는 실행 전후 동일.

**6) 비상정지 경계 정정(확정 정책 충돌)**: 확정 정책(`HOMEZ_USER_OPERATION_SETTINGS.md` 232·296행)은 "긴급 중지 시 상품등록·가격변경·결제·발주는 멈추고 **조회·백업·진단은 유지**"인데 66차 `reconcile`은 비상정지 중 읽기까지 차단해 충돌했다. `reconcile_pending`의 비상정지 조기 반환을 제거해 읽기 조회·대조는 유지하고, **변경은 계속 차단**한다(외부 변경 요청은 이 경로에 없음, 내부 주문 취소 반영은 `_step_gate`가 BLOCKED, `re_execute`는 현재 상태 읽기만 하고 변경은 BLOCKED). 테스트 2건(읽기 유지·변경 차단 / 재실행 차단) + 변조 2건(옛 차단 복원·`_step_gate`가 비상정지 무시) 모두 잡힘, 원본 복구 확인. 실환경 반영 없음.

**7) 세금·포장비 정정**: §9-5에서 철회·정정(세금 라벨·순부담 계산·사용자 선택 요구 철회, `tax_basis_rate`는 내부 비용 근사 입력, 회사 공통 적용은 구현 사실 아님).

**8) 검증 기준선(커밋 없이 고정)**: `docs/HOMEZ_V7_RC_BASELINE_MANIFEST_20261005.txt` — HEAD `36f970f…`, 실행 환경(OS, Python 3.13.14, Node v24.18.0, 주요 패키지 버전, pip freeze 해시, 가드 파일 해시), 두 DB 지문, **대상 64개 파일의 파일별 SHA-256**(수정 시각은 참고용), 코드 집계(문서 제외 60개) `b48cfb13089b8faa948a1c8f25ce98d71857dc9e7864dee1334fbea157229f97`. BIGBUY 조사 문서·목록 자신·DB·자격증명은 제외. **목록 스크립트 결함 발견**: 최초 목록은 `git status` 출력에 `strip()`을 적용해 첫 줄의 앞 공백이 사라져 첫 파일(`app/domains/channel_policy/service.py`)을 빠뜨린 63개였다 — 실행 후 집계 불일치로 발견해 수정·재생성했다. 최초 목록의 나머지 63개 파일은 실행 후 해시가 **모두 일치**함을 확인했다. 빠졌던 1개는 실행 전 해시가 없어 수정 시각(2026-10-04 17:40, 실행 이전)이라는 **보조 근거**로만 불변을 뒷받침한다. 커밋 승인은 진단·집중 테스트의 선행조건이 아니었다.

**9) 남은 한계**: 단일 프로세스 전체 회귀의 통과는 이 PC에서 확인하지 못했다(원인 미확정). 샤드 통과는 단일 프로세스 통과와 동치가 아니다. 건너뜀 25건이 가리키는 실환경 검증(실제 DB 불변·자격증명 저장소)은 이 회귀 범위 밖이다. 설치판 사본 검증, 커밋·push, Migration 적용, 실거래 시험은 하지 않았고 승인도 없다.

### 9-9. 68차 — 테스트 집계 대조 (실행 5,136건의 빈틈 확인, 전체 재실행 없음)
- **발견 ID와 실행 ID를 직접 대조**: 337개 파일을 실행 없이 열거(가드 활성, 샤드와 같은 파일 단위 discover)해 발견 5,136개, 12개 샤드 로그(`-v`)에서 실행 ID를 추출해 대조했다 → **누락 0, 횟수 불일치 0**(로그 파싱에서 나온 "실행 5,137"의 1은 docstring 문구 "(버그로)"가 정규식에 걸린 아티팩트).
- **중복**: 고유 ID 5,044개, 중복 실행분 92건 — `tests.test_listing_wizard_service`의 테스트 클래스를 3개 파일(`test_coupang_brand_search_endpoint`·`test_coupang_live_submission`·`test_operational_events_wiring`)이 가져와(import) 같은 46개 ID가 3번씩 수집·실행된다. 단일 프로세스와 샤드가 같은 구조이며 샤드 때문에 생긴 중복·누락이 아니다.
- **"실행 5,136건"에 건너뜀 25건이 포함된다**(`Ran N`은 건너뜀을 포함) → 통과 5,111 + 건너뜀 25. (5,111이라는 숫자는 66차 단일 프로세스 실행의 총 건수와 우연히 같다 — 서로 다른 값임을 혼동하지 않는다.)
- **5,111 → 5,136의 차이 25 설명**: `TourManifestNodeParsedTestCase` 20건(66차 실행에서는 `setUpClass` 오류로 한 건도 실행되지 않음 — 발견 ID로 직접 확인) + 진단 자체 검증 `NodeDiagnosticsTestCase` 4건 + 비상정지 테스트 1건 분리(+1, 코드 변경 기록 근거) = 25.
- **샤드 이후 추가된 테스트**(5,136에 미포함, 집중 실행 통과): `test_rc_baseline_manifest` 10, `test_db_logical_compare` 8, `test_packaged_guard_hook` 3 = 21건.
- 이상이 없으므로 이 확인을 이유로 전체 테스트를 다시 돌리지 않았다.

### 9-10. 건너뜀 25건 → 별도 검증 목록 (의도된 skip은 검증 완료가 아니다)
| 건너뜀(건수) | 테스트(요약) | 이 검증이 하는 일 | 별도 검증 방법(연결) |
|---|---|---|---|
| 실제 설치환경 진단 18 | ① 실제 DB 불변 확인 8(`test_audit_logs_migration`의 파일 불변, `test_bootstrap_production_db_guard`, `test_homez_canonical_db_check_script`, `test_migration_approval`, `test_migration_restricted_mode`, `…_db_path_contract`, `test_notification_delivery_migration`, `test_v7_pre_live_migration_rehearsal`) ② 실DB에 Migration·테이블이 적용돼 있는지 확인 4(`test_marketplace_fulfillment_migration`, `test_media_listing_package_migration`, `test_store_connection_migration`, `test_media_asset_public_hosting_migration`) ③ 승인 드리프트(새 컬럼·테이블이 승인된 감사 로그와 함께만 존재) 3(`test_listing_wizard_soft_delete_migration`, `test_media_asset_rights_evidence_migration`, `test_media_asset_source_tracking_migration`) ④ 감사 로그 Migration의 실DB 복사본 대조 3(`test_audit_logs_migration`) — 8+4+3+3=18 | 실제 업무·설치판 DB에 대한 불변·적용 상태 단언 | **회귀가 하지 않는 검증.** 승인안(`…INSTALL_COPY_VERIFICATION_APPROVAL…`) 단계 b·d·f의 원본 전후 지문, `db_logical_compare`, `schema_migrations` 대조가 대체하는 항목과 대체하지 못하는 항목(실제 설치판 DB의 Migration 상태 — 설치판 DB는 대기 36건이며 별도 승인 대상)을 구분해 기록 |
| Credential Manager 통합 7 | `test_windows_credential_store`(저장·조회·삭제·교체 6) + `test_restore_helper` 종단 1 | **실제** Windows 자격증명 저장소 연동 | **별도 승인 필요**(이 테스트는 실제 저장소에 시험 항목을 만들고 지운다). 승인안에는 포함하지 않았다 — 설치판 자격증명 연동의 실환경 검증 목록에 남긴다 |
환경 문제로 인한 건너뜀 0, 설명 없는 건너뜀 0. skip을 통과로 세지 않으며 가드 해제·skip 추가는 없었다.

### 9-11. 샤드 결과의 출시 기준 충족 판단
- **확정 기준 원문**: `HOMEZ_USER_OPERATION_SETTINGS.md` 299행 "개인 베타 시작 조건은 **전체 회귀**, Migration 정합성, 백업·복구 시험, 실제 상품 2건의 지정된 전체 과정과 긴급 중지 검증이다." — **실행 방식(단일 프로세스 여부)을 규정하지 않는다.** `V6_EXECUTION_LEDGER.md` 6434행의 "단일 프로세스, 청크 없이 1회"는 V6 Gate 1의 실행 기록이며 이후 기준이 아니다. 같은 장부 6620행(V7 Gate 2)·6770행에는 **메모리 제약 대응으로 청크 24~25개로 나눠 순차 실행한 전체 회귀가 공식 결과로 인정**돼 있다(실패 시 청크 전체를 처음부터 재실행). 따라서 **12샤드 전체 통과는 확정 기준 "전체 회귀"의 범위를 충족하는 것으로 판단하며 단일 프로세스를 새 출시 조건으로 만들지 않는다.** (같은 문서 `V7_TEST_ISOLATION` §3: 병렬 4프로세스 사전 점검이 `MemoryError`로 죽어 폐기한 이력 — 이 PC의 메모리 제약은 새로운 사실이 아니다.)
- **미해결 위험(별도 기록, 출시 조건 아님)**: R-1 "단일 프로세스 누적 상태 결함 가능성". 근거 판단: ① 실패·정지는 **테스트 실행기 프로세스**(수천 개의 서브프로세스·임시 DB를 만드는 장시간 프로세스)에서 관측됐고 앱 프로세스에서는 관측되지 않았다 ② 샤드별 핸들 수는 107→최대 235(샤드 4)로 한 샤드(405~602건) 안에서 증가가 있었으나 프로세스당 한도에 비해 매우 작다 — 선형 외삽해도 5,136건에서 약 1,600개 수준(**추정**) ③ 그러나 **실제 앱의 장시간 실행(soak)은 한 번도 측정하지 않았다**(미검증) → 앱과 무관하다고 단정하지 않는다. 승인안 단계 e에 시작·10분 후 핸들·작업 집합 기록(소규모 관측)을 넣었다.
- **추가 시험이 필요하면(지금은 필요하지 않음)**: 가설 H-A "단일 프로세스의 누적 자원이 늦은 구간의 Node 자식 실패를 유발". 최소 범위: 샤드 1~3(약 1,430건, 약 30분)을 **한 프로세스**에서 실행하며 50건마다 자원 기록(`SHARD_RESOURCE_LOG`)과 Node 호출 기록을 남겨 핸들·메모리 추이만 관측(재현이 목적이 아님). 종료 조건: ① 완료 ② Node 비정상 종료 발생(진단 보존) ③ 20분 로그 무진행(스택 덤프 후 종료). 실행 조건: 시작 시 가용 물리 메모리 ≥1GB(다른 프로그램 종료 필요 — 사용자 동작), 다른 부하 없음. 가설 H-B "시스템 메모리 압박"은 같은 전체를 메모리 여유 상태에서 단일 프로세스로 1회 실행해야 검증되므로(약 2시간 이상) **출시 조건이 아니라 선택 시험**으로만 둔다.

### 9-12. 기준선 목록 결함 마무리와 `channel_policy/service.py` 재검증
- **구조적 방지**: 기준선 생성을 `tools/rc_baseline_manifest.py`로 옮겼다 — `git status --porcelain=v1 -z --untracked-files=all`(NUL 구분, 따옴표·이스케이프 없음)을 바이트로 파싱하고 어떤 문자열에도 `strip()`을 쓰지 않는다. 분류(제품/프런트/Migration/테스트/문서/스킬/도구/기타)별 집계, 경로·**접두사 제외**(다른 작업자의 `docs/research/` 전체), 이름 변경 처리 포함. 집중 테스트 `tests/test_rc_baseline_manifest.py` 10건(첫 항목의 앞 공백, 공백·한글 경로, 수정·신규·삭제·이름 변경, 신규 폴더 펼침, 제외·접두사 제외, 내용 대 mtime 구분) + 변조 3건(`strip()` 복원, 첫 항목 건너뜀, 이름 변경 토큰 미소비) 모두 잡힘. 이 사건의 문제 기록: 증상=기준선 집계 불일치 / 원인=출력 전체 `strip()` / 영향=첫 파일(`channel_policy/service.py`) 누락 / 수정=NUL 구분 파서 / 재발 방지=위 테스트 / 실환경 반영=해당 없음.
- **`channel_policy/service.py`**: 실행 전 해시가 없던 파일이라 **수정 시각(2026-10-04 17:40)만으로 불변을 확정하지 않았다.** 영향 경로(이 파일을 쓰는 앱 모듈의 테스트)를 **현재 해시 기준**으로 재검증: 해시 `1ac42f0f466f5e9b9e8a674b6bb9aa1c44c5e272cff2d7a4955cd75b94901d5e`가 실행 전후 동일한 상태에서 9개 모듈 **607건 전부 통과**(`test_channel_policy_engine` 73, `test_ai_governance` 16, `test_listing_wizard_service` 46, `test_coupang_live_submission` 184, `test_live_gate4_fix_defects` 29, `test_operational_events_wiring` 222, `test_product_selection_overview` 7, `test_listing_wizard_margin_gate` 9, `test_purchase_order_margin_policy_gate` 21). 샤드 실행 당시 내용과 같다는 증명은 아니며(그 시점 해시 없음), "현재 내용이 영향 경로 테스트를 통과한다"는 증거다.
- **새 기준선**: `docs/HOMEZ_V7_RC_BASELINE_MANIFEST_20261006.txt` — 72개 파일(제품 33·프런트 3·Migration 1·테스트 25·문서 6·스킬 1·도구 3 — 승인안 문서 추가 후 최종 목록 기준), 분류별 집계 해시, 코드 집계(문서 제외) `51d80f88d129bc141f2dd7077c01704d2b139bc4dc09a6db69fdb690d03a208f`. 제외: `docs/research/`(다른 작업자 — BIGBUY 문서와 오늘 생긴 `20261006_CJ_ORDER_FLOW_FEASIBILITY.md`), 목록 자신, DB·자격증명. 67차 기준선(64개) 대비 **제품·프런트·Migration·기존 테스트는 변경 0**, 문서 2개만 변경, 신규는 이번 도구·테스트 6개.

### 9-13. 설치판 검증 승인안과 새 검증 도구
- 승인안: `docs/HOMEZ_V7_INSTALL_COPY_VERIFICATION_APPROVAL_20261006.md`(하나의 승인안, 단계 a~f). 승인 전 어떤 단계도 시작하지 않았다.
- **개발용 Python 가드는 패키징 실행을 보호하지 않는다(직접 실측)**: 탐침 앱(HOMEZ 아님)을 같은 `PYTHONPATH` 가드 환경변수로 실행하면 `no_site=1`·`ignore_environment=1`로 `sitecustomize`가 로드되지 않아 가드 로그가 생기지 않았다(일반 파이썬 양성 대조는 `GUARD_ACTIVE`). 검증용 빌드 전용 런타임 훅 `tools/packaged_guard_hook.py`(옵트인, 가드 미설치 시 시작 거부)를 탐침으로 검증(합성 보호 파일 차단 확인)하고 `tests/test_packaged_guard_hook.py` 3건 + 변조 3건으로 고정. **HOMEZ 빌드에 훅을 넣은 상태의 확인은 아직 하지 않았다**(승인안 단계 c의 양성 대조).
- **논리 대조기** `tools/db_logical_compare.py`(읽기 전용, 셀 값 미출력): 무결성·스키마·Migration 이력·테이블별 행 수와 내용 지문·논리 참조 고아 수를 대조. 바이트 해시는 합격 조건이 아니다. `tests/test_db_logical_compare.py` 8건(바이트가 다른 `VACUUM INTO` 사본이 논리 동일로 판정되는 것 포함) + 변조 4건 모두 잡힘. 기존 복구 검증(`restore/service.py`)은 바이트 SHA-256 + `integrity_check` + 필수 테이블 존재까지만 본다는 점을 확인했다.
- 승인 구분: A 설치판 사본 검증(이 승인안) / B 커밋·push / C 원본 Migration 적용 / D 설치판 경로 전환·이력 이관 / E 외부 실행 — 서로를 포함하지 않는다. 커밋은 검증의 선행조건이 아니다.
- UNKNOWN 제출 이력: 현재의 "재등록 금지"는 **임시 운영 제한**이지 영구 중복 방지 구현이 아니며, 원본 전환(D) 전에 방안을 선택해야 한다(승인안 §7). 설치판 DB의 과거 이력은 보존하며 덮어쓰지 않는다.

### 9-14. 69차 — 승인 범위 확인과 승인안 정정 (실행 없음)
- **기존 승인 확인**: 설치판 사본 검증(빌드·원본 읽기·앱 실행)을 포함한 승인은 없다(이전 지시는 모두 "승인안으로 정리"였고 승인 응답이 없었다) → 승인안 A~D를 요청 상태로 두고 어떤 단계도 시작하지 않았다. 이 프롬프트도 승인으로 해석하지 않았다.
- **빌드 환경 기술 근거(직접 확인)**: 잠금 파일 없음(최상위 20개만 정확한 버전, 하위 미고정). 선언 의존성 폐포 85개가 전부 개발 venv에 동일 버전으로 설치(폐포 밖 5개: `cryptography`·`passlib`·`pip`·`py-spy`·`pyjwt`), 619MB·스크래치 여유 약 104GB. pip 캐시 휠 1개라 `--no-index` 오프라인 설치 불가, `python -m venv`(번들 pip 26.1.2)는 오프라인 생성 가능 → **네트워크 없이 폐포 복사 venv 가능(권장)**, PyPI 정확 버전 설치와 개발 venv 직접 사용은 선택지로 한계와 함께 승인안 §4에 정리.
- **승인안의 틀린 가정 정정(자격증명 격리)**: `HOMEZ_DATA_ROOT`/`HOMEZ_TEST`는 **새로 만드는** 자격증명 대상명에만 적용되고(`store_connection/service.py:313,573`), 읽을 때는 DB에 저장된 참조를 그대로 쓴다(`coupang_collection_service.py:124`, `listing_wizard_router.py:297,324,443`) + 고정 대상명 경로 존재. 업무 DB 사본을 연결한 격리 앱은 실제 자격증명을 읽을 수 있었다. **대책**: 작업 사본에서만 참조 무력화 + 순수 백업 사본 분리 보존 + 가드의 Credential Manager 차단 + 가드 양성 대조 실패 시 합성 데이터 검증까지만. 문제 기록: 증상=승인안 §2 보조 방어 ②의 오류(실행 전 발견) / 원인=네임스페이스가 생성 시점 규칙임을 놓침 / 영향=승인 후 실행했다면 격리 앱이 운영 자격증명 접근 가능 / 수정=승인안 정정 / 재발 방지=c0 양성 대조·무력화 확인을 중단 조건으로 명시 / 실환경 반영=없음.
- **패키징 가드 범위 실측(탐침, HOMEZ 아님)**: 훅 활성 시 `CredReadW` 심볼 조회와 비라우팅 주소(`192.0.2.1:9`) 접속이 호출 이전에 `ATTEMPT_BLOCKED`로 차단·기록됨. 가드 한계(네이티브 접근, 미리 해석한 ctypes 호출, 비파이썬 자식, SQL `ATTACH`)는 기록하고 보호를 보장할 수 없는 동작은 실행하지 않는다는 규칙을 승인안에 추가.
- **UNKNOWN 제출 이력 — 기존 기능 범위**: 정합화(`preview/apply_reconciliation`)는 **같은 DB 안의 제출 건만** 처리하고 설치판 DB에는 필요한 테이블 Migration(`20260830_01`)이 대기 중, **다른 DB로 이력을 옮기는 기능은 없음**(app/ 검색). "재등록 금지"는 임시 운영 제한이며 영구 중복 방지 구현이 아니다. 승인 없이 이관·상태 변경 없음. 사본 검증을 멈추지 않는 독립 조건으로 유지.
- 새 코드·테스트 변경 없음(스크래치 탐침 빌드만). 코드 집계 `51d80f88…a208f` 그대로, 두 DB 해시 불변.

### 9-15. 70차 — 설치판 사본 검증 실행 (A~D 승인 범위, 2026-10-06)
승인 범위: 검증용 임시 경로 쓰기만. 원본 DB·운영 자격증명·실제 설치판 변경, 경로 전환, 이력 이관, 외부 업무 API, 실거래, 커밋·push는 승인 밖이며 하지 않았다. 화면 확인 결과는 사용자 보고 대기 중이다(아래 "대기").
- **A 검증 빌드(직접 실행)**: 개발 venv(Python 3.13.14, PyInstaller 6.16.0, pip freeze 89줄 SHA `be189e40…90b3`), 네트워크·PyPI 사용 없음. 빌드 명령 `venv\Scripts\python.exe -m PyInstaller <scratch>\homez_verify70.spec --noconfirm --distpath <scratch>\dist --workpath <scratch>\work`, 454초, exit 0. spec은 `homez.spec` 사본(절대 REPO_ROOT, 진입 스크립트 절대경로, 가드 hiddenimports, 런타임 훅 `tools/packaged_guard_hook.py`). Homez.exe SHA `97691a96…74e4`(26,399,548 B), 번들 3,637파일 363 MB, 번들 파일목록 해시 `b920906d…4856`, migrations 77개, `20261005_00` SHA `18ae61f4…8b55` 저장소와 일치, 웹 자산 4종 소스와 해시 동일. 저장소 `homez.spec`·`dist`·`build` 변경 없음. 빌드 경고 9건은 선택 의존성 모듈 누락과 numba용 DLL 2개(앱 기동에는 영향 없음, 실행 중 문제 미관측). 재현 가능 빌드(깨끗한 환경, 요구사항 완전성)는 이번 범위가 아니며 별도 남은 검증이다. 85개 복사 환경은 정식 설치와 동등하다고 보지 않는다.
- **실제 HOMEZ 빌드의 가드(직접 실행)**: 가드 파일 없음 → exit 97, 가드 미설치 → exit 98(시작 거부). 합성 가짜 DB를 보호 대상으로 두면 앱의 `sqlite3.connect`가 `bootstrap_environment`(앱 프레임)에서 차단되고 앱은 exit 1, 가짜 DB 불변. 안전 차단 시험 13건 중 11건 차단: 원본 DB 열기·`mode=ro` 연결·보호 폴더 안 파일 생성, Credential Manager 심볼 `CredReadW`·`CredEnumerateW`·`CredWriteW`, 비루프백 접속 2종. **미차단 2건: 보호 폴더의 목록 조회**(이름만 노출, 내용 열기·생성은 차단). 앱 실행 중 앱 자신의 `CredReadW`(세션 저장소) 9건이 차단됐고 쓰기 시도는 0건이다. 중요한 발견: 격리 앱은 `LOCALAPPDATA`를 덮어쓰므로 가드의 기본 보호 목록이 실제 `%LOCALAPPDATA%\HOMEZ`를 가리키지 않는다 → 실제 경로를 `HOMEZ_GUARD_PROTECTED_PATHS`에 절대경로로 명시해 해결. 가드 한계(미해소): ctypes 사전 해석·네이티브 확장, 비-Python 자식(앱 코드에서는 `restore_helper`만 subprocess 사용, 같은 exe), 목록 조회. 죽은 프록시·`HOMEZ_TEST`만으로 격리됐다고 보지 않는다.
- **B 복사본(직접 실행)**: 저장소 `homez.db`를 `mode=ro` 연결에서 `sqlite3.backup()`으로 보존 백업(읽기 전용 속성) 생성 → 작업 사본은 보존 백업에서 파생. 두 사본 SHA 동일(`f342a854…`), 원본과 논리 비교 일치(무결성 ok, FK 위반 0, 마이그레이션 76행, 테이블 159, 총 492행). 원본 SHA `34c5002a…`·설치판 DB `c28eaf57…` 작업 전후 동일, 백업 폴더 목록 동일.
- **C 자격증명 무력화**: 작업 사본 3행(store_connections 1, purchase_channel_connections 2 — 1행은 원래 비어 있었음)의 `credential_reference`만 변경(`expected_changes_neutralize.json`). 원본·보존 백업에는 적용하지 않음. **이 시점 이후 "보존 백업 = 작업 사본"은 성립하지 않는다.**
- **C 화면 Migration(직접 실행, 사용자 로그인·재인증)**: 앱이 승인 전 `/desktop-setup/migration-status`로 대기 파일 `20261005_00` 한 건, 대상 테이블 1·인덱스 4, 백업 경로(scratch)를 보고했다. 사용자가 로그인하고 승인했다(비밀번호는 읽지도 기록하지도 않음). 작업 사본: 이력 77행(신규 1행, 체크섬 일치, 기존 76행 불변), 테이블 `channel_action_requests`(0행)+인덱스 4, 무결성 ok, FK 위반 0, 스키마·이력은 별도 사본 dry-run과 동일. 앱 자동 백업 `homez_pre_bootstrap_migration_*.db` 1개(3,899,392 B)는 무결성 ok·이력 76행·새 테이블 없음(적용 이전 상태), 로그인 직후 시점이라 로그인 흔적 포함.
- **차이 분류(전수)**: ① 예상: 신규 테이블·인덱스 4·이력 1행, 자격증명 3행(컬럼 `credential_reference`만). ② 정상 앱 동작: 감사 4건(`MIGRATION_PENDING_DETECTED`·`MIGRATION_APPLY_APPROVED`·`MIGRATION_APPLIED`·`CHANNEL_POLICY_RULE_CATALOG_SEEDED`), 로그인 세션·리프레시 토큰·토큰 가족 각 1, 백업 기록 1. ③ **계획에 없던 차이(기동 시 시딩)**: `channel_policy_rules` 0→10행, 권한 `VIEW_SENSITIVE_DATA` +1 — 새 코드 기동 시 기준 데이터이며 원본 첫 기동에서도 같은 행이 생긴다(재시작 시 추가 0건, 멱등). 보존 백업에 있던 행이 사라진 경우 0. 업무 테이블(마법사 2·최소 마진 0.18·스토어 연결 1·구매 연결 2·판매 신청 1 NEEDS_REVIEW·제출 0·옵션 링크 0·상품 후보 3·미디어 2·사용자 1) 상태·행 수 동일.
- **D 파일 복원(직접 실행)**: 보존 백업을 별도 파일로 복원 → 논리 비교 일치, 핵심 스냅샷 동일(바이트 해시는 합격 기준 아님). **앱 복원 UI·`restore_helper` 경로는 실행하지 않았다(미검증)**: 코드상 도우미는 `env=` 없이 같은 exe를 띄워 환경이 상속되고 대상 DB는 앱 엔진 경로로 고정되지만 실행 증거가 없다. 파일 복원 완료를 앱 복원 검증으로 확대하지 않는다.
- **재시작(직접 실행)**: 창 정상 종료(14:12:50 "정상 흐름 복귀") 후 같은 격리 설정으로 1회 재시작(포트 63002→50323, 무작위가 설계). 대기 Migration 없음, 이력 77행 유지(중복 적용 없음), 종료 직전·재시작 후 스냅샷 동일, 시딩 추가 0건, 가드 재활성. 사용자가 재로그인(Credential Manager 차단으로 세션이 유지되지 않는 것은 제한이며 데이터 유실 아님).
- **자원 관찰(보조 증거, 출시 필수 조건 아님)**: 핸들 549(시작 직후)→523~530(약 +30분), 비공개 메모리 244 MB. 작업 집합은 메모리 압박으로 변동 커 의미 없음. 측정점이 적어 누수 없음·장시간 안정성은 판단하지 않는다.
- **원본 보존 확인과 Credential Manager 관측(70차 후속)**: 원본 두 DB·백업 폴더 목록은 시작 전·중간·마지막(앱 실행 중) 대조에서 동일. 대상 개수는 **12→15**. 관측 사실과 추론을 구분한다. (관측) 격리 앱에서 가드가 기록한 Credential 호출은 `CredReadW` 읽기 차단 9건뿐이고 쓰기·삭제 시도 0건, `HOMEZ_TEST` 항목 0, 실제 설치판 미실행. (식별) 전 상태는 정렬 이름의 해시만 남아 있었으나, 현재 15개 이름 중 3개를 뺀 455개 조합의 해시를 기존 해시와 대조해 **정확히 1개가 일치**했고, 새로 생긴 3개는 `MicrosoftAccount` 2·`WindowsLive` 1(길이 41/61/72)이며 HOMEZ 항목의 추가·삭제는 없다(새 접근 없음, 이름만 사용). 중간 지문(13:20:55)은 12개, 마지막은 15개(14:39) → 증가 시점은 그 사이이며 **격리 앱 실행 구간(13:23~14:12, 14:37~)과 겹친다**. (한계) 이 식별은 이름 분류까지이며 쓴 주체를 확정하지 못한다. 가드는 ctypes 사전 해석·네이티브·비-Python 자식 경로를 보지 못하므로 "앱과 무관"으로 확정하지 않는다. 대상명·개수·해시 동일은 비밀값 불변의 증명이 아니다. Microsoft 계정류가 운영체제에 의해 갱신된다는 것은 가능한 설명일 뿐 확인하지 않았다. 이름별 해시 기준선(`fp_baseline2.json`)은 **이후 변경 식별용**이며 과거 원인 해소나 값 불변을 뜻하지 않는다. 기존 기준선(`fp_before`·`fp_after_scope`·`fp_after`)은 덮어쓰지 않고 보존했다. 다음에 필요한 증거: 같은 구간에 격리 앱 없이 개수를 관측하는 대조(앱 미실행 상태에서 일정 시간 전후 개수·이름 해시) — 이번 범위는 아니며 필수 조건으로 만들지 않는다. 비밀값 조회·항목 삭제·재생성은 하지 않았다.
- **기동 시 시딩(예정 외 차이)의 경로·범위·내용과 원본 적용 계획 반영**: Migration 변경과 앱 기동 시딩은 별개다. 시딩은 부팅 때 자동 실행되고(`app/database/seed.py`, `channel_policy.service.seed_channel_policy_catalog_at_boot`), **Migration 승인 전**에 일어난다(작업 사본 로그: 승인 대기 Migration=True 상태에서 시딩 완료). (권한) `VIEW_SENSITIVE_DATA` 1행을 없으면 추가(`권한 추가=1건` → 재시작 `0건`), 어떤 역할에도 매핑하지 않으며 `role_permissions` 변화 없음(SUPER_ADMIN만 단락 평가로 통과, 다른 역할은 "아무도 못 봄"이 기본). (채널 정책) 코드 카탈로그 10규칙을 `(channel, rule_code)`로 upsert: 없으면 삽입, **있고 값이 다르면 카탈로그 값으로 덮어쓰기(active 포함)**, 같으면 무변경·감사 없음. 감사 `CHANNEL_POLICY_RULE_CATALOG_SEEDED`는 변경이 있을 때만 1건. 개별 규칙 수정 API는 없다(라우터는 시딩·평가·목록·회사 설정만) → 운영자 편집과 충돌할 경로는 코드상 없고, 동일 값 재시딩 무변경은 재시작에서 직접 관측했으며, 값이 다른 기존 행의 덮어쓰기·감사 1건은 기존 테스트(`test_channel_policy_engine`의 재시딩 3건)가 검증한다(이번에 재실행하지 않음). **원본(저장소 `homez.db`)에 대한 예상**: 현재 `channel_policy_rules` 0행, 권한 `VIEW_SENSITIVE_DATA` 없음이므로 첫 기동에서 **규칙 10행 삽입+권한 1행+감사 1건**이 예상되나, 이는 그 시점의 DB 상태에 따라 달라지는 조건부 결과이며 무조건 11행으로 단정하지 않는다. 설치판 DB(`%LOCALAPPDATA%`)는 36건 Migration이 대기 중이라 `channel_policy_rules` 테이블 유무와 시딩 동작을 확인하지 않았다(미검증, 이번 업무 DB 범위 밖). **승인안에 포함할 영향**: ① 시딩이 Migration 승인 전에 원본을 바꾼다. ② 앱이 만드는 자동 백업(`homez_pre_bootstrap_migration_*`)은 시딩 **이후** 시점이라 순수한 적용 전 복원점이 아니다(실측: 자동 백업에 시딩 행 포함) → 원본 적용 전에 앱을 띄우기 전 별도 보존 백업을 먼저 만들어야 한다. ③ 예상 변경 목록: Migration `20261005_00`(테이블 1·인덱스 4·이력 +1), 시딩(규칙 10·권한 1·감사 1, 조건부), 로그인·감사·세션·백업 기록(정상 동작). 이번에 원본 적용·권한 변경은 실행하지 않았다.
- **대기·미검증**: 사용자 보고를 아직 받지 못해 화면 확인은 **미검증**이다("확인 끝, 미저장 없음"을 받기 전에는 검증 앱을 종료하지 않는다 — PID 11560 실행 중, 검증 빌드 경로 확인). 요청한 화면은 핵심 화면(Wizard 목록·#1, 채널 정책 최소 마진, 쿠팡 스토어 연결, 옵션 링크, 구매 연결, 판매 신청 시도, 상품 후보·미디어) 사용자 육안 확인 결과. 앱이 요청 로그를 남기지 않아(`access_log=False`) 제가 화면을 대신 증명할 수 없고, 브라우저 창은 이 앱 주소 접근이 거부되어 재시도하지 않았다. 연결 오류는 자격증명 무력화의 정상 결과로 제품 결함과 구분한다.
- 코드 변경 없음(검증 도구·스크립트는 scratch). 전체 회귀·607건 영향 경로·기존 도구 테스트는 재실행하지 않음. 남은 별도 조건(이번 승인 밖): 배포 산출물·재현 빌드 확인, 원본 적용, UNKNOWN 이력 보호·영구 중복 방지, 정상·반품 거래 정산·최종 손익 검증.

### 9-16. 71차 — 원본(저장소 `homez.db`) 적용 승인안 확정 (실행 없음)
이번 라운드는 원본 DB·자격증명·외부 호출을 실행하지 않았다. 화면 확인은 사용자 보고가 없어 **미검증**이고 검증 앱(PID 11560, 검증 빌드 경로)은 "확인 끝, 미저장 없음"을 받기 전까지 종료하지 않았다.

**선행 사실(직접 확인)**
- **업무 DB 선택은 사용자 진술이 있다.** 사용자 메시지에 "업무 DB는 기존 결정인 `C:\Users\Daum pc\Homez-OS\homez.db`를 계획 대상으로 유지/기준으로 한다. 설치본 DB는 별도 대상"이 두 차례 있다(대화 기록 187726행, 192277행). 다만 "계획 대상·기준"이며 **원본 쓰기 승인은 아니다.** 문서(`HOMEZ_V7_TEST_ISOLATION_20260921.md` §7)의 "확정" 표현은 Claude가 쓴 계획 문서이므로 사용자 진술의 근거로 쓰지 않는다. 설치판 DB(2026-08-31 사용자 확정, 패키징 배포본 기준)는 별개이며 이번 적용에 포함하지 않는다.
- **패키징 빌드로는 저장소 DB를 대상으로 삼을 수 없다.** 패키징 모드의 DB는 `_frozen_data_root()\data\homez.db`(`app/desktop/paths.py:77-116`)이고 저장소 `homez.db`는 `data\` 아래가 아니다. 경로 전환·이관은 미승인이다. 개발 모드 `uvicorn app.main:app`(`scripts/start_homez.ps1`)은 데스크톱 토큰이 없어 Migration 승인 엔드포인트가 403(`desktop_setup.py:83-104`)이고, 시딩 코드는 `app/desktop/main.py:382-474`에만 있다. 개발 모드에서 데스크톱 셸을 띄우면(`python -m app.desktop.main`) 저장소 `homez.db`·`storage/backups`·`storage/logs`를 쓰고 `HOMEZ_DATA_ROOT`는 무시된다(`paths.py:77-145`).
- **백업이 시딩보다 늦다(문제 기록).** 앱 기동 흐름은 `bootstrap_environment`(승인 대기 판정) → 역할·권한 시딩(`seed_environment`) → 채널 정책 카탈로그 시딩(대상 테이블이 없으면 건너뜀) → 서버 기동이며, 앱이 만드는 `homez_pre_bootstrap_migration_*` 백업은 사용자가 승인한 **뒤**에 생성된다. 작업 사본에서 재현·실측했다(자동 백업에 `channel_policy_rules` 10행과 권한 1행 포함). 영향: 앱의 자동 백업은 순수한 적용 전 복원점이 아니다. **정정(71차 보완): "앱 코드가 `journal_mode`를 설정하지 않으므로 WAL이 아니다"라는 추론은 철회한다.** 앱 코드 부재는 대상 DB의 실제 상태를 말해 주지 않는다(다른 도구가 WAL로 바꿀 수 있고 WAL은 DB 헤더에 영속된다). 실측(읽기 전용): 원본·보존 백업 모두 헤더 쓰기/읽기 버전 1/1, `PRAGMA journal_mode=delete`, `locking_mode=normal`, `page_count=952`, `-journal`·`-wal`·`-shm` 없음, 확인 전후 폴더 변화 없음, 원본 SHA 불변. 이 측정은 현재 시점의 사실이며 적용 직전에 다시 측정한다.
- **백그라운드 작업**: 앱 프로세스 안에서 주간 백업 리허설, 리콜 점검(기본 PAUSED, 공급원 제공자 없음), 주문 수집(매분 트리거, 긴급 정지·Migration 제한 모드·함수 모드 AUTOMATIC·중복 실행·자격증명 만료 5중 게이트, 기본 MANUAL)이 돈다. 저장소 DB `function_automation_states`는 0행이라 기본 MANUAL이고, Migration 대기 중에는 제한 모드다.

**원본 적용 승인안(한 표) — 이 표는 승인 요청이며 아직 승인되지 않았다**

| 항목 | 내용 |
|---|---|
| 대상 DB·식별 근거 | `C:\Users\Daum pc\Homez-OS\homez.db`만. 적용 직전 대조: ① SHA-256과 크기(참고 기준선 `34c5002a…b5913d`, 3,899,392 B — 다르면 변경 원인을 먼저 밝히고 진행하지 않음) ② `schema_migrations` 76행에 대기 파일이 정확히 `20261005_00` 한 건 ③ 마법사 2·`channel_policy_rules` 0·권한 38·`function_automation_states` 0·판매 신청 1(NEEDS_REVIEW)·제출 0 ④ `homez.db-journal`·`-wal`·`-shm` 없음 ⑤ `Homez.exe`·`python.exe` 중 이 DB를 쓰는 프로세스 없음 |
| 실행 산출물·방법 | **권고 하나**: 개발 모드 데스크톱 셸 `venv\Scripts\python.exe -m app.desktop.main`(현재 작업 트리, 커밋 `36f970f`+미커밋 변경 — 적용 직전 `tools/rc_baseline_manifest.py`로 코드 집계를 재생성해 참고값 `51d80f88…a208f`와 대조). 근거: 승인 화면(정상 승인 경로, 감사 기록 `MIGRATION_APPLY_APPROVED`·`MIGRATION_APPLIED`)이 데스크톱 모드에서만 열리고, 패키징 빌드는 저장소 DB를 대상으로 할 수 없다. 검증용 가드 빌드(`tools/packaged_guard_hook.py` 포함, 배포 빌드 아님)·정식 배포 빌드와 구분한다. 개발용 가드(`PYTHONPATH`=`tests/support/regression_guard`)를 켜고(`HOMEZ_GUARD_NO_DEFAULTS=1`, 보호: 설치판 `%LOCALAPPDATA%\HOMEZ`·새 보존 백업 폴더, 저장소 DB는 대상이므로 제외) 이 세션은 Credential Manager와 비루프백 접속을 차단한다. 개발 모드 데스크톱 셸 경로는 아직 미검증이며 사본 리허설은 **기존 A~D 승인에 포함되지 않는다**(아래 "71차 보완") |
| Migration 변경 | `20261005_00`(SHA `18ae61f4…8b55`): 테이블 `channel_action_requests` 1·인덱스 4·`schema_migrations` +1행. 기존 행 변경 없음(작업 사본·별도 사본 실측). 파일은 **미추적(untracked)** 상태 |
| 부수 변경(기동 시딩·기록) | 시딩은 **Migration 승인 전 부팅 때 자동**: 권한 `VIEW_SENSITIVE_DATA` 추가(어떤 역할에도 미매핑), 채널 정책 규칙 10행 삽입+감사 `CHANNEL_POLICY_RULE_CATALOG_SEEDED` 1건. 그 외 정상 기록: 감사 `MIGRATION_PENDING_DETECTED`·`MIGRATION_APPLY_APPROVED`·`MIGRATION_APPLIED`, 로그인 세션·리프레시 토큰·토큰 가족, `backup_records`, 앱이 만드는 자동 백업 1개(`storage/backups`). 작업 사본 실측 기준이며 원본에서 무조건 같다고 단정하지 않는다 |
| 기존 값이 다를 때 upsert 범위 | 권한: 없을 때 추가만(`db.add`만 존재). 채널 정책: `(channel, rule_code)` 일치 행이 있고 값이 다르면 **`category_scope_json`·`severity`·`validation_type`·`required_fields_json`·`required_evidence_json`·`official_source_url`·`source_title`·`verified_at`·`profile_version`·`active` 전부를 카탈로그 값으로 덮어씀**. 개별 수정 API가 없다는 사실만으로 충돌 없음을 단정하지 않는다(직접 DB 수정·과거 코드 버전 행 가능) → 적용 직전 `channel_policy_rules`가 0행임을 확인하고, **0이 아니면 진행하지 않고** 행별 차이(컬럼 이름 수준)부터 보고 |
| 적용 전 백업 | **앱 기동 전** 별도 프로세스에서 `sqlite3.connect("file:homez.db?mode=ro", uri=True)`로 읽어 `Connection.backup()`으로 새 파일 생성(실행 중인 DB 파일 단순 복사 금지). **적용 직전에 헤더 바이트·`PRAGMA journal_mode`·사이드카를 다시 측정**하고, ① `-journal`(핫 저널 가능성)이 있거나 이 DB를 쓰는 프로세스가 있으면 중단(파일을 지우거나 손상이라고 단정하지 않고 상태를 그대로 보존해 보고) ② 헤더 2(WAL)이면 `-wal`·`-shm`을 포함해 연결 단위 `backup()`만 사용(파일 단순 복사 금지)하고 중단 후 절차를 다시 정함. 롤백 저널 모드에서 `backup()`은 읽기 연결의 공유 잠금 아래 한 번에 복사하므로 일관된 스냅샷이 되지만 동시 쓰기 연결은 막히거나 실패할 수 있어 쓰기 프로세스가 없음을 먼저 확인한다. 새 폴더(예: `C:\Users\Daum pc\Homez-Backups\v7_pre_apply_<시각>`, 위치는 승인 때 확정)에 읽기 전용 속성으로 보관. 확인: ① 원본 SHA 백업 전후 동일 ② 백업 `integrity_check` ok·FK 위반 0 ③ `tools/db_logical_compare.py 원본 백업` 동일(바이트 해시는 합격 기준 아님) ④ 복원 대상 지정: 복원은 앱 복원 UI(미검증)가 아니라 **앱·스케줄러 종료 후 백업을 별도 파일로 복원해 논리 비교**한 방식(D단계에서 검증)이며, 원본 덮어쓰기 복귀는 별도 판단·승인 |
| 중단 조건 | 선행 대조 중 하나라도 불일치, 백업 검증 실패, 가드 `GUARD_ACTIVE` 미확인, 승인 화면의 대기 파일이 `20261005_00` 한 건이 아니거나 체크섬 `18ae61f4…8b55`가 다름, 예상 밖 시딩·감사·행 변경(목록 외), 앱 오류 코드(E100x) — 원인 확인 전 진행·재시도하지 않음 |
| 적용 후 검증 | 사용자가 화면에서 직접 로그인·재인증(비밀번호 읽기·기록·우회 없음) → 승인. 이후 `integrity_check` ok·FK 0, 이력 77행·신규 체크섬, 테이블 1·인덱스 4, 업무 테이블 값·행 수 백업과 동일(`tools/db_logical_compare.py`), 예상 변경 목록과 전수 대조, 정상 종료 후 1회 재시작(중복 적용 없음·시딩 0건·스냅샷 동일), 자동 백업 경로·무결성 |
| 외부·실거래 비활성 유지 | 모드는 현재값 유지(전역 RECOMMEND_ONLY, 함수 모드 기본 MANUAL, 변경하지 않음). Migration 대기 중 제한 모드로 주문 수집 틱 차단. 개발용 가드로 Credential Manager·비루프백 접속 차단(관측 한계: ctypes 사전 해석·네이티브·비-Python 자식, 보호 폴더 목록 조회). 이 세션에서 연결 확인·외부 조회·등록·판매중지·주문·결제 버튼을 누르지 않음. 앱 종료 후 가드 없이 쓰는 실사용 세션은 별도 승인 |

**재발 방지 조건(고정)**: 원본 적용 절차는 "앱 기동 전 일관된 보존 백업 확보"부터 시작하며, 앱의 자동 백업을 적용 전 복원점으로 취급하지 않는다. 이것은 **수동 절차 보완**이며 제품 결함 수정이 아니다. 제품 수정 후보(미실시): 재현 조건 = 승인 대기 Migration이 있고 시딩이 새 행을 만드는 DB를 앱으로 기동한 뒤 승인(시딩이 백업보다 먼저). 최소 범위 = `app/desktop/main.py::run()`에서 승인 대기 판정 후 `seed_environment` 이전에 `MigrationRunner.create_backup()`을 1회 호출하거나, 승인 대기 중에는 시딩을 보류하는 안(후자는 승인 화면이 권한 시딩에 의존하는지 확인 필요). 수정 시 임시 DB 테스트로 "백업에 시딩 행이 없다"를 검증한다. 필요 승인: 코드 변경.

**UNKNOWN 이력 분리와 실거래 개방은 별개**: 이번 Migration에서 설치판 DB(UNKNOWN 제출 #10, 판매자 상품코드 `HOMEZ-DISHCLOTH-40-V1`)를 제외하는 것과 저장소 DB에서 새 등록·발주를 허용하는 것은 다르다. 기존 증거로 보호가 막는 것: 한 DB 안에서 `(company_id, listing_id)`당 전송 시도 행이 UNIQUE(`uq_marketplace_submissions_live_claim_per_listing`, `external_submission_ref` 있음 또는 `SUBMITTING`/`UNKNOWN`)이고, 사전 점검이 같은 listing의 다른 제출 행이 이미 전송을 시도했는지 본다(`listing_wizard_live_service.py:177-200`). 막지 못하는 것: ① DB를 분리하면 다른 DB의 UNKNOWN을 알지 못한다(저장소 DB 제출 이력 0건, 정합화는 같은 DB 안) ② 같은 상품·SKU의 **다른 listing**에 대한 교차 검사를 찾지 못했다(57차) ③ 쿠팡 쪽 현재 상태 확인·기록은 아직 안 했다(설치판 감사 #42에 `sellerProductId` 형태 응답이 남아 있어 실제 등록됐을 가능성이 있음) ④ "재등록 금지"는 임시 운영 제한이며 영구 중복 방지가 아니다. 그러므로 **Migration 성공만으로 등록·발주 가능이라고 판정하지 않는다**. 실거래 개방 조건: 해당 상품을 등록 대상에서 제외하는 운영 제한 유지, 쿠팡 쪽 상태 확인(별도 읽기 승인), 이력 이관·상태 변경·재등록·모드 전환은 별도 승인(이번에 실행하지 않음). 재현 빌드 미검증의 영향: 이번 적용은 빌드 산출물이 아니라 개발 모드 실행이므로 직접 영향이 없다. 확정 출시 기준(`HOMEZ_USER_OPERATION_SETTINGS.md:299`)에는 재현 빌드 항목이 없으며 새 출시 조건으로 추가하지 않는다(설치판 배포 확인 시점의 별도 확인 사항).

**71차 보완 — 실행 경로 미검증·WAL 정정·복구 절차(실행: 원본 쓰기 0, 앱 실행 0)**
- **리허설 승인 포함 여부(좁은 확인)**: 사용자 원문(대화 기록 223189행): "검증용 빌드 생성 / 업무 DB를 읽어 임시 폴더에 백업 사본 / 격리 작업 사본의 자격증명 참조 무력화·Migration·화면·재시작 검증 / 복원 사본 논리 대조 / 쓰기는 검증용 임시 경로로 한정", 제목 "설치판 사본 검증". 소스 트리를 복사해 개발 모드로 실행하는 방식은 적혀 있지 않다 → **포함되지 않는 것으로 판단하고 실행하지 않았다.** 임시 경로 쓰기 한도는 같지만 실행 방식이 새롭다.
- **리허설 승인 요청(쓰기: 검증용 임시 폴더만, 원본 쓰기 0)**: ① 소스 사본 `<scratch>\rehearse71\src\` = 저장소의 `app\`·`migrations\`·`assets\`·`tools\` 복사(`.env`·`venv`·`.git`·`homez.db`·`storage\` 제외) + **합성 `.env`**(`DATABASE_URL=sqlite:///./homez.db` 한 줄; 실제 `.env`는 상대 경로 DATABASE_URL을 쓰고 키 3개이며 나머지 2개는 재현하지 않는다) ② DB 사본 = 보존 백업에서 복사한 `src\homez.db`(읽기 전용 속성 해제는 사본에서만) ③ 명령(cwd=`src`): `C:\Users\Daum pc\Homez-OS\venv\Scripts\python.exe -m app.desktop.main`. `app`은 venv에 편집 설치되어 있지 않고 저장소 밖 cwd에서는 import되지 않으므로 cwd의 사본 코드만 쓰인다(실측) ④ 환경: `PYTHONPATH`=`tests\support\regression_guard`, `HOMEZ_GUARD_NO_DEFAULTS=1`, **보호 대상 = 실제 저장소 `homez.db`·`storage\`·`.env`, 설치판 `%LOCALAPPDATA%\HOMEZ`, 백업 폴더, 보존 백업**(사본은 운영 경로에 닿지 않아야 하므로 어떤 `ATTEMPT_BLOCKED`도 실패로 본다), `HOMEZ_FORBID_REAL_CREDENTIAL_STORE=1`(**철회: 개발 모드 서버 기동을 막음 — 아래 "71차 리허설 결과" 참조**), `HOMEZ_STORE_CONNECTION_ADAPTER_MODE=FAKE`, `HOMEZ_TEST_MEDIA_ROOT`·`LOCALAPPDATA`=scratch, 죽은 프록시. 운영 자격증명·모드·원본 설정은 건드리지 않는다. ⑤ 관찰: 엔진이 연 DB 경로가 사본인지, 로그·백업·미디어·설정이 사본 안인지, 시딩 로그, 사용자 직접 로그인·재인증→Migration 승인(화면), 재시작 1회(중복 적용·시딩 0건), 가드 로그에 운영 경로 시도 0건, 원본 지문 불변. ⑥ 중단: 엔진 DB 경로가 사본이 아님, 운영 경로 시도, 오류 코드 발생. 필요한 사용자 동작: 로그인과 승인 때 비밀번호 직접 입력. 이 리허설은 **실행 경로 차이(경로 계약·`.env` 분기·시딩·승인 화면)만** 확인하며 같은 소스에서 이미 검증된 Migration·복원 내용은 반복하지 않는다.
- **개발 모드 경로 계약(코드 확인)**: DB·로그·백업·설정·미디어는 `get_repo_root()`(`paths.py` 모듈 위치) 기준이라 소스 사본 안에서는 사본 안으로 닫힌다. `HOMEZ_DATA_ROOT`는 개발 모드에서 경로에는 무시되지만 `desktop_console_session_store`·`store_connection`의 자격증명 이름공간에는 여전히 적용된다 — 이것으로 격리를 주장하지 않는다. `config.py`의 `storage/*`와 `.env`는 **작업 디렉터리 기준 상대 경로**이다. **새로 확인한 위험**: 실제 `.env`의 `DATABASE_URL`은 상대 경로이며 cwd가 저장소 루트일 때만 저장소 `homez.db`를 가리킨다(불리언 계산, 값 미출력). 그래서 실제 적용은 cwd=저장소 루트가 선행조건이고, cwd가 다르면 `app/desktop/main.py:355-380`의 DB 경로 일치 검사가 막거나 다른 위치를 가리킬 수 있다(미실행·미검증). 앱 코드에 하드코딩된 운영 경로는 없다.
- **가드의 보장 범위**: 관측된 차단(이번 세션): 시작 거부 2종, 합성 DB 열기·연결 차단, 원본 경로 열기·`mode=ro` 연결·보호 폴더 내 생성 차단, Credential Manager 심볼 3종, 비루프백 접속 2종, 앱의 `CredReadW` 9건. 이것은 **완전한 격리가 아니다**: 보호 폴더 목록 조회 허용, ctypes 사전 해석·네이티브 확장, 비-Python 자식, SQL `ATTACH`·`VACUUM INTO`는 가드가 보지 못한다(앱 코드에는 `ATTACH`·`VACUUM INTO`가 없고 subprocess는 `restore_helper`뿐이나 이는 부재 확인이지 차단이 아니다). 코드 수준 통제를 겹쳐 쓴다: `HOMEZ_FORBID_REAL_CREDENTIAL_STORE=1`(**철회: 개발 모드 서버 기동을 막음 — 아래 "71차 리허설 결과" 참조**)(실제 자격증명 저장소 생성 거부, 앱은 세션 영속화만 끄고 계속), 개발 모드 판매 연결 어댑터 기본 FAKE(설치판은 PRODUCTION). 통제로 허용 범위를 지킬 수 없으면(가드 미활성, 위 환경 변수 미적용 확인 실패) 실행하지 않는다.
- **복구 절차(정상 적용과 실패 복귀 구분)**: "별도 파일 복원 성공"(D단계)과 "실패한 업무 DB 복귀"는 다르다. 절차: ① 앱·스케줄러 종료, 이 DB를 여는 프로세스·열린 연결 없음 확인(Windows에서 열린 연결이 있으면 교체가 `PermissionError`로 막힘을 사본에서 실측) ② **실패 상태 보존**: `homez.db`와 `-journal`·`-wal`·`-shm`을 전부 별도 폴더로 이동하고 SHA 기록(남은 사이드카를 복귀본 옆에 두면 안 됨 — 사본 실측은 사이드카 없는 경우만, 핫 저널 적용 위험은 SQLite 규칙에 따른 근거이며 실행으로 검증하지 않음) ③ 검증된 보존 백업을 같은 폴더의 임시 파일로 복사 → `integrity_check` ok·`db_logical_compare` 백업과 동일을 통과해야만 교체 ④ `os.replace`로 교체 → 교체 후 무결성·논리 대조(백업과 동일, 이력 76행) ⑤ 재기동 여부 판단: **재기동하면 시딩이 다시 발생한다**(권한 1·규칙 10·감사 1, 작업 사본 첫 기동에서 같은 조건으로 실측) — 복귀본은 시딩 이전 상태이므로 재기동은 "적용 전 상태로의 정상 부팅"이며 Migration 승인 화면이 다시 열린다. 실패한 적용 시도의 감사·로그인 기록은 라이브 DB에서 사라지고 보존 폴더의 실패 상태 파일에만 남는다. 사본 검증 결과(앱 미실행, 파일 수준): 열린 연결 시 교체 차단, 실패 상태 보존본 SHA 동일, 복귀본 무결성 ok·백업과 논리 동일(이력 76행), 복귀 후 남은 사이드카 0, 실패 상태는 백업과 5건 차이(예상 Migration 변경). **실제 원본 교체·복구 쓰기는 실행하지 않았고** 앱 복원 UI·`restore_helper`는 복구 수단으로 쓰지 않는다(미검증). 복구 승인은 원본 적용 승인에 함께 포함한다(실패 시 위 ②~④ 범위: 원본 `homez.db`와 사이드카 이동·교체).
- **승인 범위 정리**: (가) 사본 리허설 승인: scratch 쓰기만, 원본 쓰기 0. (나) 원본 적용 승인: 보존 백업 생성 + 저장소 `homez.db`에 Migration과 시딩 적용(+ 실패 시 위 복구 범위), 로그인·재인증은 사용자 직접. DB 선택은 다시 묻지 않으며(사용자 진술 있음) 쓰기 승인은 별개다. UNKNOWN 이력(설치판 DB)은 포함하지 않고 해당 상품 재등록 금지·실거래 비개방을 유지한다. Migration 통과를 V7 출시·실거래 가능으로 확대하지 않는다. 추가된 한계: 개발 모드 셸의 `.env` 분기·cwd 의존은 리허설 전까지 미검증이다.

**71차 리허설 결과(개발 모드 데스크톱 셸, 소스·DB 사본, 진행 중 — 화면 확인·재시작·종료·최종 대조 대기)**
- **1회차 실패(원인·영향·보완·검증·한계)**: 원인 — `app/main.py` import 시 `app/domains/media_asset/image_search_providers.py`가 모듈 수준에서 `WindowsCredentialStore()`를 만들어, `HOMEZ_FORBID_REAL_CREDENTIAL_STORE=1`이면 `CredentialStoreUnavailableError`가 나 서버가 뜨지 않았다(E1002, 45초 타임아웃). 영향 — 이 스위치는 개발 모드 앱 기동과 양립하지 않는다(테스트용 주입 전제로 설계된 것으로 보이며 제품 기동 경로에서는 쓸 수 없음; 제품 결함으로 단정하지 않고 수정하지 않음). 실패 시점은 Migration 승인 전·시딩 직후였고 실패 상태·로그는 `rehearse71\failed_start1`에 보존했다(승인 전 부팅만으로 권한 +1·규칙 +10·감사 +2, Migration 미적용). 보완 — 통제를 가드로 대체: Credential Manager API는 앱 코드에서 `windows_credential_store.py` 한 모듈에만 있고 가드가 호출 시점의 심볼 조회를 차단한다. 검증 — 같은 환경에서 합성 대상 읽기가 `BLOCKED_BY_GUARD`, 비루프백 접속·실제 저장소 DB 연결 차단. 한계 — 가드는 ctypes 사전 해석·네이티브·비-Python 자식을 못 본다, WebView2 엔진 자체의 통신은 HOMEZ 코드 밖이라 통제하지 못한다, 죽은 프록시와 가드를 완전한 네트워크 격리라고 부르지 않는다. 재발 방지 — 원본 적용 승인안은 이 스위치를 통제로 쓰지 않고 가드 양성 대조(합성 대상)를 실행 전 필수 확인으로 둔다.
- **실행 전 확인(불리언 기록, 비밀값 미출력)**: 실제 import된 `app`·DB·로그·백업·설정·미디어·`storage/*` 12개가 전부 사본 안, 엔진 경로=부트스트랩 경로(합성 `.env`의 상대 `DATABASE_URL` 분기 통과), 상속 `DATABASE_URL`·`HOMEZ_DATA_ROOT` 없음(제거된 상속 키는 `PYTHONDONTWRITEBYTECODE`·`PYTHONIOENCODING`뿐), 어댑터 FAKE, 스케줄러 활성(게이트는 코드 확인: 긴급 정지·Migration 제한 모드·함수 모드 AUTOMATIC), 이미지 생성 대기 작업 0건. venv는 `app`을 편집 설치하지 않아 사본 코드만 import된다.
- **기동·적용 결과(2회차)**: 기동 시 시딩(권한 +1·규칙 +10)과 Migration을 분리 기록 — 승인 전 부팅 감사 `MIGRATION_PENDING_DETECTED`·`CHANNEL_POLICY_RULE_CATALOG_SEEDED`, 사용자 로그인·재인증·승인 후 `MIGRATION_APPLY_APPROVED`·`MIGRATION_APPLIED`. 적용 후 사본: 이력 77행(신규 `20261005_00`, 체크섬 `18ae61f4…8b55`, 기존 76행 불변), 테이블 1·인덱스 4, 무결성 ok·FK 0, 논리 비교 차이 11개 테이블 전부 설명됨(신규 테이블·인덱스·이력, 시딩 권한 1·규칙 10, 감사 4, 세션·리프레시 토큰·토큰 가족 각 1, 백업 기록 1). 스케줄러 상태 기록 등 목록 밖 테이블 변화 없음. 이번에는 `credential_reference`를 무력화하지 않았고(가드로 통제) 연결 테이블 변경 0. 업무 행 상태 동일. 앱 자동 백업은 무결성 ok·이력 76행이나 **시딩 행(규칙 10)이 이미 포함** — 개발 모드에서도 "백업이 시딩보다 늦다" 문제 재현.
- **관측한 쓰기·차단**: 가드 차단은 앱 세션 저장소의 `CredReadW` 읽기 2건뿐(운영 경로 시도 0). 리허설용 `LOCALAPPDATA`·미디어 폴더 쓰기 0건, 설치판 폴더(`%LOCALAPPDATA%\HOMEZ`)는 최신 쓰기 2026-09-07 그대로. 사본 소스 폴더 안: `homez.db`, `storage\logs`·`storage\backups`(자동 백업)·`storage\config`, **cwd 기준 상대 경로 `logs\*.log`**. **원본 적용 시 예상 쓰기 목록에 추가**: 저장소 `homez.db`, `storage\logs`, `storage\backups`(자동 백업), `storage\config`, 저장소 기존 `logs\*.log`(상대 경로, cwd=저장소 루트일 때).
- **리허설 종료 결과(통과 — 실행 경로 한정)**: 사용자 "확인 끝, 미저장 없음" 수신 후 검증 앱만 정상 종료(`CloseMainWindow`, 강제 종료 없음; 종료 이벤트 16:00:41 → 프로세스 종료 16:00:45). 종료 직후 사본 스냅샷은 적용 직후와 동일. 같은 격리 설정으로 1회 재시작: 대기 Migration 없음, 이력 77행 유지(중복 적용·추가 자동 백업 없음, 백업 파일 1개 그대로), 시딩 권한 +0·역할 +0, 규칙 10건은 재확인만(`CHANNEL_POLICY_RULE_CATALOG_SEEDED` 재기록 없음), 재시작 후 스냅샷은 종료 직전과 동일, `/console` 200·`/health` 정상, 가드는 앱 세션 저장소 `CredReadW` 2건만 차단. 재시작 인스턴스도 정상 종료. 최종: 사본 무결성 ok·FK 0, 이력 77, 사이드카 없음. 스케줄러는 켠 채 약 21분 동작(매분 주문 수집 틱)했으나 목록 밖 테이블 변화는 없었다. **원본 불변**: 저장소 `homez.db` SHA `34c5002a…`, 설치판 DB `c28eaf57…`, 백업 폴더 목록, Credential 이름 해시 모두 리허설 전과 동일, 저장소 `storage\`·`logs\`·`.env`·`%LOCALAPPDATA%\HOMEZ`·`Homez-Backups`에서 리허설 이후 수정된 파일 0건. 가드 차단 중 `CredReadW` 외 3건은 실행 전 확인 프로브(`<string>` 프레임)의 의도된 양성 대조이며 앱 코드의 운영 경로 시도는 0건.
- **화면 확인(한계)**: 사용자가 "확인 끝, 미저장 없음"으로 응답했으나 7개 화면의 개별 값·오류 문구는 보고되지 않았다. 이 응답을 값별 확인 결과로 확대하지 않는다(보고된 이상 없음 = 미수신). 사본에는 이미지 파일·실제 자격증명이 없어 이미지·연결 오류는 리허설 구성의 결과로 구분한다.
- **개발 모드 경로로 검증된 것 / 안 된 것**: 검증됨 — 사본 소스·DB 경로 계약(`get_repo_root()` 기준), 합성 `.env` 상대 `DATABASE_URL` 분기, 부팅 시딩과 Migration 분리, 데스크톱 승인 화면(사용자 직접 로그인·재인증), 재시작 멱등, 가드·FAKE 어댑터 구성. **미검증** — 실제 `.env`(키 3개, 나머지 2개는 재현하지 않음)의 영향, 실제 `credential_reference`가 있는 DB에서 가드 없이 동작하는 경우(의도적으로 하지 않음), 앱 복원 UI·`restore_helper`, 핫 저널 상황, cwd가 저장소 루트가 아닌 경우의 실제 `.env` 분기.

**원본(저장소 `homez.db`) 적용 승인안 — 71차 리허설 통과 후 확정안(제시용, 미실행·미승인)**
이 확정안은 앞의 §9-16 표와 "71차 보완"을 대체한다(철회된 `HOMEZ_FORBID_REAL_CREDENTIAL_STORE=1` 제거, 상대 경로 로그 쓰기 추가). DB 선택(저장소 `homez.db`)은 다시 묻지 않으며 쓰기 승인은 별개다.

| 항목 | 내용 |
|---|---|
| 대상 DB·적용 직전 확인 | `C:\Users\Daum pc\Homez-OS\homez.db`만. 직전 대조(참고 기준선, 다르면 원인 설명 전 진행 금지): SHA `34c5002a…b5913d`·3,899,392 B, 이력 76행·대기 파일 정확히 `20261005_00` 한 건, 마법사 2·`channel_policy_rules` 0·권한 38·`function_automation_states` 0·판매 신청 1(NEEDS_REVIEW)·제출 0, 헤더 1/1·`journal_mode=delete`·`-journal`/`-wal`/`-shm` 없음, 이 DB를 쓰는 프로세스 없음 |
| 실행 산출물·방법 | 개발 모드 데스크톱 셸 `C:\Users\Daum pc\Homez-OS\venv\Scripts\python.exe -m app.desktop.main`, **cwd=저장소 루트**(실제 `.env`의 상대 `DATABASE_URL` 때문에 필수). 소스: 커밋 `36f970f`+미커밋 변경(적용 직전 `tools/rc_baseline_manifest.py`로 코드 집계 재생성해 `51d80f88…a208f`와 대조), Migration `20261005_00` SHA `18ae61f4…8b55`(미추적 파일). 검증용 가드 빌드·정식 배포 빌드와 구분(이번 적용에 빌드 산출물은 쓰지 않으므로 재현 빌드 미검증의 직접 영향 없음). 환경: 리허설과 같은 허용 목록 방식(상속 `DATABASE_URL`·`HOMEZ_*`·`PYTHON*`·프록시 제거), 개발용 가드(`PYTHONPATH`=`tests\support\regression_guard`, `HOMEZ_GUARD_NO_DEFAULTS=1`, 보호: 설치판 `%LOCALAPPDATA%\HOMEZ`·`Homez-Backups`·새 보존 백업 폴더 — 저장소 DB·`storage`·`logs`·`.env`는 대상이므로 제외), `HOMEZ_STORE_CONNECTION_ADAPTER_MODE=FAKE`, 죽은 프록시. 실행 전 확인(리허설과 같은 불리언 확인 + 합성 Credential 읽기·비루프백·설치판 경로 차단 양성 대조)이 하나라도 실패하면 기동하지 않는다 |
| Migration 변경 | 테이블 `channel_action_requests` 1, 인덱스 4(`ix_channel_action_requests_company_id`·`_id`·`_status`·`_store_connection_id`), `schema_migrations` +1행. 기존 행 변경 없음(사본 2회 실측) |
| 기동 시딩·감사·세션 등 부수 변경 | **Migration 승인 전 부팅 때**: 권한 `VIEW_SENSITIVE_DATA` +1(어떤 역할에도 미매핑), 채널 정책 규칙 +10, 감사 `MIGRATION_PENDING_DETECTED`·`CHANNEL_POLICY_RULE_CATALOG_SEEDED`. **승인 후**: 감사 `MIGRATION_APPLY_APPROVED`·`MIGRATION_APPLIED`, 로그인 `auth_sessions`·`refresh_tokens`·`refresh_token_families` 각 +1, `backup_records` +1, 앱 자동 백업 파일 1개(`storage\backups`, **시딩 후·적용 전 상태이므로 순수 복원점 아님**). 재시작 시 추가 변경 0. 사본 실측 기준이며 원본에서 무조건 같다고 단정하지 않는다 |
| upsert가 덮어쓰는 범위 | 권한: 없을 때 추가만. 채널 정책 규칙: `(channel, rule_code)` 일치 행이 있고 값이 다르면 `category_scope_json`·`severity`·`validation_type`·`required_fields_json`·`required_evidence_json`·`official_source_url`·`source_title`·`verified_at`·`profile_version`·`active` 전부 카탈로그 값으로 덮어씀. 현재 0행이라 덮어쓸 행이 없음 — **직전 대조에서 0이 아니면 진행하지 않고 행별 차이(컬럼 이름 수준)부터 보고**. 개별 수정 API가 없다는 사실로 충돌 없음을 단정하지 않는다 |
| 쓰기 위치(예상) | 저장소 `homez.db`, `storage\backups`(자동 백업), `storage\logs`, `storage\config`, 기존 `logs\*.log`(cwd 기준 상대 경로), 보존 백업 폴더. 설치판 `%LOCALAPPDATA%\HOMEZ`·`Homez-Backups` 기존 파일 변경 0이 기대값(리허설 실측 0) |
| 적용 전 백업 | **앱 기동 전** 별도 프로세스에서 `mode=ro` 연결 + `Connection.backup()`으로 새 폴더(예: `C:\Users\Daum pc\Homez-Backups\v7_pre_apply_<시각>\homez_pre_apply.db`, 위치는 승인 때 확정) 생성, 읽기 전용 속성. 검증: 원본 SHA 전후 동일, 백업 `integrity_check` ok·FK 0, `tools/db_logical_compare.py 원본 백업` 동일(바이트 해시는 합격 기준 아님), 헤더·저널 재측정. 파일 단순 복사 금지 |
| 중단 조건 | 직전 대조 불일치, 백업 검증 실패, 실행 전 확인·가드 양성 대조 실패, 승인 화면의 대기 파일이 `20261005_00` 한 건 아니거나 체크섬 불일치, 목록 밖 시딩·감사·행 변경, 오류 코드(E100x) — 원인 확인 전 진행·재시도·강제 교체·사이드카 삭제 금지, 실패 상태·로그 보존 |
| 적용 후 검증 | 사용자 직접 로그인·재인증(비밀번호 읽기·기록·우회 없음) → 승인 → 무결성·FK, 이력 77·체크섬, 테이블 1·인덱스 4, 업무 테이블 백업과 동일(`db_logical_compare`), 예상 변경 목록 전수 대조, 정상 종료 → 재시작 1회(중복 적용·시딩 0·스냅샷 동일), 자동 백업 경로·무결성, 원본 외 운영 경로(설치판·기존 백업) 불변 |
| 실패 시 복구 | 앱 정상 종료와 알려진 DB 사용 프로세스 확인 우선(열린 연결 탐지를 보장하지 않음 — 사본 시험의 `PermissionError`는 그 조건의 결과일 뿐). 상태가 불분명하면 강제 교체·사이드카 삭제 금지. 실패 상태(`homez.db`+`-journal`/`-wal`/`-shm`)와 로그를 별도 폴더에 이동 보존 → 검증된 보존 백업을 임시 파일로 복사해 무결성·논리 대조 통과 후 교체 → 교체 후 재대조 → 재기동 판단(**재기동하면 시딩 재발생**, Migration 승인 화면 재등장). 앱 복원 UI는 미검증이라 쓰지 않음. 이번에 실제 실패가 없었으므로 복구 시험은 반복하지 않음 |
| 외부·실거래 비활성 | 모드·자격증명·설정 불변(전역 RECOMMEND_ONLY, 함수 모드 기본 MANUAL, 변경 금지). Migration 대기 중 제한 모드로 주문 수집 차단, 적용 후에는 게이트(긴급 정지·함수 모드 AUTOMATIC·중복 실행·자격증명 만료)와 가드·FAKE 어댑터. 이 세션에서 연결 확인·외부 조회·등록·판매중지·주문·결제 버튼 사용 금지. 한계: 가드는 ctypes 사전 해석·네이티브·비-Python 자식·보호 폴더 목록 조회를 못 막고 WebView2 엔진 통신은 HOMEZ 코드 밖 — 죽은 프록시와 가드를 완전한 네트워크 격리라고 부르지 않음 |
| 적용 범위 밖(유지) | UNKNOWN 이력(설치판 DB 제출 #10)은 이관·변경 없음, 해당 상품 재등록 금지·실거래 비개방 유지. Migration 통과를 V7 출시·실거래 가능으로 확대하지 않음. 커밋·push, 이력 이관, 모드 전환, 외부 호출, 실제 자격증명 사용은 별도 승인 |

**원본 적용 실행 절차(확정, 미실행 — 승인 문구 수신 후에만 시작)**
"원본 적용 승인안 확정 진행" 지시는 승인안의 확정으로 해석하며 **원본 쓰기 승인으로 해석하지 않는다.** 실행에는 아래 "필요한 승인 문구"가 별도로 필요하다.
- **직전 상태(읽기 전용 재확인, 2026-10-06 16시경)**: 원본 SHA `34c5002a…b5913d`·3,899,392 B, 헤더 1/1·`journal_mode=delete`·사이드카 없음, 무결성 ok, 이력 76행·대기 파일 `20261005_00` 한 건뿐·디렉터리에 없는 적용 기록 0, 마법사 2·`channel_policy_rules` 0·권한 38·`function_automation_states` 0·판매 신청 1·제출 0·옵션 링크 0·감사 32, 확인 전후 폴더 변화 없음, 실행 중인 python/Homez 프로세스 0. 코드 집계(문서 제외) `51d80f88…a208f` 기준선과 동일(제품 33·프런트 3·Migration 1·테스트 25 분류 집계도 동일). 이 값들은 참고 기준선이며 **적용 직전에 다시 대조**한다.
- **절차**: ① 직전 대조(`orig_state.py`: SHA·헤더·저널·사이드카·이력·대기 파일·핵심 행 수·프로세스; 불일치 시 중지) ② **보존 백업(앱 기동 전)**: `real_apply_backup.py <원본> <새 폴더> --expect-sha <직전 SHA>` — 백업 폴더 `C:\Users\Daum pc\Homez-Backups\v7_pre_apply_<YYYYMMDD_HHMMSS>\homez_pre_apply.db`(폴더는 새로 만들며 이미 있으면 거부), 거부 조건: 사이드카·비롤백 헤더·폴더 비어 있지 않음·SHA 불일치(종료코드 2), 검증 실패(종료코드 3: 무결성·FK·`db_logical_compare`·원본 SHA 전후) ③ 앱 기동 전 확인: 리허설 `preflight.py`와 같은 불리언 확인을 cwd=저장소 루트·실제 보호 목록으로 수행(엔진 경로=저장소 `homez.db`, 어댑터 FAKE, 합성 Credential 읽기·비루프백·설치판 경로 차단 양성 대조) — 하나라도 실패하면 기동하지 않음 ④ 기동(cwd=저장소 루트, 리허설과 같은 허용 목록 환경; 차이는 cwd·보호 목록(저장소 DB·`storage`·`logs`·`.env` 제외, 설치판·`Homez-Backups`·새 보존 백업 폴더 포함)·`LOCALAPPDATA`와 `HOMEZ_TEST_MEDIA_ROOT`는 임시 폴더 유지) ⑤ 사용자 직접 로그인·재인증 → 승인 화면 대상이 `20261005_00` 한 건·체크섬 `18ae61f4…8b55`인지 확인 후 승인 ⑥ 적용 후 대조(무결성·FK·이력 77·테이블 1·인덱스 4·업무 테이블 백업과 동일·예상 변경 목록 전수·자동 백업) ⑦ 정상 종료 → 재시작 1회(중복 적용·시딩 0·스냅샷 동일) → 정상 종료 ⑧ 원본 외 운영 경로(설치판·기존 백업·Credential 이름 해시) 불변 대조.
- **도구 상태**: 보존 백업 도구는 **사본에서만 시험**했다 — 정상(무결성 ok·논리 동일·원본 SHA 불변·읽기 전용 속성), 사이드카 존재·폴더 비어 있지 않음·기대 SHA 불일치는 종료코드 2로 거부하고 거부 시 파일을 만들지 않음. 위치: scratch(`C:\Users\DAUMPC~1\AppData\Local\Temp\claude\C--Users-Daum-pc-Homez-OS\def2c399-1c65-4bed-929f-35007cd48254\scratchpad\verify70\real_apply_backup.py`, `orig_state.py`, `preflight.py`, `rehearse_env.py`) — **저장소에 들어 있지 않고 세션 임시 폴더에 있다**(저장소 `tools/`에 두려면 신규 파일 생성 승인 필요). 원본에는 어떤 도구도 실행하지 않았다(읽기 전용 상태 확인 제외). 앱 기동용 스크립트는 리허설 `run_rehearsal.py`의 세 가지 변경(cwd, 보호 목록, 실제 경로)으로 만들며 **아직 작성·시험하지 않았다** — 승인 후 첫 단계에서 기동 없이 확인 모드로 먼저 점검한다.
- **필요한 승인 문구(예)**: "저장소 `homez.db`에 대해 ① 앱 기동 전 읽기 전용 보존 백업을 `C:\Users\Daum pc\Homez-Backups\v7_pre_apply_<시각>\`에 생성하고 ② 확정안 조건으로 개발 모드 데스크톱 셸을 기동해 Migration `20261005_00`과 기동 시딩을 적용하며 ③ 실패 시 확정안의 복구 절차(실패 상태 보존 후 보존 백업으로 교체)를 수행하는 것을 승인한다. 로그인·재인증은 내가 직접 한다." 이 문구가 없으면 어떤 단계도 시작하지 않는다. 포함되지 않는 것: 커밋·push, 설치판 DB, 이력 이관, 모드 전환, 외부 호출·실제 자격증명 사용, 등록·발주·결제·환불, 자동화 모드 변경.

**원본 적용 실행 결과 (2026-10-06 16:14~16:21, 사용자 승인 문구 수신 후 실행 — 저장소 `homez.db`)**
- **승인과 범위**: 사용자 "원본 적용승인한다. 로그인 재인증은 내가 직접한다."(확정안의 ①보존 백업 ②Migration·시딩 적용 ③실패 시 복구 범위). 로그인·재인증·Migration 승인은 사용자가 직접 입력했다(비밀번호 읽기·기록·우회 없음). 승인 화면 전 확인 항목(대기 파일 1건·테이블 1·인덱스 4)을 안내했고 사용자가 승인 완료를 알렸다.
- **직전 대조(전부 기준선 일치)**: SHA `34c5002a…b5913d`·3,899,392 B, 헤더 1/1·`journal_mode=delete`·사이드카 없음, 무결성 ok, 이력 76·대기 파일 `20261005_00` 한 건, 규칙 0·권한 38·자동화 상태 0·판매 신청 1·제출 0·옵션 링크 0·감사 32, 프로세스 0, Migration 파일 SHA `18ae61f4…8b55`, HEAD `36f970f`.
- **보존 백업(앱 기동 전)**: `C:\Users\Daum pc\Homez-Backups\v7_pre_apply_20261006_161424\homez_pre_apply.db`(읽기 전용) — `mode=ro`+`backup()`, 무결성 ok·FK 0·논리 동일·원본 SHA 전후 동일, 백업 SHA16 `f342a854…`(앞선 보존 백업과 바이트 동일). **이것이 순수한 적용 전 복원점**이다. 앱의 자동 백업 `storage\backups\homez_pre_bootstrap_migration_20261006_161743.db`는 무결성 ok·이력 76이나 시딩 행(규칙 10) 포함.
- **기동 전 확인(실제 `.env` 포함, 앱 본체 기동 없이)**: 21개 항목 PASS — 엔진 경로=부트스트랩 경로=저장소 `homez.db`(실제 `.env`의 상대 `DATABASE_URL` 분기 통과), cwd=저장소 루트, 로그·백업·설정은 저장소 `storage\`, 어댑터 FAKE, 상속 `DATABASE_URL`·`HOMEZ_DATA_ROOT`·FORBID 스위치 없음, 가드 활성, 설치판 DB·보존 백업 열기·합성 Credential 읽기·비루프백 접속 차단. 원본 불변.
- **적용 결과**: 승인 전 부팅 시딩(권한 38→39, 규칙 0→10, 감사 +2), 승인 후 `20261005_00` 적용 — 이력 77행·체크섬 `18ae61f4…8b55`·`APPLIED`, 테이블 `channel_action_requests`(0행)+인덱스 4, 무결성 ok·FK 0, 대기 Migration 없음. 보존 백업 대비 **차이 11개 테이블 전부 예상 목록**(신규 테이블·인덱스·이력 +1, 권한 +1, 규칙 +10, 감사 +4, `auth_sessions`·`refresh_tokens`·`refresh_token_families` 각 +1, `backup_records` +1), 스키마 다이제스트 `d0d91e29…`·총 행 512가 사본 실행과 동일. 업무 행(마법사 2·최소 마진 0.18·스토어 연결 1·구매 연결 2·판매 신청 1 NEEDS_REVIEW·제출 0·옵션 링크 0)은 보존 백업과 동일, 보존 백업에 있던 행 소실 0. 새 원본 기준선: SHA `e2927521d0b43ae60daebd9d0c77d6bd914b064857c1962218c145af7cab60b0`·3,899,392 B.
- **종료·재시작**: 정상 종료(16:18:31) → 종료 직후 스냅샷은 적용 직후와 동일 → 같은 설정으로 1회 재시작(대기 Migration 없음, 시딩 권한 +0·역할 +0, 이력 77 유지, 추가 자동 백업 없음, `/console` 200, 스냅샷 종료 직전과 동일) → 정상 종료(16:20:52), 남은 프로세스 0.
- **관측한 쓰기·차단**: 가드 차단은 앱 세션 저장소의 `CredReadW` 읽기뿐(실행당 3건, 쓰기·삭제·운영 경로 시도 0). 설치판 `%LOCALAPPDATA%\HOMEZ` 보존 백업 이후 수정 파일 0, 설치판 DB `c28eaf57…` 불변, 기존 `Homez-Backups` 항목 수정 0(새 폴더 1개만 추가), Credential 이름 해시 15개 불변(값 불변의 증명 아님), 실제 `.env` 미수정, `git status` 항목 수 76 그대로(`homez.db`·`storage`·`logs`는 추적 대상 아님).
- **예상 밖 변화**: 없음. (1회차 리허설의 FORBID 스위치 문제는 사본에서 이미 처리.)
- **이 적용으로 검증되지 않은 것**: 원본 화면의 값별 확인(Wizard·마진·연결 등)은 이번 세션에서 하지 않았다(Migration 승인 화면만 사용자가 확인). 앱 복원 UI·`restore_helper`, 핫 저널, Credential 3개 증가 원인(미확정), 설치판 DB의 시딩 동작은 그대로 미검증. 복구 절차는 실제 실패가 없어 실행하지 않았다.
- **이 적용이 의미하지 않는 것**: Migration 통과는 V7 출시·실거래 가능이 아니다. UNKNOWN 이력(설치판 DB 제출 #10)은 이관·변경하지 않았고 해당 상품 재등록 금지·실거래 비개방을 유지한다. 적용된 코드와 `20261005_00` 파일은 **아직 미커밋·미추적**이다(커밋·push는 별도 승인). 남은 출시 기준: 실제 상품 2건 전 과정, 긴급 중지 실사용 검증, 정상·반품 거래 정산·최종 손익, 배포 산출물·재현 빌드 확인.

**원본 화면 값별 확인 결과 (2026-10-06 16:26~16:44, 원본 DB 연결 세션, 읽기 전용)**
- **방법·통제**: 같은 가드·FAKE 어댑터 환경으로 원본 DB 연결 앱을 기동하고 사용자가 로그인(데스크톱 창 1회, 브라우저 창 1회)했다. 브라우저 패널이 "숨김" 상태라 좌표 클릭이 전달되지 않아 **메뉴 이동은 사이드 메뉴 버튼의 `click()`으로 대신**했고(이동 외 어떤 입력·삭제·저장·연결 확인·외부 조회 버튼도 누르지 않음), 값은 표시 텍스트와 **화면이 쓰는 조회 API의 GET**(DB만 읽는 엔드포인트임을 코드로 확인한 것만)로 읽었다. `/channel-connections/{id}/products·orders·point` 등 외부 쇼핑몰 조회 GET은 호출하지 않았다. 종료 후 정상 종료(16:44:42), 남은 프로세스 0.
- **확인됨(직접 읽음)**: ① Wizard 목록 — #1 "수정 필요·7단계 사전검사"(2026-10-04 18:01), #2 "작성 중·1단계 상품 소스"(10-04 16:52) ② Wizard #1(GET) — `NEEDS_CORRECTION`·`PRECHECK`·버전 45, `brandState=OFFICIAL_BRAND`·`brandId=KR-120395`·공식 브랜드명 `레이펄스`·draft 브랜드 4자, 필수값 30개, 승인·승인 지문 없음, 경제성 결과 마진율 25.00%(3,224.60원), 검증 이슈 2건(`CHANNEL_POLICY_DATA_REQUIRED`·`ECONOMICS_PROVISIONAL`) ③ 채널 정책 최소 마진 `min_target_margin_rate=0.1800`·버전 1(GET) ④ 쿠팡 판매채널 연동 화면 — `홈즈`·"✓ 연결됨"·마지막 확인 2026-09-14 19:53·키 만료 없음·오류 없음 ⑤ 옵션 링크 — 구매 작업 1의 옵션 링크 조회 `state=NO_ORDER_ITEM`·`link_id` 없음(저장된 연결 0) ⑥ 상품 후보 3건(레이펄스 워터리스 바디워시 200ml 본품, `[테스트-삭제예정]` 2건, 모두 검토 완료) ⑦ 미디어 에셋 2건(id 1·2, ACTIVE, 둘 다 후보 3 소유, Wizard #1 선택은 1) ⑧ 상단 상태 — 모드 `RECOMMEND_ONLY`·`EStop 비활성`·스키마 적용됨, 대시보드 승인 대기 0·V7 운영 현황 전부 0, 구매 실행 연결 화면 — 허용 구매처 없음(전부 차단)·구매 실행 기록 없음, 매입·발주 관리 — 구매 작업 1건(수량 1, `SEARCH_REQUIRED` "구매 필요", 수동 생성), 발주 승인 없음·제출 시도 0.
- **화면·API로 확인하지 못함**: ⑨ **매입 채널 연결 2건 목록** — `GET /purchase-tasks/channel-connections`가 HTTP 500 ⑩ **판매 신청 시도 1건(`NEEDS_REVIEW`)** — 구매 작업 1에 연결 배정·발주 승인이 없어 화면·API 경로에 나타나지 않음. 두 항목은 DB 읽기 전용 대조(보존 백업과 동일, 변경 없음)로만 같음을 확인했을 뿐 **화면 직접 확인으로 기록하지 않는다**.
- **의도적 자격증명 차단의 결과 vs 제품 결함 구분**: 두 건의 500은 모두 가드의 `PermissionError`(CredReadW 차단) 스택이다 — (a) R2 호스팅 상태 `get_r2_hosting_status → load_r2_credentials`(고정 대상명 읽기), (b) 구매 연결 목록 `list_connections → _apply_status_recompute → check_connection → _read_credential`. 의도한 비활성의 결과이며 제품 결함으로 단정하지 않는다. **다만 관찰한 위험(미확정·미수정)**: (b)는 **조회(GET)가 연결 상태를 재계산하고 변경을 저장할 수 있는 경로**(`_apply_status_recompute`, `touch_last_checked=False`)다. 가드가 읽기를 막아 예외로 끝났고 `purchase_channel_connections`는 보존 백업과 동일(실측)이라 변경은 없었으나, 가드 없이 실제 자격증명이 읽히거나 "없음"으로 판정되면 상태가 바뀌어 원본에 기록될 수 있다. 원인=목록 조회에 상태 재계산이 포함된 설계, 영향=원본 연결 상태의 의도하지 않은 변경 가능성(가드 없는 실사용 때), 보완=이번 세션은 가드 유지·해당 화면 미사용, 검증=변경 없음 실측, 한계=가드 없는 동작은 실행하지 않아 미확인. 가드 없는 실사용 전에 이 경로의 쓰기 조건을 별도로 확인해야 한다(출시 조건 아님, 실거래 개방 전 확인 후보).
- **원본에 남은 변화(이 세션)**: 로그인 기록뿐 — 누적 `auth_sessions` 58→62·`refresh_tokens` 86→90·`refresh_token_families` 42→46(이번 작업 로그인 4회: 적용 승인·재시작·데스크톱 창·브라우저 창), 감사 36 그대로, Wizard #1 버전 45 그대로(조회만), 구매 연결·판매 신청·스토어 연결·업무 행 변화 0. 가드 차단은 `CredReadW` 5건뿐(다른 종류 0). 설치판 DB `c28eaf57…`·기존 `Homez-Backups` 항목·Credential 이름 해시 15개 불변, 설치판 폴더에 수정 파일 0. **새 원본 기준선: SHA `40d5c5b61a5a8d9881083ba1376e70d7f8b7f11824988bf3b972d5be67eb8ce6`**(이전 `e2927521…`은 로그인 기록 추가 전).
- **여전히 미검증**: 매입 채널 연결·판매 신청의 화면 직접 확인, 최소 마진·옵션 링크의 전용 설정 화면(조회 API로 대체), Wizard #1 편집 화면 자체(열면 사전검사 등 부수 동작 우려로 열지 않음), 앱 복원 UI, 가드 없는 실사용 동작.

**코드·마이그레이션 커밋 (2026-10-06, 사용자 승인 "코드와 마이그레이션 커밋 승인한다")**
- 커밋 `20303c0`(main, push 안 함): 65개 파일 — `app/` 36(제품 33·웹 화면 3), `migrations/` 1(`20261005_00`), `tests/` 25, `tools/` 3. 커밋 직전 코드 집계(문서 제외) `51d80f88…a208f`로 검증된 기준선과 동일함을 확인했고, 비밀값 패턴 검사는 테스트의 합성 값(`AKIAFAKE…TEST`, 유출 방지 단언용)만 검출. 경로를 명시해 스테이징(`git add -A` 미사용), 브랜치는 만들지 않음(기존 이력과 같이 main 직접).
- **커밋에 포함하지 않은 것**: 문서 변경 2건(`HOMEZ_PROJECT_STATE.md`·`HOMEZ_USER_OPERATION_SETTINGS.md`)과 신규 문서 5건(이 문서 포함)·`docs/research/` 3건·`.claude/skills/homez-action-briefing`(다른 작업자 파일) — 별도 승인 대상. 원격 반영(push)도 승인되지 않아 로컬 `main`이 `origin/main`보다 1 커밋 앞서 있다.
- 커밋은 V7 출시나 실거래 가능을 뜻하지 않는다. 원본 DB는 커밋 전후 동일(SHA `40d5c5b6…`, 로그인 기록 추가 후 값).

**문서 커밋 (2026-10-06, 사용자 승인 "문서 커밋 승인한다")**
- 이 문서와 함께 7개 문서를 커밋한다(커밋 해시는 자기 참조가 되므로 `git log`로 확인): `HOMEZ_PROJECT_STATE.md`·`HOMEZ_USER_OPERATION_SETTINGS.md`(수정), `HOMEZ_V7_RELEASE_CANDIDATE_PREP_20261005.md`(이 문서)·`HOMEZ_V7_INSTALL_COPY_VERIFICATION_APPROVAL_20261006.md`·`HOMEZ_V7_NORMAL_REGISTRATION_FLOW_20261005.md`·`HOMEZ_V7_RC_BASELINE_MANIFEST_20261005.txt`·`HOMEZ_V7_RC_BASELINE_MANIFEST_20261006.txt`(신규). 비밀값 패턴 검사 결과 없음.
- **제외**: `docs/research/` 3건과 `.claude/skills/homez-action-briefing`(다른 작업자 파일, 승인 범위 밖). **push는 승인되지 않아** 로컬 `main`은 `origin/main`보다 앞서 있다. 위 "코드·마이그레이션 커밋" 항목의 "문서 미포함"은 코드 커밋(`20303c0`) 시점의 사실이다.
- 이 커밋도 V7 출시나 실거래 가능을 뜻하지 않는다.
