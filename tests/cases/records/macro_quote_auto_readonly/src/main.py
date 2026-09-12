# Regression: macro-emitted @auto_readonly methods flow through sema expansion.
from tpy import int32, readonly
from ro_getter import ro_getter


@ro_getter
class Holder:
    value: int32


def touch_readonly(h: readonly[Holder]) -> int32:
    return h.first()


def main() -> None:
    h = Holder(int32(42))
    print(h.first())
    print(touch_readonly(h))


main()
