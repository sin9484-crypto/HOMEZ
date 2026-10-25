"""
=========================================================
Homez OS

File : app/domains/media_asset/image_ai_service.py

2026-10-25 — 실제 AI 이미지 생성(Gemini) 설정·관문·기록. 글쓰기 AI(`marketplace_listing/ai_draft_service.py`)와 같은 틀을 따른다.

  - **키**: Windows 자격증명 저장소(`HOMEZ/IMAGE_AI/GEMINI`)에만 둔다. 화면·기록·오류 문구에 싣지 않는다.
  - **설정**(사용자 설정 `listing_defaults.image_ai`): 모델(기본 `gemini-nano-banana-2.1`), 이미지 크기(1K/2K/4K, 기본 2K),
    장당 예상 비용(원, 선택 — 0이면 '모름'으로 기록). 사용자 결정(10-24)으로 **장당·월 비용 한도는 두지 않는다**(기록만).
  - **관문**: 한 장 요청마다 승인 센터 허용(시스템 `GEMINI`, 목적 `IMAGE_GENERATION`, 횟수형)을 확인한다. 허용이 없으면 호출하지 않고
    허용 요청이 자동으로 남는다(사용자가 승인 센터에서 승인).
  - **기록**: 호출마다 감사 기록 `IMAGE_AI_USAGE`(모델·크기·성공 여부·예상 비용, 이미지·키 없음). 생성이 끝나면 앱 알림을 남긴다
    (사용자 결정: 대표 사진을 임의로 정하지 않고 만든 뒤 알린다).
=========================================================
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from typing import Callable

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.media_asset import gemini_image_client as gic
from app.domains.media_asset.providers import ImageGenerationProvider
from app.domains.media_asset.providers import ImageGenerationProviderResult
from app.domains.media_asset.providers import ImageGenerationRequest
from app.domains.media_asset.providers import ImageGenerationResultItem
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.service import UserSettingService

logger = logging.getLogger("homez")

CREDENTIAL_TARGET = "HOMEZ/IMAGE_AI/GEMINI"
SETTINGS_KEY = "listing_defaults"
SETTINGS_SUBKEY = "image_ai"
USAGE_ACTION = "IMAGE_AI_USAGE"
PROVIDER_CODE = "GEMINI"
MAX_PRICE_PER_IMAGE_KRW = 100_000
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{20,300}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")


class GeminiImageProvider(ImageGenerationProvider):
    """제공자 요청(ImageGenerationRequest) 한 장마다 관문(`before_call`)을 거쳐 Gemini를 부른다. 한 장의 실패가 나머지를 막지 않는다."""

    code = PROVIDER_CODE

    def __init__(self, client: gic.GeminiImageClient, *, image_size: str, before_call: Callable[[int], Any], after_call: Callable[[Any, gic.GeminiImageResult], None]):
        self._client = client
        self._size = image_size
        self._before = before_call
        self._after = after_call

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationProviderResult:
        items = []
        if not request.reference_image_bytes or not request.reference_mime_type:
            raise gic.GeminiImageError("참고 이미지가 없다")
        for item in request.items:
            try:
                ticket = self._before(item.sequence_index)
            except ExternalCallNotAllowed as exc:
                items.append(ImageGenerationResultItem(item.sequence_index, item.purpose, "FAILED", None, None, "UNKNOWN", str(exc.detail)[:300]))
                continue
            prompt = gic.build_prompt(request.style_params, item.sequence_index)
            try:
                result = self._client.generate(prompt, [(request.reference_image_bytes, request.reference_mime_type)], image_size=self._size)
            except gic.GeminiImageError as exc:
                result = gic.GeminiImageResult(False, detail=str(exc)[:300])
            self._after(ticket, result)
            if result.ok:
                # 안전 검사는 HOMEZ가 하지 않았다 — 통과(PASSED)로 쓰지 않는다.
                items.append(ImageGenerationResultItem(item.sequence_index, item.purpose, "SUCCEEDED", result.image_bytes, result.mime_type, "UNKNOWN"))
            else:
                items.append(ImageGenerationResultItem(item.sequence_index, item.purpose, "FAILED", None, None, "UNKNOWN", result.detail[:300] or "생성 실패"))
        return ImageGenerationProviderResult(items=tuple(items), provider_cost=None)


class ImageAiService:

    def __init__(self, db: Session, credential_store: CredentialStore, *, client_factory: Callable[[str, str], Any] | None = None,
                 now_factory: Callable[[], datetime] | None = None):
        self.db = db
        self.store = credential_store
        self._client_factory = client_factory or (lambda api_key, model: gic.GeminiImageClient(api_key, model))
        self._now = now_factory or datetime.utcnow

    # ---- 설정 ----

    def _stored_blob(self, user_id: int, company_id: int) -> tuple[dict, int]:
        row = UserSettingRepository(self.db).get_for_user(user_id, company_id, SETTINGS_KEY)
        if row is None:
            return {}, 0
        try:
            value = json.loads(row.setting_value)
        except ValueError:
            return {}, row.version
        return (value if isinstance(value, dict) else {}), row.version

    def _settings(self, user_id: int, company_id: int) -> dict:
        value = self._stored_blob(user_id, company_id)[0].get(SETTINGS_SUBKEY)
        return value if isinstance(value, dict) else {}

    def _key_saved(self) -> bool:
        try:
            return bool(self.store.exists(CREDENTIAL_TARGET))
        except CredentialStoreError:
            return False

    def _read_key(self) -> str | None:
        try:
            payload = self.store.read(CREDENTIAL_TARGET)
        except (CredentialNotFoundError, CredentialStoreError):
            return None
        key = payload.get("api_key") if isinstance(payload, dict) else None
        return key if isinstance(key, str) and key else None

    def status(self, user_id: int, company_id: int) -> dict:
        s = self._settings(user_id, company_id)
        key_saved = self._key_saved()
        model = s.get("model") or ""
        return {
            "configured": bool(key_saved and model and s.get("image_size") in gic.IMAGE_SIZES), "key_saved": key_saved,
            "model": model, "default_model": gic.DEFAULT_MODEL, "image_size": s.get("image_size") or "", "image_sizes": list(gic.IMAGE_SIZES),
            "price_per_image_krw": s.get("price_per_image_krw") or 0, "provider": PROVIDER_CODE, "store_on_google": False,
            "watermark_note": "모든 결과 이미지에는 Google의 눈에 보이지 않는 SynthID 표식이 들어갑니다(보이는 워터마크·글자는 넣지 않습니다).",
        }

    def save_settings(self, user_id: int, company_id: int, *, model: str, image_size: str, price_per_image_krw: Any = 0, api_key: str | None = None) -> dict:
        model = (model or "").strip()
        if not _MODEL_PATTERN.match(model):
            raise BadRequestException("모델 이름 형식이 올바르지 않습니다(영문·숫자·.-_ 3~80자).")
        if image_size not in gic.IMAGE_SIZES:
            raise BadRequestException("이미지 크기는 1K·2K·4K 중 하나여야 합니다.")
        if isinstance(price_per_image_krw, bool) or not isinstance(price_per_image_krw, (int, float)) or not 0 <= float(price_per_image_krw) <= MAX_PRICE_PER_IMAGE_KRW:
            raise BadRequestException(f"장당 예상 비용은 0~{MAX_PRICE_PER_IMAGE_KRW:,}원이어야 합니다(모르면 0).")
        if api_key is not None and api_key != "":
            if not _KEY_PATTERN.match(api_key):
                raise BadRequestException("API 키 형식이 올바르지 않습니다.")
            try:
                self.store.save(CREDENTIAL_TARGET, {"api_key": api_key})
            except CredentialStoreError:
                raise ServiceUnavailableException("API 키를 안전 저장소에 저장하지 못했습니다.") from None
        blob, version = self._stored_blob(user_id, company_id)
        blob[SETTINGS_SUBKEY] = {"model": model, "image_size": image_size, "price_per_image_krw": float(price_per_image_krw)}
        try:
            UserSettingService(UserSettingRepository(self.db)).put(user_id, company_id, SETTINGS_KEY, blob, version, 1)
        except ConflictException:
            self.db.rollback()
            raise
        return self.status(user_id, company_id)

    # ---- 호출 ----

    def provider(self, company_id: int, user_id: int, *, target: str) -> GeminiImageProvider:
        """설정·키를 확인하고, 한 장마다 승인 센터 허용을 확인하는 제공자를 돌려준다. 설정이 없으면 호출 전에 막는다."""

        s = self._settings(user_id, company_id)
        model, size = s.get("model"), s.get("image_size")
        api_key = self._read_key()
        if not (model and size in gic.IMAGE_SIZES and api_key):
            raise BadRequestException("AI 이미지 생성을 쓰려면 Gemini API 키·모델·이미지 크기를 먼저 저장해야 합니다.")
        price = float(s.get("price_per_image_krw") or 0)
        policy = ExternalCallPolicyService(self.db) if ExternalCallPolicyService.installed(self.db) else None
        if policy is None:
            raise ServiceUnavailableException("승인 센터(바깥 호출 허용) 표가 없어 AI 이미지를 부르지 않았습니다.")

        def before(index: int):
            return policy.authorize(company_id, ExternalSystem.GEMINI, CallKind.WRITE, CallPurpose.IMAGE_GENERATION,
                                    actor_user_id=user_id, target=f"{target}#{index}"[:120], method="POST")

        def after(ticket, result: gic.GeminiImageResult):
            try:
                policy.finish(ticket, succeeded=result.ok, http_status=result.http_status)
            except Exception:  # noqa: BLE001 — 기록 실패가 결과를 바꾸지 않는다
                logger.warning("이미지 AI 호출 기록 갱신 실패")
            self._record_usage(company_id, user_id, target, model, size, result.ok, price if result.ok and price > 0 else None)

        return GeminiImageProvider(self._client_factory(api_key, model), image_size=size, before_call=before, after_call=after)

    def _record_usage(self, company_id: int, user_id: int, target: str, model: str, size: str, ok: bool, cost_krw: float | None) -> None:
        now = self._now()
        try:
            write_audit_log(
                self.db, user_id=user_id, action=USAGE_ACTION, entity="image_ai", entity_id=f"{now.year:04d}{now.month:02d}:{target}"[:100],
                description=json.dumps({"model": model, "image_size": size, "ok": ok, "cost_krw": cost_krw}, ensure_ascii=False), company_id=company_id,
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.warning("이미지 AI 사용 기록 실패")

    def notify_done(self, company_id: int, user_id: int, *, created: int, failed: int, link_path: str | None = None) -> None:
        """생성이 끝났음을 앱 알림으로 남긴다. 대표 사진은 정하지 않았다고 함께 알린다. 알림 실패가 생성 결과를 바꾸지 않는다."""

        try:
            from app.domains.notification_center.service import NotificationService

            NotificationService(self.db).notify_user(
                company_id=company_id, user_id=user_id, category="IMAGE_AI", level="info" if not failed else "warning",
                title=f"AI 이미지 {created}장을 만들었습니다" + (f"(실패 {failed}장)" if failed else ""),
                message="대표 사진은 정하지 않았습니다. 이미지 단계에서 확인하고 직접 고르세요. 쿠팡 이미지 정책 적합 여부는 직접 확인해야 합니다.",
                link_path=link_path,
            )
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.warning("이미지 AI 완료 알림 실패")


__all__ = ["ImageAiService", "GeminiImageProvider", "CREDENTIAL_TARGET", "USAGE_ACTION", "PROVIDER_CODE"]
