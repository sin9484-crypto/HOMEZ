# HOMEZ V6.5 사용자 가이드 통합 목차

이 디렉터리는 HOMEZ V6.5의 최종 사용자 교육 자료 모음이다. 실제
코드(`app/web/console.html`, `console.js`, 각 도메인 라우터/서비스)와
임시 데모 환경(별도 SQLite 파일, 가상 회사 "HOMEZ Guide Demo Co.",
가상 계정 4개, 가상 상품 후보 1건)을 대상으로 검증했다. 실제 운영 DB
(`homez.db`)는 어떤 단계에서도 쓰기 작업을 하지 않았다 — 자세한 내용은
[SECURITY_REVIEW.md](SECURITY_REVIEW.md) 참고.

## 가이드 목록 (권장 시청 순서)

| # | 제목 | 목표 시간 | 한국어 | English | PDF | 스토리보드 | 자막(KO/EN) |
|---|---|---|---|---|---|---|---|
| 1 | 빠른 시작 | 3-5분 | [01_QUICK_START_KO.md](01_QUICK_START_KO.md) | [01_QUICK_START_EN.md](01_QUICK_START_EN.md) | [pdf](pdf/01_QUICK_START_KO.pdf) | [storyboard](storyboards/01_QUICK_START_STORYBOARD.md) | [ko](subtitles/01_QUICK_START_KO.srt) / [en](subtitles/01_QUICK_START_EN.srt) |
| 2 | 관리자 초기 설정 | 5-8분 | [02_ADMIN_SETUP_KO.md](02_ADMIN_SETUP_KO.md) | [02_ADMIN_SETUP_EN.md](02_ADMIN_SETUP_EN.md) | [pdf](pdf/02_ADMIN_SETUP_KO.pdf) | [storyboard](storyboards/02_ADMIN_SETUP_STORYBOARD.md) | [ko](subtitles/02_ADMIN_SETUP_KO.srt) / [en](subtitles/02_ADMIN_SETUP_EN.srt) |
| 3 | 상품 등록 전체 과정 | 10-15분 | [03_PRODUCT_REGISTRATION_KO.md](03_PRODUCT_REGISTRATION_KO.md) | [03_PRODUCT_REGISTRATION_EN.md](03_PRODUCT_REGISTRATION_EN.md) | [pdf](pdf/03_PRODUCT_REGISTRATION_KO.pdf) | [storyboard](storyboards/03_PRODUCT_REGISTRATION_STORYBOARD.md) | [ko](subtitles/03_PRODUCT_REGISTRATION_KO.srt) / [en](subtitles/03_PRODUCT_REGISTRATION_EN.srt) |
| 4 | 모바일 사용법 | 5-8분 | [04_MOBILE_USAGE_KO.md](04_MOBILE_USAGE_KO.md) | [04_MOBILE_USAGE_EN.md](04_MOBILE_USAGE_EN.md) | [pdf](pdf/04_MOBILE_USAGE_KO.pdf) | [storyboard](storyboards/04_MOBILE_USAGE_STORYBOARD.md) | [ko](subtitles/04_MOBILE_USAGE_KO.srt) / [en](subtitles/04_MOBILE_USAGE_EN.srt) |
| 5 | 문제 해결과 안전 사용 | 5-8분 | [05_TROUBLESHOOTING_KO.md](05_TROUBLESHOOTING_KO.md) | [05_TROUBLESHOOTING_EN.md](05_TROUBLESHOOTING_EN.md) | [pdf](pdf/05_TROUBLESHOOTING_KO.pdf) | [storyboard](storyboards/05_TROUBLESHOOTING_STORYBOARD.md) | [ko](subtitles/05_TROUBLESHOOTING_KO.srt) / [en](subtitles/05_TROUBLESHOOTING_EN.srt) |

각 가이드의 내레이션 대본은 `scripts/`, 앱 내부 도움말 요약본은
`apphelp/`, 실 화면 캡처는 `screenshots/<가이드번호>_.../`에 있다.

## 부가 문서

- [FEATURE_LIMITATIONS.md](FEATURE_LIMITATIONS.md) — 5개 가이드 전체의
  기능 포함/제외 목록을 한곳에 모은 표.
- [SECURITY_REVIEW.md](SECURITY_REVIEW.md) — 민감정보 노출 검사 결과와
  실제 운영 DB 불변 증거.

## 영상 (Desktop/Mobile MP4)

**아직 제작되지 않았다.** 이 작업 환경에는 화면 녹화·인코딩 도구
(ffmpeg 등)가 전혀 설치되어 있지 않다 — 지시문의 명시적 중단 조건
("영상 제작 도구 또는 코덱 부재")에 해당한다. 대신 각 가이드의
스토리보드·내레이션 대본·자막·실 화면 캡처를 모두 완성해, 도구가
설치된 환경에서 그대로 녹화·편집만 하면 영상을 만들 수 있는 상태로
준비했다.

## 앱 내부 도움말/버전 표시/언어 토글 연결

V6.5 Desktop Console에는 아직 이 가이드들을 직접 열람하는 앱 내부
도움말 메뉴가 없다(코드로 확인됨 — `console.html`에 도움말 인덱스나
가이드 링크 UI 없음). 이번 작업에서는 각 가이드에 대응하는 짧은
"앱 내부 도움말 요약"(`apphelp/*.md`)을 텍스트 콘텐츠로 준비했으며,
실제 UI에 넣는 것은 별도의 코드 변경(도움말 메뉴 신설)이 필요한
V7 범위의 작업이다.
