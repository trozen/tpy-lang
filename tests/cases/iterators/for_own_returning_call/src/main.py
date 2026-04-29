# Iterating directly over a call that returns Own[list[T]] / Own[dict[...]] /
# Own[set[T]] must use 'auto' (not 'auto&') for the hidden iteration temporary,
# since Own returns are by-value rvalues even when the wrapped type is a
# reference type. Covers free function, method, Callable-typed variable
# (expression callee), and top-level call sites.
from tpy import Int32, Own
from typing import Callable


class Maker:
    def make(self, n: Int32) -> Own[list[Int32]]:
        out: list[Int32] = []
        for i in range(n):
            out.append(i)
        return out


def make_batch(n: Int32) -> Own[list[Int32]]:
    out: list[Int32] = []
    for i in range(n):
        out.append(i)
    return out


def make_pairs() -> Own[dict[str, Int32]]:
    return {"a": 1, "b": 2}


def make_uniques() -> Own[set[Int32]]:
    return {Int32(10), Int32(20), Int32(30)}


def main() -> None:
    print("--- list ---")
    for e in make_batch(3):
        print(e)
    print("--- method ---")
    m = Maker()
    for e in m.make(2):
        print(e)
    print("--- dict ---")
    klen = Int32(0)
    for k in make_pairs():
        klen = klen + len(k)
    print(klen)
    print("--- set ---")
    ssum = Int32(0)
    for v in make_uniques():
        ssum = ssum + v
    print(ssum)
    print("--- callable var ---")
    f: Callable[[Int32], Own[list[Int32]]] = make_batch
    for e in f(2):
        print(e)


main()


print("--- top-level ---")
for e in make_batch(2):
    print(100 + e)
