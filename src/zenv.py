"""
ZeroLang scoped symbol table for the type checker
"""

from typing import Optional, Dict, List
from ztypechecker import ZType


class Scope:
    """
    A single scope level in the symbol table.
    Maps names to their resolved ZType.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.symbols: Dict[str, ZType] = {}

    def define(self, name: str, ztype: ZType) -> None:
        self.symbols[name] = ztype

    def lookup(self, name: str) -> Optional[ZType]:
        return self.symbols.get(name)


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

    def lookup(self, name: str) -> Optional[ZType]:
        for scope in reversed(self._scopes):
            t = scope.lookup(name)
            if t is not None:
                return t
        return None

    @property
    def depth(self) -> int:
        return len(self._scopes)
