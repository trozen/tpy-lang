# The same name collision inside a GENERATOR body. A generator's nested def is
# emitted as a named member of the frame struct rather than a lambda, so the
# collision is a distinct unaudited hazard and keeps its own located reject --
# even without the recursion or pre-def read the lambda form needs. The async
# sibling is error_name_collision_in_async.
from typing import Iterator

from tpy import int32


def tally(x: int32) -> int32:
    return x + 1


def gen() -> Iterator[int32]:  # tpyc: error(/res\.nested_def_member/)
    # generator position: the frame-member nested def takes the module name
    def tally(x: int32) -> int32:
        return x + 100

    yield tally(1)
    yield 2


def main() -> None:
    for v in gen():
        print(v)


main()
