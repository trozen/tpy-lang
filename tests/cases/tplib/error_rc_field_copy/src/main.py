# Assigning an Rc[T] into a field via a borrowed param errors: Rc is
# nocopy and cannot be implicitly copied -- the caller must transfer
# ownership via Own[Rc[T]] (typically by passing .clone() at the call
# site).
from tpy import Int32
from tplib import Rc, make_rc


class Counter:
    value: Int32

    def __init__(self) -> None:
        self.value = Int32(0)


class Holder:
    shared: Rc[Counter]

    def __init__(self, shared: Rc[Counter]) -> None:
        self.shared = shared  # tpyc: error(/cannot copy non-copyable type 'Rc\[Counter\]'/)


def main() -> None:
    h = Holder(make_rc(Counter()))
    print(h.shared.get().value)


main()
