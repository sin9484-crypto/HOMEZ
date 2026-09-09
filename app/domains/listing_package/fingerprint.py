"""
=========================================================
Homez OS

File : app/domains/listing_package/fingerprint.py

app/domains/marketplace_listing/fingerprint.py와 동일한 SHA-256 +
canonical JSON 패턴(도메인 간 모듈을 직접 import하지 않으므로
패턴만 복제한다).

package_fingerprint는 "지금 이 패키지가 승인받을 만한 상태와 같은
상태인가"를 판단하는 유일한 근거다 — draft/채널선택/이미지자산목록/
mode를 전부 포함해야 한다. status 자체는 포함하지 않는다
(marketplace_listing/fingerprint.py에서 이미 겪은 순환 무효화
버그와 같은 이유 — 승인 자체가 status를 바꾸는 워크플로 필드는
지문에서 제외한다).

2026-08-02 CTO 보안 보완 Gate R4: 이미지 부분은 asset_id 목록만
(그것도 정렬해서) 넣던 이전 설계를 교체한다 — 그 설계로는 (1) 순서를
바꿔도 fingerprint가 그대로였고(sorted 때문에), (2) 같은 asset_id의
파일 내용이 재생성으로 바뀌어도 새 id가 발급되지 않는 한 감지할
방법이 없었다. 이제는 각 이미지의 asset_id/sha256/purpose/sequence/
mime_type/width/height/file_size/source_asset_id를 전부 담은
descriptor 목록을 **순서를 바꾸지 않고 그대로** 지문에 포함한다 —
`canonical_json`의 `sort_keys=True`는 각 dict의 key 순서만 정렬할 뿐
list(배열) 요소 순서에는 영향을 주지 않으므로, 호출자가 넘긴
descriptor 목록의 순서가 곧 지문에 반영되는 "이미지 순서"다(반드시
실제 표시 순서 그대로 넘겨야 한다 — 정렬해서 넘기면 이전과 같은
결함이 재발한다).
=========================================================
"""

import hashlib
import json


def canonical_json(data: dict) -> str:

    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_package_fingerprint(
    draft_payload_json: str,
    channel_selection_json: str,
    image_descriptors: list[dict],
    mode: str,
) -> str:
    """
    image_descriptors는 실제 표시 순서 그대로 전달해야 한다(여기서
    정렬하지 않는다 — 정렬하면 순서 변경이 지문에 반영되지 않는다).
    각 원소는 최소한 asset_id/sha256/purpose/sequence/mime_type/
    width/height/file_size/source_asset_id를 포함해야 한다.
    """

    return sha256_hex(canonical_json({
        "draft_payload": json.loads(draft_payload_json),
        "channel_selection": json.loads(channel_selection_json),
        "images": image_descriptors,
        "mode": mode,
    }))


__all__ = [
    "canonical_json",
    "sha256_hex",
    "compute_package_fingerprint",
]
