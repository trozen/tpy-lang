# Inverse guard for the borrow-return aliasing: Own-returning dunders (user
# and builtin) keep VALUE semantics -- the operator result is a fresh object,
# so mutating it never touches the operands. Also pins the explicit
# .__add__() spelling: list/bytearray follow the Own stub contract (fresh
# owned concat, not a borrow of the receiver); bytes returns are fresh by
# construction (bytes is a value type).
from tpy import Int32, Own


class Acc:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def __add__(self, o: "Acc") -> Own["Acc"]:
        return Acc(self.n + o.n)


def test_own_dunder_fresh():
    a = Acc(3)
    b = Acc(4)
    c = a + b
    print(c.n)
    c.n = 100
    print(a.n, b.n)


def test_own_augassign_fallback():
    # `+=` with no __iadd__ falls back to the Own-returning __add__: a fresh
    # value lands in `a`, matching CPython's rebind-to-fresh-object (the
    # borrow-returning fallback is rejected -- error_augassign_borrow_fallback).
    a = Acc(1)
    b = Acc(2)
    a += b
    print(a.n, b.n)


def test_list_concat_fresh():
    xs: list[Int32] = [1, 2]
    ys: list[Int32] = [3]
    zs = xs.__add__(ys)
    zs.append(9)
    print(zs, xs)
    ws = xs + ys
    ws.append(7)
    print(ws, xs)


def test_bytes_concat_fresh():
    p = b"ab"
    q = p.__add__(b"cd")
    print(q, p)
    r = p * 2
    print(r)
    print(2 * p)
    ba = bytearray(b"xy")
    print(p + ba)


def test_bytearray_fresh():
    ba = bytearray(b"ab")
    bb = ba + b"cd"
    bb.append(33)
    print(bb, ba)
    print(ba + ba)
    print(ba * 2)
    # Stepped slices compared by content: TPy types the result bytes where
    # CPython returns bytearray (filed divergence), so the repr can't be
    # printed parity-safely -- equality is content-based on both sides.
    print(ba[::2] == b"a", ba[::-1] == b"ba")


def main():
    test_own_dunder_fresh()
    test_list_concat_fresh()
    test_bytes_concat_fresh()
    test_bytearray_fresh()


main()
