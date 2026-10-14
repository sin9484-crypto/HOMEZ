"""
=========================================================
Homez OS

File : app/domains/product_import/html_tree.py

URL 상품 가져오기(2026-10-14) — 신뢰할 수 없는 외부 HTML을 읽기 위한 최소 트리.

표준 라이브러리 html.parser만 쓴다. HTML을 실행하지 않고, 스크립트·스타일 내용은 (JSON-LD를 제외하고)
버린다. 닫히지 않은 태그·잘못된 중첩에도 예외 없이 끝까지 읽는다. 네트워크 접근 없음 — 순수 함수만 있다.
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from html.parser import HTMLParser

_VOID = frozenset({"img", "br", "meta", "link", "input", "hr", "area", "base", "col", "source", "wbr", "embed", "param", "track"})
_SKIP_TEXT = frozenset({"script", "style", "noscript", "template"})
_MAX_NODES = 200_000  # 비정상적으로 큰 문서에서 메모리를 쓰지 않도록 상한을 둔다.


@dataclass(eq=False)
class Node:

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)
    parent: "Node | None" = None

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def iter_nodes(self):
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed([c for c in node.children if isinstance(c, Node)]))

    def text(self) -> str:
        parts: list[str] = []
        stack: list[Node | str] = [self]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
                continue
            if item.tag in _SKIP_TEXT:
                continue
            stack.extend(reversed(item.children))
        return re.sub(r"\s+", " ", " ".join(parts)).strip()


class _TreeBuilder(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self._current = self.root
        self._count = 0

    def handle_starttag(self, tag, attrs):
        self._count += 1
        if self._count > _MAX_NODES:
            return
        node = Node(tag, {k: (v or "") for k, v in attrs}, parent=self._current)
        self._current.children.append(node)
        if tag not in _VOID:
            self._current = node

    def handle_startendtag(self, tag, attrs):
        self._count += 1
        if self._count > _MAX_NODES:
            return
        self._current.children.append(Node(tag, {k: (v or "") for k, v in attrs}, parent=self._current))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        node = self._current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._current = node.parent

    def handle_data(self, data):
        if data and self._count <= _MAX_NODES:
            self._current.children.append(data)


def parse_html(html: str) -> Node:
    """어떤 입력에도 예외 없이 트리를 돌려준다(빈 문자열이면 빈 문서)."""

    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
        builder.close()
    except Exception:  # noqa: BLE001 — 깨진 HTML은 읽은 데까지만 쓴다.
        pass
    return builder.root


def label_value_pairs(root: Node) -> list[tuple[str, str]]:
    """표(tr), 정의목록(dt/dd), 온채널식 `div.prod_detail_title + div`에서 (라벨, 값) 쌍을 모은다.
    라벨이 너무 길거나 값이 비어 있는 쌍은 버린다 — 본문 문단을 표로 오인하지 않기 위해서다."""

    pairs: list[tuple[str, str]] = []

    def add(label: str, value: str) -> None:
        label = label.strip(" :：")
        value = value.strip()
        if label and value and len(label) <= 40 and len(value) <= 600:
            pairs.append((label, value))

    for node in root.iter_nodes():
        if node.tag == "tr":
            cells = [c for c in node.children if isinstance(c, Node) and c.tag in ("td", "th")]
            if len(cells) >= 2:
                add(cells[0].text(), " ".join(c.text() for c in cells[1:]))
        elif node.tag == "dt":
            siblings = node.parent.children if node.parent else []
            index = next((i for i, s in enumerate(siblings) if s is node), -1)
            following = [s for s in siblings[index + 1:] if isinstance(s, Node)]
            if following and following[0].tag == "dd":
                add(node.text(), following[0].text())
        elif node.tag == "div" and "prod_detail_title" in node.classes():
            siblings = node.parent.children if node.parent else []
            index = next((i for i, s in enumerate(siblings) if s is node), -1)
            following = [s for s in siblings[index + 1:] if isinstance(s, Node)]
            if following and following[0].tag == "div":
                add(node.text(), following[0].text())
    return pairs
