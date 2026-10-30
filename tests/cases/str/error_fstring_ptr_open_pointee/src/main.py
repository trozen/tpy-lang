# An f-string refuses a Ptr to an open T: at T = char it would read a C string
# (docs/LANGUAGE_FEATURES.md "F-string Formatting", BUGS.md#list-ptr-char-prints-garbage).
from tpy import Ptr, take_ptr


def show[T](p: Ptr[T]) -> str:
    return f"{p}"  # tpyc: error(/Ptr\[T\] with a generic pointee cannot be used in an f-string yet; print it or dereference it first/)


def main() -> None:
    n = 3
    print(show(take_ptr(n)))


main()
