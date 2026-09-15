# A nested def that shadows a CLASS, with the name read earlier in the enclosing
# body from inside a lambda. The shadowed entity is a record here, not a
# function -- the same registry resolution, so the same divergence: Python makes
# `Tag` local to `main` for the whole body and the earlier call raises NameError,
# while sema reaches the record and constructs it
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from typing import Callable

from tpy import int32


class Tag:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def main() -> None:
    # the `Tag` read inside the lambda is the subject; the reject is on the def
    tag_of: Callable[[int32], int32] = lambda k: Tag(k).v
    print("pre:", tag_of(1))

    def Tag(x: int32) -> int32:  # tpyc: error(/stmt\.nested_def:nesteddef\.name_collision/)
        return x + 100

    print("post:", Tag(1))


main()
