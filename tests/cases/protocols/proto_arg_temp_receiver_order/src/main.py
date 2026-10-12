# A hoisted argument temporary at a protocol method whose receiver is a call:
# pins TPy's current evaluation order (argument before receiver).
from typing import Protocol
from tpy import Own, dynamic


@dynamic
class SP(Protocol):
    def put(self, v: Own[str]) -> None: ...


class Bag(SP):
    items: list[str]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: Own[str]) -> None:
        self.items.append(v)


class H:
    label: str

    def __init__(self) -> None:
        self.label = "orig"


def pick(b: SP, h: H) -> SP:
    h.label = "changed"
    return b


def drive(b: SP, h: H) -> None:
    # The `h.label` copy hoists ahead of `pick(b, h)`, so it reads "orig";
    # CPython evaluates the receiver first and reads "changed"
    # (BUGS.md#temp-arg-before-receiver-order).
    pick(b, h).put(h.label)  # tpyc: ok


def main() -> None:
    b = Bag()
    h = H()
    drive(b, h)
    print(b.items)


main()
