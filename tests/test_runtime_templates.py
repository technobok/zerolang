"""Per-template compile smoke tests.

Each `.c.tmpl` under `src/runtime/` is substituted with a
representative placeholder set and compiled via `gcc -c -Wall
-Werror`. Catches template typos, bad placeholder names, and
missing newlines well before they reach the full zerolang build.

The tests here are intentionally independent of the zerolang
pipeline: no parser, no type checker, no emitter. Just
placeholder substitution + gcc.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

import pytest

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from zemitterc_templates import apply, load  # noqa: E402


HEADERS = textwrap.dedent(
    """
    #include <stdio.h>
    #include <stdint.h>
    #include <stdlib.h>
    #include <stdbool.h>
    #include <string.h>
    """
).strip()


def _gcc_compile(body: str) -> tuple[int, str]:
    """Compile `body` as C. Returns (rc, stderr). Uses -c -o /dev/null."""
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not available")
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as fh:
        fh.write(HEADERS + "\n\n" + body + "\n")
        path = fh.name
    try:
        result = subprocess.run(
            [
                gcc,
                "-c",
                "-Wall",
                "-Werror",
                "-Wno-unused-function",
                "-o",
                "/dev/null",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stderr
    finally:
        os.unlink(path)


def test_templates_raise_on_unresolved_placeholder():
    """A template loaded with a missing placeholder surfaces a ValueError
    naming the offending token. Uses the simplest template available."""
    # Any template will do; pick the smallest once it exists, otherwise
    # synthesize one to exercise the loader's error path.
    with pytest.raises(ValueError, match=r"unresolved placeholder '@@UNSET@@'"):
        # Write a temp template into the real runtime dir for this test?
        # Easier: call apply() on a pre-registered name with a
        # deliberately-incomplete placeholder dict. Use the module-level
        # cache bypass: manually seed a fake entry.
        from zemitterc_templates import _TEMPLATE_CACHE

        _TEMPLATE_CACHE["__unit_test_missing__"] = "int @@PRESENT@@ = @@UNSET@@;\n"
        try:
            apply("__unit_test_missing__", {"PRESENT": "x"})
        finally:
            del _TEMPLATE_CACHE["__unit_test_missing__"]


def test_loader_caches():
    """`load` returns the same content on subsequent calls without
    re-reading the file. We can't easily probe the disk, but we can
    verify at least two calls return identical strings."""
    from zemitterc_templates import _TEMPLATE_CACHE

    _TEMPLATE_CACHE["__unit_test_cache__"] = "/* cached */\n"
    try:
        assert load("__unit_test_cache__") == "/* cached */\n"
        assert load("__unit_test_cache__") == "/* cached */\n"
    finally:
        del _TEMPLATE_CACHE["__unit_test_cache__"]


def test_listview_template_compiles():
    body = apply("z_listview", {"NAME": "listview_i64", "ELEM_T": "int64_t"})
    rc, err = _gcc_compile(body)
    assert rc == 0, err


def test_array_template_compiles_with_eq():
    eq_body = (
        "static bool z_array_i64_16_eq(z_array_i64_16_t a, z_array_i64_16_t b);\n"
        "static bool z_array_i64_16_eq(z_array_i64_16_t a, z_array_i64_16_t b) {\n"
        "    return memcmp(&a, &b, sizeof(z_array_i64_16_t)) == 0;\n"
        "}\n"
    )
    body = apply(
        "z_array",
        {
            "NAME": "array_i64_16",
            "ELEM_T": "int64_t",
            "LEN": "16",
            "CREATE_BODY": "    z_array_i64_16_t _this = {0};",
            "EQ_BODY": eq_body,
        },
    )
    rc, err = _gcc_compile(body)
    assert rc == 0, err


def test_str_template_compiles():
    body = apply(
        "z_str",
        {
            "NAME": "str_16",
            "CAP": "16",
            "LEN_T": "uint8_t",
            "EQ_BODY": "",
        },
    )
    # z_str uses z_string_t in the .string method — stub it for this test
    stub = (
        "typedef struct { uint64_t size; char* data; uint64_t capacity; } z_string_t;\n\n"
    )
    rc, err = _gcc_compile(stub + body)
    assert rc == 0, err


def test_list_template_compiles_with_destroy_loop():
    destroy_elems = (
        "    for (uint64_t i = 0; i < p->length; i++) {\n"
        "        z_str_16_destroy(&p->data[i]);\n"
        "    }\n"
    )
    # minimal stubs for the referenced types
    stub = (
        "typedef struct { uint64_t _x; } z_str_16_t;\n"
        "static void z_str_16_destroy(z_str_16_t* p) { (void)p; }\n\n"
    )
    body = apply(
        "z_list",
        {
            "NAME": "list_str_16",
            "ELEM_T": "z_str_16_t",
            "DESTROY_ELEMS": destroy_elems,
            "LISTVIEW_METHODS": "",
        },
    )
    rc, err = _gcc_compile(stub + body)
    assert rc == 0, err


def test_list_template_compiles_with_listview_methods():
    listview_methods = (
        "static z_listview_i64_t z_list_i64_listview(z_list_i64_t* _this);\n"
        "static z_listview_i64_t z_list_i64_listview(z_list_i64_t* _this) {\n"
        "    return *(z_listview_i64_t*)_this;\n"
        "}\n"
        "\n"
        "static void z_list_i64_extend_view(z_list_i64_t* _this, z_listview_i64_t _from);\n"
        "static void z_list_i64_extend_view(z_list_i64_t* _this, z_listview_i64_t _from) {\n"
        "    z_list_i64_grow(_this, _this->length + _from.length);\n"
        "    memcpy(&_this->data[_this->length], _from.data, _from.length * sizeof(int64_t));\n"
        "    _this->length += _from.length;\n"
        "}\n\n"
    )
    listview = apply(
        "z_listview", {"NAME": "listview_i64", "ELEM_T": "int64_t"}
    )
    body = apply(
        "z_list",
        {
            "NAME": "list_i64",
            "ELEM_T": "int64_t",
            "DESTROY_ELEMS": "",
            "LISTVIEW_METHODS": listview_methods,
        },
    )
    rc, err = _gcc_compile(listview + body)
    assert rc == 0, err
