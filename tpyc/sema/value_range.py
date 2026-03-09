"""
ValueRange -- tracks provable integer value constraints.

Represents a [lo, hi] interval with optional non_zero flag and symbolic
upper bound (hi_len_of). Used for bounds check elision and division-by-zero
check elision.

Frozen dataclass so instances can be stored in FlowFacts frozensets.
"""

from __future__ import annotations

from dataclasses import dataclass

# INT32 bounds (loop indices and len() return Int32)
_INT32_MAX = 2**31 - 1
_INT32_MIN = -(2**31)


@dataclass(frozen=True, slots=True)
class ValueRange:
    """Proven integer value constraints for a variable.

    lo/hi: concrete bounds (None = unbounded in that direction).
    non_zero: True if the value is provably != 0.
    hi_len_of: when set, hi is symbolically len(var_name) - 1.
    """

    lo: int | None = None
    hi: int | None = None
    non_zero: bool = False
    hi_len_of: str | None = None

    # -- Factories --

    @staticmethod
    def from_literal(value: int) -> ValueRange:
        return ValueRange(lo=value, hi=value, non_zero=(value != 0))

    @staticmethod
    def from_len() -> ValueRange:
        """Result of len() -- always non-negative."""
        return ValueRange(lo=0, hi=_INT32_MAX)

    @staticmethod
    def non_negative() -> ValueRange:
        return ValueRange(lo=0)

    @staticmethod
    def only_non_zero() -> ValueRange:
        return ValueRange(non_zero=True)

    @staticmethod
    def for_range_index(stop_len_of: str | None = None,
                        stop_literal: int | None = None) -> ValueRange:
        """Range fact for a for-loop variable iterating over range().

        stop_len_of: container name when stop is len(container).
        stop_literal: concrete stop value when known.
        """
        if stop_len_of is not None:
            return ValueRange(lo=0, hi_len_of=stop_len_of)
        if stop_literal is not None:
            if stop_literal <= 0:
                # Empty range -- loop body never executes, but if it did
                # the variable would have no valid value. Use [0, -1].
                return ValueRange(lo=0, hi=-1)
            return ValueRange(lo=0, hi=stop_literal - 1)
        return ValueRange(lo=0)

    # -- Queries --

    def is_non_negative(self) -> bool:
        return self.lo is not None and self.lo >= 0

    def is_bounded_by_len(self, container: str) -> bool:
        """True if hi is symbolically <= len(container) - 1."""
        return self.hi_len_of is not None and self.hi_len_of == container

    # -- Combinators --

    @staticmethod
    def merge(a: ValueRange, b: ValueRange) -> ValueRange:
        """Branch join (union): widens to cover both possibilities."""
        if a.lo is None or b.lo is None:
            lo = None
        else:
            lo = min(a.lo, b.lo)

        # Symbolic bounds only preserved when both agree
        if a.hi_len_of is not None and a.hi_len_of == b.hi_len_of:
            return ValueRange(
                lo=lo,
                hi_len_of=a.hi_len_of,
                non_zero=a.non_zero and b.non_zero,
            )

        if a.hi is None or b.hi is None:
            hi = None
        else:
            hi = max(a.hi, b.hi)

        return ValueRange(
            lo=lo,
            hi=hi,
            non_zero=a.non_zero and b.non_zero,
        )

    @staticmethod
    def intersect(a: ValueRange, b: ValueRange) -> ValueRange:
        """And-composition: tightens to what both prove."""
        if a.lo is None:
            lo = b.lo
        elif b.lo is None:
            lo = a.lo
        else:
            lo = max(a.lo, b.lo)

        # Keep symbolic bound only when both sides agree (or one is absent)
        if a.hi_len_of is not None and b.hi_len_of is not None:
            hi_len_of = a.hi_len_of if a.hi_len_of == b.hi_len_of else None
        else:
            hi_len_of = a.hi_len_of or b.hi_len_of

        if hi_len_of is not None:
            return ValueRange(
                lo=lo,
                hi_len_of=hi_len_of,
                non_zero=a.non_zero or b.non_zero,
            )

        if a.hi is None:
            hi = b.hi
        elif b.hi is None:
            hi = a.hi
        else:
            hi = min(a.hi, b.hi)

        return ValueRange(
            lo=lo,
            hi=hi,
            non_zero=a.non_zero or b.non_zero,
        )

    def with_non_zero(self, nz: bool = True) -> ValueRange:
        """Return a copy with non_zero set."""
        if self.non_zero == nz:
            return self
        return ValueRange(
            lo=self.lo, hi=self.hi,
            non_zero=nz, hi_len_of=self.hi_len_of,
        )

    def with_lo(self, lo: int) -> ValueRange:
        """Return a copy with lo tightened (only if stricter)."""
        current_lo = self.lo
        if current_lo is not None and current_lo >= lo:
            return self
        nz = self.non_zero or lo > 0
        return ValueRange(
            lo=lo, hi=self.hi,
            non_zero=nz, hi_len_of=self.hi_len_of,
        )

    def with_hi(self, hi: int) -> ValueRange:
        """Return a copy with hi tightened (only if stricter)."""
        current_hi = self.hi
        if current_hi is not None and current_hi <= hi:
            return self
        nz = self.non_zero or (self.lo is not None and self.lo > 0)
        return ValueRange(
            lo=self.lo, hi=hi,
            non_zero=nz, hi_len_of=None,
        )

    def with_hi_len_of(self, container: str) -> ValueRange:
        """Return a copy with symbolic upper bound."""
        return ValueRange(
            lo=self.lo, hi=None,
            non_zero=self.non_zero, hi_len_of=container,
        )
