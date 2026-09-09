"""
=========================================================
Homez OS

File : app/domains/guides/constants.py

HOMEZ V6.5 사용자 가이드 — 정적 레지스트리.

`docs/guides/` 아래 실제로 존재하는 파일만 여기 등록한다(2026-08-13
가이드 제작 세션 산출물 — `docs/guides/GUIDE_INDEX.md` 참고). 이
모듈 자체는 파일이 실제로 존재하는지 확인하지 않는다 — 모든 경로는
`docs/guides/` 기준 상대 경로 문자열일 뿐이며, 실제 존재 여부·pending
판정은 service.py가 매 요청마다 파일시스템을 직접 확인해서 계산한다
(영상 파일이 나중에 추가되면 이 파일이나 다른 어떤 코드도 다시 고칠
필요 없이 자동으로 "재생 가능"으로 바뀐다).

경로 문자열은 전부 `docs/guides/`를 루트로 하는 상대 경로이며, 실제
파일 서빙은 항상 `GuidesService.resolve_safe_path()`의 경로 탈출·
확장자 allowlist 검사를 통과해야만 이루어진다(constants.py에 있다고
무조건 서빙되지 않는다).
=========================================================
"""

from __future__ import annotations

from typing import TypedDict


class LocaleText(TypedDict, total=False):

    ko_KR: str
    en_US: str


class GuideDefinition(TypedDict):

    id: str
    order: int
    title_key: str
    description_key: str
    estimated_minutes: str
    document: LocaleText
    pdf: LocaleText
    video: LocaleText
    subtitles: LocaleText
    screenshots: list[str]
    category: str


GUIDE_REGISTRY: list[GuideDefinition] = [
    {
        "id": "quick-start",
        "order": 1,
        "title_key": "guide.quick_start",
        "description_key": "guide.quick_start_description",
        "estimated_minutes": "3-5",
        "category": "quick_start",
        "document": {
            "ko-KR": "01_QUICK_START_KO.md",
            "en-US": "01_QUICK_START_EN.md",
        },
        "pdf": {
            "ko-KR": "pdf/01_QUICK_START_KO.pdf",
        },
        "video": {
            "ko-KR": "videos/01_quick_start_ko.mp4",
            "en-US": "videos/01_quick_start_en.mp4",
        },
        "subtitles": {
            "ko-KR": "subtitles/01_QUICK_START_KO.srt",
            "en-US": "subtitles/01_QUICK_START_EN.srt",
        },
        "screenshots": [
            "screenshots/01_quick_start/02_login_ko.png",
            "screenshots/01_quick_start/05_dashboard_super_admin_ko.png",
            "screenshots/01_quick_start/08_dashboard_manager_ko.png",
        ],
    },
    {
        "id": "admin-setup",
        "order": 2,
        "title_key": "guide.admin_setup",
        "description_key": "guide.admin_setup_description",
        "estimated_minutes": "5-8",
        "category": "admin_setup",
        "document": {
            "ko-KR": "02_ADMIN_SETUP_KO.md",
            "en-US": "02_ADMIN_SETUP_EN.md",
        },
        "pdf": {
            "ko-KR": "pdf/02_ADMIN_SETUP_KO.pdf",
        },
        "video": {
            "ko-KR": "videos/02_admin_setup_ko.mp4",
            "en-US": "videos/02_admin_setup_en.mp4",
        },
        "subtitles": {
            "ko-KR": "subtitles/02_ADMIN_SETUP_KO.srt",
            "en-US": "subtitles/02_ADMIN_SETUP_EN.srt",
        },
        "screenshots": [
            "screenshots/02_admin_setup/03_register_ko.png",
            "screenshots/02_admin_setup/04_invitations_and_requests.png",
            "screenshots/02_admin_setup/01_store_connection.png",
        ],
    },
    {
        "id": "product-registration",
        "order": 3,
        "title_key": "guide.product_registration",
        "description_key": "guide.product_registration_description",
        "estimated_minutes": "10-15",
        "category": "product_registration",
        "document": {
            "ko-KR": "03_PRODUCT_REGISTRATION_KO.md",
            "en-US": "03_PRODUCT_REGISTRATION_EN.md",
        },
        "pdf": {
            "ko-KR": "pdf/03_PRODUCT_REGISTRATION_KO.pdf",
        },
        "video": {
            "ko-KR": "videos/03_product_registration_ko.mp4",
            "en-US": "videos/03_product_registration_en.mp4",
        },
        "subtitles": {
            "ko-KR": "subtitles/03_PRODUCT_REGISTRATION_KO.srt",
            "en-US": "subtitles/03_PRODUCT_REGISTRATION_EN.srt",
        },
        "screenshots": [
            "screenshots/03_product_registration/04_candidates_with_virtual_item.png",
            "screenshots/03_product_registration/05_listing_wizard_start_click.png",
            "screenshots/03_product_registration/02_marketplace_listing_banner.png",
        ],
    },
    {
        "id": "mobile-usage",
        "order": 4,
        "title_key": "guide.mobile_usage",
        "description_key": "guide.mobile_usage_description",
        "estimated_minutes": "5-8",
        "category": "mobile_usage",
        "document": {
            "ko-KR": "04_MOBILE_USAGE_KO.md",
            "en-US": "04_MOBILE_USAGE_EN.md",
        },
        "pdf": {
            "ko-KR": "pdf/04_MOBILE_USAGE_KO.pdf",
        },
        "video": {
            "ko-KR": "videos/04_mobile_usage_ko.mp4",
            "en-US": "videos/04_mobile_usage_en.mp4",
        },
        "subtitles": {
            "ko-KR": "subtitles/04_MOBILE_USAGE_KO.srt",
            "en-US": "subtitles/04_MOBILE_USAGE_EN.srt",
        },
        "screenshots": [
            "screenshots/04_mobile/00_login_430x932.png",
            "screenshots/04_mobile/03_nav_drawer_open_430x932.png",
            "screenshots/04_mobile/02_candidates_430x932.png",
        ],
    },
    {
        "id": "troubleshooting",
        "order": 5,
        "title_key": "guide.troubleshooting",
        "description_key": "guide.troubleshooting_description",
        "estimated_minutes": "5-8",
        "category": "troubleshooting",
        "document": {
            "ko-KR": "05_TROUBLESHOOTING_KO.md",
            "en-US": "05_TROUBLESHOOTING_EN.md",
        },
        "pdf": {
            "ko-KR": "pdf/05_TROUBLESHOOTING_KO.pdf",
        },
        "video": {
            "ko-KR": "videos/05_troubleshooting_ko.mp4",
            "en-US": "videos/05_troubleshooting_en.mp4",
        },
        "subtitles": {
            "ko-KR": "subtitles/05_TROUBLESHOOTING_KO.srt",
            "en-US": "subtitles/05_TROUBLESHOOTING_EN.srt",
        },
        "screenshots": [
            "screenshots/05_troubleshooting/01_login_wrong_password.png",
        ],
    },
]

# guide.category 필터 값 — console.js 필터 버튼과 1:1 대응.
GUIDE_CATEGORIES = [
    "quick_start",
    "admin_setup",
    "product_registration",
    "mobile_usage",
    "troubleshooting",
]

# 실제 서빙을 허용하는 확장자(대소문자 무시) — 이 목록 밖의 확장자는
# constants.py에 경로가 등록되어 있어도 서빙되지 않는다.
ALLOWED_FILE_EXTENSIONS = {
    ".mp4", ".webm", ".vtt", ".srt",
    ".md", ".pdf", ".png", ".jpg", ".jpeg",
}

MEDIA_TYPE_BY_EXTENSION = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".vtt": "text/vtt",
    ".srt": "application/x-subrip",
    ".md": "text/markdown; charset=utf-8",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


__all__ = [
    "GuideDefinition",
    "LocaleText",
    "GUIDE_REGISTRY",
    "GUIDE_CATEGORIES",
    "ALLOWED_FILE_EXTENSIONS",
    "MEDIA_TYPE_BY_EXTENSION",
]
