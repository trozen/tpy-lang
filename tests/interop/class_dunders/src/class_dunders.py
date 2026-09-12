# tpy: ext_module
# Exposed-class dunders: __repr__ -> Py_tp_repr, __eq__/__ne__/__lt__ ->
# Py_tp_richcompare, __hash__ -> Py_tp_hash. Vec2 defines
# __eq__/__lt__/__hash__ but not __ne__ (auto-derived from __eq__, like
# CPython's own object.__ne__). Frac (__eq__ only) and Ordered (__lt__ only)
# both become unhashable once exposed, no __hash__ defined -- source-parity
# for Frac (a plain Python class overriding __eq__ without __hash__ also
# unhashes), but an ext-only divergence for Ordered (a plain Python class
# overriding only __lt__ stays hashable; PyType_FromSpec has no way to tell
# which specific comparison dunder populated tp_richcompare, so it nulls the
# hash whenever richcompare is populated at all -- see ext_checks.py).
from __future__ import annotations
from tpy import int64, uint64
from tpy.extern import export


@export
class Vec2:
    def __init__(self, x: int64, y: int64):
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return f"Vec2({self.x}, {self.y})"

    def __eq__(self, other: Vec2) -> bool:
        return self.x == other.x and self.y == other.y

    def __lt__(self, other: Vec2) -> bool:
        return self.x < other.x or (self.x == other.x and self.y < other.y)

    def __hash__(self) -> uint64:
        return uint64(self.x * 31 + self.y)


@export
class Frac:
    def __init__(self, num: int64, den: int64):
        self.num = num
        self.den = den

    def __eq__(self, other: Frac) -> bool:
        return self.num == other.num and self.den == other.den


@export
class Ordered:
    def __init__(self, rank: int64):
        self.rank = rank

    def __lt__(self, other: Ordered) -> bool:
        return self.rank < other.rank
