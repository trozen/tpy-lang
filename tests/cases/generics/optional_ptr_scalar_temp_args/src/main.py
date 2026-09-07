# A scalar passed to a generic's `T | None` parameter: the generic body spells
# that parameter as a pointer for every `T`, so each scalar rvalue is hoisted
# into a typed temp whose address is passed. The non-generic twin takes the
# value by `std::optional`; the case pins the current render
# (BUGS.md#generic-optional-scalar-param-pointer-form).
from tpy import Int32


class Container[T]:
    _val: T | None

    def __init__(self, val: T | None):
        self._val = val

    def probe(self, val: T | None) -> bool:
        return val is not None


def mk() -> Int32:
    return 5


def main() -> None:
    n = 3
    c = Container[Int32](None)
    print(c.probe(n + 1), c.probe(-n), c.probe(mk()))


main()
