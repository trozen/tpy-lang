# For-loop over `list[tuple[T | None, T | None]]` passing the loop var
# to a `tuple[T | None, T | None]` param: the loop var binds storage form
# (`std::tuple<std::optional<T>, std::optional<T>>`); the call-arg site
# lifts via `tuple_to_pointer` to feed the borrow-form param.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def f(t: tuple[T | None, T | None]) -> int32:
    a, b = t
    if a is None or b is None:
        return int32(0)
    return a.x + b.x


def main() -> None:
    items: list[tuple[T | None, T | None]] = [
        (T(int32(1)), T(int32(2))),
        (None, T(int32(3))),
    ]
    for it in items:
        print(f(it))


main()
