"""
=========================================================
Homez OS

File : app/domains/user_settings/repository.py

2026-08-14 Gate F-2 — 낙관적 동시성 조건부 UPDATE는
app/domains/marketplace_listing/listing_wizard_repository.py의
`_update_conditional` 패턴을 그대로 따른다: 모든 조건부 UPDATE는
`WHERE version = expected_version`을 포함하고, 성공 시 version을 +1
한다 — rowcount == 0이면 Service가 409(버전 충돌)로 변환한다.

쓰기 메서드는 commit하지 않는다(*_no_commit) — Transaction 경계는
Service가 소유한다. company_id 없이 조회·변경하는 메서드는 두지
않는다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.user_settings.model import UserSetting


class UserSettingRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_for_user(
        self, user_id: int, company_id: int, setting_key: str,
    ) -> UserSetting | None:

        return (
            self.db.query(UserSetting)
            .filter(UserSetting.user_id == user_id)
            .filter(UserSetting.company_id == company_id)
            .filter(UserSetting.setting_key == setting_key)
            .first()
        )

    # --------------------------------------------------
    # 생성(최초 1회, expected_version == 0 경로에서만 호출됨)
    # --------------------------------------------------

    def create_no_commit(self, setting: UserSetting) -> UserSetting:

        self.db.add(setting)
        self.db.flush()

        return setting

    # --------------------------------------------------
    # 낙관적 동시성 조건부 UPDATE
    # --------------------------------------------------

    def update_value_conditional(
        self,
        user_id: int,
        company_id: int,
        setting_key: str,
        expected_version: int,
        setting_value: str,
        schema_version: int,
    ) -> int:

        stmt = (
            update(UserSetting)
            .where(UserSetting.user_id == user_id)
            .where(UserSetting.company_id == company_id)
            .where(UserSetting.setting_key == setting_key)
            .where(UserSetting.version == expected_version)
            .values(
                setting_value=setting_value,
                schema_version=schema_version,
                version=expected_version + 1,
                updated_at=datetime.utcnow(),
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount


__all__ = ["UserSettingRepository"]
