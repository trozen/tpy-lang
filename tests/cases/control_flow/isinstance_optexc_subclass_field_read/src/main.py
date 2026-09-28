# Subclass-typed reads after isinstance() narrowing on Optional[Polymorphic]:
# subclass-only fields are accessible inside the true branch.
from typing import Optional, Protocol
from tpy import dynamic


@dynamic
class Eventful(Protocol):
    def kind(self) -> str: ...


class Event(Eventful):
    def __init__(self) -> None: pass
    def kind(self) -> str:
        return "event"


class ClickEvent(Event):
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        super().__init__()
        self.x = x
        self.y = y
    def kind(self) -> str:
        return "click"


def describe(e: Optional[Event]) -> str:
    if e is None:
        return "<none>"
    if isinstance(e, ClickEvent):  # tpyc: ok
        narrowed = e  # tpyc: type(ClickEvent)
        return f"click({narrowed.x},{narrowed.y}) -> {narrowed.kind()}"
    return e.kind()


def main() -> None:
    print(describe(None))
    print(describe(Event()))
    print(describe(ClickEvent(3, 7)))


main()
