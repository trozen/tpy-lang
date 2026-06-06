# Ordering tuples on an Optional element is rejected: CPython raises
# TypeError when None meets `<`, and the C++ lexicographic compare derefs
# borrow slots under a non-null contract -- a null pointer-repr slot would
# be undefined behavior, so sema gates it.
from typing import Optional
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def __lt__(self, other: "Box") -> bool:
        return self.val < other.val


def main() -> None:
    a: Optional[Box] = Box(1)
    b: Optional[Box] = None
    print((1, a) < (1, b))  # tpyc: error(/ordering is undefined for an optional element/)


main()
