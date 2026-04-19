"""
Shared type helper functions for the type checker and emitter.

These check properties of monomorphized generic types (array, str, list, map)
and extract their type parameters.
"""

from typing import Optional
from ztypes import ZType, ZSubType, TAG_ORIGIN


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
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "array"
    )


def array_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of an array type."""
    base = _unwrap_typedef(ztype)
    return base.generic_args.get("of") if base else None


def array_length(ztype: ZType) -> Optional[int]:
    """Get the length of an array type."""
    base = _unwrap_typedef(ztype)
    if base is None:
        return None
    to_arg = base.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def is_str_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized str type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "str"
    )


def str_capacity(ztype: ZType) -> Optional[int]:
    """Get the capacity of a str type."""
    base = _unwrap_typedef(ztype)
    if base is None:
        return None
    to_arg = base.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def is_list_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized list type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "list"
    )


def list_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of a list type."""
    base = _unwrap_typedef(ztype)
    return base.generic_args.get("of") if base else None


def is_listview_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized listview type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "listview"
    )


def listview_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of a listview type."""
    base = _unwrap_typedef(ztype)
    return base.generic_args.get("of") if base else None


def is_map_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized map type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "map"
    )


def map_key_type(ztype: ZType) -> Optional[ZType]:
    """Get the key type of a map type."""
    base = _unwrap_typedef(ztype)
    return base.generic_args.get("key") if base else None


def map_value_type(ztype: ZType) -> Optional[ZType]:
    """Get the value type of a map type."""
    base = _unwrap_typedef(ztype)
    return base.generic_args.get("value") if base else None


def is_stringview_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is the stringview type."""
    ztype = _unwrap_typedef(ztype)
    if not ztype:
        return False
    return ztype.subtype == ZSubType.STRINGVIEW
