# Rvalue tuple literal targeting a ref-form (T&) slot. The old codegen
# emitted `std::tuple<T, T>{T(1), T(2)}` which doesn't bind to the
# function param's `const std::tuple<T&, T&>&`. The fix wraps the
# value-tuple source with ::tpy::tuple_value_to_borrow so the resulting
# tuple holds T& slots bound to the source temp.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def show(p: tuple[T, T]) -> None:
    a, b = p
    print(a.x)
    print(b.x)


def main() -> None:
    # Both rvalue.
    show((T(1), T(2)))
    # Mixed: rvalue + lvalue.
    a = T(10)
    show((a, T(20)))
    show((T(30), a))


main()
