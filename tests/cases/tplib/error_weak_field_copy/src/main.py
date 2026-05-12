# Assigning a Weak[T] into a field via a borrowed param errors: Weak is
# nocopy and cannot be implicitly copied -- the caller must transfer
# ownership via Own[Weak[T]] (typically by passing rc.downgrade() or
# weak.clone() at the call site). Mirrors error_rc_field_copy for the
# non-owning companion.
from tpy import Int32
from tplib.rc import Rc, Weak, make_rc


class Counter:
    value: Int32

    def __init__(self) -> None:
        self.value = Int32(0)


class Observer:
    target: Weak[Counter]

    def __init__(self, target: Weak[Counter]) -> None:
        self.target = target  # tpyc: error(/cannot copy non-copyable type 'Weak\[Counter\]'/)


def main() -> None:
    rc = make_rc(Counter())
    obs = Observer(rc.downgrade())
    print(obs.target.upgrade() is None)


main()
