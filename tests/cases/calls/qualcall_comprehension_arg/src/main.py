# A comprehension at a container parameter of a QUALIFIED call (a
# `@staticmethod`, and a static method of a generic record): the free-call
# family hoists the slot-typed argument temp for this shape and the qualified
# family had no cell for it at all. The callee appends through the parameter
# and returns the length, so the temp it was handed is the container the
# comprehension built.
from tpy import int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    @staticmethod
    def take_list(xs: list[int32]) -> int32:
        xs.append(99)
        return len(xs)

    @staticmethod
    def take_dict(d: dict[str, int32]) -> int32:
        d["z"] = 99
        return len(d)

    @staticmethod
    def take_set(s: set[int32]) -> int32:
        s.add(99)
        return len(s)

    @staticmethod
    def take_recs(rs: list[Rec]) -> int32:
        rs.append(Rec(99))
        return len(rs)


class GHolder[T]:
    @staticmethod
    def take_list(xs: list[int32]) -> int32:
        xs.append(99)
        return len(xs)


def main() -> None:
    # list comprehension at a staticmethod's container slot
    print("static_list", Holder.take_list([i for i in range(3)]))
    # dict comprehension
    print("static_dict", Holder.take_dict({str(i): i for i in range(2)}))
    # set comprehension
    print("static_set", Holder.take_set({i for i in range(2)}))
    # a comprehension whose element is a reference type
    print("static_recs", Holder.take_recs([Rec(i) for i in range(2)]))
    # ... and the same shape on a generic record's static method
    print("generic_static", GHolder[int32].take_list([i for i in range(3)]))


main()
