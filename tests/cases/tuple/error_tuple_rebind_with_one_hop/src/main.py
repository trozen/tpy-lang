# A `with` whose manager is an element of a tuple local (`with t[0] as gg`) is
# rejected at the statement, rebind of `t` or not
# (BUGS.md#with-tuple-element-manager-rejects).
from tpy import int32


class G:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __enter__(self) -> "G":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def f(c: bool) -> None:
    t = (G(1), 5)
    # The manager is the tuple's element itself: the lowering refuses the
    # `with` statement (`stmt.with`), whether or not `t` is rebound.
    with t[0] as gg:  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt\.with\)/)
        if c:
            t = (G(10), 6)
        gg.v += 100
        print("f", gg.v, t[0].v)


f(True)
