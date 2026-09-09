# 앱 내부 도움말 요약 — 문제 해결과 안전 사용

## 한국어

**로그인이 안 돼요**: 아이디와 비밀번호를 다시 확인하세요. 계속
실패하면 "비밀번호 재설정"을 이용하거나 관리자에게 문의하세요.

**상단 배지가 빨간색이에요**: 관리자 권한이 없는 계정에서는 정상적인
현상입니다. 관리자 계정으로 로그인하면 초록색으로 보입니다.

**권한이 없다고 나와요**: 관리자 전용 화면입니다. 설정 화면에서
관리자에게 권한을 요청하세요.

**지원팀에 문의하고 싶어요**: 관리자 권한이 있다면 `GET /diagnostics
/export`를 호출해 결과를 첨부해 주세요. 비밀번호나 토큰 같은 민감
정보는 자동으로 제거됩니다.

## English

**I can't log in**: double-check your username and password. If it
keeps failing, use "Reset password" or contact an admin.

**The top badges are red**: normal for a non-admin account. They
turn green when signed in as admin.

**"Permission denied"**: this is an admin-only screen. Ask an admin
to grant access from the Settings screen.

**I need to contact support**: if you have admin access, call
`GET /diagnostics/export` and attach the output. Sensitive values
like passwords and tokens are automatically stripped.
