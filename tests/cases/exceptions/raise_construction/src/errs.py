from typing import Protocol
from tpy import Own, dynamic, int32, readonly


@dynamic
class Shows(Protocol):
    @readonly
    def show(self) -> str: ...


class Carrier(Exception, Shows):
    """A non-generic base the generic exceptions are caught by: an except
    clause cannot name a generic class (BUGS.md#generic-exception-except-clause),
    and `show` reads the caught payload through the override."""

    @readonly
    def show(self) -> str:
        return "carrier"


class ModErr(Exception):
    def __init__(self, code: int32) -> None:
        super().__init__("mod")
        self.code = code


class ModBox[T](Carrier):
    def __init__(self, v: Own[T]) -> None:
        super().__init__("modbox")
        self.v = v

    @readonly
    def show(self) -> str:
        return str(self.v)
