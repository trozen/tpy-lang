# A generic binding whose `Ptr[readonly[T]]` result may point into a `Ptr[int32]`
# argument: returned over a local it is refused, as the `Ptr[T]` form and the
# concrete twin are (the readonly pointee is still an open `T`).
from tpy import int32, Ptr, take_ptr, readonly
from tpy.extern import cpp_template


@cpp_template("{0}")
def ident[T](p: Ptr[int32], witness: T) -> Ptr[readonly[T]]: ...


def escaped() -> Ptr[readonly[int32]]:
    n = 42
    # the result at `T = int32` may be the pointer to the local `n`
    return ident(take_ptr(n), 0)  # tpyc: error(/Cannot return pointer to local or temporary value/)


def main() -> None:
    p = escaped()


main()
