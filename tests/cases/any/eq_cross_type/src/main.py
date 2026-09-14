# `==` across two Any cells of DIFFERENT contained types follows Python's
# numeric tower (1 == 1.0 == True), and hash() agrees with it.

from typing import Any
from tpy import int32, int64, float64, String


class P:
    def __init__(self, x: int32) -> None:
        self.x = x

    def __eq__(self, o: "P") -> bool:
        return self.x == o.x


def main() -> None:
    # numeric tower: every pair of widths holding the same value is equal
    i32: Any = int32(1)
    i64: Any = int64(1)
    big: Any = 1
    f64: Any = float64(1.0)
    print("tower", i32 == i64, i32 == big, i32 == f64, big == f64, i64 == f64)  # tpyc: ok
    other: Any = int32(2)
    frac: Any = float64(1.5)
    print("tower-ne", i32 == other, big == frac)

    # bool is an int in the tower; None is not a number
    t: Any = True
    none: Any = None
    print("bool", t == i32, t == f64, t == big)  # tpyc: ok
    print("none", none == i32, none == none)

    # str from a literal, a String and a str LOCAL all store one typeid
    lit: Any = "x"
    owned: Any = String("x")
    v = "x"
    local: Any = v
    print("str", lit == owned, lit == local, owned == local)  # tpyc: ok
    print("str-vs-int", lit == i32)

    # a record compares through its own __eq__
    p: Any = P(3)
    q: Any = P(3)
    r: Any = P(4)
    print("record", p == q, p == r)

    # a list inside Any compares element-wise
    xs: Any = [1, 2]
    ys: Any = [1, 2]
    zs: Any = [1, 3]
    print("list", xs == ys, xs == zs)

    # != is the derivation of ==, across types too
    print("ne", i32 != f64, i32 != other, lit != i32)  # tpyc: ok

    # dict / set keyed on Any dedupe 1, 1.0 and True into one entry because
    # their hashes agree
    d: dict[Any, str] = {}
    d[i32] = "int"
    d[f64] = "float"
    d[t] = "bool"
    print("dict", len(d), d[i32], d[f64], d[t])  # tpyc: ok
    s: set[Any] = set()
    s.add(i32)
    s.add(f64)
    s.add(t)
    s.add(big)
    print("set", len(s))
    print("hash", hash(i32) == hash(f64), hash(i32) == hash(t), hash(big) == hash(f64))  # tpyc: ok

    # int against float compares EXACTLY: the float is decomposed, never the
    # int rounded to a double, so 2**53 + 1 differs from 2.0**53 while 2**70
    # equals 2.0**70 -- and the equal pair hashes alike
    big53: Any = 9007199254740993
    f53: Any = float64(9007199254740992.0)
    p70: Any = 2 ** 70
    fp70: Any = float64(2.0 ** 70)
    print("exact", big53 == f53, big53 != f53, p70 == fp70, hash(p70) == hash(fp70))  # tpyc: ok
    # A FIXED-WIDTH int cell is exact at the same boundary: Any promotes it
    # to the numeric tower, so it must not answer through a rounded double
    i64_53: Any = int64(9007199254740993)
    print("exact-int64", i64_53 == f53, i64_53 != f53)  # tpyc: ok

    # an int hashes by value, not by width or encoding: 2**62 as int64 and as
    # int (which no longer fits int's inline word) is one dict key
    i62: Any = int64(4611686018427387904)
    big62: Any = 4611686018427387904
    print("hash-by-value", i62 == big62, hash(i62) == hash(big62))  # tpyc: ok


main()
