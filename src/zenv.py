"""
ZeroLang scoped symbol table for the type checker

List-based environment: each scope holds a List[Entry] rather than dicts.
Scopes are small (measured max ~6 entries), so linear scan beats hash lookup.
"""

from typing import Optional, List, Tuple
from ztypes import (
    ZType,
    ZVariable,
    ZLockState,
    LockInfo,
    ScopeKind,
    Entry,
    _alloc_scope_id,
)


def _paths_overlap(p1: Tuple[str, ...], p2: Tuple[str, ...]) -> bool:
    """Two lock paths overlap iff one is a (non-strict) prefix of the other."""
    n = len(p1) if len(p1) < len(p2) else len(p2)
    return p1[:n] == p2[:n]


def _lock_acquire_conflict(
    existing: LockInfo,
    req_path: Tuple[str, ...],
    req_type: ZLockState,
) -> bool:
    """Multi-granularity conflict check for a NEW lock acquisition.

    Caller has already filtered by `entry.name == req_path[0]` so both
    paths share the same root. Rule:

    - Same path: conflict iff at least one is EXCLUSIVE.
    - Strict-ancestor existing + descendant requested:
      conflict iff the ancestor is EXCLUSIVE (it owns the whole subtree).
    - Strict-ancestor requested + descendant existing:
      conflict iff the requested is EXCLUSIVE (it would absorb the subtree
      containing an outstanding lock).
    - Sibling (no prefix relation): never conflict.

    SHARED on an ancestor is treated as INTENT-shared in the multi-
    granularity sense — it permits any locks (S or X) on descendants.
    This is what allows a single operation to install SHARED on every
    intermediate plus EXCLUSIVE on the leaf without self-conflict.
    """
    ep = existing.path
    rp = req_path
    if ep == rp:
        return (
            existing.lock_type == ZLockState.EXCLUSIVE
            or req_type == ZLockState.EXCLUSIVE
        )
    if len(ep) < len(rp) and ep == rp[: len(ep)]:
        # existing is strict ancestor of requested
        return existing.lock_type == ZLockState.EXCLUSIVE
    if len(rp) < len(ep) and rp == ep[: len(rp)]:
        # requested is strict ancestor of existing
        return req_type == ZLockState.EXCLUSIVE
    return False


def _format_path(path: Tuple[str, ...]) -> str:
    """Render a lock path as `root.f1.f2` for error messages."""
    return ".".join(path) if path else "<unknown>"


def _format_lock_conflict(
    requested_path: Tuple[str, ...],
    requested_type: ZLockState,
    existing: LockInfo,
) -> str:
    """Path-aware conflict message. Mentions the root variable name in
    quotes so existing test assertions matching `"'name'"` keep working."""
    root = requested_path[0]
    req_kind = requested_type.name.lower()
    held_kind = existing.lock_type.name.lower()
    req_detail = ""
    if len(requested_path) > 1:
        req_detail = f" on '{_format_path(requested_path)}'"
    held_detail = ""
    if existing.path != requested_path:
        held_detail = f" on '{_format_path(existing.path)}'"
    return (
        f"Cannot take {req_kind} lock on '{root}'{req_detail}: "
        f"already has {held_kind} lock{held_detail} held by '{existing.holder}'"
    )


class Scope:
    """
    A single scope level in the symbol table.
    Entries are stored in a list (not a dict) for simplicity and cache locality.
    """

    def __init__(self, name: str, kind: ScopeKind) -> None:
        self.scope_id: int = _alloc_scope_id()
        self.kind = kind
        self.name = name
        self.entries: List[Entry] = []
        self.unreachable: bool = False

    def find(self, name: str) -> Optional[Entry]:
        """Find the most recent entry for a name in this scope."""
        i = len(self.entries) - 1
        while i >= 0:
            if self.entries[i].name == name:
                return self.entries[i]
            i -= 1
        return None

    def append(self, entry: Entry) -> None:
        self.entries.append(entry)


class SymbolTable:
    """
    Scoped symbol table — a stack of Scope frames.
    Lookup searches from innermost scope to outermost.

    Three scope kinds:
    - BLOCK: language constructs (function, do, for, if, with, match, arm)
    - CALL: call-scoped lock boundary
    - OVERLAY: per-statement state change (immutable shadow records)
    """

    def __init__(self) -> None:
        self._scopes: List[Scope] = []
        # Phase 7c: archive of popped scopes, in pop order. The SQL dumper
        # reads this plus `_scopes` to reconstruct the full scope history.
        # Popped scopes keep their entries/scope_id so an id-based dump is
        # deterministic across runs.
        self._history: List[Scope] = []

    # ---- scope management ----

    def push(self, name: str) -> Scope:
        """Push a block scope. Returns the scope (marker is self.depth - 1)."""
        scope = Scope(name, ScopeKind.BLOCK)
        self._scopes.append(scope)
        return scope

    def push_block(self, name: str) -> int:
        """Push a block scope. Returns the marker for pop_to."""
        marker = len(self._scopes)
        scope = Scope(name, ScopeKind.BLOCK)
        self._scopes.append(scope)
        return marker

    def push_overlay(self) -> Scope:
        """Push an overlay scope for per-statement state changes."""
        scope = Scope("", ScopeKind.OVERLAY)
        self._scopes.append(scope)
        return scope

    def push_call(self) -> int:
        """Push a call scope for call-scoped locking. Returns marker for pop_to."""
        marker = len(self._scopes)
        scope = Scope("", ScopeKind.CALL)
        self._scopes.append(scope)
        return marker

    def pop(self) -> Scope:
        """Pop the topmost scope. Lock entries vanish naturally with the scope.
        Taken entries are merged into the parent so errors persist."""
        top = self._scopes[-1]
        self._scopes.pop()
        # merge taken entries into parent scope
        if self._scopes:
            for entry in top.entries:
                if entry.is_taken and entry.taken_at is not None:
                    if not self._is_taken(entry.name):
                        taken_entry = Entry(
                            name=entry.name,
                            ztype=entry.ztype,
                            is_definition=False,
                            is_taken=True,
                            taken_at=entry.taken_at,
                        )
                        self._scopes[-1].append(taken_entry)
        self._history.append(top)
        return top

    def pop_to(self, marker: int) -> None:
        """Pop all scopes from the given marker (inclusive)."""
        while len(self._scopes) > marker:
            self.pop()

    # ---- name resolution ----

    def define(self, name: str, ztype: ZType) -> None:
        """Define a non-variable name (type, function, control flow)."""
        entry = Entry(name=name, ztype=ztype, is_definition=True)
        self._scopes[-1].append(entry)

    def define_var(self, name: str, var: ZVariable) -> None:
        """Define a runtime variable with ownership tracking."""
        entry = Entry(name=name, ztype=var.ztype, is_definition=True, var=var)
        self._scopes[-1].append(entry)

    def lookup(self, name: str) -> Optional[ZType]:
        """Search scopes inner→outer for a name. Returns its type or None."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None:
                if entry.is_taken:
                    return None  # taken variables are not resolvable
                return entry.ztype
            i -= 1
        return None

    def lookup_var(self, name: str) -> Optional[ZVariable]:
        """Search scopes inner→outer for a variable. Returns ZVariable or None.

        Skips lock overlays and taken markers (entries without var) to find
        the actual variable definition.
        """
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if entry.name == name:
                    if entry.is_taken:
                        return None
                    if entry.var is not None:
                        return entry.var
                    # skip lock overlays and other non-var entries in this scope
                j -= 1
            i -= 1
        return None

    def lookup_entry(self, name: str) -> Optional[Entry]:
        """Search scopes inner→outer for a name. Returns the Entry or None."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None:
                return entry
            i -= 1
        return None

    # ---- invalidation (take) ----

    def invalidate(self, name: str, loc: Optional[Tuple[int, int, int]] = None) -> bool:
        """Mark a variable as consumed (taken). Pushes an overlay with is_taken.

        Returns True if the name was found in any scope.
        """
        # find the entry to get its type for the taken overlay
        entry = self.lookup_entry(name)
        if entry is None:
            return False
        # push a taken overlay in the current scope
        taken_entry = Entry(
            name=name,
            ztype=entry.ztype,
            is_definition=False,
            is_taken=True,
            taken_at=loc,
        )
        self._scopes[-1].append(taken_entry)
        return True

    def get_taken_location(self, name: str) -> Optional[Tuple[int, int, int]]:
        """Return the (line, col, file_id) where a variable was taken, or None."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None and entry.is_taken:
                return entry.taken_at
            i -= 1
        return None

    def clear_taken(self, name: str) -> None:
        """Remove the taken record for a name (used by match/case to restore
        a variable between arms). Searches from innermost scope."""
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                if scope.entries[j].name == name and scope.entries[j].is_taken:
                    scope.entries.pop(j)
                    return
                j -= 1
            i -= 1

    def set_taken_location(self, name: str, loc: Tuple[int, int, int]) -> None:
        """Override the taken location for a name (used by match/case for
        better error reporting)."""
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                if scope.entries[j].name == name and scope.entries[j].is_taken:
                    scope.entries[j].taken_at = loc
                    return
                j -= 1
            i -= 1

    def _is_taken(self, name: str) -> bool:
        """Check if a name has a taken entry in any scope."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None:
                return entry.is_taken
            i -= 1
        return False

    # ---- lock operations (scope-based: locks are Entry.lock in scope chain) ----

    def try_lock(
        self,
        path: Tuple[str, ...],
        lock_type: ZLockState,
        holder: str,
        self_holder: Optional[str] = None,
    ) -> Optional[str]:
        """Try to take a lock on `path`. Returns error message or None on success.

        `path` is `(root, f1, f2, ...)` — the full addressable lock target.
        `path[0]` must resolve to a known variable; otherwise no lock is taken.

        Conflict rule (prefix-overlap): two paths conflict iff one is a prefix
        of the other AND at least one is EXCLUSIVE. SHARED-on-SHARED never
        conflicts; SHARED-on-same-full-path dedupes (no entry added).

        `self_holder` (when set) names a lock holder that should be treated
        as "the current operation" — existing locks with that holder do not
        block the new acquisition. Lets a call install receiver + arg
        locks under one identity without self-conflict.

        On success, appends a lock Entry to the current scope keyed by
        `path[0]` so the existing scope-chain machinery (release on pop,
        release_held_locks) keeps working unchanged.
        """
        if not path:
            return None
        target_name = path[0]
        target_var = self.lookup_var(target_name)
        if target_var is None:
            return None  # unknown variable, skip lock checking

        # scan scope chain for locks rooted at target_name and apply
        # multi-granularity conflict rule
        idempotent = False
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if entry.name == target_name and entry.lock is not None:
                    existing = entry.lock
                    same_call = (
                        self_holder is not None and existing.holder == self_holder
                    )
                    if not same_call and _lock_acquire_conflict(
                        existing, path, lock_type
                    ):
                        return _format_lock_conflict(path, lock_type, existing)
                    # SHARED-on-same-path is idempotent — skip adding a new
                    # entry to keep the scope chain compact (release stays
                    # correct since the original entry's holder lives at
                    # least as long as the redundant request).
                    if (
                        existing.path == path
                        and existing.lock_type == ZLockState.SHARED
                        and lock_type == ZLockState.SHARED
                    ):
                        idempotent = True
                j -= 1
            i -= 1
        if idempotent:
            return None

        # add lock entry to current scope
        lock_entry = Entry(
            name=target_name,
            ztype=target_var.ztype,
            is_definition=False,
            lock=LockInfo(lock_type=lock_type, holder=holder, path=path),
        )
        self._scopes[-1].append(lock_entry)
        return None

    def find_lock(self, name: str) -> Optional[LockInfo]:
        """Search scope chain for any lock rooted at `name`. Returns the
        innermost LockInfo or None.

        Name-based wrapper for legacy callers (e.g. .release checks). For
        precise prefix-overlap queries use `find_exclusive_lock(path)`.
        """
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if entry.name == name and entry.lock is not None:
                    return entry.lock
                j -= 1
            i -= 1
        return None

    def find_exclusive_lock(
        self, path: Tuple[str, ...]
    ) -> Optional[Tuple[Tuple[str, ...], str]]:
        """Return `(conflicting_path, holder)` if any EXCLUSIVE lock prefix-
        overlaps with `path`. Used by reassignment / swap / access guards.

        Path semantics: `(root,)` matches any EXCLUSIVE lock rooted at root
        (covers the legacy name-only behavior). A longer path only conflicts
        with locks whose path is a prefix of it or vice versa.
        """
        if not path:
            return None
        target_name = path[0]
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if (
                    entry.name == target_name
                    and entry.lock is not None
                    and entry.lock.lock_type == ZLockState.EXCLUSIVE
                    and _paths_overlap(entry.lock.path, path)
                ):
                    return (entry.lock.path, entry.lock.holder)
                j -= 1
            i -= 1
        return None

    def is_path_locked(self, path: Tuple[str, ...]) -> Optional[LockInfo]:
        """Read-only query: return the innermost LockInfo whose path
        prefix-overlaps `path`, regardless of lock type (SHARED or
        EXCLUSIVE). Returns None if no lock is held.

        Used by lock-escape checks at storage and return sites — a value
        backed by any outstanding lock cannot be transferred into an
        aggregate field or returned to a caller unless the lock source
        transfers with it (e.g. via a `.lock`-annotated parameter).
        """
        if not path:
            return None
        target_name = path[0]
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if (
                    entry.name == target_name
                    and entry.lock is not None
                    and _paths_overlap(entry.lock.path, path)
                ):
                    return entry.lock
                j -= 1
            i -= 1
        return None

    def release_held_locks(self, holder_name: str) -> None:
        """Release all locks whose holder matches holder_name.

        Used before .take/.release to clean up locks before invalidation.
        Searches all scopes and removes matching lock entries.
        """
        # collect all lock entries held by this holder
        to_remove: List[Tuple[int, int]] = []  # (scope_idx, entry_idx)
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if entry.lock is not None and entry.lock.holder == holder_name:
                    to_remove.append((i, j))
                j -= 1
            i -= 1
        # remove in reverse order (highest indices first) to keep indices valid
        for si, ei in to_remove:
            self._scopes[si].entries.pop(ei)

    # ---- narrowing (replaces TypeState) ----

    def narrow(
        self,
        name: str,
        to_type: "ZType",
        subtype_name: str = "",
        shadow: bool = False,
    ) -> None:
        """Narrow a variable to a specific subtype. Pushes overlay entry.

        Two modes:

        * `shadow=True` (match-arm narrowing): the entry's `ztype` is the
          narrowed PAYLOAD type — name resolution returns the payload
          directly, so `r.size` resolves through the normal field-lookup
          path, `match (r)` dispatches on the payload's tag, method calls
          thread `_this` correctly. The outer union/variant is stashed in
          `original_ztype` for the emitter's C-level unwrap (the C storage
          is still the outer struct). For null-payload arms there is no
          payload value to access, so `ztype` stays the original (any field
          access errors cleanly).

        * `shadow=False` (assignment-based narrowing, default): the entry's
          `ztype` stays the OUTER union/variant type — `x: result.ok 42`
          leaves x typed as `result`, so `return x` / passing x to a
          function expecting the union still works. Only `narrowed_subtype`
          records which arm is active (for exhaustiveness / exclusion).
        """
        from ztypes import ZTypeType

        existing = self.lookup_entry(name)
        original_type = existing.ztype if existing else to_type
        if shadow:
            payload = original_type.children.get(subtype_name) if subtype_name else None
            if payload is None or payload.typetype == ZTypeType.NULL:
                # null-payload or missing arm: keep outer as ztype.
                entry_ztype = original_type
            else:
                entry_ztype = payload
        else:
            entry_ztype = original_type
        # Phase 7c: mint narrowed_subtype_id against the outer type so the
        # symbol table exposes an id-addressable handle on the arm.
        nsid = original_type.child_id_for(subtype_name) if subtype_name else None
        entry = Entry(
            name=name,
            ztype=entry_ztype,
            is_definition=False,
            narrowed_subtype=subtype_name if subtype_name else None,
            narrowed_subtype_id=nsid,
            original_ztype=original_type if shadow else None,
        )
        self._scopes[-1].append(entry)

    def exclude(self, name: str, subtype_name: str, full_type: "ZType") -> None:
        """Exclude a subtype from a variable's known type.

        If only one subtype remains, auto-collapses to narrowed_subtype.
        Adds an overlay entry to the current scope.
        """
        from ztypes import ZTypeType, is_tag_origin

        # collect all subtypes of the full union/variant
        all_subtypes = {
            k: v
            for k, v in full_type.children.items()
            if v.typetype
            not in (ZTypeType.FUNCTION, ZTypeType.DATA, ZTypeType.TAG, ZTypeType.ENUM)
            and not is_tag_origin(v.generic_origin)
        }

        # get current exclusions for this variable
        prev_excluded = self.get_excluded(name)
        new_excluded = prev_excluded | {subtype_name}

        remaining = {k: v for k, v in all_subtypes.items() if k not in new_excluded}

        if not remaining:
            # all subtypes excluded — unreachable
            self._scopes[-1].unreachable = True
            return

        narrowed_sub: Optional[str] = None
        if len(remaining) == 1:
            sname, _ = next(iter(remaining.items()))
            narrowed_sub = sname

        # Phase 7c: mint id parallels against the full outer type.
        narrowed_sub_id = full_type.child_id_for(narrowed_sub) if narrowed_sub else None
        excluded_ids = frozenset(full_type.child_id_for(s) for s in new_excluded)

        # exclude() is only called post-match (for arms that exit the
        # scope); the remaining-scope view of the variable is still
        # whole-program value-typed (no shadow). Keep full_type as ztype.
        entry = Entry(
            name=name,
            ztype=full_type,
            is_definition=False,
            narrowed_subtype=narrowed_sub,
            narrowed_subtype_id=narrowed_sub_id,
            excluded_subtypes=frozenset(new_excluded),
            excluded_subtype_ids=excluded_ids,
        )
        self._scopes[-1].append(entry)

    def reset_narrowing(self, name: str) -> None:
        """Clear narrowing for a variable. Pushes overlay with original type."""
        # find the definition entry (the one with the declared type)
        i = len(self._scopes) - 1
        while i >= 0:
            scope = self._scopes[i]
            j = len(scope.entries) - 1
            while j >= 0:
                entry = scope.entries[j]
                if entry.name == name and entry.is_definition:
                    # push overlay that resets narrowing to original type
                    reset_entry = Entry(
                        name=name,
                        ztype=entry.ztype,
                        is_definition=False,
                    )
                    self._scopes[-1].append(reset_entry)
                    return
                j -= 1
            i -= 1

    def is_excluded(self, name: str, subtype_name: str) -> bool:
        """Check if a subtype has been excluded for a variable."""
        excluded = self.get_excluded(name)
        return subtype_name in excluded

    def get_excluded(self, name: str) -> "frozenset[str]":
        """Get the set of excluded subtypes for a variable."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None and entry.excluded_subtypes is not None:
                return entry.excluded_subtypes
            if entry is not None:
                return frozenset()  # found entry but no exclusions
            i -= 1
        return frozenset()

    def get_subtype_name(self, name: str) -> "Optional[str]":
        """Return the subtype name a variable is narrowed to, or None."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None:
                return entry.narrowed_subtype
            i -= 1
        return None

    def mark_unreachable(self) -> None:
        """Mark the current scope as unreachable (all paths diverged)."""
        self._scopes[-1].unreachable = True

    def is_unreachable(self) -> bool:
        """Check if the current scope is unreachable."""
        if not self._scopes:
            return False
        return self._scopes[-1].unreachable

    # ---- utility ----

    def all_names(self) -> List[str]:
        """Return all defined names across all scopes (for did-you-mean suggestions)."""
        names: List[str] = []
        seen: set = set()
        i = len(self._scopes) - 1
        while i >= 0:
            for entry in self._scopes[i].entries:
                if entry.name not in seen and not entry.is_taken:
                    names.append(entry.name)
                    seen.add(entry.name)
            i -= 1
        return names

    def get_live_owned_vars(self) -> set:
        """Return a set of variable names that are live (defined, not taken)
        and have types that need destructors (i.e. owned resources).

        Used to snapshot live variables before if/match arms so we can detect
        which variables are taken in some arms.
        """
        names: set = set()
        taken: set = set()
        i = len(self._scopes) - 1
        while i >= 0:
            for entry in self._scopes[i].entries:
                if entry.name in names or entry.name in taken:
                    continue
                if entry.is_taken:
                    taken.add(entry.name)
                    continue
                if entry.var is not None and entry.ztype.needs_destructor:
                    names.add(entry.name)
            i -= 1
        return names

    @property
    def depth(self) -> int:
        return len(self._scopes)
