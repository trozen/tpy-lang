# A tuple local bound once inside a `try` body from a literal with a fresh
# element: the try hoist predeclares the borrow tuple, which has no storage
# for the element, so it is refused in words
# (BUGS.md#try-body-tuple-bound-once-rejects).
from tpy import Own, int32


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


def mk(x: int32) -> Own[P]:
    return P(x)


def f() -> None:
    try:
        t = (mk(10), 1)  # tpyc: error(/element 0 here would have to be held by the tuple/)
    finally:
        print("fin")
    print(t[0].x)


f()
