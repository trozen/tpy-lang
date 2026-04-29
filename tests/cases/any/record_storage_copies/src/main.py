# Any owns its contents -- storing a record in Any copies it. Mutating
# the original after storage does NOT affect the Any-held copy.
#
# This pins design Principle #1: "Any owns its contents (via std::any).
# Putting a value in copies it in." It's a documented divergence from
# CPython, where `a: Any = c` is reference assignment (mutating c shows
# through a). TPy's runtime-wrapper Any model copies on storage; the
# trade-off is shared-mutation semantics in exchange for owned-cell
# guarantees (no dangling references, std::any-managed lifetime).

from typing import Any


class Counter:
    def __init__(self, n: int) -> None:
        self.n = n


def main() -> None:
    c = Counter(1)
    a: Any = c
    c.n = 99
    if isinstance(a, Counter):
        print("a.n =", a.n)   # 1 in TPy, 99 in CPython
    print("c.n =", c.n)        # 99 in both


main()
