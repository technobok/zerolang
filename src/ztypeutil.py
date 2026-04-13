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


def is_array_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized array type."""
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "array"
    )


def array_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of an array type."""
    return ztype.generic_args.get("of")


def array_length(ztype: ZType) -> Optional[int]:
    """Get the length of an array type."""
    to_arg = ztype.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def is_str_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized str type."""
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "str"
    )


def str_capacity(ztype: ZType) -> Optional[int]:
    """Get the capacity of a str type."""
    to_arg = ztype.generic_args.get("to")
    if to_arg and to_arg.numeric_value is not None:
        return to_arg.numeric_value
    return None


def is_list_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized list type."""
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "list"
    )


def list_element_type(ztype: ZType) -> Optional[ZType]:
    """Get the element type of a list type."""
    return ztype.generic_args.get("of")


def is_map_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is a monomorphized map type."""
    if not ztype:
        return False
    return (
        ztype.generic_origin is not None
        and ztype.generic_origin is not TAG_ORIGIN
        and ztype.generic_origin.name == "map"
    )


def map_key_type(ztype: ZType) -> Optional[ZType]:
    """Get the key type of a map type."""
    return ztype.generic_args.get("key")


def map_value_type(ztype: ZType) -> Optional[ZType]:
    """Get the value type of a map type."""
    return ztype.generic_args.get("value")


def is_stringview_type(ztype: Optional[ZType]) -> bool:
    """Check if a type is the stringview type."""
    if not ztype:
        return False
    return ztype.subtype == ZSubType.STRINGVIEW
