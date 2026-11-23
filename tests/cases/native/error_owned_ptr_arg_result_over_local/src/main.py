# A pointer moved into an `Own[...]` parameter still points where it did: a
# result that may be it, returned over a local, is refused as for a plain
# `Ptr` parameter (generic `Own[T]` at `T = Ptr[int32]`).
from tpy import int32, Ptr, Own, take_ptr
from tpy.extern import cpp_template


@cpp_template("{0}")
def gk[T](x: Own[T]) -> T: ...


def escaped() -> Ptr[int32]:
    n = 42
    # the moved-in pointer is the pointer to the local `n`
    return gk(take_ptr(n))  # tpyc: error(/Cannot return pointer to local or temporary value/)


def main() -> None:
    p = escaped()


main()
