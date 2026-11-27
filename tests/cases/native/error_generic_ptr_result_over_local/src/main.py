# A generic binding whose `Ptr[T]` result may point into a `Ptr[int32]`
# argument: returned over a local it is refused, as its concrete twin
# `ident(p: Ptr[int32], witness: int32) -> Ptr[int32]` is.
from tpy import int32, Ptr, take_ptr
from tpy.extern import cpp_template


@cpp_template("(static_cast<void>({1}), {0})")
def ident[T](p: Ptr[int32], witness: T) -> Ptr[T]: ...


def escaped() -> Ptr[int32]:
    n = 42
    # the result at `T = int32` may be the pointer to the local `n`
    return ident(take_ptr(n), 0)  # tpyc: error(/Cannot return pointer to local or temporary value/)


def main() -> None:
    p = escaped()


main()
