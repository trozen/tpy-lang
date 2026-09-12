# A FILTERED comprehension's element access stays sema-unproven, so the storage
# optional is unwrapped whole by the checked deref, not lifted to a pointer.
# The filter clause should narrow and does not, which is a filed defect
# (BUGS.md#comp-filter-none-test-not-narrowing); this case pins the warning.
from typing import Optional
from tpy import int32


class Foo:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    items: list[Optional[Foo]] = [Foo(1), None]
    xs = [item.x for item in items if item is not None]  # tpyc: warning(/Potential None access/)
    print(xs)


main()
