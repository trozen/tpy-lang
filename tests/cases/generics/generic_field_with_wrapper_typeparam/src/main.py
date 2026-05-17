# Regression: TypeParamRef buried in `Int32 | T` and
# `Callable[[T], Int32]` field annotations must be detected so the
# generic-record field-init check skips them (the C++ template resolves
# at instantiation).
from typing import Callable
from tpy import Int32


class W[T]:
    # T buried inside a Union -- new walker catches it.
    via_union: Int32 | T
    # T buried inside a Callable's param list -- new walker catches it.
    via_callable: Callable[[T], Int32]
    # Sanity: T directly -- always caught.
    direct: T
    # Sentinel int we actually assign so the snippet has runtime content.
    tag: Int32

    def __init__(self, t: Int32) -> None:
        self.tag = t


def main() -> None:
    w = W[Int32](42)
    print(w.tag)


main()
