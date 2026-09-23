"""
=========================================================
Homez OS

File : tests/test_purchase_order_approval_survives_option_link_change.py

2026-09-23 후속(DB 결정 재확인·격리 검증 보완 라운드) — 옵션 연결
(SupplierOptionLink, 2026-09-21 채택)과 발주 승인 스냅샷(PurchaseOrderApproval,
2026-09-11/15) 두 기존 메커니즘이 실제로 맞물릴 때의 계약을 통합
검증한다. 기존 tests/test_purchase_order_approval_service.py의
OptionBindingTestCase는 옵션 스냅샷 불일치 차단을 서비스 단위로 이미
증명했고, tests/test_purchase_order_submission_service.py의
OrderApprovalGateIntegrationTestCase는 유효한 승인이 실제 Adapter까지
도달함을 증명한다 — 이 파일은 그 둘을 다음 순서로 이어 붙인 통합
시나리오만 새로 검증한다: 승인 생성 -> (공급처 옵션 연결이 바뀌었다고
가정한) 다른 옵션으로 실행 시도 -> 차단 + 실제 발주 함수(Adapter.
submit_order) 미호출 확인(spy) -> 승인 스냅샷 자체는 전혀 바뀌지
않았음을 DB 재조회로 확인 -> 올바른 복구 경로(새 옵션으로 재승인)는
실제로 성공함을 확인.

실제 homez.db·실제 Windows Credential Manager·실제 외부 API는 전혀
접촉하지 않는다(tests.test_purchase_order_submission_service의
OrderSubmissionServiceTestCaseBase를 그대로 재사용 — 임시 파일 SQLite
+ InMemoryCredentialStore + Fake Adapter만 사용).
=========================================================
"""

from decimal import Decimal
from unittest import mock

from app.core.exceptions import ConflictException
from app.domains.purchase_task.constants import (
    OrderSubmissionStatus,
    PurchaseOrderApprovalStatus,
    ShippingCostConfirmationSource,
)
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.order_approval_service import (
    PurchaseOrderApprovalService,
)

from tests.test_purchase_order_submission_service import (
    OrderSubmissionServiceTestCaseBase,
    VALID_KWARGS,
)


class ApprovalSurvivesOptionLinkChangeTestCase(OrderSubmissionServiceTestCaseBase):

    def setUp(self):
        super().setUp()
        self._patch_contract_confirmed()
        self.connection = self._make_ready_connection()
        self.approval_service = PurchaseOrderApprovalService(self.db)
        for task_id in (501, 502):
            self.db.add(PurchaseTask(
                id=task_id, company_id=self.company_a.id, source_order_id=task_id,
                product_title=f"테스트 상품 {task_id}", quantity=1,
                idempotency_key=f"seed-task-{task_id}",
                channel_connection_id=self.connection.id,
                coupang_sale_amount=30000.0, coupang_fee_amount=3000.0,
            ))
        self.db.commit()

    def _finalize(self, task_id, *, options):

        self.approval_service.confirm_shipping_cost(
            self.connection.id, self.company_a.id, task_id, "CH1234567",
            shipping_cost_amount=3000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        return self.approval_service.finalize_approval(
            self.connection.id, self.company_a.id, task_id,
            item_amount=10000, current_points=1_000_000, triggered_by=1,
            options=options,
        )

    def _approval_row(self, task_id):

        self.db.expire_all()
        return (
            self.db.query(PurchaseOrderApproval)
            .filter(PurchaseOrderApproval.purchase_task_id == task_id)
            .first()
        )

    def _install_adapter(self, *, call_log):
        """두 옵션(OPT-BLACK·OPT-GRAY2) 모두 동일 단가(10000)로 응답한다
        — "금액은 같아도 옵션 구성이 다르면 차단되는가"를 가격 차이와
        섞이지 않게 분리해서 보기 위함이다."""

        class _Opt:
            def __init__(self, option_id, price):
                self.option_id = option_id
                self.label = "기본"
                self.price = price
                self.in_stock = True

        class _Product:
            support = "SUPPORTED"
            external_product_id = "CH1234567"
            title = "테스트 상품"
            detail = "FAKE"
            options = (_Opt("OPT-BLACK", Decimal("10000")), _Opt("OPT-GRAY2", Decimal("10000")))

        class _Point:
            support = "SUPPORTED"
            member_id_masked = "t***"
            point = 1_000_000
            point_interpretable = True
            observed_fields = ("member_id", "point")
            detail = "FAKE"

        class _SalesAppResult:
            support = "SUPPORTED"
            submitted = True

            def __init__(self, code):
                self.applied_product_code = code

        class _FakeAdapter:
            def check_member_point(self_inner):
                return _Point()

            def lookup_product(self_inner, external_product_id):
                return _Product()

            def apply_for_sale(self_inner, external_product_id):
                return _SalesAppResult(external_product_id)

            def submit_order(self_inner, request):
                call_log.append(request)
                return "ORDER-CODE-FAKE"

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service."
            "get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stale_snapshot_still_reaches_adapter_when_options_unchanged(self):
        """대조군 — 연결이 전혀 바뀌지 않았으면(승인 시점과 실행 시도
        시점의 옵션이 동일) 기존 설계대로 그대로 실행돼 실제 발주
        함수까지 도달해야 한다. 이 대조군이 실패하면 아래 차단
        테스트의 "0회 호출"이 차단 때문인지 픽스처 결함 때문인지
        구분할 수 없다."""

        options = [{"id": "OPT-BLACK", "qty": 1}]
        self._finalize(502, options=options)

        call_log = []
        self._install_adapter(call_log=call_log)
        kwargs = dict(VALID_KWARGS)
        kwargs["product_code"] = "CH1234567"
        kwargs["options"] = options
        attempt = self.service.submit_order(
            self.connection.id, self.company_a.id,
            idempotency_key="k-control-unchanged",
            purchase_task_id=502, confirm_real_submission=True, **kwargs,
        )

        self.assertEqual(len(call_log), 1)
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)

    def test_link_change_blocks_execution_without_touching_snapshot(self):
        """본 시나리오 — 승인 이후 공급처 옵션 연결이 바뀌었다고
        가정한다(재조회하면 다른 옵션이 나온다). 그 새 옵션으로 실행을
        시도하면 차단되고, 실제 발주 함수는 전혀 호출되지 않아야
        한다. 승인 행의 옵션 스냅샷 자체는 이 시도 전후로 전혀
        바뀌지 않아야 한다(연결이 바뀌었다는 사실만으로 이미 만들어진
        승인이 조용히 갱신되는 코드 경로가 없어야 한다)."""

        approved_options = [{"id": "OPT-BLACK", "qty": 1}]
        self._finalize(501, options=approved_options)
        snapshot_before = self._approval_row(501).options_snapshot_json
        self.assertIn("OPT-BLACK", snapshot_before)

        # 연결이 바뀐 뒤 재조회하면 나올 값(2026-09-23 8차 라운드에서
        # 실제 브라우저로 save->reload->재조회까지 이미 실증된 형태를
        # 그대로 사용한다 — 이 값 자체를 만들어내는 resolve_for_task()
        # 재계산 경로는 여기서 다시 검증하지 않는다).
        changed_link_options = [{"id": "OPT-GRAY2", "qty": 1}]  # 금액은 동일(10000)

        call_log = []
        self._install_adapter(call_log=call_log)
        kwargs = dict(VALID_KWARGS)
        kwargs["product_code"] = "CH1234567"
        kwargs["options"] = changed_link_options
        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                self.connection.id, self.company_a.id,
                idempotency_key="k-blocked-by-link-change",
                purchase_task_id=501, confirm_real_submission=True, **kwargs,
            )
        self.assertIn("옵션", str(ctx.exception))
        self.assertEqual(
            call_log, [],
            "옵션 스냅샷과 다른 구성으로는 실제 발주 함수(Adapter.submit_order)에 "
            "도달하면 안 된다.",
        )

        approval_after = self._approval_row(501)
        self.assertEqual(
            approval_after.status, PurchaseOrderApprovalStatus.INVALIDATED_PRICE_CHANGE,
        )
        self.assertEqual(
            approval_after.options_snapshot_json, snapshot_before,
            "차단 시도 이후에도 스냅샷 '값' 자체는 승인 시점 그대로여야 한다 — "
            "무효화는 status 필드로만 표시되고 스냅샷을 덮어쓰지 않는다.",
        )

    def test_reapproval_with_new_options_then_succeeds(self):
        """차단 이후의 올바른 복구 경로 — 같은 작업에 새(연결 변경 후)
        옵션으로 배송비 확인부터 다시 밟아 재승인하면, 그 새 승인으로는
        실제 발주 함수까지 정상 도달해야 한다(재승인 조건이 실제로
        열려 있는지 확인 — 무한정 차단되는 것이 설계 의도가 아니다)."""

        self._finalize(501, options=[{"id": "OPT-BLACK", "qty": 1}])

        new_options = [{"id": "OPT-GRAY2", "qty": 1}]
        self._finalize(501, options=new_options)  # 같은 작업, 새 옵션으로 재승인
        self.assertEqual(
            self._approval_row(501).status, PurchaseOrderApprovalStatus.ACTIVE,
        )

        call_log = []
        self._install_adapter(call_log=call_log)
        kwargs = dict(VALID_KWARGS)
        kwargs["product_code"] = "CH1234567"
        kwargs["options"] = new_options
        attempt = self.service.submit_order(
            self.connection.id, self.company_a.id,
            idempotency_key="k-reapproved-succeeds",
            purchase_task_id=501, confirm_real_submission=True, **kwargs,
        )

        self.assertEqual(len(call_log), 1)
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
