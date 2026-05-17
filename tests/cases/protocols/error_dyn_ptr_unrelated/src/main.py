# Ptr[Subclass] -> Ptr[@dynamic Protocol] coercion only fires when the
# Subclass actually implements the protocol. Unrelated types get the
# normal type-mismatch error -- no silent reinterpret_cast.
from typing import Protocol
from tpy import Int32, Ptr, dynamic, nocopy


@dynamic
class Awaker(Protocol):
    def mark(self, task_id: Int32) -> None: ...


@nocopy
class NotAnAwaker:
    """Doesn't implement Awaker -- coercion must reject."""
    x: Int32
    def __init__(self) -> None:
        self.x = 0


def aim(p: Ptr[Awaker]) -> None:
    p.mark(1)


def main() -> None:
    n = NotAnAwaker()
    aim(n)  # tpyc: error(/argument 'p'/)


main()
