#!/usr/bin/env python3
"""Fail when the a290 and r5 add-on trees differ in any way the expected list does not explain.

Shared verbatim by a290-ha-addon and r5-ha-addon (ADR 0001 in a290), so it knows neither repo
by name. Everything model-specific is data under scripts/parity/ in the repo that runs it:

  map.tsv       how the derived tree's names are rewritten onto the canonical tree's, and
                which files are compared raw, skipped, or compared with whitespace collapsed
  expected.tsv  every difference allowed to remain, each with a category, a count and a reason

Roles come from map.tsv's `side` rows, not from which repo runs the check: whichever tree
holds the canonical marker file is canonical. So both repos run the same command:

  python3 scripts/parity_check.py --twin <path to the other repo>

What it fails on, each of which is a separate check:
  - a differing line, binary or one-sided file that no expected entry covers;
  - an expected entry that covers nothing: the difference it excused is gone (stale);
  - an entry whose count is not exactly the number of items it covered, so a new line
    cannot hide under an old excuse and a partly-fixed one is noticed;
  - a malformed map or list (unknown category, pending without a Target:, bad regex).

Each repo reads its OWN copy of map.tsv and expected.tsv, and those two files are never
compared across the pair: a list edit in one repo must not turn the other repo red. The two
copies converge anyway, because each must describe exactly the same set of differences.

Exit: 0 pass, 1 parity failure, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

CATEGORIES = {
    "model": "a genuine per-car difference (endpoint behaviour, renders, branding)",
    "legacy": "the derived tree keeps older names or wording for its users' sake",
    "local": "belongs to one repository by nature (its own backlog, ADRs, tooling)",
    "noise": "versions, release history, binaries: expected to differ forever",
    "pending": "drift or a port not yet made; must name a Target: and is expected to be removed",
}
MAP_KINDS = {"side", "define", "path", "legacy", "identity", "verbatim", "skip", "collapse-ws", "ignore-blank"}
DEFAULT_EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- configuration


@dataclass
class Rule:
    kind: str
    scope: str
    pattern: str
    replacement: str
    why: str
    lineno: int
    regex: re.Pattern | None = None
    hits: int = 0


@dataclass
class ParityMap:
    canonical_marker: str = ""
    derived_marker: str = ""
    canonical_label: str = ""
    derived_label: str = ""
    path_rules: list = field(default_factory=list)
    content_rules: list = field(default_factory=list)
    verbatim: list = field(default_factory=list)
    skip: list = field(default_factory=list)
    collapse_ws: list = field(default_factory=list)
    ignore_blank: list = field(default_factory=list)


def _rows(path):
    """(lineno, columns) for every non-blank, non-comment row of a TSV file."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as err:
        raise ConfigError(f"{path}: {err}") from err
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        yield n, line.split("\t")


def _scope_matches(scope, rel):
    """`*` = every file; `a,b` = only these globs; `!a,b` = every file except these globs."""
    if scope in ("*", ""):
        return True
    negate = scope.startswith("!")
    globs = [g for g in scope.lstrip("!").split(",") if g]
    hit = any(fnmatch.fnmatchcase(rel, g) for g in globs)
    return not hit if negate else hit


def load_map(path):
    pm = ParityMap()
    defines = {}
    for n, cols in _rows(path):
        if len(cols) != 5:
            raise ConfigError(f"{path}:{n}: expected 5 tab-separated columns, got {len(cols)}")
        kind, scope, pattern, replacement, why = cols
        if kind not in MAP_KINDS:
            raise ConfigError(f"{path}:{n}: unknown kind {kind!r}")
        if not why.strip():
            raise ConfigError(f"{path}:{n}: every rule states why it exists")
        if kind == "define":
            defines[pattern] = replacement
            continue
        for name, value in defines.items():
            pattern = pattern.replace(f"<{name}>", value)
        rule = Rule(kind, scope, pattern, replacement, why, n)
        if kind == "side":
            if scope == "canonical":
                pm.canonical_marker, pm.canonical_label = pattern, replacement
            elif scope == "derived":
                pm.derived_marker, pm.derived_label = pattern, replacement
            else:
                raise ConfigError(f"{path}:{n}: side scope must be canonical or derived")
        elif kind in ("path", "legacy", "identity"):
            try:
                rule.regex = re.compile(pattern)
            except re.error as err:
                raise ConfigError(f"{path}:{n}: bad regex {pattern!r}: {err}") from err
            (pm.path_rules if kind == "path" else pm.content_rules).append(rule)
        else:
            {"verbatim": pm.verbatim, "skip": pm.skip, "collapse-ws": pm.collapse_ws,
             "ignore-blank": pm.ignore_blank}[kind].append(rule)
    if not (pm.canonical_marker and pm.derived_marker):
        raise ConfigError(f"{path}: needs one `side canonical` and one `side derived` row")
    if pm.canonical_label == pm.derived_label or "both" in (pm.canonical_label, pm.derived_label):
        raise ConfigError(f"{path}: side labels must differ and must not be 'both'")
    return pm


@dataclass
class Entry:
    category: str
    path: str
    side: str
    match: str
    count: int | None  # None = any number (only allowed for a whole-file `*` match)
    reason: str
    lineno: int
    regex: re.Pattern | None = None
    hits: list = field(default_factory=list)

    def label(self):
        count = "*" if self.count is None else self.count
        return f"expected.tsv:{self.lineno} [{self.category}] {self.path} {self.side} {self.match} {count}"


def load_expected(path, pm):
    entries = []
    sides = {pm.canonical_label, pm.derived_label, "both"}
    for n, cols in _rows(path):
        if len(cols) != 6:
            raise ConfigError(f"{path}:{n}: expected 6 tab-separated columns, got {len(cols)}")
        category, glob, side, match, count, reason = cols
        where = f"{path}:{n}"
        if category not in CATEGORIES:
            raise ConfigError(f"{where}: unknown category {category!r} (one of {', '.join(CATEGORIES)})")
        if side not in sides:
            raise ConfigError(f"{where}: side must be one of {', '.join(sorted(sides))}")
        if len(reason.strip()) < 15:
            raise ConfigError(f"{where}: the reason must say why, in words")
        if category == "pending" and "Target:" not in reason:
            raise ConfigError(f"{where}: a pending entry names its Target: (where and how it is removed)")
        entry = Entry(category, glob, side, match, None, reason, n)
        if count == "*":
            if match != "*":
                raise ConfigError(f"{where}: count * is allowed only for a whole-file `*` match")
        else:
            try:
                entry.count = int(count)
            except ValueError as err:
                raise ConfigError(f"{where}: count must be a positive integer or *") from err
            if entry.count < 1:
                raise ConfigError(f"{where}: count must be at least 1")
        if match.startswith(("re:", "hunk:")):
            try:
                entry.regex = re.compile(match.split(":", 1)[1])
            except re.error as err:
                raise ConfigError(f"{where}: bad regex: {err}") from err
        elif not (match in ("*", "only") or match.startswith("py:")):
            raise ConfigError(f"{where}: match must be *, only, re:<regex>, hunk:<regex> or py:<symbols>")
        entries.append(entry)
    return entries


# ---------------------------------------------------------------- trees


def list_files(root):
    """Tracked files when `root` is a git checkout (so local junk never counts), else a walk."""
    if os.path.exists(os.path.join(root, ".git")):
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                             check=True, capture_output=True).stdout.decode("utf-8")
        return sorted(p for p in out.split("\0") if p and os.path.isfile(os.path.join(root, p)))
    files = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_EXCLUDED_DIRS]
        for name in names:
            files.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return sorted(files)


def read_bytes(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read()


def as_text(raw):
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def normalise_path(rel, pm):
    for rule in pm.path_rules:
        rel, n = rule.regex.subn(rule.replacement, rel)
        rule.hits += n
    return rel


def normalise_text(text, rel, pm):
    """Apply the content rules in file order. Replacements never add or remove a newline, so a
    normalised line number is the derived file's own line number."""
    for rule in pm.content_rules:
        if _scope_matches(rule.scope, rel):
            text, n = rule.regex.subn(rule.replacement, text)
            rule.hits += n
    return text


def py_symbols(text):
    """Line number -> top-level symbol that owns it. Comments and blank lines belong to the
    statement that follows them, which is where a comment about that statement sits."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    spans = []  # (last line, symbol); decorators precede their def, so they need no special case
    for i, node in enumerate(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            name = "<imports>"
        elif i == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            name = "<docstring>"
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            name = "<main-guard>"
        else:
            name = f"<statement@{node.lineno}>"
        spans.append((node.end_lineno, name))
    owner = {}
    prev_end = 0
    for end, name in spans:
        for ln in range(prev_end + 1, end + 1):
            owner[ln] = name
        prev_end = end
    for ln in range(prev_end + 1, len(text.splitlines()) + 2):
        owner[ln] = "<eof>"
    return owner


# ---------------------------------------------------------------- comparison


@dataclass
class Item:
    path: str          # canonical-named path
    side: str          # a side label, or "both" for a binary difference
    kind: str          # line | only | binary
    lineno: int = 0
    text: str = ""
    symbol: str | None = None
    source: str = ""   # the file this item came from, as that repo names it
    hunk: tuple = ()   # every line of the contiguous run this line differs in, on its side

    def where(self):
        loc = f"{self.source}:{self.lineno}" if self.lineno else self.source
        return f"{loc} ({self.side})"


def _collapse(line):
    indent = len(line) - len(line.lstrip())
    return line[:indent] + re.sub(r"\s+", " ", line[indent:]).rstrip()


def compare(canonical, derived, pm):
    items = []
    can_files = {rel: rel for rel in list_files(canonical)}
    der_files = {}
    for rel in list_files(derived):
        der_files[normalise_path(rel, pm)] = rel

    def skipped(rel):
        return any(_scope_matches(r.scope, rel) for r in pm.skip)

    for rel in sorted(set(can_files) | set(der_files)):
        if skipped(rel):
            continue
        in_c, in_d = rel in can_files, rel in der_files
        if in_c != in_d:
            label = pm.canonical_label if in_c else pm.derived_label
            src = rel if in_c else der_files[rel]
            items.append(Item(rel, label, "only", text=f"<only in {label}>", source=src))
            continue
        raw_c, raw_d = read_bytes(canonical, rel), read_bytes(derived, der_files[rel])
        if raw_c == raw_d:
            continue
        text_c, text_d = as_text(raw_c), as_text(raw_d)
        if text_c is None or text_d is None:
            items.append(Item(rel, "both", "binary", text="<binary content differs>", source=rel))
            continue
        verbatim = any(_scope_matches(r.scope, rel) for r in pm.verbatim)
        if not verbatim:
            text_d = normalise_text(text_d, rel, pm)
        lines_c, lines_d = text_c.splitlines(), text_d.splitlines()
        cmp_c, cmp_d = lines_c, lines_d
        if any(_scope_matches(r.scope, rel) for r in pm.collapse_ws):
            cmp_c, cmp_d = [_collapse(x) for x in lines_c], [_collapse(x) for x in lines_d]
        ignore_blank = any(_scope_matches(r.scope, rel) for r in pm.ignore_blank)
        sym_c = py_symbols(text_c) if rel.endswith(".py") else {}
        sym_d = py_symbols(text_d) if rel.endswith(".py") else {}
        matcher = difflib.SequenceMatcher(None, cmp_c, cmp_d, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                continue
            hunk = tuple(lines_c[i1:i2]) + tuple(lines_d[j1:j2])
            for i in range(i1, i2):
                if not (ignore_blank and not lines_c[i].strip()):
                    items.append(Item(rel, pm.canonical_label, "line", i + 1, lines_c[i], sym_c.get(i + 1), rel,
                                      hunk))
            for j in range(j1, j2):
                if not (ignore_blank and not lines_d[j].strip()):
                    items.append(Item(rel, pm.derived_label, "line", j + 1, lines_d[j], sym_d.get(j + 1),
                                      der_files[rel], hunk))
    return items


def _entry_takes(entry, item):
    if not fnmatch.fnmatchcase(item.path, entry.path):
        return False
    if item.kind == "binary":
        return entry.side == "both" and entry.match == "*"
    if entry.side != "both" and entry.side != item.side:
        return False
    if entry.match == "only":
        return item.kind == "only"
    if item.kind == "only":
        return False
    if entry.match == "*":
        return True
    if entry.match.startswith("py:"):
        return item.symbol in entry.match[3:].split(",")
    if entry.match.startswith("hunk:"):
        # The whole changed block (both sides of one diff opcode) that contains a matching line.
        return any(entry.regex.search(line) for line in item.hunk)
    return bool(entry.regex.search(item.text))


def assign(items, entries):
    unlisted = []
    for item in items:
        for entry in entries:
            if _entry_takes(entry, item):
                entry.hits.append(item)
                break
        else:
            unlisted.append(item)
    return unlisted


# ---------------------------------------------------------------- reporting


def _clip(text, width=150):
    text = text.rstrip()
    return text if len(text) <= width else text[: width - 3] + "..."


def report(items, entries, unlisted, show_all, pm):
    stale = [e for e in entries if not e.hits]
    miscounted = [e for e in entries if e.hits and e.count is not None and e.count != len(e.hits)]
    # A rewrite rule that rewrote nothing is dead weight that would hide the name if it came back.
    idle_rules = [r for r in pm.path_rules + pm.content_rules if not r.hits]
    ok = not (unlisted or stale or miscounted or idle_rules)

    for rule in idle_rules:
        print(f"STALE     map.tsv:{rule.lineno} [{rule.kind}] {rule.pattern} rewrote nothing; the derived tree "
              f"no longer uses that name, so delete the rule. Why was: {rule.why}")
    for item in unlisted:
        print(f"UNLISTED  {item.where()} [{item.symbol or '-'}]: {_clip(item.text)}")
    for e in stale:
        print(f"STALE     {e.label()} covers nothing now; the difference it excused is gone, so delete "
              f"the entry. Reason was: {e.reason}")
    for e in miscounted:
        print(f"COUNT     {e.label()} lists {e.count}, covers {len(e.hits)}:")
        for item in e.hits[:40]:
            print(f"            {item.where()}: {_clip(item.text, 120)}")
        if len(e.hits) > 40:
            print(f"            ... {len(e.hits) - 40} more")
    if show_all:
        for e in entries:
            print(f"ENTRY     {e.label()} covers {len(e.hits)}")

    by_cat = {}
    for e in entries:
        c = by_cat.setdefault(e.category, [0, 0])
        c[0] += 1
        c[1] += len(e.hits)
    unguarded = sorted({e.path for e in entries if e.count is None})
    print()
    print(f"parity: {len(items)} differing items, {len(entries)} expected entries, "
          f"{len(unlisted)} unlisted, {len(stale)} stale, {len(miscounted)} miscounted, "
          f"{len(idle_rules)} stale map rules")
    for cat in CATEGORIES:
        if cat in by_cat:
            print(f"  {cat:8s} {by_cat[cat][0]:4d} entries covering {by_cat[cat][1]:5d} items")
    if unguarded:
        print(f"  excused wholesale (count *), so drift inside them is NOT caught: {', '.join(unguarded)}")
    print("parity: PASS" if ok else "parity: FAIL")
    return ok


def suggest(unlisted, pm):
    """Draft rows for unlisted items, grouped. A draft, never a verdict: each needs a real reason."""
    groups = {}
    for item in unlisted:
        if item.kind == "only":
            key = (item.path, item.side, "only")
        elif item.kind == "binary":
            key = (item.path, "both", "*")
        elif item.symbol:
            key = (item.path, "both", f"py:{item.symbol}")
        else:
            key = (item.path, item.side, "re:" + re.escape(item.text.strip())[:80])
        groups.setdefault(key, []).append(item)
    print("\n# draft rows (category and reason are yours to write):")
    for (path, side, match), hits in groups.items():
        print(f"pending\t{path}\t{side}\t{match}\t{len(hits)}\tTODO why. Target: TODO")


# ---------------------------------------------------------------- entry point


def detect_roles(self_root, twin_root, pm):
    has = {r: os.path.isfile(os.path.join(r, pm.canonical_marker)) for r in (self_root, twin_root)}
    der = {r: os.path.isfile(os.path.join(r, pm.derived_marker)) for r in (self_root, twin_root)}
    if has[self_root] and der[twin_root] and not has[twin_root]:
        return self_root, twin_root
    if has[twin_root] and der[self_root] and not has[self_root]:
        return twin_root, self_root
    raise ConfigError(f"cannot tell which tree is canonical: expected exactly one to hold "
                      f"{pm.canonical_marker} and the other {pm.derived_marker}")


def run(self_root, twin_root, config_dir, show_all=False, draft=False):
    pm = load_map(os.path.join(config_dir, "map.tsv"))
    entries = load_expected(os.path.join(config_dir, "expected.tsv"), pm)
    canonical, derived = detect_roles(self_root, twin_root, pm)
    print(f"parity: canonical {pm.canonical_label} = {canonical}")
    print(f"parity: derived   {pm.derived_label} = {derived} (normalised onto {pm.canonical_label} names)")
    items = compare(canonical, derived, pm)
    unlisted = assign(items, entries)
    ok = report(items, entries, unlisted, show_all, pm)
    if draft and unlisted:
        suggest(unlisted, pm)
    return ok


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def self_test():
    """Prove each failure mode can fire. A check that cannot fail is not a check."""
    import contextlib
    import io

    cmap = "\n".join([
        "side\tcanonical\tcar_a/config.yaml\taa\tcanonical tree marker",
        "side\tderived\tcar_b/config.yaml\tbb\tderived tree marker",
        "path\t*\t^car_b/\tcar_a/\tadd-on directory",
        "identity\t*\t\\bB_\tA_\tenv prefix",
        "verbatim\tshared.py\t-\t-\tmust be byte-identical",
        "ignore-blank\t*\t-\t-\tblank lines carry no meaning",
    ]) + "\n"
    base = {
        "config.yaml": "name: x\n",
        "app.py": "import os\n\nKEEP = 1\n\n\ndef f():\n    return os.environ['{P}X']\n",
        "shared.py": "# mentions B_ and A_ alike\n",
    }
    listed = ("model\tcar_a/config.yaml\tboth\tre:^version\t2\tversion strings differ per add-on release\n")

    def build(tmp, mutate=None, expected=listed, extra_only=False, extra_map=""):
        a, b, cfg = (os.path.join(tmp, d) for d in ("a", "b", "cfg"))
        for root, car, prefix in ((a, "car_a", "A_"), (b, "car_b", "B_")):
            for rel, text in base.items():
                where = rel if rel == "shared.py" else f"{car}/{rel}"
                _write(root, where, text.replace("{P}", prefix))
            _write(root, f"{car}/config.yaml", f"name: x\nversion: {'1' if root == a else '2'}\n")
        if mutate:
            mutate(a, b)
        if extra_only:
            _write(b, "car_b/only.txt", "x\n")
        _write(cfg, "map.tsv", cmap + extra_map)
        _write(cfg, "expected.tsv", expected)
        return a, b, cfg

    def outcome(a, b, cfg, swap=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = run(b, a, cfg) if swap else run(a, b, cfg)
        return ok, buf.getvalue()

    cases = [
        ("identical trees apart from a listed difference pass", {}, True, None),
        ("the same trees pass when run from the derived side", {"swap": True}, True, None),
        ("an unlisted changed line fails, naming file and line",
         {"mutate": lambda a, b: _write(b, "car_b/app.py", base["app.py"].replace("{P}", "B_")
                                        .replace("KEEP = 1", "KEEP = 2"))}, False, "car_b/app.py:3"),
        ("an entry whose difference is gone fails as stale",
         {"expected": listed + "pending\tcar_a/app.py\tboth\tpy:f\t2\tdrift that was fixed. Target: gone\n"},
         False, "STALE"),
        ("a count that no longer matches fails",
         {"expected": listed.replace("\t2\t", "\t1\t")}, False, "COUNT"),
        ("a verbatim file differing only by a normalised token still fails",
         {"mutate": lambda a, b: _write(a, "shared.py", "# mentions A_ and A_ alike\n")}, False, "shared.py:1"),
        ("a file present on one side only fails until listed", {"extra_only": True}, False, "<only in bb>"),
        ("a map rule that rewrites nothing fails as stale",
         {"extra_map": "identity\t*\t\\bC_\tA_\ta prefix the derived tree no longer uses\n"}, False, "map.tsv:7"),
        ("a pending entry without a Target: is refused", {"expected": listed.replace(
            "model", "pending")}, None, "Target:"),
    ]
    failures = 0
    for name, kw, want, needle in cases:
        swap = kw.pop("swap", False)
        with tempfile.TemporaryDirectory() as tmp:
            a, b, cfg = build(tmp, **kw)
            try:
                ok, out = outcome(a, b, cfg, swap)
            except ConfigError as err:
                ok, out = None, str(err)
        good = ok is want and (needle is None or needle in out)
        failures += not good
        print(f"self-test {'ok  ' if good else 'FAIL'} {name}")
        if not good:
            print("  " + out.replace("\n", "\n  "))
    print("self-test: PASS" if not failures else f"self-test: {failures} FAILED")
    return failures == 0


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self", dest="self_root", default=os.path.dirname(here),
                    help="this repository's root (default: the parent of scripts/)")
    ap.add_argument("--twin", help="the other add-on repository's root")
    ap.add_argument("--config", help="directory holding map.tsv and expected.tsv "
                                     "(default: <self>/scripts/parity)")
    ap.add_argument("--show-all", action="store_true", help="also print every entry and what it covers")
    ap.add_argument("--draft", action="store_true", help="print draft rows for unlisted differences")
    ap.add_argument("--self-test", action="store_true", help="prove the check can fail, then exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return 0 if self_test() else 1
    if not args.twin:
        ap.error("--twin is required")
    self_root, twin_root = os.path.abspath(args.self_root), os.path.abspath(args.twin)
    config = args.config or os.path.join(self_root, "scripts", "parity")
    try:
        return 0 if run(self_root, twin_root, config, args.show_all, args.draft) else 1
    except ConfigError as err:
        print(f"parity: configuration error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
