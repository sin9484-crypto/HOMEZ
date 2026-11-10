"""
=========================================================
Homez OS

File : app/domains/media_asset/ai_background_service.py

2026-11-10 — **AI 배경으로 만들기**(사용자 결정 "cloudflare로 진행해라"). Cloudflare Workers AI가 **상품 없는 배경 장면**만 그리고, HOMEZ가 그 위에
실제 누끼를 PC 안에서 붙인다(`ConceptRunService.compose_and_save` — 로컬 컨셉과 같은 합성·검사·저장 길). 상품은 다시 그려지지 않는다.

  - **보내는 것**: 컨셉의 장면 글(코드가 정한 영어 문장)뿐. 상품 사진·상품명·회사 정보는 보내지 않는다.
  - **키**: Windows 자격증명 저장소(`HOMEZ/IMAGE_AI/CLOUDFLARE`)에 계정 ID·API 토큰을 함께 둔다. 화면·기록·오류 문구에 토큰을 싣지 않는다(계정 ID는 끝 4자리만 보인다).
  - **관문**: 배경 한 장을 새로 그릴 때마다 승인 센터 허용(시스템 `CLOUDFLARE`, 목적 `AI_BACKGROUND`, 횟수형)을 확인한다. 허용이 없으면 부르지 않고 허용 요청이 남는다.
  - **다시 쓰기**: 한 번 그린 배경은 회사별로 저장해(`ai_backgrounds/{회사}/{컨셉}/v{n}.png`) 다른 상품·다음 실행에 그대로 쓴다 — 호출 0회. 새 배경을 원하면
    `new_background=True`로 다음 번호를 그린다(그때만 허용 1회).
  - **무료 한도**: Cloudflare 무료 플랜은 하루 10,000 뉴런을 넘으면 실패한다(요금 없음). 그 응답을 받으면 남은 컨셉은 부르지 않고 같은 이유로 알린다.
  - **표기·정책**: 대표(MAIN)에는 쓰지 않는다. "생성 배경" 표기는 이미지 밖(응답·작업 기록)에 둔다. 글자 섞임·쿠팡 정책 적합은 NOT_CHECKED로 알린다.
  - **기록**: 실제 호출마다 감사 기록 `AI_BACKGROUND_USAGE`(컨셉·번호·성공 여부, 토큰·이미지 없음).
=========================================================
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Any
from typing import Callable

from PIL import Image
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset import cloudflare_image_client as cfc
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import CANVAS_SIZE
from app.domains.media_asset.concept_run_service import ConceptRunOutcome
from app.domains.media_asset.concept_run_service import ConceptRunService
from app.domains.media_asset.concept_run_service import FAILED
from app.domains.media_asset.concept_run_service import SKIPPED
from app.domains.media_asset.image_validation import validate_image_bytes
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.storage import media_file_exists
from app.domains.media_asset.storage import read_media_file
from app.domains.media_asset.storage import write_media_file

logger = logging.getLogger("homez")

CREDENTIAL_TARGET = "HOMEZ/IMAGE_AI/CLOUDFLARE"
PROVIDER_CODE = "CLOUDFLARE"
OPERATION = "AI_BACKGROUND_RUN"
USAGE_ACTION = "AI_BACKGROUND_USAGE"
SETTINGS_ACTION = "AI_BACKGROUND_SETTINGS_SAVED"
GENERATED_LABEL_KO = "생성 배경(AI가 그린 배경 위에 실제 상품 사진을 붙임)"
MAX_VARIATIONS = 20
MIN_BACKGROUND_SIDE = 512
# 1024×1024 한 장 = 512 조각 4개 × 4.80 + 4단계 × 9.60 = 57.6 뉴런(공식 단가로 계산한 값 — 실제 크기는 문서에 없어 추정).
ESTIMATED_NEURONS_PER_IMAGE = 57.6

# 결과 이유 코드(화면이 사람 말로 바꾼다)
ALLOWANCE_REQUIRED, ESTOP_ACTIVE, AI_CALL_FAILED, DAILY_FREE_LIMIT, BAD_AI_IMAGE = (
    "ALLOWANCE_REQUIRED", "ESTOP_ACTIVE", "AI_CALL_FAILED", "DAILY_FREE_LIMIT", "BAD_AI_IMAGE")


@dataclass(frozen=True)
class AiBackgroundOutcome:
    concept_code: str
    status: str
    outcome: ConceptRunOutcome | None
    background_version: int | None = None
    background_reused: bool = False
    reason: str | None = None
    detail: str = ""


def _background_path(company_id: int, code: str, version: int) -> str:
    return f"ai_backgrounds/{int(company_id)}/{code}/v{int(version)}.png"


def _normalize_background(data: bytes) -> bytes:
    """AI가 준 이미지 → 검사 → 1200×1200 PNG(정보 꼬리표 없음). 이상하면 BadRequestException."""

    validate_image_bytes(data)      # 형식·크기 제한(풀기 전에 머리글로 확인)
    image = Image.open(io.BytesIO(data))
    if min(image.size) < MIN_BACKGROUND_SIDE:
        raise BadRequestException(f"배경 이미지가 너무 작습니다(최소 {MIN_BACKGROUND_SIDE}픽셀).")
    image = image.convert("RGB")
    width, height = image.size
    side = min(width, height)
    left, top = (width - side) // 2, (height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize(CANVAS_SIZE, Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class AiBackgroundService:

    def __init__(self, db: Session, credential_store: CredentialStore, *, queue: ImageGenerationJobQueueService | None = None,
                 client_factory: Callable[[str, str], Any] | None = None):
        self.db = db
        self.store = credential_store
        self.queue = queue or ImageGenerationJobQueueService(db)
        self._client_factory = client_factory or (lambda account_id, token: cfc.CloudflareImageClient(account_id, token))

    # ---- 설정 ----

    def _credentials(self) -> dict:
        try:
            payload = self.store.read(CREDENTIAL_TARGET)
        except (CredentialNotFoundError, CredentialStoreError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def status(self) -> dict:
        cred = self._credentials()
        account_id = cred.get("account_id") if cfc.valid_account_id(cred.get("account_id")) else ""
        token_saved = cfc.valid_token(cred.get("api_token"))
        return {
            "configured": bool(account_id and token_saved), "token_saved": token_saved, "account_id_saved": bool(account_id),
            "account_id_tail": account_id[-4:] if account_id else "", "provider": PROVIDER_CODE, "model": cfc.MODEL,
            "daily_free_neurons": cfc.DAILY_FREE_NEURONS, "estimated_neurons_per_image": ESTIMATED_NEURONS_PER_IMAGE,
            "estimated_free_images_per_day": int(cfc.DAILY_FREE_NEURONS // ESTIMATED_NEURONS_PER_IMAGE),
            "sends_product_photo": False, "generated_label": GENERATED_LABEL_KO,
        }

    def save_settings(self, *, account_id: str | None, api_token: str | None) -> dict:
        """계정 ID·토큰 저장. 빈 값이면 저장된 값을 그대로 쓴다. 처음 저장할 때는 둘 다 있어야 한다."""

        cred = self._credentials()
        account_id = (account_id or "").strip().lower()
        api_token = (api_token or "").strip()
        new_account = account_id or cred.get("account_id") or ""
        new_token = api_token or cred.get("api_token") or ""
        if account_id and not cfc.valid_account_id(account_id):
            raise BadRequestException("계정 ID는 영문 소문자·숫자 32자입니다(Cloudflare 대시보드 → Workers AI → REST API 사용).")
        if api_token and not cfc.valid_token(api_token):
            raise BadRequestException("API 토큰 형식이 올바르지 않습니다.")
        if not (cfc.valid_account_id(new_account) and cfc.valid_token(new_token)):
            raise BadRequestException("처음 저장할 때는 계정 ID와 API 토큰이 둘 다 필요합니다.")
        try:
            self.store.save(CREDENTIAL_TARGET, {"account_id": new_account, "api_token": new_token})
        except CredentialStoreError:
            raise ServiceUnavailableException("토큰을 안전 저장소에 저장하지 못했습니다.") from None
        return self.status()

    # ---- 배경 보관 ----

    def _latest_version(self, company_id: int, code: str) -> int:
        latest = 0
        for version in range(1, MAX_VARIATIONS + 1):
            if not media_file_exists(self.queue.media_root, _background_path(company_id, code, version)):
                break
            latest = version
        return latest

    # ---- 실행 ----

    def run(self, company_id: int, requested_by: int, cutout_asset_id: int, concept_codes: list[str], purpose: str, *, new_background: bool = False) -> list[AiBackgroundOutcome]:
        if purpose not in abc.PURPOSES:
            raise BadRequestException("AI 배경은 추가 이미지·상세 이미지에만 씁니다(대표 이미지는 흰 배경만).")
        codes: list[str] = []
        for code in concept_codes or []:
            if not isinstance(code, str) or not code.strip():
                raise BadRequestException("컨셉 코드가 올바르지 않습니다.")
            if code not in codes:
                codes.append(code)
        if not codes:
            raise BadRequestException("만들 AI 배경 컨셉을 하나 이상 고르세요.")
        if len(codes) > abc.MAX_PICK:
            raise BadRequestException(f"AI 배경은 한 번에 최대 {abc.MAX_PICK}개까지 만들 수 있습니다.")
        for code in codes:
            abc.get_ai_concept(code)          # 모르는 코드는 시작 전에 거절(바깥 호출 0)

        runner = ConceptRunService(self.db, queue=self.queue)
        cutout = self.queue.repository.get_media_asset_for_company(cutout_asset_id, company_id)
        if cutout is None:
            raise NotFoundException("누끼 이미지를 찾을 수 없습니다.")
        if cutout.rights_status == "RIGHTS_DENIED":
            raise BadRequestException("사용이 금지된 사진은 AI 배경으로 만들 수 없습니다.")
        try:
            cutout_bytes = read_media_file(self.queue.media_root, cutout.storage_path)
            validate_image_bytes(cutout_bytes)
        except Exception as exc:  # noqa: BLE001
            raise BadRequestException("누끼 이미지 파일을 읽을 수 없습니다.") from exc

        # 새로 그릴 일이 있을 때만 설정·허용 표를 요구한다(저장된 배경만 쓰면 바깥 호출이 없다).
        plan = []
        for code in codes:
            latest = self._latest_version(company_id, code)
            if new_background and latest >= MAX_VARIATIONS:
                raise BadRequestException(f"한 컨셉의 AI 배경은 {MAX_VARIATIONS}장까지 보관합니다.")
            plan.append((code, latest + 1 if (new_background or latest == 0) else latest, new_background or latest == 0))
        client = policy = None
        if any(needs for _, _, needs in plan):
            cred = self._credentials()
            if not (cfc.valid_account_id(cred.get("account_id")) and cfc.valid_token(cred.get("api_token"))):
                raise BadRequestException("AI 배경을 쓰려면 Cloudflare 계정 ID와 API 토큰을 먼저 저장해야 합니다(더보기 · 설정 → AI 배경 설정).")
            if not ExternalCallPolicyService.installed(self.db):
                raise ServiceUnavailableException("승인 센터(바깥 호출 허용) 표가 없어 AI 배경을 부르지 않았습니다.")
            policy = ExternalCallPolicyService(self.db)
            client = self._client_factory(cred["account_id"], cred["api_token"])

        outcomes: list[AiBackgroundOutcome] = []
        stop_reason: str | None = None
        for code, version, needs_call in plan:
            concept = abc.get_ai_concept(code)
            path = _background_path(company_id, code, version)
            if needs_call:
                if stop_reason:
                    outcomes.append(AiBackgroundOutcome(code, SKIPPED, None, version, False, stop_reason))
                    continue
                drawn = self._draw(policy, client, company_id, requested_by, concept, version)
                if isinstance(drawn, AiBackgroundOutcome):
                    if drawn.reason in (DAILY_FREE_LIMIT, ALLOWANCE_REQUIRED, ESTOP_ACTIVE):
                        stop_reason = drawn.reason       # 남은 컨셉도 같은 이유로 실패할 것이라 부르지 않는다
                    outcomes.append(drawn)
                    continue
                write_media_file(self.queue.media_root, path, drawn)
                background, reused = drawn, False
            else:
                background, reused = read_media_file(self.queue.media_root, path), True
            spec = BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=background)
            result = runner.compose_and_save(
                company_id, requested_by, cutout, cutout_bytes, code, purpose, spec, concept.checks, operation=OPERATION, provider_code=PROVIDER_CODE,
                model_name=f"{code}-v{version}", label=f"ai-bg-{code}-v{version}-{purpose}.png", generated_background=True,
            )
            outcomes.append(AiBackgroundOutcome(code, result.status, result, version, reused, result.reason))
        return outcomes

    def _draw(self, policy: ExternalCallPolicyService, client, company_id: int, user_id: int, concept, version: int):
        """배경 한 장을 그린다. 성공이면 정리한 PNG 바이트, 아니면 실패 결과(AiBackgroundOutcome)."""

        try:
            ticket = policy.authorize(company_id, ExternalSystem.CLOUDFLARE, CallKind.WRITE, CallPurpose.AI_BACKGROUND,
                                      actor_user_id=user_id, target=f"{concept.code}#v{version}"[:120], method="POST")
        except ExternalCallNotAllowed as exc:
            reason = ESTOP_ACTIVE if exc.reason == "ESTOP_ACTIVE" else ALLOWANCE_REQUIRED
            return AiBackgroundOutcome(concept.code, FAILED, None, version, False, reason, str(exc.detail)[:300])
        try:
            result = client.generate(concept.generation_prompt)
        except cfc.CloudflareImageError as exc:
            result = cfc.CloudflareImageResult(False, detail=str(exc)[:300])
        image = None
        reason = None
        if result.ok:
            try:
                image = _normalize_background(result.image_bytes or b"")
            except Exception:  # noqa: BLE001 — 받은 것이 그림이 아니면 실패로 다룬다
                reason = BAD_AI_IMAGE
        else:
            reason = DAILY_FREE_LIMIT if result.error_kind == cfc.FREE_LIMIT else AI_CALL_FAILED
        try:
            policy.finish(ticket, succeeded=result.ok, http_status=result.http_status)
        except Exception:  # noqa: BLE001 — 기록 실패가 결과를 바꾸지 않는다
            logger.warning("AI 배경 호출 기록 갱신 실패")
        self._record_usage(company_id, user_id, concept.code, version, image is not None, result.http_status)
        if image is None:
            detail = "받은 이미지를 배경으로 쓸 수 없습니다" if reason == BAD_AI_IMAGE else result.detail
            return AiBackgroundOutcome(concept.code, FAILED, None, version, False, reason, (detail or "")[:300])
        return image

    def _record_usage(self, company_id: int, user_id: int, code: str, version: int, ok: bool, http_status: int | None) -> None:
        try:
            write_audit_log(
                self.db, user_id=user_id, action=USAGE_ACTION, entity="ai_background", entity_id=f"{code}#v{version}"[:100],
                description=json.dumps({"provider": PROVIDER_CODE, "model": cfc.MODEL, "concept": code, "version": version, "ok": ok, "http_status": http_status},
                                       ensure_ascii=False)[:500],
                company_id=company_id,
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.warning("AI 배경 사용 기록 실패")


__all__ = ["AiBackgroundService", "AiBackgroundOutcome", "CREDENTIAL_TARGET", "PROVIDER_CODE", "OPERATION", "USAGE_ACTION", "SETTINGS_ACTION",
           "GENERATED_LABEL_KO", "MAX_VARIATIONS", "ALLOWANCE_REQUIRED", "ESTOP_ACTIVE", "AI_CALL_FAILED", "DAILY_FREE_LIMIT", "BAD_AI_IMAGE"]
