# A local's tuple literal holds a call member by value, so a call lending
# a @nocopy object is the copy it cannot make: a located error, as
# `xs.append(k.get())` is (docs/LANGUAGE_FEATURES.md: a `@nocopy` leaf
# the slot owns is an error).
from tpy import int32
from tplib.box import Box


class R:
    def __init__(self, x: int32) -> None:
        self.x = x


class K:
    _v: Box[R]

    def __init__(self) -> None:
        self._v = Box(R(1))

    def get(self) -> Box[R]:
        return self._v


def f() -> None:
    k = K()
    t = (k.get(), 1)  # tpyc: error(/cannot copy non-copyable type 'Box\[R\]' into owned storage \(tuple element 0\)/)
    print(t[1])


f()
