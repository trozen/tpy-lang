# Imported Final[T] constants from another module are accepted as default
# parameter values; codegen emits the qualified C++ name.
from tpy import uint32, int32
from consts import NOFLAG, CASELESS


def use(flags: uint32 = CASELESS) -> int32:
    return int32(flags)


def main() -> None:
    print(use())
    print(use(NOFLAG))


main()
