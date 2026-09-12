# A null `Ptr[T]()` at an ARGUMENT slot is outside the member-init storage
# thread, so the call keeps rejecting.
from tpy import int32, Ptr


class Data:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


def use_ptr(q: Ptr[Data]) -> int32:
    if q is None:
        return 0
    return q.__deref__().value


def relay() -> int32:
    return use_ptr(Ptr[Data]())  # tpyc: error(/expr.call/)


def main() -> None:
    print(relay())


main()
