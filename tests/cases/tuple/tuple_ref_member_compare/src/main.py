# Value-level comparison over tuples with reference members: == / != / < / <=
# compare REFERENTS (CPython semantics), not the borrow slots' addresses, and
# `in` over a list of stored tuples value-compares a borrow-form needle.
# Distinct same-value objects must compare equal -- an address compare would
# silently return False.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v
    def __eq__(self, other: "Box") -> bool:
        return self.val == other.val
    def __lt__(self, other: "Box") -> bool:
        return self.val < other.val
    def __le__(self, other: "Box") -> bool:
        return self.val <= other.val
    def __gt__(self, other: "Box") -> bool:
        return self.val > other.val
    def __ge__(self, other: "Box") -> bool:
        return self.val >= other.val


def main() -> None:
    a = Box(5)
    b = Box(5)
    c = Box(7)
    print((1, a) == (1, b))
    print((1, a) != (1, b))
    print((1, a) == (2, a))
    print((1, a) < (1, c))
    print((1, c) <= (1, a))
    print((2, a) > (1, c))
    print((1, a) >= (1, b))
    ts = [(1, a), (2, c)]
    print((1, b) in ts)
    print((2, b) in ts)
    print((2, c) not in ts)


main()
