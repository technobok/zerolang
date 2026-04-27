#!/usr/bin/env python3
"""
SQL-driven targeted renamer for the Zerolang style guide apply phase.

Workflow:
  1. Generate a SQL dump from a successful zc compilation.
  2. Query the dump to find every token that needs renaming, classified by
     AST node kind (so type uses get PascalCase, but method names / variant
     tags / parameter names stay lowercase).
  3. Generate a list of (file_path, line, col, old_text, new_text) edits.
  4. Apply edits in reverse (line, col) order to avoid offset shift.

Usage:
  sql_rename.py <sql_dump_file> <project_root>

The renamer hardcodes the rename map (same as /tmp/rename_zerolang.py)
and the file-id → on-disk-path resolution for lib/system and examples.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Rename map (same logical content as /tmp/rename_zerolang.py).
# ---------------------------------------------------------------------------

# Reftype lowercase → PascalCase. Used when token references a type.
REFTYPES = {
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

# Get-prefix drops on getter-style functions. These appear as Function names
# (LHS of the def) and as method-call sites.
GET_DROPS = {
    "get_env":        "env",
    "get_option":     "option",
    "get_positional": "positional",
    "get_value":      "value",
}

# snake_case → camelCase for functions/methods/fields/constants.
SNAKE_TO_CAMEL = {
    "string_join":      "stringJoin",
    "extend_view":      "extendView",
    "iterate_items":    "iterateItems",
    "read_text":        "readText",
    "write_text":       "writeText",
    "append_text":      "appendText",
    "read_line":        "readLine",
    "write_line":       "writeLine",
    "list_dir":         "listDir",
    "set_env":          "setEnv",
    "unset_env":        "unsetEnv",
    "env_names":        "envNames",
    "set_cwd":          "setCwd",
    "user_name":        "userName",
    "home_dir":         "homeDir",
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
    "has_flag":         "hasFlag",
    "add_flag":         "addFlag",
    "add_option":       "addOption",
    "add_positional":   "addPositional",
    "help_text":        "helpText",
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
    "mtime_seconds":    "mtimeSeconds",
    "atime_seconds":    "atimeSeconds",
    "ctime_seconds":    "ctimeSeconds",
    "invalid_digit":    "invalidDigit",
}


# Reftype-defining AST kinds — for these, the def-LHS should be renamed.
REFTYPE_KINDS = ("Class", "Union", "Protocol")


# ---------------------------------------------------------------------------
# Edit model.
# ---------------------------------------------------------------------------

class Edit(NamedTuple):
    file_path: Path
    line: int
    col: int     # 1-based
    old: str
    new: str


def _resolve_file_path(unit_path: str, project_root: Path) -> Path | None:
    """Resolve the file id's basename to an on-disk path under project_root.

    Looks under lib/system/ and examples/ (in that priority).
    """
    for sub in ("lib/system", "examples"):
        candidate = project_root / sub / unit_path
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Edit collection.
# ---------------------------------------------------------------------------

def collect_atomid_edits(con: sqlite3.Connection, project_root: Path) -> list[Edit]:
    """Type-usage references: AtomId tokens whose text matches REFTYPES.

    Disambiguates type accesses from method calls using the SQL `unit`
    and `type_children` tables. The rule:

      - For a bare token (not preceded by `.`): it's a top-level reference;
        rename if it would resolve to a type. Compared against REFTYPES.
      - For a token preceded by `.<prev>`: the preceding `<prev>` is the
        receiver. If `<prev>` is a known unit name (system/cli/io/os/etc.),
        look up the token in that unit's children. Rename only when the
        child's typetype is one of CLASS/UNION/PROTOCOL/RECORD/VARIANT.
      - If `<prev>` is not a unit (i.e. a value), the token is a method
        call or field access — never rename as a type.
    """
    edits: list[Edit] = []
    keys = list(REFTYPES.keys())
    placeholders = ",".join("?" for _ in keys)

    # Build set of known unit names.
    unit_names: set[str] = {
        row[0] for row in con.execute("SELECT name FROM unit;")
    }
    # Also include common stdlib unit names that might not be in this dump.
    unit_names.update({"core", "system", "io", "os", "cli", "collections"})

    # Build name → typetype for top-level types. Used to verify a name
    # really resolves to a type (not just a method).
    name_typetype: dict[str, str] = {}
    for name, ttype in con.execute(
        "SELECT name, typetype FROM types WHERE typetype IS NOT NULL;"
    ):
        if name and "." not in name and name not in name_typetype:
            name_typetype[name] = ttype

    REFTYPE_TYPETYPES = {"CLASS", "UNION", "PROTOCOL", "RECORD", "VARIANT"}

    # Build: per (file_id, line) → list of (col, text) tokens, sorted by col.
    tokens_by_line: dict[tuple[int, int], list[tuple[int, str, str]]] = {}
    for fid, line, col, kind, text in con.execute(
        "SELECT file_id, line, col, kind, text FROM tokens;"
    ):
        tokens_by_line.setdefault((fid, line), []).append((col, kind, text))
    for v in tokens_by_line.values():
        v.sort()

    # Collect candidate edits.
    rows = con.execute(
        f"""
        SELECT t.token_id, t.file_id, t.text, f.path, t.line, t.col, a.kind
          FROM tokens t
          JOIN ast_nodes a ON a.token_id = t.token_id
          JOIN files f ON t.file_id = f.file_id
         WHERE t.text IN ({placeholders})
           AND a.kind IN ('AtomId','DottedPath')
        """,
        keys,
    ).fetchall()
    for _tid, fid, old, unit_path, line, col, _kind in rows:
        path = _resolve_file_path(unit_path, project_root)
        if path is None:
            continue

        # Determine the preceding context: was this token preceded by `.<prev>`?
        with path.open() as f:
            source_lines = f.readlines()
        if line < 1 or line > len(source_lines):
            continue
        src_line = source_lines[line - 1]
        # Look at the character just before the token's column (1-based).
        # If it's `.`, walk back further to find the previous identifier.
        idx = col - 1  # 0-based start of token
        if idx > 0 and src_line[idx - 1] == ".":
            # Find the previous identifier ending at idx-1.
            j = idx - 1
            k = j - 1
            while k >= 0 and (src_line[k].isalnum() or src_line[k] == "_"):
                k -= 1
            prev = src_line[k + 1 : j]
            # Rename only when the receiver is a known unit name AND the
            # token resolves to a type (verified via the types table).
            if prev in unit_names and name_typetype.get(old) in REFTYPE_TYPETYPES:
                edits.append(Edit(path, line, col, old, REFTYPES[old]))
            # else: method call or non-type member — leave alone.
        else:
            # Bare reference (no leading `.`) — top-level type or local name.
            edits.append(Edit(path, line, col, old, REFTYPES[old]))
    return edits


def collect_typedef_lhs_edits(con: sqlite3.Connection, project_root: Path) -> list[Edit]:
    """Reftype-definition LHS labels — find leading `name:` on the def line."""
    edits: list[Edit] = []
    rows = con.execute(
        f"""
        SELECT a.kind, a.name, a.start_line, f.path
          FROM ast_nodes a
          JOIN files f ON a.file_id = f.file_id
         WHERE a.kind IN ({','.join('?' for _ in REFTYPE_KINDS)})
        """,
        REFTYPE_KINDS,
    ).fetchall()
    for _kind, qname, start_line, unit_path in rows:
        # Strip qualifier (e.g. system.option → option, io.bufwriter → bufwriter).
        bare = qname.rsplit(".", 1)[-1]
        if bare not in REFTYPES:
            continue
        path = _resolve_file_path(unit_path, project_root)
        if path is None:
            continue
        # Read the line and find `^\s*<bare>:`.
        with path.open() as f:
            lines = f.readlines()
        if start_line < 1 or start_line > len(lines):
            continue
        line = lines[start_line - 1]
        m = re.match(rf"^(\s*)({re.escape(bare)})\s*:", line)
        if not m:
            continue
        col = len(m.group(1)) + 1  # 1-based
        edits.append(Edit(path, start_line, col, bare, REFTYPES[bare]))
    return edits


def collect_function_lhs_edits(con: sqlite3.Connection, project_root: Path) -> list[Edit]:
    """Function definition LHS — for snake_case → camelCase and get-drop."""
    edits: list[Edit] = []
    rows = con.execute(
        """
        SELECT a.name, a.start_line, f.path
          FROM ast_nodes a
          JOIN files f ON a.file_id = f.file_id
         WHERE a.kind = 'Function'
        """
    ).fetchall()
    for qname, start_line, unit_path in rows:
        bare = qname.rsplit(".", 1)[-1] if qname else None
        if not bare:
            continue
        new = None
        if bare in GET_DROPS:
            new = GET_DROPS[bare]
        elif bare in SNAKE_TO_CAMEL:
            new = SNAKE_TO_CAMEL[bare]
        if new is None:
            continue
        path = _resolve_file_path(unit_path, project_root)
        if path is None:
            continue
        with path.open() as f:
            lines = f.readlines()
        if start_line < 1 or start_line > len(lines):
            continue
        line = lines[start_line - 1]
        m = re.match(rf"^(\s*)({re.escape(bare)})\s*:", line)
        if not m:
            continue
        col = len(m.group(1)) + 1
        edits.append(Edit(path, start_line, col, bare, new))
    return edits


def collect_call_label_edits(con: sqlite3.Connection, project_root: Path) -> list[Edit]:
    """Method-call sites for snake_case → camelCase / get-drop.

    Method/function call sites appear via DottedPath (`x.read_text` →
    DottedPath whose token's text is `read_text`) or as AtomId tokens
    when called as a bare name. Dotted method calls are tokenised as
    `read_text` REFID following a `.`. Match by token text against
    SNAKE_TO_CAMEL ∪ GET_DROPS, but only when the AST node is an AtomId
    or LabelValue / DottedPath segment that we can identify as a call
    target. Simplest: rename ALL tokens whose text is a key in those
    maps and whose AST kind is AtomId / DottedPath / LabelValue (call
    sites). False positives are avoided because the tokens table only
    holds tokens that appear in AST positions, and the snake_case keys
    are unambiguous (they don't appear as variable names elsewhere in
    the stdlib).
    """
    edits: list[Edit] = []
    keys = list(SNAKE_TO_CAMEL.keys()) + list(GET_DROPS.keys())
    placeholders = ",".join("?" for _ in keys)
    rows = con.execute(
        f"""
        SELECT t.text, f.path, t.line, t.col, a.kind
          FROM tokens t
          JOIN ast_nodes a ON a.token_id = t.token_id
          JOIN files f ON t.file_id = f.file_id
         WHERE t.text IN ({placeholders})
           AND a.kind IN ('AtomId','DottedPath','LabelValue','NamedOperation')
        """,
        keys,
    ).fetchall()
    for old, unit_path, line, col, _kind in rows:
        path = _resolve_file_path(unit_path, project_root)
        if path is None:
            continue
        new = SNAKE_TO_CAMEL.get(old) or GET_DROPS.get(old)
        if new is None:
            continue
        edits.append(Edit(path, line, col, old, new))
    return edits


def collect_labelpre_edits(con: sqlite3.Connection, project_root: Path) -> list[Edit]:
    """LABELPRE tokens — these are union/variant arm shorthand `:typename`.

    The arm's name and type are both the same identifier; renaming the
    type means renaming the LABELPRE token too (the leading `:` stays).
    """
    edits: list[Edit] = []
    keys = list(REFTYPES.keys())
    placeholders = ",".join("?" for _ in keys)
    rows = con.execute(
        f"""
        SELECT t.text, f.path, t.line, t.col
          FROM tokens t
          JOIN files f ON t.file_id = f.file_id
         WHERE t.text IN ({placeholders})
           AND t.kind = 'LABELPRE'
        """,
        keys,
    ).fetchall()
    for old, unit_path, line, col in rows:
        path = _resolve_file_path(unit_path, project_root)
        if path is None:
            continue
        edits.append(Edit(path, line, col, old, REFTYPES[old]))
    return edits


def collect_alias_lhs_edits(project_root: Path) -> list[Edit]:
    """Top-level alias-style definitions whose LHS bare name is a reftype.

    The canonical case is lib/system/core.z, which re-exports stdlib
    reftypes by their lowercase original name. After PascalCase rename in
    the defining units, the alias LHS must also be renamed so cross-unit
    lookups (io.z's bare `String` reference, etc.) resolve.

    Any line of the form `^<bare>:` at column 1 (top-level, no indent) in
    a stdlib unit, where <bare> is in REFTYPES, qualifies.
    """
    edits: list[Edit] = []
    pat = re.compile(r"^([a-z][a-zA-Z0-9_]*)\s*:")
    for path in (project_root / "lib/system").glob("*.z"):
        with path.open() as f:
            lines = f.readlines()
        for lineno, line in enumerate(lines, start=1):
            m = pat.match(line)
            if not m:
                continue
            bare = m.group(1)
            if bare not in REFTYPES:
                continue
            edits.append(Edit(path, lineno, 1, bare, REFTYPES[bare]))
    return edits


def collect_field_lhs_edits_via_grep(project_root: Path) -> list[Edit]:
    """Field/constant LHS labels for snake_case → camelCase.

    Fields and constants don't have their own AST node kind in the same way
    Functions do (they're encoded as LabelValue children of records/classes).
    We catch them via line-based regex on lib/system/*.z files: any leading
    `\\s+<snake>:` where <snake> is one of our SNAKE_TO_CAMEL keys.
    """
    edits: list[Edit] = []
    keys = list(SNAKE_TO_CAMEL.keys())
    pat = re.compile(
        rf"^(\s*)({'|'.join(re.escape(k) for k in keys)})\s*:"
    )
    for sub in ("lib/system", "examples"):
        for path in (project_root / sub).glob("*.z"):
            with path.open() as f:
                lines = f.readlines()
            for lineno, line in enumerate(lines, start=1):
                m = pat.match(line)
                if not m:
                    continue
                bare = m.group(2)
                col = len(m.group(1)) + 1
                edits.append(Edit(path, lineno, col, bare, SNAKE_TO_CAMEL[bare]))
    return edits


# ---------------------------------------------------------------------------
# Apply edits.
# ---------------------------------------------------------------------------

def apply_edits(edits: list[Edit]) -> dict[Path, int]:
    """Apply edits in reverse order per file. Returns a dict of path → count."""
    by_file: dict[Path, list[Edit]] = defaultdict(list)
    for e in edits:
        by_file[e.file_path].append(e)
    counts: dict[Path, int] = {}
    for path, file_edits in by_file.items():
        # Deduplicate identical edits.
        seen = set()
        unique: list[Edit] = []
        for e in file_edits:
            key = (e.line, e.col, e.old, e.new)
            if key in seen:
                continue
            seen.add(key)
            unique.append(e)
        # Sort descending so later positions on the same line don't shift earlier.
        unique.sort(key=lambda e: (e.line, e.col), reverse=True)
        with path.open() as f:
            lines = f.readlines()
        applied = 0
        for e in unique:
            if e.line < 1 or e.line > len(lines):
                print(f"warning: skip out-of-range edit {e}", file=sys.stderr)
                continue
            line = lines[e.line - 1]
            # Validate: the old text must be at (col-1).
            start = e.col - 1
            if line[start : start + len(e.old)] != e.old:
                print(
                    f"warning: text mismatch at {path}:{e.line}:{e.col} "
                    f"expected {e.old!r}, got {line[start:start + len(e.old)]!r}",
                    file=sys.stderr,
                )
                continue
            lines[e.line - 1] = line[:start] + e.new + line[start + len(e.old):]
            applied += 1
        with path.open("w") as f:
            f.writelines(lines)
        counts[path] = applied
    return counts


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: sql_rename.py <sql_dump>... <project_root>", file=sys.stderr)
        return 2

    sql_paths = [Path(p) for p in argv[1:-1]]
    project_root = Path(argv[-1]).resolve()

    edits: list[Edit] = []

    # Apply file-only passes (don't need any SQL data).
    edits += collect_field_lhs_edits_via_grep(project_root)
    edits += collect_alias_lhs_edits(project_root)

    # Apply SQL-data-driven passes per dump and merge.
    for sql_path in sql_paths:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = Path(tf.name)
        con = sqlite3.connect(db_path)
        sql = sql_path.read_text()
        con.executescript(sql)
        edits += collect_atomid_edits(con, project_root)
        edits += collect_typedef_lhs_edits(con, project_root)
        edits += collect_function_lhs_edits(con, project_root)
        edits += collect_call_label_edits(con, project_root)
        edits += collect_labelpre_edits(con, project_root)
        con.close()
        db_path.unlink(missing_ok=True)

    print(f"{len(edits)} edits before dedup", file=sys.stderr)

    counts = apply_edits(edits)
    total = sum(counts.values())
    print(f"applied {total} edits across {len(counts)} files", file=sys.stderr)
    for path, n in sorted(counts.items()):
        print(f"  {n:5}  {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
