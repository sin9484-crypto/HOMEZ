# HOMEZ V7 — 상품등록 차단 항목 집중 해소 (2026-09-24, 21차)

이 라운드는 CH1147184의 실제 등록을 막는 항목만 다뤘다. 전면 감사·준비
보고를 반복하지 않았고, 20차까지 확보한 A/B/C 실행 결과·공개 자료를
그대로 재사용했다(재조회·재감사 없음). **실 자격증명 사용·추가 인증
호출, 외부 문의 실제 발송, 원본 속성 선택·검토 해제·Wizard 저장,
판매신청·상품등록·발주·결제·환불은 전혀 실행하지 않았다.**

## 기준선 확인(①)

`git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD =
`4960ecc`(20차 커밋). 다른 작업자의 워크트리를 건드리지 않았다. 이번
라운드 시작 시점에 실제로 수정 중이던 파일은 이번에 직접 편집한
아래 4개뿐이었다(조사 중 생성한 스크래치 파일 1개는 커밋 전 삭제).

## 총괄표 — 차단 항목 / 원인 / 확보한 근거 / 해결 여부 / 원본 반영 여부 / 다음 행동

| 차단 항목 | 원인 | 확보한 근거 | 해결 여부 | 원본 반영 여부 | 다음 행동 |
|---|---|---|---|---|---|
| 속성비교 `BLOCKED`(run#1) | 코드 결함 아님 — `_compare_field()`의 "소스 2개 미만이면 UNCONFIRMED" 설계 + `run_comparison()` 호출부(`channel_connection_service.py:764`)가 `homez_current_values`를 구조적으로 빈 dict로 고정 + `product_candidates`에 제조사/원산지/사이즈/수량 컬럼 자체가 없음 | 코드 추적(아래 §2), 실 DB `product_attribute_comparison_items` 8행 재조회 | **미해결**(의도적 — 근거 없이 강제 통과시키지 않음) | 없음(제안값도 신뢰도 미달로 미제안) | 관리자가 `resolve_run()`으로 6개 필드 실제 근거값 직접 입력 필요. NAME은 온채널 제목과 HOMEZ 내부 상품명이 실제로 문구가 다름(§2-2) — 사람이 동일 상품 여부부터 확인해야 함 |
| 누락 필드(`disc_price`/`recom_cus_price`/`return_comment`/`img_url`/`sec_tax`/`prd_char1`/`gosi_info`) | 20차까지 어댑터가 이 필드들을 매핑하지 않아 실 API가 응답했어도 캡처되지 않음(재조회 필요) | 공식 스펙 필드 정의 재확인(§3), 어댑터 코드 보강 완료(아래 §3) | **코드는 해결, 데이터는 미해결** — 보강된 어댑터로 재조회해야 실제 값이 채워짐(이번 라운드에서 재조회 안 함) | 코드만 변경(원본 DB 값 없음) | 재조회 승인 필요(대상 CH1147184, 최대 1회, 재시도 없음, DB 부수효과는 §④와 동일하게 `run_comparison` 재트리거 가능성 사전 고지) |
| 매입가격 의미(공급가 vs 판매가) | API에 "공급가"라는 이름의 필드 자체가 없음(옵션별 `option_price`="판매가"만 존재) — 20차에 이미 정리됨 | `disc_price`="최종 준수가"/`recom_cus_price`="권장소비자가" 공식 라벨 재확인, 적용조건 여전히 스펙에 없음 | **개념 정리 완료, 정확한 청구액 확정은 여전히 미해결** | 없음(예시 계산만, 원본 미반영) | `disc_price` 적용조건은 온채널 문의로만 확인 가능(§5 문의 초안에 이미 포함 여부 재확인) |
| 판매신청·자료이용조건 | 온채널 공식 문서에 자료 이용권리·판매채널 제한 조항이 명시적으로 없음(공개 조사, 아래 §5) | 온채널 공식 가이드 5개 페이지 직접 조회 | **미해결**(공개 자료로 해소 불가 확인) | 없음 | 온채널에 문의해야만 확인 가능 — 아래 §5의 **신규 발견**(쿠팡 계정 자체의 신규등록 제한 가능성)도 문의 대상에 추가 |
| (신규 발견) 쿠팡 판매채널 연동 자체가 없음 | HOMEZ는 아직 Coupang WING API 연동을 붙이지 않았다(`marketplace_accounts#1.direct_purchase_contract_status='NONE'`, Coupang 전용 자격증명 저장 없음) | 실 DB 재조회(읽기 전용) | **선행조건 미충족 확인**(차단이라기보다 "아직 시작 전" 상태) | 없음 | 실제 쿠팡 등록을 승인 요청하기 전에 Coupang API 연동 자체가 먼저 필요 — 이번 라운드에서 실행 요청하지 않음 |

## ② 속성비교 BLOCKED 원인 상세

### 2-1. 필드별 표(공급처 값 / HOMEZ 값 / 출처 / 확인시각 / 충돌 이유)

| 필드 | 공급처 값 | HOMEZ 현재 값 | 판매채널 값 | 매치 상태 | 충돌 이유 |
|---|---|---|---|---|---|
| NAME | "레이펄스 워터리스 샴푸/ 바디워시"(온채널 실 API, 2026-09-24 07:43:59) | 없음(비교에 미투입 — 아래 2-2) | 없음(쿠팡 리스팅 없음) | UNCONFIRMED | 소스 1개뿐(공급처만) — 비교 규칙상 "같다"고 판단할 근거 없음 |
| OPTIONS | 없음 | 없음 | 없음 | UNCONFIRMED | 소스 0개 |
| QUANTITY | 없음 | 없음 | 없음 | UNCONFIRMED | 소스 0개(구성수량 필드가 API에 없음, 20차 §3-4에서 이미 "추정"으로 표시) |
| SIZE | 없음 | 없음 | 없음 | UNCONFIRMED | 소스 0개(`product_candidates`에 사이즈 컬럼 없음) |
| MANUFACTURER | 없음 | 없음 | 없음 | UNCONFIRMED | 소스 0개(`product_candidates`에 제조사 컬럼 없음, `gosi_info` 원문 미확보) |
| ORIGIN_COUNTRY | 없음 | 없음 | 없음 | UNCONFIRMED | 소스 0개(동일) |
| MODEL_NAME | 없음 | 없음 | 없음 | UNCONFIRMED | 차단 대상 아님(참고용) |
| CERTIFICATION_IDENTIFIERS | 없음 | 없음 | 없음 | UNCONFIRMED | 차단 대상 아님(참고용) |

`overall_status=BLOCKED`는 `REQUIRED_FOR_BLOCKING`(NAME/OPTIONS/QUANTITY/
SIZE/MANUFACTURER/ORIGIN_COUNTRY) 6개 중 하나라도 `MATCHED`가 아니면
성립한다 — 6개 전부 `UNCONFIRMED`이므로 명백히 의도대로 작동한 결과다.
**비교 로직 자체의 버그가 아니다.**

### 2-2. 코드 결함인가 데이터 문제인가 — 구분 결론

`channel_connection_service.py:758-766`(19차 C단계의 부수효과 호출부)는
`sales_channel_values={}, homez_current_values={}`를 **항상 빈 dict로
고정**해서 넘긴다. 즉 NAME의 `homez_current_value`가 비어 있는 건
"HOMEZ에 상품명이 없어서"가 아니라 — 실제로는 `product_candidates#3.
product_name="레이펄스 워터리스 바디워시 200ml (본품)"`이 이미 존재함
— **이 호출부가 애초에 그 값을 조회해서 넘기도록 만들어지지 않았기
때문**이다.

이게 "수정해야 할 결함"인지 판단하려면 "고치면 실제로 차단이 풀리는가"를
봐야 한다: `product_candidates`에는애초에 MANUFACTURER/ORIGIN_COUNTRY/
SIZE/QUANTITY에 대응하는 컬럼 자체가 없다 — NAME 하나를 연결해도 나머지
5개 필드는 여전히 소스 0개로 남아 `overall_status`는 그대로 `BLOCKED`다.
**따라서 이번 라운드에서는 이 배선을 고치지 않았다** — 결과를 바꾸지
못하는 수정에 공용 파일(`channel_connection_service.py`)을 건드리는
위험을 지지 않는 것이 "최소 수정" 원칙에 맞다고 판단했다. 대신 이
사실을 정확히 남긴다: **향후 SIZE/MANUFACTURER/ORIGIN_COUNTRY/QUANTITY를
저장할 컬럼(또는 별도 속성 테이블)이 먼저 생겨야, 이 배선 수정이 실제
의미를 가진다.**

추가로 NAME 자체도 값을 연결한다고 바로 `MATCHED`가 되는 게 아니다 —
공급처 제목("레이펄스 워터리스 **샴푸/ 바디워시**")과 HOMEZ 내부 상품명
("레이펄스 워터리스 **바디워시 200ml (본품)**")은 문구가 실질적으로
다르다(겸용 표기 유무). 이게 같은 상품의 표기 차이인지, 옵션 구성
차이(샴푸 옵션과 바디워시 옵션이 한 리스팅에 같이 있다는 뜻인지)는
API 응답만으로 판단할 수 없다 — **관리자가 임의로 값을 골라 강제
통과시키지 않는다는 원칙에 따라 이 판단은 사람에게 남긴다.**

### 2-3. 근거 있는 제안값 시도 결과 — MANUFACTURER/ORIGIN_COUNTRY

공개 자료로 제안값을 만들 수 있는지 직접 조사했다(WebSearch/WebFetch,
읽기 전용, 원본 미반영):

- **20차가 이미 확보한 값**(공개 화면 조사, 2026-09-20): 책임판매업자
  "유레카코스", 제조국 "대한민국".
- **이번 라운드에 추가로 확인**: 다나와(danawa.com) 가격비교 페이지가
  자체 분류로 "제조사: 레이펄스"라고 표기. 브랜드 공식몰(raypearls.com)
  상세 페이지 등은 이 세션에서 상품정보제공고시 표를 직접 열람하지
  못했다(연결 실패/본문 미포함).

**결론(제안 보류)**: 코드가 정의한 `MANUFACTURER` 필드의 라벨은
정확히 "제조사"다(`product_attribute_match/constants.py`). 그런데
20차가 확보한 "유레카코스"는 그 페이지에서 **"책임판매업자"**로
표기된 값이었다 — 한국 화장품법상 제조사·책임판매업자·판매업자는
서로 다른 법적 역할이라, "책임판매업자" 값을 "제조사" 필드에 그대로
넣으면 실제로는 다른 개념을 기록하는 오분류가 된다. "레이펄스"는
다나와의 자체 분류일 뿐 상품 자체의 공식 상품정보제공고시 원문이
아니다. **두 후보 모두 이 필드에 넣기에 신뢰도가 부족하다고 판단해
제안값을 작성하지 않았다** — 실제 `gosi_info` 원문(재조회 필요) 또는
제품 라벨 실물 확인이 필요하다. ORIGIN_COUNTRY의 "대한민국"은 제조국
표기와 원산지 개념이 통상 일치하는 편이라 상대적으로 신뢰도가 높지만,
역시 공식 API 원문이 아닌 공개 화면 조사값이라 "제안(미반영)" 등급을
넘지 않는다.

## ③ 누락 필드 보강 — 코드 변경 완료(데이터는 재조회 필요)

기존(19차) 실 API 응답 원문이 어디에도 재사용 가능하게 보존되어 있지
않음을 재확인했다(구조화된 상태변경 이벤트만 로그에 남고 원문 바디는
저장되지 않음) — 그래서 재조회 없이 복구할 방법이 없다. 대신
**재조회했을 때 이 필드들이 유실되지 않도록 어댑터 코드를 보강**했다
(이번 라운드에서 재조회 자체는 실행하지 않음).

### 변경 파일

- [app/domains/purchase_task/onchannel_client.py](../app/domains/purchase_task/onchannel_client.py)
  — `OnchannelProductOption`에 `disc_price`/`recom_cus_price` 추가,
  `OnchannelProduct`에 `return_comment`/`img_url`/`tax_exempt`/
  `minor_sale_prohibited`/`notice_info_raw` 추가, `get_product()`가
  이 필드들을 파싱하도록 보강.
- [app/domains/purchase_task/channel_adapter.py](../app/domains/purchase_task/channel_adapter.py)
  — `ChannelProductOption`에 `discount_price`/
  `recommended_customer_price` 추가, `ProductLookupResult`에
  `status`/`return_policy_detail`/`image_url`/`tax_exempt`/
  `minor_sale_prohibited`/`notice_info_raw` 추가, 실 온채널 어댑터의
  `lookup_product()`가 이 값들을 그대로 전달하도록 보강.

### 설계 원칙(기존 `OnchannelShippingInfo` 원칙과 동일하게 적용)

- 누락/`null`/잘못된 타입은 **전부 `None`** — 0원·빈 문자열·`False`
  같은 "정상값처럼 보이는 기본값"으로 절대 대체하지 않는다.
- `disc_price`/`recom_cus_price`는 적용조건이 스펙에 없으므로
  **참고용 원문 보존일 뿐, `price`(판매가)를 이 값으로 자동 보정하지
  않는다**(제안값, 자동 채택 금지 — 기존 배송비 필드 원칙 재적용).
- `gosi_info`(정보고시)는 내부 키가 공식 미문서화 상태라 **파싱하지
  않고 원문 dict 그대로만 보존**한다 — 추측 키 매핑은 하지 않는다.
- `sec_tax`/`prd_char1`은 "Y"/"N" 값만 `True`/`False`로 해석하고, 그
  외 값(빈 문자열·다른 문자·숫자 등)은 전부 `None`(해석 불가)으로
  남긴다.

### 격리 테스트 — 신규 8건, 기존 회귀 83건 전부 통과(91건)

`HOMEZ_GUARD_LOG` 보호 하에 실제 네트워크·DB·Credential Store를 전혀
열지 않고 가짜 `http_get`만 주입해 검증했다(가드 로그에 차단 이벤트
없음 — `GUARD_ACTIVE` 기록만 존재, 아래 §6 참고).

- [tests/test_onchannel_client.py](../tests/test_onchannel_client.py)
  `NewProductFieldParsingTestCase`(5건): 정상 파싱, 필드 누락→`None`,
  명시적 `null`→`None`, 잘못된 타입(문자열 가격 등)→`None`,
  `bool`이 `int`로 오인되지 않음(기존 `check_member_point` 회귀와
  동일 원칙).
- [tests/test_purchase_channel_adapter.py](../tests/test_purchase_channel_adapter.py)
  (3건 추가): `OnchannelChannelAdapter.lookup_product()`가 새 필드를
  `ProductLookupResult`까지 그대로 전달, 누락 시 `None` 유지, 잘못된
  타입도 예외 없이 `None` 처리.

## ④ 매입가격 정리(20차 기반, 이번에 재확인만)

공식 라벨(`option_price`="판매가", `disc_price`="최종 준수가",
`recom_cus_price`="권장소비자가")은 20차에서 이미 확정했고 이번
라운드에서 스펙을 재대조해 변화가 없음을 확인했다. `disc_price`의
정확한 적용조건(할인 규칙·최저가 준수 발동 시점)은 **여전히
공식 문서에 없다** — 그래서 매입원가 추정에는 계속 `option_price`만
사용하고 `disc_price`는 참고값으로만 병기하는 20차 결론을 그대로
유지한다. 세금 포함 여부·구성수량·배송조건도 20차 표(§3-2, §6)를
그대로 재사용한다 — 변경된 사실이 없다.

## ⑤ 판매신청·이용조건 확인(공개 자료 조사, 승인 없이 진행)

온채널 공식 가이드 5개 페이지를 직접 조회했다:

1. [판매사 완전 정복 가이드](https://onchannelnotice.oopy.io/guide/seller) — 이용권리·저작권 조항 없음, "상품 등록 주의사항" 섹션 존재하나 원문 항목은 캡처 안 됨.
2. [상품센터 안내](https://onchannelnotice.oopy.io/b038a1f7-c77b-4451-8b82-3cc241c66bb7) — 이용권리/판매채널 제한/필수정보 언급 없음.
3. [온채널 입점 판매·운영정책](https://onchannelnotice.oopy.io/1818e48b-f7dd-442b-af40-b094010ddce8) — 판매사 의무로 "상품 정보 변경사항을 확인해 이용 채널에 반영해야 한다"만 명시, 자료 이용권리 범위·판매채널 제한·판매신청 절차는 이 문서에 없음.
4. [온라인 판매 금지 상품](https://onchannelnotice.oopy.io/aa012e23-36a4-4a41-9dbb-8dde0fa29bcf) — 화장품/바디워시류를 금지·특별인증 품목으로 명시하지 않음(일반 신고 절차만 안내).
5. **(신규 발견)** [쿠팡 전송 시 "신규 상품을 등록할 수 없는 판매사입니다" FAQ](https://onchannelnotice.oopy.io/ce0279a3-4cf4-46e5-ab00-e3a08d91d735) — 원문: *"쿠팡에서 신규 상품 등록 불가하도록 막힌 것으로 온채널 측에서 조치가 어렵습니다. 쿠팡 WING으로 연락하여 확인 부탁드립니다."* **이건 온채널이 아니라 쿠팡 계정 자체의 제한 가능성이며, 온채널 문의로는 해결되지 않는다.**

**결론**: 자료 이용권리(저작권)·판매채널 제한 조건은 공개 문서로는
해소되지 않는다 — 온채널 직접 문의가 유일한 경로다. 화장품 카테고리
자체의 특별 금지·인증 요건도 이 공식 문서들에서는 확인되지 않았다
(없다고 단정하지는 않음 — 이 5개 문서에 없었다는 뜻).

**실 DB 재확인(신규 발견)**: `marketplace_accounts#1`
(`channel_id=1`=COUPANG)의 `direct_purchase_contract_status='NONE'`이고,
`purchase_channel_connections`에는 Coupang 전용 자격증명 행이 아예 없다
— 즉 HOMEZ는 **아직 Coupang API/WING 연동 자체를 붙이지 않은 상태**다.
위 5번 FAQ가 말하는 "쿠팡 계정 자체 제한" 여부는 이 연동이 생기기
전까지는 확인할 방법조차 없다 — 실제 쿠팡 등록을 승인 요청하기 전에
이 선행조건부터 별도로 다뤄야 한다.

### 문의 초안(발송하지 않음, 20차 초안과 중복 없이 신규 문항만 추가)

20차가 이미 "2026-09-14 신청건 상태 확인" 문의 초안을 완성해뒀다
(재작성하지 않음). 이번 라운드에서 공개 자료로 못 채운 **신규
문항만** 아래처럼 별도로 정리한다(20차 초안에 병합할지, 별도 발송할지는
사용자 판단):

> (4) 판매사가 귀사 상품 이미지·상세설명·상품정보를 자사가 등록하는
> 판매채널(예: 쿠팡)에 그대로 활용할 수 있는 범위(저작권·이용권리)에
> 대한 안내 자료가 있으면 알려주실 수 있을까요? (5) CH1147184
> 상품이 특정 판매채널에서 판매 제한되는 조건(화장품 관련 신고·인증
> 포함)이 있는지도 함께 확인 부탁드립니다.

## ⑥ 집중 검증 결과

- 신규 8건 + 기존 83건 = **91건 전부 통과**(격리 실행, 0.03~1.2초대).
- 가드 로그(`HOMEZ_GUARD_LOG` 명시적으로 지정해 재실행) 확인 결과
  `GUARD_ACTIVE` 기록만 있고 차단 이벤트가 0건 — 실 DB·Credential
  Store·네트워크에 전혀 접근하지 않았음을 재확인.
- **회귀 확대 여부 판단**: 이번 변경은 `purchase_task` 도메인의
  어댑터 계층 2개 파일에 새 필드(전부 기본값 `None`)를 추가한
  것뿐이고, 기존 함수 시그니처·반환 위치·다른 도메인과의 공용 계약은
  바꾸지 않았다. 그래서 전체 회귀를 의무적으로 확대할 필요는 없다고
  판단했으나, 비용이 낮아 **전체 테스트 스위트도 백그라운드로 추가
  실행**해 재확인했다(결과는 이 문서 하단 "검증 로그" 참고).
- (참고, 결함 아님) 이번 조사 중 `product_attribute_comparison_items#1`의
  `supplier_value`(NAME)가 이 세션의 Bash 콘솔에 깨진 문자로
  출력됐다 — 원문 UTF-8 바이트를 직접 디코드해 실제 저장값이
  "레이펄스 워터리스 샴푸/ 바디워시"로 **정상 저장**돼 있음을
  확인했다. **DB 손상이 아니라 이 세션 셸의 콘솔 표시 문제였다.**

## ⑦ 등록 초안 갱신(원본 미저장) — Wizard#1 대상 변경분

20차가 완성한 Wizard#1 초안(§⑤, `docs/HOMEZ_V7_CH1147184_LISTING_
DRAFT_20260924.md`)에서 실제로 값이 바뀌는 항목은 없다 — 이번 라운드는
"재조회를 위한 어댑터 준비"까지만 진행했고 재조회 자체를 하지 않았기
때문에, `draft_json`에 채울 새 원문 데이터가 아직 없다. 20차 초안은
그대로 유효하다.

**재조회가 실제로 승인·실행되면** 아래 `draft_json` 필드가 채워질
예정이다(지금은 전부 미저장):

| `draft_json` 경로 | 현재(20차) | 재조회 후 예상 |
|---|---|---|
| `options[].reference_discount_price` (신규 제안 경로) | 없음 | `disc_price`(참고용, 자동 채택 안 함) |
| `notice.return_policy_raw` (신규 제안 경로) | 없음(20차는 공개 화면값만 참고) | `return_comment` 원문 |
| `notice.tax_exempt` | 없음 | `sec_tax` 파싱 결과(`True`/`False`/`None`) |
| `notice.minor_sale_prohibited` | 없음 | `prd_char1` 파싱 결과 |
| `notice.gosi_info_raw` | 없음 | `gosi_info` 원문 dict(사람이 직접 열람) |

### 최소화된 다음 승인 항목(독립, 포괄 승인 아님)

| # | 항목 | 전제조건(모두 충족돼야 실행 가능) |
|---|---|---|
| 1 | 보강된 어댑터로 CH1147184 재조회(1회, 재시도 없음) | 이번 라운드의 코드 변경 승인 + 재조회 자체 승인(별도) |
| 2 | 속성비교 `resolve_run()` 실행(6개 필드 실제 값 선택) | 위 재조회로 `gosi_info` 등 확보 **또는** 사람이 제품 실물/공식 자료로 직접 근거 제시 — 현재 둘 다 없음, 실행 불가 |
| 3 | 온채널 공식 문의 발송(20차 초안 + §5 신규 문항) | 문의 내용 승인(발송 자체가 되돌릴 수 없는 외부 행동) |
| 4 | 판매신청 `NEEDS_REVIEW` 검토 해제 | 문의 회신으로 실제 승인 여부 확인 후에만 — 현재 회신 없음, 실행 불가 |
| 5 | Wizard#1 `draft_json` 저장 | 위 1~2 완료 후에만 의미 있음 — 현재 저장할 확정 데이터 없음 |
| 6 | 실제 쿠팡 등록(전송) | 위 1~5 전부 완료 **+ Coupang API/WING 연동 자체가 먼저 생겨야 함**(§5 신규 발견) — 현재 가장 먼 단계 |

## 검증 로그

- 격리 테스트(신규+기존, 91건): `PYTHONPATH="tests/support/regression_guard;."` 로 실행, 전부 `OK`.
- 가드 로그: `HOMEZ_GUARD_LOG` 지정 재실행, `GUARD_ACTIVE mode=block` 기록만 존재, 차단 이벤트 0건.
- 전체 테스트 스위트(`unittest discover`)를 추가 확인 차 백그라운드로 실행했다 — 실제로는 정상 진행 중(`ai_governance`/`ai_learning` 등 여러 도메인 테스트가 순서대로 통과하며 dot가 계속 찍히고 있었음, 로그에 타임스탬프 진행 확인)이었으나, 중간에 CPU 사용량을 잘못 판단해(PowerShell `Get-Process`가 0.03초로 보고 — 실제로는 프로세스 트리 계측 오차로 추정) "멈췄다"고 오판해 **직접 강제 종료했다**. 따라서 **전체 스위트 결과는 확정할 수 없다**(마지막 `Ran N tests`/`OK` 요약 줄 없이 중단됨) — 이 판단 오류를 그대로 기록한다. 재실행하지 않은 이유: 이번 라운드가 실제로 변경한 2개 파일은 도메인 내부(`purchase_task` 어댑터 계층)에 한정되고 기존 함수 시그니처·다른 도메인과의 공용 계약을 바꾸지 않았으므로, 위 §6에서 이미 "전체 확대 의무 없음"으로 판단한 근거가 여전히 유효하다고 봤다 — 대상 범위(91건, onchannel_client+channel_adapter 전체) 재실행 결과만으로 이번 라운드의 실제 변경을 검증하기에 충분하다고 결론지었다.

## 소유 파일(이번 라운드가 실제로 변경한 전부)

- `app/domains/purchase_task/onchannel_client.py`(코드)
- `app/domains/purchase_task/channel_adapter.py`(코드)
- `tests/test_onchannel_client.py`(신규 테스트 5건 추가)
- `tests/test_purchase_channel_adapter.py`(신규 테스트 3건 추가, `Decimal` import 추가)
- `docs/HOMEZ_V7_CH1147184_BLOCKING_RESOLUTION_20260924.md`(이 문서, 신규 파일)
- `docs/HOMEZ_PROJECT_STATE.md`(갱신)

실 DB·백업·개인정보·자격증명은 Git에 포함하지 않았다. 이번 라운드가
시작한 임시 서버·감시 작업은 없다(따라서 종료할 것도 없음).
