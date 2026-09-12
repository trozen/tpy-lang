# Assigning an Rc[T] into a field via a borrowed param errors: Rc is
# nocopy and cannot be implicitly copied -- the caller must transfer
# ownership via Own[Rc[T]] (typically by passing .clone() at the call
# site).
from tpy import int32
from tplib import Rc


class Counter:
    value: int32

    def __init__(self) -> None:
        self.value = int32(0)


class Holder:
    shared: Rc[Counter]

    def __init__(self, shared: Rc[Counter]) -> None:
        self.shared = shared  # tpyc: error(/cannot copy non-copyable type 'Rc\[Counter\]'/)


def main() -> None:
    h = Holder(Rc.new(Counter()))
    print(h.shared.get().value)


main()
