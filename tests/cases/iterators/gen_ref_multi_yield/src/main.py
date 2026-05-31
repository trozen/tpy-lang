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
    # Borrow yield: mutation through the yielded `b` propagates to the source
    # element. Each element is yielded twice, so both yields alias the same Box
    # and the +10 applies twice per element.
    for b in twice(data):
        b.val = b.val + 10
    for d in data:
        print(d.val)

main()
