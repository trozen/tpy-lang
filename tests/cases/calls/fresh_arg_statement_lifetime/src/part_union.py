# A union parameter given one member builds the union explicitly and inline:
# the bare member would let C++ pick another overload of the same name.
from typing import Protocol
from tpy import dispatch, int32


class Picker(Protocol):
    def pick(self, x: int32 | str) -> int32: ...


class M:
    def __init__(self) -> None:
        pass

    @dispatch
    def pick(self, x: int32 | str) -> int32:
        return 1

    @dispatch
    def pick(self, x: float) -> int32:
        return 2


def run(p: Picker, x: int32) -> int32:
    # the union overload, not the float one
    return p.pick(x)  # tpyc: ok


def kind(x: int32 | str) -> str:
    return "str" if isinstance(x, str) else "int"


def main() -> None:
    n = 3
    print("union overload", run(M(), 7))
    print("union free", kind(n))  # tpyc: ok
