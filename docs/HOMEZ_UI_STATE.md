# HOMEZ V3 운영자 Console — UI 상태 문서

# Current Version

app/web/** (신규) — 정적 HTML/CSS/JS 운영자 Console + 읽기 전용 집계 API.
V2.4/V3 애플리케이션 코드(automation_safety/product_candidate 등)는
무변경.

# Architecture

- Node/Vite/React 미도입(기존에 package.json 없음, 신규 패키지 설치
  금지 원칙 준수).
- `app/web/router.py` — FastAPI 라우터. `/console` 정적 페이지 + 정적
  CSS/JS/자산 서빙(인증 불필요) + `/console/api/*` 읽기 전용/제한적
  쓰기(Emergency Stop, Automation Mode) 집계 엔드포인트(전부
  `admin_guard` 필요).
- `app/web/console.{html,css,js}` — 외부 CDN/폰트/JS 라이브러리 의존
  없음. 순수 fetch + DOM API 기반 SPA.
- 기존 `/product-candidates`, `/funding/*`, `/settlements` 라우터는
  그대로 재사용(중복 구현 없음). `automation_safety`에는 원래 라우터가
  없어(V2.4 구현 시 서비스 레이어만 만들기로 결정) `app/web/router.py`가
  `SafetyService`를 읽기/제한적 쓰기로 소비하는 유일한 API 계층이다.

# 완료된 화면

좌측 내비 7개: 개요 / 상품 후보 / 트렌드 탐색 / 신제품 탐색 /
자동화 안전 / 자금·정산 / 시스템 상태. 상단바: 버전(`/` 응답)·서버
연결(`/health`)·V3 스키마 상태·자동화 모드·Emergency Stop 상태·운영자
메뉴(로그아웃).

- 개요: 오늘 발견/검토 대기/승인·보류·거절/활성 트렌드/신제품 수/
  Available Funding/Settlement 요약/안전 상태 카드.
- 상품 후보: 테이블(검색·상태·출처 필터, 클라이언트 사이드 정렬) →
  상세(원본/AI 판단/Evidence/운영자 결정 이력, 승인·보류·거절 버튼,
  확인 Dialog, 중복 클릭 방지).
- 트렌드/신제품 탐색: 후보 목록에서 `trend_score`/`is_new_product`
  기준으로 클라이언트 사이드 파생(별도 API 없음, 정직하게 파생임을
  화면에 설명).
- 자동화 안전: 모드/Emergency Stop 실제 표시 + 실제 활성화·해제·모드
  변경(확인 Dialog, Emergency Stop은 사유 필수). 캐릭터 미사용.
- 자금·정산: Funding Account(Available/Hold 합계), Settlement 상태
  요약. Supplier Payment는 목록 API가 없어 "표시할 수 없음"으로 정직하게
  안내(가짜 데이터 없음). 캐릭터 미사용.
- 시스템 상태: DB 무결성, V2.3 5개/V2.4·V3 8개 테이블 존재 여부,
  마지막으로 저장소에 기록된 테스트 결과(실시간 실행 아님, 문서 텍스트
  그대로). Migration 실행 버튼 없음.

# 알려진 제약 (이번 세션에서 발견, Whitelist 밖이라 미수정)

**[Critical, 사전 존재, app/domains/company 관련 — Whitelist 밖]**
`Company.users` SQLAlchemy relationship에 FK/primaryjoin이 없어
`configure_mappers()`가 실패한다. `app.main`을 import한 뒤(어떤
authenticated 엔드포인트든) 실제 DB 쿼리를 한 번이라도 실행하면
**프로세스 전체의 이후 모든 ORM 쿼리**(Company와 무관한 모델 포함)가
같이 실패한다. 즉 실제 서버를 띄운 상태에서 `admin_guard`가 걸린 어떤
엔드포인트(`/console/api/*`뿐 아니라 기존 `/product-candidates`,
`/funding/*` 등도 동일)든 실제 로그인 토큰으로 호출하면 500이 발생한다.
재현: `venv/Scripts/python -c "import app.main; from sqlalchemy.orm
import configure_mappers; configure_mappers()"`.
`app/domains/company/**`는 이번 작업 Whitelist 밖이라 수정하지 않았다.

**[High, 사전 존재, app/core/security.py 관련 — Whitelist 밖]**
설치된 `passlib`가 `bcrypt` 5.0.0과 호환되지 않아
(`AttributeError: module 'bcrypt' has no attribute '__about__'`)
`hash_password()`/`verify_password()` 호출 자체가 예외를 던진다. 즉
**현재 환경에서는 실제 `/auth/login`으로 로그인할 수 있는 계정이 없다**
(비밀번호를 새로 만들거나 검증할 수 없음). 원본 `bcrypt` 라이브러리는
직접 호출하면 정상 동작한다(`bcrypt.hashpw`/`checkpw`) — 문제는
`passlib`의 버전 호환 레이어에 있다.

이 두 가지는 이번 Console 구현과 무관하게 이미 존재하던 결함이며, 이번
검증 과정에서 실제 서버로 로그인→데이터 조회까지 end-to-end 확인을
시도하다가 발견되었다. `app/web/router.py` 자체의 로직은 실제 Migration
적용 스키마 + 실제 시드 데이터에 대해 라우터 함수를 직접 호출하는
방식으로 전수 검증했고 전부 정상 동작한다(계정/DB 쿼리 문제는 위
두 가지 사전 결함 때문이지 Console 코드 문제가 아님).

# 시각 검증 관련 제약

이번 세션의 Browser 도구(Electron 기반 미리보기)가 컴포지팅을 하지
못하는 상태였다(`screenshot`/`requestAnimationFrame` 실패,
`sandboxed_renderer.bundle.js` 오류 — Claude 실행 환경 자체의 문제,
HOMEZ 코드와 무관). 스크린샷 기반 픽셀 검증 대신:
- `get_page_text`/`read_page`로 텍스트·구조 확인
- `getComputedStyle`/`scrollWidth` 비교로 반응형 breakpoint(1440/1024/
  390) 동작 확인(가로 스크롤 없음, 사이드바 폭 변화, 모바일 토글 버튼
  표시 등 — transform 기반 슬라이드 애니메이션 자체의 렌더 결과는
  컴포지팅 실패로 완전히 확인하지 못함, CSSOM 상 규칙은 정확히 존재함을
  확인)
- 실제 로그인은 위 bcrypt 결함으로 불가 → `create_access_token()`으로
  직접 발급한 JWT를 localStorage에 주입해 인증 후 화면 진입까지는
  확인했으나, 이후 실제 데이터 API 호출은 Company 매퍼 결함으로 500이
  되는 것까지 확인(콘솔이 이 오류를 정직하게 표시하는 것도 확인됨).

# Next Action

1. `app/domains/company` 모델의 `users` relationship에 FK/primaryjoin
   추가(별도 승인 필요, Whitelist 밖).
2. `passlib`/`bcrypt` 버전 호환 문제 해결(`passlib` 업그레이드 또는
   `bcrypt` 다운그레이드 — 패키지 변경이라 별도 승인 필요).
3. 위 두 가지가 해결되면 실제 서버 + 실제 로그인으로 전체 인증 플로우
   재검증.
4. Browser 도구 컴포지팅 문제가 해소되면 스크린샷 기반 픽셀 검증 재수행.

# Last Updated

2026-07-29

# Updated By

Claude
