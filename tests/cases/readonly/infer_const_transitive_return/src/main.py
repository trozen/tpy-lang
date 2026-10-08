# Const inference for methods returning self.field[i]:
# - without @readonly: non-const (returns mutable ref, caller can mutate self's data)
# - with @readonly and a readonly return type: const (returns const ref)
# Transitive return through a non-@readonly callee marks self as mutated -> non-const.
from tpy import readonly


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Container:
    _items: list[Point]

    def __init__(self) -> None:
        self._items = [Point(1), Point(2)]

    def first_mutable(self) -> Point:    # non-const: mutable ref into self's data
        return self._items[0]

    @readonly
    def first_readonly(self) -> readonly[Point]:   # const: explicitly read-only
        return self._items[0]

    def first_x(self) -> int:            # auto-const: value return, no self borrow
        return self._items[0].x


class Wrapper:
    _c: Container

    def __init__(self) -> None:
        self._c = Container()

    def get_mutable(self) -> Point:      # non-const: transitive through non-readonly callee
        return self._c.first_mutable()


def main() -> None:
    w = Wrapper()
    print(w.get_mutable().x)   # 1
    c = Container()
    print(c.first_x())         # 1


main()
