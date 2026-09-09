"""
=========================================================
Homez OS

File : app/domains/media_asset/public_hosting.py

Cloudflare R2(S3 호환 API) 상품 이미지 공개 호스팅.

쿠팡 Live 상품등록은 vendorPath로 공개 HTTPS URL만 받는다(로컬 파일
경로 불가 — app/domains/marketplace_listing/required_fields_schemas.py
의 CoupangProductImage 계약 참고). HOMEZ는 이미지를 로컬 디스크에만
저장하므로(app/domains/media_asset/storage.py), 실제 제출 직전
사용자가 승인한 이미지만 이 서비스를 통해 R2에 업로드하고 공개 URL을
얻는다.

원칙:
  - 권리가 VERIFIED인 자산만 업로드한다(RIGHTS_UNVERIFIED는 차단,
    fail-closed) — app/domains/media_asset/model.py의 rights_status
    계약을 그대로 따른다.
  - 같은 자산을 두 번 업로드하지 않는다 — media_assets.public_url이
    이미 있으면 그대로 재사용한다(idempotent, R2 비용·API 호출 절약).
  - object key는 company_id+sha256_hex로 결정적으로 계산한다 — 같은
    내용의 이미지는 항상 같은 key가 되어, 재계산해도 같은 URL이
    나온다(우연한 중복 업로드가 있어도 안전 — put_object는 같은
    key를 덮어쓸 뿐 내용이 달라지지 않는다).
  - Credential은 Windows Credential Manager에만 저장한다(평문 설정
    파일·DB에 저장하지 않는다) — app/core/windows_credential_store.py
    의 기존 계약을 그대로 재사용한다. 이 모듈의 어떤 예외 메시지에도
    Secret 값을 포함하지 않는다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import read_media_file

PROVIDER_CODE = "CLOUDFLARE_R2"
CREDENTIAL_TARGET_NAME = "HOMEZ:media_public_hosting:r2"

# 쿠팡이 실제로 허용하는 형식만(공식 상품등록 API 스펙 — JPG/PNG).
_MIME_TO_EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
}


class MediaHostingNotConfiguredError(Exception):
    """R2 Credential이 아직 저장되지 않았다."""


@dataclass(frozen=True)
class R2Credentials:

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    # 마지막 슬래시 없이 저장한다(save_r2_credentials가 정규화).
    public_base_url: str


def save_r2_credentials(store: CredentialStore, creds: R2Credentials) -> None:

    store.save(
        CREDENTIAL_TARGET_NAME,
        {
            "account_id": creds.account_id,
            "access_key_id": creds.access_key_id,
            "secret_access_key": creds.secret_access_key,
            "bucket_name": creds.bucket_name,
            "public_base_url": creds.public_base_url.rstrip("/"),
        },
    )


def load_r2_credentials(store: CredentialStore) -> R2Credentials:

    try:
        data = store.read(CREDENTIAL_TARGET_NAME)
    except CredentialNotFoundError as exc:
        raise MediaHostingNotConfiguredError(
            "R2 공개 호스팅 Credential이 아직 저장되지 않았습니다 — "
            "먼저 설정을 완료해야 합니다.",
        ) from exc

    return R2Credentials(**data)


def build_object_key(company_id: int, sha256_hex: str, extension: str) -> str:
    """company_id+sha256 기반 결정적 key — 같은 이미지는 항상 같은
    key가 된다(우연한 중복 업로드도 안전, 재계산해도 URL 불변)."""

    return f"company-{company_id}/{sha256_hex}.{extension}"


class R2MediaHostingService:
    """
    MediaAsset을 R2에 업로드해 공개 URL을 만든다. media_root가 없으면
    app/desktop/paths.py::get_media_dir()로 지연 계산한다(frozen 모드
    경로 계약을 그대로 따름 — app/domains/media_asset/job_queue_service.py
    의 기존 패턴과 동일).
    """

    def __init__(
        self,
        db: Session,
        credential_store: CredentialStore,
        media_root: Path | None = None,
    ):

        self.db = db
        self.credential_store = credential_store

        if media_root is None:
            from app.desktop.paths import get_media_dir

            media_root = get_media_dir()

        self.media_root = media_root

    def _build_client(self, creds: R2Credentials):

        endpoint_url = f"https://{creds.account_id}.r2.cloudflarestorage.com"

        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def ensure_public_url(self, media_asset: MediaAsset) -> str:
        """이 자산의 공개 URL을 반환한다 — 이미 업로드됐으면(캐시)
        그대로 재사용하고, 아니면 업로드한 뒤 media_assets 행을
        갱신한다(flush만 수행 — commit은 호출자 책임, 다른 서비스
        메서드들과 동일한 Transaction 경계 컨벤션)."""

        if media_asset.public_url:
            return media_asset.public_url

        # 2026-08-28 사용자 결정 — 권리 미확인(RIGHTS_UNVERIFIED) 이미지도
        # 공개 업로드를 차단하지 않는다(이전 하드 차단 대체). 경고·사용자
        # 진행-선택 기록은 화면과 ImageRightsAcknowledgementService가
        # 담당하며, 실제 판매채널 제출 직전에는 별도로 다시 경고한다
        # (listing_wizard_live_service.py). RIGHTS_DENIED(명시적
        # 사용금지·권리철회·삭제요청·신고/분쟁)는 예외 — 계속 차단.
        if media_asset.rights_status == "RIGHTS_DENIED":
            raise BadRequestException(
                f"media_asset={media_asset.id}: 사용이 금지된 이미지는 "
                "외부에 공개 업로드할 수 없습니다(rights_status=RIGHTS_DENIED).",
            )

        extension = _MIME_TO_EXTENSION.get(media_asset.mime_type)
        if extension is None:
            raise BadRequestException(
                f"media_asset={media_asset.id}: 지원하지 않는 이미지 "
                f"형식입니다(mime_type={media_asset.mime_type}) — "
                "쿠팡은 JPG/PNG만 허용합니다.",
            )

        creds = load_r2_credentials(self.credential_store)
        client = self._build_client(creds)
        object_key = build_object_key(
            media_asset.company_id, media_asset.sha256_hex, extension,
        )

        data = read_media_file(self.media_root, media_asset.storage_path)

        try:
            client.put_object(
                Bucket=creds.bucket_name,
                Key=object_key,
                Body=data,
                ContentType=media_asset.mime_type,
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "UNKNOWN")
            raise ConflictException(
                f"media_asset={media_asset.id}: R2 업로드 실패 "
                f"(error_code={error_code}).",
            ) from exc

        public_url = f"{creds.public_base_url}/{object_key}"

        media_asset.public_url = public_url
        media_asset.public_url_provider = PROVIDER_CODE
        media_asset.public_url_uploaded_at = datetime.now(timezone.utc)
        self.db.flush()

        return public_url


__all__ = [
    "PROVIDER_CODE",
    "CREDENTIAL_TARGET_NAME",
    "MediaHostingNotConfiguredError",
    "R2Credentials",
    "R2MediaHostingService",
    "save_r2_credentials",
    "load_r2_credentials",
    "build_object_key",
]
