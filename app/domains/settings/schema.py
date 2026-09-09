from pydantic import BaseModel, ConfigDict
from typing import Optional


class SettingBase(BaseModel):
    key: str
    value: str
    category: Optional[str] = None
    description: Optional[str] = None


class SettingCreate(SettingBase):
    pass


class SettingUpdate(BaseModel):
    value: str


class SettingResponse(SettingBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_system: bool
    active: bool