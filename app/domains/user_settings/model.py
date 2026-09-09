"""
=========================================================
Homez OS

File : app/domains/user_settings/model.py

2026-08-14 Gate F-2 — Desktop 재시작 시 로그인 세션·비민감 사용자
설정(언어, 가이드 진행 상태 등)이 사라지는 문제를 해소하기 위한
서버 사용자 설정 도메인.

배경(app/desktop/server.py::find_free_port()): Desktop 앱은 실행마다
완전히 무작위 포트로 뜨고(고정 포트로 바꾸지 않는다는 정책 유지), 그
결과 브라우저 origin이 매번 바뀌어 그 origin에 종속된 localStorage가
재시작마다 전부 사라진다. 언어·가이드 진행 상태처럼 "로그인된 사용자에
귀속되는" 비민감 설정은 origin이 아니라 사용자 계정에 귀속시켜야
재시작에도 살아남는다 — 이 테이블이 그 저장소다.

설계 선택 — key-value(행 단위 setting_key) vs 명시적 컬럼:
  이 도메인은 key-value 방식을 택한다(행마다 하나의 setting_key만
  담음). 이유:
    1) locale/guide_progress/ui_preferences/notification_preferences는
       서로 완전히 독립된 화면·기능이 소유한 값이다 — 한 컬럼을 자주
       쓰는 화면(가이드 진행 상태, 여러 단계 클릭마다 저장)과 거의
       바뀌지 않는 컬럼(언어)을 한 행에 묶으면, 낙관적 동시성 버전
       하나를 여러 기능이 공유하게 되어 서로 무관한 갱신끼리도 충돌
       (버전 불일치)을 일으킨다. key마다 별도 행 + 별도 version을 두면
       이 문제가 구조적으로 없다.
    2) 새 설정 키가 필요할 때 컬럼 추가 Migration 없이 허용목록
       (ALLOWED_SETTING_KEYS)만 늘리면 된다 — 스키마 변경 빈도를
       낮춘다.
    3) 이 저장소의 기존 legacy `app/domains/settings`(전역 key-value
       앱 설정, company/user 구분 없음, 어디에도 mount되지 않은 죽은
       코드)와 유사한 관례를 재사용하되, 이번에는 (company_id, user_id,
       setting_key)로 완전히 스코프를 분리한다.

절대 저장 금지(서비스 계층에서 강제): password/token/credential/
recovery_code/invitation_code/secret/api_key류 필드명을 가진 값은
스키마 설계 단계부터 거부한다(app/domains/user_settings/service.py
`_reject_forbidden_value_fields` 참고) — 이 테이블에는 인증 관련
정보를 절대 두지 않는다.

낙관적 동시성은 이 저장소의 다른 도메인(`ListingWizard.version`,
app/domains/marketplace_listing/listing_wizard_repository.py)과 동일한
"WHERE version = expected_version" 조건부 UPDATE 패턴을 그대로 쓴다.
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class UserSetting(Base):
    """
    (company_id, user_id, setting_key)로 완전히 격리된 사용자별 설정
    한 개 행. FK는 두지 않는다(이 코드베이스 전역 컨벤션 — 논리 참조만
    사용, company_id/user_id는 생성 시점에 값을 그대로 복사해 저장).
    """

    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "setting_key",
            name="uq_user_settings_user_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 격리 경계. user_id만으로도 사실상
    # 유일하지만, 이 저장소의 다른 모든 Domain과 동일하게 회사 경계도
    # 조회 WHERE 절에 항상 명시적으로 포함한다(조인 없이 바로 검증).
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (users.id)
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # ALLOWED_SETTING_KEYS(app/domains/user_settings/schema.py)의
    # 허용목록 값만 저장한다 — 서비스 계층이 강제, DB 레벨 CHECK는
    # 두지 않는다(Model에 없는 제약을 SQL에 임의로 추가하지 않는다는
    # Migration 컨벤션).
    setting_key: Mapped[str] = mapped_column(String(50), nullable=False)

    # JSON 직렬화된 원문 문자열 — 구조 검증은 서비스 계층이 전담한다
    # (이 컬럼 자체는 원문만 저장, marketplace_listing과 동일한 관례).
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)

    # 향후 이 setting_key의 값 형태가 바뀔 때(예: guide_progress v1→v2)
    # 클라이언트가 자신이 보낸/받은 값의 형태 버전을 식별할 수 있게
    # 한다 — 이 테이블 자체의 스키마(컬럼 구조) 버전이 아니라 값
    # payload의 버전이다.
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    # 낙관적 동시성 fencing 토큰 — ListingWizard.version과 동일한 계약.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = ["UserSetting"]
