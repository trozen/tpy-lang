from b import B
from tpy import int32
from typing import Iterator, Literal, overload

def helper() -> int32:
    return 42

class A:
    items: list[int32]
    def __init__(self) -> None:
        self.items = [1, 2]
    # An `@overload` variant returning `Iterator[T]` keeps its body in the
    # class, in `a.hpp`, which sees `B` only forward-declared: the by-value
    # peer parameter must be rejected.
    @overload
    def __iter__(self) -> Iterator[int32]: ...
    @overload
    def __iter__(self, b: B) -> Iterator[int32]: ...  # tpyc: error(/Cyclic import/)
    def __iter__(self, b: B | None = None) -> Iterator[int32]:
        if b is not None:
            self.items.append(b.payload)
        return iter(self.items)
