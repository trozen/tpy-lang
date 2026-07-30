# A classmethod on a generic class: `cls(...)` constructs the current
# instantiation, so the factory monomorphizes per element type.
from typing import Self

from tpy import Own


class Box[T]:
    def __init__(self, v: T):
        self.v = v  # tpyc: warning(/may copy T into field/)

    @classmethod
    def of(cls, v: T) -> Own[Self]:
        return cls(v)


def main() -> None:
    n = Box.of(7)
    s = Box.of("hi")
    print(n.v, s.v)


main()
