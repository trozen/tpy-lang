# Imported Final[T] constants from another module are accepted as default
# parameter values; codegen emits the qualified C++ name.
from tpy import UInt32, Int32
from consts import NOFLAG, CASELESS


def use(flags: UInt32 = CASELESS) -> Int32:
    return Int32(flags)


def main() -> None:
    print(use())
    print(use(NOFLAG))


main()
