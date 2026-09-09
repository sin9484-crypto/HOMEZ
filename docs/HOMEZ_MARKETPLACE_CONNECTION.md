# HOMEZ 판매채널 연결(StoreConnection) — 설계 문서

- 상태: Phase 1 구현 + 회사 간 격리 보안 보완 완료(fixture 검증
  기준). 실제 쿠팡·네이버 API 연결은 아직 수행되지 않음.
- 관련 CTO 승인: PHASE_0_APPROVED / PHASE_1_IMPLEMENTATION_AUTHORIZED
  (2026-07-30) → PHASE_1_REJECTED_FOR_REMEDIATION(2026-08-01, 회사 간
  IDOR 발견) → 보안 보완 완료, CTO 재검토 요청 중
- 작성일: 2026-07-30, 갱신: 2026-08-01(회사 소유권 격리)

## 1. 목표

판매자가 HOMEZ Desktop에서 이미지 안내를 보며 쿠팡·네이버 스마트스토어의
공식 API 자격증명을 설정하고, fixture 환경에서 안전하게 연결을 검증할
수 있게 한다. 이번 Phase는 실제 판매채널 연결이나 실제 상품 등록을
수행하지 않는다.

## 2. 공식 문서 매핑

| 채널 | 공식 문서 | 확인일 | 필요한 값 |
|---|---|---|---|
| 쿠팡 | https://developers.coupang.com/ (WING Open API) | 2026-07-30(개요), **2026-08-01(Gate 3 — 서명 방식 확정)** | 업체코드, Access Key, Secret Key |
| 네이버 스마트스토어 | https://apicenter.commerce.naver.com/ (직접 fetch 계속 실패) | 2026-07-30, **2026-08-01(Gate 3 재시도, 여전히 실패)** | Client ID, Client Secret |

### 2-1. 쿠팡 — Gate 3에서 확정된 사항(2026-08-01, 같은 날 2차 수정)

공식 1차 문서(전부 developers.coupang.com, WebFetch로 직접 확인)에서
아래 사실을 확정했다 — `app/domains/store_connection/adapters/
coupang_signing.py`에 그대로 구현했다:

| 항목 | 확정 값 | 출처 |
|---|---|---|
| 인증 서명 방식 | HMAC-SHA256 | `/hc/en-us/articles/360033461914-Creating-HMAC-Signature` |
| datetime 형식 | `yyMMddTHHmmssZ`(UTC) | 위 문서 |
| 서명 대상 메시지 | `datetime + method + path + query`(구분자 없이 이어붙임 — PHP/Python/C# 3개 예제로 교차 확인) | 위 문서 |
| Authorization 헤더 형식 | `CEA algorithm=HmacSHA256, access-key={accessKey}, signed-date={datetime}, signature={hexdigest}` | 위 문서 |
| Content-Type | `application/json;charset=UTF-8` | 위 문서 |
| 업체코드/Access Key/Secret Key 발급 | WING 로그인 → 판매자정보 → OPEN API 키 발급 약관 동의 → 발급(하단에 업체코드/Access Key/Secret Key 노출) | `/hc/en-us/sections/20299721603481-Issue-API-Key` 계열 |
| 키 만료·재발급 | 발급/재발급 시점부터 **180일(6개월)** 유효, 만료 후 재발급 필요, 재발급 자체는 가능(단, 재발급 시 기존 연동 도구에 새 값 반영 필요) | `/hc/en-us/articles/27482151049625-How-many-days-is-the-key-valid-for-OpenAPI`, `/hc/en-us/articles/20300466525593-...` |
| **반품 조회 엔드포인트 버전** | **`api/v6`**(v4 아님) | `/hc/en-us/articles/360033919613-Return-Cancellation-Request-List-Query` |
| **필수 쿼리** | `searchType=timeFrame`, `createdAtFrom`/`createdAtTo`(`yyyy-MM-ddTHH:mm`), `status`(예: `UC`) | 위 문서, 공식 예제 URL 포함 |
| 확인된 오류 코드 | 400(잘못된 요청), 403(IP 미허용 — `[FORBIDDEN] Not allowed IP`), 404 | `/ko/faq/a-403-forbidden-error-occurs` 등 |

**2026-08-01 2차 수정(CTO 재지적 반영)**: 1차 구현이 `api/v4` 경로와
`searchType`/`status` 없는 쿼리를 썼던 것을 발견해 공식 문서를 다시
직접 fetch로 재확인, `api/v6` + `searchType=timeFrame` + `status=UC`로
정정했다. 서명 query와 실제 요청 URL의 query가 절대 어긋나지 않도록
`urllib.parse.urlencode()`로 딱 한 번만 만든 문자열을 양쪽에 그대로
재사용하도록 재작성했고, 이 동일성을 실제로 서명을 재계산해 증명하는
테스트를 추가했다(`test_signed_query_and_request_url_query_are_
byte_for_byte_identical`). 400/401/403/412/429/5xx/timeout을 전부
서로 다른 정규화 코드로 분리했고(신규 `ConnectionErrorCode.
BAD_REQUEST`/`PRECONDITION_FAILED` 추가), 이 API가 문서화된 리다이렉트
동작이 없다는 점에 근거해 3xx 응답은 host와 무관하게 전부 즉시
차단한다(`_BlockRedirectHandler` — Authorization이 실린 서명된 요청이
예기치 않은 목적지로 재전송되는 것을 구조적으로 막는다).

**미확정(추측하지 않고 남김)**: 정확한 호출 제한(rate limit) 수치와
412의 정확한 발생 조건은 공식 문서에서 찾지 못했다(412는 다른 코드와
구분해 표시만 하고, 원인을 단정하지 않는다).

### 2-2. 네이버 — production adapter 구현, 여전히 1차 문서 미확인(2026-08-01)

Gate 3(2차)에서 `apicenter.commerce.naver.com`과
`api.commerce.naver.com` 양쪽 모두 WebFetch로 재차 직접 접근을
시도했으나 이번 세션에서도 동일하게 실패했다("Claude Code is unable
to fetch from ..."). **따라서 이 알고리즘은 1차 문서로 직접 확인된
것이 아니다.**

사용자가 제시한 스펙("공식 문서 2.83.0 기준")과, 네이버가 직접 운영
하는 공식 GitHub Discussion 저장소(`github.com/commerce-api-naver/
commerce-api`)를 포함한 다수의 독립적인 2차 소스가 동일하게 수렴하는
내용을 근거로 `app/domains/store_connection/adapters/
naver_signing.py` + `naver_production.py`를 구현했다:

| 항목 | 내용(2차 소스 교차확인, 1차 문서 아님) |
|---|---|
| 인증 방식 | OAuth2 Client Credentials |
| client_secret_sign | `Base64(bcrypt(client_id + "_" + timestamp_ms, salt=client_secret))` — client_secret 자체가 bcrypt salt 형식으로 발급된다고 알려짐 |
| timestamp | 밀리초 epoch, 약 5분 유효 |
| token endpoint | `POST https://api.commerce.naver.com/external/v1/oauth2/token`, `application/x-www-form-urlencoded` |
| token 유효기간 | 약 3시간 |
| 재발급 정책 | 401 + 응답 코드에 `GW.AUTHN` 포함 시 timestamp를 새로 계산해 1회만 재시도(무한 재시도 아님) |

bcrypt는 이 프로젝트 venv에 이미 설치되어 있었다(passlib 종속성,
2026-08-01 확인 — **신규 패키지 설치 아님**, "설치되어 있지 않으면
차단하고 보고하라"는 조건은 발생하지 않았다). production adapter는
fixture와 명확히 분리했고 `get_adapter()` 레지스트리에는 연결하지
않았다 — 실제 서비스 경로는 여전히 fixture만 쓴다. **1차 문서 접근이
가능해지면 반드시 재확인이 필요한 잔존 위험이다.** 실제 연결 단계에서
NAVER 상태를 READY로 표시하지 않는다(2026-07-30 결정 유지).

## 3. 도메인 구조

```
app/domains/store_connection/
  constants.py              MarketplaceCode, ConnectionStatus, ConnectionErrorCode
  model.py                   StoreConnection (SQLAlchemy, FK 없음)
  schema.py                   Pydantic 요청/응답 + 채널별 Credential 필드
  repository.py               no-commit CRUD + 조건부 UPDATE(+rowcount)
  service.py                   StoreConnectionService
  router.py                    FastAPI 라우터(admin_guard)
  verification_token.py       HMAC 서명 stateless 토큰(Secret 비영속)
  adapters/
    base.py                    공통 인터페이스
    coupang.py                 쿠팡 fixture adapter(실제 서비스 경로가 사용)
    naver.py                   네이버 fixture adapter(실제 서비스 경로가 사용)
    coupang_signing.py         쿠팡 HMAC-SHA256 서명(공식 문서 확정, 순수 함수, 네트워크 없음)
    coupang_production.py      쿠팡 실제 연결 확인 adapter(api/v6, Gate 3 — 아직 get_adapter()에 미연결)
    naver_signing.py           네이버 client_secret_sign(bcrypt, 2차 소스 교차확인, 네트워크 없음)
    naver_production.py        네이버 실제 OAuth2 토큰 발급 adapter(Gate 3 — 아직 get_adapter()에 미연결)

app/core/windows_credential_store.py   Win32 Credential Manager 래퍼
```

## 4. 연결 테스트 ↔ 저장 분리

```
1. 사용자가 자격증명 입력
2. 서버 형식 검증(Pydantic Strict Schema, extra="forbid")
3. fixture 연결 검증(adapters/*.verify_connection)
4. 성공 시 credential fingerprint를 HMAC 서명한 verification_token 발급
   (Secret 자체는 어디에도 저장하지 않음 — stateless)
5. 화면에 성공 상태 표시
6. 입력값이 바뀌면 fingerprint가 달라져 verify_token()이 거부
   (app/domains/store_connection/verification_token.py)
7. 성공 상태(유효한 token)일 때만 저장 가능
8. 저장 시 서비스가 fixture 검증을 다시 한 번 수행(defense-in-depth)
   → Windows Credential Manager에 저장 → DB에 reference/마스킹 힌트
     저장 → 감사 로그
```

## 5. Credential 저장 순서와 실패 복구

- **생성**: Credential Manager 저장 성공 → DB INSERT. DB INSERT
  실패 시 방금 저장한 Credential을 보상 삭제(compensating delete).
  Credential Manager 저장 자체가 실패하면 DB는 애초에 건드리지 않는다
  (fail-closed).
- **교체(rotate)**: 새 target_name으로 저장 → 조건부 UPDATE(낙관적
  동시성, `credential_version` 일치 확인) → 성공 후에만 이전
  target_name을 정리 삭제. UPDATE가 실패(동시 rotate 경쟁)하면 방금
  저장한 새 Credential을 보상 삭제하고 이전 Credential은 그대로 둔다.
- **비활성화(disable)**: 상태만 DISABLED로 전이, Credential은 그대로
  유지(재활성화를 염두에 둔 설계 — 이번 Phase에는 재활성화 API는
  없음, 필요 시 후속 Gate).
- **Credential 삭제**: `confirm=true` 명시적 확인 필수. DB에서
  `credential_reference`를 NULL로 바꾼 뒤(commit) Credential Manager
  에서 실제 삭제. DB 커밋 이후 삭제이므로, 실제 Credential Manager
  삭제가 실패해도 DB는 이미 일관된 상태(참조 없음)이고, 저장소에는
  고아 Credential만 남는다(로그로 기록, 수동 정리 필요 — Low 위험으로
  다음 섹션에 기록).

## 6. 동시성

- 생성: `(company_id, creation_idempotency_key)` 복합 UNIQUE +
  `(company_id, marketplace_code, seller_identifier)` UNIQUE. 두
  UNIQUE 제약 중 하나라도 위반되면 IntegrityError를 잡아 기존 행을
  반환하거나 ConflictException을 던진다(tests/
  test_store_connection_concurrency.py에서 실스레드+별도 DB
  connection으로 검증, 회사 간 교차 케이스는 tests/
  test_store_connection_tenant_isolation.py). idempotency key가
  같아도 요청 본문이 다르면 기존 행을 반환하지 않고 409를 던진다(아래
  7-1절).
- 교체: `credential_version` 낙관적 동시성 — 조건부 UPDATE의
  rowcount가 0이면 경쟁에서 진 것으로 간주하고 ConflictException.

## 6-1. idempotency 요청 본문 fingerprint(2026-08-01 CTO 2차 재심사)

**수정 전 결함**: `create()`가 같은 (company_id, creation_idempotency_
key)의 기존 행을 발견하면 지금 들어온 요청 내용과 전혀 비교하지 않고
그대로 반환했다(`service.py` 수정 전 196~206행: `if existing is not
None: return existing, True`). 클라이언트가 이미 성공한 idempotency
key를 다른 marketplace_code/seller_identifier/display_name/
Credential로 재사용해도 오류 없이 "성공"으로 위장된 엉뚱한 기존 행을
그대로 돌려받는 조용한 데이터 불일치였다 — 회사 간 격리와는 별개의,
같은 회사 안에서 발생하는 결함이다.

**수정**: `StoreConnection` Model과 미적용 Migration에
`creation_request_fingerprint VARCHAR(64) NOT NULL`을 추가했다. 새
파일 `app/domains/store_connection/idempotency_fingerprint.py`가
company_id/marketplace_code/seller_identifier/display_name과
credential_fields의 canonical fingerprint(Secret 원문이 아니라 그
SHA-256 다이제스트)를 canonical JSON으로 묶은 뒤,
`settings.SECRET_KEY`에서 파생한 전용 도메인 키(다른 도메인 문자열
사용, verification_token의 서명 키와 공유하지 않음)로 HMAC-SHA256
서명해 64자 hex 다이제스트를 만든다. Secret 원문도 canonical JSON도
반환값에 없다 — 다이제스트만 저장·비교된다.

`create()`는 credential_fields 형식 검증 직후(=idempotency 재호출
판단보다 먼저) 이번 요청의 fingerprint를 계산한다. 같은 idempotency_
key의 기존 행이 있으면: fingerprint가 같으면 진짜 재호출(replay)로
보고 기존 행을 반환하고, 다르면 `ConflictException`(409)으로 명시적
거부한다. `_insert_connection()`의 IntegrityError 경쟁 승자 복구
경로도 동일하게 winner의 fingerprint를 재검사해, 다르면 winner를
반환하지 않고 409를 던진다(같은 key를 우연히 동시에 다른 내용으로 쓴
경쟁에서도 안전).

**검증**: `tests/test_store_connection_idempotency_fingerprint.py`(신규,
9개 — 완전 동일 재호출은 기존 행 반환, marketplace/seller_identifier/
display_name/Credential 중 하나만 달라도 409, 다른 회사는 같은 key
재사용 가능, 동시 동일 요청은 fingerprint 일치로 안전 병합, 동시
다른 요청은 승자 1건+패자 409, DB·응답·audit_log에 Secret 원문 없음).
Model↔Migration DDL 일치는 기존 `tests/
test_store_connection_migration.py::test_migration_matches_
sqlalchemy_model_ddl`이 컬럼 추가를 자동으로 함께 검증한다.

## 7. 회사 소유권 격리(2026-08-01 CTO 재심사 보완)

**수정 전 결함**: 단건 조회·변경 API(`GET /store-connections/{id}`,
`POST .../verify`, `POST .../rotate-credential`, `POST .../disable`,
`DELETE .../credential`)가 `connection_id`만으로 조회했다 — Router가
`current_user.company_id`를 Service에 전달하지 않았고, Repository의
조회도 id 단독 조건이었다. 회사 A 관리자가 회사 B의 connection_id를
추측/열거하면 회사 B의 연결 정보 조회·상태 변경·Credential 교체·삭제가
가능했다(전형적 IDOR).

**수정 지점**:
- `repository.py`: 유일한 단건 조회 진입점을
  `get_connection_for_company(id, company_id)`로 통일(company_id 없는
  조회 메서드를 이 파일에서 완전히 제거). `apply_verification_success_
  conditional`/`apply_verification_failure_conditional`/
  `disable_conditional`/`delete_credential_conditional` 4개 조건부
  UPDATE 전부 `WHERE id = :id AND company_id = :company_id`를
  포함(조회만 회사 범위이고 UPDATE는 id만 쓰는 절반짜리 수정을 하지
  않음).
- `service.py`: `get`/`verify_existing`/`rotate_credential`/`disable`/
  `delete_credential`이 전부 `company_id`를 필수 인자로 받고,
  `get_connection_for_company()` 실패 시 다른 회사 소유든 진짜
  존재하지 않든 완전히 동일한 `NotFoundException`(404)을 던진다(403이나
  상세 메시지로 존재를 알려주지 않음).
- `router.py`: 6개 엔드포인트 전부 `current_user.company_id`를 Service
  호출에 전달하도록 수정.
- `creation_idempotency_key`: 전역 UNIQUE → `(company_id,
  creation_idempotency_key)` 복합 UNIQUE로 변경(설계 근거: 전역
  UNIQUE는 서로 다른 회사가 우연히/의도적으로 같은 idempotency key를
  쓰면 IntegrityError 복구 경로가 한 회사의 행을 다른 회사의 "idempotent
  재호출 결과"로 잘못 반환하는 데이터 유출 경로였다). Migration이 아직
  실제 homez.db에 적용된 적이 없어(재확인 완료) 신규 파일이 아니라
  기존 `migrations/20260730_02_create_store_connection_schema.sql`을
  그 자리에서 수정했다.
- `verification_token.py`: 토큰 payload에 `company_id`와 무작위
  `jti`를 추가. `verify_token()`이 호출자의 company_id와 토큰 발급
  당시 company_id가 다르면 즉시 거부한다. TTL을 10분 → 3분으로
  단축했다.

**검증**: `tests/test_store_connection_tenant_isolation.py`(신규, 9개
시나리오 — 회사 A가 회사 B의 연결을 GET/verify/rotate/disable/delete
시도하면 전부 404, 실패 시 회사 B의 DB row와 Credential Store가 완전히
불변임을 스냅샷 비교로 확인, 회사 A 토큰을 회사 B create()에 쓰면
거부, 같은 idempotency key를 양쪽 회사가 써도 교차 응답 없음, 실패
응답에 Secret 원문 없음) + `tests/test_store_connection_concurrency.py`
에 추가된 "경쟁 요청에서도 회사 격리 유지" 시나리오(실스레드로 회사
A/B가 동시에 같은 key로 생성 시도해도 서로 섞이지 않음).

## 8. 남은 위험(Low, 이번 Phase 범위 밖)

1. 네이버 실제 서명 인증 방식 미확인(위 2번 섹션) — 실제 연결 전 반드시
   재확인 필요. **NAVER 실제 연결 상태는 READY로 표시하지 않는다.**
2. Credential 삭제 이후 Windows Credential Manager 쪽 실제 삭제가
   실패하면 고아 Credential이 남을 수 있음(DB는 일관 상태 유지,
   `logger.error`로만 기록) — 수동 정리 절차 문서화 필요.
3. 계정 재활성화(연결 비활성화의 역방향) API는 이번 Phase에 없음.
4. 안내 이미지는 PNG가 아니라 SVG다(Pillow 등 래스터 라이브러리가
   설치되어 있지 않고 신규 패키지 설치가 금지되어 있어 결정) — 사용자
   승인 완료(2026-07-30, "SVG로 대체 제작").
5. `app/web/router.py`에 안내 이미지 서빙용 라우트 1개를 추가함(원래
   Whitelist 밖이었으나 사용자 승인 완료, 2026-07-30).
6. **verification_token 단발성(single-use) 소비 미구현**(2026-08-01
   재심사 요청 사항 중 일부만 반영) — company_id 바인딩과 jti는
   추가했지만, jti를 "이미 소비됨"으로 서버에 기록하는 완전한 단발성
   구조는 신규 영속 저장소가 필요해 이번 보완 범위 밖으로 판단하고
   구현하지 않았다. 대신 TTL을 10분→3분으로 단축했다. 잔존 위험: 3분
   이내 같은 토큰의 반복 제출 자체는 막지 못한다(다만 생성/교체 경로
   모두 별도의 UNIQUE/낙관적 동시성 보호가 있어 실질 피해는 제한적).
   이 항목을 "완료"로 보고하지 않는다.

## 9. 다음 Gate

- CTO 재검토(PHASE_1_READY_FOR_CTO_REVIEW 요청 중) 승인 이후:
  - 실제 homez.db에 Migration 적용(백업+무결성 확인 선행)
  - 네이버 실제 서명 인증 방식 1차 문서 재확인
  - 실제 Credential Manager에 사용자가 직접 Secret 입력(Claude는 Secret을
    보거나 다루지 않음)
  - 실제 쿠팡/네이버 인증 서버 호출(fixture adapter를 실제 adapter로 교체)
  - (선택) verification_token 완전한 단발성 소비 구조 구현 여부 결정
