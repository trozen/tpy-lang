# Generic ancestor: Base[T].method(self, x: T) called via Base.method(self, val)
# from a concrete child inheriting Base[Int32]. Exercises the parent-type
# substitution path (get_parent_type_subst) on the unbound-self call site.
from tpy import Int32


class Box[T]:
    item: T

    def __init__(self, item: T) -> None:
        self.item = item

    def get(self) -> T:
        return self.item


class IntBox(Box[Int32]):
    def __init__(self, value: Int32) -> None:
        Box.__init__(self, value)

    def fetch(self) -> Int32:
        # Resolved: Box[Int32].get(self) -> Int32. The substitution T -> Int32
        # comes from IntBox's parents entry Box[Int32].
        return Box.get(self)


def main() -> None:
    b = IntBox(Int32(7))
    print(b.fetch())


main()
