"""
ZeroLang scoped symbol table for the type checker
"""

from typing import Optional, Dict, List
from ztypechecker import ZType, ZVariable, ZOwnership, ZNaming


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

    @property
    def depth(self) -> int:
        return len(self._scopes)
