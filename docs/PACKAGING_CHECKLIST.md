# HOMEZ Desktop 패키징 체크리스트 (Gate Z-5, 2026-08-12)

실제 배포 실행 파일을 만들기 전 확인할 목록. 이번 세션에서는
아래 항목 중 어느 것도 실행하지 않았다 — `homez.spec`/
`requirements-build.txt`/`docs/PACKAGING_CODE_SIGNING_PLAN.md`만
준비됐다. V7에서 실제 빌드를 시작할 때 이 체크리스트를 순서대로
따라간다.

## 빌드 전

- [ ] `requirements.txt`의 모든 버전이 실제 운영 환경(Windows,
      Python 버전)에서 재현 가능한지 clean venv로 재확인
      (Gate F-10A가 이미 이 방식으로 Pillow 누락을 찾아낸 선례
      참고).
- [ ] `requirements-build.txt`(`pyinstaller==6.16.0`)를 별도 venv에
      설치.
- [ ] `python -m unittest discover -s tests` 전체 통과 확인(빌드
      직전 기준선).
- [ ] 실제 `homez.db`가 빌드 작업 디렉터리에 절대 없는지 확인
      (`homez.spec`의 datas에 DB 파일이 없음 — 사용자 데이터를
      실행 파일에 내장하지 않는다는 원칙 재확인).

## 빌드

- [ ] `pyinstaller homez.spec --noconfirm` 실행.
- [ ] `dist/Homez/Homez.exe` 생성 확인.
- [ ] 빌드 로그에서 누락된 hidden import 경고 확인(uvicorn/
      sqlalchemy 관련 — `homez.spec`의 `hiddenimports` 목록이
      부족하면 여기서 드러난다).

## 빌드 후 — 클린 머신 스모크

- [ ] Python이 설치되지 않은 클린 Windows VM에서 `Homez.exe` 실행.
- [ ] `%LOCALAPPDATA%\HOMEZ\data`, `logs`, `backups`, `config`가
      실제로 그 위치에 생기는지 확인(개발 모드 저장소 경로가
      아니라).
- [ ] 최초 실행 시 Company/관리자 설정 화면이 정상적으로 뜨는지.
- [ ] 로그인 → Console 화면 → 백업 생성 → 진단 내보내기까지
      한 번씩 실행(각 Gate Y 기능의 최소 스모크).
- [ ] 창 닫기 → 프로세스가 완전히 종료되는지(잔존 프로세스 없음).

## 서명 (인증서 확보 후, `docs/PACKAGING_CODE_SIGNING_PLAN.md` 참고)

- [ ] `signtool sign /fd sha256 /tr <타임스탬프 서버> /td sha256
      Homez.exe`.
- [ ] `signtool verify /pa /v Homez.exe`로 즉시 검증.
- [ ] SmartScreen 경고 여부 실제 확인(EV 인증서면 즉시 해소되어야
      정상).

## 설치 프로그램 (아직 선택 안 됨 — Inno Setup 권장)

- [ ] 설치 프로그램 스크립트 작성.
- [ ] 언인스톨 시 `%LOCALAPPDATA%\HOMEZ\*` 보존 정책 최종 확정
      (현재 계획 문서의 잠정 판단: 프로그램 파일만 제거, 사용자
      데이터는 기본 보존).
- [ ] 설치 프로그램 자체도 서명.
- [ ] 재설치(기존 데이터 위에 새 버전 설치) 시나리오 검증.

## 배포 후

- [ ] 첫 배포 버전을 `app/domains/update`의 `UpdateNotice`로 등록
      (관리자 수동 등록 — 자동 감지는 아직 없음, `docs/
      V7_LIVE_GATE_PLAN.md` E절).
- [ ] SmartScreen/백신 오탐 신고 채널 준비.
