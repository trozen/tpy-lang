# PStr[N] -- Pascal-flavoured fixed-capacity string for the Pascal
# frontend plugin's runtime.
#
# Built from scratch on UninitArrayStorage[Char, N] rather than wrapping
# TPy's FixStr so the Pascal runtime stays self-contained and can grow
# Pascal-specific behaviour (string concat semantics, comparison rules,
# in-place assignment, future format / I/O hooks) without touching the
# shared TPy stdlib.
#
# Indexing is 0-based at the C++ level; the Pascal translator subtracts
# the 1-based offset at the source-language indexing site, matching how
# it handles array variables.
#
# TODO (M6 follow-up): give __add__, __eq__, __ne__, and assign explicit
# @overload variants for `str`, `StrView`, `PStr[N]`, and `Char` (mirror
# `str.__add__` in lib/tpy/tpy/_builtins/_types.py). The translator
# currently funnels PStr operands through `str(...)` at call sites in
# `_coerce_string_operand` / `_lower_string_assign` to satisfy the
# single `other: str` overload; widening the runtime surface lets that
# wrapping go away. Open question: result capacity for cross-capacity
# concat (`PStr[10] + PStr[20]`) -- prefer `Own[PStr[N]]` matching the
# self-side capacity, but verify TPy's overload-resolution behaviour
# for generic-class methods first.

from __future__ import annotations
from tpy import Int32, UInt32, Own, Char, StrView
from tpy.unsafe import unsafe_load, unsafe_str_view
from tpy.mem import UninitArrayStorage


class PStr[N: int]:
    _storage: UninitArrayStorage[Char, N]
    _size: Int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[Char, N]()
        self._size = 0

    def __del__(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))

    def __copy__(self) -> Own[PStr[N]]:
        result = PStr[N]()
        for i in range(self._size):
            result.append(self._storage.load(UInt32(i)))
        return result

    def append(self, c: Char) -> None:
        self._storage.init(UInt32(self._size), c)
        self._size += 1

    def __len__(self) -> Int32:
        return self._size

    def __getitem__(self, index: Int32) -> Char:
        return self._storage.load(UInt32(index))

    def __setitem__(self, index: Int32, value: Char) -> None:
        self._storage.drop(UInt32(index))
        self._storage.init(UInt32(index), value)

    def __str__(self) -> StrView:
        return unsafe_str_view(self._storage.ptr(), UInt32(self._size))

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))
        self._size = 0

    def assign(self, s: str) -> None:
        """Overwrite the buffer with `s`. Pascal `var := literal`
        assignment lowers to a call to this method."""
        cap: Int32 = Int32(N)
        if len(s) > cap:
            raise ValueError("string literal exceeds PStr capacity")
        self.clear()
        for i in range(len(s)):
            self.append(s[i])

    def __eq__(self, other: str) -> bool:
        if self._size != len(other):
            return False
        for i in range(self._size):
            if self._storage.load(UInt32(i)) != other[i]:
                return False
        return True

    def __ne__(self, other: str) -> bool:
        return not self.__eq__(other)

    def __add__(self, other: str) -> Own[PStr[N]]:
        """Concatenation. Result capacity matches `self`; overflowing
        the buffer raises at runtime. Pascal `s := s + suffix` gets the
        new PStr by ownership transfer."""
        total = self._size + Int32(len(other))
        cap: Int32 = Int32(N)
        if total > cap:
            raise ValueError("PStr concatenation overflow")
        result = PStr[N]()
        for i in range(self._size):
            result.append(self._storage.load(UInt32(i)))
        for i in range(len(other)):
            result.append(other[i])
        return result
