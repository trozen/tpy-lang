# A nested def that shadows a CLASS, with the name read earlier in the enclosing
# body from inside a lambda. The shadowed entity is a record here, not a
# function -- the same registry resolution, so the same rule: the `def` makes
# `Tag` a local of `main` for the whole body, the earlier read cannot reach the
# record, and sema reports it.
from typing import Callable

from tpy import int32


class Tag:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def main() -> None:
    # the `Tag` read inside the lambda shares main's binding for the name
    tag_of: Callable[[int32], int32] = lambda k: Tag(k).v  # tpyc: error(/is read before the nested function 'Tag'/)
    print("pre:", tag_of(1))

    def Tag(x: int32) -> int32:
        return x + 100

    print("post:", Tag(1))


main()
