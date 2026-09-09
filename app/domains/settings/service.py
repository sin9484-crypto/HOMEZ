from app.core.exceptions import NotFoundException
from .repository import SettingRepository


class SettingService:

    def __init__(self, repository: SettingRepository):
        self.repository = repository

    def get_all(self):
        return self.repository.get_all()

    def get(self, key: str):
        setting = self.repository.get_by_key(key)
        if not setting:
            raise NotFoundException("Setting not found")
        return setting

    def update(self, key: str, value: str):
        setting = self.repository.update_value(key, value)
        if not setting:
            raise NotFoundException("Setting not found")
        return setting

    def get_str(self, key: str, default: str | None = None):
        setting = self.repository.get_by_key(key)
        return setting.value if setting else default

    def get_int(self, key: str, default: int = 0):
        value = self.get_str(key)
        return int(value) if value is not None else default

    def get_bool(self, key: str, default: bool = False):
        value = self.get_str(key)
        if value is None:
            return default
        return value.lower() in ("1", "true", "yes", "y")