# `is` on an Optional-returning @property is not lowered yet: the getter hands
# back the field's storage, which the pointer form does not convert
# (BUGS.md#property-getter-pointer-optional-return).
from __future__ import annotations


class A:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class H:
    _kid: A | None

    def __init__(self) -> None:
        self._kid = None

    @property
    def child(self) -> A | None:
        return self._kid


def main() -> None:
    h = H()
    a = A(1)
    print(h.child is a)  # tpyc: error(/not yet supported/)


main()
