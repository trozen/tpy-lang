# A subscript on an `@native` record renders through the `__getitem__` stub the
# ranking picks -- the one `t.__getitem__(i)` calls -- not the first declared.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import dispatch, int32, int64
from tpy.extern import native


@native("nb::Item")
class Item:
    n: int32


@native("nb::Item2")
class Item2:
    n: int32


@native("nb::Bag")
class Bag:
    def __init__(self) -> None: ...

    @dispatch
    @native("b")
    def __getitem__(self, k: int64) -> Item: ...

    @dispatch
    @native("a")
    def __getitem__(self, k: int32) -> Item2: ...


def main() -> None:
    t = Bag()
    i: int32 = 0
    # the int32 key picks the `a` stub, typed Item2, as the explicit call does
    print(t[i].n, t.__getitem__(i).n)  # tpyc: ok


main()
