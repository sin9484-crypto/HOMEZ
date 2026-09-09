from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository
from .model import Setting


class SettingRepository(BaseRepository[Setting]):
    def __init__(self, db: Session):
        super().__init__(Setting, db)

    def get_by_key(self, key: str) -> Setting | None:
        return (
            self.db.query(Setting)
            .filter(Setting.key == key)
            .first()
        )

    def get_by_category(self, category: str):
        return (
            self.db.query(Setting)
            .filter(Setting.category == category)
            .all()
        )

    def update_value(self, key: str, value: str) -> Setting | None:
        setting = self.get_by_key(key)
        if not setting:
            return None

        setting.value = value
        self.db.commit()
        self.db.refresh(setting)
        return setting