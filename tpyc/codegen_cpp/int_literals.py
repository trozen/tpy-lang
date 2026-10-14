"""Shared C++ rendering policy for integer literals."""

from __future__ import annotations

from collections.abc import Callable

from ..sema.numeric_lattice import fixed_int_range_contains
from ..type_def_registry import int_traits_of, is_big_int_type, is_fixed_int_type
from ..typesys import BIGINT, INT32, INT64, UINT64, TpyType

_INT32_MAX = 2**31 - 1
_INT32_MIN = -(2**31)
_INT64_MAX = 2**63 - 1
_INT64_MIN = -(2**63)
_UINT64_MAX = 2**64 - 1


def target_less_int_literal_type(value: int) -> TpyType:
    """The C++ type of `value` rendered with no target slot: `int32` while
    it fits one, else `int64` / `uint64`, else `int` (BigInt)."""
    if -_INT32_MAX <= value <= _INT32_MAX:
        return INT32
    if _INT64_MIN <= value <= _INT64_MAX:
        return INT64
    if _INT64_MAX < value <= _UINT64_MAX:
        return UINT64
    return BIGINT


def render_int_literal_value(
        value: int, target_type: TpyType | None, *,
        default_int_type: TpyType,
        type_to_cpp: Callable[[TpyType], str],
        exact_type: bool = False) -> str:
    """Render one integer literal for an optional target slot.

    `exact_type`: the render must have a fixed-width target's own C++ type,
    not merely convert to it -- where C++ chooses among overloads by it."""
    if is_big_int_type(target_type):
        # Exclude INT32_MIN from the bare ctor arm: its positive token is a C++
        # long on some targets, making BigInt overload resolution ambiguous.
        if -_INT32_MAX <= value <= _INT32_MAX:
            return f"::tpy::BigInt({value})"
        # C++ picks the positive token's type before applying unary minus, so
        # INT64_MIN's positive token (2**63) overflows signed long long -- spell
        # it (-MAX - 1), as the fixed-int path below does.
        if value == _INT64_MIN:
            return "::tpy::BigInt(static_cast<int64_t>(-9223372036854775807LL - 1))"
        if _INT64_MIN <= value <= _INT64_MAX:
            return f"::tpy::BigInt(static_cast<int64_t>({value}LL))"
        return f'::tpy::BigInt::from_str("{value}")'

    # C++ parses unary minus after choosing the positive token's type, so the
    # signed minimum needs the standard (-MAX - 1) spelling.
    if value > _INT64_MAX:
        bare = f"{value}ull"
    elif value == _INT64_MIN:
        bare = "(-9223372036854775807LL - 1)"
    else:
        bare = str(value)

    if target_type is None or not is_fixed_int_type(target_type):
        # A target-less literal must render a C++ type that is EXACTLY one of
        # BigInt's ctor param types (int32_t/int64_t/uint64_t) so an implicit
        # BigInt conversion binds one ctor unambiguously. A suffix alone is not
        # enough: a bare `long` token is exact only where int64_t == long
        # (Linux, ambiguous on macOS), and a `ull` token is `unsigned long long`
        # -- exact only where uint64_t == unsigned long long (macOS, ambiguous
        # on Linux where uint64_t == unsigned long). So pin the width with an
        # explicit static_cast by value range:
        #   |v| <= int32:      bare int (exact BigInt(int32_t) everywhere);
        #   int32 < |v| <= int64:  static_cast<int64_t> (identity into an
        #                          int64 slot);
        #   int64 < v <= uint64:   static_cast<uint64_t> (identity into a
        #                          uint64 slot);
        #   beyond uint64 / below int64-min: no fixed type holds it, so it is
        #                          necessarily BigInt -> from_str.
        # This also fires for a known NON-numeric target (the is_big_int arm
        # above already returned), so e.g. an int literal into a float slot gets
        # an inner static_cast<int64_t> that the outer numeric cast then wraps --
        # a harmless identity nesting, intentionally not special-cased.
        own = target_less_int_literal_type(value)
        if own is INT32:
            return bare
        if own is BIGINT:
            return f'::tpy::BigInt::from_str("{value}")'
        return f"static_cast<{type_to_cpp(own)}>({bare})"
    if exact_type:
        # A bare token in int range is `int`, which is `int32_t` everywhere.
        traits = int_traits_of(target_type)
        if (traits is not None and traits.bits == 32 and traits.signed
                and -_INT32_MAX <= value <= _INT32_MAX):
            return bare
        return f"static_cast<{type_to_cpp(target_type)}>({bare})"
    if target_type is default_int_type:
        return bare
    # Keep naturally fitting int constants bare to avoid conversion warnings.
    if (_INT32_MIN <= value <= _INT32_MAX
            and fixed_int_range_contains(target_type, value)):
        return bare
    return f"static_cast<{type_to_cpp(target_type)}>({bare})"
