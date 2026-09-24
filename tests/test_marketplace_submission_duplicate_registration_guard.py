"""
=========================================================
Homez OS

File : tests/test_marketplace_submission_duplicate_registration_guard.py

2026-09-24 후속(자동 재신청 방지 라운드 3 — 중복 상품등록 차단).
같은 상품 후보·같은 판매계정으로 위저드를 두 개 만들면(정상적인
사용 흐름 — 예: 첫 위저드가 결과불명으로 멈춰 사람이 새로 다시
만든 경우) 두 번째 위저드의 제출(MarketplaceSubmission)은 idempotency_
key가 달라 기존 UNIQUE 제약을 우회하지만, 둘 다 같은
MarketplaceListing(같은 candidate+account 조합은 UNIQUE라 하나뿐)을
가리킨다. 이 파일은 그 상태에서 (1) 애플리케이션 레벨 검사
(`ListingWizardLiveService.preflight()`)가 두 번째 제출의 실제 전송을
막는지, (2) 그 검사를 우회하는 경쟁 상태에서도 DB 부분 UNIQUE
INDEX(`uq_marketplace_submissions_live_claim_per_listing`, 이번
라운드에서 설계·격리 리허설만 함, 원본 DB 미적용)가 최종 방어선
역할을 하는지를 검증한다.

두 번째 위저드는 **첫 번째 위저드를 실제로 전송(send)해 결과를 받은
뒤에** 만든다 — 실제 사용 흐름과 같은 순서다(첫 시도가 결과불명으로
끝난 뒤에야 사람이 새 위저드를 만든다). 이 순서를 지켜야 하는 이유는
`MarketplaceFulfillmentSelection`이 listing당 "현재 선택"을 하나만
유지하는 기존 설계(새 선택이 이전 선택을 SUPERSEDE) 때문이다 — 두
위저드를 먼저 다 만든 뒤 첫 번째를 나중에 보내려 하면 그 사이 두
번째가 만든 새 선택이 첫 번째의 선택을 SUPERSEDE해 버려 첫 번째를
보낼 수 없게 된다(이 파일이 검증하려는 중복등록 문제와는 무관한,
기존 선택 관리 설계와의 상호작용일 뿐이다 — 그래서 순서를 바꿔
피한다). 경쟁(claim) 자체를 테스트하는 클래스만 예외적으로 두
제출을 모두 만든 뒤 각자 새로 승인받아 독립시킨다(그 클래스의
docstring 참고).

기존 위저드 테스트 픽스처(`tests.test_coupang_live_submission.
ListingWizardLiveServiceTestCase`)를 **상속하지 않고 인스턴스로
감싼다** — 상속하면 그 파일(및 그것이 상속하는 test_listing_wizard_
service.py)의 테스트 전부가 이 모듈에서도 다시 실행된다(이미
`tests/test_listing_wizard_option_links.py`가 같은 이유로 인스턴스
합성 방식을 쓰고 있다).

실제 homez.db·실제 Credential Manager·실제 외부 API는 전혀 접촉하지
않는다(임시 파일 SQLite + Fake Provider만 사용).
=========================================================
"""

import copy
import json
import threading
import unittest

from app.core.exceptions import ForbiddenException
from app.domains.marketplace_listing.coupang_live_provider import (
    LiveSubmissionResult,
)
from app.domains.marketplace_listing.listing_wizard_live_service import (
    ListingWizardLiveService,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardApproveRequest,
    WizardSubmitRequest,
)
from app.domains.marketplace_listing.model import MarketplaceSubmission

import tests.test_coupang_live_submission as live_tests

LIVE_IMAGES = live_tests.LIVE_IMAGES
LIVE_NOTICES = live_tests.LIVE_NOTICES
LIVE_CONTENTS = live_tests.LIVE_CONTENTS


class _DelayedProvider:
    """claim(UPDATE) 지점 자체의 동시성은 `threading.Barrier`로
    결정론적으로 재현하므로 인위적 sleep이 필요 없다. 스파이 목적으로
    호출 payload만 기록한다."""

    def __init__(self, result=None):
        self.result = result or LiveSubmissionResult(
            outcome="SUBMITTED", external_reference="CP-FIRST-OK",
        )
        self.calls = []

    def create_product(self, payload):
        self.calls.append(payload)
        return self.result


class _Fixture:
    """live_tests.ListingWizardLiveServiceTestCase를 인스턴스로 감싼
    합성 픽스처. 이 클래스 자신은 unittest.TestCase가 아니므로 그
    안의 test_* 메서드가 다시 수집되지 않는다."""

    def __init__(self, harness):
        self.h = harness
        self.db = harness.db
        self.company_id = harness.company_id
        self.other_company_id = harness.other_company_id
        self.db_path = harness.db_path
        self.engine = harness.engine

    def build_submission(self, candidate, account, media):
        """위저드를 완주(승인→제출)만 하고 send()는 호출하지 않는다.
        `candidate`·`account`를 공유해서 다시 호출하면(=두 번째
        위저드) 같은 MarketplaceListing을 가리키는 완전히 새 제출이
        생긴다."""

        h = self.h
        wizard = h._advance_to_ready_for_approval(candidate, account, media)

        entries = copy.deepcopy(json.loads(wizard.channel_selections_json))
        fields = entries[0]["required_fields"]
        fields["displayCategoryCode"] = 80754
        fields["notices"] = LIVE_NOTICES
        fields["images"] = LIVE_IMAGES
        fields["contents"] = LIVE_CONTENTS
        fields["liveImageRightsConfirmed"] = True
        fields["outboundShippingPlaceCode"] = "88001"
        fields["vendorUserId"] = "sin945"
        fields["deliveryCompanyCode"] = "CJGLS"
        fields["brandState"] = "NO_BRAND"
        fields["brand"] = "HOMEZ"
        wizard.channel_selections_json = json.dumps(entries, ensure_ascii=False)
        self.db.commit()

        preview = h.service.approval_preview(wizard.id, self.company_id)
        approved = h.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=h._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )
        submitted = h.service.submit(
            wizard.id, self.company_id, approved_by=99,
            data=WizardSubmitRequest(
                expected_version=approved.version, execution_mode="SUBMIT",
            ),
        )
        result = h.service.results(submitted.id, self.company_id).channels[0]
        return submitted, result

    def send(self, wizard, submission_id, provider):

        return ListingWizardLiveService(self.db).send(
            wizard.id, submission_id, self.company_id, provider,
        )

    def preflight_for(self, wizard, submission_id):

        return ListingWizardLiveService(self.db).preflight(
            wizard.id, submission_id, self.company_id,
        )

    def submission_row(self, submission_id):

        return self.db.query(MarketplaceSubmission).filter(
            MarketplaceSubmission.id == submission_id,
            MarketplaceSubmission.company_id == self.company_id,
        ).one()


def _make_fixture() -> _Fixture:

    harness = live_tests.ListingWizardLiveServiceTestCase(
        "test_success_persists_external_reference_and_duplicate_is_blocked",
    )
    harness.setUp()
    return _Fixture(harness)


class ApplicationLevelDuplicateGuardTestCase(unittest.TestCase):

    def setUp(self):
        self.fx = _make_fixture()
        self.addCleanup(self.fx.h.doCleanups)
        self.addCleanup(self.fx.h.tearDown)

    def test_second_submission_blocked_after_first_reaches_unknown(self):
        """1번 제출을 실제로 전송해 결과불명(UNKNOWN)으로 남긴 뒤,
        그제서야 같은 candidate+account로 2번 위저드를 새로 만들어
        보내려 하면 preflight()가 막고 provider.create_product()는
        전혀 호출되지 않아야 한다."""

        fx = self.fx
        candidate, _channel, account, media = fx.h._full_setup()

        wizard_1, result_1 = fx.build_submission(candidate, account, media)
        provider_1 = _DelayedProvider(
            result=LiveSubmissionResult(
                outcome="UNKNOWN", error_code="TIMEOUT",
                error_summary="응답 없음",
            ),
        )
        send_1 = fx.send(wizard_1, result_1.submission_id, provider_1)
        self.assertEqual(send_1.outcome, "UNKNOWN")
        self.assertEqual(len(provider_1.calls), 1)

        wizard_2, result_2 = fx.build_submission(candidate, account, media)
        self.assertEqual(result_1.listing_id, result_2.listing_id)
        self.assertNotEqual(result_1.submission_id, result_2.submission_id)

        preflight_2 = fx.preflight_for(wizard_2, result_2.submission_id)
        self.assertIn("DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING", preflight_2.blockers)
        self.assertFalse(preflight_2.ready)

        provider_2 = _DelayedProvider()
        with self.assertRaises(ForbiddenException) as ctx:
            fx.send(wizard_2, result_2.submission_id, provider_2)
        self.assertIn("DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING", str(ctx.exception))
        self.assertEqual(
            provider_2.calls, [],
            "이미 같은 listing에 결과불명 실제 전송이 있으면 두 번째 "
            "전송은 Adapter까지 도달하면 안 된다.",
        )

    def test_second_submission_blocked_after_first_succeeds(self):
        """1번 제출이 실제로 SUBMITTED(성공)했으면, 같은 listing에
        대한 2번 제출은 영구히 막혀야 한다(성공한 상품을 다시
        등록하지 않는다)."""

        fx = self.fx
        candidate, _channel, account, media = fx.h._full_setup()

        wizard_1, result_1 = fx.build_submission(candidate, account, media)
        provider_1 = _DelayedProvider(
            result=LiveSubmissionResult(
                outcome="SUBMITTED", external_reference="CP-ALREADY-LIVE",
            ),
        )
        send_1 = fx.send(wizard_1, result_1.submission_id, provider_1)
        self.assertEqual(send_1.outcome, "SUBMITTED")

        wizard_2, result_2 = fx.build_submission(candidate, account, media)
        provider_2 = _DelayedProvider()
        with self.assertRaises(ForbiddenException):
            fx.send(wizard_2, result_2.submission_id, provider_2)
        self.assertEqual(provider_2.calls, [])

    def test_second_submission_allowed_after_first_is_explicit_failure(self):
        """대조군 — 1번 제출이 실제로 명시적 거절(FAILED)로 끝났으면
        (결과가 확실함), 같은 listing에 대한 정상적인 수정 후 재등록
        (2번 제출)은 막지 않아야 한다."""

        fx = self.fx
        candidate, _channel, account, media = fx.h._full_setup()

        wizard_1, result_1 = fx.build_submission(candidate, account, media)
        provider_1 = _DelayedProvider(
            result=LiveSubmissionResult(
                outcome="FAILED", error_code="VALIDATION_ERROR",
                error_summary="필수 필드 누락",
            ),
        )
        send_1 = fx.send(wizard_1, result_1.submission_id, provider_1)
        self.assertEqual(send_1.outcome, "FAILED")

        wizard_2, result_2 = fx.build_submission(candidate, account, media)
        provider_2 = _DelayedProvider(
            result=LiveSubmissionResult(
                outcome="SUBMITTED", external_reference="CP-RETRY-OK",
            ),
        )
        send_2 = fx.send(wizard_2, result_2.submission_id, provider_2)
        self.assertEqual(send_2.outcome, "SUBMITTED")
        self.assertEqual(len(provider_2.calls), 1)

    def test_different_company_not_affected(self):
        """다른 회사가 같은 submission_id 숫자를 조회하면 애초에
        NotFoundException이어야 한다(company 스코프) — 교차 회사
        오탐이 없다는 것을 확인한다."""

        from app.core.exceptions import NotFoundException

        fx = self.fx
        candidate, _channel, account, media = fx.h._full_setup()
        wizard_1, result_1 = fx.build_submission(candidate, account, media)
        provider_1 = _DelayedProvider(
            result=LiveSubmissionResult(outcome="UNKNOWN", error_code="TIMEOUT"),
        )
        fx.send(wizard_1, result_1.submission_id, provider_1)

        with self.assertRaises(NotFoundException):
            ListingWizardLiveService(fx.db).preflight(
                wizard_1.id, result_1.submission_id, fx.other_company_id,
            )


class DbLevelRaceProtectionTestCase(unittest.TestCase):
    """부분 UNIQUE INDEX(uq_marketplace_submissions_live_claim_per_
    listing) 자체가 claim 지점의 경쟁을 막는지 확인한다. 이 인덱스는
    이번 라운드에서 설계·격리 리허설만 했고 원본 DB에는 적용하지
    않았다 — 그래서 이 테스트는 그 인덱스를 **이 테스트 전용 임시
    DB에서만** 직접 생성한 뒤 검증한다(원본 스키마를 건드리지 않음).

    이 클래스만 예외적으로 두 위저드를 먼저 다 만든 뒤(각자 다른
    selection을 갖게 됨 — 두 번째가 첫 번째를 SUPERSEDE) 각자 새로
    승인받아 둘 다 독립적으로 "전송 가능(PENDING)" 상태로 만든다 —
    claim 경쟁 자체를 보려면 둘 다 아직 전송 전이어야 하기 때문이다."""

    def setUp(self):
        self.fx = _make_fixture()
        self.addCleanup(self.fx.h.doCleanups)
        self.addCleanup(self.fx.h.tearDown)

        # 이 인덱스는 모델(app/domains/marketplace_listing/model.py::
        # MarketplaceSubmission.__table_args__)에 이미 선언돼 있어,
        # 기존 픽스처의 Base.metadata.create_all()이 자동으로
        # 만들어 준다 — 원본 DB에는 아직 이 Migration이 적용되지
        # 않았지만(별도 승인 대상), 모델 자체는 이미 이번 라운드에서
        # 갱신했으므로 임시 SQLite 픽스처에서는 항상 함께 생성된다.
        # 그래서 여기서 수동으로 다시 만들지 않는다(중복 생성 시도는
        # "already exists" 오류가 된다) — 존재만 재확인한다.
        from sqlalchemy import text as sa_text

        with self.fx.engine.connect() as conn:
            row = conn.execute(sa_text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_marketplace_submissions_live_claim_per_listing'",
            )).fetchone()
        self.assertIsNotNone(
            row, "모델에 선언한 부분 UNIQUE INDEX가 임시 DB에 자동 생성돼야 한다.",
        )

    def _two_pending_submissions_same_listing(self):
        """claim 경쟁 자체(§부분 UNIQUE INDEX)를 보려면 **같은 listing에
        대해 동시에 유효한 승인을 가진 두 제출**이 필요하다. 위저드를
        두 개 만들면 두 번째가 첫 번째의 selection을 SUPERSEDE해
        버려(기존 "listing당 현재 선택 하나" 설계) 첫 번째가 다시는
        승인받을 수 없게 된다 — 그래서 여기서는 위저드를 **하나만**
        완주시켜 유효한 (listing, selection, 승인)을 만든 뒤,
        `SubmissionService.submit()`을 **직접** 새 idempotency_key로
        한 번 더 호출해 같은 listing·selection·승인을 공유하는 두
        번째 MarketplaceSubmission 행을 만든다 — 이것이 정확히
        지시문이 말하는 "새 idempotency_key를 쓴 신규 등록 시도"다
        (제출 서비스 자신은 "이미 같은 listing에 제출이 있는지"를
        확인하지 않는다 — `listing.status` 표시값 갱신은 있지만
        rowcount를 무시하는 순수 표시용이라 실제로 두 번째 호출을
        막지 않는다는 것을 코드로 확인했다)."""

        from uuid import uuid4

        from app.domains.marketplace_listing.schema import (
            MarketplaceSubmissionRequest,
        )
        from app.domains.marketplace_listing.submission_service import (
            SubmissionService,
        )

        fx = self.fx
        h = fx.h
        candidate, _channel, account, media = h._full_setup()

        wizard, result_1 = fx.build_submission(candidate, account, media)
        submission_1 = fx.submission_row(result_1.submission_id)

        submission_2 = SubmissionService(fx.db).submit(
            MarketplaceSubmissionRequest(
                listing_id=submission_1.listing_id,
                selection_id=submission_1.selection_id,
                idempotency_key=f"manual-second-attempt-{uuid4().hex}",
            ),
            company_id=fx.company_id,
        )

        self.assertEqual(submission_1.listing_id, submission_2.listing_id)
        self.assertNotEqual(submission_1.id, submission_2.id)

        class _Result:
            def __init__(self, submission_id, listing_id):
                self.submission_id = submission_id
                self.listing_id = listing_id

        result_2 = _Result(submission_2.id, submission_2.listing_id)
        # 두 제출 모두 같은 wizard의 materialized_listing_ids_json에
        # 이미 등록된 (listing, account) 조합을 가리키므로,
        # ListingWizardLiveService._context()는 같은 wizard_id로 두
        # 제출 모두 정상적으로 조회한다(그 검사는 submission_id별이
        # 아니라 listing_id+account_id 단위이기 때문 — 코드로 확인).
        return wizard, result_1, wizard, result_2

    def test_index_rejects_second_concurrent_claim_at_db_level(self):
        """인덱스 자체가 동시 claim을 거부하는지 순수 SQL로 먼저
        확인한다(스레드 없이 — 제약 자체의 존재와 동작 확인)."""

        _s1, result_1, _s2, result_2 = self._two_pending_submissions_same_listing()

        from sqlalchemy import text as sa_text
        from sqlalchemy.exc import IntegrityError

        self.fx.db.execute(sa_text(
            "UPDATE marketplace_submissions SET status='SUBMITTING', "
            "correlation_id='corr-1' WHERE id=:id",
        ), {"id": result_1.submission_id})
        self.fx.db.commit()

        with self.assertRaises(IntegrityError):
            self.fx.db.execute(sa_text(
                "UPDATE marketplace_submissions SET status='SUBMITTING', "
                "correlation_id='corr-2' WHERE id=:id",
            ), {"id": result_2.submission_id})
            self.fx.db.commit()
        self.fx.db.rollback()

    def test_real_threads_race_to_claim_same_listing_only_one_wins(self):
        """실제 스레드 두 개, 서로 다른 ORM 세션(같은 파일 DB에 각자
        새로 접속) — ORM 세션을 공유하지 않는다. 각 스레드는 자신의
        `send()`가 내부적으로 호출하는 preflight()를 **둘 다 통과한
        뒤**(이 시점엔 어느 쪽도 아직 claim하지 않았으므로 애플리케이션
        레벨 검사는 정직하게 "정상"이라고 판정한다 — 이것이 SELECT
        기반 검사의 근본적 한계다), `threading.Barrier(2)`로 실제
        claim(UPDATE) 직전 지점에서 서로를 기다렸다가 **거의 동시에**
        UPDATE를 시도한다. 애플리케이션 레벨 검사만 있었다면 이 시점의
        둘 다 "문제 없음"으로 이미 판정된 뒤이므로 그대로 통과했을
        것이다 — 실제로 이 경쟁을 막는 것은 부분 UNIQUE INDEX뿐임을
        이 테스트가 증명한다."""

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        submitted_1, result_1, submitted_2, result_2 = (
            self._two_pending_submissions_same_listing()
        )

        claim_barrier = threading.Barrier(2, timeout=10)
        outcomes = {}

        def _wrap_claim(repo):
            """`send()`는 claim(선점) 시점과 결과 확정(finalize) 시점
            둘 다 `update_submission_status_conditional`을 호출한다 —
            경쟁을 재현할 지점은 **첫 번째(claim)** 호출뿐이다. 두
            번째(finalize) 호출까지 바리어를 태우면, claim에서 진
            스레드는 그 뒤로 다시 오지 않아 이긴 스레드가 두 번째
            바리어에서 영원히 기다리게 된다 — 그래서 최초 1회만
            동기화한다."""

            original = repo.update_submission_status_conditional
            state = {"waited": False}

            def wrapped(*args, **kwargs):
                if not state["waited"]:
                    state["waited"] = True
                    claim_barrier.wait()
                return original(*args, **kwargs)

            repo.update_submission_status_conditional = wrapped

        def run_thread(label, wizard, submission_id, external_ref):
            engine = create_engine(
                f"sqlite:///{self.fx.db_path}",
                connect_args={"timeout": 30},
            )
            SessionLocal = sessionmaker(bind=engine)
            db = SessionLocal()
            try:
                live = ListingWizardLiveService(db)
                _wrap_claim(live.marketplace)
                provider = _DelayedProvider(
                    result=LiveSubmissionResult(
                        outcome="SUBMITTED", external_reference=external_ref,
                    ),
                )
                result = live.send(
                    wizard.id, submission_id, self.fx.company_id, provider,
                )
                outcomes[label] = ("OK", result.outcome, len(provider.calls))
            except Exception as exc:  # noqa: BLE001
                outcomes[label] = ("ERROR", type(exc).__name__, str(exc))
            finally:
                db.close()
                engine.dispose()

        thread_1 = threading.Thread(
            target=run_thread,
            args=("t1", submitted_1, result_1.submission_id, "CP-T1"),
        )
        thread_2 = threading.Thread(
            target=run_thread,
            args=("t2", submitted_2, result_2.submission_id, "CP-T2"),
        )
        thread_1.start()
        thread_2.start()
        thread_1.join(timeout=15)
        thread_2.join(timeout=15)

        self.assertIn("t1", outcomes)
        self.assertIn("t2", outcomes)

        results = [outcomes["t1"], outcomes["t2"]]
        ok_results = [r for r in results if r[0] == "OK"]
        error_results = [r for r in results if r[0] == "ERROR"]

        self.assertEqual(
            len(ok_results), 1,
            f"두 스레드 중 정확히 하나만 실제 전송에 성공해야 한다: {outcomes}",
        )
        self.assertEqual(
            len(error_results), 1,
            f"나머지 하나는 부분 UNIQUE INDEX 위반으로 막혀야 한다: {outcomes}",
        )
        self.assertEqual(
            error_results[0][1], "ConflictException",
            f"패자는 IntegrityError를 잡아 ConflictException으로 변환한 "
            f"경로를 타야 한다(다른 예외면 경쟁이 다른 지점에서 걸렸다는 "
            f"뜻이라 이 테스트의 목적과 다르다): {outcomes}",
        )
        self.assertEqual(ok_results[0][2], 1, "승자는 provider를 정확히 1회 호출해야 한다.")

        self.fx.db.expire_all()
        rows = (
            self.fx.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.id.in_(
                [result_1.submission_id, result_2.submission_id],
            ))
            .all()
        )
        claimed_rows = [r for r in rows if r.correlation_id is not None]
        self.assertEqual(
            len(claimed_rows), 1,
            "두 행 중 정확히 하나만 실제로 claim(correlation_id 설정)돼야 "
            "한다 — 두 스레드가 서로 다른 세션이었는데도 인덱스가 "
            "하나만 통과시켰다는 증거.",
        )


if __name__ == "__main__":
    unittest.main()
