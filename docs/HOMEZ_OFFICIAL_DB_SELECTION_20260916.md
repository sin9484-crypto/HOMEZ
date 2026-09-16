# HOMEZ 공식 업무 DB 선택 준비 (2026-09-16)

- 작성 배경: 개인 베타 진입 전 잔여 작업 Phase 8
- **이 문서는 어떤 DB도 열지 않고 작성했다** — 파일 존재 여부·크기·
  수정시각만 파일시스템 레벨(`os.stat`)로 확인했다. SQL 연결,
  `PRAGMA` 조회, 내용 열람은 전혀 하지 않았다. Migration 적용도
  전혀 하지 않았다.
- 이 문서는 **선택지 정리와 절차 제안**까지만 한다. 실제로 어느
  경로를 공식으로 확정할지, 실데이터를 이관할지는 사용자 승인
  대상이다.

## 1. 두 경로는 "경쟁하는 후보"가 아니라 실행 모드 스위치다

먼저 정정해야 할 전제가 있다. 지시문은 두 경로를 나란히 비교하라고
했지만, 실제 코드(`app/desktop/paths.py::get_data_dir()`)를 보면
이 둘은 **동시에 후보가 될 수 없다** — `is_frozen()`(PyInstaller로
패키징된 실행 파일에서 실행 중인지) 값에 따라 자동으로 하나만
선택되는 구조다.

```python
def get_data_dir() -> Path:
    if is_frozen():
        return _frozen_data_root() / "data"   # %LOCALAPPDATA%\HOMEZ\data (또는 HOMEZ_DATA_ROOT)
    return get_repo_root()                     # 저장소 루트(현재 프로젝트 폴더)
```

- **개발 모드**(`is_frozen() == False` — `venv\Scripts\python.exe -m uvicorn app.main:app`로 직접 실행할 때. `.claude/launch.json`의 `homez-live` 항목이 정확히 이 방식이다): `homez.db` = 저장소 루트(`C:\Users\Daum pc\Homez-OS\homez.db`).
- **패키징 모드**(`is_frozen() == True` — PyInstaller로 빌드한 `.exe`를 실제로 실행할 때만. `HOMEZ_DATA_ROOT` 환경변수로 격리 테스트만 예외적으로 override 가능): `homez.db` = `%LOCALAPPDATA%\HOMEZ\data\homez.db`.

`DATABASE_URL` 환경변수를 명시적으로 설정하지 않는 한(`app/core/config.py::_default_database_url()`), 실제 SQLAlchemy 엔진이 붙는 DB도 항상 이 계산 결과와 정확히 일치한다 — 설정 따로, 실제 연결 따로가 아니다.

**HOMEZ는 지금까지 PyInstaller로 패키징된 적이 없다**(`app/desktop/paths.py` 자체 docstring: "이번 Phase는 그 빌드를 하지 않는다" — 이후로도 실제 빌드 스크립트가 이 저장소에 없음을 확인). 즉 `homez-live`를 포함해 이 프로젝트에서 실제로 서버를 띄운 모든 경우는 예외 없이 개발 모드였고, **실제로 요청을 처리해 온 DB는 항상 저장소 루트의 `homez.db`였다** — 이것은 선택의 문제가 아니라 이미 일어난 사실이다.

## 2. 두 파일의 현재 실측 상태 (2026-09-16, 파일시스템 레벨만)

| 항목 | 저장소 루트 `homez.db` | `%LOCALAPPDATA%\HOMEZ\data\homez.db` |
|---|---|---|
| 전체 경로 | `C:\Users\Daum pc\Homez-OS\homez.db` | `C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db` |
| 존재 여부 | 존재 | 존재 |
| 크기 | 3,420,160 bytes (~3.4MB) | 2,953,216 bytes (~2.95MB) |
| 마지막 수정 시각 | 2026-09-14 20:20:34 | 2026-08-29 21:02:41 |
| 최근 활동 여부 | **이 세션을 포함해 8월 말부터 지금까지 계속 갱신됨** | **2026-08-29 이후 약 18일간 전혀 갱신되지 않음** |

`%LOCALAPPDATA%\HOMEZ\` 디렉터리에는 그 옆에 이런 파일도 남아 있다
(전부 파일명만 확인, 열지 않음):

- `data\homez.db.incident_20260824_1516`
- `data\homez.db-journal.incident_20260824_1516`
- `data\homez.db.isolated_test_overwrite_20260824_1654`
- `backups\homez_pre_bootstrap_migration_20260827_222805.db`
- `backups\homez_pre_bootstrap_migration_20260829_140709.db`
- `backups\homez_pre_prelive_official_install_20260829_20260829_140202.db`

이 파일명들은 세션 메모리에 남아 있는 **2026-08-24 운영 DB 손상·
복구 사고**(installer 격리 테스트 도중 이 경로의 DB가 손상돼 백업에서
복구한 사고, 원인은 추정만 되고 확정되지 않음)와 정확히 일치한다.
즉 `%LOCALAPPDATA%\HOMEZ\data\homez.db`는 **그 사고 때 실제로
"운영 DB" 역할을 했던 파일이 맞다** — 다만 그건 당시 진행 중이던
설치 프로그램(installer) 테스트가 패키징 모드로 실행됐기 때문이고,
그 테스트가 끝난(2026-08-29 전후) 이후로는 이 파일을 가리키는
어떤 실행 경로도 다시 쓰이지 않았다.

**결론(사실 기준)**: 두 파일은 "아직 결정 안 된 두 후보"가 아니라
**서로 다른 시점의 실제 운영 이력을 가진 별개의 DB**다. 저장소 루트
쪽이 최신이고 계속 쓰이고 있으며, `%LOCALAPPDATA%` 쪽은 2026-08-29
이후 완전히 정지된 상태다(~18일치 데이터·Migration 공백).

## 3. 역할 정리 제안 (문서로만 — 미실행)

| 항목 | 제안 | 근거 |
|---|---|---|
| 공식 업무 DB | 저장소 루트 `homez.db` | 실제로 지금까지 계속 쓰여 온 유일한 DB. `homez-live`(개발 모드)가 가리키는 경로와 정확히 일치. 이 세션의 모든 Phase 작업 이력이 여기 기준으로 쌓여 있음(임시 DB만 썼으므로 실제로는 아직 반영 안 됨 — 4번 참고) |
| 보관만 할 DB | `%LOCALAPPDATA%\HOMEZ\data\homez.db` (및 그 옆 incident/backup 파일들) | 2026-08-24 사고의 포렌식 증거이자 2026-08-29 이전 상태의 스냅샷 — 삭제하지 않고 그대로 보관(사고 원인 규명이 아직 완료되지 않았으므로 증거를 지우지 않는다는 기존 원칙과 일치) |
| 이관 데이터 | 없음(제안) | 저장소 루트 쪽이 이미 최신이고 계속 갱신되고 있어, `%LOCALAPPDATA%` 쪽에서 가져올 "더 최신인" 데이터가 없다. 다만 8/24~8/29 사이에만 `%LOCALAPPDATA%` 쪽에 생성되고 저장소 루트 쪽에는 없는 행이 있을 가능성은 이론상 배제 못 함(확인하려면 실제 조회가 필요하므로 이 Phase에서는 하지 않음 — 5번 "실행 전 확인 목록" 참고) |
| 자격증명 재등록 여부 | 이관하지 않으므로 재등록 불필요(제안) | 저장소 루트 DB를 그대로 공식으로 쓰면 기존 Windows Credential Manager 참조(`credential_reference`)도 그대로 유효 |
| Migration 적용 대상 | 저장소 루트 `homez.db`만(이미 최신 — 이 세션이 이미 여러 번 확인한 대로 pending Migration 없음, `migration_restricted_mode` 비활성) | `%LOCALAPPDATA%` 쪽은 "보관용"으로만 남기고 더 이상 Migration을 적용하지 않는다(사용 안 하는 DB에 손대지 않는다) |

## 4. 지금까지 이 세션이 실제로 어느 DB를 건드렸는가 (정직 공개)

Phase 1~7 전체에서 이 세션은 **저장소 루트 `homez.db`를 포함해
어떤 실제 DB도 열거나 쓰지 않았다** — 공통 규칙(임시 SQLite만 사용)
을 그대로 지켰다. 새로 만든 8개 Migration 파일(`20260916_00`~`_02`
등)은 전부 임시 SQLite에서만 적용·검증됐다. 그래서 "저장소 루트가
최신"이라는 3번 항목의 판단은 **이 세션 이전까지의 실제 파일
mtime**(2026-09-14, 이전 세션들의 실제 작업 결과) 기준이며, 이
세션이 추가한 것은 아직 저장소 루트 `homez.db`에도 반영돼 있지
않다 — 공식 DB로 확정되고 사용자가 명시적으로 승인해야 Migration이
적용된다.

## 5. 실제 적용 전 실행 절차 (제안 — 사용자 승인 후에만 실행)

공식 DB를 저장소 루트 `homez.db`로 확정한다고 가정할 때:

1. **백업**: `app/domains/backup/service.py::BackupService.create_backup()`
   (이미 구현·검증된 기존 경로, Phase 5에서 서버 관리자 알림까지
   배선 완료)로 저장소 루트 `homez.db`의 암호화 백업을 만든다.
2. **복사본 리허설**: 백업을 복사한 임시 파일에 이번 세션이 만든
   신규 Migration 8개(20260916_00~_08 등, 정확한 목록은 최종 보고서
   참고)를 `MigrationRunner`로 순서대로 적용해 본다. 실패하면
   여기서 멈춘다 — 원본은 전혀 건드리지 않았으므로 안전하다.
3. **무결성 확인**: 리허설이 끝난 복사본에서 `PRAGMA
   integrity_check`, `foreign_key_check`, 적용된 Migration
   목록(`schema_migrations`)이 기대한 개수와 일치하는지 확인한다.
4. **원본 적용**: 3번이 전부 통과했을 때만, 같은 Migration들을
   실제 저장소 루트 `homez.db`에 순서대로 적용한다(적용 전 다시
   한번 최신 백업 생성).
5. **사후 검증**: 적용 직후 실제 DB에서 다시 `integrity_check`,
   `schema_migrations` 개수, 새로 생긴 테이블(`platform_alert_*`,
   `order_auto_collection_states` 등)의 존재 여부를 읽기 전용으로
   확인한다.
6. **rollback 계획**: 각 신규 Migration 파일 하단에 이미 주석으로
   역순 `DROP TABLE` 문을 남겨 뒀다(수동 실행 전용, 자동 실행 안
   됨) — 4번이 실패하면 3번 이전 백업으로 복원하는 것이 1차
   대응이다.

이 5단계 중 어느 것도 사용자의 명시적 실행 승인 없이는 수행하지
않는다 — 이 문서는 절차를 미리 적어 두는 것까지만 한다.

## 6. `%LOCALAPPDATA%` 경로가 다시 의미를 갖는 시점

향후 HOMEZ를 실제로 PyInstaller로 패키징해 설치 프로그램으로
배포하는 단계에 들어가면(`is_frozen() == True`가 되는 유일한
경우), 그 시점부터는 `%LOCALAPPDATA%\HOMEZ\data\homez.db`가 다시
실제로 쓰이기 시작한다. 그 전환 시점에 필요한 작업(저장소 루트의
최신 데이터를 `%LOCALAPPDATA%` 쪽으로 최초 1회 이관할지, 아니면
빈 DB로 새로 시작해 `bootstrap_environment()`가 처음부터 전체
Migration을 순차 적용하게 할지)은 **이번 개인 베타 범위 밖**이며,
그 시점에 별도로 다시 판단해야 한다 — 지금 미리 정하지 않는다
(추측으로 미래 결정을 대신하지 않는다).
