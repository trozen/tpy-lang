# For-loop over `list[tuple[T | None, T | None]]` passing the loop var
# to a `tuple[T | None, T | None]` param: the loop var binds storage form
# (`std::tuple<std::optional<T>, std::optional<T>>`); the call-arg site
# lifts via `tuple_to_pointer` to feed the borrow-form param.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def f(t: tuple[T | None, T | None]) -> Int32:
    a, b = t
    if a is None or b is None:
        return Int32(0)
    return a.x + b.x


def main() -> None:
    items: list[tuple[T | None, T | None]] = [
        (T(Int32(1)), T(Int32(2))),
        (None, T(Int32(3))),
    ]
    for it in items:
        print(f(it))


main()
