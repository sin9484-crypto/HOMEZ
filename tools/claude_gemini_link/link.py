"""
=========================================================
Homez OS

File : tools/claude_gemini_link/link.py

2026-11-23 — 사용자 지시 "구글 AI 스튜디오 클로드에 플러그인해라"(사진 만들기·글 질문·사용량 확인 모두).
클로드가 대화 중에 제미나이를 쓰게 하는 연결 장치의 **일**(통신 규약은 `server.py`). 앱과 같은 코드·같은 DB·같은 규칙을 쓴다:

  - 키: Windows 자격증명 저장소의 제미나이 키(쉬운 보기에서 사용자가 저장한 것). 이 장치는 키를 받거나 출력하지 않는다.
  - 한도: 이미지·글 질문 모두 같은 월 크레딧(최근 31일 추정액 ≤ 월 크레딧), 앱과 한 번에 하나씩(`call_slot`).
  - 허락: 승인 센터 허용(사진은 `IMAGE_GENERATION` 장마다, 글은 `GEMINI_QUESTION` 질문마다). 없으면 부르지 않고 요청이 남는다.
  - 사진은 앱과 똑같이 파생 사진으로 저장된다(원본은 그대로, 대표 사진은 정하지 않음, 완료 알림).
  - 사용량: 홈즈가 센 추정액만 알려 준다. 구글 실제 잔액·청구액은 키로 읽을 수 없다(사람이 구글 AI 스튜디오 결제 화면에서 본다).
=========================================================
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Callable

from sqlalchemy import text

from app.core.exceptions import AppException
from app.domains.external_call.service import ExternalCallNotAllowed

ROOT = Path(__file__).resolve().parents[2]
PHOTO_LIST_LIMIT = 50


class LinkError(Exception):
    """사람에게 그대로 보여 줄 수 있는 실패(키·이미지 내용 없음)."""


def _detail(exc: Exception) -> str:
    return str(getattr(exc, "detail", None) or exc)[:600]


class GeminiLink:
    """`session_factory`는 DB 세션을, `store_factory`는 자격증명 저장소를 만든다(시험은 임시 DB·메모리 저장소를 넣는다)."""

    def __init__(self, session_factory: Callable[[], Any], store_factory: Callable[[], Any], *, user_id: int,
                 media_root: Path | None = None, question_transport: Any = None, image_client_factory: Callable[[str, str], Any] | None = None):
        self._session_factory = session_factory
        self._store_factory = store_factory
        self.user_id = user_id
        self._media_root = media_root
        self._question_transport = question_transport
        self._image_client_factory = image_client_factory

    # ---- 공통 ----

    def _company_id(self, db) -> int:
        row = db.execute(text("SELECT company_id FROM users WHERE id = :u"), {"u": self.user_id}).fetchone()
        if row is None or row[0] is None:
            raise LinkError(f"사용자 {self.user_id}을(를) 찾을 수 없습니다.")
        return int(row[0])

    def _image_ai(self, db):
        from app.domains.media_asset.image_ai_service import ImageAiService

        kwargs = {"client_factory": self._image_client_factory} if self._image_client_factory else {}
        return ImageAiService(db, self._store_factory(), **kwargs)

    def _queue(self, db):
        from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService

        return ImageGenerationJobQueueService(db, media_root=self._media_root) if self._media_root else ImageGenerationJobQueueService(db)

    def _file_path(self, queue, storage_path: str | None) -> str | None:
        if not storage_path:
            return None
        path = Path(queue.media_root) / storage_path
        return str(path.resolve()) if path.exists() else None

    # ---- 도구 1: 참고 사진 목록(읽기만) ----

    def list_photos(self, candidate_id: int | None = None) -> dict:
        db = self._session_factory()
        try:
            company_id = self._company_id(db)
            sql = ("SELECT id, owner_type, owner_id, purpose, mime_type, width, height, rights_status, storage_path, original_filename "
                   "FROM media_assets WHERE company_id = :c AND asset_role = 'ORIGINAL'")
            params: dict = {"c": company_id, "n": PHOTO_LIST_LIMIT}
            if candidate_id is not None:
                sql += " AND owner_type = 'PRODUCT_CANDIDATE' AND owner_id = :o"
                params["o"] = int(candidate_id)
            rows = db.execute(text(sql + " ORDER BY id DESC LIMIT :n"), params).fetchall()
            queue = self._queue(db)
            photos = []
            for r in rows:
                photos.append({
                    "사진_번호": r[0], "상품후보_번호": r[2] if r[1] == "PRODUCT_CANDIDATE" else None, "용도": r[3], "형식": r[4],
                    "크기": f"{r[5]}x{r[6]}" if r[5] and r[6] else None, "사용권": r[7], "파일": self._file_path(queue, r[8]), "원래_이름": r[9],
                })
            return {"사진": photos, "안내": "사용권이 VERIFIED가 아니면 참고 사진으로 쓰기 전에 앱에서 사용권 경고를 확인해야 합니다."}
        finally:
            db.close()

    # ---- 도구 2: 사진 만들기 ----

    def make_photo(self, reference_asset_id: int, *, purpose: str = "ADDITIONAL", backgrounds: list[str] | None = None,
                   poses: list[str] | None = None, count: int = 1, instruction: str | None = None, confirmed_main: bool = False) -> dict:
        from app.domains.media_asset import reference_generation as rg
        from app.domains.media_asset.reference_generation_service import ReferenceGenerationService

        db = self._session_factory()
        try:
            company_id = self._company_id(db)
            image_ai = self._image_ai(db)
            queue = self._queue(db)
            try:
                outcome = ReferenceGenerationService(db, queue=queue, image_ai=image_ai).generate(
                    company_id=company_id, requested_by=self.user_id, reference_asset_id=int(reference_asset_id), purpose=purpose,
                    backgrounds=list(backgrounds or ["HOME_INDOOR"]), poses=list(poses or ["KEEP_ORIGINAL"]), count=int(count),
                    confirmed_main=bool(confirmed_main), provider_code="GEMINI", instruction=instruction,
                )
            except (AppException, rg.ReferenceGenerationError, ExternalCallNotAllowed) as exc:
                status = image_ai.status(self.user_id, company_id)
                return {"만듦": 0, "실패_사유": _detail(exc), "지금_더_만들_수_있는_장수": status["images_left"],
                        "최근_31일_추정_사용액_달러": status["window_used_usd"]}
            status = image_ai.status(self.user_id, company_id)
            return {
                "만듦": len(outcome.assets),
                "사진": [{"사진_번호": a.id, "형식": a.mime_type, "크기": f"{a.width}x{a.height}", "파일": self._file_path(queue, a.storage_path)}
                         for a in outcome.assets],
                "안내": "대표 사진은 정하지 않았습니다. 상품이 참고 사진과 같은지 사람이 확인하세요. 'AI 생성' 표기는 사진 밖(설명·기록)에 둡니다.",
                "지금_더_만들_수_있는_장수": status["images_left"], "최근_31일_추정_사용액_달러": status["window_used_usd"],
            }
        finally:
            db.close()

    # ---- 도구 3: 글 질문 ----

    def ask(self, question: str, *, instruction: str | None = None, model: str | None = None) -> dict:
        from app.domains.ai_governance.gemini_question_service import DEFAULT_TEXT_MODEL
        from app.domains.ai_governance.gemini_question_service import GeminiQuestionService

        db = self._session_factory()
        try:
            company_id = self._company_id(db)
            service = GeminiQuestionService(db, self._store_factory(), transport=self._question_transport)
            try:
                result = service.ask(company_id, self.user_id, question, instruction=instruction, model=model or DEFAULT_TEXT_MODEL)
            except (AppException, ExternalCallNotAllowed) as exc:
                return {"답": None, "실패_사유": _detail(exc)}
            out = {"모델": result["model"], "이번_추정_비용_달러": result["cost_usd"], "최근_31일_추정_사용액_달러": result["window_used_usd"],
                   "월_크레딧_달러": result["monthly_credit_usd"]}
            if result["ok"]:
                out["답"] = result["answer"]
            else:
                out["답"] = None
                out["실패_사유"] = result["detail"]
            return out
        finally:
            db.close()

    # ---- 도구 4: 사용량(읽기만) ----

    def usage(self) -> dict:
        db = self._session_factory()
        try:
            company_id = self._company_id(db)
            image_ai = self._image_ai(db)
            s = image_ai.status(self.user_id, company_id)
            return {
                "준비됨": s["configured"], "키_저장됨": s["key_saved"], "월_크레딧_달러": s["monthly_credit_usd"],
                "최근_31일_추정_사용액_달러": s["window_used_usd"], "최근_31일_호출_수": s["window_calls"], "남은_크레딧_달러": s["window_remaining_usd"],
                "지금_더_만들_수_있는_사진_장수": s["images_left"], "알림_단계_퍼센트": s["credit_alert_pct"],
                "최근_기록": [{"언제_UTC": r["at"], "무엇": r["ref"], "추정_비용_달러": r["cost_usd"], "성공": r["ok"]} for r in image_ai.window_records(10)],
                "안내": "홈즈가 센 추정액입니다. 구글의 실제 크레딧 잔액·청구액은 키로 읽을 수 없어, 구글 AI 스튜디오 결제 화면에서 확인해야 합니다.",
            }
        finally:
            db.close()


__all__ = ["GeminiLink", "LinkError", "PHOTO_LIST_LIMIT"]
