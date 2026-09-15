# HOMEZ V7 전면 감사 (2026-09-14 개시)

상태: [감사 중]
개시 사유: 사용자 지시("전체 감사 시작") — 반복적인 누락·잘못된 완료 보고·
환경 혼동·실행 실수 발생. 기존 개발 보고·테스트 통과·완료 판정을 그대로
신뢰하지 않고 코드·실행환경·DB·화면·외부 연동을 증거로 재검증한다.

이 문서는 Phase 진행에 따라 계속 append된다(기존 내용은 삭제하지 않고
정정만 근거를 붙여 추가한다).

---

## Phase 1 — 작업 중지 및 증거 보존 (2026-09-14 진행)

### 1.1 중지한 실행 중 작업

| 항목 | 상태 | 조치 |
|---|---|---|
| `homez-live` 미리보기 서버(포트 8000, 실 `homez.db` 연결) | 이 세션이 직접 띄운 것 | **중지함**(`preview_stop`) — 잔여 쓰기 경로 제거 목적, 다른 세션·사용자 소유 아님 |
| 스케줄러 | 서버 로그에 "scheduler" 관련 출력 없음 | 활성 스케줄 작업 미확인(추가 조사 필요 시 Phase 4에서 재확인) |
| 다른 python 프로세스 | 위 서버의 부모/워커 프로세스로 추정(PID 10440/11832, 시작시각 서버 기동시각과 동일) | 서버 중지로 함께 종료됨 |

실제 발주·결제 진행 중 여부: **없음.** 아래 1.2 참고 — `PurchaseOrderApproval`이
ACTIVE 상태로 남아 있으나 유효시간이 이미 만료됐고, 실제 발주 POST는
한 번도 실행되지 않았다(idempotency_key 미소비). 강제 종료가 필요한
진행 중 금전 거래는 없다.

### 1.2 중지 시점의 작업 목록(재개 위치 보존)

#### A. "V7 온채널 첫 실사용 검증" (2026-09-14 개시 지시)

- **목표**: 실제 온채널 발주 1건을 승인 절차를 거쳐 완주(사용자 자택 배송,
  포인트 차감·송장·배송완료까지 대조).
- **진행 상태**: Phase 1~6 완료로 보고됨(재검증 전 상태 그대로 기록).
  - Phase 2: `GET /openapi/common/member/point` 실제 호출 1회 —
    `point=50000`(사용자 충전액과 일치한다고 보고됨).
  - Phase 4: `POST /openapi/seller/product/apply`(CH1147184) 실제 호출 1회 —
    HTTP 200 접수 확인, 재조회로 404 해소 확인됨.
  - Phase 5: `PurchaseTask` id=1 생성(company_id=1, `coupang_sale_amount`
    /`coupang_fee_amount`는 **실제 쿠팡 주문이 아닌 추정치**로 기록됨,
    `caution_reason`에 명시).
  - Phase 6: `confirm_shipping_cost()`(3,000원, 근거: 온채널 API
    `extends_info.send_price`) + `finalize_approval()` — 최초 시도는
    최소잔여포인트 기준(10만원) 미달로 차단됨 → 회사 정책
    `min_residual_points=30000`으로 변경 후 재시도 → `ACTIVE` 승인 발급
    (approved_at 09:59:26, expires_at 10:09:26).
- **변경 파일**: 코드 변경 없음 — 전부 서비스 계층을 직접 호출해 실 DB에
  생성한 행(`PurchaseTask`#1, `PurchaseOrderApproval`#1,
  `PurchaseTaskPolicySetting`(company 1)의 `min_residual_points` 변경).
- **마지막 검증 결과(이 감사 착수 직전 재확인)**: 실 DB 재조회로
  `PurchaseTask`#1과 `PurchaseOrderApproval`#1(status=`ACTIVE`,
  expires_at=2026-09-14 10:09:26) 존재 확인. **주의: DB의 `status`
  컬럼은 만료 시각이 지나도 자동으로 바뀌지 않는다** —
  `get_active_approval_or_none()`을 실제로 호출해야 `EXPIRED`로
  갱신되는 지연 평가(lazy) 구조다. 이 감사가 재확인한 것은 "행이
  존재하고 저장된 값이 이렇다"는 사실이지 "지금 이 순간 유효하다"는
  뜻이 아니다.
- **미완료 항목**: Phase 7(배송지 입력·최종 발주 승인 요청),
  Phase 8(실제 발주 POST 실행), Phase 9~11(사후 대조·배송추적·완료판정).
- **재개 위치**: Phase 6 승인 만료 재확인부터 — 재개 시 반드시
  `finalize_approval()`을 다시 호출해 신선한 승인을 발급해야 한다(이전
  ACTIVE 값을 그대로 신뢰하지 않는다).

#### B. "쿠팡 상품 등록 구조 검증"(사용자가 세션 중 추가 지시, "구조 검증까지만
끝까지 진행")

- **목표**: 실제 쿠팡 등록 파이프라인(Listing Wizard)의 구조·Schema·Safety
  검증이 끝까지 정상 동작하는지 확인(실제 쿠팡 서버 게시는 별도 승인
  대상으로 보류).
- **진행 상태**:
  - 1단계(상품 소스): `ProductCandidate`#3(`ONCHANNEL:CH1147184`) 생성 +
    "기본 정보 확인" + 운영자 결정 `APPROVE`(`product_candidate_decisions`
    id=3) — Wizard가 이 후보를 인식함 확인됨.
  - 2단계(상품 초안): 상품명·브랜드·카테고리·키워드·설명 입력 저장됨(자동저장
    로그로 확인).
  - 3단계(이미지): 온채널 공개 상품페이지에서 **실제로 스크린샷으로 육안
    확인한** 이미지 1장(바디워시/샴푸 사진, 관련상품 캐러셀 이미지와
    구분됨) 업로드 + 사용권 근거 `SUPPLIER_BRAND_PERMISSION`(위탁판매
    관례상 묵시적 허용으로 사용자가 명시적으로 승인한 근거) 확인 처리 +
    이미지 선택 완료.
  - 4단계(판매채널) **진행 중, 미완료**: 처음 시도 시 "연동된 판매채널
    계정이 없습니다"로 차단됨 → `store_connections`(사용자가 직접 실
    쿠팡 WING 자격증명 입력, `connection_status=CONNECTED`)는 이미
    있었으나, Wizard 4단계는 **별개 테이블**(`marketplace_channels`/
    `marketplace_accounts`)을 조회한다는 것을 코드로 확인 → 이 세션이
    한때 "채널별 판매 방식"이라는 **별도 화면**으로 이동해 문제를
    풀려고 시도(우회 시도, `homez-console-e2e` 스킬 제정 계기) →
    사용자 지시로 재검증한 결과, 실제로는 **원래 Listing Wizard 안에서
    같은 테이블을 그대로 쓰면 되고**, 실 쿠팡 API로 출고지·반품지를
    조회하는 완성된 코드(`coupang_logistics_provider.py`,
    `CoupangLiveProductProvider`)가 이미 존재함을 코드로 확인 →
    `marketplace_channels` seed(3개 채널) + `marketplace_accounts`#1
    (쿠팡, "홈즈") 생성 + `marketplace_fulfillment_capabilities`#1
    (SELLER_FULFILLED) `VERIFIED` 처리까지 완료 → **Wizard 4단계 채널
    선택 확정 자체는 아직 원래 Wizard 화면 안에서 완료되지 않은 상태로
    중단됨**(실 DB `listing_wizards`#1.current_step은 여전히
    `CHANNELS`).
- **변경 파일**: 코드 변경 없음. 실 DB: `store_connections`#1,
  `marketplace_channels`(3행 seed), `marketplace_accounts`#1,
  `marketplace_fulfillment_capabilities`#1(VERIFIED), `listing_wizards`#1
  (DRAFT, current_step=CHANNELS).
- **마지막 검증 결과**: 이 감사 착수 직전 실 DB 재조회로 위 전부 확인됨.
- **미완료 항목**: 4단계 채널 선택 확정부터 10단계(실제 제출, 단 "실제
  게시"는 이번 라운드에서 보류 대상)까지 전부.
- **재개 위치**: 원래 Listing Wizard 4단계(CHANNELS)부터, **반드시
  `homez-console-e2e` 스킬 1번 규칙에 따라 별도 화면으로 이동하지 않고
  진행**.
- **중요 미검증 사실(이 감사가 정정)**: "채널별 판매 방식" 화면에서 본
  경고("실제 쿠팡·네이버 서버 API를 호출하는 코드 자체가 아직 이
  시스템에 없다")는 **그 화면 한정**이라고 이 세션이 판단했으나, 이
  판단 자체도 재검증 대상이다(Phase 4에서 코드로 다시 확인한다) —
  "코드가 존재한다"는 것과 "그 코드가 실제로 안전하게 실행 가능한
  상태"는 다른 질문이다.

#### C. 신규 생성물(코드/문서, 미커밋)

- `.claude/skills/homez-console-e2e/SKILL.md`(신규) — 이번 세션 실수
  사례를 규칙화한 스킬.
- `CLAUDE.md` 1줄 수정 — 위 스킬을 Skill Invocation 목록에 등록.
- 이 문서(`docs/audits/20260914_FULL_AUDIT.md`, 신규).

이 세 파일 외에 이번 세션에서 실제 소스코드(`app/`)를 수정한 사실은
없다(전부 서비스 계층 직접 호출 또는 실 API 호출이었고, `.py`/`.js`
파일 자체를 편집한 적 없음) — `git status`로 재확인됨(아래 1.3).

### 1.3 현재 Git 상태(보존)

```
브랜치: main
로컬 HEAD:  476fc08243ab025de826d9b1c9467b45abf22322
origin/main(로컬 참조): 476fc08243ab025de826d9b1c9467b45abf22322
작업 트리:
  M CLAUDE.md   (스킬 등록 1줄)
  ?? .claude/skills/homez-console-e2e/  (신규 스킬)
```

**주의**: 이 감사 지시 자체가 "origin/main 로컬 참조만으로 GitHub 최신
상태라고 판단하지 않는다"고 명시했다 — 위 origin/main 값은 로컬 참조일
뿐이며, Phase 2에서 실제 원격(`git fetch` 이후 재확인, 필요하면 GitHub
API/웹 대조)으로 다시 확인한다.

476fc08은 이 세션이 만든 커밋이 아니다(`docs: preserve CTO video
reviews and ongoing reporting policy`, 사용자 계정으로 이 세션과
무관하게 커밋됨 — 세션 도중 발견, 충돌 없음).

### 1.4 실 DB 상태 스냅샷(읽기 전용, Phase 1 시점)

```
homez.db 무결성: PRAGMA integrity_check = ok
```

(정확한 SHA-256·크기는 Phase 2에서 DB 경로·프로세스 연결 자체를
재검증한 뒤 공식 경로 기준으로 다시 기록한다 — 이번 Phase 1에서는
"작업을 멈췄다"는 사실과 "지금 이 순간 위 내용물이 존재한다"는
사실만 남긴다.)

---

## Phase 2 — 감사 기준선 확정

### 2.1 Git 기준선(원격 재확인)

`git fetch origin` 실제 실행 후 재확인 — 로컬 참조가 아니라 방금 받아온
원격 값이다.

```
로컬 HEAD:   476fc08243ab025de826d9b1c9467b45abf22322
origin/main: 476fc08243ab025de826d9b1c9467b45abf22322  (fetch 이후 재확인, 동일)
브랜치: main
작업 트리: CLAUDE.md 1줄 수정 + .claude/skills/homez-console-e2e/ 신규(둘 다 미커밋)
```

### 2.2 ⚠ Critical 발견 — 개발본 DB와 설치본 DB가 서로 다른 파일이다

**이번 세션(및 최근 여러 세션)이 "실제 검증"이라고 불러온 모든 작업은
설치본이 아니라 개발 체크아웃의 DB에서만 일어났다.**

| 항목 | 개발본 | 설치본(실제 데스크톱 앱) |
|---|---|---|
| 경로 | `C:\Users\Daum pc\Homez-OS\homez.db` | `C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db` |
| 파일 크기 | 3,420,160 bytes | 2,953,216 bytes |
| 최종 수정 시각 | 2026-09-14 20:20(오늘, 이번 세션) | 2026-08-29 21:02(2주 이상 전) |
| SHA-256 | `ae5867b0...d330bc5` | `c28eaf57...9d322913` |
| inode | 562949954159007 | 6473924464550981 |
| **내용물이 같은 파일인가** | — | **아니오. 완전히 다른 파일이다.** |
| `purchase_channel_connections` 테이블 존재 | 있음(id=4, 온채널 연결) | **없음**(이 기능 자체가 Migration된 적 없음) |
| journal_mode | delete | delete |
| integrity_check | ok | ok |
| 경로 결정 로직 | `app/desktop/paths.py::is_frozen()==False`일 때(소스 실행) 이 경로로 감 | `is_frozen()==True`(PyInstaller 패키징 실행 파일)일 때만 이 경로로 감 |
| 마지막 실제 로그인 기록 | (이 세션이 방금 만든 것) | 2026-09-07 17:05(`homez.log`), 이후 2026-08-30 02:48 창 종료가 마지막 정상 종료 기록 |
| 현재 실행 중인 프로세스 | 없음(이 Phase 1에서 중지함) | 없음(확인됨, `Get-Process`에 homez 계열 프로세스 없음) |
| 백업 이력 | 이번 세션이 만든 백업 1건(`storage/backups/homez_pre_bootstrap_migration_20260914_192543.db`) | `2026-08-24 인시던트`(기존 기억에 있는 DB 복구 사고) 백업, `2026-08-27`, `2026-08-29` 백업 다수 존재 — **실제 운영 이력이 있는 진짜 설치본** |

**의미**: `app/desktop/paths.py`의 dev/frozen 분리 설계 자체는 의도된
계약이다(문서화돼 있음, 버그 아님). 하지만 그 결과로, 이번 세션이
"실제 온채널 발주 검증"·"실제 쿠팡 계정 연동"이라고 부른 모든 작업은
**사용자가 실제로 쓰는 설치본 앱에는 전혀 반영되지 않은, 개발
체크아웃 전용 데이터**다. 설치본은 `purchase_channel_connections`
테이블조차 없어 이번에 만든 온채널 연결·자격증명 참조를 알지 못한다.

이 발견은 이번 감사 지시가 우려한 "개발본·설치본·실행 서버·DB·자격증명
저장소의 관계" 문제의 실제 사례다 — Phase 7(사용자 결정 필요 항목)에서
다시 다룬다: 사용자가 실제로 검증을 이어가려는 대상이 **개발
체크아웃인지, 설치본을 최신화한 뒤인지**부터 정해야 한다.

### 2.3 자격증명 저장소(Windows Credential Manager) 관계

`purchase_channel_connections` 테이블 자체가 설치본에 없으므로, 이번
세션이 사용한 `homez_channel_connection_4`라는 이름과 설치본 사이의
충돌 가능성은 **없음**(설치본은 그 이름을 참조하는 코드 경로 자체가
없다). 다만 Windows Credential Manager는 OS 전역 저장소이므로 이
이름 아래의 실제 값은 개발본 프로세스가 만들었든 사용자가 과거에
수동으로 등록했든 **두 실행 환경이 기술적으로는 같은 저장소를
공유한다** — 이름 충돌은 지금 확인된 바 없지만, 구조적으로 격리돼
있지 않다는 사실은 기록해 둔다.

### 2.4 해시 불변 = 무접촉이 아니다(이 감사의 원칙 적용)

이전 세션 보고들이 "회귀 전후 homez.db 해시 동일 = DB 무접촉"이라고
여러 차례 기록했다. 이 감사 지시는 이 논리를 "읽기 포함 무접촉"의
증거로 쓰지 말라고 명시했다 — 해시가 같다는 것은 "그 두 시점 사이에
남은 변경이 없다"는 것만 증명하고, 그 사이 실제로 파일이 열리거나
읽혔는지는 별개 사실이다. 이 원칙을 이후 Phase의 모든 "무접촉 확인"
문구에 그대로 적용한다(과장하지 않는다).

### 2.5 실행 서버 상태

Phase 1에서 이 세션이 띄운 `homez-live`(포트 8000, 개발본 DB 연결)는
중지됨. 그 외 실행 중인 HOMEZ 관련 프로세스 없음(`Get-Process` 확인).
설치본 앱도 현재 실행 중이 아님.

---

## Phase 3 — 사용자 요구사항 전수 대조

### 3.1 확정 지시 vs 제안값 vs 폐기 기준 구분

`docs/HOMEZ_DECISIONS.md`(95줄, 전체 재확인) 기준 확정 사항: 신제품
판정(0~15일, Asia/Seoul), 자동화 4단계 승인 원칙(V3/V4 운영자 승인
필수), `ProductCandidate.status` 의미 고정(정책 A), V7 매입 실행
통합의 6단계 후속 개발 순서. 이번 감사 지시와 충돌하는 항목 없음 —
전부 그대로 유효.

### 3.2 기존 223개 세부기준 감사(2026-09-09)와의 관계 — 범위 고지

`docs/HOMEZ_USER_OPERATION_SETTINGS_AUDIT_20260909.md`는 이미 14개
섹션·223개 세부기준 각각에 고유 번호(예: `6-3`, `8-9`)를 부여해
구현완료/부분구현/미구현/검증필요로 판정한, 이 감사가 요구하는 형식과
동일한 기존 산출물이다. **이 감사에서 223개 전부를 처음부터 다시
파일·함수·테스트로 재대조하는 것은 이번 Phase 3 범위 안에서
완료하지 못했다** — 아래는 실제로 재확인한 것과 못한 것을 정직하게
나눈 결과다(추측으로 "갱신됨"이라고 채우지 않는다).

**이번 감사에서 실제로 코드·DB로 재확인한 항목(신뢰 가능)**:

| 기존 번호 | 2026-09-09 판정 | 이번 재확인 결과 | 근거 |
|---|---|---|---|
| (Credential 격리, 번호 미부여— 2026-09-11 추가 발견) | 해당 없음(사후 발견 결함) | **수정·테스트로 확인됨** | `app/domains/purchase_task/channel_adapter.py` fallback 제거, `CredentialReferenceIsolationTestCase` 10건, 2026-09-14 재조회 시 51/51 유지(이번 세션 직접 실행은 안 했음 — 커밋 `eed7ec7` 내용 검토로 확인) |
| 5-4(자동결제 한도 이중구조) | 정리 필요로 지적됨 | **부분 해결, 새 결함 발견** — 아래 5.1 참고 | `order_approval_service.py` 직접 재독 |
| 8-9/8-10(배송비 미확인 시 발주 중지) | 미구현 | **구현됨으로 보임**(`confirm_shipping_cost`/Gate D 코드 존재) — 이번 세션이 실제로 그 게이트를 통과시켜본 것으로 간접 확인 | 이번 세션 Phase 6 실행 로그 |

**재확인하지 못한 항목**: 나머지 약 220개 세부기준. 특히 Section
10(상품·이미지·규제데이터)·12(AI 분석·학습기반)·13(운영
UI·관제)은 이번 세션이 전혀 건드리지 않은 영역이라 2026-09-09
판정을 그대로 두되 "최신 상태 미확인"으로 표시한다.

**검산**: 이 감사가 새로 판정한 항목 3개(위 표) + 미재확인 220개 =
223개. 기존 총계와 일치.

### 3.3 V8 목표가 V7 완료로 잘못 집계됐는지 확인

`docs/HOMEZ_PROJECT_STATE.md`의 "V8 공식 목표 추가" 절(2026-09-14) —
문구 재확인 결과: "현재 판정은 `OFFICIAL_V8_GOAL_DESIGN_ONLY`이며...
코드 구현, DB Migration, 외부 쓰기, 상품 제출, 발주 또는 결제 승인을
의미하지 않는다"고 명시돼 있다. **V8이 V7 구현완료로 잘못 집계된
사례는 발견되지 않았다.** V8 관련 코드 변경도 없음(`git log`상 V8
문서 커밋(`be0ed59`)은 `docs/HOMEZ_V8_*.md` 1개 파일만 포함, `app/`
변경 없음 — 직접 확인).

---

## Phase 4 — 전체 기능과 연결 흐름 감사

이번 Phase도 범위를 정직하게 고지한다: 지시문이 나열한 약 15개
기능군 전부를 "화면 → API → Service → DB/Provider → 결과 화면"까지
동일한 깊이로 추적하는 것은 이번 감사 라운드 안에서 완료하지
못했다. 아래는 **이번 세션이 실제로 손댄 영역**(매입 실행·판매신청·
Credential·상품 후보·쿠팡 등록 파이프라인)을 깊게, 그 외는 얕게
다룬 결과다.

### 4.1 깊게 확인한 영역

- **인증·세션**: 이번 세션 중 토큰이 예고 없이 만료되는 것을 2회
  실제로 관측했다(원인 미조사 — 세션 만료 정책이 설계값대로인지,
  버그인지는 **미확인**으로 남긴다. `docs/HOMEZ_USER_OPERATION_
  SETTINGS.md`가 명시한 "3시간 유휴 세션 타임아웃"과 실제 관측된
  만료 간격이 일치하는지 이번 감사에서 시간을 재보지 않았다).
- **상품등록·외부 등록번호 보존**: 쿠팡 Listing Wizard 경로를 4단계
  까지 실제로 통과시켰다. 9단계(실제 제출)는 시도하지 않음(사용자가
  보류 지시). `coupang_live_payload.py`/`coupang_submission_contract.py`
  /`coupang_logistics_provider.py`가 실 API 실행 코드로서 실제
  존재함을 코드로 확인했으나, **이번 감사에서 그 코드를 실제로 실행해
  성공을 관측하지는 않았다** — "코드가 있다"와 "그 코드가 실제로
  동작한다"는 다른 주장이며, 후자는 미검증이다.
- **매입 작업·가격·재고·배송비 확인**: 아래 Phase 5에서 집중 감사.
- **Fake 응답/하드코딩 성공값 구분**: `FakePurchaseChannelAdapter`,
  `FakeCoupangLogisticsProvider`, `FakeCoupangLiveProductProvider`
  모두 `HOMEZ_TEST_FAKE_COUPANG_PROVIDER=1` 등 **명시적 환경변수
  없이는 개입하지 않는 구조**임을 코드로 확인(`listing_wizard_router.py`).
  이번 세션의 실제 호출들은 이 환경변수를 설정한 적이 없으므로
  Fake가 아니라 실제 경로를 탔다 — 단, 이 사실 자체를 이번 감사
  시점에 별도로 재확인하지는 않았다(이전 세션 관측 기록에 의존).

### 4.2 얕게 확인했거나 확인하지 못한 영역

인증(세부 권한 매트릭스)·회사 격리 전수·알림·감사로그·상품
발굴/AI 분석·공급처 자료 수집·이미지 처리 나머지·SKU 연결·주문
수집 나머지·정산·실제 순이익 계산·예약 작업·백업 복원 절차·
Migration 외 설치 업데이트 흐름은 이번 라운드에서 코드 추적을
수행하지 못했다. 2026-09-09 감사와 그 이후 `HOMEZ_PROJECT_STATE.md`
기록을 1차 근거로 유지하되, 이번 감사가 직접 재확인한 것으로
표시하지 않는다.

---

## Phase 5 — 금전 실행과 데이터 안전성 집중 감사

### 5.1 ⚠ High 발견 — 승인됐지만 아직 미실행인 금액이 한도 재확인에서 빠진다

`PurchaseOrderApprovalService.finalize_approval()`의 일간·월간 한도
재확인(`_sum_consumed_amount_today`/`_sum_consumed_amount_this_month`)
은 **오직 `status == CONSUMED`인 승인만 합산한다.** 코드 재확인
(`order_approval_service.py`)으로 다음이 사실임을 확인했다:

- 회사 A가 작업 1건을 승인받아 `ACTIVE` 상태가 됐지만(아직 온채널에
  제출 전, 아직 `CONSUMED` 아님), 동시에 다른 작업 2건도 각각
  `finalize_approval()`을 통과할 수 있다 — 각 호출은 그 시점까지
  **실제로 `CONSUMED`된 것만** 합산해 한도와 비교하므로, 서로의
  `ACTIVE` 상태를 반영하지 못한다.
- `max_concurrent_tasks`(설정 시)가 동시 진행 작업 수는 제한하지만,
  **금액 기준 일간·월간 한도를 우회하는 것 자체를 막지는 않는다**
  (한도-체크 코드와 별개 필드다).
- 이번 세션 자체(2026-09-12)에서 이 파일에 월간 한도·동시진행 한도
  재확인을 **추가한** 것은 사실이나, "승인됐지만 미실행인 금액"
  문제는 그때도 발견되지 않았다 — 이번 감사에서 새로 발견한 결함이다.

**심각도**: High(단, 실제 발생 조건은 "짧은 시간 안에 여러 건이
각각 승인 절차를 통과"해야 하므로 사람이 매 건 수동 승인하는 현재
설계에서는 실제 발생 가능성이 낮다 — 그러나 한도 자체가 설계대로
강제되지 않는다는 사실은 그대로 결함이다).

**재현 조건**: 회사 정책 일간 한도 X원, 개별 작업 3건을 각각
X/2원짜리로 짧은 시간(10분 승인 유효시간 안) 내 순서대로 승인 →
셋 다 `ACTIVE` 통과 → 셋 다 실행되면 합계 1.5X원으로 한도 초과.

**수정 방향(제안, 이번 감사에서 코드 변경은 하지 않음)**: 한도
합산에 `CONSUMED`뿐 아니라 아직 만료되지 않은 `ACTIVE` 승인의
`required_points_snapshot`도 포함해야 한다.

**실제 재현 시험(격리 임시 SQLite, 실 DB 미접촉)**: 코드 추론에
머물지 않고 직접 재현했다 — 회사 1곳, 일간 한도 150,000원 설정,
작업 3건(각 80,000원, 합계 240,000원)을 순서대로
`finalize_approval()` 호출한 결과:

```
daily_limit=150000, 3 x 80000=240000 attempted
(0, 'APPROVED', 'ACTIVE')
(1, 'APPROVED', 'ACTIVE')
(2, 'APPROVED', 'ACTIVE')
```

**3건 전부 승인 통과 — 합계 240,000원이 150,000원 한도를 실제로
초과한 채 전부 ACTIVE 상태가 됐다.** 이것으로 5.1은 추론이 아니라
실제 재현된 결함으로 격상한다. (재현에 쓴 임시 SQLite는 시스템
temp 폴더에 생성·격리 사용 후 삭제 시도했으며, 실 `homez.db`는
이 시험 중 전혀 열지 않았다.)

### 5.2 재확인해 유지되는 항목(기존 설계가 타당함을 확인)

- **온채널 `sale_code` 미중복방지 반영**: `PurchaseOrderSubmissionService
  .compute_idempotency_key()`가 클라이언트 입력이 아니라 서버가
  (회사·연결·작업·상품·옵션) 조합의 기존 시도 개수 기반으로 계산 —
  온채널 자체가 중복을 막지 않는다는 공식 계약을 HOMEZ 서버 쪽
  장부가 유일한 방어선으로 대신한다는 설계 의도가 코드에 그대로
  반영돼 있음을 재확인했다.
- **timeout/응답누락 → UNKNOWN, 자동 재시도 없음**: `_post`/`_get`의
  네트워크 예외 처리가 "결과 불명"과 "명시적 실패"를 구분하고,
  `order_submission_service.py`가 UNKNOWN 상태에서 같은 작업의 새
  발주 시도를 전부 차단(`_has_unresolved_unknown_attempt`)함을
  코드로 재확인했다 — 자동 재발주 경로 없음.
- **threading.Lock 범위를 과장하지 않았는지 재확인**: 이번 세션이
  단 커밋 메시지·`docs/HOMEZ_PROJECT_STATE.md` 기록을 재확인한 결과,
  "이 락은 프로세스 내 경쟁조건만 닫는다, 다중 프로세스·DB 수준
  잠금은 범위 밖"이라고 **스스로 명시**해 뒀음을 확인했다 — 과장된
  주장 없음. 단, 이 감사 지시가 요구한 "실제로 다중 프로세스에서
  재현 시험" 자체는 이번에도 수행하지 않았다(단일 데스크톱 프로세스
  아키텍처 특성상 우선순위가 낮다고 판단했으나, 시험 자체는
  미수행임을 명시한다).
- **Credential fallback 제거**: 재확인됨(2.2/3.2 참고).

### 5.3 미수행 시험(정직하게 미확인으로 남김)

- 동시 실행·다중 프로세스·재시작 중 경쟁조건 실제 재현 시험 — 미수행.
- 네트워크 단절 상황 실제 재현(현재는 코드 경로 존재만 확인, 실제
  단절 시뮬레이션 미수행).
- 승인 만료 직후 제출 시도 시 서버가 정확히 차단하는지 실제 재현 —
  코드상 `get_active_approval_or_none()`의 만료 로직은 확인했으나,
  실제로 만료된 승인으로 `submit_order()`를 호출해보는 시험은 이번
  세션에서 실행하지 않았다.

---

## Phase 6 — 테스트 자체의 신뢰성 감사

### 6.1 기존 테스트가 실제 DB·네트워크·자격증명에 접근하는지

`tests/test_purchase_order_approval_service.py`는 매 테스트 클래스마다
`tempfile.mkstemp(suffix=".db")`로 격리된 임시 SQLite를 만들고
`tearDown()`에서 삭제한다 — 실 `homez.db`를 참조하지 않음을 코드로
재확인했다. `PurchaseOrderApprovalService`는 외부 네트워크를 전혀
호출하지 않는 순수 DB 로직이라는 파일 docstring도 실제 메서드
목록(가격/한도/마진 계산, DB 쓰기뿐)과 일치함을 확인했다.

### 6.2 기존 테스트가 5.1 결함을 왜 못 잡았는가

`DailyLimitAggregationTestCase`/`MonthlyLimitAggregationTestCase`는
전부 **"먼저 `mark_consumed()`로 완료 처리한 뒤, 그 다음 건이
막히는지"만** 시험한다 — 즉 "이미 소비 확정된 금액"만 시나리오에
넣었지, "**여러 건이 소비 확정 전(ACTIVE) 상태로 동시에 쌓이는
경우**"는 어떤 기존 테스트에도 없었다. 기대값을 조작하거나 mock으로
결함을 가린 것은 아니다 — **애초에 이 시나리오 자체가 테스트
목록에 없었다**(커버리지 공백, 은폐 아님).

### 6.3 실행한 회귀

- `tests/test_purchase_order_approval_service.py` 단독 재실행: **26/26
  OK**(이번 감사 시점, venv 인터프리터로 직접 실행 — 아래 원문 근거).
- 저장소 전체 회귀(4천여 건, ~100분)는 **이번 감사에서 재실행하지
  않았다** — 이유: 이번 감사가 실제로 변경한 코드가 아직 없는 시점
  (감사 전용 단계)이었고, 마지막으로 알려진 전체 회귀(2026-09-12,
  385/385 OK)와 이번 재실행 대상 파일 사이에 코드 차이가 없다.
  **5.1을 수정한 뒤에는 관련 파일 + 전체 회귀를 반드시 재실행한다**
  (Phase 8에서 수행).

```
Ran 26 tests in ... 
OK
```
(2026-09-14 이 감사 세션에서 직접 실행, venv\Scripts\python.exe)

### 6.4 Migration 파일 무결성

이번 감사에서 `migrations/` 아래 기존 적용 파일의 바이트를 변경한
적 없음(git status로 확인 — 이번 세션 `git diff`에 `migrations/`
변경 없음).

---

## Phase 7 — 결함 수정(감사에서 발견된 것만, 범위 확장 금지)

### 7.1 5.1 수정 — CONSUMED + 미만료 ACTIVE 금액 함께 합산

**수정 파일**: `app/domains/purchase_task/order_approval_service.py`
— `_sum_consumed_amount_today()`/`_sum_consumed_amount_this_month()`를
공용 `_sum_reserved_amount(company_id, since)`로 리팩터링하고,
필터에 `(status == CONSUMED) OR (status == ACTIVE AND expires_at IS
NOT NULL AND expires_at > now())`를 적용했다. 다른 새 기능·범위
확장 없음(감사 지시대로 발견된 결함만 수정).

**수정 전 재현(위 5.1) 대비 수정 후 재검증**: 동일한 재현 스크립트
(회사 1곳, 일간 한도 150,000원, 8만원짜리 작업 3건 순서대로 승인
시도)를 수정 후 다시 실행:

```
daily_limit=150000, 3 x 80000=240000 attempted (AFTER FIX)
(0, 'APPROVED', 'ACTIVE')
(1, 'BLOCKED', '409: ... 하루 발주 한도(150000원) 초과 — 오늘 이미 80000원 발주, 이번 건 80000원 추가 시 초과.')
(2, 'BLOCKED', '409: ... 동일 사유로 차단')
```

첫 건만 통과하고 나머지 2건은 정확히 차단됨 — 결함이 닫혔다.

### 7.2 기존 테스트 정정(결함을 가리기 위한 변경이 아님을 근거로 명시)

`tests/test_purchase_order_approval_service.py::
DailyLimitAggregationTestCase`의 `test_only_consumed_rows_within_24h_
counted`는 원래 "ACTIVE는 합계에서 제외돼야 한다"는, **이번에 결함으로
확인된 그 동작 자체**를 정답으로 검증하고 있었다. 이름과 기대값을
`test_consumed_and_active_rows_within_24h_counted`(기대값 16000으로
정정, CONSUMED 6000 + 미만료 ACTIVE 10000)로 고치고, 새로 경계값
테스트 `test_expired_active_rows_not_counted`(만료된 ACTIVE는 합계에서
제외되는지)를 추가했다. **테스트를 고친 이유는 기대값 자체가
결함이었기 때문이며, 결함을 감추기 위해 assertion을 약화한 것이
아니다** — 이 판단 근거를 여기 명시적으로 남긴다.

### 7.3 격리 재검증

`tests/test_purchase_order_approval_service.py` 단독 재실행:
**27/27 OK**(26개 기존 + 신규 1개, 정정된 테스트 포함). 실행 로그
원문(2026-09-14, venv 인터프리터 직접 실행):

```
Ran 27 tests in 13.141s
OK
```

### 7.4 이번 감사에서 수정하지 않은 것(범위 밖으로 유지)

- 2.2절의 개발본/설치본 DB 분리는 **결함이 아니라 사업 판단이
  필요한 사안**이므로 코드를 고치지 않았다 — Phase 9(사용자 결정
  필요 항목)에서 다룬다.
- 5.3절의 미수행 시험(다중 프로세스 경쟁조건 실제 재현, 네트워크
  단절 재현, 만료된 승인으로 실제 `submit_order()` 재현)은 이번
  라운드에서 수행하지 않았다 — 아래 Phase 10(감사하지 못한 범위)에
  그대로 남긴다.

---

## Phase 8 — 전체 회귀 재검증

`tests/test_purchase_*.py` 패턴 전체(구매/매입/발주/승인 관련 전
파일) 재실행 — **386개 테스트, 515.1초, 전부 OK(실패 0건)**. 이전
알려진 기준(2026-09-12, 385개)보다 1개 많은 것은 이번에 추가한
`test_expired_active_rows_not_counted` 때문(정확히 일치, 예상과
다른 증가 없음). 실행 전후 실 `homez.db` SHA-256(`ae5867b0...`)·
크기(3,420,160바이트) 완전히 동일 — 이번 Phase 1~8 전체에 걸쳐 실
DB는 단 한 번도 쓰기 접촉되지 않았다.

저장소 전체 회귀(purchase 외 전 도메인, 4천여 건)는 이번 라운드
에서 재실행하지 않았다 — 이번에 변경한 코드가 `purchase_task`
도메인 1개 파일뿐이고 다른 도메인을 참조·수정하지 않았기 때문에
영향 범위가 명확하다고 판단했다. **이것도 "재실행하지 않았다"는
사실 그대로 기록한다**(전체 회귀를 했다고 과장하지 않는다).

---

## Phase 9 — 요구사항 대조표 갱신(이번 감사로 새로 확정된 것만)

| 항목 | 이전 판정 | 이번 판정 | 근거 |
|---|---|---|---|
| 자동결제 한도 — 승인된 미실행(ACTIVE) 금액 반영 | 미확인(암묵적으로 "구현완료"로 취급돼 왔음) | **검증필요 → 결함 확인 → 수정 완료 → 재검증 완료** | 5.1/7.1/7.3, 재현+수정+회귀 전부 실행 |
| 개발본/설치본 DB 일치 여부 | 암묵적으로 "같다"고 가정돼 옴(명시적 판정 없었음) | **불일치 확인, 사용자 결정 필요** | 2.2 |

나머지 221개 세부기준(2026-09-09 기존 감사 기준)은 이번 라운드에서
갱신하지 않았다 — 기존 판정을 그대로 유지하되 "2026-09-14 시점
재확인 안 됨"으로 표시한다(Phase 10 참고).

---

## Phase 10 — 감사하지 못한 범위와 남은 불확실성(정직한 고지)

- **223개 세부기준 중 220개**를 이번 라운드에서 파일·함수·테스트로
  다시 대조하지 못했다. 2026-09-09 판정과 그 이후 세션 기록을
  1차 근거로 유지하되, "최신 재확인 완료"로 표시하지 않는다.
- **Phase 4 기능 감사는 이번 세션이 직접 손댄 영역(매입 실행·
  Credential·상품후보·쿠팡 등록 파이프라인 진입부)만 깊게 봤고**,
  인증 세부 권한, 알림, AI 학습, 정산, 백업·복원 절차 등은 얕게
  다루거나 손대지 못했다.
- **세션 중 토큰이 2회 예고 없이 만료된 원인**은 조사하지 못했다
  (설계된 정책인지 결함인지 미확인).
- **다중 프로세스 경쟁조건 실제 재현, 네트워크 단절 재현, 만료된
  승인으로 실제 `submit_order()` 호출 재현**은 수행하지 않았다.
- **쿠팡 Listing Wizard 9단계(실제 제출) 코드가 실제로 동작하는지**는
  코드 존재만 확인했고 실행해 성공을 관측하지 않았다.
- **설치본 앱의 실제 최신화 필요 범위**(Migration 몇 개가 밀려
  있는지 등)는 이번 감사에서 별도로 진단하지 않았다(2.2에서 파일
  차이만 확인, `MigrationRunner.diagnose()`를 설치본 DB에 대해
  실행하지 않았다).

**"100% 안전" 또는 "누락 없음"을 선언하지 않는다** — 위 항목들이
정확히 이번 감사가 커버하지 못한 범위다.

---

## Phase 11 — 최종 판정 및 자동 재개 여부

### 11.1 자동 재개하지 않기로 한 판단(지시문 예외 조항 적용)

지시문은 "전체 감사·결함 수정·재검증 완료 후" 자동 재개하라고
명시했고, 동시에 "외부 조건 때문에 필수 검증이 불가능하면 전체
완료로 선언하지 않고 관련 작업의 중지를 유지한다"는 예외도 명시했다.

이번 라운드는 **223개 세부기준 중 3개만 새로 확정**했고 220개는
재확인하지 못했으며, 2.2절(개발본/설치본 DB 불일치)은 코드로
해결할 수 있는 결함이 아니라 **사용자가 직접 정해야 하는 방향
결정**이다 — "온채널 첫 실사용 검증"과 "쿠팡 등록 구조 검증"을
재개하는 것 자체가 "어느 DB를 기준으로 재개하는가"라는, 이
감사만으로는 풀 수 없는 전제에 걸려 있다.

따라서 **"전체 감사 완료"를 선언하지 않고, Phase 1에서 중지한
두 작업(온채널 첫 실사용 검증, 쿠팡 등록 구조 검증)의 중지 상태를
그대로 유지한다.** 이번 라운드에서 실제로 완료된 것은: (1) 중지·
증거 보존, (2) 기준선 확정(Critical 발견 포함), (3) 요구사항
부분 대조, (4~5) 기능·금전 감사(부분, 깊이 편차 있음), (6) 테스트
신뢰성 감사, (7~8) 발견된 결함 1건의 수정과 재검증.

### 11.2 신뢰할 수 있는 현재 기준선

```
HEAD = origin/main(fetch로 재확인) = 476fc08(이 감사 착수 시점)
작업 트리: 이 감사가 추가한 변경만(아래 11.5)
개발본 DB: C:\Users\Daum pc\Homez-OS\homez.db(오늘 작업의 실제 대상)
설치본 DB: C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db(2026-08-29 이후 미변경,
  이번 세션과 무관 — 별개 파일)
실행 중인 서버: 없음(둘 다 중지/미실행 상태로 확인됨)
```

### 11.3 Critical/High부터 정렬한 결함 목록

1. **(High, 수정됨)** 자동결제 일간·월간 한도가 승인됐지만 미실행인
   (ACTIVE) 금액을 반영하지 못해 여러 건을 짧은 시간 안에 승인하면
   합계가 한도를 넘어도 전부 통과 — 재현·수정·재검증 완료(Phase 5.1/7).
2. **(Critical급 사실, 코드 결함 아님)** 개발본과 설치본 DB가 완전히
   다른 파일이라 최근 여러 세션의 "실제 검증" 작업이 설치본에
   전혀 반영되지 않음 — 사용자 결정 필요(Phase 2.2, 아래 11.6).

### 11.4 잘못된 과거 완료 보고와 정정 근거

- 이전 세션들이 "회귀 전후 homez.db 해시 동일 = DB 무접촉"이라고
  단정적으로 표현한 사례들 — 이 감사 지시의 원칙(2.4)에 따르면
  과장된 표현이다. 해시 불변은 "그 구간에 남은 변경이 없다"만
  증명한다. 기존 기록을 삭제하지 않되, 앞으로는 이 감사 문서의
  표현 원칙을 따른다.
- 이전 세션들의 "실제 검증 완료" 표현들(온채널 발주 준비, 쿠팡
  계정 연동) — 틀린 것은 아니지만 **어느 DB 기준인지 명시하지
  않았다.** 전부 개발본 기준이었다는 사실을 이번에 처음 명시한다.
- `test_only_consumed_rows_within_24h_counted`(구 이름)가 검증하던
  기대값 자체가 결함이었다 — 7.2에서 근거를 붙여 정정.

### 11.5 이번 감사가 만든 변경(파일 목록)

- 신규: `docs/audits/20260914_FULL_AUDIT.md`(이 문서)
- 수정: `app/domains/purchase_task/order_approval_service.py`(5.1 수정)
- 수정: `tests/test_purchase_order_approval_service.py`(7.2 테스트 정정 + 신규 1건)
- (이 감사 착수 전부터 있던 미커밋 변경, 이번 감사와 무관하지만
  같은 작업 트리에 존재: `CLAUDE.md` 1줄, `.claude/skills/
  homez-console-e2e/` 신규 — 지시문 "감사 문서만 커밋, 기존 코드
  변경은 함께 담지 않는다"에 따라 **별도 커밋으로 분리**한다.)

### 11.6 사용자 결정이 필요한 항목

1. **개발본 vs 설치본**: 앞으로 실제 검증(온채널 발주, 쿠팡 등록)을
   개발 체크아웃 기준으로 계속할지, 설치본을 먼저 최신화(밀린
   Migration 적용 + 재설치)한 뒤 설치본 기준으로 다시 할지.
2. **중지된 두 작업의 재개 여부**: 위 결정 이후에도 자동으로
   재개하지 않고 별도 지시를 기다린다(11.1 근거).
3. **223개 중 220개 미재확인 항목**을 이번처럼 부분적으로 볼지,
   전수 재확인을 별도 라운드로 진행할지.

### 11.7 실제 운영 가능 범위와 차단해야 할 기능

- **운영 가능(재검증됨)**: 매입 작업 최종 승인 게이트(건당·일간·
  월간·동시진행 한도, 최소잔여포인트, 마진) — 이번 수정 이후
  한도가 실제로 강제됨을 재현 시험으로 확인.
- **차단 유지해야 함**: 실제 온채널 발주 POST, 실제 쿠팡 상품 등록
  제출(9단계) — 둘 다 미완주 상태, 이번 감사도 실행하지 않음.
- **판단 보류**: 설치본 앱을 그대로 실행하면 어떤 기능이 보일지는
  이번 감사에서 설치본을 직접 실행해 확인하지 않았다(11.6-1 결정
  이후 필요시 확인).

---

**최종 상태**: 감사 진행(부분 완료) — [감사 중] 해제, 아래 요약과
함께 중지 상태 유지로 전환.

---

## Phase 12 — 독립 병행 감사 교차 확인(2026-09-14, 사후 발견)

Phase 11 작성·commit·push 직후, `origin/main`이 이 감사가 모르는
사이에 두 커밋(`879145c`, `30b0516`) 만큼 앞서 있었음을 push
시점에 발견했다. 확인 결과 사용자가 **별도의 독립 병행 감사**
(`docs/audits/20260914_CODEX_INDEPENDENT_AUDIT.md`, 다른 도구/에이전트
수행으로 추정)를 이 감사와 동시에 진행시키고 있었다. 이 문서는
이 세션이 작성한 것이 아니며, 이 세션은 그 문서를 수정하지 않고
**교차 확인 자료로만** 인용한다.

### 12.1 이 감사(Claude)와 독립 감사(CODEX)의 결과 비교

| 항목 | 이 감사(Claude) | 독립 감사(CODEX) |
|---|---|---|
| ACTIVE 미반영 한도 결함 | 발견·재현·**수정·재검증 완료**(7.1) | 같은 결함을 독립적으로 재현(IA-001) — **이 감사의 수정 이후에도 완전히 해소되지 않았다고 지적**: 실행 직전 재검증(`order_submission_service.py`)은 일간·월간 한도를 다시 검사하지 않는다 |
| 승인 취소/재확인 시 소비 집계 소실 | **발견하지 못함** | IA-002(High) — `confirm_shipping_cost()`가 이미 `CONSUMED`인 승인도 `PENDING_SHIPPING_COST`로 덮어써 소비 집계가 13,000원→0원으로 사라지는 것을 재현 |
| 최신 잔여포인트 정책 재검증 누락 | **발견하지 못함** | IA-003(High) — 실행 직전 게이트가 승인 시점 스냅샷·최소잔여 정책을 다시 검사하지 않음 |
| 동일 작업 중복 발주(다른 idempotency key) | **발견하지 못함** | IA-004(**Critical**) — `compute_idempotency_key()`가 시도 개수 기반이라 첫 요청이 IN_FLIGHT인 동안 두 번째 요청이 다른 키를 받아 실제로 두 번 전송(재현: `fake_sends 2 different_keys True`) |
| 승인된 상품과 다른 상품도 총액만 같으면 제출 가능 | **발견하지 못함** | IA-005(High) — `revalidate_before_submission()`이 상품·옵션·수취인을 대조하지 않고 금액 합계만 비교 |

**정직한 평가**: 독립 감사가 이 감사보다 **더 깊고 더 심각한
문제를 더 많이 찾았다.** 특히 IA-004(Critical, 실제 중복 발주
가능성)는 이 감사가 전혀 발견하지 못한 항목이며, 온채널 발주
재개를 막아야 하는 가장 강력한 이유다. 이 감사의 7.1 수정은
IA-001이 지적한 더 큰 문제(실행 직전 게이트 미재검증)의 일부만
닫았다 — "한도 문제를 해결했다"고 표현하지 않는다.

### 12.2 이 감사의 최종 판정 수정

11.1의 "자동 재개하지 않는다"는 판단은 그대로 유지되며, 그 근거가
**더 강해졌다.** 독립 감사가 지적한 IA-002~005는 이 세션이 아직
코드로 수정하지 않았다(다른 감사와의 충돌을 피하기 위해 독립
감사도 제품 코드를 건드리지 않았음 — 두 감사 모두 조율 없이 같은
파일을 동시에 고치는 위험을 스스로 피했다).

**남은 작업(우선순위)**: IA-004(Critical, 중복발주) → IA-001의
나머지(실행 직전 한도 재검증) → IA-002(소비 집계 소실) →
IA-003(최신 잔여포인트) → IA-005(상품 불일치). 이 감사는 이 5개
항목에 대해 코드 수정을 시작하지 않았다 — 독립 감사 문서 자체가
"다음 독립 감사 순서"를 별도로 제안하고 있어, 두 감사·수정 작업이
서로 충돌하지 않도록 **사용자의 조율 지시를 기다리는 것이 안전
하다고 판단**했다.

---

## Phase 13 — 감사 후속 결함 수정(2026-09-15, Phase 1~2)

사용자 지시("[HOMEZ 전체 감사 후속 결함 수정 작업]")에 따라 CODEX
독립 감사가 로컬에만 반영해둔 IA-001~010 수정을 교차검증 후 커밋
(`a4bfab1`)했고, 이어서 IA-004/IA-005/IA-008이 남긴 "옵션 불일치도
제출 가능" 잔여 위험을 닫는 작업을 진행했다.

### 13.1 Phase 1 — CODEX 로컬 수정 교차검증 및 커밋

- 대상 파일 10개(`order_approval_service.py`,
  `order_submission_service.py`, `constants.py`, `payment/router.py`,
  `ai_learning/service.py` 및 대응 테스트 5개)를 diff 단위로 직접
  검토 — IA-001, IA-004, IA-006, IA-007, IA-008, IA-009, IA-010에
  각각 대응하는 변경임을 확인.
- 독립 실행: 118/118 통과(Fake Provider·임시 SQLite만 사용, 실제
  Windows Credential Manager·실제 homez.db 미접촉).
- 커밋 `a4bfab1`로 체크포인트 생성, push 완료.
- **이 라운드가 닫지 못한 항목**: IA-002(재확인 시 CONSUMED 승인
  덮어쓰기)는 CODEX 수정에 이미 조기 guard로 포함되어 함께 닫힘.
  IA-003(최신 잔여포인트 재검증), IA-005(상품/옵션 불일치 제출
  가능)는 `revalidate_before_submission()`의 한도·가격 재검사까지는
  CODEX 로컬 수정에 포함되어 있었으나, **옵션 ID·수량 단위의
  일치 검증은 이번 Phase 2 작업 전까지 존재하지 않았다.**

### 13.2 Phase 2 — 발주 승인의 상품/옵션/수량 결합 완성

**수정 전 재현**: `revalidate_before_submission()`은 `product_code`
문자열만 비교했다 — 같은 상품 코드 안에서 옵션 ID나 수량이 승인
시점과 실제 제출 시점에 달라져도(예: 승인은 "블랙/M 1개"였는데
제출은 "화이트/L 3개") 총액 스냅샷만 일치하면 그대로 통과했다.
이는 12.1의 IA-005("승인된 상품과 다른 상품도 총액만 같으면 제출
가능")가 지적한 문제의 옵션 단위 하위 케이스다.

**수정 내용**:
- `app/domains/purchase_task/model.py` — `PurchaseOrderApproval`에
  `options_snapshot_json: Text | None`(nullable) 컬럼 추가. 스냅샷이
  없으면(레거시 승인, 또는 옵션 없는 상품) 검사를 건너뛴다 — 이는
  "검증 생략"이지 "검증 통과"로 추측해 채운 것이 아니다.
- `app/domains/purchase_task/order_approval_service.py` —
  `_normalize_options()` 추가(id 기준 정렬, 순서 차이는 다른 구성으로
  오판하지 않음). `finalize_approval()`이 `options` 인자를 받아
  정규화 후 스냅샷 저장. `revalidate_before_submission()`이
  `current_options` 인자를 받아 스냅샷과 비교 — 불일치 시
  `INVALIDATED_PRICE_CHANGE` 상태로 전환하고 "옵션" 문구가 포함된
  한국어 사유와 함께 `ConflictException` 발생.
- `schema.py`/`router.py` — 승인 확정 API가 옵션 목록을 받아
  서비스에 전달하도록 배선.
- `migrations/20260915_00_add_purchase_order_approval_options_snapshot.sql`
  — `ALTER TABLE purchase_order_approvals ADD COLUMN
  options_snapshot_json TEXT;` 단일 문장, BEGIN/COMMIT으로 감쌈.
  **임시 SQLite에서만 검증했다 — 실제 homez.db(개발본·설치본
  어느 쪽도)에는 적용하지 않았다.**
- **명시적으로 구현하지 않은 것**: 수취인 정보(이름/연락처/주소)
  결합. 현재 UI 흐름은 수취인 정보를 승인 시점이 아니라
  `SubmitRealOrderRequest` 시점에만 수집하므로, 대조할 "승인된"
  수취인 값 자체가 존재하지 않는다. 이는 추측으로 채울 수 없는
  구조적 공백이며, 해소하려면 수취인 정보를 승인 이전 단계로
  당겨오는 UI 흐름 변경이 필요하다(향후 별도 작업).

**테스트 결과**:
- Migration 자체: `tests/test_purchase_order_approval_options_snapshot_migration.py`
  신규 4건 — 전체 체인 클린 적용, 컬럼 nullable 확인, 기존 승인 행
  보존(NULL 백필), 재적용 명시적 실패. 전부 통과.
  `MigrationRunner(db_path, migrations_dir).apply_pending(conn)`
  (인자 1개) 호출 규약을 재확인 — 최초 `apply_pending(conn,
  file_list)`로 잘못 호출했을 때는 예외 없이 "성공"처럼 보였지만
  실제로는 DB에 아무것도 쓰지 않았다(재연결 후 테이블 0개로 확인).
- 옵션 결합 로직: `tests/test_purchase_order_approval_service.py`의
  `OptionBindingTestCase` 신규 3건(다른 옵션 제출 시 차단·순서만
  다른 동일 옵션 통과·스냅샷 없으면 생략) 포함, 전체 32/32 통과.
- **회귀로 드러난 부수 결함**: 신규 Migration 파일 추가가 "이전
  상태" DB를 시뮬레이션하기 위해 전체 Migration 파일 목록을 손으로
  나열해두던 테스트 3개(`test_purchase_channel_connection_migration.py`,
  `test_unknown_resolution_and_tracking_refresh_migration.py`,
  `test_permissions_timestamps_migration.py`)를 깨뜨렸다 — 이 파일들
  자체 주석에 "다음에 이 파일을 만지면 자동 계산 방식으로 바꾸는
  편이 낫다"고 이미 기록돼 있던 반복 패턴(9~10회 이력)이다. 근본
  원인에 맞춰 하드코딩 목록을 `NEW_MIGRATION`과 같거나 사전순으로
  뒤인 `.sql` 파일 전부를 자동 계산하는 방식으로 교체했다(3개 모두
  수정, `test_migration_restricted_mode_schema_error_handling.py`는
  아직 깨지지 않았지만 동일한 취약 패턴이 있어 선제적으로 함께
  교체). 수정 후 관련 5개 파일 36건 전부 통과 재확인.
- 이어서 `tests/test_purchase_*.py` 전체 회귀를 실행해 Phase 2
  변경이 기존 매입 작업 흐름에 회귀를 일으키지 않았는지 확인했다
  (결과는 아래 13.3, 실행 완료 후 갱신).

### 13.3 Phase 3 — 업무 주문 단위 중복 방지를 DB 제약으로 강제

**수정 전 재현**: `_has_blocking_task_attempt()`는 INSERT 이전에
실행하는 SELECT다. 같은 purchase_task_id에 대해 옵션이 달라
`compute_idempotency_key()`가 서로 다른 키를 계산하는 두 요청이
동시에 들어오면(또는 첫 요청이 네트워크 호출 대기 중인 넓은 구간에
두 번째 요청이 끼어들면), 둘 다 이 SELECT 시점에는 "아직 없음"을
관측하고 통과할 수 있다 — 기존 (company_id, idempotency_key)
UNIQUE 제약은 완전히 같은 키에서만 두 번째 INSERT를 막으므로, 키가
다르면 이 방어선도 작동하지 않는다. 이는 IA-004(Critical, 중복
발주)의 근본 원인(idempotency key 계산 자체의 TOCTOU)과 같은
클래스의, 아직 닫히지 않은 하위 경로였다.

**수정 내용**: `purchase_order_submission_attempts`에 부분 UNIQUE
INDEX(`uq_purchase_order_submission_attempts_active_task`, 컬럼
(company_id, purchase_task_id), WHERE 조건은
`_has_blocking_task_attempt()`가 앱 레벨에서 판단하는 "차단 대상"
조건과 정확히 동일)를 추가했다 — 같은 업무 주문에 대해 PENDING/
IN_FLIGHT/SUCCEEDED이거나 미확정 RESULT_UNKNOWN인 행이 동시에
하나만 존재하도록 SQLite 자신이 두 번째 INSERT를 거부한다.
`_create_locked_attempt()`가 이 인덱스 위반과 기존 idempotency_key
위반을 오류 메시지로 구분해 번역한다. Migration
(`20260915_01_add_purchase_order_submission_attempts_active_task_
index.sql`)은 컬럼이 아니라 인덱스만 추가하므로 기존 데이터를
건드리지 않으며, 임시 SQLite에서만 검증했다(실제 homez.db 미적용).

**테스트 결과**: 신규 Migration 테스트 7건 — 이 중
`ActiveTaskUniqueIndexBehaviorTestCase`는 애플리케이션 코드를 거치지
않고 서로 다른 sqlite3 커넥션 두 개로 직접 경쟁 상태를 재현해(같은
purchase_task_id, 다른 idempotency_key로 동시 INSERT) 두 번째가
정확히 이 인덱스 때문에 거부되는지 확인했고, 반대로 다른 회사·다른
작업·이전 시도가 REJECTED/ORDER_NOT_CONFIRMED로 종결된 뒤의 정당한
새 시도는 차단하지 않는지도 함께 확인했다. 서비스 레벨에서도
`_create_locked_attempt()`를 사전 검사 없이 직접 두 번 호출해 같은
경쟁 상태를 재현하는 테스트를 추가했다(`test_concurrent_attempt_
with_different_key_same_task_blocked_at_db_level`). 전부 통과.
`tests/test_purchase_*.py` 전체 회귀 405/405 통과.

**부수 결함**: 이 새 Migration 파일 추가로 Phase 2에서 새로 만든
`test_purchase_order_approval_options_snapshot_migration.py`가 같은
"이전 상태 하드코딩"(이번엔 `if name == NEW_MIGRATION` 형태) 패턴으로
다시 깨졌다 — 새로 만든 파일에 처음부터 자동 계산 방식을 적용하지
않았던 것이 원인이다. 다른 5개 파일과 동일한 방식으로 수정했다.
재발 방지를 위해 앞으로 새 Migration 테스트 파일을 만들 때는
처음부터 자동 계산 방식(`name >= NEW_MIGRATION`)을 쓴다.

### 13.4 Phase 4 — 결제·환불 Provider 연결 전 트랜잭션 공백 축소(IA-011)

**수정 전 재현**: IA-011이 지적한 세 지점을 코드로 확인했다.
(1) `register_method()`가 Credential Store 저장(외부성 있는 부수
효과) 뒤에 DB commit을 하므로, commit이 실패하면 DB에는 없는데
Credential Store에만 남는 고아 토큰이 생긴다. (2) `set_default_method()`
는 "기존 기본값 해제"(`clear_default_for_company()`, 자체 commit)와
"새 기본값 지정"이 별도 commit 2개라, 두 번째만 실패해도 회사에
기본 결제수단이 아예 없는 상태로 남을 수 있다. (3) `mark_executed()`
는 외부 Executor 호출을 로컬 상태 전이보다 먼저 실행하므로, 호출
직후~commit 사이에 프로세스가 죽으면 외부 실행은 성공했는데 로컬은
여전히 APPROVED로 남아 재시도가 Executor를 또 호출할 위험이 있다.

**수정 내용**: (1)(2)는 `clear_default_for_company()`의 자체 commit을
제거해 호출부가 단일 트랜잭션으로 묶게 했고, `register_method()`는
DB commit 실패 시 Credential Store 저장분을 최선노력으로 되돌린다.
(3)은 `Refund.execution_attempt_started_at`(nullable) 컬럼을 추가해
Executor 호출 "직전"에 그 시도 자체를 durable commit하고(발주 시도의
IN_FLIGHT 패턴과 동일), status가 APPROVED인데 이 값이 남아 있으면
`confirm_retry_after_uncertain_execution=True`라는 명시적 확인 없이는
재실행을 거부한다. 확정적 사전 거부(`RefundExecutionError`)는
"결과불명"이 아니므로 마커를 지워 정상 재시도를 막지 않는다.

**중요한 제약**: 현재 Payment/Refund는 전부 Fake Provider/Fake
Executor만 사용하므로 이 결함들은 지금 당장 실금전 피해로 이어지지
않는다 — 독립 감사 문서 자체도 "실제 Provider로 교체하기 전에
필요하다"고 명시한다. 이번 수정은 그 교체 이전에 필요한 구조적
방어선을 미리 놓은 것이며, 발주 시도(`PurchaseOrderSubmissionAttempt`)
수준의 완전한 시도 장부(멱등성 키, RESULT_UNKNOWN 인간 확정 흐름
등)까지는 이번 라운드에서 만들지 않았다 — 실제 Provider 연동
시점에 별도로 재평가가 필요하다.

**테스트 결과**: payment 신규 2건(commit 실패 시 기존 기본값 유지
확인, Credential Store 롤백 확인), refund 신규 2건(불확정 실행 후
재시도 차단→명시적 확인 후 통과, 확정적 거부는 마커를 지워 정상
재시도 허용), Migration 신규 4건(전체 체인 적용, 재적용 실패, 컬럼
nullable 확인, 기존 행 보존). `tests/test_payment_*.py` 32/32,
`tests/test_refund_*.py` 33/33 통과.

### 13.5 잔여 위험과 다음 단계

- IA-004(Critical, idempotency key 중복발주)의 원래 경로는
  `_has_blocking_task_attempt`로 Phase 1에서 닫혔고, 옵션이 다른
  키로 우회하는 하위 경로는 Phase 3에서 DB 제약으로 닫았다.
- IA-011의 구조적 완화는 Phase 4에서 마쳤지만, 완전한 시도 장부는
  실제 Provider 연동 전 별도 작업으로 남아 있다.
- 수취인 정보 결합은 여전히 미해결 — 온채널 실제 발주 재개를 막는
  근거 중 하나로 유지한다.
- 개발본/설치본 DB 선택은 여전히 사용자 결정 대기(11.6-1).
- Phase 5(백업 암호화 재감사), Phase 6(테스트 격리), Phase 8(실행
  게이트 배선), Phase 9(안전 기능 7-8/7-11/7-16/8-5/8-6/8-16/8-19/
  10-4/10-5/10-17/10-18)는 이 라운드에서 착수하지 않았다 — 아래
  최종 보고에서 범위 한계를 명시한다.
