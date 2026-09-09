# HOMEZ V7 "Live Gate" 계획 — V6.5와의 명시적 경계 선언

- 작성일: 2026-08-12 (Gate Z-2)
- 목적: "V6.5에서 무엇을 안 했는지"를 흐릿하게 남기지 않고, 항목
  단위로 명시해 V7에서 무엇부터 시작해야 하는지 분명히 한다.

## 원칙

V6.5(Gate 0~Z)는 **실제 외부 시스템에 아무것도 쓰지 않는다**는
원칙으로 진행됐다 — 실제 결제, 실제 마켓 등록/주문, 실제 이메일/
SMS 발송, 실제 코드 서명, 실제 배포가 전부 여기 해당한다. 이
경계를 넘는 순간부터가 V7 "Live Gate"다.

## V7에서 처음 실행될 항목 (V6.5에서는 코드만 있거나 아예 없음)

### A. 외부 마켓플레이스 실거래

- **실제 쿠팡/네이버 상품 등록 API 호출**: `app/domains/coupang`,
  `app/domains/marketplace_listing`의 adapter들은 실제 API 스펙에
  맞춰 구현돼 있지만, 이번 세션 포함 지금까지 실제 판매자 계정으로
  실제 등록 요청을 보낸 적이 없다(전부 Fake Provider/임시 DB).
- **실제 주문 수신/배송 상태 동기화**: `app/domains/order`,
  `app/domains/shipment`가 실제 마켓 웹훅/폴링을 받은 적 없음.

### B. 결제/정산

- `app/domains/settlement`, `app/domains/funding`은 모델·계산
  로직까지 구현돼 있지만, 실제 은행 API·실제 마켓 정산 데이터
  연동은 없다(`CLAUDE.md`의 "은행 API를 사용하지 않는다" 원칙과
  일치 — Funding Account는 애초에 실제 은행 잔액이 아니다).
- `app/domains/payment`는 `EMPTY_SCAFFOLD`(코드 없음). V7에서
  실제 결제 게이트웨이 연동이 필요하면 여기서 시작한다.

### C. 실제 발송

- `app/domains/email`, `app/domains/sms`: 둘 다 `EMPTY_SCAFFOLD`.
  실제 SMTP/SMS 게이트웨이 연동은 V7에서 처음 구현된다.
- `app/core/config.py`의 `SMTP_PASSWORD`, `SMS_API_KEY`,
  `SMS_API_SECRET` 필드는 이미 선언돼 있지만(Gate Y-5 진단
  redaction이 이 3개를 포함해 다룸) 실제 값이 설정된 적 없다.

### D. 인증/연동

- `app/domains/oauth`, `app/domains/webhook`: `EMPTY_SCAFFOLD`.
  써드파티 로그인·외부 웹훅 수신이 필요해지면 V7에서 시작.

### E. 서명된 업데이트 매니페스트 자동 검증

- 설계는 `docs/adr/0002-update-manifest-signing-design.md`에 이미
  확정(Ed25519, 위협 모델, 7단계 검증 절차). **실제 HTTP 클라이언트
  코드, 실제 서명 키 발급, 실제 배포 서버는 V7에서 처음 만들어진다.**
  `app/domains/update`는 지금은 관리자 수동 등록만 지원한다.

### F. 실제 패키징·서명·배포

- `homez.spec`(Gate Y-6)은 작성됐지만 **한 번도 실행되지 않았다**
  — `dist/Homez/` 산출물 자체가 존재하지 않는다.
- 코드 서명 인증서(`docs/PACKAGING_CODE_SIGNING_PLAN.md`가 EV
  인증서를 권장) 발급·서명·타임스탬프는 전부 V7 몫.
- 설치 프로그램(Inno Setup 등) 자체가 아직 없다.
- 언인스톨 시 `%LOCALAPPDATA%\HOMEZ\*` 보존/삭제 정책이 아직
  확정되지 않았다(계획 문서에 잠정 판단만 기록됨).

### G. 실제 DB 복원 실행

- `app/domains/restore`의 `RestoreService.restore()`는 완성돼
  있고 임시 DB로 전부 검증됐지만, **실제 운영 homez.db를 대상으로
  한 복원 실행 API는 이번 세션에서 의도적으로 노출하지 않았다**
  (`app/domains/restore/router.py`, `test_no_execute_restore_
  endpoint_exposed_yet`으로 고정). V7에서 별도 명시적 승인과 함께
  이 엔드포인트를 열 것인지 결정한다.

### H. 자동 백업 스케줄링

- Gate Y-1은 "지금 백업을 생성"하는 수동 트리거(`POST /backups`)
  만 구현했다. 주기적 자동 백업(스케줄러)은 구현되지 않았다 —
  `app/domains/scheduler`도 `EMPTY_SCAFFOLD`다.

### I. 실제 사업자 정보 연동

- 이 세션에서 다루지 않은 나머지 63개 `EMPTY_SCAFFOLD` 도메인
  (`docs/HOMEZ_FEATURE_INVENTORY.md` 4절 참고) 중 실제 사업 운영에
  필요한 항목(재고 소싱 자동화, 트렌드 발굴 자동 실행 등)은 V7에서
  우선순위를 다시 매겨야 한다 — 이번 V6.5 범위에는 없었다.

## V6.5에서 이미 끝난 것 (V7이 다시 손댈 필요 없음)

- 인증/Permission/Role 기반 접근 제어(Gate 1~X 전체).
- 회사(company) 단위 데이터 격리(대부분의 MOUNTED 도메인).
- Migration 안전 적용 절차(`MigrationRunner`, 백업+검증+rollback
  계획).
- 백업 생성·검증, 복원 검증(실행 제외), 알림 센터, 수동 업데이트
  공지, 진단 내보내기, 패키징 스펙 초안 — 전부 Gate Y에서 완료.

## 판단 기준 (V7 착수 시 재확인할 것)

V7의 어떤 항목이든 시작하기 전에 다음을 먼저 확인한다:

1. 실제 사업자 계정/API 키/인증서가 실제로 발급·확보됐는가.
2. 그 항목이 실제 금전(결제/정산/환불)에 영향을 주는가 — 그렇다면
   `CLAUDE.md`의 금융 정합성 원칙(net_amount 불일치 등은 항상 생성
   차단)을 그대로 적용해야 한다.
3. 실제 외부 호출 1건이라도 발생하기 전에, 반드시 임시/Fake
   Provider 경로로 먼저 전체 흐름을 검증했는가.
