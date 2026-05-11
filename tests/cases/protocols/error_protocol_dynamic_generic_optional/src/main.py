# Optional[generic @dynamic] is rejected (no pointer-repr for erased
# type) -- same rule as non-generic, applied to the parameterized form.
from typing import Protocol, Optional
from tpy import Int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...


class Box:
    def get(self) -> Int32:
        return 7


def maybe_show(c: Optional[Container[Int32]]) -> None:  # tpyc: error(/Optional\[Container\[Int32\]\] is not supported/)
    pass
