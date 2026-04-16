"""
ZeroLang scoped symbol table for the type checker
"""

from typing import Optional, Dict, List, Tuple
from ztypes import ZType, ZVariable, ZLockState, LockEntry


class Scope:
    """
    A single scope level in the symbol table.
    Maps names to their resolved ZType (and optionally ZVariable for ownership).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.symbols: Dict[str, ZType] = {}
        self.variables: Dict[str, ZVariable] = {}
        # taken (invalidated) variables in this scope: name -> (line, col, file_id)
        self.taken: Dict[str, Tuple[int, int, int]] = {}

    def define(self, name: str, ztype: ZType) -> None:
        self.symbols[name] = ztype

    def define_var(self, name: str, var: ZVariable) -> None:
        self.symbols[name] = var.ztype
        self.variables[name] = var

    def lookup(self, name: str) -> Optional[ZType]:
        return self.symbols.get(name)

    def lookup_var(self, name: str) -> Optional[ZVariable]:
        return self.variables.get(name)


class SymbolTable:
    """
    Scoped symbol table — a stack of Scope frames.
    Lookup searches from innermost to outermost.
    """

    def __init__(self) -> None:
        self._scopes: List[Scope] = []

    def push(self, name: str) -> Scope:
        scope = Scope(name)
        self._scopes.append(scope)
        return scope

    def pop(self) -> Scope:
        # release any borrow-scoped locks held by variables in this scope
        # before the scope disappears; otherwise locks placed on outer
        # variables by inner borrows would persist past their holder's
        # lifetime (see doc/ownership.pdoc, "Lock Release on Scope Exit").
        top = self._scopes[-1]
        for holder_name in list(top.variables.keys()):
            self.release_held_locks(holder_name)
        self._scopes.pop()
        # merge taken entries into parent scope so "already taken" errors
        # persist after the inner scope exits
        if self._scopes and top.taken:
            parent = self._scopes[-1]
            for name, loc in top.taken.items():
                if name not in parent.taken:
                    parent.taken[name] = loc
        return top

    def define(self, name: str, ztype: ZType) -> None:
        self._scopes[-1].define(name, ztype)

    def define_var(self, name: str, var: ZVariable) -> None:
        self._scopes[-1].define_var(name, var)

    def lookup(self, name: str) -> Optional[ZType]:
        for scope in reversed(self._scopes):
            t = scope.lookup(name)
            if t is not None:
                return t
        return None

    def lookup_var(self, name: str) -> Optional[ZVariable]:
        for scope in reversed(self._scopes):
            v = scope.lookup_var(name)
            if v is not None:
                return v
        return None

    def invalidate(self, name: str, loc: Optional[Tuple[int, int, int]] = None) -> bool:
        """Mark a variable as consumed (taken). Returns True if found and invalidated.

        loc: optional (line, col, file_id) of the take expression for error reporting.
        """
        for scope in reversed(self._scopes):
            if name in scope.symbols:
                del scope.symbols[name]
                if name in scope.variables:
                    del scope.variables[name]
                if loc is not None:
                    scope.taken[name] = loc
                return True
        return False

    def get_taken_location(self, name: str) -> Optional[Tuple[int, int, int]]:
        """Return the (line, col, file_id) where a variable was taken, or None."""
        for scope in reversed(self._scopes):
            loc = scope.taken.get(name)
            if loc is not None:
                return loc
        return None

    def clear_taken(self, name: str) -> None:
        """Remove the taken record for a name (used by match/case to restore
        a variable between arms)."""
        for scope in reversed(self._scopes):
            if name in scope.taken:
                del scope.taken[name]
                return

    def set_taken_location(self, name: str, loc: Tuple[int, int, int]) -> None:
        """Override the taken location for a name (used by match/case for
        better error reporting)."""
        for scope in reversed(self._scopes):
            if name in scope.taken:
                scope.taken[name] = loc
                return

    def _assert_lock_consistency(self, target_name: str, holder: str) -> None:
        """Debug assertion: verify bidirectional lock consistency."""
        var = self.lookup_var(target_name)
        holder_var = self.lookup_var(holder)
        if var and holder_var:
            has_lock = any(e.holder == holder for e in var.locks)
            has_held = target_name in holder_var.held_locks
            assert has_lock == has_held, (
                f"Lock inconsistency: {target_name}.locks has {holder}={has_lock}, "
                f"{holder}.held_locks has {target_name}={has_held}"
            )

    def try_lock(
        self, target_name: str, lock_type: ZLockState, holder: str
    ) -> Optional[str]:
        """Try to take a lock on target_name. Returns error message or None on success."""
        var = self.lookup_var(target_name)
        if not var:
            return None  # unknown variable, skip lock checking

        for entry in var.locks:
            if lock_type == ZLockState.EXCLUSIVE:
                # exclusive conflicts with any existing lock
                return (
                    f"Cannot take exclusive lock on '{target_name}': "
                    f"already has {entry.lock_type.name.lower()} lock held by '{entry.holder}'"
                )
            if (
                lock_type == ZLockState.SHARED
                and entry.lock_type == ZLockState.EXCLUSIVE
            ):
                return (
                    f"Cannot take shared lock on '{target_name}': "
                    f"already has exclusive lock held by '{entry.holder}'"
                )
            # shared + shared is OK

        entry = LockEntry(lock_type=lock_type, holder=holder)
        var.locks.append(entry)

        # track on holder side for cleanup
        holder_var = self.lookup_var(holder)
        if holder_var:
            holder_var.held_locks.append(target_name)

        if __debug__:
            self._assert_lock_consistency(target_name, holder)
        return None

    def release_lock(self, target_name: str, holder: str) -> None:
        """Release a specific lock held by holder on target_name."""
        var = self.lookup_var(target_name)
        if not var:
            return
        i = 0
        while i < len(var.locks):
            if var.locks[i].holder == holder:
                var.locks.pop(i)
            else:
                i += 1

    def find_exclusive_lock(self, name: str) -> Optional[Tuple[str, str]]:
        """Return (name, holder) if `name` has an outstanding EXCLUSIVE lock,
        else None. Used by mutation sites to reject operations on paths rooted
        at a variable with an active borrow-scoped lock.

        Only EXCLUSIVE locks block mutation. SHARED locks are call-scoped
        (released when the call returns) and do not represent a persisting
        borrow."""
        var = self.lookup_var(name)
        if not var:
            return None
        for entry in var.locks:
            if entry.lock_type == ZLockState.EXCLUSIVE:
                return (name, entry.holder)
        return None

    def release_held_locks(self, holder_name: str) -> None:
        """Release all locks held by a variable (called on scope exit)."""
        var = self.lookup_var(holder_name)
        if not var:
            return
        for target_name in var.held_locks:
            self.release_lock(target_name, holder_name)
        var.held_locks.clear()

    def all_names(self) -> List[str]:
        """Return all defined names across all scopes (for did-you-mean suggestions)."""
        names: List[str] = []
        seen: set = set()
        for scope in reversed(self._scopes):
            for name in scope.symbols:
                if name not in seen:
                    names.append(name)
                    seen.add(name)
        return names

    @property
    def depth(self) -> int:
        return len(self._scopes)
