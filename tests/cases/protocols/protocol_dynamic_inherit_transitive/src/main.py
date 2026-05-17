# Transitive @dynamic inheritance: a class inheriting a @dynamic protocol
# through a concrete-class intermediate must still get virtual override on
# its method redefinitions. Three-level chain: Throwable (@dynamic) ->
# BaseExc (concrete) -> ValErr / OsErr (concrete, override what()).
#
# Without transitive-override propagation, ValErr/OsErr's what() emits as a
# non-virtual hiding method and BaseExc&-typed access dispatches to
# BaseExc::what() instead of the subclass's.
from tpy import dynamic
from typing import Protocol


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


def report(e: BaseExc) -> str:  # tpyc: ok
    # Virtual dispatch through transitively-inherited Throwable vtable slot.
    return e.what()


def main() -> None:
    v = ValErr("v-msg")
    o = OsErr("o-msg")
    b = BaseExc("b-msg")
    print(report(v))
    print(report(o))
    print(report(b))


main()
