"""
=========================================================
Homez OS

File : app/domains/media_asset/image_ai_service.py

2026-10-25 — 실제 AI 이미지 생성(Gemini) 설정·관문·기록. 글쓰기 AI(`marketplace_listing/ai_draft_service.py`)와 같은 틀을 따른다.

  - **키**: Windows 자격증명 저장소(`HOMEZ/IMAGE_AI/GEMINI`)에만 둔다. 화면·기록·오류 문구에 싣지 않는다.
  - **설정**(사용자 설정 `listing_defaults.image_ai`): 모델(기본 `gemini-nano-banana-2.1`), 이미지 크기(1K/2K/4K, 기본 2K),
    월 크레딧(달러), 장당 가격(달러, 비우면 공식 가격표 값).
  - **관문**: 한 장 요청마다 승인 센터 허용(시스템 `GEMINI`, 목적 `IMAGE_GENERATION`, 횟수형)을 확인한다. 허용이 없으면 호출하지 않고
    허용 요청이 자동으로 남는다(사용자가 승인 센터에서 승인).
  - **기록**: 호출마다 감사 기록 `IMAGE_AI_USAGE`(모델·크기·성공 여부·추정 비용·시각, 이미지·키 없음). 생성이 끝나면 앱 알림을 남긴다
    (사용자 결정: 대표 사진을 임의로 정하지 않고 만든 뒤 알린다).

2026-11-22 사용자 결정 — 보류 해제("가 나 다 진행해라"), 그리고 **"매월 제공하는 크레딧 100%까지만 사용"**(Google AI Pro, 월 $10 크레딧):
  - 크레딧은 받은 날의 매월 같은 날에 들어오고 12개월 쌓인다(Google 안내). 받은 날을 몰라도 **어느 31일 동안 쓴 추정액이 월 크레딧을 넘지 않으면**
    쓴 총액이 받은 크레딧 총액을 넘을 수 없다(달은 길어야 31일이라, 첫 크레딧을 받은 뒤 t일까지 받은 크레딧은 적어도 ceil(t/31)번이다).
    그래서 달력 달이 아니라 **최근 31일** 합계로 막는다. 남은 크레딧이 쌓여도 더 쓰지 않는다(추가 결제·선결제 잔액 사용 없음).
  - 비용은 **추정**이다(실제 청구액은 Google 결제 화면에서만 확인된다). 한 장 = 장당 가격 + 입력 여유분(`INPUT_ALLOWANCE_USD`) — 넉넉히 잡는다.
    Google까지 간 호출은 실패해도 비용으로 센다. 보내기 전에 거절된 응답(400·401·403·404·429)만 0으로 센다.
  - 한 번에 여러 장을 만들면 시작 전에 전체 장수로 한 번, 장마다 한 번 더 확인한다(장 사이에 다른 요청이 쓰더라도 넘지 않게, 호출은 한 번에 하나씩).
  - 80%·100%를 넘는 순간 앱 알림을 한 번 남긴다.

2026-11-23 — 첫 실제 호출이 402(결제 필요 — 선결제 잔액 0)로 거절됐다. 일을 하지 않고 거절한 것이라 0으로 센다.
클로드 연결 장치(`tools/claude_gemini_link`)가 같은 크레딧을 쓰므로: 제미나이 글 질문 기록(`GEMINI_TEXT_USAGE`)도 같은 31일 합계에 들어가고,
'확인 → 호출 → 기록'은 앱과 연결 장치 사이에서도 한 번에 하나씩 한다(같은 PC의 잠금 파일).
=========================================================
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Callable
from pathlib import Path

from sqlalchemy import text
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
TEXT_USAGE_ACTION = "GEMINI_TEXT_USAGE"           # 2026-11-23 제미나이 글 질문(같은 월 크레딧을 쓴다)
USAGE_ACTIONS = (USAGE_ACTION, TEXT_USAGE_ACTION)
PROVIDER_CODE = "GEMINI"
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{20,300}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")

# 2026-11-22 월 크레딧 한도
CREDIT_WINDOW_DAYS = 31                 # 달은 길어야 31일 — 위 설명
DEFAULT_MONTHLY_CREDIT_USD = 10.0       # Google AI Pro 월 크레딧(사용자 결제, 2026-11-22)
MAX_MONTHLY_CREDIT_USD = 100.0          # Ultra 20x의 월 크레딧
MAX_PRICE_PER_IMAGE_USD = 1.0
INPUT_ALLOWANCE_USD = 0.005             # 입력(규칙 글 + 참고 사진 1장) 여유분: $1.50/100만 토큰 × 약 3,300토큰
# 공식 가격표(ai.google.dev, 2026-10-25 확인) 장당 출력 가격. 여기 없는 모델은 장당 가격을 직접 적어야 쓸 수 있다.
OFFICIAL_PRICE_PER_IMAGE_USD = {"gemini-nano-banana-2.1": {"1K": 0.0336, "2K": 0.0504, "4K": 0.113}}
NOT_BILLED_HTTP = (400, 401, 402, 403, 404, 429)   # 402: 결제 필요(2026-11-23 실제 응답) — 일을 하지 않고 거절
CREDIT_ALERT_STAGES = (100, 80)
CREDIT_NOTICE_CATEGORY = "IMAGE_AI"

# 장마다 '확인 → 호출 → 기록'을 한 번에 하나씩 한다. 두 요청이 같은 남은 크레딧을 동시에 보고 함께 넘기지 않게 한다.
# 프로세스 안에서는 _CALL_LOCK, 앱과 클로드 연결 장치 사이에서는 잠금 파일(같은 PC)로 막는다.
_CALL_LOCK = threading.Lock()
LOCK_FILE = Path(__file__).resolve().parents[3] / "storage" / "locks" / "gemini_calls.lock"
LOCK_WAIT_SECONDS = 600


@contextmanager
def _file_lock(path: Path = None, wait_seconds: float = None):
    """다른 프로세스와 함께 쓰는 잠금. 윈도우가 아니면(시험 환경 등) 프로세스 안 잠금만 쓴다. 기다려도 못 잡으면 호출하지 않는다."""

    path = path or LOCK_FILE
    try:
        import msvcrt
    except ImportError:  # pragma: no cover — 윈도우 전용
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    deadline = time.monotonic() + (LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    try:
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CreditLimitReached("다른 제미나이 호출이 끝나지 않아 이번 호출은 하지 않았습니다. 잠시 뒤 다시 해 주세요.") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


@contextmanager
def call_slot():
    """'확인 → 호출 → 기록' 한 번. 같은 프로세스·다른 프로세스(앱·클로드 연결 장치) 모두 한 번에 하나."""

    with _CALL_LOCK:
        with _file_lock():
            yield


class CreditLimitReached(Exception):
    """이번 장을 만들면 최근 31일 추정 사용액이 월 크레딧을 넘는다."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def credit_stage(used_usd: float, credit_usd: float) -> int | None:
    """최근 31일 사용액이 월 크레딧의 몇 % 단계를 넘었는지(100·80). 크레딧이 없거나 80% 미만이면 None."""

    if not credit_usd or credit_usd <= 0:
        return None
    return next((stage for stage in CREDIT_ALERT_STAGES if float(used_usd or 0) * 100 >= stage * credit_usd), None)


def per_image_usd(model: str | None, image_size: str | None, override: Any = None) -> float | None:
    """한 장의 추정 비용(장당 가격 + 입력 여유분). 직접 적은 장당 가격이 있으면 그것, 없으면 공식 가격표. 알 수 없으면 None."""

    if isinstance(override, (int, float)) and not isinstance(override, bool) and override > 0:
        unit = float(override)
    else:
        unit = (OFFICIAL_PRICE_PER_IMAGE_USD.get(model or "") or {}).get(image_size or "")
        if unit is None:
            return None
    return round(unit + INPUT_ALLOWANCE_USD, 6)


def billed_usd(result: gic.GeminiImageResult, estimate: float) -> float:
    """Google까지 간 호출은 실패해도 추정 비용으로 센다. 보내기 전 거절(400·401·403·404·429)만 0."""

    return 0.0 if result.http_status in NOT_BILLED_HTTP else estimate


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
            with call_slot():
                try:
                    ticket = self._before(item.sequence_index)
                except ExternalCallNotAllowed as exc:
                    items.append(ImageGenerationResultItem(item.sequence_index, item.purpose, "FAILED", None, None, "UNKNOWN", str(exc.detail)[:300]))
                    continue
                except CreditLimitReached as exc:
                    items.append(ImageGenerationResultItem(item.sequence_index, item.purpose, "FAILED", None, None, "UNKNOWN", exc.detail[:300]))
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

    @staticmethod
    def _credit(s: dict) -> float:
        value = s.get("monthly_credit_usd")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0

    def status(self, user_id: int, company_id: int) -> dict:
        s = self._settings(user_id, company_id)
        key_saved = self._key_saved()
        model = s.get("model") or ""
        size = s.get("image_size") or ""
        credit = self._credit(s)
        estimate = per_image_usd(model, size, s.get("price_per_image_usd"))
        used, calls = self.window_usage()
        remaining = max(credit - used, 0.0)
        return {
            "configured": bool(key_saved and model and size in gic.IMAGE_SIZES and credit > 0 and estimate is not None), "key_saved": key_saved,
            "model": model, "default_model": gic.DEFAULT_MODEL, "image_size": size, "image_sizes": list(gic.IMAGE_SIZES),
            "provider": PROVIDER_CODE, "store_on_google": False,
            "monthly_credit_usd": credit, "default_monthly_credit_usd": DEFAULT_MONTHLY_CREDIT_USD,
            "price_per_image_usd": s.get("price_per_image_usd"), "official_price_per_image_usd": (OFFICIAL_PRICE_PER_IMAGE_USD.get(model) or {}).get(size),
            "estimated_cost_per_image_usd": estimate, "input_allowance_usd": INPUT_ALLOWANCE_USD,
            "window_days": CREDIT_WINDOW_DAYS, "window_used_usd": round(used, 4), "window_calls": calls,
            "window_remaining_usd": round(remaining, 4),
            "images_left": int(math.floor(remaining / estimate + 1e-9)) if estimate and credit > 0 else 0,
            "credit_alert_pct": credit_stage(used, credit),
            "watermark_note": "모든 결과 이미지에는 Google의 눈에 보이지 않는 SynthID 표식이 들어갑니다(보이는 워터마크·글자는 넣지 않습니다).",
        }

    def save_settings(self, user_id: int, company_id: int, *, model: str, image_size: str, monthly_credit_usd: Any = DEFAULT_MONTHLY_CREDIT_USD,
                      price_per_image_usd: Any = None, api_key: str | None = None) -> dict:
        model = (model or "").strip()
        if not _MODEL_PATTERN.match(model):
            raise BadRequestException("모델 이름 형식이 올바르지 않습니다(영문·숫자·.-_ 3~80자).")
        if image_size not in gic.IMAGE_SIZES:
            raise BadRequestException("이미지 크기는 1K·2K·4K 중 하나여야 합니다.")
        if isinstance(monthly_credit_usd, bool) or not isinstance(monthly_credit_usd, (int, float)) or not 0 < float(monthly_credit_usd) <= MAX_MONTHLY_CREDIT_USD:
            raise BadRequestException(f"월 크레딧은 0보다 크고 {MAX_MONTHLY_CREDIT_USD:g}달러 이하여야 합니다.")
        if price_per_image_usd is not None and (
            isinstance(price_per_image_usd, bool) or not isinstance(price_per_image_usd, (int, float)) or not 0 < float(price_per_image_usd) <= MAX_PRICE_PER_IMAGE_USD
        ):
            raise BadRequestException(f"장당 가격은 0보다 크고 {MAX_PRICE_PER_IMAGE_USD:g}달러 이하여야 합니다(공식 가격을 쓰려면 비워 두세요).")
        if per_image_usd(model, image_size, price_per_image_usd) is None:
            raise BadRequestException("이 모델은 HOMEZ 가격표에 없습니다 — 공식 가격표의 장당 가격(달러)을 적어 주세요.")
        if api_key is not None and api_key != "":
            if not _KEY_PATTERN.match(api_key):
                raise BadRequestException("API 키 형식이 올바르지 않습니다.")
            try:
                self.store.save(CREDENTIAL_TARGET, {"api_key": api_key})
            except CredentialStoreError:
                raise ServiceUnavailableException("API 키를 안전 저장소에 저장하지 못했습니다.") from None
        blob, version = self._stored_blob(user_id, company_id)
        blob[SETTINGS_SUBKEY] = {
            "model": model, "image_size": image_size, "monthly_credit_usd": float(monthly_credit_usd),
            "price_per_image_usd": float(price_per_image_usd) if price_per_image_usd is not None else None,
        }
        try:
            UserSettingService(UserSettingRepository(self.db)).put(user_id, company_id, SETTINGS_KEY, blob, version, 1)
        except ConflictException:
            self.db.rollback()
            raise
        return self.status(user_id, company_id)

    # ---- 사용액(최근 31일) ----

    def _window_rows(self) -> list[tuple[int, str, str]]:
        now = self._now()
        start = now - timedelta(days=CREDIT_WINDOW_DAYS)
        months = sorted({f"{d.year:04d}{d.month:02d}" for d in (start, now)})
        rows: list = []
        for action in USAGE_ACTIONS:
            for month in months:
                rows += self.db.execute(
                    text("SELECT id, entity_id, description FROM audit_logs WHERE action = :a AND entity_id LIKE :p"),
                    {"a": action, "p": f"{month}:%"},
                ).fetchall()
        return [tuple(r) for r in rows]

    def _row_in_window(self, payload: Any) -> tuple[bool, float]:
        """(창 안인가, 셀 금액). 읽을 수 없거나 시각·금액이 없는 기록은 넉넉히 창 안·장당 최대값으로 센다(덜 세지 않는다)."""

        start = self._now() - timedelta(days=CREDIT_WINDOW_DAYS)
        if not isinstance(payload, dict):
            return True, MAX_PRICE_PER_IMAGE_USD
        try:
            at = datetime.fromisoformat(str(payload.get("at")))
        except (TypeError, ValueError):
            at = None
        if at is not None and at.tzinfo is not None:
            at = at.astimezone(timezone.utc).replace(tzinfo=None)
        cost = payload.get("cost_usd")
        cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else MAX_PRICE_PER_IMAGE_USD
        return (at is None or at >= start), cost

    def window_usage(self) -> tuple[float, int]:
        total, calls = 0.0, 0
        for _row_id, _entity_id, description in self._window_rows():
            try:
                payload = json.loads(description)
            except (TypeError, ValueError):
                payload = None
            inside, cost = self._row_in_window(payload)
            if inside:
                total += cost
                calls += 1
        return total, calls

    def window_records(self, limit: int = 30) -> list[dict]:
        """최근 31일 사용 기록(최근 것부터). 키·이미지는 원래 기록에 없다."""

        records = []
        for row_id, entity_id, description in sorted(self._window_rows(), key=lambda r: r[0], reverse=True):
            try:
                payload = json.loads(description)
            except (TypeError, ValueError):
                payload = None
            inside, cost = self._row_in_window(payload)
            if not inside:
                continue
            p = payload if isinstance(payload, dict) else {}
            records.append({"id": row_id, "ref": str(entity_id).split(":", 1)[-1], "model": p.get("model"), "image_size": p.get("image_size"),
                            "ok": p.get("ok") if isinstance(p.get("ok"), bool) else None, "cost_usd": cost, "at": p.get("at")})
            if len(records) >= max(1, min(int(limit), 200)):
                break
        return records

    def credit_usd(self, user_id: int, company_id: int) -> float:
        """저장된 월 크레딧(달러). 없으면 0 — 0이면 아무 제미나이 호출도 하지 않는다."""

        return self._credit(self._settings(user_id, company_id))

    def read_key(self) -> str | None:
        """제미나이 키(같은 키를 글 질문에도 쓴다). 값은 화면·기록에 싣지 않는다."""

        return self._read_key()

    def ensure_room(self, credit: float, need_usd: float) -> None:
        """최근 31일 사용액 + 이번 최대 비용이 월 크레딧을 넘으면 CreditLimitReached. 반드시 call_slot() 안에서 부른다."""

        used, _calls = self.window_usage()
        if credit <= 0 or used + need_usd > credit + 1e-9:
            raise CreditLimitReached(
                f"월 크레딧 한도 때문에 부르지 않았습니다(최근 {CREDIT_WINDOW_DAYS}일 사용 약 ${used:.2f} + 이번 최대 약 ${need_usd:.4f} > 월 크레딧 ${credit:.2f})."
            )

    def record_text_usage(self, company_id: int, user_id: int, ref: str, model: str, ok: bool, cost_usd: float, input_tokens: Any, output_tokens: Any) -> None:
        now = self._now()
        try:
            write_audit_log(
                self.db, user_id=user_id, action=TEXT_USAGE_ACTION, entity="gemini_text", entity_id=f"{now.year:04d}{now.month:02d}:{ref}"[:100],
                description=json.dumps({"model": model, "kind": "TEXT", "ok": ok, "cost_usd": round(float(cost_usd), 6), "i": input_tokens, "o": output_tokens,
                                        "at": now.replace(microsecond=0).isoformat()}, ensure_ascii=False),
                company_id=company_id,
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.warning("제미나이 글 질문 사용 기록 실패")

    def _check_credit(self, credit: float, estimate: float, images: int) -> None:
        used, _calls = self.window_usage()
        need = estimate * images
        if used + need > credit + 1e-9:
            left = int(math.floor(max(credit - used, 0.0) / estimate + 1e-9)) if estimate else 0
            raise CreditLimitReached(
                f"월 크레딧 한도 때문에 만들지 않았습니다(최근 {CREDIT_WINDOW_DAYS}일 사용 약 ${used:.2f} + 이번 {images}장 약 ${need:.2f} > 월 크레딧 ${credit:.2f}). "
                f"지금 만들 수 있는 장수는 {left}장입니다."
            )

    # ---- 호출 ----

    def provider(self, company_id: int, user_id: int, *, target: str, planned_images: int = 1) -> GeminiImageProvider:
        """설정·키·월 크레딧을 확인하고, 한 장마다 크레딧·승인 센터 허용을 다시 확인하는 제공자를 돌려준다. 설정이 없거나 크레딧이 모자라면 호출 전에 막는다."""

        s = self._settings(user_id, company_id)
        model, size = s.get("model"), s.get("image_size")
        credit = self._credit(s)
        estimate = per_image_usd(model, size, s.get("price_per_image_usd"))
        api_key = self._read_key()
        if not (model and size in gic.IMAGE_SIZES and api_key and credit > 0 and estimate is not None):
            raise BadRequestException("AI 이미지 생성을 쓰려면 Gemini API 키·모델·이미지 크기·월 크레딧을 먼저 저장해야 합니다.")
        policy = ExternalCallPolicyService(self.db) if ExternalCallPolicyService.installed(self.db) else None
        if policy is None:
            raise ServiceUnavailableException("승인 센터(바깥 호출 허용) 표가 없어 AI 이미지를 부르지 않았습니다.")
        try:
            self._check_credit(credit, estimate, max(1, int(planned_images)))
        except CreditLimitReached as exc:
            raise BadRequestException(exc.detail) from None

        def before(index: int):
            self._check_credit(credit, estimate, 1)
            return policy.authorize(company_id, ExternalSystem.GEMINI, CallKind.WRITE, CallPurpose.IMAGE_GENERATION,
                                    actor_user_id=user_id, target=f"{target}#{index}"[:120], method="POST")

        def after(ticket, result: gic.GeminiImageResult):
            try:
                policy.finish(ticket, succeeded=result.ok, http_status=result.http_status)
            except Exception:  # noqa: BLE001 — 기록 실패가 결과를 바꾸지 않는다
                logger.warning("이미지 AI 호출 기록 갱신 실패")
            used_before, _ = self.window_usage()
            self._record_usage(company_id, user_id, target, model, size, result.ok, billed_usd(result, estimate))
            used_after, _ = self.window_usage()
            self._notify_credit_stage(company_id, user_id, used_before, used_after, credit)

        return GeminiImageProvider(self._client_factory(api_key, model), image_size=size, before_call=before, after_call=after)

    def _record_usage(self, company_id: int, user_id: int, target: str, model: str, size: str, ok: bool, cost_usd: float) -> None:
        now = self._now()
        try:
            write_audit_log(
                self.db, user_id=user_id, action=USAGE_ACTION, entity="image_ai", entity_id=f"{now.year:04d}{now.month:02d}:{target}"[:100],
                description=json.dumps({"model": model, "image_size": size, "ok": ok, "cost_usd": round(float(cost_usd), 6),
                                        "at": now.replace(microsecond=0).isoformat()}, ensure_ascii=False),
                company_id=company_id,
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.warning("이미지 AI 사용 기록 실패")

    def _notify_credit_stage(self, company_id: int, user_id: int, used_before: float, used_after: float, credit: float) -> None:
        """80%·100%를 이번 장에서 처음 넘었으면 앱 알림을 한 번 남긴다. 알림 실패가 생성 결과를 바꾸지 않는다."""

        stage = credit_stage(used_after, credit)
        if stage is None or stage == credit_stage(used_before, credit):
            return
        try:
            from app.domains.notification_center.service import NotificationService

            NotificationService(self.db).notify_user(
                company_id=company_id, user_id=user_id, category=CREDIT_NOTICE_CATEGORY, level="warning",
                title=f"AI 이미지 월 크레딧의 {stage}%를 썼습니다",
                message=(f"최근 {CREDIT_WINDOW_DAYS}일 추정 사용액 약 ${used_after:.2f} / 월 크레딧 ${credit:.2f}. "
                         + ("크레딧을 넘지 않도록 더 만들지 않습니다. 사용액이 31일 창에서 빠지면 다시 만들 수 있습니다." if stage >= 100
                            else "100%가 되면 더 만들지 않습니다.")),
                link_path=None,
            )
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.warning("이미지 AI 크레딧 알림 실패")

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


__all__ = [
    "ImageAiService", "GeminiImageProvider", "CreditLimitReached", "credit_stage", "per_image_usd", "billed_usd", "call_slot", "TEXT_USAGE_ACTION", "USAGE_ACTIONS",
    "CREDENTIAL_TARGET", "USAGE_ACTION", "PROVIDER_CODE", "CREDIT_WINDOW_DAYS", "DEFAULT_MONTHLY_CREDIT_USD", "MAX_MONTHLY_CREDIT_USD",
    "MAX_PRICE_PER_IMAGE_USD", "INPUT_ALLOWANCE_USD", "OFFICIAL_PRICE_PER_IMAGE_USD", "NOT_BILLED_HTTP",
]
