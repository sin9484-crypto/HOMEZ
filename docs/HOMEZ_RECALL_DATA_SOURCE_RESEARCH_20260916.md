# HOMEZ 리콜/판매중지 공식 데이터 출처 조사 (2026-09-16)

- 작성 배경: 개인 베타 진입 전 잔여 작업 Phase 4 (10-17)
- 조사 방법: 공식 문서 페이지(`WebFetch`)만 사용. 실제 API 키 발급·
  실제 데이터 호출은 전혀 수행하지 않았다(공통 규칙 3·5·14 준수).
- 관련 코드: [`app/domains/recall_notice/provider.py`](../app/domains/recall_notice/provider.py),
  [`app/domains/recall_notice/mfds_provider.py`](../app/domains/recall_notice/mfds_provider.py)

## 결론 요약

| 후보 | 상태 | 상업적 이용 | 스펙 확인 수준 |
|---|---|---|---|
| 식약처(MFDS) 식품 회수·판매중지 정보 (I0490) | **1순위 — 스펙 완전 확인, Provider 코드 작성 완료** | 가능(이용허락범위 제한 없음) | 요청 URL·모든 파라미터·전체 18개 응답 필드 확인 |
| 산업부 국가기술표준원(KATS) 제품안전인증·리콜 정보 (#15116894) | 2순위(후속 조사 필요) — 일반 공산품 커버 | 가능(이용허락범위 제한 없음) | 필드명(한글)만 확인, 요청 URL 패턴·영문 키·페이징 방식 미확인 |
| 공정위·소비자원, 판매채널 자체 API | 미조사(이번 Phase 범위 밖) | — | — |

두 후보 모두 "실제 API 키 등록이나 실제 호출은 수행하지 않는다"는
공통 규칙에 따라 **키를 신청하지 않았다.** 아래는 문서로만 확인한
내용이다.

---

## 1. 식약처(MFDS) 식품의 회수 및 판매중지 정보 — I0490

- 공공데이터포털 데이터셋 ID: `15074318`
- 식품안전나라(foodsafetykorea.go.kr) 서비스 ID: `I0490`
- 문서 확인 URL: `https://www.foodsafetykorea.go.kr/api/openApiInfo.do?menu_grp=MENU_GRP31&menu_no=661&show_cnt=10&start_idx=1&svc_no=I0490&svc_type_cd=API_TYPE06`

### 요청 URL 패턴

```
http://openapi.foodsafetykorea.go.kr/api/{인증키}/I0490/{json|xml}/{시작 idx}/{종료 idx}
```

### 요청 파라미터

| 파라미터 | 필수 | 설명 |
|---|---|---|
| keyId | 필수 | 발급받은 인증키 |
| serviceId | 필수 | `I0490` (고정) |
| dataType | 필수 | `json` 또는 `xml` |
| startIdx / endIdx | 필수 | 조회 결과 범위(페이징) |
| CRET_DTM | 선택 | 등록일(YYYYMMDD) |
| PRDLST_REPORT_NO | 선택 | 품목제조보고번호 |
| CHNG_DT | 선택 | 변경일(YYYYMMDD) |

### 응답 필드 (18개, 공식 문서 명시 영문 키 그대로)

`PRDTNM`(제품명) · `RTRVLPRVNS`(회수사유) · `BSSHNM`(제조업체명) ·
`ADDR`(업체주소) · `TELNO`(전화번호) · `BRCDNO`(바코드번호) ·
`FRMLCUNIT`(포장단위) · `MNFDT`(제조일자) ·
`RTRVLPLANDOC_RTRVLMTHD`(회수방법) · `DISTBTMLMT`(유통소비기한) ·
`PRDLST_TYPE`(식품분류) · `IMG_FILE_PATH`(제품사진URL) ·
`PRDLST_CD`(품목코드) · `CRET_DTM`(등록일) ·
`RTRVLDSUSE_SEQ`(회수판매중지일련번호) ·
`PRDLST_REPORT_NO`(품목제조보고번호) · `RTRVL_GRDCD_NM`(회수등급) ·
`PRDLST_CD_NM`(품목유형)

### 갱신주기 / 이용조건

- 갱신주기: 상시
- 비용: 무료
- 이용허락범위: 제한 없음(상업적 이용 가능)
- 개인정보: 응답 필드에 개인정보로 보이는 항목 없음(업체 정보는
  사업자 정보로 개인정보 아님)

### 키 발급 절차 (신청만 문서화 — 미신청)

1. 식품안전나라(foodsafetykorea.go.kr) 회원가입
2. `/api/newUserApiAplcDtl.do?menu_grp=MENU_GRP32&menu_no=3995`
   페이지에서 "OpenAPI 이용신청" 진행
3. 승인 후 발급되는 인증키를 `MfdsRecallNoticeProvider(api_key=...)`
   생성자에 전달(Credential Manager 경유 — 평문 저장 금지, 기존
   [공급처·매입·구매실행 모델](../.claude 메모리 참고) 원칙과 동일)

### Provider 코드 준비 상태

`app/domains/recall_notice/mfds_provider.py`에 실제(미호출)
`MfdsRecallNoticeProvider` 클래스 작성 완료:

- `OnchannelApiClient`(`app/domains/purchase_task/onchannel_client.py`)와
  동일한 `http_get` 주입 패턴 — 테스트는 Fake 콜러블만 사용, 실제
  `requests`는 주입하지 않을 때만 지연 import됨.
- `parse_mfds_response()` — 위 18개 필드 중 확인된 필드만 파싱,
  필수로 취급하는 두 필드(품목식별자·회수사유) 중 하나라도 없는
  행은 건너뜀(추측 채움 없음).
- 파싱 로직은 `tests/test_recall_notice_mfds_provider.py`에서
  스펙과 동일한 구조의 합성 JSON으로만 검증(실제 서버 응답을 한
  번도 관측한 적 없음 — 실제 키로 최초 1회 호출 후 필드가 문서와
  다를 가능성은 열려 있다).
- `get_real_provider()`는 여전히 이 클래스를 반환하지 않는다.

---

## 2. 산업통상부 국가기술표준원(KATS) 제품 안전인증 및 리콜 정보

- 공공데이터포털 데이터셋 ID: `15116894`
- 문서 확인 URL: `https://www.data.go.kr/data/15116894/openapi.do`
- 커버 범위: 식품이 아닌 일반 공산품(전기용품·생활용품 등) — HOMEZ
  취급 상품군과 더 넓게 겹칠 가능성이 있는 후보.

### 확인된 내용

- 응답 필드(한글명만 확인, 영문 키 미확인): 제품명 / 모델명 /
  인증번호 / 인증기관 / 제조수입사 / 리콜사유 / 조치내용 / 공표일
  — "모델명"이 Adapter의 신규 `model_name` 필드와 직접 대응.
- 이용허락범위: 제한 없음(상업적 이용 가능)
- 포맷: JSON + XML
- 심의유형: 개발단계 자동승인 / 운영단계 심의승인

### 미확인 사항 (후속 조사 필요)

- 요청 URL 패턴(엔드포인트 경로)
- 영문 필드 키
- 페이징 파라미터 이름
- 갱신주기

**따라서 KATS는 이번 Phase에서 Provider 클래스를 작성하지 않았다**
— MFDS(I0490)처럼 "요청/응답 스펙 완전 확인" 상태에 아직 도달하지
못했기 때문이다(추측으로 코드를 작성하지 않는다는 원칙).

---

## 3. 조사하지 않은 후보 (이번 Phase 범위 밖)

- 공정거래위원회 / 한국소비자원 리콜 정보 — 데이터셋 존재 여부만
  인지, 상세 조사는 후속 Phase 대상.
- 쿠팡 등 판매채널 자체의 "회수/판매중지 안내" API — 채널사 공식
  Open API 문서에 리콜 전용 엔드포인트가 있는지 별도 확인 필요.

## 4. 다음 실행에 필요한 사용자 승인

1. MFDS(I0490) 인증키 발급 신청(식품안전나라 회원가입 포함) 여부
   승인
2. 발급된 인증키를 Credential Manager에 등록하는 절차 승인
3. `get_real_provider()`를 `MfdsRecallNoticeProvider`로 연결하고
   실제 키로 최초 1회 호출해 응답 스펙을 재검증하는 작업 승인
4. (선택) KATS 후보의 상세 스펙 후속 조사 진행 여부
