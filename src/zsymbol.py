"""
Symbol definition and symbol table
"""

from dataclasses import dataclass  # , field
from ztypes import ZType


@dataclass
class ZSymbol:
    """
    symbol - a particular instance
    """

    type: ZType
    # kind?: local, parameter etc...?
