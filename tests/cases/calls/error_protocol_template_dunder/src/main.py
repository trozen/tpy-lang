# A call through a protocol to a dunder whose parameters are not its operator's
# operands is refused (BUGS.md#protocol-call-skips-template-dispatch-variant).
from typing import Protocol
from tpy import int32, dispatch, readonly
from tpy.extern import cpp_template


class P(Protocol):
    def __getitem__(self, k: int32, extra: int32) -> int32: ...


class R:
    def __init__(self) -> None:
        pass

    @dispatch
    @readonly
    @cpp_template("(static_cast<void>({self}), {0} + {1})")
    def __getitem__(self, k: int32, extra: int32) -> int32: ...

    @dispatch
    def __getitem__(self, k: float, extra: float = 0.0) -> int32:
        return 42


def check[T: P](r: T) -> int32:
    return r.__getitem__(3, 4)  # tpyc: error(/not yet supported/)


def main() -> None:
    r = R()
    print(r.__getitem__(3, 4))
    print(check(r))


main()
