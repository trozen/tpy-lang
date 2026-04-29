# Module-level Final[T] constants are accepted as default parameter values
# in functions and methods. Codegen emits the bare name (or qualified name
# for cross-module references) rather than inlining the literal value.
from typing import Final
from tpy import UInt32, Int32

NOFLAG: Final[UInt32] = UInt32(0)
DEFAULT_LIMIT: Final[Int32] = 16


def fn(flags: UInt32 = NOFLAG, limit: Int32 = DEFAULT_LIMIT) -> Int32:
    return limit + Int32(flags)


class Engine:
    def run(self, flags: UInt32 = NOFLAG, limit: Int32 = DEFAULT_LIMIT) -> Int32:
        return limit + Int32(flags)


def main() -> None:
    print(fn())
    print(fn(UInt32(7)))
    print(fn(UInt32(0), Int32(3)))
    e = Engine()
    print(e.run())
    print(e.run(UInt32(2), Int32(1)))


main()
