# HOMEZ V7 민감정보 보존·접근권한 정책 (2026-08-30)

V7 후속 안정화 지시 Phase 4 산출물. 이 문서는 **정책 결정**을 기록한다
— 코드 자체의 구현 상태는 별도로 "확인 상태" 열에 명시한다(정책이
있다고 전부 구현됐다는 뜻이 아니다).

## 대상 데이터와 등급

| 데이터 | 등급 | 근거 |
|---|---|---|
| Access/Refresh Token 원문 | Critical | 탈취 시 계정 전체 장악 |
| Credential(쿠팡 Access/Secret Key, R2 Secret) | Critical | 탈취 시 외부 계정 장악·과금 |
| A/S 전화번호(companyContactNumber) | High | 개인정보(연락처) |
| 출고지·반품지 주소(returnAddress 등) | High | 개인정보(위치) |
| raw_response / raw_response_snippet | Medium | 위 항목이 우연히 섞여 들어올 수 있는 비구조 텍스트 |
| audit_logs.description | Medium | 위와 동일한 이유 — 사람이 자유 문자열을 채우는 지점 |
| UI 오류 상세(내부 코드·스택) | Low~Medium | 직접적 개인정보는 아니나 내부 구조 노출 |
| 자동화 스크린샷(Browser 자동화 등) | High | 화면 전체가 그대로 캡처되어 위 항목을 모두 포함할 수 있음 |

## 보존 기간

- **Access/Refresh Token 원문**: 어떤 저장소에도 보존하지 않는다(서버는
  Refresh Token의 sha256 해시만 보관 —
  app/domains/session/model.py::RefreshToken.token_hash). 원문은
  Windows Credential Manager(Desktop) 또는 브라우저 메모리에만,
  각 토큰 자신의 만료 시각까지만 존재한다.
- **raw_response_snippet**: 현재 `LiveSubmissionResult`/
  `ProductStatusResult` 값 객체에만 존재하고 DB에 영구 저장하는 코드
  경로는 이번 세션 기준으로 확인되지 않았다(호출자가 그 값을 그대로
  audit_log description에 옮겨 적는 지점—listing_wizard_live_
  service.py—만 실제 영구화 지점이다). **정책**: audit_logs 자체의
  보존 기간은 이번 세션에서 별도로 정하지 않는다(기존 감사 도메인
  전체의 일반 보존 정책을 따른다 — 이 문서 범위 밖). 다만 그 안에
  들어가는 원문은 Phase 4에서 추가한 `redact_free_text()`를 거친
  뒤에만 기록되도록 이미 연결했다(coupang_live_provider.py::_snippet()).
- **A/S 전화번호·주소**: `required_fields`(listing_wizards.channel_
  selections_json) 자체에는 실제 제출에 필요한 원문이 그대로
  저장된다(쿠팡에 실제로 보내야 하는 값이므로 마스킹 대상이 아니다).
  **정책**: 이 원문 자체의 보존 기간은 위저드/제출 기록 전체의
  보존 기간을 따른다(별도 단축 정책 없음) — 다만 화면·로그·자동화
  스크린샷으로 "다시 내보낼 때"만 이 문서의 마스킹 규칙을 적용한다.

## 접근 권한

- **원문 그대로 볼 수 있는 경우**: 실제 쿠팡 제출을 위해 그 값을
  입력·확인해야 하는 운영자 본인의 위저드 화면(8단계 승인 미리보기
  등)뿐이다 — 이건 마스킹 대상이 아니다(값을 확인해야 승인할 수
  있으므로).
- **마스킹돼야 하는 경우**: 그 값이 원래 목적과 무관한 곳(오류 로그,
  일반 상태 조회 화면, 자동화 스크린샷, 이 세션이 새로 만든
  submission_reconciliation 감사 사유 등)에 "부수적으로" 노출될 때.
- 이번 세션은 `app/core/sensitive_data.py`에 4개 마스킹 함수
  (mask_phone/mask_address/mask_secret/redact_free_text)와 허용
  필드 기반 dict 필터(redact_dict)를 만들었다 — **아직 애플리케이션
  전체에 걸쳐 적용을 완료하지 않았다**(아래 "확인 상태" 참고). 이번에
  실제로 연결한 지점은 `coupang_live_provider.py::_snippet()` 하나뿐이다.

## 자동화 스크린샷

이번 세션(및 그 이전 세션)에서 Browser 자동화로 8단계 승인 미리보기
화면을 캡처하다 실제 A/S 전화번호가 스크린샷에 그대로 노출된 사례가
반복적으로 발생했다(대화 기록에 명시적으로 남아 있음). **정책**: 화면
자체의 설계(승인 확인을 위해 원문을 보여줘야 함)를 바꾸지 않는 한,
자동화 도구 사용자가 그 단계에서는 전체 화면 캡처 대신 `get_page_text`
+ 구조화 조회로 필요한 항목만 읽는 습관으로 방어한다 — 코드 레벨
강제(예: CSS로 화면에서만 가리고 스크린샷에서도 가리는 것)는 이번
세션에서 구현하지 않았다(그러면 운영자 본인도 승인 시점에 값을 확인할
수 없게 된다 — 화면 자체의 마스킹은 이 정책의 범위 밖으로 남긴다).

## 확인 상태 (이번 세션 기준)

| 항목 | 상태 |
|---|---|
| 중앙 마스킹 함수 | 구현·테스트 완료(app/core/sensitive_data.py, tests/test_sensitive_data_masking.py) |
| raw_response_snippet에 적용 | 완료(coupang_live_provider.py::_snippet()) |
| audit_logs 전체에 허용 필드 기반 필터 적용 | **미실행** — write_audit_log() 호출부가 많아(7곳 이상) 이번 세션에서 전수 적용하지 못했다 |
| UI 오류 상세 분리(운영자 상세보기 vs 일반 화면) | **미실행** — 기존 화면 대부분이 이미 오류 코드를 감춘 요약 메시지만 보여주고 있으나, "상세보기" 전용 뷰를 명시적으로 만들지는 않았다 |
| 자동화 스크린샷 마스킹 | **미실행**(위 설명 — 운영 습관으로만 대체) |
| 보존기간 정책 | 이 문서로 최초 문서화(코드 강제 없음 — 정책 텍스트만) |
