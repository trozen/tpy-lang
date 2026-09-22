# The wide-ptr-opt deref row is a NAME row: the narrowing fact it reads is
# recorded for a local binding, so a narrowed Optional FIELD read at the same
# pointee slot has no cell in any callee family and keeps rejecting.
from typing import Optional
from tpy import int32


class Box:
    items: Optional[list[int32]]

    def __init__(self) -> None:
        self.items = [0]


def take(xs: list[int32]) -> None:
    xs.append(2)


def main() -> None:
    b = Box()
    if b.items is not None:
        take(b.items)  # tpyc: error(/not yet supported.*arg_shape/)
        print(b.items)


main()
