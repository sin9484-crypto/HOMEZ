"""
=========================================================
Homez OS

File : app/domains/update/service.py

Gate Y-4(2026-08-12) — 업데이트 공지 Service.

이번 단계의 명시적 경계: 이 서비스는 외부 URL을 절대 호출하지
않는다(HTTP 클라이언트 코드가 전혀 없다). "새 버전이 나왔다"는
사실은 오직 admin_guard로 보호된 create_notice() 호출을 통해서만
이 시스템에 들어온다 — 즉 사람이 직접 등록해야 한다. 자동으로
원격 서버에서 매니페스트를 가져와 서명을 검증하는 진짜 자동
업데이트 확인 메커니즘은 구현하지 않았고,
`docs/adr/0002-update-manifest-signing-design.md`에 설계만 남겼다.
=========================================================
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.version import VERSION as CURRENT_VERSION
from app.domains.update.model import UPDATE_SEVERITIES
from app.domains.update.model import UpdateNotice
from app.domains.update.repository import UpdateNoticeRepository

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

_NOT_FOUND_MESSAGE = "해당 업데이트 공지를 찾을 수 없습니다."


def parse_semver(version: str) -> tuple[int, int, int]:

    match = _SEMVER_RE.match(version.strip())

    if match is None:
        raise BadRequestException(
            f"버전 형식이 올바르지 않습니다(major.minor.patch만 "
            f"허용): {version!r}",
        )

    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )


class UpdateNoticeService:

    def __init__(
        self,
        db: Session,
    ):
        self.repository = UpdateNoticeRepository(db)

    def create_notice(
        self,
        *,
        version: str,
        title: str,
        message: str,
        severity: str,
        release_notes_url: str | None = None,
        published_by_user_id: int | None = None,
    ) -> UpdateNotice:

        parse_semver(version)  # 형식만 검증, 실패 시 즉시 예외

        if severity not in UPDATE_SEVERITIES:
            raise BadRequestException(
                f"알 수 없는 severity입니다: {severity!r}",
            )

        if not title or not message:
            raise BadRequestException(
                "title/message는 비어 있을 수 없습니다.",
            )

        notice = UpdateNotice(
            version=version.strip(),
            title=title,
            message=message,
            severity=severity,
            release_notes_url=release_notes_url,
            published_by_user_id=published_by_user_id,
        )

        return self.repository.create(notice)

    def deactivate_notice(
        self,
        notice_id: int,
    ) -> UpdateNotice:

        notice = self.repository.get(notice_id)

        if notice is None:
            raise NotFoundException(_NOT_FOUND_MESSAGE)

        return self.repository.deactivate(notice)

    def list_notices(
        self,
        limit: int = 50,
    ) -> list[UpdateNotice]:

        return self.repository.list_all(limit)

    def get_update_status(
        self,
        current_version: str = CURRENT_VERSION,
    ) -> dict:

        current_tuple = parse_semver(current_version)

        newer_notices = []
        for notice in self.repository.list_active():
            try:
                notice_tuple = parse_semver(notice.version)
            except BadRequestException:
                continue  # 손상된 데이터는 조용히 무시(fail-closed)

            if notice_tuple > current_tuple:
                newer_notices.append((notice_tuple, notice))

        if not newer_notices:
            return {
                "current_version": current_version,
                "update_available": False,
                "latest_notice": None,
            }

        newer_notices.sort(key=lambda pair: pair[0], reverse=True)
        latest_notice = newer_notices[0][1]

        return {
            "current_version": current_version,
            "update_available": True,
            "latest_notice": latest_notice,
        }


__all__ = [
    "UpdateNoticeService",
    "parse_semver",
]
