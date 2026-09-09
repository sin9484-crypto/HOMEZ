# Homez v0.1.0

AI Commerce ERP Platform

Build 1 Started

## 로컬 실행

바탕화면의 **HOMEZ** 아이콘을 더블클릭하면 콘솔(CMD/PowerShell) 창
없이 주소창 없는 독립 Desktop 창(PyWebView 기반)만 열리고 그 안에서
운영자 Console(`/console`)이 바로 표시됩니다(최초 1회는
`scripts/create_homez_shortcut.ps1`로 바탕화면 아이콘을 생성해야
합니다). 자세한 내용은 `docs/HOMEZ_DESKTOP_APP.md` 참고.

실행 방식 2가지(둘 다 유지됨, 용도가 다름):

| 방식 | 대상 | 콘솔 창 | 용도 |
|---|---|---|---|
| `scripts\start_homez_desktop.vbs` | 일반 사용자(바탕화면 아이콘) | 없음(완전 숨김) | 평상시 실행 |
| `scripts\start_homez_desktop.cmd` | 개발자 | 보임 | 콘솔 출력을 보며 디버깅할 때 |

수동 실행(Desktop, 콘솔 숨김 — 바탕화면 아이콘과 동일):

```
wscript.exe scripts\start_homez_desktop.vbs
```

수동 실행(Desktop, 개발/디버그용 콘솔 표시):

```
scripts\start_homez_desktop.cmd
```

수동 실행(브라우저, fallback — Desktop 경로에 문제가 있을 때):

```
scripts\start_homez.cmd
```

또는 직접:

```
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

기본 접속 주소: http://127.0.0.1:8000/docs

아이콘/런처는 `assets/`, `scripts/`에 있는 임시 V1 자산이며 추후 변경될
수 있습니다.