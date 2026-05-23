# Grandparent-chain coverage for the super-init rule. The deleted default
# ctor lives on Grand (because of its @nocopy+__del__ field); Mid inherits
# but its own fields are default-ctor-able, so the predicate must recurse
# through Mid's ancestor list to reach Grand and reject Leaf.
from tpy import Ptr, Int32, nocopy
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop


@nocopy
class Resource:
    _ptr: Ptr[Int32]

    def __init__(self, value: Int32) -> None:
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self) -> None:
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)


class Grand:
    res: Resource

    def __init__(self, v: Int32) -> None:
        self.res = Resource(v)


class Mid(Grand):
    label: str

    def __init__(self, v: Int32, label: str) -> None:
        super().__init__(v)
        self.label = label


class Leaf(Mid):
    extra: Int32

    def __init__(self, v: Int32, label: str, e: Int32) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.extra = e


def main() -> None:
    leaf = Leaf(42, "x", 7)
    print(leaf.extra)


main()
