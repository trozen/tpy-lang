"""Shared C++ rendering policy for integer literals."""

from __future__ import annotations

from collections.abc import Callable

from ..sema.numeric_lattice import fixed_int_range_contains
from ..type_def_registry import is_big_int_type, is_fixed_int_type
from ..typesys import TpyType


def render_int_literal_value(
        value: int, target_type: TpyType | None, *,
        default_int_type: TpyType,
        type_to_cpp: Callable[[TpyType], str]) -> str:
    """Render one integer literal for an optional target slot."""
    if is_big_int_type(target_type):
        # Exclude INT32_MIN from the bare ctor arm: its positive token is a C++
        # long on some targets, making BigInt overload resolution ambiguous.
        if -(2**31 - 1) <= value <= 2**31 - 1:
            return f"::tpy::BigInt({value})"
        if -2**63 <= value <= 2**63 - 1:
            return f"::tpy::BigInt(static_cast<int64_t>({value}LL))"
        return f'::tpy::BigInt::from_str("{value}")'

    # C++ parses unary minus after choosing the positive token's type, so the
    # signed minimum needs the standard (-MAX - 1) spelling.
    if value > 0x7FFFFFFFFFFFFFFF:
        bare = f"{value}ull"
    elif value == -0x8000000000000000:
        bare = "(-9223372036854775807LL - 1)"
    else:
        bare = str(value)

    if target_type is None or not is_fixed_int_type(target_type):
        return bare
    if target_type is default_int_type:
        return bare
    # Keep naturally fitting int constants bare to avoid conversion warnings.
    if (-2**31 <= value <= 2**31 - 1
            and fixed_int_range_contains(target_type, value)):
        return bare
    return f"static_cast<{type_to_cpp(target_type)}>({bare})"
