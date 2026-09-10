"""
=========================================================
Homez OS

File : app/domains/purchase_task/channel_connection_service.py

Gate PT-3(2026-09-08, item 7 "사용자 계정 기반 매입 연결") —
PurchaseChannelConnection 생성·조회·라벨변경·연결확인·비활성화·재활성화
와, 매입 작업이 실행 시점에 사용할 연결을 선택·검증하는 로직.

핵심 규칙(사용자 지시 원문):
1. 자격증명 등록 여부만으로 연결됨을 선언하지 않는다.
   2026-09-08 재정정(id=3/4 사고 이후) — CREDENTIAL 방식 연결은
   mark_verified()로 사람이 직접 성공을 선언할 수 없다(이 메서드가
   그 경로를 명시적으로 차단한다). verified_at은 오직 (a) BROWSER_
   LOGIN 연결의 mark_verified()(사람의 유일하게 가능한 자기보고),
   또는 (b) CREDENTIAL 연결의 lookup_product()/lookup_order()가
   실제 네트워크 호출에 성공했을 때만 채워진다. check_connection_
   status()는 그 값을 Adapter에 그대로 전달해 표시용 status를
   재계산할 뿐, verified_at을 스스로 만들지 않는다.
2. account_label은 표시값일 뿐이라 변경해도 연결 자체(= id)는 그대로
   유지된다.
3. 회사 간 조회·수정·실행은 항상 company_id로 격리한다.
4. 비활성(연결 해제)된 연결로는 매입 작업을 실행할 수 없다 —
   select_connection_for_task()가 이를 항상 검증한다.
5. 여러 연결 중 하나를 임의로 자동 선택하지 않는다 — 이 서비스
   어디에도 "첫 번째 것을 쓴다" 같은 fallback이 없다. 호출자가 항상
   connection_id를 명시해야 한다.
6. 연결을 비활성화해도 그 연결을 참조했던 과거 PurchaseTask/
   PurchaseRecord의 channel_connection_id는 절대 지우지 않는다(이
   서비스가 그 값을 건드리는 메서드 자체를 두지 않는다 — 삭제/NULL화
   경로가 없다는 것이 곧 그 보장이다).
7. (2026-09-08 후속) connection_method는 클라이언트가 요청 바디로
   무엇을 보내든 mall_code 기준으로 서버가 다시 계산해 덮어쓴다
   (PurchaseChannelMallCode.resolve_connection_method) — 매입처별
   실제 연결 방식(자격증명형/브라우저 로그인형)을 클라이언트가
   임의로 바꿔 표시하지 못하게 한다.
8. (2026-09-08 후속) BROWSER_LOGIN 매입처는 API로 세션을 확인할
   방법이 전혀 없다(자동 로그인 금지 원칙) — 그래서 사람이 마지막
   으로 "연결 확인 완료"를 누른 시각이 BROWSER_LOGIN_TRUST_WINDOW_DAYS
   보다 오래되면 check_connection_status()가 자동으로 EXPIRED로
   낮춘다. 이것도 여전히 verified_at을 스스로 만드는 것이 아니라
   "이미 있는 verified_at이 너무 오래됐다"는 사실만 반영한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError
from app.domains.purchase_task.channel_adapter import get_purchase_channel_adapter
from app.domains.purchase_task.constants import BROWSER_LOGIN_TRUST_WINDOW_DAYS
from app.domains.purchase_task.constants import ChannelConnectionEventType
from app.domains.purchase_task.constants import ChannelConnectionStatus
from app.domains.purchase_task.constants import ConnectionMethod
from app.domains.purchase_task.constants import CREDENTIAL_REVERIFICATION_WINDOW_HOURS
from app.domains.purchase_task.constants import is_onchannel_order_contract_fully_confirmed
from app.domains.purchase_task.constants import PurchaseChannelMallCode
from app.domains.purchase_task.constants import unconfirmed_onchannel_order_contract_items
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.repository import PurchaseTaskRepository


class PurchaseChannelConnectionService:

    def __init__(self, db: Session, credential_store=None):

        self.db = db
        self.repository = PurchaseTaskRepository(db)
        # 2026-09-08 후속 — 테스트가 실제 Windows Credential Manager를
        # 건드리지 않도록 주입 지점을 둔다(운영 코드 경로는 항상 기본값,
        # 즉 실제 저장소를 쓴다).
        if credential_store is None:
            from app.core.windows_credential_store import WindowsCredentialStore
            credential_store = WindowsCredentialStore()
        self._credential_store = credential_store

    # ---------------- 생성·조회 ----------------

    def create_connection(
        self, company_id: int, *, mall_code: str, account_label: str,
        memo: str | None = None, idempotency_key: str | None = None,
        triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:

        if not account_label or not account_label.strip():
            raise BadRequestException("계정 표시 이름을 입력해야 합니다.")

        if idempotency_key:
            existing = self.repository.get_connection_by_idempotency(
                company_id, idempotency_key,
            )
            if existing is not None:
                return existing

        resolved_method = PurchaseChannelMallCode.resolve_connection_method(mall_code)
        connection = PurchaseChannelConnection(
            company_id=company_id, mall_code=mall_code,
            connection_method=resolved_method,
            account_label=account_label.strip(),
            status=ChannelConnectionStatus.LOGIN_REQUIRED,
            is_active=True, memo=memo, idempotency_key=idempotency_key,
        )
        self.repository.add_connection(connection)

        # 2026-09-08 후속 — CREDENTIAL 방식은 연결마다 별도 Credential
        # Manager 슬롯을 갖는다(전역 단일 슬롯이었던 이전 OnchannelSupplierOrderProvider.
        # CREDENTIAL_REFERENCE와 달리, 회사가 같은 매입처에 여러 계정을
        # 연결해도 서로 자격증명이 섞이지 않는다). id가 생긴 뒤에만
        # 만들 수 있으므로 add_connection() 이후에 채운다.
        if resolved_method == ConnectionMethod.CREDENTIAL:
            connection.credential_reference = f"homez_channel_connection_{connection.id}"

        self._log_event(
            connection, ChannelConnectionEventType.CREATED,
            detail=f"mall_code={mall_code}", triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def get_connection_or_404(
        self, connection_id: int, company_id: int,
    ) -> PurchaseChannelConnection:

        connection = self.repository.get_connection(connection_id, company_id)
        if connection is None:
            raise NotFoundException("매입처 연결을 찾을 수 없습니다.")
        return connection

    def list_connections(
        self, company_id: int, *, mall_code: str | None = None,
        include_inactive: bool = False,
    ) -> list[PurchaseChannelConnection]:
        """2026-09-08 재정정 — id=3/4 사고: 화면에 보여줄 status를 DB에
        저장된 값 그대로 신뢰하지 않는다. 활성 연결마다 현재 진실
        (자격증명 실제 존재 여부 등, 전부 로컬 조회 — 외부 네트워크
        호출 없음)로 다시 계산해 어긋나면 그 자리에서 바로잡는다.
        last_checked_at은 건드리지 않는다(그건 "사람이 명시적으로
        확인을 눌렀다"는 사실 전용 — 목록 조회는 그런 행동이 아니다)."""

        connections = self.repository.list_connections(
            company_id, mall_code=mall_code, include_inactive=include_inactive,
        )
        changed_any = False
        for connection in connections:
            if not connection.is_active:
                continue
            if self._apply_status_recompute(connection, touch_last_checked=False):
                changed_any = True
        if changed_any:
            self.db.commit()
            for connection in connections:
                self.db.refresh(connection)
        return connections

    # ---------------- 라벨 변경 ----------------

    def rename_connection(
        self, connection_id: int, company_id: int, new_label: str,
        *, triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:

        if not new_label or not new_label.strip():
            raise BadRequestException("계정 표시 이름을 입력해야 합니다.")

        connection = self.get_connection_or_404(connection_id, company_id)
        old_label = connection.account_label
        connection.account_label = new_label.strip()
        self._log_event(
            connection, ChannelConnectionEventType.LABEL_RENAMED,
            detail=f"{old_label} -> {connection.account_label}",
            triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    # ---------------- 연결 상태 확인·연결 확인 완료 ----------------

    def check_connection_status(
        self, connection_id: int, company_id: int,
    ):
        """확인 가능한 방식으로만 확인하고 표시용 status/last_checked_at
        을 갱신한다. verified_at은 여기서 절대 만들지 않는다 — 이미
        있는 값을 근거로 판단할 뿐이다.

        2026-09-08 후속 — 매입처 연결 방식(connection_method)에 따라
        분기한다:
        - ADDITIONAL_AUTH_REQUIRED로 사람이 직접 표시해 둔 상태는 이
          자동 재확인이 덮어쓰지 않는다(재로그인 후 mark_verified()를
          다시 눌러야만 해제된다) — 그렇지 않으면 사람이 남긴 경고를
          다음 조회에서 조용히 지워버리게 된다.
        - BROWSER_LOGIN(HOMEZ가 세션을 기술적으로 확인할 방법이
          전혀 없는 매입처)은 verified_at이 BROWSER_LOGIN_TRUST_
          WINDOW_DAYS보다 오래됐으면 EXPIRED로 자동 전환한다 — 유일하게
          정직하게 구현 가능한 "만료" 신호다.
        - 그 외(CREDENTIAL 등)는 기존처럼 Adapter에게 위임한다.
        """

        connection = self.get_connection_or_404(connection_id, company_id)
        self._apply_status_recompute(connection, touch_last_checked=True)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def _compute_current_status(self, connection: PurchaseChannelConnection) -> str:
        """부수효과 없는 순수 계산 — check_connection_status()/
        list_connections()/select_connection_for_task()가 전부 이
        하나의 규칙을 공유한다(서로 다른 판단 기준이 생기지 않게)."""

        if connection.status == ChannelConnectionStatus.ADDITIONAL_AUTH_REQUIRED:
            return connection.status
        if connection.connection_method == ConnectionMethod.BROWSER_LOGIN:
            return self._compute_browser_login_status(connection)

        try:
            adapter = get_purchase_channel_adapter(
                connection.mall_code,
                credential_reference=connection.credential_reference,
                credential_store=self._credential_store,
            )
        except PurchaseChannelAdapterError:
            adapter = None

        if adapter is not None:
            result = adapter.check_connection(
                connection.account_label, verified_at=connection.verified_at,
            )
            return result.status

        # 확인 방법 자체가 없는 매입처 — verified_at 유무로만
        # 판단한다(자격증명 개념은 알 수 없음).
        if connection.verified_at is not None:
            return ChannelConnectionStatus.CONNECTED
        return ChannelConnectionStatus.LOGIN_REQUIRED

    def _apply_status_recompute(
        self, connection: PurchaseChannelConnection, *, touch_last_checked: bool,
    ) -> bool:
        """새 status를 계산해 달라지면 DB에 반영하고 감사 이벤트를
        남긴다(commit은 호출자 책임 — 여러 연결을 한 번에 처리하는
        list_connections()가 커밋을 모아서 하기 위함). 반환값은 실제로
        바뀌었는지 여부."""

        new_status = self._compute_current_status(connection)
        changed = new_status != connection.status
        if changed:
            self._log_event(
                connection, ChannelConnectionEventType.STATUS_CHANGED,
                detail=f"{connection.status} -> {new_status}", triggered_by=None,
            )
            connection.status = new_status
        if touch_last_checked:
            connection.last_checked_at = datetime.utcnow()
        return changed

    def mark_verified(
        self, connection_id: int, company_id: int,
        *, triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:
        """사람이 직접 "연결 확인 완료"를 눌렀을 때만 호출된다.

        2026-09-08 재정정(id=3/4 사고) — 이전에는 이 메서드가
        connection_method와 무관하게 무조건 CONNECTED를 만들었다.
        그 결과 CREDENTIAL 방식(온채널) 연결도 자격증명을 전혀
        저장하지 않은 채로 사람이 이 버튼만 눌러 CONNECTED로
        표시될 수 있었다(id=3 "온채널 사업자 계정", id=4 "sin945"가
        실제로 이 경로로 그렇게 됐다 — Credential Manager에 값이
        전혀 없는데도 CONNECTED였다). CREDENTIAL 방식은 사람이 성공을
        "선언"할 수 없다 — 실제 API 호출 성공(lookup_product/
        lookup_order)만이 verified_at을 채운다. BROWSER_LOGIN
        방식(HOMEZ가 세션을 기술적으로 확인할 방법이 전혀 없는
        매입처)만 이 경로가 여전히 유효한 유일한 확인 수단이다.

        이전에 사람이 ADDITIONAL_AUTH_REQUIRED로 표시해 뒀어도, 다시
        로그인해 확인했다는 뜻이므로(BROWSER_LOGIN 한정) 이 호출로
        정상 해제된다."""

        connection = self.get_connection_or_404(connection_id, company_id)
        if not connection.is_active:
            raise BadRequestException(
                "비활성화된 연결입니다 — 먼저 재연결해야 합니다.",
            )
        if connection.connection_method == ConnectionMethod.CREDENTIAL:
            raise BadRequestException(
                "이 연결은 자격증명(API 키) 방식입니다 — 사람이 직접 \"연결 확인 "
                "완료\"를 선언할 수 없습니다. 실제 조회(예: 상품 조회)가 성공해야만 "
                "연결 확인 상태가 됩니다.",
            )

        now = datetime.utcnow()
        connection.verified_at = now
        connection.last_checked_at = now
        connection.status = ChannelConnectionStatus.CONNECTED
        self._log_event(
            connection, ChannelConnectionEventType.VERIFIED,
            detail="사용자가 직접 연결 확인 완료로 기록(BROWSER_LOGIN 자기보고)",
            triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def mark_additional_auth_required(
        self, connection_id: int, company_id: int,
        *, triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:
        """HOMEZ는 2단계 인증·본인확인 요구 여부를 기술적으로 감지할
        방법이 없다(자동 로그인 금지 원칙) — 사람이 실제로 그 매입처에
        로그인하다가 추가 인증 화면을 만났을 때 직접 눌러 남기는
        자기보고 상태다. verified_at은 건드리지 않는다(이전에 확인된
        적이 있다는 사실 자체는 유지) — status만 바꿔 다음 check_
        connection_status() 호출에서 자동으로 지워지지 않게 한다."""

        connection = self.get_connection_or_404(connection_id, company_id)
        if not connection.is_active:
            raise BadRequestException(
                "비활성화된 연결입니다 — 먼저 재연결해야 합니다.",
            )

        connection.status = ChannelConnectionStatus.ADDITIONAL_AUTH_REQUIRED
        connection.last_checked_at = datetime.utcnow()
        self._log_event(
            connection, ChannelConnectionEventType.STATUS_CHANGED,
            detail="사용자가 추가 인증 필요로 직접 표시", triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def _compute_browser_login_status(
        self, connection: PurchaseChannelConnection,
    ) -> str:

        if connection.verified_at is None:
            return ChannelConnectionStatus.LOGIN_REQUIRED

        age = datetime.utcnow() - connection.verified_at
        if age >= timedelta(days=BROWSER_LOGIN_TRUST_WINDOW_DAYS):
            return ChannelConnectionStatus.EXPIRED

        return ChannelConnectionStatus.CONNECTED

    # ---------------- 비활성화·재활성화 ----------------

    def deactivate_connection(
        self, connection_id: int, company_id: int,
        *, triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:

        connection = self.get_connection_or_404(connection_id, company_id)
        if not connection.is_active:
            return connection

        connection.is_active = False
        connection.disconnected_at = datetime.utcnow()
        self._log_event(
            connection, ChannelConnectionEventType.DEACTIVATED,
            detail=None, triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def reactivate_connection(
        self, connection_id: int, company_id: int,
        *, triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:

        connection = self.get_connection_or_404(connection_id, company_id)
        if connection.is_active:
            return connection

        connection.is_active = True
        connection.disconnected_at = None
        self._log_event(
            connection, ChannelConnectionEventType.REACTIVATED,
            detail=None, triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    # ---------------- 완전 삭제(선택 삭제 UI 지원) ----------------

    def delete_connection(
        self, connection_id: int, company_id: int,
        *, triggered_by: int | None = None,
    ) -> None:
        """연결 해제(비활성화)와 달리 행 자체를 완전히 지운다 —
        "사용해본 적 없는 테스트/오등록 연결을 목록에서 치운다"는
        목적 전용이다.

        2026-09-08 후속(사용자 요청 — "불필요한것 선택 삭제") — Model
        docstring의 기존 불변식("연결을 비활성화해도 그 연결을 참조한
        과거 PurchaseTask/PurchaseRecord의 channel_connection_id는
        절대 지우지 않는다")을 이 메서드도 그대로 지킨다: 이 연결을
        실제로 참조하는 PurchaseTask나 PurchaseRecord가 하나라도
        있으면(같은 회사 범위) 완전 삭제를 거부한다 — 과거 매입
        기록이 가리키는 계정이 갑자기 사라지는 것을 막기 위함이다.
        그런 연결은 "연결 해제"(비활성화)만 가능하다.

        CREDENTIAL 방식이면 Credential Manager에 남아있는 값도 함께
        정리한다(최선 노력 — 저장소 접근이 실패해도 연결 행 삭제
        자체는 막지 않는다, 값이 이미 없거나 접근 불가능한 경우가
        전부 정상적인 상황이기 때문)."""

        connection = self.get_connection_or_404(connection_id, company_id)

        task_ref_exists = (
            self.db.query(PurchaseTask.id)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.channel_connection_id == connection.id,
            )
            .first()
            is not None
        )
        record_ref_exists = (
            self.db.query(PurchaseRecord.id)
            .filter(
                PurchaseRecord.company_id == company_id,
                PurchaseRecord.channel_connection_id == connection.id,
            )
            .first()
            is not None
        )
        if task_ref_exists or record_ref_exists:
            raise ConflictException(
                "이 연결은 과거 구매 작업 또는 매입 기록에서 실제로 사용된 "
                "적이 있어 완전히 삭제할 수 없습니다 — \"연결 해제\"만 "
                "가능합니다(과거 기록이 가리키는 계정 정보를 보존하기 위함).",
            )

        if connection.connection_method == ConnectionMethod.CREDENTIAL and connection.credential_reference:
            from app.core.windows_credential_store import (
                CredentialNotFoundError, CredentialStoreError,
            )
            try:
                self._credential_store.delete(connection.credential_reference)
            except (CredentialNotFoundError, CredentialStoreError):
                pass

        (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(
                PurchaseChannelConnectionEvent.company_id == company_id,
                PurchaseChannelConnectionEvent.connection_id == connection.id,
            )
            .delete(synchronize_session=False)
        )
        self.db.delete(connection)
        self.db.commit()

    # ---------------- 매입 작업 실행 시점 연결 선택·검증 ----------------

    def select_connection_for_task(
        self, connection_id: int, company_id: int,
        *, expected_mall_code: str | None = None,
    ) -> PurchaseChannelConnection:
        """PurchaseTaskService가 어느 연결로 이 작업을 실행할지 배정할
        때 반드시 거쳐야 하는 검증. 호출자는 항상 connection_id를
        명시해야 한다 — 이 메서드는 후보 여러 개 중 하나를 대신 골라
        주지 않는다.

        2026-09-08 재정정(id=3/4 사고) — DB에 저장된 connection.status를
        그대로 신뢰하지 않는다. 이 상태가 실행 가능 여부를 가르는
        안전장치이므로, 실행 직전에 항상 현재 진실(자격증명 실제
        존재 여부 등, 전부 로컬 조회)로 다시 계산한 뒤에만 판단한다 —
        과거의 잘못된 self-attestation이나 자격증명 소실이 남긴 낡은
        CONNECTED 값으로 실행을 허용하지 않기 위함."""

        connection = self.repository.get_connection(connection_id, company_id)
        if connection is None:
            raise NotFoundException(
                "매입처 연결을 찾을 수 없습니다 — 같은 회사 소유의 연결인지 확인하세요.",
            )
        if not connection.is_active:
            raise ConflictException(
                "비활성화(연결 해제)된 매입처 연결로는 매입 작업을 실행할 수 없습니다.",
            )
        self._apply_status_recompute(connection, touch_last_checked=True)
        self.db.commit()
        self.db.refresh(connection)
        if connection.status not in ChannelConnectionStatus.USABLE_FOR_EXECUTION:
            raise ConflictException(
                f"이 연결은 아직 실행 가능한 상태가 아닙니다(현재 상태: "
                f"{connection.status}) — \"연결 확인 완료\" 후 다시 시도하세요.",
            )
        if expected_mall_code is not None and connection.mall_code != expected_mall_code:
            raise BadRequestException(
                "선택한 연결의 매입처가 이 작업의 매입처와 다릅니다.",
            )
        return connection

    def verify_connection_ready_for_order_submission(
        self, connection_id: int, company_id: int,
    ) -> PurchaseChannelConnection:
        """order_submission_service.py 전용 게이트. **select_connection_
        for_task()와 의도적으로 별개의 메서드다** — "매입 작업에 이
        연결을 배정해도 되는가"와 "지금 이 연결로 실제 발주 API를
        호출해도 되는가"는 다른 질문이다.

        2026-09-08 세 번째 정정("발주 준비 판정 수정") — 이전 버전은
        아래 (1)(2)만 확인하고 통과하면 사실상 "발주 준비 완료"로
        취급했다. 이제 판정을 서로 다른 사실 3가지로 명확히 나눈다.
        이 셋을 하나로 뭉뚱그리지 않는 것 자체가 이번 수정의 핵심이다.

        (1) **계정 배정 가능** — 연결이 활성 상태인가(비활성화된
            연결로는 아무것도 할 수 없다). select_connection_for_task()
            와 같은 기준이다.
        (2) **조회 인증 확인** — 실제 API 조회(상품/주문/상품목록)
            성공으로 CONNECTED가 됐고, 그 성공이 CREDENTIAL_
            REVERIFICATION_WINDOW_HOURS 안에 있는가(EXPIRED가
            아닌가). `ChannelConnectionStatus.USABLE_FOR_EXECUTION`은
            원래 "매입 작업 배정"을 위해 만든 상수이지 "실제 발주
            허용"을 뜻하지 않는다 — 이름과 실제 쓰임 사이의 괴리를
            여기 명시적으로 남긴다. (1)(2)는 "이 계정으로 온채널에
            정상적으로 인증된 요청을 보낼 수 있다"만 증명한다.
        (3) **실제 발주 실행 허용** — (1)(2)를 모두 만족해도, 온채널의
            공식 발주 계약 4개 항목(판매신청·결제 재원·중복 방지·
            결과불명 후 재조회 방법, constants.py의
            `OnchannelOrderContractItem`)이 **전부 각각** 근거·확인
            시각과 함께 확인되지 않는 한 이 조건은 거짓이다
            (`is_onchannel_order_contract_fully_confirmed()`).

            2026-09-09 후속("계약 상태 세분화") — 이전 버전은 이
            판정을 boolean 하나(`ONCHANNEL_ORDER_EXECUTION_CONTRACT_
            CONFIRMED`)로 뭉뚱그렸다. 감사 결과 그 설계의 위험이
            드러났다: 4개 질문 중 1개 답변만 받아도 그 값 하나를
            True로 바꾸면 나머지 3개가 여전히 미확인이어도 전체
            발주가 즉시 열릴 수 있었다. 지금은 4개 항목을 독립적으로
            추적하고 전부(all) 만족해야만 통과한다 — 답변이 하나씩
            도착해도 확인된 항목만 갱신되고 나머지가 남아있는 한
            이 메서드는 계속 막는다. 각 항목은 confirmed=True 만으로
            부족하고 공식 근거(official_basis)와 확인 시각
            (confirmed_at)이 함께 있어야 "제대로 확인됨"으로
            인정된다 — 근거 없이 값만 바꿔치기하는 것이 코드 구조상
            불가능하다.

            `order_submission_service.submit_order()`의
            `confirm_real_submission=False` 하드 기본값과 라우터
            미배선은 "지금 실행을 막아 둔 임시 조치"일 뿐 이 판정을
            대신하지 않는다 — 그 두 조치가 나중에 사라져도(라우터가
            배선되고 누군가 confirm_real_submission=True를 보내도)
            이 메서드 자체가 (3)에서 여전히 막는다."""

        connection = self.repository.get_connection(connection_id, company_id)
        if connection is None:
            raise NotFoundException(
                "매입처 연결을 찾을 수 없습니다 — 같은 회사 소유의 연결인지 확인하세요.",
            )
        if not connection.is_active:
            raise ConflictException(
                "비활성화(연결 해제)된 매입처 연결로는 실제 발주를 실행할 수 없습니다.",
            )
        if connection.connection_method != ConnectionMethod.CREDENTIAL:
            raise BadRequestException(
                "이 매입처는 API 기반 발주를 지원하지 않습니다(브라우저 로그인형).",
            )
        self._apply_status_recompute(connection, touch_last_checked=True)
        self.db.commit()
        self.db.refresh(connection)
        if connection.status not in ChannelConnectionStatus.USABLE_FOR_EXECUTION:
            raise ConflictException(
                f"이 연결은 조회 인증이 확인된 상태가 아닙니다(현재 상태: "
                f"{connection.status}) — 실제 상품 조회가 최근 "
                f"{CREDENTIAL_REVERIFICATION_WINDOW_HOURS}시간 안에 먼저 성공해야 합니다.",
            )
        if not is_onchannel_order_contract_fully_confirmed():
            remaining = ", ".join(unconfirmed_onchannel_order_contract_items())
            raise ConflictException(
                "계정 인증은 확인됐지만(조회 인증 확인), 온채널의 공식 발주 "
                f"계약 중 다음 항목이 아직 확인되지 않아 실제 발주 실행은 "
                f"허용되지 않습니다 — 공식 답변 확인 후에만 허용됩니다: "
                f"{remaining}",
            )
        return connection

    # ---------------- 자격증명(CREDENTIAL 방식 전용) ----------------

    def save_credential(
        self, connection_id: int, company_id: int, *, auth_key: str,
        allowed_ip: str = "", triggered_by: int | None = None,
    ) -> PurchaseChannelConnection:
        """CREDENTIAL 방식 연결(현재 온채널)에 API 키를 등록한다. 값
        자체는 이 함수를 거쳐 가기만 할 뿐 어디에도 로그로 남기지
        않는다(감사 이벤트에도 값 대신 등록 사실만 기록). 이 호출
        자체는 온채널에 아무 요청도 보내지 않는다 — 로컬 저장뿐이라
        등록한 값이 실제로 유효한지는 알 수 없다(등록됨 ≠ 검증됨,
        기존 규칙과 동일).

        2026-09-08 재정정(id=3/4 사고) — 저장(재저장 포함)할 때마다
        기존 verified_at을 무조건 지운다. 그렇지 않으면 과거에(예:
        지금은 제거된 mark_verified() 오남용 경로로, 또는 이전
        자격증명으로) 남은 CONNECTED 기록이 이번에 새로 저장하는
        자격증명의 성공인 것처럼 재사용된다 — "자격증명 변경 시 과거
        인증 성공을 현재 성공으로 재사용하지 않는다"는 원칙을 이
        지점에서 강제한다. 저장 자체가 실패하면(Credential Manager
        쓰기 예외) 아래 상태 변경·이벤트 기록·commit 전에 예외가
        전파되므로 "저장 성공"으로 잘못 표시될 수 없다."""

        connection = self.get_connection_or_404(connection_id, company_id)
        if connection.connection_method != ConnectionMethod.CREDENTIAL:
            raise BadRequestException(
                "이 매입처는 자격증명 등록 방식이 아닙니다(브라우저 로그인형).",
            )
        if not connection.credential_reference:
            # 정상 경로라면 create_connection()에서 이미 채워져 있어야
            # 한다 — 여기 걸리면 데이터 정합성 문제이므로 그 자리에서
            # 채워 복구한다(값 유실을 막는다).
            connection.credential_reference = f"homez_channel_connection_{connection.id}"
        if not auth_key or not auth_key.strip():
            raise BadRequestException("인증키를 입력해야 합니다.")

        self._credential_store.save(
            connection.credential_reference,
            {"auth_key": auth_key.strip(), "allowed_ip": allowed_ip},
        )

        connection.verified_at = None
        connection.status = ChannelConnectionStatus.REGISTERED_UNVERIFIED

        self._log_event(
            connection, ChannelConnectionEventType.STATUS_CHANGED,
            detail="자격증명 등록(값은 기록하지 않음) — 과거 연결 확인 기록 무효화",
            triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)
        return connection

    # ---------------- 실 API 조회(읽기 전용) ----------------

    def lookup_product(
        self, connection_id: int, company_id: int, external_product_id: str,
        *, triggered_by: int | None = None,
    ):
        """실제 매입처 API로 상품을 조회한다 — 이 메서드를 호출하는
        시점에 실제 네트워크 요청이 나간다(사용자 승인 범위 안에서만
        호출부가 이 메서드를 불러야 한다). 온채널 쪽 실패는 종류별로
        구분되는 예외를 그대로 다시 던진다.

        2026-09-08 재정정(id=3/4 사고 이후) — CREDENTIAL 방식 연결은
        이제 사람이 성공을 선언할 수 없으므로(mark_verified() 차단),
        이 실제 조회 성공이 유일한 "연결 확인" 경로다. 성공하면
        verified_at을 지금 채우고, 인증 관련 실패(401/403)면 이
        연결을 사용 불가로 낮춘다 — 그 외 실패(429/응답형식오류/
        네트워크오류)는 자격증명이 잘못됐다는 증거가 아니므로 상태를
        건드리지 않는다.

        2026-09-08 추가 재정정 — 요청 시작 시점과 응답을 실제로 저장
        하는 시점 사이(네트워크 왕복 시간 동안)에 자격증명이 재저장
        되거나 삭제될 수 있다. 그러면 방금 받은 성공 응답은 "지금"의
        자격증명이 아니라 그 순간 이미 대체된 과거 자격증명으로 얻은
        것이므로, 그 성공을 지금 저장된 자격증명의 연결 확인으로
        반영하지 않는다(`_read_credential_fingerprint()`로 요청 전후
        지문만 비교 — 값 자체는 절대 비교 결과 밖으로 나가지 않는다)."""

        connection = self.get_connection_or_404(connection_id, company_id)
        credential_fingerprint_before = self._read_credential_fingerprint(connection)
        adapter = get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )
        try:
            result = adapter.lookup_product(external_product_id)
        except Exception as exc:
            self._record_real_check_failure(connection, exc, triggered_by=triggered_by)
            raise
        self._record_real_check_success(
            connection, detail="실제 상품 조회 성공으로 연결 확인 기록",
            triggered_by=triggered_by,
            credential_fingerprint_before=credential_fingerprint_before,
        )
        return result

    def list_products(
        self, connection_id: int, company_id: int,
        *, page: int = 1, page_size: int = 1, triggered_by: int | None = None,
    ):
        """실제 매입처 API로 상품 "목록"을 조회한다 — item 7 승인된
        검증(`GET seller/product?page=1&page_size=1`) 전용. 특정
        상품 코드를 몰라도 호출 가능하다는 점만 `lookup_product`와
        다르고, 그 외 규칙(자격증명 버전 대조, 성공/실패 기록)은
        전부 동일하다."""

        connection = self.get_connection_or_404(connection_id, company_id)
        credential_fingerprint_before = self._read_credential_fingerprint(connection)
        adapter = get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )
        try:
            result = adapter.list_products(page=page, page_size=page_size)
        except Exception as exc:
            self._record_real_check_failure(connection, exc, triggered_by=triggered_by)
            raise
        self._record_real_check_success(
            connection, detail="실제 상품 목록 조회 성공으로 연결 확인 기록",
            triggered_by=triggered_by,
            credential_fingerprint_before=credential_fingerprint_before,
        )
        return result

    def check_member_point(
        self, connection_id: int, company_id: int,
        *, triggered_by: int | None = None,
    ):
        """발주·결제 계약 조사 승인 — GET common/member/point 진단
        호출 전용. 응답 필드가 스펙에 정의돼 있지 않아 구조 자체를
        확인하는 것이 목적이다 — 이 응답만으로 예치금·결제 방식을
        단정하지 않는다(호출자·화면 표시 책임).

        2026-09-08 재정정 — **이 메서드는 connection.status/verified_at
        을 절대 건드리지 않는다.** lookup_product()/lookup_order()/
        list_products()와 의도적으로 다르다 — "포인트 조회 성공"과
        "이 연결이 실제 조회로 확인됐다"(연결 확인 상태, 곧 발주
        실행 게이트의 전제조건)를 같은 사실로 취급하면, 포인트
        조회 성공만으로 발주 실행 게이트(verify_connection_ready_
        for_order_submission)가 열리게 된다 — 그건 이 메서드가 검증
        하려는 것과 무관한 부작용이다. 요청 발생 사실은 감사
        이벤트로만 남기고(성공/실패 모두), 상태는 그대로 둔다."""

        connection = self.get_connection_or_404(connection_id, company_id)
        adapter = get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )
        try:
            result = adapter.check_member_point()
        except Exception as exc:
            self._log_event(
                connection, ChannelConnectionEventType.STATUS_CHANGED,
                detail=(
                    f"포인트 조회 시도 실패({type(exc).__name__}) — 연결 확인 "
                    "상태에는 영향 없음"
                ),
                triggered_by=triggered_by,
            )
            self.db.commit()
            raise
        self._log_event(
            connection, ChannelConnectionEventType.STATUS_CHANGED,
            detail="포인트 조회 성공 — 연결 확인 상태에는 영향 없음(발주 권한 판정과 무관)",
            triggered_by=triggered_by,
        )
        self.db.commit()
        return result

    def lookup_order(
        self, connection_id: int, company_id: int, external_order_number: str,
        *, triggered_by: int | None = None,
    ):
        """실제 매입처 API로 주문을 조회한다 — lookup_product와 동일한
        승인 전제 및 동일한 연결 확인 기록 규칙(자격증명 버전 대조
        포함)."""

        connection = self.get_connection_or_404(connection_id, company_id)
        credential_fingerprint_before = self._read_credential_fingerprint(connection)
        adapter = get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )
        try:
            result = adapter.lookup_order(external_order_number)
        except Exception as exc:
            self._record_real_check_failure(connection, exc, triggered_by=triggered_by)
            raise
        self._record_real_check_success(
            connection, detail="실제 주문 조회 성공으로 연결 확인 기록",
            triggered_by=triggered_by,
            credential_fingerprint_before=credential_fingerprint_before,
        )
        return result

    def lookup_tracking(
        self, connection_id: int, company_id: int, external_order_number: str,
        *, triggered_by: int | None = None,
    ):
        """2026-09-10 후속(Phase 7) — 실제 매입처 API로 배송·송장
        정보를 조회한다(온채널은 GET seller/order/{code}의 deliverys
        배열을 재사용 — 별도 배송조회 엔드포인트가 스펙에 없음).
        lookup_order()와 동일한 승인 전제 및 연결 확인 기록 규칙."""

        connection = self.get_connection_or_404(connection_id, company_id)
        credential_fingerprint_before = self._read_credential_fingerprint(connection)
        adapter = get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )
        try:
            result = adapter.lookup_tracking(external_order_number)
        except Exception as exc:
            self._record_real_check_failure(connection, exc, triggered_by=triggered_by)
            raise
        self._record_real_check_success(
            connection, detail="실제 배송·송장 조회 성공으로 연결 확인 기록",
            triggered_by=triggered_by,
            credential_fingerprint_before=credential_fingerprint_before,
        )
        return result

    def _read_credential_fingerprint(
        self, connection: PurchaseChannelConnection,
    ) -> str | None:
        """이 연결의 credential_reference가 "지금" 가리키는 자격증명의
        지문(auth_key의 SHA-256 hex)만 반환한다 — 값 자체는 절대
        반환·기록·로그에 남기지 않는다. 자격증명이 없으면 None — 이
        역시 하나의 유효한 "버전" 상태다(요청 시작 시 있었는데 저장
        시점에 None이면, 또는 그 반대면 반드시 불일치로 잡혀야
        한다)."""

        if not connection.credential_reference:
            return None

        from app.core.windows_credential_store import (
            CredentialNotFoundError, CredentialStoreError,
        )

        try:
            credential = self._credential_store.read(connection.credential_reference)
        except (CredentialNotFoundError, CredentialStoreError):
            return None

        auth_key = str(credential.get("auth_key", ""))
        if not auth_key:
            return None

        import hashlib
        return hashlib.sha256(auth_key.encode("utf-8")).hexdigest()

    def _record_real_check_success(
        self, connection: PurchaseChannelConnection, *, detail: str,
        triggered_by: int | None, credential_fingerprint_before: str | None,
    ) -> None:

        credential_fingerprint_after = self._read_credential_fingerprint(connection)
        if (
            credential_fingerprint_before is None
            or credential_fingerprint_after != credential_fingerprint_before
        ):
            # 조회 도중(요청 시작~응답 저장 사이) 자격증명이 바뀌거나
            # 사라졌다 — 방금 받은 성공을 "지금" 자격증명의 연결
            # 확인으로 반영하지 않는다. 상태는 건드리지 않는다(이
            # 성공이 자격증명이 "틀렸다"는 증거는 아니다 — 단지 어느
            # 자격증명에 대한 성공인지 더 이상 확정할 수 없을 뿐).
            self._log_event(
                connection, ChannelConnectionEventType.STATUS_CHANGED,
                detail=(
                    "조회 도중 자격증명이 변경되어 이번 조회 성공을 연결 "
                    "확인에 반영하지 않음"
                ),
                triggered_by=triggered_by,
            )
            self.db.commit()
            self.db.refresh(connection)
            return

        now = datetime.utcnow()
        connection.verified_at = now
        connection.last_checked_at = now
        connection.status = ChannelConnectionStatus.CONNECTED
        self._log_event(
            connection, ChannelConnectionEventType.VERIFIED,
            detail=detail, triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)

    def _record_real_check_failure(
        self, connection: PurchaseChannelConnection, exc: Exception,
        *, triggered_by: int | None,
    ) -> None:
        """인증이 명백히 잘못됐다는 신호(401/403)일 때만 연결을 사용
        불가로 낮춘다. 자격증명 자체가 없어서 나는 PurchaseChannelAdapterError,
        일시적 오류(429/응답형식오류/네트워크오류)는 "자격증명이
        잘못됐다"는 증거가 아니므로 여기서 상태를 건드리지 않는다."""

        from app.domains.purchase_task.onchannel_client import (
            OnchannelAuthenticationError, OnchannelPermissionError,
        )

        if not isinstance(exc, (OnchannelAuthenticationError, OnchannelPermissionError)):
            return

        connection.verified_at = None
        connection.last_checked_at = datetime.utcnow()
        connection.status = ChannelConnectionStatus.ERROR
        self._log_event(
            connection, ChannelConnectionEventType.STATUS_CHANGED,
            detail="실제 API 인증 실패로 이 연결을 사용 불가로 기록",
            triggered_by=triggered_by,
        )
        self.db.commit()
        self.db.refresh(connection)

    # ---------------- 내부 ----------------

    def _log_event(
        self, connection: PurchaseChannelConnection, event_type: str,
        *, detail: str | None, triggered_by: int | None,
    ) -> None:

        event = PurchaseChannelConnectionEvent(
            company_id=connection.company_id, connection_id=connection.id,
            event_type=event_type, detail=detail, triggered_by=triggered_by,
        )
        self.repository.add_connection_event(event)


__all__ = ["PurchaseChannelConnectionService"]
