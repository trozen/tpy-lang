# PStr[N] -- Pascal-flavoured fixed-capacity string for the Pascal
# frontend plugin's runtime.
#
# Built from scratch on UninitArrayStorage[char, N] rather than wrapping
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
# @overload variants for `str`, `StrView`, `PStr[N]`, and `char` (mirror
# `str.__add__` in lib/tpy/tpy/_builtins/_types.py). The translator
# currently funnels PStr operands through `str(...)` at call sites in
# `_coerce_string_operand` / `_lower_string_assign` to satisfy the
# single `other: str` overload; widening the runtime surface lets that
# wrapping go away. Open question: result capacity for cross-capacity
# concat (`PStr[10] + PStr[20]`) -- prefer `Own[PStr[N]]` matching the
# self-side capacity, but verify TPy's overload-resolution behaviour
# for generic-class methods first.

from __future__ import annotations
from tpy import int32, uint32, Own, char, StrView
from tpy.unsafe import unsafe_load, unsafe_str_view
from tpy.mem import UninitArrayStorage


class PStr[N: int]:
    _storage: UninitArrayStorage[char, N]
    _size: int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[char, N]()
        self._size = 0

    def __del__(self) -> None:
        for i in range(self._size):
            self._storage.drop(uint32(i))

    def __copy__(self) -> Own[PStr[N]]:
        result = PStr[N]()
        for i in range(self._size):
            result.append(self._storage.load(uint32(i)))
        return result

    def __move__(self, other: Own[PStr[N]]) -> None:
        # The @nomove storage can't move itself; the owner relocates.
        self._storage.relocate_from(other._storage, uint32.trunc(other._size))
        self._size = other._size

    def append(self, c: char) -> None:
        self._storage.init(uint32(self._size), c)
        self._size += 1

    def __len__(self) -> int32:
        return self._size

    def __getitem__(self, index: int32) -> char:
        return self._storage.load(uint32(index))

    def __setitem__(self, index: int32, value: char) -> None:
        self._storage.drop(uint32(index))
        self._storage.init(uint32(index), value)

    def __str__(self) -> StrView:
        return unsafe_str_view(self._storage.ptr(), uint32(self._size))

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(uint32(i))
        self._size = 0

    def assign(self, s: str) -> None:
        """Overwrite the buffer with `s`. Pascal `var := literal`
        assignment lowers to a call to this method."""
        cap: int32 = int32(N)
        if len(s) > cap:
            raise ValueError("string literal exceeds PStr capacity")
        self.clear()
        for i in range(len(s)):
            self.append(s[i])

    def __eq__(self, other: str) -> bool:
        if self._size != len(other):
            return False
        for i in range(self._size):
            if self._storage.load(uint32(i)) != other[i]:
                return False
        return True

    def __ne__(self, other: str) -> bool:
        return not self.__eq__(other)

    def __add__(self, other: str) -> Own[PStr[N]]:
        """Concatenation. Result capacity matches `self`; overflowing
        the buffer raises at runtime. Pascal `s := s + suffix` gets the
        new PStr by ownership transfer."""
        total = self._size + int32(len(other))
        cap: int32 = int32(N)
        if total > cap:
            raise ValueError("PStr concatenation overflow")
        result = PStr[N]()
        for i in range(self._size):
            result.append(self._storage.load(uint32(i)))
        for i in range(len(other)):
            result.append(other[i])
        return result
