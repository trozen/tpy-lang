# Resumable-frame generator (two yields per element) yields a borrowed non-value
# loop var: the yield-return must deref the stored Box*, not return the pointer.
from typing import Iterator

class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v

def twice(xs: list[Box]) -> Iterator[Box]:
    for b in xs:
        yield b
        yield b

def main() -> None:
    data = [Box(1), Box(2)]
    # Read-only on purpose: scalar non-value yields currently copy rather than
    # borrow (value-form slot, BUGS.md), so mutating `b` would not propagate to
    # `data` and would diverge from CPython.
    for b in twice(data):
        print(b.val)

main()
