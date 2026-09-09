"""
=========================================================
Homez OS

File : app/domains/guides/schema.py

HOMEZ V6.5 사용자 가이드 API 응답 스키마. 전부 읽기 전용 — 요청
바디를 받는 엔드포인트가 하나도 없다(가이드 화면은 데이터를 변경하지
않는다).

locale 맵은 `{"ko-KR": GuideAssetStatus, "en-US": GuideAssetStatus}`
형태의 일반 dict로 표현한다(고정 필드 모델 대신) — 프런트엔드가 이미
어디서나 쓰는 locale 코드 문자열("ko-KR"/"en-US")을 키로 그대로
쓰기 위함이며, 파이썬 식별자 제약(하이픈 불가) 때문에 별도 별칭
매핑을 두는 것보다 단순하다.
=========================================================
"""

from __future__ import annotations

from pydantic import BaseModel


class GuideAssetStatus(BaseModel):
    """locale 하나에 대한 자산(영상/문서/PDF/자막) 가용 상태.

    available=False면 path는 항상 None이다(존재하지 않는 파일의
    경로를 클라이언트에 알려줄 이유가 없다 — 로컬 파일 경로를 UI에
    노출하지 않는다는 보안 요구사항과도 일치).
    """

    available: bool
    path: str | None = None


class GuideSummary(BaseModel):

    id: str
    order: int
    title_key: str
    description_key: str
    estimated_minutes: str
    category: str
    document: dict[str, GuideAssetStatus]
    pdf: dict[str, GuideAssetStatus]
    video: dict[str, GuideAssetStatus]
    subtitles: dict[str, GuideAssetStatus]
    screenshots: list[str]


class GuideListResponse(BaseModel):

    version: str
    guides: list[GuideSummary]


class GuidanceAskResponse(BaseModel):
    """Gate AI-F2(2026-08-22) — 대화형 안내 응답. 비밀번호·Credential류
    필드는 존재하지 않는다(이 서비스는 그런 값을 요구하지 않는다)."""

    suggested_guides: list[GuideSummary]
    ai_result: dict


__all__ = [
    "GuideAssetStatus",
    "GuideSummary",
    "GuideListResponse",
    "GuidanceAskResponse",
]
