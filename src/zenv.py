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
        self, target_name: str, lock_type: ZLockState, holder: str
    ) -> Optional[str]:
        """Try to take a lock on target_name. Returns error message or None on success.

        Searches the scope chain for existing locks. On success, appends a lock
        Entry to the current scope.
        """
        # check the target exists as a variable
        target_var = self.lookup_var(target_name)
        if target_var is None:
            return None  # unknown variable, skip lock checking

        # check for conflicts in scope chain
        existing = self.find_lock(target_name)
        if existing is not None:
            if lock_type == ZLockState.EXCLUSIVE:
                return (
                    f"Cannot take exclusive lock on '{target_name}': "
                    f"already has {existing.lock_type.name.lower()} lock held by '{existing.holder}'"
                )
            if (
                lock_type == ZLockState.SHARED
                and existing.lock_type == ZLockState.EXCLUSIVE
            ):
                return (
                    f"Cannot take shared lock on '{target_name}': "
                    f"already has exclusive lock held by '{existing.holder}'"
                )
            # shared + shared: already locked at this level, no need to add another
            if (
                lock_type == ZLockState.SHARED
                and existing.lock_type == ZLockState.SHARED
            ):
                return None

        # add lock entry to current scope
        lock_entry = Entry(
            name=target_name,
            ztype=target_var.ztype,
            is_definition=False,
            lock=LockInfo(lock_type=lock_type, holder=holder),
        )
        self._scopes[-1].append(lock_entry)
        return None

    def find_lock(self, name: str) -> Optional[LockInfo]:
        """Search scope chain for any lock on name. Returns LockInfo or None."""
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

    def find_exclusive_lock(self, name: str) -> Optional[Tuple[str, str]]:
        """Return (name, holder) if name has an outstanding EXCLUSIVE lock.

        Compatibility wrapper around find_lock for existing callers.
        """
        lock = self.find_lock(name)
        if lock is not None and lock.lock_type == ZLockState.EXCLUSIVE:
            return (name, lock.holder)
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

    def narrow(self, name: str, to_type: "ZType", subtype_name: str = "") -> None:
        """Narrow a variable to a specific subtype. Pushes overlay entry.

        The entry keeps the ORIGINAL declared type in ztype (so name resolution
        continues to return the union/variant type). The narrowed_subtype field
        records which subtype the variable is known to be.
        """
        # find the original declared type for this variable
        existing = self.lookup_entry(name)
        original_type = existing.ztype if existing else to_type
        entry = Entry(
            name=name,
            ztype=original_type,
            is_definition=False,
            narrowed_subtype=subtype_name if subtype_name else None,
        )
        self._scopes[-1].append(entry)

    def exclude(self, name: str, subtype_name: str, full_type: "ZType") -> None:
        """Exclude a subtype from a variable's known type.

        If only one subtype remains, auto-collapses to narrowed_subtype.
        Adds an overlay entry to the current scope.
        """
        from ztypes import ZTypeType, TAG_ORIGIN

        # collect all subtypes of the full union/variant
        all_subtypes = {
            k: v
            for k, v in full_type.children.items()
            if v.typetype
            not in (ZTypeType.FUNCTION, ZTypeType.DATA, ZTypeType.TAG, ZTypeType.ENUM)
            and getattr(v, "generic_origin", None) is not TAG_ORIGIN
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

        entry = Entry(
            name=name,
            ztype=full_type,  # keep original type for name resolution
            is_definition=False,
            narrowed_subtype=narrowed_sub,
            excluded_subtypes=frozenset(new_excluded),
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

    def lookup_narrowed(self, name: str) -> "Optional[ZType]":
        """Return the narrowed type for a name, or None if not narrowed."""
        i = len(self._scopes) - 1
        while i >= 0:
            entry = self._scopes[i].find(name)
            if entry is not None:
                if entry.narrowed_subtype is not None:
                    return entry.ztype
                return None  # found an entry but not narrowed
            i -= 1
        return None

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

    @property
    def depth(self) -> int:
        return len(self._scopes)
