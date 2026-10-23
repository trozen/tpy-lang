# `v += k` with no __iadd__ falls back to __add__, which rebinds `v`; on a
# zip head (it refers to an element it does not own) that is refused for now,
# since the in-place update would write through to the element
# (BUGS.md#borrow-unpack-target-rebind-writes-through).
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[V]":
        return V(self.n + k)


def bump_all(css: list[list[V]], ks: list[int32]) -> None:
    for cs in css:
        for v, k in zip(cs, ks):
            # the subject: a rebinding += on a name that borrows its element
            v += k  # tpyc: error(/'\+=' on 'v' would rebind 'v' \(V has no '__iadd__'.*refers to an object it does not own.*write 'v = v \+ k'/)


def main() -> None:
    css = [[V(1), V(2)]]
    bump_all(css, [10, 20])
    print(css[0][0].n, css[0][1].n)


main()
