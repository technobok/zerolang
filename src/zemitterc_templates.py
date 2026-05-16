"""Runtime template loader for the zerolang C emitter.

Monomorphized container implementations (list, map, array, str,
listview) live as `.c.tmpl` files under `src/runtime/`. The emitter
reads each template once, substitutes a per-monomorphization
placeholder dict, and splices the result into the generated C.

Placeholder syntax is `@@NAME@@` — chosen to be invalid C so a
half-substituted template fails gcc rather than silently emitting
wrong code. `apply()` rejects any leftover `@@` occurrence with a
descriptive error.

Non-templated runtime fragments (z_string.inc, z_stringview.inc)
go through zemitterc_runtime's `_load_runtime_fragment` instead.

# Templated codegen surface (F7 / codereview20260428)

Templated today (`src/runtime/*.c.tmpl`):

| Template                   | Caller in `zemitterc.py`     | Subsystem                       |
|----------------------------|------------------------------|---------------------------------|
| `z_array.c.tmpl`           | `_emit_mono_array`           | fixed-size array                |
| `z_str.c.tmpl`             | `_emit_mono_str`             | bounded string                  |
| `z_List.c.tmpl`            | `_emit_mono_list`            | dynamic list                    |
| `z_ListView.c.tmpl`        | `_emit_mono_listview`        | borrowed list view              |
| `z_protocol_vtable.c.tmpl` | `_emit_protocol` /           | protocol vtable struct +        |
|                            | `_emit_mono_protocol`        | instance wrapper + destroy      |

Not yet templated (ad-hoc f-string emission in `zemitterc.py`):

- **Protocol implementation wrappers + static vtable init**
  (`_emit_protocol_impl`, `:2158-2242+`). Natural next target — same
  per-method-mechanical shape as the vtable struct but more
  bookkeeping (per-method wrapper functions plus the static init
  block). Defer until a concrete need (e.g. ABI change requires
  centralised audit).
- **Map containers** (`_emit_mono_map`). Has key/value branching
  on string vs reftype that doesn't fit the
  pure-placeholder model; would need either conditional sections
  in the template format (new feature) or multiple variant
  templates.
- **`meta.create` + user constructors** (`_emit_create_functions`).
  Tightly integrated with caller; not a standalone subsystem.

Adding a new template is just:
  1. Write `src/runtime/<name>.c.tmpl` using `@@PLACEHOLDER@@`.
  2. Call `ztmpl.apply("<name>.c.tmpl", {...})` from the emitter.
  3. Add a smoke test in `tests/test_runtime_templates.py`.
"""

import os
import re
from typing import Dict

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "runtime")
_TEMPLATE_CACHE: "dict[str, str]" = {}

# match any @@WORD@@ sequence so we can point at unresolved placeholders
_PLACEHOLDER_PATTERN = re.compile(r"@@[A-Z_]+@@")


def load(name: str) -> str:
    """Read a `.c.tmpl` from src/runtime/ and cache it.

    `name` is the bare template basename (e.g. `z_list`), no
    directory, no extension. The `.c.tmpl` suffix is appended here.
    """
    cached = _TEMPLATE_CACHE.get(name)
    if cached is not None:
        return cached
    path = os.path.join(_TEMPLATE_DIR, f"{name}.c.tmpl")
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    _TEMPLATE_CACHE[name] = content
    return content


def apply(template_name: str, placeholders: Dict[str, str]) -> str:
    """Substitute `@@KEY@@` -> value for every entry in `placeholders`,
    then reject any remaining `@@...@@` occurrence as an unresolved
    placeholder. Ordering is not significant — placeholder tokens
    don't overlap — so a single pass of `str.replace` per key is
    correct.
    """
    out = load(template_name)
    for key, value in placeholders.items():
        out = out.replace(f"@@{key}@@", value)
    leftover = _PLACEHOLDER_PATTERN.search(out)
    if leftover is not None:
        raise ValueError(
            f"unresolved placeholder {leftover.group(0)!r} in template "
            f"{template_name!r}; known keys: {sorted(placeholders)}"
        )
    return out
