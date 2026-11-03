# A Ptr type argument whose pointee has no C++ spelling (a list of `Fn`)
# keeps rejecting rather than crashing the compiler.
from tpy import Ptr, Fn, int32


class Empty[T]:
    def __init__(self) -> None:
        pass


def main() -> None:
    h = Empty[Ptr[list[Fn[[int32], int32]]]]()  # tpyc: error(/not yet supported by C\+\+ code generation/)
    print("built")


main()
