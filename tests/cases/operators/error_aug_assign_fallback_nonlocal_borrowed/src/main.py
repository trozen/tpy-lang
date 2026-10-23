# `w += k` with no __iadd__ falls back to __add__, which rebinds `w`; through
# `nonlocal`, on an enclosing local bound to a list element, that is refused
# as in the enclosing function, since the in-place update would write through
# to the element (BUGS.md#borrow-unpack-target-rebind-writes-through).
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[V]":
        return V(self.n + k)


def f(vs: list[V]) -> None:
    w = vs[-1]

    def g() -> None:
        nonlocal w
        # the subject: a rebinding += on a nonlocal name that borrows
        w += 10  # tpyc: error(/'\+=' on 'w' would rebind 'w' \(V has no '__iadd__'.*refers to an object it does not own/)
    g()
    print(w.n)


def main() -> None:
    vs = [V(1), V(2)]
    f(vs)
    print(vs[1].n)


main()
