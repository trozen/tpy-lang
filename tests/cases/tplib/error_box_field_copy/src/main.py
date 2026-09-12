# Assigning a Box[T] into a field via a borrowed param errors: Box is
# @nocopy and cannot be implicitly copied -- the caller must transfer
# ownership via Own[Box[T]] (typically by passing .clone() at the call
# site, or moving an owned local at its last use). Mirrors the Rc analog
# in error_rc_field_copy.
from tpy import int32
from tplib import Box


class Counter:
    value: int32

    def __init__(self) -> None:
        self.value = int32(0)


class Holder:
    owned: Box[Counter]

    def __init__(self, owned: Box[Counter]) -> None:
        self.owned = owned  # tpyc: error(/cannot copy non-copyable type 'Box\[Counter\]'/)


def main() -> None:
    h = Holder(Box(Counter()))
    print(h.owned.get().value)


main()
