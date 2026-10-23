# `**=` with no __ipow__ on a local bound to a list element is refused for now
# (BUGS.md#borrow-unpack-target-rebind-writes-through); the message names the
# in-place dunder Python looks up, `__ipow__`.
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __pow__(self, k: int32) -> "Own[V]":
        return V(self.n + k)


def f(vs: list[V]) -> None:
    w = vs[0]
    # the subject: the fallback `**=` on a name that does not own its object
    w **= 2  # tpyc: error(/V has no '__ipow__', so it falls back to '__pow__'.*write 'w = w \*\* 2'/)
    print(w.n, vs[0].n)


def main() -> None:
    f([V(1)])


main()
