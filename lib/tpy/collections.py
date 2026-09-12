# CPython-compatible collections.Counter (pure TPy) over dict[T, int].
#
# v1 surface: Counter() / Counter(iterable), c[key] (missing -> 0), len, `in`,
# most_common(n), update/subtract, total. The arithmetic / set operators
# (+, -, &, |) and elements() are deferred on filed compiler blockers (see
# BUGS.md / TODO.md); use update/subtract for accumulation.
#
# Keys are copied into the Counter (CPython aliases the key object); counts are
# `int` (arbitrary precision), matching CPython.
#
# DIVERGENCE: Counter(mapping) counts the mapping's KEYS (a dict is an
# Iterable over its keys), it does NOT read the values as counts the way
# CPython does -- `Counter({"a": 3})` yields a=1, not a=3. To seed counts from
# a mapping, build empty and assign: `c = Counter(); c[k] = n`. (A general
# "dict passed where an element-iterable is expected" warning is filed in
# TODO.md; until it lands this is silent.) update()/subtract() take another
# Counter[T] only, not an arbitrary iterable / kwargs (CPython accepts both).
#
# tpy: cpp_namespace("tpystd::collections")
from typing import Iterable
from tpy import Own, int32, Hashable, copy


class Counter[T: Hashable]:
    _data: dict[T, int]

    def __init__(self, items: Iterable[T] | None = None) -> None:
        self._data = {}
        if items is not None:
            for x in items:
                self._data[x] = self._data.get(x, 0) + 1

    def __getitem__(self, key: T) -> int:
        # Missing key returns 0 without inserting (unlike defaultdict).
        return self._data.get(key, 0)

    def __setitem__(self, key: T, count: int) -> None:
        self._data[key] = count

    def __len__(self) -> int32:
        return len(self._data)

    def __contains__(self, key: T) -> bool:
        return key in self._data

    def total(self) -> int:
        s = 0
        for k in self._data:
            s = s + self._data[k]
        return s

    def most_common(self, n: int32) -> Own[list[tuple[T, int]]]:
        pairs: list[tuple[T, int]] = []
        for k in self._data:
            pairs.append((copy(k), self._data[k]))
        ranked = sorted(pairs, key=lambda kv: -kv[1])
        out: list[tuple[T, int]] = []
        i = 0
        while i < n and i < len(ranked):
            out.append(ranked[i])
            i += 1
        return out

    def update(self, other: Counter[T]) -> None:
        for k in other._data:
            self._data[copy(k)] = self._data.get(k, 0) + other._data[k]

    def subtract(self, other: Counter[T]) -> None:
        for k in other._data:
            self._data[copy(k)] = self._data.get(k, 0) - other._data[k]
