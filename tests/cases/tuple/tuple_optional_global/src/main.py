# tuple[T | None, ...] as a top-level global. Storage form is
# std::tuple<std::optional<T>, ...>; init from pointer-form literal goes
# through tuple_to_storage. Reading the global into a pointer-form param
# goes through tuple_to_pointer.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


t1 = T(10)
t2 = T(20)
g: tuple[T | None, T | None] = (t1, t2)
g_partial: tuple[T | None, T | None] = (t1, None)


def consume(p: tuple[T | None, T | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)
    else:
        print("None")
    if b is not None:
        print(b.x)
    else:
        print("None")


def main() -> None:
    consume(g)
    consume(g_partial)


main()
