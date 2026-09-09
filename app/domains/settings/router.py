from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.session import get_db
from app.core.guard import require_super_admin

from .repository import SettingRepository
from .schema import SettingResponse, SettingUpdate
from .service import SettingService

router = APIRouter(prefix="/settings", tags=["Settings"])


def get_service(db: Session = Depends(get_db)):
    return SettingService(SettingRepository(db))


@router.get("", response_model=list[SettingResponse])
def get_settings(
    service: SettingService = Depends(get_service),
    _: dict = Depends(get_current_user),
):
    return service.get_all()


@router.get("/{key}", response_model=SettingResponse)
def get_setting(
    key: str,
    service: SettingService = Depends(get_service),
    _: dict = Depends(get_current_user),
):
    return service.get(key)


@router.put("/{key}", response_model=SettingResponse)
def update_setting(
    key: str,
    body: SettingUpdate,
    service: SettingService = Depends(get_service),
    _: dict = Depends(require_super_admin),
):
    return service.update(key, body.value)