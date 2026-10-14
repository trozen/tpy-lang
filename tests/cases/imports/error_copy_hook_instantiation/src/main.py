# A `__copy__` hook returns the class at its own type arguments: `Box[T]`'s
# hook returning a `Box` of another module's same-named `Point` is refused.
from tpy import Own, copy
import pointa
import pointb


class Box[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def __copy__(self) -> Own[Box[pointb.Point]]:  # tpyc: error(/__copy__ must return Box\[T\], got Own\[Box\[Point\]\]/)
        return Box(pointb.Point())


def main() -> None:
    b = Box(pointa.Point())
    c = copy(b)
    print(c.v.v)


main()
