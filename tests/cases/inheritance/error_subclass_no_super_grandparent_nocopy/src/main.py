# No super(), missing default ctor two `__init__`-less levels up (Grand's
# @nocopy+__del__ field) (LANGUAGE_FEATURES "Single class inheritance").
from tpy import Ptr, int32, nocopy
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop


@nocopy
class Resource:
    _ptr: Ptr[int32]

    def __init__(self, value: int32) -> None:
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self) -> None:
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)


class Grand:
    res: Resource


class Mid(Grand):
    label: str


class Leaf(Mid):
    extra: int32

    def __init__(self, v: int32, label: str, e: int32) -> None:   # tpyc: error(/cannot be constructed without arguments \(ancestor 'Grand' \(inherited by 'Mid'\)/)
        self.res = Resource(v)
        self.label = label
        self.extra = e


def main() -> None:
    leaf = Leaf(42, "x", 7)
    print(leaf.extra)


main()
