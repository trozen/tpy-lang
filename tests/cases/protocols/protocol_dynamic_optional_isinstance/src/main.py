# Optional[Polymorphic] class dispatch via isinstance + dynamic_cast.
# Inner type (BaseExc) inherits a @dynamic protocol (Throwable), so:
#   - rvalue construction into Optional[BaseExc] preserves dynamic type
#     (codegen materializes the temp at the rvalue's actual class type)
#   - isinstance(opt, Subclass) lowers to dynamic_cast on the pointer
#   - virtual dispatch on methods inherited from the base class works
#     through the polymorphic-class chain (Throwable -> BaseExc -> subclass)
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
        return "[base] " + self.message


class ValErr(BaseExc):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)

    def what(self) -> str:
        return "[val] " + self.message


class OsErr(BaseExc):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)

    def what(self) -> str:
        return "[os] " + self.message


def classify(e: Optional[BaseExc]) -> str:
    if e is None:
        return "<none>"
    if isinstance(e, ValErr):  # tpyc: ok
        return "VAL: " + e.what()
    if isinstance(e, (OsErr, ValErr)):  # tpyc: ok -- tuple form
        return "OS-or-VAL: " + e.what()
    return "BASE: " + e.what()


def classify_direct(e: Optional[BaseExc]) -> str:
    # Optional-source isinstance path (no preceding `is None` narrowing) --
    # exercises _analyze_isinstance's Optional-direct branch.
    if isinstance(e, ValErr):  # tpyc: ok
        return "DIRECT-VAL"
    if isinstance(e, BaseExc):  # tpyc: ok -- always-True boundary on non-None
        return "DIRECT-BASE"
    return "DIRECT-NONE"


def main() -> None:
    # rvalue construction into Optional[BaseExc] -- previously sliced
    print(classify(ValErr("v1")))
    print(classify(OsErr("o1")))
    print(classify(BaseExc("b1")))
    print(classify(None))
    # Optional-source path
    print(classify_direct(ValErr("d1")))
    print(classify_direct(BaseExc("d2")))
    print(classify_direct(None))


main()
