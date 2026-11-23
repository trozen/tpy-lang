# A generic binding whose `Ptr[T | None]` result may point into a `Ptr[int32]`
# argument: returned over a local it is refused, as the concrete twin is (each
# member of an Optional pointee is a referent).
from tpy import int32, Ptr, take_ptr
from tpy.extern import cpp_template


@cpp_template("nullptr")
def ident_opt[T](p: Ptr[int32], witness: T) -> Ptr[T | None]: ...


def escaped() -> Ptr[int32 | None]:
    n = 42
    # the result at `T = int32` may be the pointer to the local `n`
    return ident_opt(take_ptr(n), 0)  # tpyc: error(/Cannot return pointer to local or temporary value/)


def main() -> None:
    p = escaped()


main()
