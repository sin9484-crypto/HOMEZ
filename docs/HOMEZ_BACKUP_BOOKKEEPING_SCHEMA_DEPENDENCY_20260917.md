# BackupService 북키핑의 스키마 의존성 결함 — 설계안 (2026-09-17)

- 작성 배경: Phase 4(실제 DB Migration 적용) 실행 중 실제로 재현된 결함의 기록과 향후 수정 설계안.
- **이번 문서는 설계안만 담는다 — 코드·실제 DB·백업 파일을 전혀 수정하지 않았다.**

## 1. 실제로 재현된 문제

`BackupService.create_backup()`(`app/domains/backup/service.py`)를 "Migration 적용 직전 백업" 용도로 표준 호출했을 때, 다음 순서로 실행된다:

1. SQLite Online Backup API로 파일 복사
2. `PRAGMA integrity_check`
3. sha256 계산
4. 암호화(`encrypt_file`)
5. `BackupRecord` 행을 `self.db`(백업 대상과 같은 DB!)에 INSERT

2026-09-17 Phase 4에서 1~4단계는 전부 성공했지만, 5단계가 `sqlite3.OperationalError: table backup_records has no column named is_encrypted`로 실패했다. 원인은 `is_encrypted` 컬럼 자체가 그 순간 적용하려던 13개 Migration 중 하나(`20260915_03_add_backup_records_is_encrypted.sql`)였기 때문이다 — **백업 북키핑 테이블이 적용 대상 Migration에 의존하는 순환 구조**였다.

이번에는 다음과 같이 수동으로 우회했다: 실패한 것은 DB 기록뿐이고(트랜잭션이 롤백돼 원본에 흔적 없음) 백업 파일 자체는 이미 유효했으므로, 파일을 별도 위치에서 복호화해 `integrity_check`·sha256을 직접 재확인한 뒤 백업으로 채택하고, Migration 적용 후 컬럼이 생기자 같은 내용으로 `BackupRecord` 행을 수동 기록했다. **이 우회는 이번 1회만 유효한 수작업이었고, 코드 계약으로 만들어 두지 않으면 다음에 또 사람이 직접 판단해야 한다.**

## 2. 왜 반복될 수 있는가

이 저장소의 Migration은 append-only로 계속 늘어난다. `BackupRecord` 모델(`app/domains/backup/model.py`)에 앞으로 컬럼이 하나라도 더 추가되고, 그 Migration이 아직 실제 DB에 적용되지 않은 상태에서 "적용 직전 백업"을 시도하면 **똑같은 실패가 다시 난다.** 이는 `BackupRecord`만의 문제가 아니라, "표준 백업 도구로 Migration 적용 전 백업을 만든다"는 원칙 자체가 그 도구의 북키핑 테이블이 최신 스키마를 요구하는 한 구조적으로 반복될 수 있는 패턴이다.

## 3. 검토한 설계안

### 안 A — 백업 이력을 별도 독립 DB로 분리
백업 이력(`BackupRecord`)을 `homez.db`가 아닌 별도 파일(예: `storage/backups/backup_ledger.db`)에 보관하고, 그 자체의 독립된 최소 스키마·Migration 이력을 갖는다.
- 장점: homez.db의 스키마 상태와 완전히 무관해져 순환 의존성이 원천적으로 사라진다.
- 단점: 감사 이력이 두 DB로 쪼개져 조회·백업 대상 관리가 늘어난다. 기존 `BackupRepository`/관련 라우터·테스트를 전부 새 DB 연결로 바꿔야 해 변경 범위가 크다.

### 안 B — 누락 컬럼을 조용히 허용(introspection 기반 부분 INSERT)
INSERT 전에 `PRAGMA table_info(backup_records)`로 실제 존재하는 컬럼만 골라 INSERT한다.
- 장점: 변경 범위가 작다.
- 단점: "Model이 항상 Source of Truth"라는 이 저장소의 기존 Migration 안전 원칙과 정면으로 어긋난다 — 스키마 드리프트를 코드가 조용히 흡수하면, 실제로 스키마가 뒤처져 있다는 신호 자체가 사라진다(이번처럼 명확한 예외로 드러나는 것이 오히려 안전).

### 안 C(권장) — 파일 생성과 북키핑 기록을 명시적으로 분리하고, 북키핑 실패를 "정상적으로 예상 가능한" 별도 예외로 승격
`create_backup()`을 두 단계로 나눈다:

```python
def _create_backup_file(self, *, source_db_path, backups_dir, ...) -> _BackupFileResult:
    """1~4단계만 수행(복사·integrity_check·sha256·암호화). DB 쓰기 없음."""

def create_backup(self, *, ..., trigger_source, ...) -> BackupRecord:
    file_result = self._create_backup_file(...)
    try:
        return self.repository.create(BackupRecord(...))
    except OperationalError as exc:
        if _looks_like_missing_column(exc):
            raise BackupBookkeepingUnavailableError(
                backup_file_path=file_result.path,
                file_size_bytes=file_result.size,
                sha256=file_result.sha256,
                integrity_check_result=file_result.integrity,
                is_encrypted=True,
            ) from exc
        raise
```

`BackupBookkeepingUnavailableError`는 이미 완료된 백업의 경로·크기·해시·무결성 결과를 속성으로 그대로 담아, 호출자가 "백업 자체는 성공했다"는 사실을 예외 메시지 파싱 없이 바로 알 수 있게 한다. Migration 적용 흐름(향후 스크립트/서비스)은 이 예외를 잡아 "북키핑은 Migration 적용 후 재시도"로 명시적으로 처리하거나, 최소한 사람이 읽기 쉬운 안내를 얻는다.

- 장점: 변경 범위가 안 A보다 작고, 안 B와 달리 스키마 드리프트를 숨기지 않는다(여전히 명확한 신호). 오늘 겪은 "성공인지 실패인지 트레이스백만으로는 헷갈리는" 상황 자체가 재발하지 않는다 — 이 예외 타입 자체가 "파일은 됐고 기록만 안 됐다"는 뜻이 되므로.
- 단점: `BackupService`를 호출하는 기존 코드(`app/domains/restore/service.py::run_weekly_rehearsal()` 등)가 이 새 예외를 알아야 한다 — 기존 호출부 영향 검토 필요.

## 4. 권장안

**안 C**를 권장한다. 백업 파일 자체의 신뢰성(가장 중요한 부분)은 그대로 유지하면서, "기록이 안 됐다"는 사실을 예외 타입으로 명시화해 다음에 같은 상황이 생겨도 사람이 매번 직접 재현·판단하지 않아도 되게 한다.

## 5. 이번 라운드에서 하지 않은 것

- `app/domains/backup/service.py` 코드 변경 — 하지 않음(다음 별도 라운드 대상)
- 새 예외 클래스·테스트 추가 — 하지 않음
- 실제 DB·백업 파일 수정 — 하지 않음
- 기존 `BackupRecord` id=6(2026-09-17 Phase 4에서 수동 기록한 행) 재작업 — 하지 않음(이미 정상 기록됨, 그대로 유지)
