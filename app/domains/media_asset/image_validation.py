"""
=========================================================
Homez OS

File : app/domains/media_asset/image_validation.py

이미지 바이트 검증(signature/MIME/해상도) + 저장 경로 계산.

Pillow 등 신규 외부 이미지 라이브러리를 추가하지 않는다(승인 없는
패키지 설치 금지 원칙) — PNG/JPEG/WEBP의 최소 필요한 바이너리
헤더만 표준 라이브러리로 직접 파싱한다. 해상도를 신뢰성 있게 읽지
못하면(포맷이 예상과 다르거나 손상됨) fail-closed로 거부한다 —
"모르니까 통과"시키지 않는다.

storage_path는 항상 서버가 계산한다 — 사용자가 올린 원본 파일명을
그대로 파일시스템 경로에 쓰지 않는다(path traversal 방지). 파일명은
company_id/sha256 기반 결정론적 경로로만 만든다.
=========================================================
"""

import struct

from app.core.exceptions import BadRequestException
from app.domains.media_asset.constants import ALLOWED_MIME_TYPES
from app.domains.media_asset.constants import MAX_IMAGE_FILE_SIZE_BYTES
from app.domains.media_asset.constants import MAX_IMAGE_HEIGHT
from app.domains.media_asset.constants import MAX_IMAGE_WIDTH
from app.domains.media_asset.constants import MAX_LONG_DETAIL_IMAGE_FILE_SIZE_BYTES
from app.domains.media_asset.constants import MAX_LONG_DETAIL_IMAGE_HEIGHT
from app.domains.media_asset.constants import MAX_LONG_DETAIL_IMAGE_PIXELS
from app.domains.media_asset.constants import MIN_IMAGE_HEIGHT
from app.domains.media_asset.constants import MIN_IMAGE_WIDTH

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_JPEG_SOF_MARKERS = frozenset({
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
})


class ImageValidationResult:

    def __init__(self, mime_type: str, width: int, height: int):

        self.mime_type = mime_type
        self.width = width
        self.height = height


def _parse_png(data: bytes) -> tuple[int, int]:

    if len(data) < 24:
        raise BadRequestException("PNG 헤더가 손상되었습니다(길이 부족).")

    width, height = struct.unpack(">II", data[16:24])

    return width, height


def _parse_jpeg(data: bytes) -> tuple[int, int]:

    pos = 2  # 0xFFD8 이후

    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise BadRequestException("JPEG 마커 구조가 손상되었습니다.")

        marker = data[pos + 1]
        pos += 2

        if marker in (0xD8, 0xD9):  # SOI/EOI, 길이 필드 없음
            continue

        if pos + 2 > len(data):
            break

        segment_length = struct.unpack(">H", data[pos:pos + 2])[0]

        if marker in _JPEG_SOF_MARKERS:
            if pos + 7 > len(data):
                raise BadRequestException("JPEG SOF 세그먼트가 손상되었습니다.")

            height, width = struct.unpack(">HH", data[pos + 3:pos + 7])

            return width, height

        pos += segment_length

    raise BadRequestException("JPEG에서 SOF(해상도 정보) 세그먼트를 찾지 못했습니다.")


def _parse_webp(data: bytes) -> tuple[int, int]:

    if len(data) < 30 or data[8:12] != b"WEBP":
        raise BadRequestException("WEBP 헤더가 손상되었습니다.")

    chunk_id = data[12:16]

    if chunk_id == b"VP8X":
        # flags(1) + reserved(3) + width-1(3, LE) + height-1(3, LE)
        w_minus1 = int.from_bytes(data[24:27], "little")
        h_minus1 = int.from_bytes(data[27:30], "little")

        return w_minus1 + 1, h_minus1 + 1

    if chunk_id == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1

        return width, height

    if chunk_id == b"VP8 " and len(data) >= 30:
        if data[23:26] != b"\x9d\x01\x2a":
            raise BadRequestException("WEBP(VP8) 시작 코드가 손상되었습니다.")

        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF

        return width, height

    raise BadRequestException(f"지원하지 않는 WEBP 하위 포맷입니다: {chunk_id!r}")


def _sniff_format_and_size(data: bytes) -> tuple[str, int, int]:
    """
    signature만으로 포맷을 판정하고 헤더 바이트만 읽어 해상도를
    구한다(확장자/self-reported MIME은 신뢰하지 않는다) — 전체 이미지
    디코딩(PIL 등)을 전혀 하지 않으므로 이 단계 자체는 파일 크기와
    무관하게 항상 메모리 안전하다(압축폭탄이어도 헤더 몇十바이트만
    읽는다).
    """

    if data.startswith(_PNG_SIGNATURE):
        return "image/png", *_parse_png(data)
    if data.startswith(_JPEG_SIGNATURE):
        return "image/jpeg", *_parse_jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", *_parse_webp(data)

    raise BadRequestException(
        "지원하지 않는 이미지 형식입니다(PNG/JPEG/WEBP signature 불일치).",
    )


def validate_image_bytes(data: bytes) -> ImageValidationResult:
    """
    일반 상품 이미지(대표/상세/썸네일) 검증 — 기존 제한(MAX_IMAGE_
    HEIGHT=4096 등) 그대로. 실패하면 BadRequestException(fail-closed).
    """

    if len(data) == 0:
        raise BadRequestException("빈 파일은 업로드할 수 없습니다.")

    if len(data) > MAX_IMAGE_FILE_SIZE_BYTES:
        raise BadRequestException(
            f"파일 크기가 제한({MAX_IMAGE_FILE_SIZE_BYTES} bytes)을 "
            f"초과합니다: {len(data)} bytes",
        )

    mime_type, width, height = _sniff_format_and_size(data)

    if mime_type not in ALLOWED_MIME_TYPES:
        raise BadRequestException(f"허용되지 않는 MIME 타입입니다: {mime_type}")

    if not (MIN_IMAGE_WIDTH <= width <= MAX_IMAGE_WIDTH):
        raise BadRequestException(
            f"이미지 너비가 허용 범위({MIN_IMAGE_WIDTH}~{MAX_IMAGE_WIDTH})를 "
            f"벗어났습니다: {width}",
        )

    if not (MIN_IMAGE_HEIGHT <= height <= MAX_IMAGE_HEIGHT):
        raise BadRequestException(
            f"이미지 높이가 허용 범위({MIN_IMAGE_HEIGHT}~{MAX_IMAGE_HEIGHT})를 "
            f"벗어났습니다: {height}",
        )

    return ImageValidationResult(mime_type, width, height)


def validate_long_detail_image_bytes(data: bytes) -> ImageValidationResult:
    """
    "상세페이지용 긴 이미지"로 사용자가 명시적으로 선택한 업로드
    경로 전용 — 일반 이미지 한도(validate_image_bytes)를 전역으로
    올리지 않고, 이 경로에서만 더 넓지만 여전히 유한한 한도
    (MAX_LONG_DETAIL_IMAGE_*)를 적용한다. 폭 제한은 일반 이미지와
    동일하게 유지한다(긴 건 세로 방향뿐이라는 전제) — 가로 폭까지
    넓히면 압축폭탄 방어 의미가 약해진다.

    이 함수도 헤더만 읽는다(_sniff_format_and_size) — 실제 PIL 디코딩은
    호출자가 이 검증을 통과한 뒤(픽셀 수 상한 이내임을 이미 확인한
    뒤)에만 수행해 메모리 안전을 보장한다.
    """

    if len(data) == 0:
        raise BadRequestException("빈 파일은 업로드할 수 없습니다.")

    if len(data) > MAX_LONG_DETAIL_IMAGE_FILE_SIZE_BYTES:
        raise BadRequestException(
            "파일 크기가 긴 이미지 제한"
            f"({MAX_LONG_DETAIL_IMAGE_FILE_SIZE_BYTES} bytes)을 "
            f"초과합니다: {len(data)} bytes",
        )

    mime_type, width, height = _sniff_format_and_size(data)

    if mime_type not in ALLOWED_MIME_TYPES:
        raise BadRequestException(f"허용되지 않는 MIME 타입입니다: {mime_type}")

    if not (MIN_IMAGE_WIDTH <= width <= MAX_IMAGE_WIDTH):
        raise BadRequestException(
            f"이미지 너비가 허용 범위({MIN_IMAGE_WIDTH}~{MAX_IMAGE_WIDTH})를 "
            f"벗어났습니다: {width}",
        )

    if not (MIN_IMAGE_HEIGHT <= height <= MAX_LONG_DETAIL_IMAGE_HEIGHT):
        raise BadRequestException(
            "이미지 높이가 긴 이미지 허용 범위"
            f"({MIN_IMAGE_HEIGHT}~{MAX_LONG_DETAIL_IMAGE_HEIGHT})를 "
            f"벗어났습니다: {height}",
        )

    if width * height > MAX_LONG_DETAIL_IMAGE_PIXELS:
        raise BadRequestException(
            "COMPRESSION_BOMB_SUSPECTED: 이미지 픽셀 수가 상한"
            f"({MAX_LONG_DETAIL_IMAGE_PIXELS})을 초과합니다: "
            f"{width * height}",
        )

    return ImageValidationResult(mime_type, width, height)


_MIME_TO_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def build_storage_path(
    company_id: int, sha256_hex: str, mime_type: str,
) -> str:
    """
    사용자 입력(원본 파일명 등)을 절대 경로 계산에 쓰지 않는다 —
    company_id(정수)와 sha256_hex(이 함수 호출 전에 서버가 직접
    계산한 값)만으로 결정론적 경로를 만든다. sha256_hex는 정확히
    64자리 소문자 hex여야 한다(형식이 다르면 상위 호출부 버그로
    간주해 즉시 차단).
    """

    if len(sha256_hex) != 64 or not all(
        c in "0123456789abcdef" for c in sha256_hex
    ):
        raise BadRequestException("sha256_hex 형식이 올바르지 않습니다.")

    extension = _MIME_TO_EXTENSION.get(mime_type)
    if extension is None:
        raise BadRequestException(f"알 수 없는 MIME 타입입니다: {mime_type}")

    return (
        f"media/{int(company_id)}/{sha256_hex[:2]}/"
        f"{sha256_hex}.{extension}"
    )


__all__ = [
    "ImageValidationResult",
    "validate_image_bytes",
    "validate_long_detail_image_bytes",
    "build_storage_path",
]
