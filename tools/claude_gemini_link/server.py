"""
=========================================================
Homez OS

File : tools/claude_gemini_link/server.py

2026-11-23 — 클로드 연결 장치(MCP, 표준 입출력). 새 패키지를 설치하지 않으려고 통신 규약(JSON-RPC 2.0, 한 줄에 한 메시지)을 직접 구현한다.
지원: initialize · notifications/initialized · ping · tools/list · tools/call. 그 밖의 요청은 '모르는 요청' 오류.

  - 표준 출력에는 규약 메시지만 쓴다(앱 코드가 무엇을 출력해도 표준 오류로 돌린다).
  - 도구 이름은 규약상 영문만 되지만, 설명·결과는 한국어다.
  - 실행: `.mcp.json`이 저장소의 venv 파이썬으로 이 파일을 띄운다. 작업 폴더를 저장소로 옮겨 앱과 같은 설정·DB를 쓴다.
  - 사용자: 환경 변수 HOMEZ_LINK_USER_ID(기본 1 — 제미나이 설정을 저장한 최고 관리자).
=========================================================
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "homez-gemini", "version": "1.0.0"}

_PURPOSES = ["ADDITIONAL", "DETAIL", "MAIN"]
_BACKGROUNDS = ["CAFE", "WALKING_PATH", "STREET", "SEASIDE", "HOME_INDOOR", "STUDIO_PLAIN"]
_POSES = ["KEEP_ORIGINAL", "ARMS_DOWN", "HAND_ON_HIP", "BODY_TURN_LEFT", "BODY_TURN_RIGHT", "WALKING"]

TOOLS = [
    {
        "name": "gemini_list_photos",
        "description": "홈즈에 있는 원본 상품 사진 목록(사진 번호·상품 후보 번호·사용권·파일 위치)을 읽는다. 바깥 호출·비용 없음. 사진 만들기 전에 참고 사진 번호를 고를 때 쓴다.",
        "inputSchema": {"type": "object", "properties": {"candidate_id": {"type": "integer", "description": "상품 후보 번호(비우면 전체 최근 50장)"}},
                        "additionalProperties": False},
    },
    {
        "name": "gemini_make_photo",
        "description": ("참고 사진 한 장으로 제미나이가 같은 상품 사진을 만들어 홈즈에 파생 사진으로 저장한다. 장마다 사용료(2K 약 0.055달러)가 들고 "
                        "월 크레딧 100%를 넘으면 만들지 않는다. 장마다 승인 센터 허락 1회가 필요하다(없으면 만들지 않고 요청이 남는다). "
                        "대표(MAIN)는 사용자 최종 확인(confirmed_main)이 있어야 한다. 사용자가 사진 만들기를 요청했을 때만 쓴다."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_asset_id": {"type": "integer", "description": "참고 사진 번호(gemini_list_photos로 확인)"},
                "purpose": {"type": "string", "enum": _PURPOSES, "description": "추가 이미지(기본)·상세 이미지·대표 이미지"},
                "backgrounds": {"type": "array", "items": {"type": "string", "enum": _BACKGROUNDS}, "maxItems": 3, "description": "배경(기본 실내)"},
                "poses": {"type": "array", "items": {"type": "string", "enum": _POSES}, "maxItems": 3, "description": "모습(기본 원래 모습 유지)"},
                "count": {"type": "integer", "minimum": 1, "maximum": 6, "description": "만들 장수(배경×모습 조합 수 이하)"},
                "instruction": {"type": "string", "maxLength": 500, "description": "요청 글(선택). 상품을 바꾸거나 글자를 넣으라는 요청은 규칙상 따르지 않는다."},
                "confirmed_main": {"type": "boolean", "description": "대표 이미지일 때 사용자가 최종 확인했는가"},
            },
            "required": ["reference_asset_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gemini_ask",
        "description": ("제미나이에게 글 질문을 보내고 답을 받는다(기본 모델 gemini-3.5-flash-lite). 보내는 것은 질문 글과 지시 글뿐이다 — "
                        "고객 개인정보·비밀값을 넣지 않는다. 질문마다 사용료가 들고 월 크레딧 100%를 넘으면 부르지 않는다. 승인 센터 허락(제미나이 글 질문) 1회가 필요하다."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "maxLength": 8000, "description": "질문 글"},
                "instruction": {"type": "string", "maxLength": 2000, "description": "답하는 방식 지시(선택)"},
                "model": {"type": "string", "enum": ["gemini-3.5-flash-lite", "gemini-3.8-flash"], "description": "모델(기본 gemini-3.5-flash-lite)"},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gemini_usage",
        "description": "제미나이 사용량(홈즈가 센 최근 31일 추정액·남은 크레딧·더 만들 수 있는 장수·최근 기록)을 읽는다. 바깥 호출·비용 없음. 구글의 실제 잔액은 키로 읽을 수 없다.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def make_link():
    """실제 연결: 앱과 같은 설정·DB·Windows 자격증명 저장소."""

    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.core.windows_credential_store import WindowsCredentialStore
    from app.database.session import SessionLocal
    from tools.claude_gemini_link.link import GeminiLink

    return GeminiLink(SessionLocal, WindowsCredentialStore, user_id=int(os.environ.get("HOMEZ_LINK_USER_ID", "1")))


def call_tool(link, name: str, args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    if name == "gemini_list_photos":
        return link.list_photos(args.get("candidate_id"))
    if name == "gemini_make_photo":
        if not isinstance(args.get("reference_asset_id"), int) or isinstance(args.get("reference_asset_id"), bool):
            raise ValueError("reference_asset_id(참고 사진 번호)가 필요합니다.")
        return link.make_photo(args["reference_asset_id"], purpose=args.get("purpose") or "ADDITIONAL", backgrounds=args.get("backgrounds"),
                               poses=args.get("poses"), count=args.get("count") or 1, instruction=args.get("instruction"),
                               confirmed_main=args.get("confirmed_main") is True)
    if name == "gemini_ask":
        if not isinstance(args.get("question"), str):
            raise ValueError("question(질문 글)이 필요합니다.")
        return link.ask(args["question"], instruction=args.get("instruction"), model=args.get("model"))
    if name == "gemini_usage":
        return link.usage()
    raise KeyError(name)


class Server:

    def __init__(self, link_factory=make_link):
        self._link_factory = link_factory
        self._link = None

    def link(self):
        if self._link is None:
            self._link = self._link_factory()
        return self._link

    def handle(self, message: dict) -> dict | None:
        """메시지 하나 → 답 하나(알림이면 None)."""

        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                    "error": {"code": -32600, "message": "잘못된 요청"}}
        method, msg_id = message["method"], message.get("id")
        if msg_id is None:            # 알림(initialized 등)은 답하지 않는다
            return None
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "initialize":
            version = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else PROTOCOL_VERSION
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                                                                "serverInfo": SERVER_INFO}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            name = params.get("name")
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": f"모르는 도구: {name}"}}
            try:
                result = call_tool(self.link(), name, params.get("arguments") or {})
                body, is_error = json.dumps(result, ensure_ascii=False, indent=1), False
            except Exception as exc:  # noqa: BLE001 — 도구 실패는 규약 오류가 아니라 도구 결과로 돌려준다
                traceback.print_exc(file=sys.stderr)
                body, is_error = f"실패: {type(exc).__name__}: {str(getattr(exc, 'detail', None) or exc)[:500]}", True
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": body}], "isError": is_error}}
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"모르는 요청: {method}"}}


def serve(stdin=None, stdout=None, server: Server | None = None) -> None:
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    if stdout is None:
        sys.stdout = sys.stderr       # 앱 코드의 출력이 규약 통로를 더럽히지 않게
    server = server or Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "해석할 수 없는 메시지"}}
        else:
            reply = server.handle(message)
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    _protocol_out = sys.stdout
    sys.stdout = sys.stderr           # 표준 출력은 규약 전용 — 다른 출력은 표준 오류로
    serve(stdout=_protocol_out)
