"""
=========================================================
Homez OS

File : app/core/desktop_auth.py

HOMEZ Desktop Shell — Desktop session token을 실제 방어 계층에 연결.

배경(2026-07-29 Desktop Shell 1단계 기록):
  "Desktop 세션마다 secrets.token_urlsafe(32)로 세션 토큰을 생성해
  메모리에만 보관한다... 알려진 제약: 이 세션 토큰은 현재 어떤 API
  엔드포인트도 보호하지 않는다."

이 모듈은 그 알려진 제약을 해소한다. 설계:

  1. Desktop Shell(app/desktop/main.py)이 pywebview의 js_api 브리지로만
     토큰을 노출한다 — URL 쿼리/fragment, localStorage 어디에도 토큰이
     나타나지 않는다(일반 브라우저는 window.pywebview 자체가 없어 이
     브리지를 호출할 방법이 없다 — 이것이 "일반 브라우저 접근과 Desktop
     접근을 분리"하는 실질적 경계다).
  2. Console JS가 페이지 로드 시 그 브리지로 토큰을 얻어 1회
     POST /desktop-auth/bootstrap 로 서버에 전달한다(URL이 아니라
     요청 본문 — 요청 자체는 로그에 토큰 값을 남기지 않는다).
  3. 서버는 토큰이 현재 프로세스의 토큰과 일치하면(상수시간 비교)
     HttpOnly + SameSite=Strict 쿠키를 심는다. 이후 요청은 이 쿠키를
     자동으로 동반한다 — JS가 다시 읽을 수 없다(HttpOnly).
  4. `require_desktop_token` 의존성은 위험도가 높은 변경 작업
     엔드포인트(Emergency Stop 조작, Decision 평가/승인/보류/거절/
     override)에 admin_guard와 "함께" 요구된다 — 토큰만으로는 어떤
     보호 API에도 접근할 수 없고, 사용자 로그인만으로도 이 계층을
     우회할 수 없다.

한계(정직하게 명시): 이 서버는 loopback HTTP만 사용하고(TLS 없음),
loopback 트래픽은 이 머신 밖으로 나가지 않으므로 `Secure` 쿠키 속성은
적용하지 않는다(HTTPS가 아니면 브라우저가 쿠키 자체를 거부한다) — 즉
이 환경에서는 `Secure`를 켤 수 없다는 제약이 있다. 패키징 이후 실제
배포 환경에서 TLS를 적용한다면 그때 `Secure=True`로 전환해야 한다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Cookie
from fastapi import HTTPException
from fastapi import Response
from fastapi import status
from pydantic import BaseModel
from pydantic import Field

from app.core.desktop_token import get_desktop_token
from app.core.security import constant_time_compare

DESKTOP_TOKEN_COOKIE_NAME = "homez_desktop_token"

router = APIRouter(tags=["Desktop"])


class DesktopBootstrapRequest(BaseModel):

    token: str = Field(..., description="pywebview js_api로 전달받은 Desktop 세션 토큰")


class DesktopBootstrapResponse(BaseModel):

    ok: bool


@router.post(
    "/desktop-auth/bootstrap",
    response_model=DesktopBootstrapResponse,
)
def bootstrap_desktop_session(
    data: DesktopBootstrapRequest,
    response: Response,
):
    """
    pywebview 창 안에서만 알 수 있는 토큰을 제시하면, 이후 요청에
    자동으로 동반될 HttpOnly 쿠키를 발급한다. 이 엔드포인트 자체는
    사용자 로그인을 요구하지 않는다(로그인 화면이 뜨기 전에도 Desktop
    Shell임을 먼저 확인할 수 있어야 하므로) — 사용자 인증은 별도로
    `/auth/login`이 담당하며, 이 쿠키는 그것을 대체하지 않는다.
    """

    current = get_desktop_token()

    if current is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이 서버는 Desktop 모드로 실행되고 있지 않습니다.",
        )

    if not constant_time_compare(data.token, current):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desktop 토큰이 일치하지 않습니다.",
        )

    response.set_cookie(
        key=DESKTOP_TOKEN_COOKIE_NAME,
        value=data.token,
        httponly=True,
        samesite="strict",
        secure=False,  # loopback HTTP 환경 — 위 모듈 docstring의 한계 참고
        path="/",
    )

    return {"ok": True}


def require_desktop_token(
    homez_desktop_token: str | None = Cookie(default=None),
) -> None:
    """
    위험도가 높은 변경 작업 엔드포인트에 admin_guard와 함께 추가한다.

    이 서버가 Desktop 모드로 실행 중이 아니면(get_desktop_token()이
    None — 기존 브라우저 기반 런처 `scripts/start_homez.cmd` 또는 이
    기능 도입 이전부터 있던 테스트/자동화 호출 경로) 이 방어 계층은
    조용히 비활성화된다 — 순수한 "추가" 방어이며 기존 브라우저 launcher
    fallback이나 기존 API 호출자를 깨지 않는다. Desktop 모드가 활성일
    때만 쿠키 일치 여부를 상수시간으로 검사한다.
    """

    current = get_desktop_token()

    if current is None:
        return

    if not homez_desktop_token or not constant_time_compare(
        homez_desktop_token, current,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desktop 인증이 필요합니다.",
        )


__all__ = [
    "router",
    "require_desktop_token",
    "DESKTOP_TOKEN_COOKIE_NAME",
]
