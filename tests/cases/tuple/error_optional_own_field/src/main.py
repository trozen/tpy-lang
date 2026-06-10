# `Optional[Own[T]]` on a FIELD is rejected as redundant: a field is storage
# form regardless, so the Own exemption is load-bearing only for locals.
from typing import Optional

from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


class Holder:
    x: Optional[Own[Box]]  # tpyc: error(/Own\[T\] is redundant in this field type/)

    def __init__(self):
        self.x = None


def main() -> None:
    h = Holder()
    print(0 if h.x is None else h.x.val)


main()
