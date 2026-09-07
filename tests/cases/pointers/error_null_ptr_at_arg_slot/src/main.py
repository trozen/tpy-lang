# A null `Ptr[T]()` at an ARGUMENT slot is outside the member-init storage
# thread, so the call keeps rejecting.
from tpy import Int32, Ptr


class Data:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v


def use_ptr(q: Ptr[Data]) -> Int32:
    if q is None:
        return 0
    return q.__deref__().value


def relay() -> Int32:
    return use_ptr(Ptr[Data]())  # tpyc: error(/expr.call/)


def main() -> None:
    print(relay())


main()
