# tuple[T | None, ...] as a top-level global: a tuple of references, so the
# global is a tuple of pointer slots (`std::tuple<T*, T*>`), a `None` element
# is `nullptr`, and reading it into a pointer-form param binds it bare.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
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
