"""
ZeroLang scoped symbol table for the type checker
"""

from typing import Optional, Dict, List
from ztypechecker import ZType, ZVariable, ZLockState, LockEntry


class Scope:
    """
    A single scope level in the symbol table.
    Maps names to their resolved ZType (and optionally ZVariable for ownership).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.symbols: Dict[str, ZType] = {}
        self.variables: Dict[str, ZVariable] = {}

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
        return self._scopes.pop()

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

    def invalidate(self, name: str) -> bool:
        """Mark a variable as consumed (taken). Returns True if found and invalidated."""
        for scope in reversed(self._scopes):
            if name in scope.symbols:
                del scope.symbols[name]
                if name in scope.variables:
                    del scope.variables[name]
                return True
        return False

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

        return None

    def release_lock(self, target_name: str, holder: str) -> None:
        """Release a specific lock held by holder on target_name."""
        var = self.lookup_var(target_name)
        if not var:
            return
        var.locks = [e for e in var.locks if e.holder != holder]

    def release_held_locks(self, holder_name: str) -> None:
        """Release all locks held by a variable (called on scope exit)."""
        var = self.lookup_var(holder_name)
        if not var:
            return
        for target_name in var.held_locks:
            self.release_lock(target_name, holder_name)
        var.held_locks.clear()

    @property
    def depth(self) -> int:
        return len(self._scopes)
