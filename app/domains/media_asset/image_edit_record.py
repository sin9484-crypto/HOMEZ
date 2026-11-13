"""
=========================================================
Homez OS

File : app/domains/media_asset/image_edit_record.py

2026-11-13 — **이미지 AI 편집 기록**(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-3, 사용자 지시 "다음 작업 진행해라").
사진마다 "무엇을 했는지"(원본 그대로 / 나눈 조각 / 배경 지움 / 직접 편집 / 밝기·색 보정 / 배경 교체 / 장면 합성 / AI가 그린 배경 / AI 생성 / 상세 페이지 조립)를
원본까지 거슬러 올라가며 단계별로 돌려준다. 새 표 없이 **이미 남아 있는 처리 기록**(image_generation_jobs·results)과 사진의 출처(source_asset_id)만 읽는다.

근거(2026-11-13 확인):
  - 과기정통부 '인공지능 투명성 확보 안내 지침'(2026-01, 보도 기준) — 표시 의무는 AI 서비스를 제공하는 사업자에게 있고, AI를 도구로 쓰는 이용자는 대상이 아니다.
    계도기간 1년 이상. 판매자인 우리는 의무 대상이 아니지만, 기록을 남겨 두면 나중에 표시가 필요해져도 소급하지 않아도 된다.
  - 쿠팡 마켓의 AI 이미지 규정 원문은 찾지 못했다. 대표 이미지는 흰 배경·상품만이 원칙(2026-07 정책 보도).

지키는 것:
  - **읽기 전용**(DB 쓰기 0). 회사 범위 밖 사진은 없는 것으로 다룬다.
  - **모르는 것은 모름으로** — 처리 기록이 없는 생성 사진은 UNKNOWN. 단 하나의 예외는 '긴 상세 이미지 나누기'다: 이 흐름만 처리 기록을 남기지 않으며
    (job_queue_service.split_long_image·upload_and_split_long_detail_image), 결과는 항상 용도 DETAIL·출처 DETAIL_SOURCE 원본이라 그 모양으로만 SPLIT으로 본다.
  - 출처 고리는 최대 12단계, 같은 사진이 다시 나오면 멈춘다(고리 오류로 멈추지 않게).
=========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from sqlalchemy.orm import Session

from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset

MAX_DEPTH = 12

# 단계 종류
ORIGINAL = "ORIGINAL"                    # 올린 원본 그대로
SPLIT = "SPLIT"                          # 긴 상세 이미지를 나눈 조각(자르기만)
CUTOUT = "CUTOUT"                        # 배경 지움(누끼, PC 안 ML)
MANUAL_EDIT = "MANUAL_EDIT"              # 직접 편집(자르기·회전·밝기·도형)
ADJUST = "ADJUST"                        # 밝기·색온도 일괄 보정
BACKGROUND_SWAP = "BACKGROUND_SWAP"      # 단색·그라데이션 배경으로 교체(PC 안)
SCENE = "SCENE"                          # 질감·조명 장면 합성(PC 안 코드로 그림 — 생성형 아님)
BACKGROUND_PHOTO = "BACKGROUND_PHOTO"    # 다른 사진을 배경으로 합성
AI_BACKGROUND = "AI_BACKGROUND"          # AI가 그린 배경에 실제 상품 합성(Cloudflare)
AI_GENERATED = "AI_GENERATED"            # AI가 만든 이미지(참고 사진 생성 등)
DETAIL_PAGE = "DETAIL_PAGE"              # 상세 페이지 조립(PC 안)
UNKNOWN = "UNKNOWN"                      # 처리 기록 없음

ALL_KINDS = (ORIGINAL, SPLIT, CUTOUT, MANUAL_EDIT, ADJUST, BACKGROUND_SWAP, SCENE, BACKGROUND_PHOTO, AI_BACKGROUND, AI_GENERATED, DETAIL_PAGE, UNKNOWN)
GENERATIVE_KINDS = frozenset({AI_BACKGROUND, AI_GENERATED})
STAGED_KINDS = frozenset({SCENE, BACKGROUND_PHOTO, AI_BACKGROUND, AI_GENERATED})   # 흰 배경·상품만이 아닌 연출

_PLAIN_BACKGROUND_KINDS = {"WHITE", "SOLID_COLOR", "GRADIENT"}


@dataclass(frozen=True)
class EditStep:
    kind: str
    asset_id: int
    operation: str | None = None
    detail: str | None = None      # 컨셉·배경 코드 등(화면에서 그대로 보이지 않는 코드)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "asset_id": self.asset_id, "operation": self.operation, "detail": self.detail}


@dataclass(frozen=True)
class ImageEditRecord:
    asset_id: int
    steps: tuple[EditStep, ...] = field(default_factory=tuple)     # 원본 쪽 → 이 사진 쪽 순서
    truncated: bool = False

    @property
    def kinds(self) -> list[str]:
        return [s.kind for s in self.steps]

    @property
    def generative(self) -> bool:
        return any(k in GENERATIVE_KINDS for k in self.kinds)

    @property
    def staged(self) -> bool:
        return any(k in STAGED_KINDS for k in self.kinds)

    @property
    def unknown(self) -> bool:
        return self.truncated or UNKNOWN in self.kinds

    def to_dict(self) -> dict[str, Any]:
        return {"asset_id": self.asset_id, "steps": [s.to_dict() for s in self.steps], "generative": self.generative,
                "staged": self.staged, "unknown": self.unknown}


def _payload(job: ImageGenerationJob) -> dict:
    try:
        value = json.loads(job.request_payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _concept_kind(code: str | None) -> str:
    concept = ic.CONCEPTS.get(code or "")
    if concept is None:
        return UNKNOWN
    if concept.background_kind == "SCENE":
        return SCENE
    return BACKGROUND_SWAP


def classify_job(job: ImageGenerationJob) -> tuple[str, str | None, str | None]:
    """처리 기록 하나 → (단계 종류, 작업 이름, 세부 코드). 순수 함수."""

    payload = _payload(job)
    operation = payload.get("operation") if isinstance(payload.get("operation"), str) else None
    model = job.model_name
    if operation == "BACKGROUND_REMOVAL":
        return CUTOUT, operation, None
    if operation == "MANUAL_EDIT":
        return MANUAL_EDIT, operation, None
    if operation == "BATCH_ADJUST":
        return ADJUST, operation, model
    if operation == "BACKGROUND_COMPOSITION":
        if model in _PLAIN_BACKGROUND_KINDS:
            return BACKGROUND_SWAP, operation, model
        if model == "SCENE":
            return SCENE, operation, model
        if model == "EXISTING_ASSET":
            return BACKGROUND_PHOTO, operation, model
        return UNKNOWN, operation, model
    if operation == "ADAPTIVE_BACKGROUND_COMPOSITION":
        return BACKGROUND_SWAP, operation, model          # 적응형 배경은 모두 PC 안 단색·그라데이션·빛 번짐(background_designer)
    if operation == "CONCEPT_RUN":
        return _concept_kind(model), operation, model
    if operation == "AI_BACKGROUND_RUN":
        code = (model or "").rsplit("-v", 1)[0]
        return AI_BACKGROUND, operation, code if code in abc.AI_CONCEPTS else model
    if operation == "REFERENCE_GENERATION":
        return AI_GENERATED, operation, job.provider_code
    if operation == "DETAIL_PAGE_GENERATION":
        return DETAIL_PAGE, operation, None
    if operation is None and job.provider_code not in ("PILLOW_LOCAL", "REMBG", "LOCAL_MANUAL_EDIT"):
        return AI_GENERATED, None, job.provider_code       # 이미지 생성 작업(submit_job) — 작업 이름 없이 생성 제공자만 남는다
    return UNKNOWN, operation, model


class ImageEditRecordService:

    def __init__(self, db: Session):
        self.db = db

    def _producing_jobs(self, company_id: int, asset_ids: set[int]) -> dict[int, ImageGenerationJob]:
        """사진 → 그 사진을 만든 마지막 성공 처리 기록."""

        if not asset_ids:
            return {}
        rows = (
            self.db.query(ImageGenerationResult.media_asset_id, ImageGenerationJob)
            .join(ImageGenerationJob, ImageGenerationJob.id == ImageGenerationResult.job_id)
            .filter(ImageGenerationResult.company_id == company_id)
            .filter(ImageGenerationJob.company_id == company_id)
            .filter(ImageGenerationJob.status == "SUCCEEDED")
            .filter(ImageGenerationResult.media_asset_id.in_(asset_ids))
            .order_by(ImageGenerationJob.id)
            .all()
        )
        return {asset_id: job for asset_id, job in rows}

    def _load(self, company_id: int, ids: set[int], cache: dict[int, MediaAsset | None]) -> None:
        missing = {i for i in ids if i not in cache}
        if not missing:
            return
        found = self.db.query(MediaAsset).filter(MediaAsset.company_id == company_id).filter(MediaAsset.id.in_(missing)).all()
        by_id = {a.id: a for a in found}
        for i in missing:
            cache[i] = by_id.get(i)

    def records(self, company_id: int, asset_ids: list[int]) -> dict[int, ImageEditRecord]:
        # bool은 int라 True가 1과 같은 키가 된다 — 먼저 걸러 낸 뒤 겹침을 없앤다.
        wanted = list(dict.fromkeys(i for i in (asset_ids or []) if isinstance(i, int) and not isinstance(i, bool)))
        cache: dict[int, MediaAsset | None] = {}
        self._load(company_id, set(wanted), cache)

        # 출처 고리를 한 번에 몇 단계씩 모아 읽는다.
        frontier = {a.source_asset_id for a in cache.values() if a is not None and a.source_asset_id}
        for _ in range(MAX_DEPTH):
            frontier -= set(cache)
            if not frontier:
                break
            self._load(company_id, frontier, cache)
            frontier = {a.source_asset_id for a in cache.values() if a is not None and a.source_asset_id}
        jobs = self._producing_jobs(company_id, {i for i, a in cache.items() if a is not None and a.asset_role == "GENERATED"})

        out: dict[int, ImageEditRecord] = {}
        for asset_id in wanted:
            asset = cache.get(asset_id)
            if asset is None:
                continue
            out[asset_id] = self._record(asset, cache, jobs)
        return out

    def _record(self, asset: MediaAsset, cache: dict[int, MediaAsset | None], jobs: dict[int, ImageGenerationJob]) -> ImageEditRecord:
        steps: list[EditStep] = []
        seen: set[int] = set()
        current: MediaAsset | None = asset
        truncated = False
        while current is not None:
            if current.id in seen or len(steps) >= MAX_DEPTH:
                truncated = True
                break
            seen.add(current.id)
            if current.asset_role != "GENERATED":
                steps.append(EditStep(ORIGINAL, current.id))
                break
            job = jobs.get(current.id)
            source = cache.get(current.source_asset_id) if current.source_asset_id else None
            if job is not None:
                kind, operation, detail = classify_job(job)
                steps.append(EditStep(kind, current.id, operation, detail))
            elif source is not None and source.purpose == "DETAIL_SOURCE" and current.purpose == "DETAIL":
                steps.append(EditStep(SPLIT, current.id))
            else:
                steps.append(EditStep(UNKNOWN, current.id))
            if current.source_asset_id and source is None:
                truncated = True          # 출처가 있다고 적혀 있는데 읽을 수 없다(다른 회사·삭제·고리 한도)
                break
            current = source
        return ImageEditRecord(asset.id, tuple(reversed(steps)), truncated)


__all__ = [
    "ALL_KINDS", "GENERATIVE_KINDS", "STAGED_KINDS", "ORIGINAL", "SPLIT", "CUTOUT", "MANUAL_EDIT", "ADJUST", "BACKGROUND_SWAP", "SCENE",
    "BACKGROUND_PHOTO", "AI_BACKGROUND", "AI_GENERATED", "DETAIL_PAGE", "UNKNOWN", "EditStep", "ImageEditRecord", "ImageEditRecordService", "classify_job",
]
