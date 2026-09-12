# A `with ... as NAME` target whose name also names a module-level class: reads
# in the body must see the context manager, not the class. The body mutates
# through the target and the caller observes it via the original handle, so a
# silent copy at the `__enter__` boundary shows up as a wrong value rather than
# passing blind.
from typing import ClassVar

from tpy import int32


class Registry:
    code: ClassVar[int32] = 999


class Guard:
    def __init__(self, code: int32):
        self.code = code

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def run() -> int32:
    g = Guard(7)
    with g as Registry:  # tpyc: ok
        Registry.code += 1
    return g.code


def main() -> None:
    print(run())
    print(Registry.code)


main()
