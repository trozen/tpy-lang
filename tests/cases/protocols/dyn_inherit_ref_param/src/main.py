# Nominal @dynamic-protocol conformer overriding methods with reference-type
# (dict/list) params -- regression for the override-param const-mismatch fix.
from typing import Protocol
from tpy import dynamic, int32
from tplib import Box


@dynamic
class Sink(Protocol):
    def total(self, items: dict[str, int32] | None = None) -> int32: ...
    def bump(self, items: dict[str, int32]) -> None: ...
    def width(self, tags: list[str]) -> int32: ...


class Counter(Sink):
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def total(self, items: dict[str, int32] | None = None) -> int32:
        n = self.base
        if items is not None:
            for v in items.values():
                n += v
        return n

    def bump(self, items: dict[str, int32]) -> None:
        items["seen"] = self.base

    def width(self, tags: list[str]) -> int32:
        n = 0
        for t in tags:
            n += len(t)
        return int32(n)


def use(s: Box[Sink], d: dict[str, int32]) -> int32:
    s.get().bump(d)
    return s.get().total(d)


def main() -> None:
    d = {"a": int32(2), "b": int32(3)}
    box: Box[Sink] = Box(Counter(10))
    print(use(box, d))
    # The mutation is visible here -> the dict was aliased through the box, not
    # silently copied at the @dynamic boundary.
    print("seen" in d, len(d))
    tags: list[str] = ["ab", "cde"]
    print(box.get().width(tags))


main()
