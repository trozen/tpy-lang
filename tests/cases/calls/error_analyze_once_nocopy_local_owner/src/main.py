# A @nocopy local read beside its own consume in one @dispatch call cannot
# be copied for the consume (docs/LANGUAGE_FEATURES.md "Working: Consuming
# Methods": a receiver that does not own its value is copied in, and a
# @nocopy copy is an error); the Own[T] parameter twin is
# error_analyze_once_nocopy_owner.
from typing import Self
from tpy import Own, dispatch, int32, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


@dispatch
def f(a: N, x: N) -> int32:
    return a.v + x.v


@dispatch
def f(a: str, x: N) -> int32:
    return 0


def k() -> None:
    b = N(1)
    print(f(b, b.take()))  # tpyc: error(/@nocopy/)


k()
