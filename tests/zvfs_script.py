"""Pure-Python interpreter for the zerolang VFS op-script DSL.

PR 7 (differential harness) uses this to run the same `.script`
fixtures the zerolang binary runs (`tests/test_zvfs_z.py`), then
asserts byte-for-byte equality against the shared `.expected`
golden. Two ports asserting against one golden = port equivalence
proof.

Verbs mirror `src/zvfs.z`'s `dispatchLine` + `dispatchVfs` exactly.
Output formatting matches the zerolang binary's printf shape:

| Shape | Form |
|---|---|
| Bare id (register / walk / bind) | `"<n>\\n"` |
| stat (provider or vfs) | `"dir"/"file"/"notfound"\\n` |
| path / pathfromprovider | `"<path>\\n"` |
| open success | `"ok: <content>\\n"` |
| open error | `"err: <arm>\\n"` |
| getline some | `"some: <line>\\n"` |
| getline none | `"none\\n"` |
| dump | `"<id>: <arm>\\n"` |

Provider-level `entry`/`dump` verbs operate on a script-local
dentry-arm list (not Python's DEntryTable) — they're a smoke
test of the verb dispatcher, not a check of any Python data
structure.
"""

from __future__ import annotations

import sys
from typing import List, cast

# Make src/ importable when running pytest from the repo root.
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMPILER0_DIR = os.path.join(_REPO_ROOT, "compiler0")
if _COMPILER0_DIR not in sys.path:
    sys.path.insert(0, _COMPILER0_DIR)

from zvfs import (  # noqa: E402
    BindType,
    DEntryType,
    FSProvider,
    NodeID,
    NullProvider,
    ProviderNodeType,
    StringProvider,
    VfsIOError,
    ZVfs,
    ZVfsProvider,
)


# --------------------------------------------------------------------------- #
# Script state                                                                #
# --------------------------------------------------------------------------- #


class ScriptState:
    """Parallel registries matching the zerolang dispatcher's:
    - dentry_arms: arm-name list for raw `entry`/`dump` smoke verbs
    - providers: provider-level registry (matches `ProviderTable.providers`)
    - vfs: engine-level ZVfs instance (one per script)
    """

    def __init__(self) -> None:
        self.dentry_arms: List[str] = []
        self.providers: List[ZVfsProvider] = []
        # auto_register_null=False matches the zerolang port's empty-init
        # state (no auto-registered NullProvider, no implicit dentry 0).
        self.vfs: ZVfs = ZVfs(auto_register_null=False)


# --------------------------------------------------------------------------- #
# Formatting helpers                                                          #
# --------------------------------------------------------------------------- #


_PROVIDER_KIND_TO_NAME = {
    ProviderNodeType.DIR: "dir",
    ProviderNodeType.FILE: "file",
    ProviderNodeType.NOTFOUND: "notfound",
}

_DENTRY_TYPE_TO_ARM = {
    DEntryType.NOTFOUND: "notfound",
    DEntryType.FILE: "file",
    DEntryType.DIR: "directory",
    DEntryType.UNION: "mount",
}

_BIND_KIND = {
    "instead": BindType.INSTEAD,
    "before": BindType.BEFORE,
    "after": BindType.AFTER,
}


def _format_open_ok(content: str) -> str:
    return f"ok: {content}\n"


def _format_open_err(arm: str) -> str:
    return f"err: {arm}\n"


def _vfs_arm_name(vfs: ZVfs, did: int) -> str:
    """Translate a VFS dentry's arm to the nodekind name the zerolang
    `vfs stat` verb prints: mount/root collapse to dir, others map
    directly.
    """
    entry = vfs.entrytable[did]
    if entry.entrytype == DEntryType.FILE:
        return "file"
    if entry.entrytype == DEntryType.NOTFOUND:
        return "notfound"
    # DIR + UNION (mount) both print as "dir" (matches src/zvfs.z's
    # `arm` method).
    return "dir"


# --------------------------------------------------------------------------- #
# Dispatch loop                                                               #
# --------------------------------------------------------------------------- #


def run_python_dispatcher(script_text: str) -> str:
    """Execute the script against Python ZVfs APIs; return captured stdout."""
    state = ScriptState()
    out: List[str] = []
    for line in script_text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        _dispatch_line(trimmed, state, out)
    return "".join(out)


def _dispatch_line(line: str, state: ScriptState, out: List[str]) -> None:
    tokens = line.split()
    verb = tokens[0]
    if verb == "entry":
        _dispatch_entry(tokens, state, out)
    elif verb == "dump":
        _dispatch_dump(tokens, state, out)
    elif verb == "provider":
        _dispatch_provider(tokens, state, out)
    elif verb == "walk":
        _dispatch_walk(tokens, state, out)
    elif verb == "stat":
        _dispatch_stat(tokens, state, out)
    elif verb == "path":
        _dispatch_path(tokens, state, out)
    elif verb == "addFile":
        _dispatch_addfile(tokens, state, out)
    elif verb == "open":
        _dispatch_open(tokens, state, out)
    elif verb == "vfs":
        _dispatch_vfs(tokens, state, out)
    else:
        raise RuntimeError(f"unknown verb: {verb}")


# --------------------------------------------------------------------------- #
# Dentry-level verbs (smoke; arm-name list, not Python's DEntryTable)         #
# --------------------------------------------------------------------------- #


def _dispatch_entry(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    # `entry <arm> [args...]` — append arm tag, print assigned id.
    # We don't care about the args for the smoke test; the zerolang
    # port appends a real dentry to its table, but the only thing the
    # `dump` verb prints is the arm name + id.
    if len(tokens) < 2:
        raise RuntimeError("entry verb expects an arm name")
    arm = tokens[1]
    state.dentry_arms.append(arm)
    out.append(f"{len(state.dentry_arms) - 1}\n")


def _dispatch_dump(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 2:
        raise RuntimeError("dump expects 1 arg")
    did = int(tokens[1])
    out.append(f"{did}: {state.dentry_arms[did]}\n")


# --------------------------------------------------------------------------- #
# Provider-level verbs                                                        #
# --------------------------------------------------------------------------- #


def _dispatch_provider(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) < 2:
        raise RuntimeError("provider verb expects a kind name")
    kind = tokens[1]
    p: ZVfsProvider
    if kind == "null":
        if len(tokens) != 2:
            raise RuntimeError("provider null takes no args")
        p = NullProvider()
    elif kind == "string":
        if len(tokens) != 2:
            raise RuntimeError("provider string takes no args")
        p = StringProvider({})
    elif kind == "fs":
        if len(tokens) != 4:
            raise RuntimeError("provider fs expects 2 args (rootpath parentpath)")
        p = FSProvider(rootpath=tokens[2], parentpath=tokens[3])
    else:
        raise RuntimeError(f"unknown provider kind: {kind}")
    state.providers.append(p)
    out.append(f"{len(state.providers) - 1}\n")


def _dispatch_walk(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 4:
        raise RuntimeError("walk expects 3 args (providerid name parentid)")
    pid = int(tokens[1])
    name = tokens[2]
    parent = int(tokens[3])
    nid = state.providers[pid].walk(name, NodeID(parent))
    out.append(f"{int(nid)}\n")


def _dispatch_stat(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 3:
        raise RuntimeError("stat expects 2 args (providerid itemid)")
    pid = int(tokens[1])
    item = int(tokens[2])
    kind = state.providers[pid].stat(NodeID(item))
    out.append(f"{_PROVIDER_KIND_TO_NAME[kind]}\n")


def _dispatch_path(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 3:
        raise RuntimeError("path expects 2 args (providerid itemid)")
    pid = int(tokens[1])
    item = int(tokens[2])
    s = state.providers[pid].path(NodeID(item))
    out.append(f"{s}\n")


def _dispatch_addfile(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 4:
        raise RuntimeError("addFile expects 3 args (providerid fpath content)")
    pid = int(tokens[1])
    fpath = tokens[2]
    content = tokens[3]
    p = state.providers[pid]
    if isinstance(p, StringProvider):
        p.addFile(fpath, content)
    else:
        raise VfsIOError("other", f"{type(p).__name__} has no file storage")


def _dispatch_open(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 3:
        raise RuntimeError("open expects 2 args (providerid itemid)")
    pid = int(tokens[1])
    item = int(tokens[2])
    try:
        f = state.providers[pid].open(NodeID(item))
        content = f.read()
        f.close()
        out.append(_format_open_ok(content))
    except VfsIOError as e:
        out.append(_format_open_err(e.arm))


# --------------------------------------------------------------------------- #
# VFS engine verbs                                                            #
# --------------------------------------------------------------------------- #


def _dispatch_vfs(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) < 2:
        raise RuntimeError("vfs verb expects a subverb")
    sub = tokens[1]
    if sub == "register":
        _dispatch_vfs_register(tokens, state, out)
    elif sub == "walk":
        _dispatch_vfs_walk(tokens, state, out)
    elif sub == "stat":
        _dispatch_vfs_stat(tokens, state, out)
    elif sub == "path":
        _dispatch_vfs_path(tokens, state, out)
    elif sub == "pathfromprovider":
        _dispatch_vfs_pathfromprovider(tokens, state, out)
    elif sub == "open":
        _dispatch_vfs_open(tokens, state, out)
    elif sub == "getline":
        _dispatch_vfs_getline(tokens, state, out)
    elif sub == "bindroot":
        _dispatch_vfs_bindroot(tokens, state, out)
    elif sub == "bind":
        _dispatch_vfs_bind(tokens, state, out)
    else:
        raise RuntimeError(f"vfs: unknown subverb: {sub}")


def _dispatch_vfs_register(
    tokens: List[str], state: ScriptState, out: List[str]
) -> None:
    if len(tokens) < 3:
        raise RuntimeError("vfs register expects a kind")
    kind = tokens[2]
    p: ZVfsProvider
    if kind == "null":
        if len(tokens) != 3:
            raise RuntimeError("vfs register null takes no args")
        p = NullProvider()
    elif kind == "string":
        if len(tokens) != 3:
            raise RuntimeError("vfs register string takes no args")
        p = StringProvider({})
    elif kind == "fs":
        if len(tokens) != 5:
            raise RuntimeError("vfs register fs expects 2 args (rootpath parentpath)")
        p = FSProvider(rootpath=tokens[3], parentpath=tokens[4])
    else:
        raise RuntimeError(f"vfs register: unknown kind: {kind}")
    did = state.vfs.register(p)
    out.append(f"{int(did)}\n")


def _dispatch_vfs_walk(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 4:
        raise RuntimeError("vfs walk expects 2 args (parentid name)")
    pid = int(tokens[2])
    name = tokens[3]
    did = state.vfs.walk(path=[name], parentid=cast(NodeID, pid))
    out.append(f"{int(did)}\n")


def _dispatch_vfs_stat(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 3:
        raise RuntimeError("vfs stat expects 1 arg (dentryid)")
    did = int(tokens[2])
    out.append(f"{_vfs_arm_name(state.vfs, did)}\n")


def _dispatch_vfs_path(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 3:
        raise RuntimeError("vfs path expects 1 arg (dentryid)")
    did = int(tokens[2])
    s = state.vfs.path(cast(NodeID, did))
    out.append(f"{s}\n")


def _dispatch_vfs_pathfromprovider(
    tokens: List[str], state: ScriptState, out: List[str]
) -> None:
    if len(tokens) != 3:
        raise RuntimeError("vfs pathfromprovider expects 1 arg (dentryid)")
    did = int(tokens[2])
    s = state.vfs.pathfromprovider(cast(NodeID, did))
    out.append(f"{s}\n")


def _dispatch_vfs_open(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 3:
        raise RuntimeError("vfs open expects 1 arg (dentryid)")
    did = int(tokens[2])
    try:
        f = state.vfs.open(cast(NodeID, did))
        content = f.filehandle.read()
        f.close()
        out.append(_format_open_ok(content))
    except VfsIOError as e:
        out.append(_format_open_err(e.arm))


def _dispatch_vfs_getline(
    tokens: List[str], state: ScriptState, out: List[str]
) -> None:
    if len(tokens) != 4:
        raise RuntimeError("vfs getline expects 2 args (dentryid lineno)")
    did = int(tokens[2])
    lineno = int(tokens[3])
    line = state.vfs.getline(cast(NodeID, did), lineno)
    if line is None:
        out.append("none\n")
    else:
        out.append(f"some: {line}\n")


def _dispatch_vfs_bindroot(
    tokens: List[str], state: ScriptState, out: List[str]
) -> None:
    if len(tokens) != 4:
        raise RuntimeError("vfs bindroot expects 2 args (newid kind)")
    newid = int(tokens[2])
    kind_str = tokens[3]
    if kind_str not in _BIND_KIND:
        raise RuntimeError(f"vfs bindroot: unknown kind: {kind_str}")
    kind = _BIND_KIND[kind_str]
    did = state.vfs.bind(
        parentid=state.vfs.rootid,
        name=None,
        newid=cast(NodeID, newid),
        bindtype=kind,
    )
    out.append(f"{int(did)}\n")


def _dispatch_vfs_bind(tokens: List[str], state: ScriptState, out: List[str]) -> None:
    if len(tokens) != 6:
        raise RuntimeError("vfs bind expects 4 args (parentid name newid kind)")
    parentid = int(tokens[2])
    name = tokens[3]
    newid = int(tokens[4])
    kind_str = tokens[5]
    if kind_str not in _BIND_KIND:
        raise RuntimeError(f"vfs bind: unknown kind: {kind_str}")
    kind = _BIND_KIND[kind_str]
    did = state.vfs.bind(
        parentid=cast(NodeID, parentid),
        name=name,
        newid=cast(NodeID, newid),
        bindtype=kind,
    )
    out.append(f"{int(did)}\n")
