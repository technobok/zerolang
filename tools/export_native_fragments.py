"""Export every _Z_* native C template constant from zemitterc_runtime to
src/runtime/natives/<NAME>.inc, so the self-hosted emitter reads the same
texts. The python module stays the source of truth; test_runtime_templates
pins the exported files in sync.

Usage: uv run python tools/export_native_fragments.py   (repo root)
"""

import os
import sys

sys.path.insert(0, "src")

import zemitterc_runtime as rt  # noqa: E402

OUT_DIR = os.path.join("src", "runtime", "natives")


def native_constants() -> "list[tuple[str, str]]":
    out = []
    for name in sorted(dir(rt)):
        if name.startswith("_Z_") and isinstance(getattr(rt, name), str):
            out.append((name, getattr(rt, name)))
    return out


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    count = 0
    for name, text in native_constants():
        with open(os.path.join(OUT_DIR, f"{name}.inc"), "w", encoding="utf-8") as fh:
            fh.write(text)
        count += 1
    print(f"exported {count} native fragments to {OUT_DIR}")


if __name__ == "__main__":
    main()
