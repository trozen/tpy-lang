# Tuple literals as list elements with rvalue contents must keep the
# storage form (value tuple) -- the helper would emit T&-form slots that
# can't be stored in a std::vector<std::tuple<T, T>>. Verifies the
# in_storage_context guard in _tuple_literal_slot_info.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    items: list[tuple[T, T]] = [(T(1), T(2)), (T(3), T(4))]
    for it in items:
        a, b = it
        print(a.x)
        print(b.x)


main()
