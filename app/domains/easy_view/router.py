"""
2026-10-16 — 쉬운 보기 화면용 읽기 전용 집계 API. 모두 관리자 전용·회사 범위이고 DB에 쓰지 않으며 외부 서비스를 부르지 않는다.
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import AdminGuard
from app.domains.easy_view.service import EasyViewService
from app.domains.user.model import User

router = APIRouter(prefix="/easy-view", tags=["easy-view"])


@router.get("/today")
def get_today(current_user: User = Depends(AdminGuard), db: Session = Depends(get_db)):
    """흐름 숫자, 출고일 확인이 필요한 등록 상품, 송장 등록 대기 주문."""

    return EasyViewService(db).build_today(current_user.company_id)


@router.get("/orders")
def get_orders(current_user: User = Depends(AdminGuard), db: Session = Depends(get_db)):
    """최근 주문의 5단계 진행(주문→발주→발송→송장→도착)과 다음 할 일."""

    return EasyViewService(db).build_orders(current_user.company_id)
