# HOMEZ V6.5 — 운영 기반 보고서 (Gate Z-4, 2026-08-12)

Gate Y에서 구현한 백업/복원/알림/업데이트/진단 기능을 실제
운영자가 어떻게 쓰는지 요약한다. 전부 `admin_guard`(SUPER_ADMIN/
ADMIN) 전용이며, 실제 HTTP 호출은 이 세션에서 한 번도 하지 않았다
(코드는 완성, 임시 DB로만 테스트) — 아래는 API 계약 설명이지,
실제 운영 이력이 아니다.

## 백업

- `POST /backups` — 지금 즉시 실제 운영 DB(`homez.db`)의 온라인
  백업을 만든다. 요청 바디는 `label`(선택, 메모용) 하나뿐이다.
  백업 파일은 `%LOCALAPPDATA%\HOMEZ\backups`(패키징 모드) 또는
  `storage/backups`(개발 모드)에 저장된다.
- `GET /backups` — 백업 이력(최근 50개, 파일 경로/크기/SHA-256/
  무결성 검사 결과/누가·언제 만들었는지) 조회.
- **무결성이 `ok`가 아니면 그 백업은 이력에 아예 기록되지 않는다**
  — "백업이 있는 줄 알았는데 못 쓰는 상태"인 상황을 만들지 않기
  위함이다. 이 경우 API가 500 오류를 반환하므로, 반복 실패 시
  디스크 공간·권한을 먼저 확인해야 한다.

## 복원

- `POST /restores/validate` — 특정 백업 이력(`backup_record_id`)이
  지금도 실제로 복원 가능한 상태인지(파일 존재/SHA-256 일치/
  무결성 검사) **읽기 전용으로만** 확인한다.
- `GET /restores` — 복원 시도 이력(성공/실패 모두, 실패도 감사
  대상이라 기록됨).
- **"지금 실제로 복원을 실행하는" API는 아직 없다.** 엔진
  (`RestoreService.restore()`)은 완성돼 있고 검증도 끝났지만,
  운영 DB를 대상으로 한 실행 경로는 이번 세션에서 별도 승인
  없이는 열지 않기로 결정했다(`docs/V7_LIVE_GATE_PLAN.md` G절
  참고). 실제로 복원이 필요한 상황이 오면, 이 결정을 먼저
  재검토해야 한다.

## 알림

- `GET /notifications`, `GET /notifications/unread-count` — 로그인한
  사용자 누구나(admin 아니어도) 자기 알림(개인 알림 + 소속 회사
  공지)을 본다.
- `POST /notifications/{id}/read`, `POST /notifications/read-all` —
  읽음 처리(몇 번 눌러도 안전).
- **다른 도메인이 알림을 보내려면**: `NotificationService.
  notify_user(company_id, user_id, ...)` 또는 `notify_company(
  company_id, ...)`를 직접 호출한다. 지금은 백업/복원/진단 어떤
  도메인도 자동으로 알림을 발송하지 않는다 — 배선은 아직 안 됐다
  (예: "백업 실패 시 관리자에게 자동 알림" 같은 연결은 향후
  작업).

## 업데이트 공지

- `GET /updates/status` — 로그인한 사용자 누구나 "지금 버전보다
  새 버전이 있는지" 확인.
- `POST /updates/notices`, `POST /updates/notices/{id}/deactivate`,
  `GET /updates/notices` — admin 전용, 새 버전 공지 수동 등록/취소/
  이력 조회.
- **자동으로 새 버전을 감지하지 않는다.** 새 버전을 배포하면
  관리자가 그때마다 `POST /updates/notices`를 직접 호출해야 한다.
  자동 감지는 V7 몫(`docs/adr/0002-update-manifest-signing-
  design.md`).

## 진단 내보내기

- `GET /diagnostics/export` — admin 전용, 앱 버전/OS/DB 무결성/
  Migration 상태/라우트 수/로그 tail(비밀정보 제거됨)을 한 번에
  JSON으로 받는다. 고객지원·장애 조사 시 사용자에게 이 결과를
  요청하면 된다 — **비밀정보가 절대 포함되지 않는다는 것이
  전용 테스트로 증명돼 있으므로, 이 결과를 외부로 공유해도
  안전하다**(단, 로그 tail에 회사 고유 정보가 담겨 있을 수는
  있으니 그 자체의 민감도는 사용자가 판단해야 한다 — 비밀
  "값"만 지운다).

## 운영자가 지금 당장 할 수 있는 것 / 할 수 없는 것

| 할 수 있는 것 | 할 수 없는 것(V7 몫) |
|---|---|
| 지금 백업 만들기 | 자동 주기 백업 |
| 백업이 유효한지 확인 | API로 실제 복원 실행 |
| 사용자에게 인앱 알림 보내기(코드로) | 알림 자동 발송 배선 |
| 새 버전 공지 수동 등록 | 자동 업데이트 확인/서명 검증 |
| 진단 번들 내보내기 | — |
| 빌드 스펙으로 실행 파일 만들기(수동, 미실행) | 서명된 배포 실행 파일 |
