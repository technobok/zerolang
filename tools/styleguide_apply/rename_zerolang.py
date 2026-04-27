#!/usr/bin/env python3
"""
Mechanical renamer for the Zerolang style guide apply phase.

Strategy:
  Stage 1 — get-prefix drops, mapped to sentinels so later type renames
            don't capitalize them.
  Stage 2 — all underscore→camelCase function/method/field/constant renames.
  Stage 3 — all type lowercase→PascalCase renames, longest-first to avoid
            substring collision (stringview before string, optionview
            before option, listview before list, etc.).
  Stage 4 — restore sentinels to their final lowercase form.

Word boundaries (\\b) guard against partial matches.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Stage 1: get-prefix drops via sentinel.
# ---------------------------------------------------------------------------
# Pattern: drop the literal `get_` prefix on getter-style functions.
# These get sentinelised so subsequent stages don't touch them.
GET_DROPS = {
    "get_env":        "___ZRN_ENV___",
    "get_option":     "___ZRN_OPTION___",
    "get_positional": "___ZRN_POSITIONAL___",
    "get_value":      "___ZRN_VALUE___",
}

SENTINEL_RESTORE = {
    "___ZRN_ENV___":         "env",
    "___ZRN_OPTION___":      "option",
    "___ZRN_POSITIONAL___":  "positional",
    "___ZRN_VALUE___":       "value",
}


# ---------------------------------------------------------------------------
# Stage 2: snake_case → camelCase for functions, fields, constants.
# ---------------------------------------------------------------------------
# Order matters within each group only when one is a prefix of another;
# we sort by descending length below.
SNAKE_TO_CAMEL = {
    # collections / system
    "string_join":      "stringJoin",
    "extend_view":      "extendView",
    "iterate_items":    "iterateItems",
    # io
    "read_text":        "readText",
    "write_text":       "writeText",
    "append_text":      "appendText",
    "read_line":        "readLine",
    "write_line":       "writeLine",
    "list_dir":         "listDir",
    # os
    "set_env":          "setEnv",
    "unset_env":        "unsetEnv",
    "env_names":        "envNames",
    "set_cwd":          "setCwd",
    "user_name":        "userName",
    "home_dir":         "homeDir",
    # system string predicates / ops
    "is_empty":         "isEmpty",
    "is_ascii":         "isAscii",
    "starts_with":      "startsWith",
    "ends_with":        "endsWith",
    "index_of":         "indexOf",
    "last_index_of":    "lastIndexOf",
    "byte_at":          "byteAt",
    "trim_start":       "trimStart",
    "trim_end":         "trimEnd",
    "strip_prefix":     "stripPrefix",
    "strip_suffix":     "stripSuffix",
    "split_once":       "splitOnce",
    "to_lower_ascii":   "toLowerAscii",
    "to_upper_ascii":   "toUpperAscii",
    "replace_first":    "replaceFirst",
    "parse_i64":        "parseI64",
    "parse_u64":        "parseU64",
    "parse_f64":        "parseF64",
    # cli
    "has_flag":         "hasFlag",
    "add_flag":         "addFlag",
    "add_option":       "addOption",
    "add_positional":   "addPositional",
    "help_text":        "helpText",
    # cli fields/constants
    "short_name":           "shortName",
    "unknown_flag":         "unknownFlag",
    "missing_value":        "missingValue",
    "missing_required":     "missingRequired",
    "unexpected_positional": "unexpectedPositional",
    "unexpected_arg":       "unexpectedArg",
    "program_name":         "programName",
    "flag_set":             "flagSet",
    "option_values":        "optionValues",
    "positional_values":    "positionalValues",
    "extra_args":           "extraArgs",
    # io fields
    "mtime_seconds":    "mtimeSeconds",
    "atime_seconds":    "atimeSeconds",
    "ctime_seconds":    "ctimeSeconds",
    # system constants
    "invalid_digit":    "invalidDigit",
}


# ---------------------------------------------------------------------------
# Stage 3: lowercase reftypes → PascalCase.
# ---------------------------------------------------------------------------
# CRITICAL: longest first to avoid substring collisions.
REFTYPES = {
    # multi-syllable / compound — longest first within group
    "positionaldef":  "PositionalDef",
    "mapitemiter":    "MapItemIter",
    "mapkeyiter":     "MapKeyIter",
    "stringview":     "StringView",
    "stringlike":     "StringLike",
    "optionview":     "OptionView",
    "linesiter":      "LinesIter",
    "textwriter":     "TextWriter",
    "textreader":     "TextReader",
    "bufwriter":      "BufWriter",
    "bufreader":      "BufReader",
    "mapentry":       "MapEntry",
    "listview":       "ListView",
    "listiter":       "ListIter",
    "byteview":       "ByteView",
    "pathview":       "PathView",
    "flagdef":        "FlagDef",
    "optiondef":      "OptionDef",
    "clierror":       "CliError",
    "ioerror":        "IoError",
    "splitter":       "Splitter",
    "cpiter":         "CpIter",
    # short / single-word lowercase reftypes (predefined included)
    "stringjoin":     "stringJoin",  # safety: no-op realistically
    "string":         "String",
    "result":         "Result",
    "option":         "Option",
    "bytes":          "Bytes",
    "list":           "List",
    "map":            "Map",
    "any":            "Any",
    "text":           "Text",
    "path":           "Path",
    "file":           "File",
    "box":            "Box",
    "spec":           "Spec",
    "parsed":         "Parsed",
    "reader":         "Reader",
    "writer":         "Writer",
    "closer":         "Closer",
    "seeker":         "Seeker",
}


# ---------------------------------------------------------------------------
# Substitution helper.
# ---------------------------------------------------------------------------

def _split_code_comment(line: str) -> tuple[str, str]:
    """Return (code_part, comment_part) for a Zerolang source line.

    The comment portion includes the leading `#` and everything after it.
    `#` characters inside double-quoted strings are not treated as comment
    starts (rough heuristic; ignores triple quotes and escapes since
    substitution targets are identifiers, not pathological string content).
    """
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i], line[i:]
    return line, ""


def _zerolang_string_spans(code: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of `"..."` string literals in a Zerolang
    source line. Used to mask out string contents from substitution.
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(code)
    while i < n:
        if code[i] == '"':
            start = i
            i += 1
            while i < n:
                if code[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if code[i] == '"':
                    i += 1
                    spans.append((start, i))
                    break
                i += 1
            else:
                spans.append((start, n))
        else:
            i += 1
    return spans


def _apply_in_non_string_regions(
    code: str, fn: callable
) -> str:
    """Run `fn(text) -> new_text` on code, masking out string-literal regions
    so substitution doesn't touch their contents."""
    spans = _zerolang_string_spans(code)
    if not spans:
        return fn(code)
    out = []
    cursor = 0
    for s, e in spans:
        out.append(fn(code[cursor:s]))
        out.append(code[s:e])
        cursor = e
    out.append(fn(code[cursor:]))
    return "".join(out)


_DEF_LHS_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(:\s*)(.*)$")


def _is_top_level_type_def(rest: str) -> bool:
    """Detect `class` / `union` / `protocol` immediately on the RHS."""
    rest = rest.lstrip()
    return rest.startswith(("class", "union", "protocol"))


def apply(
    text: str, mapping: dict[str, str], *, protect_lhs_of_colon: bool = False
) -> tuple[str, dict[str, int]]:
    """Apply rename mapping to comment-stripped code.

    If `protect_lhs_of_colon` is True, occurrences of a key immediately
    followed by `:` (a definition LHS — parameter name, field name, method
    name, variant tag, local var) are NOT substituted. Used by the reftype
    PascalCase pass. Otherwise substitution applies to every word-boundary
    occurrence, which is correct for snake→camel rewrites where the same
    identifier serves on both sides (e.g. `short_name: string` → both LHS
    and RHS become camelCase / PascalCase forms appropriately because the
    snake form is unambiguously a field).
    """
    counts: dict[str, int] = {}
    keys = sorted(mapping.keys(), key=len, reverse=True)
    suffix = r"(?!\s*:)" if protect_lhs_of_colon else ""
    # Reftype rename: SOME method names share their return-type name
    # (`x.string`, `x.stringview`, `x.list`, `x.listview`, `x.byteview`).
    # When `<key>` is one of those AND it appears after `.`, it's a
    # method call — keep lowercase. For other dotted accesses (e.g.
    # `cli.spec` → `cli.Spec`) the rename should fire normally.
    METHOD_NAME_REFS = {"string", "stringview", "list", "listview", "byteview"}
    patterns = []
    for k in keys:
        prefix = r"(?<!\.)" if protect_lhs_of_colon and k in METHOD_NAME_REFS else ""
        patterns.append(
            (re.compile(rf"{prefix}\b{re.escape(k)}\b{suffix}"), mapping[k], k)
        )

    def sub_all(s: str) -> str:
        for pat, repl, key in patterns:
            new_s, n = pat.subn(repl, s)
            if n:
                counts[key] = counts.get(key, 0) + n
                s = new_s
        return s

    new_lines = []
    for line in text.splitlines(keepends=True):
        code, comment = _split_code_comment(line)
        code = _apply_in_non_string_regions(code, sub_all)
        new_lines.append(code + comment)
    return "".join(new_lines), counts


def apply_type_def_lhs(text: str, type_map: dict[str, str]) -> tuple[str, dict[str, int]]:
    """Rename top-level type-definition LHS identifiers.

    Targets ONLY lines of the form `name: class ...`, `name: union ...`,
    `name: protocol ...` at column 0 (no indent). This is the type-defining
    site; method/field/tag LHSs are indented.
    """
    counts: dict[str, int] = {}
    new_lines = []
    for line in text.splitlines(keepends=True):
        code, comment = _split_code_comment(line)
        nl = "\n" if code.endswith("\n") else ""
        code_no_nl = code[:-1] if nl else code
        m = _DEF_LHS_RE.match(code_no_nl)
        if not m or m.group(1) != "":
            new_lines.append(code_no_nl + nl + comment)
            continue
        lhs, sep, rest = m.group(2), m.group(3), m.group(4)
        if not _is_top_level_type_def(rest):
            new_lines.append(code_no_nl + nl + comment)
            continue
        if lhs in type_map:
            counts[lhs] = counts.get(lhs, 0) + 1
            lhs = type_map[lhs]
        new_lines.append(f"{lhs}{sep}{rest}{nl}{comment}")
    return "".join(new_lines), counts


def _scan_python_strings(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char-offset pairs of every Python string literal
    in `text`, plus the full content of any single-line non-f-string. Skip
    f-strings entirely because their `{...}` regions contain Python code.

    We use Python's tokenize module for accuracy.
    """
    import io
    import tokenize

    spans: list[tuple[int, int]] = []
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    def offset(row: int, col: int) -> int:
        return line_offsets[row - 1] + col

    try:
        tokens = list(tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline))
    except tokenize.TokenizeError:
        return spans
    for tok in tokens:
        if tok.type != tokenize.STRING:
            continue
        # Skip f-strings (their content is mixed code + data).
        prefix_chars = ""
        for ch in tok.string:
            if ch in ("'", '"'):
                break
            prefix_chars += ch
        if "f" in prefix_chars.lower():
            continue
        start = offset(tok.start[0], tok.start[1])
        end = offset(tok.end[0], tok.end[1])
        spans.append((start, end))
    return spans


def rename_python_file(path: Path, dry_run: bool = False) -> dict[str, int]:
    """Apply zerolang renames inside Python string literals only.

    Bare Python identifiers (locals, imports, builtins) are not touched.
    We scan the file to find every string literal span, then apply
    word-boundary substitution within those spans.
    """
    original = path.read_text()
    text = original
    total: dict[str, int] = {}

    spans = _scan_python_strings(text)
    if not spans:
        return total

    # Combine all rename maps for one pass; longest first.
    combined: dict[str, str] = {}
    combined.update(GET_DROPS)
    combined.update(SNAKE_TO_CAMEL)
    combined.update(REFTYPES)
    keys = sorted(combined.keys(), key=len, reverse=True)
    # Standard pattern: word-boundary on both sides.
    patterns = [(re.compile(rf"\b{re.escape(k)}\b"), combined[k], k) for k in keys]
    # Additional: mangled-name pattern. Reftypes can appear as the prefix
    # of a mangled monomorphization name like `list_i64` or `listview_i64`.
    # Only rename the leading reftype segment; the suffix (numeric/string
    # type names) stays intact.
    for k in sorted(REFTYPES.keys(), key=len, reverse=True):
        patterns.append(
            (re.compile(rf"\b{re.escape(k)}(?=_[a-z])"), REFTYPES[k], k)
        )

    # Apply substitutions span-by-span, in reverse so offsets remain valid.
    parts = []
    last = len(text)
    for start, end in reversed(spans):
        parts.append(text[end:last])
        chunk = text[start:end]
        for pat, repl, key in patterns:
            chunk, n = pat.subn(repl, chunk)
            if n:
                total[key] = total.get(key, 0) + n
        parts.append(chunk)
        last = start
    parts.append(text[:last])
    text = "".join(reversed(parts))

    # Restore sentinels (also inside strings only — apply unconditionally is fine
    # because sentinels are unique tokens that wouldn't collide with anything).
    for s, final in SENTINEL_RESTORE.items():
        pattern = re.compile(rf"\b{re.escape(s)}\b")
        text, n = pattern.subn(final, text)
        if n:
            total[s] = total.get(s, 0) + n

    if text != original and not dry_run:
        path.write_text(text)

    return total


def rename_file(path: Path, dry_run: bool = False) -> dict[str, int]:
    original = path.read_text()
    text = original
    total: dict[str, int] = {}

    # Stage 1: get-prefix drops (sentinelised).
    text, c = apply(text, GET_DROPS)
    for k, n in c.items():
        total[k] = total.get(k, 0) + n

    # Stage 2: snake_case → camelCase for functions/fields/constants.
    text, c = apply(text, SNAKE_TO_CAMEL)
    for k, n in c.items():
        total[k] = total.get(k, 0) + n

    # Stage 3a: reftype rename, protecting any LHS-of-colon occurrences.
    text, c = apply(text, REFTYPES, protect_lhs_of_colon=True)
    for k, n in c.items():
        total[k] = total.get(k, 0) + n

    # Stage 3b: reftype rename on top-level type-def LHS.
    text, c = apply_type_def_lhs(text, REFTYPES)
    for k, n in c.items():
        total[f"(type-def) {k}"] = total.get(f"(type-def) {k}", 0) + n

    # Stage 4: restore sentinels.
    text, c = apply(text, SENTINEL_RESTORE)
    for k, n in c.items():
        total[k] = total.get(k, 0) + n

    if text != original and not dry_run:
        path.write_text(text)

    return total


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: rename_zerolang.py <file>...", file=sys.stderr)
        return 2

    grand: dict[str, int] = {}
    for fn in argv[1:]:
        path = Path(fn)
        if not path.is_file():
            print(f"skip (not a file): {fn}", file=sys.stderr)
            continue
        if path.suffix == ".py":
            counts = rename_python_file(path)
        else:
            counts = rename_file(path)
        if counts:
            print(f"{path}: {sum(counts.values())} substitutions")
            for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                grand[k] = grand.get(k, 0) + n

    if grand:
        print()
        print("=== grand totals ===")
        for k, n in sorted(grand.items(), key=lambda kv: -kv[1]):
            print(f"  {n:5}  {k}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
