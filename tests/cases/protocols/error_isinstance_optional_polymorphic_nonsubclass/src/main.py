# isinstance check type must be a subclass of the Optional[Polymorphic] inner.
# Both the Optional-direct sema path and the post-narrowing path reject
# unrelated check types with a targeted error.
from tpy import dynamic
from typing import Optional, Protocol


@dynamic
class Throwable(Protocol):
    def what(self) -> str: ...


class BaseExc(Throwable):
    message: str

    def __init__(self, msg: str) -> None:
        self.message = msg

    def what(self) -> str:
        return self.message


class OtherDyn(Throwable):
    def __init__(self) -> None:
        pass

    def what(self) -> str:
        return "other"


def check(e: Optional[BaseExc]) -> bool:
    return isinstance(e, OtherDyn)  # tpyc: error(/not subclasses of 'BaseExc'/)


def main() -> None:
    pass


main()
