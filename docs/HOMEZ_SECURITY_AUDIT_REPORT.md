# HOMEZ V6.5 — 보안 감사 보고서 (Gate Z-4, 2026-08-12)

이 문서는 Gate X(V6.5 완성도 감사)와 Gate Y(운영 기반)에서 실제로
확인·수정된 보안 관련 사실만 요약한다. 각 항목의 전체 조사 과정과
증거는 `docs/V6_EXECUTION_LEDGER.md`의 해당 Gate 절이 원본이다 —
이 문서는 그것의 요약이며, 상충하는 내용이 있으면 Ledger가 우선한다.

## 1. 인증/인가 전수 감사 (Gate X-1, X-2A/B, X-4)

- **발견**: 기능 인벤토리 조사(Gate X-1) 중 인증 없이 접근 가능한
  레거시 CRUD 라우터를 발견 — Critical 1건(`/role-permissions`,
  `/users`)과 후속 조사로 8건이 추가 발견됐다.
- **조치**: Gate X-2A/B에서 해당 라우터 전부 제거하고, 제거 후에도
  같은 기능이 필요하면 인증된 정식 라우터로만 접근 가능함을
  검증했다.
- **재감사**: Gate X-4에서 실제 마운트된 **181개 route**를 전부
  다시 훑어, 공개(로그인 불필요) allowlist **19개**와 비공개
  **162개** 전부가 인증 `Depends`를 갖고 있는지 재확인했다. 이
  재감사는 정적 의존성 분석이며, 그것만으로 실제 HTTP 응답 검증을
  대체할 수 없다는 점이 Gate X-4 자체에 명시돼 있다 — 그래서 Gate
  X-5가 이어졌다.

## 2. 실제 브라우저 E2E 역할별 접근 검증 (Gate X-5)

임시 DB + Fake Provider로 실제 브라우저에서 5개 역할(Anonymous,
SUPER_ADMIN, Manager, Staff, Viewer) × 7개 뷰포트를 검증했다.
확인된 것: 역할별 메뉴 가시성, 로그인 후 첫 진입 화면, 서버측
403 판정이 클라이언트 UI 숨김보다 항상 우선한다는 것(즉 UI에서
버튼을 숨겨도 서버가 별도로 다시 막는다), 중복 클릭 방지, 언어
전환 시 상태 보존.

## 3. 전체 회귀 + 실제 DB 무결성 (Gate X-6)

- 전체 회귀 **1371개 전부 통과**.
- 실제 `homez.db` SHA-256 해시가 Gate X 시작~종료 완전히 동일(쓰기
  없음) — 보안 감사·수정 작업 자체가 운영 데이터를 건드리지
  않았음을 해시로 증명.
- **Gate X 판정**: `GATE_X_SECURITY_COMPLETE_FEATURE_GAPS_REMAIN`
  (보안 항목은 완료, 기능 갭은 Gate Y/V7 몫으로 명시적으로 분리).

## 4. Gate Y에서 발견·즉시 수정된 결함 1건

**알림 센터 회사 공지 읽음 상태 공유 결함**(설계 검토 중 발견,
구현 완료 전에 재설계로 해결 — 프로덕션에 노출된 적 없음): 초안
설계는 회사 전체 공지(`Notification.user_id IS NULL`)의 읽음
여부를 그 행 자체의 `is_read` 컬럼에 직접 썼다. 이 경우 한
사용자가 공지를 읽으면 같은 회사의 **다른 모든 사용자**에게도
즉시 "읽음"으로 보이는 결함이 된다(정보 누출은 아니지만, 명백한
기능 오류이자 잠재적으로 중요한 공지를 다른 사용자가 놓치게 만들
수 있는 문제). 별도 `NotificationRead`(사용자별 개별 행) 테이블로
즉시 재설계했고, 이 정확한 시나리오를 재현하는 회귀 테스트
(`test_mark_read_broadcast_does_not_leak_to_other_user`)를 추가했다.

## 5. 비밀정보 노출 방지 (Gate Y-5)

`app/domains/diagnostics`의 진단 내보내기 기능을 만들면서, 로그
tail에 실수로 찍혔을 수 있는 실제 비밀값(SECRET_KEY,
JWT_SECRET_KEY, REFRESH_SECRET_KEY, API_KEY_SECRET,
PASSWORD_PEPPER, REDIS_PASSWORD, SMTP_PASSWORD, SMS_API_KEY,
SMS_API_SECRET)이 절대 결과에 포함되지 않도록 이중 방어선(실제
런타임 값 verbatim 치환 + 일반 key=value 패턴 정규식)을 만들고,
9개 필드 각각을 개별적으로 로그에 심어 전부 지워지는지 확인하는
전용 테스트로 증명했다.

## 6. 알려진 미해결 위험 (임의로 "해결"로 표기하지 않음)

- **`test_role_permission_admin.py::test_concurrent_updates_
  exactly_one_succeeds`**: 이 세션 초반에 63회 재현 시도(60회
  격리 스트레스 테스트 + 3회 이전 재실행) 전부 무결함으로 나왔고,
  Gate X-6의 "정확히 1회" 전체 회귀에서도 정상 통과했다. **확정적
  근본 원인을 코드 결함으로 특정하지 못했다** — 사용자 지시에
  따라 "해결됨"으로 선언하지 않고, 부하 하 타이밍 잡음(Gate U-5C와
  동일 계열)으로 잠정 분류만 했다. 이후 Gate Y-7의 1451개 전체
  회귀에서도 이 테스트는 정상 통과했지만, 근본 원인 미확정이라는
  분류 자체는 바뀌지 않는다.
- **`app/domains/audit`, `app/core/audit.py`**: 둘 다 죽은/미마운트
  레거시 코드로 확인됐다(`docs/HOMEZ_FEATURE_INVENTORY.md` 3절).
  보안 위험은 아니지만(실행 경로에 없음), 저장소에 혼란을 주는
  죽은 코드로 남아있다 — 삭제는 별도 승인 대상.
- **레거시 `app/service`, `app/services`, `app/marketplace`,
  `app/api` 트리**: ADR 0001에서 이미 "레거시, 도달 불가능"으로
  확인됐다. 이번 Gate Z 재확인에서도 `app/main.py`가 이들을
  import하지 않음을 재확인했다(`docs/HOMEZ_FEATURE_INVENTORY.md`
  방법론 절 참고).
- **테스트 커버리지 공백 5건**: `order`, `supplier`, `category`,
  `brand`, `shipment` — 전부 실제 API로 마운트돼 있지만 전용 테스트
  파일도, 다른 테스트의 간접 참조도 없다(`docs/
  HOMEZ_FEATURE_INVENTORY.md` 1절). 보안 감사 범위는 아니지만,
  회귀 안전망이 없는 상태로 운영되고 있다는 사실은 기록해야 한다.

## 7. 결론

이번 세션(Gate X~Z) 동안 발견된 보안 관련 Critical/High 결함 중
**미해결로 남은 것은 없다.** 위 6절의 항목들은 "보안 결함"이
아니라 "확정 불가 위험" 또는 "테스트 공백" 범주이며, 그렇게
구분해서 기록했다 — 실행하지 않은 검증을 통과로 기록하지 않는다는
원칙을 그대로 따랐다.
