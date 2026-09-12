# Test the pass-through pattern: returned tuple flows directly into another
# function expecting the same TPy type. Both return and param use pointer
# form, so the C++ types match without an intermediate conversion.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def make_pair(a: T, b: T) -> tuple[T | None, T | None]:
    return (a, b)


def consume(p: tuple[T | None, T | None]) -> int32:
    a, b = p
    total = int32(0)
    if a is not None:
        total = total + a.x
    if b is not None:
        total = total + b.x
    return total


def main() -> None:
    a = T(3)
    b = T(4)

    # Direct pass-through: return value of make_pair flows into consume.
    print(consume(make_pair(a, b)))

    # Through a local binding.
    pair = make_pair(a, b)
    print(consume(pair))


main()
