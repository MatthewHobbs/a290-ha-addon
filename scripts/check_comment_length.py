"""Fail on a code comment block longer than 50 words.

Code comments only: Python and YAML ``#`` runs, JavaScript ``//`` runs and
``/* ... */`` blocks. Docstrings are documentation, not comments, and are not counted.

Usage: python scripts/check_comment_length.py [PATH ...]
"""

from __future__ import annotations

import pathlib
import re
import sys

MAX_WORDS = 50
SKIP = {
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "build",
    "dist",
    ".semgrep",
}
SUFFIXES = {".py": "#", ".js": "//", ".yml": "#", ".yaml": "#"}


def line_blocks(text: str, marker: str) -> list[tuple[int, str]]:
    """Consecutive comment lines, as (first line number, text)."""
    out, start, words = [], None, []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(marker):
            if start is None:
                start = number
            words.append(stripped[len(marker) :].strip())
        else:
            if start is not None:
                out.append((start, " ".join(words)))
                start, words = None, []
    if start is not None:
        out.append((start, " ".join(words)))
    return out


def block_comments(text: str) -> list[tuple[int, str]]:
    """`/* ... */` blocks, with the leading `*` decoration removed."""
    out = []
    for match in re.finditer(r"/\*.*?\*/", text, re.S):
        body = re.sub(r"^\s*/\*+|\*+/\s*$", "", match.group(0), flags=re.S)
        body = re.sub(r"^\s*\*+", "", body, flags=re.M)
        out.append((text.count("\n", 0, match.start()) + 1, body))
    return out


def comments(path: pathlib.Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    found = line_blocks(text, SUFFIXES[path.suffix])
    if path.suffix == ".js":
        found += block_comments(text)
    return found


def main(paths: list[str]) -> int:
    failures = 0
    for root in map(pathlib.Path, paths or ["."]):
        for path in sorted(root.rglob("*")):
            if path.suffix not in SUFFIXES or SKIP & set(path.parts) or ".min." in path.name:
                continue
            for line, text in comments(path):
                words = len(text.split())
                if words > MAX_WORDS:
                    print(f"{path}:{line}: {words} words")
                    failures += 1
    if failures:
        print(f"{failures} comment block(s) over {MAX_WORDS} words")
        return 1
    print(f"OK no comment block over {MAX_WORDS} words")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
