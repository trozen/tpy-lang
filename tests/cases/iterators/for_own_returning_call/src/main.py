# Iterating directly over a call that returns Own[list[T]] / Own[dict[...]] /
# Own[set[T]] must use 'auto' (not 'auto&') for the hidden iteration temporary,
# since Own returns are by-value rvalues even when the wrapped type is a
# reference type. Covers free function, method, Callable-typed variable
# (expression callee), and top-level call sites.
from tpy import int32, Own
from typing import Callable


class Maker:
    def make(self, n: int32) -> Own[list[int32]]:
        out: list[int32] = []
        for i in range(n):
            out.append(i)
        return out


def make_batch(n: int32) -> Own[list[int32]]:
    out: list[int32] = []
    for i in range(n):
        out.append(i)
    return out


def make_pairs() -> Own[dict[str, int32]]:
    return {"a": 1, "b": 2}


def make_uniques() -> Own[set[int32]]:
    return {int32(10), int32(20), int32(30)}


def main() -> None:
    print("--- list ---")
    for e in make_batch(3):
        print(e)
    print("--- method ---")
    m = Maker()
    for e in m.make(2):
        print(e)
    print("--- dict ---")
    klen = int32(0)
    for k in make_pairs():
        klen = klen + len(k)
    print(klen)
    print("--- set ---")
    ssum = int32(0)
    for v in make_uniques():
        ssum = ssum + v
    print(ssum)
    print("--- callable var ---")
    f: Callable[[int32], Own[list[int32]]] = make_batch
    for e in f(2):
        print(e)


main()


print("--- top-level ---")
for e in make_batch(2):
    print(100 + e)
