#!/bin/sh
# 시안 HTML을 PNG로 만든다(헤드리스 Edge, 외부 네트워크 없음). 사용: sh render.sh 00_menu 1440 900
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
DIR="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
NAME="$1"; W="${2:-1440}"; H="${3:-900}"
"$EDGE" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 --window-size="$W,$H" --screenshot="$DIR/$NAME.png" "file:///$DIR/$NAME.html" >/dev/null 2>&1
ls -la "$DIR/$NAME.png"
