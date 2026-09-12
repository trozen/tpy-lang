# Phase 9: T-independent mutable ClassVar on a generic class. Each
# template instantiation gets its own `static inline` slot; mutations
# on `c: C[int32]` write to `C<int32_t>::counter`, distinct from
# `C<float>::counter`.
from typing import ClassVar
from tpy import int32


class C[T]:
    counter: ClassVar[int32] = 0

    def __init__(self) -> None:
        pass


def main() -> None:
    a = C[int32]()
    b = C[float]()
    a.counter = 10  # tpyc: warning(/Assigning to ClassVar 'C.counter' via instance/)
    b.counter = 20  # tpyc: warning(/Assigning to ClassVar 'C.counter' via instance/)
    print(a.counter)
    print(b.counter)


main()
