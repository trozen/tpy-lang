# Generic ancestor: Base[T].method(self, x: T) called via Base.method(self, val)
# from a concrete child inheriting Base[int32]. Exercises the parent-type
# substitution path (get_parent_type_subst) on the unbound-self call site.
from tpy import int32


class Box[T]:
    item: T

    def __init__(self, item: T) -> None:
        self.item = item

    def get(self) -> T:
        return self.item


class IntBox(Box[int32]):
    def __init__(self, value: int32) -> None:
        Box.__init__(self, value)

    def fetch(self) -> int32:
        # Resolved: Box[int32].get(self) -> int32. The substitution T -> int32
        # comes from IntBox's parents entry Box[int32].
        return Box.get(self)


def main() -> None:
    b = IntBox(int32(7))
    print(b.fetch())


main()
