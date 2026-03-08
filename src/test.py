"""
testing
"""
import sys
from typing import Dict, Optional, Iterator, Tuple, NewType, cast, Callable
from dataclasses import dataclass, field
from itertools import count

MID = NewType("MID", int)


@dataclass
class My:
    """
    test class with autoincrementing id
    """

    mid: MID = field(
        default_factory=cast(Callable[[], MID], count().__next__), init=False
    )
    # mid2: MID = field(default_factory=count().__next__, init=False)
    reveal_type(id)

m = My()
reveal_type(m.mid)

print(repr(sys.argv))


class ForwardRefs:
    """
    ForwardRefs - manage a dict of forwardrefs
    """

    def __init__(self):
        self.refs: Dict[str, Optional[str]] = {}

    def __iter__(self) -> Iterator[Tuple[str, Optional[str]]]:
        return iter(self.refs.items())

    def __len__(self) -> int:
        return len(self.refs)

    def update(self, other: "ForwardRefs") -> None:
        """
        update - add each of the forwardrefs in values to this instance,
            skipping ones that are already in the current instance
        """
        for n, t in other:
            if n in self.refs:
                continue
            self.refs[n] = t

    def add(self, name: str, tok: Optional[str]) -> None:
        """
        add - add the given forwardref if this name does not already exist in
            this instance
        """
        if name not in self.refs:
            self.refs[name] = tok
