from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database.session import get_db

from app.domains.auth.schema import LoginRequest
from app.domains.auth.schema import LoginResponse

from app.domains.user.model import User
from app.domains.user.schema import UserResponse

from .service import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


@router.post(
    "/login",
    response_model=LoginResponse,
)
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
):

    service = AuthService(db)

    return service.login(request)


@router.get(
    "/me",
    response_model=UserResponse,
)
def me(
    current_user: User = Depends(get_current_user),
):

    return current_user