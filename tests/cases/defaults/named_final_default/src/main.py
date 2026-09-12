# Module-level Final[T] constants are accepted as default parameter values
# in functions and methods. Codegen emits the bare name (or qualified name
# for cross-module references) rather than inlining the literal value.
from typing import Final
from tpy import uint32, int32

NOFLAG: Final[uint32] = uint32(0)
DEFAULT_LIMIT: Final[int32] = 16


def fn(flags: uint32 = NOFLAG, limit: int32 = DEFAULT_LIMIT) -> int32:
    return limit + int32(flags)


class Engine:
    def run(self, flags: uint32 = NOFLAG, limit: int32 = DEFAULT_LIMIT) -> int32:
        return limit + int32(flags)


def main() -> None:
    print(fn())
    print(fn(uint32(7)))
    print(fn(uint32(0), int32(3)))
    e = Engine()
    print(e.run())
    print(e.run(uint32(2), int32(1)))


main()
