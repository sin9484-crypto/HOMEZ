"""
=========================================================
Homez OS

File : app/domains/media_asset/derived_assets.py

2026-10-23 — 파생 자산(참고 사진 생성·일괄 보정 결과)을 원본에 **연결**하는 공통 규칙. 파생 이미지를 저장하는 `_persist_generated_asset`은 같은 바이트가 이미 있으면 그 자산을 그대로 돌려준다.
그 자산이 **전혀 다른 출처**(예: 사용자가 같은 사진을 직접 올린 원본, 다른 사진에서 파생된 자산)일 수 있으므로, 그 자산의 출처·권리 상태·파일 이름을 덮어쓰면 안 된다 —
특히 권리 상태가 **격상**되면(미확인 → 확인됨) 안 된다.

`adopt_derived`는 방금 새로 만들어진 파생 자산(`asset_role == GENERATED`이고 아직 출처가 없다)에만 연결 정보를 **한 번** 쓴다. 이미 **같은 출처**에서 파생된 자산(반복 실행)은
아무것도 바꾸지 않고 `True`를 돌려준다. 그 밖의 경우(원본 자산이 우연히 같은 바이트였거나 다른 출처의 파생물)는 **건드리지 않고** `False`를 돌려준다 —
호출하는 쪽은 그 자산을 "이미 있던 자산 재사용"으로 보고한다.
=========================================================
"""

from __future__ import annotations

from app.domains.media_asset.constants import MediaAssetRole


def adopt_derived(derived, source, filename: str) -> bool:
    """새 파생 자산에 출처(source_asset_id)·권리 상태 승계·파일 이름을 한 번 기록한다. 기록했거나 이미 같은 출처면 True, 남의 자산이면 손대지 않고 False."""

    if derived.asset_role != MediaAssetRole.GENERATED:
        return False
    if derived.source_asset_id is None:
        derived.source_asset_id = source.id
        derived.rights_status = source.rights_status   # 만들어질 때 한 번만 물려받는다(원본이 나중에 바뀌어도 자동으로 따라가지 않는다 — model.py의 승계 원칙)
        derived.original_filename = filename[:255]
        return True
    return derived.source_asset_id == source.id          # 같은 출처에서 다시 만든 경우: 이미 연결돼 있으니 아무것도 바꾸지 않는다


__all__ = ["adopt_derived"]
