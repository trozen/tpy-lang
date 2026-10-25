# `+=` matching CPython unwarned: `__iadd__` record, list, bytearray update the
# shared object; int, str, String and a ValueType record rebind the name.
from tpy import Own, String, ValueType, int32


class A:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, k: int32) -> "A":
        self.n += k
        return self


class P(ValueType):
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[P]":
        return P(self.n + k)


def record_iadd() -> None:
    a = A(1)
    b = a
    # record with `__iadd__`: updated in place, as in CPython
    a += 10  # tpyc: ok
    print("iadd", a.n, b.n, a is b)


def list_iadd() -> None:
    xs = [1, 2]
    ys = xs
    # list `+=` extends the shared list
    xs += [3]  # tpyc: ok
    print("list", xs, ys, xs is ys)


def bytearray_iadd() -> None:
    ba = bytearray(b"ab")
    al = ba
    # bytearray `+=` extends the shared buffer
    ba += b"c"  # tpyc: ok
    print("bytearray", ba, al, ba is al)


def int_iadd() -> None:
    n = 1
    m = n
    # int `+=` rebinds
    n += 10  # tpyc: ok
    print("int", n, m)


def str_iadd() -> None:
    s = "ab"
    t = s
    # str `+=` rebinds
    s += "c"  # tpyc: ok
    print("str", s, t)


def string_iadd() -> None:
    s = String("ab")
    t = s
    # String `+=` appends in place; a String binding is its own value
    s += "c"  # tpyc: ok
    print("String", s, t)


def valuetype_add() -> None:
    a = P(1)
    b = a
    # a ValueType record with only `__add__`: a value, so in place is the rebind
    a += 10  # tpyc: ok
    print("valuetype", a.n, b.n)


def main() -> None:
    record_iadd()
    list_iadd()
    bytearray_iadd()
    int_iadd()
    str_iadd()
    string_iadd()
    valuetype_add()


main()
