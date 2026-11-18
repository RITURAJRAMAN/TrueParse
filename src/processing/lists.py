from __future__ import annotations

import re

from trueparse.contracts.document import ListItem

_MARKER = re.compile(r"^(\s*)(\d+[.)]|[-*•●▪‣⁃·])\s+(.*)$")


def parse_list(content: str) -> list[ListItem]:
    root: list[ListItem] = []
    stack: list[tuple[int, list[ListItem]]] = [(-1, root)]
    for line in content.splitlines():
        if not line.strip():
            continue
        match = _MARKER.match(line)
        if not match:
            if stack[-1][1]:
                stack[-1][1][-1].text += "\n" + line.strip()
            else:
                root.append(ListItem(text=line.strip()))
            continue
        indent = len(match[1].expandtabs(4))
        if len(stack) == 1 and indent < stack[0][0]:
            stack[0] = (indent, root)
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        if indent > stack[-1][0]:
            items = stack[-1][1]
            if items:
                stack.append((indent, items[-1].children))
            else:
                stack[-1] = (indent, items)
        stack[-1][1].append(ListItem(text=match[3], marker=match[2]))
    return root


def ordered(items: list[ListItem]) -> bool:
    return bool(items) and all(item.marker and item.marker[0].isdigit() for item in items)
