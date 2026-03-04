# Per-method type param bounds on inherited class type params
from tpy import Int32, Own, Default, Comparable, make_default

class Pair[T]:
    a: T
    b: T

    def __init__(self, a: Own[T], b: Own[T]) -> None:
        self.a = a
        self.b = b

    def min_val[T: Comparable](self) -> T:
        if self.a < self.b:
            return self.a
        return self.b

    def with_default[T: Default](self) -> T:
        return make_default()

def main() -> None:
    p = Pair[Int32](3, 7)
    print(p.min_val())
    print(p.with_default())

main()
