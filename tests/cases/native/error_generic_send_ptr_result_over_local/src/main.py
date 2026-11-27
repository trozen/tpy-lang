# A generic binding whose `Ptr[Send[T]]` result may point into a `Ptr[int32]`
# argument: returned over a local it is refused, as the concrete twin is (the
# marked pointee is still an open `T`).
from tpy import int32, Ptr, Send, take_ptr
from tpy.extern import cpp_template


@cpp_template("(static_cast<void>({1}), {0})")
def ident_send[T](p: Ptr[int32], witness: T) -> Ptr[Send[T]]: ...


def escaped() -> Ptr[int32]:
    n = 42
    # the result at `T = int32` may be the pointer to the local `n`
    return ident_send(take_ptr(n), 0)  # tpyc: error(/Cannot return pointer to local or temporary value/)


def main() -> None:
    p = escaped()


main()
