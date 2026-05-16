"""
Shared type helper functions for the type checker and emitter.

These check properties of monomorphized generic types (array, str, list, map)
and extract their type parameters.
"""

from typing import Optional, TYPE_CHECKING, cast
from ztypes import ZType, ZSubType

if TYPE_CHECKING:
    from ztyping import ZTyping


def is_numeric_id(name: str) -> bool:
    """Check if an identifier is a numeric literal."""
    c0 = name[0]
    return c0.isdigit() or (c0 in ("+", "-") and len(name) > 1 and name[1].isdigit())


def _unwrap_typedef(ztype: Optional[ZType]) -> Optional[ZType]:
    """Follow the typedef chain to its concrete base, if any.

    Typedef classes (e.g. `bytes` over `list of: u8`) carry a
    `typedef_base` pointer. Any shape predicate on a typedef wrapper
    should delegate to the base, because the emitter treats the
    wrapper transparently — same C layout, same methods.
    """
    seen = set()
    while ztype is not None and ztype.typedef_base is not None:
        if id(ztype) in seen:
            return ztype  # cycle guard (defensive; should not happen)
        seen.add(id(ztype))
        ztype = ztype.typedef_base
    return ztype


def is_array_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized array type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "array"


def array_element_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the element type of an array type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "of") if base else None


def array_length(typing: "ZTyping", ztype: ZType) -> Optional[int]:
    """Get the length of an array type."""
    base = _unwrap_typedef(ztype)
    if base is None:
        return None
    to_arg = typing.generic_arg_of(base, "to")
    if to_arg and to_arg.const_value is not None:
        return cast(int, to_arg.const_value)
    return None


def is_str_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized str type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "str"


def str_capacity(typing: "ZTyping", ztype: ZType) -> Optional[int]:
    """Get the capacity of a str type."""
    base = _unwrap_typedef(ztype)
    if base is None:
        return None
    to_arg = typing.generic_arg_of(base, "to")
    if to_arg and to_arg.const_value is not None:
        return cast(int, to_arg.const_value)
    return None


def is_list_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized list type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "List"


def list_element_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the element type of a list type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "of") if base else None


def is_listview_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized listview type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "ListView"


def listview_element_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the element type of a listview type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "of") if base else None


def is_listiter_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized listiter type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "ListIter"


def listiter_element_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the element type of a listiter type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "of") if base else None


def is_mapkeyiter_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized mapkeyiter type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None and ztype.generic_origin.name == "MapKeyIter"
    )


def mapkeyiter_key_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the key type of a mapkeyiter type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "key") if base else None


def is_mapitemiter_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized mapitemiter type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None and ztype.generic_origin.name == "MapItemIter"
    )


def mapitemiter_key_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the key type of a mapitemiter type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "key") if base else None


def mapitemiter_value_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the value type of a mapitemiter type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "value") if base else None


def is_mapentry_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized mapentry type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "MapEntry"


def mapentry_key_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the key type of a mapentry type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "key") if base else None


def mapentry_value_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the value type of a mapentry type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "value") if base else None


def is_map_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized map type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.generic_origin is not None and ztype.generic_origin.name == "Map"


def map_key_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the key type of a map type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "key") if base else None


def map_value_type(typing: "ZTyping", ztype: ZType) -> Optional[ZType]:
    """Get the value type of a map type."""
    base = _unwrap_typedef(ztype)
    return typing.generic_arg_of(base, "value") if base else None


def is_stringview_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is the stringview type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.subtype == ZSubType.STRINGVIEW
